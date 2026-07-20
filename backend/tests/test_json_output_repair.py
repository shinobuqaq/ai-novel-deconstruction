from __future__ import annotations

import pytest

from app.providers.json_output import JsonTextParseError, parse_json_text


@pytest.mark.parametrize(
    ("raw_text", "expected_repairs"),
    [
        (
            '```json\n{"entities": [], "events": []}\n```',
            ("extract_markdown_code_block",),
        ),
        (
            '以下是结果：\n{"entities": [], "events": []}\n请查收。',
            ("extract_complete_json_object",),
        ),
        (
            '{"entities": [], "events": [],}',
            ("remove_trailing_commas",),
        ),
        (
            '说明：```json\n{"entities": [], "events": [],}\n```',
            ("extract_markdown_code_block", "remove_trailing_commas"),
        ),
    ],
)
def test_safe_json_repairs(raw_text: str, expected_repairs: tuple[str, ...]) -> None:
    result = parse_json_text(raw_text)

    assert result.value == {"entities": [], "events": []}
    assert result.repairs == expected_repairs


def test_does_not_guess_truncated_json_content() -> None:
    with pytest.raises(JsonTextParseError) as caught:
        parse_json_text('{"entities": [{"name": "林舟"}], "events": [')

    diagnostics = caught.value.diagnostics()
    assert diagnostics["classification"] == "likely_truncated_output"
    assert diagnostics["raw_output_chars"] > 0
    assert diagnostics["repair_attempts"]


def test_does_not_replace_non_json_quote_syntax() -> None:
    with pytest.raises(JsonTextParseError):
        parse_json_text("{'entities': [], 'events': []}")
