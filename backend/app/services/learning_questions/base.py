from __future__ import annotations

from typing import Any, Protocol, runtime_checkable
from .contracts import (
    LearningContractItemDefinition,
    LearningQuestionDefinition,
    _QUESTION_BY_ID,
    LEARNING_QUESTION_ITEM_CONTRACTS,
)
from .schemas import LearningAnswerProposal


@runtime_checkable
class QuestionPlugin(Protocol):
    question_id: str

    @property
    def definition(self) -> LearningQuestionDefinition:
        ...

    @property
    def contracts(self) -> tuple[LearningContractItemDefinition, ...]:
        ...

    @property
    def requires_projection(self) -> bool:
        ...

    @property
    def is_program_compiled(self) -> bool:
        ...

    def assess_readiness(self, projection: dict[str, Any]) -> dict[str, object]:
        ...

    def validate_answer(
        self,
        answer: LearningAnswerProposal,
        projection: dict[str, Any],
        errors: list[dict[str, Any]],
        **kwargs: Any,
    ) -> None:
        ...

    def apply_program_answer(
        self,
        report: Any,
        projection: dict[str, Any],
    ) -> LearningAnswerProposal | None:
        ...


class BaseQuestionPlugin:
    question_id: str = ""
    requires_projection: bool = False
    is_program_compiled: bool = False

    @property
    def definition(self) -> LearningQuestionDefinition:
        return _QUESTION_BY_ID[self.question_id]

    @property
    def contracts(self) -> tuple[LearningContractItemDefinition, ...]:
        return LEARNING_QUESTION_ITEM_CONTRACTS.get(self.question_id, ())

    def assess_readiness(self, projection: dict[str, Any]) -> dict[str, object]:
        return {
            "question_id": self.question_id,
            "ready": False,
            "observed": {},
            "gaps": ["未实现独立就绪检查。"],
            "required_artifact": "",
            "answer_scope": "NOT_READY",
        }

    def validate_answer(
        self,
        answer: LearningAnswerProposal,
        projection: dict[str, Any],
        errors: list[dict[str, Any]],
        **kwargs: Any,
    ) -> None:
        return

    def apply_program_answer(
        self,
        report: Any,
        projection: dict[str, Any],
    ) -> LearningAnswerProposal | None:
        return None
