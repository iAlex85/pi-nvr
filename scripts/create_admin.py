#!/usr/bin/env python3
"""
Create the initial admin account interactively.

Run once after install.sh, or any time to add another admin:
    python3 scripts/create_admin.py

Deliberately NOT run automatically with a baked-in default password --
the "no default passwords" requirement means the installer prompts for
this instead of shipping admin/admin like so many NVR projects do.
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth.security import hash_password  # noqa: E402
from app.database import init_db, session_scope  # noqa: E402
from app.models import User  # noqa: E402


def main() -> int:
    init_db()
    username = input("Admin username: ").strip()
    if not username:
        print("Username cannot be empty.")
        return 1

    with session_scope() as db:
        if db.query(User).filter(User.username == username).first():
            print(f"User '{username}' already exists.")
            return 1

    password = getpass.getpass("Admin password (min 8 chars): ")
    if len(password) < 8:
        print("Password must be at least 8 characters.")
        return 1
    if len(password.encode("utf-8")) > 72:
        print("Password must be at most 72 bytes (bcrypt's hard limit).")
        return 1
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords do not match.")
        return 1

    with session_scope() as db:
        user = User(
            username=username,
            password_hash=hash_password(password),
            is_admin=True,
            must_change_password=False,
        )
        db.add(user)

    print(f"Admin user '{username}' created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
