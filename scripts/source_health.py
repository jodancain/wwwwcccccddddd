"""Check source files for encoding and known mojibake regressions."""
from __future__ import annotations

import sys
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

TEXT_ROOTS = [
    ROOT / "backend" / "app",
    ROOT / "frontend" / "src",
    ROOT / "scripts",
]

TEXT_FILES = [
    ROOT / "README.md",
    ROOT / "start.bat",
    ROOT / ".env.example",
    ROOT / "frontend" / "index.html",
]

TEXT_SUFFIXES = {".bat", ".css", ".md", ".py", ".ts", ".vue"}

# Historical mojibake fragments that previously appeared in UI/backend strings.
SUSPICIOUS_CODEPOINTS = [
    0x934A,
    0x7487,
    0x93C8,
    0x93AC,
    0x9983,
    0x922B,
    0x9225,
    0x59AF,
    0x7025,
    0x9359,
    0x7BA0,
    0x9422,
    0x5BEE,
    0x704F,
]

FORBIDDEN_STRINGS = [
    chr(0xFFFD),  # replacement character
    chr(0x041E) + chr(0x0422),  # Cyrillic OT, formerly shown as self avatar
    *[chr(code) for code in SUSPICIOUS_CODEPOINTS],
]

SENSITIVE_PATTERNS = [
    ("WeChatAI API key", re.compile(r"\bwca_[0-9a-fA-F]{24,}\b")),
    ("OpenAI-style API key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b")),
]


def iter_files() -> list[Path]:
    files: list[Path] = []
    for root in TEXT_ROOTS:
        files.extend(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES)
    files.extend(p for p in TEXT_FILES if p.exists())
    return sorted(set(files))


def suspicious_text_hits(text: str) -> list[str]:
    return sorted({f"U+{ord(token[0]):04X}" for token in FORBIDDEN_STRINGS if token in text})


def sensitive_hits(text: str) -> list[str]:
    return [name for name, pattern in SENSITIVE_PATTERNS if pattern.search(text)]


def main() -> int:
    failures: list[str] = []
    scanned = 0

    for path in iter_files():
        rel = path.relative_to(ROOT)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            failures.append(f"{rel}: UTF-8 decode failed: {exc}")
            continue

        scanned += 1
        hits = suspicious_text_hits(text)
        if hits:
            failures.append(f"{rel}: suspicious text markers: {', '.join(hits)}")
        secrets = sensitive_hits(text)
        if secrets:
            failures.append(f"{rel}: possible secret material: {', '.join(secrets)}")

    if failures:
        print("Source health check failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print(f"Source health check passed: {scanned} files scanned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
