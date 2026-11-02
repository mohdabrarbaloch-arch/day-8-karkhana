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
