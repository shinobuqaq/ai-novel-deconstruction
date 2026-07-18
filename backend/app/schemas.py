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


class AnalysisRunRead(BaseModel):
    id: str
    source_version_id: str
    stage: str
    status: str
    total_batches: int
    completed_batches: int
    failed_batches: int
    created_at: datetime
    finished_at: datetime | None
    confirmed_at: datetime | None


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
