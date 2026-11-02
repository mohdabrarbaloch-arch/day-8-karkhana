"""Chargeback arithmetic. These tests exist so a finance team can trust the
number without reading the router."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.engine.billing import ChargeableJob, chargeback, cost_of, gpu_hours


def test_gpu_hours_is_gpus_times_fractional_hours():
    # 4 GPUs for 90 minutes = 4 x 1.5 = 6.0
    assert gpu_hours(4, 90) == pytest.approx(6.0)


def test_zero_minutes_bills_one_minute():
    # A 40-second job still occupied silicon; the floor is one minute.
    assert gpu_hours(1, 0) == pytest.approx(1 / 60)


def test_gpu_hours_rejects_zero_gpus():
    with pytest.raises(ValueError):
        gpu_hours(0, 60)


def test_cost_matches_hand_arithmetic():
    # 2 GPUs, 185 min, $2.50/GPU-hr:
    #   185/60 = 3.08333 GPU-hr  x 2 = 6.16667  x 2.50 = 15.4167 -> 15.42
    assert cost_of(2, 185, 2.50) == Decimal("15.42")


def test_cost_rounds_half_up_to_cents():
    # 1 GPU for 7 min at 3.00/hr = 0.11666... x 3 = 0.35
    assert cost_of(1, 7, 3.00) == Decimal("0.35")


def test_cost_rejects_negative_rate():
    with pytest.raises(ValueError):
        cost_of(1, 60, -1)


def test_cost_of_one_gpu_hour_at_default_rate():
    assert cost_of(1, 60, 2.50) == Decimal("2.50")


def test_chargeback_aggregates_per_team():
    jobs = [
        ChargeableJob(1, "a", "research", "p", 2, 185, 2.50),
        ChargeableJob(2, "b", "research", "p", 1, 47, 2.50),
        ChargeableJob(3, "c", "platform", "p", 8, 22, 2.50),
    ]
    totals = chargeback(jobs)

    assert set(totals) == {"research", "platform"}
    assert totals["research"].jobs == 2
    assert totals["research"].job_ids == [1, 2]
    # 2x(185/60)=6.1667 + 1x(47/60)=0.7833 = 6.95
    assert totals["research"].gpu_hours == pytest.approx(6.95, abs=0.01)
    # 6.1667x2.50=15.42 + 0.7833x2.50=1.96 = 17.38
    assert totals["research"].cost == Decimal("17.38")


def test_chargeback_of_empty_list_is_empty():
    assert chargeback([]) == {}


def test_team_total_equals_sum_of_its_jobs():
    """The invariant that makes an invoice defensible."""
    jobs = [
        ChargeableJob(1, "a", "ml", "p", 3, 100, 1.75),
        ChargeableJob(2, "b", "ml", "p", 7, 55, 1.75),
    ]
    totals = chargeback(jobs)
    expected = sum(cost_of(j.gpus, j.runtime_minutes, j.rate_per_gpu_hour) for j in jobs)
    assert totals["ml"].cost == expected
