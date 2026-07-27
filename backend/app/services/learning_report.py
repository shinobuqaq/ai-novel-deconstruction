from __future__ import annotations

import json
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


LEARNING_REPORT_TASK_KIND = "analysis.learning_report"
LEARNING_REPORT_PROMPT_ID = "learning_report"
LEARNING_REPORT_PROMPT_VERSION = "1.0.0"
LEARNING_QUESTION_CATALOG_VERSION = "1.0.0"
LEARNING_REPORT_BATCH_LABEL = "核心起步批次（7/42）"


@dataclass(frozen=True, slots=True)
class LearningQuestionDefinition:
    question_id: str
    question: str
    analysis_requirement: str
    evidence_view: str

    @property
    def stage_id(self) -> int:
        return int(self.question_id.split(".", 1)[0])


STAGE_NAMES = {
    1: "开书前决策",
    2: "设定构建",
    3: "开篇",
    4: "连载运营",
    5: "长线管理",
    6: "完本与复盘",
    7: "学习结果应用",
    8: "跨书学习",
}


def _q(question_id: str, question: str, requirement: str, evidence_view: str) -> LearningQuestionDefinition:
    return LearningQuestionDefinition(question_id, question, requirement, evidence_view)


LEARNING_QUESTION_CATALOG = (
    _q("1.1", "品类“必给预期”清单；本书逐条兑现在第几章、怎么兑现？", "品类预期与章节兑现定位", "plot"),
    _q("1.2", "品类雷点禁忌清单；本书哪里擦边、怎么化解？", "品类禁忌、风险情节与化解方式分析", "plot"),
    _q("1.3", "差异化落在哪一层，动没动品类核心预期？", "品类基线与本书差异化对照", "overview"),
    _q("1.4", "卖点能否一句话概括、第几章第一次兑现？", "故事卖点归纳与首次兑现定位", "overview"),
    _q("1.5", "金手指五要素规格；限制条款被真实触发过几次？", "核心能力规格、限制和触发统计", "world"),
    _q("2.1", "前 3/10/30 章实际登场并起作用的人物各几个？", "人物登场、有效行动与分段计数", "characters"),
    _q("2.2", "主角双层欲望与最小完整集各在第几章立住？", "主角欲望、能力、缺陷和立住章节分析", "characters"),
    _q("2.3", "常驻配角按什么功能编制，有无重叠或空缺？", "配角叙事功能与角色编制分析", "characters"),
    _q("2.4", "反派梯队怎么供压？反派有没有自己的事业？", "反派层级、目标与压力供给分析", "characters"),
    _q("2.6", "哪些设定必须开书前锁定，世界观预写边界在哪？", "设定依赖与首次使用顺序倒推", "world"),
    _q("2.7", "设定揭示用什么剧情载体，才不写成说明书？", "设定揭示场景与剧情载体分析", "pacing"),
    _q("2.8", "世界级终极悬念切几段、各隔多少字兑现？", "长线悬念分段、距离与兑现统计", "foreshadowing"),
    _q("2.9", "势力圈层怎么详略、关系怎么重组、主角怎么爬？", "势力关系、圈层变化与主角路径分析", "relations"),
    _q("2.10", "力量体系阶梯够不够长？硬规则与代价是什么？", "力量层级、硬规则和代价执行分析", "world"),
    _q("2.11", "哪些设定是开书前锁死的、哪些边写边长出来的？", "设定出现顺序与前置依赖倒推", "world"),
    _q("2.12", "全书几卷？卷级参数与主线节点密度是多少？", "卷级边界、篇幅与主线节点统计", "plot"),
    _q("3.1", "开场第一幕什么类型，主角怎么登场？", "开场场景功能与主角登场方式分析", "pacing"),
    _q("3.2", "前三章逐章任务与信息装载顺序是什么？", "前三章场景任务和信息释放分析", "pacing"),
    _q("3.3", "第一个爽点在第几章第几段、铺垫距离多少字？", "爽点识别、原文定位与距离统计", "pacing"),
    _q("3.4", "前三章章末钩是什么、多快兑现？", "章末钩识别与兑现距离统计", "pacing"),
    _q("4.1", "有没有可复用的事件单元模板？复用了几次？", "事件单元聚类与复用次数统计", "events"),
    _q("4.2", "卷末大高潮的收束链与波次结构怎么运作？", "卷末事件链、冲突波次与收束分析", "conflicts"),
    _q("4.4", "主支线配比、插入位置与回勾规律是什么？", "主支线分类、篇幅和回勾位置统计", "plot"),
    _q("4.5", "爽点类型怎么配比？重复时怎么对抗边际递减？", "爽点分类、配比与变体分析", "pacing"),
    _q("4.6", "爽点密度曲线什么形状、分阶段怎么变？", "爽点逐章标注与阶段密度统计", "pacing"),
    _q("4.7", "压抑—释放间距与“憋屈上限”是多少？", "压抑和释放配对及间距统计", "pacing"),
    _q("4.9", "章末钩类型配比与强弱节律是什么？", "章末钩逐章分类、强度和节律统计", "pacing"),
    _q("4.10", "悬念的埋设、兑现和存量账本怎么管理？", "悬念生命周期与存量变化统计", "foreshadowing"),
    _q("4.12", "单章内部的微结构模板是什么？", "单章场景序列聚类与模板分析", "pacing"),
    _q("5.1", "战力曲线全景与防膨胀手法是什么？", "能力状态、对手层级与战力变化分析", "states"),
    _q("5.2", "规则与代价被真实执行过吗？硬软怎么区分？", "规则触发、代价执行与例外统计", "world"),
    _q("5.3", "长线伏笔怎么埋、怎么保温、怎么引爆？", "伏笔生命周期、强化和回收分析", "foreshadowing"),
    _q("5.4", "哪类设定最容易吃书？作者怎么圆？", "事实版本冲突、修正与补丁分析", "facts"),
    _q("6.4", "从成书倒推：作者开书前可能锁定了哪些关键决策？这些只作为学习参考，不等于用户新书的最小开书包。", "跨结构证据倒推作者前置决策", "overview"),
    _q("6.5", "本书成功哪些可复制、哪些不可复制？", "方法机制、适用条件与不可复制条件区分", "claims"),
    _q("7.1", "每条学习结论会帮助用户做哪一种新书决策？", "学习结论与创作决策用途映射", "claims"),
    _q("7.2", "每条学习结论是否同时提供通俗解释、证据、适用边界和可收藏的参考卡？", "学习答案完整性与可收藏性检查", "claims"),
    _q("7.3", "用户选择哪些参考方法进入新书共创，如何防止照搬原作？", "用户主动选择、抽象改造和防照搬边界", "claims"),
    _q("7.4", "“节奏”用什么可测指标定义？", "节奏指标定义与逐章统计", "pacing"),
    _q("7.5", "品类套路能否表达为可供用户理解和改造的阶段状态机？", "套路阶段、转移条件与变体分析", "plot"),
    _q("8.1", "哪些结论跨书验证过、可进已验证套路库？", "跨书同类方法比较与样本量记录", "claims"),
    _q("8.2", "哪些参数是品类共性、哪些是个人风格？", "跨书参数分布与作者差异比较", "claims"),
)

PRIMARY_BATCH_QUESTION_IDS = ("1.4", "2.1", "2.2", "4.9", "5.3", "6.4", "6.5")
_RECOMMENDED_RANK = {question_id: rank for rank, question_id in enumerate(PRIMARY_BATCH_QUESTION_IDS, start=1)}
_QUESTION_BY_ID = {item.question_id: item for item in LEARNING_QUESTION_CATALOG}

if len(LEARNING_QUESTION_CATALOG) != 42 or len(_QUESTION_BY_ID) != 42:
    raise RuntimeError("LEARNING_QUESTION_CATALOG_MUST_CONTAIN_42_UNIQUE_QUESTIONS")


class LearningMetricProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=160)
    value: str = Field(min_length=1, max_length=160)
    unit: str = Field(default="", max_length=60)
    method: str = Field(min_length=1, max_length=500)
    evidence_ids: list[str] = Field(default_factory=list, max_length=16)


class LearningAnswerProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(pattern=r"^[1-8]\.[0-9]+$")
    status: Literal["ANSWERED", "PARTIAL", "INSUFFICIENT_EVIDENCE"]
    conclusion: str = Field(min_length=1, max_length=1800)
    metrics: list[LearningMetricProposal] = Field(default_factory=list, max_length=20)
    evidence_ids: list[str] = Field(default_factory=list, max_length=24)
    counter_evidence_ids: list[str] = Field(default_factory=list, max_length=16)
    limitations: list[str] = Field(min_length=1, max_length=10)
    reusable_lessons: list[str] = Field(default_factory=list, max_length=8)
    do_not_copy: list[str] = Field(min_length=1, max_length=8)


class AuthorDecisionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=180)
    likely_timing: Literal["BEFORE_WRITING", "EARLY_SERIALIZATION", "LATER_GROWTH", "UNKNOWN"]
    inference: str = Field(min_length=1, max_length=1200)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    limitations: list[str] = Field(min_length=1, max_length=8)
    confidence: int = Field(ge=0, le=100)


class MethodCandidateProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=180)
    mechanism: str = Field(min_length=1, max_length=1200)
    observed_result: str = Field(min_length=1, max_length=1000)
    applicability: list[str] = Field(min_length=1, max_length=8)
    risks: list[str] = Field(min_length=1, max_length=8)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    do_not_copy: str = Field(min_length=1, max_length=800)
    verification_scope: Literal["SINGLE_BOOK_PENDING"] = "SINGLE_BOOK_PENDING"


class LearningReportOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answers: list[LearningAnswerProposal] = Field(min_length=7, max_length=7)
    author_decisions: list[AuthorDecisionProposal] = Field(default_factory=list, max_length=20)
    method_candidates: list[MethodCandidateProposal] = Field(default_factory=list, max_length=20)


@dataclass(frozen=True, slots=True)
class PersistedLearningReport:
    report_id: str


class LearningReportValidationError(ValueError):
    def __init__(self, code: str, errors: list[dict[str, Any]]) -> None:
        super().__init__(code)
        self.code = code
        self.errors = errors


def _validation_errors(error: ValidationError) -> list[dict[str, Any]]:
    return [
        {
            "path": list(item.get("loc", ())),
            "type": str(item.get("type") or "value_error"),
            "message": str(item.get("msg") or "字段不符合要求"),
        }
        for item in error.errors()
    ]


def _inline_model_schema(model: type[BaseModel]) -> dict:
    raw = model.model_json_schema()
    definitions = raw.get("$defs", {})

    def expand(value: object) -> object:
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/$defs/"):
                return expand(definitions.get(reference.rsplit("/", 1)[-1], {}))
            return {key: expand(item) for key, item in value.items() if key not in {"$defs", "$ref"}}
        if isinstance(value, list):
            return [expand(item) for item in value]
        return value

    return expand(raw)  # type: ignore[return-value]


def _prompt() -> str:
    path = Path(__file__).resolve().parents[3] / "prompts" / "learning_report_v1.md"
    return path.read_text(encoding="utf-8").strip()


def _evidence_ids(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"evidence_ids", "counter_evidence_ids"} and isinstance(item, list):
                found.update(str(entry) for entry in item if entry)
            else:
                found.update(_evidence_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_evidence_ids(item))
    return found


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


def _request_budget_chars(profile: Any) -> int:
    output_reserve = max(1, int(getattr(profile, "max_output_tokens", 16_000)))
    context_window = getattr(profile, "context_window_tokens", None)
    if context_window is not None:
        return min(160_000, max(24_000, int(context_window) - output_reserve - 4_096))
    return max(48_000, min(160_000, output_reserve * 3))


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
            "text": evidence.text_snapshot,
        })
    return records


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


def _source_materials(projection: dict) -> list[tuple[int, str, dict]]:
    deep = projection.get("deep_analysis") or {}
    materials: list[tuple[int, str, dict]] = []
    overview = projection.get("story_overview")
    if isinstance(overview, dict):
        materials.append((100, "story_overview", overview))
    for item in projection.get("characters", []):
        materials.append((92, "character", item))
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
    _service, profile = resolve_analysis_profile(
        settings,
        str(task_payload.get("model_profile_id") or ENTITIES_EVENTS_PROFILE_ID),
    )
    chapter_units = list(session.scalars(
        select(SourceUnit)
        .where(
            SourceUnit.source_version_id == version.id,
            SourceUnit.unit_type == "CHAPTER",
        )
        .order_by(SourceUnit.ordinal)
    ))
    chapter_by_unit_id = {
        unit.id: {"ordinal": ordinal, "title": unit.title}
        for ordinal, unit in enumerate(chapter_units, start=1)
    }
    source_materials = _source_materials(projection)
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
        }
        for question_id in PRIMARY_BATCH_QUESTION_IDS
    ]
    character_index = [
        {
            key: character.get(key)
            for key in (
                "id", "name", "aliases", "role", "role_reason", "goals",
                "motivations", "abilities", "first_chapter_ordinal",
                "last_chapter_ordinal", "appearance_count", "event_ids", "evidence_ids",
            )
        }
        for character in projection.get("characters", [])
    ]
    fixed_input = {
        "catalog_version": LEARNING_QUESTION_CATALOG_VERSION,
        "batch_label": LEARNING_REPORT_BATCH_LABEL,
        "question_catalog": question_catalog,
        "chapter_catalog": [
            {"ordinal": ordinal, "title": unit.title}
            for ordinal, unit in enumerate(chapter_units, start=1)
        ],
        "character_index": character_index,
    }
    fixed_chars = len(json.dumps(fixed_input, ensure_ascii=False, separators=(",", ":")))
    material_budget = max(0, _request_budget_chars(profile) - fixed_chars - 4_000)
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
        size = len(json.dumps(bundle, ensure_ascii=False, separators=(",", ":")))
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
        "input": json.dumps(input_payload, ensure_ascii=False, separators=(",", ":")),
        "output_schema": _inline_model_schema(LearningReportOutput),
        "model_profile_id": str(task_payload.get("model_profile_id") or ENTITIES_EVENTS_PROFILE_ID),
        "prompt_id": LEARNING_REPORT_PROMPT_ID,
        "prompt_version": LEARNING_REPORT_PROMPT_VERSION,
        "source_version_id": version.id,
        "source_char_start": 0,
        "source_char_end": version.total_chars,
        "context_manifest": input_payload["coverage_manifest"],
    }


def parse_learning_report(value: dict) -> LearningReportOutput:
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
    if set(question_ids) != set(PRIMARY_BATCH_QUESTION_IDS):
        raise LearningReportValidationError(
            "LEARNING_REPORT_QUESTION_COVERAGE_INVALID",
            [{"path": ["answers"], "type": "value_error", "message": "必须逐一回答当前 7 个问题"}],
        )
    return output


def persist_learning_report(
    session: Session,
    *,
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
    payload = output.model_dump(mode="json")
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
        "generated_question_ids": list(PRIMARY_BATCH_QUESTION_IDS),
        "source_deep_revision": source_deep_revision,
    })
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    existing = session.scalar(
        select(LearningReport).where(LearningReport.created_by_task_id == task.id)
    )
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
) -> Task | None:
    deep = session.scalar(
        select(DeepAnalysis)
        .where(DeepAnalysis.run_id == run.id)
        .order_by(DeepAnalysis.revision_no.desc())
    )
    if deep is None:
        return None
    latest_report = session.scalar(
        select(LearningReport)
        .where(LearningReport.run_id == run.id)
        .order_by(LearningReport.revision_no.desc())
    )
    latest_task = session.scalar(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run.id,
            Task.kind == LEARNING_REPORT_TASK_KIND,
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
    if latest_report is not None and latest_report.source_deep_revision == deep.revision_no and not force:
        return latest_task
    try:
        _service, profile = resolve_analysis_profile(settings, ENTITIES_EVENTS_PROFILE_ID)
    except ModelSettingsError:
        return None
    task_payload, max_attempts = prepare_task_provider_routes(
        settings,
        {
            "run_id": run.id,
            "source_version_id": run.source_version_id,
            "source_deep_revision": deep.revision_no,
            "provider_name": "openai",
            "model_profile_id": profile.id,
            "question_ids": list(PRIMARY_BATCH_QUESTION_IDS),
            "catalog_version": LEARNING_QUESTION_CATALOG_VERSION,
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
    next_index = max((link.batch_index for link in run.task_links), default=run.total_batches) + 1
    session.add(AnalysisRunTask(run_id=run.id, task_id=task.id, batch_index=next_index))
    run.total_batches = next_index
    run.status = AnalysisRunStatus.PENDING.value
    session.commit()
    session.refresh(task)
    return task


def build_learning_report_projection(
    session: Session,
    run_id: str,
    *,
    latest_deep_revision: int | None,
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
    active_statuses = {
        TaskStatus.PENDING.value,
        TaskStatus.RUNNING.value,
        TaskStatus.RETRY_WAIT.value,
        TaskStatus.WAITING_CONFIRMATION.value,
    }
    if latest_task is not None and latest_task.status in active_statuses:
        status = "GENERATING"
    elif report is not None and report.source_deep_revision == latest_deep_revision:
        status = "READY"
    elif report is not None:
        status = "OUTDATED"
    elif latest_task is not None and latest_task.status == TaskStatus.FAILED.value:
        status = "FAILED"
    else:
        status = "NOT_GENERATED"
    payload = json.loads(report.payload_json) if report is not None else {}
    answer_by_id = {
        item["question_id"]: item
        for item in payload.get("answers", [])
        if item.get("question_id") in _QUESTION_BY_ID
    }
    questions: list[dict[str, object]] = []
    for definition in LEARNING_QUESTION_CATALOG:
        answer = answer_by_id.get(definition.question_id, {})
        questions.append({
            "question_id": definition.question_id,
            "stage_id": definition.stage_id,
            "stage_name": STAGE_NAMES[definition.stage_id],
            "question": definition.question,
            "priority": "CORE",
            "analysis_requirement": definition.analysis_requirement,
            "evidence_view": definition.evidence_view,
            "recommended_rank": _RECOMMENDED_RANK.get(definition.question_id),
            "status": answer.get("status", "NOT_GENERATED"),
            "conclusion": answer.get("conclusion", ""),
            "metrics": answer.get("metrics", []),
            "evidence_ids": answer.get("evidence_ids", []),
            "counter_evidence_ids": answer.get("counter_evidence_ids", []),
            "limitations": answer.get("limitations", []),
            "reusable_lessons": answer.get("reusable_lessons", []),
            "do_not_copy": answer.get("do_not_copy", []),
        })
    stages: list[dict[str, object]] = []
    for stage_id, stage_name in STAGE_NAMES.items():
        stage_questions = [item for item in questions if item["stage_id"] == stage_id]
        stages.append({
            "stage_id": stage_id,
            "stage_name": stage_name,
            "total_count": len(stage_questions),
            "generated_count": sum(item["status"] != "NOT_GENERATED" for item in stage_questions),
            "answered_count": sum(item["status"] in {"ANSWERED", "PARTIAL"} for item in stage_questions),
            "insufficient_count": sum(item["status"] == "INSUFFICIENT_EVIDENCE" for item in stage_questions),
        })
    return status, {
        "catalog_version": LEARNING_QUESTION_CATALOG_VERSION,
        "batch_label": LEARNING_REPORT_BATCH_LABEL,
        "revision": report.revision_no if report is not None else None,
        "source_deep_revision": report.source_deep_revision if report is not None else None,
        "generated_at": report.created_at if report is not None else None,
        "recommended_question_ids": list(PRIMARY_BATCH_QUESTION_IDS),
        "questions": questions,
        "stages": stages,
        "author_decisions": payload.get("author_decisions", []),
        "method_candidates": payload.get("method_candidates", []),
    }
