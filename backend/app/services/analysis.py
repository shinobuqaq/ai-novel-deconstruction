from __future__ import annotations

import hashlib
import json
import re
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
    AnalysisIssue,
    CandidateStatus,
    EntityCandidate,
    EventCandidate,
    EvidenceSpan,
    DeepAnalysis,
    NarrativeSynthesis,
    SourceUnit,
    SourceVersion,
    SourceVersionStatus,
    Task,
    TaskStatus,
)
from .source_import import SourceImportError, source_text
from .provider_config import ENTITIES_EVENTS_PROFILE_ID, ModelSettingsError, resolve_analysis_profile


ANALYSIS_TASK_KIND = "analysis.entities_events"
NARRATIVE_SYNTHESIS_TASK_KIND = "analysis.narrative_synthesis"
DEEP_ANALYSIS_TASK_KIND = "analysis.deep_insights"
ANALYSIS_STAGE = "ENTITIES_EVENTS"
ANALYSIS_PROMPT_ID = "entities_events"
ANALYSIS_PROMPT_VERSION = "1.2.0"
NARRATIVE_PROMPT_ID = "narrative_synthesis"
NARRATIVE_PROMPT_VERSION = "1.2.0"
DEEP_PROMPT_ID = "deep_insights"
DEEP_PROMPT_VERSION = "1.0.0"
MAX_BATCH_CHARS = 18_000
CHUNK_OVERLAP_CHARS = 600


class StructuredOutputValidationError(ValueError):
    def __init__(self, code: str, errors: list[dict[str, Any]]) -> None:
        super().__init__(code)
        self.code = code
        self.errors = errors


def _validation_errors(error: ValidationError) -> list[dict[str, Any]]:
    return [
        {
            "path": [str(part) if not isinstance(part, int) else part for part in item["loc"]],
            "type": str(item["type"]),
            "message": str(item["msg"]),
        }
        for item in error.errors(include_url=False, include_input=False)
    ]


class EntityProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    entity_type: Literal["PERSON", "ORGANIZATION", "PLACE", "OBJECT", "OTHER"]
    aliases: list[str] = Field(default_factory=list, max_length=10)
    description: str = Field(default="", max_length=500)
    evidence_quotes: list[str] = Field(min_length=1, max_length=3)
    confidence: int = Field(ge=0, le=100)


class EventProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=150)
    event_type: Literal["ACTION", "DISCOVERY", "CONFLICT", "DECISION", "STATE_CHANGE", "OTHER"]
    summary: str = Field(min_length=1, max_length=800)
    participants: list[str] = Field(default_factory=list, max_length=20)
    narrative_mode: Literal[
        "ACTUAL",
        "MEMORY",
        "REPORT",
        "LIE",
        "MISUNDERSTANDING",
        "HYPOTHESIS",
        "REPEATED_MENTION",
        "UNCERTAIN",
    ] = "UNCERTAIN"
    location: str = Field(default="", max_length=200)
    trigger: str = Field(default="", max_length=600)
    process: str = Field(default="", max_length=1000)
    outcome: str = Field(default="", max_length=800)
    impact: str = Field(default="", max_length=800)
    evidence_quotes: list[str] = Field(min_length=1, max_length=3)
    confidence: int = Field(ge=0, le=100)


class AnalysisProviderOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entities: list[EntityProposal]
    events: list[EventProposal]


class StoryOverviewProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    premise: str = Field(min_length=1, max_length=1200)
    synopsis: str = Field(min_length=1, max_length=2400)
    protagonist: str = Field(min_length=1, max_length=120)
    protagonist_goal: str = Field(default="", max_length=600)
    central_conflict: str = Field(default="", max_length=1000)
    current_situation: str = Field(default="", max_length=1000)
    unresolved_questions: list[str] = Field(default_factory=list, max_length=8)
    evidence_ids: list[str] = Field(min_length=1, max_length=12)


class CharacterRoleProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    role: Literal["PROTAGONIST", "CORE_SUPPORTING", "IMPORTANT_SUPPORTING", "MINOR"]
    role_reason: str = Field(min_length=1, max_length=600)
    identities: list[str] = Field(default_factory=list, max_length=8)
    goals: list[str] = Field(default_factory=list, max_length=6)
    motivations: list[str] = Field(default_factory=list, max_length=6)
    abilities: list[str] = Field(default_factory=list, max_length=8)
    secrets: list[str] = Field(default_factory=list, max_length=8)
    important_experiences: list[str] = Field(default_factory=list, max_length=10)
    current_state: str = Field(default="", max_length=600)
    arc_summary: str = Field(default="", max_length=800)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)


class CharacterRelationProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_name: str = Field(min_length=1, max_length=120)
    target_name: str = Field(min_length=1, max_length=120)
    relation: str = Field(min_length=1, max_length=160)
    current_state: str = Field(default="", max_length=500)
    changes: list[str] = Field(default_factory=list, max_length=6)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)


class NarrativePhaseProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=180)
    situation: str = Field(min_length=1, max_length=800)
    goal: str = Field(default="", max_length=600)
    obstacle: str = Field(default="", max_length=600)
    key_actions: list[str] = Field(default_factory=list, max_length=8)
    outcome: str = Field(default="", max_length=800)
    change: str = Field(default="", max_length=800)
    next_hook: str = Field(default="", max_length=800)
    event_ids: list[str] = Field(min_length=1, max_length=20)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)


class EventRelationProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_event_id: str = Field(min_length=1, max_length=80)
    target_event_id: str = Field(min_length=1, max_length=80)
    relation: Literal["CAUSES", "ENABLES", "REVEALS", "ESCALATES", "RESOLVES", "PRECEDES", "SUBEVENT"]
    explanation: str = Field(min_length=1, max_length=800)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)


class NarrativeSynthesisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    story_overview: StoryOverviewProposal
    character_roles: list[CharacterRoleProposal] = Field(max_length=100)
    character_relations: list[CharacterRelationProposal] = Field(max_length=200)
    narrative_phases: list[NarrativePhaseProposal] = Field(max_length=40)
    event_relations: list[EventRelationProposal] = Field(max_length=300)


class FactVersionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=120)
    predicate: str = Field(min_length=1, max_length=160)
    value: str = Field(min_length=1, max_length=800)
    fact_type: Literal["PLACE", "ORGANIZATION", "OBJECT", "ABILITY", "RULE", "RELATION", "STATUS", "OTHER"]
    status: Literal["CONFIRMED", "REPORTED", "DISPUTED", "UNCERTAIN"]
    valid_from_chapter: int = Field(ge=1)
    valid_to_chapter: int | None = Field(default=None, ge=1)
    evidence_ids: list[str] = Field(min_length=1, max_length=12)
    counter_evidence_ids: list[str] = Field(default_factory=list, max_length=12)


class StateChangeProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=120)
    aspect: str = Field(min_length=1, max_length=160)
    before: str = Field(default="", max_length=600)
    after: str = Field(min_length=1, max_length=600)
    chapter_ordinal: int = Field(ge=1)
    event_id: str | None = Field(default=None, max_length=80)
    evidence_ids: list[str] = Field(min_length=1, max_length=12)


class ActorKnowledgeProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: str = Field(min_length=1, max_length=120)
    proposition: str = Field(min_length=1, max_length=800)
    state: Literal["KNOWS", "BELIEVES", "SUSPECTS", "MISTAKEN", "HIDDEN", "UNKNOWN"]
    chapter_ordinal: int = Field(ge=1)
    evidence_ids: list[str] = Field(min_length=1, max_length=12)


class WorldRuleProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=180)
    description: str = Field(min_length=1, max_length=1000)
    limitations: list[str] = Field(default_factory=list, max_length=8)
    costs: list[str] = Field(default_factory=list, max_length=8)
    exceptions: list[str] = Field(default_factory=list, max_length=8)
    evidence_ids: list[str] = Field(min_length=1, max_length=12)


class ForeshadowingProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=180)
    setup: str = Field(min_length=1, max_length=900)
    lifecycle: Literal["PLANTED", "REINFORCED", "MISDIRECTED", "TRANSFORMED", "PAYOFF", "INVALIDATED", "OPEN"]
    setup_chapter: int = Field(ge=1)
    payoff_chapter: int | None = Field(default=None, ge=1)
    event_ids: list[str] = Field(default_factory=list, max_length=12)
    evidence_ids: list[str] = Field(min_length=1, max_length=16)


class ConflictProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=180)
    conflict_type: Literal["PERSON_V_PERSON", "PERSON_V_SELF", "PERSON_V_WORLD", "GROUP_V_GROUP", "OTHER"]
    participants: list[str] = Field(default_factory=list, max_length=12)
    goals: str = Field(default="", max_length=800)
    obstacles: str = Field(default="", max_length=800)
    stakes: str = Field(default="", max_length=800)
    escalation: list[str] = Field(default_factory=list, max_length=8)
    resolution: str = Field(default="", max_length=800)
    status: Literal["OPEN", "ESCALATING", "RESOLVED", "SHIFTED", "UNCERTAIN"]
    event_ids: list[str] = Field(default_factory=list, max_length=16)
    evidence_ids: list[str] = Field(min_length=1, max_length=16)


class SceneAnalysisProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_ordinal: int = Field(ge=1)
    function: Literal["SETUP", "TRANSITION", "REVELATION", "CONFLICT", "DECISION", "AFTERMATH", "OTHER"]
    summary: str = Field(min_length=1, max_length=800)
    information_released: list[str] = Field(default_factory=list, max_length=8)
    action_dialogue_balance: Literal["ACTION_HEAVY", "DIALOGUE_HEAVY", "BALANCED", "REFLECTIVE", "UNCERTAIN"]
    pace: Literal["SLOW", "STEADY", "FAST", "ACCELERATING", "BRAKING", "UNCERTAIN"]
    evidence_ids: list[str] = Field(min_length=1, max_length=16)


class AnalysisClaimProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_kind: Literal["FACT", "INFERENCE", "PATTERN", "INTERPRETATION", "COMPARATIVE"]
    claim_text: str = Field(min_length=1, max_length=1200)
    scope: str = Field(min_length=1, max_length=240)
    evidence_ids: list[str] = Field(default_factory=list, max_length=16)
    counter_evidence_ids: list[str] = Field(default_factory=list, max_length=16)
    confidence: int = Field(ge=0, le=100)


class EntityResolutionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_name: str = Field(min_length=1, max_length=120)
    merged_names: list[str] = Field(min_length=2, max_length=10)
    entity_type: Literal["ORGANIZATION", "PLACE", "OBJECT", "OTHER"]
    reason: str = Field(min_length=1, max_length=600)
    evidence_ids: list[str] = Field(min_length=1, max_length=12)


class DeepAnalysisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_versions: list[FactVersionProposal] = Field(max_length=300)
    state_changes: list[StateChangeProposal] = Field(max_length=300)
    actor_knowledge: list[ActorKnowledgeProposal] = Field(max_length=300)
    world_rules: list[WorldRuleProposal] = Field(max_length=120)
    foreshadowing: list[ForeshadowingProposal] = Field(max_length=160)
    conflicts: list[ConflictProposal] = Field(max_length=160)
    scene_analysis: list[SceneAnalysisProposal] = Field(max_length=500)
    claims: list[AnalysisClaimProposal] = Field(max_length=300)
    entity_resolutions: list[EntityResolutionProposal] = Field(default_factory=list, max_length=100)


@dataclass(frozen=True, slots=True)
class AnalysisBatch:
    start_char: int
    end_char: int
    unit_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PersistedAnalysis:
    entity_ids: tuple[str, ...]
    event_ids: tuple[str, ...]
    rejected_entities: int
    rejected_events: int


@dataclass(frozen=True, slots=True)
class PersistedNarrativeSynthesis:
    synthesis_id: str


@dataclass(frozen=True, slots=True)
class PersistedDeepAnalysis:
    analysis_id: str


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalized_name(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()[:240]


def _split_long_range(text: str, start: int, end: int) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    cursor = start
    while cursor < end:
        target = min(end, cursor + MAX_BATCH_CHARS)
        if target < end:
            boundary = text.rfind("\n", cursor + MAX_BATCH_CHARS // 2, target)
            if boundary > cursor:
                target = boundary + 1
        ranges.append((cursor, target))
        if target >= end:
            break
        cursor = max(cursor + 1, target - CHUNK_OVERLAP_CHARS)
    return ranges


def build_analysis_batches(text: str, units: list[SourceUnit]) -> list[AnalysisBatch]:
    expanded: list[tuple[int, int, str]] = []
    for unit in units:
        for start, end in _split_long_range(text, unit.start_char, unit.end_char):
            expanded.append((start, end, unit.id))

    batches: list[AnalysisBatch] = []
    current_start: int | None = None
    current_end = 0
    current_units: list[str] = []
    for start, end, unit_id in expanded:
        if current_start is None:
            current_start, current_end, current_units = start, end, [unit_id]
            continue
        if end - current_start <= MAX_BATCH_CHARS:
            current_end = end
            if unit_id not in current_units:
                current_units.append(unit_id)
            continue
        batches.append(AnalysisBatch(current_start, current_end, tuple(current_units)))
        current_start, current_end, current_units = start, end, [unit_id]
    if current_start is not None:
        batches.append(AnalysisBatch(current_start, current_end, tuple(current_units)))
    return batches


def start_entities_events_run(
    session: Session,
    settings: Settings,
    version: SourceVersion,
) -> AnalysisRun:
    if version.status != SourceVersionStatus.CONFIRMED.value:
        raise SourceImportError(
            "SOURCE_NOT_CONFIRMED",
            "请先确认章节，再开始人物和事件分析。",
            status_code=409,
        )
    existing = session.scalar(
        select(AnalysisRun)
        .where(
            AnalysisRun.source_version_id == version.id,
            AnalysisRun.stage == ANALYSIS_STAGE,
            AnalysisRun.status.in_((
                AnalysisRunStatus.PENDING.value,
                AnalysisRunStatus.RUNNING.value,
                AnalysisRunStatus.REVIEW.value,
                AnalysisRunStatus.CONFIRMED.value,
            )),
        )
        .order_by(AnalysisRun.created_at.desc())
    )
    if existing is not None:
        return existing

    try:
        _service, model_profile = resolve_analysis_profile(settings, ENTITIES_EVENTS_PROFILE_ID)
    except ModelSettingsError as exc:
        raise SourceImportError(exc.code, exc.message, status_code=409) from exc

    text = source_text(settings, version)
    units = list(session.scalars(
        select(SourceUnit)
        .where(SourceUnit.source_version_id == version.id)
        .order_by(SourceUnit.ordinal)
    ))
    batches = build_analysis_batches(text, units)
    if not batches:
        raise SourceImportError("SOURCE_UNITS_MISSING", "没有可分析的章节。", status_code=409)

    run = AnalysisRun(
        source_version_id=version.id,
        stage=ANALYSIS_STAGE,
        status=AnalysisRunStatus.PENDING.value,
        total_batches=len(batches),
    )
    session.add(run)
    session.flush()
    for batch_index, batch in enumerate(batches, start=1):
        task = Task(
            project_id=version.document.project_id,
            kind=ANALYSIS_TASK_KIND,
            payload_json=json.dumps(
                {
                    "run_id": run.id,
                    "source_version_id": version.id,
                    "batch_index": batch_index,
                    "batch_count": len(batches),
                    "start_char": batch.start_char,
                    "end_char": batch.end_char,
                    "unit_ids": list(batch.unit_ids),
                    "provider_name": "openai",
                    "model_profile_id": model_profile.id,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            max_attempts=model_profile.max_retries + 1,
        )
        session.add(task)
        session.flush()
        session.add(AnalysisRunTask(run_id=run.id, task_id=task.id, batch_index=batch_index))
    session.commit()
    session.refresh(run)
    return run


def _schema() -> dict:
    path = Path(__file__).resolve().parents[3] / "schemas" / "entities_events_response.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _analysis_prompt() -> str:
    path = Path(__file__).resolve().parents[3] / "prompts" / "entities_events_v1.md"
    return path.read_text(encoding="utf-8").strip()


def provider_payload_for_claim(
    session: Session,
    settings: Settings,
    task_payload: dict,
) -> dict:
    version = session.get(SourceVersion, task_payload.get("source_version_id"))
    if version is None:
        raise ValueError("SOURCE_VERSION_NOT_FOUND")
    text = source_text(settings, version)
    start = int(task_payload["start_char"])
    end = int(task_payload["end_char"])
    excerpt = text[start:end]
    instructions = _analysis_prompt()
    return {
        "instructions": instructions,
        "input": f"原文在全书中的字符范围：{start}-{end}\n\n{excerpt}",
        "output_schema": _schema(),
        "model_profile_id": str(task_payload.get("model_profile_id") or ENTITIES_EVENTS_PROFILE_ID),
        "prompt_id": ANALYSIS_PROMPT_ID,
        "prompt_version": ANALYSIS_PROMPT_VERSION,
        "source_version_id": version.id,
        "source_char_start": start,
        "source_char_end": end,
    }


def _inline_model_schema(model: type[BaseModel]) -> dict:
    raw = model.model_json_schema()
    definitions = raw.get("$defs", {})

    def expand(value: object) -> object:
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/$defs/"):
                definition = definitions.get(reference.rsplit("/", 1)[-1])
                return expand(definition or {})
            return {
                key: expand(item)
                for key, item in value.items()
                if key not in {"$defs", "$ref"}
            }
        if isinstance(value, list):
            return [expand(item) for item in value]
        return value

    return expand(raw)  # type: ignore[return-value]


def _narrative_prompt() -> str:
    path = Path(__file__).resolve().parents[3] / "prompts" / "narrative_synthesis_v1.md"
    return path.read_text(encoding="utf-8").strip()


def _deep_prompt() -> str:
    path = Path(__file__).resolve().parents[3] / "prompts" / "deep_insights_v1.md"
    return path.read_text(encoding="utf-8").strip()


def provider_payload_for_narrative_synthesis(
    session: Session,
    settings: Settings,
    task_payload: dict,
) -> dict:
    from .workbench import build_workbench_projection

    run_id = str(task_payload.get("run_id") or "")
    version = session.get(SourceVersion, task_payload.get("source_version_id"))
    if not run_id or version is None:
        raise ValueError("ANALYSIS_RUN_NOT_FOUND")
    foundation = build_workbench_projection(session, run_id, include_synthesis=False)
    evidence_ids: set[str] = set()
    for collection in (foundation["characters"], foundation["events"]):
        for item in collection:
            evidence_ids.update(item.get("evidence_ids", []))
    evidence_rows = session.scalars(
        select(EvidenceSpan).where(
            EvidenceSpan.source_version_id == version.id,
            EvidenceSpan.id.in_(evidence_ids),
        )
    )
    evidence_by_id = {item.id: item for item in evidence_rows}
    chapter_units = list(session.scalars(
        select(SourceUnit)
        .where(SourceUnit.source_version_id == version.id, SourceUnit.unit_type == "CHAPTER")
        .order_by(SourceUnit.ordinal)
    ))
    chapters = [
        {"chapter_number": index, "title": unit.title}
        for index, unit in enumerate(chapter_units, start=1)
    ]
    chapter_title_by_id = {unit.id: unit.title for unit in chapter_units}
    evidence = [
        {
            "id": item.id,
            "chapter_title": chapter_title_by_id.get(item.source_unit_id, "章节待定"),
            "text": item.text_snapshot,
        }
        for item in sorted(evidence_by_id.values(), key=lambda row: row.start_char)
    ]
    previous_synthesis = session.scalar(
        select(NarrativeSynthesis).where(NarrativeSynthesis.run_id == run_id)
    )
    # The evidence catalog is intentionally bounded by already accepted
    # candidates; it keeps synthesis grounded without resending the whole book.
    input_payload = {
        "chapters": chapters,
        "characters": foundation["characters"],
        "related_entities": foundation["related_entities"],
        "events": foundation["events"],
        "evidence": evidence,
        "previous_synthesis": (
            json.loads(previous_synthesis.payload_json)
            if previous_synthesis is not None
            else None
        ),
        "revision_requests": task_payload.get("revision_requests", []),
    }
    return {
        "instructions": _narrative_prompt(),
        "input": json.dumps(input_payload, ensure_ascii=False, separators=(",", ":")),
        "output_schema": _inline_model_schema(NarrativeSynthesisOutput),
        "model_profile_id": str(task_payload.get("model_profile_id") or ENTITIES_EVENTS_PROFILE_ID),
        "prompt_id": NARRATIVE_PROMPT_ID,
        "prompt_version": NARRATIVE_PROMPT_VERSION,
        "source_version_id": version.id,
        "source_char_start": 0,
        "source_char_end": version.total_chars,
    }


def provider_payload_for_deep_analysis(
    session: Session,
    settings: Settings,
    task_payload: dict,
) -> dict:
    """Compile the verified foundation and narrative result for deep analysis.

    The model receives only accepted candidates and exact evidence spans. It
    never receives database identities as writable fields; IDs are references
    that the program validates after the response.
    """
    from .workbench import build_workbench_projection

    run_id = str(task_payload.get("run_id") or "")
    version = session.get(SourceVersion, task_payload.get("source_version_id"))
    synthesis = session.scalar(
        select(NarrativeSynthesis).where(NarrativeSynthesis.run_id == run_id)
    )
    if not run_id or version is None or synthesis is None:
        raise ValueError("NARRATIVE_SYNTHESIS_NOT_FOUND")
    foundation = build_workbench_projection(session, run_id, include_synthesis=False)
    narrative = json.loads(synthesis.payload_json)
    evidence_ids: set[str] = set()
    for collection in (foundation["characters"], foundation["events"]):
        for item in collection:
            evidence_ids.update(item.get("evidence_ids", []))
    for item in (
        narrative.get("story_overview"),
        *narrative.get("character_roles", []),
        *narrative.get("character_relations", []),
        *narrative.get("narrative_phases", []),
        *narrative.get("event_relations", []),
    ):
        if isinstance(item, dict):
            evidence_ids.update(item.get("evidence_ids", []))
    evidence_rows = list(session.scalars(
        select(EvidenceSpan)
        .where(
            EvidenceSpan.source_version_id == version.id,
            EvidenceSpan.id.in_(evidence_ids),
        )
        .order_by(EvidenceSpan.start_char)
    ))
    chapter_units = list(session.scalars(
        select(SourceUnit)
        .where(
            SourceUnit.source_version_id == version.id,
            SourceUnit.unit_type == "CHAPTER",
        )
        .order_by(SourceUnit.ordinal)
    ))
    chapters = [
        {"chapter_number": index, "title": unit.title}
        for index, unit in enumerate(chapter_units, start=1)
    ]
    chapter_title_by_id = {item.id: item.title for item in chapter_units}
    evidence = [
        {
            "id": item.id,
            "chapter_title": chapter_title_by_id.get(item.source_unit_id, "章节待定"),
            "text": item.text_snapshot,
        }
        for item in evidence_rows
    ]
    previous_analysis = session.scalar(
        select(DeepAnalysis)
        .where(DeepAnalysis.run_id == run_id)
        .order_by(DeepAnalysis.revision_no.desc())
    )
    input_payload = {
        "chapters": chapters,
        "characters": foundation["characters"],
        "related_entities": foundation["related_entities"],
        "events": foundation["events"],
        "story_overview": narrative.get("story_overview"),
        "character_roles": narrative.get("character_roles", []),
        "narrative_phases": narrative.get("narrative_phases", []),
        "evidence": evidence,
        "previous_analysis": (
            json.loads(previous_analysis.payload_json)
            if previous_analysis is not None
            else None
        ),
        "revision_requests": task_payload.get("revision_requests", []),
    }
    return {
        "instructions": _deep_prompt(),
        "input": json.dumps(input_payload, ensure_ascii=False, separators=(",", ":")),
        "output_schema": _inline_model_schema(DeepAnalysisOutput),
        "model_profile_id": str(task_payload.get("model_profile_id") or ENTITIES_EVENTS_PROFILE_ID),
        "prompt_id": DEEP_PROMPT_ID,
        "prompt_version": DEEP_PROMPT_VERSION,
        "source_version_id": version.id,
        "source_char_start": 0,
        "source_char_end": version.total_chars,
    }


def parse_provider_output(value: dict) -> AnalysisProviderOutput:
    try:
        return AnalysisProviderOutput.model_validate(value)
    except ValidationError as exc:
        raise StructuredOutputValidationError(
            "ANALYSIS_OUTPUT_INVALID",
            _validation_errors(exc),
        ) from exc


def parse_narrative_synthesis(value: dict) -> NarrativeSynthesisOutput:
    try:
        return NarrativeSynthesisOutput.model_validate(value)
    except ValidationError as exc:
        raise StructuredOutputValidationError(
            "NARRATIVE_OUTPUT_INVALID",
            _validation_errors(exc),
        ) from exc


def parse_deep_analysis(value: dict) -> DeepAnalysisOutput:
    try:
        return DeepAnalysisOutput.model_validate(value)
    except ValidationError as exc:
        raise StructuredOutputValidationError(
            "DEEP_ANALYSIS_OUTPUT_INVALID",
            _validation_errors(exc),
        ) from exc


def _quote_matches(text: str, quote: str, start: int, end: int) -> list[int]:
    positions: list[int] = []
    cursor = start
    while cursor < end:
        position = text.find(quote, cursor, end)
        if position < 0:
            break
        positions.append(position)
        cursor = position + 1
    return positions


def _evidence_for_quote(
    session: Session,
    *,
    version: SourceVersion,
    text: str,
    quote: str,
    start: int,
    end: int,
) -> tuple[str | None, int | None, int | None, bool]:
    positions = _quote_matches(text, quote, start, end)
    if not positions:
        return None, None, None, False
    quote_start = positions[0]
    quote_end = quote_start + len(quote)
    evidence = session.scalar(
        select(EvidenceSpan).where(
            EvidenceSpan.source_version_id == version.id,
            EvidenceSpan.start_char <= quote_start,
            EvidenceSpan.end_char >= quote_end,
        )
    )
    if evidence is None:
        unit = session.scalar(
            select(SourceUnit).where(
                SourceUnit.source_version_id == version.id,
                SourceUnit.start_char <= quote_start,
                SourceUnit.end_char >= quote_end,
            )
        )
        if unit is None:
            return None, None, None, False
        evidence_id = f"evd_{_hash(f'{version.id}:{quote_start}:{quote_end}:{quote}')[:32]}"
        evidence = session.get(EvidenceSpan, evidence_id)
        if evidence is None:
            context_start = max(0, quote_start - 80)
            context_end = min(len(text), quote_end + 80)
            evidence = EvidenceSpan(
                id=evidence_id,
                source_version_id=version.id,
                source_unit_id=unit.id,
                paragraph_index=1_000_000 + quote_start,
                start_char=quote_start,
                end_char=quote_end,
                text_snapshot=quote,
                context_hash=_hash(text[context_start:context_end]),
            )
            session.add(evidence)
            session.flush()
    return evidence.id, quote_start, quote_end, len(positions) > 1


def _merge_json_list(current: str, additions: list[str]) -> str:
    values = list(json.loads(current))
    for value in additions:
        if value not in values:
            values.append(value)
    return json.dumps(values, ensure_ascii=False)


def _stronger_candidate_status(current: str, incoming: str) -> str:
    rank = {
        CandidateStatus.REJECTED.value: 0,
        CandidateStatus.UNCERTAIN.value: 1,
        CandidateStatus.VALID.value: 2,
    }
    return incoming if rank[incoming] > rank[current] else current


def persist_analysis_output(
    session: Session,
    settings: Settings,
    *,
    task: Task,
    attempt_id: str,
    task_payload: dict,
    output: AnalysisProviderOutput,
) -> PersistedAnalysis:
    run = session.get(AnalysisRun, task_payload.get("run_id"))
    version = session.get(SourceVersion, task_payload.get("source_version_id"))
    if run is None or version is None:
        raise ValueError("ANALYSIS_RUN_NOT_FOUND")
    text = source_text(settings, version)
    batch_start = int(task_payload["start_char"])
    batch_end = int(task_payload["end_char"])
    entity_ids: list[str] = []
    event_ids: list[str] = []
    rejected_entities = 0
    rejected_events = 0

    for proposal in output.entities:
        evidence_ids: list[str] = []
        ambiguous = False
        for quote in proposal.evidence_quotes:
            evidence_id, _start, _end, multiple = _evidence_for_quote(
                session,
                version=version,
                text=text,
                quote=quote,
                start=batch_start,
                end=batch_end,
            )
            if evidence_id:
                evidence_ids.append(evidence_id)
            ambiguous = ambiguous or multiple
        normalized = _normalized_name(proposal.name)
        candidate = session.scalar(
            select(EntityCandidate).where(
                EntityCandidate.run_id == run.id,
                EntityCandidate.normalized_name == normalized,
            )
        )
        status = (
            CandidateStatus.REJECTED.value
            if not evidence_ids
            else CandidateStatus.UNCERTAIN.value
            if ambiguous
            else CandidateStatus.VALID.value
        )
        if status == CandidateStatus.REJECTED.value:
            rejected_entities += 1
        candidate_id = f"enc_{_hash(f'{run.id}:{normalized}')[:32]}"
        if candidate is None:
            candidate = EntityCandidate(
                id=candidate_id,
                run_id=run.id,
                source_version_id=version.id,
                name=proposal.name.strip(),
                normalized_name=normalized,
                entity_type=proposal.entity_type,
                aliases_json=json.dumps(proposal.aliases, ensure_ascii=False),
                description=proposal.description.strip(),
                evidence_ids_json=json.dumps(evidence_ids, ensure_ascii=False),
                status=status,
                confidence=proposal.confidence,
                created_by_task_id=task.id,
                created_by_attempt_id=attempt_id,
            )
            session.add(candidate)
        else:
            candidate.aliases_json = _merge_json_list(candidate.aliases_json, proposal.aliases)
            candidate.evidence_ids_json = _merge_json_list(candidate.evidence_ids_json, evidence_ids)
            if len(proposal.description) > len(candidate.description):
                candidate.description = proposal.description
            candidate.confidence = max(candidate.confidence, proposal.confidence)
            candidate.status = _stronger_candidate_status(candidate.status, status)
            if status != CandidateStatus.REJECTED.value:
                candidate.created_by_task_id = task.id
                candidate.created_by_attempt_id = attempt_id
        entity_ids.append(candidate.id)

    for proposal in output.events:
        evidence_ids: list[str] = []
        positions: list[tuple[int, int]] = []
        ambiguous = False
        for quote in proposal.evidence_quotes:
            evidence_id, quote_start, quote_end, multiple = _evidence_for_quote(
                session,
                version=version,
                text=text,
                quote=quote,
                start=batch_start,
                end=batch_end,
            )
            if evidence_id and quote_start is not None and quote_end is not None:
                evidence_ids.append(evidence_id)
                positions.append((quote_start, quote_end))
            ambiguous = ambiguous or multiple
        status = (
            CandidateStatus.REJECTED.value
            if not evidence_ids
            else CandidateStatus.UNCERTAIN.value
            if ambiguous
            else CandidateStatus.VALID.value
        )
        if status == CandidateStatus.REJECTED.value:
            rejected_events += 1
        event_start = min((item[0] for item in positions), default=batch_start)
        event_end = max((item[1] for item in positions), default=batch_start)
        identity_key = _hash(
            f"{_normalized_name(proposal.title)}:{proposal.narrative_mode}:{event_start}:{event_end}"
        )
        candidate = session.scalar(
            select(EventCandidate).where(
                EventCandidate.run_id == run.id,
                EventCandidate.identity_key == identity_key,
            )
        )
        candidate_id = f"evc_{_hash(f'{run.id}:{identity_key}')[:32]}"
        if candidate is None:
            candidate = EventCandidate(
                id=candidate_id,
                run_id=run.id,
                source_version_id=version.id,
                identity_key=identity_key,
                title=proposal.title.strip(),
                event_type=proposal.event_type,
                summary=proposal.summary.strip(),
                participants_json=json.dumps(proposal.participants, ensure_ascii=False),
                details_json=json.dumps(
                    {
                        "narrative_mode": proposal.narrative_mode,
                        "location": proposal.location.strip(),
                        "trigger": proposal.trigger.strip(),
                        "process": proposal.process.strip(),
                        "outcome": proposal.outcome.strip(),
                        "impact": proposal.impact.strip(),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                evidence_ids_json=json.dumps(evidence_ids, ensure_ascii=False),
                start_char=event_start,
                end_char=event_end,
                status=status,
                confidence=proposal.confidence,
                created_by_task_id=task.id,
                created_by_attempt_id=attempt_id,
            )
            session.add(candidate)
        else:
            candidate.evidence_ids_json = _merge_json_list(candidate.evidence_ids_json, evidence_ids)
            candidate.participants_json = _merge_json_list(candidate.participants_json, proposal.participants)
            candidate.details_json = json.dumps(
                {
                    "narrative_mode": proposal.narrative_mode,
                    "location": proposal.location.strip(),
                    "trigger": proposal.trigger.strip(),
                    "process": proposal.process.strip(),
                    "outcome": proposal.outcome.strip(),
                    "impact": proposal.impact.strip(),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            candidate.confidence = max(candidate.confidence, proposal.confidence)
            candidate.status = _stronger_candidate_status(candidate.status, status)
            if status != CandidateStatus.REJECTED.value:
                candidate.created_by_task_id = task.id
                candidate.created_by_attempt_id = attempt_id
        event_ids.append(candidate.id)

    session.commit()
    return PersistedAnalysis(
        tuple(entity_ids),
        tuple(event_ids),
        rejected_entities,
        rejected_events,
    )


def enqueue_narrative_synthesis(
    session: Session,
    settings: Settings,
    run: AnalysisRun,
    *,
    force: bool = False,
    revision_requests: list[dict] | None = None,
) -> Task | None:
    existing = session.scalar(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run.id,
            Task.kind == NARRATIVE_SYNTHESIS_TASK_KIND,
        )
    )
    if existing is not None and not force:
        return existing
    foundation_tasks = list(session.scalars(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run.id,
            Task.kind == ANALYSIS_TASK_KIND,
        )
    ))
    if not foundation_tasks or any(
        task.status != TaskStatus.SUCCEEDED.value for task in foundation_tasks
    ):
        return None
    try:
        _service, model_profile = resolve_analysis_profile(settings, ENTITIES_EVENTS_PROFILE_ID)
    except ModelSettingsError:
        return None
    task = Task(
        project_id=run.source_version.document.project_id,
        kind=NARRATIVE_SYNTHESIS_TASK_KIND,
        payload_json=json.dumps(
            {
                "run_id": run.id,
                "source_version_id": run.source_version_id,
                "provider_name": "openai",
                "model_profile_id": model_profile.id,
                "revision_requests": revision_requests or [],
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        max_attempts=model_profile.max_retries + 1,
    )
    session.add(task)
    session.flush()
    next_index = max(
        (link.batch_index for link in run.task_links),
        default=run.total_batches,
    ) + 1
    session.add(AnalysisRunTask(run_id=run.id, task_id=task.id, batch_index=next_index))
    run.total_batches = next_index
    run.status = AnalysisRunStatus.PENDING.value
    session.commit()
    session.refresh(task)
    return task


def persist_narrative_synthesis(
    session: Session,
    *,
    task: Task,
    attempt_id: str,
    task_payload: dict,
    output: NarrativeSynthesisOutput,
) -> PersistedNarrativeSynthesis:
    run = session.get(AnalysisRun, task_payload.get("run_id"))
    version = session.get(SourceVersion, task_payload.get("source_version_id"))
    if run is None or version is None:
        raise ValueError("ANALYSIS_RUN_NOT_FOUND")
    from .workbench import build_workbench_projection

    foundation = build_workbench_projection(session, run.id, include_synthesis=False)
    valid_evidence_ids = {
        item.id
        for item in session.scalars(
            select(EvidenceSpan).where(EvidenceSpan.source_version_id == version.id)
        )
    }
    valid_event_ids = {item["id"] for item in foundation["events"]}
    valid_character_names = {
        _normalized_name(name)
        for item in foundation["characters"]
        for name in [item["name"], *item.get("aliases", [])]
    }
    all_evidence_ids: list[str] = []
    for item in [output.story_overview, *output.character_roles, *output.character_relations, *output.narrative_phases, *output.event_relations]:
        all_evidence_ids.extend(item.evidence_ids)
    if not set(all_evidence_ids).issubset(valid_evidence_ids):
        raise ValueError("NARRATIVE_EVIDENCE_REFERENCE_INVALID")
    if not _normalized_name(output.story_overview.protagonist) in valid_character_names:
        raise ValueError("NARRATIVE_PROTAGONIST_REFERENCE_INVALID")
    if any(
        not _normalized_name(item.name) in valid_character_names
        for item in output.character_roles
    ):
        raise ValueError("NARRATIVE_CHARACTER_REFERENCE_INVALID")
    if any(
        not _normalized_name(item.source_name) in valid_character_names
        or not _normalized_name(item.target_name) in valid_character_names
        for item in output.character_relations
    ):
        raise ValueError("NARRATIVE_RELATION_REFERENCE_INVALID")
    if any(
        item.source_event_id not in valid_event_ids or item.target_event_id not in valid_event_ids
        for item in output.event_relations
    ):
        raise ValueError("NARRATIVE_EVENT_RELATION_REFERENCE_INVALID")
    if any(
        not set(item.event_ids).issubset(valid_event_ids)
        for item in output.narrative_phases
    ):
        raise ValueError("NARRATIVE_PHASE_REFERENCE_INVALID")

    existing = session.scalar(
        select(NarrativeSynthesis).where(NarrativeSynthesis.run_id == run.id)
    )
    payload_json = json.dumps(output.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    if existing is None:
        existing = NarrativeSynthesis(
            run_id=run.id,
            source_version_id=version.id,
            payload_json=payload_json,
            prompt_id=NARRATIVE_PROMPT_ID,
            prompt_version=NARRATIVE_PROMPT_VERSION,
            created_by_task_id=task.id,
            created_by_attempt_id=attempt_id,
        )
        session.add(existing)
    else:
        existing.payload_json = payload_json
        existing.created_by_task_id = task.id
        existing.created_by_attempt_id = attempt_id
    session.commit()
    session.refresh(existing)
    return PersistedNarrativeSynthesis(existing.id)


def enqueue_deep_analysis(
    session: Session,
    settings: Settings,
    run: AnalysisRun,
    *,
    force: bool = False,
    revision_requests: list[dict] | None = None,
) -> Task | None:
    existing = session.scalar(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run.id,
            Task.kind == DEEP_ANALYSIS_TASK_KIND,
        )
    )
    if existing is not None and not force:
        return existing
    narrative_task = session.scalar(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run.id,
            Task.kind == NARRATIVE_SYNTHESIS_TASK_KIND,
        )
    )
    synthesis = session.scalar(
        select(NarrativeSynthesis).where(NarrativeSynthesis.run_id == run.id)
    )
    if (
        narrative_task is None
        or narrative_task.status != TaskStatus.SUCCEEDED.value
        or synthesis is None
    ):
        return None
    try:
        _service, model_profile = resolve_analysis_profile(settings, ENTITIES_EVENTS_PROFILE_ID)
    except ModelSettingsError:
        return None
    task = Task(
        project_id=run.source_version.document.project_id,
        kind=DEEP_ANALYSIS_TASK_KIND,
        payload_json=json.dumps(
            {
                "run_id": run.id,
                "source_version_id": run.source_version_id,
                "provider_name": "openai",
                "model_profile_id": model_profile.id,
                "revision_requests": revision_requests or [],
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        max_attempts=model_profile.max_retries + 1,
    )
    session.add(task)
    session.flush()
    next_index = max(
        (link.batch_index for link in run.task_links),
        default=run.total_batches,
    ) + 1
    session.add(AnalysisRunTask(run_id=run.id, task_id=task.id, batch_index=next_index))
    run.total_batches = next_index
    run.status = AnalysisRunStatus.PENDING.value
    session.commit()
    session.refresh(task)
    return task


def _item_id(prefix: str, run_id: str, index: int, identity: str) -> str:
    return f"{prefix}_{_hash(f'{run_id}:{index}:{identity}')[:32]}"


def persist_deep_analysis(
    session: Session,
    settings: Settings,
    *,
    task: Task,
    attempt_id: str,
    task_payload: dict,
    output: DeepAnalysisOutput,
) -> PersistedDeepAnalysis:
    run = session.get(AnalysisRun, task_payload.get("run_id"))
    version = session.get(SourceVersion, task_payload.get("source_version_id"))
    if run is None or version is None:
        raise ValueError("ANALYSIS_RUN_NOT_FOUND")
    from .workbench import build_workbench_projection

    foundation = build_workbench_projection(session, run.id, include_synthesis=False)
    provider_payload = provider_payload_for_deep_analysis(session, settings, task_payload)
    visible_input = json.loads(provider_payload["input"])
    valid_evidence_ids = {item["id"] for item in visible_input.get("evidence", [])}
    valid_event_ids = {item["id"] for item in foundation["events"]}
    valid_character_names = {
        _normalized_name(name)
        for item in foundation["characters"]
        for name in [item["name"], *item.get("aliases", [])]
    }
    chapter_count = len(visible_input.get("chapters", []))

    evidence_items = [
        *output.fact_versions,
        *output.state_changes,
        *output.actor_knowledge,
        *output.world_rules,
        *output.foreshadowing,
        *output.conflicts,
        *output.scene_analysis,
        *output.claims,
        *output.entity_resolutions,
    ]
    for item in evidence_items:
        evidence_ids = set(item.evidence_ids)
        counter_ids = set(getattr(item, "counter_evidence_ids", []))
        if not evidence_ids.union(counter_ids).issubset(valid_evidence_ids):
            raise ValueError("DEEP_ANALYSIS_EVIDENCE_REFERENCE_INVALID")
    chapter_ordinals = [
        *[item.valid_from_chapter for item in output.fact_versions],
        *[item.valid_to_chapter for item in output.fact_versions if item.valid_to_chapter is not None],
        *[item.chapter_ordinal for item in output.state_changes],
        *[item.chapter_ordinal for item in output.actor_knowledge],
        *[item.setup_chapter for item in output.foreshadowing],
        *[item.payoff_chapter for item in output.foreshadowing if item.payoff_chapter is not None],
        *[item.chapter_ordinal for item in output.scene_analysis],
    ]
    if any(chapter < 1 or chapter > chapter_count for chapter in chapter_ordinals):
        raise ValueError("DEEP_ANALYSIS_CHAPTER_REFERENCE_INVALID")
    if any(
        item.valid_to_chapter is not None
        and item.valid_to_chapter < item.valid_from_chapter
        for item in output.fact_versions
    ):
        raise ValueError("DEEP_ANALYSIS_FACT_INTERVAL_INVALID")
    if any(
        _normalized_name(item.actor) not in valid_character_names
        for item in output.actor_knowledge
    ):
        raise ValueError("DEEP_ANALYSIS_ACTOR_REFERENCE_INVALID")
    referenced_event_ids = {
        event_id
        for item in [*output.foreshadowing, *output.conflicts]
        for event_id in item.event_ids
    }.union(
        item.event_id for item in output.state_changes if item.event_id is not None
    )
    if not referenced_event_ids.issubset(valid_event_ids):
        raise ValueError("DEEP_ANALYSIS_EVENT_REFERENCE_INVALID")
    related_by_name = {
        _normalized_name(item["name"]): item
        for item in foundation["related_entities"]
    }
    resolved_names: set[str] = set()
    for resolution in output.entity_resolutions:
        normalized_names = [_normalized_name(name) for name in resolution.merged_names]
        if (
            len(set(normalized_names)) != len(normalized_names)
            or _normalized_name(resolution.canonical_name) not in normalized_names
            or any(name not in related_by_name for name in normalized_names)
            or any(
                related_by_name[name]["entity_type"] != resolution.entity_type
                for name in normalized_names
            )
            or resolved_names.intersection(normalized_names)
        ):
            raise ValueError("DEEP_ANALYSIS_ENTITY_RESOLUTION_INVALID")
        resolved_names.update(normalized_names)

    payload = output.model_dump(mode="json")
    item_specs = (
        ("fact_versions", "fct", lambda item: f"{item['subject']}:{item['predicate']}:{item['value']}:{item['valid_from_chapter']}"),
        ("state_changes", "stc", lambda item: f"{item['subject']}:{item['aspect']}:{item['chapter_ordinal']}:{item['after']}"),
        ("actor_knowledge", "akn", lambda item: f"{item['actor']}:{item['proposition']}:{item['chapter_ordinal']}"),
        ("world_rules", "wrl", lambda item: item["title"]),
        ("foreshadowing", "fsh", lambda item: f"{item['title']}:{item['setup_chapter']}"),
        ("conflicts", "cnf", lambda item: item["title"]),
        ("scene_analysis", "scn", lambda item: f"{item['chapter_ordinal']}:{item['function']}:{item['summary']}"),
        ("claims", "clm", lambda item: f"{item['claim_kind']}:{item['scope']}:{item['claim_text']}"),
        ("entity_resolutions", "ers", lambda item: f"{item['entity_type']}:{item['canonical_name']}:{':'.join(item['merged_names'])}"),
    )
    for collection, prefix, identity in item_specs:
        for index, item in enumerate(payload[collection], start=1):
            item["id"] = _item_id(prefix, run.id, index, identity(item))
    for fact in payload["fact_versions"]:
        if fact["counter_evidence_ids"]:
            fact["status"] = "DISPUTED"
    for claim in payload["claims"]:
        supports = bool(claim["evidence_ids"])
        counters = bool(claim["counter_evidence_ids"])
        claim["verification_status"] = (
            "MIXED"
            if supports and counters
            else "CONTRADICTED"
            if counters
            else "SUPPORTED"
            if supports
            else "INSUFFICIENT_EVIDENCE"
        )

    existing = session.scalar(
        select(DeepAnalysis).where(DeepAnalysis.created_by_task_id == task.id)
    )
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if existing is None:
        revision_no = (
            session.scalar(
                select(func.max(DeepAnalysis.revision_no)).where(
                    DeepAnalysis.run_id == run.id
                )
            )
            or 0
        ) + 1
        existing = DeepAnalysis(
            run_id=run.id,
            source_version_id=version.id,
            revision_no=revision_no,
            payload_json=payload_json,
            prompt_id=DEEP_PROMPT_ID,
            prompt_version=DEEP_PROMPT_VERSION,
            created_by_task_id=task.id,
            created_by_attempt_id=attempt_id,
        )
        session.add(existing)
    else:
        existing.payload_json = payload_json
        existing.created_by_attempt_id = attempt_id
    for request in task_payload.get("revision_requests", []):
        issue = session.get(AnalysisIssue, request.get("issue_id"))
        if issue is not None and issue.run_id == run.id and issue.status == "OPEN":
            issue.status = "RESOLVED"
            issue.resolved_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(existing)
    return PersistedDeepAnalysis(existing.id)


def refresh_analysis_run(session: Session, run: AnalysisRun) -> AnalysisRun:
    tasks = list(session.scalars(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(AnalysisRunTask.run_id == run.id)
    ))
    if not tasks:
        return run
    if any(task.status == TaskStatus.FAILED.value for task in tasks):
        run.status = AnalysisRunStatus.FAILED.value
    elif any(task.status in {TaskStatus.RUNNING.value, TaskStatus.RETRY_WAIT.value} for task in tasks):
        run.status = AnalysisRunStatus.RUNNING.value
    elif all(task.status == TaskStatus.SUCCEEDED.value for task in tasks):
        if not any(task.kind == NARRATIVE_SYNTHESIS_TASK_KIND for task in tasks):
            run.status = AnalysisRunStatus.PENDING.value
            session.commit()
            return run
        if not any(task.kind == DEEP_ANALYSIS_TASK_KIND for task in tasks):
            run.status = AnalysisRunStatus.PENDING.value
            session.commit()
            return run
        if run.status != AnalysisRunStatus.CONFIRMED.value:
            run.status = AnalysisRunStatus.REVIEW.value
            run.finished_at = datetime.now(timezone.utc)
    else:
        run.status = AnalysisRunStatus.PENDING.value
    session.commit()
    session.refresh(run)
    return run


def analysis_run_progress(session: Session, run: AnalysisRun) -> tuple[int, int]:
    completed = session.scalar(
        select(func.count(Task.id))
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(AnalysisRunTask.run_id == run.id, Task.status == TaskStatus.SUCCEEDED.value)
    ) or 0
    failed = session.scalar(
        select(func.count(Task.id))
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(AnalysisRunTask.run_id == run.id, Task.status == TaskStatus.FAILED.value)
    ) or 0
    return completed, failed


def confirm_analysis_run(session: Session, run: AnalysisRun) -> AnalysisRun:
    refresh_analysis_run(session, run)
    if run.status not in {AnalysisRunStatus.REVIEW.value, AnalysisRunStatus.CONFIRMED.value}:
        raise SourceImportError(
            "ANALYSIS_NOT_READY",
            "人物和事件分析尚未完成，暂时不能确认。",
            status_code=409,
        )
    synthesis = session.scalar(
        select(NarrativeSynthesis).where(NarrativeSynthesis.run_id == run.id)
    )
    if synthesis is None:
        raise SourceImportError(
            "NARRATIVE_SYNTHESIS_NOT_READY",
            "完整故事总览、人物角色和剧情结构尚未生成，暂时不能确认。",
            status_code=409,
        )
    deep_analysis = session.scalar(
        select(DeepAnalysis).where(DeepAnalysis.run_id == run.id)
    )
    if deep_analysis is None:
        raise SourceImportError(
            "DEEP_ANALYSIS_NOT_READY",
            "事实状态、世界设定和核心拆解分析尚未生成，暂时不能确认。",
            status_code=409,
        )
    if run.status != AnalysisRunStatus.CONFIRMED.value:
        run.status = AnalysisRunStatus.CONFIRMED.value
        run.confirmed_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(run)
    return run
