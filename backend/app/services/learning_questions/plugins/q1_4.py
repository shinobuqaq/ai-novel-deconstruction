from __future__ import annotations

import json
import re
from typing import Any

from app.models import EvidenceSpan
from ..base import BaseQuestionPlugin
from ..schemas import (
    LearningAnswerProposal,
    LearningContractClassificationProposal,
    LearningContractItemProposal,
    LearningMetricProposal,
    LearningPayoffClassificationProposal,
)
from ..common import (
    _contract_item_by_id,
    _evidence_ids,
    _metric_blob,
    _validate_answer_evidence_subset,
)

OPENING_PAYOFF_MAX_WINDOW_CANDIDATES = 200

_PAYOFF_EXCLUSION_LABELS = {
    "PROMISE_SOURCE": "前置承诺来源",
    "PROMISE_RESTATEMENT": "承诺重述",
    "IDENTITY_GRANT": "身份授予",
    "DREAM_OR_HISTORY": "梦境或历史讲述",
    "STATIC_EVIDENCE": "静态物证或演示",
    "SIMULATION": "模拟场面",
    "PARTIAL_ONLY": "只部分兑现",
    "UNRELATED": "与核心承诺无关",
}


def _opening_promise_metric_by_source(
    item: LearningContractItemProposal,
) -> dict[str, LearningMetricProposal]:
    aliases = {
        "title": {"书名"},
        "description": {"简介", "内容提要"},
        "premise": {"故事前提", "前提"},
        "opening_chapters": {"前三章", "前 3 章", "前3章"},
    }
    by_source: dict[str, LearningMetricProposal] = {}
    for metric in item.metrics:
        label = metric.label.strip()
        source = next(
            (
                source
                for source, allowed_labels in aliases.items()
                if label in allowed_labels
            ),
            None,
        )
        if source is None or source in by_source:
            raise ValueError(
                "LEARNING_REPORT_1_4_PROMISE_SOURCE_METRICS_INVALID"
            )
        by_source[source] = metric
    if set(by_source) != set(aliases) or len(item.metrics) != len(aliases):
        raise ValueError(
            "LEARNING_REPORT_1_4_PROMISE_SOURCE_METRICS_INVALID"
        )
    return by_source


def _program_1_4_promise_sources(
    item: LearningContractItemProposal,
    projection: dict,
    opening_promise_sources: dict[str, object],
    opening_promise_rows: list[dict[str, object]],
) -> dict[str, object]:
    if item.status != "SUPPORTED":
        raise ValueError("LEARNING_REPORT_1_4_PROMISE_SOURCE_INVALID")
    metrics = _opening_promise_metric_by_source(item)
    preferred_title = str(
        opening_promise_sources.get("preferred_title") or ""
    ).strip()
    if preferred_title and preferred_title not in metrics["title"].value:
        raise ValueError("LEARNING_REPORT_1_4_TITLE_SOURCE_INVALID")

    description_evidence = {
        str(evidence_id)
        for evidence_id in opening_promise_sources.get(
            "description_evidence_ids",
            [],
        )
        if evidence_id
    }
    description_metric_evidence = set(metrics["description"].evidence_ids)
    if opening_promise_sources.get("description_present"):
        if (
            not description_metric_evidence.intersection(
                description_evidence
            )
            or re.search(
                r"(?:缺失|未提供|无法确认|不能确认)",
                metrics["description"].value,
            )
        ):
            raise ValueError(
                "LEARNING_REPORT_1_4_DESCRIPTION_SOURCE_INVALID"
            )

    overview = projection.get("story_overview") or {}
    overview_premise = str(overview.get("premise") or "").strip()
    overview_evidence = _evidence_ids(overview)
    premise_evidence = set(metrics["premise"].evidence_ids)
    if (
        not overview_premise
        or not overview_evidence
        or not premise_evidence
        or not premise_evidence.intersection(overview_evidence)
    ):
        raise ValueError(
            "LEARNING_REPORT_1_4_PREMISE_SOURCE_INVALID"
        )

    opening_events = [
        event
        for event in projection.get("events", [])
        if isinstance(event, dict)
    ]
    first_three_events = [
        event
        for event in opening_events
        if any(
            1 <= int(chapter) <= 3
            for chapter in event.get("chapter_ordinals", [])
        )
    ]
    first_three_evidence = _evidence_ids(first_three_events)
    first_three_body_evidence = (
        first_three_evidence - description_evidence
    )
    opening_metric_evidence = set(
        metrics["opening_chapters"].evidence_ids
    )
    known_opening_metric_evidence = opening_metric_evidence.intersection(
        first_three_evidence
        | description_evidence
        | overview_evidence
    )
    authoritative_opening_evidence = {
        str(row.get("anchor_evidence_id") or "")
        for row in opening_promise_rows
        if row.get("anchor_evidence_id")
    }
    if (
        not opening_promise_rows
        or not authoritative_opening_evidence
        or not opening_metric_evidence
        or (
            known_opening_metric_evidence
            and (
                not known_opening_metric_evidence.issubset(
                    first_three_evidence
                )
                or not known_opening_metric_evidence.intersection(
                    first_three_body_evidence
                )
                or not known_opening_metric_evidence.intersection(
                    authoritative_opening_evidence
                )
            )
        )
    ):
        raise ValueError(
            "LEARNING_REPORT_1_4_OPENING_CHAPTER_SOURCE_INVALID"
        )

    description_value = str(
        opening_promise_sources.get("description_text")
        or metrics["description"].value
    ).strip()
    description_ids = (
        sorted(description_evidence)
        if opening_promise_sources.get("description_present")
        else list(metrics["description"].evidence_ids)
    )
    opening_value = "；".join(
        (
            f"第 {int(row['chapter_ordinal'])} 章"
            f"“{str(row['chapter_title'])}”："
            f"{str(row['event_title'])}"
        )
        for row in opening_promise_rows
    )
    opening_evidence_ids = list(dict.fromkeys(
        str(row["anchor_evidence_id"])
        for row in opening_promise_rows
    ))
    compiled_metrics = [
        LearningMetricProposal(
            label="书名",
            value=preferred_title or metrics["title"].value,
            unit="",
            method="程序核对源文件前置书名。",
            evidence_ids=[],
        ),
        LearningMetricProposal(
            label="简介",
            value=description_value,
            unit="",
            method="程序直接读取源文件前置简介原文。",
            evidence_ids=description_ids,
        ),
        LearningMetricProposal(
            label="故事前提",
            value=overview_premise,
            unit="",
            method="程序读取当前故事总览前提及其完整依据。",
            evidence_ids=sorted(overview_evidence)[:16],
        ),
        LearningMetricProposal(
            label="前三章",
            value=opening_value,
            unit="",
            method="程序汇总前三章中命中 F1 的卖点候选及现场原文。",
            evidence_ids=opening_evidence_ids[:16],
        ),
    ]
    item.finding = "；".join(
        f"{metric.label}：{metric.value}"
        for metric in compiled_metrics
    )
    item.metrics = compiled_metrics
    item.evidence_ids = list(dict.fromkeys(
        evidence_id
        for metric in compiled_metrics
        for evidence_id in metric.evidence_ids
    ))[:32]
    item.limitations = []
    item.classifications = []
    item.payoff_classifications = []
    return {
        "title": {
            "value": compiled_metrics[0].value,
            "evidence_ids": [],
        },
        "description": {
            "value": compiled_metrics[1].value,
            "evidence_ids": compiled_metrics[1].evidence_ids,
        },
        "story_premise": {
            "value": compiled_metrics[2].value,
            "evidence_ids": compiled_metrics[2].evidence_ids,
        },
        "opening_chapters": {
            "value": compiled_metrics[3].value,
            "evidence_ids": compiled_metrics[3].evidence_ids,
            "allowed_first_three_evidence_count": len(
                first_three_body_evidence
            ),
        },
        "contract_validation": {
            "four_sources_compiled_by_program": True,
            "description_evidence_matched": bool(
                not opening_promise_sources.get("description_present")
                or description_metric_evidence.intersection(
                    description_evidence
                )
            ),
            "opening_chapters_use_non_description_evidence": True,
            "story_premise_compiled_from_overview": True,
            "opening_chapters_compiled_from_f1_candidates": True,
        },
    }


def _opening_payoff_candidate_artifact(
    projection: dict,
    evidence_by_id: dict[str, EvidenceSpan],
    chapter_by_unit_id: dict[str, dict[str, object]],
    opening_promise_sources: dict[str, object],
    *,
    candidate_start_sequence: int = 1,
    candidate_limit: int | None = OPENING_PAYOFF_MAX_WINDOW_CANDIDATES,
) -> dict[str, object]:
    description_evidence_ids = {
        str(evidence_id)
        for evidence_id in opening_promise_sources.get(
            "description_evidence_ids",
            [],
        )
        if evidence_id
    }
    sortable_events: list[tuple[int, int, str, dict, list[EvidenceSpan]]] = []
    for event in projection.get("events", []):
        if not isinstance(event, dict):
            continue
        chapters = [
            int(value)
            for value in event.get("chapter_ordinals", [])
            if int(value) > 0
        ]
        evidence = sorted(
            (
                evidence_by_id[str(evidence_id)]
                for evidence_id in event.get("evidence_ids", [])
                if str(evidence_id) in evidence_by_id
            ),
            key=lambda item: (item.start_char, item.end_char, item.id),
        )
        if not chapters or not evidence:
            continue
        sortable_events.append((
            min(chapters),
            min(item.start_char for item in evidence),
            str(event.get("id") or ""),
            event,
            evidence,
        ))
    sortable_events.sort(key=lambda item: item[:3])
    candidate_start_sequence = max(1, candidate_start_sequence)
    selection_start = candidate_start_sequence - 1
    selection_end = (
        None
        if candidate_limit is None
        else selection_start + max(0, candidate_limit)
    )
    selected_events = sortable_events[selection_start:selection_end]
    candidates: list[dict[str, object]] = []
    for sequence_no, (
        event_chapter,
        _event_start,
        event_id,
        event,
        evidence,
    ) in enumerate(
        selected_events,
        start=candidate_start_sequence,
    ):
        evidence_options: list[dict[str, object]] = []
        for evidence_no, item in enumerate(evidence[:16], start=1):
            chapter = chapter_by_unit_id.get(item.source_unit_id, {})
            evidence_options.append({
                "evidence_no": evidence_no,
                "evidence_id": item.id,
                "chapter_ordinal": int(
                    chapter.get("ordinal") or event_chapter
                ),
                "chapter_title": str(
                    chapter.get("title") or item.source_unit.title
                ),
                "paragraph_number": item.paragraph_index + 1,
                "source_char_start": item.start_char + 1,
                "text": item.text_snapshot[:360],
            })
        event_evidence_ids = {
            str(item.id) for item in evidence
        }
        candidates.append({
            "sequence_no": sequence_no,
            "event_id": event_id,
            "event_title": str(event.get("title") or "未命名事件"),
            "event_summary": str(
                event.get("summary")
                or event.get("process")
                or event.get("outcome")
                or ""
            )[:600],
            "narrative_mode": str(
                event.get("narrative_mode") or "UNKNOWN"
            ),
            "program_exclusion_code": (
                "PROMISE_SOURCE"
                if (
                    event_evidence_ids
                    and event_evidence_ids.issubset(
                        description_evidence_ids
                    )
                )
                else ""
            ),
            "evidence_options": evidence_options,
        })
    return {
        "facet_definitions": [
            {
                "facet_id": "F1",
                "definition": (
                    "一句话卖点中的核心异常、机制或主要矛盾已经在正文中"
                    "作为正在发生的实体、力量或行动出现；简介、口述、"
                    "身份授予、梦境、历史讲述和静态物证不算。"
                ),
            },
            {
                "facet_id": "F2",
                "definition": (
                    "主角在现场直接遭遇该异常、被其作用，或已经被它"
                    "实际推入一句话卖点所说的主要矛盾。"
                ),
            },
            {
                "facet_id": "F3",
                "definition": (
                    "事件发生在正文现实行动层，不是前置简介、梦境、"
                    "历史转述、模拟演示或纯设定说明。"
                ),
            },
        ],
        "classification_policy": (
            "从 sequence_no=1 连续分类；F1/F2/F3 是观察面而非"
            "缺一不可的打分项。模型结合卖点承诺与上下文判断兑现，"
            "程序选择第一条并从所引原文编译位置数字。"
        ),
        "coverage": {
            "source_event_count": len(sortable_events),
            "candidate_count": len(candidates),
            "candidate_start_sequence": candidate_start_sequence,
            "candidate_end_sequence": (
                candidate_start_sequence + len(candidates) - 1
                if candidates
                else candidate_start_sequence - 1
            ),
            "candidate_limit": candidate_limit,
            "source_sequence_contiguous": True,
            "all_source_events_in_window": (
                selection_start == 0
                and len(candidates) == len(sortable_events)
            ),
        },
        "candidates": candidates,
    }




def _legacy_1_4_payoff_ledger(
    payoff: LearningContractItemProposal,
    projection: dict,
    evidence_by_id: dict[str, EvidenceSpan],
    chapter_by_unit_id: dict[str, dict[str, object]],
    opening_promise_sources: dict[str, object],
) -> dict[str, object]:
    artifact = _opening_payoff_candidate_artifact(
        projection,
        evidence_by_id,
        chapter_by_unit_id,
        opening_promise_sources,
    )
    candidates = [
        item
        for item in artifact.get("candidates", [])
        if isinstance(item, dict)
    ]
    candidate_by_sequence = {
        int(item["sequence_no"]): item for item in candidates
    }
    classifications = payoff.payoff_classifications
    sequence_numbers = [
        classification.sequence_no for classification in classifications
    ]
    if (
        not classifications
        or len(sequence_numbers) != len(set(sequence_numbers))
        or sequence_numbers != list(range(1, len(sequence_numbers) + 1))
        or any(
            sequence_no not in candidate_by_sequence
            for sequence_no in sequence_numbers
        )
    ):
        raise ValueError(
            "LEARNING_REPORT_1_4_PAYOFF_CLASSIFICATION_COVERAGE_INVALID"
        )
    rows: list[dict[str, object]] = []
    selected: dict[str, object] | None = None
    for classification in classifications:
        candidate = candidate_by_sequence[classification.sequence_no]
        evidence_options = {
            int(item["evidence_no"]): item
            for item in candidate.get("evidence_options", [])
            if isinstance(item, dict)
        }
        anchor = evidence_options.get(classification.anchor_evidence_no)
        if anchor is None:
            raise ValueError(
                "LEARNING_REPORT_1_4_PAYOFF_EVIDENCE_REFERENCE_INVALID"
            )
        is_complete = (
            bool(classification.matched_facet_ids)
            and classification.exclusion_code == "NONE"
        )
        row = {
            **classification.model_dump(mode="json"),
            "event_id": candidate.get("event_id"),
            "event_title": candidate.get("event_title"),
            "event_summary": candidate.get("event_summary"),
            "anchor_evidence_id": anchor.get("evidence_id"),
            "chapter_ordinal": anchor.get("chapter_ordinal"),
            "chapter_title": anchor.get("chapter_title"),
            "paragraph_number": anchor.get("paragraph_number"),
            "source_char_start": anchor.get("source_char_start"),
            "program_complete_payoff": is_complete,
        }
        rows.append(row)
        if is_complete and selected is None:
            selected = row
    if selected is None:
        raise ValueError("LEARNING_REPORT_1_4_COMPLETE_PAYOFF_MISSING")
    return {
        "classifications": rows,
        "selected": selected,
        "coverage": {
            "source_event_count": len(candidates),
            "scanned_candidate_count": len(rows),
            "planned_window_count": 1,
            "completed_window_count": 1,
            "all_source_events_scanned": len(rows) == len(candidates),
            "stopped_after_first_complete": (
                len(rows) < len(candidates)
            ),
            "source_sequence_contiguous": True,
            "legacy_direct_validation_only": True,
        },
    }


def _program_1_4_answer(
    answer: LearningAnswerProposal,
    projection: dict,
    *,
    opening_promise_sources: dict[str, object] | None = None,
    evidence_by_id: dict[str, EvidenceSpan] | None = None,
    chapter_by_unit_id: dict[str, dict[str, object]] | None = None,
) -> dict[str, object]:
    opening_promise_sources = opening_promise_sources or {}
    evidence_by_id = evidence_by_id or {}
    chapter_by_unit_id = chapter_by_unit_id or {}
    overview = projection.get("story_overview") or {}
    allowed = _evidence_ids({
        "story_overview": overview,
        "opening_events": projection.get("events", []),
        "opening_promise_sources": opening_promise_sources,
    })
    items = _contract_item_by_id(answer)
    selling_point = items["selling_point_card"]
    promise_sources = items["opening_promise_sources"]
    promise_source_text = "；".join((
        promise_sources.finding,
        _metric_blob(promise_sources),
    ))
    preferred_title = str(
        opening_promise_sources.get("preferred_title") or ""
    ).strip()
    if (
        preferred_title
        and (
            preferred_title not in promise_source_text
            or re.search(
                r"(?:书名|标题).{0,12}"
                r"(?:缺失|未提供|无法确认|不能确认|不是|并非|不叫)",
                promise_source_text,
            )
        )
    ):
        raise ValueError("LEARNING_REPORT_1_4_TITLE_SOURCE_INVALID")
    description_evidence = {
        str(evidence_id)
        for evidence_id in opening_promise_sources.get(
            "description_evidence_ids",
            [],
        )
        if evidence_id
    }
    actual_promise_evidence = _evidence_ids(
        promise_sources.model_dump(mode="json")
    )
    if (
        opening_promise_sources.get("description_present")
        and (
            not actual_promise_evidence.intersection(description_evidence)
            or re.search(
                r"(?:简介|内容提要).{0,12}"
                r"(?:缺失|未提供|无法确认|不能确认)",
                promise_source_text,
            )
        )
    ):
        raise ValueError("LEARNING_REPORT_1_4_DESCRIPTION_SOURCE_INVALID")
    required_source_labels = (
        ("书名",),
        ("简介", "内容提要"),
        ("故事前提", "前提"),
        ("前三章", "前 3 章", "前3章"),
    )
    if any(
        not any(label in promise_source_text for label in aliases)
        for aliases in required_source_labels
    ):
        raise ValueError("LEARNING_REPORT_1_4_PROMISE_SOURCE_INCOMPLETE")

    payoff = items["first_payoff_location"]
    source_candidate_artifact = (
        projection.get("opening_payoff_candidates_evidence") or {}
    )
    if not source_candidate_artifact and payoff.payoff_classifications:
        source_candidate_artifact = _legacy_1_4_payoff_ledger(
            payoff,
            projection,
            evidence_by_id,
            chapter_by_unit_id,
            opening_promise_sources,
        )
    elif (
        not isinstance(source_candidate_artifact, dict)
        or source_candidate_artifact.get("is_current") is False
    ):
        raise ValueError("LEARNING_REPORT_1_4_PAYOFF_LEDGER_NOT_READY")
    candidate_artifact = json.loads(json.dumps(
        source_candidate_artifact,
        ensure_ascii=False,
        default=str,
    ))
    payoff.payoff_classifications = []
    classified_rows = [
        item
        for item in candidate_artifact.get("classifications", [])
        if isinstance(item, dict)
    ]
    coverage = candidate_artifact.get("coverage", {})
    if (
        not classified_rows
        or not isinstance(coverage, dict)
        or coverage.get("source_sequence_contiguous") is not True
    ):
        raise ValueError("LEARNING_REPORT_1_4_PAYOFF_LEDGER_INVALID")
    selected_candidate = candidate_artifact.get("selected")
    if selected_candidate is not None and not isinstance(
        selected_candidate,
        dict,
    ):
        raise ValueError("LEARNING_REPORT_1_4_PAYOFF_LEDGER_INVALID")

    opening_promise_rows = [
        row
        for row in classified_rows
        if (
            bool(row.get("matched_facet_ids"))
            and row.get("exclusion_code") != "UNRELATED"
            and 1 <= int(row.get("chapter_ordinal") or 0) <= 3
        )
    ]
    promise_source_artifact = _program_1_4_promise_sources(
        promise_sources,
        projection,
        opening_promise_sources,
        opening_promise_rows,
    )

    if selected_candidate is None:
        if coverage.get("all_source_events_scanned") is not True:
            raise ValueError(
                "LEARNING_REPORT_1_4_PAYOFF_LEDGER_INCOMPLETE"
            )
        scanned_count = int(
            coverage.get("scanned_candidate_count") or 0
        )
        payoff.status = "INSUFFICIENT_EVIDENCE"
        payoff.finding = (
            f"程序已按源文件顺序连续核对全书 {scanned_count} 个"
            "有效事件候选，当前模型判断中未发现完整兑现。"
        )
        payoff.metrics = []
        payoff.evidence_ids = []
        payoff.limitations = [
            "这是当前成书原文中的未发现结论，不等于卖点一定无效。"
        ]
        cross_book = items["cross_book_comparison"]
        cross_book.status = "INSUFFICIENT_EVIDENCE"
        cross_book.finding = (
            "当前缺少同品类、同商业模式作品的同口径数据，"
            "不能生成跨书卖点优劣结论。"
        )
        cross_book.metrics = []
        cross_book.evidence_ids = []
        cross_book.limitations = ["当前只能形成单书观察。"]
        scan_metric = LearningMetricProposal(
            label="连续核对候选数",
            value=str(scanned_count),
            unit="个事件",
            method="程序连续扫描到全书事件末尾。",
            evidence_ids=[],
        )
        answer.status = "PARTIAL"
        answer.conclusion = (
            f"{selling_point.finding} 当前全书连续候选中没有找到"
            "可由原文和上下文支持的完整兑现。"
        )
        answer.metrics = [scan_metric]
        answer.evidence_ids = list(dict.fromkeys([
            *selling_point.evidence_ids,
            *promise_sources.evidence_ids,
        ]))[:24]
        answer.limitations = [
            "全书连续候选未发现完整兑现，不能伪造首次兑现位置。",
            "缺少同品类、同商业模式作品的同口径数据，不能形成跨书比较。",
        ]
        answer.reusable_lessons = [
            "先区分承诺被提到、机制真实出现、主角直接卷入和现实行动，未同时满足时不要强报首次兑现。",
        ]
        answer.do_not_copy = [
            "不能照搬本书的专有设定、人物、事件或原文表达。",
            "不能把本书未发现完整兑现直接写成其他作品的通用规则。",
        ]
        candidate_artifact["contract_validation"] = {
            "classification_sequence_contiguous": True,
            "all_source_events_scanned": True,
            "complete_payoff_not_fabricated": True,
            "position_compiled_by_program": True,
        }
        candidate_artifact["promise_source_contract"] = (
            promise_source_artifact
        )
        _validate_answer_evidence_subset(
            answer,
            allowed,
            error_code="LEARNING_REPORT_1_4_EVIDENCE_SCOPE_INVALID",
        )
        return candidate_artifact

    selected_sequence = int(
        selected_candidate.get("sequence_no") or 0
    )
    description_start = int(
        opening_promise_sources.get("description_source_char_start") or 0
    )
    promise_chapter = int(
        opening_promise_sources.get("promise_chapter_position") or 0
    )
    chapter = int(selected_candidate.get("chapter_ordinal") or 0)
    paragraph_number = int(
        selected_candidate.get("paragraph_number") or 0
    )
    source_char_start = int(
        selected_candidate.get("source_char_start") or 0
    )
    chapter_title = str(
        selected_candidate.get("chapter_title") or ""
    )
    selected_evidence_id = str(
        selected_candidate.get("anchor_evidence_id") or ""
    )
    if description_start <= 0:
        promise_anchor_ids = {
            *selling_point.evidence_ids,
            *promise_sources.evidence_ids,
        }
        promise_anchor_options = [
            evidence_by_id[evidence_id]
            for evidence_id in promise_anchor_ids
            if evidence_id in evidence_by_id
        ]
        promise_anchor = min(
            promise_anchor_options,
            key=lambda item: item.start_char,
            default=None,
        )
        if promise_anchor is not None:
            description_start = promise_anchor.start_char + 1
            promise_chapter = int(
                chapter_by_unit_id.get(
                    promise_anchor.source_unit_id,
                    {},
                ).get("ordinal")
                or chapter
            )
        else:
            description_start = source_char_start
            promise_chapter = chapter
    if (
        chapter <= 0
        or paragraph_number <= 0
        or source_char_start <= 0
        or not chapter_title
        or not selected_evidence_id
        or description_start <= 0
    ):
        raise ValueError("LEARNING_REPORT_1_4_PAYOFF_POSITION_INVALID")

    metrics = [
        LearningMetricProposal(
            label="首次兑现章节",
            value=str(chapter),
            unit="章",
            method="程序按连续候选分类选择第一条完整兑现。",
            evidence_ids=[selected_evidence_id],
        ),
        LearningMetricProposal(
            label="兑现段落",
            value=str(paragraph_number),
            unit="段",
            method="程序读取选中原文依据的一基段落号。",
            evidence_ids=[selected_evidence_id],
        ),
        LearningMetricProposal(
            label="兑现位置累计字符",
            value=str(source_char_start),
            unit="字符",
            method="程序读取选中原文依据的一基源文件字符起点。",
            evidence_ids=[selected_evidence_id],
        ),
        LearningMetricProposal(
            label="承诺到兑现章距",
            value=str(chapter - promise_chapter),
            unit="章",
            method="首次兑现章节减去前置简介所在位置 0。",
            evidence_ids=[selected_evidence_id],
        ),
        LearningMetricProposal(
            label="承诺到兑现字符距离",
            value=str(source_char_start - description_start),
            unit="字符",
            method="兑现原文起点减去简介承诺起点。",
            evidence_ids=[
                *sorted(description_evidence)[:1],
                selected_evidence_id,
            ],
        ),
    ]
    earlier_rows = classified_rows[
        max(0, selected_sequence - 4):selected_sequence - 1
    ]
    earlier_summary = "；".join(
        (
            f"第{row['sequence_no']}项“{row['event_title']}”"
            f"为{_PAYOFF_EXCLUSION_LABELS.get(str(row['exclusion_code']), '仅部分命中')}"
        )
        for row in earlier_rows
    )
    payoff.finding = (
        f"程序按源文件顺序连续核对前 {selected_sequence} 个事件候选，"
        f"结合卖点承诺和上下文判断，第一条完整兑现是第 {chapter} 章"
        f"“{chapter_title}”第 {paragraph_number} 段、源文件第 "
        f"{source_char_start} 个字符处的“"
        f"{selected_candidate.get('event_title')}”。"
        + (
            f"紧邻的更早候选中，{earlier_summary}，因此不算首次完整兑现。"
            if earlier_summary
            else ""
        )
    )
    payoff.metrics = metrics
    payoff.evidence_ids = [selected_evidence_id]
    payoff.limitations = []
    payoff.status = "SUPPORTED"

    cross_book = items["cross_book_comparison"]
    cross_book.status = "INSUFFICIENT_EVIDENCE"
    cross_book.finding = (
        "当前缺少同品类、同商业模式作品的同口径数据，"
        "不能生成跨书卖点优劣结论。"
    )
    cross_book.metrics = []
    cross_book.evidence_ids = []
    cross_book.limitations = ["当前只能形成单书观察。"]

    answer.status = "PARTIAL"
    answer.conclusion = (
        f"{selling_point.finding} 程序按连续候选账本确定，首次完整兑现"
        f"位于第 {chapter} 章“{chapter_title}”的“"
        f"{selected_candidate.get('event_title')}”。"
    )
    answer.metrics = metrics
    answer.evidence_ids = list(dict.fromkeys([
        *selling_point.evidence_ids,
        *promise_sources.evidence_ids,
        selected_evidence_id,
    ]))[:24]
    answer.limitations = [
        "缺少同品类、同商业模式作品的同口径数据，不能形成跨书比较。",
        (
            f"程序已连续核对选中位置之前的 {selected_sequence - 1} "
            "个候选；后续更强场面不改变“首次”位置。"
        ),
    ]
    answer.reusable_lessons = [
        (
            "先明确一句话卖点的核心异常与主要矛盾，再按原文顺序寻找"
            "第一次同时出现真实机制、主角直接卷入和现实行动的场面；"
            "后续场面更强，不能反向改写首次兑现位置。"
        ),
    ]
    answer.do_not_copy = [
        "不能照搬本书的专有设定、人物、事件或原文表达。",
        (
            f"不能把本书第 {chapter} 章的兑现位置当成通用写作阈值；"
            "其他作品必须按自己的承诺与事件顺序重新核对。"
        ),
    ]

    candidate_artifact["classifications"] = classified_rows
    candidate_artifact["selected_sequence_no"] = selected_sequence
    candidate_artifact["selected_event_id"] = selected_candidate.get(
        "event_id"
    )
    candidate_artifact["selected_evidence_id"] = selected_evidence_id
    candidate_artifact["program_position"] = {
        "chapter_ordinal": chapter,
        "chapter_title": chapter_title,
        "paragraph_number": paragraph_number,
        "source_char_start": source_char_start,
        "promise_chapter_position": promise_chapter,
        "description_source_char_start": description_start,
        "chapter_distance": chapter - promise_chapter,
        "character_distance": source_char_start - description_start,
    }
    candidate_artifact["contract_validation"] = {
        "classification_sequence_contiguous": True,
        "first_complete_selected_by_program": True,
        "position_compiled_by_program": True,
    }
    candidate_artifact["promise_source_contract"] = (
        promise_source_artifact
    )
    _validate_answer_evidence_subset(
        answer,
        allowed,
        error_code="LEARNING_REPORT_1_4_EVIDENCE_SCOPE_INVALID",
    )
    return candidate_artifact


def _validate_1_4_answer_against_projection(
    answer: LearningAnswerProposal,
    projection: dict,
    *,
    opening_promise_sources: dict[str, object] | None = None,
    evidence_by_id: dict[str, EvidenceSpan] | None = None,
    chapter_by_unit_id: dict[str, dict[str, object]] | None = None,
) -> None:
    _program_1_4_answer(
        answer,
        projection,
        opening_promise_sources=opening_promise_sources,
        evidence_by_id=evidence_by_id,
        chapter_by_unit_id=chapter_by_unit_id,
    )




class Q1_4Plugin(BaseQuestionPlugin):
    question_id = "1.4"
    requires_projection = True

    def assess_readiness(self, projection: dict[str, Any]) -> dict[str, object]:
        chapters = projection.get("chapters", [])
        chapter_count = len(chapters)
        events = projection.get("events", [])
        overview = projection.get("story_overview") or {}
        opening_events = [
            event
            for event in events
            if any(
                0 < int(chapter) <= 3
                for chapter in event.get("chapter_ordinals", [])
            )
            and event.get("evidence_ids")
        ]
        selling_point_gaps: list[str] = []
        if not str(overview.get("premise") or "").strip():
            selling_point_gaps.append("缺少可核验的故事前提，不能稳定压缩一句话卖点。")
        if not overview.get("evidence_ids"):
            selling_point_gaps.append("故事前提没有原文依据。")
        if not opening_events:
            selling_point_gaps.append("前三章没有带原文依据的开篇事件，无法定位首次兑现。")
        opening_payoff_candidates = (
            projection.get("opening_payoff_candidates_evidence")
            or projection.get("opening_payoff_candidates")
            or {}
        )
        payoff_coverage = (
            opening_payoff_candidates.get("coverage", {})
            if isinstance(opening_payoff_candidates, dict)
            else {}
        )
        payoff_selected = (
            opening_payoff_candidates.get("selected")
            if isinstance(opening_payoff_candidates, dict)
            else None
        )
        payoff_projection_present = (
            "opening_payoff_candidates_status" in projection
            or "opening_payoff_candidates_evidence" in projection
        )
        if payoff_projection_present:
            if not opening_payoff_candidates:
                selling_point_gaps.append(
                    "卖点首次兑现连续候选账本尚未生成。"
                )
            elif opening_payoff_candidates.get("is_current") is False:
                selling_point_gaps.append(
                    "卖点首次兑现连续候选账本已经过期。"
                )
            elif not payoff_selected and not payoff_coverage.get(
                "all_source_events_scanned"
            ):
                selling_point_gaps.append(
                    "卖点首次兑现候选尚未连续扫描到首次完整兑现或全书末尾。"
                )

        ready = not selling_point_gaps
        return {
            "question_id": self.question_id,
            "ready": ready,
            "observed": {
                "chapter_count": chapter_count,
                "opening_event_count": len(opening_events),
                "overview_evidence_count": len(overview.get("evidence_ids", [])),
                "payoff_candidate_count": int(
                    payoff_coverage.get("source_event_count") or 0
                ),
                "payoff_scanned_candidate_count": int(
                    payoff_coverage.get("scanned_candidate_count") or 0
                ),
                "payoff_found": bool(payoff_selected),
            },
            "gaps": (
                selling_point_gaps
                or [
                    "当前只能形成单书部分回答；书名与简介须按源文件前置内容核对，"
                    "同品类同商业模式的多书对照仍缺失。"
                ]
            ),
            "required_artifact": "开篇承诺与首次兑现证据表",
            "answer_scope": "PARTIAL" if not selling_point_gaps else "NOT_READY",
            "source_material": {
                "story_overview": overview,
                "opening_events": opening_events,
                "opening_payoff_candidates": opening_payoff_candidates,
            },
        }

    def validate_answer(
        self,
        answer: LearningAnswerProposal,
        projection: dict[str, Any],
        errors: list[dict[str, Any]] | None = None,
        *,
        opening_promise_sources: dict[str, object] | None = None,
        evidence_by_id: dict[str, EvidenceSpan] | None = None,
        chapter_by_unit_id: dict[str, dict[str, object]] | None = None,
        **kwargs: Any,
    ) -> None:
        _validate_1_4_answer_against_projection(
            answer,
            projection,
            opening_promise_sources=opening_promise_sources or projection.get("opening_promise_sources"),
            evidence_by_id=evidence_by_id,
            chapter_by_unit_id=chapter_by_unit_id,
        )

    def apply_program_answer(
        self,
        report: Any,
        projection: dict[str, Any],
    ) -> LearningAnswerProposal | None:
        ans = next((a for a in report.answers if a.question_id == "1.4"), None)
        if ans:
            _program_1_4_answer(
                ans,
                projection,
                opening_promise_sources=projection.get("opening_promise_sources"),
            )
        return ans
