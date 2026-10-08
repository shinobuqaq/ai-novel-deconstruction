from __future__ import annotations

import json
import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import (
    AnalysisRun,
    AnalysisRunStatus,
    AnalysisRunTask,
    DeepAnalysis,
    EvidenceSpan,
    LearningReport,
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
from .source_import import source_text


LEARNING_REPORT_TASK_KIND = "analysis.learning_report"
LEARNING_REPORT_PROMPT_ID = "learning_report"
LEARNING_REPORT_PROMPT_VERSION = "1.8.0"
LEARNING_REPORT_COMPATIBLE_PROMPT_VERSIONS = frozenset({
    "1.4.2",
    "1.4.3",
    "1.5.0",
    "1.5.1",
    "1.5.2",
    "1.5.3",
    "1.5.4",
    "1.5.5",
    "1.6.0",
    "1.7.0",
    "1.8.0",
})
from app.services.learning_questions import (
    INITIAL_INCREMENTAL_QUESTION_IDS,
    LEARNING_ANSWER_DEFAULT_SOFT_INPUT_CAP_TOKENS,
    LEARNING_ANSWER_ESTIMATED_CHARS_PER_TOKEN,
    LEARNING_LITERARY_JUDGMENT_POLICY,
    LEARNING_OBJECTIVE_VALIDATION_POLICY,
    LEARNING_QUESTION_CATALOG,
    LEARNING_QUESTION_CATALOG_VERSION,
    LEARNING_QUESTION_CONTRACTS,
    LEARNING_QUESTION_CONTRACT_VERSIONS,
    LEARNING_QUESTION_ITEM_CONTRACTS,
    LEARNING_QUESTION_MODEL_ITEM_CONTRACTS,
    LEARNING_REPORT_BATCH_LABEL,
    LEARNING_REPORT_PROGRAM_COMPILED_QUESTION_IDS,
    LEARNING_REPORT_PROJECTION_QUESTION_IDS,
    LEARNING_VALIDATION_POLICY_VERSION,
    OPENING_PAYOFF_MAX_WINDOW_CANDIDATES,
    QG1_QUESTION_IDS,
    STAGE_NAMES,
    LearningAnswerProposal,
    LearningCommonError,
    LearningContractClassificationProposal,
    LearningContractItemDefinition,
    LearningContractItemProposal,
    LearningHandbook,
    LearningMetricProposal,
    LearningPayoffClassificationProposal,
    LearningQuestionContract,
    LearningQuestionDefinition,
    LearningReportOutput,
    LearningReportValidationError,
    LearningTemplate,
    _CHAPTER_END_HOOK_STRENGTH_LABELS,
    _CHAPTER_END_HOOK_TYPE_LABELS,
    _CHAPTER_END_RESPONSE_DISTANCE,
    _CHAPTER_END_RESPONSE_SCOPE_DENIAL,
    _EXTERNAL_EFFECT_QUESTION,
    _EXTERNAL_EFFECT_TERM,
    _EXTERNAL_EFFECT_UNCERTAINTY,
    _PAYOFF_EXCLUSION_LABELS,
    _PROHIBITED_PRICING_TERM,
    _QUESTION_BY_ID,
    _READER_BEHAVIOR_OR_PSYCHOLOGY,
    _RECOMMENDED_RANK,
    _UNSUPPORTED_EXTERNAL_CAUSALITY,
    _UNSUPPORTED_EXTERNAL_EFFECT_ASSERTION,
    _UNSUPPORTED_EXTERNAL_MAGNITUDE,
    _USER_TEXT_CLAUSE_BREAK,
    _answer_external_causality_texts,
    _answer_user_visible_texts,
    _clause_marks_external_effect_uncertainty,
    _contract_item_by_id,
    _evidence_ids,
    _has_out_of_scope_4_9_response_distance,
    _has_unsupported_external_effect_claim,
    _inline_model_schema,
    _metric_blob,
    _metric_group_matches_count,
    _metric_group_matches_ratio,
    _metric_mentions_number,
    _metric_value_matches_number,
    _metrics_with_label,
    _split_user_text_clauses,
    _validate_answer_evidence_subset,
    _validate_answer_user_text_boundaries,
    _validation_errors,
    get_all_question_plugins,
    get_program_compiled_question_ids,
    get_projection_question_ids,
    get_question_plugin,
)
from app.services.learning_questions.plugins.q1_4 import (
    _opening_payoff_candidate_artifact,
    _opening_promise_metric_by_source,
    _program_1_4_promise_sources,
    _legacy_1_4_payoff_ledger,
    _program_1_4_answer,
    _validate_1_4_answer_against_projection,
)
from app.services.learning_questions.plugins.q2_1 import (
    _opening_action_counts,
    _normalized_person_name,
    _opening_character_program_artifact,
    _validated_2_1_program_artifact,
    _program_2_1_contract_items,
    _program_2_1_representative_evidence_ids,
    _program_2_1_reading_fields,
)
from app.services.learning_questions.plugins.q2_2 import (
    _normalized_2_2_text,
    _trim_2_2_sentence,
    _2_2_metric_matches_with_evidence,
    _2_2_ordered_nodes_match,
    _program_2_2_contract_items,
    _apply_program_2_2_answer,
    _validate_2_2_answer_against_projection,
)
from app.services.learning_questions.plugins.q3_1 import (
    _opening_structure_evidence_ids,
    _program_3_1_evidence_ids,
    _program_3_1_contract_items,
    _apply_program_3_1_answer,
)
from app.services.learning_questions.plugins.q3_2 import (
    _program_3_2_contract_items,
    _apply_program_3_2_answer,
)
from app.services.learning_questions.plugins.q4_9 import (
    _program_ratio_text,
    _program_hook_label,
    _program_4_9_matrix,
    _apply_program_4_9_answer,
    _validate_4_9_answer_against_projection,
)


from pathlib import Path

def _prompt() -> str:
    path = Path(__file__).resolve().parents[3] / "prompts" / "learning_report_v1.md"
    return path.read_text(encoding="utf-8").strip()

def _balanced_items(items: list[dict], chapter_key: str) -> list[dict]:
    """Order chapter material so every prefix is spread across the book."""
    ordered = sorted(items, key=lambda item: int(item.get(chapter_key) or 0))
    if len(ordered) <= 2:
        return ordered
    remaining = list(range(len(ordered)))
    selected_indexes: list[int] = []
    while remaining:
        if not selected_indexes:
            chosen = remaining[0]
        elif len(selected_indexes) == 1:
            chosen = remaining[-1]
        else:
            chosen = max(
                remaining,
                key=lambda index: min(abs(index - selected) for selected in selected_indexes),
            )
        selected_indexes.append(chosen)
        remaining.remove(chosen)
    return [ordered[index] for index in selected_indexes]


def _request_budget(profile: Any) -> dict[str, int | float | str]:
    output_reserve = max(1, int(getattr(profile, "max_output_tokens", 16_000)))
    context_window = getattr(profile, "context_window_tokens", None)
    if context_window is not None:
        available_tokens = max(
            8_000,
            int(context_window) - output_reserve - 4_096,
        )
        input_tokens = min(
            LEARNING_ANSWER_DEFAULT_SOFT_INPUT_CAP_TOKENS,
            available_tokens,
        )
        source = "MODEL_CONTEXT_WITH_DEFAULT_SOFT_CAP"
    else:
        input_tokens = LEARNING_ANSWER_DEFAULT_SOFT_INPUT_CAP_TOKENS
        source = "DEFAULT_SOFT_CAP_WITHOUT_REPORTED_CONTEXT"
    return {
        "default_soft_input_cap_tokens": (
            LEARNING_ANSWER_DEFAULT_SOFT_INPUT_CAP_TOKENS
        ),
        "input_token_budget": input_tokens,
        "input_char_budget": int(
            input_tokens * LEARNING_ANSWER_ESTIMATED_CHARS_PER_TOKEN
        ),
        "estimated_chars_per_token": LEARNING_ANSWER_ESTIMATED_CHARS_PER_TOKEN,
        "budget_source": source,
    }


def _chapter_index_by_unit_id(
    session: Session,
    source_version_id: str,
) -> dict[str, dict[str, object]]:
    chapter_units = list(session.scalars(
        select(SourceUnit)
        .where(
            SourceUnit.source_version_id == source_version_id,
            SourceUnit.unit_type == "CHAPTER",
        )
        .order_by(SourceUnit.ordinal)
    ))
    return {
        unit.id: {"ordinal": ordinal, "title": unit.title}
        for ordinal, unit in enumerate(chapter_units, start=1)
    }


def _evidence_records(
    evidence_ids: set[str],
    evidence_by_id: dict[str, EvidenceSpan],
    chapter_by_unit_id: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for evidence_id in sorted(evidence_ids):
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None:
            continue
        chapter = chapter_by_unit_id.get(evidence.source_unit_id, {})
        records.append({
            "id": evidence.id,
            "chapter_ordinal": chapter.get("ordinal"),
            "chapter_title": chapter.get("title"),
            "paragraph_number": evidence.paragraph_index + 1,
            "source_char_start": evidence.start_char + 1,
            "source_char_end": evidence.end_char,
            "text": evidence.text_snapshot,
        })
    return records


_FRONT_MATTER_LABEL = re.compile(
    r"^(?:内容简介|内容提要|作品简介|简介)\s*[:：]?\s*$"
)
_NARRATIVE_HEADING = re.compile(
    r"^(?:序幕|楔子|引子|尾声|后记|"
    r"第[0-9一二三四五六七八九十百千万零〇两]+"
    r"(?:卷|部|篇|幕|章|回|节))"
)
_TITLE_NOISE = re.compile(
    r"^(?:作者|编者|译者|来源|正文前内容)\s*[:：]?"
)


def _opening_promise_source_artifact(
    session: Session,
    settings: Settings,
    version: SourceVersion,
) -> dict[str, object]:
    units = list(session.scalars(
        select(SourceUnit)
        .where(SourceUnit.source_version_id == version.id)
        .order_by(SourceUnit.ordinal)
    ))
    first_chapter = next(
        (unit for unit in units if unit.unit_type == "CHAPTER"),
        None,
    )
    if first_chapter is None:
        return {
            "preferred_title": "",
            "title_candidates": [],
            "description_present": False,
            "description_text": "",
            "description_evidence_ids": [],
            "chapter_numbering_policy": (
                "只对 CHAPTER 类型来源单元按顺序从 1 编号；"
                "PREFACE 等前置内容不计入章节序号。"
            ),
        }
    text = source_text(settings, version)
    excerpt_end = min(
        len(text),
        first_chapter.end_char,
        first_chapter.start_char + 12_000,
    )
    excerpt = text[:excerpt_end]
    line_records: list[tuple[str, int, int]] = []
    cursor = 0
    for raw_line in excerpt.splitlines(keepends=True):
        line = raw_line.strip()
        line_start = cursor
        cursor += len(raw_line)
        line_records.append((line, line_start, cursor))
    if cursor < len(excerpt):
        line_records.append((excerpt[cursor:].strip(), cursor, len(excerpt)))

    description_marker_index = next(
        (
            index
            for index, (line, _start, _end) in enumerate(line_records)
            if _FRONT_MATTER_LABEL.fullmatch(line)
        ),
        None,
    )
    first_narrative_index = next(
        (
            index
            for index, (line, _start, _end) in enumerate(line_records)
            if line and _NARRATIVE_HEADING.match(line)
        ),
        None,
    )
    if description_marker_index is not None:
        title_scan_end = description_marker_index
    elif first_narrative_index is not None:
        title_scan_end = first_narrative_index
    else:
        title_scan_end = min(24, len(line_records))
    title_candidates = [
        line
        for line, _start, _end in line_records[:title_scan_end]
        if (
            1 < len(line) <= 80
            and not _TITLE_NOISE.match(line)
            and not _NARRATIVE_HEADING.match(line)
            and not _FRONT_MATTER_LABEL.fullmatch(line)
            and "验收样本" not in line
        )
    ]
    preferred_title = title_candidates[-1] if title_candidates else ""

    description_lines: list[str] = []
    description_start: int | None = None
    description_end: int | None = None
    if description_marker_index is not None:
        for line, line_start, line_end in line_records[
            description_marker_index + 1:
        ]:
            if not line and not description_lines:
                continue
            if line and _NARRATIVE_HEADING.match(line):
                break
            if line:
                if description_start is None:
                    description_start = line_start + (
                        len(excerpt[line_start:line_end])
                        - len(excerpt[line_start:line_end].lstrip())
                    )
                description_lines.append(line)
                description_end = line_end
            elif description_lines:
                description_lines.append("")
    description_text = "\n".join(description_lines).strip()
    if description_start is not None and description_end is not None:
        description_evidence_ids = list(session.scalars(
            select(EvidenceSpan.id)
            .where(
                EvidenceSpan.source_version_id == version.id,
                EvidenceSpan.start_char < description_end,
                EvidenceSpan.end_char > description_start,
            )
            .order_by(EvidenceSpan.start_char)
        ))
    else:
        description_evidence_ids = []
    front_matter_end = (
        description_end
        if description_end is not None
        else min(excerpt_end, first_chapter.start_char + 2_000)
    )
    return {
        "preferred_title": preferred_title,
        "title_candidates": list(dict.fromkeys(title_candidates)),
        "front_matter_text": text[:front_matter_end].strip(),
        "description_present": bool(description_text),
        "description_text": description_text,
        "description_source_char_start": (
            description_start + 1 if description_start is not None else None
        ),
        "description_source_char_end": description_end,
        "description_evidence_ids": description_evidence_ids,
        "evidence_ids": description_evidence_ids,
        "chapter_numbering_policy": (
            "只对 CHAPTER 类型来源单元按顺序从 1 编号；"
            "PREFACE 等前置内容不计入章节序号。"
        ),
        "promise_chapter_position": 0,
    }




def _material_bundle(
    kind: str,
    item: dict,
    evidence_by_id: dict[str, EvidenceSpan],
    chapter_by_unit_id: dict[str, dict[str, object]],
) -> dict[str, object]:
    return {
        "kind": kind,
        "item": item,
        "evidence": _evidence_records(
            _evidence_ids(item), evidence_by_id, chapter_by_unit_id
        ),
    }


def _compact_item_evidence_ids(item: dict) -> list[str]:
    evidence_ids = [
        str(value)
        for value in item.get("evidence_ids", [])
        if value
    ]
    if len(evidence_ids) <= 2:
        return evidence_ids
    return [evidence_ids[0], evidence_ids[-1]]


def _compact_opening_structure_model_artifact(
    opening_structure: dict,
    question_ids: tuple[str, ...] | str,
) -> dict[str, object]:
    selected_question_ids = (
        {question_ids}
        if isinstance(question_ids, str)
        else set(question_ids)
    )
    artifact: dict[str, object] = {
        "question_ids": sorted(
            selected_question_ids.intersection({"3.1", "3.2"})
        ),
        "coverage": opening_structure.get("coverage", {}),
        "scope_boundary": (
            "该账本只覆盖当前单书前三章；同品类分布和标准节奏区间"
            "需要多书同口径数据，不能由本书推断。"
        ),
    }
    if "3.1" in selected_question_ids:
        opening = opening_structure.get("opening_scene") or {}
        artifact["opening_scene"] = {
            key: opening.get(key)
            for key in (
                "chapter_ordinal",
                "chapter_title",
                "opening_type",
                "story_start_paragraph",
                "story_start_source_char",
                "story_start_evidence_id",
                "protagonist_first_paragraph",
                "protagonist_first_source_char",
                "protagonist_first_evidence_id",
                "protagonist_action",
                "initial_trouble",
                "first_sentence_function",
                "first_paragraph_function",
                "explanation",
            )
        }
        artifact["opening_scene"]["evidence_ids"] = (
            _compact_item_evidence_ids(opening)
        )
    if "3.2" in selected_question_ids:
        artifact["chapter_tasks"] = [
            {
                **{
                    key: item.get(key)
                    for key in (
                        "chapter_ordinal",
                        "tasks",
                        "key_event_paragraphs",
                        "key_event_positions",
                        "explanation",
                    )
                },
                "evidence_ids": _compact_item_evidence_ids(item),
            }
            for item in opening_structure.get("chapter_tasks", [])
            if isinstance(item, dict)
        ]
        artifact["paragraph_segments"] = [
            {
                **{
                    key: item.get(key)
                    for key in (
                        "chapter_ordinal",
                        "paragraph_start",
                        "paragraph_end",
                        "scope",
                        "function",
                        "information_modules",
                        "explanation",
                        "character_count",
                        "source_char_start",
                        "source_char_end",
                    )
                },
                "evidence_ids": _compact_item_evidence_ids(item),
            }
            for item in opening_structure.get("paragraph_segments", [])
            if isinstance(item, dict)
        ]
        artifact["information_timeline"] = [
            {
                **{
                    key: item.get(key)
                    for key in (
                        "module",
                        "status",
                        "first_chapter_ordinal",
                        "first_paragraph",
                        "finding",
                        "character_count",
                        "source_char_start",
                        "source_char_end",
                    )
                },
                "evidence_ids": _compact_item_evidence_ids(item),
            }
            for item in opening_structure.get("information_timeline", [])
            if isinstance(item, dict)
        ]
        artifact["overall_sequence"] = opening_structure.get(
            "overall_sequence"
        )
    return artifact


def _source_materials(
    projection: dict,
    question_ids: tuple[str, ...],
) -> list[tuple[int, str, dict]]:
    deep = projection.get("deep_analysis") or {}
    materials: list[tuple[int, str, dict]] = []
    opening_only = bool(question_ids) and set(question_ids).issubset({
        "3.1",
        "3.2",
    })
    overview = projection.get("story_overview")
    if (
        question_ids != ("2.1",)
        and not opening_only
        and isinstance(overview, dict)
    ):
        materials.append((116, "story_overview", overview))
    character_design = projection.get("character_design_evidence")
    if (
        "2.2" in question_ids
        and isinstance(character_design, dict)
        and character_design.get("is_current") is not False
    ):
        materials.append((120, "character_design_evidence", character_design))
    opening_structure = projection.get("opening_structure_evidence")
    if (
        {"3.1", "3.2"}.intersection(question_ids)
        and isinstance(opening_structure, dict)
        and opening_structure.get("is_current") is not False
    ):
        materials.append((
            130,
            "opening_structure_evidence",
            _compact_opening_structure_model_artifact(
                opening_structure,
                question_ids,
            ),
        ))
        if opening_only:
            return materials
    chapter_end_hooks = projection.get("chapter_end_hooks_evidence")
    if (
        {"3.4", "4.9"}.intersection(question_ids)
        and isinstance(chapter_end_hooks, dict)
        and chapter_end_hooks.get("is_current") is not False
    ):
        hook_metadata = {
            key: value for key, value in chapter_end_hooks.items() if key != "chapters"
        }
        materials.append((122, "chapter_end_hooks_summary", hook_metadata))
        hook_items = chapter_end_hooks.get("chapters", [])
        if "3.4" in question_ids and "4.9" not in question_ids:
            hook_items = [
                item
                for item in hook_items
                if 1 <= int(item.get("chapter_ordinal") or 0) <= 3
            ]
        for item in _balanced_items(hook_items, "chapter_ordinal"):
            priority = (
                121 if int(item.get("chapter_ordinal") or 0) <= 3 else 118
            )
            materials.append((priority, "chapter_end_hook", item))
    opening_hook_payoffs = projection.get("opening_hook_payoffs_evidence")
    if (
        "3.4" in question_ids
        and isinstance(opening_hook_payoffs, dict)
        and opening_hook_payoffs.get("is_current") is not False
    ):
        payoff_metadata = {
            key: value
            for key, value in opening_hook_payoffs.items()
            if key != "hooks"
        }
        materials.append((126, "opening_hook_payoffs_summary", payoff_metadata))
        for item in opening_hook_payoffs.get("hooks", []):
            materials.append((125, "opening_hook_payoff", item))
    if question_ids == ("1.4",):
        protagonist = str((overview or {}).get("protagonist") or "").strip()
        normalized_protagonist = _normalized_person_name(protagonist)
        for item in projection.get("characters", []):
            names = [
                item.get("name"),
                *item.get("aliases", []),
            ]
            is_named_protagonist = bool(
                normalized_protagonist
                and any(
                    _normalized_person_name(name) == normalized_protagonist
                    for name in names
                )
            )
            if is_named_protagonist:
                materials.append((117, "protagonist", item))
            elif item.get("role") == "PROTAGONIST":
                materials.append((112, "protagonist", item))
        return materials
    if "2.1" in question_ids:
        opening_artifact = _opening_character_program_artifact(projection)
        first_event_ids = {
            str(item.get("first_action_event_id") or "")
            for item in opening_artifact["roles"]
            if isinstance(item, dict)
        }
        for item in projection.get("events", []):
            if str(item.get("id") or "") not in first_event_ids:
                continue
            materials.append((125, "opening_first_action_event", {
                key: item.get(key)
                for key in (
                    "id",
                    "title",
                    "chapter_ordinals",
                    "people",
                    "trigger",
                    "process",
                    "outcome",
                    "evidence_ids",
                )
            }))
        if question_ids == ("2.1",):
            return materials
    protagonist = str((overview or {}).get("protagonist") or "").strip()
    for item in projection.get("characters", []):
        if item.get("role") == "PROTAGONIST" or item.get("name") == protagonist:
            materials.append((112, "protagonist", item))
    for item in projection.get("phases", []):
        materials.append((88, "narrative_phase", item))
    for item in deep.get("foreshadowing", []):
        materials.append((96, "foreshadowing", item))
    for item in _balanced_items(deep.get("scene_analysis", []), "chapter_ordinal"):
        materials.append((86, "scene_analysis", item))
    for item in deep.get("claims", []):
        materials.append((82, "analysis_claim", item))
    for item in deep.get("world_rules", []):
        materials.append((76, "world_rule", item))
    for item in deep.get("conflicts", []):
        materials.append((74, "conflict", item))
    return materials




def _compact_opening_character_model_artifact(
    artifact: dict[str, object],
) -> dict[str, object]:
    """Keep only the stable key and context needed for the model's proposal."""

    return {
        "scope": artifact["scope"],
        "roles": [
            {
                key: item.get(key)
                for key in (
                    "sequence_no",
                    "character_name",
                    "first_action_chapter",
                    "first_action_event_id",
                    "first_action_event_title",
                    "first_action_evidence_ids",
                    "identity_summary",
                )
            }
            for item in artifact["roles"]
            if isinstance(item, dict)
        ],
    }


def _readiness_check(
    question_id: str,
    *,
    ready: bool,
    observed: dict[str, object],
    gaps: list[str],
    required_artifact: str,
    answer_scope: Literal["COMPLETE", "PARTIAL", "NOT_READY"] | None = None,
    source_material: object | None = None,
) -> dict[str, object]:
    resolved_scope = answer_scope or ("COMPLETE" if ready else "NOT_READY")
    fingerprint_material: object = (
        source_material if source_material is not None else observed
    )
    contract_version = LEARNING_QUESTION_CONTRACT_VERSIONS.get(question_id)
    if contract_version is not None:
        fingerprint_material = {
            "question_contract_version": contract_version,
            "source_material": fingerprint_material,
        }
    return {
        "question_id": question_id,
        "question": _QUESTION_BY_ID[question_id].question,
        "ready": ready,
        "answer_scope": resolved_scope,
        "source_fingerprint": hashlib.sha256(
            json.dumps(
                fingerprint_material,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest(),
        "observed": observed,
        "gaps": gaps,
        "required_artifact": required_artifact,
    }


def _evaluate_question_readiness(
    question_id: str,
    projection: dict[str, Any],
) -> dict[str, object]:
    plugin = get_question_plugin(question_id)
    eval_result = plugin.assess_readiness(projection)
    return _readiness_check(
        question_id,
        ready=bool(eval_result.get("ready")),
        observed=eval_result.get("observed", {}),
        gaps=list(eval_result.get("gaps", [])),
        required_artifact=str(eval_result.get("required_artifact") or ""),
        answer_scope=eval_result.get("answer_scope"),
        source_material=eval_result.get("source_material"),
    )


def assess_learning_report_readiness(projection: dict) -> dict[str, object]:
    """Classify each first-group question independently as complete, partial, or blocked."""

    chapters = projection.get("chapters", [])
    chapter_count = len(chapters)
    events = projection.get("events", [])
    overview = projection.get("story_overview") or {}
    characters = projection.get("characters", [])
    deep = projection.get("deep_analysis") or {}
    action_counts = _opening_action_counts(projection)

    checks: dict[str, dict[str, object]] = {}
    checks["1.4"] = _evaluate_question_readiness("1.4", projection)
    checks["3.1"] = _evaluate_question_readiness("3.1", projection)
    checks["3.2"] = _evaluate_question_readiness("3.2", projection)
    checks["2.1"] = _evaluate_question_readiness("2.1", projection)
    checks["2.2"] = _evaluate_question_readiness("2.2", projection)
    checks["4.9"] = _evaluate_question_readiness("4.9", projection)
    checks["3.4"] = _evaluate_question_readiness("3.4", projection)

    foreshadowing_ledger = projection.get("foreshadowing_ledger") or {}
    ledger_coverage = int(foreshadowing_ledger.get("covered_chapter_count") or 0)
    lifecycle_items = foreshadowing_ledger.get("lifecycles") or []
    valid_lifecycles = [
        item
        for item in lifecycle_items
        if item.get("setup_evidence_ids")
        and item.get("reinforcement_points") is not None
        and item.get("payoff_status")
    ]
    foreshadowing_gaps: list[str] = []
    if ledger_coverage < chapter_count:
        foreshadowing_gaps.append(
            f"伏笔专项账本只覆盖 {ledger_coverage}/{chapter_count} 章，不能判断未发现是否真实为零。"
        )
    if lifecycle_items and len(valid_lifecycles) != len(lifecycle_items):
        foreshadowing_gaps.append("已有伏笔条目缺少埋设、保温或回收状态中的至少一段。")
    if not foreshadowing_ledger:
        foreshadowing_gaps.append("现有深层伏笔候选不是覆盖全书的生命周期账本。")
    checks["5.3"] = _readiness_check(
        "5.3",
        ready=not foreshadowing_gaps,
        observed={
            "covered_chapter_count": ledger_coverage,
            "lifecycle_count": len(valid_lifecycles),
            "generic_foreshadowing_count": len(deep.get("foreshadowing", [])),
        },
        gaps=foreshadowing_gaps or ["当前只能给单书观察；跨书伏笔手法对比需要多书数据。"],
        required_artifact="全书伏笔埋设—保温—回收生命周期账本",
        answer_scope="PARTIAL" if not foreshadowing_gaps else "NOT_READY",
    )

    decision_dependencies = [checks[question_id] for question_id in ("1.4", "2.1", "2.2")]
    decision_gaps = [
        f"依赖问题 {item['question_id']} 的证据原料尚未就绪。"
        for item in decision_dependencies
        if not item["ready"]
    ]
    if not projection.get("phases"):
        decision_gaps.append("缺少覆盖全书的剧情阶段，无法区分前置决定与后期生长。")
    checks["6.4"] = _readiness_check(
        "6.4",
        ready=not decision_gaps,
        observed={
            "dependency_status": {
                item["question_id"]: item["ready"] for item in decision_dependencies
            },
            "phase_count": len(projection.get("phases", [])),
        },
        gaps=decision_gaps,
        required_artifact="作者前置决策的跨问题结算表",
    )

    method_dependencies = [checks[question_id] for question_id in ("1.4", "4.9", "5.3")]
    sample_metadata = projection.get("sample_metadata") or {}
    method_gaps = [
        f"依赖问题 {item['question_id']} 的证据原料尚未就绪。"
        for item in method_dependencies
        if not item["ready"]
    ]
    if not sample_metadata.get("platform") or not sample_metadata.get("commercial_model"):
        method_gaps.append("缺少平台与商业模式元数据，环境因素不能进入可复制性判断。")
    checks["6.5"] = _readiness_check(
        "6.5",
        ready=not method_gaps,
        observed={
            "dependency_status": {
                item["question_id"]: item["ready"] for item in method_dependencies
            },
            "has_platform": bool(sample_metadata.get("platform")),
            "has_commercial_model": bool(sample_metadata.get("commercial_model")),
        },
        gaps=method_gaps,
        required_artifact="单书方法候选三栏结算表",
    )

    # 1.5 金手指五要素规格 - 使用world_rules和events数据
    world_rules = deep.get("world_rules", [])
    world_rule_count = len(world_rules)
    _event_count_15 = len(events)
    gold_finger_gaps: list[str] = []
    if world_rule_count == 0:
        gold_finger_gaps.append("深层拆解尚未提取任何世界规则，无法核验金手指能力条款和代价。")
    if _event_count_15 < 3:
        gold_finger_gaps.append(f"事件数量只有 {_event_count_15} 个，不足以确认限制被真实触发。")
    if not str(overview.get("premise") or "").strip():
        gold_finger_gaps.append("缺少故事前提，无法确认金手指和主线的关系。")
    checks["1.5"] = _readiness_check(
        "1.5",
        ready=not gold_finger_gaps,
        observed={
            "world_rule_count": world_rule_count,
            "event_count": _event_count_15,
            "has_overview": bool(str(overview.get("premise") or "").strip()),
        },
        gaps=(
            gold_finger_gaps
            or ["限制触发需要事件证据；同类对照需要同品类多书数据；当前只能给单书观察。"]
        ),
        required_artifact="世界规则与事件列表（用于金手指规格分析）",
        answer_scope="PARTIAL" if not gold_finger_gaps else "NOT_READY",
        source_material={
            "world_rule_count": world_rule_count,
            "event_count": _event_count_15,
        },
    )

    # 2.3 配角功能分工矩阵 - 使用现有人物和事件数据
    non_trivial_characters = [c for c in characters if c.get("evidence_ids")]
    character_count = len(characters)
    char_gaps_23: list[str] = []
    if not str(overview.get("premise") or "").strip():
        char_gaps_23.append("缺少故事前提，无法区分主角与配角。")
    if character_count < 3:
        char_gaps_23.append(f"当前只识别到 {character_count} 名人物，不足以形成配角功能分析。")
    elif len(non_trivial_characters) < 2:
        char_gaps_23.append("多数人物缺少原文证据，无法确认叙事功能。")
    checks["2.3"] = _readiness_check(
        "2.3",
        ready=not char_gaps_23,
        observed={
            "character_count": character_count,
            "evidence_backed_character_count": len(non_trivial_characters),
            "has_overview_premise": bool(str(overview.get("premise") or "").strip()),
        },
        gaps=(
            char_gaps_23
            or ["缺少同品类配角功能对照基线；当前只能给单书观察，跨书规律待验证。"]
        ),
        required_artifact="人物列表与故事结构（用于配角功能分析）",
        answer_scope="PARTIAL" if not char_gaps_23 else "NOT_READY",
        source_material={
            "character_count": character_count,
            "non_trivial_character_count": len(non_trivial_characters),
            "has_overview": bool(overview.get("premise")),
        },
    )

    # 2.4 反派梯队表 - 使用现有人物、事件和故事结构数据
    event_count = len(events)
    villain_gaps: list[str] = []
    if not str(overview.get("premise") or "").strip():
        villain_gaps.append("缺少故事前提，无法推断主要对立力量。")
    if character_count < 2:
        villain_gaps.append("当前人物数量不足以形成反派梯队分析。")
    if event_count < 5:
        villain_gaps.append(f"当前只有 {event_count} 个事件，不足以定位反派供压节点。")
    checks["2.4"] = _readiness_check(
        "2.4",
        ready=not villain_gaps,
        observed={
            "character_count": character_count,
            "event_count": event_count,
            "has_overview_premise": bool(str(overview.get("premise") or "").strip()),
        },
        gaps=(
            villain_gaps
            or ["反派铺垫距离与实力差需要书内描写依据；当前只能给单书观察。"]
        ),
        required_artifact="人物、事件与故事结构（用于反派梯队分析）",
        answer_scope="PARTIAL" if not villain_gaps else "NOT_READY",
        source_material={
            "character_count": character_count,
            "event_count": event_count,
            "has_overview": bool(overview.get("premise")),
        },
    )

    # 2.9 势力圈层 - 使用现有人物、关系和事件数据
    relations = projection.get("relations", [])
    factions_gaps: list[str] = []
    if not str(overview.get("premise") or "").strip():
        factions_gaps.append("缺少故事前提，无法识别势力边界与主角路径。")
    if character_count < 3:
        factions_gaps.append(f"当前只有 {character_count} 名人物，不足以形成势力圈层分析。")
    if event_count < 5:
        factions_gaps.append(f"事件数量只有 {event_count} 个，不足以追踪主角在势力间的移动路径。")
    checks["2.9"] = _readiness_check(
        "2.9",
        ready=not factions_gaps,
        observed={
            "character_count": character_count,
            "event_count": event_count,
            "relation_count": len(relations),
        },
        gaps=(
            factions_gaps
            or ["势力详略与晋级速度只能给单书观察；跨书圈层结构比较需要多书数据。"]
        ),
        required_artifact="人物、关系与事件列表（用于势力圈层分析）",
        answer_scope="PARTIAL" if not factions_gaps else "NOT_READY",
        source_material={
            "character_count": character_count,
            "event_count": event_count,
            "relation_count": len(relations),
        },
    )

    # 2.10 力量体系阶梯 - 使用world_rules和人物数据
    _event_count_210 = len(events)
    _world_rule_count_210 = len(world_rules)
    power_gaps: list[str] = []
    if _world_rule_count_210 == 0:
        power_gaps.append("深层拆解尚未提取任何世界规则，无法识别力量体系约束和代价。")
    if character_count < 2:
        power_gaps.append("人物数量不足，无法形成有意义的战力层级对比。")
    if _event_count_210 < 5:
        power_gaps.append(f"事件数量只有 {_event_count_210} 个，不足以确认硬规则是否真实被执行。")
    checks["2.10"] = _readiness_check(
        "2.10",
        ready=not power_gaps,
        observed={
            "world_rule_count": _world_rule_count_210,
            "character_count": character_count,
            "event_count": _event_count_210,
        },
        gaps=(
            power_gaps
            or ["力量上限和代价需要书内执行证据；同类力量体系对比需要多书数据。"]
        ),
        required_artifact="世界规则与人物状态（用于力量体系分析）",
        answer_scope="PARTIAL" if not power_gaps else "NOT_READY",
        source_material={
            "world_rule_count": _world_rule_count_210,
            "character_count": character_count,
        },
    )

    # 4.10 悬念账本 - 复用 foreshadowing_ledger（与 5.3 共享数据）
    foreshadowing_ledger_410 = projection.get("foreshadowing_ledger") or {}
    ledger_coverage_410 = int(foreshadowing_ledger_410.get("covered_chapter_count") or 0)
    suspense_gaps: list[str] = []
    if ledger_coverage_410 == 0:
        suspense_gaps.append("全书伏笔生命周期账本尚未生成，无法系统追踪悬念埋设和兑现。")
    if event_count < 5:
        suspense_gaps.append(f"只有 {event_count} 个事件，不足以定位悬念的关键转折节点。")
    checks["4.10"] = _readiness_check(
        "4.10",
        ready=not suspense_gaps,
        observed={
            "foreshadowing_ledger_coverage": ledger_coverage_410,
            "event_count": event_count,
        },
        gaps=(
            suspense_gaps
            or ["悬念兑现质量和存量曲线需要全书覆盖；当前只能给单书观察。"]
        ),
        required_artifact="伏笔生命周期账本（用于悬念存量与兑现节奏分析）",
        answer_scope="PARTIAL" if not suspense_gaps else "NOT_READY",
        source_material={
            "foreshadowing_ledger_coverage": ledger_coverage_410,
        },
    )

    # 4.4 主支线配比 - 使用事件和剧情阶段数据
    phases = projection.get("phases", [])
    phase_count = len(phases)
    subplot_gaps: list[str] = []
    if event_count < 8:
        subplot_gaps.append(f"只有 {event_count} 个事件，不足以识别支线模式。")
    if phase_count < 2:
        subplot_gaps.append(f"只有 {phase_count} 个剧情阶段，不足以分析跨阶段的主支线交替节奏。")
    if not str(overview.get("premise") or "").strip():
        subplot_gaps.append("缺少故事前提，无法区分主线与支线事件。")
    checks["4.4"] = _readiness_check(
        "4.4",
        ready=not subplot_gaps,
        observed={
            "event_count": event_count,
            "phase_count": phase_count,
            "has_overview": bool(str(overview.get("premise") or "").strip()),
        },
        gaps=(
            subplot_gaps
            or ["主支线配比只能给单书观察；跨书规律需要多书同口径数据。"]
        ),
        required_artifact="事件与剧情阶段列表（用于主支线配比分析）",
        answer_scope="PARTIAL" if not subplot_gaps else "NOT_READY",
        source_material={
            "event_count": event_count,
            "phase_count": phase_count,
        },
    )

    # 2.8 世界级终极悬念 - 使用 foreshadowing + story_overview
    foreshadowing_items = deep.get("foreshadowing", [])
    ultimate_gaps: list[str] = []
    if not str(overview.get("premise") or "").strip():
        ultimate_gaps.append("缺少故事前提，无法判断什么是'世界级'终极悬念。")
    if event_count < 5:
        ultimate_gaps.append(f"只有 {event_count} 个事件，不足以追踪终极悬念的分段揭示。")
    checks["2.8"] = _readiness_check(
        "2.8",
        ready=not ultimate_gaps,
        observed={
            "foreshadowing_item_count": len(foreshadowing_items),
            "event_count": event_count,
            "has_overview": bool(str(overview.get("premise") or "").strip()),
        },
        gaps=(
            ultimate_gaps
            or ["终极悬念的识别依赖对全书结构的理解；当前只能给单书观察，"
                "多书规律需要跨书数据。"]
        ),
        required_artifact="深层拆解伏笔与事件列表（用于终极悬念分段分析）",
        answer_scope="PARTIAL" if not ultimate_gaps else "NOT_READY",
        source_material={
            "foreshadowing_item_count": len(foreshadowing_items),
            "event_count": event_count,
        },
    )

    ordered_checks = [
        checks[question_id] for question_id in INITIAL_INCREMENTAL_QUESTION_IDS
    ]
    blocking_checks = [item for item in ordered_checks if not item["ready"]]
    generation_ready_question_ids = [
        str(item["question_id"]) for item in ordered_checks if item["ready"]
    ]
    return {
        "ready": bool(generation_ready_question_ids),
        "policy": "按问题独立生成；部分答案明确缺口，无关问题不得阻塞。",
        "ready_question_count": len(generation_ready_question_ids),
        "complete_question_count": sum(
            item["answer_scope"] == "COMPLETE" for item in ordered_checks
        ),
        "partial_question_count": sum(
            item["answer_scope"] == "PARTIAL" for item in ordered_checks
        ),
        "total_question_count": len(LEARNING_QUESTION_CATALOG),
        "assessed_question_count": len(ordered_checks),
        "generation_ready_question_ids": generation_ready_question_ids,
        "checks": ordered_checks,
        "next_required_artifacts": list(dict.fromkeys(
            str(item["required_artifact"]) for item in blocking_checks
        )),
    }


class LearningReportNotReadyError(ValueError):
    def __init__(self, readiness: dict[str, object]) -> None:
        super().__init__("LEARNING_REPORT_DATA_NOT_READY")
        self.readiness = readiness


def _report_uses_current_contract(report: LearningReport) -> bool:
    if report.prompt_version not in LEARNING_REPORT_COMPATIBLE_PROMPT_VERSIONS:
        return False
    try:
        payload = json.loads(report.payload_json)
    except (TypeError, json.JSONDecodeError):
        return False
    return payload.get("catalog_version") == LEARNING_QUESTION_CATALOG_VERSION


def _answer_uses_current_contract(
    payload: dict[str, object],
    question_id: str,
) -> bool:
    versions = payload.get("question_contract_versions") or {}
    if not isinstance(versions, dict):
        return False
    return versions.get(question_id, "1.0.0") == (
        LEARNING_QUESTION_CONTRACT_VERSIONS.get(question_id, "1.0.0")
    )


def provider_payload_for_learning_report(
    session: Session,
    settings: Settings,
    task_payload: dict,
) -> dict:
    from .workbench import build_workbench_projection

    run_id = str(task_payload.get("run_id") or "")
    version = session.get(SourceVersion, task_payload.get("source_version_id"))
    deep = session.scalar(
        select(DeepAnalysis)
        .where(DeepAnalysis.run_id == run_id)
        .order_by(DeepAnalysis.revision_no.desc())
    )
    if not run_id or version is None or deep is None:
        raise ValueError("DEEP_ANALYSIS_NOT_READY")
    expected_deep_revision = int(task_payload.get("source_deep_revision") or 0)
    if deep.revision_no != expected_deep_revision:
        raise ValueError("LEARNING_REPORT_SOURCE_OUTDATED")
    projection = build_workbench_projection(session, run_id)
    readiness = assess_learning_report_readiness(projection)
    requested_question_ids = tuple(
        str(question_id) for question_id in task_payload.get("question_ids", [])
    )
    if (
        len(requested_question_ids) != 1
        or len(set(requested_question_ids)) != len(requested_question_ids)
        or not set(requested_question_ids).issubset(INITIAL_INCREMENTAL_QUESTION_IDS)
    ):
        raise ValueError("LEARNING_REPORT_QUESTION_SELECTION_INVALID")
    readiness_by_id = {
        str(item["question_id"]): item for item in readiness["checks"]
    }
    requested_fingerprints = task_payload.get("question_source_fingerprints") or {}
    if any(
        not readiness_by_id[question_id]["ready"]
        for question_id in requested_question_ids
    ):
        raise LearningReportNotReadyError(readiness)
    if any(
        requested_fingerprints.get(question_id)
        != readiness_by_id[question_id]["source_fingerprint"]
        for question_id in requested_question_ids
    ):
        raise ValueError("LEARNING_REPORT_SOURCE_OUTDATED")
    _service, profile = resolve_analysis_profile(
        settings,
        str(task_payload.get("model_profile_id") or ENTITIES_EVENTS_PROFILE_ID),
    )
    chapter_by_unit_id = _chapter_index_by_unit_id(session, version.id)
    source_materials = _source_materials(projection, requested_question_ids)
    opening_promise_sources: dict[str, object] | None = None
    if requested_question_ids == ("1.4",):
        opening_promise_sources = _opening_promise_source_artifact(
            session,
            settings,
            version,
        )
        source_materials.append((
            130,
            "opening_promise_sources",
            opening_promise_sources,
        ))
    all_evidence_ids = _evidence_ids({
        "story_overview": projection.get("story_overview"),
        "characters": projection.get("characters", []),
        "materials": [item for _priority, _kind, item in source_materials],
    })
    evidence_by_id = {
        item.id: item
        for item in session.scalars(
            select(EvidenceSpan).where(
                EvidenceSpan.source_version_id == version.id,
                EvidenceSpan.id.in_(all_evidence_ids),
            )
        )
    }
    question_catalog = [
        {
            "question_id": question_id,
            "question": _QUESTION_BY_ID[question_id].question,
            "analysis_requirement": _QUESTION_BY_ID[question_id].analysis_requirement,
            "output_contract": _QUESTION_BY_ID[question_id].output_contract,
            "measurement_requirements": _QUESTION_BY_ID[question_id].measurement_requirements,
            "evidence_requirements": _QUESTION_BY_ID[question_id].evidence_requirements,
            "scope_requirement": _QUESTION_BY_ID[question_id].scope_requirement,
            "external_data_policy": _QUESTION_BY_ID[question_id].external_data_policy,
            "validation_policy": {
                "version": LEARNING_VALIDATION_POLICY_VERSION,
                "objective_checks": (
                    _QUESTION_BY_ID[
                        question_id
                    ].objective_validation_policy
                ),
                "literary_judgments": (
                    _QUESTION_BY_ID[
                        question_id
                    ].literary_judgment_policy
                ),
            },
        }
        for question_id in requested_question_ids
    ]
    for item in question_catalog:
        item["answer_scope"] = readiness_by_id[item["question_id"]]["answer_scope"]
        item["known_gaps"] = readiness_by_id[item["question_id"]]["gaps"]
        item["required_contract_items"] = [
            {
                "item_id": definition.item_id,
                "label": definition.label,
                "requirement": definition.requirement,
            }
            for definition in LEARNING_QUESTION_MODEL_ITEM_CONTRACTS.get(
                item["question_id"],
                LEARNING_QUESTION_ITEM_CONTRACTS.get(item["question_id"], ()),
            )
        ]
    compact_action_counts = [
        {
            key: item[key]
            for key in (
                "through_chapter",
                "active_character_count",
                "event_count",
                "method",
            )
        }
        for item in _opening_action_counts(projection)
    ]
    question_id = requested_question_ids[0]
    include_chapter_catalog = question_id in {"1.4", "2.1", "3.4", "4.9"}
    fixed_input: dict[str, object] = {
        "catalog_version": LEARNING_QUESTION_CATALOG_VERSION,
        "question_contract_version": LEARNING_QUESTION_CONTRACT_VERSIONS.get(
            question_id,
            "1.0.0",
        ),
        "batch_label": LEARNING_REPORT_BATCH_LABEL,
        "generation_policy": (
            "本次只回答 question_catalog 中唯一一问；"
            "PARTIAL 必须保留已知缺口，不得借用其他问题的就绪状态。"
        ),
        "validation_policy": {
            "version": LEARNING_VALIDATION_POLICY_VERSION,
            "objective_checks": LEARNING_OBJECTIVE_VALIDATION_POLICY,
            "literary_judgments": LEARNING_LITERARY_JUDGMENT_POLICY,
        },
        "question_catalog": question_catalog,
    }
    if include_chapter_catalog:
        fixed_input["chapter_catalog"] = [
            {
                "ordinal": int(item.get("ordinal") or 0),
                "title": item.get("title"),
            }
            for item in sorted(
                chapter_by_unit_id.values(),
                key=lambda item: int(item.get("ordinal") or 0),
            )
        ]
    if question_id == "1.4":
        if opening_promise_sources is None:
            raise ValueError("LEARNING_REPORT_1_4_PROMISE_SOURCE_MISSING")
        payoff_ledger = (
            projection.get("opening_payoff_candidates_evidence") or {}
        )
        if (
            not isinstance(payoff_ledger, dict)
            or payoff_ledger.get("is_current") is False
            or not payoff_ledger.get("coverage")
        ):
            raise ValueError(
                "LEARNING_REPORT_1_4_PAYOFF_LEDGER_NOT_READY"
            )
        payoff_rows = [
            item
            for item in payoff_ledger.get("classifications", [])
            if isinstance(item, dict)
        ]
        selected_payoff = payoff_ledger.get("selected")
        selected_sequence = int(
            (selected_payoff or {}).get("sequence_no") or 0
        )
        representative_rows = [
            item
            for item in payoff_rows
            if (
                int(item.get("chapter_ordinal") or 0) <= 3
                or (
                    selected_sequence > 0
                    and selected_sequence - 3
                    <= int(item.get("sequence_no") or 0)
                    <= selected_sequence
                )
            )
        ]
        fixed_input["program_artifacts"] = {
            "opening_payoff_candidate_ledger": {
                "coverage": payoff_ledger["coverage"],
                "selected": selected_payoff,
                "representative_classifications": (
                    representative_rows[:24]
                ),
                "program_policy": (
                    "专项已按全书事件顺序连续分窗；"
                    "用户答案不得重做分类或改选位置。"
                ),
            },
        }
    if question_id == "2.1":
        opening_character_artifact = _opening_character_program_artifact(projection)
        fixed_input["program_metrics"] = {
            "opening_action_character_counts": compact_action_counts,
        }
        fixed_input["program_artifacts"] = {
            "opening_character_ledger": _compact_opening_character_model_artifact(
                opening_character_artifact
            ),
        }
    fixed_chars = len(
        json.dumps(fixed_input, ensure_ascii=False, separators=(",", ":"), default=str)
    )
    request_budget = _request_budget(profile)
    material_budget = max(
        0,
        int(request_budget["input_char_budget"]) - fixed_chars - 4_000,
    )
    ranked_bundles: list[tuple[int, int, str, dict]] = []
    for order, (priority, kind, item) in enumerate(source_materials):
        bundle = _material_bundle(kind, item, evidence_by_id, chapter_by_unit_id)
        ranked_bundles.append((priority, order, kind, bundle))
    ranked_bundles.sort(key=lambda entry: (-entry[0], entry[1]))
    selected: list[dict] = []
    omitted: list[dict[str, object]] = []
    used_chars = 0
    selected_by_kind: dict[str, int] = {}
    for _priority, _order, kind, bundle in ranked_bundles:
        size = len(
            json.dumps(bundle, ensure_ascii=False, separators=(",", ":"), default=str)
        )
        if used_chars + size <= material_budget:
            selected.append(bundle)
            used_chars += size
            selected_by_kind[kind] = selected_by_kind.get(kind, 0) + 1
        else:
            omitted.append({"kind": kind, "reason": "当前模型上下文预算不足"})
    input_payload = {
        **fixed_input,
        "materials": selected,
        "coverage_manifest": {
            "question_id": question_id,
            **request_budget,
            "material_budget_chars": material_budget,
            "selected_count": len(selected),
            "selected_chars": used_chars,
            "selected_by_kind": selected_by_kind,
            "omitted_count": len(omitted),
            "omitted_by_kind": {
                kind: sum(1 for item in omitted if item["kind"] == kind)
                for kind in sorted({str(item["kind"]) for item in omitted})
            },
        },
    }
    return {
        "instructions": _prompt(),
        "input": json.dumps(
            input_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ),
        "output_schema": _inline_model_schema(LearningReportOutput),
        "model_profile_id": str(task_payload.get("model_profile_id") or ENTITIES_EVENTS_PROFILE_ID),
        "prompt_id": LEARNING_REPORT_PROMPT_ID,
        "prompt_version": LEARNING_REPORT_PROMPT_VERSION,
        "source_version_id": version.id,
        "source_char_start": 0,
        "source_char_end": version.total_chars,
        "context_manifest": input_payload["coverage_manifest"],
    }


def _answer_user_visible_texts(
    answer: LearningAnswerProposal,
) -> list[str]:
    return [
        answer.conclusion,
        answer.handbook.why_important if answer.handbook else "",
        *(answer.handbook.universal_methods if answer.handbook else []),
        *(item.mistake for item in (answer.handbook.common_errors if answer.handbook else [])),
        *(item.fix for item in (answer.handbook.common_errors if answer.handbook else [])),
        *(
            checkpoint
            for template in (answer.handbook.templates if answer.handbook else [])
            for checkpoint in template.checkpoints
        ),
        *answer.limitations,
        *answer.reusable_lessons,
        *answer.do_not_copy,
        *(
            text
            for item in answer.contract_items
            for text in (
                item.finding,
                *item.limitations,
                *(
                    value
                    for metric in item.metrics
                    for value in (
                        metric.label,
                        metric.value,
                        metric.unit,
                        metric.method,
                    )
                ),
            )
        ),
        *(
            value
            for metric in answer.metrics
            for value in (
                metric.label,
                metric.value,
                metric.unit,
                metric.method,
            )
        ),
    ]


def _answer_external_causality_texts(
    answer: LearningAnswerProposal,
) -> list[str]:
    """Return the texts that are subject to the external-causality check.

    The entire ``handbook`` section contains craft-technique descriptions
    for writers and naturally uses narrative/reader terms such as
    "读者期待", "激发读者的好奇心", etc. to describe story mechanics,
    not to make market-effect claims.  All handbook sub-fields are
    excluded here.  The pricing-term check still covers all visible
    texts via _answer_user_visible_texts.

    Fields kept:
      - answer.conclusion
      - answer.limitations
      - answer.reusable_lessons  (checked — may contain market claims)
      - answer.do_not_copy       (checked — may contain market claims)
      - contract_items findings, limitations, metrics
      - answer-level metrics

    Fields excluded from the causality check:
      - All handbook sub-fields (craft-technique writing advice that
        naturally uses narrative-level "读者" language to describe
        story mechanics rather than market outcomes)
    """
    return [
        answer.conclusion,
        *answer.limitations,
        *answer.reusable_lessons,
        *answer.do_not_copy,
        *(
            text
            for item in answer.contract_items
            for text in (
                item.finding,
                *item.limitations,
                *(
                    value
                    for metric in item.metrics
                    for value in (
                        metric.label,
                        metric.value,
                        metric.unit,
                        metric.method,
                    )
                ),
            )
        ),
        *(
            value
            for metric in answer.metrics
            for value in (
                metric.label,
                metric.value,
                metric.unit,
                metric.method,
            )
        ),
    ]


def _split_user_text_clauses(text: str) -> list[str]:
    return [
        clause.strip()
        for clause in _USER_TEXT_CLAUSE_BREAK.split(text)
        if clause.strip()
    ]


def parse_learning_report(
    value: dict,
    *,
    expected_question_ids: tuple[str, ...] | list[str] | None = None,
    defer_user_text_checks_for: set[str] | None = None,
) -> LearningReportOutput:
    try:
        output = LearningReportOutput.model_validate(value)
    except ValidationError as exc:
        raise LearningReportValidationError(
            "LEARNING_REPORT_OUTPUT_INVALID", _validation_errors(exc)
        ) from exc
    question_ids = [item.question_id for item in output.answers]
    if len(set(question_ids)) != len(question_ids):
        raise LearningReportValidationError(
            "LEARNING_REPORT_QUESTION_DUPLICATED",
            [{"path": ["answers"], "type": "value_error", "message": "同一问题只能回答一次"}],
        )
    expected = tuple(expected_question_ids or INITIAL_INCREMENTAL_QUESTION_IDS)
    if set(question_ids) != set(expected) or len(question_ids) != len(expected):
        raise LearningReportValidationError(
            "LEARNING_REPORT_QUESTION_COVERAGE_INVALID",
            [{
                "path": ["answers"],
                "type": "value_error",
                "message": f"必须恰好回答本次提交的 {len(expected)} 个问题",
            }],
        )
    if output.author_decisions or output.method_candidates:
        raise LearningReportValidationError(
            "LEARNING_REPORT_OUT_OF_SCOPE_SUMMARY",
            [{
                "path": ["author_decisions", "method_candidates"],
                "type": "value_error",
                "message": "首组逐问答案不得提前生成作者决策或方法候选汇总",
            }],
        )
    deferred = defer_user_text_checks_for or set()
    for answer in output.answers:
        if answer.question_id not in deferred:
            _validate_answer_user_text_boundaries(answer)
        definitions = LEARNING_QUESTION_MODEL_ITEM_CONTRACTS.get(
            answer.question_id,
            LEARNING_QUESTION_ITEM_CONTRACTS.get(answer.question_id),
        )
        if not definitions:
            continue
        expected_item_ids = [item.item_id for item in definitions]
        actual_item_ids = [item.item_id for item in answer.contract_items]
        if (
            actual_item_ids != expected_item_ids
            or len(set(actual_item_ids)) != len(actual_item_ids)
        ):
            raise LearningReportValidationError(
                "LEARNING_REPORT_CONTRACT_ITEM_COVERAGE_INVALID",
                [{
                    "path": ["answers", answer.question_id, "contract_items"],
                    "type": "value_error",
                    "message": (
                        "必须按规定顺序逐项返回问题合同："
                        f"{'、'.join(expected_item_ids)}"
                    ),
                }],
            )
        insufficient_items = [
            item.item_id
            for item in answer.contract_items
            if item.status == "INSUFFICIENT_EVIDENCE"
        ]
        unsupported_items_without_support = [
            item.item_id
            for item in answer.contract_items
            if (
                item.status == "SUPPORTED"
                and not item.evidence_ids
                and not item.metrics
                and not item.classifications
                and not item.payoff_classifications
                and item.item_id not in {
                    "first_scene_functions",
                    "scope_boundary",
                    # 4.9 statistical items derive from the chapter_end_hooks
                    # ledger (counts/ratios), not from original-text evd_ spans.
                    "chapter_coverage",
                    "type_distribution",
                    "strength_rhythm",
                    "type_rotation",
                    "no_hook_analysis",
                }
            )
        ]
        if unsupported_items_without_support:
            raise LearningReportValidationError(
                "LEARNING_REPORT_CONTRACT_ITEM_SUPPORT_MISSING",
                [{
                    "path": [
                        "answers",
                        answer.question_id,
                        "contract_items",
                    ],
                    "type": "value_error",
                    "message": (
                        "标为已支持的合同项目必须带原文、可核查指标或程序分类："
                        f"{'、'.join(unsupported_items_without_support)}"
                    ),
                }],
            )
        if (
            insufficient_items
            and answer.status == "ANSWERED"
            and answer.question_id not in {"1.4", "2.1", "2.2", "4.9"}
        ):
            raise LearningReportValidationError(
                "LEARNING_REPORT_CONTRACT_STATUS_INCONSISTENT",
                [{
                    "path": ["answers", answer.question_id, "status"],
                    "type": "value_error",
                    "message": (
                        "仍有合同项目证据不足时，整问不能标为已完整回答："
                        f"{'、'.join(insufficient_items)}"
                    ),
                }],
            )
        if answer.question_id == "1.4":
            item_by_id = {
                item.item_id: item for item in answer.contract_items
            }
            if (
                item_by_id["cross_book_comparison"].status
                != "INSUFFICIENT_EVIDENCE"
            ):
                raise LearningReportValidationError(
                    "LEARNING_REPORT_1_4_CROSS_BOOK_SCOPE_INVALID",
                    [{
                        "path": [
                            "answers",
                            answer.question_id,
                            "contract_items",
                            "cross_book_comparison",
                        ],
                        "type": "value_error",
                        "message": "没有同口径多书数据时不得生成同类书卖点对比。",
                    }],
                )
        if answer.question_id == "2.1":
            item_by_id = {
                item.item_id: item for item in answer.contract_items
            }
            function_item = item_by_id["first_scene_functions"]
            if (
                function_item.status != "SUPPORTED"
                or not function_item.classifications
            ):
                raise LearningReportValidationError(
                    "LEARNING_REPORT_2_1_FIRST_FUNCTIONS_MISSING",
                    [{
                        "path": [
                            "answers",
                            answer.question_id,
                            "contract_items",
                            "first_scene_functions",
                        ],
                        "type": "value_error",
                        "message": "2.1 必须逐人返回首场功能分类，不能只给人数。",
                    }],
                )
    return output




def _validate_selected_answers_against_projection(
    output: LearningReportOutput,
    projection: dict,
    *,
    opening_promise_sources: dict[str, object] | None = None,
    evidence_by_id: dict[str, EvidenceSpan] | None = None,
    chapter_by_unit_id: dict[str, dict[str, object]] | None = None,
) -> None:
    errors: list[dict[str, Any]] = []
    for answer in output.answers:
        plugin = get_question_plugin(answer.question_id)
        plugin.validate_answer(
            answer,
            projection,
            errors,
            opening_promise_sources=opening_promise_sources,
            evidence_by_id=evidence_by_id,
            chapter_by_unit_id=chapter_by_unit_id,
        )


def _learning_report_json_default(value: object) -> str:
    if hasattr(value, "isoformat"):  # handles datetime, date, time regardless of import path
        return value.isoformat()  # type: ignore[union-attr]
    raise TypeError(
        f"Object of type {type(value).__name__} is not JSON serializable"
    )


def _serialize_learning_report_payload(payload: dict[str, object]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        default=_learning_report_json_default,
    )


def persist_learning_report(
    session: Session,
    *,
    settings: Settings,
    task: Task,
    attempt_id: str,
    task_payload: dict,
    output: LearningReportOutput,
) -> PersistedLearningReport:
    run = session.get(AnalysisRun, task_payload.get("run_id"))
    if run is None:
        raise ValueError("ANALYSIS_RUN_NOT_FOUND")
    deep = session.scalar(
        select(DeepAnalysis)
        .where(DeepAnalysis.run_id == run.id)
        .order_by(DeepAnalysis.revision_no.desc())
    )
    source_deep_revision = int(task_payload.get("source_deep_revision") or 0)
    if deep is None or deep.revision_no != source_deep_revision:
        raise ValueError("LEARNING_REPORT_SOURCE_OUTDATED")
    selected_question_ids = tuple(
        str(question_id) for question_id in task_payload.get("question_ids", [])
    )
    projection: dict | None = None
    program_1_4_artifact: dict[str, object] | None = None
    if set(selected_question_ids).intersection(
        get_projection_question_ids()
    ):
        from .workbench import build_workbench_projection

        projection = build_workbench_projection(session, run.id)
    if projection is not None:
        opening_promise_sources: dict[str, object] | None = None
        validation_evidence_by_id: dict[str, EvidenceSpan] = {}
        validation_chapter_by_unit_id: dict[
            str, dict[str, object]
        ] = {}
        if "1.4" in selected_question_ids:
            opening_promise_sources = _opening_promise_source_artifact(
                session,
                settings,
                run.source_version,
            )
            validation_evidence_ids = _evidence_ids({
                "opening_promise_sources": opening_promise_sources,
                "opening_events": projection.get("events", []),
            })
            validation_evidence_by_id = {
                evidence.id: evidence
                for evidence in session.scalars(
                    select(EvidenceSpan).where(
                        EvidenceSpan.source_version_id == run.source_version_id,
                        EvidenceSpan.id.in_(validation_evidence_ids),
                    )
                )
            }
            validation_chapter_by_unit_id = _chapter_index_by_unit_id(
                session,
                run.source_version_id,
            )
            answer_1_4 = next(
                answer
                for answer in output.answers
                if answer.question_id == "1.4"
            )
            program_1_4_artifact = _program_1_4_answer(
                answer_1_4,
                projection,
                opening_promise_sources=opening_promise_sources,
                evidence_by_id=validation_evidence_by_id,
                chapter_by_unit_id=validation_chapter_by_unit_id,
            )
        for answer in output.answers:
            if answer.question_id in selected_question_ids:
                plugin = get_question_plugin(answer.question_id)
                if plugin.is_program_compiled:
                    plugin.apply_program_answer(output, projection)
        _validate_selected_answers_against_projection(
            output,
            projection,
            opening_promise_sources=opening_promise_sources,
            evidence_by_id=validation_evidence_by_id,
            chapter_by_unit_id=validation_chapter_by_unit_id,
        )
    for answer in output.answers:
        _validate_answer_user_text_boundaries(answer)

    existing = session.scalar(
        select(LearningReport).where(LearningReport.created_by_task_id == task.id)
    )
    previous_report = session.scalar(
        select(LearningReport)
        .where(
            LearningReport.run_id == run.id,
            LearningReport.created_by_task_id != task.id,
        )
        .order_by(LearningReport.revision_no.desc())
    )
    previous_payload: dict[str, object] = {}
    if (
        previous_report is not None
        and previous_report.source_deep_revision == source_deep_revision
        and _report_uses_current_contract(previous_report)
    ):
        previous_payload = json.loads(previous_report.payload_json)
    current_fingerprints = task_payload.get("current_question_source_fingerprints") or {}
    previous_fingerprints = previous_payload.get("question_source_fingerprints") or {}
    merged_answers = {
        str(item["question_id"]): item
        for item in previous_payload.get("answers", [])
        if (
            isinstance(item, dict)
            and item.get("question_id") not in selected_question_ids
            and _answer_uses_current_contract(
                previous_payload,
                str(item.get("question_id")),
            )
            and previous_fingerprints.get(str(item.get("question_id")))
            == current_fingerprints.get(str(item.get("question_id")))
        )
    }
    merged_answers.update({
        answer.question_id: answer.model_dump(mode="json") for answer in output.answers
    })
    ordered_answer_ids = [
        item.question_id
        for item in LEARNING_QUESTION_CATALOG
        if item.question_id in merged_answers
    ]
    payload = {
        "answers": [merged_answers[question_id] for question_id in ordered_answer_ids],
        "author_decisions": output.model_dump(mode="json")["author_decisions"],
        "method_candidates": output.model_dump(mode="json")["method_candidates"],
    }
    if "1.4" in selected_question_ids:
        if program_1_4_artifact is None:
            raise ValueError("LEARNING_REPORT_PROGRAM_PROJECTION_MISSING")
        persisted_answer = next(
            answer
            for answer in payload["answers"]
            if answer["question_id"] == "1.4"
        )
        persisted_answer["program_artifacts"] = {
            "opening_payoff_candidate_ledger": program_1_4_artifact,
        }
    if "2.1" in selected_question_ids:
        if projection is None:
            raise ValueError("LEARNING_REPORT_PROGRAM_PROJECTION_MISSING")
        answer_model = next(
            answer for answer in output.answers if answer.question_id == "2.1"
        )
        artifact, program_metrics = _validated_2_1_program_artifact(
            answer_model,
            projection,
        )
        persisted_answer = next(
            answer
            for answer in payload["answers"]
            if answer["question_id"] == "2.1"
        )
        persisted_answer["program_artifacts"] = {
            "opening_character_ledger": artifact,
        }
        persisted_answer["contract_items"] = _program_2_1_contract_items(
            artifact
        )
        persisted_answer["evidence_ids"] = (
            _program_2_1_representative_evidence_ids(artifact)
        )
        persisted_answer["metrics"] = program_metrics
        persisted_answer.update(_program_2_1_reading_fields(artifact))
    for question_id in ("3.1", "3.2"):
        if question_id not in selected_question_ids:
            continue
        if projection is None:
            raise ValueError("LEARNING_REPORT_PROGRAM_PROJECTION_MISSING")
        persisted_answer = next(
            answer
            for answer in payload["answers"]
            if answer["question_id"] == question_id
        )
        persisted_answer["program_artifacts"] = {
            "opening_structure_ledger": (
                projection.get("opening_structure_evidence") or {}
            ),
        }
    if "4.9" in selected_question_ids:
        if projection is None:
            raise ValueError("LEARNING_REPORT_PROGRAM_PROJECTION_MISSING")
        persisted_answer = next(
            answer
            for answer in payload["answers"]
            if answer["question_id"] == "4.9"
        )
        persisted_answer["program_artifacts"] = {
            "chapter_end_hook_matrix": _program_4_9_matrix(
                projection.get("chapter_end_hooks_evidence") or {}
            ),
        }
    referenced_evidence_ids = _evidence_ids(payload)
    valid_evidence_ids = set(session.scalars(
        select(EvidenceSpan.id).where(
            EvidenceSpan.source_version_id == run.source_version_id,
            EvidenceSpan.id.in_(referenced_evidence_ids),
        )
    ))
    if referenced_evidence_ids != valid_evidence_ids:
        raise ValueError("LEARNING_REPORT_EVIDENCE_REFERENCE_INVALID")
    for answer in payload["answers"]:
        if answer["status"] in {"ANSWERED", "PARTIAL"}:
            if not answer["evidence_ids"]:
                raise ValueError("LEARNING_REPORT_ANSWER_EVIDENCE_MISSING")
            if not answer["metrics"]:
                raise ValueError("LEARNING_REPORT_METRIC_MISSING")
    payload.update({
        "catalog_version": LEARNING_QUESTION_CATALOG_VERSION,
        "batch_label": LEARNING_REPORT_BATCH_LABEL,
        "generated_question_ids": ordered_answer_ids,
        "question_source_fingerprints": {
            question_id: current_fingerprints[question_id]
            for question_id in ordered_answer_ids
        },
        "question_contract_versions": {
            question_id: LEARNING_QUESTION_CONTRACT_VERSIONS.get(
                question_id,
                "1.0.0",
            )
            for question_id in ordered_answer_ids
        },
        "source_deep_revision": source_deep_revision,
    })
    payload_json = _serialize_learning_report_payload(payload)
    if existing is None:
        revision_no = (session.scalar(
            select(func.max(LearningReport.revision_no)).where(LearningReport.run_id == run.id)
        ) or 0) + 1
        existing = LearningReport(
            run_id=run.id,
            source_version_id=run.source_version_id,
            revision_no=revision_no,
            source_deep_revision=source_deep_revision,
            payload_json=payload_json,
            prompt_id=LEARNING_REPORT_PROMPT_ID,
            prompt_version=LEARNING_REPORT_PROMPT_VERSION,
            created_by_task_id=task.id,
            created_by_attempt_id=attempt_id,
        )
        session.add(existing)
    else:
        existing.payload_json = payload_json
        existing.created_by_attempt_id = attempt_id
    session.commit()
    session.refresh(existing)
    return PersistedLearningReport(existing.id)


def enqueue_learning_report(
    session: Session,
    settings: Settings,
    run: AnalysisRun,
    *,
    force: bool = False,
    only_question_ids: tuple[str, ...] | None = None,
) -> Task | None:
    deep = session.scalar(
        select(DeepAnalysis)
        .where(DeepAnalysis.run_id == run.id)
        .order_by(DeepAnalysis.revision_no.desc())
    )
    if deep is None:
        return None
    from .workbench import build_workbench_projection

    projection = build_workbench_projection(session, run.id)
    payoff_task: Task | None = None
    if (
        only_question_ids is None
        or "1.4" in only_question_ids
    ) and projection.get(
        "opening_payoff_candidates_status"
    ) != "READY":
        from .opening_payoff_candidates import (
            enqueue_opening_payoff_candidates,
        )

        payoff_task = enqueue_opening_payoff_candidates(
            session,
            settings,
            run,
            force=force,
        )
        session.refresh(run)
        projection = build_workbench_projection(session, run.id)
    readiness = assess_learning_report_readiness(projection)
    checks_by_id = {
        str(item["question_id"]): item for item in readiness["checks"]
    }
    eligible_question_ids = [
        question_id
        for question_id in INITIAL_INCREMENTAL_QUESTION_IDS
        if (
            checks_by_id[question_id]["ready"]
            and (
                only_question_ids is None
                or question_id in only_question_ids
            )
        )
    ]
    if not eligible_question_ids:
        if payoff_task is not None:
            return payoff_task
        raise LearningReportNotReadyError(readiness)
    latest_report = session.scalar(
        select(LearningReport)
        .where(LearningReport.run_id == run.id)
        .order_by(LearningReport.revision_no.desc())
    )
    active_tasks = list(session.scalars(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run.id,
            Task.kind == LEARNING_REPORT_TASK_KIND,
            Task.status.in_((
                TaskStatus.PENDING.value,
                TaskStatus.RUNNING.value,
                TaskStatus.RETRY_WAIT.value,
                TaskStatus.WAITING_CONFIRMATION.value,
            )),
        )
        .order_by(AnalysisRunTask.batch_index)
    ))
    active_question_ids = {
        str(question_id)
        for active_task in active_tasks
        for question_id in (
            json.loads(active_task.payload_json).get("question_ids")
            or []
        )
    }
    current_fingerprints = {
        question_id: str(checks_by_id[question_id]["source_fingerprint"])
        for question_id in INITIAL_INCREMENTAL_QUESTION_IDS
    }
    current_answer_ids: set[str] = set()
    if (
        latest_report is not None
        and latest_report.source_deep_revision == deep.revision_no
        and _report_uses_current_contract(latest_report)
    ):
        latest_payload = json.loads(latest_report.payload_json)
        report_fingerprints = latest_payload.get("question_source_fingerprints") or {}
        current_answer_ids = {
            str(item.get("question_id"))
            for item in latest_payload.get("answers", [])
            if (
                isinstance(item, dict)
                and _answer_uses_current_contract(
                    latest_payload,
                    str(item.get("question_id")),
                )
                and report_fingerprints.get(str(item.get("question_id")))
                == current_fingerprints.get(str(item.get("question_id")))
            )
        }
    selected_question_ids = (
        [
            question_id
            for question_id in eligible_question_ids
            if question_id not in active_question_ids
        ]
        if force
        else [
            question_id
            for question_id in eligible_question_ids
            if (
                question_id not in current_answer_ids
                and question_id not in active_question_ids
            )
        ]
    )
    if not selected_question_ids:
        return payoff_task or (active_tasks[0] if active_tasks else None)
    try:
        _service, profile = resolve_analysis_profile(settings, ENTITIES_EVENTS_PROFILE_ID)
    except ModelSettingsError:
        return None
    next_index = (
        max((link.batch_index for link in run.task_links), default=run.total_batches)
        + 1
    )
    created_tasks: list[Task] = []
    for offset, question_id in enumerate(selected_question_ids):
        task_payload, max_attempts = prepare_task_provider_routes(
            settings,
            {
                "run_id": run.id,
                "source_version_id": run.source_version_id,
                "source_deep_revision": deep.revision_no,
                "provider_name": "openai",
                "model_profile_id": profile.id,
                "question_ids": [question_id],
                "question_answer_scopes": {
                    question_id: checks_by_id[question_id]["answer_scope"],
                },
                "question_source_fingerprints": {
                    question_id: current_fingerprints[question_id],
                },
                "current_question_source_fingerprints": current_fingerprints,
                "catalog_version": LEARNING_QUESTION_CATALOG_VERSION,
                "answer_task_policy": "ONE_QUESTION_PER_MODEL_REQUEST",
            },
            profile.max_retries + 1,
        )
        task = Task(
            project_id=run.source_version.document.project_id,
            kind=LEARNING_REPORT_TASK_KIND,
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
    return payoff_task or created_tasks[0]


def build_learning_report_projection(
    session: Session,
    run_id: str,
    *,
    latest_deep_revision: int | None,
    readiness: dict[str, object] | None = None,
) -> tuple[str, dict[str, object]]:
    report = session.scalar(
        select(LearningReport)
        .where(LearningReport.run_id == run_id)
        .order_by(LearningReport.revision_no.desc())
    )
    latest_task = session.scalar(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run_id,
            Task.kind == LEARNING_REPORT_TASK_KIND,
        )
        .order_by(AnalysisRunTask.batch_index.desc())
    )
    active_task = session.scalar(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run_id,
            Task.kind == LEARNING_REPORT_TASK_KIND,
            Task.status.in_((
                TaskStatus.PENDING.value,
                TaskStatus.RUNNING.value,
                TaskStatus.RETRY_WAIT.value,
                TaskStatus.WAITING_CONFIRMATION.value,
            )),
        )
        .order_by(AnalysisRunTask.batch_index)
    )
    payload = json.loads(report.payload_json) if report is not None else {}
    report_base_is_current = bool(
        report is not None
        and report.source_deep_revision == latest_deep_revision
        and _report_uses_current_contract(report)
    )
    readiness_by_id = {
        str(item["question_id"]): item
        for item in (readiness or {}).get("checks", [])
    }
    report_fingerprints = payload.get("question_source_fingerprints") or {}
    current_answer_ids = {
        str(item.get("question_id"))
        for item in payload.get("answers", [])
        if (
            report_base_is_current
            and isinstance(item, dict)
            and _answer_uses_current_contract(
                payload,
                str(item.get("question_id")),
            )
            and report_fingerprints.get(str(item.get("question_id")))
            == readiness_by_id.get(str(item.get("question_id")), {}).get(
                "source_fingerprint"
            )
        )
    }
    pending_question_ids = {
        question_id
        for question_id, item in readiness_by_id.items()
        if item.get("ready") and question_id not in current_answer_ids
    }
    if active_task is not None:
        status = "GENERATING"
    elif current_answer_ids and not pending_question_ids:
        status = "READY"
    elif report is not None:
        status = "OUTDATED"
    elif latest_task is not None and latest_task.status == TaskStatus.FAILED.value:
        status = "FAILED"
    else:
        status = "NOT_GENERATED"
    answer_by_id = {
        item["question_id"]: item
        for item in payload.get("answers", [])
        if item.get("question_id") in _QUESTION_BY_ID
    }
    questions: list[dict[str, object]] = []
    for definition in LEARNING_QUESTION_CATALOG:
        answer = answer_by_id.get(definition.question_id, {})
        has_current_answer = definition.question_id in current_answer_ids
        readiness_item = readiness_by_id.get(definition.question_id)
        if has_current_answer:
            question_status = answer.get("status", "NOT_GENERATED")
        elif answer:
            question_status = "OUTDATED"
        elif (readiness_item or {}).get("ready"):
            question_status = "READY_TO_GENERATE"
        else:
            question_status = "NOT_GENERATED"
        if readiness_item is None:
            material_status = "NOT_ASSESSED"
        elif readiness_item.get("ready"):
            material_status = "READY"
        else:
            observed_status = str(
                (readiness_item.get("observed") or {}).get(
                    "opening_structure_status"
                )
                or ""
            )
            material_status = (
                observed_status
                if observed_status
                in {
                    "GENERATING",
                    "OUTDATED",
                    "FAILED",
                    "NOT_GENERATED",
                }
                else "NOT_READY"
            )
        questions.append({
            "question_id": definition.question_id,
            "stage_id": definition.stage_id,
            "stage_name": STAGE_NAMES[definition.stage_id],
            "question": definition.question,
            "priority": "CORE",
            "analysis_requirement": definition.analysis_requirement,
            "evidence_view": definition.evidence_view,
            "output_contract": definition.output_contract,
            "measurement_requirements": definition.measurement_requirements,
            "evidence_requirements": definition.evidence_requirements,
            "scope_requirement": definition.scope_requirement,
            "external_data_policy": definition.external_data_policy,
            "recommended_rank": _RECOMMENDED_RANK.get(definition.question_id),
            "status": question_status,
            "contract_status": "COMPLETE",
            "material_status": material_status,
            "answer_status": question_status,
            "conclusion": answer.get("conclusion", ""),
            "metrics": answer.get("metrics", []),
            "contract_items": [
                {
                    **item,
                    "label": next(
                        (
                            definition_item.label
                            for definition_item in LEARNING_QUESTION_ITEM_CONTRACTS.get(
                                definition.question_id,
                                (),
                            )
                            if definition_item.item_id == item.get("item_id")
                        ),
                        str(item.get("item_id") or ""),
                    ),
                }
                for item in answer.get("contract_items", [])
                if isinstance(item, dict)
            ],
            "program_artifacts": answer.get("program_artifacts", {}),
            "evidence_ids": answer.get("evidence_ids", []),
            "counter_evidence_ids": answer.get("counter_evidence_ids", []),
            "limitations": answer.get("limitations", []),
            "reusable_lessons": answer.get("reusable_lessons", []),
            "do_not_copy": answer.get("do_not_copy", []),
            "handbook": answer.get("handbook"),
            "has_current_answer": has_current_answer,
        })
    stages: list[dict[str, object]] = []
    for stage_id, stage_name in STAGE_NAMES.items():
        stage_questions = [item for item in questions if item["stage_id"] == stage_id]
        stages.append({
            "stage_id": stage_id,
            "stage_name": stage_name,
            "total_count": len(stage_questions),
            "generated_count": sum(item["has_current_answer"] for item in stage_questions),
            "answered_count": sum(
                item["has_current_answer"] and item["status"] in {"ANSWERED", "PARTIAL"}
                for item in stage_questions
            ),
            "insufficient_count": sum(
                item["has_current_answer"] and item["status"] == "INSUFFICIENT_EVIDENCE"
                for item in stage_questions
            ),
        })
    return status, {
        "catalog_version": LEARNING_QUESTION_CATALOG_VERSION,
        "batch_label": LEARNING_REPORT_BATCH_LABEL,
        "revision": report.revision_no if report is not None else None,
        "source_deep_revision": report.source_deep_revision if report is not None else None,
        "generated_at": report.created_at if report is not None else None,
        "recommended_question_ids": list(QG1_QUESTION_IDS),
        "questions": questions,
        "stages": stages,
        "author_decisions": payload.get("author_decisions", []),
        "method_candidates": payload.get("method_candidates", []),
    }
