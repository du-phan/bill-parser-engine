#!/usr/bin/env python3
"""
Normalize EU legal markdown files under data/eu_law_text/ for robust parsing.

Edits (idempotent):
- Roman section headers at BOL like "I.-Texte" or "II.–Texte" → "I. - Texte".
- Ensure one space after numbered markers at BOL: "1°Texte" → "1° Texte",
  "2)Texte" → "2) Texte", "3.Texte" → "3. Texte".
- Trim trailing whitespace; ensure file ends with a newline.

Does not rename files or modify markdown heading markers ("#").
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Tuple


ROMAN_SUFFIXES = (
    "bis",
    "ter",
    "quater",
    "quinquies",
    "sexies",
    "septies",
    "octies",
    "nonies",
    "decies",
)


def normalize_roman_header(line: str) -> Tuple[str, bool]:
    # Skip markdown headings to avoid touching e.g. "# Article 10"
    if line.lstrip().startswith("#"):
        return line, False

    pattern = re.compile(
        r"^(?P<indent>\s*)(?P<roman>[IVXLCDM]+)(?:\s+(?P<suffix>" + "|".join(ROMAN_SUFFIXES) + r"))?\s*[.\-–]?\s*(?P<rest>.*)$"
    )
    m = pattern.match(line)
    if not m:
        return line, False

    roman = m.group("roman")
    if not roman:
        return line, False

    indent = m.group("indent") or ""
    suffix = m.group("suffix")
    rest = m.group("rest")

    header = roman
    if suffix:
        header = f"{header} {suffix}"
    new_line = f"{indent}{header}. - {rest.lstrip()}"
    if new_line == line:
        return line, False
    return new_line, True


def normalize_numbered_item_spacing(line: str) -> Tuple[str, bool]:
    # Skip markdown headings
    if line.lstrip().startswith("#"):
        return line, False
    pattern = re.compile(r"^(?P<indent>\s*)(?P<num>\d{1,2})(?P<marker>[°)\.])\s*(?P<rest>\S.*)$")
    m = pattern.match(line)
    if not m:
        return line, False
    new_line = f"{m.group('indent')}{m.group('num')}{m.group('marker')} {m.group('rest')}"
    if new_line == line:
        return line, False
    return new_line, True


def process_text(text: str) -> Tuple[str, int, int]:
    roman_changes = 0
    item_changes = 0
    out_lines = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        new_line, changed = normalize_roman_header(line)
        if changed:
            roman_changes += 1
        line = new_line
        new_line, changed = normalize_numbered_item_spacing(line)
        if changed:
            item_changes += 1
        line = new_line
        out_lines.append(line)
    normalized = "\n".join(out_lines)
    if not normalized.endswith("\n"):
        normalized += "\n"
    return normalized, roman_changes, item_changes


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    base_dir = project_root / "data" / "eu_law_text"
    if not base_dir.exists():
        print(f"Directory not found: {base_dir}")
        return 1

    total_files = 0
    changed_files = 0
    total_roman = 0
    total_items = 0

    for md_path in sorted(base_dir.rglob("*.md")):
        if md_path.name.startswith("."):
            continue
        try:
            text = md_path.read_text(encoding="utf-8")
        except Exception as e:
            print(f"! Skip (read error): {md_path} -> {e}")
            continue

        total_files += 1
        normalized, n_roman, n_items = process_text(text)
        if normalized != text:
            try:
                md_path.write_text(normalized, encoding="utf-8")
                changed_files += 1
                total_roman += n_roman
                total_items += n_items
                print(f"✓ {md_path}  (roman: {n_roman}, items: {n_items})")
            except Exception as e:
                print(f"! Failed to write {md_path}: {e}")
        else:
            total_roman += n_roman
            total_items += n_items

    print(
        f"Done. Files scanned: {total_files}, changed: {changed_files}, roman headers normalized: {total_roman}, item spacings normalized: {total_items}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())


