from __future__ import annotations

import hashlib
import heapq
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
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
CHAPTER_END_HOOKS_PROMPT_VERSION = "1.0.0"
CHAPTER_END_HOOK_SAMPLE_LIMIT = 50

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
    response_status: Literal["RESOLVED", "PARTIAL", "UNRESOLVED", "NOT_APPLICABLE"]
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
            if self.response_status != "NOT_APPLICABLE":
                raise ValueError("无钩章节的回应状态必须为 NOT_APPLICABLE")
            if self.response_evidence_id or self.response_summary.strip():
                raise ValueError("无钩章节不能保留回应证据或回应说明")
            return self

        if self.strength == "NONE":
            raise ValueError("有钩章节必须标注实际强度")
        if not self.hook_question.strip():
            raise ValueError("有钩章节必须写出章末形成的具体追读问题")
        if self.response_status == "NOT_APPLICABLE":
            raise ValueError("有钩章节不能使用 NOT_APPLICABLE 回应状态")
        if self.response_status in {"RESOLVED", "PARTIAL"}:
            if not self.response_evidence_id or not self.response_summary.strip():
                raise ValueError("已回应或部分回应必须同时提供后续原文和回应说明")
        elif self.response_evidence_id or self.response_summary.strip():
            raise ValueError("未回应结论不能挂接未经证明的回应原文")
        return self


class ChapterEndHooksOutput(BaseModel):
    chapters: list[ChapterEndHookProposal] = Field(min_length=1, max_length=50)


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


def _request_budget_chars(profile: Any) -> int:
    output_reserve = max(1, int(getattr(profile, "max_output_tokens", 16_000)))
    context_window = getattr(profile, "context_window_tokens", None)
    if context_window is not None:
        return min(160_000, max(24_000, int(context_window) - output_reserve - 4_096))
    return max(48_000, min(160_000, output_reserve * 3))


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


def _sample_indexes(chapter_count: int) -> list[int]:
    if chapter_count <= CHAPTER_END_HOOK_SAMPLE_LIMIT:
        return list(range(chapter_count))
    last = chapter_count - 1
    return sorted({
        round(index * last / (CHAPTER_END_HOOK_SAMPLE_LIMIT - 1))
        for index in range(CHAPTER_END_HOOK_SAMPLE_LIMIT)
    })


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


def _nested_evidence_ids(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key.endswith("evidence_ids") and isinstance(item, list):
                found.update(str(entry) for entry in item if entry)
            else:
                found.update(_nested_evidence_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_nested_evidence_ids(item))
    return found


def _balanced_records(records: list[dict[str, object]]) -> list[dict[str, object]]:
    ordered = sorted(
        records,
        key=lambda item: (
            int(item.get("chapter_ordinal") or 0),
            str(item.get("id") or ""),
        ),
    )
    if len(ordered) <= 2:
        return ordered
    selected_indexes = [0, len(ordered) - 1]
    intervals = [(-(len(ordered) - 1), 0, len(ordered) - 1)]
    while intervals:
        _negative_width, left, right = heapq.heappop(intervals)
        if right - left <= 1:
            continue
        chosen = (left + right) // 2
        selected_indexes.append(chosen)
        if chosen - left > 1:
            heapq.heappush(intervals, (-(chosen - left), left, chosen))
        if right - chosen > 1:
            heapq.heappush(intervals, (-(right - chosen), chosen, right))
    return [ordered[index] for index in selected_indexes]


def _response_evidence_catalog(
    session: Session,
    projection: dict,
    units: list[SourceUnit],
    sample_indexes: list[int],
    ending_by_chapter: dict[int, EvidenceSpan],
) -> list[dict[str, object]]:
    unit_position = {unit.id: index + 1 for index, unit in enumerate(units)}
    priority_by_id: dict[str, int] = {
        evidence_id: 80 for evidence_id in _nested_evidence_ids(projection)
    }
    for index in sample_indexes:
        for following_index in range(index + 1, min(len(units), index + 3)):
            unit = units[following_index]
            openings = list(session.scalars(
                select(EvidenceSpan)
                .where(EvidenceSpan.source_unit_id == unit.id)
                .order_by(EvidenceSpan.paragraph_index, EvidenceSpan.start_char)
            ))
            openings = [
                span
                for span in openings
                if not _is_heading(span, unit) and not _is_boilerplate(span)
            ][:3]
            for span in openings:
                priority_by_id[span.id] = max(priority_by_id.get(span.id, 0), 100)
    for span in ending_by_chapter.values():
        priority_by_id[span.id] = max(priority_by_id.get(span.id, 0), 70)

    if not priority_by_id:
        return []
    spans = list(session.scalars(
        select(EvidenceSpan).where(EvidenceSpan.id.in_(priority_by_id))
    ))
    records = []
    for span in spans:
        chapter_ordinal = unit_position.get(span.source_unit_id)
        if chapter_ordinal is None:
            continue
        text = span.text_snapshot
        records.append({
            "id": span.id,
            "chapter_ordinal": chapter_ordinal,
            "chapter_title": units[chapter_ordinal - 1].title,
            "text": text[:1200],
            "text_truncated": len(text) > 1200,
            "priority": priority_by_id[span.id],
        })
    ordered: list[dict[str, object]] = []
    for priority in sorted({int(item["priority"]) for item in records}, reverse=True):
        ordered.extend(_balanced_records([
            item for item in records if int(item["priority"]) == priority
        ]))
    for item in ordered:
        item.pop("priority", None)
    return ordered


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
    sample_indexes = _sample_indexes(len(units))
    endings, ending_by_chapter, ignored_total = _ending_evidence_catalog(
        session, units, sample_indexes
    )
    response_records = _response_evidence_catalog(
        session,
        projection,
        units,
        sample_indexes,
        ending_by_chapter,
    )
    input_payload = {
        "question_id": CHAPTER_END_HOOKS_QUESTION_ID,
        "contract": {
            "types": list(HOOK_TYPES),
            "measurement": "逐章类型占比、轮换、连续强钩、同类连续上限、无钩比例和回应距离",
            "evidence": "每章只能使用程序指定的真实章末原文；已回应结论必须引用后续章节候选原文",
            "scope": "50 章以内全量覆盖，超过 50 章按全书均衡抽样 50 章",
        },
        "source_chapter_count": len(units),
        "sample_policy": (
            "ALL_CHAPTERS" if len(units) <= CHAPTER_END_HOOK_SAMPLE_LIMIT else "BALANCED_50"
        ),
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
        ],
        "response_evidence_catalog": [],
    }
    request_budget = _request_budget_chars(profile)
    input_budget = max(24_000, request_budget - 12_000)
    base_size = len(json.dumps(input_payload, ensure_ascii=False, separators=(",", ":")))
    for record in response_records:
        record_size = len(json.dumps(record, ensure_ascii=False, separators=(",", ":"))) + 1
        if base_size + record_size > input_budget:
            continue
        input_payload["response_evidence_catalog"].append(record)
        base_size += record_size
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
            "sampled_chapter_count": len(endings),
            "sample_policy": input_payload["sample_policy"],
            "ending_evidence_count": len(endings),
            "response_candidate_count": len(input_payload["response_evidence_catalog"]),
            "ignored_trailing_boilerplate_count": ignored_total,
            "input_chars": len(input_text),
            "input_budget_chars": input_budget,
            "request_budget_chars": request_budget,
        },
    }


def _raise_for_references(
    output: ChapterEndHooksOutput,
    ending_by_chapter: dict[int, EvidenceSpan],
    response_chapter_by_evidence_id: dict[str, int],
) -> None:
    if set(item.chapter_ordinal for item in output.chapters) != set(ending_by_chapter):
        raise ValueError("CHAPTER_END_HOOKS_CHAPTER_COVERAGE_INVALID")
    for item in output.chapters:
        ending = ending_by_chapter[item.chapter_ordinal]
        if item.ending_evidence_id != ending.id:
            raise ValueError("CHAPTER_END_HOOKS_ENDING_REFERENCE_INVALID")
        if item.response_status not in {"RESOLVED", "PARTIAL"}:
            continue
        response_chapter = response_chapter_by_evidence_id.get(
            str(item.response_evidence_id)
        )
        if response_chapter is None:
            raise ValueError("CHAPTER_END_HOOKS_RESPONSE_REFERENCE_INVALID")
        if response_chapter <= item.chapter_ordinal:
            raise ValueError("CHAPTER_END_HOOKS_RESPONSE_ORDER_INVALID")


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
    distances = [
        int(item["response_distance"])
        for item in ordered
        if item.get("response_distance") is not None
    ]
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
        "resolved_or_partial_count": len(distances),
        "unresolved_count": sum(
            item["response_status"] == "UNRESOLVED" for item in ordered
        ),
        "response_distance": {
            "average": round(sum(distances) / len(distances), 2) if distances else None,
            "median": float(median(distances)) if distances else None,
            "maximum": max(distances) if distances else None,
        },
        "phase_type_distribution": [
            {
                "phase_title": phase_title,
                "counts": dict(counts),
            }
            for phase_title, counts in phase_counts.items()
        ],
        "examples_by_type": examples,
    }


def persist_chapter_end_hooks(
    session: Session,
    *,
    task: Task,
    attempt_id: str,
    task_payload: dict,
    output: ChapterEndHooksOutput,
    valid_response_evidence_ids: set[str] | None = None,
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
    projection = _base_projection(session, run.id)
    units = _chapter_units(session, version.id)
    fingerprint = chapter_end_hooks_source_fingerprint(projection, units)
    if fingerprint != task_payload.get("source_fingerprint"):
        raise ValueError("CHAPTER_END_HOOKS_SOURCE_OUTDATED")
    sample_indexes = _sample_indexes(len(units))
    _ending_records, ending_by_chapter, ignored_total = _ending_evidence_catalog(
        session, units, sample_indexes
    )
    response_records = _response_evidence_catalog(
        session,
        projection,
        units,
        sample_indexes,
        ending_by_chapter,
    )
    response_chapter_by_evidence_id = {
        str(item["id"]): int(item["chapter_ordinal"])
        for item in response_records
        if valid_response_evidence_ids is None
        or str(item["id"]) in valid_response_evidence_ids
    }
    _raise_for_references(
        output,
        ending_by_chapter,
        response_chapter_by_evidence_id,
    )

    chapters: list[dict[str, object]] = []
    for proposal in sorted(output.chapters, key=lambda item: item.chapter_ordinal):
        item = proposal.model_dump(mode="json")
        response_chapter = response_chapter_by_evidence_id.get(
            str(proposal.response_evidence_id)
        )
        phase = _phase_for_chapter(projection, proposal.chapter_ordinal)
        item.update({
            "chapter_title": units[proposal.chapter_ordinal - 1].title,
            "ending_evidence_ids": [proposal.ending_evidence_id],
            "response_evidence_ids": (
                [proposal.response_evidence_id] if proposal.response_evidence_id else []
            ),
            "response_chapter_ordinal": response_chapter,
            "response_distance": (
                response_chapter - proposal.chapter_ordinal
                if response_chapter is not None
                else None
            ),
            "phase_id": phase.get("id") if phase else None,
            "phase_title": phase.get("title") if phase else None,
        })
        chapters.append(item)

    required_sample = min(len(units), CHAPTER_END_HOOK_SAMPLE_LIMIT)
    payload = {
        "chapters": chapters,
        "summary": _summary(chapters),
        "coverage": {
            "source_chapter_count": len(units),
            "required_sample_count": required_sample,
            "sampled_chapter_count": len(chapters),
            "sample_policy": (
                "ALL_CHAPTERS"
                if len(units) <= CHAPTER_END_HOOK_SAMPLE_LIMIT
                else "BALANCED_50"
            ),
            "sampled_chapter_ordinals": [item["chapter_ordinal"] for item in chapters],
            "ending_evidence_complete": len(chapters) == required_sample,
            "response_reference_complete": True,
            "ignored_trailing_boilerplate_count": ignored_total,
        },
    }
    revision_no = int(session.scalar(
        select(func.max(LearningQuestionEvidence.revision_no)).where(
            LearningQuestionEvidence.run_id == run.id,
            LearningQuestionEvidence.question_id == CHAPTER_END_HOOKS_QUESTION_ID,
        )
    ) or 0) + 1
    ledger = LearningQuestionEvidence(
        run_id=run.id,
        source_version_id=version.id,
        question_id=CHAPTER_END_HOOKS_QUESTION_ID,
        revision_no=revision_no,
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
    return ledger


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
    run = session.get(AnalysisRun, run_id)
    units = _chapter_units(session, run.source_version_id) if run is not None else []
    current_fingerprint = chapter_end_hooks_source_fingerprint(projection, units)
    is_current = bool(
        ledger is not None
        and ledger.source_fingerprint == current_fingerprint
        and ledger.prompt_version == CHAPTER_END_HOOKS_PROMPT_VERSION
    )
    active_statuses = {
        TaskStatus.PENDING.value,
        TaskStatus.RUNNING.value,
        TaskStatus.RETRY_WAIT.value,
        TaskStatus.WAITING_CONFIRMATION.value,
    }
    if latest_task is not None and latest_task.status in active_statuses:
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
    latest_task = session.scalar(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run.id,
            Task.kind == CHAPTER_END_HOOKS_TASK_KIND,
        )
        .order_by(AnalysisRunTask.batch_index.desc())
    )
    if latest_task is not None and latest_task.status in {
        TaskStatus.PENDING.value,
        TaskStatus.RUNNING.value,
        TaskStatus.RETRY_WAIT.value,
        TaskStatus.WAITING_CONFIRMATION.value,
    }:
        return latest_task
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
    task_payload, max_attempts = prepare_task_provider_routes(
        settings,
        {
            "run_id": run.id,
            "source_version_id": run.source_version_id,
            "source_fingerprint": fingerprint,
            "question_id": CHAPTER_END_HOOKS_QUESTION_ID,
            "provider_name": "openai",
            "model_profile_id": profile.id,
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
    next_index = max(
        (link.batch_index for link in run.task_links),
        default=run.total_batches,
    ) + 1
    session.add(AnalysisRunTask(
        run_id=run.id,
        task_id=task.id,
        batch_index=next_index,
    ))
    run.total_batches = next_index
    run.status = AnalysisRunStatus.PENDING.value
    session.commit()
    session.refresh(task)
    return task
