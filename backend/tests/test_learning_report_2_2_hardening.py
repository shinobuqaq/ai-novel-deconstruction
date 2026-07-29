from __future__ import annotations

import pytest

from app.services.learning_report import (
    LearningAnswerProposal,
    _apply_program_2_2_answer,
    _validate_2_2_answer_against_projection,
)


FIELD_SOURCES = (
    ("surface_desire", 1, "争取公开演讲机会", "主动报名校级演讲"),
    ("deep_desire", 2, "获得同伴真正认可", "拒绝冒领团队功劳"),
    ("motivation", 3, "守住共同完成的承诺", "返回现场帮助同伴"),
    ("contrast", 4, "平时退让但危机时果断", "独自承担现场处置"),
    ("boundary", 5, "不能牺牲无辜者换取胜利", "拒绝执行伤人方案"),
    ("core_ability", 6, "快速重建失效系统", "首次恢复核心控制台"),
)


def _conflict_finding(nodes: list[dict]) -> str:
    return "".join(
        (
            f"第 {node['chapter_ordinal']} 章，"
            f"表层欲望是{node['surface_desire']}；"
            f"深层欲望是{node['deep_desire']}；"
            f"现场动机原文是{node['motive']}；"
            f"主角选择{node['choice']}；"
            f"现场结果是{node['result']}；"
            f"付出的代价是{node['sacrifice']}。"
        )
        for node in nodes
    )


def _arc_finding(nodes: list[dict]) -> str:
    return "".join(
        f"第 {node['chapter_ordinal']} 章，{node['arc_change']}。"
        for node in nodes
    )


def _valid_answer_and_projection() -> tuple[LearningAnswerProposal, dict]:
    fields: list[dict] = []
    contract_items: list[dict] = []
    all_evidence: list[str] = []
    for field_id, chapter, value, display_event in FIELD_SOURCES:
        evidence_ids = [f"evd_{field_id}_a", f"evd_{field_id}_b"]
        all_evidence.extend(evidence_ids)
        fields.append({
            "field": field_id,
            "status": "SUPPORTED",
            "value": value,
            "first_display_chapter_ordinal": chapter,
            "display_event": display_event,
            "evidence_ids": evidence_ids,
        })
        contract_items.append({
            "item_id": field_id,
            "status": "SUPPORTED",
            "finding": (
                f"第 {chapter} 章，{display_event}，"
                f"通过行动立住“{value}”。"
            ),
            "metrics": [{
                "label": "首次展示章节",
                "value": str(chapter),
                "unit": "章",
                "method": "按主角专项证据账本定位。",
                "evidence_ids": evidence_ids,
            }],
            "evidence_ids": evidence_ids,
            "limitations": [],
            "classifications": [],
        })

    conflicts = [
        {
            "chapter_ordinal": 7,
            "surface_desire": "保住当前竞赛名次",
            "deep_desire": "公开承认同伴贡献",
            "motive": "不能再把同伴的贡献藏起来",
            "choice": "主动公布完整协作记录",
            "result": "完整协作记录被公开确认",
            "sacrificed_desire": "SURFACE",
            "sacrifice": "失去独占竞赛名次的机会",
            "arc_change": "从独自争胜转为承担团队责任",
            "motive_evidence_ids": ["evd_conflict_7_a"],
            "choice_evidence_ids": ["evd_conflict_7_a"],
            "result_evidence_ids": ["evd_conflict_7_b"],
            "sacrifice_evidence_ids": ["evd_conflict_7_b"],
            "evidence_ids": ["evd_conflict_7_a", "evd_conflict_7_b"],
        },
        {
            "chapter_ordinal": 10,
            "surface_desire": "接受现成晋升机会",
            "deep_desire": "兑现对受损同伴的承诺",
            "motive": "答应过的事必须做到",
            "choice": "放弃晋升并返回事故现场",
            "result": "主角返回现场完成补救",
            "sacrificed_desire": "SURFACE",
            "sacrifice": "放弃已经到手的晋升机会",
            "arc_change": "从被动补救转为主动守诺",
            "motive_evidence_ids": ["evd_conflict_10_a"],
            "choice_evidence_ids": ["evd_conflict_10_a"],
            "result_evidence_ids": ["evd_conflict_10_b"],
            "sacrifice_evidence_ids": ["evd_conflict_10_b"],
            "evidence_ids": ["evd_conflict_10_a", "evd_conflict_10_b"],
        },
    ]
    conflict_evidence = [
        evidence_id
        for node in conflicts
        for evidence_id in node["evidence_ids"]
    ]
    all_evidence.extend(conflict_evidence)
    contract_items.extend([
        {
            "item_id": "desire_conflicts",
            "status": "SUPPORTED",
            "finding": _conflict_finding(conflicts),
            "metrics": [{
                "label": "冲突节点",
                "value": str(len(conflicts)),
                "unit": "个",
                "method": "按专项账本逐节点统计。",
                "evidence_ids": conflict_evidence,
            }],
            "evidence_ids": conflict_evidence,
            "limitations": [],
            "classifications": [],
        },
        {
            "item_id": "arc_timeline",
            "status": "SUPPORTED",
            "finding": _arc_finding(conflicts),
            "metrics": [{
                "label": "转折节点",
                "value": str(len(conflicts)),
                "unit": "个",
                "method": "按章节顺序汇总专项账本。",
                "evidence_ids": conflict_evidence,
            }],
            "evidence_ids": conflict_evidence,
            "limitations": [],
            "classifications": [],
        },
    ])
    answer = LearningAnswerProposal.model_validate({
        "question_id": "2.2",
        "status": "ANSWERED",
        "conclusion": "六项人物要素和双层欲望冲突均有行动证据支持。",
        "metrics": [{
            "label": "已核验人物要素",
            "value": "6",
            "unit": "项",
            "method": "按主角专项证据账本统计。",
            "evidence_ids": all_evidence[:1],
        }],
        "evidence_ids": all_evidence,
        "counter_evidence_ids": [],
        "limitations": ["结论只覆盖当前单书和现有章节。"],
        "reusable_lessons": ["用具体选择而非人物标签展示欲望。"],
        "do_not_copy": ["不能照搬原作人物、事件或表达。"],
        "contract_items": contract_items,
    })
    return answer, {
        "character_design_evidence": {
            "fields": fields,
            "desire_conflicts": conflicts,
        },
    }


def _item(payload: dict, item_id: str) -> dict:
    return next(
        item
        for item in payload["contract_items"]
        if item["item_id"] == item_id
    )


def _validate_payload(payload: dict, projection: dict) -> None:
    _validate_2_2_answer_against_projection(
        LearningAnswerProposal.model_validate(payload),
        projection,
    )


def test_2_2_complete_ledger_passes_exact_validation() -> None:
    answer, projection = _valid_answer_and_projection()

    _validate_2_2_answer_against_projection(answer, projection)


def test_program_replaces_unverified_2_2_top_level_numbers() -> None:
    answer, projection = _valid_answer_and_projection()
    answer.conclusion = "错误地声称六项都在第 99 章立住。"
    answer.metrics[0].value = "99"

    _apply_program_2_2_answer(
        answer,
        projection["character_design_evidence"],
    )

    metric_values = {
        metric.label: metric.value
        for metric in answer.metrics
    }
    assert "99" not in answer.conclusion
    assert "表层欲望的早期基线在第 1 章立住" in answer.conclusion
    assert "不是中后期不变的人设常量" in answer.conclusion
    assert "核心能力的早期基线在第 6 章立住" in answer.conclusion
    assert metric_values["表层欲望首次展示章节"] == "1"
    assert metric_values["核心能力首次展示章节"] == "6"
    assert metric_values["欲望变化或冲突观察"] == "2"


def test_empty_observation_ledger_does_not_claim_there_is_no_arc() -> None:
    answer, projection = _valid_answer_and_projection()
    projection["character_design_evidence"]["desire_conflicts"] = []

    _apply_program_2_2_answer(
        answer,
        projection["character_design_evidence"],
    )

    assert answer.status == "PARTIAL"
    assert "尚未记录欲望变化或冲突观察" in answer.conclusion
    assert "不是“人物没有弧光”的结论" in answer.conclusion


def test_program_normalizes_duplicate_terminal_punctuation() -> None:
    answer, projection = _valid_answer_and_projection()
    ledger = projection["character_design_evidence"]
    ledger["fields"][0]["value"] += "。"
    ledger["fields"][0]["display_event"] += "。"
    ledger["desire_conflicts"][0]["choice"] += "。"
    ledger["desire_conflicts"][0]["arc_change"] += "。"

    _apply_program_2_2_answer(answer, ledger)

    user_text = "\n".join([
        answer.conclusion,
        *(
            item.finding
            for item in answer.contract_items
        ),
    ])
    assert "。；" not in user_text
    assert "。。" not in user_text
    assert "。：" not in user_text


@pytest.mark.parametrize("missing_part", ["value", "display_event"])
def test_2_2_rejects_field_finding_that_mismatches_source(
    missing_part: str,
) -> None:
    answer, projection = _valid_answer_and_projection()
    payload = answer.model_dump(mode="json")
    item = _item(payload, "surface_desire")
    source = projection["character_design_evidence"]["fields"][0]
    item["finding"] = item["finding"].replace(
        source[missing_part],
        "账本中不存在的内容",
    )

    with pytest.raises(
        ValueError,
        match="^LEARNING_REPORT_2_2_SURFACE_DESIRE_REFERENCE_INVALID$",
    ):
        _validate_payload(payload, projection)


def test_2_2_rejects_field_evidence_borrowed_from_another_field() -> None:
    answer, projection = _valid_answer_and_projection()
    payload = answer.model_dump(mode="json")
    item = _item(payload, "surface_desire")
    wrong_evidence = projection["character_design_evidence"]["fields"][1][
        "evidence_ids"
    ]
    item["evidence_ids"] = wrong_evidence
    item["metrics"][0]["evidence_ids"] = wrong_evidence

    with pytest.raises(
        ValueError,
        match="^LEARNING_REPORT_2_2_SURFACE_DESIRE_REFERENCE_INVALID$",
    ):
        _validate_payload(payload, projection)


def test_2_2_rejects_conflict_with_wrong_chapter() -> None:
    answer, projection = _valid_answer_and_projection()
    payload = answer.model_dump(mode="json")
    item = _item(payload, "desire_conflicts")
    item["finding"] = item["finding"].replace("第 7 章", "第 8 章", 1)

    with pytest.raises(
        ValueError,
        match="^LEARNING_REPORT_2_2_CONFLICT_NODES_INVALID$",
    ):
        _validate_payload(payload, projection)


def test_2_2_rejects_conflict_with_wrong_node_choice() -> None:
    answer, projection = _valid_answer_and_projection()
    payload = answer.model_dump(mode="json")
    item = _item(payload, "desire_conflicts")
    first_choice = projection["character_design_evidence"]["desire_conflicts"][0][
        "choice"
    ]
    item["finding"] = item["finding"].replace(first_choice, "接受了相反选择", 1)

    with pytest.raises(
        ValueError,
        match="^LEARNING_REPORT_2_2_CONFLICT_NODES_INVALID$",
    ):
        _validate_payload(payload, projection)


def test_2_2_rejects_conflict_nodes_in_wrong_order() -> None:
    answer, projection = _valid_answer_and_projection()
    payload = answer.model_dump(mode="json")
    item = _item(payload, "desire_conflicts")
    conflicts = projection["character_design_evidence"]["desire_conflicts"]
    item["finding"] = _conflict_finding(list(reversed(conflicts)))

    with pytest.raises(
        ValueError,
        match="^LEARNING_REPORT_2_2_CONFLICT_NODES_INVALID$",
    ):
        _validate_payload(payload, projection)


def test_2_2_rejects_conflict_with_missing_node_evidence() -> None:
    answer, projection = _valid_answer_and_projection()
    payload = answer.model_dump(mode="json")
    item = _item(payload, "desire_conflicts")
    item["evidence_ids"] = item["evidence_ids"][:-1]
    item["metrics"][0]["evidence_ids"] = item["metrics"][0]["evidence_ids"][:-1]

    with pytest.raises(
        ValueError,
        match="^LEARNING_REPORT_2_2_CONFLICT_NODES_INVALID$",
    ):
        _validate_payload(payload, projection)


@pytest.mark.parametrize("corruption", ["order", "arc_change"])
def test_2_2_rejects_inexact_arc_node(
    corruption: str,
) -> None:
    answer, projection = _valid_answer_and_projection()
    payload = answer.model_dump(mode="json")
    item = _item(payload, "arc_timeline")
    conflicts = projection["character_design_evidence"]["desire_conflicts"]
    if corruption == "order":
        item["finding"] = _arc_finding(list(reversed(conflicts)))
    else:
        item["finding"] = item["finding"].replace(
            conflicts[0]["arc_change"],
            "账本中不存在的弧光变化",
            1,
        )

    with pytest.raises(
        ValueError,
        match="^LEARNING_REPORT_2_2_ARC_TIMELINE_INVALID$",
    ):
        _validate_payload(payload, projection)
