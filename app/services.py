"""Shared helpers for turning engine output into persisted state."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .engine.capacity import ClusterSnapshot, NodeCapacity, RunningJob
from .engine.scheduler import QueuedJob
from .models import Job, JobEvent, Node

ACTIVE_STATUSES = ("queued", "running")


def now_utc() -> datetime:
    return datetime.now(UTC)


def log_event(db: Session, job: Job, event: str, message: str, actor: str) -> JobEvent:
    """Append to a job's history. Every state change goes through here."""
    entry = JobEvent(job_id=job.id, event=event, message=message, actor=actor)
    db.add(entry)
    return entry


def snapshot(db: Session) -> ClusterSnapshot:
    """Read the cluster's capacity as the scheduler sees it."""
    nodes = db.scalars(select(Node).order_by(Node.name)).all()
    return ClusterSnapshot(
        nodes=[
            NodeCapacity(
                name=node.name,
                gpu_model=node.gpu_model,
                total_gpus=node.total_gpus,
                allocated_gpus=node.allocated_gpus,
                healthy=node.healthy,
                region=node.region,
                cluster_name=node.cluster_name,
            )
            for node in nodes
        ]
    )


def running_jobs(db: Session) -> list[RunningJob]:
    """Running jobs with their remaining time, for backfill reservations."""
    rows = db.scalars(select(Job).where(Job.status == "running")).all()
    now = now_utc()
    result: list[RunningJob] = []
    for job in rows:
        if job.node_id is None:
            continue
        node = db.get(Node, job.node_id)
        if node is None:
            continue
        if job.started_at is None:
            remaining = job.duration_minutes
        else:
            elapsed = int((now - job.started_at).total_seconds() // 60)
            remaining = max(0, job.duration_minutes - elapsed)
        result.append(
            RunningJob(job_id=job.id, node=node.name, gpus=job.gpus, remaining_minutes=remaining)
        )
    return result


def queued_jobs(db: Session) -> list[QueuedJob]:
    rows = db.scalars(
        select(Job).where(Job.status == "queued").order_by(Job.submitted_at, Job.id)
    ).all()
    return [
        QueuedJob(
            id=job.id,
            name=job.name,
            gpus=job.gpus,
            priority=job.priority,
            duration_minutes=job.duration_minutes,
            submitted_at=job.submitted_at,
            gpu_model=job.gpu_model,
            span=job.span,
            project=job.project,
        )
        for job in rows
    ]


def recompute_allocations(db: Session) -> None:
    """Rebuild each node's cached allocation from the running jobs.

    Cheap and idempotent, which matters more than clever: this is the function
    that keeps the dashboard honest after a crash or a manual database fix.
    """
    nodes = db.scalars(select(Node)).all()
    held: dict[int, int] = {}
    for job in db.scalars(select(Job).where(Job.status == "running")).all():
        if job.node_id is not None:
            held[job.node_id] = held.get(job.node_id, 0) + job.gpus
    for node in nodes:
        node.allocated_gpus = min(held.get(node.id, 0), node.total_gpus)
