"""
OriginalTextRetriever: Fetch current legal text for target articles using multiple sources.

This component retrieves the current legal text of target articles identified by 
TargetArticleIdentifier. This is critical because reference objects may only be visible 
in the original law, not in the amendment text.

Features:
- Primary: pylegifrance API with proper error handling (for French codes)
- EU Legal Texts: Local files from /data/eu_law_text/ with LLM extraction
- Hierarchical fallback: L. 118-1-2 → try L. 118-1 and extract subsection 2 using LLM
- Caching: Uses standardized cache_manager.py for efficiency
- INSERT handling: Return empty string for INSERT operations
"""

import os
import re
import logging
import json
from typing import Tuple, Dict, Optional, List
from pathlib import Path

from pylegifrance import recherche_code
from pylegifrance.models.constants import CodeNom

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
    Fetches the current/existing text of target articles from multiple sources:
    1. French legal codes via pylegifrance API
    2. EU legal texts from local files (/data/eu_law_text/)
    
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
        
        # Code name mapping for pylegifrance
        self.code_name_mapping = {
            "code rural et de la pêche maritime": CodeNom.CREDLPM,
            "code de l'environnement": CodeNom.CENV,
            "code civil": CodeNom.CCIV,
            "code pénal": CodeNom.CPEN,
            "code de la santé publique": CodeNom.CSP,
            "code du travail": CodeNom.CTRAV,
            "code de commerce": CodeNom.CCOM,
            "code de la consommation": CodeNom.CCONSO,
            "code de la construction et de l'habitation": CodeNom.CDLCED,
            "code forestier": CodeNom.CF,
            "code général des collectivités territoriales": CodeNom.CGCT,
            "code général des impôts": CodeNom.CGDI,
            "code de la propriété intellectuelle": CodeNom.CPI,
            "code de la route": CodeNom.CDLR,
            "code de la sécurité sociale": CodeNom.CSS,
            "code des assurances": CodeNom.CASSUR,
            "code monétaire et financier": CodeNom.CMEF,
            "code de procédure civile": CodeNom.CPRCIV,
            "code de procédure pénale": CodeNom.CPP,
        }
        
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
        
        # Detect if this is an EU legal text reference
        if self._is_eu_legal_reference(code):
            result_text, metadata = self._fetch_eu_legal_text(code, article)
        else:
            # Use pylegifrance for French codes
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
        
        return result_text, metadata
    
    def fetch_article_for_target(self, target_article: TargetArticle) -> Tuple[str, Dict]:
        """
        Convenience method to fetch article text using a TargetArticle object.
        
        Args:
            target_article: TargetArticle object from TargetArticleIdentifier
            
        Returns:
            Tuple of (article_text, retrieval_metadata)
        """
        # Handle INSERT operations - return empty text
        if target_article.operation_type == TargetOperationType.INSERT:
            logger.info(f"INSERT operation for {target_article.article} - returning empty text")
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
        article_content = self._read_eu_article_file(eu_dir_path, article_info)
        if not article_content:
            return "", {"source": "eu_legal_text", "success": False, "error": f"Article file not found: {article_info}"}
        
        # Extract specific content if needed
        if article_info.get("specific_part"):
            extracted_content = self._extract_eu_article_part(article_content, article_info, article)
            if extracted_content:
                logger.info(f"✓ Retrieved EU article {article} with specific part extraction")
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
    
    def _read_eu_article_file(self, eu_dir_path: Path, article_info: Dict) -> Optional[str]:
        """
        Read the EU article file content.
        
        Args:
            eu_dir_path: Path to EU legal text directory
            article_info: Parsed article information
            
        Returns:
            Article content or None if not found
        """
        article_num = article_info["article_number"]
        
        # Try article directory with overview.md first
        article_dir = eu_dir_path / f"Article_{article_num}"
        if article_dir.exists():
            overview_file = article_dir / "overview.md"
            if overview_file.exists():
                try:
                    return overview_file.read_text(encoding='utf-8')
                except Exception as e:
                    logger.warning(f"Error reading {overview_file}: {e}")
        
        # Try direct article file
        article_file = eu_dir_path / f"Article_{article_num}.md"
        if article_file.exists():
            try:
                return article_file.read_text(encoding='utf-8')
            except Exception as e:
                logger.warning(f"Error reading {article_file}: {e}")
        
        # Try alternative naming (lowercase)
        article_file = eu_dir_path / f"article_{article_num}.md"
        if article_file.exists():
            try:
                return article_file.read_text(encoding='utf-8')
            except Exception as e:
                logger.warning(f"Error reading {article_file}: {e}")
        
        return None
    
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
        Fetch French legal code text using pylegifrance API with hierarchical fallback.
        
        Args:
            code: French legal code name
            article: Article identifier
            
        Returns:
            Tuple of (article_text, retrieval_metadata)
        """
        # Try pylegifrance API for the full article first
        try:
            content = self._call_pylegifrance_api(code, article)
            if content:
                logger.info(f"✓ Retrieved article {article} from pylegifrance")
                return content, {"source": "pylegifrance", "success": True}
        except Exception as e:
            logger.warning(f"pylegifrance failed for {code} {article}: {e}")
        
        # Try hierarchical fallback if the article has multiple hierarchy levels
        if self._should_try_hierarchical_fallback(article):
            parent_article, subsection = self._parse_hierarchical_article(article)
            logger.info(f"→ Trying hierarchical fallback: {parent_article} → subsection {subsection}")
            
            try:
                # Try to get the parent article
                parent_content = self._call_pylegifrance_api(code, parent_article)
                if parent_content:
                    logger.info(f"✓ Retrieved parent article {parent_article}")
                    
                    # Use LLM to extract the specific subsection
                    subsection_content = self._extract_subsection_with_llm(parent_content, subsection, article)
                    if subsection_content:
                        logger.info(f"✓ Retrieved {article} via hierarchical fallback")
                        return subsection_content, {
                            "source": "hierarchical_fallback", 
                            "success": True,
                            "parent_article": parent_article,
                            "subsection": subsection,
                            "method": "llm_extraction"
                        }
                    else:
                        logger.warning(f"LLM failed to extract subsection {subsection} from {parent_article}")
                        return "", {
                            "source": "hierarchical_fallback", 
                            "success": False, 
                            "error": f"LLM extraction failed for subsection {subsection}",
                            "parent_article": parent_article,
                            "subsection": subsection
                        }
                else:
                    logger.warning(f"Parent article {parent_article} not found")
            except Exception as e:
                logger.error(f"Hierarchical fallback failed for {article}: {e}")
        
        # All methods failed
        logger.error(f"Could not retrieve article {article} from {code}")
        return "", {"source": "none", "success": False, "error": "All retrieval methods failed"}

    def _should_try_hierarchical_fallback(self, article: str) -> bool:
        """
        Check if an article should have hierarchical fallback attempted.
        
        Args:
            article: Article identifier (e.g., "L. 118-1-2")
            
        Returns:
            True if hierarchical fallback should be attempted
        """
        # Only for articles with multiple hierarchy levels (e.g., L. 118-1-2, not L. 118-1)
        return (article.startswith("L. ") and 
                article.count("-") >= 2 and
                self.mistral_client is not None)
    
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
    
    def _call_pylegifrance_api(self, code: str, article: str) -> Optional[str]:
        """
        Call pylegifrance API to retrieve article text.
        
        Args:
            code: The legal code name
            article: The article identifier
            
        Returns:
            Article text if successful, None otherwise
        """
        # Normalize code name to match pylegifrance constants
        code_lower = code.lower().strip()
        code_enum = self.code_name_mapping.get(code_lower)
        
        if not code_enum:
            logger.warning(f"Unknown code name for pylegifrance: {code}")
            return None
        
        # Clean up article identifier for search
        search_article = article.replace(" ", "").replace(".", "")
        
        logger.debug(f"recherche_code params: code_enum={code_enum}, search_article={search_article}")
        
        try:
            # Check if Legifrance credentials are available
            if not (os.getenv('LEGIFRANCE_CLIENT_ID') and os.getenv('LEGIFRANCE_CLIENT_SECRET')):
                logger.warning("Legifrance credentials not available")
                return None
            result = recherche_code(code_name=code_enum, search=search_article)
            
            # Handle the actual API response structure: list of dicts with 'article' key
            if result and isinstance(result, list) and len(result) > 0:
                first_result = result[0]
                if isinstance(first_result, dict) and 'article' in first_result:
                    article_data = first_result['article']
                    if isinstance(article_data, dict):
                        # Prefer plain text over HTML
                        text = article_data.get('texte', '')
                        if text:
                            return text
                        # Fallback to HTML version if plain text not available
                        html_text = article_data.get('texteHtml', '')
                        if html_text:
                            # Basic HTML tag removal for fallback
                            import re
                            text = re.sub(r'<[^>]+>', '', html_text)
                            return text
            
            logger.warning(f"No text found in pylegifrance result for {code} {article}")
            return None
                
        except Exception as e:
            logger.error(f"pylegifrance API call failed: {e}")
            raise
    
    def clear_cache(self) -> int:
        """
        Clear cached results for this component.
        
        Useful when you want fresh results or when iterating on functionality.
        
        Returns:
            Number of cache entries cleared
        """
        return self.cache.invalidate("original_text_retriever") 