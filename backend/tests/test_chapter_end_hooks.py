from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import EvidenceSpan, SourceUnit
from app.services.chapter_end_hooks import (
    ChapterEndHooksValidationError,
    _ending_evidence_catalog,
    _raise_for_references,
    _summary,
    _window_specs,
    parse_chapter_end_hooks,
)


def _output() -> dict:
    return {
        "chapters": [
            {
                "chapter_ordinal": 1,
                "ending_evidence_id": "evd_end_1",
                "hook_type": "NEW_INFORMATION",
                "strength": "STRONG",
                "hook_question": "密信是谁留下的？",
                "rationale": "章末第一次抛出写给主角的密信。",
                "retention_basis": "具体身份缺口会推动读者继续追查。",
                "response_status": "NOT_APPLICABLE",
                "response_evidence_id": None,
                "response_summary": "",
            },
            {
                "chapter_ordinal": 2,
                "ending_evidence_id": "evd_end_2",
                "hook_type": "NONE",
                "strength": "NONE",
                "hook_question": "",
                "rationale": "本章在行动决定完成后收束。",
                "retention_basis": "依靠已经建立的追查主线维持阅读。",
                "response_status": "NOT_APPLICABLE",
                "response_evidence_id": None,
                "response_summary": "",
            },
        ]
    }


def test_parser_rejects_fake_hook_or_response_on_none_chapter() -> None:
    payload = _output()
    payload["chapters"][1]["hook_question"] = "接下来会怎样？"

    with pytest.raises(ChapterEndHooksValidationError):
        parse_chapter_end_hooks(payload)


def test_reference_validation_requires_exact_window_and_end_evidence() -> None:
    output = parse_chapter_end_hooks(_output())
    endings = {
        1: EvidenceSpan(id="evd_end_1", text_snapshot="密信。"),
        2: EvidenceSpan(id="evd_end_2", text_snapshot="决定追查。"),
    }
    _raise_for_references(output, endings)

    incomplete_endings = {1: endings[1]}
    with pytest.raises(ValueError, match="CHAPTER_COVERAGE_INVALID"):
        _raise_for_references(output, incomplete_endings)

    wrong_end = _output()
    wrong_end["chapters"][0]["ending_evidence_id"] = "evd_other"
    with pytest.raises(ValueError, match="ENDING_REFERENCE_INVALID"):
        _raise_for_references(
            parse_chapter_end_hooks(wrong_end),
            endings,
        )


def test_windowing_keeps_every_chapter_in_contiguous_order() -> None:
    endings = [
        {
            "chapter_ordinal": ordinal,
            "ending_text": "章末" * 100,
        }
        for ordinal in range(1, 206)
    ]

    windows = _window_specs(endings, input_char_budget=20_000)

    assert len(windows) > 1
    assert windows[0]["chapter_start"] == 1
    assert windows[-1]["chapter_end"] == 205
    assert all(
        current["chapter_end"] + 1 == following["chapter_start"]
        for current, following in zip(windows, windows[1:])
    )
    assert all(
        window["chapter_end"] - window["chapter_start"] + 1 <= 80
        for window in windows
    )


def test_program_summary_counts_exact_streaks_and_type_transitions() -> None:
    chapters = [
        {
            "chapter_ordinal": 1,
            "chapter_title": "第一章",
            "hook_type": "NEW_INFORMATION",
            "strength": "STRONG",
            "phase_title": "开篇",
            "ending_evidence_ids": ["evd_1"],
        },
        {
            "chapter_ordinal": 2,
            "chapter_title": "第二章",
            "hook_type": "NEW_INFORMATION",
            "strength": "STRONG",
            "phase_title": "开篇",
            "ending_evidence_ids": ["evd_2"],
        },
        {
            "chapter_ordinal": 3,
            "chapter_title": "第三章",
            "hook_type": "NONE",
            "strength": "NONE",
            "phase_title": "开篇",
            "ending_evidence_ids": ["evd_3"],
        },
    ]

    summary = _summary(chapters)

    assert summary["max_consecutive_strong"] == 2
    assert summary["max_consecutive_same_type"] == 2
    assert summary["no_hook_count"] == 1
    assert summary["type_transition_count"] == 1
    assert "response_distance" not in summary


def test_program_skips_download_site_footer_when_selecting_real_chapter_end(client) -> None:
    project = client.post("/api/projects", json={"name": "章末证据测试"}).json()
    source = (
        "第一章 密信\n"
        "林舟看见桌上的密信。\n"
        "声明：本书为用户上传，更多好书尽在 txt80.com\n"
        "第二章 决定\n"
        "林舟决定开始追查。"
    )
    imported = client.post(
        f"/api/projects/{project['id']}/sources/import?filename=hooks.txt",
        content=source.encode("utf-8"),
    ).json()

    with client.app.state.session_factory() as session:
        units = list(session.scalars(
            select(SourceUnit)
            .where(
                SourceUnit.source_version_id == imported["version"]["id"],
                SourceUnit.unit_type == "CHAPTER",
            )
            .order_by(SourceUnit.ordinal)
        ))
        records, endings, ignored = _ending_evidence_catalog(
            session, units, list(range(len(units)))
        )

    assert len(records) == 2
    assert endings[1].text_snapshot == "林舟看见桌上的密信。"
    assert ignored == 1
