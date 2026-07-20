from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from ..config import Settings


DEFAULT_SERVICE_ID = "openai-default"
ENTITIES_EVENTS_PROFILE_ID = "entities-events"
SUPPORTED_SERVICE_TYPES = {"OPENAI", "OPENAI_COMPATIBLE"}
SUPPORTED_REASONING_EFFORTS = {"auto", "none", "low", "medium", "high"}
SUPPORTED_PRICE_CURRENCIES = {"USD", "CNY"}
CAPABILITY_UNTESTED = "UNTESTED"
CAPABILITY_SUPPORTED = "SUPPORTED"
CAPABILITY_FAILED = "FAILED"
STRUCTURED_STRICT = "STRICT_JSON_SCHEMA"
STRUCTURED_JSON_ONLY = "JSON_ONLY"
STRUCTURED_UNSUPPORTED = "UNSUPPORTED"

# JSON Schema files used by the application may contain document metadata such
# as `$id` and `$schema`. Those keywords are valid JSON Schema, but several
# OpenAI-compatible gateways (including Gemini adapters) reject them when they
# are placed inside a structured-output request. Keep the full schema locally
# and remove only wire-incompatible metadata at the provider boundary.
_WIRE_SCHEMA_METADATA = {"$schema", "$id", "$comment"}


def schema_for_provider(value: object) -> object:
    """Return a provider-compatible copy of a JSON Schema value."""
    if isinstance(value, dict):
        return {
            key: schema_for_provider(item)
            for key, item in value.items()
            if key not in _WIRE_SCHEMA_METADATA
        }
    if isinstance(value, list):
        return [schema_for_provider(item) for item in value]
    return value


class ModelSettingsError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    tested_model: str | None = None
    tested_at: str | None = None
    ordinary_request: str = CAPABILITY_UNTESTED
    structured_output: str = CAPABILITY_UNTESTED
    temperature: str = CAPABILITY_UNTESTED
    reasoning_effort: str = CAPABILITY_UNTESTED
    model_catalog: str = CAPABILITY_UNTESTED


@dataclass(frozen=True, slots=True)
class ModelService:
    id: str
    name: str
    service_type: str
    base_url: str
    api_key: str | None
    last_tested_at: str | None = None
    last_test_status: str = "NOT_TESTED"
    last_test_message: str | None = None
    capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_key.strip())


@dataclass(frozen=True, slots=True)
class AnalysisProfile:
    id: str
    name: str
    task_type: str
    service_id: str
    model: str
    temperature: float | None
    max_output_tokens: int
    reasoning_effort: str
    timeout_seconds: float
    max_retries: int
    context_window_tokens: int | None = None
    input_price_per_million_tokens: float | None = None
    output_price_per_million_tokens: float | None = None
    price_currency: str = "USD"


@dataclass(frozen=True, slots=True)
class ModelSettings:
    services: tuple[ModelService, ...]
    analysis_profiles: tuple[AnalysisProfile, ...]


@dataclass(frozen=True, slots=True)
class OpenAIConfig:
    """Compatibility view for the original single-provider API."""

    base_url: str
    model: str
    api_key: str | None

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_key.strip())


def _settings_path(settings: Settings) -> Path:
    return settings.workspace_dir / "secrets" / "model_settings.json"


def _legacy_config_path(settings: Settings) -> Path:
    return settings.workspace_dir / "secrets" / "openai.json"


def _default_settings(settings: Settings) -> ModelSettings:
    service = ModelService(
        id=DEFAULT_SERVICE_ID,
        name="OpenAI",
        service_type="OPENAI",
        base_url=settings.openai_base_url.rstrip("/"),
        api_key=settings.openai_api_key,
    )
    profile = AnalysisProfile(
        id=ENTITIES_EVENTS_PROFILE_ID,
        name="人物与事件精确提取",
        task_type="ENTITIES_EVENTS",
        service_id=service.id,
        model=settings.openai_model.strip(),
        temperature=None,
        max_output_tokens=16_000,
        reasoning_effort="auto",
        timeout_seconds=settings.openai_timeout_seconds,
        max_retries=2,
    )
    return ModelSettings((service,), (profile,))


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError("PROVIDER_CONFIG_FILE_INVALID") from exc
    if not isinstance(value, dict):
        raise RuntimeError("PROVIDER_CONFIG_FILE_INVALID")
    return value


def _from_stored(settings: Settings, stored: dict[str, Any]) -> ModelSettings:
    defaults = _default_settings(settings)
    stored_version = int(stored.get("version", 1) or 1)
    services: list[ModelService] = []
    for item in stored.get("services", []):
        if not isinstance(item, dict):
            continue
        raw_capabilities = item.get("capabilities")
        capabilities = ModelCapabilities()
        if isinstance(raw_capabilities, dict):
            capabilities = ModelCapabilities(
                tested_model=str(raw_capabilities.get("tested_model") or "").strip() or None,
                tested_at=str(raw_capabilities.get("tested_at") or "").strip() or None,
                ordinary_request=str(raw_capabilities.get("ordinary_request") or CAPABILITY_UNTESTED),
                structured_output=str(raw_capabilities.get("structured_output") or CAPABILITY_UNTESTED),
                temperature=str(raw_capabilities.get("temperature") or CAPABILITY_UNTESTED),
                reasoning_effort=str(raw_capabilities.get("reasoning_effort") or CAPABILITY_UNTESTED),
                model_catalog=str(raw_capabilities.get("model_catalog") or CAPABILITY_UNTESTED),
            )
        elif str(item.get("last_test_status") or "") == "CONNECTED":
            capabilities = replace(capabilities, model_catalog=CAPABILITY_SUPPORTED)
        services.append(
            ModelService(
                id=str(item.get("id") or uuid4().hex),
                name=str(item.get("name") or "未命名服务"),
                service_type=str(item.get("service_type") or "OPENAI_COMPATIBLE"),
                base_url=str(item.get("base_url") or "").rstrip("/"),
                api_key=str(item.get("api_key") or "").strip() or None,
                last_tested_at=str(item.get("last_tested_at") or "").strip() or None,
                last_test_status=str(item.get("last_test_status") or "NOT_TESTED"),
                last_test_message=str(item.get("last_test_message") or "").strip() or None,
                capabilities=capabilities,
            )
        )
    if not services:
        services = list(defaults.services)

    profiles: list[AnalysisProfile] = []
    for item in stored.get("analysis_profiles", []):
        if not isinstance(item, dict):
            continue
        raw_temperature = item.get("temperature")
        raw_reasoning = str(item.get("reasoning_effort") or "auto")
        legacy_defaults = (
            stored_version < 2
            and raw_temperature is not None
            and abs(float(raw_temperature) - 0.2) < 0.0001
            and raw_reasoning == "low"
        )
        temperature = None if raw_temperature is None else float(raw_temperature)
        reasoning_effort = raw_reasoning
        if legacy_defaults:
            temperature = None
            reasoning_effort = "auto"
        profiles.append(
            AnalysisProfile(
                id=str(item.get("id") or ENTITIES_EVENTS_PROFILE_ID),
                name=str(item.get("name") or "分析方案"),
                task_type=str(item.get("task_type") or "ENTITIES_EVENTS"),
                service_id=str(item.get("service_id") or services[0].id),
                model=str(item.get("model") or "").strip(),
                temperature=temperature,
                max_output_tokens=int(item.get("max_output_tokens", 16_000)),
                reasoning_effort=reasoning_effort,
                timeout_seconds=float(item.get("timeout_seconds", 180)),
                max_retries=int(item.get("max_retries", 2)),
                context_window_tokens=(
                    int(item["context_window_tokens"])
                    if item.get("context_window_tokens") is not None
                    else None
                ),
                input_price_per_million_tokens=(
                    float(item["input_price_per_million_tokens"])
                    if item.get("input_price_per_million_tokens") is not None
                    else None
                ),
                output_price_per_million_tokens=(
                    float(item["output_price_per_million_tokens"])
                    if item.get("output_price_per_million_tokens") is not None
                    else None
                ),
                price_currency=str(item.get("price_currency") or "USD").upper(),
            )
        )
    if not profiles:
        profiles = [replace(defaults.analysis_profiles[0], service_id=services[0].id)]
    return ModelSettings(tuple(services), tuple(profiles))


def read_model_settings(settings: Settings) -> ModelSettings:
    stored = _read_json(_settings_path(settings))
    if stored is not None:
        return _from_stored(settings, stored)

    result = _default_settings(settings)
    legacy = _read_json(_legacy_config_path(settings))
    if legacy is None:
        return result
    service = replace(
        result.services[0],
        base_url=str(legacy.get("base_url") or result.services[0].base_url).rstrip("/"),
        api_key=str(legacy.get("api_key") or result.services[0].api_key or "").strip() or None,
    )
    profile = replace(
        result.analysis_profiles[0],
        model=str(legacy.get("model") or result.analysis_profiles[0].model).strip(),
    )
    return ModelSettings((service,), (profile,))


def _write_model_settings(settings: Settings, value: ModelSettings) -> None:
    path = _settings_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(f".tmp-{uuid4().hex}")
    temp.write_text(
        json.dumps(
            {
                "version": 2,
                "services": [asdict(item) for item in value.services],
                "analysis_profiles": [asdict(item) for item in value.analysis_profiles],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    os.replace(temp, path)


def _validate_url(value: str) -> str:
    url = value.strip().rstrip("/")
    if not url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
        raise ModelSettingsError("PROVIDER_BASE_URL_INVALID", "接口地址必须使用 HTTPS；本机服务可以使用 localhost。")
    return url


def save_model_service(
    settings: Settings,
    *,
    service_id: str | None,
    name: str,
    service_type: str,
    base_url: str,
    api_key: str | None,
) -> ModelService:
    if service_type not in SUPPORTED_SERVICE_TYPES:
        raise ModelSettingsError("PROVIDER_TYPE_INVALID", "暂不支持这种模型服务类型。")
    service_name = name.strip()
    if not service_name:
        raise ModelSettingsError("PROVIDER_NAME_REQUIRED", "请填写服务名称。")
    url = _validate_url(base_url)
    current = read_model_settings(settings)
    existing = next((item for item in current.services if item.id == service_id), None)
    if service_id is not None and existing is None:
        raise ModelSettingsError("PROVIDER_NOT_FOUND", "没有找到这个模型服务。")
    next_key = api_key.strip() if api_key and api_key.strip() else (existing.api_key if existing else None)
    if not next_key:
        raise ModelSettingsError("PROVIDER_API_KEY_REQUIRED", "请填写 API Key。")
    connection_changed = bool(
        existing
        and (
            existing.service_type != service_type
            or existing.base_url != url
            or existing.api_key != next_key
        )
    )
    saved = ModelService(
        id=existing.id if existing else f"provider-{uuid4().hex[:12]}",
        name=service_name,
        service_type=service_type,
        base_url=url,
        api_key=next_key,
        last_tested_at=existing.last_tested_at if existing else None,
        last_test_status="NOT_TESTED" if connection_changed else (existing.last_test_status if existing else "NOT_TESTED"),
        last_test_message=None if connection_changed else (existing.last_test_message if existing else None),
        capabilities=ModelCapabilities() if connection_changed else (existing.capabilities if existing else ModelCapabilities()),
    )
    services = tuple(saved if item.id == saved.id else item for item in current.services)
    if existing is None:
        services = (*current.services, saved)
    _write_model_settings(settings, ModelSettings(services, current.analysis_profiles))
    return saved


def delete_model_service(settings: Settings, service_id: str) -> None:
    current = read_model_settings(settings)
    if not any(item.id == service_id for item in current.services):
        raise ModelSettingsError("PROVIDER_NOT_FOUND", "没有找到这个模型服务。")
    if len(current.services) == 1:
        raise ModelSettingsError("PROVIDER_LAST_SERVICE", "至少需要保留一个模型服务。")
    if any(item.service_id == service_id for item in current.analysis_profiles):
        raise ModelSettingsError("PROVIDER_IN_USE", "这个服务正在被分析方案使用，请先更换分析方案中的模型服务。")
    services = tuple(item for item in current.services if item.id != service_id)
    _write_model_settings(settings, ModelSettings(services, current.analysis_profiles))


def save_analysis_profile(
    settings: Settings,
    *,
    profile_id: str,
    name: str,
    service_id: str,
    model: str,
    temperature: float | None,
    max_output_tokens: int,
    reasoning_effort: str,
    timeout_seconds: float,
    max_retries: int,
    context_window_tokens: int | None = None,
    input_price_per_million_tokens: float | None = None,
    output_price_per_million_tokens: float | None = None,
    price_currency: str = "USD",
) -> AnalysisProfile:
    current = read_model_settings(settings)
    if not any(item.id == service_id for item in current.services):
        raise ModelSettingsError("PROVIDER_NOT_FOUND", "没有找到所选模型服务。")
    model_name = model.strip()
    if not model_name:
        raise ModelSettingsError("MODEL_REQUIRED", "请选择或填写模型。")
    if reasoning_effort not in SUPPORTED_REASONING_EFFORTS:
        raise ModelSettingsError("REASONING_EFFORT_INVALID", "推理强度设置无效。")
    if temperature is not None and not 0 <= temperature <= 2:
        raise ModelSettingsError("TEMPERATURE_INVALID", "温度必须在 0 到 2 之间。")
    if not 1 <= max_output_tokens <= 128_000:
        raise ModelSettingsError("MAX_OUTPUT_TOKENS_INVALID", "最大输出长度必须在 1 到 128000 之间。")
    if not 10 <= timeout_seconds <= 1800:
        raise ModelSettingsError("TIMEOUT_INVALID", "超时时间必须在 10 到 1800 秒之间。")
    if not 0 <= max_retries <= 10:
        raise ModelSettingsError("MAX_RETRIES_INVALID", "重试次数必须在 0 到 10 之间。")
    if context_window_tokens is not None:
        if not 1 <= context_window_tokens <= 10_000_000:
            raise ModelSettingsError("CONTEXT_WINDOW_INVALID", "模型上下文长度必须在 1 到 10000000 之间。")
        if context_window_tokens < max_output_tokens + 1_000:
            raise ModelSettingsError(
                "CONTEXT_WINDOW_TOO_SMALL",
                "模型上下文长度至少要比最大输出长度多 1000，才能留出分析输入空间。",
            )
    if (input_price_per_million_tokens is None) != (output_price_per_million_tokens is None):
        raise ModelSettingsError("MODEL_PRICING_INCOMPLETE", "输入单价和输出单价需要同时填写，或者都留空。")
    for price in (input_price_per_million_tokens, output_price_per_million_tokens):
        if price is not None and not 0 <= price <= 1_000_000:
            raise ModelSettingsError("MODEL_PRICE_INVALID", "每百万令牌单价必须在 0 到 1000000 之间。")
    currency = price_currency.strip().upper()
    if currency not in SUPPORTED_PRICE_CURRENCIES:
        raise ModelSettingsError("MODEL_PRICE_CURRENCY_INVALID", "计价币种目前支持美元或人民币。")
    existing = next((item for item in current.analysis_profiles if item.id == profile_id), None)
    saved = AnalysisProfile(
        id=profile_id,
        name=name.strip() or (existing.name if existing else "分析方案"),
        task_type=existing.task_type if existing else "ENTITIES_EVENTS",
        service_id=service_id,
        model=model_name,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        reasoning_effort=reasoning_effort,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        context_window_tokens=context_window_tokens,
        input_price_per_million_tokens=input_price_per_million_tokens,
        output_price_per_million_tokens=output_price_per_million_tokens,
        price_currency=currency,
    )
    profiles = tuple(saved if item.id == profile_id else item for item in current.analysis_profiles)
    if existing is None:
        profiles = (*current.analysis_profiles, saved)
    _write_model_settings(settings, ModelSettings(current.services, profiles))
    return saved


def resolve_analysis_profile(
    settings: Settings,
    profile_id: str = ENTITIES_EVENTS_PROFILE_ID,
) -> tuple[ModelService, AnalysisProfile]:
    current = read_model_settings(settings)
    profile = next((item for item in current.analysis_profiles if item.id == profile_id), None)
    if profile is None:
        raise ModelSettingsError("ANALYSIS_PROFILE_NOT_FOUND", "没有找到这个分析方案。")
    service = next((item for item in current.services if item.id == profile.service_id), None)
    if service is None or not service.configured:
        raise ModelSettingsError("PROVIDER_NOT_CONFIGURED", "请先到设置中心连接模型服务。")
    if not profile.model:
        raise ModelSettingsError("MODEL_REQUIRED", "请先到设置中心选择分析模型。")
    return service, profile


def model_cost_snapshot(
    profile: AnalysisProfile,
    *,
    prompt_tokens: int,
    completion_tokens: int,
) -> dict[str, float | int | str] | None:
    if (
        profile.input_price_per_million_tokens is None
        or profile.output_price_per_million_tokens is None
    ):
        return None
    input_cost = prompt_tokens * profile.input_price_per_million_tokens / 1_000_000
    output_cost = completion_tokens * profile.output_price_per_million_tokens / 1_000_000
    return {
        "currency": profile.price_currency,
        "input_price_per_million_tokens": profile.input_price_per_million_tokens,
        "output_price_per_million_tokens": profile.output_price_per_million_tokens,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "input_cost": round(input_cost, 8),
        "output_cost": round(output_cost, 8),
        "total_cost": round(input_cost + output_cost, 8),
    }


def _friendly_connection_error(response: httpx.Response) -> ModelSettingsError:
    if response.status_code in {401, 403}:
        return ModelSettingsError("PROVIDER_AUTH_FAILED", "API Key 无效，或者当前账号没有访问权限。")
    if response.status_code == 404:
        return ModelSettingsError("PROVIDER_MODELS_UNSUPPORTED", "服务已连接，但它没有提供模型列表；你仍可以手工填写模型名称。")
    if response.status_code == 429:
        return ModelSettingsError("PROVIDER_RATE_LIMITED", "服务当前请求过多，请稍后再试。")
    if response.status_code >= 500:
        return ModelSettingsError("PROVIDER_UNAVAILABLE", "模型服务暂时不可用，请稍后再试。")
    return ModelSettingsError("PROVIDER_CONNECTION_FAILED", f"模型服务拒绝了连接测试（{response.status_code}）。")


async def discover_models(
    settings: Settings,
    service_id: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[str]:
    current = read_model_settings(settings)
    service = next((item for item in current.services if item.id == service_id), None)
    if service is None:
        raise ModelSettingsError("PROVIDER_NOT_FOUND", "没有找到这个模型服务。")
    if not service.configured:
        raise ModelSettingsError("PROVIDER_NOT_CONFIGURED", "请先保存 API Key。")
    try:
        async with httpx.AsyncClient(timeout=30, transport=transport) as client:
            response = await client.get(
                f"{service.base_url}/models",
                headers={"Authorization": f"Bearer {service.api_key}"},
            )
    except httpx.TimeoutException as exc:
        raise ModelSettingsError("PROVIDER_TIMEOUT", "连接模型服务超时，请检查接口地址或网络。") from exc
    except httpx.HTTPError as exc:
        raise ModelSettingsError("PROVIDER_CONNECTION_FAILED", "无法连接模型服务，请检查接口地址和网络。") from exc
    if response.status_code >= 400:
        raise _friendly_connection_error(response)
    try:
        body = response.json()
        models = sorted(
            {
                str(item["id"])
                for item in body.get("data", [])
                if isinstance(item, dict) and item.get("id")
            },
            key=str.casefold,
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise ModelSettingsError("PROVIDER_MODELS_INVALID", "服务已响应，但模型列表格式无法识别；你仍可以手工填写模型名称。") from exc
    if not models:
        raise ModelSettingsError("PROVIDER_MODELS_EMPTY", "服务已连接，但没有返回可用模型；你仍可以手工填写模型名称。")
    return models


PROBE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "model_capability_probe.schema.json",
    "title": "Model capability probe",
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class ModelProbeResult:
    service: ModelService
    message: str


def _probe_body(
    service: ModelService,
    *,
    model: str,
    strict: bool,
    temperature: float | None = None,
    reasoning_effort: str | None = None,
) -> tuple[str, dict[str, Any]]:
    instructions = '只返回 JSON 对象 {"ok": true}，不要输出解释或 Markdown。'
    wire_schema = schema_for_provider(PROBE_SCHEMA)
    if service.service_type == "OPENAI":
        body: dict[str, Any] = {
            "model": model,
            "instructions": instructions,
            "input": "连接测试。",
            "max_output_tokens": 32,
        }
        endpoint = f"{service.base_url}/responses"
        if strict:
            body["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "model_capability_probe",
                    "strict": True,
                    "schema": wire_schema,
                }
            }
        if temperature is not None:
            body["temperature"] = temperature
        if reasoning_effort is not None:
            body["reasoning"] = {"effort": reasoning_effort}
        return endpoint, body

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": "连接测试。"},
        ],
        "max_tokens": 32,
    }
    endpoint = f"{service.base_url}/chat/completions"
    if strict:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "model_capability_probe",
                "strict": True,
                "schema": wire_schema,
            },
        }
    if temperature is not None:
        body["temperature"] = temperature
    if reasoning_effort is not None:
        body["reasoning_effort"] = reasoning_effort
    return endpoint, body


async def _request_probe(
    service: ModelService,
    *,
    model: str,
    strict: bool,
    timeout_seconds: float,
    transport: httpx.AsyncBaseTransport | None,
    temperature: float | None = None,
    reasoning_effort: str | None = None,
) -> httpx.Response:
    endpoint, body = _probe_body(
        service,
        model=model,
        strict=strict,
        temperature=temperature,
        reasoning_effort=reasoning_effort,
    )
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds, transport=transport) as client:
            return await client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {service.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
    except httpx.TimeoutException as exc:
        raise ModelSettingsError("PROVIDER_TIMEOUT", "所选模型测试超时，请检查网络或稍后再试。") from exc
    except httpx.HTTPError as exc:
        raise ModelSettingsError("PROVIDER_CONNECTION_FAILED", "无法连接所选模型，请检查接口地址和网络。") from exc


def _probe_error(response: httpx.Response) -> ModelSettingsError:
    if response.status_code in {401, 403}:
        return ModelSettingsError("PROVIDER_AUTH_FAILED", "API Key 无效，或者当前账号没有访问所选模型的权限。")
    if response.status_code == 404:
        return ModelSettingsError("MODEL_NOT_FOUND", "服务找不到这个模型，或当前账号没有使用权限。")
    if response.status_code == 429:
        return ModelSettingsError("PROVIDER_RATE_LIMITED", "模型服务当前请求过多，请稍后再试。")
    if response.status_code >= 500:
        return ModelSettingsError("PROVIDER_UNAVAILABLE", "模型服务暂时不可用，请稍后再试。")
    return ModelSettingsError(
        "MODEL_REQUEST_REJECTED",
        f"所选模型拒绝了测试请求（{response.status_code}），请检查它支持的参数和输出格式。",
    )


def _probe_output(response: httpx.Response, service: ModelService) -> dict[str, Any]:
    try:
        body = response.json()
        if service.service_type == "OPENAI":
            output_text = next(
                content["text"]
                for item in body.get("output", [])
                if item.get("type") == "message"
                for content in item.get("content", [])
                if content.get("type") == "output_text"
            )
        else:
            output_text = body["choices"][0]["message"]["content"]
        parsed = json.loads(output_text)
    except (ValueError, KeyError, StopIteration, TypeError, json.JSONDecodeError) as exc:
        raise ModelSettingsError("PROVIDER_INVALID_OUTPUT", "模型已响应，但没有返回可识别的 JSON 测试结果。") from exc
    if not isinstance(parsed, dict) or parsed.get("ok") is not True:
        raise ModelSettingsError("PROVIDER_INVALID_OUTPUT", "模型已响应，但没有返回预期的 JSON 测试结果。")
    return parsed


def _probe_succeeded(response: httpx.Response, service: ModelService) -> bool:
    if response.status_code >= 400:
        return False
    try:
        _probe_output(response, service)
    except ModelSettingsError:
        return False
    return True


def record_model_capabilities(
    settings: Settings,
    service_id: str,
    *,
    capabilities: ModelCapabilities,
) -> ModelService:
    current = read_model_settings(settings)
    service = next((item for item in current.services if item.id == service_id), None)
    if service is None:
        raise ModelSettingsError("PROVIDER_NOT_FOUND", "没有找到这个模型服务。")
    saved = replace(service, capabilities=capabilities)
    services = tuple(saved if item.id == service_id else item for item in current.services)
    _write_model_settings(settings, ModelSettings(services, current.analysis_profiles))
    return saved


def record_model_probe_failure(
    settings: Settings,
    service_id: str,
    *,
    model: str,
) -> ModelService:
    current = read_model_settings(settings)
    service = next((item for item in current.services if item.id == service_id), None)
    if service is None:
        raise ModelSettingsError("PROVIDER_NOT_FOUND", "没有找到这个模型服务。")
    capabilities = ModelCapabilities(
        tested_model=model,
        tested_at=datetime.now(timezone.utc).isoformat(),
        ordinary_request=CAPABILITY_FAILED,
        model_catalog=service.capabilities.model_catalog,
    )
    return record_model_capabilities(settings, service_id, capabilities=capabilities)


async def probe_selected_model(
    settings: Settings,
    profile_id: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ModelProbeResult:
    service, profile = resolve_analysis_profile(settings, profile_id)
    model = profile.model
    try:
        strict_response = await _request_probe(
            service,
            model=model,
            strict=True,
            timeout_seconds=min(profile.timeout_seconds, 60),
            transport=transport,
        )
    except ModelSettingsError:
        record_model_probe_failure(settings, service.id, model=model)
        raise
    structured_output = STRUCTURED_STRICT
    strict_succeeded = _probe_succeeded(strict_response, service)
    if strict_response.status_code >= 400 or not strict_succeeded:
        if strict_response.status_code not in {400, 422}:
            if strict_response.status_code >= 400:
                record_model_probe_failure(settings, service.id, model=model)
                raise _probe_error(strict_response)
        try:
            plain_response = await _request_probe(
                service,
                model=model,
                strict=False,
                timeout_seconds=min(profile.timeout_seconds, 60),
                transport=transport,
            )
        except ModelSettingsError:
            record_model_probe_failure(settings, service.id, model=model)
            raise
        if plain_response.status_code >= 400:
            record_model_probe_failure(settings, service.id, model=model)
            raise _probe_error(plain_response)
        try:
            _probe_output(plain_response, service)
        except ModelSettingsError:
            record_model_probe_failure(settings, service.id, model=model)
            raise
        structured_output = STRUCTURED_JSON_ONLY

    temperature_status = CAPABILITY_UNTESTED
    if profile.temperature is not None:
        response = await _request_probe(
            service,
            model=model,
            strict=structured_output == STRUCTURED_STRICT,
            timeout_seconds=min(profile.timeout_seconds, 60),
            transport=transport,
            temperature=profile.temperature,
        )
        temperature_status = CAPABILITY_SUPPORTED if _probe_succeeded(response, service) else CAPABILITY_FAILED
        if response.status_code in {400, 422}:
            temperature_status = STRUCTURED_UNSUPPORTED

    reasoning_status = CAPABILITY_UNTESTED
    if profile.reasoning_effort not in {"auto", "none"}:
        response = await _request_probe(
            service,
            model=model,
            strict=structured_output == STRUCTURED_STRICT,
            timeout_seconds=min(profile.timeout_seconds, 60),
            transport=transport,
            reasoning_effort=profile.reasoning_effort,
        )
        reasoning_status = CAPABILITY_SUPPORTED if _probe_succeeded(response, service) else CAPABILITY_FAILED
        if response.status_code in {400, 422}:
            reasoning_status = STRUCTURED_UNSUPPORTED

    current = read_model_settings(settings)
    service = next(item for item in current.services if item.id == service.id)
    capabilities = ModelCapabilities(
        tested_model=model,
        tested_at=datetime.now(timezone.utc).isoformat(),
        ordinary_request=CAPABILITY_SUPPORTED,
        structured_output=structured_output,
        temperature=temperature_status,
        reasoning_effort=reasoning_status,
        model_catalog=service.capabilities.model_catalog,
    )
    saved = record_model_capabilities(settings, service.id, capabilities=capabilities)
    if structured_output == STRUCTURED_STRICT:
        message = "所选模型测试成功，支持严格结构化输出，可以用于拆书。"
    else:
        message = "所选模型可以调用，但不支持严格结构化输出；分析时会自动改用普通 JSON。"
    if reasoning_status == STRUCTURED_UNSUPPORTED:
        message += " 当前推理强度不被该模型接受，分析时会自动忽略。"
    if temperature_status == STRUCTURED_UNSUPPORTED:
        message += " 当前温度参数不被该模型接受，分析时会自动忽略。"
    return ModelProbeResult(saved, message)


def record_connection_result(
    settings: Settings,
    service_id: str,
    *,
    success: bool,
    message: str,
    model_catalog_status: str | None = None,
) -> ModelService:
    current = read_model_settings(settings)
    service = next((item for item in current.services if item.id == service_id), None)
    if service is None:
        raise ModelSettingsError("PROVIDER_NOT_FOUND", "没有找到这个模型服务。")
    capabilities = service.capabilities
    if model_catalog_status is not None:
        capabilities = replace(capabilities, model_catalog=model_catalog_status)
    saved = replace(
        service,
        last_tested_at=datetime.now(timezone.utc).isoformat(),
        last_test_status="CONNECTED" if success else "FAILED",
        last_test_message=message,
        capabilities=capabilities,
    )
    services = tuple(saved if item.id == service_id else item for item in current.services)
    _write_model_settings(settings, ModelSettings(services, current.analysis_profiles))
    return saved


def read_openai_config(settings: Settings) -> OpenAIConfig:
    current = read_model_settings(settings)
    profile = next(
        (item for item in current.analysis_profiles if item.id == ENTITIES_EVENTS_PROFILE_ID),
        current.analysis_profiles[0],
    )
    service = next((item for item in current.services if item.id == profile.service_id), current.services[0])
    return OpenAIConfig(service.base_url, profile.model, service.api_key)


def write_openai_config(
    settings: Settings,
    *,
    api_key: str | None,
    base_url: str | None,
    model: str | None,
) -> OpenAIConfig:
    current = read_model_settings(settings)
    old = read_openai_config(settings)
    service = save_model_service(
        settings,
        service_id=current.services[0].id,
        name=current.services[0].name,
        service_type=current.services[0].service_type,
        base_url=base_url or old.base_url,
        api_key=api_key,
    )
    profile = next(
        (item for item in read_model_settings(settings).analysis_profiles if item.id == ENTITIES_EVENTS_PROFILE_ID),
        current.analysis_profiles[0],
    )
    saved_profile = save_analysis_profile(
        settings,
        profile_id=profile.id,
        name=profile.name,
        service_id=service.id,
        model=model or old.model,
        temperature=profile.temperature,
        max_output_tokens=profile.max_output_tokens,
        reasoning_effort=profile.reasoning_effort,
        timeout_seconds=profile.timeout_seconds,
        max_retries=profile.max_retries,
        context_window_tokens=profile.context_window_tokens,
        input_price_per_million_tokens=profile.input_price_per_million_tokens,
        output_price_per_million_tokens=profile.output_price_per_million_tokens,
        price_currency=profile.price_currency,
    )

    # Keep the old file current for older branches and local rollback.
    legacy_path = _legacy_config_path(settings)
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text(
        json.dumps(
            {"api_key": service.api_key, "base_url": service.base_url, "model": saved_profile.model},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return OpenAIConfig(service.base_url, saved_profile.model, service.api_key)
