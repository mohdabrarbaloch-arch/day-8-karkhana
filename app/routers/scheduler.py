"""Scheduler routes — run the placement pass and read the queue.

The scheduling run is idempotent: calling it twice with nothing changed is a
no-op, not a double-booking. Every accepted placement writes two entries to the
job's history (scheduled, then started) so the dashboard can show exactly why a
job moved when it did.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from ..config import get_settings
from ..deps import CurrentUser, DbSession
from ..engine.scheduler import Deferral, plan
from ..models import Job, Node
from ..schemas import (
    DecisionRead,
    DeferralRead,
    MessageResponse,
    ScheduleResult,
)
from ..services import (
    log_event,
    now_utc,
    queued_jobs,
    recompute_allocations,
    running_jobs,
    snapshot,
)

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])
settings = get_settings()


@router.get("/queue", response_model=list[DeferralRead])
def view_queue(db: DbSession, user: CurrentUser) -> list[DeferralRead]:
    """The queue in the order it will be considered, with each job's reason."""
    queued = queued_jobs(db)
    if not queued:
        return []

    now = now_utc()
    ordered = sorted(
        queued,
        key=lambda job: (
            -job.effective_priority(now, settings.aging_minutes),
            job.submitted_at,
            job.id,
        ),
    )
    return [
        DeferralRead(
            job_id=job.id,
            reason=(
                f"priority {job.priority} → effective "
                f"{job.effective_priority(now, settings.aging_minutes)} after "
                f"{int((now - job.submitted_at).total_seconds() // 60)} min waiting"
            ),
        )
        for job in ordered
    ]


@router.post("/run", response_model=ScheduleResult)
def run_scheduler(db: DbSession, user: CurrentUser) -> ScheduleResult:
    """Run one scheduling pass.

    Kept synchronous and explicit rather than a background loop. In production
    you would put this behind a cron or a worker tick; here it means the demo can
    show cause and effect — press the button, watch the placement.
    """
    nodes_snapshot = snapshot(db)
    if not nodes_snapshot.nodes:
        return ScheduleResult(
            scheduled=[],
            deferred=[],
            unplaceable=[],
            reservation_minutes=None,
            head_job_id=None,
            message="No nodes registered yet — add a node before scheduling.",
        )

    # Make sure the cached allocations match reality before we plan against them.
    recompute_allocations(db)
    db.commit()
    nodes_snapshot = snapshot(db)

    result = plan(
        nodes=nodes_snapshot.nodes,
        queue=queued_jobs(db),
        running=running_jobs(db),
        now=now_utc(),
        aging_minutes=settings.aging_minutes,
        enable_backfill=settings.enable_backfill,
    )

    scheduled_reads: list[DecisionRead] = []
    started = 0
    for decision in result.decisions:
        job = db.get(Job, decision.job_id)
        node = db.scalar(select(Node).where(Node.name == decision.node))
        if job is None or node is None:
            # A node vanished between planning and persistence. Skip rather than
            # book GPUs that no longer exist.
            continue

        job.status = "running"
        job.node_id = node.id
        job.started_at = now_utc()
        job.last_reason = f"Started on {node.name} ({decision.reason})"
        log_event(
            db,
            job,
            "scheduled",
            f"Placed on {node.name}: {decision.gpus} GPU(s), reason={decision.reason}",
            actor=user.email,
        )
        log_event(
            db,
            job,
            "running",
            f"Started on {node.name}",
            actor="scheduler",
        )
        scheduled_reads.append(
            DecisionRead(
                job_id=decision.job_id,
                node=decision.node,
                gpus=decision.gpus,
                reason=decision.reason,
            )
        )
        started += 1

    # Persist the *why* for everything we chose not to start. A scheduler that
    # silently ignores a job is impossible to debug at 3am.
    for deferral in result.deferred:
        job = db.get(Job, deferral.job_id)
        if job is not None:
            job.last_reason = deferral.reason
            log_event(db, job, "deferred", deferral.reason, actor="scheduler")

    for unplaceable in result.unplaceable:
        job = db.get(Job, unplaceable.job_id)
        if job is not None:
            job.last_reason = unplaceable.reason
            log_event(db, job, "unplaceable", unplaceable.reason, actor="scheduler")

    db.commit()
    recompute_allocations(db)
    db.commit()

    if started:
        message = f"Started {started} job(s)."
        if result.deferred:
            message += f" {len(result.deferred)} still waiting."
        if result.unplaceable:
            message += f" {len(result.unplaceable)} cannot fit any node."
    elif result.unplaceable and not result.deferred:
        message = "Nothing started — every queued job is too large for the cluster."
    elif result.deferred:
        message = "Nothing started — no capacity available yet."
    else:
        message = "Queue is empty; nothing to schedule."

    return ScheduleResult(
        scheduled=scheduled_reads,
        deferred=[DeferralRead(job_id=d.job_id, reason=d.reason) for d in result.deferred],
        unplaceable=[DeferralRead(job_id=d.job_id, reason=d.reason) for d in result.unplaceable],
        reservation_minutes=result.reservation_minutes,
        head_job_id=result.head_job_id,
        message=message,
    )


@router.post("/reset", response_model=MessageResponse)
def reset_scheduler(db: DbSession, user: CurrentUser) -> MessageResponse:
    """Return every running job to the queue. A demo convenience, and a genuine
    recovery tool if the allocator ever gets out of step with reality."""
    running = db.scalars(select(Job).where(Job.status == "running")).all()
    for job in running:
        job.status = "queued"
        job.node_id = None
        job.started_at = None
        job.last_reason = "Returned to queue by scheduler reset"
        log_event(db, job, "requeued", job.last_reason, actor=user.email)
    db.commit()
    recompute_allocations(db)
    db.commit()
    return MessageResponse(message=f"{len(running)} job(s) returned to the queue.")


@router.get("/decisions/{job_id}", response_model=list[DeferralRead])
def explain_job(job_id: int, db: DbSession, user: CurrentUser) -> list[DeferralRead]:
    """Every scheduling decision recorded against one job, oldest first."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if user.role != "admin" and job.owner.team != user.team:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    return [
        DeferralRead(job_id=event.job_id, reason=f"{event.event}: {event.message}")
        for event in job.events
    ]


__all__ = ["router", "Deferral"]
