from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.providers.openai_responses import OpenAIResponsesProvider
from app.services.provider_config import (
    ENTITIES_EVENTS_PROFILE_ID,
    ModelSettingsError,
    ModelProbeResult,
    discover_models,
    read_model_settings,
    save_analysis_profile,
    save_model_service,
    probe_selected_model,
)


def test_model_settings_api_keeps_secrets_local_and_supports_multiple_services(client) -> None:
    initial = client.get("/api/settings/models")
    assert initial.status_code == 200
    default_service = initial.json()["services"][0]
    assert default_service["configured"] is False

    saved = client.put(
        f"/api/settings/model-services/{default_service['id']}",
        json={
            "name": "主要分析服务",
            "service_type": "OPENAI",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-primary-secret",
        },
    )
    assert saved.status_code == 200
    assert saved.json()["configured"] is True
    assert "api_key" not in saved.json()

    second = client.post(
        "/api/settings/model-services",
        json={
            "name": "兼容服务",
            "service_type": "OPENAI_COMPATIBLE",
            "base_url": "https://example.test/v1",
            "api_key": "sk-compatible-secret",
        },
    )
    assert second.status_code == 201
    loaded = client.get("/api/settings/models").json()
    assert [item["name"] for item in loaded["services"]] == ["主要分析服务", "兼容服务"]
    assert "api_key" not in json.dumps(loaded)

    stored = json.loads(
        (client.app.state.settings.workspace_dir / "secrets" / "model_settings.json").read_text(
            encoding="utf-8"
        )
    )
    assert stored["services"][0]["api_key"] == "sk-primary-secret"
    assert stored["services"][1]["api_key"] == "sk-compatible-secret"

    deleted = client.delete(f"/api/settings/model-services/{second.json()['id']}")
    assert deleted.status_code == 204
    assert [item["name"] for item in client.get("/api/settings/models").json()["services"]] == [
        "主要分析服务"
    ]


def test_legacy_openai_config_is_read_without_destroying_it(client) -> None:
    path = client.app.state.settings.workspace_dir / "secrets" / "openai.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "api_key": "sk-legacy",
                "base_url": "https://legacy.example/v1",
                "model": "legacy-model",
            }
        ),
        encoding="utf-8",
    )

    settings = read_model_settings(client.app.state.settings)
    assert settings.services[0].api_key == "sk-legacy"
    assert settings.services[0].base_url == "https://legacy.example/v1"
    assert settings.analysis_profiles[0].model == "legacy-model"
    assert path.is_file()


def test_model_catalog_and_compatible_request_use_saved_profile(client) -> None:
    settings = client.app.state.settings
    service = save_model_service(
        settings,
        service_id="openai-default",
        name="兼容服务",
        service_type="OPENAI_COMPATIBLE",
        base_url="https://provider.example/v1",
        api_key="sk-test",
    )
    save_analysis_profile(
        settings,
        profile_id=ENTITIES_EVENTS_PROFILE_ID,
        name="人物与事件精确提取",
        service_id=service.id,
        model="quality-model",
        temperature=0.35,
        max_output_tokens=4096,
        reasoning_effort="medium",
        timeout_seconds=90,
        max_retries=4,
    )
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "z-model"}, {"id": "a-model"}]})
        body = json.loads(request.content)
        assert body["model"] == "quality-model"
        assert body["temperature"] == 0.35
        assert body["max_tokens"] == 4096
        assert body["reasoning_effort"] == "medium"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"entities": [], "events": []}'}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8},
            },
        )

    transport = httpx.MockTransport(handler)
    models = asyncio.run(discover_models(settings, service.id, transport=transport))
    assert models == ["a-model", "z-model"]

    provider = OpenAIResponsesProvider(settings, transport=transport)
    response = asyncio.run(
        provider.complete(
            task_kind="analysis.entities_events",
            payload={
                "model_profile_id": ENTITIES_EVENTS_PROFILE_ID,
                "instructions": "只返回 JSON",
                "input": "测试文本",
                "output_schema": {"type": "object"},
            },
        )
    )
    assert response.provider_id == service.id
    assert response.model == "quality-model"
    assert response.parameters["max_retries"] == 4
    assert [request.url.path for request in seen] == ["/v1/models", "/v1/chat/completions"]


def test_default_profile_uses_auto_parameters_and_accepts_one_token(client) -> None:
    initial = client.get("/api/settings/models").json()
    profile = initial["analysis_profiles"][0]
    assert profile["temperature"] is None
    assert profile["reasoning_effort"] == "auto"

    response = client.put(
        f"/api/settings/analysis-profiles/{profile['id']}",
        json={
            "name": profile["name"],
            "service_id": profile["service_id"],
            "model": "tiny-model",
            "temperature": None,
            "max_output_tokens": 1,
            "reasoning_effort": "auto",
            "timeout_seconds": 30,
            "max_retries": 0,
        },
    )
    assert response.status_code == 200
    assert response.json()["max_output_tokens"] == 1


def test_version_one_defaults_migrate_to_auto_without_changing_manual_values(client) -> None:
    path = client.app.state.settings.workspace_dir / "secrets" / "model_settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "services": [
                    {
                        "id": "openai-default",
                        "name": "OpenAI",
                        "service_type": "OPENAI",
                        "base_url": "https://api.openai.com/v1",
                        "api_key": "sk-test",
                    }
                ],
                "analysis_profiles": [
                    {
                        "id": "entities-events",
                        "name": "人物与事件精确提取",
                        "task_type": "ENTITIES_EVENTS",
                        "service_id": "openai-default",
                        "model": "model-a",
                        "temperature": 0.2,
                        "max_output_tokens": 16000,
                        "reasoning_effort": "low",
                        "timeout_seconds": 180,
                        "max_retries": 2,
                    },
                    {
                        "id": "manual-profile",
                        "name": "手工方案",
                        "task_type": "ENTITIES_EVENTS",
                        "service_id": "openai-default",
                        "model": "model-b",
                        "temperature": 0.7,
                        "max_output_tokens": 8000,
                        "reasoning_effort": "medium",
                        "timeout_seconds": 180,
                        "max_retries": 2,
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    loaded = read_model_settings(client.app.state.settings)
    assert loaded.analysis_profiles[0].temperature is None
    assert loaded.analysis_profiles[0].reasoning_effort == "auto"
    assert loaded.analysis_profiles[1].temperature == 0.7
    assert loaded.analysis_profiles[1].reasoning_effort == "medium"


def test_selected_model_probe_records_strict_capabilities(client) -> None:
    settings = client.app.state.settings
    service = save_model_service(
        settings,
        service_id="openai-default",
        name="严格 JSON 服务",
        service_type="OPENAI_COMPATIBLE",
        base_url="https://provider.example/v1",
        api_key="sk-test",
    )
    save_analysis_profile(
        settings,
        profile_id=ENTITIES_EVENTS_PROFILE_ID,
        name="人物与事件精确提取",
        service_id=service.id,
        model="strict-model",
        temperature=None,
        max_output_tokens=4096,
        reasoning_effort="auto",
        timeout_seconds=30,
        max_retries=1,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "strict-model"
        assert "temperature" not in body
        assert "reasoning_effort" not in body
        assert body["response_format"]["type"] == "json_schema"
        wire_schema = body["response_format"]["json_schema"]["schema"]
        assert "$schema" not in wire_schema
        assert "$id" not in wire_schema
        assert wire_schema["title"] == "Model capability probe"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"ok": true}'}}],
                "usage": {"prompt_tokens": 8, "completion_tokens": 3},
            },
        )

    result = asyncio.run(probe_selected_model(settings, ENTITIES_EVENTS_PROFILE_ID, transport=httpx.MockTransport(handler)))
    assert isinstance(result, ModelProbeResult)
    assert result.service.capabilities.tested_model == "strict-model"
    assert result.service.capabilities.ordinary_request == "SUPPORTED"
    assert result.service.capabilities.structured_output == "STRICT_JSON_SCHEMA"


def test_selected_model_probe_endpoint_returns_user_facing_result(client, monkeypatch) -> None:
    async def fake_probe(settings, profile_id):
        assert profile_id == ENTITIES_EVENTS_PROFILE_ID
        service = read_model_settings(settings).services[0]
        return ModelProbeResult(service, "模型测试完成。")

    monkeypatch.setattr("app.api.probe_selected_model", fake_probe)
    response = client.post(
        f"/api/settings/analysis-profiles/{ENTITIES_EVENTS_PROFILE_ID}/test"
    )
    assert response.status_code == 200
    assert response.json()["message"] == "模型测试完成。"
    assert response.json()["service"]["capabilities"]["structured_output"] == "UNTESTED"


def test_selected_model_probe_replaces_stale_success_with_failure(client) -> None:
    settings = client.app.state.settings
    service = save_model_service(
        settings,
        service_id="openai-default",
        name="权限失效服务",
        service_type="OPENAI_COMPATIBLE",
        base_url="https://provider.example/v1",
        api_key="sk-test",
    )
    save_analysis_profile(
        settings,
        profile_id=ENTITIES_EVENTS_PROFILE_ID,
        name="人物与事件精确提取",
        service_id=service.id,
        model="expired-model",
        temperature=None,
        max_output_tokens=4096,
        reasoning_effort="auto",
        timeout_seconds=30,
        max_retries=1,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"message": "forbidden"}})

    with pytest.raises(ModelSettingsError) as caught:
        asyncio.run(
            probe_selected_model(
                settings,
                ENTITIES_EVENTS_PROFILE_ID,
                transport=httpx.MockTransport(handler),
            )
        )
    assert caught.value.code == "PROVIDER_AUTH_FAILED"
    capabilities = read_model_settings(settings).services[0].capabilities
    assert capabilities.tested_model == "expired-model"
    assert capabilities.ordinary_request == "FAILED"
    assert capabilities.structured_output == "UNTESTED"


def test_selected_model_probe_falls_back_and_provider_filters_rejected_parameters(client) -> None:
    settings = client.app.state.settings
    service = save_model_service(
        settings,
        service_id="openai-default",
        name="普通 JSON 服务",
        service_type="OPENAI_COMPATIBLE",
        base_url="https://provider.example/v1",
        api_key="sk-test",
    )
    save_analysis_profile(
        settings,
        profile_id=ENTITIES_EVENTS_PROFILE_ID,
        name="人物与事件精确提取",
        service_id=service.id,
        model="json-model",
        temperature=0.35,
        max_output_tokens=4096,
        reasoning_effort="medium",
        timeout_seconds=30,
        max_retries=1,
    )
    probe_calls: list[dict] = []

    def probe_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        probe_calls.append(body)
        if "response_format" in body or "temperature" in body or "reasoning_effort" in body:
            return httpx.Response(400, json={"error": {"message": "unsupported parameter"}})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"ok": true}'}}]},
        )

    result = asyncio.run(probe_selected_model(settings, ENTITIES_EVENTS_PROFILE_ID, transport=httpx.MockTransport(probe_handler)))
    assert len(probe_calls) == 4
    assert result.service.capabilities.structured_output == "JSON_ONLY"
    assert result.service.capabilities.temperature == "UNSUPPORTED"
    assert result.service.capabilities.reasoning_effort == "UNSUPPORTED"

    def analysis_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "response_format" not in body
        assert "temperature" not in body
        assert "reasoning_effort" not in body
        assert "output_schema" not in body["messages"][0]["content"]
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"entities": [], "events": []}'}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8},
            },
        )

    provider = OpenAIResponsesProvider(settings, transport=httpx.MockTransport(analysis_handler))
    response = asyncio.run(
        provider.complete(
            task_kind="analysis.entities_events",
            payload={
                "model_profile_id": ENTITIES_EVENTS_PROFILE_ID,
                "instructions": "只返回 JSON",
                "input": "测试文本",
                "output_schema": {"type": "object"},
            },
        )
    )
    assert response.parameters["structured_output"] == "JSON_ONLY"
