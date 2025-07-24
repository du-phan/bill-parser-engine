"""
ResultValidator component for validating legal text reconstruction results.

This component uses LLM-based analysis to validate that legal text reconstruction
has preserved legal coherence, proper formatting, and structural integrity after
applying amendment operations.
"""

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import List, Optional

from mistralai import Mistral

from bill_parser_engine.core.reference_resolver.models import AmendmentOperation
from bill_parser_engine.core.reference_resolver.cache_manager import SimpleCache
from bill_parser_engine.core.reference_resolver.prompts import (
    RESULT_VALIDATOR_SYSTEM_PROMPT,
    RESULT_VALIDATOR_USER_PROMPT_TEMPLATE
)
from bill_parser_engine.core.reference_resolver.rate_limiter import rate_limiter, call_mistral_with_messages

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of validating a reconstructed legal text."""
    validation_status: str  # "VALID", "WARNINGS", "ERRORS"
    critical_errors: List[str]
    major_errors: List[str]
    minor_errors: List[str]
    suggestions: List[str]
    overall_score: float
    validation_summary: str
    processing_time_ms: int = 0


class ResultValidator:
    """
    Validates legal text reconstruction results using LLM-based analysis.
    
    Performs comprehensive validation of:
    - Legal coherence and hierarchical structure
    - Completeness of applied operations
    - Formatting and typographic compliance
    - Grammar and syntax correctness
    - Document structure preservation
    """

    def __init__(self, api_key: Optional[str] = None, cache: Optional[SimpleCache] = None, use_cache: bool = True):
        """
        Initialize the result validator.

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
        
        logger.info("ResultValidator initialized with caching: %s", "enabled" if use_cache else "disabled")

    def validate_legal_coherence(
        self,
        original_text: str,
        modified_text: str,
        operations: List[AmendmentOperation]
    ) -> ValidationResult:
        """
        Validate the legal coherence of a reconstructed text.

        Args:
            original_text: The original legal text before modifications
            modified_text: The text after applying all operations
            operations: List of operations that were applied

        Returns:
            ValidationResult with detailed analysis and scoring

        Raises:
            RuntimeError: If API call fails
        """
        logger.info("Validating legal coherence for %d operations", len(operations))
        
        # Check cache first
        if self.use_cache:
            cache_key_data = {
                'original_text': original_text,
                'modified_text': modified_text,
                'operations': [
                    {
                        'type': op.operation_type.value,
                        'target': op.target_text,
                        'replacement': op.replacement_text,
                        'position': op.position_hint
                    } for op in operations
                ]
            }
            cached_result = self.cache.get("result_validator", cache_key_data)
            if cached_result is not None:
                logger.debug("Found cached validation result")
                return self._deserialize_result(cached_result)

        start_time = time.time()
        
        try:
            # Build prompts
            system_prompt = RESULT_VALIDATOR_SYSTEM_PROMPT
            user_prompt = self._build_user_prompt(original_text, modified_text, operations)
            
            # Call LLM with rate limiting
            response = call_mistral_with_messages(
                client=self.client,
                rate_limiter=rate_limiter,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                component_name="ResultValidator",
                temperature=0.1,  # Slightly creative for detailed analysis
                response_format={"type": "json_object"}
            )
            
            # Parse response
            response_content = response.choices[0].message.content
            logger.debug("Raw LLM response: %s", response_content)
            
            result_data = json.loads(response_content)
            result = self._parse_response(result_data)
            
            processing_time = int((time.time() - start_time) * 1000)
            result.processing_time_ms = processing_time
            
            logger.info("Validation completed - Status: %s, Score: %.2f", 
                       result.validation_status, result.overall_score)
            
            # Cache result
            if self.use_cache:
                serialized_result = self._serialize_result(result)
                self.cache.set("result_validator", cache_key_data, serialized_result)
            
            return result

        except json.JSONDecodeError as e:
            logger.error("Failed to parse LLM response as JSON: %s", e)
            return ValidationResult(
                validation_status="ERRORS",
                critical_errors=[f"Validation system error: {e}"],
                major_errors=[],
                minor_errors=[],
                suggestions=[],
                overall_score=0.0,
                validation_summary="Validation failed due to system error",
                processing_time_ms=int((time.time() - start_time) * 1000)
            )
        except Exception as e:
            logger.error("Failed to validate result: %s", e)
            return ValidationResult(
                validation_status="ERRORS",
                critical_errors=[f"Validation failed: {e}"],
                major_errors=[],
                minor_errors=[],
                suggestions=[],
                overall_score=0.0,
                validation_summary="Critical validation failure",
                processing_time_ms=int((time.time() - start_time) * 1000)
            )

    def _build_user_prompt(
        self, 
        original_text: str, 
        modified_text: str, 
        operations: List[AmendmentOperation]
    ) -> str:
        """Build the user prompt for validation."""
        # Create operations summary
        operations_summary = "\n".join([
            f"- {i+1}. {op.operation_type.value}: {op.position_hint}"
            f"{f' | Target: {op.target_text}' if op.target_text else ''}"
            f"{f' | Replacement: {op.replacement_text}' if op.replacement_text else ''}"
            for i, op in enumerate(operations)
        ])
        
        return RESULT_VALIDATOR_USER_PROMPT_TEMPLATE.format(
            original_text=original_text,
            modified_text=modified_text,
            operations_summary=operations_summary
        )

    def _parse_response(self, result_data: dict) -> ValidationResult:
        """Parse the LLM response into a ValidationResult."""
        try:
            # Validate required fields
            required_fields = [
                "validation_status", "critical_errors", "major_errors", 
                "minor_errors", "suggestions", "overall_score", "validation_summary"
            ]
            
            for field in required_fields:
                if field not in result_data:
                    raise ValueError(f"Response missing required '{field}' field")
            
            # Validate validation_status
            status = result_data["validation_status"]
            if status not in ["VALID", "WARNINGS", "ERRORS"]:
                logger.warning("Invalid validation_status: %s, defaulting to ERRORS", status)
                status = "ERRORS"
            
            # Validate overall_score
            score = float(result_data["overall_score"])
            if not (0 <= score <= 1):
                logger.warning("Invalid overall_score: %f, clamping to [0,1]", score)
                score = max(0, min(1, score))
            
            # Ensure error lists are actually lists
            critical_errors = result_data["critical_errors"]
            if not isinstance(critical_errors, list):
                critical_errors = [str(critical_errors)] if critical_errors else []
            
            major_errors = result_data["major_errors"]
            if not isinstance(major_errors, list):
                major_errors = [str(major_errors)] if major_errors else []
            
            minor_errors = result_data["minor_errors"]
            if not isinstance(minor_errors, list):
                minor_errors = [str(minor_errors)] if minor_errors else []
            
            suggestions = result_data["suggestions"]
            if not isinstance(suggestions, list):
                suggestions = [str(suggestions)] if suggestions else []
            
            return ValidationResult(
                validation_status=status,
                critical_errors=critical_errors,
                major_errors=major_errors,
                minor_errors=minor_errors,
                suggestions=suggestions,
                overall_score=score,
                validation_summary=result_data["validation_summary"]
            )
            
        except Exception as e:
            logger.error("Failed to parse validation response: %s", e)
            raise ValueError(f"Invalid validation response format: {e}")

    def _serialize_result(self, result: ValidationResult) -> dict:
        """Serialize result for caching."""
        return {
            "validation_status": result.validation_status,
            "critical_errors": result.critical_errors,
            "major_errors": result.major_errors,
            "minor_errors": result.minor_errors,
            "suggestions": result.suggestions,
            "overall_score": result.overall_score,
            "validation_summary": result.validation_summary,
            "processing_time_ms": result.processing_time_ms
        }

    def _deserialize_result(self, cached_data: dict) -> ValidationResult:
        """Deserialize cached result."""
        return ValidationResult(
            validation_status=cached_data["validation_status"],
            critical_errors=cached_data["critical_errors"],
            major_errors=cached_data["major_errors"],
            minor_errors=cached_data["minor_errors"],
            suggestions=cached_data["suggestions"],
            overall_score=cached_data["overall_score"],
            validation_summary=cached_data["validation_summary"],
            processing_time_ms=cached_data.get("processing_time_ms", 0)
        )

    def clear_cache(self) -> int:
        """Clear the result validator cache."""
        return self.cache.clear_by_prefix("result_validator")

    def get_cache_stats(self) -> dict:
        """Get cache statistics."""
        return self.cache.get_stats_by_prefix("result_validator") 