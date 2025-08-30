"""
Run TargetArticleIdentifier on real exported chunks (single file or directory).

This focuses exclusively on Step 2 (TargetArticleIdentifier) to validate the
component behavior with actual LLM calls (Mistral), confidence propagation,
suppressed-enumeration handling, and gating suitability.

Usage examples:
  - Single chunk:
      poetry run python scripts/run_target_identifier.py \
        --chunk scripts/output/duplomb_chunks_all/071___TITRE_IV_Article_8_III_11_b_.txt

  - All chunks in a directory:
      poetry run python scripts/run_target_identifier.py \
        --dir scripts/output/duplomb_chunks_all \
        --log-file scripts/output/validation_logs/identifier_pass.jsonl

Notes:
  - Requires .env/.env.local with MISTRAL_API_KEY for LLM usage.
  - Expects a .json sidecar next to each .txt chunk (as produced by the exporter).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from dotenv import load_dotenv

from bill_parser_engine.core.reference_resolver.models import BillChunk, TargetArticle
from bill_parser_engine.core.reference_resolver.target_identifier import (
    TargetArticleIdentifier,
)


def _load_env() -> None:
    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env.local")
    load_dotenv(project_root / ".env")


def _load_chunk(chunk_txt_path: Path) -> BillChunk:
    if not chunk_txt_path.exists():
        raise FileNotFoundError(f"Chunk text not found: {chunk_txt_path}")
    base = chunk_txt_path.with_suffix("")
    json_path = base.with_suffix(".json")
    if not json_path.exists():
        raise FileNotFoundError(f"Chunk metadata not found: {json_path}")
    text = chunk_txt_path.read_text(encoding="utf-8")
    meta = json.loads(json_path.read_text(encoding="utf-8"))

    return BillChunk(
        text=text,
        titre_text=meta.get("titre_text", ""),
        article_label=meta.get("article_label", ""),
        article_introductory_phrase=meta.get("article_introductory_phrase"),
        major_subdivision_label=meta.get("major_subdivision_label"),
        major_subdivision_introductory_phrase=meta.get("major_subdivision_introductory_phrase"),
        numbered_point_label=meta.get("numbered_point_label"),
        numbered_point_introductory_phrase=meta.get("numbered_point_introductory_phrase"),
        lettered_subdivision_label=meta.get("lettered_subdivision_label"),
        hierarchy_path=meta.get("hierarchy_path", []),
        chunk_id=meta.get("chunk_id", "::".join(meta.get("hierarchy_path", []))),
        start_pos=meta.get("start_pos", 0),
        end_pos=meta.get("end_pos", 0),
        target_article=None,
        inherited_target_article=None,
    )


def _iter_chunk_txt_files(directory: Path) -> Iterable[Path]:
    for p in sorted(directory.iterdir()):
        if p.is_file() and p.suffix == ".txt":
            # sidecar must exist
            if p.with_suffix(".json").exists():
                yield p


def _write_log(log_path: Optional[Path], event: str, payload: Dict[str, Any]) -> None:
    if not log_path:
        return
    entry = {"event": event, "payload": payload}
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _preview(text: Optional[str], max_len: int = 160) -> str:
    if not text:
        return ""
    return text if len(text) <= max_len else text[: max_len - 3] + "…"


def process_single_chunk(identifier: TargetArticleIdentifier, chunk_path: Path, log_path: Optional[Path]) -> Tuple[str, Optional[TargetArticle]]:
    bc = _load_chunk(chunk_path)
    _write_log(log_path, "chunk_loaded", {"chunk_id": bc.chunk_id, "hierarchy_path": bc.hierarchy_path, "text_preview": _preview(bc.text)})
    try:
        ta = identifier.identify(bc)
        result = {
            "chunk_id": bc.chunk_id,
            "operation_type": ta.operation_type.value,
            "code": ta.code,
            "article": ta.article,
            "confidence": getattr(ta, "confidence", None),
            "reason": getattr(ta, "reason", None),
        }
        _write_log(log_path, "target_identified", result)
        print(json.dumps(result, ensure_ascii=False))
        return bc.chunk_id, ta
    except Exception as e:
        _write_log(log_path, "target_failed", {"chunk_id": bc.chunk_id, "error": str(e)})
        print(json.dumps({"chunk_id": bc.chunk_id, "error": str(e)}, ensure_ascii=False))
        return bc.chunk_id, None


def main() -> None:
    parser = argparse.ArgumentParser(description="Run TargetArticleIdentifier on chunk(s)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--chunk", help="Path to a single .txt chunk (sidecar .json required)")
    group.add_argument("--dir", help="Directory with .txt chunks and .json sidecars")
    parser.add_argument("--log-file", default=None, help="Optional JSONL log output path")
    parser.add_argument("--min-confidence", type=float, default=0.0, help="Optional minimum confidence gating for reporting")
    args = parser.parse_args()

    _load_env()

    log_path: Optional[Path] = Path(args.log_file) if args.log_file else None
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if log_path.exists():
            log_path.unlink()

    identifier = TargetArticleIdentifier(use_cache=False)

    processed = 0
    skipped_other = 0
    gated_low_conf = 0
    successes = 0

    if args.chunk:
        chunk_path = Path(args.chunk)
        chunk_id, ta = process_single_chunk(identifier, chunk_path, log_path)
        processed += 1
        if ta is None:
            pass
        elif ta.operation_type.value == "OTHER":
            skipped_other += 1
        elif ta.confidence is not None and ta.confidence < args.min_confidence:
            gated_low_conf += 1
        else:
            successes += 1
    else:
        directory = Path(args.dir)
        for txt_path in _iter_chunk_txt_files(directory):
            chunk_id, ta = process_single_chunk(identifier, txt_path, log_path)
            processed += 1
            if ta is None:
                continue
            if ta.operation_type.value == "OTHER":
                skipped_other += 1
                continue
            if ta.confidence is not None and ta.confidence < args.min_confidence:
                gated_low_conf += 1
                continue
            successes += 1

    summary = {
        "processed": processed,
        "successes": successes,
        "skipped_other": skipped_other,
        "gated_low_confidence": gated_low_conf,
        "min_confidence": args.min_confidence,
    }
    _write_log(log_path, "summary", summary)
    print(json.dumps({"summary": summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()


