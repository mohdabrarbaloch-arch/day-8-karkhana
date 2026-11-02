"""Shared fixtures. Every test gets its own SQLite file, never a shared one."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app


@pytest.fixture()
 def _unused() -> None:  # pragma: no cover - placeholder removed below
    raise NotImplementedError
