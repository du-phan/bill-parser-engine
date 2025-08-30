"""
Validate a single exported chunk (txt + json) through the pipeline (Steps 2–7) with assertions.

Usage:
  poetry run python scripts/validate_chunk_file.py --chunk scripts/output/duplomb_chunks/001___TITRE_I_Article_1_II_1_.txt \
    [--log-file scripts/output/validate_chunk_001.txt]

The script reconstructs a BillChunk from the JSON sidecar file and runs:
  2) TargetArticleIdentifier
  3) OriginalTextRetriever
  4) LegalAmendmentReconstructor
  5) ReferenceLocator
  6) ReferenceObjectLinker
  7) ReferenceResolver

It prints PASS/FAIL for each step and writes JSONL entries to the log (if provided).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from bill_parser_engine.core.reference_resolver.models import (
    BillChunk,
    TargetArticle,
    TargetOperationType,
    ReconstructorOutput,
    LocatedReference,
    LinkedReference,
)
from bill_parser_engine.core.reference_resolver.target_identifier import TargetArticleIdentifier
from bill_parser_engine.core.reference_resolver.legal_amendment_reconstructor import (
    LegalAmendmentReconstructor,
)
from bill_parser_engine.core.reference_resolver.reference_locator import ReferenceLocator
from bill_parser_engine.core.reference_resolver.reference_object_linker import ReferenceObjectLinker


def _load_chunk(chunk_txt_path: Path) -> BillChunk:
    if not chunk_txt_path.exists():
        raise FileNotFoundError(f"Chunk text not found: {chunk_txt_path}")
    base = chunk_txt_path.with_suffix("")
    json_path = base.with_suffix(".json")
    if not json_path.exists():
        raise FileNotFoundError(f"Chunk metadata not found: {json_path}")
    text = chunk_txt_path.read_text(encoding="utf-8")
    meta = json.loads(json_path.read_text(encoding="utf-8"))

    # Build BillChunk
    bc = BillChunk(
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
        structural_anchor_hint=meta.get("structural_anchor_hint"),
    )

    # Best-effort: derive an inherited target from introductory phrases if present
    try:
        import re as _re
        intro_np = meta.get("numbered_point_introductory_phrase") or ""
        intro_art = meta.get("article_introductory_phrase") or ""
        intro_ms = meta.get("major_subdivision_introductory_phrase") or ""

        # Normalize apostrophes for matching
        intro_np = intro_np.replace("’", "'")
        intro_art = intro_art.replace("’", "'")
        intro_ms = intro_ms.replace("’", "'")

        # Extract article id
        def _extract_article(s: str) -> str | None:
            m = _re.search(r"(?i)\b([LRD]\.\s*\d[\d\-]*)\b", s)
            return _re.sub(r"\s+", " ", m.group(1).strip()) if m else None

        # Extract code name
        def _extract_code(*phrases: str) -> str | None:
            for p in phrases:
                m = _re.search(r"(?i)\bcode\s+[^:\n]+", p)
                if m:
                    return m.group(0).strip().lower()
            return None

        inferred_article = _extract_article(intro_np) or _extract_article(intro_art)
        inferred_code = _extract_code(intro_np, intro_art, intro_ms)

        if inferred_article:
            # Determine correct operation type based on context
            operation_type = TargetOperationType.MODIFY  # Default
            
            # Check for INSERT patterns
            all_intro_text = f"{intro_np} {intro_art} {intro_ms}"
            if any(pattern in all_intro_text.lower() for pattern in [
                "est complétée par un article", 
                "il est inséré un article",
                "il est ajouté un article",
                "au début du chapitre"
            ]):
                operation_type = TargetOperationType.INSERT
            
            bc.inherited_target_article = TargetArticle(
                operation_type=operation_type,
                code=inferred_code,
                article=inferred_article,
                confidence=1.0,
            )
    except Exception:
        # Non-fatal; leave inherited_target_article as None
        pass

    return bc


def _write_log(log_path: Optional[Path], event: str, payload: Dict[str, Any]) -> None:
    if not log_path:
        return
    entry = {"event": event, "payload": payload}
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _preview(text: Optional[str], max_len: int = 200) -> str:
    if not text:
        return ""
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def validate_chunk(chunk_file: Path, log_file: Optional[Path], override_code: Optional[str] = None, override_article: Optional[str] = None) -> int:
    # Load env for MISTRAL
    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env.local")
    load_dotenv(project_root / ".env")

    bc = _load_chunk(chunk_file)
    _write_log(log_file, "chunk_loaded", {"chunk_id": bc.chunk_id, "hierarchy_path": bc.hierarchy_path})
    print(f"Validating chunk: {bc.chunk_id}")

    failures = 0

    # Step 2: Target identification (or manual override)
    try:
        if override_code and override_article:
            target = TargetArticle(
                operation_type=TargetOperationType.MODIFY,
                code=override_code,
                article=override_article,
                confidence=1.0,
            )
        else:
            identifier = TargetArticleIdentifier(use_cache=False)
            target = identifier.identify(bc)
        _write_log(log_file, "target_identified", {
            "operation_type": target.operation_type.value,
            "code": target.code,
            "article": target.article,
            "confidence": getattr(target, "confidence", None),
        })
        print(f"Step 2 PASS: {target.operation_type.value} {target.code} {target.article}")
    except Exception as e:
        failures += 1
        _write_log(log_file, "target_failed", {"error": str(e)})
        print(f"Step 2 FAIL: {e}")
        return failures

    # Step 3: Original text retrieval
    original_text = ""
    try:
        from bill_parser_engine.core.reference_resolver.original_text_retriever import (
            OriginalTextRetriever,
        )
        retriever = OriginalTextRetriever(use_cache=False)
        original_text, meta = retriever.fetch_article_for_target(target)
        _write_log(log_file, "retrieval_done", {"meta": meta, "original_text_preview": _preview(original_text)})
        # Basic validation
        if target.operation_type != TargetOperationType.INSERT and not original_text:
            raise AssertionError("Original text empty for non-INSERT operation")
        print(f"Step 3 PASS: source={meta.get('source')} len={len(original_text)}")
    except Exception as e:
        failures += 1
        _write_log(log_file, "retrieval_failed", {"error": str(e)})
        print(f"Step 3 FAIL: {e}")
        return failures

    # Step 4: Reconstruction
    recon_out: Optional[ReconstructorOutput] = None
    try:
        reconstructor = LegalAmendmentReconstructor(use_cache=False)
        recon_out = reconstructor.reconstruct_text(
            original_law_article=original_text,
            amendment_chunk=bc,
        )
        _write_log(log_file, "reconstruct_done", {
            "deleted_or_replaced_text": _preview(recon_out.deleted_or_replaced_text),
            "newly_inserted_text": _preview(recon_out.newly_inserted_text),
            "after_preview": _preview(recon_out.intermediate_after_state_text),
        })
        print("Step 4 PASS: reconstruction completed")
    except Exception as e:
        failures += 1
        _write_log(log_file, "reconstruct_failed", {"error": str(e)})
        print(f"Step 4 FAIL: {e}")
        return failures

    # Step 5: Locate
    located: List[LocatedReference] = []
    try:
        locator = ReferenceLocator(use_cache=False)
        located = locator.locate(recon_out)
        _write_log(log_file, "locate_done", {
            "count": len(located),
            "refs": [{"text": r.reference_text, "source": r.source.value, "conf": r.confidence} for r in located],
        })
        print(f"Step 5 PASS: located={len(located)}")
    except Exception as e:
        failures += 1
        _write_log(log_file, "locate_failed", {"error": str(e)})
        print(f"Step 5 FAIL: {e}")
        return failures

    # Step 6: Link
    linked: List[LinkedReference] = []
    try:
        linker = ReferenceObjectLinker(use_cache=False)
        linked = linker.link_references(
            located_references=located,
            original_law_article=original_text,
            intermediate_after_state_text=recon_out.intermediate_after_state_text,
        )
        _write_log(log_file, "link_done", {
            "count": len(linked),
            "links": [{"text": r.reference_text, "obj": r.object, "conf": r.confidence} for r in linked],
        })
        print(f"Step 6 PASS: linked={len(linked)}")
    except Exception as e:
        failures += 1
        _write_log(log_file, "link_failed", {"error": str(e)})
        print(f"Step 6 FAIL: {e}")
        return failures

    # Step 7: Resolve
    try:
        from bill_parser_engine.core.reference_resolver.reference_resolver import ReferenceResolver
        resolver = ReferenceResolver(use_cache=False)
        # Minimal TargetArticle context
        target_obj = TargetArticle(
            operation_type=target.operation_type,
            code=target.code,
            article=target.article,
            confidence=getattr(target, "confidence", None),
        )
        res = resolver.resolve_references(linked, original_text, target_obj)
        # Validate non-empty resolved content
        for rr in res.resolved_definitional_references + res.resolved_deletional_references:
            if not rr.resolved_content or not rr.resolved_content.strip():
                raise AssertionError(f"Empty resolved content for: {rr.linked_reference.reference_text}")
        _write_log(log_file, "resolve_done", {
            "counts": {
                "def": len(res.resolved_definitional_references),
                "del": len(res.resolved_deletional_references),
                "unres": len(res.unresolved_references),
            }
        })
        print(
            f"Step 7 PASS: def={len(res.resolved_definitional_references)} del={len(res.resolved_deletional_references)} unres={len(res.unresolved_references)}"
        )
    except Exception as e:
        failures += 1
        _write_log(log_file, "resolve_failed", {"error": str(e)})
        print(f"Step 7 FAIL: {e}")

    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a single chunk through the pipeline")
    parser.add_argument("--chunk", required=True, help="Path to chunk .txt file (sidecar .json required)")
    parser.add_argument("--log-file", default=None, help="Optional JSONL log output path")
    parser.add_argument("--override-code", default=None, help="Override code name for target identification")
    parser.add_argument("--override-article", default=None, help="Override article id for target identification")
    args = parser.parse_args()

    chunk_path = Path(args.chunk)
    log_path = Path(args.log_file) if args.log_file else None
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if log_path.exists():
            log_path.unlink()

    failures = validate_chunk(chunk_path, log_path, override_code=args.override_code, override_article=args.override_article)
    if failures == 0:
        print("ALL STEPS PASS")
    else:
        print(f"FAILED with {failures} errors")


if __name__ == "__main__":
    main()


