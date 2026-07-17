from __future__ import annotations

from app.repositories import claim_next_task
from app.services.tasks import execute_task_sync


def test_project_task_artifact_flow(client):
    project_response = client.post("/api/projects", json={"name": "测试小说"})
    assert project_response.status_code == 201
    project_id = project_response.json()["id"]

    task_response = client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "kind": "fake.echo",
            "payload": {"message": "hello"},
        },
    )
    assert task_response.status_code == 201
    task_id = task_response.json()["id"]

    app = client.app
    with app.state.session_factory() as session:
        task = claim_next_task(session, worker_id="pytest-worker", lease_seconds=60)
        assert task is not None
        assert task.id == task_id
    assert execute_task_sync(
        app.state.session_factory,
        app.state.settings,
        task,
        app.state.provider_registry,
    )

    final_task = client.get(f"/api/tasks/{task_id}").json()
    assert final_task["status"] == "SUCCEEDED"
    artifact_id = final_task["result_artifact_id"]
    assert artifact_id

    artifact_record = client.get("/api/artifacts").json()[0]
    assert artifact_record["id"] == artifact_id
    assert artifact_record["blob_id"].startswith("blb_")
    assert artifact_record["result_key"] == f"{task_id}:fake.echo.result"

    artifact = client.get(f"/api/artifacts/{artifact_id}/content")
    assert artifact.status_code == 200
    assert artifact.json()["response"]["echo"]["message"] == "hello"


def test_running_task_cancellation_is_acknowledged_by_worker(client):
    project = client.post("/api/projects", json={"name": "取消测试"}).json()
    created = client.post(
        "/api/tasks",
        json={
            "project_id": project["id"],
            "kind": "fake.echo",
            "payload": {"message": "cancel me"},
        },
    ).json()

    with client.app.state.session_factory() as session:
        claim = claim_next_task(
            session,
            worker_id="pytest-cancel-worker",
            lease_seconds=60,
        )
        assert claim is not None

    requested = client.post(f"/api/tasks/{created['id']}/cancel")
    assert requested.status_code == 200
    assert requested.json()["status"] == "CANCEL_REQUESTED"

    assert not execute_task_sync(
        client.app.state.session_factory,
        client.app.state.settings,
        claim,
        client.app.state.provider_registry,
    )
    final_task = client.get(f"/api/tasks/{created['id']}").json()
    assert final_task["status"] == "CANCELLED"
    assert final_task["result_artifact_id"] is None


def test_pending_task_cancellation_is_idempotent(client):
    project = client.post("/api/projects", json={"name": "幂等取消"}).json()
    created = client.post(
        "/api/tasks",
        json={"project_id": project["id"], "kind": "fake.echo"},
    ).json()

    first = client.post(f"/api/tasks/{created['id']}/cancel")
    second = client.post(f"/api/tasks/{created['id']}/cancel")
    retried = client.post(f"/api/tasks/{created['id']}/retry")

    assert first.status_code == 200
    assert first.json()["status"] == "CANCELLED"
    assert second.json()["status"] == "CANCELLED"
    assert second.json()["cancel_requested_at"] == first.json()["cancel_requested_at"]
    assert retried.json()["status"] == "CANCELLED"
