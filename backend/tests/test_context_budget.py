from app.services.analysis import ContextMaterial, _select_context_materials


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
