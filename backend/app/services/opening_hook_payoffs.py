from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import (
    AnalysisRun,
    AnalysisRunStatus,
    AnalysisRunTask,
    EvidenceSpan,
    LearningQuestionEvidence,
    SourceUnit,
    SourceVersion,
    Task,
    TaskStatus,
)
from ..repositories import request_task_cancellation
from .chapter_end_hooks import build_chapter_end_hooks_projection
from .learning_report import _request_budget
from .provider_config import (
    ENTITIES_EVENTS_PROFILE_ID,
    ModelSettingsError,
    prepare_task_provider_routes,
    resolve_analysis_profile,
)


OPENING_HOOK_PAYOFFS_TASK_KIND = "analysis.opening_hook_payoffs"
OPENING_HOOK_PAYOFFS_QUESTION_ID = "3.4"
OPENING_HOOK_PAYOFFS_PROMPT_ID = "opening_hook_payoffs"
OPENING_HOOK_PAYOFFS_PROMPT_VERSION = "1.1.0"
OPENING_HOOK_PAYOFFS_WINDOW_OVERHEAD_CHARS = 16_000

_TRAILING_BOILERPLATE = re.compile(
    r"(?:https?://|www\.|\.com\b|\.net\b|"
    r"更多精彩|更多好书|请看小说网|txt\d*\.com|"
    r"声明[：:]?本书|本站只提供|用户上传|免费下载服务|版权.*无任何关系)",
    re.IGNORECASE,
)


class OpeningHookPayoffProposal(BaseModel):
    model_config = {"extra": "forbid"}

    hook_chapter: int = Field(ge=1, le=3)
    result: Literal[
        "FOUND_COMPLETE",
        "FOUND_PARTIAL",
        "NOT_FOUND_IN_WINDOW",
        "NO_HOOK",
    ]
    response_evidence_id: str | None = Field(default=None, max_length=64)
    response_summary: str = Field(default="", max_length=1000)
    rationale: str = Field(min_length=1, max_length=1200)

    @model_validator(mode="after")
    def require_consistent_result(self) -> "OpeningHookPayoffProposal":
        found = self.result in {"FOUND_COMPLETE", "FOUND_PARTIAL"}
        if found and (
            not self.response_evidence_id
            or not self.response_summary.strip()
        ):
            raise ValueError("找到回应时必须返回原文编号和回应摘要")
        if not found and (
            self.response_evidence_id
            or self.response_summary.strip()
        ):
            raise ValueError("未找到回应或无钩时不能挂接回应原文")
        return self


class OpeningHookPayoffsOutput(BaseModel):
    model_config = {"extra": "forbid"}

    results: list[OpeningHookPayoffProposal] = Field(
        min_length=1,
        max_length=3,
    )


class OpeningHookPayoffsValidationError(ValueError):
    def __init__(self, code: str, errors: list[dict[str, object]]) -> None:
        super().__init__(code)
        self.code = code
        self.errors = errors


def _validation_errors(exc: ValidationError) -> list[dict[str, object]]:
    return [
        {
            "path": [str(part) for part in item.get("loc", ())],
            "type": str(item.get("type") or "value_error"),
            "message": str(item.get("msg") or "字段无效"),
        }
        for item in exc.errors()
    ]


def parse_opening_hook_payoffs(value: dict) -> OpeningHookPayoffsOutput:
    try:
        output = OpeningHookPayoffsOutput.model_validate(value)
    except ValidationError as exc:
        raise OpeningHookPayoffsValidationError(
            "OPENING_HOOK_PAYOFFS_OUTPUT_INVALID",
            _validation_errors(exc),
        ) from exc
    hook_chapters = [item.hook_chapter for item in output.results]
    if len(set(hook_chapters)) != len(hook_chapters):
        raise OpeningHookPayoffsValidationError(
            "OPENING_HOOK_PAYOFFS_HOOK_DUPLICATED",
            [{
                "path": ["results"],
                "type": "value_error",
                "message": "每个前三章钩子在一个窗口内只能返回一次",
            }],
        )
    return output


def _inline_model_schema(model: type[BaseModel]) -> dict:
    raw = model.model_json_schema()
    definitions = raw.get("$defs", {})

    def expand(value: object) -> object:
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/$defs/"):
                return expand(definitions.get(reference.rsplit("/", 1)[-1], {}))
            return {
                key: expand(item)
                for key, item in value.items()
                if key not in {"$defs", "$ref"}
            }
        if isinstance(value, list):
            return [expand(item) for item in value]
        return value

    return expand(raw)  # type: ignore[return-value]


def _prompt() -> str:
    path = (
        Path(__file__).resolve().parents[3]
        / "prompts"
        / "opening_hook_payoffs_v1.md"
    )
    return path.read_text(encoding="utf-8").strip()


def _base_projection(session: Session, run_id: str) -> dict:
    from .workbench import build_workbench_projection

    return build_workbench_projection(
        session,
        run_id,
        include_question_evidence=False,
    )


def _chapter_units(session: Session, source_version_id: str) -> list[SourceUnit]:
    return list(session.scalars(
        select(SourceUnit)
        .where(
            SourceUnit.source_version_id == source_version_id,
            SourceUnit.unit_type == "CHAPTER",
        )
        .order_by(SourceUnit.ordinal)
    ))


def _is_heading(span: EvidenceSpan, unit: SourceUnit) -> bool:
    return re.sub(r"\s+", "", span.text_snapshot).casefold() == re.sub(
        r"\s+", "",
        unit.title,
    ).casefold()


def _is_boilerplate(span: EvidenceSpan) -> bool:
    return bool(_TRAILING_BOILERPLATE.search(span.text_snapshot))


def _opening_hooks(hook_evidence: dict) -> list[dict[str, object]]:
    return [
        {
            key: item.get(key)
            for key in (
                "chapter_ordinal",
                "chapter_title",
                "ending_evidence_id",
                "ending_evidence_ids",
                "hook_type",
                "strength",
                "hook_question",
                "rationale",
                "retention_basis",
            )
        }
        for item in hook_evidence.get("chapters", [])
        if 1 <= int(item.get("chapter_ordinal") or 0) <= 3
    ]


def _current_context(
    session: Session,
    run_id: str,
) -> tuple[dict, list[SourceUnit], dict | None]:
    projection = _base_projection(session, run_id)
    source_version_id = str(projection.get("source_version_id") or "")
    units = _chapter_units(session, source_version_id) if source_version_id else []
    hook_status, hook_evidence = build_chapter_end_hooks_projection(
        session,
        run_id,
        projection,
    )
    if hook_status != "READY":
        hook_evidence = None
    return projection, units, hook_evidence


def opening_hook_payoffs_source_fingerprint(
    projection: dict,
    units: list[SourceUnit],
    hook_evidence: dict,
) -> str:
    payload = {
        "source_version_id": projection.get("source_version_id"),
        "deep_revision": projection.get("deep_revision"),
        "chapter_hashes": [
            {
                "id": unit.id,
                "ordinal": unit.ordinal,
                "content_hash": unit.content_hash,
            }
            for unit in units
        ],
        "chapter_end_hooks_prompt_version": hook_evidence.get("prompt_version"),
        "chapter_end_hooks_revision": hook_evidence.get("revision"),
        "opening_hooks": _opening_hooks(hook_evidence),
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _sources_ready(
    projection: dict,
    units: list[SourceUnit],
    hook_evidence: dict | None,
) -> bool:
    return bool(
        len(units) >= 3
        and projection.get("deep_status") == "READY"
        and hook_evidence
        and len(_opening_hooks(hook_evidence)) == 3
    )


def _chapter_response_records(
    session: Session,
    units: list[SourceUnit],
    *,
    chapter_start: int,
    chapter_end: int,
) -> tuple[list[dict[str, object]], dict[str, EvidenceSpan], int]:
    chapters: list[dict[str, object]] = []
    evidence_by_id: dict[str, EvidenceSpan] = {}
    ignored_total = 0
    for chapter_ordinal in range(chapter_start, chapter_end + 1):
        unit = units[chapter_ordinal - 1]
        spans = list(session.scalars(
            select(EvidenceSpan)
            .where(EvidenceSpan.source_unit_id == unit.id)
            .order_by(
                EvidenceSpan.start_char,
                EvidenceSpan.end_char,
                EvidenceSpan.paragraph_index,
            )
        ))
        body_spans = [
            span
            for span in spans
            if not _is_heading(span, unit) and not _is_boilerplate(span)
        ]
        ignored_total += len(spans) - len(body_spans)
        paragraphs: list[dict[str, object]] = []
        for paragraph_index, span in enumerate(body_spans, start=1):
            evidence_by_id[span.id] = span
            paragraphs.append({
                "evidence_id": span.id,
                "paragraph_index": paragraph_index,
                "source_start_char": span.start_char,
                "text": span.text_snapshot,
            })
        chapters.append({
            "chapter_ordinal": chapter_ordinal,
            "chapter_title": unit.title,
            "paragraphs": paragraphs,
        })
    return chapters, evidence_by_id, ignored_total


def _all_response_chapter_sizes(
    session: Session,
    units: list[SourceUnit],
) -> list[dict[str, int]]:
    if len(units) < 2:
        return []
    later_units = units[1:]
    spans_by_unit: dict[str, list[EvidenceSpan]] = {
        unit.id: [] for unit in later_units
    }
    for span in session.scalars(
        select(EvidenceSpan)
        .where(EvidenceSpan.source_unit_id.in_([unit.id for unit in later_units]))
        .order_by(
            EvidenceSpan.source_unit_id,
            EvidenceSpan.start_char,
            EvidenceSpan.end_char,
            EvidenceSpan.paragraph_index,
        )
    ):
        spans_by_unit.setdefault(span.source_unit_id, []).append(span)
    sizes: list[dict[str, int]] = []
    for chapter_ordinal, unit in enumerate(later_units, start=2):
        paragraphs = [
            {
                "evidence_id": span.id,
                "paragraph_index": span.paragraph_index,
                "text": span.text_snapshot,
            }
            for span in spans_by_unit.get(unit.id, [])
            if not _is_heading(span, unit) and not _is_boilerplate(span)
        ]
        record = {
            "chapter_ordinal": chapter_ordinal,
            "chapter_title": unit.title,
            "paragraphs": paragraphs,
        }
        sizes.append({
            "chapter_ordinal": chapter_ordinal,
            "estimated_material_chars": len(json.dumps(
                record,
                ensure_ascii=False,
                separators=(",", ":"),
            )),
        })
    return sizes


def _window_specs(
    chapter_sizes: list[dict[str, int]],
    *,
    input_char_budget: int,
) -> list[dict[str, int]]:
    material_budget = max(
        8_000,
        input_char_budget - OPENING_HOOK_PAYOFFS_WINDOW_OVERHEAD_CHARS,
    )
    windows: list[dict[str, int]] = []
    current: list[dict[str, int]] = []
    current_chars = 0
    for item in chapter_sizes:
        item_chars = int(item["estimated_material_chars"])
        if item_chars > material_budget:
            raise ValueError(
                "OPENING_HOOK_PAYOFFS_SINGLE_CHAPTER_TOO_LARGE:"
                f"{item['chapter_ordinal']}:{item_chars}:{material_budget}"
            )
        if current and current_chars + item_chars > material_budget:
            windows.append({
                "chapter_start": int(current[0]["chapter_ordinal"]),
                "chapter_end": int(current[-1]["chapter_ordinal"]),
                "estimated_material_chars": current_chars,
            })
            current = []
            current_chars = 0
        current.append(item)
        current_chars += item_chars
    if current:
        windows.append({
            "chapter_start": int(current[0]["chapter_ordinal"]),
            "chapter_end": int(current[-1]["chapter_ordinal"]),
            "estimated_material_chars": current_chars,
        })
    return windows


def provider_payload_for_opening_hook_payoffs(
    session: Session,
    settings: Settings,
    task_payload: dict,
) -> dict:
    run_id = str(task_payload.get("run_id") or "")
    run = session.get(AnalysisRun, run_id)
    version = session.get(SourceVersion, task_payload.get("source_version_id"))
    if run is None or version is None:
        raise ValueError("ANALYSIS_RUN_NOT_FOUND")
    projection, units, hook_evidence = _current_context(session, run_id)
    if not _sources_ready(projection, units, hook_evidence):
        raise ValueError("OPENING_HOOK_PAYOFFS_SOURCE_NOT_READY")
    assert hook_evidence is not None
    fingerprint = opening_hook_payoffs_source_fingerprint(
        projection,
        units,
        hook_evidence,
    )
    if fingerprint != task_payload.get("source_fingerprint"):
        raise ValueError("OPENING_HOOK_PAYOFFS_SOURCE_OUTDATED")
    _service, profile = resolve_analysis_profile(
        settings,
        str(task_payload.get("model_profile_id") or ENTITIES_EVENTS_PROFILE_ID),
    )
    chapter_start = int(task_payload.get("chapter_start") or 0)
    chapter_end = int(task_payload.get("chapter_end") or 0)
    if (
        chapter_start < 2
        or chapter_end < chapter_start
        or chapter_end > len(units)
    ):
        raise ValueError("OPENING_HOOK_PAYOFFS_WINDOW_INVALID")
    chapters, _evidence_by_id, ignored_total = _chapter_response_records(
        session,
        units,
        chapter_start=chapter_start,
        chapter_end=chapter_end,
    )
    input_payload = {
        "question_id": OPENING_HOOK_PAYOFFS_QUESTION_ID,
        "contract": {
            "output": "前三章章末钩清单：类型、指向问题、首次回应位置和赊账距离",
            "evidence": "钩子使用 4.9 已核验章末原文；回应必须引用本窗口真实后续原文",
            "scope": "连续窗口覆盖第 2 章至全书结尾，全部完成后由程序合并最早回应",
        },
        "source_chapter_count": len(units),
        "coverage_policy": "ALL_LATER_CHAPTERS_WINDOWED",
        "window": {
            "group_id": task_payload.get("window_group_id"),
            "index": task_payload.get("window_index"),
            "count": task_payload.get("window_count"),
            "chapter_start": chapter_start,
            "chapter_end": chapter_end,
        },
        "opening_hooks": _opening_hooks(hook_evidence),
        "response_search_chapters": chapters,
    }
    request_budget = _request_budget(profile)
    input_budget = int(request_budget["input_char_budget"])
    input_text = json.dumps(
        input_payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(input_text) > input_budget:
        raise ValueError(
            "OPENING_HOOK_PAYOFFS_CONTEXT_TOO_LARGE:"
            f"{len(input_text)}:{input_budget}"
        )
    return {
        "instructions": _prompt(),
        "input": input_text,
        "output_schema": _inline_model_schema(OpeningHookPayoffsOutput),
        "model_profile_id": str(
            task_payload.get("model_profile_id") or ENTITIES_EVENTS_PROFILE_ID
        ),
        "prompt_id": OPENING_HOOK_PAYOFFS_PROMPT_ID,
        "prompt_version": OPENING_HOOK_PAYOFFS_PROMPT_VERSION,
        "source_version_id": version.id,
        "source_char_start": 0,
        "source_char_end": version.total_chars,
        "context_manifest": {
            "source_chapter_count": len(units),
            "window_group_id": task_payload.get("window_group_id"),
            "window_index": task_payload.get("window_index"),
            "window_count": task_payload.get("window_count"),
            "chapter_start": chapter_start,
            "chapter_end": chapter_end,
            "coverage_policy": input_payload["coverage_policy"],
            "response_paragraph_count": sum(
                len(item["paragraphs"]) for item in chapters
            ),
            "ignored_heading_or_boilerplate_count": ignored_total,
            "input_chars": len(input_text),
            "input_budget_chars": input_budget,
            **request_budget,
        },
    }


def _validate_window_output(
    output: OpeningHookPayoffsOutput,
    hooks: list[dict[str, object]],
    evidence_by_id: dict[str, EvidenceSpan],
    unit_by_id: dict[str, int],
) -> list[dict[str, object]]:
    hooks_by_chapter = {
        int(item["chapter_ordinal"]): item for item in hooks
    }
    results_by_chapter = {
        item.hook_chapter: item for item in output.results
    }
    if set(results_by_chapter) != set(hooks_by_chapter):
        raise ValueError("OPENING_HOOK_PAYOFFS_HOOK_COVERAGE_INVALID")
    completed: list[dict[str, object]] = []
    for hook_chapter in sorted(hooks_by_chapter):
        hook = hooks_by_chapter[hook_chapter]
        proposal = results_by_chapter[hook_chapter]
        no_hook = hook.get("hook_type") == "NONE"
        if no_hook != (proposal.result == "NO_HOOK"):
            raise ValueError("OPENING_HOOK_PAYOFFS_NO_HOOK_STATUS_INVALID")
        response_chapter: int | None = None
        response_start_char: int | None = None
        if proposal.result in {"FOUND_COMPLETE", "FOUND_PARTIAL"}:
            evidence = evidence_by_id.get(str(proposal.response_evidence_id))
            if evidence is None:
                raise ValueError(
                    "OPENING_HOOK_PAYOFFS_RESPONSE_EVIDENCE_INVALID"
                )
            response_chapter = unit_by_id.get(evidence.source_unit_id)
            if response_chapter is None or response_chapter <= hook_chapter:
                raise ValueError(
                    "OPENING_HOOK_PAYOFFS_RESPONSE_POSITION_INVALID"
                )
            response_start_char = evidence.start_char
        completed.append({
            **proposal.model_dump(mode="json"),
            "response_chapter_ordinal": response_chapter,
            "response_start_char": response_start_char,
        })
    return completed


def _summary(records: list[dict[str, object]]) -> dict[str, object]:
    distances = [
        int(item["response_distance_chapters"])
        for item in records
        if item.get("response_distance_chapters") is not None
    ]
    return {
        "source_hook_count": len(records),
        "complete_response_count": sum(
            item.get("response_status") == "COMPLETE" for item in records
        ),
        "partial_response_count": sum(
            item.get("response_status") == "PARTIAL" for item in records
        ),
        "unresolved_count": sum(
            item.get("response_status") == "UNRESOLVED" for item in records
        ),
        "not_applicable_count": sum(
            item.get("response_status") == "NOT_APPLICABLE"
            for item in records
        ),
        "average_response_distance_chapters": (
            round(sum(distances) / len(distances), 2) if distances else None
        ),
        "maximum_response_distance_chapters": max(distances) if distances else None,
    }


def _finalize_window_group(
    session: Session,
    *,
    run: AnalysisRun,
    units: list[SourceUnit],
    hook_evidence: dict,
    fingerprint: str,
    window_group_id: str,
    window_count: int,
) -> LearningQuestionEvidence | None:
    latest_final = session.scalar(
        select(LearningQuestionEvidence)
        .where(
            LearningQuestionEvidence.run_id == run.id,
            LearningQuestionEvidence.question_id
            == OPENING_HOOK_PAYOFFS_QUESTION_ID,
        )
        .order_by(LearningQuestionEvidence.revision_no.desc())
    )
    if latest_final is not None:
        latest_payload = json.loads(latest_final.payload_json)
        if latest_payload.get("window_group_id") == window_group_id:
            return latest_final
    window_prefix = f"3.4w-{window_group_id}-"
    window_ledgers = list(session.scalars(
        select(LearningQuestionEvidence)
        .where(
            LearningQuestionEvidence.run_id == run.id,
            LearningQuestionEvidence.source_fingerprint == fingerprint,
            LearningQuestionEvidence.prompt_version
            == OPENING_HOOK_PAYOFFS_PROMPT_VERSION,
            LearningQuestionEvidence.question_id.like(f"{window_prefix}%"),
        )
        .order_by(LearningQuestionEvidence.question_id)
    ))
    if len(window_ledgers) > window_count:
        raise ValueError("OPENING_HOOK_PAYOFFS_WINDOW_COUNT_INVALID")
    window_payloads = [json.loads(item.payload_json) for item in window_ledgers]
    payload_by_index = {
        int(item.get("window_index") or 0): item
        for item in window_payloads
    }
    if (
        len(payload_by_index) != len(window_payloads)
        or any(index < 1 or index > window_count for index in payload_by_index)
    ):
        raise ValueError("OPENING_HOOK_PAYOFFS_WINDOW_COVERAGE_INVALID")
    contiguous_count = 0
    while contiguous_count + 1 in payload_by_index:
        contiguous_count += 1
    contiguous_payloads = [
        payload_by_index[index]
        for index in range(1, contiguous_count + 1)
    ]
    hooks = _opening_hooks(hook_evidence)
    all_required_hooks_resolved = all(
        hook.get("hook_type") == "NONE"
        or any(
            int(result.get("hook_chapter") or 0)
            == int(hook["chapter_ordinal"])
            and result.get("result")
            in {"FOUND_COMPLETE", "FOUND_PARTIAL"}
            for payload in contiguous_payloads
            for result in payload.get("results", [])
        )
        for hook in hooks
    )
    all_windows_completed = contiguous_count == window_count
    if not all_required_hooks_resolved and not all_windows_completed:
        return None

    hook_results: list[dict[str, object]] = []
    ending_by_id = {
        span.id: span
        for span in session.scalars(
            select(EvidenceSpan).where(
                EvidenceSpan.id.in_({
                    str(item["ending_evidence_id"]) for item in hooks
                })
            )
        )
    }
    for hook in hooks:
        hook_chapter = int(hook["chapter_ordinal"])
        if hook.get("hook_type") == "NONE":
            chosen = None
            response_status = "NOT_APPLICABLE"
        else:
            candidates = [
                result
                for payload in contiguous_payloads
                for result in payload.get("results", [])
                if (
                    int(result.get("hook_chapter") or 0) == hook_chapter
                    and result.get("result")
                    in {"FOUND_COMPLETE", "FOUND_PARTIAL"}
                )
            ]
            candidates.sort(key=lambda item: (
                int(item.get("response_chapter_ordinal") or 10**9),
                int(item.get("response_start_char") or 10**18),
            ))
            chosen = candidates[0] if candidates else None
            response_status = (
                "COMPLETE"
                if chosen and chosen.get("result") == "FOUND_COMPLETE"
                else "PARTIAL"
                if chosen
                else "UNRESOLVED"
            )
        ending = ending_by_id.get(str(hook["ending_evidence_id"]))
        response_evidence_id = (
            str(chosen.get("response_evidence_id")) if chosen else None
        )
        response_evidence = (
            session.get(EvidenceSpan, response_evidence_id)
            if response_evidence_id
            else None
        )
        response_chapter = (
            int(chosen["response_chapter_ordinal"]) if chosen else None
        )
        hook_results.append({
            **hook,
            "response_status": response_status,
            "response_evidence_id": response_evidence_id,
            "response_evidence_ids": (
                [response_evidence_id] if response_evidence_id else []
            ),
            "response_summary": (
                str(chosen.get("response_summary") or "") if chosen else ""
            ),
            "response_rationale": (
                str(chosen.get("rationale") or "") if chosen else ""
            ),
            "response_chapter_ordinal": response_chapter,
            "response_distance_chapters": (
                response_chapter - hook_chapter
                if response_chapter is not None
                else None
            ),
            "response_distance_chars": (
                max(0, response_evidence.start_char - ending.end_char)
                if response_evidence is not None and ending is not None
                else None
            ),
        })
    payload = {
        "window_group_id": window_group_id,
        "hooks": hook_results,
        "summary": _summary(hook_results),
        "coverage": {
            "source_chapter_count": len(units),
            "required_hook_count": 3,
            "tracked_hook_count": len(hook_results),
            "search_chapter_start": 2,
            "search_chapter_end": int(
                contiguous_payloads[-1]["chapter_end"]
            ),
            "search_policy": "ALL_LATER_CHAPTERS_WINDOWED",
            "window_count": contiguous_count,
            "planned_window_count": window_count,
            "completed_window_count": contiguous_count,
            "all_windows_completed": all_windows_completed,
            "all_required_hooks_resolved": all_required_hooks_resolved,
            "ending_evidence_complete": len(ending_by_id) == len(hooks),
            "response_position_program_validated": True,
        },
    }
    finalizer = next(
        ledger
        for ledger in window_ledgers
        if int(json.loads(ledger.payload_json).get("window_index") or 0)
        == contiguous_count
    )
    finalizer.question_id = OPENING_HOOK_PAYOFFS_QUESTION_ID
    finalizer.revision_no = int(session.scalar(
        select(func.max(LearningQuestionEvidence.revision_no)).where(
            LearningQuestionEvidence.run_id == run.id,
            LearningQuestionEvidence.question_id
            == OPENING_HOOK_PAYOFFS_QUESTION_ID,
        )
    ) or 0) + 1
    finalizer.payload_json = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
    )
    session.commit()
    session.refresh(finalizer)
    if all_required_hooks_resolved and not all_windows_completed:
        remaining_tasks = list(session.scalars(
            select(Task)
            .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
            .where(
                AnalysisRunTask.run_id == run.id,
                Task.kind == OPENING_HOOK_PAYOFFS_TASK_KIND,
                Task.status.in_((
                    TaskStatus.PENDING.value,
                    TaskStatus.RETRY_WAIT.value,
                    TaskStatus.WAITING_CONFIRMATION.value,
                )),
            )
        ))
        for remaining_task in remaining_tasks:
            remaining_payload = json.loads(remaining_task.payload_json)
            if (
                remaining_payload.get("window_group_id") == window_group_id
                and int(remaining_payload.get("window_index") or 0)
                > contiguous_count
            ):
                request_task_cancellation(
                    session,
                    task_id=remaining_task.id,
                )
    return finalizer


def persist_opening_hook_payoffs(
    session: Session,
    *,
    task: Task,
    attempt_id: str,
    task_payload: dict,
    output: OpeningHookPayoffsOutput,
) -> LearningQuestionEvidence:
    existing = session.scalar(
        select(LearningQuestionEvidence).where(
            LearningQuestionEvidence.created_by_task_id == task.id
        )
    )
    if existing is not None:
        return existing
    run = session.get(AnalysisRun, task_payload.get("run_id"))
    version = session.get(SourceVersion, task_payload.get("source_version_id"))
    if run is None or version is None:
        raise ValueError("ANALYSIS_RUN_NOT_FOUND")
    projection, units, hook_evidence = _current_context(session, run.id)
    if not _sources_ready(projection, units, hook_evidence):
        raise ValueError("OPENING_HOOK_PAYOFFS_SOURCE_NOT_READY")
    assert hook_evidence is not None
    fingerprint = opening_hook_payoffs_source_fingerprint(
        projection,
        units,
        hook_evidence,
    )
    if fingerprint != task_payload.get("source_fingerprint"):
        raise ValueError("OPENING_HOOK_PAYOFFS_SOURCE_OUTDATED")
    window_group_id = str(task_payload.get("window_group_id") or "")
    window_index = int(task_payload.get("window_index") or 0)
    window_count = int(task_payload.get("window_count") or 0)
    chapter_start = int(task_payload.get("chapter_start") or 0)
    chapter_end = int(task_payload.get("chapter_end") or 0)
    if (
        not re.fullmatch(r"[a-f0-9]{8}", window_group_id)
        or window_index < 1
        or window_count < 1
        or window_index > window_count
        or chapter_start < 2
        or chapter_end < chapter_start
        or chapter_end > len(units)
    ):
        raise ValueError("OPENING_HOOK_PAYOFFS_WINDOW_INVALID")
    _chapters, evidence_by_id, ignored_total = _chapter_response_records(
        session,
        units,
        chapter_start=chapter_start,
        chapter_end=chapter_end,
    )
    unit_by_id = {
        unit.id: chapter_ordinal
        for chapter_ordinal, unit in enumerate(units, start=1)
    }
    results = _validate_window_output(
        output,
        _opening_hooks(hook_evidence),
        evidence_by_id,
        unit_by_id,
    )
    payload = {
        "window_group_id": window_group_id,
        "window_index": window_index,
        "window_count": window_count,
        "chapter_start": chapter_start,
        "chapter_end": chapter_end,
        "results": results,
        "ignored_heading_or_boilerplate_count": ignored_total,
    }
    ledger = LearningQuestionEvidence(
        run_id=run.id,
        source_version_id=version.id,
        question_id=f"3.4w-{window_group_id}-{window_index:03d}",
        revision_no=1,
        source_fingerprint=fingerprint,
        payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        prompt_id=OPENING_HOOK_PAYOFFS_PROMPT_ID,
        prompt_version=OPENING_HOOK_PAYOFFS_PROMPT_VERSION,
        created_by_task_id=task.id,
        created_by_attempt_id=attempt_id,
    )
    session.add(ledger)
    session.commit()
    session.refresh(ledger)
    final_ledger = _finalize_window_group(
        session,
        run=run,
        units=units,
        hook_evidence=hook_evidence,
        fingerprint=fingerprint,
        window_group_id=window_group_id,
        window_count=window_count,
    )
    return final_ledger or ledger


def build_opening_hook_payoffs_projection(
    session: Session,
    run_id: str,
    projection: dict,
    units: list[SourceUnit] | None = None,
    hook_evidence: dict | None = None,
) -> tuple[str, dict | None]:
    ledger = session.scalar(
        select(LearningQuestionEvidence)
        .where(
            LearningQuestionEvidence.run_id == run_id,
            LearningQuestionEvidence.question_id
            == OPENING_HOOK_PAYOFFS_QUESTION_ID,
        )
        .order_by(LearningQuestionEvidence.revision_no.desc())
    )
    latest_task = session.scalar(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run_id,
            Task.kind == OPENING_HOOK_PAYOFFS_TASK_KIND,
        )
        .order_by(AnalysisRunTask.batch_index.desc())
    )
    active_task = session.scalar(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run_id,
            Task.kind == OPENING_HOOK_PAYOFFS_TASK_KIND,
            Task.status.in_((
                TaskStatus.PENDING.value,
                TaskStatus.RUNNING.value,
                TaskStatus.RETRY_WAIT.value,
                TaskStatus.WAITING_CONFIRMATION.value,
            )),
        )
        .order_by(AnalysisRunTask.batch_index)
    )
    run = session.get(AnalysisRun, run_id)
    resolved_units = units or (
        _chapter_units(session, run.source_version_id) if run is not None else []
    )
    resolved_hook_evidence = hook_evidence
    if resolved_hook_evidence is None and run is not None:
        hook_status, candidate = build_chapter_end_hooks_projection(
            session,
            run_id,
            projection,
        )
        if hook_status == "READY":
            resolved_hook_evidence = candidate
    current_fingerprint = (
        opening_hook_payoffs_source_fingerprint(
            projection,
            resolved_units,
            resolved_hook_evidence,
        )
        if resolved_hook_evidence
        else None
    )
    is_current = bool(
        ledger is not None
        and current_fingerprint is not None
        and ledger.source_fingerprint == current_fingerprint
        and ledger.prompt_version == OPENING_HOOK_PAYOFFS_PROMPT_VERSION
    )
    if active_task is not None:
        status = "GENERATING"
    elif is_current:
        status = "READY"
    elif ledger is not None:
        status = "OUTDATED"
    elif latest_task is not None and latest_task.status == TaskStatus.FAILED.value:
        status = "FAILED"
    else:
        status = "NOT_GENERATED"
    if ledger is None:
        return status, None
    payload = json.loads(ledger.payload_json)
    payload.update({
        "question_id": ledger.question_id,
        "revision": ledger.revision_no,
        "generated_at": ledger.created_at,
        "is_current": is_current,
        "prompt_id": ledger.prompt_id,
        "prompt_version": ledger.prompt_version,
    })
    return status, payload


def enqueue_opening_hook_payoffs(
    session: Session,
    settings: Settings,
    run: AnalysisRun,
    *,
    force: bool = False,
) -> Task | None:
    projection, units, hook_evidence = _current_context(session, run.id)
    if not _sources_ready(projection, units, hook_evidence):
        return None
    assert hook_evidence is not None
    fingerprint = opening_hook_payoffs_source_fingerprint(
        projection,
        units,
        hook_evidence,
    )
    active_task = session.scalar(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run.id,
            Task.kind == OPENING_HOOK_PAYOFFS_TASK_KIND,
            Task.status.in_((
                TaskStatus.PENDING.value,
                TaskStatus.RUNNING.value,
                TaskStatus.RETRY_WAIT.value,
                TaskStatus.WAITING_CONFIRMATION.value,
            )),
        )
        .order_by(AnalysisRunTask.batch_index)
    )
    if active_task is not None:
        return active_task
    latest_ledger = session.scalar(
        select(LearningQuestionEvidence)
        .where(
            LearningQuestionEvidence.run_id == run.id,
            LearningQuestionEvidence.question_id
            == OPENING_HOOK_PAYOFFS_QUESTION_ID,
        )
        .order_by(LearningQuestionEvidence.revision_no.desc())
    )
    if (
        latest_ledger is not None
        and latest_ledger.source_fingerprint == fingerprint
        and latest_ledger.prompt_version == OPENING_HOOK_PAYOFFS_PROMPT_VERSION
        and not force
    ):
        return None
    try:
        _service, profile = resolve_analysis_profile(
            settings,
            ENTITIES_EVENTS_PROFILE_ID,
        )
    except ModelSettingsError:
        return None
    request_budget = _request_budget(profile)
    chapter_sizes = _all_response_chapter_sizes(session, units)
    windows = _window_specs(
        chapter_sizes,
        input_char_budget=int(request_budget["input_char_budget"]),
    )
    if not windows:
        return None
    window_group_id = uuid.uuid4().hex[:8]
    next_index = max(
        (link.batch_index for link in run.task_links),
        default=run.total_batches,
    ) + 1
    created_tasks: list[Task] = []
    for offset, window in enumerate(windows):
        task_payload, max_attempts = prepare_task_provider_routes(
            settings,
            {
                "run_id": run.id,
                "source_version_id": run.source_version_id,
                "source_fingerprint": fingerprint,
                "question_id": OPENING_HOOK_PAYOFFS_QUESTION_ID,
                "provider_name": "openai",
                "model_profile_id": profile.id,
                "window_group_id": window_group_id,
                "window_index": offset + 1,
                "window_count": len(windows),
                "chapter_start": window["chapter_start"],
                "chapter_end": window["chapter_end"],
                "estimated_material_chars": window["estimated_material_chars"],
                "analysis_policy": "ALL_LATER_CHAPTERS_WINDOWED",
                "task_input_budget": request_budget,
            },
            profile.max_retries + 1,
        )
        task = Task(
            project_id=run.source_version.document.project_id,
            kind=OPENING_HOOK_PAYOFFS_TASK_KIND,
            payload_json=json.dumps(
                task_payload,
                ensure_ascii=False,
                sort_keys=True,
            ),
            max_attempts=max_attempts,
        )
        session.add(task)
        session.flush()
        session.add(AnalysisRunTask(
            run_id=run.id,
            task_id=task.id,
            batch_index=next_index + offset,
        ))
        created_tasks.append(task)
    run.total_batches = next_index + len(created_tasks) - 1
    run.status = AnalysisRunStatus.PENDING.value
    session.commit()
    session.refresh(created_tasks[0])
    return created_tasks[0]
