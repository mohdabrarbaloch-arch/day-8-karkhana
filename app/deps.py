"""Shared FastAPI dependencies: authentication and role checks."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .database import get_db
from .models import User
from .security import decode_access_token

# auto_error=False so a missing header produces our own 401 with a WWW-Authenticate
# challenge rather than FastAPI's bare 403.
bearer_scheme = HTTPBearer(auto_error=False)

DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> User:
    unauthorised = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None or not credentials.credentials:
        raise unauthorised

    claims = decode_access_token(credentials.credentials)
    if claims is None:
        raise unauthorised

    subject = claims.get("sub")
    if not subject:
        raise unauthorised

    user = db.get(User, int(subject))
    if user is None or not user.is_active:
        raise unauthorised
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_admin(user: CurrentUser) -> User:
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires an administrator",
        )
    return user


AdminUser = Annotated[User, Depends(require_admin)]
