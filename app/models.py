"""SQLAlchemy models.

Deliberately flat: a User owns Jobs, a Job runs on zero or one Node, and every
state change writes a JobEvent. The audit trail is the product — if a scheduler
decision cannot be explained later, the platform is untrustworthy.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base, UTCDateTime


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="operator")
    team: Mapped[str] = mapped_column(String(80), nullable=False, default="platform")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    jobs: Mapped[list[Job]] = relationship(back_populates="owner")


class Node(Base):
    """A physical (or simulated) machine contributing GPUs to a cluster."""

    __tablename__ = "nodes"
    __table_args__ = (UniqueConstraint("cluster_name", "name", name="uq_node_cluster_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    cluster_name: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    gpu_model: Mapped[str] = mapped_column(String(64), nullable=False)
    total_gpus: Mapped[int] = mapped_column(Integer, nullable=False)
    memory_gb: Mapped[int] = mapped_column(Integer, nullable=False, default=80)
    region: Mapped[str] = mapped_column(String(32), nullable=False, default="local")
    healthy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Cached count of GPUs held by running jobs. Derived from jobs, but stored so
    # the node list is one query instead of a per-node aggregate.
    allocated_gpus: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    jobs: Mapped[list[Job]] = relationship(back_populates="node")

    @property
    def free_gpus(self) -> int:
        return max(0, self.total_gpus - self.allocated_gpus)

    @property
    def utilisation(self) -> float:
        """Fraction of this node's GPUs currently held, 0.0–1.0."""
        if not self.total_gpus:
            return 0.0
        return min(1.0, self.allocated_gpus / self.total_gpus)


class Job(Base):
    """A unit of work asking for GPUs."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_status_priority", "status", "priority"),
        Index("ix_jobs_submitted_at", "submitted_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    project: Mapped[str] = mapped_column(String(80), nullable=False, default="default")
    command: Mapped[str] = mapped_column(Text, nullable=False, default="")

    gpus: Mapped[int] = mapped_column(Integer, nullable=False)
    gpu_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    span: Mapped[str] = mapped_column(String(16), nullable=False, default="node")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", index=True)
    node_id: Mapped[int | None] = mapped_column(
        ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True
    )

    submitted_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    rate_override: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")

    owner: Mapped[User] = relationship(back_populates="jobs")
    node: Mapped[Node | None] = relationship(back_populates="jobs")
    events: Mapped[list[JobEvent]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="JobEvent.id"
    )

    @property
    def runtime_minutes(self) -> int:
        """Actual runtime, or the estimate while the job is still queued."""
        if self.started_at is None:
            return 0
        end = self.ended_at or utcnow()
        delta = end - self.started_at
        return max(0, int(delta.total_seconds() // 60))


class JobEvent(Base):
    """Append-only history for a job. Never updated, only inserted."""

    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    event: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    actor: Mapped[str] = mapped_column(String(120), nullable=False, default="system")
    at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)

    job: Mapped[Job] = relationship(back_populates="events")
