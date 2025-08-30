#!/usr/bin/env python3
"""
Normalize the legislative bill markdown to align with parser expectations while
preserving quoted insert/replace content exactly.

Edits (idempotent):
- Normalize roman headers at BOL to "I. – " or "I. - " (preserve en-dash if present originally?
  We standardize to "–" inside quotes? No: we DO NOT touch inside quotes.).
- Ensure space after numbered markers at BOL ("1° ", "2) ", "3. ").
- Trim trailing whitespace; ensure trailing newline.
- Do NOT modify text inside French guillemets « » or ASCII quotes "...".
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


def split_preserving_quotes(line: str) -> Tuple[str, str, str]:
    """Split line into prefix/outside, quoted segment (first occurrence), and suffix.

    We attempt to avoid edits inside guillemets « » or straight quotes " ".
    If multiple quoted regions exist, only the first is preserved (common in bill lines).
    """
    # Prefer guillemets first
    m = re.search(r"«[^»]*»", line)
    if m:
        return line[: m.start()], line[m.start() : m.end()], line[m.end() :]
    # Fallback to straight quotes
    m = re.search(r"\"[^\"]*\"", line)
    if m:
        return line[: m.start()], line[m.start() : m.end()], line[m.end() :]
    return line, "", ""


def normalize_roman_header(s: str) -> Tuple[str, bool]:
    pattern = re.compile(
        r"^(?P<indent>\s*)(?P<roman>[IVXLCDM]+)(?:\s+(?P<suffix>" + "|".join(ROMAN_SUFFIXES) + r"))?\s*[.\-–]?\s*(?P<rest>.*)$"
    )
    m = pattern.match(s)
    if not m:
        return s, False
    roman = m.group("roman")
    if not roman:
        return s, False
    indent = m.group("indent") or ""
    suffix = m.group("suffix")
    rest = m.group("rest")
    header = roman
    if suffix:
        header = f"{header} {suffix}"
    # Standardize to en-dash for bills: "I. – "
    new_s = f"{indent}{header}. – {rest.lstrip()}"
    if new_s == s:
        return s, False
    return new_s, True


def normalize_numbered_item_spacing(s: str) -> Tuple[str, bool]:
    pattern = re.compile(r"^(?P<indent>\s*)(?P<num>\d{1,2})(?P<marker>[°)\.])\s*(?P<rest>\S.*)$")
    m = pattern.match(s)
    if not m:
        return s, False
    new_s = f"{m.group('indent')}{m.group('num')}{m.group('marker')} {m.group('rest')}"
    if new_s == s:
        return s, False
    return new_s, True


def process_text(text: str) -> Tuple[str, int, int]:
    roman_changes = 0
    item_changes = 0
    out_lines = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()

        # Protect first quoted segment if present
        prefix, quoted, suffix = split_preserving_quotes(line)

        # Apply normalization only to prefix and suffix
        changed_any = False
        new_prefix, changed = normalize_roman_header(prefix)
        if changed:
            roman_changes += 1
            changed_any = True
        new_prefix2, changed = normalize_numbered_item_spacing(new_prefix)
        if changed:
            item_changes += 1
            changed_any = True
        new_suffix, changed = normalize_numbered_item_spacing(suffix)
        if changed:
            item_changes += 1
            changed_any = True

        new_line = f"{new_prefix2}{quoted}{new_suffix}"
        out_lines.append(new_line if changed_any else line)

    normalized = "\n".join(out_lines)
    if not normalized.endswith("\n"):
        normalized += "\n"
    return normalized, roman_changes, item_changes


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    bill_path = project_root / "data" / "legal_bill" / "duplomb_legislative_bill.md"
    if not bill_path.exists():
        print(f"File not found: {bill_path}")
        return 1
    text = bill_path.read_text(encoding="utf-8")
    normalized, n_roman, n_items = process_text(text)
    if normalized != text:
        bill_path.write_text(normalized, encoding="utf-8")
        print(f"✓ Normalized {bill_path} (roman: {n_roman}, items: {n_items})")
    else:
        print(f"No changes needed for {bill_path} (roman: {n_roman}, items: {n_items})")
    return 0


if __name__ == "__main__":
    sys.exit(main())


