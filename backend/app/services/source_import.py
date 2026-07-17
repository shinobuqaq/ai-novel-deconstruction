from __future__ import annotations

import hashlib
import io
import json
import posixpath
import re
import shutil
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
)


SUPPORTED_FORMATS = {"txt", "md", "docx", "epub"}
MAX_ARCHIVE_EXPANDED_BYTES = 512 * 1024 * 1024


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
    return extension


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


_CHAPTER_LINE = re.compile(
    r"^(?:"
    r"第[0-9０-９零〇一二三四五六七八九十百千万两]+[卷章节回部篇集幕](?:[ \t：:、.\-—]*.*)?"
    r"|卷[ \t]*[0-9０-９零〇一二三四五六七八九十百千万两]+(?:[ \t：:、.\-—]+.*)?"
    r"|chapter[ \t]+[0-9０-９ivxlcdm]+(?:[ \t：:、.\-—]+.*)?"
    r")$",
    re.IGNORECASE,
)
_HEADING = re.compile(
    r"(?m)^[ \t]*(?P<title>#{1,6}[ \t]+[^\n]+|"
    r"第[0-9０-９零〇一二三四五六七八九十百千万两]+[卷章节回部篇集幕][^\n]*|"
    r"卷[ \t]*[0-9０-９零〇一二三四五六七八九十百千万两]+[^\n]*|"
    r"chapter[ \t]+[0-9０-９ivxlcdm]+[^\n]*)[ \t]*$",
    re.IGNORECASE,
)


def _display_title(raw: str) -> str:
    return re.sub(r"^#{1,6}\s+", "", raw.strip()).strip()[:500] or "未命名章节"


def parse_chapters(text: str, source_format: str) -> tuple[tuple[ParsedChapter, ...], tuple[ParsedIssue, ...]]:
    matches = list(_HEADING.finditer(text))
    if source_format == "md" and len(matches) > 1:
        first_title = _display_title(matches[0].group("title"))
        later_has_chapter = any(_CHAPTER_LINE.match(_display_title(item.group("title"))) for item in matches[1:])
        if matches[0].start() == 0 and later_has_chapter and not _CHAPTER_LINE.match(first_title):
            matches = matches[1:]

    chapters: list[ParsedChapter] = []
    issues: list[ParsedIssue] = []
    ranges: list[tuple[str, str, int, int, int]] = []
    if not matches:
        ranges.append(("全文", "DOCUMENT", 0, len(text), 0))
        issues.append(ParsedIssue(
            code="CHAPTER_TITLE_NOT_DETECTED",
            severity="REVIEW",
            message="没有识别到明确的章节标题，当前按一篇全文导入。",
        ))
    else:
        prefix = text[: matches[0].start()]
        if prefix.strip():
            ranges.append(("正文前内容", "PREFACE", 0, matches[0].start(), 0))
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            ranges.append((_display_title(match.group("title")), "CHAPTER", match.start(), end, match.end()))

    seen_body_hashes: dict[str, int] = {}
    for ordinal, (title, unit_type, start, end, body_start) in enumerate(ranges, start=1):
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
        if not body:
            issues.append(ParsedIssue(
                code="CHAPTER_EMPTY",
                severity="WARNING",
                message=f"“{title}”没有正文内容。",
                unit_ordinal=ordinal,
            ))
        elif body_hash in seen_body_hashes:
            issues.append(ParsedIssue(
                code="CHAPTER_DUPLICATE_CONTENT",
                severity="BLOCKING",
                message=f"“{title}”与第 {seen_body_hashes[body_hash]} 个章节正文重复，请确认是否保留。",
                unit_ordinal=ordinal,
                details={"duplicate_of_ordinal": seen_body_hashes[body_hash]},
            ))
        else:
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
        original_relative_path="pending",
        text_relative_path="pending",
        total_chars=len(parsed.text),
        chapter_count=len(chapters),
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
