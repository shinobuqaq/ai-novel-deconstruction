from __future__ import annotations

import re
from typing import Any
from ..base import BaseQuestionPlugin
from ..schemas import (
    LearningAnswerProposal,
    LearningContractItemProposal,
    LearningMetricProposal,
)
from .q3_1 import _opening_structure_evidence_ids

def _program_3_2_contract_items(
    opening_structure: dict,
) -> list[LearningContractItemProposal]:
    chapter_tasks = opening_structure.get("chapter_tasks", [])
    segments = opening_structure.get("paragraph_segments", [])
    timeline = opening_structure.get("information_timeline", [])
    coverage = opening_structure.get("coverage", {})
    representative_evidence = _opening_structure_evidence_ids(
        opening_structure,
        limit=32,
    )
    chapter_finding = "；".join(
        f"第 {item.get('chapter_ordinal')} 章："
        f"{'、'.join(str(task) for task in item.get('tasks', []))}"
        for item in chapter_tasks
    )
    key_positions = [
        position
        for item in chapter_tasks
        for position in item.get("key_event_positions", [])
    ]
    key_finding = "；".join(
        f"第 {item.get('chapter_ordinal')} 章第 "
        f"{position.get('paragraph')} 段（累计字符 "
        f"{position.get('source_char_start')}）"
        for item in chapter_tasks
        for position in item.get("key_event_positions", [])
    )
    timeline_finding = "；".join(
        (
            (
                f"核心能力相关信号首次见于第 "
                f"{item.get('first_chapter_ordinal')} 章第 "
                f"{item.get('first_paragraph')} 段，相关段落共承载 "
                f"{item.get('character_count')} 字符"
                if item.get("module") == "核心能力"
                else
                f"{item.get('module')}首次见于第 "
                f"{item.get('first_chapter_ordinal')} 章第 "
                f"{item.get('first_paragraph')} 段，相关段落共承载 "
                f"{item.get('character_count')} 字符"
            )
            if item.get("status") == "SUPPORTED"
            else f"{item.get('module')}：前三章当前未观察到足够依据"
        )
        for item in timeline
    )
    core = next(
        (
            item
            for item in timeline
            if item.get("module") == "核心能力"
        ),
        {},
    )
    core_supported = core.get("status") == "SUPPORTED"
    core_segments = [
        item
        for item in segments
        if (
            isinstance(item, dict)
            and "核心能力" in item.get("information_modules", [])
        )
    ]
    core_segment_fragments = [
        (
            f"第 {item.get('chapter_ordinal')} 章第 "
            f"{item.get('paragraph_start')}—{item.get('paragraph_end')} 段"
            f"“{item.get('function')}”"
        )
        for item in core_segments
    ]
    core_finding = (
        (
            f"专项账本按宽口径记录能力信息：最早信号位于第 "
            f"{core.get('first_chapter_ordinal')} 章第 "
            f"{core.get('first_paragraph')} 段、累计字符 "
            f"{core.get('source_char_start')}。"
            "这里定位的是能力相关信息首次装载，不等同于后续能力都已在该段现场展示。"
            + (
                f"账本归入同一模块的功能段依次为："
                f"{'；'.join(core_segment_fragments)}。"
                if core_segment_fragments
                else ""
            )
        )
        if core_supported
        else "前三章逐段账本当前未观察到足够依据，不能硬判核心能力已经亮相。"
    )
    core_evidence_ids = list(dict.fromkeys(
        str(evidence_id)
        for item in core_segments
        for evidence_id in item.get("evidence_ids", [])[:1]
        if evidence_id
    ))[:16]
    if core_supported:
        core_evidence_ids = list(dict.fromkeys([
            *(
                str(item)
                for item in core.get("evidence_ids", [])
                if item
            ),
            *core_evidence_ids,
        ]))
    return [
        LearningContractItemProposal(
            item_id="chapter_tasks",
            status="SUPPORTED",
            finding=chapter_finding[:800],
            metrics=[
                LearningMetricProposal(
                    label="逐章任务覆盖",
                    value=str(len(chapter_tasks)),
                    unit="章",
                    method="程序要求任务账本严格覆盖第 1 至 3 章。",
                    evidence_ids=representative_evidence[:16],
                ),
            ],
            evidence_ids=representative_evidence,
        ),
        LearningContractItemProposal(
            item_id="paragraph_task_sequence",
            status="SUPPORTED",
            finding=(
                f"前三章共形成 {len(segments)} 个连续段落功能段；"
                "所有输入段落均已覆盖，不重叠、不漏段。"
            ),
            metrics=[
                LearningMetricProposal(
                    label="连续段落功能段",
                    value=str(len(segments)),
                    unit="段组",
                    method="相邻且主要功能相同的原文段落由模型合并，程序校验连续覆盖。",
                    evidence_ids=representative_evidence[:16],
                ),
                LearningMetricProposal(
                    label="实际叙事字符",
                    value=str(
                        int(coverage.get("story_character_count") or 0)
                    ),
                    unit="字符",
                    method="只汇总账本标为实际叙事正文的连续段落范围。",
                    evidence_ids=representative_evidence[:16],
                ),
            ],
            evidence_ids=representative_evidence,
        ),
        LearningContractItemProposal(
            item_id="key_event_positions",
            status="SUPPORTED",
            finding=key_finding[:800],
            metrics=[
                LearningMetricProposal(
                    label="关键事件定位数",
                    value=str(len(key_positions)),
                    unit="处",
                    method="由模型选择关键段落，程序映射到源文件累计字符。",
                    evidence_ids=representative_evidence[:16],
                ),
            ],
            evidence_ids=[
                str(position.get("evidence_id") or "")
                for position in key_positions
                if position.get("evidence_id")
            ][:32],
        ),
        LearningContractItemProposal(
            item_id="information_loading_timeline",
            status="SUPPORTED",
            finding=timeline_finding[:800],
            metrics=[
                LearningMetricProposal(
                    label="信息模块检查数",
                    value=str(len(timeline)),
                    unit="类",
                    method="六类观察模块逐项记录首次位置或未观察状态。",
                    evidence_ids=representative_evidence[:16],
                ),
            ],
            evidence_ids=list(dict.fromkeys(
                str(evidence_id)
                for item in timeline
                for evidence_id in item.get("evidence_ids", [])
                if evidence_id
            ))[:32],
        ),
        LearningContractItemProposal(
            item_id="core_ability_first_appearance",
            status=(
                "SUPPORTED"
                if core_supported
                else "INSUFFICIENT_EVIDENCE"
            ),
            finding=core_finding[:800],
            evidence_ids=core_evidence_ids,
        ),
        LearningContractItemProposal(
            item_id="cross_book_rhythm_range",
            status="INSUFFICIENT_EVIDENCE",
            finding="当前只有单书黄金三章账本，不能形成同类书标准节奏区间。",
            limitations=[
                "需要多本同品类作品按相同章节、段落和字符口径建立对照。"
            ],
        ),
    ]




def _apply_program_3_2_answer(
    answer: LearningAnswerProposal,
    opening_structure: dict,
) -> None:
    chapter_tasks = [
        item
        for item in opening_structure.get("chapter_tasks", [])
        if isinstance(item, dict)
    ]
    segments = [
        item
        for item in opening_structure.get("paragraph_segments", [])
        if isinstance(item, dict)
    ]
    coverage = opening_structure.get("coverage") or {}
    chapter_fragments = [
        f"第 {item.get('chapter_ordinal')} 章："
        f"{'、'.join(str(task) for task in item.get('tasks', []))}"
        for item in chapter_tasks
    ]
    answer.status = "PARTIAL"
    answer.conclusion = (
        f"前三章已按 {int(coverage.get('paragraph_count') or 0)} 个原文段落"
        f"连续覆盖，合并为 {len(segments)} 个功能段，实际叙事正文共 "
        f"{int(coverage.get('story_character_count') or 0)} 字符。"
        f"逐章任务为：{'；'.join(chapter_fragments)}。"
    )[:1800]
    answer.contract_items = _program_3_2_contract_items(
        opening_structure
    )
    answer.metrics = [
        metric
        for item in answer.contract_items
        for metric in item.metrics
    ][:20]
    answer.evidence_ids = _opening_structure_evidence_ids(
        opening_structure
    )
    answer.counter_evidence_ids = []
    answer.limitations = [
        "当前只有单书证据，不能形成同品类标准节奏区间。",
    ]
    answer.reusable_lessons = [
        "可按“逐章任务—连续段落功能—六类信息首次位置”三层检查前三章的信息装载顺序。"
    ]
    answer.do_not_copy = [
        "不能照搬本书的具体任务顺序、段落长度或信息揭示位置；其他作品应按自身内容重新逐段判断。"
    ]





class Q3_2Plugin(BaseQuestionPlugin):
    question_id = "3.2"

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
        ans = next((a for a in report.answers if a.question_id == "3.2"), None)
        if ans:
            _apply_program_3_2_answer(ans, opening_structure)
        return ans
