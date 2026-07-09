"""FastAPI dependencies for pulling the current user off the session cookie."""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth.security import read_session_token
from app.config import get_config
from app.database import get_db
from app.models import User

SESSION_COOKIE_NAME = "pi_nvr_session"


async def get_current_user_optional(request: Request) -> User | None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None

    cfg = get_config()
    remember_days = cfg.get("server.remember_me_days", 30)
    timeout_minutes = cfg.get("server.session_timeout_minutes", 60)

    # Try the short session window first, then the long "remember me" window.
    payload = read_session_token(token, max_age_seconds=timeout_minutes * 60)
    if payload is None:
        payload = read_session_token(token, max_age_seconds=remember_days * 86400)
        if payload is None or not payload.get("remember"):
            return None

    user_id = payload.get("uid")
    if user_id is None:
        return None

    db_gen = get_db()
    db: Session = next(db_gen)
    try:
        return db.get(User, user_id)
    finally:
        db_gen.close()


async def get_current_user(
    user: User | None = Depends(get_current_user_optional),
) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Cookie"},
        )
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin required")
    return user
