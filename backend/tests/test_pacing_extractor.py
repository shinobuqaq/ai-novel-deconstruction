from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.models import (
    AnalysisRun,
    EvidenceSpan,
    LearningQuestionEvidence,
    SourceUnit,
)
from app.services.pacing_extractor import (
    NarrativeSceneTree,
    SceneChapterNode,
    SceneHookData,
    ScenePayoffData,
    SceneSegment,
    build_narrative_scene_tree,
    build_unified_scene_and_pacing_projection,
    calculate_ngram_overlap_ratio,
    calculate_pacing_intensity_for_hook,
    extract_grounding_text,
    extract_overlap_ngrams,
    is_trailing_boilerplate,
    normalize_narrative_text,
    normalize_without_terminal_punctuation,
)


def test_trailing_boilerplate_detection() -> None:
    assert is_trailing_boilerplate("请看小说网 txt88.com 免费下载")
    assert is_trailing_boilerplate("声明：本书仅供个人学习交流使用，版权归原作者所有")
    assert is_trailing_boilerplate("【全书完】")
    assert is_trailing_boilerplate("THE END")
    assert not is_trailing_boilerplate("林舟推开旧宅的木门，夜雨如注。")


def test_normalization_and_punctuation_strip() -> None:
    raw = "  主角 坚决 不退缩！。 "
    assert normalize_narrative_text(raw) == "主角坚决不退缩！。"
    assert normalize_without_terminal_punctuation(raw) == "主角坚决不退缩"


def test_ngram_grounding_and_overlap() -> None:
    claim = "林舟仔细查看"
    source = "桌上放着一封给林舟的密信，林舟仔细查看。"
    
    claim_ngrams = extract_overlap_ngrams(claim)
    source_ngrams = extract_overlap_ngrams(source)
    
    assert len(claim_ngrams) > 0
    ratio = calculate_ngram_overlap_ratio(claim_ngrams, source_ngrams)
    assert ratio >= 0.8


def test_calculate_pacing_intensity() -> None:
    crisis_strong = calculate_pacing_intensity_for_hook("CRISIS_SUSPENSION", "STRONG")
    none_intensity = calculate_pacing_intensity_for_hook("NONE", "NONE")
    
    assert crisis_strong >= 4.0
    assert none_intensity <= 1.5


def test_build_narrative_scene_tree_assembly() -> None:
    session = MagicMock()
    run_id = "run_test_pacing"

    # Mock AnalysisRun
    fake_run = AnalysisRun(
        id=run_id,
        source_version_id="svr_test_pacing",
    )
    session.get.return_value = fake_run

    # Mock chapters (SourceUnits)
    unit1 = SourceUnit(
        id="unt_1",
        source_version_id="svr_test_pacing",
        ordinal=1,
        title="第一章 雨夜旧宅",
    )
    unit2 = SourceUnit(
        id="unt_2",
        source_version_id="svr_test_pacing",
        ordinal=2,
        title="第二章 追踪迷雾",
    )

    # Mock Question Evidence Ledgers
    # 4.9 Chapter End Hooks
    hook_payload = {
        "chapters": [
            {
                "chapter_ordinal": 1,
                "hook_type": "CRISIS_SUSPENSION",
                "strength": "STRONG",
                "hook_question": "密信主人是否还在屋内？",
                "rationale": "门锁突然从外侧被转动",
                "retention_basis": "危险逼近",
                "ending_evidence_id": "evd_end_1",
            },
            {
                "chapter_ordinal": 2,
                "hook_type": "NONE",
                "strength": "NONE",
                "hook_question": "",
                "rationale": "调查告一段落",
                "retention_basis": "自然收束",
                "ending_evidence_id": "evd_end_2",
            },
        ]
    }
    ledger_4_9 = LearningQuestionEvidence(
        id="lqe_4_9",
        run_id=run_id,
        source_version_id="svr_test_pacing",
        question_id="4.9",
        revision_no=1,
        payload_json=json.dumps(hook_payload),
    )

    # 3.1 Opening Structure
    opening_payload = {
        "opening_scene": {
            "opening_type": "危机开局",
            "explanation": "开篇直接切入夜雨潜入",
        },
        "paragraph_segments": [
            {
                "chapter_ordinal": 1,
                "paragraph_start": 1,
                "paragraph_end": 5,
                "scope": "STORY",
                "function": "建立危机环境",
                "information_modules": ["主角困境", "威胁"],
                "explanation": "交代大雨与追兵",
                "evidence_ids": ["evd_p1"],
            }
        ],
    }
    ledger_3_1 = LearningQuestionEvidence(
        id="lqe_3_1",
        run_id=run_id,
        source_version_id="svr_test_pacing",
        question_id="3.1",
        revision_no=1,
        payload_json=json.dumps(opening_payload),
    )

    # 3.4 Opening Hook Payoffs
    payoff_payload = {
        "results": [
            {
                "hook_chapter": 1,
                "result": "FOUND_PARTIAL",
                "response_evidence_id": "evd_resp_1",
                "response_summary": "确认转动门把手的是守夜人",
                "rationale": "部分消解危机",
            }
        ]
    }
    ledger_3_4 = LearningQuestionEvidence(
        id="lqe_3_4",
        run_id=run_id,
        source_version_id="svr_test_pacing",
        question_id="3.4",
        revision_no=1,
        payload_json=json.dumps(payoff_payload),
    )

    # 2.2 Character Design
    character_payload = {
        "fields": {
            "surface_desire": {
                "status": "SUPPORTED",
                "value": "洗清被污蔑的冤屈",
                "explanation": "主角第一章多次提及",
                "first_display_chapter_ordinal": 1,
                "evidence_ids": ["evd_desire_1"],
            }
        }
    }
    ledger_2_2 = LearningQuestionEvidence(
        id="lqe_2_2",
        run_id=run_id,
        source_version_id="svr_test_pacing",
        question_id="2.2",
        revision_no=1,
        payload_json=json.dumps(character_payload),
    )

    def fake_scalars(stmt):
        stmt_str = str(stmt)
        if "source_units" in stmt_str:
            return iter([unit1, unit2])
        return iter([ledger_4_9, ledger_3_1, ledger_3_4, ledger_2_2])

    session.scalars.side_effect = fake_scalars

    scene_tree = build_narrative_scene_tree(session, run_id)

    assert scene_tree.total_chapters == 2
    assert len(scene_tree.chapter_nodes) == 2
    
    ch1 = scene_tree.chapter_nodes[0]
    assert ch1.chapter_ordinal == 1
    assert ch1.chapter_end_hook is not None
    assert ch1.chapter_end_hook.hook_type == "CRISIS_SUSPENSION"
    assert ch1.chapter_end_hook.strength == "STRONG"
    assert ch1.pacing_intensity >= 4.0
    assert len(ch1.segments) == 1
    assert ch1.segments[0].function == "建立危机环境"
    assert len(ch1.payoffs) == 1
    assert ch1.payoffs[0].result == "FOUND_PARTIAL"
    assert len(ch1.character_signals) == 1
    assert ch1.character_signals[0].value == "洗清被污蔑的冤屈"

    ch2 = scene_tree.chapter_nodes[1]
    assert ch2.chapter_ordinal == 2
    assert ch2.chapter_end_hook is not None
    assert ch2.chapter_end_hook.hook_type == "NONE"
    assert ch2.pacing_intensity <= 2.0


def test_build_unified_scene_and_pacing_projection_keys() -> None:
    session = MagicMock()
    run_id = "run_test_proj"

    # When ledgers are not yet generated, all projections should gracefully return NOT_GENERATED
    session.get.return_value = None
    session.scalars.return_value = iter([])
    session.scalar.return_value = None

    proj = build_unified_scene_and_pacing_projection(session, run_id)

    # Check that all 5 questions are present
    assert "character_design_status" in proj
    assert "character_design_evidence" in proj
    assert "opening_payoff_candidates_status" in proj
    assert "opening_payoff_candidates_evidence" in proj
    assert "chapter_end_hooks_status" in proj
    assert "chapter_end_hooks_evidence" in proj
    assert "chapter_end_hooks" in proj
    assert "opening_hook_payoffs_status" in proj
    assert "opening_hook_payoffs_evidence" in proj
    assert "opening_hook_payoffs" in proj
    assert "opening_structure_status" in proj
    assert "opening_structure_evidence" in proj
    assert "narrative_scene_tree" in proj
