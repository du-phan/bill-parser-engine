"""
Run pipeline up to ReferenceLocator and ReferenceObjectLinker on selected chunks.

Steps per chunk:
  - Identify target (with inheritance hint)
  - Retrieve original article
  - Reconstruct (InstructionDecomposer + OperationApplier)
  - Locate references (Step 5)
  - Link references (Step 6)

Usage:
  poetry run python scripts/run_locator_and_linker.py \
    --chunk scripts/output/duplomb_chunks_all/070___TITRE_IV_Article_8_III_11_a_.txt \
    --chunk scripts/output/duplomb_chunks_all/068___TITRE_IV_Article_8_III_9_.txt \
    --log-file scripts/output/validation_logs/loc_link_round.jsonl
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
from bill_parser_engine.core.reference_resolver.reference_locator import ReferenceLocator
from bill_parser_engine.core.reference_resolver.reference_object_linker import ReferenceObjectLinker
from bill_parser_engine.core.reference_resolver.bill_splitter import BillSplitter


def _load_env() -> None:
    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env.local")
    load_dotenv(project_root / ".env")


def _load_chunk(chunk_txt_path: Path) -> BillChunk:
    base = chunk_txt_path.with_suffix("")
    json_path = base.with_suffix(".json")
    text = chunk_txt_path.read_text(encoding="utf-8")
    meta = json.loads(json_path.read_text(encoding="utf-8"))

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


def process_single_chunk(identifier: TargetArticleIdentifier, retriever: OriginalTextRetriever, reconstructor: LegalAmendmentReconstructor, locator: ReferenceLocator, linker: ReferenceObjectLinker, chunk_path: Path, log_path: Optional[Path]) -> Tuple[str, Optional[TargetArticle]]:
    bc = _load_chunk(chunk_path)
    _write_log(log_path, "chunk_loaded", {"chunk_id": bc.chunk_id, "hierarchy_path": bc.hierarchy_path, "text_preview": _preview(bc.text)})

    # Identify
    ta = identifier.identify(bc)
    id_result = {"chunk_id": bc.chunk_id, "operation_type": ta.operation_type.value, "code": ta.code, "article": ta.article, "confidence": getattr(ta, "confidence", None)}
    _write_log(log_path, "target_identified", id_result)
    if ta.operation_type == TargetOperationType.OTHER:
        return bc.chunk_id, ta

    # Retrieve
    article_text, meta = retriever.fetch_article_for_target(ta)
    _write_log(log_path, "retrieval_done", {"target": {"code": ta.code, "article": ta.article, "op": ta.operation_type.value}, "retrieval_success": bool(meta.get("success")), "source": meta.get("source"), "text_length": len(article_text or ""), "preview": _preview(article_text)})

    # Reconstruct
    target_article_reference = f"{ta.code}::{ta.article}" if ta.code and ta.article else ta.article or "unknown"
    reco_output = reconstructor.reconstruct_amendment(
        original_law_article=article_text or "",
        amendment_instruction=bc.text,
        target_article_reference=target_article_reference,
        chunk_id=bc.chunk_id,
    )
    _write_log(log_path, "reconstruct_done", {"chunk_id": bc.chunk_id, "deleted_len": len(reco_output.deleted_or_replaced_text or ""), "inserted_len": len(reco_output.newly_inserted_text or ""), "after_len": len(reco_output.intermediate_after_state_text or "")})

    # Locate
    located = locator.locate(reco_output)
    _write_log(log_path, "locate_done", {"count": len(located), "refs": [{"text": r.reference_text, "source": r.source.value, "conf": r.confidence} for r in located]})

    # Link
    linked = linker.link_references(located, original_law_article=article_text or "", intermediate_after_state_text=reco_output.intermediate_after_state_text)
    _write_log(log_path, "link_done", {"count": len(linked), "links": [{"text": r.reference_text, "obj": r.object, "conf": r.confidence} for r in linked]})

    return bc.chunk_id, ta


def main() -> None:
    parser = argparse.ArgumentParser(description="Run pipeline up to locator/linker on chunk(s)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dir", help="Directory with .txt chunks and .json sidecars")
    group.add_argument("--chunk", action="append", help="Path to a .txt chunk (can be repeated)")
    parser.add_argument("--log-file", default=None, help="Optional JSONL log output path")
    args = parser.parse_args()

    _load_env()

    log_path: Optional[Path] = Path(args.log_file) if args.log_file else None
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if log_path.exists():
            log_path.unlink()

    identifier = TargetArticleIdentifier(use_cache=False)
    retriever = OriginalTextRetriever(use_cache=False)
    reconstructor = LegalAmendmentReconstructor(use_cache=False)
    locator = ReferenceLocator(use_cache=False)
    linker = ReferenceObjectLinker(use_cache=False)

    paths: List[Path]
    if args.chunk:
        paths = [Path(p) for p in args.chunk]
    else:
        paths = list(_iter_chunk_txt_files(Path(args.dir)))

    for p in paths:
        process_single_chunk(identifier, retriever, reconstructor, locator, linker, p, log_path)


if __name__ == "__main__":
    main()


