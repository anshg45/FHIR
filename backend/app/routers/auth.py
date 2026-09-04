"""Authentication: JWT login, profile, password change."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import log_action
from ..config import settings
from ..database import get_db
from ..models import User
from ..schemas import ChangePasswordRequest, LoginRequest, TokenResponse, UserOut
from ..security import (
    client_ip,
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = (
        db.execute(select(User).where(User.email == payload.email.lower())).scalars().first()
    )
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="This account has been deactivated"
        )

    user.last_login = datetime.now(timezone.utc)
    db.commit()
    log_action(
        db,
        action="LOGIN",
        entity_type="User",
        entity_id=user.id,
        user=user,
        new_value={"email": user.email, "role": user.role},
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return TokenResponse(
        access_token=create_access_token(user),
        expires_in_minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES,
        user=UserOut.model_validate(user),
    )


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return UserOut.model_validate(user)


@router.post("/refresh", response_model=TokenResponse)
def refresh(user: User = Depends(get_current_user)):
    return TokenResponse(
        access_token=create_access_token(user),
        expires_in_minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES,
        user=UserOut.model_validate(user),
    )


@router.post("/change-password")
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    user.password_hash = hash_password(payload.new_password)
    db.commit()
    log_action(
        db,
        action="CHANGE_PASSWORD",
        entity_type="User",
        entity_id=user.id,
        user=user,
        new_value={"password_changed": True},
        ip_address=client_ip(request),
    )
    return {"success": True, "message": "Password updated"}


@router.post("/logout")
def logout(request: Request, user: User = Depends(get_current_user),
           db: Session = Depends(get_db)):
    log_action(
        db, action="LOGOUT", entity_type="User", entity_id=user.id, user=user,
        ip_address=client_ip(request),
    )
    return {"success": True, "message": "Logged out. Discard the access token client-side."}
