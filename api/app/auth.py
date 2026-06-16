"""Password hashing + TOTP 2FA helpers (ACCOUNTS_AND_ONBOARDING.md)."""
import io

import bcrypt
import pyotp
import segno

ISSUER = "OneLife"


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


def qr_svg(uri: str) -> str:
    """Inline SVG of the otpauth URI, for the client to render the QR directly."""
    buf = io.BytesIO()
    segno.make(uri).save(buf, kind="svg", scale=4, border=2)
    return buf.getvalue().decode()
