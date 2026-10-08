from __future__ import annotations

import pytest

from app.services.learning_report import (
    LEARNING_QUESTION_ITEM_CONTRACTS,
    LearningReportValidationError,
    parse_learning_report,
)


def _metric() -> dict[str, object]:
    return {
        "label": "已核验章节",
        "value": "2",
        "unit": "章",
        "method": "按专项账本统计。",
        "evidence_ids": ["evd_text_boundary"],
    }


def _answer_4_9() -> dict[str, object]:
    contract_items = []
    for definition in LEARNING_QUESTION_ITEM_CONTRACTS["4.9"]:
        contract_items.append({
            "item_id": definition.item_id,
            "status": "SUPPORTED",
            "finding": (
                "4.9 不追踪后续回应；前三章首次回应属于 3.4，"
                "重要悬念生命周期属于 4.10。"
                if definition.item_id == "scope_boundary"
                else f"{definition.label}已由专项账本支持。"
            ),
            "metrics": [],
            "evidence_ids": (
                []
                if definition.item_id == "scope_boundary"
                else ["evd_text_boundary"]
            ),
            "limitations": [],
            "classifications": [],
        })
    return {
        "question_id": "4.9",
        "status": "ANSWERED",
        "conclusion": "全书章末钩类型和强弱节律已完成统计。",
        "metrics": [_metric()],
        "evidence_ids": ["evd_text_boundary"],
        "counter_evidence_ids": [],
        "limitations": ["当前结论只覆盖这一本书。"],
        "reusable_lessons": ["章末类型和强度应分别统计。"],
        "do_not_copy": ["不能照搬原书的具体章末表达。"],
        "contract_items": contract_items,
    }


def _parse(answer: dict[str, object]):
    return parse_learning_report(
        {
            "answers": [answer],
            "author_decisions": [],
            "method_candidates": [],
        },
        expected_question_ids=["4.9"],
    )


@pytest.mark.parametrize(
    "pricing_text",
    [
        "价格为 0.1。",
        "本次接口按次收费。",
        "本次调用花了 1 块钱。",
        "成本约为十块钱。",
        "每百万 Token 收 2 块。",
        "单价使用美元表示。",
    ],
)
def test_pricing_context_variants_are_rejected(pricing_text: str) -> None:
    answer = _answer_4_9()
    answer["limitations"] = [pricing_text]

    with pytest.raises(LearningReportValidationError) as error:
        _parse(answer)

    assert error.value.code == "LEARNING_REPORT_PRICING_OUT_OF_SCOPE"


def test_non_monetary_story_cost_language_is_allowed() -> None:
    answer = _answer_4_9()
    answer["limitations"] = ["主角为能力付出代价，事件铺垫花费三章。"]

    output = _parse(answer)

    assert output.answers[0].question_id == "4.9"


@pytest.mark.parametrize(
    "claim",
    [
        "现有数据无法证明销量，但这种写法会提升读者留存率。",
        "现有数据无法证明销量；这种写法会提升读者留存率。",
        "现有数据无法证明销量，写法会提升读者留存率。",
        "现有数据无法证明销量，读者留存率会提升。",
    ],
)
def test_external_effect_disclaimer_does_not_cover_another_claim(
    claim: str,
) -> None:
    answer = _answer_4_9()
    answer["reusable_lessons"] = [claim]

    with pytest.raises(LearningReportValidationError) as error:
        _parse(answer)

    assert (
        error.value.code
        == "LEARNING_REPORT_EXTERNAL_CAUSALITY_UNSUPPORTED"
    )


@pytest.mark.parametrize(
    "qualified_observation",
    [
        "这种结构是否提升读者留存，需要平台数据验证。",
        "这种结构对读者留存的影响，现有数据无法证明。",
        "缺少读者留存数据，不能外推实际效果。",
    ],
)
def test_external_effect_qualification_stays_with_its_claim(
    qualified_observation: str,
) -> None:
    answer = _answer_4_9()
    answer["limitations"] = [qualified_observation]

    output = _parse(answer)

    assert output.answers[0].question_id == "4.9"


def test_in_story_resonance_is_not_treated_as_reader_effect() -> None:
    answer = _answer_4_9()
    answer["reusable_lessons"] = [
        "书内观察是两股龙血力量在现场形成共鸣。"
    ]

    output = _parse(answer)

    assert output.answers[0].question_id == "4.9"


def test_audience_resonance_claim_is_rejected_without_platform_data() -> None:
    answer = _answer_4_9()
    answer["reusable_lessons"] = ["这种节律会形成受众共鸣。"]

    with pytest.raises(LearningReportValidationError) as error:
        _parse(answer)

    assert (
        error.value.code
        == "LEARNING_REPORT_EXTERNAL_CAUSALITY_UNSUPPORTED"
    )


@pytest.mark.parametrize(
    "reader_effect",
    [
        "这一危机会迫使读者立即翻页。",
        "这个身份缺口会激发读者好奇。",
        "关系转折会吸引读者继续阅读。",
        "读者必须继续阅读才能知道结果。",
        "读者会极其渴望知道钥匙的用途。",
    ],
)
def test_reader_behavior_or_psychology_claim_is_rejected(
    reader_effect: str,
) -> None:
    answer = _answer_4_9()
    answer["reusable_lessons"] = [reader_effect]

    with pytest.raises(LearningReportValidationError) as error:
        _parse(answer)

    assert (
        error.value.code
        == "LEARNING_REPORT_EXTERNAL_CAUSALITY_UNSUPPORTED"
    )


def test_reader_behavior_question_is_allowed_when_marked_unverified() -> None:
    answer = _answer_4_9()
    answer["limitations"] = [
        "这种结构是否会促使读者翻页，需要平台行为数据验证。"
    ]

    output = _parse(answer)

    assert output.answers[0].question_id == "4.9"


def _put_response_distance(
    answer: dict[str, object],
    location: str,
) -> None:
    text = "危机钩在第 4 章回应，回应距离为 1 章。"
    if location == "conclusion":
        answer["conclusion"] = text
    elif location == "limitation":
        answer["limitations"] = [text]
    elif location == "reusable_lesson":
        answer["reusable_lessons"] = [text]
    elif location == "do_not_copy":
        answer["do_not_copy"] = [text]
    elif location == "contract_finding":
        answer["contract_items"][0]["finding"] = text
    elif location == "contract_limitation":
        answer["contract_items"][0]["limitations"] = [text]
    elif location.startswith("contract_metric_"):
        metric = _metric()
        metric[location.removeprefix("contract_metric_")] = text
        answer["contract_items"][0]["metrics"] = [metric]
    else:
        answer["metrics"][0][location.removeprefix("answer_metric_")] = text


@pytest.mark.parametrize(
    "location",
    [
        "conclusion",
        "limitation",
        "reusable_lesson",
        "do_not_copy",
        "contract_finding",
        "contract_limitation",
        "contract_metric_label",
        "contract_metric_value",
        "contract_metric_unit",
        "contract_metric_method",
        "answer_metric_label",
        "answer_metric_value",
        "answer_metric_unit",
        "answer_metric_method",
    ],
)
def test_4_9_response_distance_is_rejected_in_all_user_text(
    location: str,
) -> None:
    answer = _answer_4_9()
    _put_response_distance(answer, location)

    with pytest.raises(LearningReportValidationError) as error:
        _parse(answer)

    assert (
        error.value.code
        == "LEARNING_REPORT_4_9_RESPONSE_METRIC_OUT_OF_SCOPE"
    )


@pytest.mark.parametrize(
    "boundary",
    [
        "4.9 不统计回应距离；该项由 3.4 处理。",
        "回应距离不在 4.9 职责范围内，由 3.4 负责。",
        "不得把兑现距离作为 4.9 的指标，重要悬念由 4.10 处理。",
    ],
)
def test_4_9_negative_scope_boundary_is_allowed(boundary: str) -> None:
    answer = _answer_4_9()
    answer["limitations"] = [boundary]

    output = _parse(answer)

    assert output.answers[0].question_id == "4.9"


def test_4_9_scope_denial_does_not_cover_a_later_distance_claim() -> None:
    answer = _answer_4_9()
    answer["limitations"] = [
        "4.9 不统计回应距离，但本书回应距离为 1 章。"
    ]

    with pytest.raises(LearningReportValidationError) as error:
        _parse(answer)

    assert (
        error.value.code
        == "LEARNING_REPORT_4_9_RESPONSE_METRIC_OUT_OF_SCOPE"
    )
