from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.providers.base import ProviderError
from app.providers.openai_responses import OpenAIResponsesProvider
from app.services.provider_config import (
    ENTITIES_EVENTS_PROFILE_ID,
    PROVIDER_HTTP_USER_AGENT,
    ModelService,
    ModelSettingsError,
    ModelProbeResult,
    _stream_response_envelope,
    discover_models,
    model_service_uses_streaming,
    read_model_settings,
    save_analysis_profile,
    save_model_service,
    snapshot_provider_routes,
    probe_selected_model,
)


class ChunkedAsyncByteStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


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


def test_analysis_profile_preserves_ordered_failover_routes(client) -> None:
    settings = client.app.state.settings
    primary = save_model_service(
        settings,
        service_id="openai-default",
        name="主服务",
        service_type="OPENAI_COMPATIBLE",
        base_url="https://primary.example/v1",
        api_key="sk-primary",
    )
    backup_one = save_model_service(
        settings,
        service_id=None,
        name="第一备用",
        service_type="OPENAI_COMPATIBLE",
        base_url="https://backup-one.example/v1",
        api_key="sk-backup-one",
    )
    backup_two = save_model_service(
        settings,
        service_id=None,
        name="第二备用",
        service_type="OPENAI_COMPATIBLE",
        base_url="https://backup-two.example/v1",
        api_key="sk-backup-two",
    )

    saved = save_analysis_profile(
        settings,
        profile_id=ENTITIES_EVENTS_PROFILE_ID,
        name="带备用链的分析方案",
        service_id=primary.id,
        model="primary-model",
        temperature=None,
        max_output_tokens=4096,
        reasoning_effort="auto",
        timeout_seconds=30,
        max_retries=2,
        failover_targets=[
            {"service_id": backup_two.id, "model": "backup-two-model"},
            {"service_id": backup_one.id, "model": "backup-one-model"},
        ],
    )

    assert [(item.service_id, item.model) for item in saved.failover_targets] == [
        (backup_two.id, "backup-two-model"),
        (backup_one.id, "backup-one-model"),
    ]
    assert [item["service_name"] for item in snapshot_provider_routes(settings)] == [
        "主服务",
        "第二备用",
        "第一备用",
    ]
    profile = client.get("/api/settings/models").json()["analysis_profiles"][0]
    assert profile["failover_targets"] == [
        {"service_id": backup_two.id, "model": "backup-two-model"},
        {"service_id": backup_one.id, "model": "backup-one-model"},
    ]

    with pytest.raises(ModelSettingsError) as duplicate:
        save_analysis_profile(
            settings,
            profile_id=ENTITIES_EVENTS_PROFILE_ID,
            name="重复备用服务",
            service_id=primary.id,
            model="primary-model",
            temperature=None,
            max_output_tokens=4096,
            reasoning_effort="auto",
            timeout_seconds=30,
            max_retries=2,
            failover_targets=[
                {"service_id": backup_one.id, "model": "one"},
                {"service_id": backup_one.id, "model": "two"},
            ],
        )
    assert duplicate.value.code == "FAILOVER_SERVICE_INVALID"


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
        base_url="http://127.0.0.1:18081/v1",
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
        assert request.headers["user-agent"] == PROVIDER_HTTP_USER_AGENT
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
    assert response.prompt_tokens == 12
    assert response.completion_tokens == 8
    assert [request.url.path for request in seen] == ["/v1/models", "/v1/chat/completions"]


@pytest.mark.parametrize(
    ("base_url", "uses_streaming"),
    [
        ("http://127.0.0.1:7861/v1", False),
        ("http://localhost:7861/v1", False),
        ("http://[::1]:7861/v1", False),
        ("https://relay.example/v1", True),
        ("http://192.168.1.8:7861/v1", True),
    ],
)
def test_only_loopback_model_services_keep_full_response_mode(
    base_url: str,
    uses_streaming: bool,
) -> None:
    service = ModelService(
        id="transport-test",
        name="传输模式测试",
        service_type="OPENAI_COMPATIBLE",
        base_url=base_url,
        api_key="sk-test",
    )
    assert model_service_uses_streaming(service) is uses_streaming


def test_remote_compatible_service_streams_and_preserves_usage(client) -> None:
    settings = client.app.state.settings
    service = save_model_service(
        settings,
        service_id="openai-default",
        name="远程中转",
        service_type="OPENAI_COMPATIBLE",
        base_url="https://relay.example/v1",
        api_key="sk-test",
    )
    save_analysis_profile(
        settings,
        profile_id=ENTITIES_EVENTS_PROFILE_ID,
        name="远程流式分析",
        service_id=service.id,
        model="stream-model",
        temperature=None,
        max_output_tokens=4096,
        reasoning_effort="auto",
        timeout_seconds=360,
        max_retries=1,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["stream"] is True
        assert body["stream_options"] == {"include_usage": True}
        assert request.headers["accept"] == "text/event-stream"
        stream_body = (
            'data: {"choices":[{"delta":{"content":"{\\"entities\\":[],"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"\\"events\\":[]}"}}]}\n\n'
            'data: {"choices":[],"usage":{"prompt_tokens":52,"completion_tokens":8},'
            '"note":"分片"}\n\n'
            "data: [DONE]\n\n"
        ).encode()
        split_at = stream_body.index("分".encode()) + 1
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream; charset=utf-8"},
            stream=ChunkedAsyncByteStream([
                stream_body[:23],
                stream_body[23:split_at],
                stream_body[split_at:split_at + 1],
                stream_body[split_at + 1:],
            ]),
        )

    provider = OpenAIResponsesProvider(settings, transport=httpx.MockTransport(handler))
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

    assert response.parsed == {"entities": [], "events": []}
    assert response.prompt_tokens == 52
    assert response.completion_tokens == 8
    assert response.parameters["transport_mode"] == "STREAMING"


@pytest.mark.parametrize(
    ("service_type", "raw_stream"),
    [
        (
            "OPENAI_COMPATIBLE",
            'data: {"choices":[{"delta":{"content":"{\\"ok\\":true}"}}]}\n\n',
        ),
        (
            "OPENAI",
            (
                "event: response.output_text.delta\n"
                'data: {"type":"response.output_text.delta",'
                '"delta":"{\\"ok\\":true}"}\n\n'
            ),
        ),
    ],
)
def test_stream_parser_rejects_eof_before_completion_signal(
    service_type: str,
    raw_stream: str,
) -> None:
    with pytest.raises(ValueError, match="完整结束标记前中断"):
        _stream_response_envelope(raw_stream, service_type)


def test_compatible_stream_accepts_finish_reason_without_done_marker() -> None:
    envelope = _stream_response_envelope(
        (
            'data: {"choices":[{"delta":{"content":"{\\"ok\\":true}"},'
            '"finish_reason":"stop"}]}\n\n'
        ),
        "OPENAI_COMPATIBLE",
    )

    assert envelope["choices"][0]["message"]["content"] == '{"ok":true}'


def test_openai_stream_accepts_done_marker_without_completed_event() -> None:
    envelope = _stream_response_envelope(
        (
            "event: response.output_text.delta\n"
            'data: {"type":"response.output_text.delta",'
            '"delta":"{\\"ok\\":true}"}\n\n'
            "data: [DONE]\n\n"
        ),
        "OPENAI",
    )

    assert envelope["output"][0]["content"][0]["text"] == '{"ok":true}'


@pytest.mark.parametrize(
    ("service_type", "raw_stream"),
    [
        (
            "OPENAI_COMPATIBLE",
            (
                'data: {"choices":[{"delta":{"content":"{\\"ok\\":"},'
                '"finish_reason":"length"}]}\n\n'
                "data: [DONE]\n\n"
            ),
        ),
        (
            "OPENAI_COMPATIBLE",
            (
                'data: {"choices":[{"delta":{"content":"{\\"ok\\":"},'
                '"finish_reason":"content_filter"}]}\n\n'
                "data: [DONE]\n\n"
            ),
        ),
        (
            "OPENAI_COMPATIBLE",
            (
                'data: {"choices":[{"delta":{"content":"{\\"ok\\":"},'
                '"finish_reason":"tool_calls"}]}\n\n'
                "data: [DONE]\n\n"
            ),
        ),
        (
            "OPENAI",
            (
                "event: response.incomplete\n"
                'data: {"type":"response.incomplete",'
                '"response":{"status":"incomplete"}}\n\n'
            ),
        ),
        (
            "OPENAI",
            (
                "event: response.failed\n"
                'data: {"type":"response.failed",'
                '"response":{"status":"failed"}}\n\n'
            ),
        ),
    ],
)
def test_stream_parser_rejects_incomplete_completion(
    service_type: str,
    raw_stream: str,
) -> None:
    with pytest.raises(ValueError, match="未完整|未正常结束"):
        _stream_response_envelope(raw_stream, service_type)


def test_remote_stream_falls_back_when_include_usage_is_rejected(client) -> None:
    settings = client.app.state.settings
    service = save_model_service(
        settings,
        service_id="openai-default",
        name="旧式远程中转",
        service_type="OPENAI_COMPATIBLE",
        base_url="https://legacy-relay.example/v1",
        api_key="sk-test",
    )
    save_analysis_profile(
        settings,
        profile_id=ENTITIES_EVENTS_PROFILE_ID,
        name="远程流式兼容",
        service_id=service.id,
        model="legacy-stream-model",
        temperature=None,
        max_output_tokens=4096,
        reasoning_effort="auto",
        timeout_seconds=360,
        max_retries=1,
    )
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if "stream_options" in body:
            return httpx.Response(
                400,
                json={"error": {"message": "Unsupported parameter: stream_options"}},
            )
        assert body["stream"] is True
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                'data: {"choices":[{"delta":{"content":"{\\"entities\\":[],\\"events\\":[]}"}}]}\n\n'
                "data: [DONE]\n\n"
            ).encode(),
        )

    provider = OpenAIResponsesProvider(settings, transport=httpx.MockTransport(handler))
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

    assert len(calls) == 2
    assert "stream_options" in calls[0]
    assert "stream_options" not in calls[1]
    assert response.parsed == {"entities": [], "events": []}
    assert response.parameters["transport_mode"] == "STREAMING"


def test_remote_service_rejects_non_streaming_response(client) -> None:
    settings = client.app.state.settings
    service = save_model_service(
        settings,
        service_id="openai-default",
        name="伪流式远程中转",
        service_type="OPENAI_COMPATIBLE",
        base_url="https://non-streaming-relay.example/v1",
        api_key="sk-test",
    )
    save_analysis_profile(
        settings,
        profile_id=ENTITIES_EVENTS_PROFILE_ID,
        name="远程流式校验",
        service_id=service.id,
        model="non-streaming-model",
        temperature=None,
        max_output_tokens=4096,
        reasoning_effort="auto",
        timeout_seconds=360,
        max_retries=1,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["stream"] is True
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"entities":[],"events":[]}'}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            },
        )

    provider = OpenAIResponsesProvider(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError) as caught:
        asyncio.run(
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

    assert caught.value.code == "PROVIDER_STREAMING_UNSUPPORTED"
    assert "必须支持流式传输" in str(caught.value)


def test_loopback_compatible_service_keeps_full_response_mode(client) -> None:
    settings = client.app.state.settings
    service = save_model_service(
        settings,
        service_id="openai-default",
        name="本机 Gemini 桥接",
        service_type="OPENAI_COMPATIBLE",
        base_url="http://127.0.0.1:7861/v1",
        api_key="sk-test",
    )
    save_analysis_profile(
        settings,
        profile_id=ENTITIES_EVENTS_PROFILE_ID,
        name="本机整包分析",
        service_id=service.id,
        model="gemini-local",
        temperature=None,
        max_output_tokens=4096,
        reasoning_effort="auto",
        timeout_seconds=360,
        max_retries=1,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "stream" not in body
        assert "stream_options" not in body
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"entities":[],"events":[]}'}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            },
        )

    provider = OpenAIResponsesProvider(settings, transport=httpx.MockTransport(handler))
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

    assert response.prompt_tokens == 12
    assert response.completion_tokens == 4
    assert response.parameters["transport_mode"] == "LOCAL_FULL_RESPONSE"


@pytest.mark.parametrize(
    "task_kind",
    [
        "analysis.character_design_evidence",
        "analysis.chapter_end_hooks",
        "analysis.learning_report",
        "analysis.opening_hook_payoffs",
    ],
)
def test_loopback_long_form_analysis_uses_streaming(
    client,
    task_kind: str,
) -> None:
    settings = client.app.state.settings
    service = save_model_service(
        settings,
        service_id="openai-default",
        name="本机 Gemini 桥接",
        service_type="OPENAI_COMPATIBLE",
        base_url="http://127.0.0.1:7861/v1",
        api_key="sk-test",
    )
    save_analysis_profile(
        settings,
        profile_id=ENTITIES_EVENTS_PROFILE_ID,
        name="本机长报告流式分析",
        service_id=service.id,
        model="gemini-local",
        temperature=None,
        max_output_tokens=30_000,
        reasoning_effort="auto",
        timeout_seconds=360,
        max_retries=1,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["stream"] is True
        assert body["stream_options"] == {"include_usage": True}
        assert request.headers["accept"] == "text/event-stream"
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                'data: {"choices":[{"delta":{"content":"{\\"answers\\":[]}"}}]}\n\n'
                'data: {"choices":[],"usage":{"prompt_tokens":79582,'
                '"completion_tokens":15892}}\n\n'
                "data: [DONE]\n\n"
            ).encode(),
        )

    provider = OpenAIResponsesProvider(settings, transport=httpx.MockTransport(handler))
    response = asyncio.run(
        provider.complete(
            task_kind=task_kind,
            payload={
                "model_profile_id": ENTITIES_EVENTS_PROFILE_ID,
                "instructions": "只返回 JSON",
                "input": "长报告测试文本",
                "output_schema": {"type": "object"},
            },
        )
    )

    assert response.parsed == {"answers": []}
    assert response.prompt_tokens == 79_582
    assert response.completion_tokens == 15_892
    assert response.parameters["transport_mode"] == "STREAMING"


def test_remote_openai_responses_service_streams(client) -> None:
    settings = client.app.state.settings
    service = save_model_service(
        settings,
        service_id="openai-default",
        name="远程 OpenAI",
        service_type="OPENAI",
        base_url="https://api.openai.example/v1",
        api_key="sk-test",
    )
    save_analysis_profile(
        settings,
        profile_id=ENTITIES_EVENTS_PROFILE_ID,
        name="远程 Responses 流式分析",
        service_id=service.id,
        model="responses-model",
        temperature=None,
        max_output_tokens=4096,
        reasoning_effort="auto",
        timeout_seconds=360,
        max_retries=1,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["stream"] is True
        assert "stream_options" not in body
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                'event: response.output_text.delta\n'
                'data: {"type":"response.output_text.delta","delta":"{\\"entities\\":[],"}\n\n'
                'event: response.output_text.delta\n'
                'data: {"type":"response.output_text.delta","delta":"\\"events\\":[]}"}\n\n'
                'event: response.completed\n'
                'data: {"type":"response.completed","response":{"usage":{"input_tokens":31,"output_tokens":9}}}\n\n'
            ).encode(),
        )

    provider = OpenAIResponsesProvider(settings, transport=httpx.MockTransport(handler))
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

    assert response.parsed == {"entities": [], "events": []}
    assert response.prompt_tokens == 31
    assert response.completion_tokens == 9
    assert response.parameters["transport_mode"] == "STREAMING"


def test_invalid_output_preserves_token_usage(client) -> None:
    settings = client.app.state.settings
    service = save_model_service(
        settings,
        service_id="openai-default",
        name="令牌记录失败测试",
        service_type="OPENAI_COMPATIBLE",
        base_url="http://127.0.0.1:18081/v1",
        api_key="sk-test",
    )
    save_analysis_profile(
        settings,
        profile_id=ENTITIES_EVENTS_PROFILE_ID,
        name="人物与事件精确提取",
        service_id=service.id,
        model="usage-model",
        temperature=None,
        max_output_tokens=4096,
        reasoning_effort="auto",
        timeout_seconds=30,
        max_retries=1,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "not-json"}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )

    provider = OpenAIResponsesProvider(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError) as caught:
        asyncio.run(
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

    assert caught.value.code == "PROVIDER_INVALID_OUTPUT"
    assert caught.value.prompt_tokens == 100
    assert caught.value.completion_tokens == 20


def test_default_profile_uses_auto_parameters_and_accepts_one_token(client) -> None:
    initial = client.get("/api/settings/models").json()
    profile = initial["analysis_profiles"][0]
    assert profile["temperature"] is None
    assert profile["reasoning_effort"] == "auto"
    assert profile["context_window_tokens"] is None

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
            "context_window_tokens": 128000,
        },
    )
    assert response.status_code == 200
    assert response.json()["max_output_tokens"] == 1
    assert response.json()["context_window_tokens"] == 128000


def test_legacy_monetary_fields_are_ignored_and_removed_on_next_save(client) -> None:
    settings = client.app.state.settings
    profile = read_model_settings(settings).analysis_profiles[0]
    save_analysis_profile(
        settings,
        profile_id=profile.id,
        name=profile.name,
        service_id=profile.service_id,
        model=profile.model,
        temperature=profile.temperature,
        max_output_tokens=profile.max_output_tokens,
        reasoning_effort=profile.reasoning_effort,
        timeout_seconds=profile.timeout_seconds,
        max_retries=profile.max_retries,
    )
    path = settings.workspace_dir / "secrets" / "model_settings.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored_profile = stored["analysis_profiles"][0]
    stored_profile.update(
        {
            "input_price_per_million_tokens": 1.0,
            "output_price_per_million_tokens": 2.0,
            "price_currency": "USD",
        }
    )
    path.write_text(json.dumps(stored, ensure_ascii=False), encoding="utf-8")

    response = client.get("/api/settings/models")
    assert response.status_code == 200
    response_profile = response.json()["analysis_profiles"][0]
    assert not {
        "input_price_per_million_tokens",
        "output_price_per_million_tokens",
        "price_currency",
    }.intersection(response_profile)

    saved = client.put(
        f"/api/settings/analysis-profiles/{profile.id}",
        json={
            **response_profile,
            "input_price_per_million_tokens": 9.0,
            "output_price_per_million_tokens": 9.0,
            "price_currency": "CNY",
        },
    )
    assert saved.status_code == 200
    persisted_profile = json.loads(path.read_text(encoding="utf-8"))["analysis_profiles"][0]
    assert not {
        "input_price_per_million_tokens",
        "output_price_per_million_tokens",
        "price_currency",
    }.intersection(persisted_profile)


def test_analysis_profile_rejects_context_window_without_input_space(client) -> None:
    profile = client.get("/api/settings/models").json()["analysis_profiles"][0]

    response = client.put(
        f"/api/settings/analysis-profiles/{profile['id']}",
        json={
            "name": profile["name"],
            "service_id": profile["service_id"],
            "model": "small-window-model",
            "temperature": None,
            "max_output_tokens": 16_000,
            "reasoning_effort": "auto",
            "timeout_seconds": 30,
            "max_retries": 0,
            "context_window_tokens": 16_999,
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "CONTEXT_WINDOW_TOO_SMALL"
    assert "分析输入空间" in response.json()["detail"]["message"]


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
        assert request.headers["user-agent"] == PROVIDER_HTTP_USER_AGENT
        body = json.loads(request.content)
        assert body["model"] == "strict-model"
        assert body["stream"] is True
        assert body["stream_options"] == {"include_usage": True}
        assert "temperature" not in body
        assert "reasoning_effort" not in body
        assert body["response_format"]["type"] == "json_schema"
        wire_schema = body["response_format"]["json_schema"]["schema"]
        assert "$schema" not in wire_schema
        assert "$id" not in wire_schema
        assert wire_schema["title"] == "Model capability probe"
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                'data: {"choices":[{"delta":{"content":"{\\"ok\\": true}"}}]}\n\n'
                'data: {"choices":[],"usage":{"prompt_tokens":8,"completion_tokens":3}}\n\n'
                "data: [DONE]\n\n"
            ).encode(),
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
            headers={"content-type": "text/event-stream"},
            content=(
                'data: {"choices":[{"delta":{"content":"{\\"ok\\": true}"}}]}\n\n'
                "data: [DONE]\n\n"
            ).encode(),
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
            headers={"content-type": "text/event-stream"},
            content=(
                'data: {"choices":[{"delta":{"content":"{\\"entities\\": [], \\"events\\": []}"}}]}\n\n'
                'data: {"choices":[],"usage":{"prompt_tokens":12,"completion_tokens":8}}\n\n'
                "data: [DONE]\n\n"
            ).encode(),
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
