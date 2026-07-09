"""Password hashing + signed session tokens.

Sessions are stateless signed cookies (itsdangerous), not DB-backed, so a
restart doesn't kick everyone out and there's no session table to prune.
The signature includes an expiry timestamp, enforced server-side on read.
"""
from __future__ import annotations

import os
import secrets
import time

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(plain, hashed)
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
