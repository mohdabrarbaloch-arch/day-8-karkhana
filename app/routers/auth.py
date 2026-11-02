"""Authentication routes: register, log in, and read the current user."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import func, select

from ..config import get_settings
from ..deps import CurrentUser, DbSession
from ..models import User
from ..schemas import LoginRequest, TokenResponse, UserCreate, UserRead
from ..security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: DbSession, request: Request) -> TokenResponse:
    """Create an account and return a token, so the client can go straight in.

    The very first account on a fresh install becomes the admin; everyone after
    that is a regular operator. That keeps a demo usable without a seed step,
    and it is not a privilege-escalation hole because it only ever applies when
    the users table is empty.
    """
    email = payload.email.lower().strip()
    existing = db.scalar(select(User).where(func.lower(User.email) == email))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists",
        )

    is_first_user = db.scalar(select(func.count()).select_from(User)) == 0
    user = User(
        email=email,
        full_name=payload.full_name.strip(),
        password_hash=hash_password(payload.password),
        role="admin" if is_first_user else "operator",
        team=payload.team.strip(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(str(user.id), user.role)
    return TokenResponse(
        access_token=token,
        expires_in=settings.access_token_expire_minutes * 60,
        user=UserRead.model_validate(user),
    )


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: DbSession, request: Request) -> TokenResponse:
    """Exchange credentials for a bearer token.

    The error message is identical whether the email is unknown or the password
    is wrong — telling an attacker which one it was is how account enumeration
    starts.
    """
    email = payload.email.lower().strip()
    user = db.scalar(select(User).where(func.lower(User.email) == email))

    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been deactivated",
        )

    token = create_access_token(str(user.id), user.role)
    return TokenResponse(
        access_token=token,
        expires_in=settings.access_token_expire_minutes * 60,
        user=UserRead.model_validate(user),
    )


@router.get("/me", response_model=UserRead)
def me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)
