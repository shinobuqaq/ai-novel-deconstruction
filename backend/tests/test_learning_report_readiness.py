from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.learning_report import (
    LEARNING_ANSWER_DEFAULT_SOFT_INPUT_CAP_TOKENS,
    LEARNING_LITERARY_JUDGMENT_POLICY,
    LEARNING_OBJECTIVE_VALIDATION_POLICY,
    LEARNING_QUESTION_CATALOG,
    LEARNING_QUESTION_CONTRACTS,
    LEARNING_QUESTION_ITEM_CONTRACTS,
    LEARNING_REPORT_PROGRAM_COMPILED_QUESTION_IDS,
    LEARNING_REPORT_PROJECTION_QUESTION_IDS,
    LearningAnswerProposal,
    LearningReportValidationError,
    LearningReportOutput,
    _answer_uses_current_contract,
    _apply_program_2_2_answer,
    _apply_program_3_1_answer,
    _apply_program_3_2_answer,
    _apply_program_4_9_answer,
    _compact_opening_structure_model_artifact,
    _request_budget,
    _serialize_learning_report_payload,
    _source_materials,
    _validate_answer_user_text_boundaries,
    _validate_selected_answers_against_projection,
    assess_learning_report_readiness,
    parse_learning_report,
)


def test_all_learning_answers_share_one_default_soft_input_cap() -> None:
    unknown_context = _request_budget(SimpleNamespace(
        max_output_tokens=16_000,
        context_window_tokens=None,
    ))
    large_context = _request_budget(SimpleNamespace(
        max_output_tokens=16_000,
        context_window_tokens=1_000_000,
    ))

    assert LEARNING_ANSWER_DEFAULT_SOFT_INPUT_CAP_TOKENS == 150_000
    assert unknown_context["input_token_budget"] == 150_000
    assert large_context["input_token_budget"] == 150_000
    assert "default_soft_input_cap_tokens" in unknown_context


def test_all_42_questions_inherit_objective_and_literary_validation_policy() -> None:
    assert len(LEARNING_QUESTION_CATALOG) == 42
    for question in LEARNING_QUESTION_CATALOG:
        assert (
            question.objective_validation_policy
            == LEARNING_OBJECTIVE_VALIDATION_POLICY
        )
        assert (
            question.literary_judgment_policy
            == LEARNING_LITERARY_JUDGMENT_POLICY
        )
        assert "不得要求同时满足若干文学条件才成立" in (
            question.literary_judgment_policy
        )
        assert "观察框架" in question.literary_judgment_policy


def test_program_compiled_questions_load_projection_and_defer_draft_text() -> None:
    assert {"3.1", "3.2"}.issubset(
        LEARNING_REPORT_PROGRAM_COMPILED_QUESTION_IDS
    )
    assert LEARNING_REPORT_PROGRAM_COMPILED_QUESTION_IDS.issubset(
        LEARNING_REPORT_PROJECTION_QUESTION_IDS
    )


def test_learning_report_payload_serializes_program_artifact_datetime() -> None:
    payload = {
        "answers": [{
            "question_id": "3.1",
            "program_artifacts": {
                "opening_structure_ledger": {
                    "generated_at": datetime(
                        2026,
                        7,
                        30,
                        2,
                        15,
                        tzinfo=timezone.utc,
                    ),
                },
            },
        }],
    }

    decoded = json.loads(_serialize_learning_report_payload(payload))

    assert decoded["answers"][0]["program_artifacts"][
        "opening_structure_ledger"
    ]["generated_at"] == "2026-07-30T02:15:00+00:00"


def test_learning_answer_cap_clamps_to_smaller_reported_context() -> None:
    budget = _request_budget(SimpleNamespace(
        max_output_tokens=16_000,
        context_window_tokens=128_000,
    ))

    assert budget["input_token_budget"] == 107_904
    assert budget["budget_source"] == "MODEL_CONTEXT_WITH_DEFAULT_SOFT_CAP"


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
        "characters": [
            {
                "name": "林舟",
                "role": "PROTAGONIST",
                "goals": ["查明寄信人"],
                "motivations": ["保护家人"],
                "evidence_ids": ["evd_character"],
            },
            {
                "name": "顾明",
                "role": "SUPPORTING",
                "goals": ["协助林舟"],
                "motivations": [],
                "evidence_ids": ["evd_char_gm"],
            },
            {
                "name": "陈峰",
                "role": "ANTAGONIST",
                "goals": ["隐瞒真相"],
                "motivations": ["自保"],
                "evidence_ids": ["evd_char_cf"],
            },
            {
                "name": "林母",
                "role": "SUPPORTING",
                "goals": ["保护林舟"],
                "motivations": [],
                "evidence_ids": ["evd_char_lm"],
            },
        ],
        "events": [
            {
                "id": "evt_opening",
                "people": ["林舟"],
                "chapter_ordinals": [1],
                "evidence_ids": ["evd_event"],
            },
            {
                "id": "evt_clue",
                "people": ["林舟", "顾明"],
                "chapter_ordinals": [2],
                "evidence_ids": ["evd_evt2"],
            },
            {
                "id": "evt_conflict",
                "people": ["林舟", "陈峰"],
                "chapter_ordinals": [3],
                "evidence_ids": ["evd_evt3"],
            },
            {
                "id": "evt_revelation",
                "people": ["林舟", "林母"],
                "chapter_ordinals": [5],
                "evidence_ids": ["evd_evt4"],
            },
            {
                "id": "evt_confrontation",
                "people": ["林舟", "陈峰"],
                "chapter_ordinals": [8],
                "evidence_ids": ["evd_evt5"],
            },
        ],
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


def test_refreshed_questions_have_independent_item_contracts() -> None:
    assert [
        item.item_id for item in LEARNING_QUESTION_ITEM_CONTRACTS["1.4"]
    ] == [
        "selling_point_card",
        "opening_promise_sources",
        "first_payoff_location",
        "cross_book_comparison",
    ]
    assert [
        item.item_id for item in LEARNING_QUESTION_ITEM_CONTRACTS["2.2"]
    ] == [
        "surface_desire",
        "deep_desire",
        "motivation",
        "contrast",
        "boundary",
        "core_ability",
        "desire_conflicts",
        "arc_timeline",
    ]
    assert [
        item.item_id for item in LEARNING_QUESTION_ITEM_CONTRACTS["3.1"]
    ] == [
        "opening_scene_type",
        "protagonist_entrance",
        "first_sentence_and_paragraph",
        "opening_scene_card",
        "cross_book_opening_distribution",
    ]
    assert [
        item.item_id for item in LEARNING_QUESTION_ITEM_CONTRACTS["3.2"]
    ] == [
        "chapter_tasks",
        "paragraph_task_sequence",
        "key_event_positions",
        "information_loading_timeline",
        "core_ability_first_appearance",
        "cross_book_rhythm_range",
    ]
    assert [
        item.item_id for item in LEARNING_QUESTION_ITEM_CONTRACTS["4.9"]
    ] == [
        "chapter_coverage",
        "type_distribution",
        "strength_rhythm",
        "type_rotation",
        "no_hook_analysis",
        "representative_examples",
        "scope_boundary",
    ]
    hook_contract = LEARNING_QUESTION_CONTRACTS["4.9"]
    assert "不统计后续回应距离" in hook_contract.measurement_requirements
    assert "后续回应证据都不能替代章末证据" in (
        hook_contract.evidence_requirements
    )


def test_sparse_deep_analysis_only_unlocks_independently_supported_questions() -> None:
    readiness = assess_learning_report_readiness(
        _projection(complete_specialized_ledgers=False)
    )

    assert readiness["ready"] is True
    assert readiness["ready_question_count"] == 5
    assert readiness["partial_question_count"] == 5
    assert readiness["complete_question_count"] == 0
    assert readiness["total_question_count"] == 42
    assert readiness["generation_ready_question_ids"] == ["1.4", "2.1", "2.3", "2.4", "2.9"]
    checks = {item["question_id"]: item for item in readiness["checks"]}
    assert checks["1.4"]["ready"] is True
    assert checks["2.1"]["ready"] is True
    assert checks["2.2"]["ready"] is False
    assert checks["4.9"]["observed"]["generic_scene_analysis_count"] == 1
    assert checks["4.9"]["observed"]["valid_chapter_end_sample_count"] == 0
    assert checks["3.4"]["ready"] is False
    assert readiness["next_required_artifacts"][0] == "前三章逐段任务与信息装载共享账本"


def test_specialized_ledgers_keep_3_4_blocked_until_its_own_payoff_tracking() -> None:
    readiness = assess_learning_report_readiness(
        _projection(complete_specialized_ledgers=True)
    )

    assert readiness["ready"] is True
    assert readiness["ready_question_count"] == 8
    assert readiness["complete_question_count"] == 2
    assert readiness["partial_question_count"] == 6
    assert readiness["generation_ready_question_ids"] == [
        "1.4",
        "2.1",
        "2.2",
        "4.9",
        "2.3",
        "2.4",
        "5.3",
        "2.9",
    ]
    assert readiness["next_required_artifacts"] == [
        "前三章逐段任务与信息装载共享账本",
        "前三章章末钩兑现追踪表（独立于 4.9）",
        "世界规则与事件列表（用于金手指规格分析）",
        "世界规则与人物状态（用于力量体系分析）",
    ]


def test_independent_opening_payoff_ledger_unlocks_3_4() -> None:
    projection = _projection(complete_specialized_ledgers=True)
    projection["opening_hook_payoffs_evidence"] = {
        "is_current": True,
        "hooks": [
            {
                "chapter_ordinal": ordinal,
                "hook_type": "NONE",
                "hook_question": "",
                "response_status": "NOT_APPLICABLE",
                "ending_evidence_ids": [f"evd_end_{ordinal}"],
                "response_evidence_ids": [],
            }
            for ordinal in range(1, 4)
        ],
        "coverage": {
            "required_hook_count": 3,
            "tracked_hook_count": 3,
            "search_policy": "ALL_LATER_CHAPTERS_WINDOWED",
            "all_windows_completed": False,
            "all_required_hooks_resolved": True,
            "ending_evidence_complete": True,
            "response_position_program_validated": True,
        },
    }

    readiness = assess_learning_report_readiness(projection)

    assert readiness["ready_question_count"] == 9
    assert readiness["complete_question_count"] == 3
    assert readiness["generation_ready_question_ids"] == [
        "1.4",
        "3.4",
        "2.1",
        "2.2",
        "4.9",
        "2.3",
        "2.4",
        "5.3",
        "2.9",
    ]


def _opening_structure_ledger() -> dict:
    return {
        "is_current": True,
        "opening_scene": {
            "opening_type": "日常被打破",
            "story_start_paragraph": 2,
            "story_start_source_char": 10,
            "story_start_evidence_id": "evd_opening",
            "protagonist_first_paragraph": 3,
            "protagonist_first_source_char": 30,
            "protagonist_first_evidence_id": "evd_opening",
            "protagonist_action": "正在查看陌生来信",
            "initial_trouble": "信中要求他立刻离开旧宅",
            "first_sentence_function": "先制造异常",
            "first_paragraph_function": "建立悬念",
            "explanation": "以异常来信打破日常。",
            "evidence_ids": ["evd_opening"],
        },
        "chapter_tasks": [
            {
                "chapter_ordinal": ordinal,
                "tasks": [f"完成第 {ordinal} 章任务"],
                "key_event_paragraphs": [2],
                "key_event_positions": [{
                    "paragraph": 2,
                    "source_char_start": ordinal * 100,
                    "source_char_end": ordinal * 100 + 20,
                    "evidence_id": f"evd_chapter_{ordinal}",
                }],
                "explanation": f"第 {ordinal} 章完成对应任务。",
                "evidence_ids": [f"evd_chapter_{ordinal}"],
            }
            for ordinal in range(1, 4)
        ],
        "paragraph_segments": [
            {
                "chapter_ordinal": ordinal,
                "paragraph_start": 1,
                "paragraph_end": 3,
                "scope": "STORY",
                "function": f"第 {ordinal} 章功能",
                "information_modules": ["主角困境"],
                "explanation": "连续段落承担同一主要功能。",
                "character_count": 400,
                "source_char_start": ordinal * 100,
                "source_char_end": ordinal * 100 + 80,
                "evidence_ids": [
                    f"evd_segment_{ordinal}_{index}"
                    for index in range(1, 51)
                ],
            }
            for ordinal in range(1, 4)
        ],
        "information_timeline": [
            {
                "module": module,
                "status": (
                    "SUPPORTED"
                    if module == "主角困境"
                    else "NOT_OBSERVED"
                ),
                "first_chapter_ordinal": (
                    1 if module == "主角困境" else None
                ),
                "first_paragraph": (
                    1 if module == "主角困境" else None
                ),
                "finding": (
                    "首章建立主角困境。"
                    if module == "主角困境"
                    else "前三章未观察到足够依据。"
                ),
                "character_count": 400 if module == "主角困境" else 0,
                "source_char_start": (
                    100 if module == "主角困境" else None
                ),
                "source_char_end": (
                    180 if module == "主角困境" else None
                ),
                "evidence_ids": (
                    ["evd_timeline_1"]
                    if module == "主角困境"
                    else []
                ),
            }
            for module in (
                "主角困境",
                "主角性格",
                "核心能力",
                "世界观规则",
                "威胁",
                "短期目标",
            )
        ],
        "coverage": {
            "paragraph_coverage_complete": True,
            "paragraph_sequence_contiguous": True,
            "story_character_count": 1200,
        },
        "overall_sequence": "前三章依次建立异常、选择与行动。",
        "limitations": [
            "这种安排会提升读者留存率。"
        ],
    }


def test_shared_opening_structure_unlocks_3_1_and_3_2_independently() -> None:
    projection = _projection(complete_specialized_ledgers=False)
    projection["opening_structure_status"] = "READY"
    projection["opening_structure_evidence"] = _opening_structure_ledger()

    readiness = assess_learning_report_readiness(projection)
    checks = {item["question_id"]: item for item in readiness["checks"]}

    assert checks["3.1"]["ready"] is True
    assert checks["3.1"]["answer_scope"] == "PARTIAL"
    assert checks["3.2"]["ready"] is True
    assert checks["3.2"]["answer_scope"] == "PARTIAL"
    assert readiness["generation_ready_question_ids"][:3] == [
        "1.4",
        "3.1",
        "3.2",
    ]


def test_3_1_and_3_2_only_receive_compact_specialized_material() -> None:
    projection = _projection(complete_specialized_ledgers=False)
    projection["opening_structure_evidence"] = _opening_structure_ledger()

    materials_3_1 = _source_materials(projection, ("3.1",))
    materials_3_2 = _source_materials(projection, ("3.2",))

    assert [kind for _priority, kind, _item in materials_3_1] == [
        "opening_structure_evidence"
    ]
    assert [kind for _priority, kind, _item in materials_3_2] == [
        "opening_structure_evidence"
    ]
    artifact_3_1 = materials_3_1[0][2]
    artifact_3_2 = materials_3_2[0][2]
    assert "opening_scene" in artifact_3_1
    assert "paragraph_segments" not in artifact_3_1
    assert "opening_scene" not in artifact_3_2
    assert len(artifact_3_2["paragraph_segments"]) == 3
    assert all(
        len(item["evidence_ids"]) <= 2
        for item in artifact_3_2["paragraph_segments"]
    )
    assert "story_overview" not in {
        kind for _priority, kind, _item in materials_3_1 + materials_3_2
    }


def test_opening_structure_compaction_keeps_all_segments_without_all_evidence() -> None:
    ledger = _opening_structure_ledger()
    ledger["paragraph_segments"] = [
        {
            **ledger["paragraph_segments"][index % 3],
            "paragraph_start": index + 1,
            "paragraph_end": index + 1,
            "evidence_ids": [
                f"evd_segment_{index}_{evidence_index}"
                for evidence_index in range(100)
            ],
        }
        for index in range(42)
    ]

    artifact = _compact_opening_structure_model_artifact(
        ledger,
        "3.2",
    )

    assert len(artifact["paragraph_segments"]) == 42
    assert all(
        len(item["evidence_ids"]) == 2
        for item in artifact["paragraph_segments"]
    )


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
        answer["contract_items"] = [
            {
                "item_id": "first_scene_functions",
                "status": "SUPPORTED",
                "finding": "逐人首场功能已经完成分类。",
                "metrics": [],
                "evidence_ids": [],
                "limitations": [],
                "classifications": [{
                    "sequence_no": 1,
                    "category": "主角锚点",
                }],
            }
        ]
    elif question_id in LEARNING_QUESTION_ITEM_CONTRACTS:
        if question_id in {"2.2", "4.9"}:
            answer["status"] = "ANSWERED"
        answer["contract_items"] = [
            {
                "item_id": definition.item_id,
                "status": (
                    "INSUFFICIENT_EVIDENCE"
                    if (
                        definition.item_id
                        in {
                            "cross_book_comparison",
                            "cross_book_opening_distribution",
                            "cross_book_rhythm_range",
                        }
                    )
                    else "SUPPORTED"
                ),
                "finding": (
                    "4.9 不追踪后续回应；前三章首次回应属于 3.4，"
                    "重要悬念生命周期属于 4.10。"
                    if definition.item_id == "scope_boundary"
                    else "当前材料支持该合同项目。"
                ),
                "metrics": (
                    []
                    if (
                        question_id == "1.4"
                        and definition.item_id == "cross_book_comparison"
                    )
                    else [{
                        "label": "测试指标",
                        "value": "1",
                        "unit": "项",
                        "method": "按固定测试夹具统计。",
                        "evidence_ids": [],
                    }]
                ),
                "evidence_ids": (
                    []
                    if definition.item_id in {
                        "cross_book_comparison",
                        "scope_boundary",
                    }
                    else ["evd_test"]
                ),
                "limitations": (
                    ["缺少同口径跨书数据。"]
                    if definition.item_id == "cross_book_comparison"
                    else []
                ),
                "classifications": [],
            }
            for definition in LEARNING_QUESTION_ITEM_CONTRACTS[question_id]
        ]
        if question_id == "1.4":
            payoff_item = next(
                item
                for item in answer["contract_items"]
                if item["item_id"] == "first_payoff_location"
            )
            payoff_item["payoff_classifications"] = [{
                "sequence_no": 1,
                "matched_facet_ids": ["F1", "F2", "F3"],
                "exclusion_code": "NONE",
                "anchor_evidence_no": 1,
            }]
    return answer


@pytest.mark.parametrize(
    ("question_id", "apply_program", "stale_text"),
    [
        (
            "3.1",
            _apply_program_3_1_answer,
            "缺少后续强力设定会导致读者失去核心期待。",
        ),
        (
            "3.2",
            _apply_program_3_2_answer,
            "这种安排会提升读者留存率。",
        ),
    ],
)
def test_program_compiles_3_1_and_3_2_before_final_text_boundary_checks(
    question_id: str,
    apply_program,
    stale_text: str,
) -> None:
    payload = _answer(question_id)
    payload["conclusion"] = "专项账本被省略，只能依据故事概览推测。"
    payload["limitations"] = [stale_text]
    payload["reusable_lessons"] = [stale_text]
    payload["do_not_copy"] = [stale_text]
    for item in payload["contract_items"]:
        if item["status"] == "SUPPORTED":
            item["metrics"] = []
            item["evidence_ids"] = []

    output = parse_learning_report(
        {
            "answers": [payload],
            "author_decisions": [],
            "method_candidates": [],
        },
        expected_question_ids=[question_id],
        defer_user_text_checks_for={question_id},
    )
    compiled = output.answers[0]
    apply_program(compiled, _opening_structure_ledger())

    _validate_answer_user_text_boundaries(compiled)

    compiled_text = "".join([
        compiled.conclusion,
        *compiled.limitations,
        *compiled.reusable_lessons,
        *compiled.do_not_copy,
        *(item.finding for item in compiled.contract_items),
    ])
    assert "专项账本被省略" not in compiled_text
    assert stale_text not in compiled_text


def test_program_3_1_uses_only_opening_evidence_and_clean_punctuation() -> None:
    answer = LearningAnswerProposal.model_validate(_answer("3.1"))
    ledger = _opening_structure_ledger()
    ledger["opening_scene"]["protagonist_action"] = "正在查看陌生来信。"
    ledger["opening_scene"]["initial_trouble"] = "必须立刻离开旧宅。"

    _apply_program_3_1_answer(answer, ledger)

    answer_text = "".join([
        answer.conclusion,
        *(item.finding for item in answer.contract_items),
    ])
    assert "。；" not in answer_text
    assert "。。" not in answer_text
    assert answer.evidence_ids == [
        "evd_opening",
        "evd_chapter_1",
    ]
    assert "evd_chapter_2" not in answer.evidence_ids
    first_functions = next(
        item
        for item in answer.contract_items
        if item.item_id == "first_sentence_and_paragraph"
    )
    assert first_functions.finding.startswith(
        "专项账本的文学判断（不是读者效果实测）"
    )


def test_program_3_2_separates_first_ability_signal_from_later_segments() -> None:
    answer = LearningAnswerProposal.model_validate(_answer("3.2"))
    ledger = _opening_structure_ledger()
    core = next(
        item
        for item in ledger["information_timeline"]
        if item["module"] == "核心能力"
    )
    core.update({
        "status": "SUPPORTED",
        "first_chapter_ordinal": 2,
        "first_paragraph": 267,
        "source_char_start": 18058,
        "source_char_end": 18080,
        "character_count": 17746,
        "finding": (
            "先被评为S级，后续展示精密枪法与龙文异常。"
        ),
        "evidence_ids": ["evd_core_signal"],
    })
    ledger["paragraph_segments"].extend([
        {
            "chapter_ordinal": 2,
            "paragraph_start": 267,
            "paragraph_end": 305,
            "information_modules": ["核心能力"],
            "function": "确认S级潜力与血统评级",
            "evidence_ids": ["evd_core_signal", "evd_core_signal_2"],
        },
        {
            "chapter_ordinal": 3,
            "paragraph_start": 296,
            "paragraph_end": 340,
            "information_modules": ["核心能力"],
            "function": "危机中显露精密枪法",
            "evidence_ids": ["evd_core_action"],
        },
        {
            "chapter_ordinal": 3,
            "paragraph_start": 404,
            "paragraph_end": 463,
            "information_modules": ["核心能力"],
            "function": "龙文测试发现异常",
            "evidence_ids": ["evd_core_language"],
        },
    ])

    _apply_program_3_2_answer(answer, ledger)

    timeline = next(
        item
        for item in answer.contract_items
        if item.item_id == "information_loading_timeline"
    )
    ability = next(
        item
        for item in answer.contract_items
        if item.item_id == "core_ability_first_appearance"
    )
    assert "核心能力相关信号首次见于第 2 章第 267 段" in timeline.finding
    assert "能力相关信息首次装载" in ability.finding
    assert "不等同于后续能力都已在该段现场展示" in ability.finding
    assert "第 3 章第 296—340 段“危机中显露精密枪法”" in ability.finding
    assert "第 3 章第 404—463 段“龙文测试发现异常”" in ability.finding
    assert "后续展示精密枪法与龙文异常" not in ability.finding
    assert ability.evidence_ids == [
        "evd_core_signal",
        "evd_core_action",
        "evd_core_language",
    ]


def _validated_2_2_answer() -> tuple[LearningAnswerProposal, dict]:
    fields = []
    contract_items = []
    for chapter, field in enumerate((
        "surface_desire",
        "deep_desire",
        "motivation",
        "contrast",
        "boundary",
        "core_ability",
    ), start=1):
        evidence_id = f"evd_{field}"
        fields.append({
            "field": field,
            "status": "SUPPORTED",
            "first_display_chapter_ordinal": chapter,
            "evidence_ids": [evidence_id],
        })
        contract_items.append({
            "item_id": field,
            "status": "SUPPORTED",
            "finding": f"第 {chapter} 章通过行动展示该要素。",
            "metrics": [{
                "label": "首次展示章节",
                "value": str(chapter),
                "unit": "章",
                "method": "按主角专项证据账本定位。",
                "evidence_ids": [evidence_id],
            }],
            "evidence_ids": [evidence_id],
            "limitations": [],
            "classifications": [],
        })
    conflicts = [
        {
            "chapter_ordinal": chapter,
            "surface_desire": "尽快找到寄信人。",
            "deep_desire": "掌握自己的处境。",
            "motive": f"第 {chapter} 章现场动机原文",
            "choice": f"第 {chapter} 章实际选择原文",
            "result": f"第 {chapter} 章现场结果原文",
            "sacrificed_desire": "SURFACE",
            "sacrifice": f"第 {chapter} 章付出代价原文",
            "arc_change": f"第 {chapter} 章弧光发生变化",
            "motive_evidence_ids": [f"evd_conflict_{index}"],
            "choice_evidence_ids": [f"evd_conflict_{index}"],
            "result_evidence_ids": [f"evd_conflict_{index}"],
            "sacrifice_evidence_ids": [f"evd_conflict_{index}"],
            "evidence_ids": [f"evd_conflict_{index}"],
        }
        for index, chapter in enumerate((7, 8), start=1)
    ]
    conflict_evidence_ids = [
        evidence_id
        for conflict in conflicts
        for evidence_id in conflict["evidence_ids"]
    ]
    contract_items.extend([
        {
            "item_id": "desire_conflicts",
            "status": "SUPPORTED",
            "finding": "第 7 章和第 8 章各有一个双层欲望冲突节点。",
            "metrics": [{
                "label": "冲突节点",
                "value": "2",
                "unit": "个",
                "method": "按专项账本逐项统计。",
                "evidence_ids": conflict_evidence_ids,
            }],
            "evidence_ids": conflict_evidence_ids,
            "limitations": [],
            "classifications": [],
        },
        {
            "item_id": "arc_timeline",
            "status": "SUPPORTED",
            "finding": "第 7 章发生第一次转折，第 8 章发生第二次转折。",
            "metrics": [{
                "label": "转折节点",
                "value": "2",
                "unit": "个",
                "method": "按章节顺序汇总专项账本。",
                "evidence_ids": conflict_evidence_ids,
            }],
            "evidence_ids": conflict_evidence_ids,
            "limitations": [],
            "classifications": [],
        },
    ])
    all_evidence_ids = [
        evidence_id
        for field in fields
        for evidence_id in field["evidence_ids"]
    ] + conflict_evidence_ids
    return LearningAnswerProposal.model_validate({
        "question_id": "2.2",
        "status": "ANSWERED",
        "conclusion": "六项人物要素和两个冲突节点均有行动原文支持。",
        "metrics": [{
            "label": "已核验人物要素",
            "value": "6",
            "unit": "项",
            "method": "按专项账本统计。",
            "evidence_ids": all_evidence_ids[:1],
        }],
        "evidence_ids": all_evidence_ids,
        "counter_evidence_ids": [],
        "limitations": ["结论只覆盖当前单书。"],
        "reusable_lessons": ["用具体选择展示人物要素。"],
        "do_not_copy": ["不能照搬人物和事件。"],
        "contract_items": contract_items,
    }), {
        "character_design_evidence": {
            "fields": fields,
            "desire_conflicts": conflicts,
        },
    }


def _validated_1_4_answer() -> tuple[
    LearningAnswerProposal,
    dict,
    dict,
    dict,
]:
    payload = _answer("1.4")
    payload["metrics"] = [{
        "label": "首次兑现章节",
        "value": "2",
        "unit": "章",
        "method": "按开篇事件时序定位。",
        "evidence_ids": ["evd_payoff"],
    }]
    payload["evidence_ids"] = ["evd_intro", "evd_payoff"]
    item_by_id = {
        item["item_id"]: item for item in payload["contract_items"]
    }
    item_by_id["selling_point_card"]["evidence_ids"] = ["evd_intro"]
    item_by_id["opening_promise_sources"].update({
        "finding": (
            "书名为《测试书》；简介承诺主角将进入未知世界；"
            "故事前提是林舟追查旧宅秘密；前三章持续建立未知世界承诺。"
        ),
        "metrics": [
            {
                "label": "书名",
                "value": "测试书",
                "unit": "",
                "method": "核对源文件书名。",
                "evidence_ids": [],
            },
            {
                "label": "简介",
                "value": "主角将进入未知世界。",
                "unit": "",
                "method": "核对前置简介。",
                "evidence_ids": ["evd_intro"],
            },
            {
                "label": "故事前提",
                "value": "林舟追查旧宅秘密。",
                "unit": "",
                "method": "核对故事总览。",
                "evidence_ids": ["evd_intro"],
            },
            {
                "label": "前三章",
                "value": "第 2 章未知力量在现实中出现。",
                "unit": "",
                "method": "核对前三章现场事件。",
                "evidence_ids": ["evd_payoff"],
            },
        ],
        "evidence_ids": ["evd_intro", "evd_payoff"],
    })
    item_by_id["first_payoff_location"].update({
        "finding": (
            "首次兑现位于第一幕 新世界；更早候选只是简介承诺重述，"
            "尚未发生正文行动。"
        ),
        "metrics": [
            {
                "label": "首次兑现章节",
                "value": "2",
                "unit": "章",
                "method": "按开篇事件时序定位。",
                "evidence_ids": ["evd_payoff"],
            },
            {
                "label": "兑现段落",
                "value": "4",
                "unit": "段",
                "method": "按原文依据段落序号定位。",
                "evidence_ids": ["evd_payoff"],
            },
            {
                "label": "兑现位置累计字符",
                "value": "101",
                "unit": "字符",
                "method": "按源文件一基字符位置定位。",
                "evidence_ids": ["evd_payoff"],
            },
            {
                "label": "承诺到兑现章距",
                "value": "2",
                "unit": "章",
                "method": "从前置简介位置 0 计算。",
                "evidence_ids": ["evd_intro", "evd_payoff"],
            },
            {
                "label": "承诺到兑现字符距离",
                "value": "90",
                "unit": "字符",
                "method": "以两条原文依据的起点相减。",
                "evidence_ids": ["evd_intro", "evd_payoff"],
            },
        ],
        "evidence_ids": ["evd_payoff"],
        "payoff_classifications": [{
            "sequence_no": 1,
            "matched_facet_ids": ["F1", "F2", "F3"],
            "exclusion_code": "NONE",
            "anchor_evidence_no": 1,
        }],
    })
    projection = {
        "story_overview": {
            "premise": "林舟追查旧宅秘密。",
            "evidence_ids": ["evd_intro"],
        },
        "events": [{
            "id": "evt_payoff",
            "title": "林舟进入未知世界",
            "summary": "未知力量在现实中出现并把林舟卷入冲突。",
            "narrative_mode": "ACTUAL",
            "start_char": 100,
            "chapter_ordinals": [2],
            "evidence_ids": ["evd_payoff"],
        }],
    }
    opening_sources = {
        "preferred_title": "测试书",
        "description_present": True,
        "description_text": "简介承诺主角将进入未知世界。",
        "description_source_char_start": 11,
        "description_evidence_ids": ["evd_intro"],
        "evidence_ids": ["evd_intro"],
        "promise_chapter_position": 0,
    }
    evidence_by_id = {
        "evd_intro": SimpleNamespace(
            id="evd_intro",
            paragraph_index=0,
            start_char=10,
            end_char=20,
            source_unit_id="unit_preface",
            text_snapshot="简介承诺主角将进入未知世界。",
            source_unit=SimpleNamespace(title="正文前内容"),
        ),
        "evd_payoff": SimpleNamespace(
            id="evd_payoff",
            paragraph_index=3,
            start_char=100,
            end_char=130,
            source_unit_id="unit_2",
            text_snapshot="未知力量在现实中出现并把林舟卷入冲突。",
            source_unit=SimpleNamespace(title="第一幕 新世界"),
        ),
    }
    return (
        LearningAnswerProposal.model_validate(payload),
        projection,
        opening_sources,
        evidence_by_id,
    )


def _validated_4_9_answer() -> tuple[LearningAnswerProposal, dict]:
    type_rows = [
        ("CRISIS_SUSPENSION", 1, 0.5),
        ("NEW_INFORMATION", 0, 0.0),
        ("PAYOFF_PRIMING", 0, 0.0),
        ("REVERSAL", 0, 0.0),
        ("EMOTIONAL_FREEZE", 0, 0.0),
        ("NONE", 1, 0.5),
    ]
    strength_rows = [
        ("STRONG", 1, 0.5),
        ("MEDIUM", 0, 0.0),
        ("LIGHT", 0, 0.0),
        ("NONE", 1, 0.5),
    ]
    evidence_ids = ["evd_end_1", "evd_end_2"]
    contract_items = [
        {
            "item_id": "chapter_coverage",
            "status": "SUPPORTED",
            "finding": "两个章节由一个连续窗口不重不漏覆盖。",
            "metrics": [
                {
                    "label": "全书章节",
                    "value": "2",
                    "unit": "章",
                    "method": "按正式来源章节统计。",
                    "evidence_ids": [],
                },
                {
                    "label": "连续窗口",
                    "value": "1",
                    "unit": "个",
                    "method": "按正式窗口账本统计。",
                    "evidence_ids": [],
                },
            ],
            "evidence_ids": evidence_ids,
            "limitations": [],
            "classifications": [],
        },
        {
            "item_id": "type_distribution",
            "status": "SUPPORTED",
            "finding": "逐类统计章末钩数量与比例。",
            "metrics": [
                {
                    "label": hook_type,
                    "value": str(count),
                    "unit": f"章，占比 {ratio * 100:.0f}%",
                    "method": "按逐章类型分类统计。",
                    "evidence_ids": [],
                }
                for hook_type, count, ratio in type_rows
            ],
            "evidence_ids": evidence_ids,
            "limitations": [],
            "classifications": [],
        },
        {
            "item_id": "strength_rhythm",
            "status": "SUPPORTED",
            "finding": "逐档统计钩子强度。",
            "metrics": [
                {
                    "label": strength,
                    "value": str(count),
                    "unit": "章",
                    "method": "按逐章强度分类统计。",
                    "evidence_ids": [],
                }
                for strength, count, _ratio in strength_rows
            ] + [{
                "label": "最长连续强钩",
                "value": "1",
                "unit": "章",
                "method": "按连续章节精确合并。",
                "evidence_ids": [],
            }],
            "evidence_ids": evidence_ids,
            "limitations": [],
            "classifications": [],
        },
        {
            "item_id": "type_rotation",
            "status": "SUPPORTED",
            "finding": "相邻两章发生一次类型切换。",
            "metrics": [
                {
                    "label": "类型切换",
                    "value": "1",
                    "unit": "次",
                    "method": "比较相邻章节类型。",
                    "evidence_ids": [],
                },
                {
                    "label": "同类连续上限",
                    "value": "1",
                    "unit": "章",
                    "method": "按连续章节精确合并。",
                    "evidence_ids": [],
                },
            ],
            "evidence_ids": evidence_ids,
            "limitations": [],
            "classifications": [],
        },
        {
            "item_id": "no_hook_analysis",
            "status": "SUPPORTED",
            "finding": "第二章是无钩章。",
            "metrics": [{
                "label": "无钩章",
                "value": "1",
                "unit": "章，占比 50%",
                "method": "按逐章 NONE 分类统计。",
                "evidence_ids": ["evd_end_2"],
            }],
            "evidence_ids": ["evd_end_2"],
            "limitations": [],
            "classifications": [],
        },
        {
            "item_id": "representative_examples",
            "status": "SUPPORTED",
            "finding": "危机悬置和无钩均引用对应章末原文。",
            "metrics": [{
                "label": "已覆盖类型",
                "value": "2",
                "unit": "种",
                "method": "每种实际出现类型取一条章末原文。",
                "evidence_ids": evidence_ids,
            }],
            "evidence_ids": evidence_ids,
            "limitations": [],
            "classifications": [],
        },
        {
            "item_id": "scope_boundary",
            "status": "SUPPORTED",
            "finding": "4.9 不追踪后续回应；3.4 管前三章首次回应，4.10 管重要悬念生命周期。",
            "metrics": [],
            "evidence_ids": [],
            "limitations": [],
            "classifications": [],
        },
    ]
    return LearningAnswerProposal.model_validate({
        "question_id": "4.9",
        "status": "ANSWERED",
        "conclusion": "两章章末类型和强弱节律已完整统计。",
        "metrics": [{
            "label": "全书章末分类覆盖",
            "value": "2",
            "unit": "章",
            "method": "按连续窗口逐章分类。",
            "evidence_ids": ["evd_end_1"],
        }],
        "evidence_ids": evidence_ids,
        "counter_evidence_ids": [],
        "limitations": ["结论只覆盖当前单书。"],
        "reusable_lessons": ["章末类型和强度分开统计。"],
        "do_not_copy": ["不能照搬具体章末表达。"],
        "contract_items": contract_items,
    }), {
        "chapter_end_hooks_evidence": {
            "chapters": [
                {
                    "chapter_ordinal": 1,
                    "hook_type": "CRISIS_SUSPENSION",
                    "strength": "STRONG",
                    "ending_evidence_ids": ["evd_end_1"],
                },
                {
                    "chapter_ordinal": 2,
                    "hook_type": "NONE",
                    "strength": "NONE",
                    "ending_evidence_ids": ["evd_end_2"],
                },
            ],
            "coverage": {
                "source_chapter_count": 2,
                "window_count": 1,
            },
            "summary": {
                "type_distribution": [
                    {"hook_type": hook_type, "count": count, "ratio": ratio}
                    for hook_type, count, ratio in type_rows
                ],
                "strength_distribution": [
                    {"strength": strength, "count": count, "ratio": ratio}
                    for strength, count, ratio in strength_rows
                ],
                "type_transition_count": 1,
                "max_consecutive_strong": 1,
                "max_consecutive_same_type": 1,
                "no_hook_count": 1,
                "no_hook_ratio": 0.5,
                "examples_by_type": {
                    "CRISIS_SUSPENSION": [{
                        "ending_evidence_ids": ["evd_end_1"],
                    }],
                    "NONE": [{
                        "ending_evidence_ids": ["evd_end_2"],
                    }],
                },
            },
        },
    }


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


def test_parser_rejects_1_4_with_missing_contract_item() -> None:
    answer = _answer("1.4")
    answer["contract_items"] = answer["contract_items"][:-1]

    with pytest.raises(LearningReportValidationError) as error:
        parse_learning_report(
            {
                "answers": [answer],
                "author_decisions": [],
                "method_candidates": [],
            },
            expected_question_ids=["1.4"],
        )

    assert error.value.code == "LEARNING_REPORT_CONTRACT_ITEM_COVERAGE_INVALID"


def test_parser_accepts_2_2_with_valid_partial_contract() -> None:
    answer = _answer("2.2")
    contrast = next(
        item
        for item in answer["contract_items"]
        if item["item_id"] == "contrast"
    )
    contrast.update({
        "status": "INSUFFICIENT_EVIDENCE",
        "finding": "全书主角事件中没有可核验的性格反差首次展示。",
        "metrics": [],
        "evidence_ids": [],
        "limitations": ["全书主角事件中没有可核验的性格反差首次展示。"],
    })
    answer["status"] = "PARTIAL"

    output = parse_learning_report(
        {
            "answers": [answer],
            "author_decisions": [],
            "method_candidates": [],
        },
        expected_question_ids=["2.2"],
    )

    assert output.answers[0].status == "PARTIAL"
    assert output.answers[0].contract_items[3].status == "INSUFFICIENT_EVIDENCE"


def test_2_2_readiness_accepts_full_book_insufficient_contrast() -> None:
    projection = _projection(complete_specialized_ledgers=True)
    contrast = next(
        item
        for item in projection["character_design_evidence"]["fields"]
        if item["field"] == "contrast"
    )
    contrast.update({
        "status": "INSUFFICIENT_EVIDENCE",
        "value": "",
        "first_display_chapter_ordinal": None,
        "first_display_event_id": None,
        "display_event": "",
        "evidence_ids": [],
        "explanation": "全书主角事件中没有可核验的性格反差首次展示。",
    })

    readiness = assess_learning_report_readiness(projection)

    checks = {item["question_id"]: item for item in readiness["checks"]}
    assert checks["2.2"]["ready"] is True
    assert checks["2.2"]["answer_scope"] == "PARTIAL"
    assert checks["2.2"]["observed"]["contract_field_evidence_count"] == 5
    assert checks["2.2"]["observed"]["contract_field_insufficient_count"] == 1
    assert "2.2" in readiness["generation_ready_question_ids"]


def test_program_compiles_2_2_insufficient_field_without_fake_support() -> None:
    projection = _projection(complete_specialized_ledgers=True)
    ledger = projection["character_design_evidence"]
    contrast = next(
        item
        for item in ledger["fields"]
        if item["field"] == "contrast"
    )
    contrast.update({
        "status": "INSUFFICIENT_EVIDENCE",
        "value": "",
        "first_display_chapter_ordinal": None,
        "first_display_event_id": None,
        "display_event": "",
        "evidence_ids": [],
        "explanation": "全书主角事件中没有可核验的性格反差首次展示。",
    })
    answer = LearningAnswerProposal.model_validate(_answer("2.2"))

    _apply_program_2_2_answer(answer, ledger)
    _validate_selected_answers_against_projection(
        LearningReportOutput(
            answers=[answer],
            author_decisions=[],
            method_candidates=[],
        ),
        projection,
    )

    compiled = next(
        item
        for item in answer.contract_items
        if item.item_id == "contrast"
    )
    assert answer.status == "PARTIAL"
    assert compiled.status == "INSUFFICIENT_EVIDENCE"
    assert compiled.metrics == []
    assert compiled.evidence_ids == []
    assert compiled.finding == (
        "全书主角事件中没有可核验的性格反差首次展示"
    )


def test_program_rejects_fake_support_for_2_2_insufficient_field() -> None:
    projection = _projection(complete_specialized_ledgers=True)
    ledger = projection["character_design_evidence"]
    contrast = next(
        item
        for item in ledger["fields"]
        if item["field"] == "contrast"
    )
    contrast.update({
        "status": "INSUFFICIENT_EVIDENCE",
        "value": "",
        "first_display_chapter_ordinal": None,
        "first_display_event_id": None,
        "display_event": "",
        "evidence_ids": [],
        "explanation": "全书主角事件中没有可核验的性格反差首次展示。",
    })
    answer = LearningAnswerProposal.model_validate(_answer("2.2"))
    _apply_program_2_2_answer(answer, ledger)
    compiled = next(
        item
        for item in answer.contract_items
        if item.item_id == "contrast"
    )
    compiled.status = "SUPPORTED"
    compiled.metrics = [answer.metrics[0]]

    with pytest.raises(ValueError) as error:
        _validate_selected_answers_against_projection(
            LearningReportOutput(
                answers=[answer],
                author_decisions=[],
                method_candidates=[],
            ),
            projection,
        )

    assert str(error.value) == (
        "LEARNING_REPORT_2_2_CONTRAST_REFERENCE_INVALID"
    )


def test_parser_rejects_pricing_text_in_contract_items() -> None:
    answer = _answer("4.9")
    answer["contract_items"][0]["finding"] = "本次模型费用为若干金额。"

    with pytest.raises(LearningReportValidationError) as error:
        parse_learning_report(
            {
                "answers": [answer],
                "author_decisions": [],
                "method_candidates": [],
            },
            expected_question_ids=["4.9"],
        )

    assert error.value.code == "LEARNING_REPORT_PRICING_OUT_OF_SCOPE"


def test_program_validation_rejects_wrong_2_2_conflict_count() -> None:
    answer, projection = _validated_2_2_answer()
    payload = answer.model_dump(mode="json")
    conflict_item = next(
        item
        for item in payload["contract_items"]
        if item["item_id"] == "desire_conflicts"
    )
    conflict_item["metrics"][0]["value"] = "1"

    with pytest.raises(ValueError) as error:
        _validate_selected_answers_against_projection(
            LearningReportOutput(
                answers=[LearningAnswerProposal.model_validate(payload)],
                author_decisions=[],
                method_candidates=[],
            ),
            projection,
        )

    assert str(error.value) == "LEARNING_REPORT_2_2_CONFLICT_COUNT_INVALID"


def test_program_validation_rejects_missing_1_4_title_that_exists_in_source() -> None:
    answer, projection, opening_sources, evidence_by_id = (
        _validated_1_4_answer()
    )
    payload = answer.model_dump(mode="json")
    promise_item = next(
        item
        for item in payload["contract_items"]
        if item["item_id"] == "opening_promise_sources"
    )
    title_metric = next(
        metric
        for metric in promise_item["metrics"]
        if metric["label"] == "书名"
    )
    title_metric["value"] = "书名缺失"

    with pytest.raises(ValueError) as error:
        _validate_selected_answers_against_projection(
            LearningReportOutput(
                answers=[LearningAnswerProposal.model_validate(payload)],
                author_decisions=[],
                method_candidates=[],
            ),
            projection,
            opening_promise_sources=opening_sources,
            evidence_by_id=evidence_by_id,
        )

    assert str(error.value) == "LEARNING_REPORT_1_4_TITLE_SOURCE_INVALID"


def test_program_rejects_intro_evidence_as_first_three_chapter_evidence() -> None:
    answer, projection, opening_sources, evidence_by_id = (
        _validated_1_4_answer()
    )
    payload = answer.model_dump(mode="json")
    promise_item = next(
        item
        for item in payload["contract_items"]
        if item["item_id"] == "opening_promise_sources"
    )
    opening_metric = next(
        metric
        for metric in promise_item["metrics"]
        if metric["label"] == "前三章"
    )
    opening_metric["evidence_ids"] = ["evd_intro"]

    with pytest.raises(ValueError) as error:
        _validate_selected_answers_against_projection(
            LearningReportOutput(
                answers=[LearningAnswerProposal.model_validate(payload)],
                author_decisions=[],
                method_candidates=[],
            ),
            projection,
            opening_promise_sources=opening_sources,
            evidence_by_id=evidence_by_id,
        )

    assert str(error.value) == (
        "LEARNING_REPORT_1_4_OPENING_CHAPTER_SOURCE_INVALID"
    )


def test_program_replaces_unknown_1_4_opening_evidence_before_validation() -> None:
    answer, projection, opening_sources, evidence_by_id = (
        _validated_1_4_answer()
    )
    payload = answer.model_dump(mode="json")
    promise_item = next(
        item
        for item in payload["contract_items"]
        if item["item_id"] == "opening_promise_sources"
    )
    opening_metric = next(
        metric
        for metric in promise_item["metrics"]
        if metric["label"] == "前三章"
    )
    opening_metric["evidence_ids"] = ["evd_model_hallucinated"]
    promise_item["evidence_ids"] = [
        "evd_intro",
        "evd_model_hallucinated",
    ]
    output = LearningReportOutput(
        answers=[LearningAnswerProposal.model_validate(payload)],
        author_decisions=[],
        method_candidates=[],
    )

    _validate_selected_answers_against_projection(
        output,
        projection,
        opening_promise_sources=opening_sources,
        evidence_by_id=evidence_by_id,
    )

    compiled = next(
        item
        for item in output.answers[0].contract_items
        if item.item_id == "opening_promise_sources"
    )
    compiled_opening = next(
        metric for metric in compiled.metrics
        if metric.label == "前三章"
    )
    assert compiled_opening.evidence_ids == ["evd_payoff"]
    assert "evd_model_hallucinated" not in {
        evidence_id
        for item in output.answers[0].contract_items
        for evidence_id in item.evidence_ids
    }


def test_program_still_rejects_unknown_1_4_selling_point_evidence() -> None:
    answer, projection, opening_sources, evidence_by_id = (
        _validated_1_4_answer()
    )
    payload = answer.model_dump(mode="json")
    selling_point = next(
        item
        for item in payload["contract_items"]
        if item["item_id"] == "selling_point_card"
    )
    selling_point["evidence_ids"] = ["evd_model_hallucinated"]

    with pytest.raises(ValueError) as error:
        _validate_selected_answers_against_projection(
            LearningReportOutput(
                answers=[LearningAnswerProposal.model_validate(payload)],
                author_decisions=[],
                method_candidates=[],
            ),
            projection,
            opening_promise_sources=opening_sources,
            evidence_by_id=evidence_by_id,
        )

    assert str(error.value) == (
        "LEARNING_REPORT_1_4_EVIDENCE_SCOPE_INVALID"
    )


def test_old_refreshed_contract_versions_are_not_current() -> None:
    oldest_payload = {
        "question_contract_versions": {
            "1.4": "2.0.0",
            "2.2": "2.0.0",
        },
    }
    previous_payload = {
        "question_contract_versions": {
            "1.4": "2.1.0",
            "2.2": "2.0.0",
        },
    }
    current_payload = {
        "question_contract_versions": {
            "1.4": "2.5.0",
            "2.2": "2.2.0",
        },
    }

    assert not _answer_uses_current_contract(oldest_payload, "1.4")
    assert not _answer_uses_current_contract(previous_payload, "1.4")
    assert not _answer_uses_current_contract(previous_payload, "2.2")
    assert _answer_uses_current_contract(current_payload, "1.4")
    assert _answer_uses_current_contract(current_payload, "2.2")


def test_program_compiles_1_4_promise_sources_from_authoritative_material() -> None:
    answer, projection, opening_sources, evidence_by_id = (
        _validated_1_4_answer()
    )
    payload = answer.model_dump(mode="json")
    promise_item = next(
        item
        for item in payload["contract_items"]
        if item["item_id"] == "opening_promise_sources"
    )
    metric_by_label = {
        metric["label"]: metric
        for metric in promise_item["metrics"]
    }
    metric_by_label["简介"]["value"] = "模型补写的简介承诺"
    metric_by_label["故事前提"]["value"] = "模型补写的故事前提"
    metric_by_label["前三章"]["value"] = "普通生活起点"
    output = LearningReportOutput(
        answers=[LearningAnswerProposal.model_validate(payload)],
        author_decisions=[],
        method_candidates=[],
    )

    _validate_selected_answers_against_projection(
        output,
        projection,
        opening_promise_sources=opening_sources,
        evidence_by_id=evidence_by_id,
    )

    compiled = next(
        item
        for item in output.answers[0].contract_items
        if item.item_id == "opening_promise_sources"
    )
    compiled_by_label = {
        metric.label: metric
        for metric in compiled.metrics
    }
    assert compiled_by_label["简介"].value == opening_sources["description_text"]
    assert compiled_by_label["故事前提"].value == (
        projection["story_overview"]["premise"]
    )
    assert "林舟进入未知世界" in compiled_by_label["前三章"].value
    assert "普通生活起点" not in compiled.finding


def test_program_keeps_complete_long_1_4_description() -> None:
    answer, projection, opening_sources, evidence_by_id = (
        _validated_1_4_answer()
    )
    long_description = (
        "这段简介明确交代主角将被卷入未知世界，并需要在现实行动中"
        "面对异常机制和主要矛盾。"
    ) * 6
    opening_sources["description_text"] = long_description
    output = LearningReportOutput(
        answers=[answer],
        author_decisions=[],
        method_candidates=[],
    )

    _validate_selected_answers_against_projection(
        output,
        projection,
        opening_promise_sources=opening_sources,
        evidence_by_id=evidence_by_id,
    )

    compiled = next(
        item
        for item in output.answers[0].contract_items
        if item.item_id == "opening_promise_sources"
    )
    description_metric = next(
        metric for metric in compiled.metrics
        if metric.label == "简介"
    )
    assert len(long_description) > 160
    assert description_metric.value == long_description


def test_1_4_prioritizes_story_overview_protagonist_material() -> None:
    projection = _projection(complete_specialized_ledgers=False)
    projection["story_overview"]["protagonist"] = "路明非"
    projection["characters"] = [
        {
            "name": "楚子航",
            "role": "PROTAGONIST",
            "evidence_ids": ["evd_chu"],
        },
        {
            "name": "路明非",
            "role": "PROTAGONIST",
            "evidence_ids": ["evd_lu"],
        },
    ]

    materials = _source_materials(projection, ("1.4",))
    priorities = {
        str(item.get("name")): priority
        for priority, kind, item in materials
        if kind == "protagonist"
    }

    assert priorities["路明非"] > priorities["楚子航"]


def test_program_uses_chapter_only_index_for_1_4_position() -> None:
    answer, projection, opening_sources, evidence_by_id = (
        _validated_1_4_answer()
    )
    evidence_by_id["evd_payoff"].source_unit.ordinal = 3
    output = LearningReportOutput(
        answers=[answer],
        author_decisions=[],
        method_candidates=[],
    )

    _validate_selected_answers_against_projection(
        output,
        projection,
        opening_promise_sources=opening_sources,
        evidence_by_id=evidence_by_id,
        chapter_by_unit_id={
            "unit_2": {
                "ordinal": 2,
                "title": "第一幕 新世界",
            },
        },
    )

    payoff = next(
        item
        for item in output.answers[0].contract_items
        if item.item_id == "first_payoff_location"
    )
    metric_by_label = {
        metric.label: metric.value
        for metric in payoff.metrics
    }
    assert metric_by_label["首次兑现章节"] == "2"
    assert metric_by_label["承诺到兑现章距"] == "2"


def test_program_compiles_1_4_position_instead_of_trusting_model_number() -> None:
    answer, projection, opening_sources, evidence_by_id = (
        _validated_1_4_answer()
    )
    payload = answer.model_dump(mode="json")
    payoff_item = next(
        item
        for item in payload["contract_items"]
        if item["item_id"] == "first_payoff_location"
    )
    paragraph_metric = next(
        metric
        for metric in payoff_item["metrics"]
        if metric["label"] == "兑现段落"
    )
    paragraph_metric["value"] = "5"

    output = LearningReportOutput(
        answers=[LearningAnswerProposal.model_validate(payload)],
        author_decisions=[],
        method_candidates=[],
    )
    _validate_selected_answers_against_projection(
        output,
        projection,
        opening_promise_sources=opening_sources,
        evidence_by_id=evidence_by_id,
    )

    compiled_payoff = next(
        item
        for item in output.answers[0].contract_items
        if item.item_id == "first_payoff_location"
    )
    compiled_paragraph = next(
        metric
        for metric in compiled_payoff.metrics
        if metric.label == "兑现段落"
    )
    assert compiled_paragraph.value == "4"


def test_program_rejects_1_4_classification_that_skips_earlier_candidates() -> None:
    answer, projection, opening_sources, evidence_by_id = (
        _validated_1_4_answer()
    )
    payload = answer.model_dump(mode="json")
    payoff_item = next(
        item
        for item in payload["contract_items"]
        if item["item_id"] == "first_payoff_location"
    )
    payoff_item["payoff_classifications"][0]["sequence_no"] = 2

    with pytest.raises(ValueError) as error:
        _validate_selected_answers_against_projection(
            LearningReportOutput(
                answers=[LearningAnswerProposal.model_validate(payload)],
                author_decisions=[],
                method_candidates=[],
            ),
            projection,
            opening_promise_sources=opening_sources,
            evidence_by_id=evidence_by_id,
        )

    assert str(error.value) == (
        "LEARNING_REPORT_1_4_PAYOFF_CLASSIFICATION_COVERAGE_INVALID"
    )


def test_program_chooses_first_complete_1_4_classification() -> None:
    answer, projection, opening_sources, evidence_by_id = (
        _validated_1_4_answer()
    )
    projection["events"].append({
        "id": "evt_late",
        "title": "更晚的强场面",
        "summary": "更晚的强场面也完整展示同一承诺。",
        "narrative_mode": "ACTUAL",
        "start_char": 200,
        "chapter_ordinals": [3],
        "evidence_ids": ["evd_late"],
    })
    evidence_by_id["evd_late"] = SimpleNamespace(
        id="evd_late",
        paragraph_index=8,
        start_char=200,
        end_char=240,
        source_unit_id="unit_3",
        text_snapshot="更晚的强场面也完整展示同一承诺。",
        source_unit=SimpleNamespace(title="第二幕 更强场面"),
    )
    payload = answer.model_dump(mode="json")
    payoff_item = next(
        item
        for item in payload["contract_items"]
        if item["item_id"] == "first_payoff_location"
    )
    payoff_item["payoff_classifications"].append({
        "sequence_no": 2,
        "matched_facet_ids": ["F1", "F2", "F3"],
        "exclusion_code": "NONE",
        "anchor_evidence_no": 1,
    })
    output = LearningReportOutput(
        answers=[LearningAnswerProposal.model_validate(payload)],
        author_decisions=[],
        method_candidates=[],
    )

    _validate_selected_answers_against_projection(
        output,
        projection,
        opening_promise_sources=opening_sources,
        evidence_by_id=evidence_by_id,
    )

    payoff = next(
        item
        for item in output.answers[0].contract_items
        if item.item_id == "first_payoff_location"
    )
    assert payoff.evidence_ids == ["evd_payoff"]
    assert "第一幕 新世界" in payoff.finding
    assert "第二幕 更强场面" not in payoff.finding


def test_program_excludes_non_scene_f1_from_opening_promise_sources() -> None:
    answer, projection, opening_sources, evidence_by_id = (
        _validated_1_4_answer()
    )
    projection["events"].insert(0, {
        "id": "evt_oral",
        "title": "导师口述异常世界",
        "summary": "导师只用语言说明异常世界和任务。",
        "narrative_mode": "ACTUAL",
        "start_char": 50,
        "chapter_ordinals": [1],
        "evidence_ids": ["evd_oral"],
    })
    evidence_by_id["evd_oral"] = SimpleNamespace(
        id="evd_oral",
        paragraph_index=1,
        start_char=50,
        end_char=80,
        source_unit_id="unit_1",
        text_snapshot="导师说世界存在异常力量，主角将承担任务。",
        source_unit=SimpleNamespace(title="第一章 口述"),
    )
    payload = answer.model_dump(mode="json")
    payoff_item = next(
        item
        for item in payload["contract_items"]
        if item["item_id"] == "first_payoff_location"
    )
    payoff_item["payoff_classifications"] = [
        {
            "sequence_no": 1,
            "matched_facet_ids": ["F1", "F2"],
            "exclusion_code": "PARTIAL_ONLY",
            "anchor_evidence_no": 1,
        },
        {
            "sequence_no": 2,
            "matched_facet_ids": ["F1", "F2", "F3"],
            "exclusion_code": "NONE",
            "anchor_evidence_no": 1,
        },
    ]
    output = LearningReportOutput(
        answers=[LearningAnswerProposal.model_validate(payload)],
        author_decisions=[],
        method_candidates=[],
    )

    _validate_selected_answers_against_projection(
        output,
        projection,
        opening_promise_sources=opening_sources,
        evidence_by_id=evidence_by_id,
    )

    promise_item = next(
        item
        for item in output.answers[0].contract_items
        if item.item_id == "opening_promise_sources"
    )
    opening_metric = next(
        metric for metric in promise_item.metrics
        if metric.label == "前三章"
    )
    assert opening_metric.evidence_ids == ["evd_oral", "evd_payoff"]
    assert "导师口述异常世界" in opening_metric.value


def test_program_validation_rejects_wrong_4_9_type_count_by_label() -> None:
    answer, projection = _validated_4_9_answer()
    payload = answer.model_dump(mode="json")
    type_item = next(
        item
        for item in payload["contract_items"]
        if item["item_id"] == "type_distribution"
    )
    crisis_metric = next(
        metric
        for metric in type_item["metrics"]
        if metric["label"] == "CRISIS_SUSPENSION"
    )
    crisis_metric["value"] = "0"

    with pytest.raises(ValueError) as error:
        _validate_selected_answers_against_projection(
            LearningReportOutput(
                answers=[LearningAnswerProposal.model_validate(payload)],
                author_decisions=[],
                method_candidates=[],
            ),
            projection,
        )

    assert str(error.value) == "LEARNING_REPORT_4_9_TYPE_DISTRIBUTION_INVALID"


def test_program_replaces_unverified_4_9_top_level_numbers() -> None:
    answer, projection = _validated_4_9_answer()
    answer.conclusion = "错误地声称全书共有 99 章。"
    answer.metrics[0].value = "99"

    _apply_program_4_9_answer(
        answer,
        projection["chapter_end_hooks_evidence"],
    )

    metric_values = {
        metric.label: metric.value
        for metric in answer.metrics
    }
    assert "99" not in answer.conclusion
    assert answer.conclusion.startswith(
        "全书 2 章由 1 个连续窗口不重不漏覆盖"
    )
    assert metric_values["全书章节"] == "2"
    assert metric_values["危机悬置"] == "1"
    assert metric_values["最长连续强钩"] == "1"
    assert metric_values["无钩章"] == "1"


def test_program_compiles_4_9_before_final_text_boundary_checks() -> None:
    answer, projection = _validated_4_9_answer()
    payload = answer.model_dump(mode="json")
    payload["reusable_lessons"] = ["这种节律会提升读者留存率。"]
    payload["contract_items"][0]["finding"] = (
        "这种覆盖方式会避免读者疲劳。"
    )

    output = parse_learning_report(
        {
            "answers": [payload],
            "author_decisions": [],
            "method_candidates": [],
        },
        expected_question_ids=["4.9"],
        defer_user_text_checks_for={"4.9"},
    )
    compiled = output.answers[0]
    _apply_program_4_9_answer(
        compiled,
        projection["chapter_end_hooks_evidence"],
    )

    _validate_answer_user_text_boundaries(compiled)

    assert [item.item_id for item in compiled.contract_items] == [
        "chapter_coverage",
        "type_distribution",
        "strength_rhythm",
        "type_rotation",
        "no_hook_analysis",
        "representative_examples",
        "scope_boundary",
    ]
    compiled_text = "".join([
        compiled.conclusion,
        *compiled.reusable_lessons,
        *(item.finding for item in compiled.contract_items),
    ])
    assert "读者留存" not in compiled_text
    assert "读者疲劳" not in compiled_text


def test_program_validation_rejects_4_9_response_distance_metric() -> None:
    answer, projection = _validated_4_9_answer()
    payload = answer.model_dump(mode="json")
    scope_item = next(
        item
        for item in payload["contract_items"]
        if item["item_id"] == "scope_boundary"
    )
    scope_item["metrics"] = [{
        "label": "回应距离",
        "value": "1",
        "unit": "章",
        "method": "错误地追踪后续回应。",
        "evidence_ids": [],
    }]

    with pytest.raises(ValueError) as error:
        _validate_selected_answers_against_projection(
            LearningReportOutput(
                answers=[LearningAnswerProposal.model_validate(payload)],
                author_decisions=[],
                method_candidates=[],
            ),
            projection,
        )

    assert str(error.value) == "LEARNING_REPORT_4_9_RESPONSE_METRIC_OUT_OF_SCOPE"


def test_parser_rejects_2_1_that_only_returns_counts() -> None:
    counts_only = _answer("2.1")
    counts_only["contract_items"][0]["item_id"] = "opening_character_counts"

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


def test_parser_rejects_unsupported_external_reader_causality() -> None:
    answer = _answer("1.4")
    answer["do_not_copy"] = ["这种写法极易导致早期读者流失。"]

    with pytest.raises(LearningReportValidationError) as error:
        parse_learning_report(
            {
                "answers": [answer],
                "author_decisions": [],
                "method_candidates": [],
            },
            expected_question_ids=["1.4"],
        )

    assert (
        error.value.code
        == "LEARNING_REPORT_EXTERNAL_CAUSALITY_UNSUPPORTED"
    )


def test_parser_allows_external_effect_only_as_unverified_boundary() -> None:
    answer = _answer("1.4")
    answer["limitations"] = [
        "是否造成读者流失需要平台追读数据验证。"
    ]

    output = parse_learning_report(
        {
            "answers": [answer],
            "author_decisions": [],
            "method_candidates": [],
        },
        expected_question_ids=["1.4"],
    )

    assert output.answers[0].question_id == "1.4"


def test_parser_rejects_invented_external_effect_magnitude() -> None:
    answer = _answer("1.4")
    answer["do_not_copy"] = [
        "这种写法存在较大概率的弃书风险，需待市场数据验证。"
    ]

    with pytest.raises(LearningReportValidationError) as error:
        parse_learning_report(
            {
                "answers": [answer],
                "author_decisions": [],
                "method_candidates": [],
            },
            expected_question_ids=["1.4"],
        )

    assert (
        error.value.code
        == "LEARNING_REPORT_EXTERNAL_CAUSALITY_UNSUPPORTED"
    )


def test_parser_rejects_unverified_reader_trust_or_resonance_effect() -> None:
    answer = _answer("1.4")
    answer["reusable_lessons"] = [
        "这种铺垫有助于建立读者信任与共鸣。"
    ]

    with pytest.raises(LearningReportValidationError) as error:
        parse_learning_report(
            {
                "answers": [answer],
                "author_decisions": [],
                "method_candidates": [],
            },
            expected_question_ids=["1.4"],
        )

    assert (
        error.value.code
        == "LEARNING_REPORT_EXTERNAL_CAUSALITY_UNSUPPORTED"
    )
