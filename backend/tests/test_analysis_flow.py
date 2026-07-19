from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.models import EntityCandidate, EventCandidate, Task
from app.providers.base import ProviderError, ProviderResponse
from app.providers.openai_responses import OpenAIResponsesProvider
from app.providers.registry import ProviderRegistry
from app.repositories import claim_next_task
from app.services.analysis import parse_provider_output, persist_analysis_output
from app.services.provider_config import (
    ENTITIES_EVENTS_PROFILE_ID,
    save_analysis_profile,
    save_model_service,
)
from app.services.tasks import execute_task_sync


ANALYSIS_OUTPUT = {
    "entities": [
        {
            "name": "林舟",
            "entity_type": "PERSON",
            "aliases": [],
            "description": "在雨夜回到旧宅的人。",
            "evidence_quotes": ["林舟推开旧宅的木门"],
            "confidence": 96,
        },
        {
            "name": "不存在的人",
            "entity_type": "PERSON",
            "aliases": [],
            "description": "这条候选没有原文依据。",
            "evidence_quotes": ["原文里没有这句话"],
            "confidence": 10,
        },
    ],
    "events": [
        {
            "title": "林舟发现密信",
            "event_type": "DISCOVERY",
            "summary": "林舟在桌上发现一封写着自己名字的密信。",
            "participants": ["林舟"],
            "evidence_quotes": ["桌上放着一封写着他名字的密信"],
            "confidence": 94,
        }
    ],
}


class StaticAnalysisProvider:
    name = "openai"

    async def complete(self, *, task_kind: str, payload: dict) -> ProviderResponse:
        assert task_kind == "analysis.entities_events"
        assert "林舟推开旧宅的木门" in payload["input"]
        return ProviderResponse(
            raw_text=json.dumps(ANALYSIS_OUTPUT, ensure_ascii=False),
            parsed=ANALYSIS_OUTPUT,
            prompt_tokens=120,
            completion_tokens=80,
        )


class AuthenticationFailureProvider:
    name = "openai"

    async def complete(self, *, task_kind: str, payload: dict) -> ProviderResponse:
        raise ProviderError(
            code="PROVIDER_AUTH_FAILED",
            message="API Key 无效或没有使用该模型的权限。",
            retryable=False,
        )


def _import_confirmed_novel(client) -> dict:
    project = client.post("/api/projects", json={"name": "雨夜旧宅"}).json()
    source = (
        "第一章 归来\n"
        "雨下得很大，林舟推开旧宅的木门。\n"
        "桌上放着一封写着他名字的密信。\n"
        "第二章 决定\n"
        "林舟决定天亮后去找寄信人。"
    )
    imported = client.post(
        f"/api/projects/{project['id']}/sources/import?filename=rain.txt",
        content=source.encode("utf-8"),
    )
    assert imported.status_code == 201
    result = imported.json()
    assert not [item for item in result["issues"] if item["severity"] == "BLOCKING"]
    confirmed = client.post(f"/api/source-versions/{result['version']['id']}/confirm")
    assert confirmed.status_code == 200
    return result


def test_analysis_requires_local_provider_configuration(client) -> None:
    imported = _import_confirmed_novel(client)

    response = client.post(
        f"/api/source-versions/{imported['version']['id']}/analysis/entities-events/start"
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PROVIDER_NOT_CONFIGURED"


def test_provider_config_never_returns_plain_api_key(client) -> None:
    saved = client.put(
        "/api/settings/openai",
        json={"api_key": "sk-test-secret-value"},
    )

    assert saved.status_code == 200
    assert saved.json()["configured"] is True
    assert "api_key" not in saved.json()
    loaded = client.get("/api/settings/openai")
    assert "api_key" not in loaded.json()
    config_path = client.app.state.settings.workspace_dir / "secrets" / "openai.json"
    assert json.loads(config_path.read_text(encoding="utf-8"))["api_key"] == "sk-test-secret-value"


def test_entities_events_flow_keeps_exact_source_evidence_and_is_idempotent(client) -> None:
    imported = _import_confirmed_novel(client)
    client.put("/api/settings/openai", json={"api_key": "sk-test"})
    version_id = imported["version"]["id"]

    started = client.post(
        f"/api/source-versions/{version_id}/analysis/entities-events/start"
    )
    assert started.status_code == 201
    run = started.json()
    assert run["status"] == "PENDING"
    assert run["total_batches"] == 1

    registry = ProviderRegistry([StaticAnalysisProvider()])
    with client.app.state.session_factory() as session:
        claim = claim_next_task(session, worker_id="analysis-test-worker", lease_seconds=60)
    assert claim is not None
    assert execute_task_sync(
        client.app.state.session_factory,
        client.app.state.settings,
        claim,
        registry,
    )

    progress = client.get(
        f"/api/source-versions/{version_id}/analysis/entities-events"
    ).json()
    assert progress["status"] == "REVIEW"
    assert progress["completed_batches"] == 1
    assert progress["failed_batches"] == 0

    completed_task = client.get(f"/api/tasks/{claim.id}").json()
    artifact = client.get(
        f"/api/artifacts/{completed_task['result_artifact_id']}/content"
    ).json()
    assert artifact["request"]["prompt_id"] == "entities_events"
    assert artifact["request"]["prompt_version"] == "1.0.0"
    assert artifact["request"]["source_version_id"] == version_id
    assert len(artifact["request"]["input_sha256"]) == 64
    assert "参与事件的人物" in artifact["request"]["instructions"]

    entities = client.get(f"/api/analysis-runs/{run['id']}/entities").json()
    events = client.get(f"/api/analysis-runs/{run['id']}/events").json()
    assert [item["name"] for item in entities] == ["林舟"]
    assert [item["title"] for item in events] == ["林舟发现密信"]
    assert entities[0]["status"] == "VALID"
    assert events[0]["status"] == "VALID"

    workbench = client.get(f"/api/analysis-runs/{run['id']}/workbench")
    assert workbench.status_code == 200
    projection = workbench.json()
    assert [item["name"] for item in projection["characters"]] == ["林舟"]
    assert projection["events"][0]["people"] == ["林舟"]
    assert projection["events"][0]["chapter_titles"] == ["第一章 归来"]
    assert len(projection["phases"]) == 1
    assert projection["phases"][0]["event_ids"] == [projection["events"][0]["id"]]

    evidence = client.get(f"/api/evidence/{events[0]['evidence_ids'][0]}")
    assert evidence.status_code == 200
    assert "桌上放着一封写着他名字的密信" in evidence.json()["context_text"]
    assert evidence.json()["chapter_title"] == "第一章 归来"

    with client.app.state.session_factory() as session:
        task = session.scalar(select(Task).where(Task.id == claim.id))
        assert task is not None
        output = parse_provider_output(ANALYSIS_OUTPUT)
        persist_analysis_output(
            session,
            client.app.state.settings,
            task=task,
            attempt_id=claim.current_attempt_id,
            task_payload=json.loads(task.payload_json),
            output=output,
        )
        assert session.scalar(select(func.count(EntityCandidate.id))) == 2
        assert session.scalar(select(func.count(EventCandidate.id))) == 1

    confirmed = client.post(f"/api/analysis-runs/{run['id']}/confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "CONFIRMED"


def test_running_or_stale_attempt_candidates_are_not_visible(client) -> None:
    imported = _import_confirmed_novel(client)
    client.put("/api/settings/openai", json={"api_key": "sk-test"})
    version_id = imported["version"]["id"]
    run = client.post(
        f"/api/source-versions/{version_id}/analysis/entities-events/start"
    ).json()

    with client.app.state.session_factory() as session:
        claim = claim_next_task(session, worker_id="old-worker", lease_seconds=60)
    assert claim is not None
    with client.app.state.session_factory() as session:
        task = session.get(Task, claim.id)
        assert task is not None
        persist_analysis_output(
            session,
            client.app.state.settings,
            task=task,
            attempt_id=claim.current_attempt_id,
            task_payload=json.loads(task.payload_json),
            output=parse_provider_output(ANALYSIS_OUTPUT),
        )

    assert client.get(f"/api/analysis-runs/{run['id']}/entities").json() == []
    assert client.get(f"/api/analysis-runs/{run['id']}/events").json() == []


def test_analysis_failure_exposes_plain_reason_and_can_start_again(client) -> None:
    imported = _import_confirmed_novel(client)
    client.put("/api/settings/openai", json={"api_key": "sk-invalid"})
    version_id = imported["version"]["id"]
    run = client.post(
        f"/api/source-versions/{version_id}/analysis/entities-events/start"
    ).json()
    with client.app.state.session_factory() as session:
        claim = claim_next_task(session, worker_id="auth-failure-worker", lease_seconds=60)
    assert claim is not None
    assert execute_task_sync(
        client.app.state.session_factory,
        client.app.state.settings,
        claim,
        ProviderRegistry([AuthenticationFailureProvider()]),
    )

    failed = client.get(
        f"/api/source-versions/{version_id}/analysis/entities-events"
    ).json()
    assert failed["id"] == run["id"]
    assert failed["status"] == "FAILED"
    assert failed["failure_code"] == "PROVIDER_AUTH_FAILED"
    assert failed["failure_message"] == "API Key 无效或没有使用该模型的权限。"

    restarted = client.post(
        f"/api/source-versions/{version_id}/analysis/entities-events/start"
    )
    assert restarted.status_code == 201
    assert restarted.json()["id"] != run["id"]


def _provider_settings(tmp_path: Path) -> Settings:
    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'provider.db').as_posix()}",
        workspace_dir=tmp_path / "workspace",
        openai_api_key="sk-test",
        openai_timeout_seconds=1,
    )
    settings.ensure_directories()
    return settings


def _provider_payload() -> dict:
    return {
        "instructions": "只返回结构化结果。",
        "input": "第一章\n林舟回来了。",
        "output_schema": {
            "type": "object",
            "properties": {"entities": {"type": "array"}, "events": {"type": "array"}},
            "required": ["entities", "events"],
            "additionalProperties": False,
        },
    }


def _run_provider(provider: OpenAIResponsesProvider) -> ProviderResponse:
    return asyncio.run(provider.complete(task_kind="analysis.entities_events", payload=_provider_payload()))


@pytest.mark.parametrize(
    ("status_code", "expected_code", "retryable"),
    [
        (401, "PROVIDER_AUTH_FAILED", False),
        (403, "PROVIDER_AUTH_FAILED", False),
        (429, "PROVIDER_RATE_LIMITED", True),
        (500, "PROVIDER_UNAVAILABLE", True),
        (400, "PROVIDER_BAD_REQUEST", False),
    ],
)
def test_openai_http_failures_have_stable_contract(
    tmp_path: Path,
    status_code: int,
    expected_code: str,
    retryable: bool,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        headers = {"retry-after": "17"} if status_code == 429 else {}
        return httpx.Response(status_code, headers=headers, json={"error": "test"})

    provider = OpenAIResponsesProvider(
        _provider_settings(tmp_path),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ProviderError) as caught:
        _run_provider(provider)
    assert caught.value.code == expected_code
    assert caught.value.retryable is retryable
    if status_code == 429:
        assert caught.value.retry_after_seconds == 17


def test_openai_timeout_is_retryable(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("test timeout", request=request)

    provider = OpenAIResponsesProvider(
        _provider_settings(tmp_path),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ProviderError) as caught:
        _run_provider(provider)
    assert caught.value.code == "PROVIDER_TIMEOUT"
    assert caught.value.retryable is True


def test_openai_invalid_output_is_retryable(tmp_path: Path) -> None:
    provider = OpenAIResponsesProvider(
        _provider_settings(tmp_path),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"output": []})),
    )

    with pytest.raises(ProviderError) as caught:
        _run_provider(provider)
    assert caught.value.code == "PROVIDER_INVALID_OUTPUT"
    assert caught.value.retryable is True


def test_openai_structured_output_is_parsed(tmp_path: Path) -> None:
    output_text = json.dumps({"entities": [], "events": []})
    response_body = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": output_text}],
            }
        ],
        "usage": {"input_tokens": 23, "output_tokens": 11},
    }
    provider = OpenAIResponsesProvider(
        _provider_settings(tmp_path),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=response_body)
        ),
    )

    result = _run_provider(provider)

    assert result.parsed == {"entities": [], "events": []}
    assert result.prompt_tokens == 23
    assert result.completion_tokens == 11


def test_provider_removes_schema_document_metadata_before_request(tmp_path: Path) -> None:
    output_text = json.dumps({"entities": [], "events": []})

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        wire_schema = body["text"]["format"]["schema"]
        assert "$schema" not in wire_schema
        assert "$id" not in wire_schema
        assert wire_schema["title"] == "Novel extraction result"
        assert "title" in wire_schema["properties"]
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": output_text}],
                    }
                ]
            },
        )

    payload = _provider_payload()
    payload["output_schema"] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "novel-result.schema.json",
        "title": "Novel extraction result",
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "entities": {"type": "array"},
            "events": {"type": "array"},
        },
        "required": ["entities", "events"],
        "additionalProperties": False,
    }
    provider = OpenAIResponsesProvider(
        _provider_settings(tmp_path),
        transport=httpx.MockTransport(handler),
    )

    result = asyncio.run(
        provider.complete(task_kind="analysis.entities_events", payload=payload)
    )

    assert result.parsed == {"entities": [], "events": []}


def test_compatible_provider_removes_schema_document_metadata_before_request(tmp_path: Path) -> None:
    settings = _provider_settings(tmp_path)
    service = save_model_service(
        settings,
        service_id="openai-default",
        name="兼容接口",
        service_type="OPENAI_COMPATIBLE",
        base_url="https://provider.example/v1",
        api_key="sk-test",
    )
    save_analysis_profile(
        settings,
        profile_id=ENTITIES_EVENTS_PROFILE_ID,
        name="人物与事件精确提取",
        service_id=service.id,
        model="gemini-compatible",
        temperature=None,
        max_output_tokens=4096,
        reasoning_effort="auto",
        timeout_seconds=30,
        max_retries=1,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        wire_schema = body["response_format"]["json_schema"]["schema"]
        assert "$schema" not in wire_schema
        assert "$id" not in wire_schema
        assert wire_schema["title"] == "Novel extraction result"
        assert "title" in wire_schema["properties"]
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"entities": [], "events": []}'}}]},
        )

    payload = _provider_payload()
    payload["model_profile_id"] = ENTITIES_EVENTS_PROFILE_ID
    payload["output_schema"] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "novel-result.schema.json",
        "title": "Novel extraction result",
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "entities": {"type": "array"},
            "events": {"type": "array"},
        },
        "required": ["entities", "events"],
        "additionalProperties": False,
    }
    provider = OpenAIResponsesProvider(
        settings,
        transport=httpx.MockTransport(handler),
    )

    result = asyncio.run(
        provider.complete(task_kind="analysis.entities_events", payload=payload)
    )

    assert result.parsed == {"entities": [], "events": []}


def test_provider_exposes_short_upstream_bad_request_reason(tmp_path: Path) -> None:
    provider = OpenAIResponsesProvider(
        _provider_settings(tmp_path),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                400,
                json={"error": {"message": 'Unknown name "$id" at response_schema'}},
            )
        ),
    )

    with pytest.raises(ProviderError) as caught:
        _run_provider(provider)

    assert caught.value.code == "PROVIDER_BAD_REQUEST"
    assert 'Unknown name "$id"' in str(caught.value)
