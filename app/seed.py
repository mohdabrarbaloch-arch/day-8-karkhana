"""Demo data.

Twelve GPUs across four nodes and a handful of jobs, chosen so that the first
page load shows something true rather than something empty: a mixed-vendor
cluster, one dead node, a queue with a gang request that cannot fit, and a
backfill candidate that can. Every number here is invented but internally
consistent — nothing is fetched from a real provider.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Job, JobEvent, Node, User
from .security import hash_password

DEMO_EMAIL = "admin@karkhana.dev"
DEMO_PASSWORD = "Karkhana2026!"

NODES = [
    # name, model, gpus, memory, region, healthy
    ("hgx-01", "H100-80GB", 8, 640, "karachi-a", True),
    ("hgx-02", "H100-80GB", 8, 640, "karachi-a", True),
    ("a100-01", "A100-80GB", 8, 640, "karachi-b", True),
    ("l40s-01", "L40S-48GB", 4, 192, "karachi-b", False),
]


def seed_if_empty(db: Session, allow_seed: bool = True) -> User | None:
    """Populate the database once. Returns the demo admin, or None if skipped."""
    if not allow_seed:
        return None
    if db.scalar(select(func.count()).select_from(User)):
        return db.scalar(select(User).where(User.email == DEMO_EMAIL))

    admin = User(
        email=DEMO_EMAIL,
        full_name="Ayesha Siddiqui",
        password_hash=hash_password(DEMO_PASSWORD),
        role="admin",
        team="platform",
    )
    researcher = User(
        email="imran@karkhana.dev",
        full_name="Imran Qureshi",
        password_hash=hash_password(DEMO_PASSWORD),
        role="operator",
        team="research",
    )
    db.add_all([admin, researcher])
    db.flush()

    nodes: dict[str, Node] = {}
    for name, model, gpus, memory, region, healthy in NODES:
        node = Node(
            name=name,
            gpu_model=model,
            total_gpus=gpus,
            memory_gb=memory,
            region=region,
            healthy=healthy,
            cluster_name="default",
        )
        db.add(node)
        nodes[name] = node
    db.flush()

    now = datetime.now(UTC)

    # A running training job on hgx-01 holding 4 of 8 GPUs, 3 hours in of 6.
    running = Job(
        owner_id=researcher.id,
        name="urdu-asr-finetune",
        project="speech",
        command="torchrun --nproc_per_node=4 train.py --lang ur",
        gpus=4,
        gpu_model="H100-80GB",
        span="node",
        priority=8,
        duration_minutes=360,
        status="running",
        node_id=nodes["hgx-01"].id,
        submitted_at=now - timedelta(hours=4),
        started_at=now - timedelta(hours=3),
        last_reason="Started on hgx-01 (head)",
    )
    db.add(running)
    db.flush()
    db.add_all(
        [
            JobEvent(
                job_id=running.id,
                event="submitted",
                message="Queued for 4 GPU(s)",
                actor=DEMO_EMAIL,
                at=now - timedelta(hours=4),
            ),
            JobEvent(
                job_id=running.id,
                event="running",
                message="Started on hgx-01",
                actor="scheduler",
                at=now - timedelta(hours=3),
            ),
        ]
    )

    # Two finished jobs yesterday, so the chargeback table is not empty.
    for name, team_user, gpus, minutes, status in [
        ("sindhi-ocr-eval", researcher, 2, 185, "completed"),
        ("retrieval-bench", researcher, 1, 47, "completed"),
        ("gan-smoke-test", admin, 8, 22, "failed"),
    ]:
        ended = now - timedelta(hours=20)
        job = Job(
            owner_id=team_user.id,
            name=name,
            project="research",
            command="python run.py",
            gpus=gpus,
            gpu_model="A100-80GB",
            span="node",
            priority=5,
            duration_minutes=minutes,
            status=status,
            node_id=nodes["a100-01"].id,
            submitted_at=ended - timedelta(minutes=minutes + 10),
            started_at=ended - timedelta(minutes=minutes),
            ended_at=ended,
            exit_code=0 if status == "completed" else 1,
            last_reason=f"Exited with code {0 if status == 'completed' else 1}",
        )
        db.add(job)

    # The queue is built so that a single scheduling pass exercises every path:
    #   16x H100 gang  -> unplaceable (no single 16-GPU node exists)
    #   24 GPU span=any-> head, deferred, with a 180 min reservation
    #   2x L40S        -> unplaceable, and the reason must name the dead node
    #   2 GPU, 20 min  -> backfills inside the head's reservation
    for name, gpus, model, span, priority, minutes, submitted in [
        ("llama-full-gang", 16, "H100-80GB", "node", 9, 240, now - timedelta(minutes=40)),
        ("whole-cluster-data-prep", 24, None, "any", 7, 45, now - timedelta(minutes=50)),
        ("l40s-tokenizer-sweep", 2, "L40S-48GB", "node", 3, 10, now - timedelta(minutes=12)),
        ("sindhi-bench-eval", 2, "A100-80GB", "node", 2, 20, now - timedelta(minutes=5)),
    ]:
        job = Job(
            owner_id=researcher.id,
            name=name,
            project="research",
            command="python pipeline.py",
            gpus=gpus,
            gpu_model=model,
            span=span,
            priority=priority,
            duration_minutes=minutes,
            status="queued",
            submitted_at=submitted,
            last_reason="Waiting for capacity",
        )
        db.add(job)
        db.flush()
        db.add(
            JobEvent(
                job_id=job.id,
                event="submitted",
                message=f"Queued for {gpus} GPU(s), priority {priority}",
                actor=DEMO_EMAIL,
                at=submitted,
            )
        )

    db.commit()
    nodes["hgx-01"].allocated_gpus = 4
    db.commit()
    return admin
