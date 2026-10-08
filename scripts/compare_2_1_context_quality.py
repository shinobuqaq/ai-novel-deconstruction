from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.db import create_db_engine  # noqa: E402
from app.providers import create_default_provider_registry  # noqa: E402
from app.services.learning_report import (  # noqa: E402
    LEARNING_QUESTION_CATALOG_VERSION,
    _validated_2_1_program_artifact,
    assess_learning_report_readiness,
    parse_learning_report,
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


def _json_text(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


def _bundle_key(value: object) -> str:
    return hashlib.sha256(_json_text(value).encode("utf-8")).hexdigest()


def _coverage_manifest(
    *,
    arm: str,
    materials: list[dict[str, Any]],
    input_chars: int,
) -> dict[str, object]:
    kinds = sorted({str(item.get("kind") or "") for item in materials})
    return {
        "diagnostic_arm": arm,
        "question_id": "2.1",
        "material_count": len(materials),
        "material_chars": sum(len(_json_text(item)) for item in materials),
        "input_chars": input_chars,
        "selected_by_kind": {
            kind: sum(1 for item in materials if item.get("kind") == kind)
            for kind in kinds
        },
        "comparison_policy": (
            "两臂保留同一 2.1 程序账本和全部首次行动事件原文；"
            "长输入臂只增加与 2.1 合同无关或非必需的旧请求材料。"
        ),
    }


def _arm_input(
    fixed: dict[str, Any],
    materials: list[dict[str, Any]],
    *,
    arm: str,
) -> str:
    payload = {**fixed, "materials": materials}
    provisional = _json_text(payload)
    payload["coverage_manifest"] = _coverage_manifest(
        arm=arm,
        materials=materials,
        input_chars=len(provisional),
    )
    return _json_text(payload)


def _comparison_inputs(
    current_input: dict[str, Any],
    old_input: dict[str, Any],
    *,
    long_target_chars: int,
) -> tuple[str, str]:
    fixed = {
        key: value
        for key, value in current_input.items()
        if key not in {"materials", "coverage_manifest"}
    }
    compact_materials = [
        item
        for item in current_input.get("materials", [])
        if item.get("kind") == "opening_first_action_event"
    ]
    expected_first_event_count = len({
        str(item.get("first_action_event_id") or "")
        for item in fixed["program_artifacts"]["opening_character_ledger"]["roles"]
    })
    if len(compact_materials) != expected_first_event_count:
        raise RuntimeError(
            "当前 2.1 输入没有完整包含每个首次行动事件原文："
            f"{len(compact_materials)}/{expected_first_event_count}"
        )
    compact_input = _arm_input(fixed, compact_materials, arm="COMPACT_RELEVANT")

    long_materials = list(compact_materials)
    seen = {_bundle_key(item) for item in long_materials}
    old_materials = [
        item
        for item in old_input.get("materials", [])
        if isinstance(item, dict)
    ]
    old_materials.sort(
        key=lambda item: (
            item.get("kind") in {
                "opening_action_program_count",
                "opening_event",
            },
            str(item.get("kind") or ""),
        )
    )
    for item in old_materials:
        key = _bundle_key(item)
        if key in seen:
            continue
        candidate = _arm_input(
            fixed,
            [*long_materials, item],
            arm="LONG_WITH_DISTRACTORS",
        )
        if len(candidate) > long_target_chars:
            continue
        long_materials.append(item)
        seen.add(key)
    long_input = _arm_input(
        fixed,
        long_materials,
        arm="LONG_WITH_DISTRACTORS",
    )
    return compact_input, long_input


async def _call_arm(
    provider: Any,
    provider_payload: dict[str, Any],
    *,
    arm: str,
    model_input: str,
    projection: dict,
) -> tuple[dict[str, object], dict[str, object]]:
    payload = {
        **provider_payload,
        "input": model_input,
        "context_manifest": {
            "diagnostic_arm": arm,
            "input_chars": len(model_input),
        },
    }
    response = await provider.complete(
        task_kind="analysis.learning_report",
        payload=payload,
    )
    result: dict[str, object] = {
        "arm": arm,
        "input_chars": len(model_input),
        "prompt_tokens": response.prompt_tokens,
        "completion_tokens": response.completion_tokens,
        "model": response.model,
        "provider_id": response.provider_id,
        "schema_valid": False,
        "contract_items_complete": False,
        "character_references_valid": False,
        "evidence_references_valid": False,
        "program_counts": [],
    }
    parsed = parse_learning_report(
        response.parsed,
        expected_question_ids=["2.1"],
    )
    answer = parsed.answers[0]
    artifact, program_metrics = _validated_2_1_program_artifact(
        answer,
        projection,
    )
    result.update({
        "schema_valid": True,
        "contract_items_complete": len(answer.contract_items) == 1,
        "character_references_valid": artifact["contract_validation"][
            "character_classification_complete"
        ],
        "evidence_references_valid": True,
        "classified_character_count": len(artifact["roles"]),
        "first_action_chapter_span": [
            min(int(item["first_action_chapter"]) for item in artifact["roles"]),
            max(int(item["first_action_chapter"]) for item in artifact["roles"]),
        ],
        "program_counts": [
            {
                "label": item["label"],
                "value": item["value"],
                "unit": item["unit"],
            }
            for item in program_metrics[:3]
        ],
        "insufficient_contract_items": [
            item.item_id
            for item in answer.contract_items
            if item.status == "INSUFFICIENT_EVIDENCE"
        ],
    })
    return result, {
        "parsed": response.parsed,
        "raw_text": response.raw_text,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "对 2.1 执行一次精简相关输入与长输入的同问同证据客观对照；"
            "不保存学习答案，不修改正式数据库。"
        )
    )
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--old-input", type=Path, default=DEFAULT_OLD_INPUT)
    parser.add_argument("--long-target-chars", type=int, default=270_000)
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="只生成两臂输入并报告规模，不调用模型。",
    )
    args = parser.parse_args()

    settings = get_settings()
    engine = create_db_engine(settings)
    output_dir = (
        settings.workspace_dir
        / "diagnostics"
        / "learning-context-comparison"
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
            if not check["ready"]:
                raise RuntimeError("2.1 专项原料未就绪，不能执行对照。")
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
        (output_dir / "compact-input.json").write_text(
            compact_input,
            encoding="utf-8",
        )
        (output_dir / "long-input.json").write_text(
            long_input,
            encoding="utf-8",
        )
        if args.prepare_only:
            prepared = {
                "compact_input_chars": len(compact_input),
                "long_input_chars": len(long_input),
                "output_dir": str(output_dir.resolve()),
            }
            print(json.dumps(prepared, ensure_ascii=False, indent=2))
            return 0

        service, profile = resolve_analysis_profile(
            settings,
            ENTITIES_EVENTS_PROFILE_ID,
        )
        provider_payload.update({
            "provider_service_id": service.id,
            "provider_model": profile.model,
        })
        provider = create_default_provider_registry(settings).resolve("openai")
        results: list[dict[str, object]] = []
        for arm, model_input in (
            ("COMPACT_RELEVANT", compact_input),
            ("LONG_WITH_DISTRACTORS", long_input),
        ):
            print(f"开始 {arm}：输入 {len(model_input):,} 字符。", flush=True)
            result, response = asyncio.run(_call_arm(
                provider,
                provider_payload,
                arm=arm,
                model_input=model_input,
                projection=projection,
            ))
            results.append(result)
            (output_dir / f"{arm.lower()}-response.json").write_text(
                json.dumps(response, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(
                f"完成 {arm}：输入 {result['prompt_tokens']:,} Token，"
                f"输出 {result['completion_tokens']:,} Token。",
                flush=True,
            )
        comparison = {
            "run_id": args.run_id,
            "question_id": "2.1",
            "model": profile.model,
            "policy": (
                "只判断合同完整性、程序数字、人物/章节/事件/证据对应、"
                "跨前中后章节覆盖和输入外编造；不比较主观措辞。"
            ),
            "results": results,
            "interpretation": (
                "BOTH_VALID_MECHANISM_WAS_PRIMARY"
                if all(
                    item["schema_valid"]
                    and item["contract_items_complete"]
                    and item["character_references_valid"]
                    and item["evidence_references_valid"]
                    for item in results
                )
                else "OBJECTIVE_DIFFERENCE_OR_MECHANISM_STILL_INCOMPLETE"
            ),
        }
        (output_dir / "comparison.json").write_text(
            json.dumps(comparison, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(comparison, ensure_ascii=False, indent=2))
        print(f"诊断制品：{output_dir.resolve()}")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
