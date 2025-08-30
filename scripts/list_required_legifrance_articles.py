#!/usr/bin/env python3
"""
List required Legifrance articles from exported chunk sidecars.

This utility scans a directory of chunk JSON sidecars (e.g., scripts/output/duplomb_chunks_all)
and extracts the target code name and article identifiers that must be retrieved from Legifrance.

Extraction logic (robust, no LLM calls):
 1) Prefer 'article_introductory_phrase' to extract the code name (e.g.,
    "Le code rural et de la pêche maritime est ainsi modifié :").
 2) Prefer 'numbered_point_introductory_phrase' to extract article ids
    (e.g., "L'article L. 254-1 est ainsi modifié :").
 3) Fallback scan the 'text' body for patterns like:
      - "Le code ... est ainsi modifié" (code name)
      - "L'article X" or "Les articles X et Y" (article ids)
 4) Deduplicate and emit a sorted list as text or JSON.

Usage:
  poetry run python scripts/list_required_legifrance_articles.py \
    --chunks-dir scripts/output/duplomb_chunks_all \
    --out txt

Output (txt default):
  Code rural et de la pêche maritime > Article L. 254-1
  Code de l'environnement > Article L. 211-1-2

Optionally write JSON to a file with --json-out path.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple


CODE_REGEX = re.compile(
    # e.g., "Le code rural et de la pêche maritime est ainsi modifié :"
    r"(?i)\b(le\s+)?code\s+(?P<code>[^\n:]+?)\s+est\s+ainsi\s+modifi[ée]s?\b"
)

ARTICLE_SINGLE_REGEX = re.compile(
    # e.g., "L'article L. 254-1 est ainsi modifié"
    r"(?i)\bL['’]?article\s+(?P<article>[LRD]\.\s*[^\s,;:]+)\b"
)

ARTICLES_MULTI_REGEX = re.compile(
    # e.g., "Les articles L. 253-1 et L. 253-2 sont ..."
    r"(?i)\bLes\s+articles\s+(?P<list>.+?)\b(?:sont|est|,|;|:)"
)

# Generic article token, permissive to catch forms like:
#  - L. 254-1
#  - L. 254-6-2
#  - L. 1 A (letter-suffix with optional space)
#  - D. 211-1, R. 123-4, etc.
ARTICLE_TOKEN_REGEX = re.compile(r"(?i)([LRD]\.\s*\d[\d\-]*(?:\s*[A-Z])?)")


@dataclass(frozen=True)
class CodeArticle:
    code_name: str
    article: str

    def as_text(self) -> str:
        # Normalize minor spacing inside article (e.g., "L.254-1" → "L. 254-1")
        art = re.sub(r"^([LRD])\.\s*", r"\1. ", self.article.strip())
        name = normalize_code_name(self.code_name)
        return f"{name} > Article {art}"


def normalize_code_name(raw: str) -> str:
    s = (raw or "").strip()
    # Ensure it starts with "Code " for readability (avoid "de l'environnement")
    if not re.match(r"(?i)^code\b", s):
        s = f"Code {s}"
    # Capitalize first letter after 'Code '
    if s.lower().startswith("code "):
        tail = s[5:].strip()
        if tail:
            s = "Code " + tail[0].upper() + tail[1:]
    return s


def extract_code_name(meta: Dict[str, object], text_body: str) -> Optional[str]:
    # 1) From article_introductory_phrase
    aip = meta.get("article_introductory_phrase")
    if isinstance(aip, str):
        m = CODE_REGEX.search(aip)
        if m:
            return m.group("code").strip()
    # 2) Fallback scan in text body
    m = CODE_REGEX.search(text_body)
    if m:
        return m.group("code").strip()
    return None


def extract_articles(meta: Dict[str, object], text_body: str) -> List[str]:
    # 1) From numbered_point_introductory_phrase
    npp = meta.get("numbered_point_introductory_phrase")
    singles: List[str] = []
    if isinstance(npp, str):
        for m in ARTICLE_SINGLE_REGEX.finditer(npp):
            singles.append(m.group("article").strip())
    if singles:
        return singles

    # 2) Scan text body for "L'article X"
    for m in ARTICLE_SINGLE_REGEX.finditer(text_body):
        singles.append(m.group("article").strip())
    if singles:
        return singles

    # 3) Scan for "Les articles X et Y" → explode into tokens
    for m in ARTICLES_MULTI_REGEX.finditer(text_body):
        token_blob = m.group("list")
        tokens = ARTICLE_TOKEN_REGEX.findall(token_blob)
        if tokens:
            singles.extend(tokens)
    if singles:
        # Deduplicate while preserving order
        seen: Set[str] = set()
        out: List[str] = []
        for t in singles:
            tt = t.strip()
            if tt not in seen:
                seen.add(tt)
                out.append(tt)
        return out

    # 4) Fallback: scan any article-like tokens anywhere in the text body
    tokens = ARTICLE_TOKEN_REGEX.findall(text_body or "")
    if tokens:
        seen: Set[str] = set()
        out: List[str] = []
        for t in tokens:
            tt = t.strip()
            if tt not in seen:
                seen.add(tt)
                out.append(tt)
        return out

    return []


def scan_chunks(chunks_dir: Path) -> List[CodeArticle]:
    pairs: Set[CodeArticle] = set()
    for path in sorted(chunks_dir.glob("*.json")):
        if path.name.lower() == "index.json":
            continue
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
            # Build a combined body from all relevant textual fields for best recall
            parts: List[str] = []
            for key in (
                "text",
                "article_introductory_phrase",
                "major_subdivision_introductory_phrase",
                "numbered_point_introductory_phrase",
            ):
                v = meta.get(key)
                if isinstance(v, str):
                    parts.append(v)
            text_body = "\n".join(parts)

            code_name = extract_code_name(meta, text_body)
            # If no code name inferred, skip (can't build full pair reliably)
            if not code_name:
                continue

            articles = extract_articles(meta, text_body)
            for art in articles:
                pairs.add(CodeArticle(code_name=code_name, article=art))
        except Exception:
            # Skip malformed json sidecars to be resilient
            continue

    # Return sorted list for stable output
    return sorted(pairs, key=lambda ca: (normalize_code_name(ca.code_name).lower(), ca.article.lower()))


def main() -> None:
    parser = argparse.ArgumentParser(description="List required Legifrance articles from chunk sidecars")
    parser.add_argument(
        "--chunks-dir",
        default=str(Path("scripts") / "output" / "duplomb_chunks_all"),
        help="Directory containing chunk JSON sidecars",
    )
    parser.add_argument(
        "--out",
        choices=["txt", "json"],
        default="txt",
        help="Output format (txt lines to stdout or JSON to stdout)",
    )
    parser.add_argument(
        "--json-out",
        default=None,
        help="Optional path to write JSON output (list of {code_name, article})",
    )
    args = parser.parse_args()

    chunks_dir = Path(args.chunks_dir)
    if not chunks_dir.exists():
        raise FileNotFoundError(f"Chunks dir not found: {chunks_dir}")

    pairs = scan_chunks(chunks_dir)

    if args.out == "txt":
        for ca in pairs:
            print(ca.as_text())
    else:
        data = [{"code_name": ca.code_name, "article": ca.article} for ca in pairs]
        print(json.dumps(data, ensure_ascii=False, indent=2))

    if args.json_out:
        data = [{"code_name": ca.code_name, "article": ca.article} for ca in pairs]
        Path(args.json_out).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()


