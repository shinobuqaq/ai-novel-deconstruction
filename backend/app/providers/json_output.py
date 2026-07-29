from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any


_FENCED_BLOCK_RE = re.compile(
    r"```(?:json)?\s*(.*?)\s*```",
    flags=re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class ParsedJsonText:
    value: Any
    repairs: tuple[str, ...]


class JsonTextParseError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        raw_text: str,
        attempts: list[dict[str, object]],
        classification: str,
        decode_error: json.JSONDecodeError | None = None,
    ) -> None:
        super().__init__(message)
        self.raw_text = raw_text
        self.attempts = attempts
        self.classification = classification
        self.decode_error = decode_error

    def diagnostics(self) -> dict[str, object]:
        result: dict[str, object] = {
            "phase": "json_decode",
            "classification": self.classification,
            "raw_output_chars": len(self.raw_text),
            "raw_output_sha256": hashlib.sha256(
                self.raw_text.encode("utf-8")
            ).hexdigest(),
            "repair_attempts": self.attempts,
        }
        if self.decode_error is not None:
            result["decode_error"] = {
                "message": self.decode_error.msg[:300],
                "line": self.decode_error.lineno,
                "column": self.decode_error.colno,
                "position": self.decode_error.pos,
            }
        return result


def parse_json_text(raw_text: str) -> ParsedJsonText:
    """Parse model JSON while applying only deterministic syntax repairs."""
    if not raw_text.strip():
        raise JsonTextParseError(
            "Model output was empty.",
            raw_text=raw_text,
            attempts=[],
            classification="empty_output",
        )

    candidates: list[tuple[str, tuple[str, ...]]] = [(raw_text, ())]
    stripped = raw_text.lstrip("\ufeff").strip()
    if stripped != raw_text:
        candidates.append((stripped, ("strip_wrapper_whitespace",)))

    fenced_blocks = _FENCED_BLOCK_RE.findall(stripped)
    for block in fenced_blocks:
        candidates.append((block.strip(), ("extract_markdown_code_block",)))

    for source, repairs in list(candidates):
        extracted = _extract_complete_object(source)
        if extracted is not None and extracted != source:
            candidates.append(
                (extracted, (*repairs, "extract_complete_json_object"))
            )

    for source, repairs in list(candidates):
        without_trailing_commas = _remove_trailing_commas(source)
        if without_trailing_commas != source:
            candidates.append(
                (without_trailing_commas, (*repairs, "remove_trailing_commas"))
            )

    attempts: list[dict[str, object]] = []
    last_error: json.JSONDecodeError | None = None
    seen: set[str] = set()
    for candidate, repairs in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            return ParsedJsonText(json.loads(candidate), repairs)
        except json.JSONDecodeError as exc:
            last_error = exc
            attempts.append(
                {
                    "repairs": list(repairs),
                    "error": exc.msg[:200],
                    "line": exc.lineno,
                    "column": exc.colno,
                    "position": exc.pos,
                }
            )

    classification = (
        "likely_truncated_output"
        if _has_unclosed_json_container(stripped)
        else "invalid_json_syntax"
    )
    raise JsonTextParseError(
        "Model output was not valid JSON after safe local repairs.",
        raw_text=raw_text,
        attempts=attempts,
        classification=classification,
        decode_error=last_error,
    )


def _extract_complete_object(text: str) -> str | None:
    cursor = 0
    last_complete: str | None = None
    while True:
        start = text.find("{", cursor)
        if start < 0:
            return last_complete
        stack: list[str] = []
        in_string = False
        escaped = False
        end: int | None = None
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char in "[{":
                stack.append(char)
            elif char in "]}":
                if not stack:
                    break
                expected = "[" if char == "]" else "{"
                if stack[-1] != expected:
                    break
                stack.pop()
                if not stack:
                    end = index
                    break
        if end is None:
            return last_complete
        last_complete = text[start:end + 1]
        cursor = end + 1


def _remove_trailing_commas(text: str) -> str:
    result: list[str] = []
    in_string = False
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            result.append(char)
            index += 1
            continue
        if char == ",":
            lookahead = index + 1
            while lookahead < len(text) and text[lookahead].isspace():
                lookahead += 1
            if lookahead < len(text) and text[lookahead] in "]}":
                index += 1
                continue
        result.append(char)
        index += 1
    return "".join(result)


def _has_unclosed_json_container(text: str) -> bool:
    start = text.find("{")
    if start < 0:
        return False
    stack: list[str] = []
    in_string = False
    escaped = False
    for char in text[start:]:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            stack.append(char)
        elif char in "]}":
            if not stack:
                return False
            expected = "[" if char == "]" else "{"
            if stack[-1] != expected:
                return False
            stack.pop()
    return in_string or bool(stack)
