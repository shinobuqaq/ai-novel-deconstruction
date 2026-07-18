from __future__ import annotations

import asyncio
import json

import httpx

from app.providers.openai_responses import OpenAIResponsesProvider
from app.services.provider_config import (
    ENTITIES_EVENTS_PROFILE_ID,
    discover_models,
    read_model_settings,
    save_analysis_profile,
    save_model_service,
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
