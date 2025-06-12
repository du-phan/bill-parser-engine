"""
Text reconstruction component.

This component applies amendment instructions mechanically to original text,
producing before/after fragments. It uses Mistral API in JSON Mode for
deterministic text operations.
"""

import json
import logging
import os
from typing import Optional

from mistralai import Mistral

from bill_parser_engine.core.reference_resolver.config import MISTRAL_MODEL
from bill_parser_engine.core.reference_resolver.models import (
    BillChunk,
    ReconstructorOutput,
    TargetOperationType,
)
from bill_parser_engine.core.reference_resolver.prompts import TEXT_RECONSTRUCTOR_SYSTEM_PROMPT
from bill_parser_engine.core.reference_resolver.cache_manager import SimpleCache, get_cache
from bill_parser_engine.core.reference_resolver.rate_limiter import rate_limiter

logger = logging.getLogger(__name__)


class TextReconstructor:
    """
    Applies amendment instructions mechanically to original text.
    
    This component is the cornerstone of the "Lawyer's Mental Model" - it creates
    the fundamental before/after text states that drive all downstream processing.
    
    Uses Mistral Chat API in JSON Mode for structured output with deterministic
    text operations.
    """

    def __init__(self, api_key: Optional[str] = None, cache: Optional[SimpleCache] = None, use_cache: bool = True):
        """
        Initialize the reconstructor with Mistral client.

        Args:
            api_key: Mistral API key (defaults to MISTRAL_API_KEY environment variable)
            cache: Cache instance for storing intermediate results (uses global if None)
            use_cache: Whether to use caching (useful to disable when iterating on prompts)
        """
        self.client = Mistral(api_key=api_key or os.getenv("MISTRAL_API_KEY"))
        self.system_prompt = TEXT_RECONSTRUCTOR_SYSTEM_PROMPT
        self.cache = cache or get_cache()
        self.use_cache = use_cache

    def reconstruct(self, original_law_article: str, amendment_chunk: BillChunk) -> ReconstructorOutput:
        """
        Apply amendment instructions mechanically to original text.

        Args:
            original_law_article: The full text of the target article
            amendment_chunk: A single chunk containing the amendment instructions

        Returns:
            ReconstructorOutput object with before/after text fragments

        Raises:
            ValueError: If trying to modify empty article or other validation errors
        """
        # Input validation
        if not amendment_chunk.target_article:
            raise ValueError("BillChunk must have a target_article")

        operation_type = amendment_chunk.target_article.operation_type
        if operation_type == TargetOperationType.MODIFY and not original_law_article.strip():
            raise ValueError("Cannot modify empty article")

        # Handle INSERT operations where original article may be empty
        if operation_type == TargetOperationType.INSERT and not original_law_article.strip():
            logger.info(f"INSERT operation with empty original article for chunk {amendment_chunk.chunk_id}")

        # Try to get from cache first (if enabled)
        if self.use_cache:
            cache_key_data = {
                'original_law_article': original_law_article,
                'amendment_text': amendment_chunk.text,
                'operation_type': operation_type.value if operation_type else None
            }
            
            cached_result = self.cache.get("text_reconstructor", cache_key_data)
            if cached_result is not None:
                print(f"✓ Using cached result for TextReconstructor")
                return cached_result
        
        print(f"→ Processing new reconstruction with TextReconstructor")

        user_prompt = self._create_user_prompt(original_law_article, amendment_chunk.text)

        try:
            # Use shared rate limiter across all components
            rate_limiter.wait_if_needed("TextReconstructor")
            
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
            self._validate_response(content)

            # Post-processing validation
            deleted_text = content.get("deleted_or_replaced_text", "")
            if deleted_text and original_law_article.strip():
                self._validate_deleted_text_exists(deleted_text, original_law_article)

            result = ReconstructorOutput(
                deleted_or_replaced_text=deleted_text,
                intermediate_after_state_text=content["intermediate_after_state_text"]
            )
            
            # Cache the successful result (if enabled)
            if self.use_cache:
                self.cache.set("text_reconstructor", cache_key_data, result)
                print(f"✓ Cached result for future use")
            
            return result

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {e}")
            raise RuntimeError(f"TextReconstructor failed to parse API response: {e}") from e
        except Exception as e:
            # Log the error and re-raise it - no silent failures
            import traceback
            logger.error(f"TextReconstructor failed: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise RuntimeError(f"TextReconstructor API call failed: {str(e)}") from e

    def _create_user_prompt(self, original_article: str, amendment_text: str) -> str:
        """
        Create a user prompt with the original article and amendment instruction.

        Args:
            original_article: The full text of the original article
            amendment_text: The amendment instruction text

        Returns:
            Formatted user prompt string
        """
        return json.dumps({
            "original_article": original_article,
            "amendment": amendment_text
        }, ensure_ascii=False)

    def _validate_response(self, content: dict) -> None:
        """
        Validate that the response contains required fields.

        Args:
            content: Parsed JSON response from Mistral

        Raises:
            ValueError: If required fields are missing
        """
        required_fields = ["deleted_or_replaced_text", "intermediate_after_state_text"]
        for field in required_fields:
            if field not in content:
                raise ValueError(f"Missing required field: {field}")
            if not isinstance(content[field], str):
                raise ValueError(f"Field {field} must be a string, got {type(content[field])}")

        # Check for warnings
        if content.get("warning"):
            logger.warning(f"LLM warning: {content['warning']}")

    def _validate_deleted_text_exists(self, deleted_text: str, original_article: str) -> None:
        """
        Validate that deleted text actually appears in the original article.

        Args:
            deleted_text: The text that was allegedly deleted/replaced
            original_article: The original article text

        Raises:
            ValueError: If deleted text is not found in original
        """
        # Simple check - for production, could be more sophisticated
        if deleted_text.strip() and deleted_text.strip() not in original_article:
            logger.warning(
                f"Deleted text not found in original article. "
                f"Deleted: '{deleted_text[:100]}...'"
            )
            # Don't raise exception - log warning and continue

    def clear_cache(self) -> int:
        """
        Clear cached results for this component.
        
        Useful when iterating on prompts or when you want fresh results.
        
        Returns:
            Number of cache entries cleared
        """
        return self.cache.invalidate("text_reconstructor")

 