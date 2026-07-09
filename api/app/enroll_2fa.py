"""Enrol two-factor authentication on an existing account, from the server side.

Admin routes require `totp_enabled` (see `main._require_admin`), but the only
in-app path to a TOTP secret is registration — so a password-only account that is
later added to ONELIFE_ADMIN_EMAILS has no way to enrol through the API. This is
the operator's door, and it is deliberately not reachable over HTTP.

    docker compose exec -T api python -m app.enroll_2fa <email> --begin
    # scan the otpauth:// URI (or type the secret in), then:
    docker compose exec -T api python -m app.enroll_2fa <email> --confirm 123456

--begin issues a fresh secret and leaves 2FA *off*; the account keeps working with
its password alone until --confirm verifies a live code. Re-running --begin before
confirming simply reissues. Confirming prints one-time recovery codes and, as with
the registration flow, replaces any codes issued earlier.
"""
import sys
import asyncio

from . import db, auth, security


async def _begin(conn, email: str) -> int:
    p = await conn.fetchrow(
        "SELECT id, totp_enabled FROM players WHERE email=$1", email)
    if p is None:
        print(f"No account with login handle {email!r}.")
        return 1
    if p["totp_enabled"]:
        print(f"{email} already has 2FA enabled. Nothing to do.")
        return 1
    secret = auth.new_totp_secret()
    await conn.execute("UPDATE players SET totp_secret=$2, totp_enabled=FALSE WHERE id=$1",
                       p["id"], security.encrypt(secret))
    print(f"Secret for {email}:\n\n    {secret}\n")
    print(f"otpauth URI (paste into your authenticator):\n\n    {auth.totp_uri(secret, email)}\n")
    print("2FA is NOT active yet. Confirm with a live code:\n"
          f"    python -m app.enroll_2fa {email} --confirm <code>")
    return 0


async def _confirm(conn, email: str, code: str) -> int:
    p = await conn.fetchrow(
        "SELECT id, totp_secret, totp_enabled FROM players WHERE email=$1", email)
    if p is None:
        print(f"No account with login handle {email!r}.")
        return 1
    if p["totp_enabled"]:
        print(f"{email} already has 2FA enabled. Nothing to do.")
        return 1
    if not p["totp_secret"]:
        print(f"No pending secret for {email}. Run --begin first.")
        return 1
    if not auth.verify_totp(security.decrypt(p["totp_secret"]), code):
        print("That code isn't valid for the pending secret. Check the clock and retry.")
        return 1
    codes = auth.generate_recovery_codes()
    async with conn.transaction():
        await conn.execute("UPDATE players SET totp_enabled=TRUE WHERE id=$1", p["id"])
        await conn.execute("DELETE FROM recovery_codes WHERE player_id=$1", p["id"])
        for c in codes:
            await conn.execute(
                "INSERT INTO recovery_codes (player_id, code_hash) VALUES ($1,$2)",
                p["id"], auth.hash_password(c))
    print(f"2FA enabled for {email}. Existing sessions still work; the next login "
          "will ask for a code.\n")
    print("Recovery codes — shown once, store them somewhere safe:\n")
    for c in codes:
        print(f"    {c}")
    return 0


async def run(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in ("--begin", "--confirm"):
        print(__doc__)
        return 2
    email = argv[0].strip().lower()
    pool = await db.get_pool()
    try:
        async with pool.acquire() as conn:
            if argv[1] == "--begin":
                return await _begin(conn, email)
            if len(argv) < 3:
                print("--confirm needs the 6-digit code from your authenticator.")
                return 2
            return await _confirm(conn, email, argv[2])
    finally:
        await db.close_pool()


if __name__ == "__main__":
    sys.exit(asyncio.run(run(sys.argv[1:])))
