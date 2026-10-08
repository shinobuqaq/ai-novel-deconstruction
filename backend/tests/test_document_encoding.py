from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

EXPECTED = {
    "README.md": "AI 小说拆解工作台",
    "docs/01_PRODUCT_METHODOLOGY.md": "产品哲学与创作方法论白皮书",
    "docs/02_SYSTEM_ARCHITECTURE.md": "系统技术架构与设计基线",
    "docs/03_LEARNING_HANDBOOK_SPEC.md": "创作学习手册规约与标准范例",
    "docs/04_NEW_BOOK_COCREATION.md": "新书共创规范与标准开书包",
}

MOJIBAKE_FRAGMENTS = (
    "鑷姩",
    "灏忚",
    "鎷嗕功",
    "銆",
    "锛",
    "鈫",
)


def test_key_chinese_documents_are_valid_utf8() -> None:
    for relative_path, expected_text in EXPECTED.items():
        path = ROOT / relative_path
        raw = path.read_bytes()
        text = raw.decode("utf-8")

        assert expected_text in text
        assert "\ufffd" not in text
        assert not any(0xE000 <= ord(char) <= 0xF8FF for char in text)
        assert not any(fragment in text for fragment in MOJIBAKE_FRAGMENTS)
