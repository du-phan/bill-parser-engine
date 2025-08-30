"""
Step 8: LegalStateSynthesizer

Deterministically renders annotated before/after fragments using already-resolved
references from Step 7. No LLM calls. Operates only on delta fragments, keeping
the focused approach intact.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import List, Optional, Tuple, Dict, Any

from .models import (
    BillChunk,
    TargetArticle,
    TargetOperationType,
    ReconstructorOutput,
    ResolutionResult,
    LegalStateSynthesizerConfig,
    LegalReferenceAnnotation,
    LegalState,
    LegalAnalysisOutput,
    ReferenceSourceType,
)


class LegalStateSynthesizer:
    def __init__(self, config: Optional[LegalStateSynthesizerConfig] = None) -> None:
        self.config = config or LegalStateSynthesizerConfig()

    def synthesize(
        self,
        *,
        chunk: BillChunk,
        target: TargetArticle,
        recon: ReconstructorOutput,
        resolution: ResolutionResult,
        original_article_text: Optional[str] = None,
    ) -> LegalAnalysisOutput:
        # Select fragments based on operation type
        op = target.operation_type
        before_text, after_text = self._select_fragments(op, recon)

        # Build per-fragment resolved references
        before_resolved = resolution.resolved_deletional_references
        after_resolved = resolution.resolved_definitional_references

        # Annotate fragments
        before_state = self._annotate_fragment(before_text, before_resolved, ReferenceSourceType.DELETIONAL)
        after_state = self._annotate_fragment(after_text, after_resolved, ReferenceSourceType.DEFINITIONAL)

        # Compute optional contextual spans (lawyer-friendly view) using original and after-state
        contextual_spans: Dict[str, str] = {}
        try:
            before_ctx = self._compute_context_span(
                original_article_text or "",
                recon.deleted_or_replaced_text or "",
            ) if original_article_text and (target.operation_type in (TargetOperationType.MODIFY, TargetOperationType.ABROGATE)) else ""
            after_ctx = self._compute_context_span(
                recon.intermediate_after_state_text or "",
                (recon.newly_inserted_text or recon.deleted_or_replaced_text or ""),
            ) if recon.intermediate_after_state_text else ""
            contextual_spans = {"before": before_ctx, "after": after_ctx}

            # Enhancement: if before_ctx is empty but we have an alinéa ordinal in the instruction,
            # extract that paragraph from original/after to present a lawyer-friendly view.
            if (not before_ctx) and original_article_text and (target.operation_type == TargetOperationType.MODIFY):
                ordinal = self._parse_alinea_ordinal(chunk.text)
                if ordinal is not None and ordinal >= 1:
                    orig_para = self._extract_alinea_by_index(original_article_text, ordinal)
                    after_para = self._extract_alinea_by_index(recon.intermediate_after_state_text or "", ordinal)
                    if orig_para:
                        contextual_spans["before"] = orig_para
                    if after_para:
                        contextual_spans["after"] = after_para
                # If alinéa extraction failed, fallback to token-anchor like "Après le mot : « X »"
                if not contextual_spans.get("before"):
                    token = self._parse_after_word_token(chunk.text)
                    if token:
                        contextual_spans["before"] = self._compute_context_span(original_article_text, token)
                        contextual_spans["after"] = self._compute_context_span(
                            recon.intermediate_after_state_text or "", token
                        )
        except Exception:
            contextual_spans = {"before": "", "after": ""}

        metadata = {
            "chunk_id": chunk.chunk_id,
            "target": {
                "operation_type": op.value,
                "code": target.code,
                "article": target.article,
            },
            "counts": {
                "before": {
                    "resolved": len(before_resolved),
                    "annotated": len(before_state.annotations),
                },
                "after": {
                    "resolved": len(after_resolved),
                    "annotated": len(after_state.annotations),
                },
            },
            "config": asdict(self.config),
            "contextual_spans": contextual_spans,
        }

        return LegalAnalysisOutput(before_state=before_state, after_state=after_state, metadata=metadata)

    # --- internals ---

    def _select_fragments(self, op: TargetOperationType, recon: ReconstructorOutput) -> Tuple[str, str]:
        # Map high-level operation types to focused fragments
        if op == TargetOperationType.MODIFY:
            # Replacements/rewrites are represented in the delta fields
            return recon.deleted_or_replaced_text or "", recon.newly_inserted_text or ""
        if op == TargetOperationType.ABROGATE:
            # Deletions only
            return recon.deleted_or_replaced_text or "", ""
        if op == TargetOperationType.INSERT:
            # Insertions only
            return "", recon.newly_inserted_text or ""
        if op in (TargetOperationType.RENUMBER, TargetOperationType.OTHER):
            # No meaningful delta to annotate
            return "", ""
        # Fallback
        return recon.deleted_or_replaced_text or "", recon.newly_inserted_text or ""

    def _annotate_fragment(self, text: str, resolved_refs: List[Any], source: ReferenceSourceType) -> LegalState:
        if not text:
            return LegalState(text="", annotations=[])

        # Build matches
        matches: List[Tuple[int, int, Any]] = []  # (start, end, resolved_ref)
        used_texts: set = set()
        for rr in resolved_refs:
            ref_text = rr.linked_reference.reference_text
            # Exact match
            pos = text.find(ref_text)
            if pos == -1 and self.config.normalize_matching:
                # Try normalized/regex fallback
                pattern = self._build_permissive_pattern(ref_text)
                m = re.search(pattern, text, re.IGNORECASE)
                if m:
                    pos, end = m.start(), m.end()
                    matches.append((pos, end, rr))
                    continue
            if pos != -1:
                matches.append((pos, pos + len(ref_text), rr))

        if not matches:
            return LegalState(text=text, annotations=[])

        # Sort and resolve overlaps: start asc, length desc
        matches.sort(key=lambda t: (t[0], -(t[1] - t[0])))
        accepted: List[Tuple[int, int, Any]] = []
        last_end = -1
        seen_ref_texts: set = set()
        for s, e, rr in matches:
            if s < last_end:
                # overlap; skip shorter span (we sorted length desc within equal starts)
                continue
            ref_text = rr.linked_reference.reference_text
            if (not self.config.annotate_all_occurrences) and (ref_text in seen_ref_texts):
                continue
            accepted.append((s, e, rr))
            last_end = e
            seen_ref_texts.add(ref_text)

        # Insert markers and build annotations
        annotated_text = text
        annotations: List[LegalReferenceAnnotation] = []
        offset_delta = 0
        for idx, (s, e, rr) in enumerate(accepted, start=1):
            insert_pos = e + offset_delta
            marker = f"[{idx}]"
            annotated_text = annotated_text[:insert_pos] + marker + annotated_text[insert_pos:]
            ann = LegalReferenceAnnotation(
                marker_index=idx,
                reference_text=rr.linked_reference.reference_text,
                object=rr.linked_reference.object,
                resolved_content=self._truncate(rr.resolved_content, self.config.max_resolved_chars),
                source=source,
                start_offset=s + offset_delta,
                end_offset=e + offset_delta + len(marker),
                retrieval_metadata=rr.retrieval_metadata or {},
            )
            annotations.append(ann)
            offset_delta += len(marker)

        # Render footnotes (default)
        if self.config.render_mode == "footnote" and annotations:
            lines = []
            if self.config.footnote_prefix:
                lines.append(self.config.footnote_prefix)
            for a in annotations:
                lines.append(
                    f"{a.marker_index}. {a.reference_text} → {a.object}: {a.resolved_content} (source: {a.source.value})"
                )
            annotated_text = annotated_text.rstrip() + "\n\n" + "\n".join(lines)
        elif self.config.render_mode == "inline":
            # Already inserted [n]; could also append " (object: …)" inline in future if desired
            pass

        return LegalState(text=annotated_text, annotations=annotations)

    def _truncate(self, s: str, n: int) -> str:
        if not s or len(s) <= n:
            return s or ""
        return s[: max(0, n - 1)] + "…"

    def _compute_context_span(self, haystack: str, needle_text: str) -> str:
        """Return the paragraph/sentence span from haystack that best contains needle_text.

        - Try exact needle; if too long or missing, use a central slice of needle.
        - Prefer paragraph (split by double newlines or line breaks); fallback to sentence span.
        """
        if not haystack or not needle_text:
            return ""
        needle = needle_text.strip()
        if len(needle) > 120:
            # Use a mid-slice to improve matching robustness
            start = max(0, len(needle) // 2 - 60)
            needle = needle[start:start + 120]
        idx = haystack.find(needle)
        if idx == -1 and self.config.normalize_matching:
            # Relaxed search
            pattern = self._build_permissive_pattern(needle)
            m = re.search(pattern, haystack, re.IGNORECASE)
            if m:
                idx = m.start()
        if idx == -1:
            return ""
        # Determine paragraph boundaries
        # Split by double newline or single newline as a fallback
        paragraphs = re.split(r"\n\n+|\r?\n", haystack)
        # Track cumulative positions to find which paragraph contains idx
        pos = 0
        for para in paragraphs:
            start = pos
            end = pos + len(para)
            if start <= idx < end:
                # Found paragraph; if too short, fallback to sentence span
                if len(para.strip()) >= 20:
                    return para.strip()
                break
            pos = end + 1  # account for at least a newline
        # Sentence fallback
        # Find sentence boundaries around idx using simple punctuation heuristics
        left = haystack.rfind('.', 0, idx)
        left_q = haystack.rfind('»', 0, idx)
        if left_q > left:
            left = left_q
        right = haystack.find('.', idx)
        right_q = haystack.find('«', idx)
        if right == -1 or (right_q != -1 and right_q < right):
            right = right_q
        if left == -1:
            left = max(0, idx - 120)
        if right == -1:
            right = min(len(haystack), idx + 200)
        span = haystack[left:right + 1]
        return span.strip()

    def _parse_alinea_ordinal(self, instruction_text: str) -> Optional[int]:
        """Parse French ordinal alinéa reference (e.g., "sixième alinéa") to an integer index (1-based)."""
        if not instruction_text:
            return None
        text = instruction_text.lower()
        # Common ordinals up to 20, extend as needed
        ord_map = {
            "premier": 1, "première": 1, "deuxième": 2, "troisième": 3, "quatrième": 4, "cinquième": 5,
            "sixième": 6, "septième": 7, "huitième": 8, "neuvième": 9, "dixième": 10,
            "onzième": 11, "douzième": 12, "treizième": 13, "quatorzième": 14, "quinzième": 15,
            "seizième": 16, "dix-septième": 17, "dix-huitième": 18, "dix-neuvième": 19, "vingtième": 20,
        }
        m = re.search(r"(premier|première|deuxième|troisième|quatrième|cinquième|sixième|septième|huitième|neuvième|dixième|onzième|douzième|treizième|quatorzième|quinzième|seizième|dix-septième|dix-huitième|dix-neuvième|vingtième)\s+alinéa", text)
        if m:
            return ord_map.get(m.group(1))
        # Also support numeric form: "6e alinéa" / "6° alinéa"
        m2 = re.search(r"(\d+)[e°]?\s+alinéa", text)
        if m2:
            try:
                return int(m2.group(1))
            except ValueError:
                return None
        return None

    def _extract_alinea_by_index(self, article_text: str, ordinal_index: int) -> str:
        """Extract the Nth non-empty paragraph (approximate alinéa) from the article text."""
        if not article_text or ordinal_index < 1:
            return ""
        # Split by blank lines first; if not present, split by single newlines
        paragraphs = [p.strip() for p in re.split(r"\n\n+|\r?\n", article_text) if p.strip()]
        if not paragraphs:
            return ""
        if ordinal_index <= len(paragraphs):
            return paragraphs[ordinal_index - 1]
        return ""

    def _parse_after_word_token(self, instruction_text: str) -> Optional[str]:
        """Extract token from phrases like: Après le mot : « X » / Après le mot "X"."""
        if not instruction_text:
            return None
        m = re.search(r"Après\s+le\s+mot\s*[:]?\s*[«\"]([^»\"]+)[»\"]", instruction_text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        return None

    def _build_permissive_pattern(self, ref_text: str) -> str:
        # Escape regex special chars, then relax quotes/spaces
        escaped = re.escape(ref_text)
        # Replace French quotes and common punctuation spacing with permissive classes
        replacements = [
            ("«", "[«\"]"),
            ("»", "[»\"]"),
            ("\u00A0", "\\s+"),  # non-breaking space
            ("\u2019", "['’]"),
            ("\u2018", "['‘]"),
            ("\u201C", "[\"“]"),
            ("\u201D", "[\"”]"),
            ("\\s+", "\\s+")
        ]
        pattern = escaped
        for src, repl in replacements:
            pattern = pattern.replace(re.escape(src), repl)
        return pattern


