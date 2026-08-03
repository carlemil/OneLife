"""Password hashing + TOTP 2FA helpers (ACCOUNTS_AND_ONBOARDING.md)."""
import io
import time
import secrets

import bcrypt
import pyotp
import segno

ISSUER = "OneLife"
_RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no ambiguous chars


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except ValueError:
        return False


def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=ISSUER)


def verify_totp(secret: str, code: str) -> bool:
    if not code:
        return False
    # valid_window=1 tolerates a little clock skew / a code that just rolled.
    return pyotp.TOTP(secret).verify(code.strip().replace(" ", ""), valid_window=1)


def totp_step(secret: str, code: str) -> int | None:
    """Which 30s time-step `code` belongs to, or None if it isn't valid.

    A code stays valid for ~90s (its own step ±1 for clock skew), so verifying it
    is not enough on its own: whoever observes it — shoulder-surfing, a phishing
    proxy, a logged request — can replay it within that window. Returning the step
    lets the caller record it and refuse to consume the same one twice. Steps are
    checked newest-first so the recorded value is the highest one that matched.
    """
    if not code:
        return None
    t = pyotp.TOTP(secret)
    code = code.strip().replace(" ", "")
    now = int(time.time())
    for offset in (1, 0, -1):
        step = now // t.interval + offset
        if secrets.compare_digest(t.at(step * t.interval), code):
            return step
    return None


def qr_svg(uri: str) -> str:
    """Inline SVG of the otpauth URI, for the client to render the QR directly."""
    buf = io.BytesIO()
    segno.make(uri).save(buf, kind="svg", scale=4, border=2)
    return buf.getvalue().decode()


def generate_recovery_codes(n: int = 8) -> list[str]:
    """One-time backup codes shown once at 2FA setup (format XXXX-XXXX)."""
    def one():
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(8))
        return f"{raw[:4]}-{raw[4:]}"
    return [one() for _ in range(n)]


def normalize_code(code: str) -> str:
    return code.strip().upper().replace(" ", "")

