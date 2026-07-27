from __future__ import annotations

import hashlib
import json
import re
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
CHARACTER_DESIGN_PROMPT_VERSION = "1.0.0"

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
    value: str = Field(default="", max_length=800)
    first_display_chapter_ordinal: int | None = Field(default=None, ge=1)
    first_display_event_id: str | None = Field(default=None, max_length=64)
    display_event: str = Field(default="", max_length=1200)
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
    surface_desire: str = Field(min_length=1, max_length=600)
    deep_desire: str = Field(min_length=1, max_length=600)
    choice: str = Field(min_length=1, max_length=1000)
    arc_change: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)


class CharacterDesignEvidenceOutput(BaseModel):
    protagonist: str = Field(min_length=1, max_length=120)
    fields: list[CharacterDesignFieldEvidence] = Field(min_length=6, max_length=6)
    desire_conflicts: list[DesireConflictEvidence] = Field(default_factory=list, max_length=40)
    arc_summary: str = Field(min_length=1, max_length=2400)


class CharacterDesignValidationError(ValueError):
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


def parse_character_design_evidence(value: dict) -> CharacterDesignEvidenceOutput:
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
        return min(160_000, max(24_000, int(context_window) - output_reserve - 4_096))
    return max(48_000, min(160_000, output_reserve * 3))


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
                    "summary",
                    "people",
                    "chapter_ordinals",
                    "process",
                    "outcome",
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


def _compact_event(event: dict) -> dict[str, object]:
    return {
        "id": event.get("id"),
        "title": event.get("title"),
        "chapter_ordinals": event.get("chapter_ordinals", []),
        "summary": event.get("summary"),
        "process": event.get("process"),
        "outcome": event.get("outcome"),
        "evidence_ids": event.get("evidence_ids", []),
    }


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

    _service, profile = resolve_analysis_profile(
        settings,
        str(task_payload.get("model_profile_id") or ENTITIES_EVENTS_PROFILE_ID),
    )
    chapter_units, chapter_by_unit_id = _chapter_catalog(session, version.id)
    evidence_ids = {
        str(evidence_id)
        for event in events
        for evidence_id in event.get("evidence_ids", [])
        if evidence_id
    }
    evidence_by_id = {
        evidence.id: evidence
        for evidence in session.scalars(
            select(EvidenceSpan).where(
                EvidenceSpan.source_version_id == version.id,
                EvidenceSpan.id.in_(evidence_ids),
            )
        )
    }
    evidence_catalog = [
        {
            "id": evidence.id,
            "chapter_ordinal": chapter_by_unit_id.get(
                evidence.source_unit_id, {}
            ).get("ordinal"),
            "chapter_title": chapter_by_unit_id.get(
                evidence.source_unit_id, {}
            ).get("title"),
            "text": evidence.text_snapshot,
        }
        for evidence in sorted(
            evidence_by_id.values(),
            key=lambda item: (item.start_char, item.id),
        )
    ]
    input_payload = {
        "question_id": CHARACTER_DESIGN_QUESTION_ID,
        "contract": {
            "output": "主角双层欲望卡、六项首次展示节奏表和弧光转折时间轴",
            "measurement": "定位六项首次行动事件，并统计两层欲望冲突节点",
            "evidence": "每项必须由主角行动或选择的原文证明，人物简介不能单独作证",
            "scope": "覆盖前三章、前 30 章和全书主角事件",
        },
        "protagonist": {
            key: protagonist.get(key)
            for key in (
                "id",
                "name",
                "aliases",
                "description",
                "role_reason",
                "goals",
                "motivations",
                "abilities",
                "arc_summary",
            )
        },
        "chapter_catalog": [
            {"ordinal": ordinal, "title": unit.title}
            for ordinal, unit in enumerate(chapter_units, start=1)
        ],
        "narrative_phases": [
            {
                key: phase.get(key)
                for key in (
                    "id",
                    "title",
                    "situation",
                    "goal",
                    "obstacle",
                    "key_actions",
                    "outcome",
                    "change",
                    "chapter_ordinals",
                    "event_ids",
                )
            }
            for phase in projection.get("phases", [])
        ],
        "protagonist_events": [_compact_event(event) for event in events],
        "evidence_catalog": evidence_catalog,
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
        "included_event_count": len(events),
        "event_coverage_complete": True,
        "evidence_span_count": len(evidence_catalog),
        "input_chars": len(input_text),
        "input_budget_chars": request_budget,
    }
    return {
        "instructions": _prompt(),
        "input": input_text,
        "output_schema": _inline_model_schema(CharacterDesignEvidenceOutput),
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
        if not evidence_set or not evidence_set.issubset(
            set(event.get("evidence_ids", []))
        ):
            raise ValueError("CHARACTER_DESIGN_EVENT_EVIDENCE_MISMATCH")
        if not evidence_set.issubset(valid_evidence_ids):
            raise ValueError("CHARACTER_DESIGN_EVIDENCE_REFERENCE_INVALID")

    for item in output.fields:
        if item.status != "SUPPORTED":
            continue
        validate_event_reference(
            event_id=str(item.first_display_event_id),
            chapter_ordinal=int(item.first_display_chapter_ordinal or 0),
            evidence_ids=item.evidence_ids,
        )
    for item in output.desire_conflicts:
        validate_event_reference(
            event_id=item.event_id,
            chapter_ordinal=item.chapter_ordinal,
            evidence_ids=item.evidence_ids,
        )


def persist_character_design_evidence(
    session: Session,
    *,
    task: Task,
    attempt_id: str,
    task_payload: dict,
    output: CharacterDesignEvidenceOutput,
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
    fingerprint = character_design_source_fingerprint(projection)
    if fingerprint != task_payload.get("source_fingerprint"):
        raise ValueError("CHARACTER_DESIGN_SOURCE_OUTDATED")
    valid_evidence_ids = {
        item.id
        for item in session.scalars(
            select(EvidenceSpan).where(EvidenceSpan.source_version_id == version.id)
        )
    }
    _raise_for_references(output, projection, valid_evidence_ids)
    protagonist_name, _character = _protagonist(projection)
    events = _protagonist_events(projection, protagonist_name)
    payload = output.model_dump(mode="json")
    payload["coverage"] = {
        "source_chapter_count": len(projection.get("chapters", [])),
        "protagonist_event_count": len(events),
        "covered_event_count": len(events),
        "event_coverage_complete": True,
        "first_30_chapter_event_count": sum(
            1
            for event in events
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
    ledger = LearningQuestionEvidence(
        run_id=run.id,
        source_version_id=version.id,
        question_id=CHARACTER_DESIGN_QUESTION_ID,
        revision_no=revision_no,
        source_fingerprint=fingerprint,
        payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True),
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
    task_payload, max_attempts = prepare_task_provider_routes(
        settings,
        {
            "run_id": run.id,
            "source_version_id": run.source_version_id,
            "source_fingerprint": fingerprint,
            "question_id": CHARACTER_DESIGN_QUESTION_ID,
            "provider_name": "openai",
            "model_profile_id": profile.id,
        },
        profile.max_retries + 1,
    )
    task = Task(
        project_id=run.source_version.document.project_id,
        kind=CHARACTER_DESIGN_TASK_KIND,
        payload_json=json.dumps(task_payload, ensure_ascii=False, sort_keys=True),
        max_attempts=max_attempts,
    )
    session.add(task)
    session.flush()
    next_index = max(
        (link.batch_index for link in run.task_links),
        default=run.total_batches,
    ) + 1
    session.add(
        AnalysisRunTask(
            run_id=run.id,
            task_id=task.id,
            batch_index=next_index,
        )
    )
    run.total_batches = next_index
    run.status = AnalysisRunStatus.PENDING.value
    session.commit()
    session.refresh(task)
    return task
