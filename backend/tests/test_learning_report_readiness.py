from __future__ import annotations

from app.services.learning_report import (
    LEARNING_QUESTION_CATALOG,
    LEARNING_QUESTION_CONTRACTS,
    assess_learning_report_readiness,
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
                key: {"value": key, "evidence_ids": [f"evd_{key}"]}
                for key in (
                    "surface_desire",
                    "deep_desire",
                    "motivation",
                    "contrast",
                    "boundary",
                    "core_ability",
                )
            },
            "chapter_end_hooks": [
                {
                    "chapter_ordinal": ordinal,
                    "hook_type": "NONE",
                    "ending_evidence_ids": [f"evd_end_{ordinal}"],
                }
                for ordinal in range(1, 51)
            ],
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


def test_sparse_deep_analysis_does_not_unlock_online_report() -> None:
    readiness = assess_learning_report_readiness(
        _projection(complete_specialized_ledgers=False)
    )

    assert readiness["ready"] is False
    assert readiness["ready_question_count"] == 2
    checks = {item["question_id"]: item for item in readiness["checks"]}
    assert checks["1.4"]["ready"] is True
    assert checks["2.1"]["ready"] is True
    assert checks["2.2"]["ready"] is False
    assert checks["4.9"]["observed"]["generic_scene_analysis_count"] == 1
    assert checks["4.9"]["observed"]["valid_chapter_end_sample_count"] == 0
    assert checks["5.3"]["observed"]["generic_foreshadowing_count"] == 1
    assert readiness["next_required_artifacts"][0] == "主角双层欲望与最小完整集证据表"


def test_all_required_ledgers_unlock_prototype_batch() -> None:
    readiness = assess_learning_report_readiness(
        _projection(complete_specialized_ledgers=True)
    )

    assert readiness["ready"] is True
    assert readiness["ready_question_count"] == 7
    assert readiness["next_required_artifacts"] == []
