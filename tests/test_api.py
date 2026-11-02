"""API behaviour: auth, authorisation, validation and the job lifecycle."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.engine.capacity import NodeCapacity
from app.engine.telemetry import utilisation


def _node(client: TestClient, headers: dict[str, str], **overrides) -> int:
    payload = {
        "name": "hgx-test",
        "gpu_model": "H100-80GB",
        "total_gpus": 8,
        "memory_gb": 640,
        "region": "karachi-a",
    }
    payload.update(overrides)
    response = client.post("/api/nodes", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _job(client: TestClient, headers: dict[str, str], **overrides) -> dict:
    payload = {
        "name": "test-job",
        "gpus": 2,
        "span": "node",
        "priority": 5,
        "duration_minutes": 30,
    }
    payload.update(overrides)
    return client.post("/api/jobs", json=payload, headers=headers)


# ─────────────────────────── auth ───────────────────────────


def test_health_is_public(client: TestClient):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_first_registered_user_becomes_admin_of_an_empty_instance(client: TestClient):
    response = client.post(
        "/api/auth/register",
        json={"email": "boss@x.dev", "password": "StrongPass1", "team": "platform"},
    )
    assert response.status_code == 201
    assert response.json()["user"]["role"] == "admin"


def test_second_user_is_a_regular_operator(client: TestClient, auth):
    response = client.post(
        "/api/auth/register",
        json={"email": "second@x.dev", "password": "StrongPass1", "team": "research"},
    )
    assert response.status_code == 201
    assert response.json()["user"]["role"] == "operator"


def test_duplicate_email_is_rejected_case_insensitively(client: TestClient, auth):
    response = client.post(
        "/api/auth/register",
        json={"email": "TEST@karkhana.dev", "password": "StrongPass1"},
    )
    assert response.status_code == 409


def test_weak_password_is_rejected(client: TestClient):
    response = client.post(
        "/api/auth/register",
        json={"email": "weak@x.dev", "password": "alllowercase"},
    )
    assert response.status_code == 422


def test_login_with_wrong_password_is_401_and_does_not_reveal_why(client: TestClient, auth):
    response = client.post(
        "/api/auth/login",
        json={"email": "test@karkhana.dev", "password": "WrongPass123"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect email or password"


def test_login_with_unknown_email_gives_the_same_message(client: TestClient, auth):
    response = client.post(
        "/api/auth/login",
        json={"email": "nobody@x.dev", "password": "WrongPass123"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect email or password"


def test_protected_route_without_a_token_is_401(client: TestClient):
    assert client.get("/api/dashboard").status_code == 401


def test_protected_route_with_a_garbage_token_is_401(client: TestClient):
    response = client.get("/api/dashboard", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401


# ─────────────────────────── nodes ───────────────────────────


def test_create_node_and_read_it_back(client: TestClient, auth):
    node_id = _node(client, auth)
    response = client.get(f"/api/nodes/{node_id}", headers=auth)
    assert response.status_code == 200
    assert response.json()["total_gpus"] == 8


def test_duplicate_node_name_in_same_cluster_is_409(client: TestClient, auth):
    _node(client, auth)
    response = client.post(
        "/api/nodes",
        json={"name": "hgx-test", "gpu_model": "H100-80GB", "total_gpus": 8},
        headers=auth,
    )
    assert response.status_code == 409


def test_node_with_zero_gpus_is_422(client: TestClient, auth):
    response = client.post(
        "/api/nodes",
        json={"name": "bad", "gpu_model": "H100-80GB", "total_gpus": 0},
        headers=auth,
    )
    assert response.status_code == 422


def test_missing_node_is_404(client: TestClient, auth):
    assert client.get("/api/nodes/9999", headers=auth).status_code == 404


def test_draining_a_node_marks_it_unhealthy(client: TestClient, auth):
    node_id = _node(client, auth)
    response = client.post(f"/api/nodes/{node_id}/drain", headers=auth)
    assert response.status_code == 200
    assert client.get(f"/api/nodes/{node_id}", headers=auth).json()["healthy"] is False


# ─────────────────────────── jobs ───────────────────────────


def test_submit_job_queues_it_with_a_history_entry(client: TestClient, auth):
    _node(client, auth)
    response = _job(client, auth)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "queued"
    assert [event["event"] for event in body["events"]] == ["submitted"]


def test_job_requesting_more_gpus_than_any_node_is_422(client: TestClient, auth):
    _node(client, auth, total_gpus=8)
    response = _job(client, auth, gpus=16, span="node")
    assert response.status_code == 422
    assert "cannot fit" in response.json()["detail"]


def test_job_with_zero_gpus_is_422(client: TestClient, auth):
    _node(client, auth)
    assert _job(client, auth, gpus=0).status_code == 422


def test_job_priority_outside_range_is_422(client: TestClient, auth):
    _node(client, auth)
    assert _job(client, auth, priority=99).status_code == 422


def test_cancelling_a_queued_job_sets_its_status(client: TestClient, auth):
    _node(client, auth)
    job = _job(client, auth).json()
    response = client.post(f"/api/jobs/{job['id']}/cancel", headers=auth)
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


def test_cancelling_twice_is_409(client: TestClient, auth):
    _node(client, auth)
    job = _job(client, auth).json()
    client.post(f"/api/jobs/{job['id']}/cancel", headers=auth)
    assert client.post(f"/api/jobs/{job['id']}/cancel", headers=auth).status_code == 409


def test_status_filter_rejects_unknown_values(client: TestClient, auth):
    response = client.get("/api/jobs?status=banana", headers=auth)
    assert response.status_code == 422


# ─────────────────────────── scheduling end to end ───────────────────────────


def test_scheduler_moves_a_queued_job_to_running_and_allocates_gpus(client: TestClient, auth):
    _node(client, auth, total_gpus=8)
    job_id = _job(client, auth, gpus=4).json()["id"]

    result = client.post("/api/scheduler/run", headers=auth)
    assert result.status_code == 200
    assert result.json()["scheduled"][0]["job_id"] == job_id

    dashboard = client.get("/api/dashboard", headers=auth).json()
    assert dashboard["allocated_gpus"] == 4
    assert dashboard["jobs_running"] == 1


def test_completing_a_job_releases_its_gpus(client: TestClient, auth):
    _node(client, auth, total_gpus=8)
    job_id = _job(client, auth, gpus=4).json()["id"]
    client.post("/api/scheduler/run", headers=auth)

    response = client.post(f"/api/jobs/{job_id}/complete?exit_code=0", headers=auth)
    assert response.status_code == 200
    assert response.json()["status"] == "completed"

    dashboard = client.get("/api/dashboard", headers=auth).json()
    assert dashboard["allocated_gpus"] == 0


def test_failed_exit_code_marks_the_job_failed(client: TestClient, auth):
    _node(client, auth, total_gpus=8)
    job_id = _job(client, auth, gpus=2).json()["id"]
    client.post("/api/scheduler/run", headers=auth)

    response = client.post(f"/api/jobs/{job_id}/complete?exit_code=1", headers=auth)
    assert response.json()["status"] == "failed"
    assert response.json()["exit_code"] == 1


def test_completing_a_queued_job_is_409(client: TestClient, auth):
    _node(client, auth)
    job_id = _job(client, auth).json()["id"]
    assert client.post(f"/api/jobs/{job_id}/complete", headers=auth).status_code == 409


def test_scheduler_run_is_safe_twice(client: TestClient, auth):
    """A second pass must not double-book the same GPUs."""
    _node(client, auth, total_gpus=8)
    _job(client, auth, gpus=6)
    client.post("/api/scheduler/run", headers=auth)
    client.post("/api/scheduler/run", headers=auth)

    dashboard = client.get("/api/dashboard", headers=auth).json()
    assert dashboard["allocated_gpus"] == 6
    assert dashboard["jobs_running"] == 1


def test_scheduler_with_no_nodes_says_so(client: TestClient, auth):
    response = client.post("/api/scheduler/run", headers=auth)
    assert response.status_code == 200
    assert "No nodes" in response.json()["message"]


# ─────────────────────────── billing endpoint ───────────────────────────


def test_chargeback_reports_the_finished_job(client: TestClient, auth):
    _node(client, auth, total_gpus=8)
    job_id = _job(client, auth, gpus=2).json()["id"]
    client.post("/api/scheduler/run", headers=auth)
    client.post(f"/api/jobs/{job_id}/complete?exit_code=0", headers=auth)

    report = client.get("/api/dashboard/chargeback?days=30", headers=auth).json()
    assert report["total_cost"] > 0
    assert report["teams"][0]["team"] == "platform"
    assert report["teams"][0]["gpu_hours"] > 0


# ─────────────────────────── telemetry ───────────────────────────


def test_utilisation_counts_dead_nodes_in_the_denominator():
    """A broken node must show as a drop in utilisation, not disappear."""
    nodes = [
        NodeCapacity(name="live", gpu_model="X", total_gpus=8, allocated_gpus=4),
        NodeCapacity(name="dead", gpu_model="X", total_gpus=8, healthy=False),
    ]
    stats = utilisation(nodes)
    assert stats.total_gpus == 16
    assert stats.allocated_gpus == 4
    assert stats.utilisation_percent == 25.0


def test_utilisation_of_an_empty_cluster_is_zero_not_a_crash():
    assert utilisation([]).utilisation_percent == 0.0
