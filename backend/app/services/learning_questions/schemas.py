from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

from .contracts import OPENING_PAYOFF_MAX_WINDOW_CANDIDATES


class LearningMetricProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=160)
    value: str = Field(min_length=1, max_length=2000)
    unit: str = Field(default="", max_length=60)
    method: str = Field(min_length=1, max_length=500)
    evidence_ids: list[str] = Field(default_factory=list, max_length=16)


class LearningContractClassificationProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence_no: int = Field(ge=1, le=10_000)
    category: Literal[
        "主角锚点",
        "关系锚点",
        "引路或导师",
        "盟友或协作者",
        "对手或竞争者",
        "威胁或施压者",
        "权威或规则执行者",
        "信息提供者",
        "世界展示载体",
        "调剂或喜剧功能",
        "背景行动者",
        "其他",
    ]


class LearningPayoffClassificationProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence_no: int = Field(ge=1)
    matched_facet_ids: list[Literal["F1", "F2", "F3"]] = Field(
        default_factory=list,
        max_length=3,
    )
    exclusion_code: Literal[
        "NONE",
        "PROMISE_SOURCE",
        "PROMISE_RESTATEMENT",
        "IDENTITY_GRANT",
        "DREAM_OR_HISTORY",
        "STATIC_EVIDENCE",
        "SIMULATION",
        "PARTIAL_ONLY",
        "UNRELATED",
    ]
    anchor_evidence_no: int = Field(ge=1, le=16)


class LearningContractItemProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=1, max_length=80)
    status: Literal["SUPPORTED", "INSUFFICIENT_EVIDENCE"]
    finding: str = Field(min_length=1, max_length=800)
    metrics: list[LearningMetricProposal] = Field(default_factory=list, max_length=20)
    evidence_ids: list[str] = Field(default_factory=list, max_length=32)
    limitations: list[str] = Field(default_factory=list, max_length=8)
    classifications: list[LearningContractClassificationProposal] = Field(
        default_factory=list,
        max_length=200,
    )
    payoff_classifications: list[LearningPayoffClassificationProposal] = Field(
        default_factory=list,
        max_length=OPENING_PAYOFF_MAX_WINDOW_CANDIDATES,
    )


class LearningCommonError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mistake: str = Field(min_length=1, max_length=300)
    fix: str = Field(min_length=1, max_length=300)


class LearningTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    structure: str = Field(min_length=1, max_length=800)
    checkpoints: list[str] = Field(default_factory=list, max_length=4)


class LearningHandbook(BaseModel):
    model_config = ConfigDict(extra="forbid")

    why_important: str = Field(min_length=1, max_length=300)
    universal_methods: list[str] = Field(default_factory=list, max_length=6)
    checklist: list[str] = Field(default_factory=list, max_length=8)
    common_errors: list[LearningCommonError] = Field(default_factory=list, max_length=6)
    templates: list[LearningTemplate] = Field(default_factory=list, max_length=2)
    genre_note: str = Field(default="", max_length=200)


class LearningAnswerProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(pattern=r"^[1-8]\.[0-9]+$")
    status: Literal["ANSWERED", "PARTIAL", "INSUFFICIENT_EVIDENCE"]
    conclusion: str = Field(min_length=1, max_length=1800)
    metrics: list[LearningMetricProposal] = Field(default_factory=list, max_length=20)
    evidence_ids: list[str] = Field(default_factory=list, max_length=24)
    counter_evidence_ids: list[str] = Field(default_factory=list, max_length=16)
    limitations: list[str] = Field(min_length=1, max_length=10)
    reusable_lessons: list[str] = Field(default_factory=list, max_length=8)
    do_not_copy: list[str] = Field(min_length=1, max_length=8)
    contract_items: list[LearningContractItemProposal] = Field(
        default_factory=list,
        max_length=16,
    )
    handbook: LearningHandbook | None = None


class AuthorDecisionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=180)
    likely_timing: Literal["BEFORE_WRITING", "EARLY_SERIALIZATION", "LATER_GROWTH", "UNKNOWN"]
    inference: str = Field(min_length=1, max_length=1200)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    limitations: list[str] = Field(min_length=1, max_length=8)
    confidence: int = Field(ge=0, le=100)


class MethodCandidateProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=180)
    mechanism: str = Field(min_length=1, max_length=1200)
    observed_result: str = Field(min_length=1, max_length=1000)
    applicability: list[str] = Field(min_length=1, max_length=8)
    risks: list[str] = Field(min_length=1, max_length=8)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    do_not_copy: str = Field(min_length=1, max_length=800)
    verification_scope: Literal["SINGLE_BOOK_PENDING"] = "SINGLE_BOOK_PENDING"


class LearningReportOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answers: list[LearningAnswerProposal] = Field(min_length=1, max_length=5)
    author_decisions: list[AuthorDecisionProposal] = Field(default_factory=list, max_length=20)
    method_candidates: list[MethodCandidateProposal] = Field(default_factory=list, max_length=20)


@dataclass(frozen=True, slots=True)
class PersistedLearningReport:
    report_id: str


class LearningReportValidationError(ValueError):
    def __init__(self, code: str, errors: list[dict[str, Any]]) -> None:
        super().__init__(code)
        self.code = code
        self.errors = errors


class LearningReportNotReadyError(ValueError):
    def __init__(self, readiness: dict[str, object]) -> None:
        super().__init__(f"LEARNING_REPORT_NOT_READY: {readiness.get('gaps')}")
        self.readiness = readiness
