"""Pure scheduling, capacity and billing logic.

Nothing in this package touches the database. That is deliberate: the rules
that decide who gets a GPU — and what they are charged for it — should be
readable, unit-testable and reproducible by hand. Routers persist the results.
"""

from .billing import chargeback, cost_of, gpu_hours
from .capacity import Allocation, NodeCapacity
from .scheduler import Decision, Plan, QueuedJob, plan
from .telemetry import utilisation

__all__ = [
    "Allocation",
    "Decision",
    "NodeCapacity",
    "Plan",
    "QueuedJob",
    "chargeback",
    "cost_of",
    "gpu_hours",
    "plan",
    "utilisation",
]
