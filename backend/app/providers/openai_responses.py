from __future__ import annotations

import json
import re
from typing import Any

import httpx

from ..config import Settings
from ..services.provider_config import (
    STRUCTURED_JSON_ONLY,
    STRUCTURED_STRICT,
    STRUCTURED_UNSUPPORTED,
    ModelSettingsError,
    model_cost_snapshot,
    resolve_analysis_profile,
    schema_for_provider,
)
from .base import ProviderError, ProviderResponse


class OpenAIResponsesProvider:
    """Gateway for OpenAI Responses and OpenAI-compatible chat APIs."""

    name = "openai"

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport

    def _configuration(self, payload: dict[str, Any]):
        try:
            return resolve_analysis_profile(
                self.settings,
                str(payload.get("model_profile_id") or "entities-events"),
            )
        except ModelSettingsError as exc:
            raise ProviderError(
                code=exc.code,
                message=exc.message,
                retryable=False,
            ) from exc

    async def complete(self, *, task_kind: str, payload: dict[str, Any]) -> ProviderResponse:
        service, profile = self._configuration(payload)
        schema = payload.get("output_schema")
        instructions = payload.get("instructions")
        model_input = payload.get("input")
        if not isinstance(schema, dict) or not isinstance(instructions, str) or not isinstance(model_input, str):
            raise ProviderError(
                code="PROVIDER_BAD_REQUEST",
                message="分析任务缺少结构化输出要求。",
                retryable=False,
            )

        wire_schema = schema_for_provider(schema)
        capability_matches = service.capabilities.tested_model == profile.model
        capabilities = service.capabilities if capability_matches else None
        structured_mode = (
            capabilities.structured_output
            if capabilities and capabilities.structured_output in {STRUCTURED_STRICT, STRUCTURED_JSON_ONLY}
            else STRUCTURED_STRICT
        )
        # Compatible gateways can advertise strict JSON support from a small
        # probe while rejecting the much larger nested schemas used by later
        # analysis stages. Keep the output contract, but move schema enforcement
        # to the local Pydantic validator for those stages.
        if (
            service.service_type == "OPENAI_COMPATIBLE"
            and task_kind in {"analysis.narrative_synthesis", "analysis.deep_insights"}
        ):
            structured_mode = STRUCTURED_JSON_ONLY
        reasoning_supported = not capabilities or capabilities.reasoning_effort != STRUCTURED_UNSUPPORTED
        temperature_supported = not capabilities or capabilities.temperature != STRUCTURED_UNSUPPORTED
        effective_reasoning = (
            profile.reasoning_effort
            if profile.reasoning_effort not in {"auto", "none"} and reasoning_supported
            else None
        )
        effective_temperature = (
            profile.temperature
            if profile.temperature is not None
            and temperature_supported
            and (service.service_type != "OPENAI" or effective_reasoning is None)
            else None
        )
        if structured_mode == STRUCTURED_JSON_ONLY:
            instructions = (
                f"{instructions}\n输出必须是 JSON 对象，并满足以下结构："
                f"{json.dumps(wire_schema, ensure_ascii=False, separators=(',', ':'))}"
            )

        if service.service_type == "OPENAI":
            endpoint = f"{service.base_url}/responses"
            request_body: dict[str, Any] = {
                "model": profile.model,
                "instructions": instructions,
                "input": model_input,
                "max_output_tokens": profile.max_output_tokens,
            }
            if structured_mode == STRUCTURED_STRICT:
                request_body["text"] = {
                    "format": {
                        "type": "json_schema",
                        "name": "novel_entities_events",
                        "strict": True,
                        "schema": wire_schema,
                    }
                }
            if effective_reasoning is not None:
                request_body["reasoning"] = {"effort": effective_reasoning}
            elif effective_temperature is not None:
                request_body["temperature"] = effective_temperature
        else:
            endpoint = f"{service.base_url}/chat/completions"
            request_body = {
                "model": profile.model,
                "messages": [
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": model_input},
                ],
                "max_tokens": profile.max_output_tokens,
            }
            if effective_temperature is not None:
                request_body["temperature"] = effective_temperature
            if structured_mode == STRUCTURED_STRICT:
                request_body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "novel_entities_events",
                        "strict": True,
                        "schema": wire_schema,
                    },
                }
            if effective_reasoning is not None:
                request_body["reasoning_effort"] = effective_reasoning

        try:
            async with httpx.AsyncClient(
                timeout=profile.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {service.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=request_body,
                )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                code="PROVIDER_TIMEOUT",
                message="在线 AI 响应超时，系统会自动重试。",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                code="PROVIDER_UNAVAILABLE",
                message="暂时无法连接在线 AI，系统会自动重试。",
                retryable=True,
            ) from exc

        if response.status_code == 429:
            retry_after = response.headers.get("retry-after")
            raise ProviderError(
                code="PROVIDER_RATE_LIMITED",
                message="在线 AI 当前请求过多，系统会稍后重试。",
                retryable=True,
                retry_after_seconds=float(retry_after) if retry_after and retry_after.isdigit() else None,
            )
        if response.status_code in {401, 403}:
            raise ProviderError(
                code="PROVIDER_AUTH_FAILED",
                message="API Key 无效或没有使用该模型的权限。",
                retryable=False,
            )
        if response.status_code >= 500:
            raise ProviderError(
                code="PROVIDER_UNAVAILABLE",
                message="在线 AI 服务暂时不可用，系统会自动重试。",
                retryable=True,
            )
        if response.status_code >= 400:
            detail = _response_error_detail(response)
            message = f"在线 AI 拒绝了请求（{response.status_code}）。"
            if detail:
                message += f" 服务返回：{detail}"
            message += "请检查模型、结构化输出和高级参数。"
            raise ProviderError(
                code="PROVIDER_BAD_REQUEST",
                message=message,
                retryable=False,
            )

        prompt_tokens = 0
        completion_tokens = 0
        try:
            body = response.json()
            if service.service_type == "OPENAI":
                usage = body.get("usage") or {}
                prompt_tokens = int(usage.get("input_tokens") or 0)
                completion_tokens = int(usage.get("output_tokens") or 0)
                output_text = next(
                    content["text"]
                    for item in body.get("output", [])
                    if item.get("type") == "message"
                    for content in item.get("content", [])
                    if content.get("type") == "output_text"
                )
            else:
                usage = body.get("usage") or {}
                prompt_tokens = int(usage.get("prompt_tokens") or 0)
                completion_tokens = int(usage.get("completion_tokens") or 0)
                output_text = body["choices"][0]["message"]["content"]
            if not isinstance(output_text, str):
                raise TypeError("OUTPUT_TEXT_MISSING")
            parsed = json.loads(output_text)
        except (ValueError, KeyError, StopIteration, TypeError, json.JSONDecodeError) as exc:
            cost = model_cost_snapshot(
                profile,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
            raise ProviderError(
                code="PROVIDER_INVALID_OUTPUT",
                message="在线 AI 没有返回符合要求的结构化结果，系统会自动重试。",
                retryable=True,
                diagnostics={"cost": cost} if cost is not None else {},
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                provider_name=service.id,
                model=profile.model,
            ) from exc
        cost = model_cost_snapshot(
            profile,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        return ProviderResponse(
            raw_text=output_text,
            parsed=parsed,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            provider_id=service.id,
            model=profile.model,
            parameters={
                "profile_id": profile.id,
                "temperature": effective_temperature,
                "max_output_tokens": profile.max_output_tokens,
                "reasoning_effort": effective_reasoning,
                "structured_output": structured_mode,
                "timeout_seconds": profile.timeout_seconds,
                "max_retries": profile.max_retries,
                "context_window_tokens": profile.context_window_tokens,
                "cost": cost,
            },
        )


def _response_error_detail(response: httpx.Response) -> str:
    """Extract a short, key-free upstream error explanation for diagnostics."""
    detail: object = None
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            detail = error.get("message") or error.get("detail") or error.get("code")
        elif isinstance(error, str):
            detail = error
        detail = detail or body.get("message") or body.get("detail")
    if not isinstance(detail, str) or not detail.strip():
        detail = response.text
    if not isinstance(detail, str):
        return ""
    cleaned = re.sub(r"\s+", " ", detail).strip()
    return cleaned[:400]
