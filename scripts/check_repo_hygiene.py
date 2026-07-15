from __future__ import annotations

import json
import subprocess
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]

TEXT_SUFFIXES = {
    ".css",
    ".env",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".ps1",
    ".py",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}

TEXT_FILENAMES = {
    ".env.example",
    ".gitignore",
}

FORBIDDEN_EXACT = {
    ".env",
}

FORBIDDEN_SUFFIXES = {
    ".db",
    ".db-shm",
    ".db-wal",
    ".pyc",
    ".pyo",
    ".tsbuildinfo",
}

FORBIDDEN_PARTS = {
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
}

SENTINEL_SOURCE_FILES = {
    "backend/tests/test_document_encoding.py",
}

MOJIBAKE_CODEPOINT_GROUPS = (
    (0x9477, 0xE044, 0x59E9),
    (0x704F, 0x5B10, 0x8E47, 0xE1E9),
    (0x93B7, 0x55D5, 0x529F),
    (0x9286,),
    (0x951B,),
    (0x922B,),
)

MOJIBAKE_FRAGMENTS = tuple(
    "".join(chr(code_point) for code_point in group)
    for group in MOJIBAKE_CODEPOINT_GROUPS
)

REQUIRED_FILES = {
    ".env.example",
    "backend/pyproject.toml",
    "frontend/package-lock.json",
}


def git_paths(*arguments: str) -> set[str]:
    result = subprocess.run(
        ["git", *arguments, "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return {
        item.decode("utf-8")
        for item in result.stdout.split(b"\0")
        if item
    }


def repository_paths() -> list[str]:
    tracked = git_paths("ls-files")
    untracked = git_paths("ls-files", "--others", "--exclude-standard")
    return sorted(tracked | untracked)


def is_forbidden(path_text: str) -> str | None:
    path = PurePosixPath(path_text)
    parts = set(path.parts)

    if path_text in FORBIDDEN_EXACT:
        return "local environment file must not be committed"

    if path_text.startswith("secrets/"):
        return "secret directory must not be committed"

    if path_text.startswith("backend/workspace/"):
        return "backend workspace data must not be committed"

    if path_text.startswith("frontend/dist/"):
        return "frontend build output must not be committed"

    if path_text.startswith("workspace/") and path_text != "workspace/.gitkeep":
        return "workspace user data must not be committed"

    if parts & FORBIDDEN_PARTS:
        return "generated cache or dependency directory must not be committed"

    if any(part.endswith(".egg-info") for part in path.parts):
        return "generated Python egg-info directory must not be committed"

    if any(path_text.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES):
        return "generated database, cache or build file must not be committed"

    return None


def should_check_utf8(path: Path) -> bool:
    return path.name in TEXT_FILENAMES or path.suffix.lower() in TEXT_SUFFIXES


def validate_utf8(path: Path, path_text: str) -> list[str]:
    problems: list[str] = []

    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        return [f"not valid UTF-8: {exc}"]

    if "\ufffd" in text:
        problems.append("contains the Unicode replacement character")

    if path_text not in SENTINEL_SOURCE_FILES:
        if any(0xE000 <= ord(character) <= 0xF8FF for character in text):
            problems.append("contains a private-use Unicode character")

        for fragment in MOJIBAKE_FRAGMENTS:
            if fragment in text:
                escaped = fragment.encode("unicode_escape").decode("ascii")
                problems.append(
                    f"contains known mojibake fragment: {escaped}"
                )

    return problems


def validate_env_example(path: Path) -> list[str]:
    problems: list[str] = []
    values: dict[str, str] = {}

    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        key, value = stripped.split("=", 1)
        values[key] = value

    cors_value = values.get("AND_CORS_ORIGINS")
    if cors_value is None:
        return ["AND_CORS_ORIGINS is missing"]

    try:
        origins = json.loads(cors_value)
    except json.JSONDecodeError as exc:
        return [f"AND_CORS_ORIGINS is not valid JSON: {exc}"]

    if not isinstance(origins, list) or not origins:
        problems.append("AND_CORS_ORIGINS must be a non-empty JSON array")
    elif not all(isinstance(origin, str) and origin for origin in origins):
        problems.append(
            "AND_CORS_ORIGINS entries must be non-empty strings"
        )

    return problems


def main() -> int:
    errors: list[str] = []
    paths = repository_paths()
    available = set(paths)

    for required in sorted(REQUIRED_FILES):
        if required not in available:
            errors.append(
                f"{required}: required repository file is missing"
            )

    for path_text in paths:
        reason = is_forbidden(path_text)
        if reason is not None:
            errors.append(f"{path_text}: {reason}")
            continue

        path = ROOT / Path(path_text)
        if path.is_file() and should_check_utf8(path):
            for problem in validate_utf8(path, path_text):
                errors.append(f"{path_text}: {problem}")

    env_example = ROOT / ".env.example"
    if env_example.is_file():
        for problem in validate_env_example(env_example):
            errors.append(f".env.example: {problem}")

    if errors:
        print("Repository hygiene check failed:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print(
        "Repository hygiene check passed: "
        f"{len(paths)} tracked/untracked non-ignored paths inspected."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
