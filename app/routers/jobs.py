"""Job routes — submit, inspect, cancel, finish."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from ..deps import AdminUser, CurrentUser, DbSession
from ..models import Job, Node, User
from ..schemas import JobCreate, JobDetail, JobRead, MessageResponse
from ..services import log_event, now_utc, recompute_allocations

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

VALID_STATUSES = {"queued", "running", "completed", "failed", "cancelled"}


def _owned_job(db, job_id: int, user: User) -> Job:
    """Fetch a job the caller is allowed to see.

    Operators see their own team's jobs; admins see everything. Anything else is
    a 404 rather than a 403 — leaking the existence of another team's job IDs is
    a small thing, but there is no reason to do it.
    """
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if user.role != "admin" and job.owner.team != user.team:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


@router.get("", response_model=list[JobRead])
def list_jobs(
    db: DbSession,
    user: CurrentUser,
    job_status: str | None = Query(default=None, alias="status"),
    project: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[JobRead]:
    if job_status is not None and job_status not in VALID_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown status {job_status!r}. Valid: {sorted(VALID_STATUSES)}",
        )

    stmt = (
        select(Job)
        .options(selectinload(Job.owner))
        .order_by(Job.submitted_at.desc(), Job.id.desc())
    )
    if user.role != "admin":
        stmt = stmt.join(User, Job.owner_id == User.id).where(User.team == user.team)
    if job_status is not None:
        stmt = stmt.where(Job.status == job_status)
    if project is not None:
        stmt = stmt.where(Job.project == project)

    jobs = db.scalars(stmt.limit(limit).offset(offset)).all()
    return [JobRead.model_validate(job) for job in jobs]


@router.post("", response_model=JobDetail, status_code=status.HTTP_201_CREATED)
def submit_job(payload: JobCreate, db: DbSession, user: CurrentUser) -> JobDetail:
    """Submit a job. It lands in the queue; nothing starts until the scheduler runs."""
    if payload.span == "node":
        biggest = db.scalar(select(func.max(Node.total_gpus)).where(Node.healthy.is_(True)))
        if biggest is not None and payload.gpus > biggest:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"A gang request for {payload.gpus} GPUs cannot fit on any node "
                    f"(largest healthy node has {biggest}). Use span='any' to allow "
                    "the work to spread across machines."
                ),
            )

    job = Job(
        owner_id=user.id,
        name=payload.name,
        project=payload.project,
        command=payload.command,
        gpus=payload.gpus,
        gpu_model=payload.gpu_model,
        span=payload.span,
        priority=payload.priority,
        duration_minutes=payload.duration_minutes,
        status="queued",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    log_event(
        db,
        job,
        "submitted",
        f"Queued for {job.gpus} GPU(s), priority {job.priority}, "
        f"estimated {job.duration_minutes} min",
        actor=user.email,
    )
    db.commit()
    db.refresh(job)
    return JobDetail.model_validate(job)


@router.get("/{job_id}", response_model=JobDetail)
def get_job(job_id: int, db: DbSession, user: CurrentUser) -> JobDetail:
    job = db.scalar(
        select(Job)
        .options(selectinload(Job.events), selectinload(Job.owner))
        .where(Job.id == job_id)
    )
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if user.role != "admin" and job.owner.team != user.team:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return JobDetail.model_validate(job)


@router.post("/{job_id}/cancel", response_model=JobDetail)
def cancel_job(job_id: int, db: DbSession, user: CurrentUser) -> JobDetail:
    """Cancel a queued or running job and release its GPUs."""
    job = _owned_job(db, job_id, user)

    if job.status in {"completed", "failed", "cancelled"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job is already {job.status} and cannot be cancelled",
        )

    was_running = job.status == "running"
    job.status = "cancelled"
    job.ended_at = now_utc()
    job.last_reason = "Cancelled by user"
    log_event(
        db,
        job,
        "cancelled",
        f"Job cancelled while {'running' if was_running else 'queued'}",
        actor=user.email,
    )
    db.commit()
    recompute_allocations(db)
    db.commit()
    db.refresh(job)
    return JobDetail.model_validate(job)


@router.post("/{job_id}/complete", response_model=JobDetail)
def complete_job(
    job_id: int,
    db: DbSession,
    user: CurrentUser,
    exit_code: int = Query(default=0, ge=0, le=255),
) -> JobDetail:
    """Report a job finished. The runner calls this; exit_code decides success.

    Kept as an explicit endpoint rather than a timer so the numbers on the
    dashboard always come from something that actually reported in.
    """
    job = _owned_job(db, job_id, user)

    if job.status != "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Only running jobs can be completed (this one is {job.status})",
        )

    job.exit_code = exit_code
    job.status = "completed" if exit_code == 0 else "failed"
    job.ended_at = now_utc()
    job.last_reason = f"Exited with code {exit_code}"
    log_event(
        db,
        job,
        job.status,
        f"Runner reported exit code {exit_code} after {job.runtime_minutes} min",
        actor=user.email,
    )
    db.commit()
    recompute_allocations(db)
    db.commit()
    db.refresh(job)
    return JobDetail.model_validate(job)


@router.delete("/{job_id}", response_model=MessageResponse)
def delete_job(job_id: int, db: DbSession, admin: AdminUser) -> MessageResponse:
    """Permanently remove a finished job and its history. Admin only."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job.status == "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cancel the job before deleting it — a running job still holds GPUs",
        )

    db.delete(job)
    db.commit()
    return MessageResponse(message=f"Job {job_id} deleted")
