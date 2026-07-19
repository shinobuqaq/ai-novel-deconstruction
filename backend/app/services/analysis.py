from __future__ import annotations

import hashlib
import json
import math
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
    AnalysisDigest,
    AnalysisDigestLevel,
    AnalysisIssue,
    AnalysisRun,
    AnalysisRunStatus,
    AnalysisRunTask,
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
from .provider_config import (
    ENTITIES_EVENTS_PROFILE_ID,
    ModelSettingsError,
    model_cost_snapshot,
    resolve_analysis_profile,
)


ANALYSIS_TASK_KIND = "analysis.entities_events"
NARRATIVE_SYNTHESIS_TASK_KIND = "analysis.narrative_synthesis"
DEEP_ANALYSIS_TASK_KIND = "analysis.deep_insights"
HIERARCHICAL_DIGEST_TASK_KIND = "analysis.hierarchical_digest"
ANALYSIS_STAGE = "ENTITIES_EVENTS"
ANALYSIS_PROMPT_ID = "entities_events"
ANALYSIS_PROMPT_VERSION = "1.2.0"
NARRATIVE_PROMPT_ID = "narrative_synthesis"
NARRATIVE_PROMPT_VERSION = "1.5.0"
DEEP_PROMPT_ID = "deep_insights"
DEEP_PROMPT_VERSION = "1.4.0"
HIERARCHICAL_DIGEST_PROMPT_ID = "hierarchical_digest"
HIERARCHICAL_DIGEST_PROMPT_VERSION = "1.0.0"
MAX_BATCH_CHARS = 18_000
CHUNK_OVERLAP_CHARS = 600
MIN_SYNTHESIS_CONTEXT_CHARS = 24_000
MAX_SYNTHESIS_CONTEXT_CHARS = 160_000
MIN_CHAPTER_DIGEST_COUNT = 20
MAX_CHAPTER_DIGEST_COUNT = 40
TARGET_CHAPTERS_PER_DIGEST = 20
MAX_DIGEST_EVENTS = 1
MAX_DIGEST_SUMMARY_CHARS = 96
MAX_DIGEST_PARTICIPANTS = 4
MAX_DIGEST_EVIDENCE_IDS = 2
MAX_DIGEST_CHAPTER_TITLES = 4
KNOWN_CONTEXT_SAFETY_TOKENS = 4_096
MIN_HIERARCHICAL_SOURCE_CHARS = 120_000
MAX_RANGE_DIGEST_CHARS = 80_000
RANGE_DIGEST_TARGET_CHARS = 48_000
MAX_STAGE_DIGEST_INPUTS = 4
DEEP_ANALYSIS_COLLECTIONS = (
    "fact_versions",
    "state_changes",
    "actor_knowledge",
    "world_rules",
    "foreshadowing",
    "conflicts",
    "scene_analysis",
    "claims",
    "entity_resolutions",
)


def deep_revision_scope(revision_requests: list[dict] | None) -> list[str]:
    if not revision_requests:
        return list(DEEP_ANALYSIS_COLLECTIONS)
    broad_targets = {"CHARACTER", "STORY", "PLOT", "EVENT", "RELATION"}
    if any(str(item.get("target_kind") or "").upper() in broad_targets for item in revision_requests):
        return list(DEEP_ANALYSIS_COLLECTIONS)
    target_collections = {
        "FACT": {"fact_versions", "state_changes", "actor_knowledge", "claims"},
        "STATE": {"fact_versions", "state_changes", "actor_knowledge", "claims"},
        "KNOWLEDGE": {"actor_knowledge", "claims"},
        "WORLD": {"fact_versions", "world_rules", "claims", "entity_resolutions"},
        "FORESHADOWING": {"foreshadowing", "claims"},
        "CONFLICT": {"conflicts", "claims"},
        "PACING": {"scene_analysis", "claims"},
        "SCENE": {"scene_analysis", "claims"},
        "CLAIM": {"claims"},
        "ENTITY": {"world_rules", "fact_versions", "entity_resolutions", "claims"},
    }
    scope: set[str] = set()
    for item in revision_requests:
        scope.update(target_collections.get(str(item.get("target_kind") or "").upper(), set()))
    return [item for item in DEEP_ANALYSIS_COLLECTIONS if item in (scope or set(DEEP_ANALYSIS_COLLECTIONS))]


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
    opening_situation: str = Field(default="", max_length=1000)
    development_path: list[str] = Field(default_factory=list, max_length=12)
    turning_points: list[str] = Field(default_factory=list, max_length=8)
    current_situation: str = Field(default="", max_length=1000)
    current_result: str = Field(default="", max_length=1000)
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


class HierarchicalDigestOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=180)
    summary: str = Field(min_length=1, max_length=1800)
    situation: str = Field(default="", max_length=1000)
    goal: str = Field(default="", max_length=800)
    obstacle: str = Field(default="", max_length=800)
    key_actions: list[str] = Field(default_factory=list, max_length=12)
    outcome: str = Field(default="", max_length=1000)
    change: str = Field(default="", max_length=1000)
    next_hook: str = Field(default="", max_length=1000)
    character_progressions: list[str] = Field(default_factory=list, max_length=16)
    event_ids: list[str] = Field(default_factory=list, max_length=80)
    evidence_ids: list[str] = Field(default_factory=list, max_length=80)


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
    entity_type: Literal["PERSON", "ORGANIZATION", "PLACE", "OBJECT", "OTHER"]
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


@dataclass(frozen=True, slots=True)
class PersistedHierarchicalDigest:
    digest_id: str


@dataclass(frozen=True, slots=True)
class ContextMaterial:
    """One explainable piece of material considered for a model request."""

    key: str
    kind: str
    text: str
    priority: int
    reason: str
    chapter_ordinal: int | None = None


@dataclass(frozen=True, slots=True)
class ContextSelection:
    selected: tuple[ContextMaterial, ...]
    omitted: tuple[ContextMaterial, ...]
    budget_chars: int

    @property
    def selected_chars(self) -> int:
        return sum(len(item.text) for item in self.selected)

    @property
    def omitted_chars(self) -> int:
        return sum(len(item.text) for item in self.omitted)

    def manifest(self) -> dict[str, Any]:
        omitted_reasons: dict[str, int] = {}
        selected_by_kind: dict[str, int] = {}
        for item in self.omitted:
            omitted_reasons[item.reason] = omitted_reasons.get(item.reason, 0) + 1
        for item in self.selected:
            selected_by_kind[item.kind] = selected_by_kind.get(item.kind, 0) + 1
        return {
            "budget_chars": self.budget_chars,
            "selected_count": len(self.selected),
            "selected_chars": self.selected_chars,
            "omitted_count": len(self.omitted),
            "omitted_chars": self.omitted_chars,
            "selected_by_kind": selected_by_kind,
            "omitted_reasons": omitted_reasons,
            "selected_materials": [
                {
                    "key": item.key,
                    "kind": item.kind,
                    "chars": len(item.text),
                    "chapter_ordinal": item.chapter_ordinal,
                    "reason": item.reason,
                }
                for item in self.selected
            ],
            "omitted_materials": [
                {
                    "key": item.key,
                    "kind": item.kind,
                    "chars": len(item.text),
                    "chapter_ordinal": item.chapter_ordinal,
                    "reason": item.reason,
                }
                for item in self.omitted[:200]
            ],
        }


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalized_name(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()[:240]


@dataclass(frozen=True, slots=True)
class SynthesisContextBudget:
    budget_chars: int
    source: str
    context_window_tokens: int | None
    output_reserve_tokens: int
    safety_reserve_tokens: int


def _synthesis_context_budget(profile: Any, source_chars: int) -> SynthesisContextBudget:
    """Choose a request budget without turning the novel into a hard limit.

    The output reservation comes from the selected analysis profile. When a
    provider does not expose its context window, use a conservative automatic
    budget and grow it for larger output profiles, while keeping a ceiling so
    a single synthesis request cannot silently consume an entire long book.
    """
    output_reserve = max(1, int(getattr(profile, "max_output_tokens", 16_000)))
    context_window = getattr(profile, "context_window_tokens", None)
    if context_window is not None:
        context_window = int(context_window)
        remaining_tokens = max(1, context_window - output_reserve)
        safety_reserve = min(
            KNOWN_CONTEXT_SAFETY_TOKENS,
            max(0, remaining_tokens - 1_000),
        )
        available_tokens = max(1, remaining_tokens - safety_reserve)
        return SynthesisContextBudget(
            budget_chars=min(MAX_SYNTHESIS_CONTEXT_CHARS, available_tokens),
            source="MODEL_CONTEXT_WINDOW",
            context_window_tokens=context_window,
            output_reserve_tokens=output_reserve,
            safety_reserve_tokens=safety_reserve,
        )

    automatic_reserve = max(8_000, output_reserve)
    budget = max(
        MIN_SYNTHESIS_CONTEXT_CHARS,
        min(MAX_SYNTHESIS_CONTEXT_CHARS, automatic_reserve * 3),
    )
    if source_chars <= budget:
        budget = max(
            MIN_SYNTHESIS_CONTEXT_CHARS,
            min(MAX_SYNTHESIS_CONTEXT_CHARS, source_chars + 8_000),
        )
    return SynthesisContextBudget(
        budget_chars=budget,
        source="CONSERVATIVE_AUTO",
        context_window_tokens=None,
        output_reserve_tokens=output_reserve,
        safety_reserve_tokens=0,
    )


def _hierarchy_required(version: SourceVersion, profile: Any) -> bool:
    budget = _synthesis_context_budget(profile, version.total_chars)
    return version.total_chars > max(MIN_HIERARCHICAL_SOURCE_CHARS, budget.budget_chars)


def _range_digest_specs(
    chapter_units: list[SourceUnit],
    *,
    target_chars: int,
) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    current: list[tuple[int, SourceUnit]] = []
    current_chars = 0

    def flush() -> None:
        nonlocal current, current_chars
        if not current:
            return
        specs.append({
            "sequence_no": len(specs) + 1,
            "start_chapter": current[0][0],
            "end_chapter": current[-1][0],
            "source_unit_ids": [unit.id for _, unit in current],
            "start_char": min(unit.start_char for _, unit in current),
            "end_char": max(unit.end_char for _, unit in current),
        })
        current = []
        current_chars = 0

    for chapter_no, unit in enumerate(chapter_units, start=1):
        if current and current_chars + unit.char_count > target_chars:
            flush()
        current.append((chapter_no, unit))
        current_chars += unit.char_count
    flush()
    return specs


def _task_payload(task: Task) -> dict[str, Any]:
    try:
        value = json.loads(task.payload_json)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _append_run_task(
    session: Session,
    run: AnalysisRun,
    *,
    kind: str,
    payload: dict[str, Any],
    max_attempts: int,
) -> Task:
    task = Task(
        project_id=run.source_version.document.project_id,
        kind=kind,
        payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        max_attempts=max_attempts,
    )
    session.add(task)
    session.flush()
    next_index = run.total_batches + 1
    session.add(AnalysisRunTask(run_id=run.id, task_id=task.id, batch_index=next_index))
    run.total_batches = next_index
    run.status = AnalysisRunStatus.PENDING.value
    return task


def enqueue_hierarchical_digests(
    session: Session,
    settings: Settings,
    run: AnalysisRun,
) -> bool:
    """Advance long-book range -> stage summaries and report readiness."""
    version = session.get(SourceVersion, run.source_version_id)
    if version is None:
        return False
    try:
        _service, profile = resolve_analysis_profile(settings, ENTITIES_EVENTS_PROFILE_ID)
    except ModelSettingsError:
        return False
    if not _hierarchy_required(version, profile):
        return True

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
        return False

    digest_tasks = list(session.scalars(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run.id,
            Task.kind == HIERARCHICAL_DIGEST_TASK_KIND,
        )
        .order_by(AnalysisRunTask.batch_index)
    ))
    range_tasks = [
        task for task in digest_tasks if _task_payload(task).get("level") == AnalysisDigestLevel.RANGE.value
    ]
    if not range_tasks:
        chapter_units = list(session.scalars(
            select(SourceUnit)
            .where(
                SourceUnit.source_version_id == version.id,
                SourceUnit.unit_type == "CHAPTER",
            )
            .order_by(SourceUnit.ordinal)
        ))
        context_budget = _synthesis_context_budget(profile, version.total_chars)
        target_chars = min(
            MAX_RANGE_DIGEST_CHARS,
            max(MAX_BATCH_CHARS, min(RANGE_DIGEST_TARGET_CHARS, context_budget.budget_chars // 2)),
        )
        for spec in _range_digest_specs(chapter_units, target_chars=target_chars):
            _append_run_task(
                session,
                run,
                kind=HIERARCHICAL_DIGEST_TASK_KIND,
                payload={
                    "run_id": run.id,
                    "source_version_id": version.id,
                    "provider_name": "openai",
                    "model_profile_id": profile.id,
                    "level": AnalysisDigestLevel.RANGE.value,
                    **spec,
                },
                max_attempts=profile.max_retries + 1,
            )
        session.commit()
        return False
    if any(task.status != TaskStatus.SUCCEEDED.value for task in range_tasks):
        return False

    range_digests = list(session.scalars(
        select(AnalysisDigest)
        .where(
            AnalysisDigest.run_id == run.id,
            AnalysisDigest.level == AnalysisDigestLevel.RANGE.value,
        )
        .order_by(AnalysisDigest.sequence_no)
    ))
    if len(range_digests) != len(range_tasks):
        return False

    stage_tasks = [
        task for task in digest_tasks if _task_payload(task).get("level") == AnalysisDigestLevel.STAGE.value
    ]
    if not stage_tasks:
        for offset in range(0, len(range_digests), MAX_STAGE_DIGEST_INPUTS):
            group = range_digests[offset:offset + MAX_STAGE_DIGEST_INPUTS]
            _append_run_task(
                session,
                run,
                kind=HIERARCHICAL_DIGEST_TASK_KIND,
                payload={
                    "run_id": run.id,
                    "source_version_id": version.id,
                    "provider_name": "openai",
                    "model_profile_id": profile.id,
                    "level": AnalysisDigestLevel.STAGE.value,
                    "sequence_no": offset // MAX_STAGE_DIGEST_INPUTS + 1,
                    "start_chapter": group[0].start_chapter,
                    "end_chapter": group[-1].end_chapter,
                    "source_digest_ids": [item.id for item in group],
                    "source_unit_ids": [
                        unit_id
                        for item in group
                        for unit_id in json.loads(item.source_unit_ids_json)
                    ],
                    "start_char": 0,
                    "end_char": version.total_chars,
                },
                max_attempts=profile.max_retries + 1,
            )
        session.commit()
        return False
    if any(task.status != TaskStatus.SUCCEEDED.value for task in stage_tasks):
        return False
    stage_digest_count = session.scalar(
        select(func.count(AnalysisDigest.id)).where(
            AnalysisDigest.run_id == run.id,
            AnalysisDigest.level == AnalysisDigestLevel.STAGE.value,
        )
    ) or 0
    return stage_digest_count == len(stage_tasks)


def _material_json(kind: str, key: str, value: object) -> str:
    return json.dumps(
        {"kind": kind, "key": key, "value": value},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _select_context_materials(
    materials: list[ContextMaterial],
    *,
    budget_chars: int,
) -> ContextSelection:
    """Greedily select high-value material while preserving chapter coverage."""
    if budget_chars <= 0:
        return ContextSelection((), tuple(materials), 0)

    # A first item from every chapter is more valuable than a dense cluster of
    # evidence from one chapter. The caller supplies the chapter coverage
    # priority; this stable sort keeps retries deterministic.
    ranked = sorted(
        enumerate(materials),
        key=lambda pair: (-pair[1].priority, pair[0]),
    )
    selected: list[ContextMaterial] = []
    omitted: list[ContextMaterial] = []
    used = 0
    for _, item in ranked:
        size = len(item.text)
        if used + size <= budget_chars:
            selected.append(item)
            used += size
        else:
            omitted.append(
                ContextMaterial(
                    key=item.key,
                    kind=item.kind,
                    text=item.text,
                    priority=item.priority,
                    reason="上下文预算不足",
                    chapter_ordinal=item.chapter_ordinal,
                )
            )
    selected.sort(key=lambda item: (item.chapter_ordinal or 0, item.kind, item.key))
    omitted.sort(key=lambda item: (item.chapter_ordinal or 0, item.kind, item.key))
    return ContextSelection(tuple(selected), tuple(omitted), budget_chars)


def _compact_chapter_catalog(chapters: list[dict[str, object]]) -> str:
    return json.dumps(chapters, ensure_ascii=False, separators=(",", ":"))


def _chapter_digest_count(chapter_count: int) -> int:
    if chapter_count <= MIN_CHAPTER_DIGEST_COUNT:
        return chapter_count
    return min(
        MAX_CHAPTER_DIGEST_COUNT,
        max(MIN_CHAPTER_DIGEST_COUNT, math.ceil(chapter_count / TARGET_CHAPTERS_PER_DIGEST)),
    )


def _short_text(value: object, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return f"{text[: max(1, limit - 1)].rstrip()}…"


def _chapter_number(chapter: dict[str, object], fallback: int) -> int:
    value = chapter.get("chapter_number", chapter.get("ordinal", fallback))
    try:
        return int(value or fallback)
    except (TypeError, ValueError):
        return fallback


def _compact_range_titles(chapters: list[dict[str, object]]) -> tuple[list[str], int]:
    titles = [_short_text(item.get("title"), 80) for item in chapters]
    if len(titles) <= MAX_DIGEST_CHAPTER_TITLES:
        return titles, 0
    edge_count = MAX_DIGEST_CHAPTER_TITLES // 2
    return titles[:edge_count] + titles[-edge_count:], len(titles) - MAX_DIGEST_CHAPTER_TITLES


def _event_rank(item: dict[str, object]) -> tuple[int, int, int, int, str]:
    return (
        -int(item.get("confidence") or 0),
        -int(item.get("mention_count") or 0),
        -len(item.get("evidence_ids", [])),
        int(item.get("start_char") or 0),
        str(item.get("id") or item.get("title") or ""),
    )


def _build_chapter_digests(
    chapters: list[dict[str, object]],
    events: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Build a compact whole-book navigation layer without promoting it to fact."""
    if not chapters:
        return []

    digest_count = _chapter_digest_count(len(chapters))
    base_size, larger_ranges = divmod(len(chapters), digest_count)
    digests: list[dict[str, object]] = []
    cursor = 0
    for index in range(digest_count):
        range_size = base_size + (1 if index < larger_ranges else 0)
        range_chapters = chapters[cursor:cursor + range_size]
        cursor += range_size
        start_chapter = _chapter_number(range_chapters[0], cursor - range_size + 1)
        end_chapter = _chapter_number(range_chapters[-1], cursor)
        chapter_numbers = set(range(start_chapter, end_chapter + 1))
        range_events = [
            item
            for item in events
            if chapter_numbers.intersection(
                int(value)
                for value in item.get("chapter_ordinals", [])
                if value is not None
            )
        ]
        selected_events = sorted(
            sorted(range_events, key=_event_rank)[:MAX_DIGEST_EVENTS],
            key=lambda item: (
                int(item.get("start_char") or 0),
                str(item.get("title") or ""),
                str(item.get("id") or ""),
            ),
        )
        chapter_titles, omitted_title_count = _compact_range_titles(range_chapters)
        digests.append({
            "id": f"chapter-digest-{start_chapter}-{end_chapter}",
            "authority": "DERIVED_NAVIGATION_ONLY",
            "start_chapter": start_chapter,
            "end_chapter": end_chapter,
            "chapter_count": len(range_chapters),
            "chapter_titles": chapter_titles,
            "omitted_chapter_title_count": omitted_title_count,
            "main_events": [
                {
                    "event_id": item.get("id"),
                    "title": _short_text(item.get("title"), 80),
                    "summary": _short_text(item.get("summary"), MAX_DIGEST_SUMMARY_CHARS),
                    "participants": list(dict.fromkeys([
                        *item.get("people", []),
                        *item.get("related_entities", []),
                    ]))[:MAX_DIGEST_PARTICIPANTS],
                    "evidence_ids": list(item.get("evidence_ids", []))[:MAX_DIGEST_EVIDENCE_IDS],
                }
                for item in selected_events
            ],
            "event_count": len(range_events),
            "omitted_event_count": max(0, len(range_events) - len(selected_events)),
        })
    return digests


def _build_synthesis_context(
    *,
    foundation: dict[str, Any],
    chapters: list[dict[str, object]],
    evidence_by_id: dict[str, EvidenceSpan],
    chapter_title_by_id: dict[str, str],
    source_chars: int,
    profile: Any,
    extra_values: dict[str, object] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a bounded, source-addressable input for narrative/deep stages."""
    materials: list[ContextMaterial] = []
    events = list(foundation.get("events", []))
    chapter_digests = _build_chapter_digests(chapters, events)
    for item in chapter_digests:
        materials.append(ContextMaterial(
            key=str(item["id"]),
            kind="chapter_digest",
            text=_material_json("chapter_digest", str(item["id"]), item),
            priority=11_000,
            reason="保持长篇章节范围和代表事件覆盖",
            chapter_ordinal=int(item["start_chapter"]),
        ))
    chapter_catalog = _material_json("chapter_catalog", "chapter-catalog", chapters)
    materials.append(ContextMaterial(
        key="chapter-catalog",
        kind="chapter_catalog",
        text=chapter_catalog,
        priority=10_000,
        reason="保持整本章节覆盖",
    ))

    extra_priorities = {
        "revision_requests": 12_000,
        "hierarchical_digests": 11_500,
        "story_overview": 4_500,
        "narrative_phases": 4_000,
        "character_roles": 3_500,
        "previous_synthesis": 1_000,
        "previous_analysis": 1_000,
        "revision_scope": 12_000,
    }
    for key, value in (extra_values or {}).items():
        if value is None:
            continue
        materials.append(ContextMaterial(
            key=f"extra:{key}",
            kind=f"extra:{key}",
            text=_material_json(f"extra:{key}", key, value),
            priority=extra_priorities.get(key, 2_000),
            reason="任务必需的上游结果" if key in {"revision_requests", "story_overview"} else "上一阶段结果",
        ))

    characters = list(foundation.get("characters", []))
    for item in characters:
        appearance = int(item.get("appearance_count") or 0)
        confidence = int(item.get("confidence") or 0)
        payload = _material_json("character", str(item.get("id") or item.get("name")), item)
        materials.append(ContextMaterial(
            key=str(item.get("id") or item.get("name")),
            kind="character",
            text=payload,
            priority=2_000 + appearance * 5 + confidence,
            reason="人物身份和跨章连续性",
        ))

    related_entities = list(foundation.get("related_entities", []))
    for item in related_entities:
        payload = _material_json("related_entity", str(item.get("id") or item.get("name")), item)
        materials.append(ContextMaterial(
            key=str(item.get("id") or item.get("name")),
            kind="related_entity",
            text=payload,
            priority=1_500 + int(item.get("confidence") or 0),
            reason="世界设定和关联实体线索",
        ))

    seen_chapters: set[int] = set()
    for item in events:
        chapter_ordinals = [int(value) for value in item.get("chapter_ordinals", []) if value]
        first_chapter = min(chapter_ordinals) if chapter_ordinals else None
        coverage_bonus = 5_000 if first_chapter is not None and first_chapter not in seen_chapters else 0
        if first_chapter is not None:
            seen_chapters.add(first_chapter)
        confidence = int(item.get("confidence") or 0)
        payload = _material_json("event", str(item.get("id") or item.get("title")), item)
        materials.append(ContextMaterial(
            key=str(item.get("id") or item.get("title")),
            kind="event",
            text=payload,
            priority=4_000 + coverage_bonus + confidence,
            reason="剧情发展和章节覆盖",
            chapter_ordinal=first_chapter,
        ))

    for evidence_id, item in evidence_by_id.items():
        chapter_ordinal = next(
            (
                index
                for index, chapter in enumerate(chapters, start=1)
                if chapter_title_by_id.get(item.source_unit_id) == chapter.get("title")
            ),
            None,
        )
        payload = _material_json(
            "evidence",
            evidence_id,
            {
                "id": evidence_id,
                "chapter_title": chapter_title_by_id.get(item.source_unit_id, "章节待定"),
                "start_char": item.start_char,
                "end_char": item.end_char,
                "text": item.text_snapshot,
            },
        )
        materials.append(ContextMaterial(
            key=evidence_id,
            kind="evidence",
            text=payload,
            priority=1_000,
            reason="正式原文证据",
            chapter_ordinal=chapter_ordinal,
        ))

    context_budget = _synthesis_context_budget(profile, source_chars)
    selection = _select_context_materials(
        materials,
        budget_chars=context_budget.budget_chars,
    )
    selected_values: dict[str, Any] = {
        "chapters": [],
        "chapter_digests": [],
        "characters": [],
        "events": [],
        "related_entities": [],
        "evidence": [],
    }
    for item in selection.selected:
        try:
            value = json.loads(item.text)["value"]
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
        if item.kind == "chapter_catalog":
            selected_values["chapters"] = value
        elif item.kind == "chapter_digest":
            selected_values["chapter_digests"].append(value)
        elif item.kind.startswith("extra:"):
            selected_values[item.key.removeprefix("extra:")] = value
        elif item.kind == "character":
            selected_values["characters"].append(value)
        elif item.kind == "event":
            selected_values["events"].append(value)
        elif item.kind == "related_entity":
            selected_values["related_entities"].append(value)
        elif item.kind == "evidence":
            selected_values["evidence"].append(value)
    selected_values["context"] = {
        "selection_mode": "预算内相关材料选择",
        "budget_source": context_budget.source,
        "context_window_tokens": context_budget.context_window_tokens,
        "output_reserve_tokens": context_budget.output_reserve_tokens,
        "safety_reserve_tokens": context_budget.safety_reserve_tokens,
        "budget_chars": selection.budget_chars,
        "selected_count": len(selection.selected),
        "selected_chars": selection.selected_chars,
        "omitted_count": len(selection.omitted),
        "omitted_chars": selection.omitted_chars,
        "omitted_reasons": selection.manifest()["omitted_reasons"],
        "chapter_count": len(chapters),
        "chapter_digest_count": len(chapter_digests),
        "chapter_digest_complete": len(selected_values["chapter_digests"]) == len(chapter_digests),
        "chapter_catalog_complete": bool(selected_values["chapters"])
        and len(selected_values["chapters"]) == len(chapters),
    }
    return selected_values, selection.manifest()


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


def estimate_analysis_cost(
    session: Session,
    settings: Settings,
    version: SourceVersion,
) -> dict[str, object]:
    """Estimate a conservative run ceiling without sending content online."""
    try:
        _service, profile = resolve_analysis_profile(settings, ENTITIES_EVENTS_PROFILE_ID)
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

    context_budget = _synthesis_context_budget(profile, len(text))
    batch_input_chars = sum(item.end_char - item.start_char for item in batches)
    range_digest_count = 0
    stage_digest_count = 0
    hierarchy_input_chars = 0
    if _hierarchy_required(version, profile):
        chapter_units = [item for item in units if item.unit_type == "CHAPTER"]
        target_chars = min(
            MAX_RANGE_DIGEST_CHARS,
            max(MAX_BATCH_CHARS, min(RANGE_DIGEST_TARGET_CHARS, context_budget.budget_chars // 2)),
        )
        range_digest_count = len(
            _range_digest_specs(chapter_units, target_chars=target_chars)
        )
        stage_digest_count = math.ceil(range_digest_count / MAX_STAGE_DIGEST_INPUTS)
        hierarchy_input_chars = version.total_chars + range_digest_count * 4_000
    estimated_input_tokens = (
        batch_input_chars
        + hierarchy_input_chars
        + context_budget.budget_chars * 2
    )
    planned_call_count = (
        len(batches) + range_digest_count + stage_digest_count + 2
    )
    maximum_output_tokens = planned_call_count * profile.max_output_tokens
    retry_multiplier = profile.max_retries + 1
    normal_cost = model_cost_snapshot(
        profile,
        prompt_tokens=estimated_input_tokens,
        completion_tokens=maximum_output_tokens,
    )
    retry_cost = model_cost_snapshot(
        profile,
        prompt_tokens=estimated_input_tokens * retry_multiplier,
        completion_tokens=maximum_output_tokens * retry_multiplier,
    )
    return {
        "source_version_id": version.id,
        "batch_count": len(batches),
        "planned_call_count": planned_call_count,
        "retry_ceiling_call_count": planned_call_count * retry_multiplier,
        "estimated_input_tokens": estimated_input_tokens,
        "maximum_output_tokens": maximum_output_tokens,
        "maximum_cost_without_retries": (
            normal_cost["total_cost"] if normal_cost is not None else None
        ),
        "maximum_cost_with_retries": (
            retry_cost["total_cost"] if retry_cost is not None else None
        ),
        "cost_currency": profile.price_currency if normal_cost is not None else None,
        "pricing_available": normal_cost is not None,
        "basis": (
            "按当前分批数量、必要的长篇范围与阶段整理、两次全局分析、最大输出设置"
            "和字符数近似令牌数计算；"
            "这是避免低估的保守上限，不是预扣费或最终账单。"
        ),
    }


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
        "context_manifest": {
            "budget_chars": MAX_BATCH_CHARS,
            "selected_count": 1,
            "selected_chars": len(excerpt),
            "omitted_count": 0,
            "omitted_chars": 0,
            "selected_by_kind": {"source_batch": 1},
            "omitted_reasons": {},
            "selected_materials": [{
                "key": f"source:{start}-{end}",
                "kind": "source_batch",
                "chars": len(excerpt),
                "chapter_ordinal": None,
                "reason": "当前章节分批原文",
            }],
            "omitted_materials": [],
        },
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


def _hierarchical_digest_prompt() -> str:
    path = Path(__file__).resolve().parents[3] / "prompts" / "hierarchical_digest_v1.md"
    return path.read_text(encoding="utf-8").strip()


def provider_payload_for_hierarchical_digest(
    session: Session,
    settings: Settings,
    task_payload: dict,
) -> dict:
    from .workbench import build_workbench_projection

    run_id = str(task_payload.get("run_id") or "")
    version = session.get(SourceVersion, task_payload.get("source_version_id"))
    if not run_id or version is None:
        raise ValueError("ANALYSIS_RUN_NOT_FOUND")
    level = str(task_payload.get("level") or "")
    if level not in {AnalysisDigestLevel.RANGE.value, AnalysisDigestLevel.STAGE.value}:
        raise ValueError("ANALYSIS_DIGEST_LEVEL_INVALID")

    start_chapter = int(task_payload.get("start_chapter") or 1)
    end_chapter = int(task_payload.get("end_chapter") or start_chapter)
    foundation = build_workbench_projection(session, run_id, include_synthesis=False)
    source_unit_ids = [str(value) for value in task_payload.get("source_unit_ids", [])]
    chapter_units = list(session.scalars(
        select(SourceUnit)
        .where(
            SourceUnit.source_version_id == version.id,
            SourceUnit.id.in_(source_unit_ids),
        )
        .order_by(SourceUnit.ordinal)
    ))
    if not chapter_units:
        raise ValueError("ANALYSIS_DIGEST_SOURCE_UNIT_INVALID")
    chapter_by_unit_id = {
        unit.id: start_chapter + index
        for index, unit in enumerate(chapter_units)
    }

    if level == AnalysisDigestLevel.RANGE.value:
        start_char = int(task_payload.get("start_char") or chapter_units[0].start_char)
        end_char = int(task_payload.get("end_char") or chapter_units[-1].end_char)
        events = [
            item
            for item in foundation.get("events", [])
            if int(item.get("end_char") or 0) > start_char
            and int(item.get("start_char") or 0) < end_char
        ]
        evidence_ids = {
            evidence_id
            for event in events
            for evidence_id in event.get("evidence_ids", [])
        }
        evidence_rows = list(session.scalars(
            select(EvidenceSpan)
            .where(
                EvidenceSpan.source_version_id == version.id,
                EvidenceSpan.id.in_(evidence_ids),
            )
            .order_by(EvidenceSpan.start_char)
        ))
        text = source_text(settings, version)
        input_payload = {
            "authority": "DERIVED_NAVIGATION_ONLY",
            "level": level,
            "chapter_range": {
                "start": start_chapter,
                "end": end_chapter,
                "chapters": [
                    {
                        "chapter_number": chapter_by_unit_id.get(unit.id),
                        "title": unit.title,
                    }
                    for unit in chapter_units
                ],
            },
            "source_excerpt": text[start_char:end_char],
            "events": events,
            "evidence": [
                {
                    "id": item.id,
                    "chapter_number": chapter_by_unit_id.get(item.source_unit_id),
                    "start_char": item.start_char,
                    "end_char": item.end_char,
                    "text": item.text_snapshot,
                }
                for item in evidence_rows
            ],
        }
        source_char_start = start_char
        source_char_end = end_char
    else:
        source_digest_ids = [str(value) for value in task_payload.get("source_digest_ids", [])]
        source_digests = list(session.scalars(
            select(AnalysisDigest)
            .where(
                AnalysisDigest.run_id == run_id,
                AnalysisDigest.id.in_(source_digest_ids),
            )
            .order_by(AnalysisDigest.sequence_no)
        ))
        if len(source_digests) != len(source_digest_ids):
            raise ValueError("ANALYSIS_DIGEST_SOURCE_MISSING")
        input_payload = {
            "authority": "DERIVED_NAVIGATION_ONLY",
            "level": level,
            "chapter_range": {"start": start_chapter, "end": end_chapter},
            "source_digests": [
                {
                    "id": item.id,
                    "start_chapter": item.start_chapter,
                    "end_chapter": item.end_chapter,
                    **json.loads(item.payload_json),
                }
                for item in source_digests
            ],
        }
        source_char_start = 0
        source_char_end = version.total_chars

    return {
        "instructions": _hierarchical_digest_prompt(),
        "input": json.dumps(input_payload, ensure_ascii=False, separators=(",", ":")),
        "output_schema": _inline_model_schema(HierarchicalDigestOutput),
        "model_profile_id": str(task_payload.get("model_profile_id") or ENTITIES_EVENTS_PROFILE_ID),
        "prompt_id": HIERARCHICAL_DIGEST_PROMPT_ID,
        "prompt_version": HIERARCHICAL_DIGEST_PROMPT_VERSION,
        "source_version_id": version.id,
        "source_char_start": source_char_start,
        "source_char_end": source_char_end,
        "context_manifest": {
            "budget_chars": len(json.dumps(input_payload, ensure_ascii=False)),
            "selected_count": len(input_payload.get("events", input_payload.get("source_digests", []))),
            "selected_chars": len(json.dumps(input_payload, ensure_ascii=False)),
            "omitted_count": 0,
            "omitted_chars": 0,
            "selected_by_kind": {level.lower(): 1},
            "omitted_reasons": {},
        },
    }


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
    service, model_profile = resolve_analysis_profile(
        settings,
        str(task_payload.get("model_profile_id") or ENTITIES_EVENTS_PROFILE_ID),
    )
    previous_synthesis = session.scalar(
        select(NarrativeSynthesis).where(NarrativeSynthesis.run_id == run_id)
    )
    previous_synthesis_payload = (
        json.loads(previous_synthesis.payload_json)
        if previous_synthesis is not None
        else None
    )
    hierarchical_digests = [
        {
            "id": item.id,
            "authority": "DERIVED_NAVIGATION_ONLY",
            "level": item.level,
            "sequence_no": item.sequence_no,
            "start_chapter": item.start_chapter,
            "end_chapter": item.end_chapter,
            "source_digest_ids": json.loads(item.source_digest_ids_json),
            "source_event_ids": json.loads(item.source_event_ids_json),
            "evidence_ids": json.loads(item.evidence_ids_json),
            **json.loads(item.payload_json),
        }
        for item in session.scalars(
            select(AnalysisDigest)
            .where(
                AnalysisDigest.run_id == run_id,
                AnalysisDigest.level == AnalysisDigestLevel.STAGE.value,
            )
            .order_by(AnalysisDigest.sequence_no)
        )
    ]
    selected, context_manifest = _build_synthesis_context(
        foundation=foundation,
        chapters=chapters,
        evidence_by_id=evidence_by_id,
        chapter_title_by_id=chapter_title_by_id,
        source_chars=version.total_chars,
        profile=model_profile,
        extra_values={
            "hierarchical_digests": hierarchical_digests or None,
            "previous_synthesis": previous_synthesis_payload,
            "revision_requests": task_payload.get("revision_requests", []),
        },
    )
    input_payload = {
        **selected,
        "hierarchical_digests": selected.get("hierarchical_digests", []),
        "previous_synthesis": selected.get("previous_synthesis"),
        "revision_requests": selected.get("revision_requests", []),
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
        "context_manifest": context_manifest,
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
    service, model_profile = resolve_analysis_profile(
        settings,
        str(task_payload.get("model_profile_id") or ENTITIES_EVENTS_PROFILE_ID),
    )
    previous_analysis = session.scalar(
        select(DeepAnalysis)
        .where(DeepAnalysis.run_id == run_id)
        .order_by(DeepAnalysis.revision_no.desc())
    )
    previous_analysis_payload = (
        json.loads(previous_analysis.payload_json)
        if previous_analysis is not None
        else None
    )
    hierarchical_digests = [
        {
            "id": item.id,
            "authority": "DERIVED_NAVIGATION_ONLY",
            "level": item.level,
            "sequence_no": item.sequence_no,
            "start_chapter": item.start_chapter,
            "end_chapter": item.end_chapter,
            "source_digest_ids": json.loads(item.source_digest_ids_json),
            "source_event_ids": json.loads(item.source_event_ids_json),
            "evidence_ids": json.loads(item.evidence_ids_json),
            **json.loads(item.payload_json),
        }
        for item in session.scalars(
            select(AnalysisDigest)
            .where(
                AnalysisDigest.run_id == run_id,
                AnalysisDigest.level == AnalysisDigestLevel.STAGE.value,
            )
            .order_by(AnalysisDigest.sequence_no)
        )
    ]
    selected, context_manifest = _build_synthesis_context(
        foundation=foundation,
        chapters=chapters,
        evidence_by_id={item.id: item for item in evidence_rows},
        chapter_title_by_id=chapter_title_by_id,
        source_chars=version.total_chars,
        profile=model_profile,
        extra_values={
            "hierarchical_digests": hierarchical_digests or None,
            "story_overview": narrative.get("story_overview"),
            "character_roles": narrative.get("character_roles", []),
            "narrative_phases": narrative.get("narrative_phases", []),
            "previous_analysis": previous_analysis_payload,
            "revision_requests": task_payload.get("revision_requests", []),
            "revision_scope": task_payload.get("revision_scope", list(DEEP_ANALYSIS_COLLECTIONS)),
        },
    )
    input_payload = {
        **selected,
        "hierarchical_digests": selected.get("hierarchical_digests", []),
        "story_overview": selected.get("story_overview"),
        "character_roles": selected.get("character_roles", []),
        "narrative_phases": selected.get("narrative_phases", []),
        "previous_analysis": selected.get("previous_analysis"),
        "revision_requests": selected.get("revision_requests", []),
        "revision_scope": selected.get("revision_scope", list(DEEP_ANALYSIS_COLLECTIONS)),
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
        "context_manifest": context_manifest,
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


def parse_hierarchical_digest(value: dict) -> HierarchicalDigestOutput:
    try:
        return HierarchicalDigestOutput.model_validate(value)
    except ValidationError as exc:
        raise StructuredOutputValidationError(
            "HIERARCHICAL_DIGEST_OUTPUT_INVALID",
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


def _claim_verification(claim: dict[str, Any]) -> tuple[str, str]:
    support_count = len(set(claim.get("evidence_ids", [])))
    counter_count = len(set(claim.get("counter_evidence_ids", [])))
    required_support = 1 if claim.get("claim_kind") in {"FACT", "INFERENCE"} else 2
    if counter_count and not support_count:
        status = "CONTRADICTED"
    elif counter_count:
        status = "DISPUTED"
    elif support_count < required_support:
        status = "INSUFFICIENT"
    elif int(claim.get("confidence") or 0) < 75:
        status = "PARTIAL"
    else:
        status = "SUPPORTED"
    note = (
        f"支持证据 {support_count} 条，反面证据 {counter_count} 条；"
        f"{claim.get('claim_kind', 'UNKNOWN')} 类型至少需要 {required_support} 条支持证据。"
    )
    return status, note


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


def persist_hierarchical_digest(
    session: Session,
    *,
    task: Task,
    attempt_id: str,
    task_payload: dict,
    output: HierarchicalDigestOutput,
) -> PersistedHierarchicalDigest:
    run = session.get(AnalysisRun, task_payload.get("run_id"))
    version = session.get(SourceVersion, task_payload.get("source_version_id"))
    if run is None or version is None:
        raise ValueError("ANALYSIS_RUN_NOT_FOUND")
    level = str(task_payload.get("level") or "")
    if level not in {AnalysisDigestLevel.RANGE.value, AnalysisDigestLevel.STAGE.value}:
        raise ValueError("ANALYSIS_DIGEST_LEVEL_INVALID")

    source_unit_ids = [str(value) for value in task_payload.get("source_unit_ids", [])]
    source_units = list(session.scalars(
        select(SourceUnit).where(
            SourceUnit.source_version_id == version.id,
            SourceUnit.id.in_(source_unit_ids),
        )
    ))
    if len(source_units) != len(set(source_unit_ids)):
        raise ValueError("ANALYSIS_DIGEST_SOURCE_UNIT_INVALID")

    from .workbench import build_workbench_projection

    foundation = build_workbench_projection(session, run.id, include_synthesis=False)
    valid_event_ids = {item["id"] for item in foundation.get("events", [])}
    if not set(output.event_ids).issubset(valid_event_ids):
        raise ValueError("ANALYSIS_DIGEST_EVENT_REFERENCE_INVALID")

    source_digest_ids = [str(value) for value in task_payload.get("source_digest_ids", [])]
    source_digests = list(session.scalars(
        select(AnalysisDigest).where(
            AnalysisDigest.run_id == run.id,
            AnalysisDigest.id.in_(source_digest_ids),
        )
    ))
    if level == AnalysisDigestLevel.RANGE.value:
        start_char = int(task_payload.get("start_char") or 0)
        end_char = int(task_payload.get("end_char") or version.total_chars)
        source_event_ids = {
            item["id"]
            for item in foundation.get("events", [])
            if int(item.get("end_char") or 0) > start_char
            and int(item.get("start_char") or 0) < end_char
        }
        source_evidence_ids = {
            evidence_id
            for item in foundation.get("events", [])
            if item.get("id") in source_event_ids
            for evidence_id in item.get("evidence_ids", [])
        }
    else:
        if len(source_digests) != len(set(source_digest_ids)):
            raise ValueError("ANALYSIS_DIGEST_SOURCE_MISSING")
        source_event_ids = {
            event_id
            for item in source_digests
            for event_id in json.loads(item.source_event_ids_json)
        }
        source_evidence_ids = {
            evidence_id
            for item in source_digests
            for evidence_id in json.loads(item.evidence_ids_json)
        }
    if not set(output.event_ids).issubset(source_event_ids):
        raise ValueError("ANALYSIS_DIGEST_EVENT_OUTSIDE_SOURCE")
    if not set(output.evidence_ids).issubset(source_evidence_ids):
        raise ValueError("ANALYSIS_DIGEST_EVIDENCE_OUTSIDE_SOURCE")
    valid_evidence_count = session.scalar(
        select(func.count(EvidenceSpan.id)).where(
            EvidenceSpan.source_version_id == version.id,
            EvidenceSpan.id.in_(source_evidence_ids),
        )
    ) or 0
    if valid_evidence_count != len(source_evidence_ids):
        raise ValueError("ANALYSIS_DIGEST_EVIDENCE_REFERENCE_INVALID")

    fingerprint_payload = {
        "level": level,
        "source_units": sorted(
            (item.id, item.content_hash) for item in source_units
        ),
        "source_digests": sorted(
            (item.id, item.source_fingerprint) for item in source_digests
        ),
        "events": sorted(source_event_ids),
        "evidence": sorted(source_evidence_ids),
    }
    payload_json = json.dumps(output.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    existing = session.scalar(
        select(AnalysisDigest).where(
            AnalysisDigest.run_id == run.id,
            AnalysisDigest.level == level,
            AnalysisDigest.sequence_no == int(task_payload.get("sequence_no") or 1),
        )
    )
    values = {
        "source_version_id": version.id,
        "start_chapter": int(task_payload.get("start_chapter") or 1),
        "end_chapter": int(task_payload.get("end_chapter") or 1),
        "payload_json": payload_json,
        "source_digest_ids_json": json.dumps(source_digest_ids, sort_keys=True),
        "source_event_ids_json": json.dumps(sorted(source_event_ids), sort_keys=True),
        "evidence_ids_json": json.dumps(sorted(source_evidence_ids), sort_keys=True),
        "source_unit_ids_json": json.dumps(source_unit_ids, sort_keys=True),
        "source_fingerprint": _hash(json.dumps(fingerprint_payload, sort_keys=True)),
        "prompt_id": HIERARCHICAL_DIGEST_PROMPT_ID,
        "prompt_version": HIERARCHICAL_DIGEST_PROMPT_VERSION,
        "created_by_task_id": task.id,
        "created_by_attempt_id": attempt_id,
    }
    if existing is None:
        existing = AnalysisDigest(
            run_id=run.id,
            level=level,
            sequence_no=int(task_payload.get("sequence_no") or 1),
            **values,
        )
        session.add(existing)
    else:
        for key, value in values.items():
            setattr(existing, key, value)
    session.commit()
    session.refresh(existing)
    return PersistedHierarchicalDigest(existing.id)


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
    if not enqueue_hierarchical_digests(session, settings, run):
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
    required_characters = sorted(
        foundation["characters"],
        key=lambda item: (
            -int(item.get("appearance_count") or 0),
            -int(item.get("confidence") or 0),
            item.get("name", ""),
        ),
    )[:100]
    returned_role_names = {
        _normalized_name(item.name)
        for item in output.character_roles
    }
    missing_required = [
        item["name"]
        for item in required_characters
        if not {
            _normalized_name(item["name"]),
            *(_normalized_name(alias) for alias in item.get("aliases", [])),
        }.intersection(returned_role_names)
    ]
    if missing_required:
        raise ValueError("NARRATIVE_CHARACTER_ROLES_INCOMPLETE")
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
                "revision_scope": deep_revision_scope(revision_requests),
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
    # The selected chapter catalog may be abbreviated for a very long source;
    # validation must use the authoritative imported source version instead
    # of treating the abbreviated request as the whole book.
    chapter_count = int(version.chapter_count)

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
    available_entities_by_name: dict[str, tuple[str, str]] = {}
    for item in foundation["characters"]:
        for name in [item["name"], *item.get("aliases", [])]:
            available_entities_by_name[_normalized_name(name)] = ("PERSON", item["id"])
    for item in foundation["related_entities"]:
        for name in [item["name"], *item.get("aliases", [])]:
            available_entities_by_name[_normalized_name(name)] = (item["entity_type"], item["id"])
    resolved_names: set[str] = set()
    for resolution in output.entity_resolutions:
        normalized_names = [_normalized_name(name) for name in resolution.merged_names]
        entity_ids = {
            available_entities_by_name.get(name, (None, None))[1]
            for name in normalized_names
        }
        if (
            len(set(normalized_names)) != len(normalized_names)
            or _normalized_name(resolution.canonical_name) not in normalized_names
            or any(name not in available_entities_by_name for name in normalized_names)
            or any(
                available_entities_by_name[name][0] != resolution.entity_type
                for name in normalized_names
            )
            or len(entity_ids) != len(normalized_names)
            or None in entity_ids
            or resolved_names.intersection(normalized_names)
        ):
            raise ValueError("DEEP_ANALYSIS_ENTITY_RESOLUTION_INVALID")
        resolved_names.update(normalized_names)

    payload = output.model_dump(mode="json")
    revision_scope = set(task_payload.get("revision_scope") or DEEP_ANALYSIS_COLLECTIONS)
    if task_payload.get("revision_requests") and revision_scope != set(DEEP_ANALYSIS_COLLECTIONS):
        previous = session.scalar(
            select(DeepAnalysis)
            .where(DeepAnalysis.run_id == run.id)
            .order_by(DeepAnalysis.revision_no.desc())
        )
        if previous is None:
            raise ValueError("DEEP_ANALYSIS_PREVIOUS_REVISION_MISSING")
        previous_payload = json.loads(previous.payload_json)
        for collection in DEEP_ANALYSIS_COLLECTIONS:
            if collection not in revision_scope:
                payload[collection] = previous_payload.get(collection, [])
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
        verification_status, verification_note = _claim_verification(claim)
        claim["verification_status"] = verification_status
        claim["verification_note"] = verification_note

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
            run.status = AnalysisRunStatus.REVIEW.value
            run.finished_at = datetime.now(timezone.utc)
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
