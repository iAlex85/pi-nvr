"""Password hashing + signed session tokens.

Sessions are stateless signed cookies (itsdangerous), not DB-backed, so a
restart doesn't kick everyone out and there's no session table to prune.
The signature includes an expiry timestamp, enforced server-side on read.

Password hashing calls the `bcrypt` library directly rather than going
through passlib's CryptContext. passlib has been unmaintained since 2020
and its bcrypt backend detection breaks under modern versions of the
`bcrypt` package (it probes a `bcrypt.__about__` attribute that newer
`bcrypt` releases removed, which raises a confusing "password cannot be
longer than 72 bytes" error that has nothing to do with the actual
password). Calling bcrypt directly sidesteps that fragile compatibility
layer entirely.
"""
from __future__ import annotations

import os
import secrets
import time

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

# bcrypt's algorithm has a hard 72-byte input limit (a property of the
# cipher, not a passlib quirk). We enforce this explicitly at the
# boundaries where a password is accepted (see auth/routes.py and
# scripts/create_admin.py) so a user gets a clear "too long" message
# instead of a stack trace.
BCRYPT_MAX_BYTES = 72


def hash_password(plain: str) -> str:
    if len(plain.encode("utf-8")) > BCRYPT_MAX_BYTES:
        raise ValueError(f"Password must be at most {BCRYPT_MAX_BYTES} bytes")
    hashed = bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def _get_secret() -> str:
    """Session signing secret. Generated once by install.sh into the
    systemd environment file (PI_NVR_SESSION_SECRET); falls back to a
    process-local random secret in dev so `uvicorn app.main:app` still
    works without extra setup (sessions just won't survive a restart)."""
    secret = os.environ.get("PI_NVR_SESSION_SECRET")
    if secret:
        return secret
    return _dev_fallback_secret()


_dev_secret_cache: str | None = None


def _dev_fallback_secret() -> str:
    global _dev_secret_cache
    if _dev_secret_cache is None:
        _dev_secret_cache = secrets.token_urlsafe(32)
    return _dev_secret_cache


def create_session_token(user_id: int, remember: bool = False) -> str:
    serializer = URLSafeTimedSerializer(_get_secret(), salt="pi-nvr-session")
    return serializer.dumps({"uid": user_id, "remember": remember, "iat": time.time()})


def read_session_token(token: str, max_age_seconds: int) -> dict | None:
    serializer = URLSafeTimedSerializer(_get_secret(), salt="pi-nvr-session")
    try:
        return serializer.loads(token, max_age=max_age_seconds)
    except (BadSignature, SignatureExpired):
        return None
