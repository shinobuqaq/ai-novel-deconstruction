from __future__ import annotations

import hashlib
import io
import json
import posixpath
import re
import shutil
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import (
    EvidenceSpan,
    Project,
    SourceDocument,
    SourceIssue,
    SourceIssueStatus,
    SourceUnit,
    SourceVersion,
    SourceVersionStatus,
    new_id,
)


SUPPORTED_FORMATS = {"txt", "md", "markdown", "docx", "epub"}
MAX_ARCHIVE_EXPANDED_BYTES = 512 * 1024 * 1024
SOURCE_PARSER_VERSION = 3


class SourceImportError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class ParsedSource:
    text: str
    source_format: str
    detected_encoding: str | None
    warnings: tuple["ParsedIssue", ...] = ()


@dataclass(frozen=True, slots=True)
class ParsedChapter:
    ordinal: int
    unit_type: str
    title: str
    start_char: int
    end_char: int
    content_hash: str
    body_hash: str
    body_is_empty: bool


@dataclass(slots=True)
class _HeadingCandidate:
    raw_title: str
    start_char: int
    end_char: int
    unit_type: str
    confidence: str
    recognized: bool = True
    is_markdown: bool = False
    sequence_number: int | None = None
    sequence_style: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedIssue:
    code: str
    severity: str
    message: str
    unit_ordinal: int | None = None
    details: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class SourceImportResult:
    document: SourceDocument
    version: SourceVersion
    units: tuple[SourceUnit, ...]
    issues: tuple[SourceIssue, ...]
    reused_existing: bool = False


@dataclass(frozen=True, slots=True)
class SourceStructureEditResult:
    version: SourceVersion
    units: tuple[SourceUnit, ...]
    issues: tuple[SourceIssue, ...]
    selected_unit_id: str


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_filename(filename: str) -> str:
    name = Path(filename).name.strip()
    if not name:
        raise SourceImportError("SOURCE_FILENAME_REQUIRED", "请选择一个小说文件。")
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).rstrip(". ")
    return cleaned or "novel.txt"


def _source_format(filename: str) -> str:
    extension = Path(filename).suffix.lower().lstrip(".")
    if extension not in SUPPORTED_FORMATS:
        raise SourceImportError(
            "SOURCE_FORMAT_UNSUPPORTED",
            "当前只支持 TXT、Markdown、DOCX 和 EPUB 文件。",
            status_code=415,
        )
    return "md" if extension == "markdown" else extension


def _normalize_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    normalized = "\n".join(line.rstrip() for line in normalized.split("\n"))
    return normalized.strip("\ufeff\n ")


def _decode_text(payload: bytes) -> tuple[str, str, tuple[ParsedIssue, ...]]:
    if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return payload.decode("utf-16"), "utf-16", ()
        except UnicodeDecodeError as exc:
            raise SourceImportError("SOURCE_ENCODING_INVALID", "文件的 UTF-16 编码不完整。") from exc

    try:
        return payload.decode("utf-8-sig"), "utf-8", ()
    except UnicodeDecodeError:
        pass

    try:
        text = payload.decode("gb18030")
    except UnicodeDecodeError as exc:
        raise SourceImportError(
            "SOURCE_ENCODING_UNSUPPORTED",
            "无法识别文件编码，请将文件另存为 UTF-8 后重试。",
        ) from exc
    warning = ParsedIssue(
        code="SOURCE_ENCODING_FALLBACK",
        severity="WARNING",
        message="文件不是 UTF-8，系统已按常见中文编码读取；请抽查原文是否正常。",
        details={"detected_encoding": "gb18030"},
    )
    return text, "gb18030", (warning,)


def _safe_zip(payload: bytes) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except (zipfile.BadZipFile, OSError) as exc:
        raise SourceImportError("SOURCE_ARCHIVE_INVALID", "文件结构已损坏，无法读取。") from exc
    expanded_size = sum(item.file_size for item in archive.infolist())
    if expanded_size > MAX_ARCHIVE_EXPANDED_BYTES:
        archive.close()
        raise SourceImportError(
            "SOURCE_ARCHIVE_EXPANDED_TOO_LARGE",
            "压缩文件展开后的内容异常大，为保护电脑已停止读取。",
        )
    return archive


def _docx_text(payload: bytes) -> str:
    with _safe_zip(payload) as archive:
        try:
            document_xml = archive.read("word/document.xml")
        except KeyError as exc:
            raise SourceImportError("DOCX_DOCUMENT_MISSING", "DOCX 中没有找到正文内容。") from exc
    try:
        root = ElementTree.fromstring(document_xml)
    except ElementTree.ParseError as exc:
        raise SourceImportError("DOCX_XML_INVALID", "DOCX 正文结构已损坏。") from exc

    word_ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{word_ns}p"):
        parts: list[str] = []
        for node in paragraph.iter():
            if node.tag == f"{word_ns}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{word_ns}tab":
                parts.append("\t")
            elif node.tag in {f"{word_ns}br", f"{word_ns}cr"}:
                parts.append("\n")
        paragraphs.append("".join(parts))
    return "\n".join(paragraphs)


class _EpubHtmlText(HTMLParser):
    block_tags = {
        "address", "article", "aside", "blockquote", "br", "div", "footer",
        "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main",
        "nav", "ol", "p", "pre", "section", "table", "tr", "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_depth = 0
        self.body_seen = False

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "head"}:
            self.ignored_depth += 1
        if tag == "body":
            self.body_seen = True
        if tag in self.block_tags and self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "head"} and self.ignored_depth:
            self.ignored_depth -= 1
        if tag in self.block_tags and self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.ignored_depth == 0 and (self.body_seen or data.strip()):
            self.parts.append(data)

    def text(self) -> str:
        value = "".join(self.parts)
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r"\n[ \t]+", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _epub_text(payload: bytes) -> str:
    with _safe_zip(payload) as archive:
        try:
            container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
        except (KeyError, ElementTree.ParseError) as exc:
            raise SourceImportError("EPUB_CONTAINER_INVALID", "EPUB 缺少有效的目录信息。") from exc
        rootfile = next(
            (
                item.attrib.get("full-path")
                for item in container.iter()
                if _local_name(item.tag) == "rootfile"
            ),
            None,
        )
        if not rootfile:
            raise SourceImportError("EPUB_PACKAGE_MISSING", "EPUB 没有找到正文目录。")
        try:
            package = ElementTree.fromstring(archive.read(rootfile))
        except (KeyError, ElementTree.ParseError) as exc:
            raise SourceImportError("EPUB_PACKAGE_INVALID", "EPUB 正文目录已损坏。") from exc

        manifest: dict[str, str] = {}
        for item in package.iter():
            if _local_name(item.tag) != "item":
                continue
            item_id = item.attrib.get("id")
            href = item.attrib.get("href")
            media_type = item.attrib.get("media-type", "")
            if item_id and href and media_type in {"application/xhtml+xml", "text/html"}:
                manifest[item_id] = href

        base = posixpath.dirname(rootfile)
        documents: list[str] = []
        for itemref in package.iter():
            if _local_name(itemref.tag) != "itemref" or itemref.attrib.get("linear", "yes") == "no":
                continue
            href = manifest.get(itemref.attrib.get("idref", ""))
            if not href:
                continue
            entry = posixpath.normpath(posixpath.join(base, href))
            if PurePosixPath(entry).is_absolute() or entry.startswith("../"):
                continue
            try:
                raw_html = archive.read(entry)
            except KeyError:
                continue
            parser = _EpubHtmlText()
            try:
                parser.feed(raw_html.decode("utf-8-sig"))
            except UnicodeDecodeError:
                parser.feed(raw_html.decode("utf-8", errors="replace"))
            text = parser.text()
            if text:
                documents.append(text)
    if not documents:
        raise SourceImportError("EPUB_TEXT_MISSING", "EPUB 中没有找到可读取的正文。")
    return "\n\n".join(documents)


def parse_source(filename: str, payload: bytes) -> ParsedSource:
    if not payload:
        raise SourceImportError("SOURCE_FILE_EMPTY", "文件是空的，请重新选择。")
    source_format = _source_format(filename)
    if source_format in {"txt", "md"}:
        text, encoding, warnings = _decode_text(payload)
    elif source_format == "docx":
        text, encoding, warnings = _docx_text(payload), None, ()
    else:
        text, encoding, warnings = _epub_text(payload), None, ()
    text = _normalize_text(text)
    if not text:
        raise SourceImportError("SOURCE_TEXT_EMPTY", "文件中没有可分析的正文。")
    if "\ufffd" in text:
        warnings = (*warnings, ParsedIssue(
            code="SOURCE_REPLACEMENT_CHARACTER",
            severity="WARNING",
            message="正文中出现无法识别的字符，请抽查原文。",
        ))
    return ParsedSource(text, source_format, encoding, tuple(warnings))


_CHAPTER_NUMBER = r"0-9０-９零〇一二三四五六七八九十百千万两壹贰叁肆伍陆柒捌玖拾佰仟"
_WRAPPER_LINE_PATTERN = (
    r"(?:引子|尾声)(?:[^\n]{0,120})"
    r"|(?:楔子|序章|终章|番外|后记)(?:[ \t·•・：:\-—《][^\n]{0,120})?"
)
_CHAPTER_LINE = re.compile(
    rf"^(?:"
    rf"第[{_CHAPTER_NUMBER}]+[卷章回部篇集幕话](?:[ \t：:、.\-—]*.*)?"
    rf"|卷[ \t]*[{_CHAPTER_NUMBER}]+(?:[ \t：:、.\-—]+.*)?"
    rf"|chapter[ \t]+[0-9０-９ivxlcdm]+(?:[ \t：:、.\-—]+.*)?"
    rf"|{_WRAPPER_LINE_PATTERN}"
    rf")$",
    re.IGNORECASE,
)
_SECTION_LINE = re.compile(
    rf"^第[{_CHAPTER_NUMBER}]+节(?:[ \t：:、.\-—…]*.*)?$",
    re.IGNORECASE,
)
_VOLUME_LINE = re.compile(
    rf"^(?:第[{_CHAPTER_NUMBER}]+卷|卷[ \t]*[{_CHAPTER_NUMBER}]+)(?:[ \t：:、.\-—]*.*)?$",
    re.IGNORECASE,
)
_HEADING = re.compile(
    rf"(?m)^[ \t]*(?P<title>#{{1,6}}[ \t]+[^\n]+|"
    rf"第[{_CHAPTER_NUMBER}]+[卷章回部篇集幕话][^\n]*|"
    rf"第[{_CHAPTER_NUMBER}]+节[^\n]*|"
    rf"卷[ \t]*[{_CHAPTER_NUMBER}]+[^\n]*|"
    rf"chapter[ \t]+[0-9０-９ivxlcdm]+[^\n]*|"
    rf"{_WRAPPER_LINE_PATTERN})[ \t]*$",
    re.IGNORECASE,
)
_BARE_INLINE_LINE = re.compile(
    r"(?P<number>[0-9０-９]{1,6})[ \t]+(?P<title>\S[^\n]{0,79})"
)
_BARE_NUMBER_LINE = re.compile(r"[0-9０-９]{1,6}")
_BARE_ROMAN_LINE = re.compile(
    r"(?P<number>[ivxlcdm]{1,8})[.．、][ \t]*(?P<title>\S[^\n]{0,79})",
    re.IGNORECASE,
)


def _display_title(raw: str) -> str:
    return re.sub(r"^#{1,6}\s+", "", raw.strip()).strip()[:500] or "未命名章节"


def _heading_unit_type(raw: str) -> str | None:
    title = unicodedata.normalize("NFKC", _display_title(raw))
    if _VOLUME_LINE.fullmatch(title):
        return "VOLUME"
    if _CHAPTER_LINE.fullmatch(title):
        return "CHAPTER"
    return None


def _text_lines(text: str) -> list[tuple[int, int, str]]:
    lines: list[tuple[int, int, str]] = []
    offset = 0
    for raw_line in text.splitlines(keepends=True):
        content = raw_line.rstrip("\r\n")
        lines.append((offset, offset + len(content), content))
        offset += len(raw_line)
    if not lines or offset < len(text):
        lines.append((offset, len(text), text[offset:]))
    return lines


def _plausible_bare_title(value: str) -> bool:
    title = value.strip()
    if not title or len(title) > 80:
        return False
    if _BARE_NUMBER_LINE.fullmatch(title) or _BARE_ROMAN_LINE.fullmatch(title):
        return False
    if re.match(r"^[一二三四五六七八九十]+[、.．]", title):
        return False
    return True


def _bare_heading_candidates(text: str) -> list[_HeadingCandidate]:
    lines = _text_lines(text)
    candidates: list[_HeadingCandidate] = []
    consumed_title_lines: set[int] = set()
    for index, (start, end, raw_line) in enumerate(lines):
        title = raw_line.strip()
        if not title or index in consumed_title_lines:
            continue
        before_boundary = index == 0 or not lines[index - 1][2].strip()
        after_boundary = index + 1 >= len(lines) or not lines[index + 1][2].strip()
        if not before_boundary:
            continue

        inline = _BARE_INLINE_LINE.fullmatch(title)
        if inline and after_boundary and _plausible_bare_title(inline.group("title")):
            normalized_number = unicodedata.normalize("NFKC", inline.group("number"))
            candidates.append(_HeadingCandidate(
                raw_title=title,
                start_char=start,
                end_char=end,
                unit_type="CHAPTER",
                confidence="REVIEW",
                sequence_number=int(normalized_number),
                sequence_style="BARE_INLINE",
            ))
            continue

        roman = _BARE_ROMAN_LINE.fullmatch(title)
        if roman and after_boundary and _plausible_bare_title(roman.group("title")):
            candidates.append(_HeadingCandidate(
                raw_title=title,
                start_char=start,
                end_char=end,
                unit_type="CHAPTER",
                confidence="REVIEW",
                sequence_style="ROMAN_INLINE",
            ))
            continue

        if not _BARE_NUMBER_LINE.fullmatch(title):
            continue
        title_index = index + 1
        if title_index < len(lines) and not lines[title_index][2].strip():
            title_index += 1
        if title_index >= len(lines) or title_index > index + 2:
            continue
        title_line = lines[title_index][2].strip()
        title_after_boundary = (
            title_index + 1 >= len(lines)
            or not lines[title_index + 1][2].strip()
        )
        if not title_after_boundary or not _plausible_bare_title(title_line):
            continue
        candidates.append(_HeadingCandidate(
            raw_title=f"{title} {title_line}",
            start_char=start,
            end_char=lines[title_index][1],
            unit_type="CHAPTER",
            confidence="REVIEW",
            sequence_style="BARE_BLOCK",
        ))
        consumed_title_lines.add(title_index)

    inline_candidates = [
        item for item in candidates if item.sequence_style == "BARE_INLINE"
    ]
    run: list[_HeadingCandidate] = []
    for candidate in inline_candidates:
        if (
            run
            and candidate.sequence_number == (run[-1].sequence_number or 0) + 1
        ):
            run.append(candidate)
        else:
            if len(run) >= 3:
                gaps = [
                    current.start_char - previous.start_char
                    for previous, current in zip(run, run[1:])
                ]
                if min(gaps) >= 50 and max(gaps) <= min(gaps) * 4:
                    for item in run:
                        item.confidence = "HIGH"
            run = [candidate]
    if len(run) >= 3:
        gaps = [
            current.start_char - previous.start_char
            for previous, current in zip(run, run[1:])
        ]
        if min(gaps) >= 50 and max(gaps) <= min(gaps) * 4:
            for item in run:
                item.confidence = "HIGH"
    return candidates


def _collect_heading_candidates(
    text: str,
    source_format: str,
) -> list[_HeadingCandidate]:
    candidates: list[_HeadingCandidate] = []
    for match in _HEADING.finditer(text):
        raw_title = match.group("title")
        unit_type = _heading_unit_type(raw_title)
        is_section = bool(_SECTION_LINE.fullmatch(_display_title(raw_title)))
        if is_section:
            unit_type = "CHAPTER"
        candidates.append(_HeadingCandidate(
            raw_title=raw_title,
            start_char=match.start(),
            end_char=match.end(),
            unit_type=unit_type or "CHAPTER",
            confidence="REVIEW" if is_section else "HIGH",
            recognized=unit_type is not None,
            is_markdown=raw_title.lstrip().startswith("#"),
        ))
    candidates.extend(_bare_heading_candidates(text))
    candidates.sort(key=lambda item: (item.start_char, item.end_char))

    if source_format == "md":
        markdown_structural = [
            item for item in candidates if item.is_markdown and item.recognized
        ]
        if markdown_structural:
            candidates = [item for item in candidates if item.is_markdown]
        if (
            len(candidates) > 1
            and candidates[0].start_char == 0
            and candidates[0].is_markdown
            and not candidates[0].recognized
            and any(item.recognized for item in candidates[1:])
        ):
            candidates[0].unit_type = "TITLE"

    non_overlapping: list[_HeadingCandidate] = []
    for candidate in candidates:
        if non_overlapping and candidate.start_char < non_overlapping[-1].end_char:
            continue
        non_overlapping.append(candidate)
    return non_overlapping


def source_unit_display_content(text: str, unit: SourceUnit) -> tuple[int, str]:
    """Return reader-facing content without duplicating the chapter heading.

    Stored source coordinates remain untouched so evidence spans continue to
    point at the exact imported file. Only the chapter reader starts after an
    exact first-line heading.
    """
    content = text[unit.start_char:unit.end_char]
    if unit.unit_type != "CHAPTER":
        return unit.start_char, content
    first_line_end = content.find("\n")
    if first_line_end < 0:
        return unit.start_char, content
    first_line = content[:first_line_end].rstrip("\r")
    if _display_title(first_line) != unit.title:
        return unit.start_char, content
    body_offset = first_line_end + 1
    return unit.start_char + body_offset, content[body_offset:]


def _chapter_identity(raw: str) -> str | None:
    title = unicodedata.normalize("NFKC", _display_title(raw)).casefold()
    match = re.match(
        r"^(第[0-9零〇一二三四五六七八九十百千万两壹贰叁肆伍陆柒捌玖拾佰仟]+[卷章节回部篇集幕话]|"
        r"卷[ ]*[0-9零〇一二三四五六七八九十百千万两壹贰叁肆伍陆柒捌玖拾佰仟]+|"
        r"chapter[ ]+[0-9ivxlcdm]+)",
        title,
    )
    return re.sub(r"\s+", "", match.group(1)) if match else None


def _merge_adjacent_duplicate_headings(
    text: str,
    matches: list[_HeadingCandidate],
) -> list[_HeadingCandidate]:
    merged: list[_HeadingCandidate] = []
    for match in matches:
        if merged:
            previous = merged[-1]
            same_chapter = (
                _chapter_identity(previous.raw_title) is not None
                and _chapter_identity(previous.raw_title)
                == _chapter_identity(match.raw_title)
            )
            if (
                same_chapter
                and not text[previous.end_char:match.start_char].strip()
            ):
                continue
        merged.append(match)
    return merged


def _front_matter_title(value: str) -> str:
    first_line = next(
        (line.strip() for line in value.splitlines() if line.strip()),
        "",
    )
    if (
        len(first_line) >= 2
        and first_line.startswith("《")
        and first_line.endswith("》")
    ):
        first_line = first_line[1:-1].strip()
    return first_line[:500] or "正文前内容"


def parse_chapters(text: str, source_format: str) -> tuple[tuple[ParsedChapter, ...], tuple[ParsedIssue, ...]]:
    matches = _merge_adjacent_duplicate_headings(
        text,
        _collect_heading_candidates(text, source_format),
    )

    chapters: list[ParsedChapter] = []
    issues: list[ParsedIssue] = []
    ranges: list[tuple[str, str, int, int, int, str]] = []
    content_start = 0
    if matches and matches[0].unit_type == "TITLE":
        title_match = matches.pop(0)
        ranges.append((
            _display_title(title_match.raw_title),
            "TITLE",
            title_match.start_char,
            title_match.end_char,
            0,
            "HIGH",
        ))
        content_start = title_match.end_char
    if not matches:
        if text[content_start:].strip():
            ranges.append((
                "全文",
                "DOCUMENT",
                content_start,
                len(text),
                0,
                "HIGH",
            ))
            issues.append(ParsedIssue(
                code="CHAPTER_TITLE_NOT_DETECTED",
                severity="REVIEW",
                message="没有识别到明确的章节标题，当前按一篇全文导入。",
            ))
    else:
        prefix = text[content_start:matches[0].start_char]
        if prefix.strip():
            ranges.append((
                _front_matter_title(prefix),
                "PREFACE",
                content_start,
                matches[0].start_char,
                0,
                "REVIEW",
            ))
        for index, match in enumerate(matches):
            end = (
                matches[index + 1].start_char
                if index + 1 < len(matches)
                else len(text)
            )
            ranges.append((
                _display_title(match.raw_title),
                match.unit_type,
                match.start_char,
                end,
                match.end_char,
                match.confidence,
            ))

    seen_body_hashes: dict[str, int] = {}
    for ordinal, (
        title,
        unit_type,
        start,
        end,
        body_start,
        confidence,
    ) in enumerate(ranges, start=1):
        content = text[start:end]
        body = text[body_start:end].strip() if body_start else content.strip()
        body_hash = _sha256_text(body)
        chapter = ParsedChapter(
            ordinal=ordinal,
            unit_type=unit_type,
            title=title,
            start_char=start,
            end_char=end,
            content_hash=_sha256_text(content),
            body_hash=body_hash,
            body_is_empty=not body,
        )
        chapters.append(chapter)
        if confidence == "REVIEW":
            front_matter = unit_type == "PREFACE"
            issues.append(ParsedIssue(
                code=(
                    "FRONT_MATTER_REVIEW"
                    if front_matter
                    else "CHAPTER_BOUNDARY_REVIEW"
                ),
                severity="BLOCKING",
                message=(
                    f"已把“{title}”识别为作品前置信息，请确认它不属于第一章正文。"
                    if front_matter
                    else f"“{title}”可能是章节标题，请确认这处分章是否正确。"
                ),
                unit_ordinal=ordinal,
                details={
                    "start_char": start,
                    "end_char": body_start or end,
                    "suggested_unit_type": unit_type,
                },
            ))
        if unit_type == "CHAPTER" and not body:
            issues.append(ParsedIssue(
                code="CHAPTER_EMPTY",
                severity="BLOCKING",
                message=f"“{title}”没有正文内容。",
                unit_ordinal=ordinal,
            ))
        elif unit_type == "CHAPTER" and body_hash in seen_body_hashes:
            issues.append(ParsedIssue(
                code="CHAPTER_DUPLICATE_CONTENT",
                severity="BLOCKING",
                message=f"“{title}”与第 {seen_body_hashes[body_hash]} 个章节正文重复，请确认是否保留。",
                unit_ordinal=ordinal,
                details={"duplicate_of_ordinal": seen_body_hashes[body_hash]},
            ))
        elif unit_type == "CHAPTER":
            seen_body_hashes[body_hash] = ordinal
        if len(content) > 100_000:
            issues.append(ParsedIssue(
                code="CHAPTER_VERY_LONG",
                severity="WARNING",
                message=f"“{title}”超过 10 万字符，分析时将自动分块。",
                unit_ordinal=ordinal,
                details={"char_count": len(content)},
            ))
    return tuple(chapters), tuple(issues)


def _unit_id(version_id: str, ordinal: int, content_hash: str) -> str:
    digest = _sha256_text(f"{version_id}:{ordinal}:{content_hash}")[:32]
    return f"unt_{digest}"


def _evidence_id(version_id: str, start: int, end: int, snapshot: str) -> str:
    digest = _sha256_text(f"{version_id}:{start}:{end}:{_sha256_text(snapshot)}")[:32]
    return f"evd_{digest}"


def _store_source_files(
    settings: Settings,
    *,
    project_id: str,
    version_id: str,
    filename: str,
    payload: bytes,
    text: str,
) -> tuple[str, str, Path]:
    directory = settings.workspace_dir / "sources" / project_id / version_id
    directory.mkdir(parents=True, exist_ok=False)
    original_dir = directory / "original"
    original_dir.mkdir()
    original = original_dir / _safe_filename(filename)
    extracted = directory / "source.txt"
    original_tmp = original.with_suffix(original.suffix + ".tmp")
    text_tmp = extracted.with_suffix(".txt.tmp")
    original_tmp.write_bytes(payload)
    text_tmp.write_text(text, encoding="utf-8", newline="\n")
    original_tmp.replace(original)
    text_tmp.replace(extracted)
    return (
        original.relative_to(settings.workspace_dir).as_posix(),
        extracted.relative_to(settings.workspace_dir).as_posix(),
        directory,
    )


def import_source(
    session: Session,
    settings: Settings,
    *,
    project: Project,
    filename: str,
    payload: bytes,
) -> SourceImportResult:
    filename = _safe_filename(filename)
    parsed = parse_source(filename, payload)
    chapters, chapter_issues = parse_chapters(parsed.text, parsed.source_format)
    content_hash = _sha256_text(parsed.text)

    document = session.scalar(
        select(SourceDocument).where(
            SourceDocument.project_id == project.id,
            SourceDocument.original_filename == filename,
        )
    )
    if document is not None:
        existing = session.scalar(
            select(SourceVersion).where(
                SourceVersion.document_id == document.id,
                SourceVersion.content_hash == content_hash,
                SourceVersion.parser_version == SOURCE_PARSER_VERSION,
            )
        )
        if existing is not None:
            return SourceImportResult(
                document=document,
                version=existing,
                units=tuple(existing.units),
                issues=tuple(existing.issues),
                reused_existing=True,
            )
        next_version = (session.scalar(
            select(func.max(SourceVersion.version_no)).where(SourceVersion.document_id == document.id)
        ) or 0) + 1
    else:
        document = SourceDocument(
            project_id=project.id,
            original_filename=filename,
            source_format=parsed.source_format,
        )
        session.add(document)
        session.flush()
        next_version = 1

    version = SourceVersion(
        document_id=document.id,
        version_no=next_version,
        content_hash=content_hash,
        parser_version=SOURCE_PARSER_VERSION,
        original_relative_path="pending",
        text_relative_path="pending",
        total_chars=len(parsed.text),
        chapter_count=sum(item.unit_type == "CHAPTER" for item in chapters),
        detected_encoding=parsed.detected_encoding,
        status=SourceVersionStatus.REVIEW.value,
    )
    session.add(version)
    session.flush()

    directory: Path | None = None
    try:
        original_path, text_path, directory = _store_source_files(
            settings,
            project_id=project.id,
            version_id=version.id,
            filename=filename,
            payload=payload,
            text=parsed.text,
        )
        version.original_relative_path = original_path
        version.text_relative_path = text_path

        units: list[SourceUnit] = []
        units_by_ordinal: dict[int, SourceUnit] = {}
        for chapter in chapters:
            unit = SourceUnit(
                id=_unit_id(version.id, chapter.ordinal, chapter.content_hash),
                source_version_id=version.id,
                ordinal=chapter.ordinal,
                unit_type=chapter.unit_type,
                title=chapter.title,
                start_char=chapter.start_char,
                end_char=chapter.end_char,
                content_hash=chapter.content_hash,
                char_count=chapter.end_char - chapter.start_char,
            )
            session.add(unit)
            units.append(unit)
            units_by_ordinal[chapter.ordinal] = unit
        session.flush()

        issues: list[SourceIssue] = []
        for parsed_issue in (*parsed.warnings, *chapter_issues):
            issue = SourceIssue(
                source_version_id=version.id,
                source_unit_id=(
                    units_by_ordinal[parsed_issue.unit_ordinal].id
                    if parsed_issue.unit_ordinal in units_by_ordinal
                    else None
                ),
                code=parsed_issue.code,
                severity=parsed_issue.severity,
                message=parsed_issue.message,
                details_json=json.dumps(parsed_issue.details or {}, ensure_ascii=False, sort_keys=True),
            )
            session.add(issue)
            issues.append(issue)

        for unit in units:
            paragraph_index = 0
            for match in re.finditer(r"[^\n]+", parsed.text[unit.start_char:unit.end_char]):
                snapshot = match.group(0).strip()
                if not snapshot:
                    continue
                start = unit.start_char + match.start() + len(match.group(0)) - len(match.group(0).lstrip())
                end = start + len(snapshot)
                context_start = max(0, start - 80)
                context_end = min(len(parsed.text), end + 80)
                evidence = EvidenceSpan(
                    id=_evidence_id(version.id, start, end, snapshot),
                    source_version_id=version.id,
                    source_unit_id=unit.id,
                    paragraph_index=paragraph_index,
                    start_char=start,
                    end_char=end,
                    text_snapshot=snapshot,
                    context_hash=_sha256_text(parsed.text[context_start:context_end]),
                )
                session.add(evidence)
                paragraph_index += 1

        session.commit()
        session.refresh(document)
        session.refresh(version)
        return SourceImportResult(document, version, tuple(units), tuple(issues))
    except Exception:
        session.rollback()
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)
        raise


def source_text(settings: Settings, version: SourceVersion) -> str:
    path = settings.workspace_dir / Path(version.text_relative_path)
    if not path.is_file():
        raise SourceImportError(
            "SOURCE_TEXT_FILE_MISSING",
            "小说正文文件不存在，请从备份恢复或重新导入。",
            status_code=409,
        )
    return path.read_text(encoding="utf-8")


def _ensure_structure_is_editable(version: SourceVersion) -> None:
    if version.status != SourceVersionStatus.REVIEW.value:
        raise SourceImportError(
            "SOURCE_STRUCTURE_ALREADY_CONFIRMED",
            "章节结构已经确认，不能原地修改；请重新导入为新版本后再校正。",
            status_code=409,
        )


def _source_structure_result(
    session: Session,
    version: SourceVersion,
    selected_unit_id: str,
) -> SourceStructureEditResult:
    units = tuple(session.scalars(
        select(SourceUnit)
        .where(SourceUnit.source_version_id == version.id)
        .order_by(SourceUnit.ordinal)
    ))
    issues = tuple(session.scalars(
        select(SourceIssue)
        .where(SourceIssue.source_version_id == version.id)
        .order_by(SourceIssue.created_at, SourceIssue.id)
    ))
    return SourceStructureEditResult(version, units, issues, selected_unit_id)


def _resolve_unit_issues(
    session: Session,
    unit_id: str,
    *,
    boundary_only: bool,
) -> None:
    stmt = select(SourceIssue).where(
        SourceIssue.source_unit_id == unit_id,
        SourceIssue.status == SourceIssueStatus.OPEN.value,
    )
    if boundary_only:
        stmt = stmt.where(SourceIssue.code.in_({
            "CHAPTER_BOUNDARY_REVIEW",
            "FRONT_MATTER_REVIEW",
        }))
    for issue in session.scalars(stmt):
        issue.status = SourceIssueStatus.RESOLVED.value
        issue.resolved_at = datetime.now(timezone.utc)


def _refresh_source_structure(
    session: Session,
    version: SourceVersion,
    units: list[SourceUnit],
    text: str,
) -> None:
    units.sort(key=lambda item: (item.start_char, item.end_char, item.id))
    if not units:
        raise SourceImportError(
            "SOURCE_STRUCTURE_EMPTY",
            "至少需要保留一个正文单元。",
            status_code=409,
        )
    if units[0].start_char != 0 or units[-1].end_char != len(text):
        raise SourceImportError(
            "SOURCE_STRUCTURE_RANGE_INVALID",
            "章节边界没有完整覆盖原文，系统已拒绝保存。",
            status_code=409,
        )
    for previous, current in zip(units, units[1:]):
        if previous.end_char != current.start_char:
            raise SourceImportError(
                "SOURCE_STRUCTURE_RANGE_INVALID",
                "章节之间出现空白或重叠范围，系统已拒绝保存。",
                status_code=409,
            )

    for index, unit in enumerate(units, start=1):
        unit.ordinal = -index
    session.flush()
    for ordinal, unit in enumerate(units, start=1):
        unit.ordinal = ordinal
        content = text[unit.start_char:unit.end_char]
        unit.content_hash = _sha256_text(content)
        unit.char_count = len(content)
    version.chapter_count = sum(
        unit.unit_type == "CHAPTER" for unit in units
    )

    for unit in units:
        spans = list(session.scalars(
            select(EvidenceSpan)
            .where(EvidenceSpan.source_unit_id == unit.id)
            .order_by(EvidenceSpan.start_char, EvidenceSpan.end_char)
        ))
        for paragraph_index, span in enumerate(spans):
            if (
                span.start_char < unit.start_char
                or span.end_char > unit.end_char
            ):
                raise SourceImportError(
                    "SOURCE_EVIDENCE_RANGE_INVALID",
                    "校正后的章节边界截断了原文证据，请把光标移到完整行的开头再试。",
                    status_code=409,
                )
            span.paragraph_index = paragraph_index


def update_source_unit(
    session: Session,
    settings: Settings,
    unit: SourceUnit,
    *,
    title: str,
    unit_type: str,
) -> SourceStructureEditResult:
    version = unit.source_version
    _ensure_structure_is_editable(version)
    cleaned_title = title.strip()
    if not cleaned_title or "\n" in cleaned_title or "\r" in cleaned_title:
        raise SourceImportError(
            "SOURCE_UNIT_TITLE_INVALID",
            "标题不能为空，也不能包含换行。",
        )
    if unit_type not in {"TITLE", "PREFACE", "VOLUME", "CHAPTER", "DOCUMENT"}:
        raise SourceImportError(
            "SOURCE_UNIT_TYPE_INVALID",
            "请选择有效的单元类型。",
        )
    text = source_text(settings, version)
    unit.title = cleaned_title
    unit.unit_type = unit_type
    _resolve_unit_issues(session, unit.id, boundary_only=True)
    units = list(session.scalars(
        select(SourceUnit).where(SourceUnit.source_version_id == version.id)
    ))
    _refresh_source_structure(session, version, units, text)
    session.commit()
    session.refresh(version)
    return _source_structure_result(session, version, unit.id)


def split_source_unit(
    session: Session,
    settings: Settings,
    unit: SourceUnit,
    *,
    split_char: int,
    title: str,
    unit_type: str,
) -> SourceStructureEditResult:
    version = unit.source_version
    _ensure_structure_is_editable(version)
    cleaned_title = title.strip()
    if not cleaned_title or "\n" in cleaned_title or "\r" in cleaned_title:
        raise SourceImportError(
            "SOURCE_UNIT_TITLE_INVALID",
            "新单元标题不能为空，也不能包含换行。",
        )
    if unit_type not in {"VOLUME", "CHAPTER"}:
        raise SourceImportError(
            "SOURCE_UNIT_TYPE_INVALID",
            "新单元只能设为分卷或章节。",
        )
    text = source_text(settings, version)
    if not unit.start_char < split_char < unit.end_char:
        raise SourceImportError(
            "SOURCE_UNIT_SPLIT_OUT_OF_RANGE",
            "请把光标放在当前单元正文内部。",
            status_code=409,
        )
    if text[split_char - 1] != "\n":
        raise SourceImportError(
            "SOURCE_UNIT_SPLIT_NOT_LINE_BOUNDARY",
            "请把光标放在新标题所在行的开头。",
            status_code=409,
        )
    if (
        not text[unit.start_char:split_char].strip()
        or not text[split_char:unit.end_char].strip()
    ):
        raise SourceImportError(
            "SOURCE_UNIT_SPLIT_EMPTY",
            "切分后两边都必须保留内容。",
            status_code=409,
        )

    spans = list(session.scalars(
        select(EvidenceSpan)
        .where(EvidenceSpan.source_unit_id == unit.id)
        .order_by(EvidenceSpan.start_char)
    ))
    if any(
        span.start_char < split_char < span.end_char
        for span in spans
    ):
        raise SourceImportError(
            "SOURCE_UNIT_SPLIT_CUTS_EVIDENCE",
            "这个位置截断了一行原文，请把光标移到下一行开头。",
            status_code=409,
        )

    original_end = unit.end_char
    unit.end_char = split_char
    new_unit = SourceUnit(
        id=new_id("unt"),
        source_version_id=version.id,
        ordinal=0,
        unit_type=unit_type,
        title=cleaned_title,
        start_char=split_char,
        end_char=original_end,
        content_hash="pending",
        char_count=original_end - split_char,
    )
    session.add(new_unit)
    for span in spans:
        if span.start_char >= split_char:
            span.source_unit_id = new_unit.id

    units = list(session.scalars(
        select(SourceUnit).where(SourceUnit.source_version_id == version.id)
    ))
    if not any(item.id == new_unit.id for item in units):
        units.append(new_unit)
    _refresh_source_structure(session, version, units, text)
    session.commit()
    session.refresh(version)
    return _source_structure_result(session, version, new_unit.id)


def merge_source_unit(
    session: Session,
    settings: Settings,
    unit: SourceUnit,
    *,
    direction: str,
) -> SourceStructureEditResult:
    version = unit.source_version
    _ensure_structure_is_editable(version)
    text = source_text(settings, version)
    units = list(session.scalars(
        select(SourceUnit)
        .where(SourceUnit.source_version_id == version.id)
        .order_by(SourceUnit.ordinal)
    ))
    index = next(
        (position for position, item in enumerate(units) if item.id == unit.id),
        -1,
    )
    if index < 0:
        raise SourceImportError(
            "SOURCE_UNIT_NOT_FOUND",
            "没有找到要合并的章节。",
            status_code=404,
        )
    if direction == "PREVIOUS":
        if index == 0:
            raise SourceImportError(
                "SOURCE_UNIT_MERGE_NO_PREVIOUS",
                "当前已经是第一个单元，无法向前合并。",
                status_code=409,
            )
        survivor = units[index - 1]
        removed = unit
        survivor.end_char = removed.end_char
    elif direction == "NEXT":
        if index + 1 >= len(units):
            raise SourceImportError(
                "SOURCE_UNIT_MERGE_NO_NEXT",
                "当前已经是最后一个单元，无法向后合并。",
                status_code=409,
            )
        survivor = unit
        removed = units[index + 1]
        survivor.end_char = removed.end_char
    else:
        raise SourceImportError(
            "SOURCE_UNIT_MERGE_DIRECTION_INVALID",
            "请选择向前或向后合并。",
        )

    for span in session.scalars(
        select(EvidenceSpan).where(EvidenceSpan.source_unit_id == removed.id)
    ):
        span.source_unit_id = survivor.id
    for issue in session.scalars(
        select(SourceIssue).where(SourceIssue.source_unit_id == removed.id)
    ):
        if issue.status == SourceIssueStatus.OPEN.value:
            issue.status = SourceIssueStatus.RESOLVED.value
            issue.resolved_at = datetime.now(timezone.utc)
        issue.source_unit_id = survivor.id
    session.flush()
    session.delete(removed)
    units = [item for item in units if item.id != removed.id]
    _refresh_source_structure(session, version, units, text)
    session.commit()
    session.refresh(version)
    return _source_structure_result(session, version, survivor.id)


def confirm_source_version(session: Session, version: SourceVersion) -> SourceVersion:
    blocking = session.scalar(
        select(func.count(SourceIssue.id)).where(
            SourceIssue.source_version_id == version.id,
            SourceIssue.status == SourceIssueStatus.OPEN.value,
            SourceIssue.severity == "BLOCKING",
        )
    ) or 0
    if blocking:
        raise SourceImportError(
            "SOURCE_BLOCKING_ISSUES",
            f"还有 {blocking} 个必须确认的问题，处理后才能进入下一步。",
            status_code=409,
        )
    if version.status != SourceVersionStatus.CONFIRMED.value:
        version.status = SourceVersionStatus.CONFIRMED.value
        version.confirmed_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(version)
    return version


def resolve_source_issue(session: Session, issue: SourceIssue) -> SourceIssue:
    if issue.status != SourceIssueStatus.RESOLVED.value:
        issue.status = SourceIssueStatus.RESOLVED.value
        issue.resolved_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(issue)
    return issue
