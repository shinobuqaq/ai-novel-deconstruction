from __future__ import annotations

from app.services.learning_report import (
    _opening_character_program_artifact,
    _program_2_1_reading_fields,
    _program_4_9_matrix,
)
from app.services.opening_payoff_candidates import (
    OpeningPayoffCandidatesOutput,
    _validated_rows,
)


def test_1_4_facets_are_observation_aids_not_all_required() -> None:
    output = OpeningPayoffCandidatesOutput.model_validate({
        "classifications": [{
            "sequence_no": 1,
            "matched_facet_ids": ["F1"],
            "exclusion_code": "NONE",
            "anchor_evidence_no": 1,
        }],
    })
    artifact = {
        "candidates": [{
            "sequence_no": 1,
            "event_id": "evt_1",
            "event_title": "异常力量首次现身",
            "event_summary": "卖点机制进入正文。",
            "program_exclusion_code": "PARTIAL_ONLY",
            "evidence_options": [{
                "evidence_no": 1,
                "evidence_id": "evd_1",
                "chapter_ordinal": 1,
                "chapter_title": "第一章",
                "paragraph_number": 3,
                "source_char_start": 120,
            }],
        }],
    }

    rows = _validated_rows(output, artifact)

    assert rows[0]["program_complete_payoff"] is True


def test_2_1_answer_explains_identity_function_and_later_activity() -> None:
    projection = {
        "characters": [{
            "name": "林舟",
            "role": "PROTAGONIST",
            "identities": ["调查员"],
            "description": "负责追查密信来源。",
        }],
        "events": [{
            "id": "evt_1",
            "title": "林舟接下调查",
            "chapter_ordinals": [1],
            "people": ["林舟"],
            "evidence_ids": ["evd_1"],
        }],
        "person_identity_candidates": [],
    }
    artifact = _opening_character_program_artifact(projection)
    artifact["roles"][0].update({
        "first_scene_function": "主角锚点",
        "first_scene_function_evidence_ids": ["evd_1"],
    })
    artifact["function_distribution"] = [{
        "function": "主角锚点",
        "count": 1,
    }]

    reading = _program_2_1_reading_fields(artifact)

    assert "调查员" in reading["conclusion"]
    assert "主角锚点" in reading["conclusion"]
    assert "参与 1 个行动事件" in reading["conclusion"]
    assert "人数只说明人物进入速度" not in reading["conclusion"]
    assert "这些数字只说明人物进入速度" in reading["conclusion"]


def test_4_9_matrix_keeps_type_and_strength_as_independent_dimensions() -> None:
    matrix = _program_4_9_matrix({
        "chapters": [
            {
                "chapter_ordinal": 1,
                "chapter_title": "第一章",
                "hook_type": "NEW_INFORMATION",
                "strength": "LIGHT",
                "ending_evidence_ids": ["evd_1"],
            },
            {
                "chapter_ordinal": 2,
                "chapter_title": "第二章",
                "hook_type": "NEW_INFORMATION",
                "strength": "STRONG",
                "ending_evidence_ids": ["evd_2"],
            },
        ],
    })

    row = matrix["rows"][0]
    assert row["hook_type"] == "NEW_INFORMATION"
    assert row["counts"]["STRONG"] == 1
    assert row["counts"]["LIGHT"] == 1
    assert {
        example["strength"] for example in row["examples"]
    } == {"STRONG", "LIGHT"}
