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
    _CHAPTER_END_HOOK_STRENGTH_LABELS,
    _CHAPTER_END_HOOK_TYPE_LABELS,
    _contract_item_by_id,
    _evidence_ids,
    _has_out_of_scope_4_9_response_distance,
    _metric_group_matches_count,
    _metric_group_matches_ratio,
    _validate_answer_evidence_subset,
)


def _program_ratio_text(ratio: float) -> str:
    value = f"{ratio * 100:.2f}".rstrip("0").rstrip(".")
    return f"{value}%"


def _program_hook_label(
    aliases_by_code: dict[str, tuple[str, ...]],
    code: str,
) -> str:
    aliases = aliases_by_code.get(code, (code,))
    return aliases[1] if len(aliases) > 1 else aliases[0]


def _program_4_9_matrix(ledger: dict) -> dict[str, object]:
    chapters = [
        item
        for item in ledger.get("chapters", [])
        if isinstance(item, dict)
    ]
    rows: list[dict[str, object]] = []
    for hook_type in _CHAPTER_END_HOOK_TYPE_LABELS:
        type_chapters = [
            item
            for item in chapters
            if str(item.get("hook_type") or "") == hook_type
        ]
        if not type_chapters:
            continue
        counts = {
            strength: sum(
                str(item.get("strength") or "") == strength
                for item in type_chapters
            )
            for strength in ("STRONG", "MEDIUM", "LIGHT", "NONE")
        }
        examples: list[dict[str, object]] = []
        for strength in ("STRONG", "MEDIUM", "LIGHT", "NONE"):
            example = next(
                (
                    item for item in type_chapters
                    if str(item.get("strength") or "") == strength
                ),
                None,
            )
            if example is None:
                continue
            examples.append({
                "chapter_ordinal": int(
                    example.get("chapter_ordinal") or 0
                ),
                "chapter_title": str(
                    example.get("chapter_title") or ""
                ),
                "strength": strength,
                "strength_label": _program_hook_label(
                    _CHAPTER_END_HOOK_STRENGTH_LABELS,
                    strength,
                ),
                "ending_evidence_ids": list(
                    example.get("ending_evidence_ids", [])
                ),
            })
        rows.append({
            "hook_type": hook_type,
            "type_label": _program_hook_label(
                _CHAPTER_END_HOOK_TYPE_LABELS,
                hook_type,
            ),
            "counts": counts,
            "examples": examples,
        })
    return {"rows": rows}


def _apply_program_4_9_answer(
    answer: LearningAnswerProposal,
    ledger: dict,
) -> None:
    chapters = [
        item
        for item in ledger.get("chapters", [])
        if isinstance(item, dict)
    ]
    coverage = ledger.get("coverage") or {}
    summary = ledger.get("summary") or {}
    hook_matrix = _program_4_9_matrix(ledger)
    all_evidence = list(dict.fromkeys(
        str(evidence_id)
        for chapter in chapters
        for evidence_id in chapter.get("ending_evidence_ids", [])
        if evidence_id
    ))
    source_chapter_count = int(coverage.get("source_chapter_count") or 0)
    window_count = int(coverage.get("window_count") or 0)
    coverage_metrics = [
        LearningMetricProposal(
            label="全书章节",
            value=str(source_chapter_count),
            unit="章",
            method="由全书连续章末钩账本确定性统计。",
            evidence_ids=all_evidence[:16],
        ),
        LearningMetricProposal(
            label="连续窗口",
            value=str(window_count),
            unit="个",
            method="由全书章末钩账本的窗口覆盖记录确定性统计。",
            evidence_ids=all_evidence[:16],
        ),
    ]
    metrics = list(coverage_metrics)

    type_fragments: list[str] = []
    type_metrics: list[LearningMetricProposal] = []
    for row in summary.get("type_distribution", []):
        if not isinstance(row, dict):
            continue
        hook_type = str(row.get("hook_type") or "")
        count = int(row.get("count") or 0)
        ratio = float(row.get("ratio") or 0)
        label = _program_hook_label(
            _CHAPTER_END_HOOK_TYPE_LABELS,
            hook_type,
        )
        evidence_ids = list(dict.fromkeys(
            str(evidence_id)
            for chapter in chapters
            if str(chapter.get("hook_type") or "") == hook_type
            for evidence_id in chapter.get("ending_evidence_ids", [])
            if evidence_id
        ))
        ratio_text = _program_ratio_text(ratio)
        type_fragments.append(f"{label} {count} 章（{ratio_text}）")
        metric = LearningMetricProposal(
            label=label,
            value=str(count),
            unit=f"章，占比 {ratio_text}",
            method="由逐章钩类型账本确定性汇总。",
            evidence_ids=evidence_ids[:16],
        )
        type_metrics.append(metric)
        metrics.append(metric)

    strength_fragments: list[str] = []
    strength_metrics: list[LearningMetricProposal] = []
    for row in summary.get("strength_distribution", []):
        if not isinstance(row, dict):
            continue
        strength = str(row.get("strength") or "")
        count = int(row.get("count") or 0)
        ratio = float(row.get("ratio") or 0)
        label = _program_hook_label(
            _CHAPTER_END_HOOK_STRENGTH_LABELS,
            strength,
        )
        evidence_ids = list(dict.fromkeys(
            str(evidence_id)
            for chapter in chapters
            if str(chapter.get("strength") or "") == strength
            for evidence_id in chapter.get("ending_evidence_ids", [])
            if evidence_id
        ))
        ratio_text = _program_ratio_text(ratio)
        strength_fragments.append(f"{label} {count} 章（{ratio_text}）")
        metric = LearningMetricProposal(
            label=label,
            value=str(count),
            unit=f"章，占比 {ratio_text}",
            method="由逐章钩强度账本确定性汇总。",
            evidence_ids=evidence_ids[:16],
        )
        strength_metrics.append(metric)
        metrics.append(metric)

    matrix_fragments = [
        (
            f"{row['type_label']}：强 {row['counts']['STRONG']}、"
            f"中 {row['counts']['MEDIUM']}、轻 {row['counts']['LIGHT']}、"
            f"无 {row['counts']['NONE']}"
        )
        for row in hook_matrix["rows"]
    ]
    max_consecutive_strong = int(
        summary.get("max_consecutive_strong") or 0
    )
    type_transition_count = int(
        summary.get("type_transition_count") or 0
    )
    max_consecutive_same_type = int(
        summary.get("max_consecutive_same_type") or 0
    )
    no_hook_count = int(summary.get("no_hook_count") or 0)
    no_hook_ratio = float(summary.get("no_hook_ratio") or 0)
    no_hook_evidence = list(dict.fromkeys(
        str(evidence_id)
        for chapter in chapters
        if str(chapter.get("hook_type") or "") == "NONE"
        for evidence_id in chapter.get("ending_evidence_ids", [])
        if evidence_id
    ))
    strong_run_metric = LearningMetricProposal(
        label="最长连续强钩",
        value=str(max_consecutive_strong),
        unit="章",
        method="由连续章节强度序列确定性合并。",
        evidence_ids=all_evidence[:16],
    )
    transition_metric = LearningMetricProposal(
        label="类型切换",
        value=str(type_transition_count),
        unit="次",
        method="由相邻章节钩类型序列确定性比较。",
        evidence_ids=all_evidence[:16],
    )
    same_type_metric = LearningMetricProposal(
        label="同类连续上限",
        value=str(max_consecutive_same_type),
        unit="章",
        method="由连续章节钩类型序列确定性合并。",
        evidence_ids=all_evidence[:16],
    )
    no_hook_metric = LearningMetricProposal(
        label="无钩章",
        value=str(no_hook_count),
        unit=f"章，占比 {_program_ratio_text(no_hook_ratio)}",
        method="由逐章 NONE 分类确定性统计。",
        evidence_ids=no_hook_evidence[:16],
    )
    strength_metrics.append(strong_run_metric)
    metrics.extend([
        strong_run_metric,
        transition_metric,
        same_type_metric,
        no_hook_metric,
    ])

    example_fragments: list[str] = []
    example_evidence: list[str] = []
    examples_by_type = summary.get("examples_by_type") or {}
    for row in summary.get("type_distribution", []):
        if not isinstance(row, dict) or int(row.get("count") or 0) <= 0:
            continue
        hook_type = str(row.get("hook_type") or "")
        examples = [
            item
            for item in examples_by_type.get(hook_type, [])
            if isinstance(item, dict)
        ]
        if not examples:
            raise ValueError("LEARNING_REPORT_4_9_TYPE_EXAMPLES_INCOMPLETE")
        example = examples[0]
        evidence_ids = [
            str(evidence_id)
            for evidence_id in example.get("ending_evidence_ids", [])
            if evidence_id
        ]
        if not evidence_ids:
            raise ValueError("LEARNING_REPORT_4_9_TYPE_EXAMPLES_INCOMPLETE")
        label = _program_hook_label(
            _CHAPTER_END_HOOK_TYPE_LABELS,
            hook_type,
        )
        example_fragments.append(
            f"{label}：第 {int(example.get('chapter_ordinal') or 0)} 章"
            f"“{str(example.get('chapter_title') or '')}”"
        )
        example_evidence.extend(evidence_ids)
    example_evidence = list(dict.fromkeys(example_evidence))

    answer.contract_items = [
        LearningContractItemProposal(
            item_id="chapter_coverage",
            status="SUPPORTED",
            finding=(
                f"全书 {source_chapter_count} 章由 {window_count} 个连续窗口"
                "不重不漏覆盖。"
            ),
            metrics=coverage_metrics,
            evidence_ids=all_evidence[:32],
            limitations=[],
        ),
        LearningContractItemProposal(
            item_id="type_distribution",
            status="SUPPORTED",
            finding=f"类型配比：{'；'.join(type_fragments)}。",
            metrics=type_metrics,
            evidence_ids=all_evidence[:32],
            limitations=[],
        ),
        LearningContractItemProposal(
            item_id="strength_rhythm",
            status="SUPPORTED",
            finding=(
                f"强弱配比：{'；'.join(strength_fragments)}；"
                f"最长连续强钩 {max_consecutive_strong} 章。"
                "强弱不是由类型数量相加得到，而是对每章同一钩子的"
                f"第二维判断；类型×强度交叉表为：{'；'.join(matrix_fragments)}。"
            ),
            metrics=strength_metrics,
            evidence_ids=all_evidence[:32],
            limitations=[],
        ),
        LearningContractItemProposal(
            item_id="type_rotation",
            status="SUPPORTED",
            finding=(
                f"相邻章节共切换类型 {type_transition_count} 次，"
                f"同类连续上限为 {max_consecutive_same_type} 章。"
            ),
            metrics=[transition_metric, same_type_metric],
            evidence_ids=all_evidence[:32],
            limitations=[],
        ),
        LearningContractItemProposal(
            item_id="no_hook_analysis",
            status="SUPPORTED",
            finding=(
                f"无钩章共 {no_hook_count} 章，占全书"
                f" {_program_ratio_text(no_hook_ratio)}。"
            ),
            metrics=[no_hook_metric],
            evidence_ids=no_hook_evidence[:32],
            limitations=[],
        ),
        LearningContractItemProposal(
            item_id="representative_examples",
            status="SUPPORTED",
            finding=(
                "每种实际出现类型均保留一条章末原文实例："
                + "；".join(example_fragments)
                + "。"
            ),
            metrics=[],
            evidence_ids=example_evidence[:32],
            limitations=[],
        ),
        LearningContractItemProposal(
            item_id="scope_boundary",
            status="SUPPORTED",
            finding=(
                "4.9 只统计全书章末钩类型与强弱节律，不追踪后续回应；"
                "前三章首次回应属于 3.4，重要悬念生命周期属于 4.10。"
            ),
            metrics=[],
            evidence_ids=[],
            limitations=[],
        ),
    ]

    answer.status = "ANSWERED"
    answer.conclusion = (
        f"全书 {source_chapter_count} 章由 {window_count} 个连续窗口"
        f"不重不漏覆盖。类型配比：{'；'.join(type_fragments)}。"
        f"强弱配比：{'；'.join(strength_fragments)}。"
        "类型回答“用什么方式留下问题”，强弱回答“这个问题有多具体、"
        "多紧迫、信息缺口多大”，两者独立判断，不做加法。"
        f"交叉结果：{'；'.join(matrix_fragments)}。"
        f"最长连续强钩 {max_consecutive_strong} 章，"
        f"类型切换 {type_transition_count} 次，"
        f"同类连续上限 {max_consecutive_same_type} 章，"
        f"无钩章 {no_hook_count} 章"
        f"（{_program_ratio_text(no_hook_ratio)}）。"
    )
    answer.metrics = metrics
    answer.evidence_ids = all_evidence[:24]
    answer.counter_evidence_ids = []
    answer.limitations = [
        "4.9 只统计全书章末钩类型与强弱节律，不追踪后续回应；前三章首次回应属于 3.4，重要悬念生命周期属于 4.10。"
    ]
    answer.reusable_lessons = [
        "先把章末类型与强度作为两个独立维度逐章记账，再看同一类型能否有不同强度，并核对类型切换、连续强钩、同类连续段和无钩章。"
    ]
    answer.do_not_copy = [
        "不能照搬本书的类型比例、强度序列或具体章末表达；其他作品必须按自己的全书章节重新统计。"
    ]


def _validate_4_9_answer_against_projection(
    answer: LearningAnswerProposal,
    projection: dict,
) -> None:
    ledger = projection.get("chapter_end_hooks_evidence") or {}
    chapters = [
        item
        for item in ledger.get("chapters", [])
        if isinstance(item, dict)
    ]
    ending_evidence = {
        str(evidence_id)
        for item in chapters
        for evidence_id in item.get("ending_evidence_ids", [])
        if evidence_id
    }
    _validate_answer_evidence_subset(
        answer,
        ending_evidence,
        error_code="LEARNING_REPORT_4_9_EVIDENCE_SCOPE_INVALID",
    )
    items = _contract_item_by_id(answer)
    summary = ledger.get("summary") or {}
    coverage = ledger.get("coverage") or {}
    chapter_coverage = items["chapter_coverage"]
    source_chapter_count = int(coverage.get("source_chapter_count") or 0)
    window_count = int(coverage.get("window_count") or 0)
    if (
        not _metric_group_matches_count(
            chapter_coverage,
            ("全书章节", "覆盖章节", "章节覆盖"),
            source_chapter_count,
        )
        or not _metric_group_matches_count(
            chapter_coverage,
            ("连续窗口", "窗口数"),
            window_count,
        )
    ):
        raise ValueError("LEARNING_REPORT_4_9_COVERAGE_METRIC_INVALID")

    type_distribution = items["type_distribution"]
    type_rows = [
        row
        for row in summary.get("type_distribution", [])
        if isinstance(row, dict)
    ]
    if not type_rows or any(
        not _metric_group_matches_count(
            type_distribution,
            _CHAPTER_END_HOOK_TYPE_LABELS.get(
                str(row.get("hook_type") or ""),
                (str(row.get("hook_type") or ""),),
            ),
            int(row.get("count") or 0),
        )
        or not _metric_group_matches_ratio(
            type_distribution,
            _CHAPTER_END_HOOK_TYPE_LABELS.get(
                str(row.get("hook_type") or ""),
                (str(row.get("hook_type") or ""),),
            ),
            float(row.get("ratio") or 0),
        )
        for row in type_rows
    ):
        raise ValueError("LEARNING_REPORT_4_9_TYPE_DISTRIBUTION_INVALID")

    strength_rhythm = items["strength_rhythm"]
    strength_rows = [
        row
        for row in summary.get("strength_distribution", [])
        if isinstance(row, dict)
    ]
    max_consecutive_strong = int(
        summary.get("max_consecutive_strong") or 0
    )
    if any(
        not _metric_group_matches_count(
            strength_rhythm,
            _CHAPTER_END_HOOK_STRENGTH_LABELS.get(
                str(row.get("strength") or ""),
                (str(row.get("strength") or ""),),
            ),
            int(row.get("count") or 0),
        )
        for row in strength_rows
    ) or not _metric_group_matches_count(
        strength_rhythm,
        ("最长连续强钩",),
        max_consecutive_strong,
    ):
        raise ValueError("LEARNING_REPORT_4_9_STRENGTH_RHYTHM_INVALID")

    type_rotation = items["type_rotation"]
    if not _metric_group_matches_count(
        type_rotation,
        ("类型切换", "切换次数"),
        int(summary.get("type_transition_count") or 0),
    ) or not _metric_group_matches_count(
        type_rotation,
        ("同类连续上限", "同类型连续上限"),
        int(summary.get("max_consecutive_same_type") or 0),
    ):
        raise ValueError("LEARNING_REPORT_4_9_TYPE_ROTATION_INVALID")

    no_hook = items["no_hook_analysis"]
    no_hook_ratio = float(summary.get("no_hook_ratio") or 0)
    if (
        not _metric_group_matches_count(
            no_hook,
            ("无钩章", "无钩"),
            int(summary.get("no_hook_count") or 0),
        )
        or not _metric_group_matches_ratio(
            no_hook,
            ("无钩章", "无钩"),
            no_hook_ratio,
        )
    ):
        raise ValueError("LEARNING_REPORT_4_9_NO_HOOK_METRIC_INVALID")

    examples = items["representative_examples"]
    example_evidence = _evidence_ids(examples.model_dump(mode="json"))
    examples_by_type = summary.get("examples_by_type") or {}
    if any(
        not example_evidence.intersection(_evidence_ids(type_examples))
        for type_examples in examples_by_type.values()
        if type_examples
    ):
        raise ValueError("LEARNING_REPORT_4_9_TYPE_EXAMPLES_INCOMPLETE")

    scope_finding = items["scope_boundary"].finding
    if (
        "3.4" not in scope_finding
        or "4.10" not in scope_finding
        or not re.search(r"(?:不|不得|不再).{0,8}(?:回应|回收|追踪)", scope_finding)
    ):
        raise ValueError("LEARNING_REPORT_4_9_SCOPE_BOUNDARY_INVALID")
    if _has_out_of_scope_4_9_response_distance(answer):
        raise ValueError("LEARNING_REPORT_4_9_RESPONSE_METRIC_OUT_OF_SCOPE")


class Q4_9Plugin(BaseQuestionPlugin):
    question_id = "4.9"

    def assess_readiness(self, projection: dict[str, Any]) -> dict[str, object]:
        chapter_count = len(projection.get("chapters", []))
        chapter_end_hooks_evidence = projection.get("chapter_end_hooks_evidence") or {}
        chapter_end_hooks = projection.get("chapter_end_hooks") or []
        valid_hook_samples = [
            item
            for item in chapter_end_hooks
            if item.get("ending_evidence_ids")
            and item.get("hook_type")
            and item.get("strength")
        ]
        hook_gaps: list[str] = []
        if chapter_end_hooks_evidence and chapter_end_hooks_evidence.get("is_current") is False:
            hook_gaps.append("章末钩账本对应旧版拆解或旧版正文，需要重新生成。")
        if len(valid_hook_samples) < chapter_count:
            hook_gaps.append(
                f"需要连续覆盖全书 {chapter_count} 章，当前只有 {len(valid_hook_samples)} 章有效分类。"
            )
        coverage = (
            chapter_end_hooks_evidence.get("coverage", {})
            if isinstance(chapter_end_hooks_evidence, dict)
            else {}
        )
        if chapter_end_hooks and coverage.get("ending_evidence_complete") is not True:
            hook_gaps.append("章末钩账本没有通过程序的逐章结尾证据覆盖检查。")
        if chapter_end_hooks and coverage.get("sample_policy") != "ALL_CHAPTERS_WINDOWED":
            hook_gaps.append("章末钩账本不是按连续窗口覆盖全书，不能精确计算相邻轮换与连续记录。")
        if chapter_end_hooks and coverage.get("sequence_metrics_exact") is not True:
            hook_gaps.append("全书相邻轮换与连续强钩指标尚未通过精确合并检查。")

        return {
            "question_id": self.question_id,
            "ready": not hook_gaps,
            "gaps": hook_gaps or ["单书已就绪，跨书对比需多书数据。"],
            "required_evidences": ["chapter_end_hooks_evidence"],
            "answer_scope": "ANSWERED" if not hook_gaps else "NOT_READY",
        }

    def validate_answer(
        self,
        answer: LearningAnswerProposal,
        projection: dict[str, Any],
        errors: list[dict[str, Any]],
    ) -> None:
        _validate_4_9_answer_against_projection(answer, projection)

    def apply_program_answer(
        self,
        report: Any,
        projection: dict[str, Any],
    ) -> LearningAnswerProposal | None:
        chapter_end_hooks = projection.get("chapter_end_hooks_evidence")
        if not isinstance(chapter_end_hooks, dict):
            return None
        ans = next((a for a in report.answers if a.question_id == "4.9"), None)
        if ans:
            _apply_program_4_9_answer(ans, chapter_end_hooks)
        return ans
