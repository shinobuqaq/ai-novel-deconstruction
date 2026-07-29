from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
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


CHARACTER_DESIGN_TASK_KIND = "analysis.character_design_evidence"
CHARACTER_DESIGN_QUESTION_ID = "2.2"
CHARACTER_DESIGN_PROMPT_ID = "character_design_evidence"
CHARACTER_DESIGN_PROMPT_VERSION = "2.2.0"
CHARACTER_DESIGN_SUPPORT_POLICY_VERSION = "2.0.0"
CHARACTER_DESIGN_SOFT_INPUT_TOKENS = 150_000
CHARACTER_DESIGN_REQUEST_OVERHEAD_TOKENS = 4_096
CHARACTER_DESIGN_ESTIMATED_CHARS_PER_TOKEN = 1.5
CHARACTER_DESIGN_WINDOW_OVERHEAD_CHARS = 16_000
CHARACTER_DESIGN_MAX_WINDOW_EVENTS = 80
CHARACTER_DESIGN_DECISION_CONTEXT_CHARS = 4_000
CHARACTER_DESIGN_PROXIMITY_SUPPORT_LIMIT = 10
CHARACTER_DESIGN_EVENT_SUPPORT_LIMIT = 21
CHARACTER_DESIGN_ROLE_SUPPORT_PER_QUERY = 3
CHARACTER_DESIGN_FIRST_DISPLAY_PROXIMITY_LIMIT = 2
CHARACTER_DESIGN_FIRST_DISPLAY_SUPPORT_LIMIT = 10
CHARACTER_DESIGN_FIRST_DISPLAY_GUARDRAIL_LIMIT = 24
CHARACTER_DESIGN_CONTRAST_PROXIMITY_LIMIT = 4
CHARACTER_DESIGN_CONTRAST_SUPPORT_LIMIT = 8
CHARACTER_DESIGN_GROUNDING_MIN_NGRAM_OVERLAP = 2
CHARACTER_DESIGN_GROUNDING_MAX_NGRAM_OVERLAP = 5
CHARACTER_DESIGN_GROUNDING_OVERLAP_RATIO = 0.6
CHARACTER_DESIGN_STRICT_GROUNDING_MIN_CHAR_COVERAGE = 0.6
CHARACTER_DESIGN_STRICT_GROUNDING_MAX_UNSUPPORTED_RUN = 3
CHARACTER_DESIGN_EARLIER_DUPLICATE_MIN_NGRAM_OVERLAP = 6
CHARACTER_DESIGN_EARLIER_DUPLICATE_RATIO = 0.6

CHARACTER_DESIGN_FIELDS = (
    "surface_desire",
    "deep_desire",
    "motivation",
    "contrast",
    "boundary",
    "core_ability",
)


def _normalized(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _normalized_without_terminal_punctuation(value: object) -> str:
    return _normalized(value).rstrip("。！？!?；;，,")


class CharacterDesignFieldEvidence(BaseModel):
    field: Literal[
        "surface_desire",
        "deep_desire",
        "motivation",
        "contrast",
        "boundary",
        "core_ability",
    ]
    status: Literal["SUPPORTED", "INSUFFICIENT_EVIDENCE"]
    value: str = Field(
        default="",
        max_length=800,
        description=(
            "SUPPORTED 时写出由 evidence_ids 所引原文支持的人物判断；"
            "可以忠实概括，解释写入 explanation"
        ),
    )
    first_display_chapter_ordinal: int | None = Field(default=None, ge=1)
    first_display_event_id: str | None = Field(default=None, max_length=64)
    display_event: str = Field(
        default="",
        max_length=1200,
        description=(
            "原样复制 first_display_event_id 对应事件的 title；"
            "程序会按事件编号确定性回填"
        ),
    )
    evidence_ids: list[str] = Field(default_factory=list, max_length=8)
    explanation: str = Field(min_length=1, max_length=1600)

    @model_validator(mode="after")
    def require_complete_supported_evidence(self) -> "CharacterDesignFieldEvidence":
        if self.status == "SUPPORTED":
            if not self.value.strip():
                raise ValueError("有证据的要素必须说明具体内容")
            if self.first_display_chapter_ordinal is None:
                raise ValueError("有证据的要素必须给出首次展示章节")
            if not (self.first_display_event_id or "").strip():
                raise ValueError("有证据的要素必须给出首次展示事件")
            if not self.display_event.strip():
                raise ValueError("有证据的要素必须说明展示事件")
            if not self.evidence_ids:
                raise ValueError("有证据的要素必须引用原文")
        elif any((
            self.first_display_chapter_ordinal is not None,
            bool((self.first_display_event_id or "").strip()),
            bool(self.display_event.strip()),
            bool(self.evidence_ids),
        )):
            raise ValueError("证据不足的要素不能保留未经支持的章节、事件说明或原文编号")
        return self


class DesireConflictEvidence(BaseModel):
    chapter_ordinal: int = Field(ge=1)
    event_id: str = Field(min_length=1, max_length=64)
    observation_kind: Literal[
        "CONFLICT",
        "DEEPENING",
        "SHIFT",
        "PRESSURE",
        "REINTERPRETATION",
        "OTHER",
    ] = "CONFLICT"
    surface_desire_stage: Literal["INITIAL", "EVOLVED"] = "INITIAL"
    surface_desire: str = Field(default="", max_length=600)
    deep_desire: str = Field(default="", max_length=600)
    motive: str = Field(default="", max_length=1000)
    motive_source: Literal[
        "PROTAGONIST",
        "NARRATOR_ABOUT_PROTAGONIST",
        "OTHER_CHARACTER",
    ] | None = None
    motive_role: Literal[
        "PROTAGONIST_GOAL",
        "PROTAGONIST_MOTIVE",
        "EXTERNAL_OFFER",
        "EXTERNAL_THREAT",
        "OTHER",
    ] | None = None
    choice: str = Field(default="", max_length=1000)
    choice_polarity: Literal["ACCEPT", "REFUSE", "ACT"] | None = None
    result: str = Field(default="", max_length=1000)
    sacrificed_desire: Literal["SURFACE", "DEEP"] | None = None
    sacrifice: str = Field(default="", max_length=1000)
    sacrifice_role: Literal[
        "CONCRETE_COST",
        "MOTIVE_OR_FEELING",
        "POSSIBLE_COST",
        "OTHER",
    ] | None = None
    arc_change: str = Field(min_length=1, max_length=1000)
    motive_evidence_ids: list[str] = Field(default_factory=list, max_length=4)
    choice_evidence_ids: list[str] = Field(default_factory=list, max_length=4)
    result_evidence_ids: list[str] = Field(default_factory=list, max_length=4)
    sacrifice_evidence_ids: list[str] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def require_at_least_one_evidence(self) -> "DesireConflictEvidence":
        evidence_ids = {
            *self.motive_evidence_ids,
            *self.choice_evidence_ids,
            *self.result_evidence_ids,
            *self.sacrifice_evidence_ids,
        }
        if not evidence_ids:
            raise ValueError("欲望变化或冲突观察至少需要一条原文")
        return self


class CharacterDesignEvidenceOutput(BaseModel):
    protagonist: str = Field(min_length=1, max_length=120)
    fields: list[CharacterDesignFieldEvidence] = Field(min_length=6, max_length=6)
    desire_conflicts: list[DesireConflictEvidence] = Field(default_factory=list)
    arc_summary: str = Field(min_length=1, max_length=2400)


class CharacterDesignFieldRepairOutput(BaseModel):
    protagonist: str = Field(min_length=1, max_length=120)
    fields: list[CharacterDesignFieldEvidence] = Field(min_length=1, max_length=6)


class CharacterDesignConflictRepairOutput(BaseModel):
    protagonist: str = Field(min_length=1, max_length=120)
    desire_conflicts: list[DesireConflictEvidence] = Field(
        default_factory=list,
        max_length=40,
    )


class CharacterDesignValidationError(ValueError):
    def __init__(self, code: str, errors: list[dict[str, object]]) -> None:
        super().__init__(code)
        self.code = code
        self.errors = errors


@dataclass(frozen=True)
class CharacterDesignReconciliation:
    output: CharacterDesignEvidenceOutput | None
    accepted_fields: list[dict[str, object]]
    accepted_field_attempt_ids: dict[str, str]
    accepted_desire_conflicts: list[dict[str, object]]
    accepted_desire_conflicts_attempt_id: str | None
    desire_conflicts_accepted: bool
    pending_desire_conflicts: list[object] | None
    pending_desire_conflicts_attempt_id: str | None
    repair_components: list[str]
    errors: list[dict[str, object]]


def _validation_errors(exc: ValidationError) -> list[dict[str, object]]:
    return [
        {
            "path": [str(part) for part in item.get("loc", ())],
            "type": str(item.get("type") or "value_error"),
            "message": str(item.get("msg") or "字段无效"),
        }
        for item in exc.errors()
    ]


def parse_character_design_evidence(value: dict) -> CharacterDesignEvidenceOutput:
    if (
        set(value) == {"properties"}
        and isinstance(value.get("properties"), dict)
    ):
        value = value["properties"]
    try:
        output = CharacterDesignEvidenceOutput.model_validate(value)
    except ValidationError as exc:
        raise CharacterDesignValidationError(
            "CHARACTER_DESIGN_OUTPUT_INVALID",
            _validation_errors(exc),
        ) from exc
    field_names = [item.field for item in output.fields]
    if set(field_names) != set(CHARACTER_DESIGN_FIELDS) or len(set(field_names)) != 6:
        raise CharacterDesignValidationError(
            "CHARACTER_DESIGN_FIELD_COVERAGE_INVALID",
            [{
                "path": ["fields"],
                "type": "value_error",
                "message": "必须恰好覆盖表层欲望、深层欲望、动机、性格反差、行为底线和核心能力",
            }],
        )
    return output


def _compiled_arc_summary(
    output: CharacterDesignEvidenceOutput,
    *,
    covered_event_count: int,
) -> str:
    fields = {item.field: item for item in output.fields}

    def field_value(field: str) -> str:
        item = fields[field]
        if item.status != "SUPPORTED":
            return "当前证据不足"
        return f"“{item.value.strip()}”"

    prefix = (
        f"表层欲望的首次行动证据是{field_value('surface_desire')}；"
        f"深层欲望的首次行动证据是{field_value('deep_desire')}。"
        f"程序已覆盖全书 {covered_event_count} 个主角事件，"
    )
    if not output.desire_conflicts:
        return (
            prefix
            + "当前账本尚未记录到有原文依据的欲望变化、受压、"
            "重新解释或冲突观察；这不表示主角后续不会变化，"
            "也不表示只有付出明确代价才算人物弧光。"
        )
    chapters = "、".join(
        f"第{chapter}章"
        for chapter in sorted({
            item.chapter_ordinal for item in output.desire_conflicts
        })
    )
    return (
        prefix
        + f"记录到 {len(output.desire_conflicts)} 个有原文依据的"
        f"欲望变化、受压、重新解释或冲突观察，位于{chapters}；"
        "各节点只呈现原文实际支持的部分，不要求每项都同时具备。"
    )


_CHARACTER_DESIGN_FIELD_LABELS = {
    "surface_desire": "表层欲望",
    "deep_desire": "深层欲望",
    "motivation": "持续行动的动机",
    "contrast": "性格反差",
    "boundary": "行为底线",
    "core_ability": "核心能力",
}


def _compiled_field_explanation(
    item: CharacterDesignFieldEvidence,
) -> str:
    label = _CHARACTER_DESIGN_FIELD_LABELS[item.field]
    if item.status != "SUPPORTED":
        return f"当前全书主角事件中没有找到足以确认{label}的行动原文。"
    return (
        f"第 {item.first_display_chapter_ordinal} 章"
        f"“{item.display_event}”中，原文“{item.value.strip()}”"
        f"是本账本用于确认{label}首次展示的行动或选择证据。"
        "结论只限于该事件，不把后续效果或尚未发生的代价倒写到这里。"
    )


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
        / "character_design_evidence_v1.md"
    )
    return path.read_text(encoding="utf-8").strip()


def _request_budget_chars(profile: Any) -> int:
    output_reserve = max(1, int(getattr(profile, "max_output_tokens", 16_000)))
    context_window = getattr(profile, "context_window_tokens", None)
    if context_window is not None:
        available_tokens = max(
            1,
            int(context_window) - output_reserve,
        )
        input_tokens = max(
            1,
            min(
                CHARACTER_DESIGN_SOFT_INPUT_TOKENS,
                available_tokens,
            )
            - CHARACTER_DESIGN_REQUEST_OVERHEAD_TOKENS,
        )
    else:
        input_tokens = max(
            1,
            max(
                48_000,
                min(
                    CHARACTER_DESIGN_SOFT_INPUT_TOKENS,
                    output_reserve * 3,
                ),
            )
            - CHARACTER_DESIGN_REQUEST_OVERHEAD_TOKENS,
        )
    return int(
        input_tokens * CHARACTER_DESIGN_ESTIMATED_CHARS_PER_TOKEN
    )


def _protagonist(projection: dict) -> tuple[str, dict | None]:
    overview = projection.get("story_overview") or {}
    name = str(overview.get("protagonist") or "").strip()
    characters = projection.get("characters", [])
    if name:
        normalized_name = _normalized(name)
        character = next(
            (
                item
                for item in characters
                if _normalized(item.get("name")) == normalized_name
                or any(
                    _normalized(alias) == normalized_name
                    for alias in item.get("aliases", [])
                )
            ),
            None,
        )
    else:
        character = next(
            (item for item in characters if item.get("role") == "PROTAGONIST"),
            None,
        )
    if character is not None and not name:
        name = str(character.get("name") or "").strip()
    return name, character


def _protagonist_events(projection: dict, protagonist_name: str) -> list[dict]:
    return [
        event
        for event in projection.get("events", [])
        if any(
            _normalized(person) == _normalized(protagonist_name)
            for person in event.get("people", [])
        )
    ]


def character_design_source_fingerprint(projection: dict) -> str:
    protagonist_name, character = _protagonist(projection)
    events = _protagonist_events(projection, protagonist_name)
    payload = {
        "support_policy": {
            "version": CHARACTER_DESIGN_SUPPORT_POLICY_VERSION,
            "soft_input_tokens": CHARACTER_DESIGN_SOFT_INPUT_TOKENS,
            "request_overhead_tokens": (
                CHARACTER_DESIGN_REQUEST_OVERHEAD_TOKENS
            ),
            "estimated_chars_per_token": (
                CHARACTER_DESIGN_ESTIMATED_CHARS_PER_TOKEN
            ),
            "decision_context_chars": (
                CHARACTER_DESIGN_DECISION_CONTEXT_CHARS
            ),
            "proximity_support_limit": (
                CHARACTER_DESIGN_PROXIMITY_SUPPORT_LIMIT
            ),
            "event_support_limit": (
                CHARACTER_DESIGN_EVENT_SUPPORT_LIMIT
            ),
            "role_support_per_query": (
                CHARACTER_DESIGN_ROLE_SUPPORT_PER_QUERY
            ),
            "first_display_proximity_limit": (
                CHARACTER_DESIGN_FIRST_DISPLAY_PROXIMITY_LIMIT
            ),
            "first_display_support_limit": (
                CHARACTER_DESIGN_FIRST_DISPLAY_SUPPORT_LIMIT
            ),
            "first_display_guardrail_limit": (
                CHARACTER_DESIGN_FIRST_DISPLAY_GUARDRAIL_LIMIT
            ),
            "contrast_proximity_limit": (
                CHARACTER_DESIGN_CONTRAST_PROXIMITY_LIMIT
            ),
            "contrast_support_limit": (
                CHARACTER_DESIGN_CONTRAST_SUPPORT_LIMIT
            ),
            "grounding_min_ngram_overlap": (
                CHARACTER_DESIGN_GROUNDING_MIN_NGRAM_OVERLAP
            ),
            "grounding_max_ngram_overlap": (
                CHARACTER_DESIGN_GROUNDING_MAX_NGRAM_OVERLAP
            ),
            "grounding_overlap_ratio": (
                CHARACTER_DESIGN_GROUNDING_OVERLAP_RATIO
            ),
            "strict_grounding_min_char_coverage": (
                CHARACTER_DESIGN_STRICT_GROUNDING_MIN_CHAR_COVERAGE
            ),
            "strict_grounding_max_unsupported_run": (
                CHARACTER_DESIGN_STRICT_GROUNDING_MAX_UNSUPPORTED_RUN
            ),
            "earlier_duplicate_min_ngram_overlap": (
                CHARACTER_DESIGN_EARLIER_DUPLICATE_MIN_NGRAM_OVERLAP
            ),
            "earlier_duplicate_ratio": (
                CHARACTER_DESIGN_EARLIER_DUPLICATE_RATIO
            ),
        },
        "source_version_id": projection.get("source_version_id"),
        "deep_revision": projection.get("deep_revision"),
        "protagonist": protagonist_name,
        "character": {
            key: (character or {}).get(key)
            for key in (
                "id",
                "name",
                "aliases",
                "role",
                "goals",
                "motivations",
                "abilities",
                "event_ids",
                "evidence_ids",
            )
        },
        "events": [
            {
                key: event.get(key)
                for key in (
                    "id",
                    "title",
                    "event_type",
                    "summary",
                    "people",
                    "chapter_ordinals",
                    "start_char",
                    "end_char",
                    "trigger",
                    "process",
                    "outcome",
                    "impact",
                    "evidence_ids",
                )
            }
            for event in events
        ],
        "phases": [
            {
                key: phase.get(key)
                for key in (
                    "id",
                    "title",
                    "chapter_ordinals",
                    "event_ids",
                    "evidence_ids",
                )
            }
            for phase in projection.get("phases", [])
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _base_projection(session: Session, run_id: str) -> dict:
    from .workbench import build_workbench_projection

    return build_workbench_projection(
        session,
        run_id,
        include_question_evidence=False,
    )


def _character_design_sources_ready(projection: dict) -> bool:
    # INCOMPLETE means some supporting entities still lack a narrative role.
    # That does not invalidate an already identified protagonist, the full
    # protagonist event ledger or the existing story phases used by question 2.2.
    return bool(
        projection.get("narrative_status") in {"READY", "INCOMPLETE"}
        and projection.get("story_overview")
        and projection.get("phases")
    )


def _chapter_catalog(
    session: Session,
    source_version_id: str,
) -> tuple[list[SourceUnit], dict[str, dict[str, object]]]:
    units = list(session.scalars(
        select(SourceUnit)
        .where(
            SourceUnit.source_version_id == source_version_id,
            SourceUnit.unit_type == "CHAPTER",
        )
        .order_by(SourceUnit.ordinal)
    ))
    return units, {
        unit.id: {"ordinal": ordinal, "title": unit.title}
        for ordinal, unit in enumerate(units, start=1)
    }


def _compact_event(
    event: dict,
    support_evidence_ids: list[str],
    *,
    sequence_no: int,
) -> dict[str, object]:
    return {
        "sequence_no": sequence_no,
        "id": event.get("id"),
        "title": event.get("title"),
        "chapter_ordinals": event.get("chapter_ordinals", []),
        "summary": event.get("summary"),
        "process": event.get("process"),
        "outcome": event.get("outcome"),
        "evidence_ids": support_evidence_ids,
    }


def _window_specs(
    compact_events: list[dict[str, object]],
    evidence_text_by_id: dict[str, str],
    *,
    input_char_budget: int,
) -> list[dict[str, int]]:
    material_budget = max(
        8_000,
        input_char_budget - CHARACTER_DESIGN_WINDOW_OVERHEAD_CHARS,
    )
    windows: list[dict[str, int]] = []
    current: list[dict[str, object]] = []
    current_chars = 0
    current_evidence_ids: set[str] = set()
    for event in compact_events:
        event_evidence_ids = {
            str(evidence_id)
            for evidence_id in event.get("evidence_ids", [])
            if str(evidence_id) in evidence_text_by_id
        }
        new_evidence_ids = event_evidence_ids - current_evidence_ids
        item_chars = len(json.dumps(
            event,
            ensure_ascii=False,
            separators=(",", ":"),
        )) + sum(
            len(evidence_text_by_id[evidence_id]) + len(evidence_id) + 64
            for evidence_id in new_evidence_ids
        )
        if current and (
            current_chars + item_chars > material_budget
            or len(current) >= CHARACTER_DESIGN_MAX_WINDOW_EVENTS
        ):
            windows.append({
                "event_start_sequence": int(current[0]["sequence_no"]),
                "event_end_sequence": int(current[-1]["sequence_no"]),
                "estimated_material_chars": current_chars,
            })
            current = []
            current_chars = 0
            current_evidence_ids = set()
            new_evidence_ids = event_evidence_ids
            item_chars = len(json.dumps(
                event,
                ensure_ascii=False,
                separators=(",", ":"),
            )) + sum(
                len(evidence_text_by_id[evidence_id]) + len(evidence_id) + 64
                for evidence_id in new_evidence_ids
            )
        current.append(event)
        current_chars += item_chars
        current_evidence_ids.update(event_evidence_ids)
    if current:
        windows.append({
            "event_start_sequence": int(current[0]["sequence_no"]),
            "event_end_sequence": int(current[-1]["sequence_no"]),
            "estimated_material_chars": current_chars,
        })
    return windows


def _evidence_overlap_ngrams(value: object) -> set[str]:
    normalized = _grounding_text(value)
    return {
        normalized[index:index + size]
        for size in (2, 3, 4)
        for index in range(max(0, len(normalized) - size + 1))
    }


def _grounding_text(value: object) -> str:
    return re.sub(
        r"[^0-9a-z\u4e00-\u9fff]+",
        "",
        str(value or "").casefold(),
    )


_NEGATION_TERMS = (
    "并没有",
    "并未",
    "没有",
    "未能",
    "不能",
    "不曾",
    "从未",
    "无法",
    "没",
    "未",
    "不",
    "无",
    "非",
)


def _negated_anchors(value: object) -> set[str]:
    normalized = _grounding_text(value)
    anchors: set[str] = set()
    for negation in _NEGATION_TERMS:
        cursor = 0
        while True:
            index = normalized.find(negation, cursor)
            if index < 0:
                break
            following = normalized[
                index + len(negation):index + len(negation) + 6
            ]
            anchors.update(
                following[offset:offset + size]
                for offset in range(min(2, len(following)))
                for size in (2, 3, 4)
                if len(following[offset:offset + size]) == size
            )
            cursor = index + len(negation)
    return anchors


def _has_negation_mismatch(
    claim: object,
    source_texts: list[object],
) -> bool:
    claim_ngrams = _evidence_overlap_ngrams(claim)
    source_ngrams = set().union(*(
        _evidence_overlap_ngrams(text) for text in source_texts
    ))
    claim_negated = _negated_anchors(claim)
    source_negated = set().union(*(
        _negated_anchors(text) for text in source_texts
    ))
    return bool(
        (claim_negated - source_negated).intersection(source_ngrams)
        or (source_negated - claim_negated).intersection(claim_ngrams)
    )


def _is_extract_from_selected_evidence(
    claim: object,
    source_texts: list[object],
) -> bool:
    normalized_claim = _grounding_text(claim)
    return (
        len(normalized_claim) >= 2
        and any(
            normalized_claim in _grounding_text(source)
            for source in source_texts
        )
    )


def _claim_is_grounded(
    claim: object,
    source_texts: list[object],
    *,
    ignored_terms: tuple[object, ...] = (),
) -> bool:
    normalized_ignored = tuple(
        normalized
        for normalized in (
            _grounding_text(term) for term in ignored_terms
        )
        if len(normalized) >= 2
    )

    def without_ignored(value: object) -> str:
        normalized = _grounding_text(value)
        for term in normalized_ignored:
            normalized = normalized.replace(term, "")
        return normalized

    normalized_claim = without_ignored(claim)
    normalized_sources = [
        without_ignored(text)
        for text in source_texts
        if without_ignored(text)
    ]
    if len(normalized_claim) < 2 or not normalized_sources:
        return False
    if any(normalized_claim in source for source in normalized_sources):
        return True
    if _has_negation_mismatch(normalized_claim, normalized_sources):
        return False
    if any(
        (
            2 <= len(source) <= 3
            and source in normalized_claim
        )
        or (
            2 <= len(normalized_claim) <= 3
            and normalized_claim in source
        )
        for source in normalized_sources
    ):
        return True
    claim_ngrams = _evidence_overlap_ngrams(normalized_claim)
    source_ngrams = set().union(*(
        _evidence_overlap_ngrams(text)
        for text in normalized_sources
    ))
    if not claim_ngrams or not source_ngrams:
        return False
    shorter_count = min(len(claim_ngrams), len(source_ngrams))
    required = min(
        CHARACTER_DESIGN_GROUNDING_MAX_NGRAM_OVERLAP,
        max(
            CHARACTER_DESIGN_GROUNDING_MIN_NGRAM_OVERLAP,
            int(
                shorter_count
                * CHARACTER_DESIGN_GROUNDING_OVERLAP_RATIO
            )
            + 1,
        ),
    )
    return len(claim_ngrams.intersection(source_ngrams)) >= required


def _claim_is_strictly_grounded(
    claim: object,
    source_texts: list[object],
    *,
    ignored_terms: tuple[object, ...] = (),
) -> bool:
    if not _claim_is_grounded(
        claim,
        source_texts,
        ignored_terms=ignored_terms,
    ):
        return False
    normalized_ignored = tuple(
        normalized
        for normalized in (
            _grounding_text(term) for term in ignored_terms
        )
        if len(normalized) >= 2
    )

    def without_ignored(value: object) -> str:
        normalized = _grounding_text(value)
        for term in normalized_ignored:
            normalized = normalized.replace(term, "")
        return normalized

    normalized_claim = without_ignored(claim)
    normalized_sources = [
        without_ignored(text)
        for text in source_texts
        if without_ignored(text)
    ]
    if len(normalized_claim) < 2 or not normalized_sources:
        return False
    supported = [False] * len(normalized_claim)
    for size in (2, 3, 4):
        for index in range(max(0, len(normalized_claim) - size + 1)):
            fragment = normalized_claim[index:index + size]
            if any(fragment in source for source in normalized_sources):
                for position in range(index, index + size):
                    supported[position] = True
    coverage = sum(supported) / len(supported)
    longest_unsupported_run = 0
    current_run = 0
    for is_supported in supported:
        current_run = 0 if is_supported else current_run + 1
        longest_unsupported_run = max(
            longest_unsupported_run,
            current_run,
        )
    return (
        coverage
        >= CHARACTER_DESIGN_STRICT_GROUNDING_MIN_CHAR_COVERAGE
        and longest_unsupported_run
        <= CHARACTER_DESIGN_STRICT_GROUNDING_MAX_UNSUPPORTED_RUN
    )


def _contains_earlier_distinctive_repeat(
    selected_text: object,
    earlier_event: dict,
    earlier_evidence_texts: list[object] | None = None,
) -> bool:
    normalized = re.sub(
        r"[^0-9a-z\u4e00-\u9fff]+",
        "",
        str(selected_text or "").casefold(),
    )
    if len(normalized) < 8:
        return False
    selected_ngrams = {
        normalized[index:index + 4]
        for index in range(len(normalized) - 3)
    }
    earlier_ngrams = {
        gram
        for text in [
            *(
                earlier_event.get(key)
                for key in ("title", "summary", "process", "outcome")
            ),
            *(earlier_evidence_texts or []),
        ]
        for gram in _evidence_overlap_ngrams(text)
        if len(gram) == 4
    }
    if not selected_ngrams or not earlier_ngrams:
        return False
    overlap = len(selected_ngrams.intersection(earlier_ngrams))
    required = max(
        CHARACTER_DESIGN_EARLIER_DUPLICATE_MIN_NGRAM_OVERLAP,
        int(
            min(len(selected_ngrams), len(earlier_ngrams))
            * CHARACTER_DESIGN_EARLIER_DUPLICATE_RATIO
        )
        + 1,
    )
    return overlap >= required


_SURFACE_COMMITMENT_TERMS = (
    "决定",
    "决心",
    "选择",
    "答应",
    "同意",
    "拒绝",
    "打算",
    "准备",
    "想要",
    "希望",
    "要做",
    "要去",
)
_SAME_TARGET_STOP_NGRAMS = {
    "一个",
    "这个",
    "那个",
    "什么",
    "自己",
    "他的",
    "她的",
    "他们",
    "已经",
    "后来",
    "于是",
    "但是",
    "如果",
    "接受",
    "决定",
    "选择",
    "希望",
    "想要",
}


def _contains_earlier_same_named_target_commitment(
    selected_value: object,
    earlier_event: dict,
    *,
    other_character_names: set[str],
) -> bool:
    selected = _grounding_text(selected_value)
    earlier = _grounding_text(" ".join(
        str(earlier_event.get(key) or "")
        for key in ("title", "summary", "process", "outcome")
    ))
    if not selected or not earlier or not any(
        _grounding_text(term) in earlier
        for term in _SURFACE_COMMITMENT_TERMS
    ):
        return False
    shared_names = {
        _grounding_text(name)
        for name in other_character_names
        if (
            len(_grounding_text(name)) >= 2
            and _grounding_text(name) in selected
            and _grounding_text(name) in earlier
        )
    }
    if not shared_names:
        return False
    for name in shared_names:
        selected = selected.replace(name, "")
        earlier = earlier.replace(name, "")
    overlap = _evidence_overlap_ngrams(selected).intersection(
        _evidence_overlap_ngrams(earlier)
    )
    return any(
        len(gram) >= 2
        and gram not in _SAME_TARGET_STOP_NGRAMS
        and not gram.isdigit()
        for gram in overlap
    )


_DECISION_SUPPORT_ROLE_QUERIES = (
    "想要 愿意 希望 害怕 不想 为了 理由 保护 拯救 同伴 羁绊",
    "选择 决定 接受 拒绝 同意 交换 交易 换取 成交 行动 伸手 回来",
    "代价 牺牲 失去 放弃 耗费 付出 生命 寿命 卖命 记过 惩罚 后果",
)

_CONCRETE_SACRIFICE_TERMS = (
    "放弃",
    "失去了",
    "失去过",
    "舍弃",
    "交出",
    "耗费",
    "耗尽",
    "付出",
    "代价",
    "牺牲",
    "承受",
    "卖命",
    "赌命",
    "记过",
    "惩罚",
    "受伤",
    "死了",
    "再也不能",
    "再也无法",
    "只剩",
    "就剩",
)

_UNREALIZED_SACRIFICE_PATTERNS = (
    re.compile(
        r"(?:不想|不愿|不肯|害怕|担心|怕|不希望).{0,10}"
        r"(?:失去|死亡|死|生命|寿命|受伤|代价|牺牲)"
    ),
    re.compile(
        r"(?:可能|也许|或许|如果|一旦|差点|险些|恐怕).{0,12}"
        r"(?:失去|死亡|死|生命|寿命|受伤|代价|牺牲)"
    ),
)

_EXTERNAL_MOTIVE_DIRECTIVE_PATTERNS = (
    re.compile(r"你.{0,4}(?:应该|应当|就该|该|可以|必须|只要|要不要)"),
    re.compile(r"(?:跟|和)我.{0,4}(?:换|走|交易|交换)"),
    re.compile(r"我.{0,4}(?:给你|帮你|替你|让你)"),
)

_CHOICE_REFUSAL_TERMS = (
    "不想",
    "不要",
    "不愿",
    "不肯",
    "不能",
    "不会",
    "不答应",
    "没答应",
    "不接受",
    "不交换",
    "不换",
    "不跟",
    "拒绝",
    "休想",
)

_CHOICE_ACCEPTANCE_TERMS = (
    "同意",
    "接受",
    "答应",
    "成交",
    "交换",
    "换取",
    "伸出手",
    "握手",
    "点头",
    "愿意",
)

_CHOICE_ACTION_TERMS = (
    "决定",
    "选择",
    "转身",
    "回来",
    "返回",
    "保护",
    "守护",
    "救",
    "行动",
)


def _has_concrete_sacrifice_signal(value: object) -> bool:
    normalized = _grounding_text(value)
    return (
        len(normalized) >= 3
        and not any(
            pattern.search(normalized)
            for pattern in _UNREALIZED_SACRIFICE_PATTERNS
        )
        and any(
            _grounding_text(term) in normalized
            for term in _CONCRETE_SACRIFICE_TERMS
        )
    )


def _motive_appears_external(
    motive: object,
    source_texts: list[object],
    *,
    protagonist_name: str,
    other_character_names: set[str],
) -> bool:
    normalized_motive = _grounding_text(motive)
    if any(
        pattern.search(normalized_motive)
        for pattern in _EXTERNAL_MOTIVE_DIRECTIVE_PATTERNS
    ):
        return True
    speech_verbs = (
        "说",
        "问",
        "喊",
        "道",
        "告诉",
        "劝",
        "要求",
        "命令",
        "威胁",
        "提议",
        "承诺",
        "许诺",
        "邀请",
        "提出",
        "提供",
        "笑",
    )
    speaker_names = {
        _grounding_text(name)
        for name in other_character_names
        if (
            len(_grounding_text(name)) >= 2
            and _normalized(name) != _normalized(protagonist_name)
        )
    }
    speaker_names.update({
        "同伴",
        "朋友",
        "女孩",
        "男孩",
        "男人",
        "女人",
        "对方",
        "老师",
        "校长",
        "叔叔",
        "婶婶",
    })
    for source in source_texts:
        normalized_source = _grounding_text(source)
        if any(
            re.search(
                rf"{re.escape(name)}.{{0,10}}"
                rf"(?:{'|'.join(speech_verbs)})",
                normalized_source,
            )
            for name in speaker_names
        ):
            return True
    return False


def _choice_polarity_from_text(value: object) -> str | None:
    normalized = _grounding_text(value)
    if any(
        _grounding_text(term) in normalized
        for term in _CHOICE_REFUSAL_TERMS
    ):
        return "REFUSE"
    if any(
        _grounding_text(term) in normalized
        for term in _CHOICE_ACCEPTANCE_TERMS
    ):
        return "ACCEPT"
    if any(
        _grounding_text(term) in normalized
        for term in _CHOICE_ACTION_TERMS
    ):
        return "ACT"
    return None


def _rank_decision_support(
    evidence: list[EvidenceSpan],
    *,
    event_start: int,
    event_end: int,
    query_ngrams: set[str],
    proximity_limit: int,
    role_query_ngrams: tuple[set[str], ...] = (),
) -> list[EvidenceSpan]:
    def features(item: EvidenceSpan) -> tuple[int, int]:
        overlap = len(
            query_ngrams.intersection(
                _evidence_overlap_ngrams(item.text_snapshot)
            )
        )
        distance = (
            0
            if item.start_char <= event_end
            and item.end_char >= event_start
            else min(
                abs(item.end_char - event_start),
                abs(item.start_char - event_end),
            )
        )
        return overlap, distance

    feature_by_id = {
        item.id: features(item)
        for item in evidence
    }
    proximity_ranked = sorted(
        evidence,
        key=lambda item: (
            feature_by_id[item.id][1],
            -feature_by_id[item.id][0],
            item.start_char,
            item.id,
        ),
    )
    semantic_ranked = sorted(
        evidence,
        key=lambda item: (
            -feature_by_id[item.id][0],
            feature_by_id[item.id][1],
            item.start_char,
            item.id,
        ),
    )
    role_ranked: list[EvidenceSpan] = []
    for role_query in role_query_ngrams:
        role_features = {
            item.id: len(
                role_query.intersection(
                    _evidence_overlap_ngrams(item.text_snapshot)
                )
            )
            for item in evidence
        }
        role_ranked.extend(
            item
            for item in sorted(
                evidence,
                key=lambda item: (
                    -role_features[item.id],
                    feature_by_id[item.id][1],
                    item.start_char,
                    item.id,
                ),
            )[:CHARACTER_DESIGN_ROLE_SUPPORT_PER_QUERY]
            if role_features[item.id] > 0
        )
    ranked: list[EvidenceSpan] = []
    seen_ids: set[str] = set()
    for item in [
        *proximity_ranked[:proximity_limit],
        *role_ranked,
        *semantic_ranked,
    ]:
        if item.id in seen_ids:
            continue
        seen_ids.add(item.id)
        ranked.append(item)
    return ranked


def _merge_event_support_ids(
    original_ids: list[str],
    ranked_ids: list[str],
    *,
    support_limit: int = CHARACTER_DESIGN_EVENT_SUPPORT_LIMIT,
) -> list[str]:
    original = list(dict.fromkeys(original_ids))
    selected = list(original)
    selected_ids = set(original)
    target_count = max(
        support_limit,
        len(original),
    )
    for evidence_id in ranked_ids:
        if evidence_id in selected_ids:
            continue
        if len(selected) >= target_count:
            break
        selected_ids.add(evidence_id)
        selected.append(evidence_id)
    return selected


def _first_display_repeat_guardrails(
    events: list[dict],
    evidence_by_id: dict[str, EvidenceSpan],
) -> list[dict[str, object]]:
    ordered_events = sorted(
        events,
        key=lambda event: (
            min(
                (
                    int(chapter)
                    for chapter in event.get("chapter_ordinals", [])
                    if int(chapter) > 0
                ),
                default=10**9,
            ),
            int(event.get("start_char") or 0),
            str(event.get("id") or ""),
        ),
    )
    guardrails: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    for later_index, later_event in enumerate(ordered_events):
        later_chapter = min(
            (
                int(chapter)
                for chapter in later_event.get("chapter_ordinals", [])
                if int(chapter) > 0
            ),
            default=0,
        )
        if later_chapter <= 0:
            continue
        for evidence_id in later_event.get("evidence_ids", []):
            evidence = evidence_by_id.get(str(evidence_id))
            if evidence is None:
                continue
            for earlier_event in ordered_events[:later_index]:
                earlier_chapter = min(
                    (
                        int(chapter)
                        for chapter in earlier_event.get(
                            "chapter_ordinals",
                            [],
                        )
                        if int(chapter) > 0
                    ),
                    default=0,
                )
                if (
                    earlier_chapter <= 0
                    or earlier_chapter >= later_chapter
                    or not _contains_earlier_distinctive_repeat(
                        evidence.text_snapshot,
                        earlier_event,
                    )
                ):
                    continue
                earlier_event_id = str(earlier_event.get("id") or "")
                later_event_id = str(later_event.get("id") or "")
                key = (
                    earlier_event_id,
                    later_event_id,
                    evidence.id,
                )
                if not earlier_event_id or not later_event_id or key in seen:
                    continue
                seen.add(key)
                guardrails.append({
                    "earlier_event_id": earlier_event_id,
                    "earlier_chapter_ordinal": earlier_chapter,
                    "later_event_id": later_event_id,
                    "later_chapter_ordinal": later_chapter,
                    "repeated_evidence_id": evidence.id,
                    "repeated_quote": evidence.text_snapshot,
                })
                if (
                    len(guardrails)
                    >= CHARACTER_DESIGN_FIRST_DISPLAY_GUARDRAIL_LIMIT
                ):
                    return guardrails
    return guardrails


_CONTRAST_CONTEXT_TERMS = (
    "突然反击",
    "出乎意料",
    "一反常态",
    "形成反差",
    "截然相反",
)


def _needs_contrast_context(event: dict) -> bool:
    if str(event.get("event_type") or "") != "ACTION":
        return False
    event_text = " ".join(
        str(event.get(key) or "")
        for key in (
            "title",
            "summary",
            "trigger",
            "process",
            "outcome",
            "impact",
        )
    )
    return (
        any(term in event_text for term in _CONTRAST_CONTEXT_TERMS)
        or bool(re.search(
            r"(?:原本|平时|一直).{0,40}(?:却|突然|反而|转而)",
            event_text,
        ))
    )


def _event_support_evidence(
    session: Session,
    source_version_id: str,
    events: list[dict],
) -> tuple[
    dict[str, list[str]],
    dict[str, EvidenceSpan],
    list[dict[str, object]],
]:
    chapter_units, _chapter_by_unit_id = _chapter_catalog(
        session,
        source_version_id,
    )
    unit_by_chapter = {
        ordinal: unit
        for ordinal, unit in enumerate(chapter_units, start=1)
    }
    original_ids = {
        str(evidence_id)
        for event in events
        for evidence_id in event.get("evidence_ids", [])
        if evidence_id
    }
    if not original_ids:
        return {
            str(event.get("id") or ""): []
            for event in events
            if event.get("id")
        }, {}, []
    original_evidence = list(session.scalars(
        select(EvidenceSpan)
        .where(
            EvidenceSpan.source_version_id == source_version_id,
            EvidenceSpan.id.in_(original_ids),
        )
        .order_by(EvidenceSpan.start_char, EvidenceSpan.id)
    ))
    original_evidence_by_id = {
        item.id: item for item in original_evidence
    }
    first_display_guardrails = _first_display_repeat_guardrails(
        events,
        original_evidence_by_id,
    )
    first_display_event_ids = {
        str(item["earlier_event_id"])
        for item in first_display_guardrails
    }
    expanded_unit_ids = {
        unit_by_chapter[chapter].id
        for event in events
        if (
            str(event.get("event_type") or "") == "DECISION"
            or _needs_contrast_context(event)
            or str(event.get("id") or "") in first_display_event_ids
        )
        for chapter in (
            int(value)
            for value in event.get("chapter_ordinals", [])
            if int(value) > 0
        )
        if chapter in unit_by_chapter
    }
    expanded_evidence = (
        list(session.scalars(
            select(EvidenceSpan)
            .where(
                EvidenceSpan.source_version_id == source_version_id,
                EvidenceSpan.source_unit_id.in_(expanded_unit_ids),
            )
            .order_by(EvidenceSpan.start_char, EvidenceSpan.id)
        ))
        if expanded_unit_ids
        else []
    )
    evidence = list({
        item.id: item
        for item in [*original_evidence, *expanded_evidence]
    }.values())
    evidence.sort(key=lambda item: (item.start_char, item.id))
    evidence_by_id = {item.id: item for item in evidence}
    evidence_by_unit: dict[str, list[EvidenceSpan]] = {}
    for item in evidence:
        evidence_by_unit.setdefault(item.source_unit_id, []).append(item)

    support_by_event_id: dict[str, list[str]] = {}
    for event in events:
        event_id = str(event.get("id") or "")
        if not event_id:
            continue
        original = list(dict.fromkeys(
            str(evidence_id)
            for evidence_id in event.get("evidence_ids", [])
            if str(evidence_id) in evidence_by_id
        ))
        is_decision = str(event.get("event_type") or "") == "DECISION"
        needs_contrast_context = _needs_contrast_context(event)
        is_first_display_candidate = event_id in first_display_event_ids
        if (
            not is_decision
            and not needs_contrast_context
            and not is_first_display_candidate
        ):
            support_by_event_id[event_id] = original
            continue
        chapters = list(dict.fromkeys(
            int(value)
            for value in event.get("chapter_ordinals", [])
            if int(value) > 0
        ))
        units = [
            unit_by_chapter[chapter]
            for chapter in chapters
            if chapter in unit_by_chapter
        ]
        if not units:
            support_by_event_id[event_id] = original
            continue
        event_start = int(event.get("start_char") or units[0].start_char)
        event_end = int(event.get("end_char") or event_start)
        query_ngrams = _evidence_overlap_ngrams(" ".join(
            str(event.get(key) or "")
            for key in (
                "title",
                "summary",
                "trigger",
                "process",
                "outcome",
                "impact",
            )
        ))
        event_support: list[str] = []
        for unit in units:
            overlaps_unit = (
                event_start < unit.end_char
                and event_end > unit.start_char
            )
            local_start = (
                max(event_start, unit.start_char)
                if overlaps_unit
                else unit.start_char
            )
            local_end = (
                min(event_end, unit.end_char)
                if overlaps_unit
                else local_start
            )
            local = [
                item
                for item in evidence_by_unit.get(unit.id, [])
                if (
                    item.end_char
                    >= local_start
                    - CHARACTER_DESIGN_DECISION_CONTEXT_CHARS
                    and item.start_char
                    <= local_end
                    + CHARACTER_DESIGN_DECISION_CONTEXT_CHARS
                )
            ]
            ranked = _rank_decision_support(
                local,
                event_start=local_start,
                event_end=local_end,
                query_ngrams=query_ngrams,
                proximity_limit=(
                    CHARACTER_DESIGN_PROXIMITY_SUPPORT_LIMIT
                    if is_decision
                    else CHARACTER_DESIGN_CONTRAST_PROXIMITY_LIMIT
                    if needs_contrast_context
                    else CHARACTER_DESIGN_FIRST_DISPLAY_PROXIMITY_LIMIT
                ),
                role_query_ngrams=(
                    tuple(
                        _evidence_overlap_ngrams(query)
                        for query in _DECISION_SUPPORT_ROLE_QUERIES
                    )
                    if is_decision
                    else tuple(
                        _evidence_overlap_ngrams(
                            query
                        )
                        for query in (
                            "原本 平时 一直 躲在 躺下 尸体旁 "
                            "装死 胆小 害怕 逃避",
                            "却 突然 反击 起身 决定 行动 开枪",
                        )
                    )
                    if needs_contrast_context
                    else ()
                ),
            )
            original_in_unit = [
                evidence_id
                for evidence_id in original
                if evidence_by_id[evidence_id].source_unit_id == unit.id
            ]
            event_support.extend(_merge_event_support_ids(
                original_in_unit,
                [item.id for item in ranked],
                support_limit=(
                    CHARACTER_DESIGN_EVENT_SUPPORT_LIMIT
                    if is_decision
                    else CHARACTER_DESIGN_CONTRAST_SUPPORT_LIMIT
                    if needs_contrast_context
                    else CHARACTER_DESIGN_FIRST_DISPLAY_SUPPORT_LIMIT
                ),
            ))
        support_by_event_id[event_id] = list(dict.fromkeys(event_support))
    selected_evidence_ids = {
        evidence_id
        for evidence_ids in support_by_event_id.values()
        for evidence_id in evidence_ids
    }
    return support_by_event_id, {
        evidence_id: evidence_by_id[evidence_id]
        for evidence_id in selected_evidence_ids
        if evidence_id in evidence_by_id
    }, first_display_guardrails


def provider_payload_for_character_design(
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
    if not _character_design_sources_ready(projection):
        raise ValueError("NARRATIVE_SYNTHESIS_NOT_READY")
    if projection.get("deep_status") != "READY":
        raise ValueError("DEEP_ANALYSIS_NOT_READY")
    fingerprint = character_design_source_fingerprint(projection)
    if fingerprint != task_payload.get("source_fingerprint"):
        raise ValueError("CHARACTER_DESIGN_SOURCE_OUTDATED")
    protagonist_name, protagonist = _protagonist(projection)
    if not protagonist_name or protagonist is None:
        raise ValueError("PROTAGONIST_NOT_READY")
    events = _protagonist_events(projection, protagonist_name)
    if not events:
        raise ValueError("PROTAGONIST_EVENTS_NOT_READY")
    window_phase = str(task_payload.get("window_phase") or "LEGACY")
    if window_phase not in {"LEGACY", "FIELDS", "CONFLICTS"}:
        raise ValueError("CHARACTER_DESIGN_WINDOW_PHASE_INVALID")
    event_start_sequence = int(
        task_payload.get("event_start_sequence") or 1
    )
    event_end_sequence = int(
        task_payload.get("event_end_sequence") or len(events)
    )
    if (
        event_start_sequence < 1
        or event_end_sequence < event_start_sequence
        or event_end_sequence > len(events)
    ):
        raise ValueError("CHARACTER_DESIGN_WINDOW_INVALID")
    selected_events = events[
        event_start_sequence - 1:event_end_sequence
    ]

    _service, profile = resolve_analysis_profile(
        settings,
        str(task_payload.get("model_profile_id") or ENTITIES_EVENTS_PROFILE_ID),
    )
    chapter_units, chapter_by_unit_id = _chapter_catalog(session, version.id)
    (
        support_by_event_id,
        evidence_by_id,
        first_display_guardrails,
    ) = _event_support_evidence(
        session,
        version.id,
        events,
    )
    selected_event_ids = {
        str(event.get("id") or "") for event in selected_events
    }
    selected_support_ids = {
        str(evidence_id)
        for event in selected_events
        for evidence_id in support_by_event_id.get(
            str(event.get("id") or ""),
            [],
        )
    }
    selected_guardrails = [
        item
        for item in first_display_guardrails
        if str(item.get("later_event_id") or "") in selected_event_ids
    ]
    selected_support_ids.update(
        str(item.get("repeated_evidence_id") or "")
        for item in selected_guardrails
        if item.get("repeated_evidence_id")
    )
    evidence_catalog = [
        {
            "id": evidence.id,
            "chapter_ordinal": chapter_by_unit_id.get(
                evidence.source_unit_id, {}
            ).get("ordinal"),
            "text": evidence.text_snapshot,
        }
        for evidence in sorted(
            (
                evidence
                for evidence_id, evidence in evidence_by_id.items()
                if evidence_id in selected_support_ids
            ),
            key=lambda item: (item.start_char, item.id),
        )
    ]
    repair_raw = task_payload.get("repair_character_components")
    repair_mode = isinstance(repair_raw, list)
    repair_components = [
        str(item)
        for item in (repair_raw or [])
        if str(item) in {
            *CHARACTER_DESIGN_FIELDS,
            "desire_conflicts",
        }
    ]
    if repair_mode and not repair_components:
        raise ValueError("CHARACTER_DESIGN_REPAIR_COMPONENTS_EMPTY")
    repair_fields = [
        field_name
        for field_name in CHARACTER_DESIGN_FIELDS
        if field_name in repair_components
    ]
    repair_conflicts = "desire_conflicts" in repair_components
    if repair_mode and repair_fields and repair_conflicts:
        raise ValueError("CHARACTER_DESIGN_REPAIR_COMPONENTS_MIXED")
    if window_phase == "FIELDS" and repair_conflicts:
        raise ValueError("CHARACTER_DESIGN_WINDOW_COMPONENT_INVALID")
    if window_phase == "CONFLICTS" and repair_fields:
        raise ValueError("CHARACTER_DESIGN_WINDOW_COMPONENT_INVALID")
    accepted_fields = list(
        task_payload.get("accepted_character_fields") or []
    )
    if window_phase == "CONFLICTS" and len(accepted_fields) != len(
        CHARACTER_DESIGN_FIELDS
    ):
        raise ValueError("CHARACTER_DESIGN_WINDOW_FIELDS_NOT_READY")
    window_chapter_ordinals = {
        int(chapter)
        for event in selected_events
        for chapter in event.get("chapter_ordinals", [])
        if int(chapter) > 0
    }
    input_payload = {
        "question_id": CHARACTER_DESIGN_QUESTION_ID,
        "contract": {
            "output": (
                "当前连续事件窗口内的六项首次展示候选"
                if window_phase == "FIELDS"
                else "当前连续事件窗口内的人物欲望变化观察"
                if window_phase == "CONFLICTS"
                else "主角双层欲望卡、六项首次展示节奏表和弧光转折时间轴"
            ),
            "measurement": (
                "逐窗定位六项首次行动候选，由程序跨窗选择全书最早位置"
                if window_phase == "FIELDS"
                else "使用程序已确定的六项早期基线，逐窗记录后续欲望变化"
                if window_phase == "CONFLICTS"
                else "定位六项首次行动事件，并记录后续欲望变化"
            ),
            "evidence": "每项必须引用所属事件的真实原文；文学解释可忠实概括，不按固定关键词验收",
            "scope": (
                f"全书连续窗口 {task_payload.get('window_index')}/"
                f"{task_payload.get('window_count')}，事件序号 "
                f"{event_start_sequence}..{event_end_sequence}；"
                "全部窗口完成后由程序合并为全书结论"
                if window_phase != "LEGACY"
                else "覆盖前三章、前 30 章和全书主角事件"
            ),
            "repair": (
                "本次只返回 repair_request.components 指定的失败部分；"
                "已接受部分由程序保留，不得重复返回"
                if repair_mode
                else "本次返回完整六字段和欲望冲突列表"
            ),
        },
        "protagonist": {
            key: protagonist.get(key)
            for key in (
                "id",
                "name",
                "aliases",
            )
        },
        "chapter_catalog": [
            {"ordinal": ordinal, "title": unit.title}
            for ordinal, unit in enumerate(chapter_units, start=1)
            if ordinal in window_chapter_ordinals
        ],
        "protagonist_events": [
            _compact_event(
                event,
                support_by_event_id.get(str(event.get("id") or ""), []),
                sequence_no=sequence_no,
            )
            for sequence_no, event in enumerate(events, start=1)
            if event_start_sequence <= sequence_no <= event_end_sequence
        ],
        "first_display_guardrails": selected_guardrails,
        "evidence_catalog": evidence_catalog,
    }
    if window_phase != "LEGACY":
        input_payload["window"] = {
            "group_id": task_payload.get("window_group_id"),
            "phase": window_phase,
            "index": task_payload.get("window_index"),
            "count": task_payload.get("window_count"),
            "event_start_sequence": event_start_sequence,
            "event_end_sequence": event_end_sequence,
            "estimated_material_chars": int(
                task_payload.get("estimated_material_chars") or 0
            ),
            "all_windows_form_complete_event_coverage": True,
        }
    if window_phase == "FIELDS":
        input_payload["window_policy"] = (
            "只判断本窗口；SUPPORTED 表示本窗口首次候选，"
            "INSUFFICIENT_EVIDENCE 表示本窗口未发现。"
            "desire_conflicts 必须返回空数组，程序按全书窗口顺序选择最早候选。"
        )
    elif window_phase == "CONFLICTS":
        input_payload["accepted_character_fields"] = accepted_fields
        input_payload["window_policy"] = (
            "六项字段已由程序跨全部字段窗口确定；"
            "本次记录当前窗口内有原文依据的欲望加深、转向、受压、"
            "重新解释或冲突；不要求同时具备明确代价或固定要素。"
        )
    if repair_mode:
        input_payload["repair_request"] = {
            "components": repair_components,
            "accepted_fields": list(
                accepted_fields
            ),
            "accepted_desire_conflicts": list(
                task_payload.get("accepted_desire_conflicts") or []
            ),
        }
    input_text = json.dumps(
        input_payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    request_budget = _request_budget_chars(profile)
    if len(input_text) > request_budget:
        raise ValueError(
            f"CHARACTER_DESIGN_CONTEXT_TOO_LARGE:{len(input_text)}:{request_budget}"
        )
    context_manifest = {
        "source_chapter_count": len(chapter_units),
        "protagonist_event_count": len(events),
        "included_event_count": len(selected_events),
        "event_coverage_complete": window_phase == "LEGACY",
        "evidence_span_count": len(evidence_catalog),
        "decision_support_limit": CHARACTER_DESIGN_EVENT_SUPPORT_LIMIT,
        "first_display_support_limit": (
            CHARACTER_DESIGN_FIRST_DISPLAY_SUPPORT_LIMIT
        ),
        "first_display_guardrail_count": len(first_display_guardrails),
        "input_chars": len(input_text),
        "input_budget_chars": request_budget,
        "estimated_input_tokens": (
            int(
                len(input_text)
                / CHARACTER_DESIGN_ESTIMATED_CHARS_PER_TOKEN
            )
            + 1
        ),
        "soft_input_tokens": CHARACTER_DESIGN_SOFT_INPUT_TOKENS,
        "request_overhead_tokens": CHARACTER_DESIGN_REQUEST_OVERHEAD_TOKENS,
        "estimated_chars_per_token": (
            CHARACTER_DESIGN_ESTIMATED_CHARS_PER_TOKEN
        ),
        "repair_mode": repair_mode,
        "repair_components": repair_components,
        "window_phase": window_phase,
        "window_group_id": task_payload.get("window_group_id"),
        "window_index": task_payload.get("window_index"),
        "window_count": task_payload.get("window_count"),
        "event_start_sequence": event_start_sequence,
        "event_end_sequence": event_end_sequence,
        "accepted_field_count": len(
            task_payload.get("accepted_character_fields") or []
        ),
    }
    output_model: type[BaseModel]
    if window_phase == "CONFLICTS" or (
        repair_mode and repair_conflicts
    ):
        output_model = CharacterDesignConflictRepairOutput
    elif repair_mode:
        output_model = CharacterDesignFieldRepairOutput
    else:
        output_model = CharacterDesignEvidenceOutput
    return {
        "instructions": _prompt(),
        "input": input_text,
        "output_schema": _inline_model_schema(output_model),
        "model_profile_id": str(
            task_payload.get("model_profile_id") or ENTITIES_EVENTS_PROFILE_ID
        ),
        "prompt_id": CHARACTER_DESIGN_PROMPT_ID,
        "prompt_version": CHARACTER_DESIGN_PROMPT_VERSION,
        "source_version_id": version.id,
        "source_char_start": 0,
        "source_char_end": version.total_chars,
        "context_manifest": context_manifest,
    }


def _raise_for_references(
    output: CharacterDesignEvidenceOutput,
    projection: dict,
    valid_evidence_ids: set[str],
    support_by_event_id: dict[str, list[str]],
    evidence_chapter_by_id: dict[str, int] | None = None,
    evidence_text_by_id: dict[str, str] | None = None,
) -> None:
    protagonist_name, _character = _protagonist(projection)
    if _normalized(output.protagonist) != _normalized(protagonist_name):
        raise ValueError("CHARACTER_DESIGN_PROTAGONIST_REFERENCE_INVALID")
    event_by_id = {
        str(event.get("id")): event
        for event in _protagonist_events(projection, protagonist_name)
    }

    def validate_event_reference(
        *,
        event_id: str,
        chapter_ordinal: int,
        evidence_ids: list[str],
    ) -> None:
        event = event_by_id.get(event_id)
        if event is None:
            raise ValueError("CHARACTER_DESIGN_EVENT_REFERENCE_INVALID")
        if chapter_ordinal not in {
            int(item) for item in event.get("chapter_ordinals", [])
        }:
            raise ValueError("CHARACTER_DESIGN_CHAPTER_REFERENCE_INVALID")
        evidence_set = set(evidence_ids)
        allowed_event_evidence = set(
            support_by_event_id.get(
                event_id,
                [
                    str(evidence_id)
                    for evidence_id in event.get("evidence_ids", [])
                    if evidence_id
                ],
            )
        )
        if not evidence_set or not evidence_set.issubset(
            allowed_event_evidence
        ):
            raise ValueError("CHARACTER_DESIGN_EVENT_EVIDENCE_MISMATCH")
        if not evidence_set.issubset(valid_evidence_ids):
            raise ValueError("CHARACTER_DESIGN_EVIDENCE_REFERENCE_INVALID")
        if (
            evidence_chapter_by_id is not None
            and any(
                evidence_chapter_by_id.get(evidence_id)
                != chapter_ordinal
                for evidence_id in evidence_set
            )
        ):
            raise ValueError(
                "CHARACTER_DESIGN_EVIDENCE_CHAPTER_MISMATCH"
            )

    for item in output.fields:
        if item.status != "SUPPORTED":
            continue
        selected_event = event_by_id.get(str(item.first_display_event_id))
        if selected_event is None:
            raise ValueError("CHARACTER_DESIGN_EVENT_REFERENCE_INVALID")
        validate_event_reference(
            event_id=str(item.first_display_event_id),
            chapter_ordinal=int(item.first_display_chapter_ordinal or 0),
            evidence_ids=item.evidence_ids,
        )
        item.display_event = str(
            selected_event.get("title")
            or selected_event.get("process")
            or selected_event.get("outcome")
            or selected_event.get("summary")
            or ""
        ).strip()
        if not item.display_event:
            raise ValueError(
                "CHARACTER_DESIGN_FIELD_DISPLAY_EVENT_MISSING"
            )
        if evidence_text_by_id is not None:
            grounding_sources = [
                *(
                    evidence_text_by_id[evidence_id]
                    for evidence_id in item.evidence_ids
                    if evidence_id in evidence_text_by_id
                ),
                *(
                    selected_event.get(key)
                    for key in (
                        "title",
                        "summary",
                        "trigger",
                        "process",
                        "outcome",
                        "impact",
                    )
                    if selected_event.get(key)
                ),
            ]
            if not _claim_is_grounded(
                item.value,
                grounding_sources,
                ignored_terms=(protagonist_name,),
            ):
                raise ValueError(
                    "CHARACTER_DESIGN_FIELD_VALUE_UNGROUNDED"
                )
            clauses = [
                clause
                for clause in re.split(
                    r"(?:并且|并|而且|同时|随后|然后)",
                    item.value,
                )
                if len(_grounding_text(clause)) >= 4
            ]
            if len(clauses) > 1 and any(
                not _claim_is_grounded(
                    clause,
                    grounding_sources,
                    ignored_terms=(protagonist_name,),
                )
                for clause in clauses
            ):
                raise ValueError(
                    "CHARACTER_DESIGN_FIELD_VALUE_UNGROUNDED"
                )
        # 人物要素属于文学解释。程序不再要求逐字摘录、固定关键词
        # 覆盖率或用重复动作启发式替模型裁决“是否足够典型”。
    accepted_conflicts: list[DesireConflictEvidence] = []
    for item in output.desire_conflicts:
        evidence_groups = (
            item.motive_evidence_ids,
            item.choice_evidence_ids,
            item.result_evidence_ids,
            item.sacrifice_evidence_ids,
        )
        non_empty_evidence_groups = [
            evidence_ids for evidence_ids in evidence_groups if evidence_ids
        ]
        for evidence_ids in non_empty_evidence_groups:
            validate_event_reference(
                event_id=item.event_id,
                chapter_ordinal=item.chapter_ordinal,
                evidence_ids=evidence_ids,
            )
        all_evidence = {
            evidence_id
            for evidence_ids in evidence_groups
            for evidence_id in evidence_ids
        }
        if not all_evidence:
            raise ValueError(
                "CHARACTER_DESIGN_CONFLICT_EVIDENCE_INCOMPLETE"
            )
        if evidence_text_by_id is not None:
            claim_groups = (
                (item.motive, item.motive_evidence_ids, "MOTIVE"),
                (item.choice, item.choice_evidence_ids, "CHOICE"),
                (item.result, item.result_evidence_ids, "RESULT"),
                (
                    item.sacrifice,
                    item.sacrifice_evidence_ids,
                    "SACRIFICE",
                ),
            )
            for claim, evidence_ids, label in claim_groups:
                if not claim:
                    continue
                supporting_texts = [
                    evidence_text_by_id[evidence_id]
                    for evidence_id in evidence_ids
                    if evidence_id in evidence_text_by_id
                ]
                if not supporting_texts or not _claim_is_grounded(
                    claim,
                    supporting_texts,
                    ignored_terms=(protagonist_name,),
                ):
                    raise ValueError(
                        "CHARACTER_DESIGN_OBSERVATION_"
                        f"{label}_EVIDENCE_INCOMPLETE"
                    )
        # 动机、选择、结果、代价可以只出现其中一部分。程序不再要求
        # 同一事件集齐固定要素，也不再用关键词判断文学解释是否成立。
        accepted_conflicts.append(item)
    output.desire_conflicts = accepted_conflicts


def _reference_validation_context(
    session: Session,
    version: SourceVersion,
    projection: dict,
) -> tuple[
    set[str],
    dict[str, list[str]],
    dict[str, int],
    dict[str, str],
    int,
]:
    all_evidence = list(session.scalars(
        select(EvidenceSpan).where(
            EvidenceSpan.source_version_id == version.id
        )
    ))
    valid_evidence_ids = {item.id for item in all_evidence}
    _chapter_units, chapter_by_unit_id = _chapter_catalog(
        session,
        version.id,
    )
    evidence_chapter_by_id = {
        item.id: int(
            chapter_by_unit_id.get(item.source_unit_id, {}).get(
                "ordinal"
            )
            or 0
        )
        for item in all_evidence
    }
    evidence_text_by_id = {
        item.id: item.text_snapshot
        for item in all_evidence
    }
    protagonist_name, _character = _protagonist(projection)
    events = _protagonist_events(projection, protagonist_name)
    (
        support_by_event_id,
        _support_evidence_by_id,
        _first_display_guardrails,
    ) = _event_support_evidence(
        session,
        version.id,
        events,
    )
    return (
        valid_evidence_ids,
        support_by_event_id,
        evidence_chapter_by_id,
        evidence_text_by_id,
        len(events),
    )


def _validate_character_design_field(
    field: CharacterDesignFieldEvidence,
    *,
    protagonist_name: str,
    projection: dict,
    valid_evidence_ids: set[str],
    support_by_event_id: dict[str, list[str]],
    evidence_chapter_by_id: dict[str, int],
    evidence_text_by_id: dict[str, str],
) -> CharacterDesignFieldEvidence:
    fields = [
        (
            field
            if field_name == field.field
            else CharacterDesignFieldEvidence(
                field=field_name,
                status="INSUFFICIENT_EVIDENCE",
                explanation="该字段不属于本次局部校验范围。",
            )
        )
        for field_name in CHARACTER_DESIGN_FIELDS
    ]
    output = CharacterDesignEvidenceOutput(
        protagonist=protagonist_name,
        fields=fields,
        desire_conflicts=[],
        arc_summary="最终弧光总结由程序编译。",
    )
    _raise_for_references(
        output,
        projection,
        valid_evidence_ids,
        support_by_event_id,
        evidence_chapter_by_id,
        evidence_text_by_id,
    )
    return next(item for item in output.fields if item.field == field.field)


def _validate_character_design_conflicts(
    *,
    protagonist_name: str,
    fields: list[CharacterDesignFieldEvidence],
    raw_conflicts: list[object],
    projection: dict,
    valid_evidence_ids: set[str],
    support_by_event_id: dict[str, list[str]],
    evidence_chapter_by_id: dict[str, int],
    evidence_text_by_id: dict[str, str],
) -> list[DesireConflictEvidence]:
    output = parse_character_design_evidence({
        "protagonist": protagonist_name,
        "fields": [
            item.model_dump(mode="json")
            for item in fields
        ],
        "desire_conflicts": raw_conflicts,
        "arc_summary": "最终弧光总结由程序编译。",
    })
    _raise_for_references(
        output,
        projection,
        valid_evidence_ids,
        support_by_event_id,
        evidence_chapter_by_id,
        evidence_text_by_id,
    )
    return output.desire_conflicts


def reconcile_character_design_response(
    session: Session,
    *,
    task_payload: dict,
    value: dict,
    attempt_id: str,
) -> CharacterDesignReconciliation:
    run = session.get(AnalysisRun, task_payload.get("run_id"))
    version = session.get(SourceVersion, task_payload.get("source_version_id"))
    if run is None or version is None:
        raise ValueError("ANALYSIS_RUN_NOT_FOUND")
    projection = _base_projection(session, run.id)
    fingerprint = character_design_source_fingerprint(projection)
    if fingerprint != task_payload.get("source_fingerprint"):
        raise ValueError("CHARACTER_DESIGN_SOURCE_OUTDATED")
    protagonist_name, _character = _protagonist(projection)
    if not protagonist_name:
        raise ValueError("PROTAGONIST_NOT_READY")
    (
        valid_evidence_ids,
        support_by_event_id,
        evidence_chapter_by_id,
        evidence_text_by_id,
        _covered_event_count,
    ) = _reference_validation_context(
        session,
        version,
        projection,
    )
    errors: list[dict[str, object]] = []
    accepted: dict[str, CharacterDesignFieldEvidence] = {}
    accepted_attempt_ids = {
        str(key): str(item)
        for key, item in (
            task_payload.get("accepted_character_field_attempt_ids") or {}
        ).items()
        if str(key) in CHARACTER_DESIGN_FIELDS and item
    }

    for index, raw in enumerate(
        task_payload.get("accepted_character_fields") or []
    ):
        try:
            field = CharacterDesignFieldEvidence.model_validate(raw)
            if field.field in accepted:
                continue
            accepted[field.field] = _validate_character_design_field(
                field,
                protagonist_name=protagonist_name,
                projection=projection,
                valid_evidence_ids=valid_evidence_ids,
                support_by_event_id=support_by_event_id,
                evidence_chapter_by_id=evidence_chapter_by_id,
                evidence_text_by_id=evidence_text_by_id,
            )
        except (ValidationError, ValueError) as exc:
            field_name = (
                str(raw.get("field") or "")
                if isinstance(raw, dict)
                else ""
            )
            accepted.pop(field_name, None)
            accepted_attempt_ids.pop(field_name, None)
            errors.append({
                "path": ["accepted_character_fields", str(index)],
                "type": "discarded_saved_component",
                "message": str(exc),
            })

    allowed_components = {
        *CHARACTER_DESIGN_FIELDS,
        "desire_conflicts",
    }
    window_phase = str(task_payload.get("window_phase") or "LEGACY")
    event_start_sequence = int(
        task_payload.get("event_start_sequence") or 1
    )
    event_end_sequence = int(
        task_payload.get("event_end_sequence") or _covered_event_count
    )
    event_sequence_by_id = {
        str(event.get("id") or ""): sequence_no
        for sequence_no, event in enumerate(
            _protagonist_events(projection, protagonist_name),
            start=1,
        )
    }

    def validate_conflict_window(
        conflicts: list[DesireConflictEvidence],
    ) -> None:
        if any(
            (
                event_sequence_by_id.get(item.event_id) is None
                or event_sequence_by_id[item.event_id] < event_start_sequence
                or event_sequence_by_id[item.event_id] > event_end_sequence
            )
            for item in conflicts
        ):
            raise ValueError(
                "CHARACTER_DESIGN_WINDOW_CONFLICT_REFERENCE_INVALID"
            )

    repair_raw = task_payload.get("repair_character_components")
    if isinstance(repair_raw, list):
        requested_components = [
            str(item)
            for item in repair_raw
            if str(item) in allowed_components
        ]
    elif window_phase == "FIELDS":
        requested_components = list(CHARACTER_DESIGN_FIELDS)
    elif window_phase == "CONFLICTS":
        requested_components = ["desire_conflicts"]
    else:
        requested_components = [
            *CHARACTER_DESIGN_FIELDS,
            "desire_conflicts",
        ]
    requested_fields = [
        field_name
        for field_name in CHARACTER_DESIGN_FIELDS
        if field_name in requested_components and field_name not in accepted
    ]
    response_protagonist = str(value.get("protagonist") or "")
    protagonist_matches = (
        _normalized(response_protagonist)
        == _normalized(protagonist_name)
    )
    if not protagonist_matches:
        errors.append({
            "path": ["protagonist"],
            "type": "value_error",
            "message": "主角姓名与已确认主角不一致",
        })

    raw_fields = value.get("fields")
    if not isinstance(raw_fields, list):
        raw_fields = []
        if requested_fields:
            errors.append({
                "path": ["fields"],
                "type": "missing",
                "message": "本次返回没有可识别的人物字段列表",
            })
    raw_by_field: dict[str, list[tuple[int, object]]] = {}
    for index, raw in enumerate(raw_fields):
        if not isinstance(raw, dict):
            errors.append({
                "path": ["fields", str(index)],
                "type": "model_type",
                "message": "人物字段必须是对象",
            })
            continue
        field_name = str(raw.get("field") or "")
        raw_by_field.setdefault(field_name, []).append((index, raw))

    if protagonist_matches:
        for field_name in requested_fields:
            candidates = raw_by_field.get(field_name, [])
            if len(candidates) != 1:
                errors.append({
                    "path": ["fields", field_name],
                    "type": "missing" if not candidates else "duplicated",
                    "message": (
                        "本次没有返回该字段"
                        if not candidates
                        else "本次重复返回了该字段"
                    ),
                })
                continue
            index, raw = candidates[0]
            try:
                field = CharacterDesignFieldEvidence.model_validate(raw)
                selected_sequence = event_sequence_by_id.get(
                    str(field.first_display_event_id or "")
                )
                if (
                    field.status == "SUPPORTED"
                    and (
                        selected_sequence is None
                        or selected_sequence < event_start_sequence
                        or selected_sequence > event_end_sequence
                    )
                ):
                    raise ValueError(
                        "CHARACTER_DESIGN_WINDOW_EVENT_REFERENCE_INVALID"
                    )
                accepted[field_name] = _validate_character_design_field(
                    field,
                    protagonist_name=protagonist_name,
                    projection=projection,
                    valid_evidence_ids=valid_evidence_ids,
                    support_by_event_id=support_by_event_id,
                    evidence_chapter_by_id=evidence_chapter_by_id,
                    evidence_text_by_id=evidence_text_by_id,
                )
                accepted_attempt_ids[field_name] = attempt_id
            except ValidationError as exc:
                errors.extend({
                    "path": [
                        "fields",
                        str(index),
                        *[str(part) for part in item.get("loc", ())],
                    ],
                    "type": str(item.get("type") or "value_error"),
                    "message": str(item.get("msg") or "字段无效"),
                } for item in exc.errors())
            except ValueError as exc:
                errors.append({
                    "path": ["fields", str(index)],
                    "type": "reference_error",
                    "message": str(exc),
                })

    for field_name, candidates in raw_by_field.items():
        if field_name not in requested_fields:
            errors.append({
                "path": ["fields", str(candidates[0][0]), "field"],
                "type": "unexpected",
                "message": "本次返回了未请求的字段，程序已忽略",
            })

    missing_fields = [
        field_name
        for field_name in CHARACTER_DESIGN_FIELDS
        if field_name not in accepted
    ]
    accepted_conflicts: list[DesireConflictEvidence] = []
    conflicts_accepted = False
    conflicts_attempt_id: str | None = None
    pending_conflicts = (
        list(task_payload.get("pending_desire_conflicts") or [])
        if isinstance(
            task_payload.get("pending_desire_conflicts"),
            list,
        )
        else None
    )
    pending_conflicts_attempt_id = str(
        task_payload.get("pending_desire_conflicts_attempt_id")
        or ""
    ) or None
    if (
        missing_fields
        and "desire_conflicts" in requested_components
        and protagonist_matches
        and isinstance(value.get("desire_conflicts"), list)
    ):
        pending_conflicts = list(value["desire_conflicts"])
        pending_conflicts_attempt_id = attempt_id
    if not missing_fields and task_payload.get("desire_conflicts_accepted"):
        try:
            accepted_conflicts = _validate_character_design_conflicts(
                protagonist_name=protagonist_name,
                fields=[accepted[name] for name in CHARACTER_DESIGN_FIELDS],
                raw_conflicts=list(
                    task_payload.get("accepted_desire_conflicts") or []
                ),
                projection=projection,
                valid_evidence_ids=valid_evidence_ids,
                support_by_event_id=support_by_event_id,
                evidence_chapter_by_id=evidence_chapter_by_id,
                evidence_text_by_id=evidence_text_by_id,
            )
            validate_conflict_window(accepted_conflicts)
            conflicts_accepted = True
            conflicts_attempt_id = str(
                task_payload.get(
                    "accepted_desire_conflicts_attempt_id"
                )
                or ""
            ) or None
        except (CharacterDesignValidationError, ValueError) as exc:
            errors.append({
                "path": ["accepted_desire_conflicts"],
                "type": "discarded_saved_component",
                "message": str(exc),
            })

    if (
        not missing_fields
        and not conflicts_accepted
        and pending_conflicts is not None
    ):
        try:
            accepted_conflicts = _validate_character_design_conflicts(
                protagonist_name=protagonist_name,
                fields=[accepted[name] for name in CHARACTER_DESIGN_FIELDS],
                raw_conflicts=pending_conflicts,
                projection=projection,
                valid_evidence_ids=valid_evidence_ids,
                support_by_event_id=support_by_event_id,
                evidence_chapter_by_id=evidence_chapter_by_id,
                evidence_text_by_id=evidence_text_by_id,
            )
            validate_conflict_window(accepted_conflicts)
            conflicts_accepted = True
            conflicts_attempt_id = pending_conflicts_attempt_id
            pending_conflicts = None
            pending_conflicts_attempt_id = None
        except (CharacterDesignValidationError, ValueError) as exc:
            errors.append({
                "path": ["pending_desire_conflicts"],
                "type": "reference_error",
                "message": str(exc),
            })
            pending_conflicts = None
            pending_conflicts_attempt_id = None

    if (
        not missing_fields
        and not conflicts_accepted
        and "desire_conflicts" in requested_components
        and protagonist_matches
    ):
        raw_conflicts = value.get("desire_conflicts")
        if not isinstance(raw_conflicts, list):
            errors.append({
                "path": ["desire_conflicts"],
                "type": "missing",
                "message": "本次返回没有可识别的欲望冲突列表",
            })
        else:
            try:
                accepted_conflicts = _validate_character_design_conflicts(
                    protagonist_name=protagonist_name,
                    fields=[
                        accepted[name]
                        for name in CHARACTER_DESIGN_FIELDS
                    ],
                    raw_conflicts=raw_conflicts,
                    projection=projection,
                    valid_evidence_ids=valid_evidence_ids,
                    support_by_event_id=support_by_event_id,
                    evidence_chapter_by_id=evidence_chapter_by_id,
                    evidence_text_by_id=evidence_text_by_id,
                )
                validate_conflict_window(accepted_conflicts)
                conflicts_accepted = True
                conflicts_attempt_id = attempt_id
            except CharacterDesignValidationError as exc:
                errors.extend(exc.errors)
            except ValueError as exc:
                errors.append({
                    "path": ["desire_conflicts"],
                    "type": "reference_error",
                    "message": str(exc),
                })

    if window_phase == "FIELDS":
        repair_components = missing_fields
        accepted_conflicts = []
        conflicts_accepted = True
        conflicts_attempt_id = None
    else:
        repair_components = (
            missing_fields
            if missing_fields
            else ([] if conflicts_accepted else ["desire_conflicts"])
        )
    accepted_fields = [
        accepted[field_name].model_dump(mode="json")
        for field_name in CHARACTER_DESIGN_FIELDS
        if field_name in accepted
    ]
    accepted_conflict_payload = [
        item.model_dump(mode="json")
        for item in accepted_conflicts
    ]
    output = (
        CharacterDesignEvidenceOutput(
            protagonist=protagonist_name,
            fields=[accepted[name] for name in CHARACTER_DESIGN_FIELDS],
            desire_conflicts=accepted_conflicts,
            arc_summary="最终弧光总结由程序编译。",
        )
        if not repair_components
        else None
    )
    return CharacterDesignReconciliation(
        output=output,
        accepted_fields=accepted_fields,
        accepted_field_attempt_ids=accepted_attempt_ids,
        accepted_desire_conflicts=accepted_conflict_payload,
        accepted_desire_conflicts_attempt_id=conflicts_attempt_id,
        desire_conflicts_accepted=conflicts_accepted,
        pending_desire_conflicts=(
            pending_conflicts
            if not conflicts_accepted
            else None
        ),
        pending_desire_conflicts_attempt_id=(
            pending_conflicts_attempt_id
            if not conflicts_accepted
            else None
        ),
        repair_components=repair_components,
        errors=errors,
    )


def _enqueue_character_design_window_tasks(
    session: Session,
    settings: Settings,
    *,
    run: AnalysisRun,
    fingerprint: str,
    window_group_id: str,
    window_phase: Literal["FIELDS", "CONFLICTS"],
    windows: list[dict[str, int]],
    accepted_fields: list[dict[str, object]] | None = None,
    accepted_field_attempt_ids: dict[str, str] | None = None,
) -> list[Task]:
    _service, profile = resolve_analysis_profile(
        settings,
        ENTITIES_EVENTS_PROFILE_ID,
    )
    next_index = max(
        (link.batch_index for link in run.task_links),
        default=run.total_batches,
    ) + 1
    created_tasks: list[Task] = []
    for offset, window in enumerate(windows):
        base_payload: dict[str, object] = {
            "run_id": run.id,
            "source_version_id": run.source_version_id,
            "source_fingerprint": fingerprint,
            "question_id": CHARACTER_DESIGN_QUESTION_ID,
            "provider_name": "openai",
            "model_profile_id": profile.id,
            "window_group_id": window_group_id,
            "window_phase": window_phase,
            "window_index": offset + 1,
            "window_count": len(windows),
            "event_start_sequence": window["event_start_sequence"],
            "event_end_sequence": window["event_end_sequence"],
            "estimated_material_chars": window["estimated_material_chars"],
            "analysis_policy": "ALL_PROTAGONIST_EVENTS_WINDOWED",
        }
        if window_phase == "CONFLICTS":
            base_payload.update({
                "accepted_character_fields": accepted_fields or [],
                "accepted_character_field_attempt_ids": (
                    accepted_field_attempt_ids or {}
                ),
                "repair_character_components": ["desire_conflicts"],
            })
        task_payload, max_attempts = prepare_task_provider_routes(
            settings,
            base_payload,
            profile.max_retries + 1,
        )
        task = Task(
            project_id=run.source_version.document.project_id,
            kind=CHARACTER_DESIGN_TASK_KIND,
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
    return created_tasks


def _character_design_window_ledgers(
    session: Session,
    *,
    run_id: str,
    fingerprint: str,
    window_group_id: str,
    window_phase: Literal["FIELDS", "CONFLICTS"],
) -> list[LearningQuestionEvidence]:
    marker = "f" if window_phase == "FIELDS" else "c"
    prefix = f"2.2{marker}-{window_group_id}-"
    return list(session.scalars(
        select(LearningQuestionEvidence)
        .where(
            LearningQuestionEvidence.run_id == run_id,
            LearningQuestionEvidence.source_fingerprint == fingerprint,
            LearningQuestionEvidence.prompt_version
            == CHARACTER_DESIGN_PROMPT_VERSION,
            LearningQuestionEvidence.question_id.like(f"{prefix}%"),
        )
        .order_by(LearningQuestionEvidence.question_id)
    ))


def _merged_character_design_fields(
    field_payloads: list[dict[str, object]],
) -> tuple[
    list[CharacterDesignFieldEvidence],
    dict[str, str],
]:
    selected: dict[str, CharacterDesignFieldEvidence] = {}
    selected_attempt_ids: dict[str, str] = {}
    for payload in field_payloads:
        for raw in payload.get("fields", []):
            if not isinstance(raw, dict):
                continue
            item = CharacterDesignFieldEvidence.model_validate(raw)
            current = selected.get(item.field)
            if current is not None and current.status == "SUPPORTED":
                continue
            if item.status == "SUPPORTED" or current is None:
                selected[item.field] = item
                attempt_id = str(raw.get("created_by_attempt_id") or "")
                if attempt_id:
                    selected_attempt_ids[item.field] = attempt_id
    merged: list[CharacterDesignFieldEvidence] = []
    for field_name in CHARACTER_DESIGN_FIELDS:
        item = selected.get(field_name)
        if item is None or item.status != "SUPPORTED":
            item = CharacterDesignFieldEvidence(
                field=field_name,
                status="INSUFFICIENT_EVIDENCE",
                explanation=(
                    "程序已连续核对全部主角事件窗口，"
                    "当前成书原文不足以确定该项首次行动证据。"
                ),
            )
            selected_attempt_ids.pop(field_name, None)
        merged.append(item)
    return merged, selected_attempt_ids


def persist_character_design_evidence(
    session: Session,
    *,
    settings: Settings | None = None,
    task: Task,
    attempt_id: str,
    task_payload: dict,
    output: CharacterDesignEvidenceOutput,
) -> LearningQuestionEvidence:
    window_phase = str(task_payload.get("window_phase") or "LEGACY")
    existing = session.scalar(
        select(LearningQuestionEvidence).where(
            LearningQuestionEvidence.created_by_task_id == task.id
        )
    )
    if existing is not None and window_phase == "LEGACY":
        return existing
    run = session.get(AnalysisRun, task_payload.get("run_id"))
    version = session.get(SourceVersion, task_payload.get("source_version_id"))
    if run is None or version is None:
        raise ValueError("ANALYSIS_RUN_NOT_FOUND")
    projection = _base_projection(session, run.id)
    fingerprint = character_design_source_fingerprint(projection)
    if fingerprint != task_payload.get("source_fingerprint"):
        raise ValueError("CHARACTER_DESIGN_SOURCE_OUTDATED")
    (
        valid_evidence_ids,
        support_by_event_id,
        evidence_chapter_by_id,
        evidence_text_by_id,
        covered_event_count,
    ) = _reference_validation_context(
        session,
        version,
        projection,
    )
    _raise_for_references(
        output,
        projection,
        valid_evidence_ids,
        support_by_event_id,
        evidence_chapter_by_id,
        evidence_text_by_id,
    )
    if window_phase in {"FIELDS", "CONFLICTS"}:
        window_group_id = str(
            task_payload.get("window_group_id") or ""
        )
        window_index = int(task_payload.get("window_index") or 0)
        window_count = int(task_payload.get("window_count") or 0)
        event_start_sequence = int(
            task_payload.get("event_start_sequence") or 0
        )
        event_end_sequence = int(
            task_payload.get("event_end_sequence") or 0
        )
        if (
            not re.fullmatch(r"[a-f0-9]{8}", window_group_id)
            or window_index < 1
            or window_count < 1
            or window_index > window_count
            or event_start_sequence < 1
            or event_end_sequence < event_start_sequence
            or event_end_sequence > covered_event_count
        ):
            raise ValueError("CHARACTER_DESIGN_WINDOW_INVALID")
        marker = "f" if window_phase == "FIELDS" else "c"
        internal_payload = {
            "window_group_id": window_group_id,
            "window_phase": window_phase,
            "window_index": window_index,
            "window_count": window_count,
            "event_start_sequence": event_start_sequence,
            "event_end_sequence": event_end_sequence,
            "fields": output.model_dump(mode="json")["fields"],
            "desire_conflicts": output.model_dump(mode="json")[
                "desire_conflicts"
            ],
        }
        field_attempt_ids = {
            str(key): str(value)
            for key, value in (
                task_payload.get(
                    "accepted_character_field_attempt_ids"
                ) or {}
            ).items()
            if value
        }
        for item in internal_payload["fields"]:
            item["created_by_attempt_id"] = field_attempt_ids.get(
                str(item["field"]),
                attempt_id,
            )
        conflict_attempt_id = str(
            task_payload.get(
                "accepted_desire_conflicts_attempt_id"
            )
            or attempt_id
        )
        for item in internal_payload["desire_conflicts"]:
            item["created_by_attempt_id"] = conflict_attempt_id
        if existing is None:
            existing = LearningQuestionEvidence(
                run_id=run.id,
                source_version_id=version.id,
                question_id=(
                    f"2.2{marker}-{window_group_id}-{window_index:03d}"
                ),
                revision_no=1,
                source_fingerprint=fingerprint,
                payload_json=json.dumps(
                    internal_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                prompt_id=CHARACTER_DESIGN_PROMPT_ID,
                prompt_version=CHARACTER_DESIGN_PROMPT_VERSION,
                created_by_task_id=task.id,
                created_by_attempt_id=attempt_id,
            )
            session.add(existing)
            session.commit()
            session.refresh(existing)

        phase_ledgers = _character_design_window_ledgers(
            session,
            run_id=run.id,
            fingerprint=fingerprint,
            window_group_id=window_group_id,
            window_phase=window_phase,
        )
        if len(phase_ledgers) < window_count:
            return existing
        if len(phase_ledgers) != window_count:
            raise ValueError("CHARACTER_DESIGN_WINDOW_COUNT_INVALID")
        phase_payloads = [
            json.loads(item.payload_json) for item in phase_ledgers
        ]
        if [
            int(item.get("window_index") or 0)
            for item in phase_payloads
        ] != list(range(1, window_count + 1)):
            raise ValueError("CHARACTER_DESIGN_WINDOW_COVERAGE_INVALID")
        if (
            int(phase_payloads[0].get("event_start_sequence") or 0)
            != 1
            or int(
                phase_payloads[-1].get("event_end_sequence") or 0
            )
            != covered_event_count
            or any(
                int(current.get("event_end_sequence") or 0) + 1
                != int(following.get("event_start_sequence") or 0)
                for current, following in zip(
                    phase_payloads,
                    phase_payloads[1:],
                )
            )
        ):
            raise ValueError(
                "CHARACTER_DESIGN_FULL_EVENT_COVERAGE_INVALID"
            )

        field_ledgers = _character_design_window_ledgers(
            session,
            run_id=run.id,
            fingerprint=fingerprint,
            window_group_id=window_group_id,
            window_phase="FIELDS",
        )
        if len(field_ledgers) != window_count:
            raise ValueError("CHARACTER_DESIGN_FIELD_WINDOWS_INCOMPLETE")
        field_payloads = [
            json.loads(item.payload_json) for item in field_ledgers
        ]
        merged_fields, merged_field_attempt_ids = (
            _merged_character_design_fields(field_payloads)
        )
        if window_phase == "FIELDS":
            field_by_name = {
                item.field: item for item in merged_fields
            }
            can_scan_conflicts = all(
                field_by_name[name].status == "SUPPORTED"
                for name in ("surface_desire", "deep_desire")
            )
            if can_scan_conflicts:
                if settings is None:
                    raise ValueError(
                        "CHARACTER_DESIGN_SETTINGS_REQUIRED"
                    )
                conflict_ledgers = _character_design_window_ledgers(
                    session,
                    run_id=run.id,
                    fingerprint=fingerprint,
                    window_group_id=window_group_id,
                    window_phase="CONFLICTS",
                )
                active_character_tasks = list(session.scalars(
                    select(Task)
                    .join(
                        AnalysisRunTask,
                        AnalysisRunTask.task_id == Task.id,
                    )
                    .where(
                        AnalysisRunTask.run_id == run.id,
                        Task.kind == CHARACTER_DESIGN_TASK_KIND,
                        Task.status.in_((
                            TaskStatus.PENDING.value,
                            TaskStatus.RUNNING.value,
                            TaskStatus.RETRY_WAIT.value,
                            TaskStatus.WAITING_CONFIRMATION.value,
                        )),
                    )
                    .order_by(AnalysisRunTask.batch_index)
                ))
                active_conflict_task = next(
                    (
                        candidate
                        for candidate in active_character_tasks
                        if (
                            json.loads(
                                candidate.payload_json
                            ).get("window_group_id")
                            == window_group_id
                            and json.loads(
                                candidate.payload_json
                            ).get("window_phase")
                            == "CONFLICTS"
                        )
                    ),
                    None,
                )
                if (
                    not conflict_ledgers
                    and active_conflict_task is None
                ):
                    windows = [
                        {
                            "event_start_sequence": int(
                                item["event_start_sequence"]
                            ),
                            "event_end_sequence": int(
                                item["event_end_sequence"]
                            ),
                            "estimated_material_chars": int(
                                item.get(
                                    "estimated_material_chars",
                                    0,
                                )
                            ),
                        }
                        for item in phase_payloads
                    ]
                    _enqueue_character_design_window_tasks(
                        session,
                        settings,
                        run=run,
                        fingerprint=fingerprint,
                        window_group_id=window_group_id,
                        window_phase="CONFLICTS",
                        windows=windows,
                        accepted_fields=[
                            item.model_dump(mode="json")
                            for item in merged_fields
                        ],
                        accepted_field_attempt_ids=(
                            merged_field_attempt_ids
                        ),
                    )
                    session.commit()
                return existing
            output = CharacterDesignEvidenceOutput(
                protagonist=_protagonist(projection)[0],
                fields=merged_fields,
                desire_conflicts=[],
                arc_summary="最终弧光总结由程序编译。",
            )
            task_payload = {
                **task_payload,
                "accepted_character_field_attempt_ids": (
                    merged_field_attempt_ids
                ),
                "character_design_field_window_count": window_count,
                "character_design_conflict_window_count": 0,
                "character_design_window_group_id": window_group_id,
            }
        else:
            event_sequence_by_id = {
                str(event.get("id") or ""): sequence_no
                for sequence_no, event in enumerate(
                    _protagonist_events(
                        projection,
                        _protagonist(projection)[0],
                    ),
                    start=1,
                )
            }
            merged_conflicts: list[DesireConflictEvidence] = []
            conflict_attempt_ids: dict[str, str] = {}
            seen_conflict_events: set[str] = set()
            for payload in phase_payloads:
                for raw in payload.get("desire_conflicts", []):
                    if not isinstance(raw, dict):
                        continue
                    item = DesireConflictEvidence.model_validate(raw)
                    if item.event_id in seen_conflict_events:
                        continue
                    seen_conflict_events.add(item.event_id)
                    merged_conflicts.append(item)
                    source_attempt_id = str(
                        raw.get("created_by_attempt_id") or ""
                    )
                    if source_attempt_id:
                        conflict_attempt_ids[item.event_id] = (
                            source_attempt_id
                        )
            merged_conflicts.sort(
                key=lambda item: (
                    event_sequence_by_id.get(item.event_id, 10**9),
                    item.event_id,
                )
            )
            output = CharacterDesignEvidenceOutput(
                protagonist=_protagonist(projection)[0],
                fields=merged_fields,
                desire_conflicts=merged_conflicts,
                arc_summary="最终弧光总结由程序编译。",
            )
            task_payload = {
                **task_payload,
                "accepted_character_field_attempt_ids": (
                    merged_field_attempt_ids
                ),
                "desire_conflict_attempt_ids": conflict_attempt_ids,
                "character_design_field_window_count": window_count,
                "character_design_conflict_window_count": window_count,
                "character_design_window_group_id": window_group_id,
            }
    for item in output.fields:
        item.explanation = _compiled_field_explanation(item)
    output.arc_summary = _compiled_arc_summary(
        output,
        covered_event_count=covered_event_count,
    )
    payload = output.model_dump(mode="json")
    field_attempt_ids = {
        str(key): str(value)
        for key, value in (
            task_payload.get("accepted_character_field_attempt_ids") or {}
        ).items()
        if value
    }
    for item in payload["fields"]:
        item["created_by_attempt_id"] = field_attempt_ids.get(
            str(item["field"]),
            attempt_id,
        )
    conflict_attempt_id = str(
        task_payload.get("accepted_desire_conflicts_attempt_id")
        or attempt_id
    )
    conflict_attempt_ids = {
        str(key): str(value)
        for key, value in (
            task_payload.get("desire_conflict_attempt_ids") or {}
        ).items()
        if value
    }
    for item in payload["desire_conflicts"]:
        item["evidence_ids"] = list(dict.fromkeys([
            *item["motive_evidence_ids"],
            *item["choice_evidence_ids"],
            *item["result_evidence_ids"],
            *item["sacrifice_evidence_ids"],
        ]))
        item["created_by_attempt_id"] = conflict_attempt_ids.get(
            str(item["event_id"]),
            conflict_attempt_id,
        )
    payload["coverage"] = {
        "source_chapter_count": len(projection.get("chapters", [])),
        "protagonist_event_count": covered_event_count,
        "covered_event_count": covered_event_count,
        "event_coverage_complete": True,
        "event_coverage_policy": (
            "ALL_PROTAGONIST_EVENTS_WINDOWED"
            if task_payload.get("character_design_window_group_id")
            else "ALL_PROTAGONIST_EVENTS_SINGLE_REQUEST"
        ),
        "window_group_id": task_payload.get(
            "character_design_window_group_id"
        ),
        "field_window_count": int(
            task_payload.get("character_design_field_window_count") or 1
        ),
        "conflict_window_count": int(
            task_payload.get(
                "character_design_conflict_window_count"
            ) or 1
        ),
        "first_30_chapter_event_count": sum(
            1
            for event in _protagonist_events(
                projection,
                _protagonist(projection)[0],
            )
            if any(
                0 < int(chapter) <= 30
                for chapter in event.get("chapter_ordinals", [])
            )
        ),
    }
    revision_no = int(session.scalar(
        select(func.max(LearningQuestionEvidence.revision_no)).where(
            LearningQuestionEvidence.run_id == run.id,
            LearningQuestionEvidence.question_id == CHARACTER_DESIGN_QUESTION_ID,
        )
    ) or 0) + 1
    payload_json = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
    )
    if window_phase in {"FIELDS", "CONFLICTS"} and existing is not None:
        ledger = existing
        ledger.question_id = CHARACTER_DESIGN_QUESTION_ID
        ledger.revision_no = revision_no
        ledger.payload_json = payload_json
        ledger.created_by_attempt_id = attempt_id
    else:
        ledger = LearningQuestionEvidence(
            run_id=run.id,
            source_version_id=version.id,
            question_id=CHARACTER_DESIGN_QUESTION_ID,
            revision_no=revision_no,
            source_fingerprint=fingerprint,
            payload_json=payload_json,
            prompt_id=CHARACTER_DESIGN_PROMPT_ID,
            prompt_version=CHARACTER_DESIGN_PROMPT_VERSION,
            created_by_task_id=task.id,
            created_by_attempt_id=attempt_id,
        )
        session.add(ledger)
    session.commit()
    session.refresh(ledger)
    return ledger


def build_character_design_projection(
    session: Session,
    run_id: str,
    projection: dict,
) -> tuple[str, dict | None]:
    ledger = session.scalar(
        select(LearningQuestionEvidence)
        .where(
            LearningQuestionEvidence.run_id == run_id,
            LearningQuestionEvidence.question_id == CHARACTER_DESIGN_QUESTION_ID,
        )
        .order_by(LearningQuestionEvidence.revision_no.desc())
    )
    latest_task = session.scalar(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run_id,
            Task.kind == CHARACTER_DESIGN_TASK_KIND,
        )
        .order_by(AnalysisRunTask.batch_index.desc())
    )
    current_fingerprint = character_design_source_fingerprint(projection)
    is_current = bool(
        ledger is not None
        and ledger.source_fingerprint == current_fingerprint
        and ledger.prompt_version == CHARACTER_DESIGN_PROMPT_VERSION
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


def enqueue_character_design_evidence(
    session: Session,
    settings: Settings,
    run: AnalysisRun,
    *,
    force: bool = False,
) -> Task | None:
    projection = _base_projection(session, run.id)
    if not _character_design_sources_ready(projection):
        return None
    if projection.get("deep_status") != "READY":
        return None
    protagonist_name, protagonist = _protagonist(projection)
    if not protagonist_name or protagonist is None:
        return None
    fingerprint = character_design_source_fingerprint(projection)
    latest_task = session.scalar(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run.id,
            Task.kind == CHARACTER_DESIGN_TASK_KIND,
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
            LearningQuestionEvidence.question_id == CHARACTER_DESIGN_QUESTION_ID,
        )
        .order_by(LearningQuestionEvidence.revision_no.desc())
    )
    if (
        latest_ledger is not None
        and latest_ledger.source_fingerprint == fingerprint
        and latest_ledger.prompt_version == CHARACTER_DESIGN_PROMPT_VERSION
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
    events = _protagonist_events(projection, protagonist_name)
    if not events:
        return None
    (
        support_by_event_id,
        evidence_by_id,
        _first_display_guardrails,
    ) = _event_support_evidence(
        session,
        run.source_version_id,
        events,
    )
    compact_events = [
        _compact_event(
            event,
            support_by_event_id.get(str(event.get("id") or ""), []),
            sequence_no=sequence_no,
        )
        for sequence_no, event in enumerate(events, start=1)
    ]
    windows = _window_specs(
        compact_events,
        {
            evidence_id: evidence.text_snapshot
            for evidence_id, evidence in evidence_by_id.items()
        },
        input_char_budget=_request_budget_chars(profile),
    )
    if not windows:
        return None
    created_tasks = _enqueue_character_design_window_tasks(
        session,
        settings,
        run=run,
        fingerprint=fingerprint,
        window_group_id=uuid.uuid4().hex[:8],
        window_phase="FIELDS",
        windows=windows,
    )
    session.commit()
    session.refresh(created_tasks[0])
    return created_tasks[0]
