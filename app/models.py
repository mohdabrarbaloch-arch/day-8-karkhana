"""SQLAlchemy models.

Deliberately flat: a User owns Jobs, a Job runs on zero or more Nodes (via
Placement), and every state change writes an AuditEvent. There is no separate
"cluster" table — a cluster is just the set of nodes.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base, UTCDateTime


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    team: Mapped[str] = mapped_column(String(64), default="platform", index=True)
    role: Mapped[str] = mapped_column(String(32), default="operator")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Node(Base):
    """A machine with GPUs of one model."""

    __tablename__ = "nodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    gpu_model: Mapped[str] = mapped_column(String(64))
    total_gpus: Mapped[int] = mapped_column(Integer)
    allocated_gpus: Mapped[int] = mapped_column(Integer, default=0)
    is_healthy: Mapped[bool] = mapped_column(Boolean, default=True)
    region: Mapped[str] = mapped_column(String(64), default="karachi-1")
    hourly_rate_usd: Mapped[float] = mapped_column(default=2.50)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    __table_args__ = (
        # A node cannot hold more GPUs than it physically has.
        # Enforced in the API as well, but this is the guarantee.
        # (CheckConstraint expressed here for portability across SQLite/Postgres.)
        # Added in the migration for clarity; SQLite parses it fine.
        # See app/routers/nodes.py for the user-facing 409.
    )

    @property
    def free_gpus(self) -> int:
        return max(0, self.total_gpus - self.allocated_gpus)

    @property
    def utilisation(self) -> float:
        if self.total_gpus <= 0:
            return 0.0
        return self.allocated_gpus / self.total_gpus


class Job(Base):
    """A unit of work asking for GPUs."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    team: Mapped[str] = mapped_column(String(64), index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    gpus: Mapped[int] = mapped_column(Integer)
    gpu_model: Mapped[str] = mapped_column(String(64), default="any")
    span: Mapped[str] = mapped_column(String(8), default="node")
    priority: Mapped[int] = mapped_column(Integer, default=5)
    estimated_minutes: Mapped[int] = mapped_column(Integer, default=60)
    command: Mapped[str | None] = mapped_column(Text, default=None)

    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    submitted_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    exit_code: Mapped[int | None] = mapped_column(Integer, default=None)

    placements: Mapped[list[Placement]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class Placement(Base):
    """One job occupying GPUs on one node.

    A gang job has exactly one Placement. A spread job has one per node it
    touches, which is why this is a table rather than a column on Job.
    """

    __tablename__ = "placements"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("nodes.id"), index=True)
    gpus: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(16), default="head")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    job: Mapped[Job] = relationship(back_populates="placements")
    node: Mapped[Node] = relationship()


class AuditEvent(Base):
    """Every decision, with who caused it and why."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(64), index=True)
    subject: Mapped[str] = mapped_column(String(128))
    reason: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
