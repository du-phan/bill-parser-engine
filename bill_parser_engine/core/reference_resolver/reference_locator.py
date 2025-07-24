"""
Reference location component for the normative reference resolver pipeline.

Identifies normative references (legal citations) in delta text fragments from TextReconstructor output.

Key Features:
=============
- **Delta Fragment Analysis**: Scans only changed text (deleted_or_replaced_text + newly_inserted_text)
- **DELETIONAL/DEFINITIONAL Classification**: Tags references by source type for downstream processing
- **French Legal Reference Recognition**: Specialized for French legislative patterns
- **Confidence-Based Filtering**: Quality control through confidence scoring
- **Structured Output**: JSON Mode for reliable parsing
- **Caching**: Avoids redundant API calls

Reference Classification:
========================
- DELETIONAL references: Found in deleted_or_replaced_text, use original law context for object linking
- DEFINITIONAL references: Found in newly_inserted_text, use amended text context for object linking

Technical Implementation:
========================
- Uses Mistral Chat API in JSON Mode for structured output
- Implements French legal reference pattern recognition
- Focuses on reference text extraction and source classification
- Provides confidence-based quality filtering
- Supports caching for performance optimization
"""

import json
import logging
import os
from typing import List, Optional

from mistralai import Mistral

from bill_parser_engine.core.reference_resolver.config import MISTRAL_MODEL
from bill_parser_engine.core.reference_resolver.models import (
    LocatedReference,
    ReconstructorOutput,
    ReferenceSourceType,
)
from bill_parser_engine.core.reference_resolver.prompts import REFERENCE_LOCATOR_SYSTEM_PROMPT
from bill_parser_engine.core.reference_resolver.cache_manager import SimpleCache, get_cache
from bill_parser_engine.core.reference_resolver.rate_limiter import rate_limiter
from .rate_limiter import call_mistral_json_model

logger = logging.getLogger(__name__)


class ReferenceLocator:
    """
    Locates normative references in delta text fragments.
    
    Scans only the changed text fragments (deleted_or_replaced_text + newly_inserted_text)
    instead of full article text for efficient reference detection.
    
    Reference Classification:
    - DELETIONAL references: Found in deleted_or_replaced_text fragments
    - DEFINITIONAL references: Found in newly_inserted_text fragments
    
    Uses Mistral Chat API in JSON Mode for reliable structured output.
    
    Implementation:
    - Scan only the delta fragments that actually changed
    - Focus on reference text and source classification
    - Simple validation without complex correction logic
    - Confidence-based quality filtering
    """

    def __init__(self, api_key: str = None, min_confidence: float = 0.5, cache: Optional[SimpleCache] = None, use_cache: bool = True):
        """
        Initialize the reference locator.

        Args:
            api_key: Mistral API key (defaults to MISTRAL_API_KEY environment variable)
            min_confidence: Minimum confidence threshold for including references
            cache: Cache instance for storing intermediate results (uses global if None)
            use_cache: Whether to use caching (useful to disable when iterating on prompts)
        """
        self.client = Mistral(api_key=api_key or os.getenv("MISTRAL_API_KEY"))
        self.min_confidence = min_confidence
        self.cache = cache or get_cache()
        self.use_cache = use_cache

    def locate(self, reconstructor_output: ReconstructorOutput) -> List[LocatedReference]:
        """
        Locate all normative references in the delta fragments.
        
        Process:
        1. Extract delta fragments (deleted_or_replaced_text + newly_inserted_text)
        2. Send fragments to LLM for reference detection
        3. Classify found references by source type:
           - DELETIONAL: References found in deleted_or_replaced_text
           - DEFINITIONAL: References found in newly_inserted_text
        4. Apply confidence filtering and deduplication
        5. Return structured LocatedReference objects

        Args:
            reconstructor_output: Output from TextReconstructor containing:
                - deleted_or_replaced_text: Text that was removed (scan for DELETIONAL refs)
                - newly_inserted_text: Text that was added (scan for DEFINITIONAL refs)
                - intermediate_after_state_text: Full article (NOT used for scanning)

        Returns:
            List of LocatedReference objects with reference_text, source, and confidence

        Raises:
            ValueError: If input validation fails
            RuntimeError: If API call fails or returns invalid JSON
        """
        # Input validation
        if not isinstance(reconstructor_output, ReconstructorOutput):
            raise ValueError("Input must be a ReconstructorOutput object")

        # Try to get from cache first (if enabled)
        if self.use_cache:
            cache_key_data = {
                'deleted_or_replaced_text': reconstructor_output.deleted_or_replaced_text,
                'newly_inserted_text': reconstructor_output.newly_inserted_text,
                'min_confidence': self.min_confidence
            }
            
            cached_result = self.cache.get("reference_locator", cache_key_data)
            if cached_result is not None:
                logger.info("Using cached result for ReferenceLocator")
                return cached_result
        
        # Calculate delta fragments for processing
        deleted_len = len(reconstructor_output.deleted_or_replaced_text)
        inserted_len = len(reconstructor_output.newly_inserted_text)
        delta_chars = deleted_len + inserted_len

        # Prepare delta fragments for LLM
        user_prompt_payload = self._create_user_prompt_payload(
            deleted_text=reconstructor_output.deleted_or_replaced_text,
            newly_inserted_text=reconstructor_output.newly_inserted_text
        )

        try:
            # Send fragments to LLM for reference detection
            content = call_mistral_json_model(
                client=self.client,
                rate_limiter=rate_limiter,
                system_prompt=REFERENCE_LOCATOR_SYSTEM_PROMPT,
                user_payload=user_prompt_payload,
                component_name="ReferenceLocator",
            )
            
            if not content:
                raise RuntimeError("ReferenceLocator received no content from API utility.")

            # Parse and validate JSON response
            self._validate_response_structure(content)

            # Convert API response to LocatedReference objects
            located_refs = []
            for ref_data in content.get("located_references", []):
                if self._validate_reference_data(ref_data):
                    located_ref = self._create_located_reference(ref_data)
                    located_refs.append(located_ref)
                else:
                    logger.warning(f"Skipping invalid reference data: {ref_data}")

            # Filter by confidence threshold
            filtered_refs = self._filter_by_confidence(located_refs)
            
            # Remove exact duplicates while preserving cross-source variations
            deduplicated_refs = self._deduplicate_references(filtered_refs)
            
            # Cache the result (if enabled)
            if self.use_cache:
                self.cache.set("reference_locator", cache_key_data, deduplicated_refs)
                logger.info("Cached result for future use")
            
            return deduplicated_refs

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {e}")
            raise RuntimeError(f"ReferenceLocator received invalid JSON from API: {e}") from e
        except Exception as e:
            logger.error(f"Reference location failed: {e}")
            raise RuntimeError(f"ReferenceLocator API call failed: {e}") from e

    def _create_user_prompt_payload(self, deleted_text: str, newly_inserted_text: str) -> dict:
        """
        Create a user prompt payload with the delta fragments.
        
        Creates a dictionary containing only the changed text fragments:
        - deleted_or_replaced_text: Text that was removed (for DELETIONAL refs)
        - newly_inserted_text: Text that was added (for DEFINITIONAL refs)
        
        Args:
            deleted_text: Text that was deleted or replaced
            newly_inserted_text: Text that was added
            
        Returns:
            Dictionary with delta fragments
        """
        return {
            "deleted_or_replaced_text": deleted_text,
            "newly_inserted_text": newly_inserted_text
        }

    def _validate_response_structure(self, content: dict) -> None:
        """
        Validate that the API response has the expected structure.
        
        Ensures the LLM response follows the expected JSON schema before processing.
        
        Args:
            content: Parsed JSON response from Mistral
            
        Raises:
            ValueError: If response structure is invalid
        """
        if "located_references" not in content:
            raise ValueError("API response missing required field: located_references")

        if not isinstance(content["located_references"], list):
            raise ValueError("located_references must be a list")

    def _validate_reference_data(self, ref_data: dict) -> bool:
        """
        Validate individual reference data from the LLM response.
        
        Validation checks:
        - Required fields present (reference_text, source, confidence)
        - Source type is valid (DELETIONAL or DEFINITIONAL)
        - Confidence is numeric and in valid range (0-1)
        - Reference text is non-empty string
        
        Args:
            ref_data: Dictionary containing reference information
            
        Returns:
            True if the reference data is valid, False otherwise
        """
        # Check required fields
        required_fields = ["reference_text", "source", "confidence"]
        for field in required_fields:
            if field not in ref_data:
                logger.warning(f"Reference missing required field: {field}")
                return False

        # Validate source type - must be DELETIONAL or DEFINITIONAL
        source = ref_data["source"]
        if source not in ["DELETIONAL", "DEFINITIONAL"]:
            logger.warning(f"Invalid source type: {source}")
            return False

        # Validate confidence range - must be between 0 and 1
        confidence = ref_data["confidence"]
        if not isinstance(confidence, (int, float)) or not (0 <= confidence <= 1):
            logger.warning(f"Invalid confidence value: {confidence}")
            return False

        # Validate reference text is non-empty
        reference_text = ref_data["reference_text"]
        if not isinstance(reference_text, str) or not reference_text.strip():
            logger.warning(f"Invalid reference text: {reference_text}")
            return False

        return True

    def _create_located_reference(self, ref_data: dict) -> LocatedReference:
        """
        Create a LocatedReference object from validated reference data.
        
        Args:
            ref_data: Validated reference data from LLM response
            
        Returns:
            LocatedReference object with reference_text, source, and confidence
        """
        return LocatedReference(
            reference_text=ref_data["reference_text"].strip(),
            source=ReferenceSourceType(ref_data["source"]),
            confidence=float(ref_data["confidence"])
        )

    def _filter_by_confidence(self, located_refs: List[LocatedReference]) -> List[LocatedReference]:
        """
        Filter references by minimum confidence threshold.
        
        Removes low-confidence references to improve overall accuracy.
        
        Args:
            located_refs: List of located references
            
        Returns:
            Filtered list of references meeting confidence threshold
        """
        filtered_refs = [
            ref for ref in located_refs 
            if ref.confidence >= self.min_confidence
        ]
        
        if len(filtered_refs) < len(located_refs):
            filtered_count = len(located_refs) - len(filtered_refs)
            logger.info(f"Filtered {filtered_count} low-confidence references "
                       f"(threshold: {self.min_confidence})")

        # Log reference statistics for monitoring
        logger.info(f"Located {len(filtered_refs)} references: "
                   f"{sum(1 for r in filtered_refs if r.source == ReferenceSourceType.DELETIONAL)} DELETIONAL, "
                   f"{sum(1 for r in filtered_refs if r.source == ReferenceSourceType.DEFINITIONAL)} DEFINITIONAL")

        return filtered_refs

    def _deduplicate_references(self, located_refs: List[LocatedReference]) -> List[LocatedReference]:
        """
        Remove exact duplicates while preserving legitimate cross-source variations.
        
        Deduplication logic:
        - Remove exact duplicates: same reference_text + same source
        - Keep cross-source duplicates: same reference_text but different source
        - Preserve highest confidence when duplicates exist
        
        Cross-source references are preserved because the same legal reference might
        appear in both deleted and newly inserted text, requiring different handling:
        
        Example:
        - DELETIONAL: "aux articles L. 254-1" (being removed from old context)
        - DEFINITIONAL: "aux articles L. 254-1" (being added in new context)
        
        Args:
            located_refs: List of located references potentially containing duplicates
            
        Returns:
            Deduplicated list of references
        """
        if not located_refs:
            return []
        
        # Group by (reference_text, source) tuple to identify exact duplicates
        ref_groups = {}
        for ref in located_refs:
            key = (ref.reference_text, ref.source)
            if key not in ref_groups:
                ref_groups[key] = []
            ref_groups[key].append(ref)
        
        # Keep highest confidence reference from each group
        deduplicated_refs = []
        duplicates_removed = 0
        
        for key, refs in ref_groups.items():
            if len(refs) > 1:
                # Multiple references with same text+source - keep highest confidence
                best_ref = max(refs, key=lambda r: r.confidence)
                deduplicated_refs.append(best_ref)
                duplicates_removed += len(refs) - 1
                logger.debug(f"Deduplicated {len(refs)} instances of '{key[0]}' ({key[1].value}), "
                           f"kept confidence {best_ref.confidence}")
            else:
                # Unique reference
                deduplicated_refs.append(refs[0])
        
        if duplicates_removed > 0:
            logger.info(f"Removed {duplicates_removed} exact duplicate references, "
                       f"kept {len(deduplicated_refs)} unique references")
        
        return deduplicated_refs

    def clear_cache(self) -> int:
        """
        Clear cached results for this component.
        
        Useful when iterating on prompts or when you want fresh results.
        
        Returns:
            Number of cache entries cleared
        """
        return self.cache.invalidate("reference_locator") 