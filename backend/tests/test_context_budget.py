from types import SimpleNamespace

from app.services.analysis import (
    ContextMaterial,
    _build_chapter_digests,
    _build_synthesis_context,
    _select_context_materials,
)


def test_context_selection_is_bounded_and_explains_omissions() -> None:
    materials = [
        ContextMaterial(
            key="chapter-1",
            kind="event",
            text="一" * 12,
            priority=100,
            reason="剧情发展和章节覆盖",
            chapter_ordinal=1,
        ),
        ContextMaterial(
            key="chapter-2",
            kind="event",
            text="二" * 12,
            priority=90,
            reason="剧情发展和章节覆盖",
            chapter_ordinal=2,
        ),
        ContextMaterial(
            key="evidence-3",
            kind="evidence",
            text="三" * 12,
            priority=10,
            reason="正式原文证据",
            chapter_ordinal=3,
        ),
    ]

    selection = _select_context_materials(materials, budget_chars=24)

    assert selection.selected_chars <= 24
    assert [item.key for item in selection.selected] == ["chapter-1", "chapter-2"]
    assert [item.key for item in selection.omitted] == ["evidence-3"]
    assert selection.manifest()["omitted_reasons"] == {"上下文预算不足": 1}


def test_context_selection_is_deterministic_for_equal_priorities() -> None:
    materials = [
        ContextMaterial(key="b", kind="event", text="b" * 5, priority=10, reason="覆盖"),
        ContextMaterial(key="a", kind="event", text="a" * 5, priority=10, reason="覆盖"),
    ]

    first = _select_context_materials(materials, budget_chars=5)
    second = _select_context_materials(materials, budget_chars=5)

    assert [item.key for item in first.selected] == [item.key for item in second.selected]
    assert len(first.omitted) == len(second.omitted) == 1


def _chapters(count: int) -> list[dict[str, object]]:
    return [
        {"chapter_number": index, "title": f"第{index}章 标题{index}"}
        for index in range(1, count + 1)
    ]


def _event(index: int, chapter: int) -> dict[str, object]:
    return {
        "id": f"event-{index}",
        "title": f"事件{index}",
        "summary": f"事件{index}的简短经过和结果。",
        "people": [f"人物{index}"],
        "related_entities": [],
        "evidence_ids": [f"evidence-{index}", f"evidence-extra-{index}"],
        "chapter_ordinals": [chapter],
        "confidence": 80 + index % 20,
        "mention_count": index % 3 + 1,
        "start_char": chapter * 1000,
    }


def test_chapter_digests_cover_the_whole_book_without_gaps() -> None:
    digests = _build_chapter_digests(_chapters(100), [])

    assert len(digests) == 20
    assert digests[0]["start_chapter"] == 1
    assert digests[-1]["end_chapter"] == 100
    for previous, current in zip(digests, digests[1:]):
        assert previous["end_chapter"] + 1 == current["start_chapter"]
    assert sum(int(item["chapter_count"]) for item in digests) == 100


def test_chapter_digest_count_adapts_to_book_length() -> None:
    assert len(_build_chapter_digests(_chapters(10), [])) == 10
    assert len(_build_chapter_digests(_chapters(100), [])) == 20
    assert len(_build_chapter_digests(_chapters(600), [])) == 30
    assert len(_build_chapter_digests(_chapters(1_000), [])) == 40
    assert len(_build_chapter_digests(_chapters(2_000), [])) == 40


def test_chapter_digest_records_omitted_events_and_source_references() -> None:
    events = [_event(index, 1) for index in range(1, 6)]

    digest = _build_chapter_digests(_chapters(1), events)[0]

    assert digest["event_count"] == 5
    assert digest["omitted_event_count"] == 4
    assert digest["authority"] == "DERIVED_NAVIGATION_ONLY"
    assert digest["main_events"][0]["event_id"] in {item["id"] for item in events}
    assert digest["main_events"][0]["evidence_ids"]


def test_chapter_digest_order_is_stable_when_event_input_changes() -> None:
    events = [_event(index, (index % 50) + 1) for index in range(1, 80)]

    first = _build_chapter_digests(_chapters(200), events)
    second = _build_chapter_digests(_chapters(200), list(reversed(events)))

    assert first == second


def test_long_book_digest_context_stays_bounded_and_keeps_range_coverage() -> None:
    chapters = _chapters(1_000)
    events = [_event(index, (index * 17) % 1_000 + 1) for index in range(1, 401)]
    selected, manifest = _build_synthesis_context(
        foundation={"characters": [], "related_entities": [], "events": events},
        chapters=chapters,
        evidence_by_id={},
        chapter_title_by_id={},
        source_chars=2_000_000,
        profile=SimpleNamespace(max_output_tokens=16_000),
    )

    assert manifest["selected_chars"] <= manifest["budget_chars"]
    assert selected["context"]["chapter_digest_count"] == 40
    assert selected["context"]["chapter_digest_complete"] is True
    assert selected["chapter_digests"][0]["start_chapter"] == 1
    assert selected["chapter_digests"][-1]["end_chapter"] == 1_000


def test_known_model_context_window_controls_input_budget() -> None:
    selected, manifest = _build_synthesis_context(
        foundation={"characters": [], "related_entities": [], "events": []},
        chapters=_chapters(50),
        evidence_by_id={},
        chapter_title_by_id={},
        source_chars=500_000,
        profile=SimpleNamespace(max_output_tokens=8_000, context_window_tokens=64_000),
    )

    assert manifest["budget_chars"] == 64_000 - 8_000 - 4_096
    assert selected["context"]["budget_source"] == "MODEL_CONTEXT_WINDOW"
    assert selected["context"]["context_window_tokens"] == 64_000
    assert selected["context"]["output_reserve_tokens"] == 8_000


def test_unknown_model_context_window_uses_conservative_auto_budget() -> None:
    selected, _manifest = _build_synthesis_context(
        foundation={"characters": [], "related_entities": [], "events": []},
        chapters=_chapters(50),
        evidence_by_id={},
        chapter_title_by_id={},
        source_chars=500_000,
        profile=SimpleNamespace(max_output_tokens=8_000, context_window_tokens=None),
    )

    assert selected["context"]["budget_source"] == "CONSERVATIVE_AUTO"
    assert selected["context"]["context_window_tokens"] is None
