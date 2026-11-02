#!/usr/bin/env python3
"""Generate docs/api.md sections from a live server.

Not part of the app. Run this when the API changes and paste the output into
docs/api.md rather than hand-writing a response shape that can drift.

    ALLOW_DEMO_SEED=true uvicorn app.main:app --port 8123 &
    python scripts/capture_api_docs.py > /tmp/captured.txt
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8123"
EMAIL = "admin@karkhana.dev"
PASSWORD = "Karkhana2026!"


def call(path: str, method: str = "GET", body: dict | None = None, token: str | None = None):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def show(label: str, status: int, payload) -> None:
    print(f"\n### {label}  ->  HTTP {status}")
    print(json.dumps(payload, indent=2)[:2500])


def main() -> int:
    status, health = call("/api/health")
    show("GET /api/health", status, health)
    if status != 200:
        print("server is not up", file=sys.stderr)
        return 1

    status, token_payload = call(
        "/api/auth/login", "POST", {"email": EMAIL, "password": PASSWORD}
    )
    show("POST /api/auth/login", status, {**token_payload, "access_token": "<jwt>"})
    if status != 200:
        print("seed the database first (ALLOW_DEMO_SEED=true)", file=sys.stderr)
        return 1
    token = token_payload["access_token"]

    for path in ("/api/auth/me", "/api/nodes", "/api/dashboard", "/api/dashboard/teams"):
        status, payload = call(path, token=token)
        show(f"GET {path}", status, payload)

    status, payload = call("/api/dashboard/chargeback?days=30", token=token)
    show("GET /api/dashboard/chargeback?days=30", status, payload)

    status, payload = call("/api/scheduler/queue", token=token)
    show("GET /api/scheduler/queue", status, payload)

    status, payload = call("/api/scheduler/run", "POST", token=token)
    show("POST /api/scheduler/run", status, payload)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
