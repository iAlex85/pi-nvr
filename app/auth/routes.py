from __future__ import annotations

import datetime
import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.dependencies import SESSION_COOKIE_NAME, get_current_user
from app.auth.security import create_session_token, hash_password, verify_password
from app.config import get_config
from app.database import get_db
from app.models import User

logger = logging.getLogger("pi_nvr.auth")
router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str
    remember_me: bool = False


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


@router.post("/login")
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == payload.username).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        # Same error for unknown-user vs bad-password: don't leak which one.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    user.last_login_at = datetime.datetime.now(datetime.timezone.utc)
    db.add(user)

    token = create_session_token(user.id, remember=payload.remember_me)
    cfg = get_config()
    max_age = (
        cfg.get("server.remember_me_days", 30) * 86400
        if payload.remember_me
        else cfg.get("server.session_timeout_minutes", 60) * 60
    )
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=cfg.get("server.https_enabled", False),
    )
    logger.info("User '%s' logged in", user.username)
    return {
        "username": user.username,
        "is_admin": user.is_admin,
        "must_change_password": user.must_change_password,
    }


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {
        "username": user.username,
        "is_admin": user.is_admin,
        "must_change_password": user.must_change_password,
    }


@router.post("/change-password")
def change_password(
    payload: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Current password incorrect")
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    db.add(user)
    logger.info("User '%s' changed their password", user.username)
    return {"ok": True}
