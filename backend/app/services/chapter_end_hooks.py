from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import Counter, defaultdict
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
from .provider_config import (
    ENTITIES_EVENTS_PROFILE_ID,
    ModelSettingsError,
    prepare_task_provider_routes,
    resolve_analysis_profile,
)


CHAPTER_END_HOOKS_TASK_KIND = "analysis.chapter_end_hooks"
CHAPTER_END_HOOKS_QUESTION_ID = "4.9"
CHAPTER_END_HOOKS_PROMPT_ID = "chapter_end_hooks"
CHAPTER_END_HOOKS_PROMPT_VERSION = "2.0.0"
CHAPTER_END_HOOK_TARGET_INPUT_TOKENS = 24_000
CHAPTER_END_HOOK_SOFT_INPUT_TOKENS = 32_000
CHAPTER_END_HOOK_ESTIMATED_CHARS_PER_TOKEN = 2
CHAPTER_END_HOOK_WINDOW_OVERHEAD_CHARS = 12_000
CHAPTER_END_HOOK_MAX_WINDOW_CHAPTERS = 80

HOOK_TYPES = (
    "CRISIS_SUSPENSION",
    "NEW_INFORMATION",
    "PAYOFF_PRIMING",
    "REVERSAL",
    "EMOTIONAL_FREEZE",
    "NONE",
)

_TRAILING_BOILERPLATE = re.compile(
    r"(?:https?://|www\.|\.com\b|\.net\b|"
    r"更多精彩|更多好书|请看小说网|txt\d*\.com|"
    r"声明[：:]?本书|本站只提供|用户上传|免费下载服务|版权.*无任何关系)",
    re.IGNORECASE,
)


class ChapterEndHookProposal(BaseModel):
    chapter_ordinal: int = Field(ge=1)
    ending_evidence_id: str = Field(min_length=1, max_length=64)
    hook_type: Literal[
        "CRISIS_SUSPENSION",
        "NEW_INFORMATION",
        "PAYOFF_PRIMING",
        "REVERSAL",
        "EMOTIONAL_FREEZE",
        "NONE",
    ]
    strength: Literal["STRONG", "MEDIUM", "LIGHT", "NONE"]
    hook_question: str = Field(default="", max_length=1000)
    rationale: str = Field(min_length=1, max_length=1600)
    retention_basis: str = Field(default="", max_length=1200)
    response_status: Literal["NOT_APPLICABLE"] = "NOT_APPLICABLE"
    response_evidence_id: str | None = Field(default=None, max_length=64)
    response_summary: str = Field(default="", max_length=1200)

    @model_validator(mode="after")
    def require_consistent_hook_and_response(self) -> "ChapterEndHookProposal":
        if self.hook_type == "NONE":
            if self.strength != "NONE":
                raise ValueError("无钩章节的强度必须为 NONE")
            if self.hook_question.strip():
                raise ValueError("无钩章节不能虚构追读问题")
            if not self.retention_basis.strip():
                raise ValueError("无钩章节必须说明靠什么维持阅读或为何属于非叙事内容")
        elif self.strength == "NONE":
            raise ValueError("有钩章节必须标注实际强度")
        elif not self.hook_question.strip():
            raise ValueError("有钩章节必须写出章末形成的具体追读问题")
        if self.response_evidence_id or self.response_summary.strip():
            raise ValueError("4.9 不追踪逐章回应，不能挂接回应证据")
        return self


class ChapterEndHooksOutput(BaseModel):
    chapters: list[ChapterEndHookProposal] = Field(
        min_length=1,
        max_length=CHAPTER_END_HOOK_MAX_WINDOW_CHAPTERS,
    )


class ChapterEndHooksValidationError(ValueError):
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


def parse_chapter_end_hooks(value: dict) -> ChapterEndHooksOutput:
    try:
        output = ChapterEndHooksOutput.model_validate(value)
    except ValidationError as exc:
        raise ChapterEndHooksValidationError(
            "CHAPTER_END_HOOKS_OUTPUT_INVALID",
            _validation_errors(exc),
        ) from exc
    ordinals = [item.chapter_ordinal for item in output.chapters]
    if len(set(ordinals)) != len(ordinals):
        raise ChapterEndHooksValidationError(
            "CHAPTER_END_HOOKS_CHAPTER_DUPLICATED",
            [{
                "path": ["chapters"],
                "type": "value_error",
                "message": "每个抽样章节只能出现一次",
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
    path = Path(__file__).resolve().parents[3] / "prompts" / "chapter_end_hooks_v1.md"
    return path.read_text(encoding="utf-8").strip()


def _request_budget(profile: Any) -> dict[str, int | str]:
    output_reserve = max(1, int(getattr(profile, "max_output_tokens", 16_000)))
    context_window = getattr(profile, "context_window_tokens", None)
    if context_window is not None:
        available_tokens = max(
            8_000,
            int(context_window) - output_reserve - 4_096,
        )
        input_tokens = min(CHAPTER_END_HOOK_SOFT_INPUT_TOKENS, available_tokens)
        source = "MODEL_CONTEXT_WITH_TASK_SOFT_CAP"
    else:
        input_tokens = CHAPTER_END_HOOK_TARGET_INPUT_TOKENS
        source = "TASK_TARGET_WITHOUT_REPORTED_CONTEXT"
    return {
        "target_input_tokens": CHAPTER_END_HOOK_TARGET_INPUT_TOKENS,
        "soft_input_tokens": CHAPTER_END_HOOK_SOFT_INPUT_TOKENS,
        "input_token_budget": input_tokens,
        "input_char_budget": (
            input_tokens * CHAPTER_END_HOOK_ESTIMATED_CHARS_PER_TOKEN
        ),
        "estimated_chars_per_token": CHAPTER_END_HOOK_ESTIMATED_CHARS_PER_TOKEN,
        "budget_source": source,
    }


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
        r"\s+", "", unit.title
    ).casefold()


def _is_boilerplate(span: EvidenceSpan) -> bool:
    return bool(_TRAILING_BOILERPLATE.search(span.text_snapshot))


def _ending_evidence_catalog(
    session: Session,
    units: list[SourceUnit],
    sample_indexes: list[int],
) -> tuple[list[dict[str, object]], dict[int, EvidenceSpan], int]:
    selected: list[dict[str, object]] = []
    by_chapter: dict[int, EvidenceSpan] = {}
    ignored_total = 0
    for index in sample_indexes:
        unit = units[index]
        chapter_ordinal = index + 1
        spans = list(session.scalars(
            select(EvidenceSpan)
            .where(EvidenceSpan.source_unit_id == unit.id)
            .order_by(EvidenceSpan.paragraph_index, EvidenceSpan.start_char)
        ))
        body_spans = [span for span in spans if not _is_heading(span, unit)] or spans
        effective = next(
            (span for span in reversed(body_spans) if not _is_boilerplate(span)),
            body_spans[-1] if body_spans else None,
        )
        if effective is None:
            raise ValueError(f"CHAPTER_END_EVIDENCE_MISSING:{chapter_ordinal}")
        ignored = sum(
            1
            for span in body_spans
            if span.start_char > effective.start_char and _is_boilerplate(span)
        )
        ignored_total += ignored
        by_chapter[chapter_ordinal] = effective
        text = effective.text_snapshot
        selected.append({
            "chapter_ordinal": chapter_ordinal,
            "chapter_title": unit.title,
            "ending_evidence_id": effective.id,
            "ending_text": text[-1600:],
            "ending_text_truncated": len(text) > 1600,
            "ignored_trailing_boilerplate_count": ignored,
        })
    return selected, by_chapter, ignored_total


def _window_specs(
    endings: list[dict[str, object]],
    *,
    input_char_budget: int,
) -> list[dict[str, int]]:
    material_budget = max(
        8_000,
        input_char_budget - CHAPTER_END_HOOK_WINDOW_OVERHEAD_CHARS,
    )
    windows: list[dict[str, int]] = []
    current: list[dict[str, object]] = []
    current_chars = 0
    for item in endings:
        item_chars = len(json.dumps(
            item,
            ensure_ascii=False,
            separators=(",", ":"),
        )) + 1
        if current and (
            current_chars + item_chars > material_budget
            or len(current) >= CHAPTER_END_HOOK_MAX_WINDOW_CHAPTERS
        ):
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


def chapter_end_hooks_source_fingerprint(
    projection: dict,
    units: list[SourceUnit],
) -> str:
    payload = {
        "source_version_id": projection.get("source_version_id"),
        "deep_revision": projection.get("deep_revision"),
        "chapters": [
            {
                "id": unit.id,
                "ordinal": unit.ordinal,
                "content_hash": unit.content_hash,
            }
            for unit in units
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _chapter_end_sources_ready(projection: dict, units: list[SourceUnit]) -> bool:
    return bool(
        units
        and projection.get("narrative_status") in {"READY", "INCOMPLETE"}
        and projection.get("deep_status") == "READY"
        and projection.get("phases")
    )


def provider_payload_for_chapter_end_hooks(
    session: Session,
    settings: Settings,
    task_payload: dict,
) -> dict:
    run_id = str(task_payload.get("run_id") or "")
    run = session.get(AnalysisRun, run_id)
    version = session.get(SourceVersion, task_payload.get("source_version_id"))
    if run is None or version is None:
        raise ValueError("ANALYSIS_RUN_NOT_FOUND")
    projection = _base_projection(session, run_id)
    units = _chapter_units(session, version.id)
    if not _chapter_end_sources_ready(projection, units):
        raise ValueError("CHAPTER_END_HOOKS_SOURCE_NOT_READY")
    fingerprint = chapter_end_hooks_source_fingerprint(projection, units)
    if fingerprint != task_payload.get("source_fingerprint"):
        raise ValueError("CHAPTER_END_HOOKS_SOURCE_OUTDATED")

    _service, profile = resolve_analysis_profile(
        settings,
        str(task_payload.get("model_profile_id") or ENTITIES_EVENTS_PROFILE_ID),
    )
    chapter_start = int(task_payload.get("chapter_start") or 0)
    chapter_end = int(task_payload.get("chapter_end") or 0)
    if (
        chapter_start < 1
        or chapter_end < chapter_start
        or chapter_end > len(units)
    ):
        raise ValueError("CHAPTER_END_HOOKS_WINDOW_INVALID")
    window_indexes = list(range(chapter_start - 1, chapter_end))
    endings, _ending_by_chapter, ignored_total = _ending_evidence_catalog(
        session, units, window_indexes
    )
    input_payload = {
        "question_id": CHAPTER_END_HOOKS_QUESTION_ID,
        "contract": {
            "types": list(HOOK_TYPES),
            "measurement": "逐章类型、强弱、追读问题和无钩维持依据；全书统计由程序在全部连续窗口完成后合并",
            "evidence": "每章只能使用程序指定的真实章末原文；4.9 不判断后续回应",
            "scope": "正式模式按连续窗口覆盖全书全部章节，本请求只负责一个连续窗口",
        },
        "source_chapter_count": len(units),
        "coverage_policy": "ALL_CHAPTERS_WINDOWED",
        "window": {
            "group_id": task_payload.get("window_group_id"),
            "index": task_payload.get("window_index"),
            "count": task_payload.get("window_count"),
            "chapter_start": chapter_start,
            "chapter_end": chapter_end,
        },
        "chapter_endings": endings,
        "narrative_phases": [
            {
                key: phase.get(key)
                for key in (
                    "id",
                    "title",
                    "chapter_ordinals",
                    "situation",
                    "goal",
                    "outcome",
                    "change",
                )
            }
            for phase in projection.get("phases", [])
            if any(
                chapter_start <= int(ordinal) <= chapter_end
                for ordinal in phase.get("chapter_ordinals", [])
            )
        ],
    }
    request_budget = _request_budget(profile)
    input_budget = int(request_budget["input_char_budget"])
    input_text = json.dumps(input_payload, ensure_ascii=False, separators=(",", ":"))
    if len(input_text) > input_budget:
        raise ValueError(
            f"CHAPTER_END_HOOKS_CONTEXT_TOO_LARGE:{len(input_text)}:{input_budget}"
        )
    return {
        "instructions": _prompt(),
        "input": input_text,
        "output_schema": _inline_model_schema(ChapterEndHooksOutput),
        "model_profile_id": str(
            task_payload.get("model_profile_id") or ENTITIES_EVENTS_PROFILE_ID
        ),
        "prompt_id": CHAPTER_END_HOOKS_PROMPT_ID,
        "prompt_version": CHAPTER_END_HOOKS_PROMPT_VERSION,
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
            "window_chapter_count": len(endings),
            "coverage_policy": input_payload["coverage_policy"],
            "ending_evidence_count": len(endings),
            "ignored_trailing_boilerplate_count": ignored_total,
            "input_chars": len(input_text),
            "input_budget_chars": input_budget,
            **request_budget,
        },
    }


def _raise_for_references(
    output: ChapterEndHooksOutput,
    ending_by_chapter: dict[int, EvidenceSpan],
) -> None:
    if set(item.chapter_ordinal for item in output.chapters) != set(ending_by_chapter):
        raise ValueError("CHAPTER_END_HOOKS_CHAPTER_COVERAGE_INVALID")
    for item in output.chapters:
        ending = ending_by_chapter[item.chapter_ordinal]
        if item.ending_evidence_id != ending.id:
            raise ValueError("CHAPTER_END_HOOKS_ENDING_REFERENCE_INVALID")


def _phase_for_chapter(projection: dict, chapter_ordinal: int) -> dict | None:
    return next(
        (
            phase
            for phase in projection.get("phases", [])
            if chapter_ordinal in {
                int(item) for item in phase.get("chapter_ordinals", [])
            }
        ),
        None,
    )


def _max_streak(
    chapters: list[dict[str, object]],
    predicate: Any,
) -> int:
    longest = 0
    current = 0
    previous_ordinal: int | None = None
    for item in chapters:
        ordinal = int(item["chapter_ordinal"])
        if previous_ordinal is None or ordinal != previous_ordinal + 1 or not predicate(item):
            current = 1 if predicate(item) else 0
        else:
            current += 1
        longest = max(longest, current)
        previous_ordinal = ordinal
    return longest


def _summary(chapters: list[dict[str, object]]) -> dict[str, object]:
    ordered = sorted(chapters, key=lambda item: int(item["chapter_ordinal"]))
    total = len(ordered)
    type_counts = Counter(str(item["hook_type"]) for item in ordered)
    strength_counts = Counter(str(item["strength"]) for item in ordered)
    phase_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for item in ordered:
        phase_counts[str(item.get("phase_title") or "未归入剧情阶段")][
            str(item["hook_type"])
        ] += 1
    examples = {
        hook_type: [
            {
                "chapter_ordinal": item["chapter_ordinal"],
                "chapter_title": item["chapter_title"],
                "ending_evidence_ids": item["ending_evidence_ids"],
            }
            for item in ordered
            if item["hook_type"] == hook_type
        ][:3]
        for hook_type in HOOK_TYPES
    }
    return {
        "type_distribution": [
            {
                "hook_type": hook_type,
                "count": type_counts[hook_type],
                "ratio": round(type_counts[hook_type] / total, 4),
            }
            for hook_type in HOOK_TYPES
        ],
        "strength_distribution": [
            {
                "strength": strength,
                "count": strength_counts[strength],
                "ratio": round(strength_counts[strength] / total, 4),
            }
            for strength in ("STRONG", "MEDIUM", "LIGHT", "NONE")
        ],
        "type_transition_count": sum(
            1
            for previous, current in zip(ordered, ordered[1:])
            if int(current["chapter_ordinal"]) == int(previous["chapter_ordinal"]) + 1
            and current["hook_type"] != previous["hook_type"]
        ),
        "max_consecutive_strong": _max_streak(
            ordered, lambda item: item["strength"] == "STRONG"
        ),
        "max_consecutive_same_type": max(
            (
                _max_streak(ordered, lambda item, value=hook_type: item["hook_type"] == value)
                for hook_type in HOOK_TYPES
            ),
            default=0,
        ),
        "no_hook_count": type_counts["NONE"],
        "no_hook_ratio": round(type_counts["NONE"] / total, 4),
        "phase_type_distribution": [
            {
                "phase_title": phase_title,
                "counts": dict(counts),
            }
            for phase_title, counts in phase_counts.items()
        ],
        "examples_by_type": examples,
    }


def _finalize_window_group(
    session: Session,
    *,
    run: AnalysisRun,
    units: list[SourceUnit],
    fingerprint: str,
    window_group_id: str,
    window_count: int,
) -> LearningQuestionEvidence | None:
    final_ledger = session.scalar(
        select(LearningQuestionEvidence)
        .where(
            LearningQuestionEvidence.run_id == run.id,
            LearningQuestionEvidence.question_id == CHAPTER_END_HOOKS_QUESTION_ID,
            LearningQuestionEvidence.source_fingerprint == fingerprint,
            LearningQuestionEvidence.prompt_version == CHAPTER_END_HOOKS_PROMPT_VERSION,
        )
        .order_by(LearningQuestionEvidence.revision_no.desc())
    )
    if final_ledger is not None:
        return final_ledger

    window_prefix = f"4.9w-{window_group_id}-"
    window_ledgers = list(session.scalars(
        select(LearningQuestionEvidence)
        .where(
            LearningQuestionEvidence.run_id == run.id,
            LearningQuestionEvidence.source_fingerprint == fingerprint,
            LearningQuestionEvidence.prompt_version == CHAPTER_END_HOOKS_PROMPT_VERSION,
            LearningQuestionEvidence.question_id.like(f"{window_prefix}%"),
        )
        .order_by(LearningQuestionEvidence.question_id)
    ))
    if len(window_ledgers) < window_count:
        return None
    if len(window_ledgers) != window_count:
        raise ValueError("CHAPTER_END_HOOKS_WINDOW_COUNT_INVALID")

    window_payloads = [json.loads(item.payload_json) for item in window_ledgers]
    if sorted(
        int(item.get("window_index") or 0) for item in window_payloads
    ) != list(range(1, window_count + 1)):
        raise ValueError("CHAPTER_END_HOOKS_WINDOW_COVERAGE_INVALID")
    merged_chapters = sorted(
        [
            chapter
            for payload in window_payloads
            for chapter in payload.get("chapters", [])
        ],
        key=lambda item: int(item["chapter_ordinal"]),
    )
    if [int(item["chapter_ordinal"]) for item in merged_chapters] != list(
        range(1, len(units) + 1)
    ):
        raise ValueError("CHAPTER_END_HOOKS_FULL_COVERAGE_INVALID")
    ignored_total = sum(
        int(item.get("ignored_trailing_boilerplate_count") or 0)
        for item in window_payloads
    )
    payload = {
        "chapters": merged_chapters,
        "summary": _summary(merged_chapters),
        "coverage": {
            "source_chapter_count": len(units),
            "required_sample_count": len(units),
            "sampled_chapter_count": len(merged_chapters),
            "sample_policy": "ALL_CHAPTERS_WINDOWED",
            "sampled_chapter_ordinals": [
                item["chapter_ordinal"] for item in merged_chapters
            ],
            "window_count": window_count,
            "completed_window_count": window_count,
            "ending_evidence_complete": True,
            "sequence_metrics_exact": True,
            "response_tracking_scope": "OUT_OF_SCOPE_FOR_4.9",
            "ignored_trailing_boilerplate_count": ignored_total,
        },
    }
    finalizer = next(
        ledger
        for ledger, window_payload in zip(window_ledgers, window_payloads)
        if int(window_payload.get("window_index") or 0) == window_count
    )
    finalizer.question_id = CHAPTER_END_HOOKS_QUESTION_ID
    finalizer.revision_no = int(session.scalar(
        select(func.max(LearningQuestionEvidence.revision_no)).where(
            LearningQuestionEvidence.run_id == run.id,
            LearningQuestionEvidence.question_id == CHAPTER_END_HOOKS_QUESTION_ID,
        )
    ) or 0) + 1
    finalizer.payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    session.commit()
    session.refresh(finalizer)
    return finalizer


def persist_chapter_end_hooks(
    session: Session,
    *,
    task: Task,
    attempt_id: str,
    task_payload: dict,
    output: ChapterEndHooksOutput,
) -> LearningQuestionEvidence:
    existing = session.scalar(
        select(LearningQuestionEvidence).where(
            LearningQuestionEvidence.created_by_task_id == task.id
        )
    )
    if (
        existing is not None
        and existing.question_id == CHAPTER_END_HOOKS_QUESTION_ID
    ):
        return existing
    run = session.get(AnalysisRun, task_payload.get("run_id"))
    version = session.get(SourceVersion, task_payload.get("source_version_id"))
    if run is None or version is None:
        raise ValueError("ANALYSIS_RUN_NOT_FOUND")
    projection = _base_projection(session, run.id)
    units = _chapter_units(session, version.id)
    fingerprint = chapter_end_hooks_source_fingerprint(projection, units)
    if fingerprint != task_payload.get("source_fingerprint"):
        raise ValueError("CHAPTER_END_HOOKS_SOURCE_OUTDATED")
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
        or chapter_start < 1
        or chapter_end < chapter_start
        or chapter_end > len(units)
    ):
        raise ValueError("CHAPTER_END_HOOKS_WINDOW_INVALID")
    window_indexes = list(range(chapter_start - 1, chapter_end))
    _ending_records, ending_by_chapter, ignored_total = _ending_evidence_catalog(
        session, units, window_indexes
    )
    _raise_for_references(output, ending_by_chapter)

    chapters: list[dict[str, object]] = []
    for proposal in sorted(output.chapters, key=lambda item: item.chapter_ordinal):
        item = proposal.model_dump(mode="json")
        phase = _phase_for_chapter(projection, proposal.chapter_ordinal)
        item.update({
            "chapter_title": units[proposal.chapter_ordinal - 1].title,
            "ending_evidence_ids": [proposal.ending_evidence_id],
            "response_evidence_ids": [],
            "response_chapter_ordinal": None,
            "response_distance": None,
            "phase_id": phase.get("id") if phase else None,
            "phase_title": phase.get("title") if phase else None,
        })
        chapters.append(item)

    if existing is None:
        payload = {
            "window_group_id": window_group_id,
            "window_index": window_index,
            "window_count": window_count,
            "chapter_start": chapter_start,
            "chapter_end": chapter_end,
            "chapters": chapters,
            "ignored_trailing_boilerplate_count": ignored_total,
        }
        question_id = f"4.9w-{window_group_id}-{window_index:03d}"
        ledger = LearningQuestionEvidence(
            run_id=run.id,
            source_version_id=version.id,
            question_id=question_id,
            revision_no=1,
            source_fingerprint=fingerprint,
            payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            prompt_id=CHAPTER_END_HOOKS_PROMPT_ID,
            prompt_version=CHAPTER_END_HOOKS_PROMPT_VERSION,
            created_by_task_id=task.id,
            created_by_attempt_id=attempt_id,
        )
        session.add(ledger)
        session.commit()
        session.refresh(ledger)
    else:
        ledger = existing
    final_ledger = _finalize_window_group(
        session,
        run=run,
        units=units,
        fingerprint=fingerprint,
        window_group_id=window_group_id,
        window_count=window_count,
    )
    return final_ledger or ledger


def build_chapter_end_hooks_projection(
    session: Session,
    run_id: str,
    projection: dict,
) -> tuple[str, dict | None]:
    ledger = session.scalar(
        select(LearningQuestionEvidence)
        .where(
            LearningQuestionEvidence.run_id == run_id,
            LearningQuestionEvidence.question_id == CHAPTER_END_HOOKS_QUESTION_ID,
        )
        .order_by(LearningQuestionEvidence.revision_no.desc())
    )
    latest_task = session.scalar(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run_id,
            Task.kind == CHAPTER_END_HOOKS_TASK_KIND,
        )
        .order_by(AnalysisRunTask.batch_index.desc())
    )
    active_task = session.scalar(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run_id,
            Task.kind == CHAPTER_END_HOOKS_TASK_KIND,
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
    units = _chapter_units(session, run.source_version_id) if run is not None else []
    current_fingerprint = chapter_end_hooks_source_fingerprint(projection, units)
    is_current = bool(
        ledger is not None
        and ledger.source_fingerprint == current_fingerprint
        and ledger.prompt_version == CHAPTER_END_HOOKS_PROMPT_VERSION
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


def enqueue_chapter_end_hooks(
    session: Session,
    settings: Settings,
    run: AnalysisRun,
    *,
    force: bool = False,
) -> Task | None:
    projection = _base_projection(session, run.id)
    units = _chapter_units(session, run.source_version_id)
    if not _chapter_end_sources_ready(projection, units):
        return None
    fingerprint = chapter_end_hooks_source_fingerprint(projection, units)
    active_task = session.scalar(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run.id,
            Task.kind == CHAPTER_END_HOOKS_TASK_KIND,
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
            LearningQuestionEvidence.question_id == CHAPTER_END_HOOKS_QUESTION_ID,
        )
        .order_by(LearningQuestionEvidence.revision_no.desc())
    )
    if (
        latest_ledger is not None
        and latest_ledger.source_fingerprint == fingerprint
        and latest_ledger.prompt_version == CHAPTER_END_HOOKS_PROMPT_VERSION
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
    endings, _ending_by_chapter, _ignored_total = _ending_evidence_catalog(
        session,
        units,
        list(range(len(units))),
    )
    request_budget = _request_budget(profile)
    windows = _window_specs(
        endings,
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
                "question_id": CHAPTER_END_HOOKS_QUESTION_ID,
                "provider_name": "openai",
                "model_profile_id": profile.id,
                "window_group_id": window_group_id,
                "window_index": offset + 1,
                "window_count": len(windows),
                "chapter_start": window["chapter_start"],
                "chapter_end": window["chapter_end"],
                "estimated_material_chars": window["estimated_material_chars"],
                "analysis_policy": "ALL_CHAPTERS_WINDOWED",
                "task_input_budget": request_budget,
            },
            profile.max_retries + 1,
        )
        task = Task(
            project_id=run.source_version.document.project_id,
            kind=CHAPTER_END_HOOKS_TASK_KIND,
            payload_json=json.dumps(task_payload, ensure_ascii=False, sort_keys=True),
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
