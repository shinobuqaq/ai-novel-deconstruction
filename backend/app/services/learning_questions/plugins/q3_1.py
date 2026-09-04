from __future__ import annotations

import re
from typing import Any
from ..base import BaseQuestionPlugin
from ..schemas import (
    LearningAnswerProposal,
    LearningContractItemProposal,
    LearningMetricProposal,
)

def _opening_structure_evidence_ids(
    opening_structure: dict,
    *,
    limit: int = 24,
) -> list[str]:
    values: list[str] = []
    opening = opening_structure.get("opening_scene", {})
    values.extend(opening.get("evidence_ids", []))
    for item in opening_structure.get("chapter_tasks", []):
        values.extend(item.get("evidence_ids", []))
    for item in opening_structure.get("information_timeline", []):
        values.extend(item.get("evidence_ids", []))
    for item in opening_structure.get("paragraph_segments", []):
        evidence_ids = item.get("evidence_ids", [])
        if evidence_ids:
            values.extend((evidence_ids[0], evidence_ids[-1]))
    return list(dict.fromkeys(
        str(value) for value in values if value
    ))[:limit]


def _program_3_1_evidence_ids(
    opening_structure: dict,
    *,
    limit: int = 16,
) -> list[str]:
    opening = opening_structure.get("opening_scene") or {}
    values = [
        *opening.get("evidence_ids", []),
        opening.get("story_start_evidence_id"),
        opening.get("protagonist_first_evidence_id"),
    ]
    first_chapter_task = next(
        (
            item
            for item in opening_structure.get("chapter_tasks", [])
            if (
                isinstance(item, dict)
                and int(item.get("chapter_ordinal") or 0) == 1
            )
        ),
        {},
    )
    values.extend(first_chapter_task.get("evidence_ids", []))
    return list(dict.fromkeys(
        str(value) for value in values if value
    ))[:limit]


def _program_3_1_contract_items(
    opening_structure: dict,
) -> list[LearningContractItemProposal]:
    opening = opening_structure.get("opening_scene", {})
    evidence_ids = _program_3_1_evidence_ids(opening_structure)
    opening_type = str(opening.get("opening_type") or "")
    entrance_paragraph = int(
        opening.get("protagonist_first_paragraph") or 0
    )
    entrance_char = int(
        opening.get("protagonist_first_source_char") or 0
    )
    protagonist_action = str(
        opening.get("protagonist_action") or ""
    ).rstrip("。；，,; ")
    initial_trouble = str(
        opening.get("initial_trouble") or ""
    ).rstrip("。；，,; ")
    first_sentence_function = str(
        opening.get("first_sentence_function") or ""
    ).rstrip("。；，,; ")
    first_paragraph_function = str(
        opening.get("first_paragraph_function") or ""
    ).rstrip("。；，,; ")
    first_functions = (
        f"专项账本的文学判断（不是读者效果实测）："
        f"正文第一句话主要用于：{first_sentence_function}；"
        f"第一段主要用于：{first_paragraph_function}。"
    )
    card = (
        f"开场类型为“{opening_type}”；主角在第 {entrance_paragraph} "
        f"段登场；开场行动：{protagonist_action}；"
        f"初始麻烦：{initial_trouble}。"
    )
    return [
        LearningContractItemProposal(
            item_id="opening_scene_type",
            status="SUPPORTED",
            finding=(
                f"首章开场主要属于“{opening_type}”。"
                f"{opening.get('explanation')}"
            )[:800],
            evidence_ids=evidence_ids,
        ),
        LearningContractItemProposal(
            item_id="protagonist_entrance",
            status="SUPPORTED",
            finding=(
                f"主角在首章第 {entrance_paragraph} 段、源文件累计字符 "
                f"{entrance_char} 处首次登场；"
                f"开场行动：{protagonist_action}；"
                f"初始麻烦：{initial_trouble}。"
            )[:800],
            metrics=[
                LearningMetricProposal(
                    label="主角首次出场段落",
                    value=str(entrance_paragraph),
                    unit="段",
                    method="由程序把模型选择的首章段落号映射回原文段落。",
                    evidence_ids=evidence_ids,
                ),
                LearningMetricProposal(
                    label="主角首次出场累计字符",
                    value=str(entrance_char),
                    unit="字符位置",
                    method="使用原文证据段的源文件起始字符位置。",
                    evidence_ids=evidence_ids,
                ),
            ],
            evidence_ids=evidence_ids,
        ),
        LearningContractItemProposal(
            item_id="first_sentence_and_paragraph",
            status="SUPPORTED",
            finding=first_functions[:800],
            evidence_ids=[
                str(value)
                for value in (opening.get("story_start_evidence_id"),)
                if value
            ],
        ),
        LearningContractItemProposal(
            item_id="opening_scene_card",
            status="SUPPORTED",
            finding=card[:800],
            metrics=[
                LearningMetricProposal(
                    label="正文起点段落",
                    value=str(
                        int(opening.get("story_start_paragraph") or 0)
                    ),
                    unit="段",
                    method="由前三章逐段账本区分前置信息与实际叙事正文。",
                    evidence_ids=[
                        str(
                            opening.get(
                                "story_start_evidence_id"
                            )
                            or ""
                        )
                    ],
                ),
            ],
            evidence_ids=evidence_ids,
        ),
        LearningContractItemProposal(
            item_id="cross_book_opening_distribution",
            status="INSUFFICIENT_EVIDENCE",
            finding="当前只有单书开场账本，不能形成同品类头部书开局类型分布。",
            limitations=[
                "需要多本同品类、同商业模式作品按相同段落口径建立开场卡。"
            ],
        ),
    ]




def _apply_program_3_1_answer(
    answer: LearningAnswerProposal,
    opening_structure: dict,
) -> None:
    opening = opening_structure.get("opening_scene", {})
    protagonist_action = str(
        opening.get("protagonist_action") or ""
    ).rstrip("。；，,; ")
    initial_trouble = str(
        opening.get("initial_trouble") or ""
    ).rstrip("。；，,; ")
    answer.status = "PARTIAL"
    answer.conclusion = (
        f"首章开场主要属于“{opening.get('opening_type')}”；"
        f"主角从第 {opening.get('protagonist_first_paragraph')} 段开始登场。"
        f"开场行动是：{protagonist_action}；"
        f"初始麻烦是：{initial_trouble}。"
    )[:1800]
    answer.contract_items = _program_3_1_contract_items(
        opening_structure
    )
    answer.metrics = [
        metric
        for item in answer.contract_items
        for metric in item.metrics
    ][:20]
    answer.evidence_ids = _program_3_1_evidence_ids(opening_structure)
    answer.counter_evidence_ids = []
    answer.limitations = [
        "当前只有单书证据，不能给出同品类头部书开局类型分布。",
    ]
    answer.reusable_lessons = [
        "可把开场类型、正文起点、主角首次行动、初始麻烦、第一句话与第一段功能放在同一张开场卡里核对。"
    ]
    answer.do_not_copy = [
        "不能照搬本书的具体人物、设定或开场事件；其他作品必须依据自己的首章原文重新判断。"
    ]





class Q3_1Plugin(BaseQuestionPlugin):
    question_id = "3.1"

    def assess_readiness(self, projection: dict[str, Any]) -> dict[str, object]:
        opening_structure = projection.get("opening_structure_evidence") or {}
        cov = opening_structure.get("coverage") or {}
        complete = bool(cov.get("event_coverage_complete"))
        gaps = []
        if not complete:
            gaps.append("前三章开篇结构账本未生成或覆盖不全。")
        return {
            "question_id": self.question_id,
            "ready": complete,
            "gaps": gaps or ["单书已就绪，跨书对比需多书数据。"],
            "required_evidences": ["opening_structure_evidence"],
            "answer_scope": "PARTIAL" if complete else "NOT_READY",
        }

    def apply_program_answer(
        self,
        report: Any,
        projection: dict[str, Any],
    ) -> LearningAnswerProposal | None:
        opening_structure = projection.get("opening_structure_evidence")
        if not isinstance(opening_structure, dict):
            return None
        ans = next((a for a in report.answers if a.question_id == "3.1"), None)
        if ans:
            _apply_program_3_1_answer(ans, opening_structure)
        return ans
