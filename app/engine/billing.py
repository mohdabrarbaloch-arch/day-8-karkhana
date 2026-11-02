"""Chargeback arithmetic.

The rule the whole platform rests on:

    cost = gpus x (runtime_minutes / 60) x rate_per_gpu_hour

Everything here is plain multiplication so a finance team can reproduce any
invoice by hand. ``runtime_minutes`` is truncated to whole minutes by the
caller (``Job.runtime_minutes``); this module then applies a **one-minute
floor**, because a 40-second job still occupied the silicon. It does not
round partial minutes up to the next minute: a 61-second job is billed at the
floor of 1 minute, not at 2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

MINUTES_PER_HOUR = 60
MINIMUM_BILLED_MINUTES = 1


def gpu_hours(gpus: int, runtime_minutes: int) -> float:
    """GPU-hours consumed, with a one-minute floor."""
    if gpus < 1:
        raise ValueError("gpus must be at least 1")
    billed = max(runtime_minutes, MINIMUM_BILLED_MINUTES)
    return gpus * (billed / MINUTES_PER_HOUR)


def cost_of(gpus: int, runtime_minutes: int, rate_per_gpu_hour: float) -> Decimal:
    """Exact cost in USD, rounded to cents."""
    if rate_per_gpu_hour < 0:
        raise ValueError("rate cannot be negative")
    hours = Decimal(str(gpu_hours(gpus, runtime_minutes)))
    rate = Decimal(str(rate_per_gpu_hour))
    return (hours * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass(slots=True)
class ChargeableJob:
    job_id: int
    name: str
    team: str
    project: str
    gpus: int
    runtime_minutes: int
    rate_per_gpu_hour: float


@dataclass(slots=True)
class TeamCharge:
    team: str
    jobs: int = 0
    gpu_hours: float = 0.0
    cost: Decimal = Decimal("0.00")
    job_ids: list[int] = field(default_factory=list)


def chargeback(jobs: list[ChargeableJob]) -> dict[str, TeamCharge]:
    """Aggregate jobs into per-team charges.

    Returned as a dict keyed by team so the caller can look one up in O(1); the
    insertion order follows first appearance, which is what the dashboard wants.
    """
    totals: dict[str, TeamCharge] = {}
    for job in jobs:
        entry = totals.setdefault(job.team, TeamCharge(team=job.team))
        entry.jobs += 1
        entry.gpu_hours = round(entry.gpu_hours + gpu_hours(job.gpus, job.runtime_minutes), 4)
        entry.cost = (
            entry.cost + cost_of(job.gpus, job.runtime_minutes, job.rate_per_gpu_hour)
        ).quantize(Decimal("0.01"))
        entry.job_ids.append(job.job_id)
    return totals
