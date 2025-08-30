"""
Export chunks from a legislative bill using BillSplitter into individual files.

Usage:
  poetry run python scripts/export_chunks.py --input /absolute/path/to/bill.md \
    [--out-dir scripts/output/duplomb_chunks]

Outputs:
  - For each chunk, writes:
      <index>__<sanitized_chunk_id>.txt  (raw chunk text)
      <index>__<sanitized_chunk_id>.json (metadata: hierarchy, positions, labels)
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import List

from bill_parser_engine.core.reference_resolver.bill_splitter import BillSplitter
from bill_parser_engine.core.reference_resolver.models import BillChunk


def _sanitize_filename(name: str) -> str:
    # Replace non-filename-safe characters with underscores; limit length
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", name)
    return safe[:120]


def _write_chunk_files(out_dir: Path, idx: int, chunk: BillChunk) -> None:
    base = f"{idx:03d}__{_sanitize_filename(chunk.chunk_id or 'chunk')}"
    txt_path = out_dir / f"{base}.txt"
    json_path = out_dir / f"{base}.json"

    # Write raw text
    txt_path.write_text(chunk.text, encoding="utf-8")

    # Write metadata JSON
    meta = {
        "chunk_id": chunk.chunk_id,
        "hierarchy_path": chunk.hierarchy_path,
        "text": chunk.text,
        "titre_text": chunk.titre_text,
        "article_label": chunk.article_label,
        "article_introductory_phrase": chunk.article_introductory_phrase,
        "major_subdivision_label": chunk.major_subdivision_label,
        "major_subdivision_introductory_phrase": chunk.major_subdivision_introductory_phrase,
        "numbered_point_label": chunk.numbered_point_label,
        "numbered_point_introductory_phrase": chunk.numbered_point_introductory_phrase,
        "lettered_subdivision_label": chunk.lettered_subdivision_label,
        "structural_anchor_hint": chunk.structural_anchor_hint,
        "start_pos": chunk.start_pos,
        "end_pos": chunk.end_pos,
    }
    json_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def export_chunks(input_path: Path, out_dir: Path) -> List[Path]:
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    out_dir.mkdir(parents=True, exist_ok=True)

    text = input_path.read_text(encoding="utf-8")
    splitter = BillSplitter()
    chunks = splitter.split(text)

    written: List[Path] = []
    for i, ch in enumerate(chunks, start=1):
        _write_chunk_files(out_dir, i, ch)
        written.append(out_dir / f"{i:03d}__{_sanitize_filename(ch.chunk_id or 'chunk')}.txt")

    # Write an index file summarizing all chunks
    (out_dir / "index.json").write_text(
        json.dumps(
            [
                {
                    "idx": i,
                    "chunk_id": ch.chunk_id,
                    "hierarchy_path": ch.hierarchy_path,
                    "text_preview": (ch.text[:200] + ("..." if len(ch.text) > 200 else "")),
                }
                for i, ch in enumerate(chunks, start=1)
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Export bill chunks using BillSplitter")
    parser.add_argument("--input", required=True, help="Absolute path to input bill .md file")
    parser.add_argument(
        "--out-dir",
        default=str(Path("scripts") / "output" / "duplomb_chunks"),
        help="Output directory for chunks",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.is_absolute():
        # Prefer absolute paths to avoid surprises
        input_path = input_path.resolve()
    out_dir = Path(args.out_dir)

    written = export_chunks(input_path, out_dir)
    print(f"Exported {len(written)} chunk text files to: {out_dir}")


if __name__ == "__main__":
    main()


