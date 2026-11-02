"""Cluster dashboard and chargeback reporting."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Query
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from ..config import get_settings
from ..deps import CurrentUser, DbSession
from ..engine.billing import ChargeableJob, chargeback, cost_of, gpu_hours
from ..models import Job, Node, User
from ..schemas import ChargebackReport, Dashboard, NodeUtilisation, TeamCharge

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
settings = get_settings()


@router.get("", response_model=Dashboard)
def dashboard(db: DbSession, user: CurrentUser) -> Dashboard:
    """One screen: what the cluster has, and what it did today."""
    nodes = db.scalars(select(Node).order_by(Node.name)).all()
    total = sum(node.total_gpus for node in nodes)
    allocated = sum(min(node.allocated_gpus, node.total_gpus) for node in nodes)
    healthy = sum(1 for node in nodes if node.healthy)

    def count(status: str) -> int:
        return int(
            db.scalar(select(func.count()).select_from(Job).where(Job.status == status)) or 0
        )

    day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    finished_today = db.scalars(
        select(Job)
        .options(selectinload(Job.owner))
        .where(Job.ended_at.is_not(None), Job.ended_at >= day_start)
    ).all()

    today_hours = 0.0
    today_cost = Decimal("0.00")
    for job in finished_today:
        today_hours += gpu_hours(job.gpus, job.runtime_minutes)
        rate = (
            job.rate_override
            if job.rate_override is not None
            else settings.default_cost_per_gpu_hour
        )
        today_cost += cost_of(job.gpus, job.runtime_minutes, rate)

    return Dashboard(
        total_gpus=total,
        allocated_gpus=allocated,
        free_gpus=max(0, total - allocated),
        utilisation_percent=round((allocated / total * 100) if total else 0.0, 1),
        nodes=len(nodes),
        healthy_nodes=healthy,
        jobs_queued=count("queued"),
        jobs_running=count("running"),
        jobs_completed=count("completed"),
        jobs_failed=count("failed"),
        gpu_hours_today=round(today_hours, 2),
        cost_today=float(today_cost),
        per_node=[
            NodeUtilisation(
                name=node.name,
                gpu_model=node.gpu_model,
                total_gpus=node.total_gpus,
                allocated_gpus=node.allocated_gpus,
                free_gpus=node.free_gpus,
                utilisation_percent=round(node.utilisation * 100, 1),
                healthy=node.healthy,
                region=node.region,
            )
            for node in nodes
        ],
    )


@router.get("/chargeback", response_model=ChargebackReport)
def chargeback_report(
    db: DbSession,
    user: CurrentUser,
    days: int = Query(default=30, ge=1, le=365),
) -> ChargebackReport:
    """Per-team spend for finished jobs in the window.

    Admins see every team; an operator sees only their own, because a team's
    spend is not another team's business.
    """
    period_end = datetime.now(UTC)
    period_start = period_end - timedelta(days=days)

    stmt = (
        select(Job)
        .options(selectinload(Job.owner))
        .where(
            Job.ended_at.is_not(None),
            Job.ended_at >= period_start,
            Job.status.in_(("completed", "failed", "cancelled")),
        )
    )
    jobs = db.scalars(stmt).all()

    chargeable: list[ChargeableJob] = []
    for job in jobs:
        team = job.owner.team if job.owner else "unknown"
        if user.role != "admin" and team != user.team:
            continue
        chargeable.append(
            ChargeableJob(
                job_id=job.id,
                name=job.name,
                team=team,
                project=job.project,
                gpus=job.gpus,
                runtime_minutes=job.runtime_minutes,
                rate_per_gpu_hour=(
                    job.rate_override
                    if job.rate_override is not None
                    else settings.default_cost_per_gpu_hour
                ),
            )
        )

    totals = chargeback(chargeable)
    total_hours = round(sum(entry.gpu_hours for entry in totals.values()), 2)
    total_cost = sum((entry.cost for entry in totals.values()), Decimal("0.00"))

    return ChargebackReport(
        period_start=period_start,
        period_end=period_end,
        total_gpu_hours=total_hours,
        total_cost=float(total_cost),
        teams=sorted(
            (
                TeamCharge(
                    team=entry.team,
                    jobs=entry.jobs,
                    gpu_hours=entry.gpu_hours,
                    cost=float(entry.cost),
                    job_ids=entry.job_ids,
                )
                for entry in totals.values()
            ),
            key=lambda entry: entry.cost,
            reverse=True,
        ),
    )


@router.get("/teams", response_model=list[str])
def list_teams(db: DbSession, user: CurrentUser) -> list[str]:
    """Team names, so the UI can populate a filter without guessing."""
    rows = db.scalars(select(User.team).distinct().order_by(User.team)).all()
    return list(rows)
