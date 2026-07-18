from __future__ import annotations

import json
from typing import Any

import httpx

from ..config import Settings
from ..services.provider_config import read_openai_config
from .base import ProviderError, ProviderResponse


class OpenAIResponsesProvider:
    name = "openai"

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport

    async def complete(self, *, task_kind: str, payload: dict[str, Any]) -> ProviderResponse:
        config = read_openai_config(self.settings)
        if not config.configured:
            raise ProviderError(
                code="PROVIDER_NOT_CONFIGURED",
                message="请先在工作台中配置 OpenAI API Key。",
                retryable=False,
            )
        schema = payload.get("output_schema")
        instructions = payload.get("instructions")
        model_input = payload.get("input")
        if not isinstance(schema, dict) or not isinstance(instructions, str) or not isinstance(model_input, str):
            raise ProviderError(
                code="PROVIDER_BAD_REQUEST",
                message="分析任务缺少结构化输出要求。",
                retryable=False,
            )
        request_body = {
            "model": config.model,
            "instructions": instructions,
            "input": model_input,
            "reasoning": {"effort": self.settings.openai_reasoning_effort},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "novel_entities_events",
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.openai_timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    f"{config.base_url}/responses",
                    headers={
                        "Authorization": f"Bearer {config.api_key}",
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
                message=f"在线 AI 拒绝了请求（{response.status_code}）。",
                retryable=False,
            )

        try:
            body = response.json()
            output_text = next(
                content["text"]
                for item in body.get("output", [])
                if item.get("type") == "message"
                for content in item.get("content", [])
                if content.get("type") == "output_text"
            )
            parsed = json.loads(output_text)
        except (ValueError, KeyError, StopIteration, TypeError, json.JSONDecodeError) as exc:
            raise ProviderError(
                code="PROVIDER_INVALID_OUTPUT",
                message="在线 AI 没有返回符合要求的结构化结果，系统会自动重试。",
                retryable=True,
            ) from exc
        usage = body.get("usage") or {}
        return ProviderResponse(
            raw_text=output_text,
            parsed=parsed,
            prompt_tokens=int(usage.get("input_tokens") or 0),
            completion_tokens=int(usage.get("output_tokens") or 0),
        )
