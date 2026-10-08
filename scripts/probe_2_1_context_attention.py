from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.db import create_db_engine  # noqa: E402
from app.providers import create_default_provider_registry  # noqa: E402
from app.services.learning_report import (  # noqa: E402
    LEARNING_QUESTION_CATALOG_VERSION,
    assess_learning_report_readiness,
    provider_payload_for_learning_report,
)
from app.services.provider_config import (  # noqa: E402
    ENTITIES_EVENTS_PROFILE_ID,
    resolve_analysis_profile,
)
from app.services.workbench import build_workbench_projection  # noqa: E402


DEFAULT_RUN_ID = "run_e9bfe7fa166348bba53115394608955e"
DEFAULT_OLD_INPUT = (
    REPO_ROOT
    / "workspace"
    / "diagnostics"
    / "model-call-inputs"
    / "tsk_c42fd89f430346d1b99606cfb9242873"
    / "att_50159b2da500413b8aded98c11620509.txt"
)


class AttentionProbeItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=160)
    first_action_chapter: int = Field(ge=1, le=30)
    first_action_event_id: str = Field(min_length=1, max_length=80)
    evidence_id: str = Field(min_length=1, max_length=80)
    first_scene_function: Literal[
        "主角锚点",
        "关系锚点",
        "引路或导师",
        "盟友或协作者",
        "对手或竞争者",
        "威胁或施压者",
        "权威或规则执行者",
        "信息提供者",
        "世界展示载体",
        "调剂或喜剧功能",
        "背景行动者",
        "其他",
    ]


class AttentionProbeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AttentionProbeItem] = Field(min_length=24, max_length=24)


def _json_text(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


def _inline_schema(model: type[BaseModel]) -> dict[str, object]:
    raw = model.model_json_schema()
    definitions = raw.get("$defs", {})

    def expand(value: object) -> object:
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/$defs/"):
                return expand(definitions[reference.rsplit("/", 1)[-1]])
            return {
                key: expand(item)
                for key, item in value.items()
                if key not in {"$defs", "$ref"}
            }
        if isinstance(value, list):
            return [expand(item) for item in value]
        return value

    return expand(raw)  # type: ignore[return-value]


def _evenly_spread(items: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    if len(items) < count:
        raise RuntimeError(f"客观核查点不足：{len(items)}/{count}")
    if count == 1:
        return [items[0]]
    indexes = [
        round(index * (len(items) - 1) / (count - 1))
        for index in range(count)
    ]
    return [items[index] for index in indexes]


def _bundle_key(value: object) -> str:
    return hashlib.sha256(_json_text(value).encode("utf-8")).hexdigest()


def _build_inputs(
    current_input: dict[str, Any],
    old_input: dict[str, Any],
    *,
    long_target_chars: int,
) -> tuple[str, str, list[dict[str, Any]]]:
    all_roles = list(
        current_input["program_artifacts"]["opening_character_ledger"]["roles"]
    )
    all_roles.sort(
        key=lambda item: (
            int(item["first_action_chapter"]),
            int(item["sequence_no"]),
        )
    )
    sample_roles = _evenly_spread(all_roles, 24)
    sampled_event_ids = {
        str(item["first_action_event_id"]) for item in sample_roles
    }
    compact_materials = [
        item
        for item in current_input["materials"]
        if (
            item.get("kind") == "opening_first_action_event"
            and str(item.get("item", {}).get("id") or "") in sampled_event_ids
        )
    ]
    material_by_event = {
        str(item["item"]["id"]): item for item in compact_materials
    }
    missing_events = sorted(sampled_event_ids - set(material_by_event))
    if missing_events:
        raise RuntimeError(f"精简臂缺首次事件原文：{missing_events}")

    probe_cases = []
    for role in sample_roles:
        material = material_by_event[str(role["first_action_event_id"])]
        evidence_ids = {
            str(item["id"]) for item in material.get("evidence", [])
        }
        allowed_ids = [
            str(evidence_id)
            for evidence_id in role["first_action_evidence_ids"]
            if str(evidence_id) in evidence_ids
        ]
        if not allowed_ids:
            raise RuntimeError(
                f"{role['character_name']} 的首次事件没有可用原文。"
            )
        probe_cases.append({
            "subject": role["character_name"],
            "first_action_chapter": role["first_action_chapter"],
            "first_action_event_id": role["first_action_event_id"],
            "allowed_evidence_ids": allowed_ids,
        })
    fixed = {
        "diagnostic_goal": (
            "测量上下文变长后是否客观漏掉、抄错或编造指定事实；"
            "不是生成正式 2.1 答案。"
        ),
        "required_cases": probe_cases,
    }
    compact_input = _json_text({
        **fixed,
        "materials": compact_materials,
        "coverage": {
            "arm": "COMPACT_RELEVANT",
            "required_case_count": 24,
        },
    })
    long_materials = list(compact_materials)
    seen = {_bundle_key(item) for item in long_materials}
    distractors = [
        item
        for item in old_input.get("materials", [])
        if isinstance(item, dict)
    ]
    distractors.sort(
        key=lambda item: (
            item.get("kind") in {
                "opening_action_program_count",
                "opening_event",
            },
            str(item.get("kind") or ""),
        )
    )
    for item in distractors:
        key = _bundle_key(item)
        if key in seen:
            continue
        candidate = _json_text({
            **fixed,
            "materials": [*long_materials, item],
            "coverage": {
                "arm": "LONG_WITH_DISTRACTORS",
                "required_case_count": 24,
            },
        })
        if len(candidate) > long_target_chars:
            continue
        long_materials.append(item)
        seen.add(key)
    long_input = _json_text({
        **fixed,
        "materials": long_materials,
        "coverage": {
            "arm": "LONG_WITH_DISTRACTORS",
            "required_case_count": 24,
        },
    })
    return compact_input, long_input, probe_cases


def _validate(
    parsed: dict[str, Any],
    probe_cases: list[dict[str, Any]],
) -> dict[str, object]:
    try:
        output = AttentionProbeOutput.model_validate(parsed)
    except ValidationError as exc:
        return {
            "schema_valid": False,
            "complete": False,
            "errors": [
                {
                    "path": list(item["loc"]),
                    "message": item["msg"],
                }
                for item in exc.errors()
            ],
        }
    expected = {str(item["subject"]): item for item in probe_cases}
    actual = {item.subject: item for item in output.items}
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    duplicated = len(actual) != len(output.items)
    mismatches: list[dict[str, object]] = []
    chapter_bands = {"1-10": 0, "11-20": 0, "21-30": 0}
    for subject, expected_item in expected.items():
        item = actual.get(subject)
        if item is None:
            continue
        if item.first_action_chapter <= 10:
            chapter_bands["1-10"] += 1
        elif item.first_action_chapter <= 20:
            chapter_bands["11-20"] += 1
        else:
            chapter_bands["21-30"] += 1
        problems = []
        if item.first_action_chapter != expected_item["first_action_chapter"]:
            problems.append("chapter")
        if item.first_action_event_id != expected_item["first_action_event_id"]:
            problems.append("event")
        if item.evidence_id not in expected_item["allowed_evidence_ids"]:
            problems.append("evidence")
        if problems:
            mismatches.append({"subject": subject, "fields": problems})
    return {
        "schema_valid": True,
        "complete": not missing and not extra and not duplicated and not mismatches,
        "required_count": len(expected),
        "returned_count": len(output.items),
        "missing_subjects": missing,
        "extra_subjects": extra,
        "duplicated_subject": duplicated,
        "reference_mismatches": mismatches,
        "correct_cases_by_chapter_band": chapter_bands,
    }


async def _run_arm(
    provider: Any,
    payload: dict[str, Any],
    *,
    arm: str,
    model_input: str,
    probe_cases: list[dict[str, Any]],
) -> tuple[dict[str, object], dict[str, Any]]:
    response = await provider.complete(
        task_kind="analysis.learning_report",
        payload={**payload, "input": model_input},
    )
    validation = _validate(response.parsed, probe_cases)
    result = {
        "arm": arm,
        "input_chars": len(model_input),
        "prompt_tokens": response.prompt_tokens,
        "completion_tokens": response.completion_tokens,
        "model": response.model,
        **validation,
    }
    return result, {
        "parsed": response.parsed,
        "raw_text": response.raw_text,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="用 24 个跨章节客观核查点执行一次精简/长输入注意力对照。"
    )
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--old-input", type=Path, default=DEFAULT_OLD_INPUT)
    parser.add_argument("--long-target-chars", type=int, default=270_000)
    parser.add_argument(
        "--arm",
        choices=("compact", "long", "both"),
        default="both",
        help="只跑精简臂、长输入臂，或同时运行两臂。",
    )
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    engine = create_db_engine(settings)
    output_dir = (
        settings.workspace_dir
        / "diagnostics"
        / "learning-context-attention-probe"
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
            base_payload = provider_payload_for_learning_report(
                session,
                settings,
                task_payload,
            )
        current_input = json.loads(base_payload["input"])
        old_input = json.loads(args.old_input.read_text(encoding="utf-8"))
        compact_input, long_input, probe_cases = _build_inputs(
            current_input,
            old_input,
            long_target_chars=args.long_target_chars,
        )
        (output_dir / "compact-input.json").write_text(
            compact_input,
            encoding="utf-8",
        )
        (output_dir / "long-input.json").write_text(
            long_input,
            encoding="utf-8",
        )
        if args.prepare_only:
            print(json.dumps({
                "compact_input_chars": len(compact_input),
                "long_input_chars": len(long_input),
                "required_case_count": len(probe_cases),
                "output_dir": str(output_dir.resolve()),
            }, ensure_ascii=False, indent=2))
            return 0

        service, profile = resolve_analysis_profile(
            settings,
            ENTITIES_EVENTS_PROFILE_ID,
        )
        prompt = (
            "你正在执行长上下文客观注意力测试，不是写分析报告。"
            "required_cases 恰好有 24 项；items 必须按相同顺序逐项返回，"
            "subject、first_action_chapter、first_action_event_id 必须原样复制。"
            "evidence_id 必须从该项 allowed_evidence_ids 中选择，并确认对应 materials 原文。"
            "first_scene_function 只做枚举分类，不写解释。不得漏项、增项或改名。"
            "只返回符合 JSON Schema 的对象。"
        )
        payload = {
            **base_payload,
            "instructions": prompt,
            "output_schema": _inline_schema(AttentionProbeOutput),
            "provider_service_id": service.id,
            "provider_model": profile.model,
        }
        provider = create_default_provider_registry(settings).resolve("openai")
        results = []
        selected_arms = (
            ("COMPACT_RELEVANT", compact_input),
            ("LONG_WITH_DISTRACTORS", long_input),
        )
        if args.arm == "compact":
            selected_arms = selected_arms[:1]
        elif args.arm == "long":
            selected_arms = selected_arms[1:]
        for arm, model_input in selected_arms:
            print(f"开始 {arm}：输入 {len(model_input):,} 字符。", flush=True)
            result, response = asyncio.run(_run_arm(
                provider,
                payload,
                arm=arm,
                model_input=model_input,
                probe_cases=probe_cases,
            ))
            results.append(result)
            (output_dir / f"{arm.lower()}-response.json").write_text(
                json.dumps(response, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(
                f"完成 {arm}：输入 {result['prompt_tokens']:,} Token，"
                f"输出 {result['completion_tokens']:,} Token，"
                f"客观通过={'是' if result['complete'] else '否'}。",
                flush=True,
            )
        if args.arm == "both":
            compact_ok = bool(results[0]["complete"])
            long_ok = bool(results[1]["complete"])
            interpretation = (
                "BOTH_PASS_NO_OBJECTIVE_DEGRADATION_AT_TESTED_LENGTH"
                if compact_ok and long_ok
                else "LONG_ONLY_FAILS_OBJECTIVE_LONG_CONTEXT_DEGRADATION"
                if compact_ok and not long_ok
                else "MECHANISM_OR_MODEL_FAILS_EVEN_COMPACT"
            )
        else:
            selected_ok = bool(results[0]["complete"])
            interpretation = (
                f"{results[0]['arm']}_PASS"
                if selected_ok
                else f"{results[0]['arm']}_FAIL"
            )
        report = {
            "run_id": args.run_id,
            "model": profile.model,
            "objective_policy": (
                "只检查 24 项是否不重不漏，以及人物、首次章节、事件和证据是否精确；"
                "首场功能类别差异不计客观错误。"
            ),
            "results": results,
            "interpretation": interpretation,
        }
        (output_dir / "comparison.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"诊断制品：{output_dir.resolve()}")
        return 0 if all(bool(item["complete"]) for item in results) else 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
