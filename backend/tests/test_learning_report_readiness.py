from __future__ import annotations

import pytest

from app.services.learning_report import (
    LEARNING_QUESTION_CATALOG,
    LEARNING_QUESTION_CONTRACTS,
    LearningReportValidationError,
    assess_learning_report_readiness,
    parse_learning_report,
)


def _projection(*, complete_specialized_ledgers: bool) -> dict:
    projection = {
        "chapters": [
            {"ordinal": ordinal, "title": f"第 {ordinal} 章"}
            for ordinal in range(1, 61)
        ],
        "story_overview": {
            "premise": "林舟因一封密信开始追查旧宅秘密。",
            "protagonist": "林舟",
            "evidence_ids": ["evd_overview"],
        },
        "characters": [{
            "name": "林舟",
            "role": "PROTAGONIST",
            "goals": ["查明寄信人"],
            "motivations": ["保护家人"],
            "evidence_ids": ["evd_character"],
        }],
        "events": [{
            "id": "evt_opening",
            "people": ["林舟"],
            "chapter_ordinals": [1],
            "evidence_ids": ["evd_event"],
        }],
        "phases": [{"id": "phase_1"}],
        "deep_analysis": {
            "scene_analysis": [{"chapter_ordinal": 1}],
            "foreshadowing": [{"title": "密信来源"}],
        },
    }
    if complete_specialized_ledgers:
        projection.update({
            "character_design_evidence": {
                "is_current": True,
                "fields": [
                    {
                        "field": key,
                        "status": "SUPPORTED",
                        "value": key,
                        "first_display_chapter_ordinal": 1,
                        "first_display_event_id": "evt_opening",
                        "display_event": "林舟在密信出现后决定追查。",
                        "evidence_ids": [f"evd_{key}"],
                        "explanation": "主角通过行动展示该要素。",
                    }
                    for key in (
                        "surface_desire",
                        "deep_desire",
                        "motivation",
                        "contrast",
                        "boundary",
                        "core_ability",
                    )
                ],
                "desire_conflicts": [],
                "arc_summary": "林舟由被动发现线索转为主动追查。",
                "coverage": {"event_coverage_complete": True},
            },
            "chapter_end_hooks": [
                {
                    "chapter_ordinal": ordinal,
                    "hook_type": "NONE",
                    "strength": "NONE",
                    "response_status": "NOT_APPLICABLE",
                    "ending_evidence_ids": [f"evd_end_{ordinal}"],
                }
                for ordinal in range(1, 61)
            ],
            "chapter_end_hooks_evidence": {
                "is_current": True,
                "coverage": {
                    "sample_policy": "ALL_CHAPTERS_WINDOWED",
                    "ending_evidence_complete": True,
                    "sequence_metrics_exact": True,
                    "window_count": 2,
                    "response_tracking_scope": "OUT_OF_SCOPE_FOR_4.9",
                },
                "summary": {"type_transition_count": 0},
            },
            "foreshadowing_ledger": {
                "covered_chapter_count": 60,
                "lifecycles": [],
            },
            "sample_metadata": {
                "platform": "测试平台",
                "commercial_model": "付费阅读",
            },
        })
    return projection


def test_every_core_question_has_full_fable_contract() -> None:
    assert len(LEARNING_QUESTION_CATALOG) == 42
    assert set(LEARNING_QUESTION_CONTRACTS) == {
        item.question_id for item in LEARNING_QUESTION_CATALOG
    }
    for item in LEARNING_QUESTION_CATALOG:
        assert item.output_contract
        assert item.measurement_requirements
        assert item.evidence_requirements
        assert item.scope_requirement
        assert item.external_data_policy


def test_sparse_deep_analysis_only_unlocks_independently_supported_questions() -> None:
    readiness = assess_learning_report_readiness(
        _projection(complete_specialized_ledgers=False)
    )

    assert readiness["ready"] is True
    assert readiness["ready_question_count"] == 2
    assert readiness["partial_question_count"] == 2
    assert readiness["complete_question_count"] == 0
    assert readiness["total_question_count"] == 42
    assert readiness["generation_ready_question_ids"] == ["1.4", "2.1"]
    checks = {item["question_id"]: item for item in readiness["checks"]}
    assert checks["1.4"]["ready"] is True
    assert checks["2.1"]["ready"] is True
    assert checks["2.2"]["ready"] is False
    assert checks["4.9"]["observed"]["generic_scene_analysis_count"] == 1
    assert checks["4.9"]["observed"]["valid_chapter_end_sample_count"] == 0
    assert checks["3.4"]["ready"] is False
    assert readiness["next_required_artifacts"][0] == "主角双层欲望与最小完整集证据表"


def test_specialized_ledgers_keep_3_4_blocked_until_its_own_payoff_tracking() -> None:
    readiness = assess_learning_report_readiness(
        _projection(complete_specialized_ledgers=True)
    )

    assert readiness["ready"] is True
    assert readiness["ready_question_count"] == 4
    assert readiness["complete_question_count"] == 2
    assert readiness["partial_question_count"] == 2
    assert readiness["generation_ready_question_ids"] == [
        "1.4",
        "2.1",
        "2.2",
        "4.9",
    ]
    assert readiness["next_required_artifacts"] == [
        "前三章章末钩兑现追踪表（独立于 4.9）"
    ]


def _answer(question_id: str) -> dict:
    answer = {
        "question_id": question_id,
        "status": "PARTIAL",
        "conclusion": "当前材料支持部分回答。",
        "metrics": [],
        "evidence_ids": [],
        "counter_evidence_ids": [],
        "limitations": ["仍有明确缺口。"],
        "reusable_lessons": [],
        "do_not_copy": ["不能照搬原作设定。"],
    }
    if question_id == "2.1":
        item_ids = (
            "opening_character_counts",
            "character_appearance_sequence",
            "first_scene_functions",
            "later_role_volume",
            "new_character_intervals",
            "first_function_distribution",
            "identity_duplicate_risks",
            "cross_book_comparison",
        )
        answer["contract_items"] = [
            {
                "item_id": item_id,
                "status": (
                    "INSUFFICIENT_EVIDENCE"
                    if item_id == "cross_book_comparison"
                    else "SUPPORTED"
                ),
                "finding": (
                    "当前没有同口径跨书数据。"
                    if item_id == "cross_book_comparison"
                    else "当前程序原料支持这一项。"
                ),
                "metrics": [],
                "evidence_ids": [],
                "limitations": [],
                "classifications": (
                    [{
                        "subject": "林舟",
                        "category": "主角锚点",
                        "first_action_chapter": 1,
                        "first_action_event_id": "evt_opening",
                        "explanation": "以主角行动启动开篇任务。",
                        "evidence_ids": ["evd_event"],
                    }]
                    if item_id == "first_scene_functions"
                    else []
                ),
            }
            for item_id in item_ids
        ]
    return answer


def test_parser_accepts_exact_incremental_question_selection() -> None:
    output = parse_learning_report(
        {
            "answers": [_answer("1.4"), _answer("2.1")],
            "author_decisions": [],
            "method_candidates": [],
        },
        expected_question_ids=["1.4", "2.1"],
    )

    assert [item.question_id for item in output.answers] == ["1.4", "2.1"]


def test_parser_rejects_missing_incremental_question() -> None:
    with pytest.raises(LearningReportValidationError) as error:
        parse_learning_report(
            {
                "answers": [_answer("1.4")],
                "author_decisions": [],
                "method_candidates": [],
            },
            expected_question_ids=["1.4", "2.1"],
        )

    assert error.value.code == "LEARNING_REPORT_QUESTION_COVERAGE_INVALID"


def test_parser_rejects_2_1_that_only_returns_counts() -> None:
    counts_only = _answer("2.1")
    counts_only["contract_items"] = counts_only["contract_items"][:1]

    with pytest.raises(LearningReportValidationError) as error:
        parse_learning_report(
            {
                "answers": [counts_only],
                "author_decisions": [],
                "method_candidates": [],
            },
            expected_question_ids=["2.1"],
        )

    assert error.value.code == "LEARNING_REPORT_CONTRACT_ITEM_COVERAGE_INVALID"


def test_parser_rejects_2_1_without_per_character_first_functions() -> None:
    answer = _answer("2.1")
    first_functions = next(
        item
        for item in answer["contract_items"]
        if item["item_id"] == "first_scene_functions"
    )
    first_functions["classifications"] = []

    with pytest.raises(LearningReportValidationError) as error:
        parse_learning_report(
            {
                "answers": [answer],
                "author_decisions": [],
                "method_candidates": [],
            },
            expected_question_ids=["2.1"],
        )

    assert error.value.code == "LEARNING_REPORT_2_1_FIRST_FUNCTIONS_MISSING"
