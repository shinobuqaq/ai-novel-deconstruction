from __future__ import annotations

import json
from typing import Any

import httpx

from ..config import Settings
from ..services.provider_config import ModelSettingsError, resolve_analysis_profile
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

        if service.service_type == "OPENAI":
            endpoint = f"{service.base_url}/responses"
            request_body: dict[str, Any] = {
                "model": profile.model,
                "instructions": instructions,
                "input": model_input,
                "max_output_tokens": profile.max_output_tokens,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "novel_entities_events",
                        "strict": True,
                        "schema": schema,
                    }
                },
            }
            if profile.reasoning_effort != "none":
                request_body["reasoning"] = {"effort": profile.reasoning_effort}
            else:
                request_body["temperature"] = profile.temperature
        else:
            endpoint = f"{service.base_url}/chat/completions"
            request_body = {
                "model": profile.model,
                "messages": [
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": model_input},
                ],
                "temperature": profile.temperature,
                "max_tokens": profile.max_output_tokens,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "novel_entities_events",
                        "strict": True,
                        "schema": schema,
                    },
                },
            }
            if profile.reasoning_effort != "none":
                request_body["reasoning_effort"] = profile.reasoning_effort

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
            raise ProviderError(
                code="PROVIDER_BAD_REQUEST",
                message=f"在线 AI 拒绝了请求（{response.status_code}）。请检查模型和高级参数。",
                retryable=False,
            )

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
                usage = body.get("usage") or {}
                prompt_tokens = int(usage.get("input_tokens") or 0)
                completion_tokens = int(usage.get("output_tokens") or 0)
            else:
                output_text = body["choices"][0]["message"]["content"]
                usage = body.get("usage") or {}
                prompt_tokens = int(usage.get("prompt_tokens") or 0)
                completion_tokens = int(usage.get("completion_tokens") or 0)
            if not isinstance(output_text, str):
                raise TypeError("OUTPUT_TEXT_MISSING")
            parsed = json.loads(output_text)
        except (ValueError, KeyError, StopIteration, TypeError, json.JSONDecodeError) as exc:
            raise ProviderError(
                code="PROVIDER_INVALID_OUTPUT",
                message="在线 AI 没有返回符合要求的结构化结果，系统会自动重试。",
                retryable=True,
            ) from exc
        return ProviderResponse(
            raw_text=output_text,
            parsed=parsed,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            provider_id=service.id,
            model=profile.model,
            parameters={
                "profile_id": profile.id,
                "temperature": profile.temperature,
                "max_output_tokens": profile.max_output_tokens,
                "reasoning_effort": profile.reasoning_effort,
                "timeout_seconds": profile.timeout_seconds,
                "max_retries": profile.max_retries,
            },
        )
