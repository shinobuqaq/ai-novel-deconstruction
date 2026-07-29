from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

import app.services.chapter_end_hooks as chapter_end_hooks_service
from app.models import EvidenceSpan, SourceUnit
from app.services.chapter_end_hooks import (
    CHAPTER_END_HOOKS_PROMPT_VERSION,
    ChapterEndHooksValidationError,
    _ending_evidence_catalog,
    _prompt,
    _raise_for_references,
    _summary,
    _window_specs,
    chapter_end_hooks_source_fingerprint,
    parse_chapter_end_hooks,
    provider_payload_for_chapter_end_hooks,
    reconcile_chapter_end_hooks_response,
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
                "retention_basis": "写信人的身份尚未揭晓。",
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
                "retention_basis": "本章完成行动决定，没有新增未闭合问题。",
                "response_status": "NOT_APPLICABLE",
                "response_evidence_id": None,
                "response_summary": "",
            },
        ]
    }


def test_prompt_semver_matches_service_version() -> None:
    assert f"semver: `{CHAPTER_END_HOOKS_PROMPT_VERSION}`" in _prompt()


def test_parser_rejects_fake_hook_or_response_on_none_chapter() -> None:
    payload = _output()
    payload["chapters"][1]["hook_question"] = "接下来会怎样？"

    with pytest.raises(ChapterEndHooksValidationError):
        parse_chapter_end_hooks(payload)


@pytest.mark.parametrize(
    "subjective_phrase",
    [
        "危机会迫使读者立即翻页。",
        "这个身份缺口会激发强烈好奇心，让人忍不住往下看。",
        "悬念能够勾起好奇心，使人想马上看下一段。",
        "神秘身份增强阅读欲望并吸引用户继续看。",
        "这个喜剧情境会吸引关注。",
        "章末向读者抛出一个事实。",
    ],
)
def test_parser_does_not_hard_reject_subjective_reader_language(
    subjective_phrase: str,
) -> None:
    payload = _output()
    payload["chapters"][0]["retention_basis"] = subjective_phrase

    parsed = parse_chapter_end_hooks(payload)

    assert parsed.chapters[0].retention_basis == subjective_phrase


def test_parser_allows_curiosity_explicitly_owned_by_story_character() -> None:
    payload = _output()
    payload["chapters"][0]["rationale"] = (
        "赵孟华被好奇心驱使进入隐藏车站，局势停在未知危险之前。"
    )

    parsed = parse_chapter_end_hooks(payload)

    assert parsed.chapters[0].rationale == (
        "赵孟华被好奇心驱使进入隐藏车站，局势停在未知危险之前。"
    )


def test_parser_allows_neutral_information_given_to_reader() -> None:
    payload = _output()
    payload["chapters"][0]["rationale"] = (
        "章末对话告知读者烟花的实际购买者，完成事件收束。"
    )

    parsed = parse_chapter_end_hooks(payload)

    assert parsed.chapters[0].rationale == (
        "章末对话告知读者烟花的实际购买者，完成事件收束。"
    )


def test_parser_does_not_hard_reject_reference_wording_choice() -> None:
    payload = _output()
    payload["chapters"][0]["hook_question"] = (
        "楚子航将如何对荣超（或凯雷德车主）采取行动？"
    )

    parsed = parse_chapter_end_hooks(payload)

    assert "荣超（或凯雷德车主）" in parsed.chapters[0].hook_question


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


def test_reconciliation_keeps_32_chapters_and_only_repairs_the_bad_one(
    monkeypatch,
) -> None:
    units = [
        SimpleNamespace(
            id=f"unit_{ordinal}",
            ordinal=ordinal,
            title=f"第{ordinal}章",
            content_hash=f"hash_{ordinal}",
        )
        for ordinal in range(1, 34)
    ]
    projection = {
        "source_version_id": "version_1",
        "deep_revision": 1,
        "narrative_status": "READY",
        "deep_status": "READY",
        "phases": [{
            "id": "phase",
            "chapter_ordinals": list(range(1, 34)),
        }],
    }
    fingerprint = chapter_end_hooks_source_fingerprint(projection, units)
    run = SimpleNamespace(id="run_1")
    version = SimpleNamespace(id="version_1", total_chars=100_000)

    class FakeSession:
        def get(self, model, object_id):
            if model.__name__ == "AnalysisRun" and object_id == run.id:
                return run
            if model.__name__ == "SourceVersion" and object_id == version.id:
                return version
            return None

    def fake_endings(_session, _units, indexes):
        records = []
        by_chapter = {}
        for index in indexes:
            ordinal = index + 1
            evidence = EvidenceSpan(
                id=f"ending_{ordinal}",
                text_snapshot=f"第{ordinal}章章末。",
            )
            by_chapter[ordinal] = evidence
            records.append({
                "chapter_ordinal": ordinal,
                "chapter_title": f"第{ordinal}章",
                "ending_evidence_id": evidence.id,
                "ending_text": evidence.text_snapshot,
                "ending_text_truncated": False,
                "ending_context": evidence.text_snapshot,
                "ending_context_evidence_ids": [evidence.id],
                "ignored_trailing_boilerplate_count": 0,
            })
        return records, by_chapter, 0

    monkeypatch.setattr(
        chapter_end_hooks_service,
        "_base_projection",
        lambda _session, _run_id: projection,
    )
    monkeypatch.setattr(
        chapter_end_hooks_service,
        "_chapter_units",
        lambda _session, _version_id: units,
    )
    monkeypatch.setattr(
        chapter_end_hooks_service,
        "_ending_evidence_catalog",
        fake_endings,
    )
    monkeypatch.setattr(
        chapter_end_hooks_service,
        "resolve_analysis_profile",
        lambda _settings, _profile_id: (
            SimpleNamespace(),
            SimpleNamespace(
                max_output_tokens=16_000,
                context_window_tokens=1_000_000,
            ),
        ),
    )
    task_payload = {
        "run_id": run.id,
        "source_version_id": version.id,
        "source_fingerprint": fingerprint,
        "model_profile_id": "profile",
        "chapter_start": 1,
        "chapter_end": 33,
        "window_group_id": "1234abcd",
        "window_index": 1,
        "window_count": 1,
    }

    def chapter(ordinal: int, evidence_id: str | None = None) -> dict:
        return {
            "chapter_ordinal": ordinal,
            "ending_evidence_id": evidence_id or f"ending_{ordinal}",
            "hook_type": "NEW_INFORMATION",
            "strength": "MEDIUM",
            "hook_question": f"第{ordinal}章留下的问题是什么？",
            "rationale": "章末给出新的书内事实。",
            "retention_basis": "该事实尚未在本章内解释。",
            "response_status": "NOT_APPLICABLE",
            "response_evidence_id": None,
            "response_summary": "",
        }

    first_response = {
        "chapters": [
            chapter(
                ordinal,
                "wrong_evidence" if ordinal == 17 else None,
            )
            for ordinal in range(1, 34)
        ]
    }
    first = reconcile_chapter_end_hooks_response(
        FakeSession(),
        task_payload=task_payload,
        value=first_response,
        attempt_id="attempt_1",
    )

    assert first.output is None
    assert len(first.accepted_chapters) == 32
    assert first.repair_chapter_ordinals == [17]
    repair_payload = {
        **task_payload,
        "accepted_chapter_proposals": first.accepted_chapters,
        "accepted_chapter_attempt_ids": first.accepted_attempt_ids,
        "repair_chapter_ordinals": first.repair_chapter_ordinals,
    }
    provider_payload = provider_payload_for_chapter_end_hooks(
        FakeSession(),
        SimpleNamespace(),
        repair_payload,
    )
    repair_input = json.loads(provider_payload["input"])
    assert [
        item["chapter_ordinal"]
        for item in repair_input["chapter_endings"]
    ] == [17]
    assert repair_input["window"]["repair_mode"] is True

    second = reconcile_chapter_end_hooks_response(
        FakeSession(),
        task_payload=repair_payload,
        value={"chapters": [chapter(1), chapter(17)]},
        attempt_id="attempt_2",
    )

    assert second.output is not None
    assert len(second.output.chapters) == 33
    assert second.accepted_attempt_ids["1"] == "attempt_1"
    assert second.accepted_attempt_ids["17"] == "attempt_2"


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
    assert records[0]["ending_context"] == (
        "林舟看见桌上的密信。"
    )
    assert records[0]["ending_context_evidence_ids"] == [
        endings[1].id
    ]
    assert ignored == 1


@pytest.mark.parametrize(
    "end_marker",
    ["【END】", "[END]", "THE END", "全文完"],
)
def test_program_skips_pure_end_marker(client, end_marker: str) -> None:
    project = client.post(
        "/api/projects",
        json={"name": f"结束标记测试-{end_marker}"},
    ).json()
    source = (
        "第一章 密信\n"
        "林舟终于拆开了那封密信。\n"
        f"{end_marker}\n"
        "第二章 决定\n"
        "林舟决定开始追查。"
    )
    imported = client.post(
        f"/api/projects/{project['id']}/sources/import?filename=end-marker.txt",
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
            session,
            units,
            list(range(len(units))),
        )

    assert records[0]["ending_text"] == "林舟终于拆开了那封密信。"
    assert endings[1].text_snapshot == "林舟终于拆开了那封密信。"
    assert ignored == 1


def test_program_uses_source_position_when_derived_span_has_large_index(client) -> None:
    project = client.post("/api/projects", json={"name": "章末坐标测试"}).json()
    source = (
        "第一章 密信\n"
        "林舟在半路发现一封信。\n"
        "远处钟声响起，信封上的名字仍无人认识。\n"
        "第二章 决定\n"
        "林舟决定开始追查。"
    )
    imported = client.post(
        f"/api/projects/{project['id']}/sources/import?filename=hook-order.txt",
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
        middle = session.scalar(
            select(EvidenceSpan).where(
                EvidenceSpan.source_unit_id == units[0].id,
                EvidenceSpan.text_snapshot == "林舟在半路发现一封信。",
            )
        )
        assert middle is not None
        middle.paragraph_index = 1_000_000 + middle.start_char
        session.flush()

        records, endings, _ignored = _ending_evidence_catalog(
            session,
            units,
            [0],
        )

    assert records[0]["ending_text"] == "远处钟声响起，信封上的名字仍无人认识。"
    assert records[0]["ending_context"] == (
        "林舟在半路发现一封信。\n"
        "远处钟声响起，信封上的名字仍无人认识。"
    )
    assert records[0]["ending_context_evidence_ids"] == [
        middle.id,
        endings[1].id,
    ]
    assert endings[1].text_snapshot == "远处钟声响起，信封上的名字仍无人认识。"


def test_program_keeps_five_paragraphs_of_chapter_end_context(client) -> None:
    project = client.post("/api/projects", json={"name": "章末上下文测试"}).json()
    source = (
        "第一章 寻人\n"
        "林舟收起地图。\n"
        "他决定立刻开车去找苏清。\n"
        "汽车驶入长街。\n"
        "路灯一盏盏亮起。\n"
        "他把速度提到最高。\n"
        "音响唱起最后一天。\n"
        "第二章 会面\n"
        "林舟终于看见苏清。"
    )
    imported = client.post(
        f"/api/projects/{project['id']}/sources/import?filename=context.txt",
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
        records, _endings, _ignored = _ending_evidence_catalog(
            session,
            units,
            [0],
        )

    assert records[0]["ending_context"].splitlines() == [
        "他决定立刻开车去找苏清。",
        "汽车驶入长街。",
        "路灯一盏盏亮起。",
        "他把速度提到最高。",
        "音响唱起最后一天。",
    ]
    assert len(records[0]["ending_context_evidence_ids"]) == 5
