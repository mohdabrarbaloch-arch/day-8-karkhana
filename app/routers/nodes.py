"""Node routes — the GPUs themselves."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from ..deps import AdminUser, CurrentUser, DbSession
from ..models import Job, Node
from ..schemas import MessageResponse, NodeCreate, NodeHealthUpdate, NodeRead
from ..services import recompute_allocations

router = APIRouter(prefix="/api/nodes", tags=["nodes"])


@router.get("", response_model=list[NodeRead])
def list_nodes(db: DbSession, user: CurrentUser) -> list[NodeRead]:
    nodes = db.scalars(select(Node).order_by(Node.cluster_name, Node.name)).all()
    return [NodeRead.model_validate(node) for node in nodes]


@router.post("", response_model=NodeRead, status_code=status.HTTP_201_CREATED)
def create_node(payload: NodeCreate, db: DbSession, admin: AdminUser) -> NodeRead:
    """Register a machine. Admin only — capacity is not a self-service thing."""
    clash = db.scalar(
        select(Node).where(Node.cluster_name == payload.cluster_name, Node.name == payload.name)
    )
    if clash is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Node {payload.name!r} already exists in cluster {payload.cluster_name!r}",
        )

    node = Node(**payload.model_dump())
    db.add(node)
    db.commit()
    db.refresh(node)
    return NodeRead.model_validate(node)


@router.get("/{node_id}", response_model=NodeRead)
def get_node(node_id: int, db: DbSession, user: CurrentUser) -> NodeRead:
    node = db.get(Node, node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    return NodeRead.model_validate(node)


@router.patch("/{node_id}/health", response_model=NodeRead)
def set_health(
    node_id: int, payload: NodeHealthUpdate, db: DbSession, admin: AdminUser
) -> NodeRead:
    """Mark a node healthy or dead.

    Taking a node out of service does **not** kill its running jobs — that
    decision belongs to whoever owns them. It only stops new work landing there.
    """
    node = db.get(Node, node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")

    node.healthy = payload.healthy
    db.commit()
    db.refresh(node)
    return NodeRead.model_validate(node)


@router.post("/{node_id}/drain", response_model=MessageResponse)
def drain_node(node_id: int, db: DbSession, admin: AdminUser) -> MessageResponse:
    """Cancel every queued job pinned to this node and mark it unhealthy.

    Draining is what you do before maintenance: stop accepting work, let the
    running jobs finish on their own, then take the box down.
    """
    node = db.get(Node, node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")

    node.healthy = False
    requeued = db.scalars(select(Job).where(Job.status == "queued", Job.node_id == node.id)).all()
    for job in requeued:
        job.node_id = None
    db.commit()
    recompute_allocations(db)
    db.commit()

    return MessageResponse(
        message=(
            f"Node {node.name!r} is now draining. "
            f"{len(requeued)} queued job(s) released back to the pool."
        )
    )
