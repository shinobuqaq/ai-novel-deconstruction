from __future__ import annotations

import pytest

from app.models import EvidenceSpan
from app.services.opening_structure import (
    OPENING_INFORMATION_MODULES,
    OpeningStructureOutput,
    _validate_and_compile,
)


def _span(chapter: int, paragraph: int) -> EvidenceSpan:
    start = chapter * 1000 + paragraph * 100
    return EvidenceSpan(
        id=f"evd_{chapter}_{paragraph}",
        source_version_id="svr_test",
        source_unit_id=f"unt_{chapter}",
        paragraph_index=paragraph - 1,
        start_char=start,
        end_char=start + 50,
        text_snapshot=f"第 {chapter} 章第 {paragraph} 段",
        context_hash=f"hash_{chapter}_{paragraph}",
    )


def _output() -> OpeningStructureOutput:
    return OpeningStructureOutput.model_validate({
        "opening_scene": {
            "opening_type": "日常被打破",
            "story_start_paragraph": 1,
            "protagonist_first_paragraph": 2,
            "protagonist_action": "正在拆开陌生来信",
            "initial_trouble": "来信要求他立刻离开旧宅",
            "first_sentence_function": "制造异常",
            "first_paragraph_function": "建立悬念",
            "explanation": "平静行动被陌生要求打断。",
        },
        "chapter_tasks": [
            {
                "chapter_ordinal": chapter,
                "tasks": [f"完成第 {chapter} 章任务"],
                "key_event_paragraphs": [2],
                "explanation": "关键变化发生在第二段。",
            }
            for chapter in range(1, 4)
        ],
        "paragraph_segments": [
            {
                "chapter_ordinal": 1,
                "paragraph_start": 1,
                "paragraph_end": 2,
                "scope": "STORY",
                "function": "建立主角处境",
                "information_modules": ["主角困境"],
                "explanation": "先展示主角遭遇。",
            },
            {
                "chapter_ordinal": 2,
                "paragraph_start": 1,
                "paragraph_end": 2,
                "scope": "STORY",
                "function": "展示特殊能力",
                "information_modules": ["核心能力"],
                "explanation": "能力在行动中第一次产生作用。",
            },
            {
                "chapter_ordinal": 3,
                "paragraph_start": 1,
                "paragraph_end": 2,
                "scope": "STORY",
                "function": "确立短期行动",
                "information_modules": ["短期目标"],
                "explanation": "主角作出下一步选择。",
            },
        ],
        "module_assessments": [
            {
                "module": module,
                "status": (
                    "SUPPORTED"
                    if module == "核心能力"
                    else "NOT_OBSERVED"
                ),
                "first_chapter_ordinal": 2 if module == "核心能力" else None,
                "first_paragraph": 1 if module == "核心能力" else None,
                "finding": (
                    "能力第一次实际生效。"
                    if module == "核心能力"
                    else "当前三章没有足够依据。"
                ),
            }
            for module in OPENING_INFORMATION_MODULES
        ],
        "overall_sequence": "先建立困境，再展示能力，最后确立短期目标。",
        "limitations": ["当前只有单书，不能形成跨书节奏区间。"],
    })


def _context() -> tuple[
    list[dict[str, object]],
    dict[tuple[int, int], EvidenceSpan],
]:
    by_position = {
        (chapter, paragraph): _span(chapter, paragraph)
        for chapter in range(1, 4)
        for paragraph in range(1, 3)
    }
    chapters = [
        {
            "chapter_ordinal": chapter,
            "chapter_title": f"第 {chapter} 章",
            "paragraph_count": 2,
            "paragraphs": [],
        }
        for chapter in range(1, 4)
    ]
    return chapters, by_position


def test_opening_structure_compiles_objective_positions_without_forcing_modules() -> None:
    chapters, by_position = _context()

    payload = _validate_and_compile(
        _output(),
        chapters,
        by_position,
    )

    assert payload["coverage"] == {
        "chapter_count": 3,
        "paragraph_count": 6,
        "story_character_count": 300,
        "paragraph_coverage_complete": True,
        "paragraph_sequence_contiguous": True,
        "information_module_assessed_count": 6,
        "analysis_policy": "FIRST_THREE_CHAPTERS_ALL_PARAGRAPHS",
    }
    assert payload["opening_scene"]["protagonist_first_source_char"] == 1200
    timeline = {
        item["module"]: item
        for item in payload["information_timeline"]
    }
    assert timeline["核心能力"]["character_count"] == 100
    assert timeline["主角性格"]["status"] == "NOT_OBSERVED"
    assert timeline["主角性格"]["evidence_ids"] == []


def test_opening_structure_rejects_only_objective_paragraph_gap() -> None:
    chapters, by_position = _context()
    output = _output()
    output.paragraph_segments[0].paragraph_start = 2

    with pytest.raises(
        ValueError,
        match="OPENING_STRUCTURE_PARAGRAPH_COVERAGE_INVALID",
    ):
        _validate_and_compile(output, chapters, by_position)
