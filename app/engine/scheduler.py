"""The scheduler: priority ordering, ageing, and EASY backfill.

This is the part of a GPU platform that people actually argue about, so the
rules are written out plainly rather than buried in SQL.

Ordering
--------
A queued job's *effective* priority is its declared priority plus one point for
every ``aging_minutes`` spent waiting, capped at ``MAX_EFFECTIVE_PRIORITY``.
Without ageing, a steady trickle of high-priority submissions starves the queue
forever; with it, a patient job eventually outranks everything.

Placement
---------
* ``span="node"`` (the default) is a *gang* request: all ``gpus`` must land on
  one node. A 16-GPU kernel split across two 8-GPU boxes cannot use NVLink, so
  honouring that constraint is the difference between a correct answer and a
  stalled training run.
* ``span="any"`` may be spread across nodes — correct for embarrassingly
  parallel data work.

Backfill
--------
The head of the queue is the job that matters. If it can start now, it does and
everything behind it is fit-tested against the remaining capacity. If it
*cannot* start now, we compute its reservation — the minute at which enough
GPUs will have freed for it — and then allow later jobs to start immediately
**only if they will finish before that reservation**. That is EASY backfill:
it fills idle silicon without ever delaying the job at the front.

A job too large for any single node is not scheduled at all; it is returned as
``unplaceable`` with a reason, because silently down-sizing it would be worse
than telling the user their request is impossible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .capacity import NodeCapacity, RunningJob

MAX_EFFECTIVE_PRIORITY = 10
DEFAULT_AGING_MINUTES = 30


@dataclass(slots=True)
class QueuedJob:
    id: int
    name: str
    gpus: int
    priority: int
    duration_minutes: int
    submitted_at: datetime
    gpu_model: str | None = None
    span: str = "node"
    project: str = "default"

    def __post_init__(self) -> None:
        if self.gpus < 1:
            raise ValueError(f"job {self.id} must request at least one GPU")
        if self.span not in {"node", "any"}:
            raise ValueError(f"job {self.id} has unknown span {self.span!r}")

    def effective_priority(self, now: datetime, aging_minutes: int = DEFAULT_AGING_MINUTES) -> int:
        """Declared priority plus ageing, capped."""
        waited = (now - self.submitted_at).total_seconds() / 60.0
        if aging_minutes <= 0:
            return min(self.priority, MAX_EFFECTIVE_PRIORITY)
        bonus = int(waited // aging_minutes)
        return min(self.priority + bonus, MAX_EFFECTIVE_PRIORITY)


@dataclass(slots=True)
class Decision:
    """One placement. ``reason`` is ``head``, ``backfill`` or ``immediate``."""

    job_id: int
    node: str
    gpus: int
    reason: str


@dataclass(slots=True)
class Deferral:
    job_id: int
    reason: str


@dataclass(slots=True)
class Plan:
    decisions: list[Decision] = field(default_factory=list)
    deferred: list[Deferral] = field(default_factory=list)
    unplaceable: list[Deferral] = field(default_factory=list)
    reservation_minutes: int | None = None
    head_job_id: int | None = None

    @property
    def scheduled_ids(self) -> list[int]:
        return [decision.job_id for decision in self.decisions]


def _sorted_queue(queue: list[QueuedJob], now: datetime, aging_minutes: int) -> list[QueuedJob]:
    # Ties break on submission time, then id, so the result is deterministic.
    return sorted(
        queue,
        key=lambda job: (-job.effective_priority(now, aging_minutes), job.submitted_at, job.id),
    )


def _unplaceable_reason(nodes: list[NodeCapacity], job: QueuedJob) -> str | None:
    """Why this job can never run, or None if it is merely waiting.

    The distinction matters enormously to whoever is waiting. "Your job is too
    big" means shrink it; "the only node that fits is down" means page the
    on-call engineer. Reporting the first when the second is true sends people
    to fix the wrong thing, so we check the dead nodes explicitly.
    """
    healthy = [node for node in nodes if node.healthy]
    dead = [node for node in nodes if not node.healthy]

    def fits_somewhere(pool: list[NodeCapacity]) -> bool:
        if not pool:
            return False
        if job.gpu_model:
            pool = [node for node in pool if node.gpu_model == job.gpu_model]
        if not pool:
            return False
        if job.span == "node":
            return any(node.total_gpus >= job.gpus for node in pool)
        return sum(node.total_gpus for node in pool) >= job.gpus

    if fits_somewhere(healthy):
        return None

    shape = f"{job.gpus} GPU(s)" + (f" of {job.gpu_model}" if job.gpu_model else "")
    if job.span == "node":
        shape += " on a single node"

    if fits_somewhere(dead):
        names = ", ".join(sorted(node.name for node in dead))
        return f"{shape} would fit, but the only matching node(s) are unhealthy ({names})"
    return f"{shape} exceeds every node in the cluster"


def _find_node(nodes: list[NodeCapacity], free: dict[str, int], job: QueuedJob) -> str | None:
    """Pick a node that can host ``job`` right now, or None.

    Best-fit: the smallest node that still fits. Leaving big nodes whole matters
    more than leaving many small ones, because a gang request for 8 GPUs cannot
    use four scattered free GPUs.
    """
    if job.span == "any":
        return _find_split(nodes, free, job)

    candidates = [
        node
        for node in nodes
        if node.healthy
        and (job.gpu_model is None or node.gpu_model == job.gpu_model)
        and free.get(node.name, 0) >= job.gpus
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda node: (free[node.name], node.name)).name


def _find_split(nodes: list[NodeCapacity], free: dict[str, int], job: QueuedJob) -> str | None:
    """For ``span="any"`` we still report a single node — the one that takes the
    bulk — and the caller subtracts from it. Split accounting is a v2 concern;
    what matters here is that the capacity check is cluster-wide, not per-node."""
    candidates = [
        node
        for node in nodes
        if node.healthy
        and (job.gpu_model is None or node.gpu_model == job.gpu_model)
        and free.get(node.name, 0) > 0
    ]
    if not candidates:
        return None
    if sum(free[node.name] for node in candidates) < job.gpus:
        return None
    return max(candidates, key=lambda node: (free[node.name], node.name)).name


def _reservation_minutes(
    nodes: list[NodeCapacity],
    running: list[RunningJob],
    free: dict[str, int],
    job: QueuedJob,
) -> int | None:
    """Minutes until ``job`` could start. ``None`` means we cannot say — some
    other job may run forever."""
    candidates = [
        node
        for node in nodes
        if node.healthy and (job.gpu_model is None or node.gpu_model == job.gpu_model)
    ]
    if not candidates:
        return None

    if job.span == "node":
        best: int | None = None
        for node in candidates:
            if node.total_gpus < job.gpus:
                continue
            frees = sorted(
                (item.remaining_minutes, item.gpus) for item in running if item.node == node.name
            )
            available = free.get(node.name, 0)
            if available >= job.gpus:
                return 0
            for minutes, gpus in frees:
                available += gpus
                if available >= job.gpus:
                    best = minutes if best is None else min(best, minutes)
                    break
        return best

    # span="any": cluster-wide arithmetic.
    available = sum(free.get(node.name, 0) for node in candidates)
    if available >= job.gpus:
        return 0
    names = {node.name for node in candidates}
    for minutes, gpus in sorted(
        (item.remaining_minutes, item.gpus) for item in running if item.node in names
    ):
        available += gpus
        if available >= job.gpus:
            return minutes
    return None


def plan(
    nodes: list[NodeCapacity],
    queue: list[QueuedJob],
    running: list[RunningJob],
    now: datetime,
    aging_minutes: int = DEFAULT_AGING_MINUTES,
    enable_backfill: bool = True,
) -> Plan:
    """Decide who starts now.

    ``nodes[].allocated_gpus`` must already reflect ``running``; this function
    only reasons about the deltas it is about to add.
    """
    result = Plan()
    if not queue:
        return result

    ordered = _sorted_queue(queue, now, aging_minutes)
    free = {node.name: node.free_gpus for node in nodes}

    # Split the queue into "could never run" and "waiting".
    placeable: list[QueuedJob] = []
    for job in ordered:
        reason = _unplaceable_reason(nodes, job)
        if reason is not None:
            result.unplaceable.append(Deferral(job_id=job.id, reason=reason))
        else:
            placeable.append(job)

    if not placeable:
        return result

    head = placeable[0]
    result.head_job_id = head.id

    head_node = _find_node(nodes, free, head)
    backfill_from = 1
    if head_node is not None:
        free[head_node] -= head.gpus
        result.decisions.append(Decision(head.id, head_node, head.gpus, "head"))
    else:
        reservation = _reservation_minutes(nodes, running, free, head)
        result.reservation_minutes = reservation
        result.deferred.append(
            Deferral(
                job_id=head.id,
                reason=(
                    f"head of queue; needs {head.gpus} GPU(s), "
                    + (
                        f"next capacity window in {reservation} min"
                        if reservation is not None
                        else "no capacity window is currently forecastable"
                    )
                ),
            )
        )

    for job in placeable[backfill_from:]:
        node = _find_node(nodes, free, job)
        if node is None:
            result.deferred.append(
                Deferral(job_id=job.id, reason="no node currently has enough free GPUs")
            )
            continue

        if result.reservation_minutes is not None:
            if not enable_backfill:
                result.deferred.append(
                    Deferral(job_id=job.id, reason="backfill is disabled and the head is waiting")
                )
                continue
            # EASY: backfill may only run if it clears out before the head's slot.
            if (
                result.reservation_minutes <= 0
                or job.duration_minutes >= result.reservation_minutes
            ):
                result.deferred.append(
                    Deferral(
                        job_id=job.id,
                        reason=(
                            f"backfill refused: {job.duration_minutes} min would overrun the "
                            f"head's {result.reservation_minutes} min reservation"
                        ),
                    )
                )
                continue

        free[node] -= job.gpus
        reason = "backfill" if result.reservation_minutes is not None else "immediate"
        result.decisions.append(Decision(job.id, node, job.gpus, reason))

    return result
