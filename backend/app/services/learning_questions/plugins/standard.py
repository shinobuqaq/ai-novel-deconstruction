from __future__ import annotations

from typing import Any

from ..base import BaseQuestionPlugin
from ..schemas import LearningAnswerProposal


class StandardQuestionPlugin(BaseQuestionPlugin):
    """Generic plugin for questions evaluated via standard LLM synthesis without custom program ledgers."""

    def __init__(self, question_id: str):
        self.question_id = question_id

    def assess_readiness(self, projection: dict[str, Any]) -> dict[str, object]:
        chapter_count = int(projection.get("chapter_count") or 0)
        ready = chapter_count > 0
        return {
            "question_id": self.question_id,
            "ready": ready,
            "gaps": [] if ready else ["书籍缺少有效章节内容。"],
            "required_evidences": [],
            "answer_scope": "PARTIAL" if ready else "NOT_READY",
        }

    def validate_answer(
        self,
        answer: LearningAnswerProposal,
        projection: dict[str, Any],
        errors: list[dict[str, Any]],
        **kwargs: Any,
    ) -> None:
        pass

    def apply_program_answer(
        self,
        report: Any,
        projection: dict[str, Any],
    ) -> LearningAnswerProposal | None:
        return None
