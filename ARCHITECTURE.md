# Architecture

## The shape

```
browser ──► FastAPI (app/main.py)
                 │
                 ├── routers/      auth, nodes, jobs, scheduler, dashboard
                 ├── engine/       pure functions, no I/O
                 │     ├── capacity.py    free-GPU accounting per node
                 │     ├── scheduler.py   the pass: ageing, gang, backfill
                 │     ├── billing.py     chargeback arithmetic
                 │     └── telemetry.py   event log
                 └── services.py   ORM reads the engine needs, as plain values
```

The important line is between `engine/` and everything else. `engine/` takes
dataclasses in and returns decision objects out. It does not import SQLAlchemy,
open a session, or read a clock it was not given. That is what makes it
testable without a database, and what lets the tests assert exact reasons rather
than "a job probably started".

## The scheduling rules

**Priority with ageing.** Effective priority is
`priority + floor(waiting_minutes / AGE_STEP_MINUTES)`, capped at
`MAX_EFFECTIVE_PRIORITY`. Without ageing, a low-priority job starves forever.
With it, waiting is itself a claim on the cluster.

**Gang placement.** A job with `span=node` needs all its GPUs on one machine.
`span=any` may spread across nodes. Gang jobs are the reason a cluster can look
empty and still refuse a job — 16 GPUs free across four nodes is not 16 GPUs on
one node.

**EASY backfill.** The head of the queue gets a reservation: the earliest time
capacity will exist for it. Smaller jobs may start *now* only if they will
finish before that reservation expires. This is what stops a 4-hour job from
blocking a 10-minute one behind it, without letting small jobs starve the head.

## Three outcomes, not two

Every queued job ends a pass in exactly one of three arrays:

| Array | Means | What the operator should do |
|---|---|---|
| `scheduled` | Placed now, on a named node | Nothing |
| `deferred` | Waiting for capacity that *will* exist | Wait, or raise priority |
| `unplaceable` | No amount of waiting helps | Fix the request or the hardware |

The third one is the design decision worth defending. A scheduler that reports
both "waiting" is being polite at the expense of being useful: a job requesting
16 GPUs of a model whose largest node has 8 will never run, and telling its
owner to "wait" is a lie they cannot act on. Splitting the categories costs one
branch and gives the operator a different verb for each situation.

## Data model

```
User ──1:N── Job ──N:1── Node
              │
              └──1:N── JobEvent   (append-only)
```

`Job.status` is a plain string, not a Postgres enum: adding a state should not
require a migration on a system where the states are still being discovered.
The valid set is enforced by the API layer and asserted by tests.

`JobEvent` is append-only. Nothing updates it. An audit trail you can edit is
not an audit trail.

`Node.allocated_gpus` is a stored counter, recomputed from running jobs
(`recompute_allocations`) whenever a job changes state. It is derived data kept
for read speed — every write path that can change it calls the recompute, and
the tests check the counter agrees with the jobs that justify it.

## Failure handling

- **A node dies.** It keeps its `total_gpus` so capacity accounting stays honest
  — utilisation drops rather than the node vanishing. Its running jobs are not
  killed; that is the owner's call. `POST /api/nodes/{id}/drain` stops new work
  landing and releases queued jobs pinned to it.
- **The allocator drifts.** `POST /api/scheduler/reset` returns every running
  job to the queue and holds nothing. A demo convenience, and a real recovery
  tool if the counter ever disagrees with reality.
- **A job is submitted that cannot fit.** Accepted, then reported unplaceable
  with a reason. Rejecting it at submit time would mean the submitter has to
  know cluster topology to phrase a request.

## What this deliberately does not do

- No preemption. A higher-priority job cannot evict a running one. Preemption
  needs checkpointing to be safe, and pretending otherwise would lose work.
- No multi-cluster federation. One cluster, one `cluster_name` field reserved
  for later.
- No GPU health beyond node-level. A node is healthy or it is not.
- No scheduler daemon. Each pass is an explicit call, so the algorithm can be
  stepped and inspected.

## Testing strategy

`tests/test_scheduler.py` and `tests/test_billing.py` test pure functions with
constructed inputs — no database, no HTTP. `tests/test_api.py` runs the real app
against SQLite through `TestClient` for the paths where the wiring matters
(auth, ownership, status transitions).

The split is deliberate: rules get exhaustive unit tests because that is where
bugs are expensive, and the API gets a smaller set of integration tests because
the rules are already proven.
