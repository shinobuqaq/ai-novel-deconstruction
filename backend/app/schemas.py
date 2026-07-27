from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime


class TaskCreate(BaseModel):
    project_id: str
    kind: str = Field(default="fake.echo", pattern=r"^[a-z0-9_.-]+$")
    payload: dict[str, Any] = Field(default_factory=dict)
    max_attempts: int = Field(default=3, ge=1, le=10)


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    kind: str
    status: str
    payload: dict[str, Any]
    result_artifact_id: str | None
    attempts: int
    max_attempts: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    current_attempt_id: str | None
    lease_generation: int
    next_attempt_at: datetime | None
    cancel_requested_at: datetime | None
    last_error_code: str | None
    last_error_message: str | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime


class ArtifactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    kind: str
    schema_version: str
    status: str
    result_key: str
    blob_id: str
    content_hash: str
    relative_path: str
    created_by_task_id: str | None
    created_by_attempt_id: str | None
    lease_generation: int | None
    metadata: dict[str, Any]
    created_at: datetime


class FakeEchoRequest(BaseModel):
    message: str = "hello"


class SourceDocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    original_filename: str
    source_format: str
    created_at: datetime


class SourceVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    document_id: str
    version_no: int
    content_hash: str
    parser_version: int
    total_chars: int
    chapter_count: int
    detected_encoding: str | None
    status: str
    created_at: datetime
    confirmed_at: datetime | None


class SourceUnitRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_version_id: str
    ordinal: int
    unit_type: str
    title: str
    start_char: int
    end_char: int
    content_hash: str
    char_count: int


class SourceUnitUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    unit_type: str = Field(pattern=r"^(TITLE|PREFACE|VOLUME|CHAPTER|DOCUMENT)$")


class SourceUnitSplit(BaseModel):
    split_char: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=500)
    unit_type: str = Field(default="CHAPTER", pattern=r"^(VOLUME|CHAPTER)$")


class SourceUnitMerge(BaseModel):
    direction: str = Field(pattern=r"^(PREVIOUS|NEXT)$")


class SourceIssueRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_version_id: str
    source_unit_id: str | None
    code: str
    severity: str
    message: str
    details: dict[str, Any]
    status: str
    created_at: datetime
    resolved_at: datetime | None


class SourceImportRead(BaseModel):
    document: SourceDocumentRead
    version: SourceVersionRead
    units: list[SourceUnitRead]
    issues: list[SourceIssueRead]
    reused_existing: bool


class SourceStructureRead(BaseModel):
    version: SourceVersionRead
    units: list[SourceUnitRead]
    issues: list[SourceIssueRead]
    selected_unit_id: str


class SourceUnitContentRead(BaseModel):
    id: str
    source_version_id: str
    ordinal: int
    title: str
    start_char: int
    end_char: int
    content: str


class EvidenceSpanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_version_id: str
    source_unit_id: str
    paragraph_index: int
    start_char: int
    end_char: int
    text_snapshot: str
    context_hash: str


class EvidenceContextRead(BaseModel):
    evidence: EvidenceSpanRead
    chapter_title: str
    context_start: int
    context_end: int
    context_text: str


class OpenAIConfigRead(BaseModel):
    configured: bool
    base_url: str
    model: str


class OpenAIConfigWrite(BaseModel):
    api_key: str | None = Field(default=None, max_length=500)
    base_url: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=200)


class ModelServiceRead(BaseModel):
    id: str
    name: str
    service_type: str
    base_url: str
    configured: bool
    last_tested_at: datetime | None
    last_test_status: str
    last_test_message: str | None
    capabilities: dict[str, str | None]


class ModelServiceWrite(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    service_type: str = Field(max_length=50)
    base_url: str = Field(min_length=1, max_length=500)
    api_key: str | None = Field(default=None, max_length=500)


class AnalysisProfileRead(BaseModel):
    id: str
    name: str
    task_type: str
    service_id: str
    model: str
    temperature: float | None
    max_output_tokens: int
    reasoning_effort: str
    timeout_seconds: float
    max_retries: int
    context_window_tokens: int | None
    failover_targets: list["FailoverTargetWrite"] = Field(default_factory=list)


class FailoverTargetWrite(BaseModel):
    service_id: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)


class AnalysisProfileWrite(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    service_id: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_output_tokens: int = Field(ge=1, le=128_000)
    reasoning_effort: str = Field(max_length=20)
    timeout_seconds: float = Field(ge=10, le=1800)
    max_retries: int = Field(ge=0, le=10)
    context_window_tokens: int | None = Field(default=None, ge=1, le=10_000_000)
    failover_targets: list[FailoverTargetWrite] = Field(default_factory=list, max_length=10)


class ModelSettingsRead(BaseModel):
    services: list[ModelServiceRead]
    analysis_profiles: list[AnalysisProfileRead]


class ModelCatalogRead(BaseModel):
    service_id: str
    models: list[str]
    message: str


class ModelConnectionRead(BaseModel):
    service: ModelServiceRead
    model_count: int
    message: str


class ModelProbeRead(BaseModel):
    service: ModelServiceRead
    message: str


class ProviderConfirmationRead(BaseModel):
    current_service_name: str
    current_model: str
    next_service_name: str
    next_model: str
    failure_count: int
    threshold: int
    error_code: str
    message: str


class ProviderConfirmationWrite(BaseModel):
    decision: str = Field(pattern="^(SWITCH|RETRY_CURRENT|STOP)$")


class AnalysisRunRead(BaseModel):
    id: str
    source_version_id: str
    stage: str
    status: str
    total_batches: int
    completed_batches: int
    failed_batches: int
    failure_code: str | None
    failure_message: str | None
    has_usable_result: bool = False
    usable_result_level: str = "NONE"
    latest_update_failed: bool = False
    created_at: datetime
    finished_at: datetime | None
    confirmed_at: datetime | None
    provider_confirmation: ProviderConfirmationRead | None = None


class AnalysisCallDiagnosticRead(BaseModel):
    attempt_id: str
    task_id: str
    task_kind: str
    component: str | None = None
    component_label: str | None = None
    attempt_no: int
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    duration_seconds: float = 0
    provider_name: str | None = None
    model: str | None = None
    transport_mode: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    input_chars: int = 0
    output_chars: int = 0
    selected_material_count: int = 0
    selected_material_chars: int = 0
    omitted_material_count: int = 0
    omitted_material_chars: int = 0
    omitted_material_reasons: dict[str, int] = Field(default_factory=dict)
    error_message: str | None = None
    result_artifact_id: str | None = None
    has_input_content: bool = False
    has_output_content: bool = False
    can_retry_component: bool = False


class AnalysisCallContentRead(BaseModel):
    attempt_id: str
    input_text: str | None = None
    output_text: str | None = None
    input_note: str | None = None
    output_note: str | None = None


class AnalysisStageDiagnosticRead(BaseModel):
    key: str
    label: str
    status: str
    task_count: int
    attempt_count: int
    retry_count: int
    duration_seconds: float = 0
    prompt_tokens: int
    completion_tokens: int
    input_chars: int
    output_chars: int
    selected_material_count: int = 0
    selected_material_chars: int = 0
    omitted_material_count: int = 0
    omitted_material_chars: int = 0
    omitted_material_reasons: dict[str, int] = Field(default_factory=dict)
    latest_error: str | None
    calls: list[AnalysisCallDiagnosticRead] = Field(default_factory=list)


class AnalysisRunDiagnosticsRead(BaseModel):
    run_id: str
    current_step: str
    attempt_count: int
    retry_count: int
    duration_seconds: float = 0
    prompt_tokens: int
    completion_tokens: int
    input_chars: int
    output_chars: int
    stages: list[AnalysisStageDiagnosticRead]


class AnalysisUsageEstimateRead(BaseModel):
    source_version_id: str
    batch_count: int
    planned_call_count: int
    retry_ceiling_call_count: int
    estimated_input_tokens: int
    maximum_output_tokens: int
    basis: str


class EntityCandidateRead(BaseModel):
    id: str
    run_id: str
    source_version_id: str
    name: str
    entity_type: str
    aliases: list[str]
    description: str
    evidence_ids: list[str]
    status: str
    confidence: int


class EventCandidateRead(BaseModel):
    id: str
    run_id: str
    source_version_id: str
    title: str
    event_type: str
    summary: str
    participants: list[str]
    evidence_ids: list[str]
    start_char: int
    end_char: int
    status: str
    confidence: int


class WorkbenchCharacterRead(BaseModel):
    id: str
    name: str
    aliases: list[str]
    description: str
    evidence_ids: list[str]
    event_ids: list[str]
    first_chapter_ordinal: int | None
    first_chapter_title: str | None
    last_chapter_ordinal: int | None
    last_chapter_title: str | None
    appearance_count: int
    activity_level: str
    status: str
    confidence: int
    role_required: bool = True
    role: str = "UNCLASSIFIED"
    role_reason: str = ""
    goals: list[str] = Field(default_factory=list)
    motivations: list[str] = Field(default_factory=list)
    identities: list[str] = Field(default_factory=list)
    abilities: list[str] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)
    important_experiences: list[str] = Field(default_factory=list)
    current_state: str = ""
    arc_summary: str = ""
    identity_notes: list[str] = Field(default_factory=list)


class WorkbenchPersonIdentityCandidateRead(BaseModel):
    candidate_key: str
    left_name: str
    right_name: str
    left_aliases: list[str] = Field(default_factory=list)
    right_aliases: list[str] = Field(default_factory=list)
    recommended_name: str
    reason: str
    signals: list[str] = Field(default_factory=list)
    confidence: int
    review_priority: str
    recommended_decision: str
    cooccurrence_count: int = 0
    evidence_ids: list[str] = Field(default_factory=list)


class WorkbenchPersonIdentityDecisionRead(BaseModel):
    id: str
    candidate_key: str
    left_name: str
    right_name: str
    canonical_name: str | None
    decision: str
    created_at: datetime
    updated_at: datetime


class PersonIdentityDecisionWrite(BaseModel):
    candidate_key: str = Field(min_length=1, max_length=64)
    decision: str = Field(pattern=r"^(SAME|DIFFERENT)$")


class WorkbenchEventRead(BaseModel):
    id: str
    title: str
    event_type: str
    summary: str
    people: list[str]
    related_entities: list[str]
    evidence_ids: list[str]
    chapter_ordinals: list[int]
    chapter_titles: list[str]
    start_char: int
    end_char: int
    mention_count: int
    status: str
    confidence: int
    narrative_mode: str = "UNCERTAIN"
    location: str = ""
    trigger: str = ""
    process: str = ""
    outcome: str = ""
    impact: str = ""
    boundary_status: str = "UNRESOLVED"
    boundary_note: str = ""
    discovery_routes: list[str] = Field(default_factory=list)


class WorkbenchPhaseRead(BaseModel):
    id: str
    title: str
    summary: str
    event_ids: list[str]
    evidence_ids: list[str]
    chapter_ordinals: list[int]
    chapter_titles: list[str]
    people: list[str]
    situation: str = ""
    goal: str = ""
    obstacle: str = ""
    key_actions: list[str] = Field(default_factory=list)
    outcome: str = ""
    change: str = ""
    next_hook: str = ""


class WorkbenchStoryOverviewRead(BaseModel):
    premise: str
    synopsis: str
    protagonist: str
    protagonist_goal: str
    central_conflict: str
    opening_situation: str = ""
    development_path: list[str] = Field(default_factory=list)
    turning_points: list[str] = Field(default_factory=list)
    current_situation: str
    current_result: str = ""
    unresolved_questions: list[str]
    evidence_ids: list[str]


class WorkbenchCharacterRelationRead(BaseModel):
    source_name: str
    target_name: str
    relation: str
    current_state: str
    changes: list[str]
    change_history: list[dict] = Field(default_factory=list)
    evidence_ids: list[str]


class WorkbenchEventRelationRead(BaseModel):
    source_event_id: str
    target_event_id: str
    relation: str
    explanation: str
    evidence_ids: list[str]
    source_title: str = ""
    target_title: str = ""


class WorkbenchFactVersionRead(BaseModel):
    id: str
    subject: str
    predicate: str
    value: str
    fact_type: str
    status: str
    valid_from_chapter: int
    valid_to_chapter: int | None
    evidence_ids: list[str]
    counter_evidence_ids: list[str]
    timeline_version: int = 1
    timeline_status: str = "ACTIVE"
    timeline_note: str = ""


class WorkbenchStateChangeRead(BaseModel):
    id: str
    subject: str
    aspect: str
    before: str
    after: str
    chapter_ordinal: int
    event_id: str | None
    evidence_ids: list[str]


class WorkbenchActorKnowledgeRead(BaseModel):
    id: str
    actor: str
    proposition: str
    state: str
    chapter_ordinal: int
    evidence_ids: list[str]


class WorkbenchKnowledgeTransferRead(BaseModel):
    id: str
    source_actor: str
    target_actor: str
    proposition: str
    transfer_type: str
    resulting_state: str
    chapter_ordinal: int
    event_id: str | None
    evidence_ids: list[str]


class WorkbenchWorldRuleRead(BaseModel):
    id: str
    title: str
    description: str
    limitations: list[str]
    costs: list[str]
    exceptions: list[str]
    evidence_ids: list[str]
    discovered_chapter: int


class WorkbenchForeshadowingRead(BaseModel):
    id: str
    title: str
    setup: str
    lifecycle: str
    setup_chapter: int
    payoff_chapter: int | None
    event_ids: list[str]
    evidence_ids: list[str]


class WorkbenchConflictRead(BaseModel):
    id: str
    title: str
    conflict_type: str
    participants: list[str]
    goals: str
    obstacles: str
    stakes: str
    escalation: list[str]
    resolution: str
    status: str
    event_ids: list[str]
    evidence_ids: list[str]


class WorkbenchSceneAnalysisRead(BaseModel):
    id: str
    chapter_ordinal: int
    function: str
    summary: str
    information_released: list[str]
    action_dialogue_balance: str
    pace: str
    evidence_ids: list[str]


class WorkbenchClaimRead(BaseModel):
    id: str
    claim_kind: str
    claim_text: str
    scope: str
    evidence_ids: list[str]
    counter_evidence_ids: list[str]
    verification_status: str
    verification_note: str = ""
    confidence: int


class WorkbenchDeepAnalysisRead(BaseModel):
    fact_versions: list[WorkbenchFactVersionRead] = Field(default_factory=list)
    state_changes: list[WorkbenchStateChangeRead] = Field(default_factory=list)
    actor_knowledge: list[WorkbenchActorKnowledgeRead] = Field(default_factory=list)
    knowledge_transfers: list[WorkbenchKnowledgeTransferRead] = Field(default_factory=list)
    world_rules: list[WorkbenchWorldRuleRead] = Field(default_factory=list)
    foreshadowing: list[WorkbenchForeshadowingRead] = Field(default_factory=list)
    conflicts: list[WorkbenchConflictRead] = Field(default_factory=list)
    scene_analysis: list[WorkbenchSceneAnalysisRead] = Field(default_factory=list)
    claims: list[WorkbenchClaimRead] = Field(default_factory=list)


class WorkbenchLearningMetricRead(BaseModel):
    label: str
    value: str
    unit: str = ""
    method: str
    evidence_ids: list[str] = Field(default_factory=list)


class WorkbenchLearningQuestionRead(BaseModel):
    question_id: str
    stage_id: int
    stage_name: str
    question: str
    priority: str
    analysis_requirement: str
    evidence_view: str
    recommended_rank: int | None = None
    status: str
    conclusion: str = ""
    metrics: list[WorkbenchLearningMetricRead] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    counter_evidence_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    reusable_lessons: list[str] = Field(default_factory=list)
    do_not_copy: list[str] = Field(default_factory=list)


class WorkbenchLearningStageRead(BaseModel):
    stage_id: int
    stage_name: str
    total_count: int
    generated_count: int
    answered_count: int
    insufficient_count: int


class WorkbenchAuthorDecisionRead(BaseModel):
    title: str
    likely_timing: str
    inference: str
    evidence_ids: list[str]
    limitations: list[str]
    confidence: int


class WorkbenchMethodCandidateRead(BaseModel):
    title: str
    mechanism: str
    observed_result: str
    applicability: list[str]
    risks: list[str]
    evidence_ids: list[str]
    do_not_copy: str
    verification_scope: str


class WorkbenchLearningReportRead(BaseModel):
    catalog_version: str
    batch_label: str
    revision: int | None = None
    source_deep_revision: int | None = None
    generated_at: datetime | None = None
    recommended_question_ids: list[str]
    questions: list[WorkbenchLearningQuestionRead]
    stages: list[WorkbenchLearningStageRead]
    author_decisions: list[WorkbenchAuthorDecisionRead] = Field(default_factory=list)
    method_candidates: list[WorkbenchMethodCandidateRead] = Field(default_factory=list)


class WorkbenchChapterRefRead(BaseModel):
    ordinal: int
    title: str


class WorkbenchRead(BaseModel):
    run_id: str
    source_version_id: str
    status: str
    characters: list[WorkbenchCharacterRead]
    person_identity_candidates: list[WorkbenchPersonIdentityCandidateRead] = Field(
        default_factory=list
    )
    person_identity_decisions: list[WorkbenchPersonIdentityDecisionRead] = Field(
        default_factory=list
    )
    related_entities: list[EntityCandidateRead]
    events: list[WorkbenchEventRead]
    phases: list[WorkbenchPhaseRead]
    narrative_status: str = "NOT_GENERATED"
    story_overview: WorkbenchStoryOverviewRead | None = None
    character_relations: list[WorkbenchCharacterRelationRead] = Field(default_factory=list)
    event_relations: list[WorkbenchEventRelationRead] = Field(default_factory=list)
    deep_status: str = "NOT_GENERATED"
    deep_analysis: WorkbenchDeepAnalysisRead | None = None
    deep_revision: int | None = None
    learning_report_status: str = "NOT_GENERATED"
    learning_report: WorkbenchLearningReportRead
    chapters: list[WorkbenchChapterRefRead] = Field(default_factory=list)


class WorkbenchStateAtChapterRead(BaseModel):
    run_id: str
    deep_revision: int | None
    chapter_ordinal: int
    chapter_title: str
    facts: list[WorkbenchFactVersionRead] = Field(default_factory=list)
    states: list[WorkbenchStateChangeRead] = Field(default_factory=list)
    knowledge: list[WorkbenchActorKnowledgeRead] = Field(default_factory=list)
    knowledge_transfers: list[WorkbenchKnowledgeTransferRead] = Field(default_factory=list)
    world_rules: list[WorkbenchWorldRuleRead] = Field(default_factory=list)


class AnalysisIssueCreate(BaseModel):
    target_kind: str = Field(min_length=1, max_length=40)
    target_id: str | None = Field(default=None, max_length=64)
    target_label: str = Field(min_length=1, max_length=300)
    category: str = Field(min_length=1, max_length=40)
    note: str = Field(min_length=1, max_length=2000)


class AnalysisIssueRead(BaseModel):
    id: str
    run_id: str
    target_kind: str
    target_id: str | None
    target_label: str
    category: str
    note: str
    status: str
    created_at: datetime
    resolved_at: datetime | None


class DeepRevisionImpactSectionRead(BaseModel):
    key: str
    label: str
    reason: str
    item_count: int
    item_labels: list[str] = Field(default_factory=list)


class DeepRevisionImpactRead(BaseModel):
    mode: str
    issue_count: int
    summary: str
    sections: list[DeepRevisionImpactSectionRead] = Field(default_factory=list)


class DeepAnalysisRevisionRead(BaseModel):
    revision_no: int
    created_at: datetime
    prompt_version: str


class DeepAnalysisDiffRead(BaseModel):
    from_revision: int
    to_revision: int
    added: dict[str, list[str]]
    removed: dict[str, list[str]]
    changed: dict[str, list[str]]
    changed_counts: dict[str, int]
