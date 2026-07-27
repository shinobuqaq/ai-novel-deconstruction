from __future__ import annotations

from copy import deepcopy

import pytest
from sqlalchemy import select

from app.models import EvidenceSpan, SourceUnit
from app.services.chapter_end_hooks import (
    ChapterEndHooksValidationError,
    _ending_evidence_catalog,
    _raise_for_references,
    _sample_indexes,
    _summary,
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
                "retention_basis": "",
                "response_status": "RESOLVED",
                "response_evidence_id": "evd_response_2",
                "response_summary": "下一章确认主角开始寻找寄信人。",
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


def test_reference_validation_requires_exact_end_and_later_response() -> None:
    output = parse_chapter_end_hooks(_output())
    endings = {
        1: EvidenceSpan(id="evd_end_1", text_snapshot="密信。"),
        2: EvidenceSpan(id="evd_end_2", text_snapshot="决定追查。"),
    }
    _raise_for_references(output, endings, {"evd_response_2": 2})

    with pytest.raises(ValueError, match="RESPONSE_ORDER_INVALID"):
        _raise_for_references(output, endings, {"evd_response_2": 1})

    wrong_end = deepcopy(_output())
    wrong_end["chapters"][0]["ending_evidence_id"] = "evd_other"
    with pytest.raises(ValueError, match="ENDING_REFERENCE_INVALID"):
        _raise_for_references(
            parse_chapter_end_hooks(wrong_end),
            endings,
            {"evd_response_2": 2},
        )


def test_sampling_covers_all_small_books_and_balances_long_books() -> None:
    assert _sample_indexes(33) == list(range(33))
    indexes = _sample_indexes(200)
    assert len(indexes) == 50
    assert indexes[0] == 0
    assert indexes[-1] == 199


def test_program_summary_counts_streaks_types_and_response_distance() -> None:
    chapters = [
        {
            "chapter_ordinal": 1,
            "chapter_title": "第一章",
            "hook_type": "NEW_INFORMATION",
            "strength": "STRONG",
            "response_status": "RESOLVED",
            "response_distance": 1,
            "phase_title": "开篇",
            "ending_evidence_ids": ["evd_1"],
        },
        {
            "chapter_ordinal": 2,
            "chapter_title": "第二章",
            "hook_type": "NEW_INFORMATION",
            "strength": "STRONG",
            "response_status": "UNRESOLVED",
            "response_distance": None,
            "phase_title": "开篇",
            "ending_evidence_ids": ["evd_2"],
        },
        {
            "chapter_ordinal": 3,
            "chapter_title": "第三章",
            "hook_type": "NONE",
            "strength": "NONE",
            "response_status": "NOT_APPLICABLE",
            "response_distance": None,
            "phase_title": "开篇",
            "ending_evidence_ids": ["evd_3"],
        },
    ]

    summary = _summary(chapters)

    assert summary["max_consecutive_strong"] == 2
    assert summary["max_consecutive_same_type"] == 2
    assert summary["no_hook_count"] == 1
    assert summary["response_distance"]["average"] == 1.0


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
