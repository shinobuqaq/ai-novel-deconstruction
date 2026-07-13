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
        execute_task_sync(session, app.state.settings, task)

    final_task = client.get(f"/api/tasks/{task_id}").json()
    assert final_task["status"] == "SUCCEEDED"
    artifact_id = final_task["result_artifact_id"]
    assert artifact_id

    artifact = client.get(f"/api/artifacts/{artifact_id}/content")
    assert artifact.status_code == 200
    assert artifact.json()["response"]["echo"]["message"] == "hello"
