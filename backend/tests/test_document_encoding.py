from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

EXPECTED = {
    "README.md": "AI 自动小说拆书分析器",
    "docs/CURRENT_BASELINE.md": "统一产品与系统设计基线 V1.0",
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
