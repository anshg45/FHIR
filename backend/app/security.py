"""Password hashing, JWT issuance and RBAC dependencies."""
from datetime import datetime, timedelta, timezone
from typing import Iterable

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .models import User

bearer_scheme = HTTPBearer(auto_error=False)


# ------------------------------------------------------------------ passwords
def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ----------------------------------------------------------------------- JWT
def create_access_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.id,
        "email": user.email,
        "role": user.role,
        "name": user.name,
        "site_id": user.site_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)).timestamp()),
        "iss": "aiia-ctms",
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except JWTError as exc:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid or expired token: {exc}"
        )


# ---------------------------------------------------------------- dependencies
def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if creds is None or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    payload = decode_token(creds.credentials)
    user = db.get(User, payload.get("sub"))
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or deactivated"
        )
    # attach request metadata for the audit trail
    request.state.current_user = user
    return user


class RequireRoles:
    """Server-side role enforcement dependency.

    Usage: `user: User = Depends(RequireRoles("admin", "pi"))`
    """

    def __init__(self, *roles: str):
        self.roles = set(roles)

    def __call__(self, user: User = Depends(get_current_user)) -> User:
        if self.roles and user.role not in self.roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Role '{user.role}' is not permitted to perform this action. "
                    f"Allowed roles: {sorted(self.roles)}"
                ),
            )
        return user


def has_role(user: User, roles: Iterable[str]) -> bool:
    return user.role in set(roles)


def client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None
