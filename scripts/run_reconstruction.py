"""
Run reconstruction (InstructionDecomposer + OperationApplier) on selected chunk(s).

Pipeline stages covered per chunk:
  - TargetArticleIdentifier (with inheritance hint)
  - OriginalTextRetriever (INSERT intra-article probe supported)
  - LegalAmendmentReconstructor (InstructionDecomposer + OperationApplier)

Usage:
  poetry run python scripts/run_reconstruction.py \
    --chunk scripts/output/duplomb_chunks_all/072___TITRE_IV_Article_8_III_12_a_.txt \
    --chunk scripts/output/duplomb_chunks_all/040___TITRE_II_Article_4_I_1_a_.txt \
    --log-file scripts/output/validation_logs/reconstruct_round2.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from dotenv import load_dotenv

from bill_parser_engine.core.reference_resolver.models import BillChunk, TargetArticle, TargetOperationType
from bill_parser_engine.core.reference_resolver.target_identifier import TargetArticleIdentifier
from bill_parser_engine.core.reference_resolver.original_text_retriever import OriginalTextRetriever
from bill_parser_engine.core.reference_resolver.legal_amendment_reconstructor import LegalAmendmentReconstructor
from bill_parser_engine.core.reference_resolver.bill_splitter import BillSplitter


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

    # Populate inheritance target hint
    splitter = BillSplitter()
    numbered_intro = meta.get("numbered_point_introductory_phrase") or ""
    ms_intro = meta.get("major_subdivision_introductory_phrase") or ""
    article_intro = meta.get("article_introductory_phrase") or ""
    code_ctx = splitter._extract_code_from_article_intro(ms_intro) or splitter._extract_code_from_article_intro(article_intro)
    inherited = splitter._create_inheritance_hint(numbered_intro, code_ctx)

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
        inherited_target_article=inherited,
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


def process_single_chunk(identifier: TargetArticleIdentifier, retriever: OriginalTextRetriever, reconstructor: LegalAmendmentReconstructor, chunk_path: Path, log_path: Optional[Path]) -> Tuple[str, Optional[TargetArticle]]:
    bc = _load_chunk(chunk_path)
    _write_log(log_path, "chunk_loaded", {"chunk_id": bc.chunk_id, "hierarchy_path": bc.hierarchy_path, "text_preview": _preview(bc.text)})

    # Identify
    try:
        ta = identifier.identify(bc)
        id_result = {
            "chunk_id": bc.chunk_id,
            "operation_type": ta.operation_type.value,
            "code": ta.code,
            "article": ta.article,
            "confidence": getattr(ta, "confidence", None),
            "reason": getattr(ta, "reason", None),
        }
        _write_log(log_path, "target_identified", id_result)
        print(json.dumps({"identify": id_result}, ensure_ascii=False))
    except Exception as e:
        _write_log(log_path, "target_failed", {"chunk_id": bc.chunk_id, "error": str(e)})
        print(json.dumps({"identify_error": {"chunk_id": bc.chunk_id, "error": str(e)}}, ensure_ascii=False))
        return bc.chunk_id, None

    if ta.operation_type == TargetOperationType.OTHER:
        _write_log(log_path, "skipped_other", {"chunk_id": bc.chunk_id, "reason": getattr(ta, "reason", None)})
        print(json.dumps({"skip": {"chunk_id": bc.chunk_id, "reason": getattr(ta, "reason", None)}}, ensure_ascii=False))
        return bc.chunk_id, ta

    # Retrieve
    try:
        article_text, meta = retriever.fetch_article_for_target(ta)
        ret_result = {
            "chunk_id": bc.chunk_id,
            "target": {"code": ta.code, "article": ta.article, "op": ta.operation_type.value},
            "retrieval_success": bool(meta.get("success")),
            "source": meta.get("source"),
            "note": meta.get("note"),
            "error": meta.get("error"),
            "text_length": len(article_text or ""),
            "preview": _preview(article_text),
            "metadata": {k: v for k, v in meta.items() if k not in {"success", "source", "error", "note"}},
        }
        _write_log(log_path, "retrieval_done", ret_result)
        print(json.dumps({"retrieve": ret_result}, ensure_ascii=False))
    except Exception as e:
        _write_log(log_path, "retrieval_failed", {"chunk_id": bc.chunk_id, "error": str(e)})
        print(json.dumps({"retrieve_error": {"chunk_id": bc.chunk_id, "error": str(e)}}, ensure_ascii=False))
        return bc.chunk_id, ta

    # Reconstruct
    target_article_reference = f"{ta.code}::{ta.article}" if ta.code and ta.article else ta.article or "unknown"
    reco_output = reconstructor.reconstruct_amendment(
        original_law_article=article_text or "",
        amendment_instruction=bc.text,
        target_article_reference=target_article_reference,
        chunk_id=bc.chunk_id,
    )

    # Extract detailed result if available
    detailed = getattr(reconstructor, "last_detailed_result", None)
    if detailed is not None:
        rec = {
            "chunk_id": bc.chunk_id,
            "success": detailed.success,
            "applied_count": len(detailed.operations_applied),
            "failed_count": len(detailed.operations_failed),
            "original_len": detailed.original_text_length,
            "final_len": detailed.final_text_length,
            "processing_ms": detailed.processing_time_ms,
            "validation_warnings": detailed.validation_warnings,
        }
        _write_log(log_path, "reconstruct_done", rec)
        print(json.dumps({"reconstruct": rec}, ensure_ascii=False))
    else:
        _write_log(log_path, "reconstruct_done", {"chunk_id": bc.chunk_id, "note": "no detailed result"})
        print(json.dumps({"reconstruct": {"chunk_id": bc.chunk_id, "note": "no detailed result"}}, ensure_ascii=False))

    return bc.chunk_id, ta


def main() -> None:
    parser = argparse.ArgumentParser(description="Run reconstruction on chunk(s)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dir", help="Directory with .txt chunks and .json sidecars")
    group.add_argument("--chunk", action="append", help="Path to a .txt chunk (can be repeated)")
    parser.add_argument("--log-file", default=None, help="Optional JSONL log output path")
    parser.add_argument("--no-cache", action="store_true", help="Disable component caches for fresh processing")
    args = parser.parse_args()

    _load_env()

    log_path: Optional[Path] = Path(args.log_file) if args.log_file else None
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if log_path.exists():
            log_path.unlink()

    use_cache = not args.no_cache
    identifier = TargetArticleIdentifier(use_cache=use_cache)
    retriever = OriginalTextRetriever(use_cache=use_cache)
    reconstructor = LegalAmendmentReconstructor(use_cache=use_cache)

    processed = 0
    succeeded = 0
    skipped = 0

    paths: List[Path]
    if args.chunk:
        paths = [Path(p) for p in args.chunk]
    else:
        paths = list(_iter_chunk_txt_files(Path(args.dir)))

    for p in paths:
        _, ta = process_single_chunk(identifier, retriever, reconstructor, p, log_path)
        processed += 1
        if ta is None:
            continue
        if ta.operation_type == TargetOperationType.OTHER:
            skipped += 1
            continue
        succeeded += 1

    summary = {"processed": processed, "with_targets": succeeded, "skipped_other": skipped}
    _write_log(log_path, "summary", summary)
    print(json.dumps({"summary": summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()


