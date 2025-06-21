"""
OriginalTextRetriever: Fetch current legal text for target articles using pylegifrance API.

This component retrieves the current legal text of target articles identified by 
TargetArticleIdentifier. This is critical because reference objects may only be visible 
in the original law, not in the amendment text.

Features:
- Primary: pylegifrance API with proper error handling
- Hierarchical fallback: L. 118-1-2 → try L. 118-1 and extract subsection 2 using LLM
- Caching: Uses standardized cache_manager.py for efficiency
- INSERT handling: Return empty string for INSERT operations
"""

import os
import logging
import json
from typing import Tuple, Dict, Optional

from pylegifrance import recherche_code
from pylegifrance.models.constants import CodeNom

try:
    from mistralai import Mistral
except ImportError:
    Mistral = None

from .models import TargetArticle, TargetOperationType
from .cache_manager import SimpleCache, get_cache

logger = logging.getLogger(__name__)


class OriginalTextRetriever:
    """
    Fetches the current/existing text of target articles identified by TargetArticleIdentifier.
    
    This is critical because reference objects may only be visible in the original law,
    not in the amendment text. Without this context, the ReferenceObjectLinker cannot
    properly identify what concepts/objects the references in deleted text refer to.
    
    Features hierarchical fallback: if L. 118-1-2 is not found, tries L. 118-1 and then
    uses LLM to extract the specific subsection 2 from the parent article text.
    """
    
    def __init__(self, cache: Optional[SimpleCache] = None, use_cache: bool = True):
        """
        Initialize the retriever with caching configuration.
        
        Args:
            cache: Cache instance for storing intermediate results (uses global if None)
            use_cache: Whether to use caching (useful to disable when iterating)
        """
        self.cache = cache or get_cache()
        self.use_cache = use_cache
        
        # Initialize Mistral client for hierarchical fallback
        api_key = os.getenv('MISTRAL_API_KEY')
        if api_key and Mistral:
            self.mistral_client = Mistral(api_key=api_key)
        else:
            self.mistral_client = None
            if not api_key:
                logger.warning("MISTRAL_API_KEY not found - hierarchical fallback with LLM will be disabled")
            if not Mistral:
                logger.warning("mistralai package not available - hierarchical fallback with LLM will be disabled")
        
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
    
    def fetch_article_text(self, code: str, article: str) -> Tuple[str, Dict]:
        """
        Fetch the full text of a target article with proper segmentation.
        
        Implements hierarchical fallback: if L. 118-1-2 is not found,
        tries L. 118-1 and then uses LLM to extract subsection 2.
        
        Args:
            code: The legal code name (e.g., "code rural et de la pêche maritime")
            article: The article identifier (e.g., "L. 254-1", "L. 118-1-2")
            
        Returns:
            Tuple of (article_text, retrieval_metadata)
            - article_text: Full text with hierarchy (I, II, 1°, 2°, etc.) or empty string
            - retrieval_metadata: Contains retrieval status, source, and any error information
        """
        if not code or not article:
            return "", {"source": "none", "success": False, "error": "Missing code or article"}
        
        # Try to get from cache first (if enabled)
        if self.use_cache:
            cache_key_data = {
                'code': code,
                'article': article
            }
            
            cached_result = self.cache.get("original_text_retriever", cache_key_data)
            if cached_result is not None:
                logger.info(f"✓ Retrieved article {article} from cache")
                return cached_result, {"source": "cache", "success": True}
        
        logger.info(f"→ Fetching article {article} from {code}")
        
        # Try pylegifrance API for the full article first
        try:
            content = self._call_pylegifrance_api(code, article)
            if content:
                # Cache the successful result (if enabled)
                if self.use_cache:
                    cache_key_data = {
                        'code': code,
                        'article': article
                    }
                    self.cache.set("original_text_retriever", cache_key_data, content)
                    logger.info(f"✓ Cached article {article} for future use")
                
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
                        # Cache the successful result (if enabled)
                        if self.use_cache:
                            cache_key_data = {
                                'code': code,
                                'article': article
                            }
                            self.cache.set("original_text_retriever", cache_key_data, subsection_content)
                            logger.info(f"✓ Cached hierarchical result for {article}")
                        
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
        
        system_prompt = f"""Vous êtes un spécialiste de l'extraction de textes juridiques. Étant donné un texte d'article juridique français et un identifiant de sous-section, extrayez le contenu spécifique de la sous-section.

Votre tâche :
1. Trouvez la sous-section identifiée par "{subsection}" dans le texte juridique fourni
2. Extrayez le contenu complet de cette sous-section
3. Retournez uniquement le contenu de cette sous-section spécifique

L'identifiant de sous-section "{subsection}" peut apparaître comme :
- Un numéro autonome : "{subsection}"
- Avec le symbole degré : "{subsection}°"
- Dans un format de liste numérotée
- Comme partie d'une structure hiérarchique

Retournez un objet JSON avec :
- "found": booléen (true si la sous-section a été trouvée)
- "content": chaîne (le contenu extrait de la sous-section, ou chaîne vide si non trouvée)
- "explanation": chaîne (brève explication de ce qui a été trouvé ou pourquoi cela a échoué)

Exemple :
Si vous cherchez la sous-section "2" dans un texte contenant "1° Premier élément... 2° Contenu du deuxième élément ici... 3° Troisième élément...", 
retournez {{"found": true, "content": "2° Contenu du deuxième élément ici", "explanation": "Sous-section 2 trouvée comme point numéroté"}}"""

        user_message = f"""Extrayez la sous-section "{subsection}" de ce texte d'article juridique :

Article original recherché : {original_article}
Texte de l'article parent :
{parent_text}

Trouvez et extrayez le contenu complet de la sous-section "{subsection}"."""

        try:
            response = self.mistral_client.chat.complete(
                model="mistral-large-latest",
                temperature=0.0,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
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