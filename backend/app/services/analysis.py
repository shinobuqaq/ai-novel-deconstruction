from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import (
    AnalysisRun,
    AnalysisRunStatus,
    AnalysisRunTask,
    CandidateStatus,
    EntityCandidate,
    EventCandidate,
    EvidenceSpan,
    SourceUnit,
    SourceVersion,
    SourceVersionStatus,
    Task,
    TaskStatus,
)
from .source_import import SourceImportError, source_text
from .provider_config import ENTITIES_EVENTS_PROFILE_ID, ModelSettingsError, resolve_analysis_profile


ANALYSIS_TASK_KIND = "analysis.entities_events"
ANALYSIS_STAGE = "ENTITIES_EVENTS"
MAX_BATCH_CHARS = 18_000
CHUNK_OVERLAP_CHARS = 600


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
    evidence_quotes: list[str] = Field(min_length=1, max_length=3)
    confidence: int = Field(ge=0, le=100)


class AnalysisProviderOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entities: list[EntityProposal]
    events: list[EventProposal]


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
    instructions = (
        "你是中文小说信息抽取器。只根据给定原文识别明确出现的人物、地点、组织、重要物品和关键事件。"
        "不要补写原文没有的信息，不要把推测写成事实。每个候选必须附上1到3段从原文逐字复制的短引文；"
        "引文是后续定位的唯一依据。事件应是会改变剧情、信息、关系或状态的具体发生项，忽略普通修辞和无关动作。"
        "输出必须严格符合给定 JSON Schema。"
    )
    return {
        "instructions": instructions,
        "input": f"原文在全书中的字符范围：{start}-{end}\n\n{excerpt}",
        "output_schema": _schema(),
        "model_profile_id": str(task_payload.get("model_profile_id") or ENTITIES_EVENTS_PROFILE_ID),
    }


def parse_provider_output(value: dict) -> AnalysisProviderOutput:
    try:
        return AnalysisProviderOutput.model_validate(value)
    except ValidationError as exc:
        raise ValueError("ANALYSIS_OUTPUT_INVALID") from exc


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
        identity_key = _hash(f"{_normalized_name(proposal.title)}:{event_start}:{event_end}")
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


def refresh_analysis_run(session: Session, run: AnalysisRun) -> AnalysisRun:
    statuses = list(session.scalars(
        select(Task.status)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(AnalysisRunTask.run_id == run.id)
    ))
    if not statuses:
        return run
    if all(status == TaskStatus.SUCCEEDED.value for status in statuses):
        if run.status != AnalysisRunStatus.CONFIRMED.value:
            run.status = AnalysisRunStatus.REVIEW.value
            run.finished_at = datetime.now(timezone.utc)
    elif any(status == TaskStatus.FAILED.value for status in statuses):
        run.status = AnalysisRunStatus.FAILED.value
    elif any(status in {TaskStatus.RUNNING.value, TaskStatus.RETRY_WAIT.value} for status in statuses):
        run.status = AnalysisRunStatus.RUNNING.value
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
    if run.status != AnalysisRunStatus.CONFIRMED.value:
        run.status = AnalysisRunStatus.CONFIRMED.value
        run.confirmed_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(run)
    return run
