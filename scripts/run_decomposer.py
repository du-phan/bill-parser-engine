"""
Run InstructionDecomposer on chunk(s) and log outputs.

Usage examples:
  - Multiple specific chunks:
      poetry run python scripts/run_decomposer.py \
        --chunk scripts/output/duplomb_chunks_all/071___TITRE_IV_Article_8_III_11_b_.txt \
        --chunk scripts/output/duplomb_chunks_all/058___TITRE_IV_Article_8_III_2_a_.txt \
        --chunk scripts/output/duplomb_chunks_all/054___TITRE_IV_Article_7_2_a_.txt \
        --chunk scripts/output/duplomb_chunks_all/055___TITRE_IV_Article_7_2_b_.txt \
        --log-file scripts/output/validation_logs/decomposer_round.jsonl

  - Whole directory (runs on every .txt with .json sidecar):
      poetry run python scripts/run_decomposer.py \
        --dir scripts/output/duplomb_chunks_all \
        --log-file scripts/output/validation_logs/decomposer_all.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from dotenv import load_dotenv

from bill_parser_engine.core.reference_resolver.models import BillChunk, AmendmentOperation
from bill_parser_engine.core.reference_resolver.instruction_decomposer import InstructionDecomposer


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
        if p.is_file() and p.suffix == ".txt" and p.with_suffix(".json").exists():
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


def process_single_chunk(decomposer: InstructionDecomposer, chunk_path: Path, log_path: Optional[Path]) -> Tuple[str, List[AmendmentOperation]]:
    bc = _load_chunk(chunk_path)
    _write_log(log_path, "chunk_loaded", {"chunk_id": bc.chunk_id, "hierarchy_path": bc.hierarchy_path, "text_preview": _preview(bc.text)})

    try:
        ops = decomposer.parse_instruction(bc.text)
        result = {
            "chunk_id": bc.chunk_id,
            "ops_count": len(ops),
            "operations": [
                {
                    "operation_type": op.operation_type.value,
                    "target_text": op.target_text,
                    "replacement_text": op.replacement_text,
                    "position_hint": op.position_hint,
                    "sequence_order": op.sequence_order,
                    "confidence_score": op.confidence_score,
                }
                for op in ops
            ],
        }
        _write_log(log_path, "decompose_done", result)
        print(json.dumps({"decompose": result}, ensure_ascii=False))
        return bc.chunk_id, ops
    except Exception as e:
        _write_log(log_path, "decompose_failed", {"chunk_id": bc.chunk_id, "error": str(e)})
        print(json.dumps({"decompose_error": {"chunk_id": bc.chunk_id, "error": str(e)}}, ensure_ascii=False))
        return bc.chunk_id, []


def main() -> None:
    parser = argparse.ArgumentParser(description="Run InstructionDecomposer on chunk(s)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dir", help="Directory with .txt chunks and .json sidecars")
    group.add_argument("--chunk", action="append", help="Path to a .txt chunk (can be repeated)")
    parser.add_argument("--log-file", default=None, help="Optional JSONL log output path")
    parser.add_argument("--no-cache", action="store_true", help="Disable decomposer cache for fresh outputs")
    args = parser.parse_args()

    _load_env()

    log_path: Optional[Path] = Path(args.log_file) if args.log_file else None
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if log_path.exists():
            log_path.unlink()

    decomposer = InstructionDecomposer(use_cache=not args.no_cache)

    processed = 0
    succeeded = 0

    paths: List[Path]
    if args.chunk:
        paths = [Path(p) for p in args.chunk]
    else:
        paths = list(_iter_chunk_txt_files(Path(args.dir)))

    for p in paths:
        _, ops = process_single_chunk(decomposer, p, log_path)
        processed += 1
        if ops:
            succeeded += 1

    summary = {"processed": processed, "with_ops": succeeded}
    _write_log(log_path, "summary", summary)
    print(json.dumps({"summary": summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()


