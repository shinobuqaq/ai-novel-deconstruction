from __future__ import annotations

import pytest

from app.services.learning_questions.common import (
    LearningAnswerProposal,
    LearningReportValidationError,
    validate_answer_user_text_boundaries,
)
from test_learning_report_text_boundaries import _answer_4_9


def test_soft_annotation_mode_converts_causality_rejection_to_warnings() -> None:
    raw = _answer_4_9()
    raw["reusable_lessons"] = ["这种章末危机写法必然会大幅提高读者的留存率。"]
    answer = LearningAnswerProposal.model_validate(raw)

    # Strict mode: raises error
    with pytest.raises(LearningReportValidationError) as exc:
        validate_answer_user_text_boundaries(answer, soft_annotation=False)
    assert exc.value.code == "LEARNING_REPORT_EXTERNAL_CAUSALITY_UNSUPPORTED"

    # Soft mode: does not raise, returns structured soft annotations
    annotations = validate_answer_user_text_boundaries(answer, soft_annotation=True)
    assert len(annotations) > 0
    assert annotations[0]["type"] == "EXTERNAL_CAUSALITY_UNSUPPORTED"
    assert annotations[0]["confidence_penalty"] > 0
    assert "留存率" in annotations[0]["flagged_text"]


def test_soft_annotation_mode_still_strictly_forbids_pricing() -> None:
    raw = _answer_4_9()
    raw["reusable_lessons"] = ["本章字数较多，单价定为 0.1 元。"]
    answer = LearningAnswerProposal.model_validate(raw)

    with pytest.raises(LearningReportValidationError) as exc_strict:
        validate_answer_user_text_boundaries(answer, soft_annotation=False)
    assert exc_strict.value.code == "LEARNING_REPORT_PRICING_OUT_OF_SCOPE"

    with pytest.raises(LearningReportValidationError) as exc_soft:
        validate_answer_user_text_boundaries(answer, soft_annotation=True)
    assert exc_soft.value.code == "LEARNING_REPORT_PRICING_OUT_OF_SCOPE"


def test_uncertainty_qualified_observation_produces_no_annotations() -> None:
    raw = _answer_4_9()
    raw["limitations"] = ["这种结构对读者留存的影响，需要平台行为数据验证。"]
    answer = LearningAnswerProposal.model_validate(raw)

    annotations = validate_answer_user_text_boundaries(answer, soft_annotation=True)
    assert len(annotations) == 0
