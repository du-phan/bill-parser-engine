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
from typing import Optional

from mistralai import Mistral

from bill_parser_engine.core.reference_resolver.models import AmendmentOperation, OperationType
from bill_parser_engine.core.reference_resolver.cache_manager import SimpleCache
from bill_parser_engine.core.reference_resolver.prompts import (
    OPERATION_APPLIER_SYSTEM_PROMPT,
    OPERATION_APPLIER_USER_PROMPT_TEMPLATE
)
from bill_parser_engine.core.reference_resolver.rate_limiter import rate_limiter

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
        
        # Simple input validation to catch common failure patterns
        validation_result = self._validate_operation_input(original_text, operation)
        if not validation_result.success:
            return OperationApplicationResult(
                success=False,
                modified_text=original_text,
                applied_fragment="",
                error_message=validation_result.error_message,
                processing_time_ms=int((time.time() - start_time) * 1000)
            )
        
        # Check cache first
        cache_key = f"operation_applier_{hash((original_text, str(operation)))}"
        if self.use_cache:
            cached_result = self.cache.get("operation_applier", cache_key)
            if cached_result is not None:
                logger.debug("Found cached operation result")
                return self._deserialize_result(cached_result)

        start_time = time.time()
        
        try:
            # Build prompts
            system_prompt = OPERATION_APPLIER_SYSTEM_PROMPT
            user_prompt = self._build_user_prompt(original_text, operation)
            
            # Call LLM with rate limiting
            def make_api_call():
                return self.client.chat.complete(
                    model="mistral-large-latest",
                    temperature=0.0,  # Deterministic application
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    response_format={"type": "json_object"}
                )
            
            response = rate_limiter.execute_with_retry(make_api_call, "OperationApplier")
            
            # Parse response
            response_content = response.choices[0].message.content
            logger.debug("Raw LLM response: %s", response_content)
            
            result_data = json.loads(response_content)
            result = self._parse_response(result_data, operation)
            
            processing_time = int((time.time() - start_time) * 1000)
            result.processing_time_ms = processing_time
            
            logger.info("Operation applied - Success: %s (processing time: %dms)", result.success, processing_time)
            
            # Cache result
            if self.use_cache:
                serialized_result = self._serialize_result(result)
                self.cache.set("operation_applier", cache_key, serialized_result)
            
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