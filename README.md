# Karkhana — کارخانہ

A GPU cluster scheduler that explains every decision it makes.

Most schedulers tell you a job is "pending". Karkhana tells you *why*: how many
GPUs it needs, which node it is waiting for, when that capacity frees up, and
what the wait will cost. Every placement, deferral and refusal carries a reason
string a human can act on.

```
$ curl -X POST localhost:8000/api/scheduler/run
{
  "scheduled": [],
  "deferred": [
    { "job_id": 6,
      "reason": "head of queue; needs 24 GPU(s), next capacity window in 176 min" }
  ],
  "unplaceable": [
    { "job_id": 5,
      "reason": "16 GPU(s) of H100-80GB on a single node exceeds every node in the cluster" },
    { "job_id": 7,
      "reason": "2 GPU(s) of L40S-48GB on a single node would fit, but the only matching node(s) are unhealthy (l40s-01)" }
  ],
  "reservation_minutes": 176,
  "head_job_id": 6,
  "message": "Nothing started — no capacity available yet."
}
```

## Why

A GPU cluster is an expensive, shared, contended resource. The hard part is not
starting a job — it is deciding which job *not* to start, and being able to
defend that decision to the person whose training run is now third in line.

Three problems this solves that a naive "first come, first served" loop does not:

1. **Starvation.** A low-priority job submitted on a busy day never runs. Fixed
   by ageing: waiting is itself a claim on the cluster.
2. **Gang requests.** A job needing 8 GPUs on one machine cannot use 8 free GPUs
   spread across four nodes. Placement has to understand the difference.
3. **Small-job blocking.** A 4-hour job at the head of the queue blocks the
   10-minute job behind it for 4 hours. Fixed by EASY backfill inside the head
   job's reservation.

## Features

- **Scheduler pass** — priority with ageing, gang placement, EASY backfill
  inside the head job's reservation, and a written reason for every outcome.
- **Chargeback** — GPU-hours and cost per team, from a formula you can
  reproduce on paper.
- **Audit trail** — every placement and status change is recorded with an actor.
- **Auth** — JWT, bcrypt hashing, two enforced roles (`admin`, `operator`).
  The first account on an empty database becomes `admin`.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"
# put that value in SECRET_KEY, then:

ALLOW_DEMO_SEED=true uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000>. The demo account is pre-filled on the login card:

```
admin@karkhana.dev  /  Karkhana2026!
```

`ALLOW_DEMO_SEED=true` seeds four nodes and 28 GPUs (`a100-01` 8×, `hgx-01`
8×, `hgx-02` 8×, `l40s-01` 4× — the last of which is seeded unhealthy) plus a
queue built so one scheduling pass demonstrates scheduling, backfill and
unplaceable outcomes together. It is refused when `ENVIRONMENT=production`.

## Docker

```bash
docker compose up --build
```

That brings up the app on port 8000 and PostgreSQL 16 alongside it. The app
reads `DATABASE_URL` and needs no other change to use Postgres instead of
SQLite.

## Tests

```bash
pytest -q          # 61 passed
ruff check .       # All checks passed
black --check .    # 26 files unchanged
```

The tests are concentrated on `app/engine/`, because that is where the rules
live. They cover ageing arithmetic, gang vs. spread placement, backfill
admission, the reservation window, unplaceable-job reasons, and the chargeback
formula — including a case that asserts the exact cost of a known job so a
refactor can't quietly change the bill.

## API

| Method | Path | What it does |
|---|---|---|
| `POST` | `/api/auth/register` | Create a user |
| `POST` | `/api/auth/login` | Get a JWT |
| `GET` | `/api/auth/me` | Current user |
| `GET` | `/api/nodes` | All nodes with occupancy |
| `POST` | `/api/nodes` | Add a node (admin) |
| `GET` | `/api/nodes/{node_id}` | One node |
| `PATCH` | `/api/nodes/{node_id}/health` | Set health (admin) |
| `POST` | `/api/nodes/{node_id}/drain` | Drain a node (admin) |
| `GET` | `/api/jobs` | List jobs, filter by status |
| `POST` | `/api/jobs` | Submit a job |
| `GET` | `/api/jobs/{job_id}` | One job |
| `POST` | `/api/jobs/{job_id}/cancel` | Cancel a queued or running job |
| `POST` | `/api/jobs/{job_id}/complete` | Mark finished, with exit code |
| `DELETE` | `/api/jobs/{job_id}` | Delete a job (admin) |
| `GET` | `/api/scheduler/queue` | Queue with reasons, in order |
| `POST` | `/api/scheduler/run` | Run one scheduling pass |
| `POST` | `/api/scheduler/reset` | Requeue running jobs and release GPUs |
| `GET` | `/api/scheduler/decisions/{job_id}` | Why this job did or did not place |
| `GET` | `/api/dashboard` | Cluster totals, `per_node` breakdown |
| `GET` | `/api/dashboard/chargeback` | Cost per team for a window |
| `GET` | `/api/dashboard/teams` | Known teams |

That is 21 operations; `GET /api/health` makes 22. Full request/response
shapes are in [`docs/api.md`](docs/api.md), and the always-current version is
served at `/api/openapi.json`.

## Deploying

```bash
npm i -g vercel
vercel --prod --yes --token "$VERCEL_TOKEN"
```

`vercel.json` routes everything to the ASGI app. For a real deployment set
`SECRET_KEY`, `ENVIRONMENT=production`, and a `DATABASE_URL` pointing at a
managed Postgres — SQLite on a serverless filesystem will lose writes.

## Docs

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — the scheduling rules, and why they are
  shaped that way
- [`docs/setup.md`](docs/setup.md) — local, Docker and Postgres setup
- [`docs/usage.md`](docs/usage.md) — a walk through the app as a user
- [`docs/api.md`](docs/api.md) — every endpoint with real captured responses

## Honest limits

- The scheduler runs on demand (`POST /api/scheduler/run`), not on a background
  loop. A real cluster wants a tick; this is a demo that lets you step the
  algorithm and read what it decided.
- Rates are a single platform default with a per-job override. There is no
  per-node pricing, no committed-use discount, no spot tier.
- JWT only — no refresh tokens, no revocation list. A token is valid for its
  full 24 hours.
- No quotas. A team can consume the whole cluster; chargeback tells them what it
  cost after the fact rather than stopping them.

## License

MIT
