"""
OperationApplier component for applying atomic legal amendment operations.

This component uses LLM-based processing to apply individual atomic operations
to legal text with sophisticated understanding of French legal document structure
and formatting variations.
"""

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any, List

from mistralai import Mistral

from bill_parser_engine.core.reference_resolver.models import AmendmentOperation, OperationType
from bill_parser_engine.core.reference_resolver.cache_manager import SimpleCache
from bill_parser_engine.core.reference_resolver.prompts import (
    OPERATION_APPLIER_SYSTEM_PROMPT,
    OPERATION_APPLIER_USER_PROMPT_TEMPLATE
)
from bill_parser_engine.core.reference_resolver.rate_limiter import rate_limiter, call_mistral_with_messages

logger = logging.getLogger(__name__)


@dataclass
class OperationApplicationResult:
    """Result of applying a single atomic operation."""
    success: bool
    modified_text: str
    applied_fragment: str
    error_message: Optional[str] = None
    confidence: float = 0.0
    processing_time_ms: int = 0


class OperationApplier:
    """
    Applies atomic legal amendment operations to text using LLM intelligence.
    
    Handles:
    - Format differences between amendment text and original legal text
    - Complex legal position specifications
    - French legal document structure preservation
    - All 6 operation types: REPLACE, DELETE, INSERT, ADD, REWRITE, ABROGATE
    """

    def __init__(self, api_key: Optional[str] = None, cache: Optional[SimpleCache] = None, use_cache: bool = True):
        """
        Initialize the operation applier.

        Args:
            api_key: Mistral API key (defaults to MISTRAL_API_KEY environment variable)
            cache: Cache instance (uses global if None)
            use_cache: Whether to use caching
        """
        self.api_key = api_key or os.getenv("MISTRAL_API_KEY")
        if not self.api_key:
            raise ValueError("MISTRAL_API_KEY environment variable is required")
        
        self.client = Mistral(api_key=self.api_key)
        self.cache = cache or SimpleCache()
        self.use_cache = use_cache
        
        logger.info("OperationApplier initialized with caching: %s", "enabled" if use_cache else "disabled")

    def apply_single_operation(
        self, 
        original_text: str, 
        operation: AmendmentOperation
    ) -> OperationApplicationResult:
        """
        Apply a single amendment operation to legal text.

        Returns an OperationApplicationResult with success status and modified text.
        """
        start_time = time.time()
        logger.info("Applying %s operation: %.100s...", operation.operation_type.value, str(operation))
        
        # 0) Deterministic fast-paths using structured position hints (JSON) or recognizable targets
        deterministic_result = self._try_deterministic_application(original_text, operation)
        if deterministic_result is not None:
            deterministic_result.processing_time_ms = int((time.time() - start_time) * 1000)
            logger.info("Deterministic application path used - Success: %s", deterministic_result.success)
            if deterministic_result.success:
                # Cache and return on success
                if self.use_cache:
                    cache_key_data = {
                        'original_text': original_text,
                        'operation_type': operation.operation_type.value,
                        'target_text': operation.target_text,
                        'replacement_text': operation.replacement_text,
                        'position_hint': operation.position_hint
                    }
                    serialized_result = self._serialize_result(deterministic_result)
                    self.cache.set("operation_applier", cache_key_data, serialized_result)
                return deterministic_result
            # On deterministic failure, fall through to standard LLM path

        # 1) Simple input validation to catch common failure patterns
        validation_result = self._validate_operation_input(original_text, operation)
        if not validation_result.success:
            # Idempotency: If REPLACE target not found but replacement already present, treat as success no-op
            try:
                if (
                    operation.operation_type == OperationType.REPLACE
                    and operation.replacement_text
                    and self._replacement_already_present(original_text, operation)
                ):
                    logger.info("Idempotent REPLACE detected: replacement already present; returning no-op success")
                    return OperationApplicationResult(
                        success=True,
                        modified_text=original_text,
                        applied_fragment=operation.replacement_text,
                        confidence=0.99,
                        error_message=None,
                        processing_time_ms=int((time.time() - start_time) * 1000),
                    )
            except Exception:
                # Non-fatal: continue with failure return below
                pass
            return OperationApplicationResult(
                success=False,
                modified_text=original_text,
                applied_fragment="",
                error_message=validation_result.error_message,
                processing_time_ms=int((time.time() - start_time) * 1000)
            )
        
        # Check cache first
        if self.use_cache:
            cache_key_data = {
                'original_text': original_text,
                'operation_type': operation.operation_type.value,
                'target_text': operation.target_text,
                'replacement_text': operation.replacement_text,
                'position_hint': operation.position_hint
            }
            cached_result = self.cache.get("operation_applier", cache_key_data)
            if cached_result is not None:
                logger.info("Found cached operation result")
                return self._deserialize_result(cached_result)

        start_time = time.time()
        
        try:
            # Build prompts
            system_prompt = OPERATION_APPLIER_SYSTEM_PROMPT
            user_prompt = self._build_user_prompt(original_text, operation)
            
            # Call LLM with rate limiting
            response = call_mistral_with_messages(
                client=self.client,
                rate_limiter=rate_limiter,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                component_name="OperationApplier",
                temperature=0.0,  # Deterministic application
                response_format={"type": "json_object"}
            )
            
            # Parse response
            response_content = response.choices[0].message.content
            logger.debug("Raw LLM response: %s", response_content)
            
            result_data = json.loads(response_content)
            result = self._parse_response(result_data, operation)
            
            processing_time = int((time.time() - start_time) * 1000)
            result.processing_time_ms = processing_time
            
            logger.info("Operation applied - Success: %s", result.success)
            
            # Cache result
            if self.use_cache:
                serialized_result = self._serialize_result(result)
                self.cache.set("operation_applier", cache_key_data, serialized_result)
            
            return result

        except json.JSONDecodeError as e:
            logger.error("Failed to parse LLM response as JSON: %s", e)
            return OperationApplicationResult(
                success=False,
                modified_text=original_text,
                applied_fragment="",
                error_message=f"Invalid LLM response format: {e}",
                processing_time_ms=int((time.time() - start_time) * 1000)
            )
        except Exception as e:
            logger.error("Failed to apply operation: %s", e)
            return OperationApplicationResult(
                success=False,
                modified_text=original_text,
                applied_fragment="",
                error_message=f"Operation application failed: {e}",
                processing_time_ms=int((time.time() - start_time) * 1000)
            )

    # ---- Deterministic application helpers ----
    def _try_deterministic_application(self, original_text: str, operation: AmendmentOperation) -> Optional[OperationApplicationResult]:
        """Attempt deterministic application for supported structured hints before using the LLM.
        Supports:
        - Alinéa-level REWRITE or REPLACE indicated by JSON position_hint {"type":"alinea","index":N|"last"|"prev"}
        - REPLACE with target_text like "Le cinquième alinéa" (converted to paragraph REWRITE)
        """
        hint = self._parse_position_hint(operation.position_hint)

        # Heuristic: REPLACE of a full alinéa label → paragraph REWRITE
        if operation.operation_type == OperationType.REPLACE and self._is_full_alinea_target(operation.target_text or ""):
            # Convert to REWRITE behavior on the indicated alinéa if any, else fallback to LLM
            if hint and hint.get("type") == "alinea":
                return self._apply_alinea_rewrite(original_text, operation.replacement_text or "", hint)
            # Try without explicit hint: not safe → fall back to LLM
            return None

        # REWRITE/REPLACE with explicit alinéa position
        if operation.operation_type in (OperationType.REWRITE, OperationType.REPLACE) and hint:
            logger.info("Deterministic hint parsed: %s", hint)
            # New: scoped REPLACE within a specific numbered point inside a Roman numeral section
            if operation.operation_type == OperationType.REPLACE and hint.get("type") == "structure" and hint.get("point") and hint.get("section"):
                logger.info("Attempting scoped section-point REPLACE at section=%s point=%s", hint.get("section"), hint.get("point"))
                scoped = self._apply_scoped_section_point_replace(original_text, operation.target_text or "", operation.replacement_text or "", hint)
                if scoped is not None:
                    logger.info("Scoped REPLACE result: success=%s, fragment=%.80s", scoped.success, scoped.applied_fragment)
                    return scoped
            # New: token-tail micro-edit inside an alinéa or uniquely matched paragraph
            if (hint.get("after_word") or hint.get("after_words")) and hint.get("token_action") == "replace_tail":
                return self._apply_alinea_token_tail_rewrite(original_text, operation.replacement_text or "", hint)
            # Fallback to full alinéa rewrite if explicitly indicated
            if hint.get("type") == "alinea" or hint.get("alinea_index"):
                # Normalize to index field for compatibility
                if hint.get("alinea_index") and not hint.get("index"):
                    hint = {**hint, "index": hint.get("alinea_index")}
                return self._apply_alinea_rewrite(original_text, operation.replacement_text or "", hint)

        return None

    def _parse_position_hint(self, position_hint: Optional[str]) -> Optional[Dict[str, Any]]:
        if not position_hint:
            return None
        try:
            # Try JSON first
            data = json.loads(position_hint)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        # Natural-language fallback parsing for common hints like "au 3° du II"
        if isinstance(position_hint, str):
            import re as _re
            s = position_hint
            m = _re.search(r"(?i)au\s+(?P<point>\d+)°\s+du\s+(?P<section>[IVXLCDM]+)", s)
            if m:
                return {"type": "structure", "placement": "at", "point": m.group("point"), "section": m.group("section")}
            m = _re.search(r"(?i)à\s+la\s+fin\s+du\s+(?P<section>[IVXLCDM]+)", s)
            if m:
                return {"type": "structure", "placement": "at_end", "section": m.group("section")}
            m = _re.search(r"(?i)au\s+début\s+du\s+(?P<section>[IVXLCDM]+)", s)
            if m:
                return {"type": "structure", "placement": "at_start", "section": m.group("section")}
        return None

    def _apply_alinea_rewrite(self, original_text: str, replacement_text: str, hint: Dict[str, Any]) -> OperationApplicationResult:
        """Apply a paragraph-level rewrite using alinéa index semantics."""
        if not replacement_text:
            return OperationApplicationResult(success=False, modified_text=original_text, applied_fragment="", error_message="Missing replacement_text for alinéa rewrite")

        # Segment into paragraphs (alinéas). Try multiple strategies to be robust across code styles.
        paragraphs = self._split_into_paragraphs(original_text)
        if len(paragraphs) < 2:
            # Fallback: split by single newlines as individual alinéas if blank lines are not used
            paragraphs = [p for p in original_text.splitlines() if p.strip()]
        if not paragraphs:
            return OperationApplicationResult(success=False, modified_text=original_text, applied_fragment="", error_message="Could not segment text into alinéas")

        # Resolve index
        index_spec = hint.get("index")
        idx: Optional[int] = None
        if isinstance(index_spec, int):
            idx = index_spec
        elif isinstance(index_spec, str):
            if index_spec == "last":
                idx = len(paragraphs)
            # 'prev' is ambiguous without context; not supported in isolation
        
        # Default to None if invalid
        if idx is None or idx < 1 or idx > len(paragraphs):
            return OperationApplicationResult(success=False, modified_text=original_text, applied_fragment="", error_message=f"Invalid alinéa index: {index_spec}")

        # Replace paragraph at 1-based index
        new_paragraphs = paragraphs[:]
        new_paragraphs[idx - 1] = replacement_text.strip()
        modified = self._join_paragraphs(new_paragraphs)
        return OperationApplicationResult(success=True, modified_text=modified, applied_fragment=replacement_text.strip(), confidence=0.95)

    def _apply_scoped_section_point_replace(self, original_text: str, target_text: str, replacement_text: str, hint: Dict[str, Any]) -> Optional[OperationApplicationResult]:
        """Apply a REPLACE limited to the scope of a numbered point within a Roman numeral section.
        Expected hint keys: {type:"structure", section:"II", point:"3", [section_suffix], [point_suffix]}
        Strategy:
          1) Locate the section block for the Roman numeral (e.g., "II.") up to next Roman numeral or end.
          2) Within that block, locate the numbered point line (e.g., "3°") and capture its block up to next point.
          3) Perform a single replacement of target_text→replacement_text within that block only.
        """
        import re
        section = hint.get("section")
        point = hint.get("point")
        if not section or not point or not target_text or not replacement_text:
            return None

        text = original_text
        # 1) Locate section block
        # Recognize forms like "II.", "II -", "II –", or line starting with "II"
        # Allow punctuation combos after Roman numeral label, e.g., "II.-" or "II) -"
        section_pattern = rf"(?m)^\s*(?P<label>{re.escape(section)})\s*(?:[.\-–—\)]+)\s*"
        # Find start of section
        sec_match_iter = list(re.finditer(section_pattern, text))
        if not sec_match_iter:
            logger.warning("Section label '%s' not found with strict pattern; falling back to whole text scope", section)
            sec_start = 0
            sec_end = len(text)
            section_text = text
        else:
            sec_match = sec_match_iter[0]
            sec_start = sec_match.start()
            # Determine end of section by finding next Roman numeral label at line start
            roman_line = r"(?m)^\s*[IVXLCDM]+\s*(?:[.\-–—\)]+)\s*"
            next_sec = re.search(roman_line, text[sec_match.end():])
            sec_end = sec_match.end() + (next_sec.start() if next_sec else len(text[sec_match.end():]))
            section_text = text[sec_start:sec_end]
        try:
            sample = section_text[:300].replace("\n", " ⏎ ")
            logger.info("Section '%s' block (head): %.300s", section, sample)
            import re as _re_dbg
            pts = _re_dbg.findall(r"(?m)^\s*\d+[^\n]*", section_text)
            logger.info("Detected %d potential point lines in section", len(pts))
            if pts:
                logger.info("First point line: %.200s", pts[0])
        except Exception:
            pass

        # 2) Within section, locate point block (e.g., "3°"). Tolerate degree sign °, ordinal º, or ascii 'o'.
        # Do not require line starts; points can be inline.
        point_anywhere = re.search(rf"{re.escape(point)}(?:[°ºo])", section_text)
        if not point_anywhere:
            logger.warning("Point label '%s°' not found in section; cannot scope replace", point)
            return None
        p_start = point_anywhere.start()
        # End at next point occurrence or end of section
        next_point = re.search(r"\d+(?:[°ºo])", section_text[p_start + len(point):])
        p_end = (p_start + len(point) + next_point.start()) if next_point else len(section_text)
        point_block = section_text[p_start:p_end]

        logger.info("Point block (head raw): %.200s", point_block[:200].replace("\n", " ⏎ "))
        # 3) Replace once within point block using normalization + index mapping
        norm_block, map_block = self._normalize_for_match(point_block)
        norm_target, _ = self._normalize_for_match(target_text)
        logger.info("Normalized target for match: %.120s", norm_target)
        logger.info("Normalized point block (head): %.200s", norm_block[:200])

        def _try_find_span(nb: str, nt: str) -> Optional[tuple[int, int]]:
            idx = nb.find(nt)
            if idx == -1:
                return None
            return idx, idx + len(nt)

        span = _try_find_span(norm_block, norm_target)
        logger.info("Exact normalized match found: %s", span is not None)

        # If not found, try relaxed variants: agreement on "prévu" and optional plural on "article(s)"
        if span is None:
            relaxed_targets: List[str] = self._generate_relaxed_targets(norm_target)
            logger.info("Trying %d relaxed target variants", len(relaxed_targets))
            for cand in relaxed_targets:
                span = _try_find_span(norm_block, cand)
                if span is not None:
                    logger.info("Matched relaxed variant: %.120s", cand)
                    break
        if span is None:
            logger.warning("No match found after normalization and relaxations")

        if span is None:
            return None

        nstart, nend = span
        # Map normalized span back to original indices
        try:
            ostart = map_block[nstart]
            oend = map_block[nend - 1] + 1
        except Exception:
            logger.warning("Failed to map normalized span back to original indices: %s-%s", nstart, nend)
            return None
        logger.info("Original indices mapped: %d-%d", ostart, oend)

        new_block = point_block[:ostart] + replacement_text + point_block[oend:]
        n = 1

        if n == 0:
            return None

        # Reconstruct text
        new_section = section_text[:p_start] + new_block + section_text[p_end:]
        modified = text[:sec_start] + new_section + text[sec_end:]
        return OperationApplicationResult(success=True, modified_text=modified, applied_fragment=replacement_text.strip(), confidence=0.9)

    def _extract_section_point_block(self, original_text: str, hint: Dict[str, Any]) -> Optional[str]:
        """Extract text block for a specific Roman numeral section and numbered point using the same
        scoping logic as _apply_scoped_section_point_replace, but without performing replacement.
        Returns the block substring or None if scoping could not be determined.
        """
        import re
        section = hint.get("section")
        point = hint.get("point")
        if not section or not point:
            return None
        text = original_text
        section_pattern = rf"(?m)^\s*(?P<label>{re.escape(section)})\s*(?:[.\-–—\)]+)\s*"
        sec_match_iter = list(re.finditer(section_pattern, text))
        if not sec_match_iter:
            return None
        sec_match = sec_match_iter[0]
        roman_line = r"(?m)^\s*[IVXLCDM]+\s*(?:[.\-–—\)]+)\s*"
        next_sec = re.search(roman_line, text[sec_match.end():])
        sec_end = sec_match.end() + (next_sec.start() if next_sec else len(text[sec_match.end():]))
        section_text = text[sec_match.start():sec_end]
        point_anywhere = re.search(rf"{re.escape(point)}(?:[°ºo])", section_text)
        if not point_anywhere:
            return None
        p_start = point_anywhere.start()
        next_point = re.search(r"\d+(?:[°ºo])", section_text[p_start + len(point):])
        p_end = (p_start + len(point) + next_point.start()) if next_point else len(section_text)
        return section_text[p_start:p_end]

    def _replacement_already_present(self, original_text: str, operation: AmendmentOperation) -> bool:
        """Return True if the replacement_text already appears in the scoped block (if any) or globally.
        Uses normalization identical to _normalize_for_match for robust checks.
        """
        try:
            hint = self._parse_position_hint(operation.position_hint)
        except Exception:
            hint = None
        candidate_text = original_text
        # If we have a structured section/point hint, narrow to that block
        if hint and hint.get("type") == "structure" and hint.get("section") and hint.get("point"):
            block = self._extract_section_point_block(original_text, hint)
            if block:
                candidate_text = block
        norm_cand, _ = self._normalize_for_match(candidate_text)
        norm_repl, _ = self._normalize_for_match(operation.replacement_text or "")
        if not norm_repl:
            return False
        return norm_repl in norm_cand

    def _apply_alinea_token_tail_rewrite(self, original_text: str, replacement_text: str, hint: Dict[str, Any]) -> OperationApplicationResult:
        """Replace the tail of the specified alinéa after a given token with replacement_text.
        Hint fields used:
          - alinea_index: 1-based index of the paragraph
          - after_word/after_words: token to anchor replacement
          - scope: 'sentence' (default) or 'paragraph'
        """
        if not replacement_text:
            return OperationApplicationResult(success=False, modified_text=original_text, applied_fragment="", error_message="Missing replacement_text for token-tail rewrite")

        # Segment paragraphs
        paragraphs = self._split_into_paragraphs(original_text)
        if len(paragraphs) < 2:
            paragraphs = [p for p in original_text.splitlines() if p.strip()]
        if not paragraphs:
            return OperationApplicationResult(success=False, modified_text=original_text, applied_fragment="", error_message="Could not segment text into alinéas")

        index_spec = hint.get("alinea_index") or hint.get("index")
        idx: Optional[int] = None
        if isinstance(index_spec, int):
            idx = index_spec
        elif isinstance(index_spec, str):
            if index_spec == "last":
                idx = len(paragraphs)
        # Allow proceeding without a valid explicit index; we'll search globally if needed
        if not (isinstance(idx, int) and 1 <= idx <= len(paragraphs)):
            idx = None

        token = hint.get("after_word") or hint.get("after_words")
        if not token:
            return OperationApplicationResult(success=False, modified_text=original_text, applied_fragment="", error_message="Missing after_word token for tail rewrite")

        import re as _re

        def find_token_position(text: str, tk: str) -> int:
            """Return end index of token match in text, or -1.
            Uses case-insensitive search, tolerant to whitespace differences, and word boundaries when possible.
            """
            tk_esc = _re.escape(tk)
            patterns = [
                rf"\b{tk_esc}\b",
                tk_esc,
                tk_esc.replace(" ", "\\s+")
            ]
            for pat in patterns:
                m = _re.search(pat, text, _re.IGNORECASE)
                if m:
                    return m.end()
            return -1

        # Try specified alinéa first (if any)
        pos = -1
        para = None
        if isinstance(idx, int):
            para = paragraphs[idx - 1]
            pos = find_token_position(para, token)

        # If not found, search across all alinéas and choose best candidate
        if pos == -1:
            matches: List[tuple[int, int]] = []  # (paragraph_index, end_pos)
            for j, p in enumerate(paragraphs, start=1):
                ppos = find_token_position(p, token)
                if ppos != -1:
                    matches.append((j, ppos))
            if not matches:
                return OperationApplicationResult(success=False, modified_text=original_text, applied_fragment="", error_message=f"Anchor token not found in any alinéa: '{token}'")
            # Prefer the match at the requested alinéa index; else closest; else last
            if isinstance(index_spec, int):
                matches.sort(key=lambda t: abs(t[0] - index_spec))
                idx, pos = matches[0]
            else:
                idx, pos = matches[-1]
            para = paragraphs[idx - 1]

        prefix = para[:pos].rstrip()
        new_para = f"{prefix} {replacement_text.strip()}".strip()
        new_paragraphs = paragraphs[:]
        new_paragraphs[idx - 1] = new_para
        modified = self._join_paragraphs(new_paragraphs)
        return OperationApplicationResult(success=True, modified_text=modified, applied_fragment=replacement_text.strip(), confidence=0.95)

    def _split_into_paragraphs(self, text: str) -> List[str]:
        # Prefer double-newline as paragraph separator; if not found, fall back to single newline groups
        if "\n\n" in text:
            parts = [p for p in text.split("\n\n")]
            return parts
        # Fallback: split by single newline but merge short lines into paragraphs
        lines = text.splitlines()
        paragraphs: List[str] = []
        buf: List[str] = []
        for line in lines:
            if line.strip() == "":
                if buf:
                    paragraphs.append("\n".join(buf).strip())
                    buf = []
            else:
                buf.append(line)
        if buf:
            paragraphs.append("\n".join(buf).strip())
        return paragraphs

    def _join_paragraphs(self, paragraphs: List[str]) -> str:
        return "\n\n".join(paragraphs)

    # --- Matching utilities ---
    def _normalize_for_match(self, text: str) -> tuple[str, List[int]]:
        """Normalize text for robust substring matching and return index map to original text.
        Normalization steps:
          - Unicode NFKC
          - Collapse all whitespace (incl. NBSP) to single space
          - Normalize hyphen-like chars to '-'
          - Normalize French quotes to simple quotes
          - Lowercase
        Returns (normalized_text, index_map) where index_map[i] gives original index for normalized char i.
        """
        import unicodedata
        src = unicodedata.normalize("NFKC", text)
        hyphens = {"\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2015", "\u2212"}
        quotes_map = {"«": '"', "»": '"'}
        out_chars: List[str] = []
        index_map: List[int] = []
        i = 0
        last_was_space = False
        while i < len(src):
            ch = src[i]
            orig_idx = i
            # Map whitespace to space; collapse runs
            if ch.isspace():
                if not last_was_space:
                    out_chars.append(' ')
                    index_map.append(orig_idx)
                    last_was_space = True
                i += 1
                continue
            last_was_space = False
            # Hyphens
            if ch in hyphens:
                out_chars.append('-')
                index_map.append(orig_idx)
                i += 1
                continue
            # Quotes
            if ch in quotes_map:
                out_chars.append(quotes_map[ch])
                index_map.append(orig_idx)
                i += 1
                continue
            out_chars.append(ch.lower())
            index_map.append(orig_idx)
            i += 1
        # Trim leading/trailing spaces in normalized while preserving mapping
        # Left trim
        while out_chars and out_chars[0] == ' ':
            out_chars.pop(0)
            index_map.pop(0)
        # Right trim
        while out_chars and out_chars[-1] == ' ':
            out_chars.pop()
            index_map.pop()
        return ("".join(out_chars), index_map)

    def _generate_relaxed_targets(self, norm_target: str) -> List[str]:
        """Generate minimally relaxed target variants for robust matching.
        - Agreement on 'prévu' → 'prevu|prevus|prevue|prevues' (diacritics already preserved; also handle without accent)
        - Optional plural on 'article' → 'article' vs 'articles'
        """
        variants: List[str] = [norm_target]
        # Handle prévu agreement and accentless form
        def add_prev_variants(s: str) -> List[str]:
            outs = set()
            for token in ["prévu", "prevu"]:
                if token in s:
                    for form in ["prévu", "prévus", "prévue", "prévues", "prevu", "prevus", "prevue", "prevues"]:
                        outs.add(s.replace(token, form))
            return list(outs) or [s]
        new_vars: List[str] = []
        for v in variants:
            new_vars.extend(add_prev_variants(v))
        variants = list(dict.fromkeys(new_vars or variants))
        # Handle article(s)
        more_vars: List[str] = []
        for v in variants:
            if "article " in v:
                more_vars.append(v.replace("article ", "articles "))
            if "articles " in v:
                more_vars.append(v.replace("articles ", "article "))
        variants.extend(more_vars)
        # De-duplicate
        seen = set()
        dedup: List[str] = []
        for v in variants:
            if v not in seen:
                seen.add(v)
                dedup.append(v)
        return dedup

    def _is_full_alinea_target(self, target_text: str) -> bool:
        if not target_text:
            return False
        t = target_text.strip().lower().replace("’", "'")
        import re as _re
        m = _re.match(r"^(?:le|la)\s+[a-zéèêîôûàç]+\s+alinéa\s*$", t)
        return bool(m)

    def _build_user_prompt(self, original_text: str, operation: AmendmentOperation) -> str:
        """Build the user prompt for the specific operation."""
        return OPERATION_APPLIER_USER_PROMPT_TEMPLATE.format(
            original_text=original_text,
            operation_type=operation.operation_type.value,
            target_text=operation.target_text or "N/A",
            replacement_text=operation.replacement_text or "N/A",
            position_hint=operation.position_hint
        )

    def _validate_operation_input(self, original_text: str, operation: AmendmentOperation) -> "OperationApplicationResult":
        """
        Simple validation to catch common failure patterns before calling LLM.
        
        Returns:
            OperationApplicationResult with success=True if validation passes,
            or success=False with error_message if validation fails.
        """
        # For REPLACE operations, check if target text exists (with fuzzy matching)
        if operation.operation_type == OperationType.REPLACE and operation.target_text:
            if not self._fuzzy_text_exists(original_text, operation.target_text):
                similar_matches = self._find_similar_text(original_text, operation.target_text)
                error_msg = f"Target text not found: '{operation.target_text[:100]}...'"
                if similar_matches:
                    error_msg += f" Similar matches found: {similar_matches[:3]}"
                
                return OperationApplicationResult(
                    success=False, 
                    modified_text=original_text,
                    applied_fragment="",
                    error_message=error_msg
                )
        
        # For operations with position hints, validate context alignment
        if operation.position_hint and self._indicates_missing_section(original_text, operation.position_hint):
            return OperationApplicationResult(
                success=False,
                modified_text=original_text, 
                applied_fragment="",
                error_message=f"Context misalignment: position '{operation.position_hint}' refers to non-existent section in text"
            )
        
        return OperationApplicationResult(success=True, modified_text="", applied_fragment="")

    def _fuzzy_text_exists(self, text: str, target: str) -> bool:
        """Check if target text exists with fuzzy matching for formatting differences."""
        # Direct match
        if target in text:
            return True
        
        # Try with different quote styles
        variations = [
            target.replace('"', '« ').replace('"', ' »'),
            target.replace('« ', '"').replace(' »', '"'),
            target.replace('  ', ' '),  # double space to single
            target.replace(' ', ''),    # remove all spaces
        ]
        
        return any(var in text for var in variations if var != target)

    def _find_similar_text(self, text: str, target: str, max_matches: int = 3) -> list:
        """Find similar text matches in the original text."""
        # Simple similarity: look for partial matches with common words
        target_words = target.lower().split()
        if len(target_words) < 2:
            return []
        
        # Find text segments that contain multiple target words
        text_lower = text.lower()
        matches = []
        
        for i in range(len(text) - len(target) + 1):
            segment = text_lower[i:i + len(target) + 50]  # slightly longer segment
            word_matches = sum(1 for word in target_words if word in segment)
            
            if word_matches >= len(target_words) // 2:  # at least half the words match
                matches.append(text[i:i + min(100, len(target) + 30)])
                if len(matches) >= max_matches:
                    break
        
        return matches

    def _indicates_missing_section(self, text: str, position_hint: str) -> bool:
        """Check if position hint refers to a section that doesn't exist."""
        # Look for section references that don't exist in text
        section_patterns = [
            r'Le ([IVX]+)', r'au ([IVX]+)', r'du ([IVX]+)',  # Roman numerals
            r'Le (\d+°)', r'au (\d+°)', r'du (\d+°)',        # Numbered points
        ]
        
        import re
        for pattern in section_patterns:
            matches = re.findall(pattern, position_hint)
            for match in matches:
                # Check if this section exists in the text
                section_patterns_in_text = [
                    f'{match}.-',  # Roman numeral with dash
                    f'{match} ',   # Roman numeral with space
                    f'{match}°',   # Numbered point
                ]
                
                if not any(sp in text for sp in section_patterns_in_text):
                    return True
        
        return False

    def _parse_response(
        self, 
        result_data: dict, 
        operation: AmendmentOperation
    ) -> OperationApplicationResult:
        """Parse the LLM response into an OperationApplicationResult."""
        try:
            # Validate required fields
            if "success" not in result_data:
                raise ValueError("Response missing required 'success' field")
            if "modified_text" not in result_data:
                raise ValueError("Response missing required 'modified_text' field")
            
            success = result_data["success"]
            modified_text = result_data["modified_text"]
            applied_fragment = result_data.get("applied_fragment", "")
            error_message = result_data.get("error_message")
            confidence = float(result_data.get("confidence", 0.5))
            
            # Validate confidence range
            if not (0 <= confidence <= 1):
                logger.warning("Invalid confidence score: %f, clamping to [0,1]", confidence)
                confidence = max(0, min(1, confidence))
            
            return OperationApplicationResult(
                success=success,
                modified_text=modified_text,
                applied_fragment=applied_fragment,
                error_message=error_message,
                confidence=confidence
            )
            
        except Exception as e:
            logger.error("Failed to parse operation response: %s", e)
            raise ValueError(f"Invalid operation response format: {e}")

    def _serialize_result(self, result: OperationApplicationResult) -> dict:
        """Serialize result for caching."""
        return {
            "success": result.success,
            "modified_text": result.modified_text,
            "applied_fragment": result.applied_fragment,
            "error_message": result.error_message,
            "confidence": result.confidence,
            "processing_time_ms": result.processing_time_ms
        }

    def _deserialize_result(self, cached_data: dict) -> OperationApplicationResult:
        """Deserialize cached result."""
        return OperationApplicationResult(
            success=cached_data["success"],
            modified_text=cached_data["modified_text"],
            applied_fragment=cached_data["applied_fragment"],
            error_message=cached_data.get("error_message"),
            confidence=cached_data.get("confidence", 0.5),
            processing_time_ms=cached_data.get("processing_time_ms", 0)
        )

    def clear_cache(self) -> int:
        """Clear the operation applier cache."""
        return self.cache.clear_by_prefix("operation_applier")

    def get_cache_stats(self) -> dict:
        """Get cache statistics."""
        return self.cache.get_stats_by_prefix("operation_applier") 