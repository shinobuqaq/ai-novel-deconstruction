from __future__ import annotations

import hashlib
import json
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
    EvidenceSpan,
    LearningQuestionEvidence,
    SourceUnit,
    Task,
    TaskStatus,
)
from .provider_config import (
    ENTITIES_EVENTS_PROFILE_ID,
    ModelSettingsError,
    prepare_task_provider_routes,
    resolve_analysis_profile,
)


OPENING_STRUCTURE_TASK_KIND = "analysis.opening_structure"
OPENING_STRUCTURE_QUESTION_ID = "3.2"
OPENING_STRUCTURE_PROMPT_ID = "opening_structure"
OPENING_STRUCTURE_PROMPT_VERSION = "1.0.0"
OPENING_STRUCTURE_SOFT_INPUT_TOKENS = 150_000
OPENING_STRUCTURE_REQUEST_OVERHEAD_TOKENS = 4_096
OPENING_STRUCTURE_ESTIMATED_CHARS_PER_TOKEN = 1.5

OPENING_INFORMATION_MODULES = (
    "主角困境",
    "主角性格",
    "核心能力",
    "世界观规则",
    "威胁",
    "短期目标",
)


class OpeningParagraphSegmentProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_ordinal: int = Field(ge=1, le=3)
    paragraph_start: int = Field(ge=1)
    paragraph_end: int = Field(ge=1)
    scope: Literal["FRONT_MATTER", "STORY"]
    function: str = Field(min_length=1, max_length=160)
    information_modules: list[
        Literal[
            "主角困境",
            "主角性格",
            "核心能力",
            "世界观规则",
            "威胁",
            "短期目标",
        ]
    ] = Field(default_factory=list, max_length=6)
    explanation: str = Field(min_length=1, max_length=800)


class OpeningChapterTaskProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_ordinal: int = Field(ge=1, le=3)
    tasks: list[str] = Field(min_length=1, max_length=12)
    key_event_paragraphs: list[int] = Field(min_length=1, max_length=24)
    explanation: str = Field(min_length=1, max_length=1200)


class OpeningModuleAssessmentProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    module: Literal[
        "主角困境",
        "主角性格",
        "核心能力",
        "世界观规则",
        "威胁",
        "短期目标",
    ]
    status: Literal["SUPPORTED", "NOT_OBSERVED"]
    first_chapter_ordinal: int | None = Field(default=None, ge=1, le=3)
    first_paragraph: int | None = Field(default=None, ge=1)
    finding: str = Field(min_length=1, max_length=800)


class OpeningSceneCardProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opening_type: str = Field(min_length=1, max_length=160)
    story_start_paragraph: int = Field(ge=1)
    protagonist_first_paragraph: int = Field(ge=1)
    protagonist_action: str = Field(min_length=1, max_length=800)
    initial_trouble: str = Field(min_length=1, max_length=800)
    first_sentence_function: str = Field(min_length=1, max_length=500)
    first_paragraph_function: str = Field(min_length=1, max_length=500)
    explanation: str = Field(min_length=1, max_length=1200)


class OpeningStructureOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opening_scene: OpeningSceneCardProposal
    chapter_tasks: list[OpeningChapterTaskProposal] = Field(
        min_length=3,
        max_length=3,
    )
    paragraph_segments: list[OpeningParagraphSegmentProposal] = Field(
        min_length=3,
        max_length=1500,
    )
    module_assessments: list[OpeningModuleAssessmentProposal] = Field(
        min_length=6,
        max_length=6,
    )
    overall_sequence: str = Field(min_length=1, max_length=2000)
    limitations: list[str] = Field(default_factory=list, max_length=12)


class OpeningStructureValidationError(ValueError):
    def __init__(self, errors: list[dict[str, object]]):
        super().__init__("OPENING_STRUCTURE_SCHEMA_INVALID")
        self.validation_errors = errors


def _validation_errors(exc: ValidationError) -> list[dict[str, object]]:
    return [
        {
            "path": [str(part) for part in item.get("loc", ())],
            "type": str(item.get("type") or "value_error"),
            "message": str(item.get("msg") or "字段无效"),
        }
        for item in exc.errors()
    ]


def parse_opening_structure(value: dict) -> OpeningStructureOutput:
    if (
        set(value) == {"properties"}
        and isinstance(value.get("properties"), dict)
    ):
        value = value["properties"]
    try:
        return OpeningStructureOutput.model_validate(value)
    except ValidationError as exc:
        raise OpeningStructureValidationError(
            _validation_errors(exc)
        ) from exc


def _inline_model_schema(model: type[BaseModel]) -> dict:
    raw = model.model_json_schema()
    definitions = raw.pop("$defs", {})

    def expand(value: object) -> object:
        if isinstance(value, list):
            return [expand(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            name = str(value["$ref"]).split("/")[-1]
            merged = dict(definitions[name])
            merged.update({
                key: item for key, item in value.items() if key != "$ref"
            })
            return expand(merged)
        return {key: expand(item) for key, item in value.items()}

    return expand(raw)  # type: ignore[return-value]


def _prompt() -> str:
    path = (
        Path(__file__).resolve().parents[3]
        / "prompts"
        / "opening_structure_v1.md"
    )
    return path.read_text(encoding="utf-8").strip()


def _base_projection(session: Session, run_id: str) -> dict:
    from .workbench import build_workbench_projection

    return build_workbench_projection(
        session,
        run_id,
        include_question_evidence=False,
    )


def _opening_units(
    session: Session,
    source_version_id: str,
) -> list[SourceUnit]:
    return list(session.scalars(
        select(SourceUnit)
        .where(
            SourceUnit.source_version_id == source_version_id,
            SourceUnit.unit_type == "CHAPTER",
        )
        .order_by(SourceUnit.ordinal)
        .limit(3)
    ))


def _opening_paragraphs(
    session: Session,
    units: list[SourceUnit],
) -> tuple[list[dict[str, object]], dict[tuple[int, int], EvidenceSpan]]:
    result: list[dict[str, object]] = []
    by_position: dict[tuple[int, int], EvidenceSpan] = {}
    for chapter_ordinal, unit in enumerate(units, start=1):
        spans = list(session.scalars(
            select(EvidenceSpan)
            .where(EvidenceSpan.source_unit_id == unit.id)
            .order_by(
                EvidenceSpan.paragraph_index,
                EvidenceSpan.start_char,
            )
        ))
        paragraphs: list[dict[str, object]] = []
        for paragraph_number, span in enumerate(spans, start=1):
            by_position[(chapter_ordinal, paragraph_number)] = span
            paragraphs.append({
                "paragraph": paragraph_number,
                "source_paragraph_index": span.paragraph_index,
                "source_char_start": span.start_char,
                "source_char_end": span.end_char,
                "evidence_id": span.id,
                "text": span.text_snapshot,
            })
        result.append({
            "chapter_ordinal": chapter_ordinal,
            "chapter_title": unit.title,
            "paragraph_count": len(paragraphs),
            "paragraphs": paragraphs,
        })
    return result, by_position


def opening_structure_source_fingerprint(
    projection: dict,
    units: list[SourceUnit],
) -> str:
    overview = projection.get("story_overview") or {}
    payload = {
        "policy_version": OPENING_STRUCTURE_PROMPT_VERSION,
        "source_version_id": projection.get("source_version_id"),
        "protagonist": overview.get("protagonist"),
        "chapter_hashes": [
            {
                "id": unit.id,
                "ordinal": unit.ordinal,
                "content_hash": unit.content_hash,
            }
            for unit in units
        ],
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _sources_ready(projection: dict, units: list[SourceUnit]) -> bool:
    overview = projection.get("story_overview") or {}
    return bool(
        len(units) == 3
        and projection.get("deep_status") == "READY"
        and str(overview.get("protagonist") or "").strip()
    )


def _request_budget(profile: Any) -> dict[str, int | str | None]:
    output_reserve = max(
        1,
        int(getattr(profile, "max_output_tokens", 16_000)),
    )
    context_window = getattr(profile, "context_window_tokens", None)
    safe_input_tokens = OPENING_STRUCTURE_SOFT_INPUT_TOKENS
    source = "default_soft_input_cap_tokens"
    if context_window is not None:
        safe_input_tokens = min(
            safe_input_tokens,
            max(
                1,
                int(context_window)
                - output_reserve
                - OPENING_STRUCTURE_REQUEST_OVERHEAD_TOKENS,
            ),
        )
        source = "min(default_soft_input_cap_tokens, model_context_minus_reserve)"
    return {
        "input_token_budget": safe_input_tokens,
        "input_char_budget": int(
            safe_input_tokens
            * OPENING_STRUCTURE_ESTIMATED_CHARS_PER_TOKEN
        ),
        "output_reserve_tokens": output_reserve,
        "model_context_window_tokens": context_window,
        "budget_source": source,
    }


def provider_payload_for_opening_structure(
    session: Session,
    settings: Settings,
    task_payload: dict,
) -> dict:
    run = session.get(AnalysisRun, task_payload.get("run_id"))
    if run is None:
        raise ValueError("ANALYSIS_RUN_NOT_FOUND")
    projection = _base_projection(session, run.id)
    units = _opening_units(session, run.source_version_id)
    if not _sources_ready(projection, units):
        raise ValueError("OPENING_STRUCTURE_SOURCE_NOT_READY")
    fingerprint = opening_structure_source_fingerprint(projection, units)
    if fingerprint != task_payload.get("source_fingerprint"):
        raise ValueError("OPENING_STRUCTURE_SOURCE_OUTDATED")
    chapters, _spans = _opening_paragraphs(session, units)
    compact_chapters = [
        {
            "chapter_ordinal": chapter["chapter_ordinal"],
            "chapter_title": chapter["chapter_title"],
            "paragraph_count": chapter["paragraph_count"],
            "paragraph_columns": [
                "paragraph",
                "source_char_start",
                "source_char_end",
                "evidence_id",
                "text",
            ],
            "paragraphs": [
                [
                    paragraph["paragraph"],
                    paragraph["source_char_start"],
                    paragraph["source_char_end"],
                    paragraph["evidence_id"],
                    paragraph["text"],
                ]
                for paragraph in chapter["paragraphs"]
            ],
        }
        for chapter in chapters
    ]
    overview = projection.get("story_overview") or {}
    input_payload = {
        "question_group": ["3.1", "3.2"],
        "validation_policy": {
            "objective_checks": (
                "程序只校验三章和段落连续覆盖、引用位置、字符位置、"
                "来源版本及六个观察模块是否逐项返回。"
            ),
            "literary_judgments": (
                "开场类型、段落功能、章节任务和信息模块归属由模型结合"
                "上下文判断；观察模块不是必须全部出现的硬门槛。"
            ),
        },
        "contracts": {
            "3.1": (
                "首章开场卡：开场类型、正文起点、主角出场段落、"
                "正在做什么、初始麻烦、第一句话和第一段的功能。"
            ),
            "3.2": (
                "前三章逐章任务、连续段落功能序列、关键事件位置，"
                "以及六类信息模块的首次位置与实际承载字符数。"
            ),
        },
        "protagonist": str(overview.get("protagonist") or ""),
        "information_modules": list(OPENING_INFORMATION_MODULES),
        "chapters": compact_chapters,
    }
    _service, profile = resolve_analysis_profile(
        settings,
        str(
            task_payload.get("model_profile_id")
            or ENTITIES_EVENTS_PROFILE_ID
        ),
    )
    budget = _request_budget(profile)
    input_text = json.dumps(
        input_payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(input_text) > int(budget["input_char_budget"]):
        raise ValueError(
            "OPENING_STRUCTURE_CONTEXT_TOO_LARGE:"
            f"{len(input_text)}:{budget['input_char_budget']}"
        )
    return {
        "instructions": _prompt(),
        "input": input_text,
        "output_schema": _inline_model_schema(OpeningStructureOutput),
        "model_profile_id": str(
            task_payload.get("model_profile_id")
            or ENTITIES_EVENTS_PROFILE_ID
        ),
        "prompt_id": OPENING_STRUCTURE_PROMPT_ID,
        "prompt_version": OPENING_STRUCTURE_PROMPT_VERSION,
        "source_version_id": run.source_version_id,
        "source_char_start": units[0].start_char,
        "source_char_end": units[-1].end_char,
        "context_manifest": {
            "question_group": ["3.1", "3.2"],
            "chapter_count": len(chapters),
            "paragraph_count": sum(
                int(item["paragraph_count"]) for item in chapters
            ),
            "input_chars": len(input_text),
            "input_budget_chars": budget["input_char_budget"],
            **budget,
        },
    }


def _paragraph(
    by_position: dict[tuple[int, int], EvidenceSpan],
    chapter_ordinal: int,
    paragraph: int,
) -> EvidenceSpan:
    span = by_position.get((chapter_ordinal, paragraph))
    if span is None:
        raise ValueError("OPENING_STRUCTURE_PARAGRAPH_REFERENCE_INVALID")
    return span


def _validate_and_compile(
    output: OpeningStructureOutput,
    chapters: list[dict[str, object]],
    by_position: dict[tuple[int, int], EvidenceSpan],
) -> dict[str, object]:
    expected_chapters = [1, 2, 3]
    chapter_tasks = sorted(
        output.chapter_tasks,
        key=lambda item: item.chapter_ordinal,
    )
    if [item.chapter_ordinal for item in chapter_tasks] != expected_chapters:
        raise ValueError("OPENING_STRUCTURE_CHAPTER_TASK_COVERAGE_INVALID")

    segments = sorted(
        output.paragraph_segments,
        key=lambda item: (
            item.chapter_ordinal,
            item.paragraph_start,
            item.paragraph_end,
        ),
    )
    compiled_segments: list[dict[str, object]] = []
    for chapter_ordinal, chapter in enumerate(chapters, start=1):
        chapter_segments = [
            item
            for item in segments
            if item.chapter_ordinal == chapter_ordinal
        ]
        expected_start = 1
        paragraph_count = int(chapter["paragraph_count"])
        for item in chapter_segments:
            if (
                item.paragraph_start != expected_start
                or item.paragraph_end < item.paragraph_start
                or item.paragraph_end > paragraph_count
            ):
                raise ValueError(
                    "OPENING_STRUCTURE_PARAGRAPH_COVERAGE_INVALID"
                )
            spans = [
                _paragraph(by_position, chapter_ordinal, paragraph)
                for paragraph in range(
                    item.paragraph_start,
                    item.paragraph_end + 1,
                )
            ]
            compiled_segments.append({
                **item.model_dump(mode="json"),
                "character_count": sum(
                    span.end_char - span.start_char for span in spans
                ),
                "source_char_start": spans[0].start_char,
                "source_char_end": spans[-1].end_char,
                "evidence_ids": [span.id for span in spans],
            })
            expected_start = item.paragraph_end + 1
        if expected_start != paragraph_count + 1:
            raise ValueError(
                "OPENING_STRUCTURE_PARAGRAPH_COVERAGE_INVALID"
            )

    task_payloads: list[dict[str, object]] = []
    for item in chapter_tasks:
        paragraph_count = int(
            chapters[item.chapter_ordinal - 1]["paragraph_count"]
        )
        if any(
            paragraph < 1 or paragraph > paragraph_count
            for paragraph in item.key_event_paragraphs
        ):
            raise ValueError("OPENING_STRUCTURE_KEY_EVENT_INVALID")
        spans = [
            _paragraph(
                by_position,
                item.chapter_ordinal,
                paragraph,
            )
            for paragraph in item.key_event_paragraphs
        ]
        task_payloads.append({
            **item.model_dump(mode="json"),
            "key_event_positions": [
                {
                    "paragraph": paragraph,
                    "source_char_start": span.start_char,
                    "source_char_end": span.end_char,
                    "evidence_id": span.id,
                }
                for paragraph, span in zip(
                    item.key_event_paragraphs,
                    spans,
                )
            ],
            "evidence_ids": [span.id for span in spans],
        })

    assessments = {
        item.module: item for item in output.module_assessments
    }
    if set(assessments) != set(OPENING_INFORMATION_MODULES):
        raise ValueError("OPENING_STRUCTURE_MODULE_COVERAGE_INVALID")
    module_timeline: list[dict[str, object]] = []
    for module in OPENING_INFORMATION_MODULES:
        item = assessments[module]
        if item.status == "SUPPORTED":
            if (
                item.first_chapter_ordinal is None
                or item.first_paragraph is None
            ):
                raise ValueError(
                    "OPENING_STRUCTURE_MODULE_POSITION_MISSING"
                )
            span = _paragraph(
                by_position,
                item.first_chapter_ordinal,
                item.first_paragraph,
            )
            containing_segment = next(
                (
                    segment
                    for segment in compiled_segments
                    if segment["chapter_ordinal"]
                    == item.first_chapter_ordinal
                    and int(segment["paragraph_start"])
                    <= item.first_paragraph
                    <= int(segment["paragraph_end"])
                ),
                None,
            )
            if (
                containing_segment is None
                or module
                not in containing_segment["information_modules"]
            ):
                raise ValueError(
                    "OPENING_STRUCTURE_MODULE_POSITION_INCONSISTENT"
                )
            module_segments = [
                segment
                for segment in compiled_segments
                if module in segment["information_modules"]
                and segment["scope"] == "STORY"
            ]
            module_timeline.append({
                **item.model_dump(mode="json"),
                "source_char_start": span.start_char,
                "source_char_end": span.end_char,
                "evidence_ids": [span.id],
                "character_count": sum(
                    int(segment["character_count"])
                    for segment in module_segments
                ),
            })
        else:
            if (
                item.first_chapter_ordinal is not None
                or item.first_paragraph is not None
            ):
                raise ValueError(
                    "OPENING_STRUCTURE_MODULE_POSITION_UNEXPECTED"
                )
            module_timeline.append({
                **item.model_dump(mode="json"),
                "source_char_start": None,
                "source_char_end": None,
                "evidence_ids": [],
                "character_count": 0,
            })

    opening = output.opening_scene
    story_start = _paragraph(
        by_position,
        1,
        opening.story_start_paragraph,
    )
    protagonist_first = _paragraph(
        by_position,
        1,
        opening.protagonist_first_paragraph,
    )
    opening_segments = [
        segment
        for segment in compiled_segments
        if segment["chapter_ordinal"] == 1
    ]
    story_start_segment = next(
        (
            item
            for item in opening_segments
            if int(item["paragraph_start"])
            <= opening.story_start_paragraph
            <= int(item["paragraph_end"])
        ),
        None,
    )
    protagonist_segment = next(
        (
            item
            for item in opening_segments
            if int(item["paragraph_start"])
            <= opening.protagonist_first_paragraph
            <= int(item["paragraph_end"])
        ),
        None,
    )
    if (
        story_start_segment is None
        or story_start_segment["scope"] != "STORY"
        or protagonist_segment is None
        or protagonist_segment["scope"] != "STORY"
        or opening.protagonist_first_paragraph
        < opening.story_start_paragraph
    ):
        raise ValueError("OPENING_STRUCTURE_OPENING_POSITION_INVALID")

    opening_card = {
        **opening.model_dump(mode="json"),
        "chapter_ordinal": 1,
        "chapter_title": chapters[0]["chapter_title"],
        "story_start_source_char": story_start.start_char,
        "story_start_evidence_id": story_start.id,
        "protagonist_first_source_char": protagonist_first.start_char,
        "protagonist_first_evidence_id": protagonist_first.id,
        "evidence_ids": [story_start.id, protagonist_first.id],
    }
    story_character_count = sum(
        int(segment["character_count"])
        for segment in compiled_segments
        if segment["scope"] == "STORY"
    )
    return {
        "opening_scene": opening_card,
        "chapter_tasks": task_payloads,
        "paragraph_segments": compiled_segments,
        "information_timeline": module_timeline,
        "overall_sequence": output.overall_sequence,
        "limitations": output.limitations,
        "coverage": {
            "chapter_count": 3,
            "paragraph_count": len(by_position),
            "story_character_count": story_character_count,
            "paragraph_coverage_complete": True,
            "paragraph_sequence_contiguous": True,
            "information_module_assessed_count": len(module_timeline),
            "analysis_policy": "FIRST_THREE_CHAPTERS_ALL_PARAGRAPHS",
        },
    }


def persist_opening_structure(
    session: Session,
    *,
    task: Task,
    attempt_id: str,
    task_payload: dict,
    output: OpeningStructureOutput,
) -> LearningQuestionEvidence:
    existing = session.scalar(
        select(LearningQuestionEvidence).where(
            LearningQuestionEvidence.created_by_task_id == task.id
        )
    )
    if existing is not None:
        return existing
    run = session.get(AnalysisRun, task_payload.get("run_id"))
    if run is None:
        raise ValueError("ANALYSIS_RUN_NOT_FOUND")
    projection = _base_projection(session, run.id)
    units = _opening_units(session, run.source_version_id)
    if not _sources_ready(projection, units):
        raise ValueError("OPENING_STRUCTURE_SOURCE_NOT_READY")
    fingerprint = opening_structure_source_fingerprint(projection, units)
    if fingerprint != task_payload.get("source_fingerprint"):
        raise ValueError("OPENING_STRUCTURE_SOURCE_OUTDATED")
    chapters, by_position = _opening_paragraphs(session, units)
    payload = _validate_and_compile(output, chapters, by_position)
    revision_no = int(session.scalar(
        select(func.max(LearningQuestionEvidence.revision_no)).where(
            LearningQuestionEvidence.run_id == run.id,
            LearningQuestionEvidence.question_id
            == OPENING_STRUCTURE_QUESTION_ID,
        )
    ) or 0) + 1
    ledger = LearningQuestionEvidence(
        run_id=run.id,
        source_version_id=run.source_version_id,
        question_id=OPENING_STRUCTURE_QUESTION_ID,
        revision_no=revision_no,
        source_fingerprint=fingerprint,
        payload_json=json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
        ),
        prompt_id=OPENING_STRUCTURE_PROMPT_ID,
        prompt_version=OPENING_STRUCTURE_PROMPT_VERSION,
        created_by_task_id=task.id,
        created_by_attempt_id=attempt_id,
    )
    session.add(ledger)
    session.commit()
    session.refresh(ledger)
    return ledger


def build_opening_structure_projection(
    session: Session,
    run_id: str,
    projection: dict,
) -> tuple[str, dict | None]:
    ledger = session.scalar(
        select(LearningQuestionEvidence)
        .where(
            LearningQuestionEvidence.run_id == run_id,
            LearningQuestionEvidence.question_id
            == OPENING_STRUCTURE_QUESTION_ID,
        )
        .order_by(LearningQuestionEvidence.revision_no.desc())
    )
    latest_task = session.scalar(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run_id,
            Task.kind == OPENING_STRUCTURE_TASK_KIND,
        )
        .order_by(AnalysisRunTask.batch_index.desc())
    )
    active_task = session.scalar(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run_id,
            Task.kind == OPENING_STRUCTURE_TASK_KIND,
            Task.status.in_((
                TaskStatus.PENDING.value,
                TaskStatus.RUNNING.value,
                TaskStatus.RETRY_WAIT.value,
                TaskStatus.WAITING_CONFIRMATION.value,
            )),
        )
        .order_by(AnalysisRunTask.batch_index)
    )
    run = session.get(AnalysisRun, run_id)
    units = (
        _opening_units(session, run.source_version_id)
        if run is not None
        else []
    )
    fingerprint = (
        opening_structure_source_fingerprint(projection, units)
        if len(units) == 3
        else None
    )
    is_current = bool(
        ledger is not None
        and fingerprint is not None
        and ledger.source_fingerprint == fingerprint
        and ledger.prompt_version == OPENING_STRUCTURE_PROMPT_VERSION
    )
    if active_task is not None:
        status = "GENERATING"
    elif is_current:
        status = "READY"
    elif ledger is not None:
        status = "OUTDATED"
    elif (
        latest_task is not None
        and (
            latest_task.status == TaskStatus.FAILED.value
            or (
                latest_task.status == TaskStatus.CANCELLED.value
                and bool(latest_task.last_error_code)
            )
        )
    ):
        status = "FAILED"
    else:
        status = "NOT_GENERATED"
    if ledger is None:
        return status, None
    payload = json.loads(ledger.payload_json)
    payload.update({
        "question_id": ledger.question_id,
        "question_ids": ["3.1", "3.2"],
        "revision": ledger.revision_no,
        "generated_at": ledger.created_at,
        "is_current": is_current,
        "prompt_id": ledger.prompt_id,
        "prompt_version": ledger.prompt_version,
    })
    return status, payload


def enqueue_opening_structure(
    session: Session,
    settings: Settings,
    run: AnalysisRun,
    *,
    force: bool = False,
) -> Task | None:
    projection = _base_projection(session, run.id)
    units = _opening_units(session, run.source_version_id)
    if not _sources_ready(projection, units):
        return None
    fingerprint = opening_structure_source_fingerprint(projection, units)
    active_task = session.scalar(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run.id,
            Task.kind == OPENING_STRUCTURE_TASK_KIND,
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
    latest_ledger = session.scalar(
        select(LearningQuestionEvidence)
        .where(
            LearningQuestionEvidence.run_id == run.id,
            LearningQuestionEvidence.question_id
            == OPENING_STRUCTURE_QUESTION_ID,
        )
        .order_by(LearningQuestionEvidence.revision_no.desc())
    )
    if (
        latest_ledger is not None
        and latest_ledger.source_fingerprint == fingerprint
        and latest_ledger.prompt_version
        == OPENING_STRUCTURE_PROMPT_VERSION
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
    budget = _request_budget(profile)
    payload, max_attempts = prepare_task_provider_routes(
        settings,
        {
            "run_id": run.id,
            "source_version_id": run.source_version_id,
            "source_fingerprint": fingerprint,
            "question_ids": ["3.1", "3.2"],
            "provider_name": "openai",
            "model_profile_id": profile.id,
            "analysis_policy": (
                "FIRST_THREE_CHAPTERS_ALL_PARAGRAPHS_ONE_SHARED_TASK"
            ),
            "task_input_budget": budget,
        },
        profile.max_retries + 1,
    )
    task = Task(
        project_id=run.source_version.document.project_id,
        kind=OPENING_STRUCTURE_TASK_KIND,
        payload_json=json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
        ),
        max_attempts=max_attempts,
    )
    session.add(task)
    session.flush()
    next_index = max(
        (link.batch_index for link in run.task_links),
        default=run.total_batches,
    ) + 1
    session.add(AnalysisRunTask(
        run_id=run.id,
        task_id=task.id,
        batch_index=next_index,
    ))
    run.total_batches = next_index
    run.status = AnalysisRunStatus.PENDING.value
    session.commit()
    session.refresh(task)
    return task
