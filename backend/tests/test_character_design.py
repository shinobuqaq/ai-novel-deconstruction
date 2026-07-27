from __future__ import annotations

from copy import deepcopy

import pytest

from app.services.character_design import (
    CHARACTER_DESIGN_FIELDS,
    CharacterDesignValidationError,
    _character_design_sources_ready,
    _protagonist,
    _raise_for_references,
    character_design_source_fingerprint,
    parse_character_design_evidence,
)


def _projection() -> dict:
    return {
        "source_version_id": "srcv_test",
        "deep_revision": 1,
        "story_overview": {"protagonist": "林舟"},
        "characters": [{
            "id": "entity_protagonist",
            "name": "林舟",
            "role": "PROTAGONIST",
            "goals": ["找到寄信人"],
            "motivations": ["弄清密信来意"],
            "abilities": ["观察线索"],
            "event_ids": ["event_1", "event_2"],
            "evidence_ids": ["evidence_1", "evidence_2"],
        }],
        "events": [
            {
                "id": "event_1",
                "title": "发现密信",
                "people": ["林舟"],
                "chapter_ordinals": [1],
                "process": "林舟发现密信。",
                "outcome": "林舟决定追查。",
                "evidence_ids": ["evidence_1"],
            },
            {
                "id": "event_2",
                "title": "追查线索",
                "people": ["林舟"],
                "chapter_ordinals": [2],
                "process": "林舟核对新的线索。",
                "outcome": "追查方向发生变化。",
                "evidence_ids": ["evidence_2"],
            },
        ],
        "phases": [{
            "id": "phase_1",
            "title": "开始追查",
            "chapter_ordinals": [1, 2],
            "event_ids": ["event_1", "event_2"],
            "evidence_ids": ["evidence_1", "evidence_2"],
        }],
    }


def _output_dict() -> dict:
    return {
        "protagonist": "林舟",
        "fields": [
            {
                "field": field,
                "status": "SUPPORTED",
                "value": f"{field} 的可核查判断",
                "first_display_chapter_ordinal": 1,
                "first_display_event_id": "event_1",
                "display_event": "林舟发现密信后决定主动追查。",
                "evidence_ids": ["evidence_1"],
                "explanation": "这一行动直接展示了人物要素。",
            }
            for field in CHARACTER_DESIGN_FIELDS
        ],
        "desire_conflicts": [{
            "chapter_ordinal": 2,
            "event_id": "event_2",
            "surface_desire": "尽快找到寄信人。",
            "deep_desire": "掌握自己的处境。",
            "choice": "林舟选择继续追查。",
            "arc_change": "主角从被动等待转向主动承担。",
            "evidence_ids": ["evidence_2"],
        }],
        "arc_summary": "林舟由被动发现线索转为主动追查。",
    }


@pytest.mark.parametrize("mode", ["missing", "duplicate"])
def test_parser_rejects_missing_or_duplicate_contract_fields(mode: str) -> None:
    payload = _output_dict()
    if mode == "missing":
        payload["fields"].pop()
    else:
        payload["fields"][-1]["field"] = payload["fields"][0]["field"]

    with pytest.raises(CharacterDesignValidationError):
        parse_character_design_evidence(payload)


def test_insufficient_field_cannot_invent_event_chapter_or_evidence() -> None:
    payload = _output_dict()
    payload["fields"][0].update({
        "status": "INSUFFICIENT_EVIDENCE",
        "value": "",
        "display_event": "",
    })

    with pytest.raises(CharacterDesignValidationError):
        parse_character_design_evidence(payload)


def test_insufficient_field_cannot_invent_display_event_text() -> None:
    payload = _output_dict()
    payload["fields"][0].update({
        "status": "INSUFFICIENT_EVIDENCE",
        "value": "",
        "first_display_chapter_ordinal": None,
        "first_display_event_id": None,
        "display_event": "林舟在没有可引用事件时仍表现出这一特征。",
        "evidence_ids": [],
    })

    with pytest.raises(CharacterDesignValidationError):
        parse_character_design_evidence(payload)


@pytest.mark.parametrize(
    ("update", "expected_code"),
    [
        ({"first_display_event_id": "event_missing"}, "CHARACTER_DESIGN_EVENT_REFERENCE_INVALID"),
        ({"first_display_chapter_ordinal": 2}, "CHARACTER_DESIGN_CHAPTER_REFERENCE_INVALID"),
        ({"evidence_ids": ["evidence_2"]}, "CHARACTER_DESIGN_EVENT_EVIDENCE_MISMATCH"),
    ],
)
def test_reference_validation_rejects_wrong_event_chapter_and_cross_event_evidence(
    update: dict,
    expected_code: str,
) -> None:
    payload = _output_dict()
    payload["fields"][0].update(update)
    output = parse_character_design_evidence(payload)

    with pytest.raises(ValueError, match=expected_code):
        _raise_for_references(
            output,
            _projection(),
            {"evidence_1", "evidence_2"},
        )


def test_source_fingerprint_changes_when_deep_revision_or_events_change() -> None:
    projection = _projection()
    original = character_design_source_fingerprint(projection)
    revised_deep = deepcopy(projection)
    revised_deep["deep_revision"] = 2
    revised_event = deepcopy(projection)
    revised_event["events"][0]["outcome"] = "林舟决定暂时隐瞒密信。"

    assert character_design_source_fingerprint(revised_deep) != original
    assert character_design_source_fingerprint(revised_event) != original


def test_unclassified_supporting_characters_do_not_block_protagonist_ledger() -> None:
    projection = _projection()
    projection.update({
        "narrative_status": "INCOMPLETE",
        "story_overview": {"protagonist": "林舟"},
    })

    assert _character_design_sources_ready(projection) is True
    projection["phases"] = []
    assert _character_design_sources_ready(projection) is False


def test_story_overview_name_wins_when_multiple_characters_are_marked_protagonist() -> None:
    projection = _projection()
    projection["story_overview"]["protagonist"] = "林舟"
    projection["characters"].insert(0, {
        "id": "entity_other_lead",
        "name": "顾川",
        "aliases": [],
        "role": "PROTAGONIST",
    })
    projection["characters"][1]["role"] = "PROTAGONIST"

    name, character = _protagonist(projection)

    assert name == "林舟"
    assert character is not None
    assert character["id"] == "entity_protagonist"
