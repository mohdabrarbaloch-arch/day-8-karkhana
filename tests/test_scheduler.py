"""Scheduler rules: priority ordering, ageing, gang placement, EASY backfill."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.engine.capacity import NodeCapacity, RunningJob
from app.engine.scheduler import QueuedJob, plan

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def job(**overrides) -> QueuedJob:
    defaults = {
        "id": 1,
        "name": "job",
        "gpus": 1,
        "priority": 5,
        "duration_minutes": 60,
        "submitted_at": NOW - timedelta(minutes=5),
        "gpu_model": None,
        "span": "node",
        "project": "test",
    }
    defaults.update(overrides)
    return QueuedJob(**defaults)


def node(name: str, total: int, model: str = "H100-80GB", allocated: int = 0, healthy: bool = True):
    return NodeCapacity(
        name=name, gpu_model=model, total_gpus=total, allocated_gpus=allocated, healthy=healthy
    )


# ─────────────────────────── ordering ───────────────────────────


def test_higher_priority_runs_first():
    nodes = [node("n1", 1)]
    queue = [job(id=1, priority=2), job(id=2, priority=9)]
    result = plan(nodes, queue, [], NOW)
    assert result.scheduled_ids == [2]


def test_ageing_lifts_a_patient_job_above_a_fresh_one():
    # Submitted 90 minutes ago at priority 1 with 30-min ageing -> 1 + 3 = 4.
    old = job(id=1, priority=1, submitted_at=NOW - timedelta(minutes=90))
    fresh = job(id=2, priority=3, submitted_at=NOW - timedelta(minutes=1))
    assert old.effective_priority(NOW, 30) == 4
    assert old.effective_priority(NOW, 30) > fresh.effective_priority(NOW, 30)


def test_ageing_is_capped():
    ancient = job(id=1, priority=1, submitted_at=NOW - timedelta(days=10))
    assert ancient.effective_priority(NOW, 30) == 10


def test_ageing_happens_at_the_configured_interval():
    # 29 minutes waited, 30-minute interval -> no bonus yet.
    assert (
        job(priority=5, submitted_at=NOW - timedelta(minutes=29)).effective_priority(NOW, 30) == 5
    )
    # 30 minutes -> exactly one bonus point.
    assert (
        job(priority=5, submitted_at=NOW - timedelta(minutes=30)).effective_priority(NOW, 30) == 6
    )


def test_aging_zero_means_no_ageing():
    old = job(priority=2, submitted_at=NOW - timedelta(days=5))
    assert old.effective_priority(NOW, 0) == 2


# ─────────────────────────── gang placement ───────────────────────────


def test_gang_job_needs_one_node():
    # Two nodes with 4 free each cannot host a 6-GPU gang request.
    nodes = [node("n1", 8, allocated=4), node("n2", 8, allocated=4)]
    result = plan(nodes, [job(gpus=6)], [], NOW)
    assert result.scheduled_ids == []
    assert result.deferred


def test_gang_job_lands_on_the_node_with_room():
    nodes = [node("small", 4, allocated=4), node("big", 8)]
    result = plan(nodes, [job(gpus=6)], [], NOW)
    assert result.scheduled_ids == [1]
    assert result.decisions[0].node == "big"


def test_best_fit_prefers_the_smaller_node_that_still_fits():
    # n1 has 4 free, n2 has 8 free. A 3-GPU job should take n1 and leave n2 intact.
    nodes = [node("n1", 4), node("n2", 8)]
    result = plan(nodes, [job(gpus=3)], [], NOW)
    assert result.decisions[0].node == "n1"


def test_model_affinity_is_respected():
    nodes = [node("h100", 8, model="H100-80GB"), node("a100", 8, model="A100-80GB")]
    result = plan(nodes, [job(gpus=4, gpu_model="A100-80GB")], [], NOW)
    assert result.decisions[0].node == "a100"


def test_unhealthy_node_is_never_chosen():
    nodes = [node("n1", 8, healthy=False)]
    result = plan(nodes, [job(gpus=1)], [], NOW)
    assert result.scheduled_ids == []
    assert result.unplaceable


def test_span_any_can_use_cluster_wide_capacity():
    # 4 free on each of two nodes satisfies a 6-GPU span=any request.
    nodes = [node("n1", 8, allocated=4), node("n2", 8, allocated=4)]
    result = plan(nodes, [job(gpus=6, span="any")], [], NOW)
    assert result.scheduled_ids == [1]


def test_unplaceable_reason_names_the_dead_node():
    nodes = [node("live", 8, model="H100-80GB"), node("dead", 8, model="A100-80GB", healthy=False)]
    result = plan(nodes, [job(gpus=4, gpu_model="A100-80GB")], [], NOW)
    assert "unhealthy" in result.unplaceable[0].reason
    assert "dead" in result.unplaceable[0].reason


def test_unplaceable_reason_says_exceeds_when_no_node_is_big_enough():
    nodes = [node("n1", 8)]
    result = plan(nodes, [job(gpus=16)], [], NOW)
    assert "exceeds every node" in result.unplaceable[0].reason


# ─────────────────────────── backfill ───────────────────────────


def test_head_waits_and_short_job_backfills_inside_the_reservation():
    """The core EASY scenario.

    n1: 4 of 8 GPUs held by a job with 30 minutes left.
    A 6-GPU head cannot start until that frees (30 min reservation).
    A 2-GPU, 10-minute job fits now and finishes before the reservation,
    so it is allowed to start.
    """
    nodes = [node("n1", 8, allocated=4)]
    running = [RunningJob(job_id=99, node="n1", gpus=4, remaining_minutes=30)]
    queue = [
        job(id=1, gpus=6, priority=9, duration_minutes=120),
        job(id=2, gpus=2, priority=1, duration_minutes=10),
    ]

    result = plan(nodes, queue, running, NOW)

    assert result.head_job_id == 1
    assert result.reservation_minutes == 30
    assert result.scheduled_ids == [2], "only the short job should backfill"
    assert result.decisions[0].reason == "backfill"


def test_backfill_refused_when_it_would_overrun_the_reservation():
    nodes = [node("n1", 8, allocated=4)]
    running = [RunningJob(job_id=99, node="n1", gpus=4, remaining_minutes=30)]
    queue = [job(id=1, gpus=6, priority=9), job(id=2, gpus=2, priority=1, duration_minutes=45)]

    result = plan(nodes, queue, running, NOW)

    assert result.scheduled_ids == []
    assert any("overrun" in d.reason for d in result.deferred)


def test_backfill_disabled_defers_everything_behind_a_waiting_head():
    nodes = [node("n1", 8, allocated=4)]
    running = [RunningJob(job_id=99, node="n1", gpus=4, remaining_minutes=30)]
    queue = [job(id=1, gpus=6, priority=9), job(id=2, gpus=2, priority=1, duration_minutes=5)]

    result = plan(nodes, queue, running, NOW, enable_backfill=False)

    assert result.scheduled_ids == []
    assert any("backfill is disabled" in d.reason for d in result.deferred)


def test_no_reservation_when_the_head_can_start_immediately():
    nodes = [node("n1", 8)]
    result = plan(nodes, [job(id=1, gpus=4), job(id=2, gpus=2)], [], NOW)
    assert result.reservation_minutes is None
    assert result.scheduled_ids == [1, 2]


def test_empty_queue_returns_an_empty_plan():
    result = plan([node("n1", 8)], [], [], NOW)
    assert result.decisions == []
    assert result.head_job_id is None


def test_planning_is_deterministic_for_equal_priorities():
    """Two runs over the same input must produce the same placement — otherwise
    a scheduler is impossible to reason about or debug."""
    nodes = [node("n1", 8), node("n2", 8)]
    queue = [
        job(id=1, priority=5, submitted_at=NOW - timedelta(minutes=10)),
        job(id=2, priority=5, submitted_at=NOW - timedelta(minutes=10)),
    ]
    first = plan(nodes, queue, [], NOW)
    second = plan(nodes, queue, [], NOW)
    assert first.scheduled_ids == second.scheduled_ids
    assert [d.node for d in first.decisions] == [d.node for d in second.decisions]


def test_queued_job_rejects_zero_gpus():
    with pytest.raises(ValueError):
        job(gpus=0)


def test_node_rejects_zero_gpus():
    with pytest.raises(ValueError):
        NodeCapacity(name="bad", gpu_model="X", total_gpus=0)
