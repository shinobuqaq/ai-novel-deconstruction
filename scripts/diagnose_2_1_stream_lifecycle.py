from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.orm import Session


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.db import create_db_engine  # noqa: E402
from app.providers.json_output import parse_json_text  # noqa: E402
from app.services.learning_report import (  # noqa: E402
    LEARNING_QUESTION_CATALOG_VERSION,
    _validated_2_1_program_artifact,
    assess_learning_report_readiness,
    parse_learning_report,
    provider_payload_for_learning_report,
)
from app.services.provider_config import (  # noqa: E402
    ENTITIES_EVENTS_PROFILE_ID,
    provider_http_headers,
    resolve_analysis_profile,
    schema_for_provider,
)
from app.services.workbench import build_workbench_projection  # noqa: E402

from compare_2_1_context_quality import (  # noqa: E402
    DEFAULT_OLD_INPUT,
    DEFAULT_RUN_ID,
    _comparison_inputs,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _event_fragment(event: dict[str, Any]) -> tuple[str, str]:
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    choices = event.get("choices")
    if not isinstance(choices, list):
        return "", ""
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            continue
        content = delta.get("content")
        if isinstance(content, str):
            content_parts.append(content)
        reasoning = delta.get("reasoning_content")
        if isinstance(reasoning, str):
            reasoning_parts.append(reasoning)
    return "".join(content_parts), "".join(reasoning_parts)


def _event_usage(event: dict[str, Any]) -> tuple[int, int]:
    usage = event.get("usage")
    if not isinstance(usage, dict):
        return 0, 0
    return (
        int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
        int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
    )


async def _stream_same_request(
    *,
    service: Any,
    profile: Any,
    provider_payload: dict[str, Any],
    model_input: str,
    output_dir: Path,
    idle_timeout_seconds: float,
    total_timeout_seconds: float,
) -> dict[str, Any]:
    wire_schema = schema_for_provider(provider_payload["output_schema"])
    instructions = (
        f"{provider_payload['instructions']}\n"
        "输出必须是 JSON 对象，并满足以下结构："
        f"{json.dumps(wire_schema, ensure_ascii=False, separators=(',', ':'))}"
    )
    endpoint = f"{service.base_url}/chat/completions"
    request_body: dict[str, Any] = {
        "model": profile.model,
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": model_input},
        ],
        "max_tokens": profile.max_output_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if profile.temperature is not None:
        request_body["temperature"] = profile.temperature
    if profile.reasoning_effort not in {"auto", "none"}:
        request_body["reasoning_effort"] = profile.reasoning_effort

    raw_path = output_dir / "stream-events.sse"
    partial_path = output_dir / "partial-output.txt"
    reasoning_path = output_dir / "partial-reasoning.txt"
    lifecycle_path = output_dir / "lifecycle.json"
    started = time.monotonic()
    lifecycle: dict[str, Any] = {
        "status": "RUNNING",
        "started_at": _utc_now(),
        "endpoint": endpoint,
        "model": profile.model,
        "input_chars": len(model_input),
        "max_output_tokens": profile.max_output_tokens,
        "idle_timeout_seconds": idle_timeout_seconds,
        "total_timeout_seconds": total_timeout_seconds,
        "response_headers_seconds": None,
        "first_event_seconds": None,
        "first_model_delta_seconds": None,
        "first_content_seconds": None,
        "event_count": 0,
        "content_chars": 0,
        "reasoning_chars": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "done_marker_seen": False,
        "http_status": None,
    }
    _write_json(lifecycle_path, lifecycle)
    timeout = httpx.Timeout(
        connect=30.0,
        read=idle_timeout_seconds,
        write=120.0,
        pool=30.0,
    )
    headers = provider_http_headers(service.api_key, json_content=True)
    headers["Accept"] = "text/event-stream"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with asyncio.timeout(total_timeout_seconds):
                async with client.stream(
                    "POST",
                    endpoint,
                    headers=headers,
                    json=request_body,
                ) as response:
                    lifecycle["http_status"] = response.status_code
                    lifecycle["response_headers_seconds"] = round(
                        time.monotonic() - started,
                        3,
                    )
                    _write_json(lifecycle_path, lifecycle)
                    if response.status_code >= 400:
                        body = await response.aread()
                        raise RuntimeError(
                            f"HTTP {response.status_code}: "
                            f"{body.decode('utf-8', errors='replace')[:500]}"
                        )

                    with (
                        raw_path.open("w", encoding="utf-8", newline="\n") as raw_file,
                        partial_path.open("w", encoding="utf-8", newline="") as partial_file,
                        reasoning_path.open("w", encoding="utf-8", newline="") as reasoning_file,
                    ):
                        async for line in response.aiter_lines():
                            raw_file.write(f"{line}\n")
                            raw_file.flush()
                            stripped = line.strip()
                            if not stripped or stripped.startswith(":"):
                                continue
                            if not stripped.startswith("data:"):
                                continue
                            data = stripped[5:].strip()
                            if data == "[DONE]":
                                lifecycle["done_marker_seen"] = True
                                _write_json(lifecycle_path, lifecycle)
                                continue
                            try:
                                event = json.loads(data)
                            except json.JSONDecodeError:
                                continue
                            if not isinstance(event, dict):
                                continue
                            if event.get("error"):
                                raise RuntimeError(
                                    "流式上游返回错误："
                                    f"{json.dumps(event['error'], ensure_ascii=False)[:500]}"
                                )
                            elapsed = round(time.monotonic() - started, 3)
                            lifecycle["event_count"] += 1
                            if lifecycle["first_event_seconds"] is None:
                                lifecycle["first_event_seconds"] = elapsed
                                print(
                                    f"收到首个 SSE 事件：{elapsed:.3f} 秒。",
                                    flush=True,
                                )
                            content, reasoning = _event_fragment(event)
                            if (
                                lifecycle["first_model_delta_seconds"] is None
                                and (content or reasoning)
                            ):
                                lifecycle["first_model_delta_seconds"] = elapsed
                                print(
                                    f"收到首个模型增量：{elapsed:.3f} 秒。",
                                    flush=True,
                                )
                            if content:
                                if lifecycle["first_content_seconds"] is None:
                                    lifecycle["first_content_seconds"] = elapsed
                                    print(
                                        f"收到首个正文字符：{elapsed:.3f} 秒。",
                                        flush=True,
                                    )
                                partial_file.write(content)
                                partial_file.flush()
                                lifecycle["content_chars"] += len(content)
                            if reasoning:
                                reasoning_file.write(reasoning)
                                reasoning_file.flush()
                                lifecycle["reasoning_chars"] += len(reasoning)
                            prompt_tokens, completion_tokens = _event_usage(event)
                            lifecycle["prompt_tokens"] = (
                                prompt_tokens or lifecycle["prompt_tokens"]
                            )
                            lifecycle["completion_tokens"] = (
                                completion_tokens or lifecycle["completion_tokens"]
                            )
                            if lifecycle["event_count"] % 25 == 0:
                                _write_json(lifecycle_path, lifecycle)

        lifecycle["status"] = "STREAM_COMPLETE"
    except BaseException as exc:
        lifecycle["status"] = "FAILED"
        lifecycle["error_type"] = type(exc).__name__
        lifecycle["error"] = str(exc) or repr(exc)
        raise
    finally:
        lifecycle["finished_at"] = _utc_now()
        lifecycle["elapsed_seconds"] = round(time.monotonic() - started, 3)
        if partial_path.exists():
            lifecycle["content_chars"] = len(
                partial_path.read_text(encoding="utf-8")
            )
        if reasoning_path.exists():
            lifecycle["reasoning_chars"] = len(
                reasoning_path.read_text(encoding="utf-8")
            )
        _write_json(lifecycle_path, lifecycle)
    return lifecycle


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "用与正式 2.1 精简臂相同的输入和输出合同强制走本机流式请求，"
            "逐事件落盘，用来区分首字等待、输出过重和客户端超时。"
        )
    )
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--old-input", type=Path, default=DEFAULT_OLD_INPUT)
    parser.add_argument("--long-target-chars", type=int, default=270_000)
    parser.add_argument(
        "--arm",
        choices=("compact", "long"),
        default="compact",
        help="选择精简相关输入臂或加入无关材料的长输入臂。",
    )
    parser.add_argument("--idle-timeout-seconds", type=float, default=900.0)
    parser.add_argument("--total-timeout-seconds", type=float, default=1800.0)
    args = parser.parse_args()

    settings = get_settings()
    engine = create_db_engine(settings)
    output_dir = (
        settings.workspace_dir
        / "diagnostics"
        / "learning-context-stream-lifecycle"
        / datetime.now().strftime("%Y%m%d-%H%M%S")
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    try:
        with Session(engine) as session:
            projection = build_workbench_projection(session, args.run_id)
            readiness = assess_learning_report_readiness(projection)
            check = next(
                item
                for item in readiness["checks"]
                if item["question_id"] == "2.1"
            )
            task_payload = {
                "run_id": args.run_id,
                "source_version_id": projection["source_version_id"],
                "source_deep_revision": projection["deep_revision"],
                "model_profile_id": ENTITIES_EVENTS_PROFILE_ID,
                "question_ids": ["2.1"],
                "question_answer_scopes": {"2.1": check["answer_scope"]},
                "question_source_fingerprints": {
                    "2.1": check["source_fingerprint"]
                },
                "current_question_source_fingerprints": {
                    item["question_id"]: item["source_fingerprint"]
                    for item in readiness["checks"]
                },
                "catalog_version": LEARNING_QUESTION_CATALOG_VERSION,
            }
            provider_payload = provider_payload_for_learning_report(
                session,
                settings,
                task_payload,
            )
        current_input = json.loads(provider_payload["input"])
        old_input = json.loads(args.old_input.read_text(encoding="utf-8"))
        compact_input, long_input = _comparison_inputs(
            current_input,
            old_input,
            long_target_chars=args.long_target_chars,
        )
        selected_input = compact_input if args.arm == "compact" else long_input
        (output_dir / f"{args.arm}-input.json").write_text(
            selected_input,
            encoding="utf-8",
        )
        service, profile = resolve_analysis_profile(
            settings,
            ENTITIES_EVENTS_PROFILE_ID,
        )
        print(
            f"开始本机流式复测：{args.arm} 输入 {len(selected_input):,} 字符；"
            f"诊断目录 {output_dir.resolve()}",
            flush=True,
        )
        lifecycle = asyncio.run(
            _stream_same_request(
                service=service,
                profile=profile,
                provider_payload=provider_payload,
                model_input=selected_input,
                output_dir=output_dir,
                idle_timeout_seconds=args.idle_timeout_seconds,
                total_timeout_seconds=args.total_timeout_seconds,
            )
        )
        raw_text = (output_dir / "partial-output.txt").read_text(encoding="utf-8")
        parsed_json = parse_json_text(raw_text)
        parsed = parse_learning_report(
            parsed_json.value,
            expected_question_ids=["2.1"],
        )
        answer = parsed.answers[0]
        artifact, program_metrics = _validated_2_1_program_artifact(
            answer,
            projection,
        )
        validation = {
            "schema_valid": True,
            "contract_items_complete": len(answer.contract_items) == 1,
            "classified_character_count": len(artifact["roles"]),
            "character_classification_complete": artifact[
                "contract_validation"
            ]["character_classification_complete"],
            "program_counts": [
                {
                    "label": item["label"],
                    "value": item["value"],
                    "unit": item["unit"],
                }
                for item in program_metrics[:3]
            ],
            "json_repairs": list(parsed_json.repairs),
        }
        _write_json(output_dir / "validation.json", validation)
        print(
            json.dumps(
                {
                    "lifecycle": lifecycle,
                    "validation": validation,
                    "output_dir": str(output_dir.resolve()),
                },
                ensure_ascii=False,
                indent=2,
            ),
            flush=True,
        )
        return 0
    except BaseException as exc:
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "error_type": type(exc).__name__,
                    "error": str(exc) or repr(exc),
                    "output_dir": str(output_dir.resolve()),
                },
                ensure_ascii=False,
                indent=2,
            ),
            flush=True,
        )
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
