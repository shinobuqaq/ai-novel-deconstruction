from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    AnalysisRun,
    EvidenceSpan,
    LearningQuestionEvidence,
    SourceUnit,
)


# ==============================================================================
# 1. 统一文本清洗与废料正则 (Unified Text Cleaning & Boilerplate Normalization)
# ==============================================================================

TRAILING_BOILERPLATE_REGEX = re.compile(
    r"(?:https?://|www\.|\.com\b|\.net\b|"
    r"更多精彩|更多好书|请看小说网|txt\d*\.com|"
    r"声明[：:]?本书|本站只提供|用户上传|免费下载服务|版权.*无任何关系|"
    r"^\s*(?:"
    r"[【\[\(（]?\s*(?:THE\s+)?END\s*[】\]\)）]?|"
    r"[【\[\(（]?\s*(?:全文|全书)?完(?:结)?\s*[】\]\)）]?"
    r")\s*[。.!！]?\s*$)",
    re.IGNORECASE,
)


def is_trailing_boilerplate(text: str) -> bool:
    """Check whether a line or block of text is trailing boilerplate or disclaimer."""
    return bool(TRAILING_BOILERPLATE_REGEX.search(text or ""))


def normalize_narrative_text(value: object) -> str:
    """Normalize narrative text by stripping all whitespaces and lowercasing."""
    return re.sub(r"\s+", "", str(value or "")).casefold()


def normalize_without_terminal_punctuation(value: object) -> str:
    """Normalize text and strip trailing Chinese/English terminal punctuation."""
    return normalize_narrative_text(value).rstrip("。！？!?；;，,")


def extract_grounding_text(value: object) -> str:
    """Extract Chinese and alphanumeric characters for strict grounding verification."""
    return re.sub(
        r"[^0-9a-z\u4e00-\u9fff]+",
        "",
        str(value or "").casefold(),
    )


def extract_overlap_ngrams(
    value: object,
    n_sizes: tuple[int, ...] = (2, 3, 4),
) -> set[str]:
    """Extract character n-grams from text for fast overlap matching."""
    normalized = extract_grounding_text(value)
    return {
        normalized[index : index + size]
        for size in n_sizes
        for index in range(max(0, len(normalized) - size + 1))
    }


def calculate_ngram_overlap_ratio(
    claim_ngrams: set[str],
    source_ngrams: set[str],
) -> float:
    """Calculate overlap ratio between two sets of ngrams relative to shorter set."""
    if not claim_ngrams or not source_ngrams:
        return 0.0
    shorter_count = min(len(claim_ngrams), len(source_ngrams))
    if shorter_count <= 0:
        return 0.0
    overlap_count = len(claim_ngrams.intersection(source_ngrams))
    return overlap_count / shorter_count


# ==============================================================================
# 2. 叙事场景树数据模型 (Narrative Scene Tree Domain Models)
# ==============================================================================

class SceneHookData(BaseModel):
    """Chapter-end or scene-end suspense hook data."""
    model_config = ConfigDict(extra="ignore")

    chapter_ordinal: int = Field(ge=1)
    hook_type: Literal[
        "CRISIS_SUSPENSION",
        "NEW_INFORMATION",
        "PAYOFF_PRIMING",
        "REVERSAL",
        "EMOTIONAL_FREEZE",
        "NONE",
    ]
    strength: Literal["STRONG", "MEDIUM", "LIGHT", "NONE"]
    hook_question: str = ""
    rationale: str = ""
    retention_basis: str = ""
    ending_evidence_id: str | None = None


class ScenePayoffData(BaseModel):
    """Resolution and payoff tracking for an earlier narrative promise/hook."""
    model_config = ConfigDict(extra="ignore")

    hook_chapter: int = Field(ge=1)
    result: Literal[
        "FOUND_COMPLETE",
        "FOUND_PARTIAL",
        "NOT_FOUND_IN_WINDOW",
        "NO_HOOK",
    ]
    response_evidence_id: str | None = None
    response_summary: str = ""
    rationale: str = ""


class SceneCharacterSignal(BaseModel):
    """Character desire, boundary, contrast, or capability signal in a scene."""
    model_config = ConfigDict(extra="ignore")

    field: Literal[
        "surface_desire",
        "deep_desire",
        "motivation",
        "contrast",
        "boundary",
        "core_ability",
    ]
    status: Literal["SUPPORTED", "INSUFFICIENT_EVIDENCE"] = "SUPPORTED"
    value: str = ""
    explanation: str = ""
    first_display_chapter_ordinal: int | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class SceneSegment(BaseModel):
    """Fine-grained narrative beat / paragraph slice."""
    model_config = ConfigDict(extra="ignore")

    chapter_ordinal: int = Field(ge=1)
    paragraph_start: int = Field(ge=1)
    paragraph_end: int = Field(ge=1)
    scope: Literal["FRONT_MATTER", "STORY"] = "STORY"
    function: str = Field(default="叙事推进")
    pacing_intensity: int = Field(default=2, ge=1, le=5)
    information_modules: list[str] = Field(default_factory=list)
    explanation: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class SceneChapterNode(BaseModel):
    """Chapter-level narrative node containing segment beats and hooks."""
    model_config = ConfigDict(extra="ignore")

    chapter_ordinal: int = Field(ge=1)
    title: str = ""
    pacing_intensity: float = Field(default=2.0, ge=1.0, le=5.0)
    segments: list[SceneSegment] = Field(default_factory=list)
    chapter_end_hook: SceneHookData | None = None
    payoffs: list[ScenePayoffData] = Field(default_factory=list)
    character_signals: list[SceneCharacterSignal] = Field(default_factory=list)
    summary: str = ""


class NarrativeSceneTree(BaseModel):
    """Unified full-book Narrative Scene Tree consolidating 5 question streams."""
    model_config = ConfigDict(extra="ignore")

    run_id: str
    source_version_id: str
    total_chapters: int = 0
    chapter_nodes: list[SceneChapterNode] = Field(default_factory=list)
    opening_scene_card: dict[str, Any] | None = None
    overall_pacing_curve: list[float] = Field(default_factory=list)
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ==============================================================================
# 3. 统一节律计算与场景树构建器 (Pacing Engine & Tree Builder)
# ==============================================================================

def calculate_pacing_intensity_for_hook(
    hook_type: str,
    strength: str,
) -> float:
    """Calculate pacing tension bonus based on chapter-end hook severity."""
    base = 1.0
    if strength == "STRONG":
        base += 2.0
    elif strength == "MEDIUM":
        base += 1.2
    elif strength == "LIGHT":
        base += 0.5

    if hook_type in {"CRISIS_SUSPENSION", "REVERSAL"}:
        base += 1.0
    elif hook_type in {"NEW_INFORMATION", "PAYOFF_PRIMING"}:
        base += 0.5
    return min(5.0, max(1.0, base))


def build_narrative_scene_tree(
    session: Session,
    run_id: str,
) -> NarrativeSceneTree:
    """Construct the unified NarrativeSceneTree from existing versioned ledgers."""
    run = session.get(AnalysisRun, run_id)
    if run is None:
        return NarrativeSceneTree(run_id=run_id, source_version_id="", total_chapters=0)

    # 1. Fetch Source Units (Chapters)
    units = list(
        session.scalars(
            select(SourceUnit)
            .where(SourceUnit.source_version_id == run.source_version_id)
            .order_by(SourceUnit.ordinal)
        )
    )

    # 2. Fetch Latest Evidence Ledgers for the 5 consolidated questions
    # Q4.9: Chapter End Hooks
    # Q3.1: Opening Structure
    # Q3.4: Opening Hook Payoffs
    # Q2.2: Character Design
    ledgers = list(
        session.scalars(
            select(LearningQuestionEvidence)
            .where(
                LearningQuestionEvidence.run_id == run_id,
                LearningQuestionEvidence.question_id.in_(("4.9", "3.1", "3.4", "2.2")),
            )
            .order_by(
                LearningQuestionEvidence.question_id,
                LearningQuestionEvidence.revision_no.desc(),
            )
        )
    )
    latest_by_q: dict[str, LearningQuestionEvidence] = {}
    for ledger in ledgers:
        if ledger.question_id not in latest_by_q:
            latest_by_q[ledger.question_id] = ledger

    # Parse payloads safely
    hook_payload: dict[str, Any] = {}
    if "4.9" in latest_by_q:
        try:
            hook_payload = json.loads(latest_by_q["4.9"].payload_json)
        except Exception:
            hook_payload = {}

    opening_payload: dict[str, Any] = {}
    if "3.1" in latest_by_q:
        try:
            opening_payload = json.loads(latest_by_q["3.1"].payload_json)
        except Exception:
            opening_payload = {}

    payoff_payload: dict[str, Any] = {}
    if "3.4" in latest_by_q:
        try:
            payoff_payload = json.loads(latest_by_q["3.4"].payload_json)
        except Exception:
            payoff_payload = {}

    character_payload: dict[str, Any] = {}
    if "2.2" in latest_by_q:
        try:
            character_payload = json.loads(latest_by_q["2.2"].payload_json)
        except Exception:
            character_payload = {}

    # Map chapter-end hooks by chapter ordinal
    hooks_by_chapter: dict[int, SceneHookData] = {}
    for item in hook_payload.get("chapters", []):
        ch = item.get("chapter_ordinal")
        if ch:
            hooks_by_chapter[ch] = SceneHookData(
                chapter_ordinal=ch,
                hook_type=item.get("hook_type", "NONE"),
                strength=item.get("strength", "NONE"),
                hook_question=item.get("hook_question", ""),
                rationale=item.get("rationale", ""),
                retention_basis=item.get("retention_basis", ""),
                ending_evidence_id=item.get("ending_evidence_id"),
            )

    # Map opening hook payoffs
    payoffs_by_chapter: dict[int, list[ScenePayoffData]] = {}
    for item in payoff_payload.get("results", []):
        ch = item.get("hook_chapter")
        if ch:
            payoffs_by_chapter.setdefault(ch, []).append(
                ScenePayoffData(
                    hook_chapter=ch,
                    result=item.get("result", "NOT_FOUND_IN_WINDOW"),
                    response_evidence_id=item.get("response_evidence_id"),
                    response_summary=item.get("response_summary", ""),
                    rationale=item.get("rationale", ""),
                )
            )

    # Map paragraph segments from opening structure
    segments_by_chapter: dict[int, list[SceneSegment]] = {}
    for seg in opening_payload.get("paragraph_segments", []):
        ch = seg.get("chapter_ordinal")
        if ch:
            segments_by_chapter.setdefault(ch, []).append(
                SceneSegment(
                    chapter_ordinal=ch,
                    paragraph_start=seg.get("paragraph_start", 1),
                    paragraph_end=seg.get("paragraph_end", 1),
                    scope=seg.get("scope", "STORY"),
                    function=seg.get("function", "叙事推进"),
                    information_modules=seg.get("information_modules", []),
                    explanation=seg.get("explanation", ""),
                    evidence_ids=seg.get("evidence_ids", []),
                )
            )

    # Map character signals
    character_signals: list[SceneCharacterSignal] = []
    for field_name, field_val in character_payload.get("fields", {}).items():
        if isinstance(field_val, dict) and field_val.get("status") == "SUPPORTED":
            character_signals.append(
                SceneCharacterSignal(
                    field=field_name,
                    status="SUPPORTED",
                    value=field_val.get("value", ""),
                    explanation=field_val.get("explanation", ""),
                    first_display_chapter_ordinal=field_val.get(
                        "first_display_chapter_ordinal"
                    ),
                    evidence_ids=field_val.get("evidence_ids", []),
                )
            )

    # 3. Assemble Chapter Nodes
    chapter_nodes: list[SceneChapterNode] = []
    pacing_curve: list[float] = []

    for unit in units:
        ch = unit.ordinal
        ch_hook = hooks_by_chapter.get(ch)
        ch_payoffs = payoffs_by_chapter.get(ch, [])
        ch_segs = segments_by_chapter.get(ch, [])

        # Filter character signals relevant to this chapter
        ch_signals = [
            sig
            for sig in character_signals
            if sig.first_display_chapter_ordinal == ch
        ]

        # Calculate chapter pacing intensity
        if ch_hook:
            intensity = calculate_pacing_intensity_for_hook(
                ch_hook.hook_type, ch_hook.strength
            )
        elif ch_segs:
            intensity = 2.5
        else:
            intensity = 2.0

        pacing_curve.append(round(intensity, 2))

        node = SceneChapterNode(
            chapter_ordinal=ch,
            title=unit.title or f"第 {ch} 章",
            pacing_intensity=intensity,
            segments=ch_segs,
            chapter_end_hook=ch_hook,
            payoffs=ch_payoffs,
            character_signals=ch_signals,
            summary="",
        )
        chapter_nodes.append(node)

    return NarrativeSceneTree(
        run_id=run.id,
        source_version_id=run.source_version_id,
        total_chapters=len(units),
        chapter_nodes=chapter_nodes,
        opening_scene_card=opening_payload.get("opening_scene"),
        overall_pacing_curve=pacing_curve,
    )


# ==============================================================================
# 4. 统一投影组装器 (Unified Projection Builder for Workbench)
# ==============================================================================

def build_unified_scene_and_pacing_projection(
    session: Session,
    run_id: str,
    base_projection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the unified scene & pacing projection while populating all 5 legacy keys.

    Zero-breaking guarantee:
    1. chapter_end_hooks_status & chapter_end_hooks_evidence
    2. opening_structure_status & opening_structure_evidence
    3. opening_hook_payoffs_status & opening_hook_payoffs_evidence
    4. opening_payoff_candidates_status & opening_payoff_candidates_evidence
    5. character_design_status & character_design_evidence
    + narrative_scene_tree (enhanced Gundam Part)
    """
    from .chapter_end_hooks import build_chapter_end_hooks_projection
    from .character_design import build_character_design_projection
    from .opening_hook_payoffs import build_opening_hook_payoffs_projection
    from .opening_payoff_candidates import build_opening_payoff_candidates_projection
    from .opening_structure import build_opening_structure_projection

    base = dict(base_projection or {})

    # 1. Character design
    cd_status, cd_evidence = build_character_design_projection(
        session, run_id, base
    )
    base["character_design_status"] = cd_status
    base["character_design_evidence"] = cd_evidence

    # 2. Opening payoff candidates
    opc_status, opc_evidence = build_opening_payoff_candidates_projection(
        session, run_id, base
    )
    base["opening_payoff_candidates_status"] = opc_status
    base["opening_payoff_candidates_evidence"] = opc_evidence

    # 3. Chapter end hooks
    ceh_status, ceh_evidence = build_chapter_end_hooks_projection(
        session, run_id, base
    )
    base["chapter_end_hooks_status"] = ceh_status
    base["chapter_end_hooks_evidence"] = ceh_evidence
    base["chapter_end_hooks"] = (
        ceh_evidence.get("chapters", []) if ceh_evidence else []
    )

    # 4. Opening hook payoffs
    ohp_status, ohp_evidence = build_opening_hook_payoffs_projection(
        session, run_id, base, hook_evidence=ceh_evidence
    )
    base["opening_hook_payoffs_status"] = ohp_status
    base["opening_hook_payoffs_evidence"] = ohp_evidence
    base["opening_hook_payoffs"] = (
        ohp_evidence.get("hooks", []) if ohp_evidence else []
    )

    # 5. Opening structure
    os_status, os_evidence = build_opening_structure_projection(
        session, run_id, base
    )
    base["opening_structure_status"] = os_status
    base["opening_structure_evidence"] = os_evidence

    # 6. Unified Narrative Scene Tree
    try:
        scene_tree = build_narrative_scene_tree(session, run_id)
        base["narrative_scene_tree"] = scene_tree.model_dump(mode="json")
    except Exception:
        base["narrative_scene_tree"] = None

    return base
