"""Node capacity primitives.

A node is a bag of GPUs of one model. "Allocated" is whatever running jobs
hold; "free" is the remainder. Keeping this dumb and immutable-ish means the
scheduler can try placements without touching the database.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Allocation:
    """A job's claim on one node."""

    node: str
    gpus: int


@dataclass(slots=True)
class NodeCapacity:
    name: str
    gpu_model: str
    total_gpus: int
    allocated_gpus: int = 0
    healthy: bool = True
    region: str = "local"
    cluster_name: str = "default"

    def __post_init__(self) -> None:
        if self.total_gpus < 1:
            raise ValueError(f"node {self.name!r} must have at least one GPU")
        if self.allocated_gpus < 0:
            raise ValueError(f"node {self.name!r} cannot have negative allocation")
        # Trust the total over a stale cached allocation.
        if self.allocated_gpus > self.total_gpus:
            self.allocated_gpus = self.total_gpus

    @property
    def free_gpus(self) -> int:
        return self.total_gpus - self.allocated_gpus

    @property
    def utilisation(self) -> float:
        return self.allocated_gpus / self.total_gpus if self.total_gpus else 0.0

    def accepts(self, gpus: int, gpu_model: str | None = None) -> bool:
        """Can this node host a job asking for ``gpus`` of ``gpu_model``?"""
        if not self.healthy:
            return False
        if gpus < 1:
            return False
        if gpu_model and gpu_model != self.gpu_model:
            return False
        return self.free_gpus >= gpus


@dataclass(slots=True)
class RunningJob:
    """The bit of a running job the scheduler needs: what it holds and for how
    much longer. ``remaining_minutes`` of 0 means "about to free"."""

    job_id: int
    node: str
    gpus: int
    remaining_minutes: int


@dataclass(slots=True)
class ClusterSnapshot:
    nodes: list[NodeCapacity] = field(default_factory=list)

    @property
    def total_gpus(self) -> int:
        return sum(node.total_gpus for node in self.nodes)

    @property
    def allocated_gpus(self) -> int:
        return sum(node.allocated_gpus for node in self.nodes)

    @property
    def free_gpus(self) -> int:
        return self.total_gpus - self.allocated_gpus

    def by_name(self, name: str) -> NodeCapacity | None:
        for node in self.nodes:
            if node.name == name:
                return node
        return None
