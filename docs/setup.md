# Setup

## Requirements

- Python 3.11 or newer
- pip
- Optional: Docker and Docker Compose, if you'd rather not manage a venv

## Local, no Docker

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Generate a secret key. Do not ship with the placeholder in `.env.example`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Paste that into `SECRET_KEY` in `.env`.

Run:

```bash
ALLOW_DEMO_SEED=true uvicorn app.main:app --reload
```

`--reload` is for development only.

## Docker

```bash
docker compose up --build
```

This starts:

- `app` on <http://localhost:8000>
- `db` — PostgreSQL 16 on port 5432

The app connects using `DATABASE_URL` from the compose environment. On first
boot it creates its tables. To seed demo data, set `ALLOW_DEMO_SEED=true` in
the compose environment.

## Environment variables

Every one is documented in `.env.example`. The ones that matter:

| Variable | Default | Notes |
|---|---|---|
| `SECRET_KEY` | `dev-only-change-me-before-deploying` | **Set this for production.** Refused at startup when `ENVIRONMENT=production`. |
| `DATABASE_URL` | `sqlite:///./karkhana.db` | Any SQLAlchemy URL. |
| `ENVIRONMENT` | `development` | Must be one of `development`, `staging`, `production`, `test`. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` | 24 hours. |
| `ALLOW_DEMO_SEED` | `true` | Seeds a demo cluster. Refused in production. |
| `CORS_ORIGINS` | `*` | Comma-separated. |
| `RATE_LIMIT_PER_MINUTE` | `120` | Per-process; see the note below. |
| `DEFAULT_COST_PER_GPU_HOUR` | `2.50` | The chargeback rate. |
| `AGING_MINUTES` | `30` | Minutes of waiting that buy one priority level. |
| `ENABLE_BACKFILL` | `true` | Turn EASY backfill off to see the plain queue. |

## Verifying it worked

```bash
curl -s localhost:8000/api/health
```

Which returns exactly:

```json
{"status":"ok","app":"Karkhana","version":"1.0.0","environment":"development","backfill":true,"aging_minutes":30}
```

Then open <http://localhost:8000> and sign in with the pre-filled demo account.
API docs are at <http://localhost:8000/api/docs>, and the machine-readable spec
at `/api/openapi.json` — both stay mounted in every environment, including
production.

## Running the tests

```bash
pytest -q
```

Tests use an in-memory SQLite database (`sqlite://`), with a **fresh engine
created and disposed per test** — so tests cannot leak state into each other —
and need no running server.

## Common problems

**`SECRET_KEY is still the development default`** — this only fires when
`ENVIRONMENT=production`. Development happily runs on the placeholder, which is
exactly why you must not leave it there for anything reachable. Generate a real
one and set it.

**Login returns 401** — the demo user only exists if `ALLOW_DEMO_SEED=true` was
set on the boot that created the database. Note that `ALLOW_DEMO_SEED`
defaults to `true`, so a fresh dev boot is already seeded; delete `karkhana.db`
and restart if you have mutated it past usefulness.

**`database is locked`** — two uvicorn processes are writing to the same SQLite
file. Either run one, or move to Postgres.

## Known limitations

- **Rate limiting is per-process.** It is `slowapi` with an in-memory store, so
  two workers give you two independent budgets and a restart resets the counter.
- **No Content-Security-Policy header.** The app sets `X-Content-Type-Options`,
  `X-Frame-Options`, `Referrer-Policy` and `Permissions-Policy`, and adds HSTS
  in production — but not CSP, because the inline styles in the single-file SPA
  would need nonces first.
- **Registration is open.** Anyone who can reach the app can create an account;
  only the first one becomes `admin`. Put it behind a network you trust.
