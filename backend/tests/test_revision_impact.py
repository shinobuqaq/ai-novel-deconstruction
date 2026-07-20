import pytest

from app.services.analysis import (
    _validate_deep_temporal_consistency,
    build_deep_revision_impact,
    deep_revision_scope,
    merge_targeted_narrative_payload,
    narrative_phase_id,
)
from app.services.tasks import _deep_consistency_message


def test_each_deep_workbench_target_has_a_bounded_revision_scope() -> None:
    expected = {
        "FACT": ["fact_versions", "state_changes", "actor_knowledge", "knowledge_transfers", "claims"],
        "STATE": ["fact_versions", "state_changes", "actor_knowledge", "knowledge_transfers", "claims"],
        "KNOWLEDGE": ["actor_knowledge", "knowledge_transfers", "claims"],
        "WORLD": ["fact_versions", "world_rules", "claims", "entity_resolutions"],
        "FORESHADOWING": ["foreshadowing", "claims"],
        "CONFLICT": ["conflicts", "claims"],
        "SCENE": ["scene_analysis", "claims"],
        "CLAIM": ["claims"],
    }

    for target_kind, collections in expected.items():
        assert deep_revision_scope([{"target_kind": target_kind}]) == collections


def test_direct_deep_problem_points_to_the_selected_item() -> None:
    previous = {
        "actor_knowledge": [
            {
                "id": "knowledge-1",
                "actor": "林舟",
                "proposition": "密信来自旧宅主人",
                "chapter_ordinal": 2,
                "evidence_ids": ["evidence-1"],
            }
        ],
        "claims": [
            {
                "id": "claim-1",
                "claim_kind": "INFERENCE",
                "claim_text": "林舟开始怀疑旧宅主人",
                "scope": "第二章",
                "evidence_ids": ["evidence-1"],
            }
        ],
    }

    impact = build_deep_revision_impact(
        [
            {
                "target_kind": "KNOWLEDGE",
                "target_id": "knowledge-1",
                "target_label": "林舟：密信来源认知",
            }
        ],
        previous,
    )

    assert impact["mode"] == "TARGETED"
    knowledge = next(
        section for section in impact["sections"] if section["key"] == "actor_knowledge"
    )
    assert knowledge["item_ids"] == ["knowledge-1"]
    assert knowledge["item_labels"] == ["林舟：密信来自旧宅主人"]
    claims = next(section for section in impact["sections"] if section["key"] == "claims")
    assert claims["item_ids"] == ["claim-1"]


def test_revision_impact_follows_a_multi_hop_state_and_claim_chain() -> None:
    previous = {
        "fact_versions": [
            {
                "id": "fact-1",
                "subject": "密信",
                "predicate": "状态",
                "value": "完整",
                "valid_from_chapter": 1,
                "evidence_ids": ["evidence-1"],
            }
        ],
        "state_changes": [
            {
                "id": "state-1",
                "subject": "密信",
                "aspect": "完整性",
                "after": "完整",
                "chapter_ordinal": 2,
                "event_id": "event-1",
                "evidence_ids": ["evidence-2"],
            }
        ],
        "actor_knowledge": [
            {
                "id": "knowledge-1",
                "actor": "林舟",
                "proposition": "纸灰来自一封重要书信",
                "chapter_ordinal": 3,
                "evidence_ids": ["evidence-2"],
            }
        ],
        "knowledge_transfers": [
            {
                "id": "transfer-1",
                "source_actor": "直接观察",
                "target_actor": "林舟",
                "proposition": "纸灰来自一封重要书信",
                "transfer_type": "WITNESSED",
                "resulting_state": "KNOWS",
                "chapter_ordinal": 3,
                "evidence_ids": ["evidence-2"],
            }
        ],
        "claims": [
            {
                "id": "claim-1",
                "claim_kind": "INFERENCE",
                "claim_text": "林舟开始追查书信来源",
                "scope": "第三章",
                "evidence_ids": ["evidence-3"],
            }
        ],
    }

    impact = build_deep_revision_impact(
        [{"target_kind": "FACT", "target_id": "fact-1", "target_label": "密信：状态"}],
        previous,
    )

    assert next(item for item in impact["sections"] if item["key"] == "fact_versions")["item_ids"] == ["fact-1"]
    assert next(item for item in impact["sections"] if item["key"] == "state_changes")["item_ids"] == ["state-1"]
    assert next(item for item in impact["sections"] if item["key"] == "actor_knowledge")["item_ids"] == ["knowledge-1"]
    assert next(item for item in impact["sections"] if item["key"] == "knowledge_transfers")["item_ids"] == ["transfer-1"]
    assert next(item for item in impact["sections"] if item["key"] == "claims")["item_ids"] == ["claim-1"]


def test_temporal_guard_rejects_future_evidence_and_same_chapter_conflicts() -> None:
    with pytest.raises(ValueError, match="FUTURE_EVIDENCE"):
        _validate_deep_temporal_consistency(
            {
                "state_changes": [{
                    "subject": "林舟",
                    "aspect": "目标",
                    "after": "追查",
                    "chapter_ordinal": 1,
                    "evidence_ids": ["evidence-2"],
                }]
            },
            {"evidence-2": 2},
        )

    with pytest.raises(ValueError, match="STATE_REPLAY_CONFLICT"):
        _validate_deep_temporal_consistency(
            {
                "state_changes": [
                    {"subject": "林舟", "aspect": "目标", "after": "追查", "chapter_ordinal": 1, "evidence_ids": ["evidence-1"]},
                    {"subject": "林舟", "aspect": "目标", "after": "逃离", "chapter_ordinal": 1, "evidence_ids": ["evidence-1"]},
                ]
            },
            {"evidence-1": 1},
        )

    with pytest.raises(ValueError, match="KNOWLEDGE_REPLAY_CONFLICT"):
        _validate_deep_temporal_consistency(
            {
                "actor_knowledge": [
                    {"actor": "林舟", "proposition": "密信存在", "state": "KNOWS", "chapter_ordinal": 2, "evidence_ids": ["evidence-1"]},
                    {"actor": "林舟", "proposition": "密信存在", "state": "UNKNOWN", "chapter_ordinal": 2, "evidence_ids": ["evidence-1"]},
                ]
            },
            {"evidence-1": 1},
        )

    with pytest.raises(ValueError, match="KNOWLEDGE_TRANSFER_RESULT_MISSING"):
        _validate_deep_temporal_consistency(
            {
                "knowledge_transfers": [
                    {
                        "source_actor": "直接观察",
                        "target_actor": "林舟",
                        "proposition": "密信存在",
                        "transfer_type": "WITNESSED",
                        "resulting_state": "KNOWS",
                        "chapter_ordinal": 2,
                        "evidence_ids": ["evidence-1"],
                    }
                ]
            },
            {"evidence-1": 1},
        )


def test_deep_consistency_errors_are_explained_in_plain_chinese() -> None:
    assert "后文章节" in _deep_consistency_message("DEEP_ANALYSIS_FUTURE_EVIDENCE_LEAK")
    assert "互相矛盾的状态" in _deep_consistency_message("DEEP_ANALYSIS_STATE_REPLAY_CONFLICT")
    assert "互相矛盾的认知" in _deep_consistency_message("DEEP_ANALYSIS_KNOWLEDGE_REPLAY_CONFLICT")
    assert "传播过程" in _deep_consistency_message("DEEP_ANALYSIS_KNOWLEDGE_TRANSFER_RESULT_MISSING")


def test_narrative_revision_preserves_unaffected_roles_and_phases() -> None:
    previous = {
        "story_overview": {"protagonist": "林舟", "current_result": "旧总览"},
        "character_roles": [
            {"name": "林舟", "role": "PROTAGONIST", "evidence_ids": ["e1"]},
            {"name": "沈默", "role": "IMPORTANT_SUPPORTING", "evidence_ids": ["e2"]},
        ],
        "character_relations": [],
        "narrative_phases": [
            {"title": "密信出现", "event_ids": ["event-1"], "evidence_ids": ["e1"]},
            {"title": "旧宅离开", "event_ids": ["event-2"], "evidence_ids": ["e2"]},
        ],
        "event_relations": [],
    }
    proposed = {
        "story_overview": {"protagonist": "林舟", "current_result": "模型改写了总览"},
        "character_roles": [
            {"name": "林舟", "role": "CORE_SUPPORTING", "evidence_ids": ["e1"]},
            {"name": "沈默", "role": "MINOR", "evidence_ids": ["e2"]},
        ],
        "character_relations": [],
        "narrative_phases": [
            {"title": "密信出现（修订）", "event_ids": ["event-1"], "evidence_ids": ["e1"]},
            {"title": "旧宅离开（模型越界修改）", "event_ids": ["event-2"], "evidence_ids": ["e2"]},
        ],
        "event_relations": [],
    }
    merged = merge_targeted_narrative_payload(
        previous,
        proposed,
        [{"target_kind": "CHARACTER", "target_id": "char-1", "target_label": "林舟"}],
        {
            "characters": [{"id": "char-1", "name": "林舟", "aliases": [], "evidence_ids": ["e1"], "event_ids": ["event-1"]}],
            "events": [{"id": "event-1", "people": ["林舟"], "evidence_ids": ["e1"]}],
        },
        "run-1",
    )

    assert merged["story_overview"] == proposed["story_overview"]
    assert merged["character_roles"] == [proposed["character_roles"][0], previous["character_roles"][1]]
    assert merged["narrative_phases"] == [proposed["narrative_phases"][0], previous["narrative_phases"][1]]


def test_narrative_phase_identity_survives_text_revision() -> None:
    first = {"title": "密信出现", "event_ids": ["event-1"], "evidence_ids": ["e1"]}
    revised = {"title": "密信出现并改变目标", "event_ids": ["event-1"], "evidence_ids": ["e1"]}
    assert narrative_phase_id("run-1", first) == narrative_phase_id("run-1", revised)
