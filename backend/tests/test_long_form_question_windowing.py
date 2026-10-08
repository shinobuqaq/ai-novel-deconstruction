from types import SimpleNamespace

import pytest

from app.services.character_design import (
    CHARACTER_DESIGN_FIELDS,
    _merged_character_design_fields,
    _window_specs as character_window_specs,
)
from app.services.chapter_end_hooks import (
    _window_specs as hook_window_specs,
)
from app.services.learning_report import (
    _opening_payoff_candidate_artifact,
)
from app.services.opening_payoff_candidates import (
    OpeningPayoffCandidatesOutput,
    _validated_rows,
    _window_specs as payoff_window_specs,
)


ARBITRARY_LONG_FORM_COUNTS = (1, 51, 201, 520, 1001, 1111, 2000)


def test_1_4_can_address_candidates_after_the_old_200_item_cutoff() -> None:
    events = []
    evidence_by_id = {}
    chapter_by_unit_id = {}
    for sequence_no in range(1, 251):
        evidence_id = f"evd_{sequence_no}"
        unit_id = f"unit_{sequence_no}"
        events.append({
            "id": f"evt_{sequence_no}",
            "title": f"事件 {sequence_no}",
            "summary": "候选事件",
            "chapter_ordinals": [sequence_no],
            "evidence_ids": [evidence_id],
        })
        evidence_by_id[evidence_id] = SimpleNamespace(
            id=evidence_id,
            start_char=sequence_no * 100,
            end_char=sequence_no * 100 + 20,
            paragraph_index=0,
            source_unit_id=unit_id,
            text_snapshot=f"第 {sequence_no} 个候选原文",
            source_unit=SimpleNamespace(title=f"第 {sequence_no} 章"),
        )
        chapter_by_unit_id[unit_id] = {
            "ordinal": sequence_no,
            "title": f"第 {sequence_no} 章",
        }

    artifact = _opening_payoff_candidate_artifact(
        {"events": events},
        evidence_by_id,
        chapter_by_unit_id,
        {"description_evidence_ids": []},
        candidate_start_sequence=201,
        candidate_limit=50,
    )

    assert artifact["coverage"]["source_event_count"] == 250
    assert artifact["coverage"]["candidate_start_sequence"] == 201
    assert artifact["coverage"]["candidate_end_sequence"] == 250
    assert [
        item["sequence_no"] for item in artifact["candidates"]
    ] == list(range(201, 251))


@pytest.mark.parametrize("candidate_count", ARBITRARY_LONG_FORM_COUNTS)
def test_1_4_windowing_covers_any_candidate_count_contiguously(
    candidate_count: int,
) -> None:
    candidates = [
        {
            "sequence_no": sequence_no,
            "event_id": f"evt_{sequence_no}",
            "event_summary": "候选" * 120,
            "evidence_options": [{
                "evidence_no": 1,
                "text": "原文" * 180,
            }],
        }
        for sequence_no in range(1, candidate_count + 1)
    ]

    windows = payoff_window_specs(
        candidates,
        input_char_budget=24_000,
    )

    assert windows[0]["candidate_start_sequence"] == 1
    assert windows[-1]["candidate_end_sequence"] == candidate_count
    assert all(
        current["candidate_end_sequence"] + 1
        == following["candidate_start_sequence"]
        for current, following in zip(windows, windows[1:])
    )
    assert all(
        window["candidate_end_sequence"]
        - window["candidate_start_sequence"]
        + 1
        <= 200
        for window in windows
    )


def test_1_4_window_may_stop_at_first_complete_candidate() -> None:
    artifact = {
        "candidates": [
            {
                "sequence_no": sequence_no,
                "event_id": f"evt_{sequence_no}",
                "event_title": f"事件 {sequence_no}",
                "event_summary": "候选",
                "program_exclusion_code": "",
                "evidence_options": [{
                    "evidence_no": 1,
                    "evidence_id": f"evd_{sequence_no}",
                    "chapter_ordinal": sequence_no,
                    "chapter_title": f"第 {sequence_no} 章",
                    "paragraph_number": 1,
                    "source_char_start": sequence_no * 100,
                }],
            }
            for sequence_no in range(201, 251)
        ],
    }
    output = OpeningPayoffCandidatesOutput.model_validate({
        "classifications": [
            {
                "sequence_no": 201,
                "matched_facet_ids": ["F1"],
                "exclusion_code": "PARTIAL_ONLY",
                "anchor_evidence_no": 1,
            },
            {
                "sequence_no": 202,
                "matched_facet_ids": ["F1", "F2", "F3"],
                "exclusion_code": "NONE",
                "anchor_evidence_no": 1,
            },
        ],
    })

    rows = _validated_rows(output, artifact)

    assert [row["sequence_no"] for row in rows] == [201, 202]
    assert rows[-1]["program_complete_payoff"] is True


@pytest.mark.parametrize("event_count", ARBITRARY_LONG_FORM_COUNTS)
def test_2_2_windowing_covers_all_protagonist_events(
    event_count: int,
) -> None:
    compact_events = [
        {
            "sequence_no": sequence_no,
            "id": f"evt_{sequence_no}",
            "summary": "主角行动" * 120,
            "evidence_ids": [f"evd_{sequence_no}"],
        }
        for sequence_no in range(1, event_count + 1)
    ]
    evidence_text = {
        f"evd_{sequence_no}": "原文" * 180
        for sequence_no in range(1, event_count + 1)
    }

    windows = character_window_specs(
        compact_events,
        evidence_text,
        input_char_budget=24_000,
    )

    assert windows[0]["event_start_sequence"] == 1
    assert windows[-1]["event_end_sequence"] == event_count
    assert all(
        current["event_end_sequence"] + 1
        == following["event_start_sequence"]
        for current, following in zip(windows, windows[1:])
    )
    assert all(
        window["event_end_sequence"]
        - window["event_start_sequence"]
        + 1
        <= 80
        for window in windows
    )


def test_2_2_merge_keeps_the_earliest_supported_field_across_windows() -> None:
    def field(
        field_name: str,
        *,
        supported: bool,
        event_id: str = "",
        chapter: int = 1,
    ) -> dict:
        if not supported:
            return {
                "field": field_name,
                "status": "INSUFFICIENT_EVIDENCE",
                "explanation": "本窗口未发现。",
            }
        return {
            "field": field_name,
            "status": "SUPPORTED",
            "value": "主角采取行动",
            "first_display_chapter_ordinal": chapter,
            "first_display_event_id": event_id,
            "display_event": "主角行动",
            "evidence_ids": [f"evd_{event_id}"],
            "explanation": "原文证明该项。",
            "created_by_attempt_id": f"att_{event_id}",
        }

    first_window = {
        "fields": [
            field(
                name,
                supported=name == "surface_desire",
                event_id="evt_2",
                chapter=2,
            )
            for name in CHARACTER_DESIGN_FIELDS
        ],
    }
    second_window = {
        "fields": [
            field(
                name,
                supported=name in {"surface_desire", "deep_desire"},
                event_id="evt_90",
                chapter=90,
            )
            for name in CHARACTER_DESIGN_FIELDS
        ],
    }

    merged, attempt_ids = _merged_character_design_fields([
        first_window,
        second_window,
    ])
    by_name = {item.field: item for item in merged}

    assert by_name["surface_desire"].first_display_event_id == "evt_2"
    assert by_name["deep_desire"].first_display_event_id == "evt_90"
    assert by_name["motivation"].status == "INSUFFICIENT_EVIDENCE"
    assert attempt_ids["surface_desire"] == "att_evt_2"


@pytest.mark.parametrize("chapter_count", ARBITRARY_LONG_FORM_COUNTS)
def test_4_9_windowing_covers_all_chapters(
    chapter_count: int,
) -> None:
    endings = [
        {
            "chapter_ordinal": chapter,
            "ending_text": "章末原文" * 240,
        }
        for chapter in range(1, chapter_count + 1)
    ]

    windows = hook_window_specs(
        endings,
        input_char_budget=24_000,
    )

    assert windows[0]["chapter_start"] == 1
    assert windows[-1]["chapter_end"] == chapter_count
    assert all(
        current["chapter_end"] + 1
        == following["chapter_start"]
        for current, following in zip(windows, windows[1:])
    )
