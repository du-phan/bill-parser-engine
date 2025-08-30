"""
LegalAmendmentReconstructor - Main orchestrator for legal amendment text reconstruction.

This component orchestrates the 3-step LLM-based architecture:
1. InstructionDecomposer - Parse compound instructions into atomic operations
2. OperationApplier - Apply each operation sequentially using LLM intelligence
3. ResultValidator - Validate final result for legal coherence and formatting

The component extracts focused output fragments from the structured operations
for downstream reference location processing.

KEY FEATURES:
- Uses the 3-step architecture for operation tracking
- Extracts focused delta fragments from structured operations
- Maintains audit trail and detailed logging
- Provides ReconstructorOutput format for downstream processing
"""

import logging
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

from bill_parser_engine.core.reference_resolver.models import (
    AmendmentOperation, 
    ReconstructionResult,
    OperationType,
    ReconstructorOutput,
    BillChunk
)
from bill_parser_engine.core.reference_resolver.instruction_decomposer import InstructionDecomposer
from bill_parser_engine.core.reference_resolver.operation_applier import (
    OperationApplier, 
    OperationApplicationResult
)
from bill_parser_engine.core.reference_resolver.result_validator import (
    ResultValidator, 
    ValidationResult
)
from bill_parser_engine.core.reference_resolver.cache_manager import SimpleCache, get_mistral_cache
from bill_parser_engine.core.reference_resolver.rate_limiter import rate_limiter

logger = logging.getLogger(__name__)


class LegalAmendmentReconstructor:
    """
    Legal amendment processor using 3-step LLM architecture.
    
    This component processes French legal amendment instructions:
    - Handles format differences between sources
    - Understands position specifications
    - Supports all operation types (REPLACE, DELETE, INSERT, ADD, REWRITE, ABROGATE)
    - Provides error handling and operation tracking
    - Maintains audit trail with detailed result reporting
    - Logs detailed information for verification and debugging
    - Caches final results (no component-level caching)
    """

    def __init__(self, api_key: Optional[str] = None, use_cache: bool = True):
        """
        Initialize the legal amendment reconstructor.

        Args:
            api_key: Mistral API key (defaults to MISTRAL_API_KEY environment variable)
            use_cache: Whether to use centralized Mistral API caching
        """
        # Store API key for component initialization
        self.api_key = api_key or os.getenv("MISTRAL_API_KEY")
        if not self.api_key:
            raise ValueError("Mistral API key is required. Set MISTRAL_API_KEY environment variable or pass api_key parameter.")
        
        # Use centralized Mistral API cache
        self.cache = get_mistral_cache() if use_cache else None
        self.use_cache = use_cache
        
        # Initialize the 3-step pipeline components with shared centralized cache
        self.decomposer = InstructionDecomposer(api_key=api_key, cache=self.cache, use_cache=use_cache)
        self.applier = OperationApplier(api_key=api_key, cache=self.cache, use_cache=use_cache)
        self.validator = ResultValidator(api_key=api_key, cache=self.cache, use_cache=use_cache)
        
        # Store the last detailed reconstruction result for pipeline access
        self.last_detailed_result: Optional[ReconstructionResult] = None
        


    def _initialize_log_file(self):
        # This method is no longer needed as log_file_path is removed from constructor
        pass

    def reconstruct_amendment(
        self,
        original_law_article: str,
        amendment_instruction: str,
        target_article_reference: str,
        chunk_id: str = "unknown"
    ) -> ReconstructorOutput:
        """
        Reconstruct legal text by applying amendment instructions using 3-step pipeline.
        
        Uses the 3-step architecture (InstructionDecomposer → OperationApplier → ResultValidator) 
        and extracts focused output fragments from the structured operations.

        Args:
            original_law_article: The original legal article text
            amendment_instruction: The French amendment instruction text
            target_article_reference: Reference to the target article (e.g., "L. 254-1")
            chunk_id: Unique identifier for the chunk (for logging purposes)

        Returns:
            ReconstructorOutput with separated delta fragments:
            - deleted_or_replaced_text: Text that was removed/replaced
            - newly_inserted_text: Text that was added/inserted
            - intermediate_after_state_text: Complete article after changes

        Raises:
            ValueError: If inputs are invalid
            RuntimeError: If critical system components fail
        """
        # Input validation
        # Note: original_law_article can be empty for INSERT operations
        if not amendment_instruction or not amendment_instruction.strip():
            raise ValueError("Amendment instruction cannot be empty")
        if not target_article_reference or not target_article_reference.strip():
            raise ValueError("Target article reference cannot be empty")
        
        # For INSERT operations, original text is expected to be empty
        is_insert_operation = not original_law_article or not original_law_article.strip()
        if is_insert_operation:
            logger.info("Processing INSERT operation - original text is empty as expected")
            # Use empty string as starting point for INSERT operations
            original_law_article = ""
        

        
        # CACHING: Try to get cached result first (if enabled)
        # Define cache key data for both cache and non-cache scenarios
        cache_key_data = {
            'original_text': original_law_article,
            'amendment_instruction': amendment_instruction,
            'target_article_reference': target_article_reference,
            'chunk_id': chunk_id
        }
        
        # TEMPORARILY DISABLE CACHE TO FORCE FRESH PROCESSING
        # This ensures we get real results and can see the actual processing
        if self.use_cache and self.cache:
            logger.info("Cache temporarily disabled for chunk %s to force fresh processing", chunk_id)
            # Uncomment the following lines to re-enable cache once we verify the processing works
            # cached_result = self.cache.get("legal_amendment_reconstructor_focused", cache_key_data)
            # if cached_result is not None:
            #     logger.info("Cache HIT for chunk %s - creating detailed result", chunk_id)
            #     # For cached results, we need to create a minimal detailed result
            #     # since we don't have the full reconstruction details from the cache
            #     cached_detailed_result = ReconstructionResult(
            #         success=True,  # Assume success for cached results
            #         final_text=cached_result.intermediate_after_state_text,
            #         operations_applied=[],  # We don't have operation details from cache
            #         operations_failed=[],
            #         original_text_length=len(original_law_article),
            #         final_text_length=len(cached_result.intermediate_after_state_text),
            #         processing_time_ms=0,  # No processing time for cached results
            #         validation_warnings=["Result retrieved from cache"]
            #     )
            #     self.last_detailed_result = cached_detailed_result
            #     logger.info("Set last_detailed_result for cached chunk %s", chunk_id)
            #     return cached_result
        
        start_time = time.time()
        operations_applied = []
        operations_failed = []
        current_text = original_law_article
        step_by_step_states = []  # Track text state after each operation
        operations = []  # Initialize for logging
        validation = None  # Initialize for logging
        
        try:
            # STEP 1: Decompose compound instruction into atomic operations
            logger.info("Step 1: Decomposing amendment instruction for chunk %s", chunk_id)
            operations = self.decomposer.parse_instruction(amendment_instruction)
            
            if not operations:
                logger.warning("No operations extracted from instruction")
                
                # Return minimal focused output for failed decomposition
                reconstructor_output = ReconstructorOutput(
                    deleted_or_replaced_text="",
                    newly_inserted_text="",
                    intermediate_after_state_text=original_law_article
                )
                
                # Create detailed result for logging purposes
                detailed_result = ReconstructionResult(
                    success=False,
                    final_text=original_law_article,
                    operations_applied=[],
                    operations_failed=[(None, "No operations could be extracted from instruction")],
                    original_text_length=len(original_law_article),
                    final_text_length=len(original_law_article),
                    processing_time_ms=int((time.time() - start_time) * 1000),
                    validation_warnings=["No operations found in instruction"]
                )
                
                # Store detailed result for pipeline access
                self.last_detailed_result = detailed_result

                # Log the failed reconstruction
                logger.info(
                    "Reconstruction completed - Success: %s, Applied: %d/%d operations, Validation: %s",
                    detailed_result.success, len(detailed_result.operations_applied), len(operations), detailed_result.validation_warnings
                )
                
                # Cache failed result (temporarily disabled)
                if self.use_cache and self.cache:
                    logger.info("Cache setting temporarily disabled for chunk %s", chunk_id)
                    # self.cache.set("legal_amendment_reconstructor_focused", cache_key_data, reconstructor_output)
                
                return reconstructor_output
            

            
            # STEP 2: Apply operations sequentially with individual error isolation
            logger.info("Step 2: Applying %d operations sequentially for chunk %s", len(operations), chunk_id)
            for i, operation in enumerate(operations, 1):
                logger.info("Applying operation %d/%d: %s for chunk %s", i, len(operations), operation.operation_type.value, chunk_id)
                
                try:
                    # Apply single operation
                    result_op = self.applier.apply_single_operation(current_text, operation)
                    
                    if result_op.success:
                        current_text = result_op.modified_text
                        operations_applied.append(operation)
                        step_by_step_states.append(current_text)  # Save state after this operation
                        logger.info("Operation %d succeeded (confidence: %.2f) for chunk %s", i, result_op.confidence, chunk_id)
                    else:
                        operations_failed.append((operation, result_op.error_message or "Unknown error"))
                        step_by_step_states.append(current_text)  # Save unchanged state
                        logger.warning("Operation %d failed: %s for chunk %s", i, result_op.error_message, chunk_id)
                        # Continue with next operation instead of aborting
                        
                except Exception as e:
                    logger.error("Exception during operation %d for chunk %s: %s", i, chunk_id, e)
                    operations_failed.append((operation, f"Exception during application: {e}"))
                    step_by_step_states.append(current_text)  # Save unchanged state
                    # Continue processing remaining operations
            
            # STEP 3: Validate final result
            logger.info("Step 3: Validating final result for chunk %s", chunk_id)
            try:
                validation = self.validator.validate_legal_coherence(
                    original_text=original_law_article,
                    modified_text=current_text,
                    operations=operations_applied
                )
            except Exception as e:
                logger.error("Validation failed for chunk %s: %s", chunk_id, e)
                # Create minimal validation result to avoid blocking the pipeline
                validation = ValidationResult(
                    validation_status="ERRORS",
                    critical_errors=[f"Validation system error: {e}"],
                    major_errors=[],
                    minor_errors=[],
                    suggestions=[],
                    overall_score=0.0,
                    validation_summary="Validation failed due to system error"
                )

            # Calculate processing metrics
            processing_time = int((time.time() - start_time) * 1000)
            # Stricter success semantics: all operations must have applied and validation must be VALID
            success = (
                len(operations_failed) == 0
                and len(operations_applied) == len(operations)
                and validation.validation_status == "VALID"
            )

            # Extract focused output fragments from the 3-step process
            deleted_or_replaced_text = self._extract_deleted_or_replaced_text(operations_applied, original_law_article)
            newly_inserted_text = self._extract_newly_inserted_text(operations_applied)

            # Construct focused output result
            reconstructor_output = ReconstructorOutput(
                deleted_or_replaced_text=deleted_or_replaced_text,
                newly_inserted_text=newly_inserted_text,
                intermediate_after_state_text=current_text
            )

            # Log final status
            logger.info(
                "Reconstruction completed - Success: %s, Applied: %d/%d operations, Validation: %s",
                success, len(operations_applied), len(operations), validation.validation_status
            )

            # Create detailed result for logging purposes
            detailed_result = ReconstructionResult(
                success=success,
                final_text=current_text,
                operations_applied=operations_applied,
                operations_failed=operations_failed,
                original_text_length=len(original_law_article),
                final_text_length=len(current_text),
                processing_time_ms=processing_time,
                validation_warnings=self._extract_validation_warnings(validation)
            )

            # Store detailed result for pipeline access
            self.last_detailed_result = detailed_result

            # Log detailed reconstruction information to file
            logger.info(
                "Reconstruction completed for chunk %s - Success: %s, Applied: %d/%d operations",
                chunk_id, detailed_result.success, len(detailed_result.operations_applied), len(operations)
            )

            # Cache result if enabled (temporarily disabled)
            if self.use_cache and self.cache:
                logger.info("Cache setting temporarily disabled for chunk %s", chunk_id)
                # self.cache.set("legal_amendment_reconstructor_focused", cache_key_data, reconstructor_output)

            return reconstructor_output

        except Exception as e:
            processing_time = int((time.time() - start_time) * 1000)
            logger.error("Critical failure during amendment reconstruction: %s", e)
            
            # Return minimal focused output for critical failure
            reconstructor_output = ReconstructorOutput(
                deleted_or_replaced_text="",
                newly_inserted_text="",
                intermediate_after_state_text=original_law_article
            )
            
            # Create detailed result for logging purposes
            detailed_result = ReconstructionResult(
                success=False,
                final_text=original_law_article,  # Return original text on critical failure
                operations_applied=operations_applied,  # Include any operations that succeeded
                operations_failed=operations_failed + [(None, f"Critical reconstruction failure: {e}")],
                original_text_length=len(original_law_article),
                final_text_length=len(original_law_article),
                processing_time_ms=processing_time,
                validation_warnings=[f"System error prevented full processing: {e}"]
            )
            
            # Store detailed result for pipeline access
            self.last_detailed_result = detailed_result

            # Log the failed reconstruction
            logger.warning(
                "Reconstruction failed for chunk %s - Success: %s, Applied: %d/%d operations",
                chunk_id, detailed_result.success, len(detailed_result.operations_applied), len(operations)
            )
            
            # Cache failed result (temporarily disabled)
            if self.use_cache and self.cache:
                logger.info("Cache setting temporarily disabled for chunk %s", chunk_id)
                # self.cache.set("legal_amendment_reconstructor_focused", cache_key_data, reconstructor_output)
            
            return reconstructor_output

    def _extract_deleted_or_replaced_text(
        self, 
        operations_applied: List[AmendmentOperation], 
        original_text: str
    ) -> str:
        """
        Extract the exact text that was deleted or replaced from applied operations.
        
        Args:
            operations_applied: List of successfully applied operations
            original_text: Original article text for context
            
        Returns:
            Concatenated text that was deleted or replaced
        """
        deleted_fragments = []
        
        for operation in operations_applied:
            if operation.operation_type in [OperationType.DELETE, OperationType.REPLACE, OperationType.REWRITE]:
                if operation.target_text:
                    deleted_fragments.append(operation.target_text)
            elif operation.operation_type == OperationType.ABROGATE:
                # For ABROGATE operations, the deleted text is typically the entire original content
                # or a significant portion based on the position hint
                if operation.target_text:
                    deleted_fragments.append(operation.target_text)
                elif not deleted_fragments and original_text:
                    # If no specific target text, but we have original text, include it
                    deleted_fragments.append(original_text)
        
        return " ".join(deleted_fragments).strip()

    def _extract_newly_inserted_text(self, operations_applied: List[AmendmentOperation]) -> str:
        """
        Extract the exact text that was newly inserted from applied operations.
        
        Args:
            operations_applied: List of successfully applied operations
            
        Returns:
            Concatenated text that was newly inserted (empty string for pure deletions)
        """
        inserted_fragments = []
        
        for operation in operations_applied:
            if operation.operation_type in [
                OperationType.INSERT, 
                OperationType.ADD, 
                OperationType.REPLACE, 
                OperationType.REWRITE
            ]:
                if operation.replacement_text:
                    inserted_fragments.append(operation.replacement_text)
        
        return " ".join(inserted_fragments).strip()

    def _extract_validation_warnings(self, validation: ValidationResult) -> List[str]:
        """Extract and consolidate validation warnings from the validation result."""
        warnings = []
        
        # Add critical errors as high-priority warnings
        if validation.critical_errors:
            warnings.extend([f"CRITICAL: {error}" for error in validation.critical_errors])
        
        # Add major errors as warnings
        if validation.major_errors:
            warnings.extend([f"MAJOR: {error}" for error in validation.major_errors])
        
        # Add minor errors as low-priority warnings
        if validation.minor_errors:
            warnings.extend([f"MINOR: {error}" for error in validation.minor_errors])
        
        # Add suggestions as informational warnings
        if validation.suggestions:
            warnings.extend([f"SUGGESTION: {suggestion}" for suggestion in validation.suggestions])
        
        # Include overall validation summary
        if validation.validation_summary:
            warnings.append(f"SUMMARY: {validation.validation_summary}")
        
        return warnings

    def set_log_file_path(self, log_file_path: str):
        """
        Set a new log file path and mark for lazy initialization.
        
        Args:
            log_file_path: New path for the detailed log file
        """
        # This method is no longer needed as log_file_path is removed from constructor
        pass


    def get_log_file_path(self) -> str:
        """
        Get the current log file path.
        
        Returns:
            String path to the current log file
        """
        # This method is no longer needed as log_file_path is removed from constructor
        return "Log file path not available"

    def clear_all_caches(self) -> dict:
        """
        Clear all cached Mistral API results.

        Returns:
            Dictionary with cache clearing statistics
        """
        if not self.use_cache or not self.cache:
            return {"message": "Caching is disabled"}
        
        # Clear all Mistral API cache entries
        cleared_count = self.cache.clear()
        
        return {
            "mistral_api_cache_cleared": cleared_count,
            "message": f"Cleared {cleared_count} Mistral API cache entries"
        }

    def get_cache_stats(self) -> dict:
        """
        Get cache statistics for all Mistral API calls.

        Returns:
            Dictionary with cache statistics
        """
        if not self.use_cache or not self.cache:
            return {"message": "Caching is disabled"}
        
        return self.cache.get_stats()

    def test_components(self) -> dict:
        """
        Test all pipeline components with minimal operations.
        
        Useful for health checks and integration testing.

        Returns:
            Dictionary with component test results
        """

        
        # Test data
        test_instruction = "les mots : « test » sont remplacés par les mots : « nouveau test »"
        test_original = "Art. L. 254-1. Ceci est un test pour validation."
        
        results = {}
        
        # Test decomposer
        try:
            operations = self.decomposer.parse_instruction(test_instruction)
            results["decomposer"] = {
                "success": True,
                "operations_count": len(operations),
                "message": f"Successfully decomposed into {len(operations)} operations"
            }
        except Exception as e:
            results["decomposer"] = {
                "success": False,
                "error": str(e),
                "message": "Decomposer test failed"
            }
        
        # Test applier (only if decomposer succeeded)
        if results["decomposer"]["success"] and operations:
            try:
                result = self.applier.apply_single_operation(test_original, operations[0])
                results["applier"] = {
                    "success": result.success,
                    "confidence": result.confidence,
                    "message": f"Operation application: {'succeeded' if result.success else 'failed'}"
                }
            except Exception as e:
                results["applier"] = {
                    "success": False,
                    "error": str(e),
                    "message": "Applier test failed"
                }
        else:
            results["applier"] = {
                "success": False,
                "message": "Skipped due to decomposer failure"
            }
        
        # Test validator
        try:
            validation = self.validator.validate_legal_coherence(
                original_text=test_original,
                modified_text=test_original,  # No change for test
                operations=[]
            )
            results["validator"] = {
                "success": validation.validation_status != "ERRORS",
                "status": validation.validation_status,
                "score": validation.overall_score,
                "message": f"Validation status: {validation.validation_status}"
            }
        except Exception as e:
            results["validator"] = {
                "success": False,
                "error": str(e),
                "message": "Validator test failed"
            }
        
        # Overall health
        all_success = all(component.get("success", False) for component in results.values())
        results["overall"] = {
            "success": all_success,
            "message": "All components healthy" if all_success else "Some components have issues"
        }
        

        
        return results

    def reconstruct_text(
        self,
        original_law_article: str,
        amendment_chunk: BillChunk
    ) -> ReconstructorOutput:
        """
        Reconstruct legal text from a BillChunk and return focused output format.

        Takes a BillChunk as input and returns the ReconstructorOutput format 
        with separated text fragments.

        Args:
            original_law_article: The original legal article text
            amendment_chunk: BillChunk containing the amendment instruction

        Returns:
            ReconstructorOutput with separated deleted/replaced text, newly inserted text,
            and full intermediate state text

        Raises:
            ValueError: If inputs are invalid
            RuntimeError: If critical system components fail
        """
        # Extract target article reference from the chunk (for logging)
        if amendment_chunk.target_article and amendment_chunk.target_article.article:
            if amendment_chunk.target_article.code:
                target_article_reference = f"{amendment_chunk.target_article.code}::{amendment_chunk.target_article.article}"
            else:
                target_article_reference = amendment_chunk.target_article.article
        else:
            target_article_reference = "unknown"
        
        # Implementation mirrors reconstruct_amendment, with enrichment using chunk context before apply
        if not amendment_chunk.text or not amendment_chunk.text.strip():
            raise ValueError("Amendment instruction cannot be empty")

        # INSERT may have empty base; keep as is
        is_insert_operation = not original_law_article or not original_law_article.strip()
        if is_insert_operation:
            original_law_article = ""

        start_time = time.time()
        operations_applied: List[AmendmentOperation] = []
        operations_failed: List[Tuple[AmendmentOperation, str]] = []
        current_text = original_law_article

        try:
            # Step 1: Decompose
            operations = self.decomposer.parse_instruction(amendment_chunk.text)
            if not operations:
                reconstructor_output = ReconstructorOutput("", "", original_law_article)
                detailed_result = ReconstructionResult(
                    success=False,
                    final_text=original_law_article,
                    operations_applied=[],
                    operations_failed=[(None, "No operations could be extracted from instruction")],
                    original_text_length=len(original_law_article),
                    final_text_length=len(original_law_article),
                    processing_time_ms=int((time.time() - start_time) * 1000),
                    validation_warnings=["No operations found in instruction"],
                )
                self.last_detailed_result = detailed_result
                return reconstructor_output

            # Enrich operations with chunk scope (e.g., numbered point context)
            operations = self._enrich_operations_with_chunk_context(operations, amendment_chunk)

            # Step 2: Apply sequentially
            for operation in operations:
                result_op = self.applier.apply_single_operation(current_text, operation)
                if result_op.success:
                    current_text = result_op.modified_text
                    operations_applied.append(operation)
                else:
                    operations_failed.append((operation, result_op.error_message or "Unknown error"))

            # Step 3: Validate
            validation = self.validator.validate_legal_coherence(
                original_text=original_law_article, modified_text=current_text, operations=operations_applied
            )

            processing_time = int((time.time() - start_time) * 1000)
            success = (
                len(operations_failed) == 0
                and len(operations_applied) == len(operations)
                and validation.validation_status == "VALID"
            )

            deleted_or_replaced_text = self._extract_deleted_or_replaced_text(operations_applied, original_law_article)
            newly_inserted_text = self._extract_newly_inserted_text(operations_applied)

            self.last_detailed_result = ReconstructionResult(
                success=success,
                final_text=current_text,
                operations_applied=operations_applied,
                operations_failed=operations_failed,
                original_text_length=len(original_law_article),
                final_text_length=len(current_text),
                processing_time_ms=processing_time,
                validation_warnings=self._extract_validation_warnings(validation),
            )

            return ReconstructorOutput(
                deleted_or_replaced_text=deleted_or_replaced_text,
                newly_inserted_text=newly_inserted_text,
                intermediate_after_state_text=current_text,
            )

        except Exception as e:
            processing_time = int((time.time() - start_time) * 1000)
            self.last_detailed_result = ReconstructionResult(
                success=False,
                final_text=original_law_article,
                operations_applied=operations_applied,
                operations_failed=operations_failed + [(None, f"Critical reconstruction failure: {e}")],
                original_text_length=len(original_law_article),
                final_text_length=len(original_law_article),
                processing_time_ms=processing_time,
                validation_warnings=[f"System error prevented full processing: {e}"],
            )
            return ReconstructorOutput("", "", original_law_article)

    def _enrich_operations_with_chunk_context(
        self, operations: List[AmendmentOperation], chunk: BillChunk
    ) -> List[AmendmentOperation]:
        """Augment operations with structured scope hints from chunk context (e.g., numbered point).
        If chunk.numbered_point_introductory_phrase contains "Le N° de l'article ...", add point=N to position_hint JSON.
        """
        point_num: Optional[int] = None
        intro = chunk.numbered_point_introductory_phrase or ""
        import re as _re
        m = _re.search(r"(?i)\ble\s+(\d+)°\b", intro)
        if m:
            try:
                point_num = int(m.group(1))
            except Exception:
                point_num = None

        enriched: List[AmendmentOperation] = []
        for op in operations:
            hint_obj: Dict[str, Any] = {}
            # Load existing JSON hint if any
            try:
                if op.position_hint:
                    parsed = json.loads(op.position_hint)
                    if isinstance(parsed, dict):
                        hint_obj = parsed
            except Exception:
                hint_obj = {}
            # Attach point scope if available
            if point_num is not None:
                hint_obj.setdefault("point", point_num)
            # Serialize back
            if hint_obj:
                op.position_hint = json.dumps(hint_obj, ensure_ascii=False)
            enriched.append(op)
        return enriched

 