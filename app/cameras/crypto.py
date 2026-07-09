"""
Encrypt camera/ONVIF passwords at rest in the SQLite DB so a copied .db
file or casual file browse doesn't leak plaintext credentials. This is not
a defense against root-level access to the running host -- it protects the
DB file in isolation (backups, misconfigured file shares, etc).

Key is generated once by install.sh into the systemd environment file
(PI_NVR_DB_SECRET) and never stored alongside the database itself.
"""
from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken


def _get_fernet() -> Fernet:
    secret = os.environ.get("PI_NVR_DB_SECRET")
    if not secret:
        # Dev fallback: derive a stable key from a fixed string so local
        # runs don't crash. install.sh always sets a real random secret
        # in production; this path should never be hit there.
        secret = "pi-nvr-dev-only-insecure-key"
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def encrypt(value: str | None) -> str | None:
    if not value:
        return None
    return _get_fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _get_fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return None
