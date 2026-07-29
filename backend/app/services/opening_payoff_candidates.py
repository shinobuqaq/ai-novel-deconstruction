from __future__ import annotations

import hashlib
import json
import re
import uuid
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
    SourceVersion,
    Task,
    TaskStatus,
)
from ..repositories import request_task_cancellation
from .learning_report import (
    OPENING_PAYOFF_MAX_WINDOW_CANDIDATES,
    _chapter_index_by_unit_id,
    _evidence_ids,
    _opening_payoff_candidate_artifact,
    _opening_promise_source_artifact,
    _request_budget,
)
from .provider_config import (
    ENTITIES_EVENTS_PROFILE_ID,
    ModelSettingsError,
    prepare_task_provider_routes,
    resolve_analysis_profile,
)


OPENING_PAYOFF_CANDIDATES_TASK_KIND = (
    "analysis.opening_payoff_candidates"
)
OPENING_PAYOFF_CANDIDATES_QUESTION_ID = "1.4p"
OPENING_PAYOFF_CANDIDATES_PROMPT_ID = (
    "opening_payoff_candidates"
)
OPENING_PAYOFF_CANDIDATES_PROMPT_VERSION = "1.0.0"
OPENING_PAYOFF_CANDIDATES_WINDOW_OVERHEAD_CHARS = 12_000


class OpeningPayoffCandidateProposal(BaseModel):
    model_config = {"extra": "forbid"}

    sequence_no: int = Field(ge=1)
    matched_facet_ids: list[Literal["F1", "F2", "F3"]] = Field(
        default_factory=list,
        max_length=3,
    )
    exclusion_code: Literal[
        "NONE",
        "PROMISE_SOURCE",
        "PROMISE_RESTATEMENT",
        "IDENTITY_GRANT",
        "DREAM_OR_HISTORY",
        "STATIC_EVIDENCE",
        "SIMULATION",
        "PARTIAL_ONLY",
        "UNRELATED",
    ]
    anchor_evidence_no: int = Field(ge=1, le=16)

    @model_validator(mode="after")
    def require_unique_facets(self) -> "OpeningPayoffCandidateProposal":
        if len(self.matched_facet_ids) != len(
            set(self.matched_facet_ids)
        ):
            raise ValueError("兑现要素不能重复")
        return self


class OpeningPayoffCandidatesOutput(BaseModel):
    model_config = {"extra": "forbid"}

    classifications: list[OpeningPayoffCandidateProposal] = Field(
        min_length=1,
        max_length=OPENING_PAYOFF_MAX_WINDOW_CANDIDATES,
    )


def _validation_errors(
    exc: ValidationError,
) -> list[dict[str, object]]:
    return [
        {
            "path": [str(part) for part in item.get("loc", ())],
            "type": str(item.get("type") or "value_error"),
            "message": str(item.get("msg") or "字段无效"),
        }
        for item in exc.errors()
    ]


def parse_opening_payoff_candidates(
    value: dict,
) -> OpeningPayoffCandidatesOutput:
    if (
        set(value) == {"properties"}
        and isinstance(value.get("properties"), dict)
    ):
        value = value["properties"]
    try:
        return OpeningPayoffCandidatesOutput.model_validate(value)
    except ValidationError as exc:
        error = ValueError("OPENING_PAYOFF_CANDIDATES_OUTPUT_INVALID")
        error.validation_errors = _validation_errors(exc)  # type: ignore[attr-defined]
        raise error from exc


def _inline_model_schema(model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})

    def resolve(value: object) -> object:
        if isinstance(value, list):
            return [resolve(item) for item in value]
        if not isinstance(value, dict):
            return value
        reference = value.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/$defs/"):
            return resolve(definitions[reference.rsplit("/", 1)[-1]])
        return {
            key: resolve(item)
            for key, item in value.items()
            if key not in {"title", "default"}
        }

    return resolve(schema)  # type: ignore[return-value]


def _prompt() -> str:
    path = (
        Path(__file__).resolve().parents[3]
        / "prompts"
        / "opening_payoff_candidates_v1.md"
    )
    return path.read_text(encoding="utf-8").strip()


def opening_payoff_candidates_source_fingerprint(
    projection: dict,
) -> str:
    payload = {
        "policy_version": OPENING_PAYOFF_CANDIDATES_PROMPT_VERSION,
        "source_version_id": projection.get("source_version_id"),
        "deep_revision": projection.get("deep_revision"),
        "story_overview": projection.get("story_overview"),
        "events": [
            {
                key: event.get(key)
                for key in (
                    "id",
                    "title",
                    "summary",
                    "process",
                    "outcome",
                    "narrative_mode",
                    "chapter_ordinals",
                    "start_char",
                    "evidence_ids",
                )
            }
            for event in projection.get("events", [])
        ],
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _base_projection(session: Session, run_id: str) -> dict:
    from .workbench import build_workbench_projection

    return build_workbench_projection(
        session,
        run_id,
        include_question_evidence=False,
    )


def _candidate_context(
    session: Session,
    settings: Settings,
    run: AnalysisRun,
) -> tuple[
    dict,
    SourceVersion,
    dict[str, EvidenceSpan],
    dict[str, dict[str, object]],
    dict[str, object],
]:
    version = run.source_version
    projection = _base_projection(session, run.id)
    promise_sources = _opening_promise_source_artifact(
        session,
        settings,
        version,
    )
    evidence_ids = _evidence_ids({
        "events": projection.get("events", []),
        "opening_promise_sources": promise_sources,
    })
    evidence_by_id = {
        item.id: item
        for item in session.scalars(
            select(EvidenceSpan).where(
                EvidenceSpan.source_version_id == version.id,
                EvidenceSpan.id.in_(evidence_ids),
            )
        )
    }
    return (
        projection,
        version,
        evidence_by_id,
        _chapter_index_by_unit_id(session, version.id),
        promise_sources,
    )


def _window_specs(
    candidates: list[dict[str, object]],
    *,
    input_char_budget: int,
) -> list[dict[str, int]]:
    material_budget = max(
        8_000,
        input_char_budget
        - OPENING_PAYOFF_CANDIDATES_WINDOW_OVERHEAD_CHARS,
    )
    windows: list[dict[str, int]] = []
    current: list[dict[str, object]] = []
    current_chars = 0
    for candidate in candidates:
        item_chars = len(json.dumps(
            candidate,
            ensure_ascii=False,
            separators=(",", ":"),
        ))
        if current and (
            current_chars + item_chars > material_budget
            or len(current)
            >= OPENING_PAYOFF_MAX_WINDOW_CANDIDATES
        ):
            windows.append({
                "candidate_start_sequence": int(
                    current[0]["sequence_no"]
                ),
                "candidate_end_sequence": int(
                    current[-1]["sequence_no"]
                ),
                "estimated_material_chars": current_chars,
            })
            current = []
            current_chars = 0
        current.append(candidate)
        current_chars += item_chars
    if current:
        windows.append({
            "candidate_start_sequence": int(
                current[0]["sequence_no"]
            ),
            "candidate_end_sequence": int(
                current[-1]["sequence_no"]
            ),
            "estimated_material_chars": current_chars,
        })
    return windows


def provider_payload_for_opening_payoff_candidates(
    session: Session,
    settings: Settings,
    task_payload: dict,
) -> dict:
    run = session.get(AnalysisRun, task_payload.get("run_id"))
    version = session.get(
        SourceVersion,
        task_payload.get("source_version_id"),
    )
    if run is None or version is None:
        raise ValueError("ANALYSIS_RUN_NOT_FOUND")
    (
        projection,
        _version,
        evidence_by_id,
        chapter_by_unit_id,
        promise_sources,
    ) = _candidate_context(session, settings, run)
    fingerprint = opening_payoff_candidates_source_fingerprint(
        projection
    )
    if fingerprint != task_payload.get("source_fingerprint"):
        raise ValueError("OPENING_PAYOFF_CANDIDATES_SOURCE_OUTDATED")
    start = int(task_payload.get("candidate_start_sequence") or 0)
    end = int(task_payload.get("candidate_end_sequence") or 0)
    if start < 1 or end < start:
        raise ValueError("OPENING_PAYOFF_CANDIDATES_WINDOW_INVALID")
    artifact = _opening_payoff_candidate_artifact(
        projection,
        evidence_by_id,
        chapter_by_unit_id,
        promise_sources,
        candidate_start_sequence=start,
        candidate_limit=end - start + 1,
    )
    candidates = artifact.get("candidates", [])
    if (
        not candidates
        or int(candidates[0]["sequence_no"]) != start
        or int(candidates[-1]["sequence_no"]) != end
    ):
        raise ValueError("OPENING_PAYOFF_CANDIDATES_WINDOW_INVALID")
    input_payload = {
        "question_id": "1.4",
        "facet_definitions": artifact["facet_definitions"],
        "classification_policy": artifact[
            "classification_policy"
        ],
        "window": {
            "group_id": task_payload.get("window_group_id"),
            "index": task_payload.get("window_index"),
            "count": task_payload.get("window_count"),
            "candidate_start_sequence": start,
            "candidate_end_sequence": end,
        },
        "candidates": candidates,
    }
    _service, profile = resolve_analysis_profile(
        settings,
        str(
            task_payload.get("model_profile_id")
            or ENTITIES_EVENTS_PROFILE_ID
        ),
    )
    request_budget = _request_budget(profile)
    input_text = json.dumps(
        input_payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(input_text) > int(request_budget["input_char_budget"]):
        raise ValueError(
            "OPENING_PAYOFF_CANDIDATES_CONTEXT_TOO_LARGE:"
            f"{len(input_text)}:{request_budget['input_char_budget']}"
        )
    return {
        "instructions": _prompt(),
        "input": input_text,
        "output_schema": _inline_model_schema(
            OpeningPayoffCandidatesOutput
        ),
        "model_profile_id": str(
            task_payload.get("model_profile_id")
            or ENTITIES_EVENTS_PROFILE_ID
        ),
        "prompt_id": OPENING_PAYOFF_CANDIDATES_PROMPT_ID,
        "prompt_version": OPENING_PAYOFF_CANDIDATES_PROMPT_VERSION,
        "source_version_id": version.id,
        "source_char_start": 0,
        "source_char_end": version.total_chars,
        "context_manifest": {
            "source_event_count": artifact["coverage"][
                "source_event_count"
            ],
            "window_group_id": task_payload.get("window_group_id"),
            "window_index": task_payload.get("window_index"),
            "window_count": task_payload.get("window_count"),
            "candidate_start_sequence": start,
            "candidate_end_sequence": end,
            "candidate_count": len(candidates),
            "input_chars": len(input_text),
            **request_budget,
        },
    }


def _validated_rows(
    output: OpeningPayoffCandidatesOutput,
    artifact: dict[str, object],
) -> list[dict[str, object]]:
    candidates = [
        item
        for item in artifact.get("candidates", [])
        if isinstance(item, dict)
    ]
    candidate_by_sequence = {
        int(item["sequence_no"]): item for item in candidates
    }
    start = int(candidates[0]["sequence_no"])
    end = int(candidates[-1]["sequence_no"])
    sequence_numbers = [
        item.sequence_no for item in output.classifications
    ]
    if (
        len(sequence_numbers) != len(set(sequence_numbers))
        or sequence_numbers
        != list(range(start, start + len(sequence_numbers)))
        or any(
            sequence not in candidate_by_sequence
            for sequence in sequence_numbers
        )
    ):
        raise ValueError(
            "OPENING_PAYOFF_CANDIDATES_COVERAGE_INVALID"
        )
    rows: list[dict[str, object]] = []
    found_complete = False
    for proposal in output.classifications:
        candidate = candidate_by_sequence[proposal.sequence_no]
        evidence_options = {
            int(item["evidence_no"]): item
            for item in candidate.get("evidence_options", [])
            if isinstance(item, dict)
        }
        anchor = evidence_options.get(proposal.anchor_evidence_no)
        if anchor is None:
            raise ValueError(
                "OPENING_PAYOFF_CANDIDATES_EVIDENCE_INVALID"
            )
        program_exclusion = str(
            candidate.get("program_exclusion_code") or ""
        )
        if (
            program_exclusion
            and proposal.exclusion_code != program_exclusion
        ):
            raise ValueError(
                "OPENING_PAYOFF_CANDIDATES_EXCLUSION_INVALID"
            )
        is_complete = (
            set(proposal.matched_facet_ids) == {"F1", "F2", "F3"}
            and proposal.exclusion_code == "NONE"
        )
        if found_complete:
            raise ValueError(
                "OPENING_PAYOFF_CANDIDATES_AFTER_COMPLETE_INVALID"
            )
        found_complete = is_complete
        rows.append({
            **proposal.model_dump(mode="json"),
            "event_id": candidate.get("event_id"),
            "event_title": candidate.get("event_title"),
            "event_summary": candidate.get("event_summary"),
            "anchor_evidence_id": anchor.get("evidence_id"),
            "chapter_ordinal": anchor.get("chapter_ordinal"),
            "chapter_title": anchor.get("chapter_title"),
            "paragraph_number": anchor.get("paragraph_number"),
            "source_char_start": anchor.get("source_char_start"),
            "program_complete_payoff": is_complete,
        })
    if not found_complete and sequence_numbers[-1] != end:
        raise ValueError(
            "OPENING_PAYOFF_CANDIDATES_WINDOW_INCOMPLETE"
        )
    return rows


def _finalize_window_group(
    session: Session,
    *,
    run: AnalysisRun,
    fingerprint: str,
    window_group_id: str,
    window_count: int,
) -> LearningQuestionEvidence | None:
    prefix = f"1.4pw-{window_group_id}-"
    ledgers = list(session.scalars(
        select(LearningQuestionEvidence)
        .where(
            LearningQuestionEvidence.run_id == run.id,
            LearningQuestionEvidence.source_fingerprint
            == fingerprint,
            LearningQuestionEvidence.prompt_version
            == OPENING_PAYOFF_CANDIDATES_PROMPT_VERSION,
            LearningQuestionEvidence.question_id.like(f"{prefix}%"),
        )
        .order_by(LearningQuestionEvidence.question_id)
    ))
    payload_by_index = {
        int(payload["window_index"]): (ledger, payload)
        for ledger in ledgers
        for payload in [json.loads(ledger.payload_json)]
    }
    contiguous: list[
        tuple[LearningQuestionEvidence, dict[str, object]]
    ] = []
    for index in range(1, window_count + 1):
        item = payload_by_index.get(index)
        if item is None:
            break
        contiguous.append(item)
        if any(
            bool(row.get("program_complete_payoff"))
            for row in item[1].get("classifications", [])
        ):
            break
    if not contiguous:
        return None
    rows = [
        row
        for _ledger, payload in contiguous
        for row in payload.get("classifications", [])
    ]
    selected = next(
        (
            row
            for row in rows
            if row.get("program_complete_payoff")
        ),
        None,
    )
    all_windows_completed = len(payload_by_index) == window_count
    if selected is None and not all_windows_completed:
        return None
    finalizer = contiguous[-1][0]
    source_event_count = int(
        contiguous[0][1].get("source_event_count") or 0
    )
    payload = {
        "classifications": rows,
        "selected": selected,
        "coverage": {
            "source_event_count": source_event_count,
            "scanned_candidate_count": len(rows),
            "planned_window_count": window_count,
            "completed_window_count": len(contiguous),
            "all_source_events_scanned": (
                selected is None and all_windows_completed
            ),
            "stopped_after_first_complete": bool(
                selected is not None
                and len(contiguous) < window_count
            ),
            "source_sequence_contiguous": True,
        },
    }
    finalizer.question_id = OPENING_PAYOFF_CANDIDATES_QUESTION_ID
    finalizer.revision_no = int(session.scalar(
        select(func.max(LearningQuestionEvidence.revision_no)).where(
            LearningQuestionEvidence.run_id == run.id,
            LearningQuestionEvidence.question_id
            == OPENING_PAYOFF_CANDIDATES_QUESTION_ID,
        )
    ) or 0) + 1
    finalizer.payload_json = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
    )
    session.commit()
    session.refresh(finalizer)
    if selected is not None and len(contiguous) < window_count:
        remaining_tasks = list(session.scalars(
            select(Task)
            .join(
                AnalysisRunTask,
                AnalysisRunTask.task_id == Task.id,
            )
            .where(
                AnalysisRunTask.run_id == run.id,
                Task.kind == OPENING_PAYOFF_CANDIDATES_TASK_KIND,
                Task.status.in_((
                    TaskStatus.PENDING.value,
                    TaskStatus.RETRY_WAIT.value,
                    TaskStatus.WAITING_CONFIRMATION.value,
                )),
            )
        ))
        for remaining_task in remaining_tasks:
            remaining_payload = json.loads(
                remaining_task.payload_json
            )
            if (
                remaining_payload.get("window_group_id")
                == window_group_id
                and int(
                    remaining_payload.get("window_index") or 0
                )
                > len(contiguous)
            ):
                request_task_cancellation(
                    session,
                    task_id=remaining_task.id,
                )
    return finalizer


def persist_opening_payoff_candidates(
    session: Session,
    settings: Settings,
    *,
    task: Task,
    attempt_id: str,
    task_payload: dict,
    output: OpeningPayoffCandidatesOutput,
) -> LearningQuestionEvidence:
    existing = session.scalar(
        select(LearningQuestionEvidence).where(
            LearningQuestionEvidence.created_by_task_id == task.id
        )
    )
    run = session.get(AnalysisRun, task_payload.get("run_id"))
    if run is None:
        raise ValueError("ANALYSIS_RUN_NOT_FOUND")
    (
        projection,
        version,
        evidence_by_id,
        chapter_by_unit_id,
        promise_sources,
    ) = _candidate_context(session, settings, run)
    fingerprint = opening_payoff_candidates_source_fingerprint(
        projection
    )
    if fingerprint != task_payload.get("source_fingerprint"):
        raise ValueError("OPENING_PAYOFF_CANDIDATES_SOURCE_OUTDATED")
    group_id = str(task_payload.get("window_group_id") or "")
    window_index = int(task_payload.get("window_index") or 0)
    window_count = int(task_payload.get("window_count") or 0)
    start = int(task_payload.get("candidate_start_sequence") or 0)
    end = int(task_payload.get("candidate_end_sequence") or 0)
    if (
        not re.fullmatch(r"[a-f0-9]{8}", group_id)
        or window_index < 1
        or window_count < 1
        or window_index > window_count
        or start < 1
        or end < start
    ):
        raise ValueError("OPENING_PAYOFF_CANDIDATES_WINDOW_INVALID")
    artifact = _opening_payoff_candidate_artifact(
        projection,
        evidence_by_id,
        chapter_by_unit_id,
        promise_sources,
        candidate_start_sequence=start,
        candidate_limit=end - start + 1,
    )
    rows = _validated_rows(output, artifact)
    if existing is None:
        payload = {
            "window_group_id": group_id,
            "window_index": window_index,
            "window_count": window_count,
            "candidate_start_sequence": start,
            "candidate_end_sequence": end,
            "source_event_count": artifact["coverage"][
                "source_event_count"
            ],
            "classifications": rows,
        }
        existing = LearningQuestionEvidence(
            run_id=run.id,
            source_version_id=version.id,
            question_id=f"1.4pw-{group_id}-{window_index:03d}",
            revision_no=1,
            source_fingerprint=fingerprint,
            payload_json=json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
            ),
            prompt_id=OPENING_PAYOFF_CANDIDATES_PROMPT_ID,
            prompt_version=OPENING_PAYOFF_CANDIDATES_PROMPT_VERSION,
            created_by_task_id=task.id,
            created_by_attempt_id=attempt_id,
        )
        session.add(existing)
        session.commit()
        session.refresh(existing)
    final = _finalize_window_group(
        session,
        run=run,
        fingerprint=fingerprint,
        window_group_id=group_id,
        window_count=window_count,
    )
    if (
        final is not None
        and final.question_id
        == OPENING_PAYOFF_CANDIDATES_QUESTION_ID
    ):
        from .learning_report import enqueue_learning_report

        enqueue_learning_report(
            session,
            settings,
            run,
            force=True,
            only_question_ids=("1.4",),
        )
    return final or existing


def build_opening_payoff_candidates_projection(
    session: Session,
    run_id: str,
    projection: dict,
) -> tuple[str, dict | None]:
    ledger = session.scalar(
        select(LearningQuestionEvidence)
        .where(
            LearningQuestionEvidence.run_id == run_id,
            LearningQuestionEvidence.question_id
            == OPENING_PAYOFF_CANDIDATES_QUESTION_ID,
        )
        .order_by(LearningQuestionEvidence.revision_no.desc())
    )
    latest_task = session.scalar(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run_id,
            Task.kind == OPENING_PAYOFF_CANDIDATES_TASK_KIND,
        )
        .order_by(AnalysisRunTask.batch_index.desc())
    )
    active_task = session.scalar(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run_id,
            Task.kind == OPENING_PAYOFF_CANDIDATES_TASK_KIND,
            Task.status.in_((
                TaskStatus.PENDING.value,
                TaskStatus.RUNNING.value,
                TaskStatus.RETRY_WAIT.value,
                TaskStatus.WAITING_CONFIRMATION.value,
            )),
        )
        .order_by(AnalysisRunTask.batch_index)
    )
    fingerprint = opening_payoff_candidates_source_fingerprint(
        projection
    )
    is_current = bool(
        ledger is not None
        and ledger.source_fingerprint == fingerprint
        and ledger.prompt_version
        == OPENING_PAYOFF_CANDIDATES_PROMPT_VERSION
    )
    if active_task is not None:
        status = "GENERATING"
    elif is_current:
        status = "READY"
    elif ledger is not None:
        status = "OUTDATED"
    elif (
        latest_task is not None
        and latest_task.status == TaskStatus.FAILED.value
    ):
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
        "prompt_id": ledger.prompt_id,
        "prompt_version": ledger.prompt_version,
    })
    return status, payload


def enqueue_opening_payoff_candidates(
    session: Session,
    settings: Settings,
    run: AnalysisRun,
    *,
    force: bool = False,
) -> Task | None:
    (
        projection,
        _version,
        evidence_by_id,
        chapter_by_unit_id,
        promise_sources,
    ) = _candidate_context(session, settings, run)
    fingerprint = opening_payoff_candidates_source_fingerprint(
        projection
    )
    active_task = session.scalar(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run.id,
            Task.kind == OPENING_PAYOFF_CANDIDATES_TASK_KIND,
            Task.status.in_((
                TaskStatus.PENDING.value,
                TaskStatus.RUNNING.value,
                TaskStatus.RETRY_WAIT.value,
                TaskStatus.WAITING_CONFIRMATION.value,
            )),
        )
        .order_by(AnalysisRunTask.batch_index)
    )
    if active_task is not None:
        return active_task
    latest = session.scalar(
        select(LearningQuestionEvidence)
        .where(
            LearningQuestionEvidence.run_id == run.id,
            LearningQuestionEvidence.question_id
            == OPENING_PAYOFF_CANDIDATES_QUESTION_ID,
        )
        .order_by(LearningQuestionEvidence.revision_no.desc())
    )
    if (
        latest is not None
        and latest.source_fingerprint == fingerprint
        and latest.prompt_version
        == OPENING_PAYOFF_CANDIDATES_PROMPT_VERSION
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
    artifact = _opening_payoff_candidate_artifact(
        projection,
        evidence_by_id,
        chapter_by_unit_id,
        promise_sources,
        candidate_limit=None,
    )
    candidates = [
        item
        for item in artifact.get("candidates", [])
        if isinstance(item, dict)
    ]
    windows = _window_specs(
        candidates,
        input_char_budget=int(
            _request_budget(profile)["input_char_budget"]
        ),
    )
    if not windows:
        return None
    group_id = uuid.uuid4().hex[:8]
    next_index = max(
        (link.batch_index for link in run.task_links),
        default=run.total_batches,
    ) + 1
    created_tasks: list[Task] = []
    for offset, window in enumerate(windows):
        task_payload, max_attempts = prepare_task_provider_routes(
            settings,
            {
                "run_id": run.id,
                "source_version_id": run.source_version_id,
                "source_fingerprint": fingerprint,
                "question_id": "1.4",
                "provider_name": "openai",
                "model_profile_id": profile.id,
                "window_group_id": group_id,
                "window_index": offset + 1,
                "window_count": len(windows),
                **window,
                "analysis_policy": (
                    "ALL_EVENTS_WINDOWED_UNTIL_FIRST_COMPLETE"
                ),
            },
            profile.max_retries + 1,
        )
        task = Task(
            project_id=run.source_version.document.project_id,
            kind=OPENING_PAYOFF_CANDIDATES_TASK_KIND,
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
    session.commit()
    session.refresh(created_tasks[0])
    return created_tasks[0]
