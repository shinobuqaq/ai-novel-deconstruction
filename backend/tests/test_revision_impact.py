from app.services.analysis import build_deep_revision_impact, deep_revision_scope


def test_each_deep_workbench_target_has_a_bounded_revision_scope() -> None:
    expected = {
        "FACT": ["fact_versions", "state_changes", "actor_knowledge", "claims"],
        "STATE": ["fact_versions", "state_changes", "actor_knowledge", "claims"],
        "KNOWLEDGE": ["actor_knowledge", "claims"],
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
