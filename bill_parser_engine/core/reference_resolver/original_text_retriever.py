"""
OriginalTextRetriever: Fetch current legal text for target articles using local sources.

This component retrieves the current legal text of target articles identified by 
TargetArticleIdentifier. This is critical because reference objects may only be visible 
in the original law, not in the amendment text.

Features:
- French Codes: Local files under data/fr_code_text/ (no external APIs)
- EU Legal Texts: Local files under data/eu_law_text/ with optional LLM subsection extraction
- Hierarchical fallback: L. 118-1-2 → try L. 118-1 and extract subsection 2 (deterministic first)
- Caching: Uses standardized cache_manager.py for efficiency
- INSERT handling: Return empty string for INSERT operations
"""

import os
import re
import unicodedata
import logging
import json
from typing import Tuple, Dict, Optional, List
from pathlib import Path

try:
    from mistralai import Mistral
except ImportError:
    Mistral = None

from .models import TargetArticle, TargetOperationType
from .cache_manager import SimpleCache, get_cache
from .rate_limiter import RateLimiter, get_rate_limiter, call_mistral_with_messages
from .prompts import (
    EU_LEGAL_TEXT_SUBSECTION_EXTRACTION_SYSTEM_PROMPT,
    FRENCH_LEGAL_TEXT_SUBSECTION_EXTRACTION_SYSTEM_PROMPT
)

logger = logging.getLogger(__name__)


class OriginalTextRetriever:
    """
    Fetches the current/existing text of target articles from local sources:
    1. French legal codes from data/fr_code_text/
    2. EU legal texts from data/eu_law_text/
    
    This is critical because reference objects may only be visible in the original law,
    not in the amendment text. Without this context, the ReferenceObjectLinker cannot
    properly identify what concepts/objects the references in deleted text refer to.
    
    Features hierarchical fallback and LLM-based extraction for both sources.
    """
    
    def __init__(self, cache: Optional[SimpleCache] = None, use_cache: bool = True, 
                 rate_limiter: Optional[RateLimiter] = None):
        """
        Initialize the retriever with caching configuration.
        
        Args:
            cache: Cache instance for storing intermediate results (uses global if None)
            use_cache: Whether to use caching (useful to disable when iterating)
            rate_limiter: Rate limiter for LLM calls (uses global if None)
        """
        self.cache = cache or get_cache()
        self.use_cache = use_cache
        self.rate_limiter = rate_limiter or get_rate_limiter()
        
        # Initialize Mistral client for LLM extraction
        api_key = os.getenv('MISTRAL_API_KEY')
        if api_key and Mistral:
            self.mistral_client = Mistral(api_key=api_key)
        else:
            self.mistral_client = None
            if not api_key:
                logger.warning("MISTRAL_API_KEY not found - LLM extraction will be disabled")
            if not Mistral:
                logger.warning("mistralai package not available - LLM extraction will be disabled")
        
        # EU legal text directory mapping
        self.eu_text_base_path = Path("data/eu_law_text")
        self.eu_regulation_patterns = {
            r"règlement\s*\(ce\)\s*n[°o]\s*1107/2009": "Règlement CE No 1107_2009",
            # Add more regulations as needed
        }
        self.eu_directive_patterns = {
            r"directive\s*2009/128/ce": "Directive 2009_128_CE",
            r"directive\s*2010/75/ue": "Directive 2010_75_UE", 
            r"directive\s*2011/92/ue": "Directive 2011_92_UE",
            # Add more directives as needed
        }
    
    def fetch_article_text(self, code: str, article: str) -> Tuple[str, Dict]:
        """
        Fetch the full text of a target article with proper segmentation.
        
        Automatically detects source type (French code vs EU legal text) and uses
        appropriate retrieval method. Implements hierarchical fallback and LLM extraction.
        
        Args:
            code: The legal code/regulation name (e.g., "code rural", "règlement (CE) n° 1107/2009")
            article: The article identifier (e.g., "L. 254-1", "article 3", "11 de l'article 3")
            
        Returns:
            Tuple of (article_text, retrieval_metadata)
            - article_text: Full text with hierarchy or specific excerpt
            - retrieval_metadata: Contains retrieval status, source, and any error information
        """
        if not code or not article:
            return "", {"source": "none", "success": False, "error": "Missing code or article"}
        
        # Try to get from cache first (if enabled)
        if self.use_cache:
            cache_key_data = {
                'code': code,
                'article': article,
                'method': 'fetch_article_text'
            }
            
            cached_result = self.cache.get("original_text_retriever", cache_key_data)
            if cached_result is not None:
                logger.info(f"✓ Retrieved article {article} from cache")
                return cached_result, {"source": "cache", "success": True}
        
        logger.info(f"→ Fetching article {article} from {code}")

        # Structural guard: avoid calling external APIs for structural nodes
        if re.search(r"\b(titre|livre|chapitre|section)\b", article, re.IGNORECASE):
            return "", {"source": "none", "success": False, "error": "Structural node provided as article"}
        
        # Detect if this is an EU legal text reference
        if self._is_eu_legal_reference(code):
            result_text, metadata = self._fetch_eu_legal_text(code, article)
        else:
            # Local-only retrieval for French codes (no external APIs)
            result_text, metadata = self._fetch_french_code_text(code, article)
        
        # Cache successful results (if enabled)
        if self.use_cache and metadata.get("success", False):
            cache_key_data = {
                'code': code,
                'article': article,
                'method': 'fetch_article_text'
            }
            self.cache.set("original_text_retriever", cache_key_data, result_text)
            logger.info(f"✓ Cached article {article} for future use")
        
        # No write-through persistence - all legal text is stored offline in curated directories
        return result_text, metadata
    
    def fetch_article_for_target(self, target_article: TargetArticle) -> Tuple[str, Dict]:
        """
        Convenience method to fetch article text using a TargetArticle object.
        
        Args:
            target_article: TargetArticle object from TargetArticleIdentifier
            
        Returns:
            Tuple of (article_text, retrieval_metadata)
        """
        # Handle INSERT operations
        if target_article.operation_type == TargetOperationType.INSERT:
            # Distinguish between new-article insertion vs intra-article insertion.
            # If the (code, article) already exists in Legifrance, treat as intra-article insert and fetch it.
            # Otherwise, return empty for a truly new article.
            try:
                if target_article.code and target_article.article:
                    existing_text, meta = self.fetch_article_text(target_article.code, target_article.article)
                    if existing_text and existing_text.strip():
                        logger.info(
                            f"INSERT on existing article detected for {target_article.article} - returning current text for intra-article insertion"
                        )
                        meta = dict(meta or {})
                        meta.update({"source": "insert_existing_article", "success": True})
                        return existing_text, meta
            except Exception as e:
                # Non-fatal; fall back to empty new-article case
                logger.debug(f"INSERT existence probe failed: {e}")

            logger.info(f"INSERT operation for {target_article.article} - returning empty text (new article)")
            return "", {"source": "insert_operation", "success": True, "note": "Empty text for INSERT operation"}
        
        if not target_article.code or not target_article.article:
            return "", {"source": "none", "success": False, "error": "Missing code or article in TargetArticle"}
        
        return self.fetch_article_text(target_article.code, target_article.article)
    
    def _is_eu_legal_reference(self, code: str) -> bool:
        """
        Check if a code reference refers to EU legal text.
        
        Args:
            code: The legal code name
            
        Returns:
            True if this is an EU regulation or directive
        """
        code_lower = code.lower().strip()
        
        # Check regulation patterns
        for pattern in self.eu_regulation_patterns:
            if re.search(pattern, code_lower, re.IGNORECASE):
                return True
        
        # Check directive patterns  
        for pattern in self.eu_directive_patterns:
            if re.search(pattern, code_lower, re.IGNORECASE):
                return True
                
        return False
    
    def _get_eu_directory_name(self, code: str) -> Optional[str]:
        """
        Get the directory name for an EU legal text reference.
        
        Args:
            code: The legal code name
            
        Returns:
            Directory name or None if not found
        """
        code_lower = code.lower().strip()
        
        # Check regulation patterns
        for pattern, directory in self.eu_regulation_patterns.items():
            if re.search(pattern, code_lower, re.IGNORECASE):
                return directory
        
        # Check directive patterns
        for pattern, directory in self.eu_directive_patterns.items():
            if re.search(pattern, code_lower, re.IGNORECASE):
                return directory
                
        return None
    
    def _fetch_eu_legal_text(self, code: str, article: str) -> Tuple[str, Dict]:
        """
        Fetch EU legal text from local files.
        
        Args:
            code: EU regulation/directive reference
            article: Article reference (e.g., "article 3", "11 de l'article 3")
            
        Returns:
            Tuple of (article_text, retrieval_metadata)
        """
        directory_name = self._get_eu_directory_name(code)
        if not directory_name:
            return "", {"source": "eu_legal_text", "success": False, "error": f"Unknown EU legal text: {code}"}
        
        eu_dir_path = self.eu_text_base_path / directory_name
        if not eu_dir_path.exists():
            return "", {"source": "eu_legal_text", "success": False, "error": f"EU directory not found: {eu_dir_path}"}
        
        # Parse article reference
        article_info = self._parse_eu_article_reference(article)
        if not article_info:
            return "", {"source": "eu_legal_text", "success": False, "error": f"Cannot parse article reference: {article}"}
        
        # Find and read the article file
        article_content, is_specific_part_file = self._read_eu_article_file(eu_dir_path, article_info)
        if not article_content:
            return "", {"source": "eu_legal_text", "success": False, "error": f"Article file not found: {article_info}"}
        
        # Handle specific part extraction
        if article_info.get("specific_part"):
            if is_specific_part_file:
                # We already read the specific part file directly
                logger.info(f"✓ Retrieved EU article {article} from direct specific part file")
                return article_content, {
                    "source": "eu_legal_text", 
                    "success": True,
                    "directory": directory_name,
                    "article_info": article_info,
                    "extraction_method": "direct_file"
                }
            else:
                # Try LLM extraction from full article content
                extracted_content = self._extract_eu_article_part(article_content, article_info, article)
                if extracted_content:
                    logger.info(f"✓ Retrieved EU article {article} with LLM-based specific part extraction")
                    return extracted_content, {
                        "source": "eu_legal_text", 
                        "success": True,
                        "directory": directory_name,
                        "article_info": article_info,
                        "extraction_method": "llm"
                    }
                else:
                    logger.warning(f"Failed to extract specific part from EU article {article}")
                    # Fall back to full article content
        
        # Return full article content (either no specific part needed or LLM extraction failed)
        logger.info(f"✓ Retrieved EU article {article} (full content)")
        return article_content, {
            "source": "eu_legal_text", 
            "success": True,
            "directory": directory_name,
            "article_info": article_info,
            "extraction_method": "full"
        }
    
    def _parse_eu_article_reference(self, article: str) -> Optional[Dict]:
        """
        Parse EU article reference into components.
        
        Args:
            article: Article reference (e.g., "article 3", "11 de l'article 3", "article 47")
            
        Returns:
            Dict with article info or None if cannot parse
        """
        article_lower = article.lower().strip()
        
        # Pattern: "11 de l'article 3" -> article 3, point 11
        match = re.search(r"(\d+)\s+de\s+l['\"]?article\s+(\d+)", article_lower)
        if match:
            point_num = match.group(1)
            article_num = match.group(2)
            return {
                "article_number": article_num,
                "specific_part": point_num,
                "part_type": "point"
            }
        
        # Pattern: "article 3" -> article 3, full content
        match = re.search(r"article\s+(\d+)", article_lower)
        if match:
            article_num = match.group(1)
            return {
                "article_number": article_num,
                "specific_part": None,
                "part_type": None
            }
        
        # Pattern: just number "3" -> article 3
        if article.strip().isdigit():
            return {
                "article_number": article.strip(),
                "specific_part": None,
                "part_type": None
            }
        
        return None
    
    def _read_eu_article_file(self, eu_dir_path: Path, article_info: Dict) -> Tuple[Optional[str], bool]:
        """
        Read the EU article file content.
        
        Args:
            eu_dir_path: Path to EU legal text directory
            article_info: Parsed article information
            
        Returns:
            Tuple of (article_content, is_specific_part_file)
            - article_content: The file content or None if not found
            - is_specific_part_file: True if we read a specific part file directly (no LLM extraction needed)
        """
        article_num = article_info["article_number"]
        specific_part = article_info.get("specific_part")
        part_type = article_info.get("part_type")
        
        # First, try to read specific part file directly if we have a specific part
        if specific_part and part_type:
            article_dir = eu_dir_path / f"Article_{article_num}"
            if article_dir.exists():
                # Try specific point file (e.g., Point_11.md)
                if part_type.lower() == "point":
                    point_file = article_dir / f"Point_{specific_part}.md"
                    if point_file.exists():
                        try:
                            logger.info(f"✓ Reading specific EU point file: {point_file}")
                            content = point_file.read_text(encoding='utf-8')
                            return content, True  # True = specific part file read directly
                        except Exception as e:
                            logger.warning(f"Error reading {point_file}: {e}")
        
        # Fallback: Try article directory with overview.md 
        article_dir = eu_dir_path / f"Article_{article_num}"
        if article_dir.exists():
            overview_file = article_dir / "overview.md"
            if overview_file.exists():
                try:
                    logger.info(f"✓ Reading EU overview file for LLM extraction: {overview_file}")
                    content = overview_file.read_text(encoding='utf-8')
                    return content, False  # False = full article, LLM extraction needed
                except Exception as e:
                    logger.warning(f"Error reading {overview_file}: {e}")
        
        # Try direct article file
        article_file = eu_dir_path / f"Article_{article_num}.md"
        if article_file.exists():
            try:
                content = article_file.read_text(encoding='utf-8')
                return content, False  # False = full article, LLM extraction needed
            except Exception as e:
                logger.warning(f"Error reading {article_file}: {e}")
        
        # Try alternative naming (lowercase)
        article_file = eu_dir_path / f"article_{article_num}.md"
        if article_file.exists():
            try:
                content = article_file.read_text(encoding='utf-8')
                return content, False  # False = full article, LLM extraction needed
            except Exception as e:
                logger.warning(f"Error reading {article_file}: {e}")
        
        return None, False
    
    def _extract_eu_article_part(self, article_content: str, article_info: Dict, original_ref: str) -> Optional[str]:
        """
        Use LLM to extract a specific part from EU article content.
        
        Args:
            article_content: Full article content
            article_info: Parsed article information
            original_ref: Original article reference
            
        Returns:
            Extracted content or None if extraction failed
        """
        if not self.mistral_client:
            logger.warning("Mistral client not available for EU article part extraction")
            return None
        
        specific_part = article_info["specific_part"]
        part_type = article_info["part_type"]
        
        system_prompt = EU_LEGAL_TEXT_SUBSECTION_EXTRACTION_SYSTEM_PROMPT

        user_message = f"""Extrayez la partie "{specific_part}" ({part_type}) de ce texte d'article juridique européen :

Référence originale recherchée : {original_ref}
Texte de l'article :
{article_content}

Trouvez et extrayez le contenu complet de la partie "{specific_part}"."""

        try:
            response = call_mistral_with_messages(
                client=self.mistral_client,
                rate_limiter=self.rate_limiter,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                component_name="OriginalTextRetriever-EU",
                temperature=0.0,
                response_format={"type": "json_object"}
            )
            result = json.loads(response.choices[0].message.content)
            
            if result.get("found", False) and result.get("content"):
                logger.info(f"✓ LLM extracted EU article part {specific_part}: {result.get('explanation', '')}")
                return result["content"]
            else:
                logger.warning(f"LLM could not find EU article part {specific_part}: {result.get('explanation', 'Unknown reason')}")
                return None
                
        except Exception as e:
            logger.error(f"LLM EU article part extraction failed: {e}")
            return None
    
    def _fetch_french_code_text(self, code: str, article: str) -> Tuple[str, Dict]:
        """
        Fetch French legal code text from local curated store only (no external APIs).
        
        Strategy:
        1) Try read-through store under data/fr_code_text/<slugified>/<article>.txt
        2) Try curated markdowns under data/fr_code_text/<Code Name>/ (article <ID>.md)
        3) If hierarchical (e.g., L. 118-1-2), read parent locally and deterministically carve subsection
        """
        logger.info(f"→ Fetching article {article} from {code} (local store)")
        cleaned_article = self._extract_base_article_identifier(article)

        # Read from curated markdown files in data/fr_code_text/<Code Name>/
        md_path = self._resolve_existing_local_file(code, cleaned_article)
        if md_path and md_path.exists():
            try:
                text = md_path.read_text(encoding="utf-8")
                # Strip markdown headers/front-matter
                lines = text.split('\n')
                content_start = 0
                for i, line in enumerate(lines):
                    if line.strip() and not line.startswith('#') and not line.startswith('---'):
                        content_start = i
                        break
                content = '\n'.join(lines[content_start:]).strip()
                return content, {"source": "local_fr", "success": True, "file": str(md_path)}
            except Exception as e:
                logger.warning(f"Error reading curated file {md_path}: {e}")

        # 3) Hierarchical carve from parent if applicable (e.g., L. 118-1-2)
        if self._should_try_hierarchical_fallback(cleaned_article):
            parent_article, subsection = self._parse_hierarchical_article(cleaned_article)
            logger.info(f"→ Trying local hierarchical fallback: {parent_article} → subsection {subsection}")

            parent_text = None  # Initialize to prevent UnboundLocalError
            # Try curated markdown for parent
            parent_md = self._resolve_existing_local_file(code, parent_article)
            if parent_md and parent_md.exists():
                try:
                    parent_raw = parent_md.read_text(encoding='utf-8')
                    parent_lines = parent_raw.split('\n')
                    start = 0
                    for i, line in enumerate(parent_lines):
                        if line.strip() and not line.startswith('#') and not line.startswith('---'):
                            start = i
                            break
                    parent_text = '\n'.join(parent_lines[start:]).strip()
                except Exception as e:
                    logger.warning(f"Error reading parent curated file {parent_md}: {e}")
                    parent_text = None

            if parent_text:
                subsection_content = self._deterministic_carve_from_parent(parent_text, subsection)
                if not subsection_content:
                    subsection_content = self._extract_subsection_with_llm(parent_text, subsection, cleaned_article)
                if subsection_content:
                    return subsection_content, {
                        "source": "local_fr_hierarchical",
                        "success": True,
                        "parent_article": parent_article,
                        "subsection": subsection,
                        "method": "deterministic_or_llm"
                    }

        # No local content found
        logger.error(f"Could not retrieve article {cleaned_article} from local store for code {code}")
        return "", {"source": "local_fr", "success": False, "error": "Local article not found"}

    def _should_try_hierarchical_fallback(self, article: str) -> bool:
        """
        Check if an article should have hierarchical fallback attempted.
        
        Args:
            article: Article identifier (e.g., "L. 118-1-2")
            
        Returns:
            True if hierarchical fallback should be attempted
        """
        # Only for articles with multiple hierarchy levels (e.g., L. 118-1-2, not L. 118-1)
        starts_with_l = article.startswith("L. ")
        has_multiple_hyphens = article.count("-") >= 2
        # Deterministic carve can run without LLM; so we do not require Mistral for fallback eligibility
        print(f"🔍 DEBUG: Hierarchical fallback check for '{article}': starts_with_L={starts_with_l}, hyphens={article.count('-')}>=2={has_multiple_hyphens}")
        return (starts_with_l and has_multiple_hyphens)
    
    def _parse_hierarchical_article(self, article: str) -> Tuple[str, str]:
        """
        Parse a hierarchical article identifier into parent and subsection.
        
        Args:
            article: Full article identifier (e.g., "L. 118-1-2")
            
        Returns:
            Tuple of (parent_article, subsection)
            Example: "L. 118-1-2" → ("L. 118-1", "2")
        """
        # Split by hyphens and take all but the last part for parent
        parts = article.split("-")
        if len(parts) >= 3:
            parent_article = "-".join(parts[:-1])  # e.g., "L. 118-1"
            subsection = parts[-1]  # e.g., "2"
            return parent_article, subsection
        
        # Fallback (shouldn't happen if _should_try_hierarchical_fallback is correct)
        return article, ""
    
    def _extract_subsection_with_llm(self, parent_text: str, subsection: str, original_article: str) -> Optional[str]:
        """
        Use LLM to extract a specific subsection from a parent article text.
        
        Args:
            parent_text: Full text of the parent article
            subsection: Subsection identifier to extract (e.g., "2")
            original_article: Original article being sought (for context)
            
        Returns:
            Extracted subsection text, or None if extraction failed
        """
        if not self.mistral_client:
            logger.warning("Mistral client not available for subsection extraction")
            return None
        
        system_prompt = FRENCH_LEGAL_TEXT_SUBSECTION_EXTRACTION_SYSTEM_PROMPT

        user_message = f"""Extrayez la sous-section "{subsection}" de ce texte d'article juridique :

Article original recherché : {original_article}
Texte de l'article parent :
{parent_text}

Trouvez et extrayez le contenu complet de la sous-section "{subsection}"."""

        try:
            response = call_mistral_with_messages(
                client=self.mistral_client,
                rate_limiter=self.rate_limiter,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                component_name="OriginalTextRetriever-French",
                temperature=0.0,
                response_format={"type": "json_object"}
            )
            result = json.loads(response.choices[0].message.content)
            
            if result.get("found", False) and result.get("content"):
                logger.info(f"✓ LLM extracted subsection {subsection}: {result.get('explanation', '')}")
                return result["content"]
            else:
                logger.warning(f"LLM could not find subsection {subsection}: {result.get('explanation', 'Unknown reason')}")
                return None
                
        except Exception as e:
            logger.error(f"LLM subsection extraction failed: {e}")
            return None
    
    # NOTE: pylegifrance support removed; local-only retrieval is used for French codes.

    def _extract_base_article_identifier(self, article: str) -> str:
        """
        Extract the base article identifier by removing subsection information.
        
        Args:
            article: Full article identifier (e.g., "L. 254-1 (au 3° du II)")
            
        Returns:
            Base article identifier (e.g., "L. 254-1")
        """
        # Remove subsection information in parentheses
        if '(' in article:
            base_article = article.split('(')[0].strip()
        else:
            base_article = article
            
        # Remove any trailing whitespace or punctuation
        base_article = base_article.strip()
        
        return base_article

    # --- Deterministic carve helpers ---

    def _deterministic_carve_from_parent(self, parent_text: str, subsection: str) -> Optional[str]:
        """Deterministically extract a numbered subsection from a parent article text.

        Strategy:
        - Anchor on lines starting with the subsection number using common legal list markers:
          '{n}°', '{n})', '{n}.'.
        - If not found, try roman section header for subsection '1' as 'I.' (and 'II.' for '2', etc.).
        - Capture until the next subsection anchor or the next roman section header, whichever comes first.
        - Works without LLM; returns None if patterns are not present.
        """
        if not subsection or not subsection.strip():
            return None

        n = re.escape(subsection.strip())
        # Roman headers (section boundaries)
        # Roman section header lines, normalized forms like "I. – ..." or "I. - ..." or just "I. ..."
        # Accept a mandatory dot after the roman numeral, optional dash (en-dash or hyphen) after the dot.
        roman_header = re.compile(
            r"(?m)^\s*[IVXLCDM]+(?:\s+(?:bis|ter|quater|quinquies|sexies|septies|octies|nonies|decies))?\s*\.\s*(?:[\-–]\s*)?"
        )
        # Subsection start patterns
        start_patterns = [
            re.compile(rf"(?m)^\s*{n}°\b"),
            re.compile(rf"(?m)^\s*{n}\)\b"),
            re.compile(rf"(?m)^\s*{n}\.(?=\s)"),
        ]
        # Try roman mapping for small n if numeric markers not found
        roman_map = {
            "1": "I",
            "2": "II",
            "3": "III",
            "4": "IV",
            "5": "V",
            "6": "VI",
            "7": "VII",
            "8": "VIII",
            "9": "IX",
            "10": "X",
        }
        # Next subsection patterns (generic next number)
        next_subsection = re.compile(r"(?m)^\s*\d+(?:°|\)|\.)\b")

        # Find start
        start_idx = None
        for pat in start_patterns:
            m = pat.search(parent_text)
            if m:
                start_idx = m.start()
                break
        if start_idx is None:
            # Try roman header as section start if mapping exists
            r = roman_map.get(subsection.strip())
            if r:
                m_rom_start = re.search(rf"(?m)^\s*{r}(?:\s+(?:bis|ter|quater|quinquies|sexies|septies|octies|nonies|decies))?\s*[.\-–]\s", parent_text)
                if m_rom_start:
                    start_idx = m_rom_start.start()
                else:
                    return None

        # Determine end: next subsection or next roman header
        end_candidates: List[int] = []
        m_next = next_subsection.search(parent_text, pos=start_idx + 1)
        if m_next:
            end_candidates.append(m_next.start())
        m_roman = roman_header.search(parent_text, pos=start_idx + 1)
        if m_roman:
            end_candidates.append(m_roman.start())

        end_idx = min(end_candidates) if end_candidates else len(parent_text)

        return parent_text[start_idx:end_idx].strip() or None

    

    # --- Obsolete cache methods removed - all legal text is now stored offline in curated directories ---

    def _map_to_curated_directory(self, code: str) -> Optional[str]:
        """
        Map a code name to one of the three curated directories.
        Only these directories should be used - no automatic directory creation.
        
        Args:
            code: Code name to map
            
        Returns:
            Curated directory name or None if no match
        """
        code_normalized = self._normalize_code_key(code)
        
        # Curated directory mappings (exact names as they exist)
        curated_mappings = {
            "code rural et de la peche maritime": "Code Rural et de la pêche maritime",
            "code de l'environnement": "Code De l'environnement", 
            "code de la sante publique": "Code De la Santé Publique"
        }
        
        # Direct mapping
        if code_normalized in curated_mappings:
            return curated_mappings[code_normalized]
        
        # Fuzzy matching for variations
        for normalized_key, directory_name in curated_mappings.items():
            # Handle variations like "code rural et de la pêche maritime est ainsi modifié"
            if normalized_key in code_normalized or code_normalized in normalized_key:
                logger.info(f"Mapped code '{code}' to curated directory '{directory_name}'")
                return directory_name
                
        logger.warning(f"No curated directory mapping found for code: '{code}'")
        return None

    def _slugify_code_name(self, code: str) -> str:
        s = self._normalize_code_key(code)
        s = re.sub(r"[^a-z0-9]+", "_", s)
        s = re.sub(r"_+", "_", s).strip("_")
        return s or "unknown_code"

    def _normalize_code_key(self, code: str) -> str:
        """Accent-insensitive, punctuation-normalized key for code mapping."""
        s = code.strip().lower()
        # Normalize unicode, strip diacritics
        s = unicodedata.normalize("NFKD", s)
        s = "".join(ch for ch in s if not unicodedata.combining(ch))
        # Normalize quotes/hyphens/spaces
        s = s.replace("\u2019", "'").replace("’", "'")
        s = re.sub(r"\s+", " ", s)
        return s

    def _try_local_store_read(self, code: str, article: str) -> Tuple[Optional[str], Dict]:
        # REMOVED: No more cache files - read directly from curated markdowns only
        # Read from existing curated markdowns under data/fr_code_text/<Code Name>/article <ID>.md
        try:
            resolved = self._resolve_existing_local_file(code, article)
            if resolved and resolved.exists():
                text = resolved.read_text(encoding="utf-8")
                # Strip any leading markdown headings
                lines = text.splitlines()
                content_start = 0
                for i, line in enumerate(lines):
                    if line.strip() and not line.startswith('#') and not line.startswith('---'):
                        content_start = i
                        break
                content = "\n".join(lines[content_start:]).strip() + ("\n" if text.endswith("\n") else "")
                return content, {"source": "local_fr_md", "success": True, "file": str(resolved)}
        except Exception as e:
            logger.warning(f"Heuristic local .md read failed: {e}")

        return None, {}

    def _resolve_existing_local_file(self, code: str, article: str) -> Optional[Path]:
        """Best-effort resolution of an existing curated file for a French code article.

        Looks under data/fr_code_text/<Code Dir>/ for files like:
          - "article <ID>.md" (preferred) or 
          - "<ID>.md" / .txt variants.

        Where <ID> is derived from the article string by removing spaces and dots, e.g.,
        "L. 254-1" → "L254-1".
        """
        base_dir = Path("data/fr_code_text")
        if not base_dir.exists():
            return None

        def _norm(s: str) -> str:
            # Robust normalization: lowercase, strip accents, collapse whitespace,
            # normalize special apostrophes and quotes, standardize dashes
            s = (s or "").strip().lower()
            s = s.replace("’", "'")
            s = unicodedata.normalize("NFKD", s)
            s = "".join(ch for ch in s if not unicodedata.combining(ch))
            s = re.sub(r"[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]", "-", s)
            s = re.sub(r"\s+", " ", s)
            return s

        target_dir: Optional[Path] = None
        norm_code = _norm(code)
        logger.info(f"Local FR resolver: norm_code='{norm_code}' raw_code='{code}' base_dir='{base_dir}'")
        for d in base_dir.iterdir():
            if d.is_dir():
                norm_d = _norm(d.name)
                logger.info(f"  Consider dir: '{d.name}' norm='{norm_d}' match={norm_d == norm_code}")
                if norm_d == norm_code:
                    target_dir = d
                    break
        # Fallback: try relaxed startswith/contains to handle minor wording differences
        if not target_dir:
            for d in base_dir.iterdir():
                if not d.is_dir():
                    continue
                norm_d = _norm(d.name)
                if norm_code in norm_d or norm_d in norm_code:
                    logger.warning("Local FR resolver: Using relaxed directory match '%s' for code '%s'", d.name, code)
                    target_dir = d
                    break
        if not target_dir:
            logger.warning("Local FR resolver: No matching directory for code")
            return None

        # Build candidate stems
        art_compact = article.replace(" ", "").replace(".", "")
        logger.info(f"  Article raw='{article}', compact='{art_compact}'")
        candidates = [
            f"article {art_compact}",
            art_compact,
            article.replace(" ", "_").replace("/", "-"),
        ]
        exts = [".md", ".txt"]
        for stem in candidates:
            for ext in exts:
                p = target_dir / f"{stem}{ext}"
                logger.info(f"    Try file: {p} exists={p.exists()}")
                if p.exists():
                    return p
        # Last resort: glob match for case-insensitive
        for ext in exts:
            matches = list(target_dir.glob(f"*{art_compact}{ext}"))
            logger.info(f"    Glob '*{art_compact}{ext}' -> {len(matches)} matches")
            if matches:
                return matches[0]
        return None

    # REMOVED: _persist_to_local_store - all legal text is stored offline, no caching needed
    
    def clear_cache(self) -> int:
        """
        Clear cached results for this component.
        
        Useful when you want fresh results or when iterating on functionality.
        
        Returns:
            Number of cache entries cleared
        """
        return self.cache.invalidate("original_text_retriever") 