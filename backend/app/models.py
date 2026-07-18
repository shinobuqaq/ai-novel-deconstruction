from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class Base(DeclarativeBase):
    pass


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    RETRY_WAIT = "RETRY_WAIT"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskAttemptStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    RETRYABLE_FAILED = "RETRYABLE_FAILED"
    PERMANENT_FAILED = "PERMANENT_FAILED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    STALE = "STALE"


class ArtifactStatus(StrEnum):
    WRITING = "WRITING"
    READY = "READY"
    FAILED = "FAILED"
    DIRTY = "DIRTY"


class SourceVersionStatus(StrEnum):
    REVIEW = "REVIEW"
    CONFIRMED = "CONFIRMED"


class SourceIssueStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


class AnalysisRunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    REVIEW = "REVIEW"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class CandidateStatus(StrEnum):
    VALID = "VALID"
    UNCERTAIN = "UNCERTAIN"
    REJECTED = "REJECTED"


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("prj"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    tasks: Mapped[list["Task"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    artifacts: Mapped[list["Artifact"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    source_documents: Mapped[list["SourceDocument"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )


class SourceDocument(Base):
    __tablename__ = "source_documents"
    __table_args__ = (
        Index("ix_source_documents_project_created", "project_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("src"))
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    source_format: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    project: Mapped[Project] = relationship(back_populates="source_documents")
    versions: Mapped[list["SourceVersion"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="SourceVersion.version_no",
    )


class SourceVersion(Base):
    __tablename__ = "source_versions"
    __table_args__ = (
        Index("ux_source_version_no", "document_id", "version_no", unique=True),
        Index("ux_source_version_hash", "document_id", "content_hash", unique=True),
        Index("ix_source_versions_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("svr"))
    document_id: Mapped[str] = mapped_column(
        ForeignKey("source_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    original_relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    text_relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    total_chars: Mapped[int] = mapped_column(Integer, nullable=False)
    chapter_count: Mapped[int] = mapped_column(Integer, nullable=False)
    detected_encoding: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default=SourceVersionStatus.REVIEW.value, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    document: Mapped[SourceDocument] = relationship(back_populates="versions")
    units: Mapped[list["SourceUnit"]] = relationship(
        back_populates="source_version",
        cascade="all, delete-orphan",
        order_by="SourceUnit.ordinal",
    )
    issues: Mapped[list["SourceIssue"]] = relationship(
        back_populates="source_version",
        cascade="all, delete-orphan",
    )
    evidence_spans: Mapped[list["EvidenceSpan"]] = relationship(
        back_populates="source_version",
        cascade="all, delete-orphan",
    )


class SourceUnit(Base):
    __tablename__ = "source_units"
    __table_args__ = (
        Index("ux_source_unit_ordinal", "source_version_id", "ordinal", unique=True),
        Index("ix_source_units_version_range", "source_version_id", "start_char", "end_char"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_version_id: Mapped[str] = mapped_column(
        ForeignKey("source_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_type: Mapped[str] = mapped_column(String(20), default="CHAPTER", nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    start_char: Mapped[int] = mapped_column(Integer, nullable=False)
    end_char: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    source_version: Mapped[SourceVersion] = relationship(back_populates="units")
    evidence_spans: Mapped[list["EvidenceSpan"]] = relationship(
        back_populates="source_unit",
        cascade="all, delete-orphan",
    )


class SourceIssue(Base):
    __tablename__ = "source_issues"
    __table_args__ = (
        Index("ix_source_issues_version_status", "source_version_id", "status", "severity"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("iss"))
    source_version_id: Mapped[str] = mapped_column(
        ForeignKey("source_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_unit_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_units.id", ondelete="SET NULL"), nullable=True
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default=SourceIssueStatus.OPEN.value, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    source_version: Mapped[SourceVersion] = relationship(back_populates="issues")


class EvidenceSpan(Base):
    __tablename__ = "evidence_spans"
    __table_args__ = (
        Index(
            "ux_evidence_span_range",
            "source_version_id",
            "start_char",
            "end_char",
            unique=True,
        ),
        Index("ix_evidence_spans_unit_paragraph", "source_unit_id", "paragraph_index"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_version_id: Mapped[str] = mapped_column(
        ForeignKey("source_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_unit_id: Mapped[str] = mapped_column(
        ForeignKey("source_units.id", ondelete="CASCADE"), nullable=False, index=True
    )
    paragraph_index: Mapped[int] = mapped_column(Integer, nullable=False)
    start_char: Mapped[int] = mapped_column(Integer, nullable=False)
    end_char: Mapped[int] = mapped_column(Integer, nullable=False)
    text_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    context_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    source_version: Mapped[SourceVersion] = relationship(back_populates="evidence_spans")
    source_unit: Mapped[SourceUnit] = relationship(back_populates="evidence_spans")


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    __table_args__ = (
        Index("ix_analysis_runs_source_stage", "source_version_id", "stage", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("run"))
    source_version_id: Mapped[str] = mapped_column(
        ForeignKey("source_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default=AnalysisRunStatus.PENDING.value, nullable=False
    )
    total_batches: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    source_version: Mapped[SourceVersion] = relationship()
    task_links: Mapped[list["AnalysisRunTask"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="AnalysisRunTask.batch_index",
    )
    entity_candidates: Mapped[list["EntityCandidate"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )
    event_candidates: Mapped[list["EventCandidate"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )


class AnalysisRunTask(Base):
    __tablename__ = "analysis_run_tasks"
    __table_args__ = (
        Index("ux_analysis_run_batch", "run_id", "batch_index", unique=True),
    )

    run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), primary_key=True
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    batch_index: Mapped[int] = mapped_column(Integer, nullable=False)

    run: Mapped[AnalysisRun] = relationship(back_populates="task_links")
    task: Mapped[Task] = relationship()


class EntityCandidate(Base):
    __tablename__ = "entity_candidates"
    __table_args__ = (
        Index("ux_entity_candidate_run_name", "run_id", "normalized_name", unique=True),
        Index("ix_entity_candidates_source_status", "source_version_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_version_id: Mapped[str] = mapped_column(
        ForeignKey("source_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(240), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    aliases_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    evidence_ids_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_task_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_attempt_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    run: Mapped[AnalysisRun] = relationship(back_populates="entity_candidates")


class EventCandidate(Base):
    __tablename__ = "event_candidates"
    __table_args__ = (
        Index("ux_event_candidate_run_identity", "run_id", "identity_key", unique=True),
        Index("ix_event_candidates_source_status", "source_version_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_version_id: Mapped[str] = mapped_column(
        ForeignKey("source_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    identity_key: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    participants_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    evidence_ids_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    start_char: Mapped[int] = mapped_column(Integer, nullable=False)
    end_char: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_task_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_attempt_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    run: Mapped[AnalysisRun] = relationship(back_populates="event_candidates")


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_status_created", "status", "created_at"),
        Index("ix_tasks_lease", "lease_expires_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("tsk"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=TaskStatus.PENDING.value, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    result_artifact_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_attempt_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "task_attempts.id",
            name="fk_tasks_current_attempt_id_task_attempts",
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
    )
    lease_generation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    project: Mapped[Project] = relationship(back_populates="tasks")
    attempt_records: Mapped[list["TaskAttempt"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        foreign_keys="TaskAttempt.task_id",
    )
    current_attempt: Mapped["TaskAttempt | None"] = relationship(
        foreign_keys=[current_attempt_id],
        post_update=True,
    )

    @property
    def lease_token(self) -> str | None:
        if self.current_attempt is None:
            return None
        return self.current_attempt.lease_token


class TaskAttempt(Base):
    __tablename__ = "task_attempts"
    __table_args__ = (
        Index("ux_task_attempt_task_no", "task_id", "attempt_no", unique=True),
        Index("ux_task_attempt_lease_token", "lease_token", unique=True),
        Index("ix_task_attempt_status_lease", "status", "lease_expires_at"),
        Index("ix_task_attempt_task_started", "task_id", "started_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("att"))
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_token: Mapped[str] = mapped_column(String(128), nullable=False)
    worker_id: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default=TaskAttemptStatus.RUNNING.value, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)

    task: Mapped[Task] = relationship(
        back_populates="attempt_records",
        foreign_keys=[task_id],
    )


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        Index("ux_artifact_result_key", "result_key", unique=True),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("art"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(30), default="1.0.0", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=ArtifactStatus.WRITING.value, nullable=False)
    result_key: Mapped[str] = mapped_column(String(240), nullable=False)
    blob_id: Mapped[str] = mapped_column(
        ForeignKey("artifact_blobs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by_attempt_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    project: Mapped[Project] = relationship(back_populates="artifacts")
    blob: Mapped["ArtifactBlob"] = relationship(back_populates="artifacts")


class ArtifactBlob(Base):
    __tablename__ = "artifact_blobs"
    __table_args__ = (
        Index("ux_artifact_blob_content_hash", "content_hash", unique=True),
    )

    id: Mapped[str] = mapped_column(String(68), primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        default=ArtifactStatus.WRITING.value,
        nullable=False,
    )
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    artifacts: Mapped[list[Artifact]] = relationship(back_populates="blob")
