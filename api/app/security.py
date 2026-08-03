"""Security helpers: secret encryption at rest + in-memory rate limiting/lockout.

Encryption uses a Fernet key derived from ONELIFE_SECRET_KEY. Rate limiting is
in-process (fine for the single-instance prototype; use Redis for multi-instance).
"""
import os
import time
import json
import base64
import hashlib
import ipaddress

from cryptography.fernet import Fernet

# Deployment posture. `dev` (the default) keeps the app bootable with no config at
# all — stubs, fallbacks, auto-docs. `prod` is set by docker-compose.prod.yml and
# turns those conveniences off wherever one would be a hole on the open internet.
ENV = os.environ.get("ONELIFE_ENV", "dev").strip().lower()
IS_PROD = ENV in ("prod", "production")

_secret = os.environ.get("ONELIFE_SECRET_KEY", "").strip()
INSECURE_DEFAULT = not _secret
if INSECURE_DEFAULT:
    # The fallback key is in the repo, so anything it protects (TOTP secrets at
    # rest, the signed inference-broker envelope) is protected by nothing. That is
    # an acceptable trade for a keyless dev boot and unacceptable in production —
    # refuse to start rather than serve with a public key nobody notices.
    if IS_PROD:
        raise RuntimeError(
            "ONELIFE_SECRET_KEY must be set when ONELIFE_ENV=prod — the dev fallback "
            "key is published in this repo and would leave TOTP secrets readable by "
            "anyone with the database. Set it in .env and restart.")
    _secret = "dev-insecure-onelife-key"
_fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(_secret.encode()).digest()))


def hash_token(token: str) -> str:
    """One-way digest of a session token, for storage.

    Session tokens are high-entropy UUIDs, so a plain SHA-256 is enough (no salt,
    no KDF — there is nothing to brute-force) and it stays a single indexed
    equality lookup. The point is only that a leaked database dump — an admin
    export, a stray backup — hands over no live sessions.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def encrypt(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt; tolerate values that were stored as plaintext (legacy/dev)."""
    try:
        return _fernet.decrypt(token.encode()).decode()
    except Exception:  # noqa: BLE001
        return token


def sign(payload: dict) -> str:
    """Opaque, tamper-proof token carrying phase-1 state across the two-phase
    inference-broker round-trip (llm.py browser mode). Fernet is authenticated
    encryption, so the client cannot read or forge it."""
    return _fernet.encrypt(json.dumps(payload).encode()).decode()


def unsign(token: str, max_age: int = 3600) -> dict:
    """Verify + decode a token from sign(). Raises ValueError if forged/expired."""
    try:
        raw = _fernet.decrypt(token.encode(), ttl=max_age)
    except Exception as e:  # noqa: BLE001
        raise ValueError("invalid or expired token") from e
    return json.loads(raw)


# --------------------------------------------------------------------------- #
#  Client IP behind a reverse proxy
# --------------------------------------------------------------------------- #
# Behind Caddy every request's socket peer is Caddy itself, so an IP-keyed rate
# limit would degenerate into one bucket shared by the whole internet. To recover
# the real client we must read X-Forwarded-For — but only when the peer is a proxy
# we actually run, otherwise anyone could spoof the header and evade the limiter.
# TRUSTED_PROXY_CIDRS is empty by default: no proxy, no XFF, no spoofing.
_TRUSTED_PROXIES = [
    ipaddress.ip_network(c.strip(), strict=False)
    for c in os.environ.get("TRUSTED_PROXY_CIDRS", "").split(",") if c.strip()
]


def _trusted_proxy(ip: str) -> bool:
    try:
        return any(ipaddress.ip_address(ip) in net for net in _TRUSTED_PROXIES)
    except ValueError:
        return False


def client_ip(peer: str | None, forwarded_for: str | None) -> str:
    """The real client address, or `peer` when we can't trust the header.

    X-Forwarded-For is `client, proxy1, proxy2` — appended left-to-right, so only
    the rightmost entries are written by infrastructure we control. We walk from
    the right, skip our own proxies, and take the first hop that isn't one: that
    is the furthest address our own proxies actually observed. Everything left of
    it was supplied by the client and is forgeable.
    """
    peer = peer or "unknown"
    if not _TRUSTED_PROXIES or not forwarded_for or not _trusted_proxy(peer):
        return peer
    for hop in reversed([h.strip() for h in forwarded_for.split(",") if h.strip()]):
        if _trusted_proxy(hop):
            continue
        try:
            ipaddress.ip_address(hop)
        except ValueError:
            return peer  # garbage in the header — fall back to the socket peer
        return hop
    return peer


# --------------------------------------------------------------------------- #
#  Rate limiting / lockout (sliding window, in-memory)
# --------------------------------------------------------------------------- #
_hits: dict[str, list[float]] = {}
_fails: dict[str, list[float]] = {}


def allow(key: str, limit: int, window: float) -> bool:
    """True if `key` is under `limit` events in the last `window` seconds."""
    now = time.time()
    arr = [t for t in _hits.get(key, []) if t > now - window]
    if len(arr) >= limit:
        _hits[key] = arr
        return False
    arr.append(now)
    _hits[key] = arr
    return True


def record_fail(key: str) -> None:
    now = time.time()
    _fails[key] = [t for t in _fails.get(key, []) if t > now - 3600] + [now]


def locked(key: str, max_fails: int, window: float) -> bool:
    now = time.time()
    return len([t for t in _fails.get(key, []) if t > now - window]) >= max_fails


def clear_fails(key: str) -> None:
    _fails.pop(key, None)
