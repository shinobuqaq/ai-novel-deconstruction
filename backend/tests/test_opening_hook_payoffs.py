from __future__ import annotations

import pytest

from app.models import EvidenceSpan
from app.services.opening_hook_payoffs import (
    OpeningHookPayoffsValidationError,
    _summary,
    _validate_window_output,
    _window_specs,
    parse_opening_hook_payoffs,
)


def _output() -> dict:
    return {
        "results": [
            {
                "hook_chapter": 1,
                "result": "FOUND_COMPLETE",
                "response_evidence_id": "evd_response_1",
                "response_summary": "第二章确认密信来自旧宅管家。",
                "rationale": "回应直接解释了第一章提出的寄信人问题。",
            },
            {
                "hook_chapter": 2,
                "result": "NOT_FOUND_IN_WINDOW",
                "response_evidence_id": None,
                "response_summary": "",
                "rationale": "本窗口没有出现能回答该问题的原文。",
            },
            {
                "hook_chapter": 3,
                "result": "NO_HOOK",
                "response_evidence_id": None,
                "response_summary": "",
                "rationale": "第三章在行动完成后收束，没有具体追读问题。",
            },
        ]
    }


def _hooks() -> list[dict[str, object]]:
    return [
        {"chapter_ordinal": 1, "hook_type": "NEW_INFORMATION"},
        {"chapter_ordinal": 2, "hook_type": "CRISIS_SUSPENSION"},
        {"chapter_ordinal": 3, "hook_type": "NONE"},
    ]


def test_parser_rejects_duplicate_hook_chapter() -> None:
    payload = _output()
    payload["results"][1]["hook_chapter"] = 1

    with pytest.raises(
        OpeningHookPayoffsValidationError,
        match="OPENING_HOOK_PAYOFFS_HOOK_DUPLICATED",
    ):
        parse_opening_hook_payoffs(payload)


def test_window_validation_accepts_only_later_evidence_from_current_window() -> None:
    output = parse_opening_hook_payoffs(_output())
    response = EvidenceSpan(
        id="evd_response_1",
        source_unit_id="chapter_2",
        start_char=120,
        end_char=150,
        text_snapshot="密信来自旧宅管家。",
    )

    accepted = _validate_window_output(
        output,
        _hooks(),
        {response.id: response},
        {"chapter_2": 2},
    )

    assert accepted[0]["response_chapter_ordinal"] == 2
    assert accepted[0]["response_start_char"] == 120

    with pytest.raises(
        ValueError,
        match="OPENING_HOOK_PAYOFFS_RESPONSE_EVIDENCE_INVALID",
    ):
        _validate_window_output(output, _hooks(), {}, {"chapter_2": 2})

    with pytest.raises(
        ValueError,
        match="OPENING_HOOK_PAYOFFS_RESPONSE_POSITION_INVALID",
    ):
        _validate_window_output(
            output,
            _hooks(),
            {response.id: response},
            {"chapter_2": 1},
        )


def test_windowing_covers_thousand_chapters_in_contiguous_order() -> None:
    chapter_sizes = [
        {
            "chapter_ordinal": ordinal,
            "estimated_material_chars": 4_000,
        }
        for ordinal in range(2, 1001)
    ]

    windows = _window_specs(chapter_sizes, input_char_budget=225_000)

    assert len(windows) > 1
    assert windows[0]["chapter_start"] == 2
    assert windows[-1]["chapter_end"] == 1000
    assert all(
        current["chapter_end"] + 1 == following["chapter_start"]
        for current, following in zip(windows, windows[1:])
    )
    assert sum(
        window["chapter_end"] - window["chapter_start"] + 1
        for window in windows
    ) == 999


def test_program_summary_counts_response_states_and_distances() -> None:
    summary = _summary([
        {
            "response_status": "COMPLETE",
            "response_distance_chapters": 2,
        },
        {
            "response_status": "PARTIAL",
            "response_distance_chapters": 5,
        },
        {
            "response_status": "UNRESOLVED",
            "response_distance_chapters": None,
        },
    ])

    assert summary["complete_response_count"] == 1
    assert summary["partial_response_count"] == 1
    assert summary["unresolved_count"] == 1
    assert summary["average_response_distance_chapters"] == 3.5
    assert summary["maximum_response_distance_chapters"] == 5
