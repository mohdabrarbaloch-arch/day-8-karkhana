"""Shared fixtures. Every test gets its own in-memory database."""

from __future__ import annotations

import os

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", "sqlite://")  # in-memory
os.environ.setdefault("SECRET_KEY", "test-secret-not-used-anywhere-real")
os.environ.setdefault("ALLOW_DEMO_SEED", "true")

from collections.abc import Iterator  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture
def db() -> Iterator[Session]:
    """A blank in-memory database, isolated per test."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    """A client wired to the test database instead of the real one."""

    def override() -> Iterator[Session]:
        yield db

    app.dependency_overrides[get_db] = override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def auth(client: TestClient) -> dict[str, str]:
    """Register a fresh admin and return the Authorization header."""
    response = client.post(
        "/api/auth/register",
        json={
            "email": "test@karkhana.dev",
            "password": "TestPass123",
            "full_name": "Test User",
            "team": "platform",
        },
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
