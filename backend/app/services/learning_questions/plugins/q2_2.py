from __future__ import annotations

import re
from typing import Any

from ..base import BaseQuestionPlugin
from ..schemas import (
    LearningAnswerProposal,
    LearningContractItemProposal,
    LearningMetricProposal,
)
from ..common import (
    _contract_item_by_id,
    _evidence_ids,
    _metric_group_matches_count,
    _metric_value_matches_number,
    _metrics_with_label,
    _validate_answer_evidence_subset,
)

def _normalized_2_2_text(value: object) -> str:
    return re.sub(
        r"[，,。；;！？!?：:“”‘’\"']+",
        "",
        "".join(str(value or "").split()).casefold(),
    )


def _trim_2_2_sentence(value: object) -> str:
    return str(value or "").strip().rstrip("，,。；;！？!?：:")


def _2_2_metric_matches_with_evidence(
    item: LearningContractItemProposal,
    aliases: tuple[str, ...],
    value: int,
    expected_evidence: set[str],
) -> bool:
    return any(
        _metric_value_matches_number(metric, value)
        and set(metric.evidence_ids) == expected_evidence
        for metric in _metrics_with_label(item, aliases)
    )


def _2_2_ordered_nodes_match(
    finding: str,
    nodes: list[dict],
    *,
    detail_keys: tuple[str, ...],
) -> bool:
    chapter_positions: list[int] = []
    cursor = 0
    for node in nodes:
        chapter = int(node.get("chapter_ordinal") or 0)
        if chapter <= 0:
            return False
        match = re.search(
            rf"第\s*{re.escape(str(chapter))}\s*章",
            finding[cursor:],
        )
        if match is None:
            return False
        chapter_start = cursor + match.start()
        chapter_positions.append(chapter_start)
        cursor += match.end()

    for index, node in enumerate(nodes):
        segment_end = (
            chapter_positions[index + 1]
            if index + 1 < len(chapter_positions)
            else len(finding)
        )
        segment = _normalized_2_2_text(
            finding[chapter_positions[index]:segment_end]
        )
        for key in detail_keys:
            expected = _normalized_2_2_text(node.get(key))
            if expected and expected not in segment:
                return False
    return True


def _program_2_2_contract_items(
    ledger: dict,
) -> list[LearningContractItemProposal]:
    field_order = (
        "surface_desire",
        "deep_desire",
        "motivation",
        "contrast",
        "boundary",
        "core_ability",
    )
    fields = {
        str(item.get("field") or ""): item
        for item in ledger.get("fields", [])
        if isinstance(item, dict)
    }
    compiled: list[LearningContractItemProposal] = []
    for field_id in field_order:
        source = fields.get(field_id)
        if not isinstance(source, dict):
            raise ValueError("LEARNING_REPORT_2_2_SOURCE_FIELD_MISSING")
        status = str(source.get("status") or "")
        explanation = _trim_2_2_sentence(source.get("explanation"))
        chapter = int(source.get("first_display_chapter_ordinal") or 0)
        value = _trim_2_2_sentence(source.get("value"))
        display_event = _trim_2_2_sentence(source.get("display_event"))
        evidence_ids = list(dict.fromkeys(
            str(evidence_id)
            for evidence_id in source.get("evidence_ids", [])
            if evidence_id
        ))
        if status == "INSUFFICIENT_EVIDENCE":
            if (
                chapter > 0
                or value
                or display_event
                or evidence_ids
                or source.get("first_display_event_id") is not None
                or not explanation
            ):
                raise ValueError(
                    f"LEARNING_REPORT_2_2_{field_id.upper()}_SOURCE_INVALID"
                )
            compiled.append(LearningContractItemProposal.model_validate({
                "item_id": field_id,
                "status": "INSUFFICIENT_EVIDENCE",
                "finding": explanation,
                "metrics": [],
                "evidence_ids": [],
                "limitations": [explanation],
                "classifications": [],
            }))
            continue
        if (
            status != "SUPPORTED"
            or chapter <= 0
            or not value
            or not display_event
            or not evidence_ids
        ):
            raise ValueError(
                f"LEARNING_REPORT_2_2_{field_id.upper()}_SOURCE_INVALID"
            )
        compiled.append(LearningContractItemProposal.model_validate({
            "item_id": field_id,
            "status": "SUPPORTED",
            "finding": f"第 {chapter} 章，{display_event}：{value}。",
            "metrics": [{
                "label": "首次展示章节",
                "value": str(chapter),
                "unit": "章",
                "method": "由主角专项证据账本确定性定位。",
                "evidence_ids": evidence_ids,
            }],
            "evidence_ids": evidence_ids,
            "limitations": [],
            "classifications": [],
        }))

    conflicts = [
        item
        for item in ledger.get("desire_conflicts", [])
        if isinstance(item, dict)
    ]
    conflicts = [
        item
        for _index, item in sorted(
            enumerate(conflicts),
            key=lambda pair: (
                int(pair[1].get("chapter_ordinal") or 0),
                pair[0],
            ),
        )
    ]
    conflict_evidence_ids = list(dict.fromkeys(
        str(evidence_id)
        for conflict in conflicts
        for evidence_id in conflict.get("evidence_ids", [])
        if evidence_id
    ))
    if any(
        int(conflict.get("chapter_ordinal") or 0) <= 0
        or not str(conflict.get("arc_change") or "").strip()
        or not conflict.get("evidence_ids")
        for conflict in conflicts
    ):
        raise ValueError("LEARNING_REPORT_2_2_CONFLICT_SOURCE_INVALID")

    observation_kind_labels = {
        "CONFLICT": "欲望冲突",
        "DEEPENING": "欲望加深",
        "SHIFT": "目标转向",
        "PRESSURE": "欲望受压",
        "REINTERPRETATION": "重新解释",
        "OTHER": "人物变化",
    }
    def observation_detail(conflict: dict) -> str:
        details = [
            f"表层欲望“{_trim_2_2_sentence(conflict.get('surface_desire'))}”"
            if conflict.get("surface_desire") else "",
            f"深层欲望“{_trim_2_2_sentence(conflict.get('deep_desire'))}”"
            if conflict.get("deep_desire") else "",
            f"现场动机“{_trim_2_2_sentence(conflict.get('motive'))}”"
            if conflict.get("motive") else "",
            f"选择“{_trim_2_2_sentence(conflict.get('choice'))}”"
            if conflict.get("choice") else "",
            f"结果“{_trim_2_2_sentence(conflict.get('result'))}”"
            if conflict.get("result") else "",
            f"代价“{_trim_2_2_sentence(conflict.get('sacrifice'))}”"
            if conflict.get("sacrifice") else "",
        ]
        return "，".join(detail for detail in details if detail)

    conflict_finding = (
        "".join(
            (
                f"第 {int(conflict['chapter_ordinal'])} 章，"
                f"{observation_kind_labels.get(str(conflict.get('observation_kind') or 'CONFLICT'), '人物变化')}："
                f"{observation_detail(conflict) or _trim_2_2_sentence(conflict['arc_change'])}。"
            )
            for conflict in conflicts
        )
        or (
            "当前专项账本尚未记录到有原文依据的欲望变化或冲突观察；"
            "这不能反推主角中后期没有变化。"
        )
    )
    arc_finding = (
        "".join(
            (
                f"第 {int(conflict['chapter_ordinal'])} 章，"
                f"{_trim_2_2_sentence(conflict['arc_change'])}。"
            )
            for conflict in conflicts
        )
        or (
            "当前没有可编入时间轴的变化观察；需要重新按加深、转向、"
            "受压、重新解释和冲突等宽口径检查后文。"
        )
    )
    for item_id, finding, label in (
        ("desire_conflicts", conflict_finding, "变化观察"),
        ("arc_timeline", arc_finding, "时间轴节点"),
    ):
        compiled.append(LearningContractItemProposal.model_validate({
            "item_id": item_id,
            "status": "SUPPORTED" if conflicts else "INSUFFICIENT_EVIDENCE",
            "finding": finding,
            "metrics": [{
                "label": label,
                "value": str(len(conflicts)),
                "unit": "个",
                "method": "由主角专项证据账本按章节顺序确定性汇总。",
                "evidence_ids": conflict_evidence_ids,
            }],
            "evidence_ids": conflict_evidence_ids,
            "limitations": (
                [] if conflicts else [
                    "账本空白只表示当前未记录，不能解释为人物没有变化。"
                ]
            ),
            "classifications": [],
        }))
    return compiled


def _apply_program_2_2_answer(
    answer: LearningAnswerProposal,
    ledger: dict,
) -> None:
    field_labels = (
        ("surface_desire", "表层欲望"),
        ("deep_desire", "深层欲望"),
        ("motivation", "动机"),
        ("contrast", "反差"),
        ("boundary", "边界"),
        ("core_ability", "核心能力"),
    )
    answer.contract_items = _program_2_2_contract_items(ledger)
    fields = {
        str(item.get("field") or ""): item
        for item in ledger.get("fields", [])
        if isinstance(item, dict)
    }
    field_summaries: list[str] = []
    insufficient_field_summaries: list[str] = []
    metrics: list[LearningMetricProposal] = []
    representative_evidence: list[str] = []
    for field_id, label in field_labels:
        source = fields[field_id]
        if source.get("status") == "INSUFFICIENT_EVIDENCE":
            explanation = _trim_2_2_sentence(source.get("explanation"))
            insufficient_field_summaries.append(
                f"{label}：{explanation}"
            )
            field_summaries.append(f"{label}证据不足：{explanation}")
            continue
        chapter = int(source["first_display_chapter_ordinal"])
        value = _trim_2_2_sentence(source["value"])
        evidence_ids = list(dict.fromkeys(
            str(evidence_id)
            for evidence_id in source.get("evidence_ids", [])
            if evidence_id
        ))
        field_summaries.append(f"{label}的早期基线在第 {chapter} 章立住：{value}")
        metrics.append(LearningMetricProposal(
            label=f"{label}首次展示章节",
            value=str(chapter),
            unit="章",
            method="由主角专项证据账本确定性定位。",
            evidence_ids=evidence_ids[:16],
        ))
        representative_evidence.extend(evidence_ids)

    conflicts = [
        item
        for item in ledger.get("desire_conflicts", [])
        if isinstance(item, dict)
    ]
    conflicts = [
        item
        for _index, item in sorted(
            enumerate(conflicts),
            key=lambda pair: (
                int(pair[1].get("chapter_ordinal") or 0),
                pair[0],
            ),
        )
    ]
    conflict_evidence = list(dict.fromkeys(
        str(evidence_id)
        for conflict in conflicts
        for evidence_id in conflict.get("evidence_ids", [])
        if evidence_id
    ))
    representative_evidence.extend(conflict_evidence)
    metrics.append(LearningMetricProposal(
        label="欲望变化或冲突观察",
        value=str(len(conflicts)),
        unit="个",
        method="由主角专项证据账本按章节顺序汇总；观察类型由模型结合原文判断。",
        evidence_ids=conflict_evidence[:16],
    ))
    conflict_summary = (
        "、".join(
            f"第 {int(item['chapter_ordinal'])} 章"
            for item in conflicts
        )
        if conflicts
        else "当前专项账本未发现"
    )

    answer.status = (
        "PARTIAL"
        if insufficient_field_summaries or not conflicts
        else "ANSWERED"
    )
    answer.conclusion = (
        (
            f"主角双层欲望与最小完整集已有 "
            f"{len(field_labels) - len(insufficient_field_summaries)}/"
            f"{len(field_labels)} 项按首次行动证据定位；"
            f"{len(insufficient_field_summaries)} 项证据不足。"
            if insufficient_field_summaries
            else (
                "主角双层欲望与最小完整集的早期基线"
                "均已按首次行动证据定位；这些不是中后期不变的人设常量。"
            )
        )
        + "；".join(field_summaries)
        + (
            f"。另有 {len(conflicts)} 个欲望变化或冲突观察，"
            f"位于{conflict_summary}。"
            if conflicts
            else (
                "。当前专项账本尚未记录欲望变化或冲突观察；"
                "这不是“人物没有弧光”的结论，需按放宽后的口径重新检查后文。"
            )
        )
    )
    answer.metrics = metrics
    answer.evidence_ids = list(dict.fromkeys(
        representative_evidence
    ))[:24]
    answer.counter_evidence_ids = []
    answer.limitations = [
        *insufficient_field_summaries,
        "本结论只覆盖当前单书的主角专项账本，不能外推为其他作品的通用章节阈值。",
    ]
    answer.reusable_lessons = [
        "前六项用于确定人物最早立住的基线，不代表中后期保持不变；后文应另按加深、转向、受压、重新解释和冲突持续记录变化。"
    ]
    answer.do_not_copy = [
        "不能照搬本书的欲望内容、人物选择或首次展示章节；其他作品必须按自己的原文证据重新定位。"
    ]



def _validate_2_2_answer_against_projection(
    answer: LearningAnswerProposal,
    projection: dict,
) -> None:
    ledger = projection.get("character_design_evidence") or {}
    allowed = _evidence_ids(ledger)
    _validate_answer_evidence_subset(
        answer,
        allowed,
        error_code="LEARNING_REPORT_2_2_EVIDENCE_SCOPE_INVALID",
    )
    items = _contract_item_by_id(answer)
    fields = {
        str(item.get("field") or ""): item
        for item in ledger.get("fields", [])
        if isinstance(item, dict)
    }
    for field_id in (
        "surface_desire",
        "deep_desire",
        "motivation",
        "contrast",
        "boundary",
        "core_ability",
    ):
        source = fields.get(field_id)
        item = items[field_id]
        if not isinstance(source, dict):
            raise ValueError("LEARNING_REPORT_2_2_SOURCE_FIELD_MISSING")
        source_status = str(source.get("status") or "")
        expected_evidence = _evidence_ids(source)
        actual_evidence = set(item.evidence_ids)
        if source_status == "INSUFFICIENT_EVIDENCE":
            explanation = _normalized_2_2_text(source.get("explanation"))
            if (
                source.get("first_display_chapter_ordinal") is not None
                or source.get("first_display_event_id") is not None
                or str(source.get("value") or "").strip()
                or str(source.get("display_event") or "").strip()
                or expected_evidence
                or not explanation
            ):
                raise ValueError(
                    f"LEARNING_REPORT_2_2_{field_id.upper()}_SOURCE_INVALID"
                )
            if (
                item.status != "INSUFFICIENT_EVIDENCE"
                or actual_evidence
                or item.metrics
                or item.classifications
                or item.payoff_classifications
                or _normalized_2_2_text(item.finding) != explanation
                or [_normalized_2_2_text(value) for value in item.limitations]
                != [explanation]
            ):
                raise ValueError(
                    f"LEARNING_REPORT_2_2_{field_id.upper()}_REFERENCE_INVALID"
                )
            continue
        expected_chapter = int(
            source.get("first_display_chapter_ordinal") or 0
        )
        finding = _normalized_2_2_text(item.finding)
        expected_value = _normalized_2_2_text(source.get("value"))
        expected_event = _normalized_2_2_text(source.get("display_event"))
        if (
            source_status != "SUPPORTED"
            or item.status != "SUPPORTED"
            or not actual_evidence
            or actual_evidence != expected_evidence
            or expected_chapter <= 0
            or re.search(
                rf"第\s*{re.escape(str(expected_chapter))}\s*章",
                item.finding,
            )
            is None
            or (expected_value and expected_value not in finding)
            or (expected_event and expected_event not in finding)
            or not _2_2_metric_matches_with_evidence(
                item,
                ("首次展示章节", "展示章节"),
                expected_chapter,
                expected_evidence,
            )
        ):
            raise ValueError(
                f"LEARNING_REPORT_2_2_{field_id.upper()}_REFERENCE_INVALID"
            )
    conflicts = [
        item
        for item in ledger.get("desire_conflicts", [])
        if isinstance(item, dict)
    ]
    conflicts = [
        item
        for _index, item in sorted(
            enumerate(conflicts),
            key=lambda pair: (
                int(pair[1].get("chapter_ordinal") or 0),
                pair[0],
            ),
        )
    ]
    conflict_evidence = _evidence_ids(conflicts)
    conflict_item = items["desire_conflicts"]
    observation_status = (
        "SUPPORTED" if conflicts else "INSUFFICIENT_EVIDENCE"
    )
    if (
        conflict_item.status != observation_status
        or not _metric_group_matches_count(
            conflict_item,
            ("冲突节点", "转折节点", "变化观察", "时间轴节点"),
            len(conflicts),
        )
    ):
        raise ValueError("LEARNING_REPORT_2_2_CONFLICT_COUNT_INVALID")
    if (
        set(conflict_item.evidence_ids) != conflict_evidence
        or not _2_2_metric_matches_with_evidence(
            conflict_item,
            ("冲突节点", "转折节点", "变化观察", "时间轴节点"),
            len(conflicts),
            conflict_evidence,
        )
        or not _2_2_ordered_nodes_match(
            conflict_item.finding,
            conflicts,
            detail_keys=(
                "surface_desire",
                "deep_desire",
                "motive",
                "choice",
                "result",
                "sacrifice",
            ),
        )
    ):
        raise ValueError("LEARNING_REPORT_2_2_CONFLICT_NODES_INVALID")
    arc_item = items["arc_timeline"]
    if (
        arc_item.status != observation_status
        or set(arc_item.evidence_ids) != conflict_evidence
        or not _2_2_metric_matches_with_evidence(
            arc_item,
            ("冲突节点", "转折节点", "变化观察", "时间轴节点"),
            len(conflicts),
            conflict_evidence,
        )
        or not _2_2_ordered_nodes_match(
            arc_item.finding,
            conflicts,
            detail_keys=("arc_change",),
        )
    ):
        raise ValueError("LEARNING_REPORT_2_2_ARC_TIMELINE_INVALID")




class Q2_2Plugin(BaseQuestionPlugin):
    question_id = "2.2"

    def assess_readiness(self, projection: dict[str, Any]) -> dict[str, object]:
        character_design = projection.get("character_design_evidence") or {}
        character_design_by_field = {
            item.get("field"): item
            for item in character_design.get("fields", [])
            if isinstance(item, dict) and item.get("field")
        }
        required_character_fields = {
            "surface_desire": "表层欲望",
            "deep_desire": "深层欲望",
            "motivation": "动机",
            "contrast": "性格反差",
            "boundary": "行为底线",
            "core_ability": "核心能力",
        }
        character_gaps: list[str] = []
        character_limitations: list[str] = []
        insufficient_character_fields: list[str] = []
        protagonist = projection.get("protagonist")
        if protagonist is None:
            character_gaps.append("尚未确认主角。")
        if character_design and character_design.get("is_current") is False:
            character_gaps.append("主角证据表基于旧人物或旧拆解结果，需要重新生成。")
        coverage = character_design.get("coverage", {}) if isinstance(character_design, dict) else {}
        if character_design and coverage.get("event_coverage_complete") is not True:
            character_gaps.append("主角证据表没有覆盖当前运行的全部主角事件。")
        for key, label in required_character_fields.items():
            item = character_design_by_field.get(key)
            if not isinstance(item, dict):
                character_gaps.append(f"缺少{label}的首次展示事件与原文。")
                continue
            status = item.get("status", "SUPPORTED")
            if status == "SUPPORTED":
                if (
                    not str(item.get("value") or "").strip()
                    or not item.get("first_display_chapter_ordinal")
                    or not item.get("first_display_event_id")
                    or not str(item.get("display_event") or "").strip()
                    or not item.get("evidence_ids")
                ):
                    character_gaps.append(f"缺少{label}的首次展示事件与原文。")
            elif status == "INSUFFICIENT_EVIDENCE":
                if (
                    str(item.get("value") or "").strip()
                    or item.get("first_display_chapter_ordinal") is not None
                    or str(item.get("first_display_event_id") or "").strip()
                    or str(item.get("display_event") or "").strip()
                    or item.get("evidence_ids")
                    or not str(item.get("explanation") or "").strip()
                ):
                    character_gaps.append(f"{label}的证据不足记录格式无效，需要重新生成。")
                else:
                    insufficient_character_fields.append(label)
                    character_limitations.append(
                        f"当前全书主角事件账本未找到可核验的{label}首次展示事件与原文。"
                    )
            else:
                character_gaps.append(f"{label}使用了未知证据状态，需要重新生成。")
        if character_design and not str(character_design.get("arc_summary") or "").strip():
            character_gaps.append("主角证据表缺少全书人物弧光总结。")

        ready = not character_gaps
        return {
            "question_id": self.question_id,
            "ready": ready,
            "gaps": character_gaps or character_limitations or ["单书已就绪，跨书对比需多书数据。"],
            "required_evidences": ["character_design_evidence"],
            "answer_scope": (
                "PARTIAL"
                if ready and insufficient_character_fields
                else ("ANSWERED" if ready else "NOT_READY")
            ),
        }

    def validate_answer(
        self,
        answer: LearningAnswerProposal,
        projection: dict[str, Any],
        errors: list[dict[str, Any]],
    ) -> None:
        _validate_2_2_answer_against_projection(answer, projection)

    def apply_program_answer(
        self,
        report: Any,
        projection: dict[str, Any],
    ) -> LearningAnswerProposal | None:
        character_design = projection.get("character_design_evidence")
        if not isinstance(character_design, dict):
            return None
        ans = next((a for a in report.answers if a.question_id == "2.2"), None)
        if ans:
            _apply_program_2_2_answer(ans, character_design)
        return ans
