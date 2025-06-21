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
from typing import List, Optional

from mistralai import Mistral

from bill_parser_engine.core.reference_resolver.models import BillChunk, TargetArticle, TargetOperationType
from bill_parser_engine.core.reference_resolver.config import MISTRAL_MODEL
from bill_parser_engine.core.reference_resolver.cache_manager import SimpleCache, get_cache
from bill_parser_engine.core.reference_resolver.rate_limiter import rate_limiter
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
        # Check if chunk contains only versioning metadata without legal operations
        if self._is_pure_versioning_metadata(chunk.text):
            print(f"✓ Detected pure versioning metadata for chunk {chunk.chunk_id}: {chunk.text}")
            return TargetArticle(
                operation_type=TargetOperationType.OTHER,
                code=None,
                article=None
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
        
        user_prompt = self._create_user_prompt(chunk)

        try:
            # Use shared rate limiter with retry logic for 429 errors
            def make_api_call():
                return self.client.chat.complete(
                    model=MISTRAL_MODEL,
                    temperature=0.0,
                    messages=[
                        {
                            "role": "system",
                            "content": TARGET_ARTICLE_IDENTIFIER_SYSTEM_PROMPT
                        },
                        {
                            "role": "user", 
                            "content": user_prompt
                        }
                    ],
                    response_format={"type": "json_object"}
                )
            
            response = rate_limiter.execute_with_retry(make_api_call, "TargetArticleIdentifier")
            
            content = json.loads(response.choices[0].message.content)
            target_article = self._create_target_article(content)
            
            # Cache the successful result (if enabled)
            if self.use_cache:
                self.cache.set("target_identifier_unified", cache_key_data, target_article)
                print(f"✓ Cached result for future use")
            
            return target_article
            
        except Exception as e:
            # Log the error and re-raise it - no silent failures
            import traceback
            print(f"TargetArticleIdentifier failed: {str(e)}")
            print(f"Traceback: {traceback.format_exc()}")
            raise RuntimeError(f"TargetArticleIdentifier API call failed: {str(e)}") from e

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
        
        # Combine context parts
        context_text = " | ".join(context_parts) if context_parts else "Aucun"
        
        return f"""
FRAGMENT À ANALYSER : {chunk.text}

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
            article=content.get("article")
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

 