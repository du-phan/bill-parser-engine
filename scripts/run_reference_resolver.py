"""
Run full pipeline through ReferenceResolver on selected chunk(s).

Steps per chunk:
  - Identify target (with inheritance hint)
  - Retrieve original article
  - Reconstruct (InstructionDecomposer + OperationApplier)
  - Locate references
  - Link references
  - Resolve references (question-guided extraction with carve retry)

Usage example:
  poetry run python scripts/run_reference_resolver.py \
    --chunk scripts/output/duplomb_chunks_all/060___TITRE_IV_Article_8_III_3_.txt \
    --chunk scripts/output/duplomb_chunks_all/057___TITRE_IV_Article_8_III_1_.txt \
    --chunk scripts/output/duplomb_chunks_all/055___TITRE_IV_Article_7_2_b_.txt \
    --chunk scripts/output/duplomb_chunks_all/072___TITRE_IV_Article_8_III_12_a_.txt \
    --chunk scripts/output/duplomb_chunks_all/025___TITRE_I_Article_2_II_1_B.txt \
    --chunk scripts/output/duplomb_chunks_all/042___TITRE_II_Article_4_I_1_bis.txt \
    --chunk scripts/output/duplomb_chunks_all/043___TITRE_II_Article_4_I_1_ter.txt \
    --log-file scripts/output/validation_logs/resolve_round2.jsonl --no-cache | cat
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from bill_parser_engine.core.reference_resolver.models import BillChunk
from dotenv import load_dotenv
from bill_parser_engine.core.reference_resolver.bill_splitter import BillSplitter
from bill_parser_engine.core.reference_resolver.target_identifier import TargetArticleIdentifier
from bill_parser_engine.core.reference_resolver.original_text_retriever import OriginalTextRetriever
from bill_parser_engine.core.reference_resolver.legal_amendment_reconstructor import LegalAmendmentReconstructor
from bill_parser_engine.core.reference_resolver.reference_locator import ReferenceLocator
from bill_parser_engine.core.reference_resolver.reference_object_linker import ReferenceObjectLinker
from bill_parser_engine.core.reference_resolver.reference_resolver import ReferenceResolver
from bill_parser_engine.core.reference_resolver.legal_state_synthesizer import LegalStateSynthesizer
from bill_parser_engine.core.reference_resolver.models import (
    LegalStateSynthesizerConfig,
)


def _load_chunk(chunk_txt_path: Path) -> BillChunk:
    meta_path = chunk_txt_path.with_suffix(".json")
    text = chunk_txt_path.read_text(encoding="utf-8")
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
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
        chunk_id=meta.get("chunk_id", str(chunk_txt_path.name)),
        start_pos=meta.get("start_pos", 0),
        end_pos=meta.get("end_pos", len(text)),
    )


def run_on_chunks(chunks: List[Path], log_file: Optional[Path], use_cache: bool) -> None:
    # Load environment variables from .env (e.g., MISTRAL_API_KEY)
    load_dotenv()
    splitter = BillSplitter()
    identifier = TargetArticleIdentifier(use_cache=use_cache)
    retriever = OriginalTextRetriever(use_cache=use_cache)
    reconstructor = LegalAmendmentReconstructor(use_cache=use_cache)
    locator = ReferenceLocator(use_cache=use_cache)
    linker = ReferenceObjectLinker(use_cache=use_cache)
    resolver = ReferenceResolver(use_cache=use_cache)

    out = open(log_file, "w", encoding="utf-8") if log_file else None
    try:
        for p in chunks:
            chunk = _load_chunk(p)
            event = {"event": "chunk_loaded", "payload": {"chunk_id": chunk.article_label or p.name, "hierarchy_path": chunk.hierarchy_path, "text_preview": chunk.text[:160]}}
            if out: out.write(json.dumps(event, ensure_ascii=False) + "\n")

            # identify
            target = identifier.identify(chunk)
            chunk.target_article = target
            if out: out.write(json.dumps({"event":"target_identified","payload": {"article": target.article, "code": target.code, "operation_type": target.operation_type.value}} , ensure_ascii=False) + "\n")

            # retrieve
            original_text, retrieval_meta = retriever.fetch_article_for_target(target)
            if out: out.write(json.dumps({"event":"retrieval_done","payload": {"success": bool(original_text), "source": retrieval_meta.get("source"), "text_length": len(original_text)}}, ensure_ascii=False) + "\n")

            # reconstruct
            recon_out = reconstructor.reconstruct_text(original_text, chunk)
            if out: out.write(json.dumps({"event":"reconstruct_done","payload": {"deleted_len": len(recon_out.deleted_or_replaced_text), "inserted_len": len(recon_out.newly_inserted_text), "after_len": len(recon_out.intermediate_after_state_text)}}, ensure_ascii=False) + "\n")

            # locate + link
            located = locator.locate(recon_out)
            linked = linker.link_references(located, original_text, recon_out.intermediate_after_state_text)
            if out: out.write(json.dumps({"event":"link_done","payload": {"count": len(linked)}}, ensure_ascii=False) + "\n")

            # resolve
            resolution = resolver.resolve_references(linked, original_text, target, recon_out.intermediate_after_state_text)
            if out:
                out.write(json.dumps({"event":"resolve_done","payload": {
                    "definitional": len(resolution.resolved_definitional_references),
                    "deletional": len(resolution.resolved_deletional_references),
                    "unresolved": len(resolution.unresolved_references),
                }}, ensure_ascii=False) + "\n")

            # Step 8: Synthesize annotated before/after fragments (deterministic)
            synthesizer = LegalStateSynthesizer(LegalStateSynthesizerConfig())
            synthesis = synthesizer.synthesize(
                chunk=chunk,
                target=target,
                recon=recon_out,
                resolution=resolution,
                original_article_text=original_text,
            )
            if out:
                spans = synthesis.metadata.get("contextual_spans", {}) or {}
                out.write(json.dumps({"event":"synthesize_done","payload": {
                    "before_annotations": len(synthesis.before_state.annotations),
                    "after_annotations": len(synthesis.after_state.annotations),
                    "before_preview": synthesis.before_state.text[:240],
                    "after_preview": synthesis.after_state.text[:240],
                    "before_context": spans.get("before", ""),
                    "after_context": spans.get("after", ""),
                }}, ensure_ascii=False) + "\n")
    finally:
        if out: out.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunk", action="append", default=[], help="Path to a chunk .txt file (repeatable)")
    ap.add_argument("--dir", default=None, help="Directory of chunk .txt files")
    ap.add_argument("--log-file", default=None)
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    # Load environment variables (prefer local override if present)
    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env.local")
    load_dotenv(project_root / ".env")

    paths: List[Path] = []
    for c in args.chunk:
        paths.append(Path(c))
    if args.dir:
        for p in sorted(Path(args.dir).glob("*.txt")):
            paths.append(p)
    if not paths:
        raise SystemExit("No chunks provided")

    run_on_chunks(paths, Path(args.log_file) if args.log_file else None, use_cache=not args.no_cache)


if __name__ == "__main__":
    main()


