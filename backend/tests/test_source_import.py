from __future__ import annotations

import io
import zipfile

import pytest
from sqlalchemy import select

from app.models import EvidenceSpan, SourceVersion
from app.services.source_import import parse_chapters, parse_source


def _docx_bytes(paragraphs: list[str]) -> bytes:
    body = "".join(
        f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("word/document.xml", document)
    return output.getvalue()


def _epub_bytes() -> bytes:
    container = """<?xml version="1.0"?>
    <container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
      <rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles>
    </container>"""
    package = """<?xml version="1.0"?>
    <package xmlns="http://www.idpf.org/2007/opf">
      <manifest>
        <item id="c1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
        <item id="c2" href="chapter2.xhtml" media-type="application/xhtml+xml"/>
      </manifest>
      <spine><itemref idref="c1"/><itemref idref="c2"/></spine>
    </package>"""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", package)
        archive.writestr(
            "OEBPS/chapter1.xhtml",
            "<html><body><h1>第一章 开始</h1><p>张三推开了门。</p></body></html>",
        )
        archive.writestr(
            "OEBPS/chapter2.xhtml",
            "<html><body><h1>第二章 相遇</h1><p>李四站在门外。</p></body></html>",
        )
    return output.getvalue()


@pytest.mark.parametrize(
    ("filename", "payload", "expected_text"),
    [
        ("novel.txt", "第一章 开始\n正文".encode(), "第一章 开始\n正文"),
        ("novel.md", "# 第一章 开始\n\n正文".encode(), "# 第一章 开始\n\n正文"),
        ("novel.docx", _docx_bytes(["第一章 开始", "正文"]), "第一章 开始\n正文"),
        ("novel.epub", _epub_bytes(), "张三推开了门"),
    ],
)
def test_supported_source_formats_are_parsed(
    filename: str,
    payload: bytes,
    expected_text: str,
) -> None:
    parsed = parse_source(filename, payload)
    assert expected_text in parsed.text


def test_chapter_parser_keeps_exact_ranges_and_flags_duplicates() -> None:
    text = "第一章 开始\n相同正文\n第二章 重复\n相同正文"
    chapters, issues = parse_chapters(text, "txt")

    assert [item.title for item in chapters] == ["第一章 开始", "第二章 重复"]
    assert text[chapters[0].start_char:chapters[0].end_char].startswith("第一章")
    assert issues[0].code == "CHAPTER_DUPLICATE_CONTENT"
    assert issues[0].severity == "BLOCKING"


def test_markdown_book_title_is_kept_as_title_not_empty_chapter() -> None:
    text = "# 我的小说\n\n## 第一章 开始\n正文\n## 第二章 继续\n更多正文"
    chapters, issues = parse_chapters(text, "md")

    assert [item.unit_type for item in chapters] == ["TITLE", "CHAPTER", "CHAPTER"]
    assert chapters[0].title == "我的小说"
    assert not any(item.code == "CHAPTER_EMPTY" for item in issues)


def test_markdown_duplicate_chapter_heading_is_merged_without_swallowing_body() -> None:
    text = (
        "# 我才三岁，怎么会是灭世级灾厄\n"
        "## 第1章 天才陨落就陨落吧\n"
        "第1章 天才陨落就陨落吧【作者提示】\n"
        "以下为正文。\n第一章正文。\n"
        "## 第2章 一口咬下去\n"
        "第2章 一口咬下去这只是正文第一句。\n"
        "第二章正文。"
    )

    chapters, issues = parse_chapters(text, "md")

    assert [item.unit_type for item in chapters] == ["TITLE", "CHAPTER", "CHAPTER"]
    assert [item.title for item in chapters] == [
        "我才三岁，怎么会是灭世级灾厄",
        "第1章 天才陨落就陨落吧",
        "第2章 一口咬下去",
    ]
    assert "第一章正文" in text[chapters[1].start_char:chapters[1].end_char]
    assert "这只是正文第一句" in text[chapters[2].start_char:chapters[2].end_char]
    assert not any(item.code == "CHAPTER_EMPTY" for item in issues)


def test_real_empty_chapter_blocks_confirmation() -> None:
    chapters, issues = parse_chapters("第一章 空章\n第二章 有正文\n正文", "txt")

    assert chapters[0].body_is_empty is True
    empty = next(item for item in issues if item.code == "CHAPTER_EMPTY")
    assert empty.severity == "BLOCKING"


def test_markdown_import_counts_chapters_without_preface(client) -> None:
    project = client.post("/api/projects", json={"name": "章节统计"}).json()
    response = client.post(
        f"/api/projects/{project['id']}/sources/import?filename=book.md",
        content="# 书名\n\n## 第一章\n正文\n## 第二章\n正文二".encode(),
    )

    assert response.status_code == 201
    assert response.json()["version"]["chapter_count"] == 2
    assert len(response.json()["units"]) == 3


def test_import_api_builds_chapters_evidence_and_confirmation_gate(client) -> None:
    project = client.post("/api/projects", json={"name": "真实小说"}).json()
    payload = "第一章 开始\n张三推开了门。\n第二章 重复\n张三推开了门。".encode("utf-8")
    imported = client.post(
        f"/api/projects/{project['id']}/sources/import?filename=novel.txt",
        content=payload,
        headers={"content-type": "application/octet-stream"},
    )

    assert imported.status_code == 201
    result = imported.json()
    assert result["version"]["total_chars"] > 20
    assert result["version"]["chapter_count"] == 2
    assert [unit["title"] for unit in result["units"]] == ["第一章 开始", "第二章 重复"]
    duplicate = next(issue for issue in result["issues"] if issue["code"] == "CHAPTER_DUPLICATE_CONTENT")

    blocked = client.post(f"/api/source-versions/{result['version']['id']}/confirm")
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "SOURCE_BLOCKING_ISSUES"

    resolved = client.post(f"/api/source-issues/{duplicate['id']}/resolve")
    assert resolved.json()["status"] == "RESOLVED"
    confirmed = client.post(f"/api/source-versions/{result['version']['id']}/confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "CONFIRMED"

    chapter = client.get(f"/api/chapters/{result['units'][0]['id']}/content").json()
    assert chapter["content"] == "第一章 开始\n张三推开了门。\n"

    with client.app.state.session_factory() as session:
        evidence_id = session.scalar(select(EvidenceSpan.id))
    evidence = client.get(f"/api/evidence/{evidence_id}")
    assert evidence.status_code == 200
    snapshot = evidence.json()["evidence"]["text_snapshot"]
    assert snapshot in evidence.json()["context_text"]


def test_reimporting_identical_file_reuses_source_version(client) -> None:
    project = client.post("/api/projects", json={"name": "幂等导入"}).json()
    url = f"/api/projects/{project['id']}/sources/import?filename=same.txt"
    payload = "第一章\n正文".encode()

    first = client.post(url, content=payload).json()
    second = client.post(url, content=payload).json()

    assert first["version"]["id"] == second["version"]["id"]
    assert second["reused_existing"] is True


def test_new_parser_version_reparses_unchanged_file(client) -> None:
    project = client.post("/api/projects", json={"name": "解析升级"}).json()
    url = f"/api/projects/{project['id']}/sources/import?filename=upgrade.md"
    payload = "# 书名\n## 第一章 开始\n第一章 开始【提示】\n正文".encode()
    first = client.post(url, content=payload).json()

    with client.app.state.session_factory() as session:
        version = session.get(SourceVersion, first["version"]["id"])
        assert version is not None
        version.parser_version = 1
        session.commit()

    second = client.post(url, content=payload)

    assert second.status_code == 201
    result = second.json()
    assert result["reused_existing"] is False
    assert result["version"]["id"] != first["version"]["id"]
    assert result["version"]["version_no"] == 2
    assert result["version"]["parser_version"] == 2
    assert result["version"]["chapter_count"] == 1


def test_gb18030_import_reports_readable_warning(client) -> None:
    project = client.post("/api/projects", json={"name": "编码测试"}).json()
    response = client.post(
        f"/api/projects/{project['id']}/sources/import?filename=gb.txt",
        content="第一章\n中文正文".encode("gb18030"),
    )

    assert response.status_code == 201
    assert response.json()["version"]["detected_encoding"] == "gb18030"
    assert response.json()["issues"][0]["code"] == "SOURCE_ENCODING_FALLBACK"


def test_unsupported_format_has_plain_error(client) -> None:
    project = client.post("/api/projects", json={"name": "格式测试"}).json()
    response = client.post(
        f"/api/projects/{project['id']}/sources/import?filename=novel.pdf",
        content=b"pdf",
    )

    assert response.status_code == 415
    assert response.json()["detail"]["code"] == "SOURCE_FORMAT_UNSUPPORTED"
