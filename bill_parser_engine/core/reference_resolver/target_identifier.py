"""
Target article identification component.

This component analyzes chunks of legislative text and identifies the primary legal article,
section, or code provision that is the target of modification, insertion, or abrogation.
"""

import json
import os
from typing import List, Optional

from mistralai import Mistral

from bill_parser_engine.core.reference_resolver.models import BillChunk, TargetArticle, TargetOperationType
from bill_parser_engine.core.reference_resolver.config import MISTRAL_MODEL
from bill_parser_engine.core.reference_resolver.prompts import TARGET_ARTICLE_IDENTIFIER_SYSTEM_PROMPT
from bill_parser_engine.core.reference_resolver.cache_manager import SimpleCache, get_cache
from bill_parser_engine.core.reference_resolver.rate_limiter import rate_limiter


class TargetArticleIdentifier:
    """
    Identifies the primary legal article or section that is the target of modification,
    insertion, or abrogation in each chunk of a legislative bill.
    
    This component analyzes each chunk to determine:
    1. The main legal article/section being affected (target article)
    2. The operation type (INSERT, MODIFY, ABROGATE, RENUMBER, or OTHER)
    3. The relevant code and article identifiers
    
    Uses Mistral Chat API in JSON Mode for structured output.
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
        self.system_prompt = TARGET_ARTICLE_IDENTIFIER_SYSTEM_PROMPT
        self.cache = cache or get_cache()
        self.use_cache = use_cache



    def identify(self, chunk: BillChunk) -> TargetArticle:
        """
        Identify the target article for a bill chunk using Mistral JSON Mode.

        Args:
            chunk: The BillChunk to analyze

        Returns:
            TargetArticle object with identified information
        """
        # Try to get from cache first (if enabled)
        if self.use_cache:
            cache_key_data = {
                'text': chunk.text,
                'article_introductory_phrase': chunk.article_introductory_phrase,
                'major_subdivision_introductory_phrase': chunk.major_subdivision_introductory_phrase,
                'hierarchy_path': chunk.hierarchy_path
            }
            
            cached_result = self.cache.get("target_identifier", cache_key_data)
            if cached_result is not None:
                print(f"✓ Using cached result for TargetArticleIdentifier")
                return cached_result
        
        print(f"→ Processing new chunk with TargetArticleIdentifier")
        
        user_prompt = self._create_user_prompt(chunk)

        try:
            # Use shared rate limiter across all components
            rate_limiter.wait_if_needed("TargetArticleIdentifier")
            
            # Use the chat.complete method with response_format for JSON mode
            response = self.client.chat.complete(
                model=MISTRAL_MODEL,
                temperature=0.0,
                messages=[
                    {
                        "role": "system",
                        "content": self.system_prompt
                    },
                    {
                        "role": "user", 
                        "content": user_prompt
                    }
                ],
                response_format={"type": "json_object"}
            )
            
            content = json.loads(response.choices[0].message.content)
            target_article = self._create_target_article(content)
            
            # Cache the successful result (if enabled)
            if self.use_cache:
                self.cache.set("target_identifier", cache_key_data, target_article)
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
        Create a user prompt with chunk text and all available metadata context.

        Args:
            chunk: The BillChunk to analyze

        Returns:
            Formatted user prompt string with comprehensive context
        """
        # Collect all available context information
        context_parts = []
        
        if chunk.article_introductory_phrase:
            context_parts.append(f"Article Context: {chunk.article_introductory_phrase}")
        
        if chunk.major_subdivision_introductory_phrase:
            context_parts.append(f"Subdivision Context: {chunk.major_subdivision_introductory_phrase}")
        
        # Combine context parts, or use "None" if no context available
        context_text = " | ".join(context_parts) if context_parts else "None"
        
        return f"""
Chunk: {chunk.text}
Context: {context_text}
Hierarchy: {' > '.join(chunk.hierarchy_path)}
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
            full_citation=self._generate_full_citation(content.get("code"), content.get("article")),
            confidence=float(content.get("confidence", 0.5)),
            raw_text=content.get("raw_text"),
            version="v0"
        )

    def _generate_full_citation(self, code: Optional[str], article: Optional[str]) -> Optional[str]:
        """
        Generate full citation from code and article if both are present.

        Args:
            code: The code name
            article: The article identifier

        Returns:
            Full citation string or None
        """
        if code and article:
            return f"article {article} du {code}"
        return None

    def clear_cache(self) -> int:
        """
        Clear cached results for this component.
        
        Useful when iterating on prompts or when you want fresh results.
        
        Returns:
            Number of cache entries cleared
        """
        return self.cache.invalidate("target_identifier")

 