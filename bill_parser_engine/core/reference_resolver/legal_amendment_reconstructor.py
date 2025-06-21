"""
LegalAmendmentReconstructor - Main orchestrator for legal amendment text reconstruction.

This is the central component implementing the clean slate approach for legal amendment
processing. It orchestrates the 3-step LLM-based architecture:
1. InstructionDecomposer - Parse compound instructions into atomic operations
2. OperationApplier - Apply each operation sequentially using LLM intelligence
3. ResultValidator - Validate final result for legal coherence and formatting
"""

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from bill_parser_engine.core.reference_resolver.models import (
    AmendmentOperation, 
    ReconstructionResult,
    OperationType
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
from bill_parser_engine.core.reference_resolver.cache_manager import SimpleCache

logger = logging.getLogger(__name__)


class LegalAmendmentReconstructor:
    """
    Clean, purpose-built legal amendment processor using 3-step LLM architecture.
    
    This component provides a complete solution for French legal amendment processing:
    - Robust handling of format differences between sources
    - Complex position specification understanding
    - All 6 operation types (REPLACE, DELETE, INSERT, ADD, REWRITE, ABROGATE)
    - Comprehensive error handling and operation tracking
    - Transparent audit trail with detailed result reporting
    - Detailed logging to file for verification and debugging
    """

    def __init__(self, api_key: Optional[str] = None, use_cache: bool = True, log_file_path: Optional[str] = None):
        """
        Initialize the legal amendment reconstructor.

        Args:
            api_key: Mistral API key (defaults to MISTRAL_API_KEY environment variable)
            use_cache: Whether to use caching across all components
            log_file_path: Path to detailed log file (defaults to 'reconstruction_log.txt')
        """
        # Initialize shared cache
        self.cache = SimpleCache() if use_cache else None
        
        # Initialize the 3-step pipeline components
        self.decomposer = InstructionDecomposer(
            api_key=api_key, 
            cache=self.cache, 
            use_cache=use_cache
        )
        self.applier = OperationApplier(
            api_key=api_key, 
            cache=self.cache, 
            use_cache=use_cache
        )
        self.validator = ResultValidator(
            api_key=api_key, 
            cache=self.cache, 
            use_cache=use_cache
        )
        
        self.use_cache = use_cache
        
        # Setup detailed logging to file
        self.log_file_path = Path(log_file_path) if log_file_path else Path("reconstruction_log.txt")
        self._initialize_log_file()
        
        logger.info("LegalAmendmentReconstructor initialized with caching: %s, log file: %s", 
                   "enabled" if use_cache else "disabled", self.log_file_path)

    def _initialize_log_file(self):
        """Initialize the detailed log file with header information."""
        try:
            with open(self.log_file_path, 'w', encoding='utf-8') as f:
                f.write("=" * 100 + "\n")
                f.write("LEGAL AMENDMENT RECONSTRUCTOR - DETAILED LOG\n")
                f.write("=" * 100 + "\n")
                f.write(f"Log initialized at: {datetime.now().isoformat()}\n")
                f.write(f"Log file: {self.log_file_path.absolute()}\n")
                f.write("=" * 100 + "\n\n")
        except Exception as e:
            logger.warning("Failed to initialize log file %s: %s", self.log_file_path, e)

    def log_reconstruction_details(
        self,
        chunk_id: str,
        target_article_reference: str,
        original_law_article: str,
        amendment_instruction: str,
        operations: List[AmendmentOperation],
        result: ReconstructionResult,
        validation: Optional[ValidationResult] = None,
        step_by_step_states: Optional[List[str]] = None
    ):
        """
        Write comprehensive reconstruction details to the log file.

        Args:
            chunk_id: Unique identifier for the chunk being processed
            target_article_reference: Reference to the target article
            original_law_article: Original legal text before modification
            amendment_instruction: The amendment instruction text
            operations: List of atomic operations that were decomposed
            result: Final reconstruction result
            validation: Validation result (if available)
            step_by_step_states: List of text states after each operation (if available)
        """
        try:
            with open(self.log_file_path, 'a', encoding='utf-8') as f:
                # Header for this reconstruction
                f.write("\n" + "=" * 80 + "\n")
                f.write(f"RECONSTRUCTION ENTRY - {datetime.now().isoformat()}\n")
                f.write("=" * 80 + "\n")
                
                # Basic information
                f.write(f"CHUNK ID: {chunk_id}\n")
                f.write(f"TARGET ARTICLE: {target_article_reference}\n")
                f.write(f"SUCCESS: {result.success}\n")
                f.write(f"PROCESSING TIME: {result.processing_time_ms}ms\n")
                f.write(f"OPERATIONS APPLIED: {len(result.operations_applied)}/{len(operations)}\n")
                f.write(f"OPERATIONS FAILED: {len(result.operations_failed)}\n")
                f.write("\n")
                
                # Original legal text
                f.write("-" * 40 + " ORIGINAL LEGAL TEXT " + "-" * 40 + "\n")
                f.write(f"Length: {len(original_law_article)} characters\n")
                f.write(f"Text:\n{original_law_article}\n")
                f.write("\n")
                
                # Amendment instruction
                f.write("-" * 40 + " AMENDMENT INSTRUCTION " + "-" * 39 + "\n")
                f.write(f"Length: {len(amendment_instruction)} characters\n")
                f.write(f"Text:\n{amendment_instruction}\n")
                f.write("\n")
                
                # Decomposed operations
                f.write("-" * 40 + " DECOMPOSED OPERATIONS " + "-" * 39 + "\n")
                f.write(f"Total operations: {len(operations)}\n")
                for i, op in enumerate(operations, 1):
                    f.write(f"\nOperation {i}:\n")
                    f.write(f"  Type: {op.operation_type.value}\n")
                    f.write(f"  Position: {op.position_hint}\n")
                    f.write(f"  Target Text: {op.target_text or 'N/A'}\n")
                    f.write(f"  Replacement Text: {op.replacement_text or 'N/A'}\n")
                    f.write(f"  Sequence Order: {op.sequence_order}\n")
                    f.write(f"  Confidence: {op.confidence_score:.3f}\n")
                f.write("\n")
                
                # Step-by-step application (if available)
                if step_by_step_states:
                    f.write("-" * 40 + " STEP-BY-STEP APPLICATION " + "-" * 33 + "\n")
                    f.write("State 0 (Original):\n")
                    f.write(f"{original_law_article}\n\n")
                    
                    for i, state in enumerate(step_by_step_states, 1):
                        f.write(f"State {i} (After Operation {i}):\n")
                        f.write(f"{state}\n\n")
                
                # Final result
                f.write("-" * 40 + " FINAL RECONSTRUCTED TEXT " + "-" * 35 + "\n")
                f.write(f"Length: {len(result.final_text)} characters\n")
                f.write(f"Length change: {result.final_text_length - result.original_text_length:+d} characters\n")
                f.write(f"Text:\n{result.final_text}\n")
                f.write("\n")
                
                # Before/After comparison
                f.write("-" * 40 + " BEFORE/AFTER COMPARISON " + "-" * 36 + "\n")
                f.write("BEFORE:\n")
                f.write(f"{original_law_article}\n")
                f.write("\nAFTER:\n")
                f.write(f"{result.final_text}\n")
                f.write("\n")
                
                # Operations results
                if result.operations_applied:
                    f.write("-" * 40 + " SUCCESSFUL OPERATIONS " + "-" * 39 + "\n")
                    for i, op in enumerate(result.operations_applied, 1):
                        f.write(f"{i}. {op.operation_type.value} - {op.position_hint}\n")
                    f.write("\n")
                
                if result.operations_failed:
                    f.write("-" * 40 + " FAILED OPERATIONS " + "-" * 43 + "\n")
                    for i, (op, error) in enumerate(result.operations_failed, 1):
                        if op:
                            f.write(f"{i}. {op.operation_type.value} - {op.position_hint}\n")
                            f.write(f"   Error: {error}\n")
                        else:
                            f.write(f"{i}. System Error: {error}\n")
                    f.write("\n")
                
                # Validation results
                if validation:
                    f.write("-" * 40 + " VALIDATION RESULTS " + "-" * 42 + "\n")
                    f.write(f"Status: {validation.validation_status}\n")
                    f.write(f"Overall Score: {validation.overall_score:.3f}\n")
                    f.write(f"Summary: {validation.validation_summary}\n")
                    
                    if validation.critical_errors:
                        f.write(f"\nCritical Errors ({len(validation.critical_errors)}):\n")
                        for error in validation.critical_errors:
                            f.write(f"  - {error}\n")
                    
                    if validation.major_errors:
                        f.write(f"\nMajor Errors ({len(validation.major_errors)}):\n")
                        for error in validation.major_errors:
                            f.write(f"  - {error}\n")
                    
                    if validation.minor_errors:
                        f.write(f"\nMinor Errors ({len(validation.minor_errors)}):\n")
                        for error in validation.minor_errors:
                            f.write(f"  - {error}\n")
                    
                    if validation.suggestions:
                        f.write(f"\nSuggestions ({len(validation.suggestions)}):\n")
                        for suggestion in validation.suggestions:
                            f.write(f"  - {suggestion}\n")
                    f.write("\n")
                
                # Validation warnings from result
                if result.validation_warnings:
                    f.write("-" * 40 + " VALIDATION WARNINGS " + "-" * 41 + "\n")
                    for warning in result.validation_warnings:
                        f.write(f"  - {warning}\n")
                    f.write("\n")
                
                # Summary statistics
                f.write("-" * 40 + " SUMMARY STATISTICS " + "-" * 42 + "\n")
                f.write(f"Original text length: {result.original_text_length} chars\n")
                f.write(f"Final text length: {result.final_text_length} chars\n")
                f.write(f"Length change: {result.final_text_length - result.original_text_length:+d} chars\n")
                f.write(f"Operations attempted: {len(operations)}\n")
                f.write(f"Operations successful: {len(result.operations_applied)}\n")
                f.write(f"Operations failed: {len(result.operations_failed)}\n")
                f.write(f"Success rate: {len(result.operations_applied)/len(operations)*100:.1f}%\n" if operations else "Success rate: N/A\n")
                f.write(f"Processing time: {result.processing_time_ms}ms\n")
                f.write(f"Overall success: {result.success}\n")
                
                f.write("\n" + "=" * 80 + "\n")
                
        except Exception as e:
            logger.error("Failed to write reconstruction details to log file: %s", e)

    def reconstruct_amendment(
        self,
        original_law_article: str,
        amendment_instruction: str,
        target_article_reference: str,
        chunk_id: str = "unknown"
    ) -> ReconstructionResult:
        """
        Reconstruct legal text by applying amendment instructions using 3-step pipeline.

        Args:
            original_law_article: The original legal article text
            amendment_instruction: The French amendment instruction text
            target_article_reference: Reference to the target article (e.g., "L. 254-1")
            chunk_id: Unique identifier for the chunk (for logging purposes)

        Returns:
            ReconstructionResult with success status and reconstructed text

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
        
        logger.info(
            "Starting amendment reconstruction for article %s - instruction: %.100s...",
            target_article_reference, amendment_instruction
        )
        
        start_time = time.time()
        operations_applied = []
        operations_failed = []
        current_text = original_law_article
        step_by_step_states = []  # Track text state after each operation
        operations = []  # Initialize for logging
        validation = None  # Initialize for logging
        
        try:
            # STEP 1: Decompose compound instruction into atomic operations
            logger.debug("Step 1: Decomposing amendment instruction")
            operations = self.decomposer.parse_instruction(amendment_instruction)
            
            if not operations:
                logger.warning("No operations extracted from instruction")
                result = ReconstructionResult(
                    success=False,
                    final_text=original_law_article,
                    operations_applied=[],
                    operations_failed=[(None, "No operations could be extracted from instruction")],
                    original_text_length=len(original_law_article),
                    final_text_length=len(original_law_article),
                    processing_time_ms=int((time.time() - start_time) * 1000),
                    validation_warnings=["No operations found in instruction"]
                )
                
                # Log the failed reconstruction
                self.log_reconstruction_details(
                    chunk_id=chunk_id,
                    target_article_reference=target_article_reference,
                    original_law_article=original_law_article,
                    amendment_instruction=amendment_instruction,
                    operations=operations,
                    result=result,
                    validation=validation,
                    step_by_step_states=step_by_step_states
                )
                
                return result
            
            logger.info("Decomposed into %d atomic operations", len(operations))
            
            # STEP 2: Apply operations sequentially with individual error isolation
            logger.debug("Step 2: Applying operations sequentially")
            for i, operation in enumerate(operations, 1):
                logger.debug("Applying operation %d/%d: %s", i, len(operations), operation.operation_type.value)
                
                try:
                    # Apply single operation
                    result_op = self.applier.apply_single_operation(current_text, operation)
                    
                    if result_op.success:
                        current_text = result_op.modified_text
                        operations_applied.append(operation)
                        step_by_step_states.append(current_text)  # Save state after this operation
                        logger.debug("Operation %d succeeded (confidence: %.2f)", i, result_op.confidence)
                    else:
                        operations_failed.append((operation, result_op.error_message or "Unknown error"))
                        step_by_step_states.append(current_text)  # Save unchanged state
                        logger.warning("Operation %d failed: %s", i, result_op.error_message)
                        # Continue with next operation instead of aborting
                        
                except Exception as e:
                    logger.error("Exception during operation %d: %s", i, e)
                    operations_failed.append((operation, f"Exception during application: {e}"))
                    step_by_step_states.append(current_text)  # Save unchanged state
                    # Continue processing remaining operations
            
            # STEP 3: Validate final result
            logger.debug("Step 3: Validating final result")
            try:
                validation = self.validator.validate_legal_coherence(
                    original_text=original_law_article,
                    modified_text=current_text,
                    operations=operations_applied
                )
            except Exception as e:
                logger.error("Validation failed: %s", e)
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
            success = len(operations_failed) == 0 and validation.validation_status != "ERRORS"

            # Construct comprehensive result
            result = ReconstructionResult(
                success=success,
                final_text=current_text,
                operations_applied=operations_applied,
                operations_failed=operations_failed,
                original_text_length=len(original_law_article),
                final_text_length=len(current_text),
                processing_time_ms=processing_time,
                validation_warnings=self._extract_validation_warnings(validation)
            )

            # Log final status
            logger.info(
                "Reconstruction completed - Success: %s, Applied: %d/%d operations, "
                "Validation: %s (processing time: %dms)",
                success, len(operations_applied), len(operations), 
                validation.validation_status, processing_time
            )

            # Log detailed reconstruction information to file
            self.log_reconstruction_details(
                chunk_id=chunk_id,
                target_article_reference=target_article_reference,
                original_law_article=original_law_article,
                amendment_instruction=amendment_instruction,
                operations=operations,
                result=result,
                validation=validation,
                step_by_step_states=step_by_step_states
            )

            return result

        except Exception as e:
            processing_time = int((time.time() - start_time) * 1000)
            logger.error("Critical failure during amendment reconstruction: %s", e)
            
            # Return failure result with diagnostic information
            result = ReconstructionResult(
                success=False,
                final_text=original_law_article,  # Return original text on critical failure
                operations_applied=operations_applied,  # Include any operations that succeeded
                operations_failed=operations_failed + [(None, f"Critical reconstruction failure: {e}")],
                original_text_length=len(original_law_article),
                final_text_length=len(original_law_article),
                processing_time_ms=processing_time,
                validation_warnings=[f"System error prevented full processing: {e}"]
            )
            
            # Log the failed reconstruction
            self.log_reconstruction_details(
                chunk_id=chunk_id,
                target_article_reference=target_article_reference,
                original_law_article=original_law_article,
                amendment_instruction=amendment_instruction,
                operations=operations,
                result=result,
                validation=validation,
                step_by_step_states=step_by_step_states
            )
            
            return result

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
        Set a new log file path and reinitialize the log file.
        
        Args:
            log_file_path: New path for the detailed log file
        """
        self.log_file_path = Path(log_file_path)
        self._initialize_log_file()
        logger.info("Log file path updated to: %s", self.log_file_path)

    def get_log_file_path(self) -> str:
        """
        Get the current log file path.
        
        Returns:
            String path to the current log file
        """
        return str(self.log_file_path.absolute())

    def clear_all_caches(self) -> dict:
        """
        Clear all caches across the 3-step pipeline.

        Returns:
            Dictionary with cache clearing statistics for each component
        """
        if not self.use_cache:
            return {"message": "Caching is disabled"}
        
        stats = {
            "decomposer_cleared": self.decomposer.clear_cache(),
            "applier_cleared": self.applier.clear_cache(),
            "validator_cleared": self.validator.clear_cache()
        }
        
        total_cleared = sum(stats.values())
        logger.info("Cleared %d total cache entries across all components", total_cleared)
        
        return stats

    def get_cache_stats(self) -> dict:
        """
        Get comprehensive cache statistics for all components.

        Returns:
            Dictionary with detailed cache statistics for the entire pipeline
        """
        if not self.use_cache:
            return {"message": "Caching is disabled"}
        
        return {
            "decomposer": self.decomposer.get_cache_stats(),
            "applier": self.applier.get_cache_stats(),
            "validator": self.validator.get_cache_stats()
        }

    def test_components(self) -> dict:
        """
        Test all pipeline components with minimal operations.
        
        Useful for health checks and integration testing.

        Returns:
            Dictionary with component test results
        """
        logger.info("Testing LegalAmendmentReconstructor components")
        
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
        
        logger.info("Component testing completed - Overall health: %s", all_success)
        
        return results 