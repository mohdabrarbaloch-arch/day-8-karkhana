"""Password hashing and JWT helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from .config import get_settings

settings = get_settings()

# bcrypt silently truncates at 72 bytes; reject anything longer instead of
# letting two different passwords hash identically.
MAX_PASSWORD_BYTES = 72


def hash_password(password: str) -> str:
    payload = password.encode("utf-8")
    if len(payload) > MAX_PASSWORD_BYTES:
        raise ValueError(f"password must be at most {MAX_PASSWORD_BYTES} bytes")
    return bcrypt.hashpw(payload, bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        payload = password.encode("utf-8")
        if len(payload) > MAX_PASSWORD_BYTES:
            return False
        return bcrypt.checkpw(payload, password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        # A malformed hash must read as "wrong password", never as a crash.
        return False


def create_access_token(subject: str, role: str, expires_minutes: int | None = None) -> str:
    minutes = (
        expires_minutes if expires_minutes is not None else settings.access_token_expire_minutes
    )
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=minutes)).timestamp()),
    }
    return jwt.encode(claims, settings.secret_key, algorithm=settings.algorithm)


def decode_access_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except jwt.PyJWTError:
        return None
