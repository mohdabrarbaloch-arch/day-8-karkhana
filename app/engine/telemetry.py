"""Utilisation maths — the numbers a capacity meeting argues over."""

from __future__ import annotations

from dataclasses import dataclass

from .capacity import NodeCapacity


@dataclass(slots=True)
class Utilisation:
    nodes: int
    healthy_nodes: int
    total_gpus: int
    allocated_gpus: int
    free_gpus: int
    utilisation: float

    @property
    def utilisation_percent(self) -> float:
        return round(self.utilisation * 100, 1)


def utilisation(nodes: list[NodeCapacity]) -> Utilisation:
    """Cluster-wide utilisation.

    Unhealthy nodes still count toward total capacity — their GPUs exist, they
    are just unreachable — so a broken node should show up as a *drop* in
    utilisation rather than a drop in the denominator. Hiding broken capacity in
    the maths is how clusters end up looking mysteriously cheap.
    """
    total = sum(node.total_gpus for node in nodes)
    allocated = sum(min(node.allocated_gpus, node.total_gpus) for node in nodes)
    healthy = sum(1 for node in nodes if node.healthy)
    return Utilisation(
        nodes=len(nodes),
        healthy_nodes=healthy,
        total_gpus=total,
        allocated_gpus=allocated,
        free_gpus=max(0, total - allocated),
        utilisation=(allocated / total) if total else 0.0,
    )
