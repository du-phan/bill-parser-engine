"""
Target article identification component.

This component analyzes chunks of legislative text and identifies the primary legal article,
section, or code provision that is the target of modification, insertion, or abrogation.

This is the single source of truth for target article identification. It uses LLM-based analysis
with proper inheritance logic to handle complex French legislative patterns.
"""

import json
import os
import re
from typing import List, Optional, Tuple

from mistralai import Mistral

from bill_parser_engine.core.reference_resolver.models import BillChunk, TargetArticle, TargetOperationType
from bill_parser_engine.core.reference_resolver.config import MISTRAL_MODEL
from bill_parser_engine.core.reference_resolver.cache_manager import SimpleCache, get_cache
from bill_parser_engine.core.reference_resolver.rate_limiter import rate_limiter, call_mistral_with_messages
from bill_parser_engine.core.reference_resolver.prompts import TARGET_ARTICLE_IDENTIFIER_SYSTEM_PROMPT


class TargetArticleIdentifier:
    """
    Identifies the primary legal article or section that is the target of modification,
    insertion, or abrogation in each chunk of a legislative bill.
    
    This component analyzes each chunk to determine:
    1. The main legal article/section being affected (target article)
    2. The operation type (INSERT, MODIFY, ABROGATE, RENUMBER, or OTHER)
    3. The relevant code and article identifiers
    
    Uses Mistral Chat API in JSON Mode for structured output with comprehensive
    inheritance logic to handle French legislative hierarchy patterns.
    """
    
    def __init__(self, api_key: Optional[str] = None, cache: Optional[SimpleCache] = None, use_cache: bool = True):
        """
        Initialize the identifier with Mistral client.

        Args:
            api_key: Mistral API key (defaults to MISTRAL_API_KEY environment variable)
            cache: Cache instance for storing intermediate results (uses global if None)
            use_cache: Whether to use caching (useful to disable when iterating on prompts)
        """
        self.client = Mistral(api_key=api_key or os.getenv("MISTRAL_API_KEY"))
        self.cache = cache or get_cache()
        self.use_cache = use_cache

    def identify(self, chunk: BillChunk) -> TargetArticle:
        """
        Identify target article using LLM-based analysis with inheritance logic.

        Args:
            chunk: The BillChunk to analyze

        Returns:
            TargetArticle object with identified information
        """
        # Fast-path: suppressed enumeration/no-op lines (e.g., "3° (Supprimé)", "a) (Supprimé)", "3. Est supprimé.")
        if self._is_suppressed_enumeration(chunk.text):
            print(f"✓ Detected suppressed enumeration for chunk {chunk.chunk_id}")
            return TargetArticle(
                operation_type=TargetOperationType.OTHER,
                code=None,
                article=None,
                confidence=1.0,
                reason="suppressed_enumeration",
            )

        # Check if chunk contains only versioning metadata without legal operations
        if self._is_pure_versioning_metadata(chunk.text):
            print(f"✓ Detected pure versioning metadata for chunk {chunk.chunk_id}: {chunk.text}")
            return TargetArticle(
                operation_type=TargetOperationType.OTHER,
                code=None,
                article=None,
                confidence=1.0,
                reason="versioning_metadata",
            )

        # Try to get from cache first (if enabled)
        if self.use_cache:
            cache_key_data = {
                'text': chunk.text,
                'article_introductory_phrase': chunk.article_introductory_phrase,
                'major_subdivision_introductory_phrase': chunk.major_subdivision_introductory_phrase,
                'hierarchy_path': chunk.hierarchy_path,
                'inherited_target': self._serialize_target_for_cache(chunk.inherited_target_article)
            }
            
            cached_result = self.cache.get("target_identifier_unified", cache_key_data)
            if cached_result is not None:
                print(f"✓ Using cached result for TargetArticleIdentifier")
                return cached_result
        
        print(f"→ Processing chunk with unified LLM-based target identification: {chunk.chunk_id}")

        # Deterministic guard: prefer inherited target from introductory phrase when present.
        # Rationale: parent intro like "L'article L. 250-3 est ainsi modifié :" is authoritative.
        # Special case: for patterns like "Après l'article X, il est inséré un article Y", extract Y from
        # the chunk text and treat Y as the INSERT target.
        deterministic = self._deterministic_target_from_intro(chunk)
        if deterministic is not None:
            # Cache and return deterministic decision
            if self.use_cache:
                self.cache.set("target_identifier_unified", cache_key_data, deterministic)
                print("✓ Cached deterministic target identification result")
            return deterministic

        # Build prompts (with optional hints) and run with schema validation + retries
        base_user_prompt = self._create_user_prompt(chunk)

        # Retry strategy: up to 3 attempts with stricter instructions and abbreviated inputs
        max_attempts = 3
        last_exception: Optional[Exception] = None
        for attempt in range(1, max_attempts + 1):
            try:
                system_prompt = TARGET_ARTICLE_IDENTIFIER_SYSTEM_PROMPT
                user_prompt = base_user_prompt

                # Attempt-specific adjustments
                if attempt == 2:
                    # Stricter schema instructions for retry 1
                    system_prompt = self._append_stricter_instructions(system_prompt)
                elif attempt == 3:
                    # Abbreviated user prompt for retry 2 (last)
                    user_prompt = self._create_abbreviated_user_prompt(chunk)
                    system_prompt = self._append_stricter_instructions(system_prompt)

                response = call_mistral_with_messages(
                    client=self.client,
                    rate_limiter=rate_limiter,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    component_name="TargetArticleIdentifier",
                    temperature=0.0,
                    response_format={"type": "json_object"},
                )

                content = json.loads(response.choices[0].message.content)

                # Validate schema and fields
                valid, validation_error = self._validate_llm_response(content)
                if not valid:
                    raise ValueError(f"Invalid LLM response schema: {validation_error}")

                target_article = self._create_target_article(content)

                # Post-process OTHER to ensure no stray fields
                if target_article.operation_type == TargetOperationType.OTHER:
                    target_article.code = None
                    target_article.article = None

                # Cache the successful result (if enabled)
                if self.use_cache:
                    self.cache.set("target_identifier_unified", cache_key_data, target_article)
                    print(f"✓ Cached result for future use")

                return target_article

            except Exception as e:
                last_exception = e
                # Continue to next attempt if any left
                print(f"Attempt {attempt}/{max_attempts} failed for chunk {chunk.chunk_id}: {e}")
                continue

        # If all attempts failed, surface a controlled failure as OTHER
        if last_exception is not None:
            print(f"All attempts failed for chunk {chunk.chunk_id}: {last_exception}")
        return TargetArticle(
            operation_type=TargetOperationType.OTHER,
            code=None,
            article=None,
            confidence=0.0,
        )

    def _deterministic_target_from_intro(self, chunk: BillChunk) -> Optional[TargetArticle]:
        """
        Derive a deterministic target article from the inherited introductory phrase when available.

        Logic:
        - If `chunk.inherited_target_article` exists with operation MODIFY or ABROGATE → return it directly.
        - If it's INSERT (e.g., "Après l'article X, il est inséré …"), attempt to extract the NEW article
          identifier from the chunk text ("il est inséré un article L. Y …"). If found, return INSERT target Y
          with the inherited code. If not found, return None (fallback to LLM).
        - Otherwise, try to parse explicit targets from the chunk's introductory phrases
          (article/major subdivision/numbered point) before falling back to LLM.
        """
        inherited = chunk.inherited_target_article
        if inherited is None:
            # Try to deterministically parse the article/code from available intro phrases
            try:
                import re as _re
                # Prefer the most specific intro first (numbered point), then article-level, then major subdivision
                raw_candidates = [
                    chunk.numbered_point_introductory_phrase or "",
                    chunk.article_introductory_phrase or "",
                    chunk.major_subdivision_introductory_phrase or "",
                ]
                # Normalize curly apostrophes and strange spaces for robust regex
                candidates = [
                    c.replace("’", "'").replace("\u00A0", " ") for c in raw_candidates
                ]

                def _extract_article(s: str) -> Optional[str]:
                    # Matches: L'article L. 254-1 est ainsi modifié / 'article L. 254-1 est ainsi modifié
                    m = _re.search(r"(?i)(?:l\s*'\s*article|l'article|'article)\s+([LRD]\.\s*[\d][\d\-]*)\s+est\s+ainsi\s+modifié", s)
                    if m:
                        return _re.sub(r"\s+", " ", m.group(1).strip())
                    # Generic: À l'article L. 254-1, ...
                    m = _re.search(r"(?i)\bà\s+l\s*'\s*article\s+([LRD]\.\s*[\d][\d\-]*)\b", s)
                    if m:
                        return _re.sub(r"\s+", " ", m.group(1).strip())
                    # Fallback: any explicit legal article marker like "L. 254-1" present
                    m = _re.search(r"(?i)\b([LRD]\.\s*\d[\d\-]*)\b", s)
                    if m:
                        return _re.sub(r"\s+", " ", m.group(1).strip())
                    return None

                def _extract_code(s: str) -> Optional[str]:
                    # Matches: (Le|La|L’) code X est ainsi modifié
                    m = _re.search(r"(?i)\b((?:le|la|l’|l')?\s*code\s+[^\n:]+?)\s+est\s+ainsi\s+modifié", s)
                    if m:
                        code = m.group(1)
                        # Normalize spaces and strip articles like leading 'le '
                        code = _re.sub(r"^(?i)(le|la|l’|l')\s+", "", code).strip()
                        # Ensure it starts with 'code '
                        if not code.lower().startswith("code "):
                            code = f"code {code}"
                        return code
                    # Fallback: look for a well-known code mention even without the leading article
                    m = _re.search(r"(?i)\b(code\s+rural\s+et\s+de\s+la\s+p[êe]che\s+maritime)\b", s)
                    if m:
                        return m.group(1).lower()
                    return None

                parsed_article: Optional[str] = None
                parsed_code: Optional[str] = None
                for intro in candidates:
                    if intro and not parsed_article:
                        parsed_article = _extract_article(intro)
                    if intro and not parsed_code:
                        parsed_code = _extract_code(intro)
                # If code still unknown, try to infer from intro phrases by detecting known code mentions
                if not parsed_code:
                    for intro in candidates:
                        if not intro:
                            continue
                        if _re.search(r"(?i)code\s+rural\s+et\s+de\s+la\s+p[êe]che\s+maritime", intro):
                            parsed_code = "code rural et de la pêche maritime"
                            break
                if parsed_article:
                    ta = TargetArticle(
                        operation_type=TargetOperationType.MODIFY,
                        code=parsed_code,
                        article=parsed_article,
                        confidence=1.0,
                    )
                    print(f"✓ Deterministic target from intro phrases: {ta.code} {ta.article}")
                    return ta
            except Exception:
                # Ignore and fall back to other deterministic logic / LLM path
                pass
            return None

        try:
            # Normalize text for robust matching
            text = (chunk.text or "").replace("’", "'")

            # Directly trust MODIFY/ABROGATE from inherited intro (authoritative parent statement)
            if inherited.operation_type in (TargetOperationType.MODIFY, TargetOperationType.ABROGATE):
                return TargetArticle(
                    operation_type=inherited.operation_type,
                    code=inherited.code,
                    article=inherited.article,
                    confidence=1.0,
                )

            # If inherited suggests an INSERT-after pattern, try to extract the new article id from the text
            if inherited.operation_type == TargetOperationType.INSERT:
                # Look for "il est inséré un article L. 123-4(-x)?" in the chunk body
                m = re.search(r"(?i)il\s+est\s+inséré\s+un\s+article\s+([LRD]\.?\s*[\d][\d\-]*)", text)
                if m:
                    new_article = re.sub(r"\s+", " ", m.group(1).strip())
                    return TargetArticle(
                        operation_type=TargetOperationType.INSERT,
                        code=inherited.code,
                        article=new_article,
                        confidence=1.0,
                    )
                # Could be an intra-article insert (alinéa/word). In that case, the real target is the existing
                # article referred by the parent intro (commonly expressed as MODIFY scope). Prefer MODIFY semantics
                # so the retriever fetches the article text deterministically.
                return TargetArticle(
                    operation_type=TargetOperationType.MODIFY,
                    code=inherited.code,
                    article=inherited.article,
                    confidence=1.0,
                )
        except Exception:
            # Fall back silently to LLM path on any parsing error
            return None

        return None

    def _create_user_prompt(self, chunk: BillChunk) -> str:
        """
        Create a comprehensive user prompt with all context information.

        Args:
            chunk: The BillChunk to analyze

        Returns:
            Formatted user prompt string with comprehensive context
        """
        # Build context sections
        context_parts = []
        
        if chunk.article_introductory_phrase:
            context_parts.append(f"CONTEXTE D'ARTICLE : {chunk.article_introductory_phrase}")
        
        if chunk.major_subdivision_introductory_phrase:
            context_parts.append(f"CONTEXTE DE SUBDIVISION : {chunk.major_subdivision_introductory_phrase}")
        
        # Include inherited target if available
        inheritance_info = "Aucun"
        if chunk.inherited_target_article:
            inherited = chunk.inherited_target_article
            inheritance_info = f"Article: {inherited.article}, Code: {inherited.code}, Opération: {inherited.operation_type.value if inherited.operation_type else 'None'}"
        
        # Optional hint for Arabic-numbered headings like "1." that can degrade identification
        hint_line = ""
        if self._has_arabic_point_heading(chunk.text):
            parent_article = next((p for p in chunk.hierarchy_path if p.startswith("Article ")), None)
            if parent_article:
                hint_line = f"INDICE: ce point appartient à {parent_article}."

        # Combine context parts
        context_text = " | ".join(context_parts) if context_parts else "Aucun"

        return f"""
FRAGMENT À ANALYSER : {chunk.text}

{hint_line}

CONTEXTE DISPONIBLE :
{context_text}

HIÉRARCHIE COMPLÈTE : {' > '.join(chunk.hierarchy_path)}

HÉRITAGE DISPONIBLE : {inheritance_info}

Analysez ce fragment et identifiez l'article cible en suivant la logique d'héritage expliquée dans les instructions système.
"""

    def _create_target_article(self, content: dict) -> TargetArticle:
        """
        Create TargetArticle object from parsed JSON content.

        Args:
            content: Parsed JSON response from Mistral

        Returns:
            TargetArticle object
        """
        # Validate and convert operation_type
        operation_type_str = content.get("operation_type", "OTHER").upper()
        try:
            operation_type = TargetOperationType[operation_type_str]
        except KeyError:
            operation_type = TargetOperationType.OTHER

        return TargetArticle(
            operation_type=operation_type,
            code=content.get("code"),
            article=content.get("article"),
            confidence=content.get("confidence")
        )

    def _is_pure_versioning_metadata(self, text: str) -> bool:
        """
        Check if the text contains only versioning metadata without actual legal operations.
        
        Args:
            text: The chunk text to analyze
            
        Returns:
            True if the text contains only versioning metadata, False otherwise
        """
        if not text or not text.strip():
            return True
            
        # Strip common versioning prefixes
        stripped_text = text.strip()
        
        # Remove numbered/lettered prefixes like "1°", "2°", "a)", "b)", etc.
        versioning_prefix_patterns = [
            r'^\d+°\s*à\s*\d+°\s*',  # "1° à 3°", etc. (check ranges first)
            r'^\d+°\s*',  # "1°", "2°", etc.
            r'^[a-z]\)\s*',  # "a)", "b)", etc.
            r'^[A-Z]\)\s*',  # "A)", "B)", etc.
            r'^[IVX]+\.\s*[–-]?\s*',  # Roman numerals like "I.", "II. –", etc.
            r'^[IVX]+\s+et\s+[IVX]+\.\s*[–-]?\s*',  # "I et II. –", etc.
            r'^[a-z]+,\s*[a-z]+\s+et\s+[a-z]+\)\s*',  # "aa, a et b)", etc.
        ]
        
        for pattern in versioning_prefix_patterns:
            stripped_text = re.sub(pattern, '', stripped_text).strip()
        
        # After removing prefixes, check if only versioning metadata remains
        versioning_metadata_patterns = [
            r'^\(nouveau\)$',
            r'^\(Supprimé\)$',
            r'^\(nouveau\)\(Supprimé\)$',
            r'^\(Supprimés\)$',
            r'^\(nouveau\)\(Supprimés\)$',
            r'^$',  # Empty after stripping prefixes
        ]
        
        for pattern in versioning_metadata_patterns:
            if re.match(pattern, stripped_text, re.IGNORECASE):
                return True
                
        return False

    def _is_suppressed_enumeration(self, text: str) -> bool:
        """
        Detect bill-side suppressed enumerations that indicate no-op for target identification.
        Matches forms like:
        - "3° (Supprimé)", "3° (Supprimée)", "3° (Supprimés)", "3° (Supprimées)", "3° (Sans objet)"
        - "3. (Supprimé)"
        - "a) (Supprimé)"
        - "3° Est supprimé.", "a) Est supprimé." (and plural/feminine variants)
        """
        if not text:
            return False
        stripped = text.strip()
        # Parenthesized forms
        pattern_paren = r"^\s*(?:\d+°|\d+\.|[a-z]\))\s*\((?:Supprimé|Supprimée|Supprimés|Supprimées|Sans\s+objet)\)\s*\.?\s*$"
        # Verbal forms (est/sont supprimé(e)(s)) possibly followed by semicolon/period
        pattern_verbal = r"^\s*(?:\d+°|\d+\.|[a-z]\))\s*(?:est\s+supprimé(?:e|s|es)?|sont\s+supprimé(?:e|s|es)?)\s*\.?\s*$"
        return bool(re.match(pattern_paren, stripped, re.IGNORECASE)) or bool(re.match(pattern_verbal, stripped, re.IGNORECASE))

    # --- Retry helpers and validators ---

    def _append_stricter_instructions(self, system_prompt: str) -> str:
        """Append stricter schema instructions used for retries."""
        strict = (
            "\n\nINSTRUCTIONS STRICTES (RETRY):\n"
            "- Ne retournez JAMAIS des chapitres/titres/livres/sections dans 'article'.\n"
            "- Pour operation_type != OTHER, 'code' ET 'article' sont OBLIGATOIRES.\n"
            "- Si seule une numérotation d'item (ex: '3.' ou '3°') est détectée sans opération juridique, retournez OTHER.\n"
            "- Utilisez la HIÉRARCHIE COMPLÈTE pour résoudre l'article parent quand nécessaire.\n"
        )
        return f"{system_prompt}{strict}"

    def _create_abbreviated_user_prompt(self, chunk: BillChunk) -> str:
        """Create a shorter user prompt variant for retry attempts."""
        text = (chunk.text or "").strip()
        snippet = text[:200] + ("…" if len(text) > 200 else "")
        parent_article = next((p for p in chunk.hierarchy_path if p.startswith("Article ")), None)
        hint_line = ""
        if self._has_arabic_point_heading(chunk.text) and parent_article:
            hint_line = f"INDICE: ce point appartient à {parent_article}.\n"
        return (
            f"FRAGMENT (abrégé) : {snippet}\n\n"
            f"HIÉRARCHIE COMPLÈTE : {' > '.join(chunk.hierarchy_path)}\n\n"
            f"{hint_line}"
            "Retournez STRICTEMENT un JSON valide conforme au schéma."
        )

    def _validate_llm_response(self, content: dict) -> Tuple[bool, str]:
        """Validate LLM JSON response for required schema and fields."""
        if not isinstance(content, dict):
            return False, "Response is not a JSON object"
        op = str(content.get("operation_type", "OTHER")).upper()
        allowed_ops = {t.name for t in TargetOperationType}
        if op not in allowed_ops:
            return False, f"operation_type '{op}' not in {allowed_ops}"

        # Confidence should be within [0,1] if provided
        conf = content.get("confidence")
        if conf is not None:
            try:
                conf_f = float(conf)
            except Exception:
                return False, "confidence must be a float"
            if not (0.0 <= conf_f <= 1.0):
                return False, "confidence must be between 0 and 1"

        # For non-OTHER, require code and article
        if op != "OTHER":
            code = content.get("code")
            article = content.get("article")
            if not code or not article:
                return False, "code and article required for non-OTHER operation"
            # Basic guard: reject structural nodes mistakenly placed in article
            if isinstance(article, str) and re.search(r"\b(titre|livre|chapitre|section)\b", article, re.IGNORECASE):
                return False, "article contains structural node (titre/livre/chapitre/section)"

        return True, ""

    def _has_arabic_point_heading(self, text: str) -> bool:
        """Detect headings of the form 'N.' at start of text."""
        if not text:
            return False
        return re.match(r"^\s*\d+\.\s*", text) is not None

    def _serialize_target_for_cache(self, target: Optional[TargetArticle]) -> Optional[dict]:
        """Serialize target article for cache key."""
        if target is None:
            return None
        return {
            'operation_type': target.operation_type.value if target.operation_type else None,
            'code': target.code,
            'article': target.article
        }

    def clear_cache(self) -> int:
        """
        Clear cached results for this component.
        
        Useful when iterating on prompts or when you want fresh results.
        
        Returns:
            Number of cache entries cleared
        """
        return self.cache.invalidate("target_identifier_unified")

 