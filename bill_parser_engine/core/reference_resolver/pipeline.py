"""
Master pipeline for processing legislative bills through the reference resolver.

This module provides a comprehensive pipeline that orchestrates the processing
of legislative bills through multiple stages, managing data flow and providing
detailed analysis and reporting.
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from bill_parser_engine.core.reference_resolver.bill_splitter import BillSplitter
from bill_parser_engine.core.reference_resolver.target_identifier import TargetArticleIdentifier
from bill_parser_engine.core.reference_resolver.original_text_retriever import OriginalTextRetriever
from bill_parser_engine.core.reference_resolver.legal_amendment_reconstructor import LegalAmendmentReconstructor
from bill_parser_engine.core.reference_resolver.reference_locator import ReferenceLocator
from bill_parser_engine.core.reference_resolver.reference_object_linker import ReferenceObjectLinker
from bill_parser_engine.core.reference_resolver.reference_resolver import ReferenceResolver
from bill_parser_engine.core.reference_resolver.cache_manager import get_mistral_cache
from bill_parser_engine.core.reference_resolver.legal_state_synthesizer import LegalStateSynthesizer

from bill_parser_engine.core.reference_resolver.models import (
    BillChunk, 
    TargetArticle, 
    TargetOperationType,
    ReconstructorOutput,
    LocatedReference,
    ReferenceSourceType,
    LinkedReference,
    ResolutionResult
)

logger = logging.getLogger(__name__)


class BillProcessingPipeline:
    """
    Master pipeline for processing legislative bills through the reference resolver.
    
    This class orchestrates the complete processing workflow from raw legislative text
    to reference object linking results, managing data flow between components and providing
    comprehensive analysis and reporting.
    
    Current pipeline steps:
    1. BillSplitter - breaks the bill into atomic chunks
    2. TargetArticleIdentifier - identifies target articles for each chunk
    3. OriginalTextRetriever - fetches current legal text for unique target articles
    4. LegalAmendmentReconstructor - applies amendment instructions using 3-step LLM architecture (InstructionDecomposer → OperationApplier → ResultValidator)
    5. ReferenceLocator - locates normative references in delta fragments using focused scanning (30x+ performance improvement)
    6. ReferenceObjectLinker - links references to grammatical objects using context-aware analysis with resolution question generation
    7. ReferenceResolver - resolves linked references through question-guided content extraction with EU file access optimization
    """

    def __init__(self, use_cache: bool = True):
        """
        Initialize the pipeline with all required components.
        
        Args:
            use_cache: Whether to enable centralized Mistral API caching
        
        Note: All components use the same centralized Mistral API cache to avoid
        redundant API calls and respect rate limits.
        """
        
        # Initialize all pipeline components with centralized cache
        self.bill_splitter = BillSplitter()
        self.target_identifier = TargetArticleIdentifier(use_cache=use_cache)
        self.original_text_retriever = OriginalTextRetriever(use_cache=use_cache)
        # Historical retrieval disabled; French codes use local store only
        self.text_reconstructor = LegalAmendmentReconstructor(use_cache=use_cache)
        self.reference_locator = ReferenceLocator(use_cache=use_cache)
        self.reference_object_linker = ReferenceObjectLinker(use_cache=use_cache)
        self.reference_resolver = ReferenceResolver(use_cache=use_cache)
        self.legal_state_synthesizer = LegalStateSynthesizer()
        
        # Pipeline state and results
        self.legislative_text: Optional[str] = None
        self.chunks: List[BillChunk] = []
        self.target_results: List[Dict] = []
        self.retrieval_results: List[Dict] = []
        self.reconstruction_results: List[Dict] = []
        self.reference_location_results: List[Dict] = []
        self.reference_linking_results: List[Dict] = []
        self.reference_resolution_results: List[Dict] = []
        self.legal_state_results: List[Dict] = []
        
        # Analysis results
        self.target_analysis: Dict = {}
        self.retrieval_analysis: Dict = {}
        self.reconstruction_analysis: Dict = {}
        self.reference_location_analysis: Dict = {}
        self.reference_linking_analysis: Dict = {}
        self.reference_resolution_analysis: Dict = {}
        self.legal_state_analysis: Dict = {}
        

    def load_legislative_text(self, text: str) -> None:
        """
        Load legislative text into the pipeline.

        Args:
            text: The raw legislative text to process
        """
        self.legislative_text = text
        logger.info("Loaded legislative text: %d characters", len(text))

    def load_legislative_text_from_file(self, file_path: Path) -> None:
        """
        Load legislative text from a file.

        Args:
            file_path: Path to the legislative text file

        Raises:
            FileNotFoundError: If the file doesn't exist
            Exception: If there's an error reading the file
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                text = f.read()
            self.load_legislative_text(text)
            logger.info("Loaded legislative text from file: %s", file_path)
        except FileNotFoundError:
            logger.error("Legislative bill file not found at %s", file_path)
            raise
        except Exception as e:
            logger.error("Error reading legislative bill file: %s", e)
            raise

    def step_1_split_bill(self) -> List[BillChunk]:
        """
        Step 1: Split the legislative bill into atomic chunks.

        Returns:
            List of BillChunk objects

        Raises:
            ValueError: If no legislative text has been loaded
        """
        if not self.legislative_text:
            raise ValueError("No legislative text loaded. Call load_legislative_text() first.")

        logger.info("Step 1: Splitting legislative bill into chunks...")
        
        try:
            self.chunks = self.bill_splitter.split(self.legislative_text)
            logger.info("Split into %d chunks", len(self.chunks))
            return self.chunks
            
        except Exception as e:
            raise

    def step_2_identify_target_articles(self) -> List[Dict]:
        """
        Step 2: Process chunks through TargetArticleIdentifier.

        Returns:
            List of target identification results

        Raises:
            ValueError: If chunks haven't been created yet
        """
        if not self.chunks:
            raise ValueError("No chunks available. Call step_1_split_bill() first.")

        logger.info("Step 2: Identifying target articles for %d chunks...", len(self.chunks))
        
        results = []
        for i, chunk in enumerate(self.chunks, 1):
            logger.info("Processing chunk %d/%d: %s", i, len(self.chunks), chunk.chunk_id[:50])
            
            try:
                target_article = self.target_identifier.identify(chunk)

                # Skip chunks with pure versioning metadata or suppressed/no-op (OTHER operation type)
                if target_article.operation_type == TargetOperationType.OTHER:
                    logger.debug("Skipping chunk with OTHER (likely versioning/no-op): %s", chunk.chunk_id)
                    continue

                # Confidence gating and malformed guard: require code+article and minimum confidence
                if (not target_article.code) or (not target_article.article) or (target_article.confidence is not None and target_article.confidence < 0.6):
                    logger.warning(
                        "Identification gated: code/article missing or low confidence for chunk %s (code=%s, article=%s, confidence=%s)",
                        chunk.chunk_id, target_article.code, target_article.article, target_article.confidence
                    )
                    continue
                
                result_entry = {
                    "chunk_id": chunk.chunk_id,
                    "chunk_text_preview": chunk.text[:100] + "..." if len(chunk.text) > 100 else chunk.text,
                    "hierarchy_path": chunk.hierarchy_path,
                    "target_article": {
                        "operation_type": target_article.operation_type.value if target_article.operation_type else None,
                        "code": target_article.code,
                        "article": target_article.article,
                        "full_citation": f"{target_article.code}::{target_article.article}" if target_article.code and target_article.article else target_article.article,
                        "confidence": (target_article.confidence if target_article.confidence is not None else 1.0),
                        "raw_text": chunk.text[:50] + "..." if len(chunk.text) > 50 else chunk.text
                    }
                }
                
                results.append(result_entry)
                
                if target_article.article:
                    logger.debug("Identified: %s (%s)", target_article.article, target_article.operation_type.value)
                else:
                    logger.debug("No specific article identified (%s)", target_article.operation_type.value)
                    
            except Exception as e:
                logger.error("Error processing chunk %s: %s", chunk.chunk_id, e)
                
                result_entry = {
                    "chunk_id": chunk.chunk_id,
                    "chunk_text_preview": chunk.text[:100] + "..." if len(chunk.text) > 100 else chunk.text,
                    "hierarchy_path": chunk.hierarchy_path,
                    "target_article": None,
                    "error": str(e)
                }
                results.append(result_entry)

        self.target_results = results
        self.target_analysis = self._analyze_target_results(results)
        logger.info("Target identification complete: %d unique articles identified", 
                   self.target_analysis['total_unique_articles'])
        
        return results

    def step_3_retrieve_original_texts(self) -> List[Dict]:
        """
        Step 3: Retrieve original text for unique target articles.

        Returns:
            List of retrieval results

        Raises:
            ValueError: If target identification hasn't been done yet
        """
        if not self.target_results:
            raise ValueError("No target results available. Call step_2_identify_target_articles() first.")

        logger.info("Step 3: Retrieving original text for target articles...")
        
        unique_articles_data = self.target_analysis["unique_articles_data"]
        retrieval_results = []
        
        for i, (article_key, article_info) in enumerate(unique_articles_data.items(), 1):
            code = article_info["code"]
            article = article_info["article"]
            operation_type = article_info["operation_type"]
            
            logger.debug("Retrieving %d/%d: %s (%s)", i, len(unique_articles_data), article_key, operation_type)
            
            try:
                # Handle INSERT operations - skip retrieval as articles don't exist yet
                if operation_type == "INSERT":
                    result_entry = {
                        "article_key": article_key,
                        "code": code,
                        "article": article,
                        "operation_type": operation_type,
                        "original_text": "",
                        "text_length": 0,
                        "retrieval_metadata": {"source": "insert_operation", "success": True, "note": "Empty text for INSERT operation"},
                        "retrieved_at": datetime.now().isoformat()
                    }
                    retrieval_results.append(result_entry)
                    logger.debug("Handled INSERT operation")
                    continue
                
                # Filter out exotic formats (titles, books, etc.)
                if self._is_exotic_format(article):
                    result_entry = {
                        "article_key": article_key,
                        "code": code,
                        "article": article,
                        "operation_type": operation_type,
                        "original_text": "",
                        "text_length": 0,
                        "retrieval_metadata": {"source": "exotic_format", "success": False, "note": "Exotic format skipped"},
                        "retrieved_at": datetime.now().isoformat()
                    }
                    retrieval_results.append(result_entry)
                    logger.debug("Skipped exotic format")
                    continue
                
                if not code or not article:
                    logger.warning("Skipping malformed article: code=%s, article=%s", code, article)
                    continue
                
                # Fetch the original text (historical if configured)
                original_text, metadata = self.original_text_retriever.fetch_article_text(
                    code, article
                )
                
                # Hierarchical fallback is now handled inside OriginalTextRetriever
                
                result_entry = {
                    "article_key": article_key,
                    "code": code,
                    "article": article,
                    "operation_type": operation_type,
                    "original_text": original_text,
                    "text_length": len(original_text),
                    "retrieval_metadata": metadata,
                    "retrieved_at": datetime.now().isoformat()
                }
                
                retrieval_results.append(result_entry)
                
                if metadata.get("success", False):
                    logger.debug("Retrieved from %s (%d chars)", metadata.get("source", "unknown"), len(original_text))
                else:
                    logger.warning("Failed to retrieve %s: %s", article_key, metadata.get("error", "Unknown error"))
                    
            except Exception as e:
                logger.error("Error retrieving %s: %s", article_key, e)
                result_entry = {
                    "article_key": article_key,
                    "code": code,
                    "article": article,
                    "operation_type": operation_type,
                    "original_text": "",
                    "text_length": 0,
                    "retrieval_metadata": {"source": "none", "success": False, "error": str(e)},
                    "retrieved_at": datetime.now().isoformat(),
                    "error": str(e)
                }
                retrieval_results.append(result_entry)

        self.retrieval_results = retrieval_results
        self.retrieval_analysis = self._analyze_retrieval_results(retrieval_results)
        logger.info("Original text retrieval complete: %d/%d successful retrievals", 
                   self.retrieval_analysis['successful_retrievals'],
                   self.retrieval_analysis['total_articles'])
        
        return retrieval_results

    def step_4_reconstruct_texts(self) -> List[Dict]:
        """
        Step 4: Apply text reconstruction to produce before/after fragments.

        Returns:
            List of reconstruction results

        Raises:
            ValueError: If previous steps haven't been completed
        """
        if not self.target_results or not self.retrieval_results:
            raise ValueError("Target identification and original text retrieval must be completed first.")

        logger.info("Step 4: Applying text reconstruction...")
        
        # Only process chunks that were successfully processed in step 2 (have target articles)
        target_chunk_ids = {result["chunk_id"] for result in self.target_results if result.get("target_article") and not result.get("error")}
        
        # Filter chunks to only those with identified target articles
        chunks_to_process = [chunk for chunk in self.chunks if chunk.chunk_id in target_chunk_ids]
        
        logger.info("Processing %d chunks with identified target articles (skipping %d chunks with pure versioning metadata or errors)", 
                   len(chunks_to_process), len(self.chunks) - len(chunks_to_process))
        
        # Enrich filtered chunks with target articles
        enriched_chunks = self._enrich_chunks_with_target_articles(chunks_to_process)
        
        # Create lookup for original texts
        original_texts_lookup = self._create_original_texts_lookup()
        
        reconstruction_results = []
        
        for i, chunk in enumerate(enriched_chunks, 1):
            logger.debug("Processing chunk %d/%d: %s", i, len(enriched_chunks), chunk.chunk_id[:50])
            
            # Capture start time for performance tracking
            chunk_start_time = time.time()
            
            try:
                # Skip chunks without target articles or with errors
                if not chunk.target_article:
                    
                    result_entry = {
                        "chunk_id": chunk.chunk_id,
                        "chunk_text_preview": chunk.text[:100] + "..." if len(chunk.text) > 100 else chunk.text,
                        "hierarchy_path": chunk.hierarchy_path,
                        "target_article": None,
                        "reconstruction_result": None,
                        "error": "No target article identified"
                    }
                    reconstruction_results.append(result_entry)
                    continue
                
                # Get the original text for this chunk's target article
                article_key = self._build_article_key(chunk.target_article.code, chunk.target_article.article)
                original_text = original_texts_lookup.get(article_key, "")
                
                if not original_text and chunk.target_article.operation_type.value != "INSERT":
                    
                    result_entry = {
                        "chunk_id": chunk.chunk_id,
                        "chunk_text_preview": chunk.text[:100] + "..." if len(chunk.text) > 100 else chunk.text,
                        "hierarchy_path": chunk.hierarchy_path,
                        "target_article": {
                            "operation_type": chunk.target_article.operation_type.value,
                            "code": chunk.target_article.code,
                            "article": chunk.target_article.article,
                            "confidence": 1.0,  # Default confidence
                            "raw_text": chunk.text[:50] + "..." if len(chunk.text) > 50 else chunk.text
                        },
                        "reconstruction_result": None,
                        "error": f"No original text found for {article_key}"
                    }
                    reconstruction_results.append(result_entry)
                    continue
                
                # Apply text reconstruction using new focused output format
                target_article_reference = f"{chunk.target_article.code}::{chunk.target_article.article}" if chunk.target_article.code and chunk.target_article.article else chunk.target_article.article or "unknown"
                
                # Use the updated reconstruct_amendment method that returns ReconstructorOutput
                reconstructor_output = self.text_reconstructor.reconstruct_amendment(
                    original_law_article=original_text,
                    amendment_instruction=chunk.text,
                    target_article_reference=target_article_reference,
                    chunk_id=chunk.chunk_id
                )
                
                chunk_duration = time.time() - chunk_start_time
                
                # Get the detailed reconstruction result from the reconstructor
                reconstruction_result = self.text_reconstructor.last_detailed_result
                logger.info("Retrieved last_detailed_result for chunk %s: %s", chunk.chunk_id, 
                           "None" if reconstruction_result is None else f"success={reconstruction_result.success}")
                if reconstruction_result is None:
                    # This should never happen - indicates a bug in LegalAmendmentReconstructor
                    logger.error("Critical bug: last_detailed_result is None for chunk %s", chunk.chunk_id)
                    raise RuntimeError(f"LegalAmendmentReconstructor failed to produce detailed result for chunk {chunk.chunk_id}")
                
                # Create result entry compatible with existing pipeline format
                result_entry = {
                    "chunk_id": chunk.chunk_id,
                    "chunk_text_preview": chunk.text[:100] + "..." if len(chunk.text) > 100 else chunk.text,
                    "hierarchy_path": chunk.hierarchy_path,
                    "target_article": {
                        "operation_type": chunk.target_article.operation_type.value,
                        "code": chunk.target_article.code,
                        "article": chunk.target_article.article,
                        "confidence": 1.0,  # Default confidence
                        "raw_text": chunk.text[:50] + "..." if len(chunk.text) > 50 else chunk.text
                    },
                    "reconstruction_result": {
                        "deleted_or_replaced_text": reconstructor_output.deleted_or_replaced_text,
                        "newly_inserted_text": reconstructor_output.newly_inserted_text,
                        "intermediate_after_state_text": reconstructor_output.intermediate_after_state_text,
                        "deleted_text_length": len(reconstructor_output.deleted_or_replaced_text),
                        "newly_inserted_text_length": len(reconstructor_output.newly_inserted_text),
                        "after_state_length": len(reconstructor_output.intermediate_after_state_text)
                    },
                    # Enhanced metadata from new reconstructor
                    "advanced_reconstruction_metadata": {
                        "success": reconstruction_result.success,
                        "operations_applied": len(reconstruction_result.operations_applied),
                        "operations_failed": len(reconstruction_result.operations_failed),
                        "processing_time_ms": reconstruction_result.processing_time_ms,
                        "validation_warnings": reconstruction_result.validation_warnings,
                        "operations_details": [
                            {
                                "type": op.operation_type.value,
                                "position": op.position_hint,
                                "confidence": op.confidence_score
                            } for op in reconstruction_result.operations_applied
                        ]
                    },
                    "original_text_used": {
                        "article_key": article_key,
                        "text_length": len(original_text),
                        "text_preview": original_text[:100] + "..." if len(original_text) > 100 else original_text
                    },
                    "reconstructed_at": datetime.now().isoformat()
                }
                
                reconstruction_results.append(result_entry)
                
                # Progress indicator
                deleted_len = len(reconstructor_output.deleted_or_replaced_text)
                inserted_len = len(reconstructor_output.newly_inserted_text)
                after_len = len(reconstructor_output.intermediate_after_state_text)
                operations_info = f"{len(reconstruction_result.operations_applied)} operations applied"
                if reconstruction_result.operations_failed:
                    operations_info += f", {len(reconstruction_result.operations_failed)} failed"
                logger.debug("Reconstructed: %d chars deleted/replaced, %d chars inserted → %d chars after state (%s)", 
                           deleted_len, inserted_len, after_len, operations_info)
                    
            except Exception as e:
                logger.error("Error processing chunk %s: %s", chunk.chunk_id, e)
                
                result_entry = {
                    "chunk_id": chunk.chunk_id,
                    "chunk_text_preview": chunk.text[:100] + "..." if len(chunk.text) > 100 else chunk.text,
                    "hierarchy_path": chunk.hierarchy_path,
                    "target_article": {
                        "operation_type": chunk.target_article.operation_type.value if chunk.target_article else None,
                        "code": chunk.target_article.code if chunk.target_article else None,
                        "article": chunk.target_article.article if chunk.target_article else None,
                        "confidence": 1.0 if chunk.target_article else None,
                        "raw_text": (chunk.text[:50] + "..." if len(chunk.text) > 50 else chunk.text) if chunk.target_article else None
                    },
                    "reconstruction_result": None,  # Fixed: No reconstruction result due to error
                    "error": str(e)
                }
                reconstruction_results.append(result_entry)

        self.reconstruction_results = reconstruction_results
        self.reconstruction_analysis = self._analyze_reconstruction_results(reconstruction_results)
        
        # Enhanced logging with detailed failure information
        successful_count = self.reconstruction_analysis['successful_reconstructions']
        failed_count = self.reconstruction_analysis['failed_reconstructions']
        total_count = self.reconstruction_analysis['total_chunks']
        
        logger.info("Text reconstruction complete: %d/%d successful reconstructions (%d failed)", 
                   successful_count, total_count, failed_count)
        
        # Log detailed failure analysis if there are failures
        if failed_count > 0:
            failure_analysis = self.reconstruction_analysis.get('failure_analysis', {})
            logger.info("Failure breakdown: %d no reconstruction result, %d reconstruction failed", 
                       failure_analysis.get('no_reconstruction_result', 0),
                       failure_analysis.get('reconstruction_failed', 0))
            
            # Log chunks with operation failures
            operation_failures = self.reconstruction_analysis.get('operation_failure_stats', {})
            if operation_failures:
                logger.info("Chunks with partial operation failures: %s", list(operation_failures.keys()))
        
        return reconstruction_results

    def step_5_locate_references(self) -> List[Dict]:
        """
        Step 5: Locate normative references in delta fragments using focused scanning.


        
        REFERENCE CLASSIFICATION:
        ========================
        The method classifies references by source type for downstream processing:
        
        - DELETIONAL references: Found in deleted_or_replaced_text
          → Use original law context for object linking (Step 3)
        - DEFINITIONAL references: Found in newly_inserted_text  
          → Use amended text context for object linking (Step 3)
        
        This classification drives the entire downstream reference resolution process.


        Args:
            None (uses self.reconstruction_results from Step 4)

        Returns:
            List of reference location results, each containing:
            - chunk_id: Unique identifier for the chunk
            - chunk_text_preview: Preview of the chunk text
            - hierarchy_path: Legislative hierarchy path
            - target_article: Target article information
            - reconstruction_result: Text reconstruction data
            - located_references: List of found references with source classification
            - reference_count: Total number of references found
            - reference_breakdown: Count by source type (DELETIONAL/DEFINITIONAL)

            - located_at: Timestamp of processing

        Raises:
            ValueError: If text reconstruction results from Step 4 are not available
        """
        if not self.reconstruction_results:
            raise ValueError("Text reconstruction results must be completed first.")

        logger.info("Step 5: Locating normative references using focused scanning...")
        
        # FILTER: Only process successful reconstructions to avoid downstream issues
        successful_reconstructions = []
        failed_reconstructions = []
        
        for result in self.reconstruction_results:
            reconstruction_result = result.get("reconstruction_result")
            if reconstruction_result:
                # Check if the reconstruction was actually successful
                advanced_metadata = result.get("advanced_reconstruction_metadata", {})
                actual_success = advanced_metadata.get("success", True)  # Default to True for backward compatibility
                
                if actual_success:
                    successful_reconstructions.append(result)
                else:
                    failed_reconstructions.append(result)
            else:
                # No reconstruction result at all
                failed_reconstructions.append(result)
        
        logger.info("Processing %d successful reconstructions (skipping %d failed reconstructions)", 
                   len(successful_reconstructions), len(failed_reconstructions))
        
        reference_location_results = []
        
        # Process only successful reconstruction results with focused scanning
        for i, result in enumerate(successful_reconstructions, 1):
            logger.info("Processing successful reconstruction %d/%d: %s", i, len(successful_reconstructions), result["chunk_id"][:50])
            
            # Capture start time for performance tracking
            chunk_start_time = time.time()
            
            try:
                # VALIDATION: All results at this point should have successful reconstruction_result
                # This is guaranteed by the filtering above
                reconstruction_result = result["reconstruction_result"]
                
                # FOCUSED SCANNING SETUP: Create ReconstructorOutput from successful reconstruction
                # This is the key step - we create the focused input format for the ReferenceLocator
                reconstructor_output = ReconstructorOutput(
                    deleted_or_replaced_text=reconstruction_result["deleted_or_replaced_text"],
                    newly_inserted_text=reconstruction_result["newly_inserted_text"],
                    intermediate_after_state_text=reconstruction_result["intermediate_after_state_text"]
                )
                
                # CORE FOCUSED SCANNING: Use ReferenceLocator to scan delta fragments
                # This achieves the 30x+ performance improvement by focusing on changes only
                located_references = self.reference_locator.locate(reconstructor_output)
                
                chunk_duration = time.time() - chunk_start_time
                

                
                # REFERENCE CLASSIFICATION: Analyze found references by source type
                # This breakdown is crucial for downstream processing decisions
                deletional_refs = [r for r in located_references if r.source == ReferenceSourceType.DELETIONAL]
                definitional_refs = [r for r in located_references if r.source == ReferenceSourceType.DEFINITIONAL]
                

                
                # RESULT CONSTRUCTION: Build comprehensive result entry
                # This provides all necessary information for downstream processing and analysis
                result_entry = {
                    "chunk_id": result["chunk_id"],
                    "chunk_text_preview": result.get("chunk_text_preview", ""),
                    "hierarchy_path": result.get("hierarchy_path", []),
                    "target_article": result.get("target_article"),
                    "reconstruction_result": reconstruction_result,
                    "located_references": [
                        {
                            "reference_text": ref.reference_text,
                            "source": ref.source.value,
                            "confidence": ref.confidence
                        }
                        for ref in located_references
                    ],
                    "reference_count": len(located_references),
                    "reference_breakdown": {
                        "deletional_count": len(deletional_refs),
                        "definitional_count": len(definitional_refs)
                    },

                    "located_at": datetime.now().isoformat()
                }
                
                reference_location_results.append(result_entry)
                
                # PROGRESS REPORTING: Log processing progress
                logger.info(
                    f"Successful reconstruction {i}/{len(successful_reconstructions)}: "
                    f"Located {len(located_references)} references "
                    f"({len(deletional_refs)} DELETIONAL, {len(definitional_refs)} DEFINITIONAL) "
                    f"in {chunk_duration:.2f}s"
                )

            except Exception as e:
                # ERROR HANDLING: Comprehensive error logging and graceful degradation
                # Individual chunk failures don't abort the entire pipeline
                chunk_duration = time.time() - chunk_start_time
                logger.error(f"Reference location failed for chunk {result['chunk_id']}: {e}")
                

                
                # Create error result entry to maintain pipeline consistency
                result_entry = {
                    "chunk_id": result["chunk_id"],
                    "chunk_text_preview": result.get("chunk_text_preview", ""),
                    "hierarchy_path": result.get("hierarchy_path", []),
                    "target_article": result.get("target_article"),
                    "reconstruction_result": result.get("reconstruction_result"),
                    "located_references": [],
                    "reference_count": 0,
                    "error": str(e)
                }
                reference_location_results.append(result_entry)

        # ADD FAILED RECONSTRUCTIONS: Include failed reconstructions in results for pipeline consistency
        # These will have empty reference results since they weren't processed
        for failed_result in failed_reconstructions:
            result_entry = {
                "chunk_id": failed_result["chunk_id"],
                "chunk_text_preview": failed_result.get("chunk_text_preview", ""),
                "hierarchy_path": failed_result.get("hierarchy_path", []),
                "target_article": failed_result.get("target_article"),
                "reconstruction_result": failed_result.get("reconstruction_result"),
                "located_references": [],
                "reference_count": 0,
                "skip_reason": "Reconstruction failed - skipped to avoid downstream issues"
            }
            reference_location_results.append(result_entry)
        
        # PIPELINE STATE UPDATE: Store results and perform comprehensive analysis
        self.reference_location_results = reference_location_results
        self.reference_location_analysis = self._analyze_reference_location_results(reference_location_results)
        
        # FINAL REPORTING: Log overall pipeline step completion with summary statistics
        total_refs = sum(result["reference_count"] for result in reference_location_results)
        successful_chunks = sum(1 for result in reference_location_results if "skip_reason" not in result)
        
        logger.info(
            f"Step 5 completed: Located {total_refs} references across {successful_chunks} successful chunks "
            f"(skipped {len(failed_reconstructions)} failed reconstructions) "
            f"using focused scanning approach"
        )
        
        return reference_location_results

    def step_6_link_references(self) -> List[Dict]:
        """
        Step 6: Link references to grammatical objects using context-aware analysis with resolution question generation.

        Args:
            None (uses self.reference_location_results from Step 5)

        Returns:
            List of reference linking results, each containing:
            - chunk_id: Unique identifier for the chunk
            - chunk_text_preview: Preview of the chunk text
            - hierarchy_path: Legislative hierarchy path
            - target_article: Target article information
            - reconstruction_result: Text reconstruction data
            - linked_references: List of found linked references with objects and resolution questions
            - linked_reference_count: Total number of linked references found
            - linked_at: Timestamp of processing

        Raises:
            ValueError: If reference location results from Step 5 are not available
        """
        if not self.reference_location_results:
            raise ValueError("Reference location results must be completed first.")

        logger.info("Step 6: Linking references to grammatical objects...")
        
        # FILTER: Only process successful reference locations to avoid downstream issues
        successful_reference_locations = []
        failed_reference_locations = []
        
        for result in self.reference_location_results:
            # Skip chunks that were skipped in step 5 due to failed reconstructions
            if "skip_reason" in result:
                failed_reference_locations.append(result)
                continue
                
            # Skip chunks without reconstruction_result or located_references
            if not result.get("reconstruction_result") or not result.get("located_references"):
                failed_reference_locations.append(result)
                continue
                
            # Skip chunks with no references to process
            if len(result.get("located_references", [])) == 0:
                failed_reference_locations.append(result)
                continue
                
            successful_reference_locations.append(result)
        
        logger.info("Processing %d successful reference locations (skipping %d failed/skipped locations)", 
                   len(successful_reference_locations), len(failed_reference_locations))
        
        reference_linking_results = []
        
        # Process only successful reference location results
        for i, result in enumerate(successful_reference_locations, 1):
            logger.info("Processing successful reference location %d/%d: %s", i, len(successful_reference_locations), result["chunk_id"][:50])
            
            # Capture start time for performance tracking
            chunk_start_time = time.time()
            
            try:
                # VALIDATION: All results at this point should have successful reconstruction_result and located_references
                # This is guaranteed by the filtering above
                reconstruction_result = result["reconstruction_result"]
                located_references_data = result["located_references"]
                
                # Convert located references data back to LocatedReference objects
                located_references = []
                for ref_data in located_references_data:
                    located_ref = LocatedReference(
                        reference_text=ref_data["reference_text"],
                        source=ReferenceSourceType(ref_data["source"]),
                        confidence=ref_data["confidence"]
                    )
                    located_references.append(located_ref)
                
                # CONTEXT PREPARATION: Get original text from retrieval results
                target_article = result.get("target_article", {})
                article_key = self._build_article_key(target_article.get("code"), target_article.get("article"))
                original_texts_lookup = self._create_original_texts_lookup()
                original_law_article = original_texts_lookup.get(article_key, "")
                intermediate_after_state_text = reconstruction_result["intermediate_after_state_text"]
                
                # CORE REFERENCE LINKING: Use ReferenceObjectLinker with context switching
                linked_references = self.reference_object_linker.link_references(
                    located_references=located_references,
                    original_law_article=original_law_article,
                    intermediate_after_state_text=intermediate_after_state_text
                )
                
                chunk_duration = time.time() - chunk_start_time
                
                # REFERENCE CLASSIFICATION: Analyze linked references by source type
                deletional_refs = [r for r in linked_references if r.source == ReferenceSourceType.DELETIONAL]
                definitional_refs = [r for r in linked_references if r.source == ReferenceSourceType.DEFINITIONAL]
                

                
                # RESULT CONSTRUCTION: Build comprehensive result entry
                result_entry = {
                    "chunk_id": result["chunk_id"],
                    "chunk_text_preview": result.get("chunk_text_preview", ""),
                    "hierarchy_path": result.get("hierarchy_path", []),
                    "target_article": result.get("target_article"),
                    "reconstruction_result": reconstruction_result,
                    "linked_references": [
                        {
                            "reference_text": ref.reference_text,
                            "source": ref.source.value,
                            "object": ref.object,
                            "agreement_analysis": ref.agreement_analysis,
                            "confidence": ref.confidence,
                            "resolution_question": ref.resolution_question
                        }
                        for ref in linked_references
                    ],
                    "linked_reference_count": len(linked_references),
                    "reference_breakdown": {
                        "deletional_count": len(deletional_refs),
                        "definitional_count": len(definitional_refs)
                    },
                    "context_switching_info": {
                        "original_law_article_available": bool(original_law_article.strip()),
                        "intermediate_after_state_text_available": bool(intermediate_after_state_text.strip()),
                        "article_key": article_key
                    },
                    "processing_duration_seconds": chunk_duration,
                    "linked_at": datetime.now().isoformat()
                }
                
                reference_linking_results.append(result_entry)
                
                # PROGRESS REPORTING: Log processing progress
                logger.info(
                    f"Successful reference location {i}/{len(successful_reference_locations)}: "
                    f"Linked {len(linked_references)} references "
                    f"({len(deletional_refs)} DELETIONAL, {len(definitional_refs)} DEFINITIONAL) "
                    f"in {chunk_duration:.2f}s"
                )

            except Exception as e:
                # ERROR HANDLING: Comprehensive error logging and graceful degradation
                chunk_duration = time.time() - chunk_start_time
                logger.error(f"Reference linking failed for chunk {result['chunk_id']}: {e}")
                

                
                # Create error result entry to maintain pipeline consistency
                result_entry = {
                    "chunk_id": result["chunk_id"],
                    "chunk_text_preview": result.get("chunk_text_preview", ""),
                    "hierarchy_path": result.get("hierarchy_path", []),
                    "target_article": result.get("target_article"),
                    "reconstruction_result": result.get("reconstruction_result"),
                    "linked_references": [],
                    "linked_reference_count": 0,
                    "error": str(e)
                }
                reference_linking_results.append(result_entry)

        # ADD FAILED REFERENCE LOCATIONS: Include failed reference locations in results for pipeline consistency
        # These will have empty linking results since they weren't processed
        for failed_result in failed_reference_locations:
            result_entry = {
                "chunk_id": failed_result["chunk_id"],
                "chunk_text_preview": failed_result.get("chunk_text_preview", ""),
                "hierarchy_path": failed_result.get("hierarchy_path", []),
                "target_article": failed_result.get("target_article"),
                "reconstruction_result": failed_result.get("reconstruction_result"),
                "linked_references": [],
                "linked_reference_count": 0,
                "skip_reason": "Reference location failed or skipped - skipped to avoid downstream issues"
            }
            reference_linking_results.append(result_entry)

        # PIPELINE STATE UPDATE: Store results and perform comprehensive analysis
        self.reference_linking_results = reference_linking_results
        self.reference_linking_analysis = self._analyze_reference_linking_results(reference_linking_results)
        
        # FINAL REPORTING: Log overall pipeline step completion with summary statistics
        total_refs = sum(result["linked_reference_count"] for result in reference_linking_results)
        successful_chunks = sum(1 for result in reference_linking_results if "skip_reason" not in result)
        
        logger.info(
            f"Step 6 completed: Linked {total_refs} references across {successful_chunks} successful chunks "
            f"(skipped {len(failed_reference_locations)} failed/skipped reference locations) "
            f"using context-switching approach"
        )
        
        return reference_linking_results

    def step_7_resolve_references(self) -> List[Dict]:
        """
        Step 7: Resolve linked references through question-guided content extraction with EU file access optimization.

        Args:
            None (uses self.reference_linking_results from Step 6)

        Returns:
            List of reference resolution results, each containing:
            - chunk_id: Unique identifier for the chunk
            - chunk_text_preview: Preview of the chunk text
            - hierarchy_path: Legislative hierarchy path
            - target_article: Target article information
            - reconstruction_result: Text reconstruction data
            - resolution_result: Reference resolution data with resolved content
            - resolved_reference_count: Total number of resolved references
            - resolved_at: Timestamp of processing

        Raises:
            ValueError: If reference linking results from Step 6 are not available
        """
        if not self.reference_linking_results:
            raise ValueError("Reference linking results must be completed first.")

        logger.info("Step 7: Resolving references through question-guided content extraction...")
        
        # FILTER: Only process successful reference links to avoid downstream issues
        successful_reference_links = []
        failed_reference_links = []
        
        for result in self.reference_linking_results:
            # Skip chunks that were skipped in step 6 due to failed reference locations
            if "skip_reason" in result:
                failed_reference_links.append(result)
                continue
                
            # Skip chunks without reconstruction_result or linked_references
            if not result.get("reconstruction_result") or not result.get("linked_references"):
                failed_reference_links.append(result)
                continue
                
            # Skip chunks with no references to resolve
            if len(result.get("linked_references", [])) == 0:
                failed_reference_links.append(result)
                continue
                
            successful_reference_links.append(result)
        
        logger.info("Processing %d successful reference links (skipping %d failed/skipped links)", 
                   len(successful_reference_links), len(failed_reference_links))
        
        reference_resolution_results = []
        
        # Process only successful reference linking results
        for i, result in enumerate(successful_reference_links, 1):
            logger.info("Processing successful reference link %d/%d: %s", i, len(successful_reference_links), result["chunk_id"][:50])
            
            # Capture start time for performance tracking
            chunk_start_time = time.time()
            
            try:
                # VALIDATION: All results at this point should have successful reconstruction_result and linked_references
                # This is guaranteed by the filtering above
                reconstruction_result = result["reconstruction_result"]
                linked_references_data = result["linked_references"]
                
                # Convert linked references data back to LinkedReference objects
                linked_references = []
                for ref_data in linked_references_data:
                    from bill_parser_engine.core.reference_resolver.models import LinkedReference, ReferenceSourceType
                    linked_ref = LinkedReference(
                        reference_text=ref_data["reference_text"],
                        source=ReferenceSourceType(ref_data["source"]),
                        object=ref_data["object"],
                        agreement_analysis=ref_data["agreement_analysis"],
                        confidence=ref_data["confidence"],
                        resolution_question=ref_data["resolution_question"]
                    )
                    linked_references.append(linked_ref)
                
                # CONTEXT PREPARATION: Get original text from retrieval results
                target_article = result.get("target_article", {})
                article_key = self._build_article_key(target_article.get("code"), target_article.get("article"))
                original_texts_lookup = self._create_original_texts_lookup()
                original_article_text = original_texts_lookup.get(article_key, "")
                
                # Create TargetArticle object for context
                target_article_obj = None
                if target_article.get("code") and target_article.get("article"):
                    target_article_obj = TargetArticle(
                        operation_type=TargetOperationType[target_article["operation_type"]],
                        code=target_article["code"],
                        article=target_article["article"]
                    )
                
                # CORE REFERENCE RESOLUTION: Use ReferenceResolver with proper parameters
                # This fixes the critical bug by passing original_article_text
                resolution_result = self.reference_resolver.resolve_references(
                    linked_references=linked_references,
                    original_article_text=original_article_text,
                    target_article=target_article_obj
                )
                
                chunk_duration = time.time() - chunk_start_time
                
                # REFERENCE CLASSIFICATION: Analyze resolved references by source type
                deletional_refs = resolution_result.resolved_deletional_references
                definitional_refs = resolution_result.resolved_definitional_references
                unresolved_refs = resolution_result.unresolved_references
                
                # RESULT CONSTRUCTION: Build comprehensive result entry
                result_entry = {
                    "chunk_id": result["chunk_id"],
                    "chunk_text_preview": result.get("chunk_text_preview", ""),
                    "hierarchy_path": result.get("hierarchy_path", []),
                    "target_article": result.get("target_article"),
                    "reconstruction_result": reconstruction_result,
                    "resolution_result": {
                        "resolved_deletional_references": [
                            {
                                "reference_text": ref.linked_reference.reference_text,
                                "object": ref.linked_reference.object,
                                "resolved_content": ref.resolved_content,
                                "retrieval_metadata": ref.retrieval_metadata
                            }
                            for ref in deletional_refs
                        ],
                        "resolved_definitional_references": [
                            {
                                "reference_text": ref.linked_reference.reference_text,
                                "object": ref.linked_reference.object,
                                "resolved_content": ref.resolved_content,
                                "retrieval_metadata": ref.retrieval_metadata
                            }
                            for ref in definitional_refs
                        ],
                        "unresolved_references": [
                            {
                                "reference_text": ref.reference_text,
                                "object": ref.object,
                                "error": "Failed to resolve"
                            }
                            for ref in unresolved_refs
                        ]
                    },
                    "resolved_reference_count": len(deletional_refs) + len(definitional_refs),
                    "reference_breakdown": {
                        "deletional_resolved": len(deletional_refs),
                        "definitional_resolved": len(definitional_refs),
                        "unresolved": len(unresolved_refs)
                    },
                    "context_info": {
                        "original_article_text_available": bool(original_article_text.strip()),
                        "article_key": article_key,
                        "target_article_provided": target_article_obj is not None
                    },
                    "processing_duration_seconds": chunk_duration,
                    "resolved_at": datetime.now().isoformat()
                }
                
                reference_resolution_results.append(result_entry)
                
                # PROGRESS REPORTING: Log processing progress
                logger.info(
                    f"Successful reference link {i}/{len(successful_reference_links)}: "
                    f"Resolved {len(deletional_refs) + len(definitional_refs)} references "
                    f"({len(deletional_refs)} DELETIONAL, {len(definitional_refs)} DEFINITIONAL, {len(unresolved_refs)} unresolved) "
                    f"in {chunk_duration:.2f}s"
                )

            except Exception as e:
                # ERROR HANDLING: Comprehensive error logging and graceful degradation
                chunk_duration = time.time() - chunk_start_time
                logger.error(f"Reference resolution failed for chunk {result['chunk_id']}: {e}")
                
                # Create error result entry to maintain pipeline consistency
                result_entry = {
                    "chunk_id": result["chunk_id"],
                    "chunk_text_preview": result.get("chunk_text_preview", ""),
                    "hierarchy_path": result.get("hierarchy_path", []),
                    "target_article": result.get("target_article"),
                    "reconstruction_result": result.get("reconstruction_result"),
                    "resolution_result": None,
                    "resolved_reference_count": 0,
                    "error": str(e)
                }
                reference_resolution_results.append(result_entry)

        # ADD FAILED REFERENCE LINKS: Include failed reference links in results for pipeline consistency
        # These will have empty resolution results since they weren't processed
        for failed_result in failed_reference_links:
            result_entry = {
                "chunk_id": failed_result["chunk_id"],
                "chunk_text_preview": failed_result.get("chunk_text_preview", ""),
                "hierarchy_path": failed_result.get("hierarchy_path", []),
                "target_article": failed_result.get("target_article"),
                "reconstruction_result": failed_result.get("reconstruction_result"),
                "resolution_result": None,
                "resolved_reference_count": 0,
                "skip_reason": "Reference linking failed or skipped - skipped to avoid downstream issues"
            }
            reference_resolution_results.append(result_entry)

        # PIPELINE STATE UPDATE: Store results and perform comprehensive analysis
        self.reference_resolution_results = reference_resolution_results
        self.reference_resolution_analysis = self._analyze_reference_resolution_results(reference_resolution_results)
        
        # FINAL REPORTING: Log overall pipeline step completion with summary statistics
        total_resolved = sum(result["resolved_reference_count"] for result in reference_resolution_results)
        successful_chunks = sum(1 for result in reference_resolution_results if "skip_reason" not in result)
        
        logger.info(
            f"Step 7 completed: Resolved {total_resolved} references across {successful_chunks} successful chunks "
            f"(skipped {len(failed_reference_links)} failed/skipped reference links) "
            f"using question-guided content extraction"
        )
        
        return reference_resolution_results

    def _analyze_reference_resolution_results(self, reference_resolution_results: List[Dict]) -> Dict:
        """Analyze reference resolution results with detailed per-chunk and per-reference statistics."""
        # Categorize chunks by their processing status
        successful_processed = []
        failed_processing = []
        skipped_no_refs = []
        
        for result in reference_resolution_results:
            if result.get("error"):
                # Actual processing error
                failed_processing.append(result)
            elif result.get("skip_reason"):
                # Intentionally skipped (usually no references were found)
                skipped_no_refs.append(result)
            else:
                # Successfully processed (with or without resolved references)
                successful_processed.append(result)
        
        # Detailed analysis of reference resolution within each chunk
        total_resolved = 0
        total_unresolved = 0
        deletional_resolved = 0
        definitional_resolved = 0
        retrieval_sources = {}
        
        # Per-chunk analysis
        chunks_with_all_refs_resolved = 0
        chunks_with_partial_refs_resolved = 0
        chunks_with_no_refs_resolved = 0
        chunks_with_mixed_results = 0
        
        for result in successful_processed:
            resolution_result = result.get("resolution_result")
            if not resolution_result:
                chunks_with_no_refs_resolved += 1
                continue
                
            deletional_refs = resolution_result.get("resolved_deletional_references", [])
            definitional_refs = resolution_result.get("resolved_definitional_references", [])
            unresolved_refs = resolution_result.get("unresolved_references", [])
            
            resolved_in_chunk = len(deletional_refs) + len(definitional_refs)
            total_in_chunk = resolved_in_chunk + len(unresolved_refs)
            
            # Categorize chunk by resolution success
            if total_in_chunk == 0:
                chunks_with_no_refs_resolved += 1
            elif resolved_in_chunk == total_in_chunk:
                chunks_with_all_refs_resolved += 1
            elif resolved_in_chunk > 0:
                chunks_with_partial_refs_resolved += 1
                if len(unresolved_refs) > 0:
                    chunks_with_mixed_results += 1
            else:
                chunks_with_no_refs_resolved += 1
            
            # Aggregate reference counts
            total_resolved += resolved_in_chunk
            total_unresolved += len(unresolved_refs)
            deletional_resolved += len(deletional_refs)
            definitional_resolved += len(definitional_refs)
            
            # Analyze retrieval sources
            for ref in deletional_refs + definitional_refs:
                metadata = ref.get("retrieval_metadata", {})
                source = metadata.get("source", "unknown")
                retrieval_sources[source] = retrieval_sources.get(source, 0) + 1
        
        # Calculate comprehensive success rates
        total_chunks = len(reference_resolution_results)
        total_references = total_resolved + total_unresolved
        
        processing_success_rate = len(successful_processed) / total_chunks if total_chunks else 0
        reference_resolution_success_rate = total_resolved / total_references if total_references > 0 else 0
        
        # Per-chunk success rates
        chunks_with_any_resolved_rate = (chunks_with_all_refs_resolved + chunks_with_partial_refs_resolved) / len(successful_processed) if successful_processed else 0
        chunks_with_all_resolved_rate = chunks_with_all_refs_resolved / len(successful_processed) if successful_processed else 0
        
        return {
            "total_chunks": total_chunks,
            "total_references": total_references,
            "processing_results": {
                "successful_processed": len(successful_processed),
                "failed_processing": len(failed_processing),
                "skipped_no_refs": len(skipped_no_refs),
                "processing_success_rate": processing_success_rate
            },
            "chunk_resolution_analysis": {
                "chunks_with_all_refs_resolved": chunks_with_all_refs_resolved,
                "chunks_with_partial_refs_resolved": chunks_with_partial_refs_resolved,
                "chunks_with_no_refs_resolved": chunks_with_no_refs_resolved,
                "chunks_with_mixed_results": chunks_with_mixed_results,
                "chunks_with_any_resolved_rate": chunks_with_any_resolved_rate,
                "chunks_with_all_resolved_rate": chunks_with_all_resolved_rate
            },
            "reference_resolution_stats": {
                "total_resolved": total_resolved,
                "total_unresolved": total_unresolved,
                "deletional_resolved": deletional_resolved,
                "definitional_resolved": definitional_resolved,
                "reference_resolution_success_rate": reference_resolution_success_rate,
                "deletional_success_rate": deletional_resolved / (deletional_resolved + sum(1 for r in successful_processed for ref in r.get("resolution_result", {}).get("unresolved_references", []) if "DELETIONAL" in str(ref.get("object", "")))) if (deletional_resolved + sum(1 for r in successful_processed for ref in r.get("resolution_result", {}).get("unresolved_references", []) if "DELETIONAL" in str(ref.get("object", "")))) > 0 else 0,
                "definitional_success_rate": definitional_resolved / (definitional_resolved + sum(1 for r in successful_processed for ref in r.get("resolution_result", {}).get("unresolved_references", []) if "DEFINITIONAL" in str(ref.get("object", "")))) if (definitional_resolved + sum(1 for r in successful_processed for ref in r.get("resolution_result", {}).get("unresolved_references", []) if "DEFINITIONAL" in str(ref.get("object", "")))) > 0 else 0
            },
            "retrieval_analysis": {
                "retrieval_sources": retrieval_sources,
                "eu_file_access_count": retrieval_sources.get("eu_file", 0),
                "local_fr_count": retrieval_sources.get("local_fr", 0),
                "original_article_count": retrieval_sources.get("original_article_text", 0),
                "cache_count": retrieval_sources.get("cache", 0)
            },
            "failed_chunks": [r["chunk_id"] for r in failed_processing],
            "skipped_chunks": [r["chunk_id"] for r in skipped_no_refs]
        }

    def run_full_pipeline(self) -> Dict:
        """
        Run the complete pipeline from start to finish.

        Returns:
            Dictionary containing all results and analyses

        Raises:
            ValueError: If no legislative text has been loaded
        """
        logger.info("Starting full pipeline execution...")
        
        # Execute all pipeline steps
        chunks = self.step_1_split_bill()
        target_results = self.step_2_identify_target_articles()
        retrieval_results = self.step_3_retrieve_original_texts()
        reconstruction_results = self.step_4_reconstruct_texts()
        reference_location_results = self.step_5_locate_references()
        reference_linking_results = self.step_6_link_references()
        reference_resolution_results = self.step_7_resolve_references()
        legal_state_results = self.step_8_synthesize_legal_states()
        
        # Compile comprehensive results
        pipeline_results = {
            "metadata": {
                "generated_at": datetime.now().isoformat(),
                "total_chunks": len(chunks),
                "pipeline_version": "1.0",
                "pipeline_steps": ["BillSplitter", "TargetArticleIdentifier", "OriginalTextRetriever", "LegalAmendmentReconstructor", "ReferenceLocator", "ReferenceObjectLinker", "ReferenceResolver"]
            },
            "chunks": [self._chunk_to_dict(chunk) for chunk in chunks],
            "target_analysis": self.target_analysis,
            "target_identification_results": target_results,
            "retrieval_analysis": self.retrieval_analysis,
            "original_text_results": retrieval_results,
            "reconstruction_analysis": self.reconstruction_analysis,
            "text_reconstruction_results": reconstruction_results,
            "reference_location_analysis": self.reference_location_analysis,
            "reference_linking_analysis": self.reference_linking_analysis,
            "reference_resolution_analysis": self.reference_resolution_analysis,
            "reference_location_results": reference_location_results,
            "reference_linking_results": reference_linking_results,
            "reference_resolution_results": reference_resolution_results,
            "legal_state_analysis": self.legal_state_analysis,
            "legal_state_results": legal_state_results
        }
        
        logger.info("Full pipeline execution complete")
        return pipeline_results

    def step_8_synthesize_legal_states(self) -> List[Dict]:
        """
        Step 8: Synthesize annotated before/after fragments deterministically.

        Uses the resolved references from Step 7 and reconstruction fragments
        from Step 4 to render concise before/after states with footnote markers
        and metadata for legal review.

        Returns:
            List of synthesis results per chunk

        Raises:
            ValueError: If required prior steps haven't been completed
        """
        if not self.reference_resolution_results:
            raise ValueError("Reference resolution results must be completed first.")
        if not self.reconstruction_results:
            raise ValueError("Text reconstruction results must be completed first.")

        logger.info("Step 8: Synthesizing annotated before/after fragments...")

        # Build lookups we need
        recon_lookup: Dict[str, ReconstructorOutput] = {}
        for r in self.reconstruction_results:
            chunk_id = r.get("chunk_id")
            recon = r.get("reconstruction_result")
            if not chunk_id or not recon:
                continue
            recon_lookup[chunk_id] = ReconstructorOutput(
                deleted_or_replaced_text=recon.get("deleted_or_replaced_text", ""),
                newly_inserted_text=recon.get("newly_inserted_text", ""),
                intermediate_after_state_text=recon.get("intermediate_after_state_text", ""),
            )

        synthesis_results: List[Dict] = []

        from bill_parser_engine.core.reference_resolver.legal_state_synthesizer import LegalStateSynthesizer
        from bill_parser_engine.core.reference_resolver.models import (
            LegalStateSynthesizerConfig,
            ResolutionResult as ResolutionResultModel,
            ResolvedReference as ResolvedReferenceModel,
            LinkedReference as LinkedReferenceModel,
        )

        synthesizer = LegalStateSynthesizer(LegalStateSynthesizerConfig())

        processed = 0
        skipped = 0

        # Build original text lookup (used to feed Step 8 so we always have Before context)
        original_texts_lookup = self._create_original_texts_lookup()

        for result in self.reference_resolution_results:
            # Skip chunks without a resolution_result
            if result.get("skip_reason") or result.get("error"):
                skipped += 1
                continue
            res = result.get("resolution_result")
            if not res:
                skipped += 1
                continue

            chunk_id = result.get("chunk_id", "")
            target_article_data = result.get("target_article") or {}
            reconstruction_result = result.get("reconstruction_result") or {}

            # Build minimal BillChunk (only fields needed for metadata)
            # We keep text fields empty to avoid large memory use; synthesizer uses only chunk_id in metadata
            chunk_obj = BillChunk(
                text="",
                titre_text="",
                article_label=chunk_id,
                article_introductory_phrase=None,
                major_subdivision_label=None,
                major_subdivision_introductory_phrase=None,
                numbered_point_label=None,
                numbered_point_introductory_phrase=None,
                lettered_subdivision_label=None,
                hierarchy_path=result.get("hierarchy_path", []),
                chunk_id=chunk_id,
                start_pos=0,
                end_pos=0,
            )

            # TargetArticle
            target_obj = None
            if target_article_data.get("operation_type"):
                target_obj = TargetArticle(
                    operation_type=TargetOperationType[target_article_data["operation_type"]],
                    code=target_article_data.get("code"),
                    article=target_article_data.get("article"),
                    confidence=target_article_data.get("confidence", 1.0),
                )
            else:
                skipped += 1
                continue

            # ReconstructorOutput
            recon_obj = recon_lookup.get(chunk_id)
            if not recon_obj:
                # Attempt to rebuild from current result if lookup missing
                rr = reconstruction_result
                recon_obj = ReconstructorOutput(
                    deleted_or_replaced_text=(rr or {}).get("deleted_or_replaced_text", ""),
                    newly_inserted_text=(rr or {}).get("newly_inserted_text", ""),
                    intermediate_after_state_text=(rr or {}).get("intermediate_after_state_text", ""),
                )

            # Rebuild ResolutionResult dataclass with minimal data
            deletional_rr: List[ResolvedReferenceModel] = []
            for d in res.get("resolved_deletional_references", []):
                linked = LinkedReferenceModel(
                    reference_text=d.get("reference_text", ""),
                    source=ReferenceSourceType.DELETIONAL,
                    object=d.get("object", ""),
                    agreement_analysis="",
                    confidence=1.0,
                    resolution_question="",
                )
                deletional_rr.append(
                    ResolvedReferenceModel(
                        linked_reference=linked,
                        resolved_content=d.get("resolved_content", ""),
                        retrieval_metadata=d.get("retrieval_metadata", {}),
                    )
                )

            definitional_rr: List[ResolvedReferenceModel] = []
            for d in res.get("resolved_definitional_references", []):
                linked = LinkedReferenceModel(
                    reference_text=d.get("reference_text", ""),
                    source=ReferenceSourceType.DEFINITIONAL,
                    object=d.get("object", ""),
                    agreement_analysis="",
                    confidence=1.0,
                    resolution_question="",
                )
                definitional_rr.append(
                    ResolvedReferenceModel(
                        linked_reference=linked,
                        resolved_content=d.get("resolved_content", ""),
                        retrieval_metadata=d.get("retrieval_metadata", {}),
                    )
                )

            resolution_obj = ResolutionResultModel(
                resolved_deletional_references=deletional_rr,
                resolved_definitional_references=definitional_rr,
                resolution_tree={},
                unresolved_references=[],
            )

            try:
                # Provide original article text so Step 8 can always show before-context even for micro-edits
                article_key = self._build_article_key(target_article_data.get("code"), target_article_data.get("article"))
                original_text = original_texts_lookup.get(article_key, "")
                synthesis = synthesizer.synthesize(
                    chunk=chunk_obj, target=target_obj, recon=recon_obj, resolution=resolution_obj,
                    original_article_text=original_text
                )
            except Exception as e:
                logger.error("Synthesis failed for chunk %s: %s", chunk_id, e)
                skipped += 1
                continue

            # Build result entry
            entry = {
                "chunk_id": chunk_id,
                "target_article": target_article_data,
                "before_state": {
                    "text": synthesis.before_state.text,
                    "annotations": [
                        {
                            "marker_index": a.marker_index,
                            "reference_text": a.reference_text,
                            "object": a.object,
                            "resolved_content": a.resolved_content,
                            "source": a.source.value,
                            "start_offset": a.start_offset,
                            "end_offset": a.end_offset,
                            "retrieval_metadata": a.retrieval_metadata,
                        }
                        for a in synthesis.before_state.annotations
                    ],
                },
                "after_state": {
                    "text": synthesis.after_state.text,
                    "annotations": [
                        {
                            "marker_index": a.marker_index,
                            "reference_text": a.reference_text,
                            "object": a.object,
                            "resolved_content": a.resolved_content,
                            "source": a.source.value,
                            "start_offset": a.start_offset,
                            "end_offset": a.end_offset,
                            "retrieval_metadata": a.retrieval_metadata,
                        }
                        for a in synthesis.after_state.annotations
                    ],
                },
                "metadata": synthesis.metadata,
            }

            synthesis_results.append(entry)
            processed += 1

        # Store and analyze
        self.legal_state_results = synthesis_results
        self.legal_state_analysis = self._analyze_legal_state_results(synthesis_results, processed, skipped)

        logger.info(
            "Step 8 completed: Synthesized %d chunks (skipped %d). Total annotations: before=%d, after=%d",
            processed,
            skipped,
            sum(len(e["before_state"]["annotations"]) for e in synthesis_results),
            sum(len(e["after_state"]["annotations"]) for e in synthesis_results),
        )

        return synthesis_results

    def _analyze_legal_state_results(self, results: List[Dict], processed: int, skipped: int) -> Dict:
        """Compute simple analysis stats for synthesized legal states."""
        total_before_annotations = sum(len(r.get("before_state", {}).get("annotations", [])) for r in results)
        total_after_annotations = sum(len(r.get("after_state", {}).get("annotations", [])) for r in results)
        chunks_with_before = sum(1 for r in results if r.get("before_state", {}).get("annotations"))
        chunks_with_after = sum(1 for r in results if r.get("after_state", {}).get("annotations"))

        return {
            "processed_chunks": processed,
            "skipped_chunks": skipped,
            "annotation_counts": {
                "total_before_annotations": total_before_annotations,
                "total_after_annotations": total_after_annotations,
            },
            "coverage": {
                "chunks_with_before_annotations": chunks_with_before,
                "chunks_with_after_annotations": chunks_with_after,
            },
        }

    def save_results(self, output_dir: Path, filename_prefix: str = "pipeline_results") -> Path:
        """
        Save all pipeline results to JSON files.

        Args:
            output_dir: Directory to save results
            filename_prefix: Prefix for output files

        Returns:
            Path to the main results file
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Save comprehensive results
        results_file = output_dir / f"{filename_prefix}_{timestamp}.json"
        pipeline_results = {
            "metadata": {
                "generated_at": datetime.now().isoformat(),
                "total_chunks": len(self.chunks),
                "pipeline_version": "1.0",
                "pipeline_steps": ["BillSplitter", "TargetArticleIdentifier", "OriginalTextRetriever", "LegalAmendmentReconstructor", "ReferenceLocator", "ReferenceObjectLinker"]
            },
            "target_analysis": self.target_analysis,
            "target_identification_results": self.target_results,
            "retrieval_analysis": self.retrieval_analysis,
            "original_text_results": self.retrieval_results,
            "reconstruction_analysis": self.reconstruction_analysis,
            "text_reconstruction_results": self.reconstruction_results,
            "reference_location_analysis": self.reference_location_analysis,
            "reference_linking_analysis": self.reference_linking_analysis,
            "reference_location_results": self.reference_location_results,
            "reference_linking_results": self.reference_linking_results
        }
        
        with open(results_file, 'w', encoding='utf-8') as f:
            json.dump(pipeline_results, f, indent=2, ensure_ascii=False)
        
                    # Save reconstruction results for next pipeline step (ReferenceResolver)
        reconstruction_output_file = output_dir / f"reference_linking_output_{timestamp}.json"
        reconstruction_output = {
            "metadata": {
                "generated_at": datetime.now().isoformat(),
                "total_chunks_processed": len(self.reconstruction_results),
                "successful_reconstructions": self.reconstruction_analysis.get("successful_reconstructions", 0),
                "successful_reference_locations": self.reference_location_analysis.get("successful_locations", 0),
                "successful_reference_links": self.reference_linking_analysis.get("successful_links", 0),
                "next_pipeline_step": "ReferenceResolver"
            },
            "reconstruction_results": self.reconstruction_results,
            "reconstruction_analysis": self.reconstruction_analysis,
            "reference_location_results": self.reference_location_results,
            "reference_location_analysis": self.reference_location_analysis,
            "reference_linking_results": self.reference_linking_results,
            "reference_linking_analysis": self.reference_linking_analysis
        }
        
        with open(reconstruction_output_file, 'w', encoding='utf-8') as f:
            json.dump(reconstruction_output, f, indent=2, ensure_ascii=False)
        
        logger.info("Results saved to %s", results_file)
        return results_file

    def get_summary(self) -> Dict:
        """
        Get a summary of pipeline execution results.

        Returns:
            Dictionary containing summary statistics
        """
        return {
            "total_chunks": len(self.chunks),
            "target_identification": {
                "unique_articles": self.target_analysis.get("total_unique_articles", 0),
                "chunks_with_articles": self.target_analysis.get("chunks_with_identified_articles", 0),
                "operation_distribution": self.target_analysis.get("operation_type_stats", {})
            },
            "original_text_retrieval": {
                "success_rate": self.retrieval_analysis.get("success_rate", 0),
                "successful_retrievals": self.retrieval_analysis.get("successful_retrievals", 0),
                "total_articles": self.retrieval_analysis.get("total_articles", 0)
            },
            "text_reconstruction": {
                "success_rate": self.reconstruction_analysis.get("success_rate", 0),
                "successful_reconstructions": self.reconstruction_analysis.get("successful_reconstructions", 0),
                "failed_reconstructions": self.reconstruction_analysis.get("failed_reconstructions", 0),
                "total_chunks": self.reconstruction_analysis.get("total_chunks", 0),
                "failure_analysis": self.reconstruction_analysis.get("failure_analysis", {}),
                "operation_failure_stats": self.reconstruction_analysis.get("operation_failure_stats", {})
            },
            "reference_location": {
                "successful_locations": self.reference_location_analysis.get("successful_locations", 0),
                "total_locations": self.reference_location_analysis.get("total_locations", 0)
            },
            "reference_linking": {
                "successful_links": self.reference_linking_analysis.get("successful_links", 0),
                "total_links": self.reference_linking_analysis.get("total_links", 0)
            },
            "reference_resolution": {
                "total_resolved": self.reference_resolution_analysis.get("resolution_stats", {}).get("total_resolved", 0),
                "resolution_success_rate": self.reference_resolution_analysis.get("resolution_stats", {}).get("resolution_success_rate", 0),
                "eu_file_access_count": self.reference_resolution_analysis.get("retrieval_analysis", {}).get("eu_file_access_count", 0)
            }
        }

    def get_reconstruction_status_for_chunk(self, chunk_id: str) -> Optional[Dict]:
        """
        Get detailed reconstruction status for a specific chunk.
        
        Args:
            chunk_id: The chunk ID to look up
            
        Returns:
            Dictionary with reconstruction status details, or None if chunk not found
        """
        if not self.reconstruction_results:
            return None
            
        for result in self.reconstruction_results:
            if result["chunk_id"] == chunk_id:
                reconstruction_result = result.get("reconstruction_result")
                advanced_metadata = result.get("advanced_reconstruction_metadata", {})
                
                return {
                    "chunk_id": chunk_id,
                    "has_reconstruction_result": bool(reconstruction_result),
                    "actual_success": advanced_metadata.get("success", True),
                    "operations_applied": advanced_metadata.get("operations_applied", 0),
                    "operations_failed": advanced_metadata.get("operations_failed", 0),
                    "processing_time_ms": advanced_metadata.get("processing_time_ms", 0),
                    "validation_warnings": advanced_metadata.get("validation_warnings", []),
                    "error": result.get("error"),
                    "target_article": result.get("target_article"),
                    "reconstruction_result_preview": {
                        "deleted_text_length": reconstruction_result.get("deleted_text_length", 0) if reconstruction_result else 0,
                        "newly_inserted_text_length": reconstruction_result.get("newly_inserted_text_length", 0) if reconstruction_result else 0,
                        "after_state_length": reconstruction_result.get("after_state_length", 0) if reconstruction_result else 0
                    } if reconstruction_result else None
                }
        
        return None

    # Cache management methods

    def clear_cache(self, component: Optional[str] = None) -> int:
        """
        Clear cached Mistral API results.
        
        Args:
            component: Optional component name to clear cache for. If None, clears all cache entries.
                      Valid component names: 'TargetArticleIdentifier', 'InstructionDecomposer', 
                      'OperationApplier', 'ResultValidator', 'ReferenceLocator', 
                      'ReferenceObjectLinker', 'ReferenceObjectLinker-Evaluator', 
                      'ReferenceObjectLinker-Optimizer', 'ReferenceResolver.parser', 
                      'ReferenceResolver.extractor', 'ReferenceResolver.subsection_parser', 
                      'ReferenceResolver.subsection_extractor', 'OriginalTextRetriever-EU', 
                      'OriginalTextRetriever-French'
        
        Returns:
            Number of cache entries cleared
        """
        # All components use the same centralized cache
        cache = get_mistral_cache()
        
        if component is None:
            # Clear all cache entries
            cleared_count = cache.clear()
            logger.info("Cleared %d Mistral API cache entries", cleared_count)
        else:
            # Clear cache for specific component
            cleared_count = cache.clear_by_component(component)
            logger.info("Cleared %d Mistral API cache entries for component '%s'", cleared_count, component)
        
        return cleared_count

    def get_cache_stats(self, component: Optional[str] = None) -> dict:
        """
        Get statistics for cached Mistral API calls.
        
        Args:
            component: Optional component name to get stats for. If None, returns stats for all components.
                      Valid component names: 'target_identifier', 'text_reconstructor', 
                      'reference_locator', 'reference_object_linker'
        
        Returns:
            Dictionary with cache statistics
        """
        cache = get_mistral_cache()
        return cache.get_stats(component)

    # Comprehensive tracing methods







    # Private helper methods

    def _analyze_target_results(self, results: List[Dict]) -> Dict:
        """Analyze target identification results."""
        unique_articles_data = {}
        operation_stats = {}
        code_stats = {}
        error_count = 0
        
        for result in results:
            if "error" in result:
                error_count += 1
                continue
                
            target_article = result.get("target_article")
            if not target_article or not target_article.get("article"):
                continue
                
            article = target_article["article"]
            code = target_article.get("code")
            operation_type = target_article.get("operation_type")
            
            if article:
                article_key = f"{code}::{article}" if code else article
                if article_key not in unique_articles_data:
                    unique_articles_data[article_key] = {
                        "code": code,
                        "article": article,
                        "operation_type": operation_type,
                        "article_key": article_key
                    }
                
            if operation_type:
                operation_stats[operation_type] = operation_stats.get(operation_type, 0) + 1
                
            if code:
                code_stats[code] = code_stats.get(code, 0) + 1
        
        return {
            "unique_articles_data": unique_articles_data,
            "unique_articles": sorted(list(unique_articles_data.keys())),
            "total_unique_articles": len(unique_articles_data),
            "operation_type_stats": operation_stats,
            "code_stats": code_stats,
            "total_chunks_processed": len(results),
            "chunks_with_errors": error_count,
            "chunks_with_identified_articles": len([r for r in results if r.get("target_article") and r["target_article"].get("article")])
        }

    def _analyze_retrieval_results(self, retrieval_results: List[Dict]) -> Dict:
        """Analyze retrieval results."""
        successful_retrievals = [r for r in retrieval_results if r["retrieval_metadata"].get("success", False)]
        failed_retrievals = [r for r in retrieval_results if not r["retrieval_metadata"].get("success", False)]
        
        source_stats = {}
        for result in successful_retrievals:
            source = result["retrieval_metadata"].get("source", "unknown")
            source_stats[source] = source_stats.get(source, 0) + 1
        
        text_lengths = [r["text_length"] for r in successful_retrievals]
        avg_length = sum(text_lengths) / len(text_lengths) if text_lengths else 0
        max_length = max(text_lengths) if text_lengths else 0
        min_length = min(text_lengths) if text_lengths else 0
        
        return {
            "total_articles": len(retrieval_results),
            "successful_retrievals": len(successful_retrievals),
            "failed_retrievals": len(failed_retrievals),
            "success_rate": len(successful_retrievals) / len(retrieval_results) if retrieval_results else 0,
            "source_stats": source_stats,
            "text_length_stats": {
                "average": avg_length,
                "maximum": max_length,
                "minimum": min_length,
                "total_characters": sum(text_lengths)
            },
            "failed_articles": [r["article_key"] for r in failed_retrievals]
        }

    def _analyze_reconstruction_results(self, reconstruction_results: List[Dict]) -> Dict:
        """Analyze reconstruction results."""
        # Check both presence of reconstruction_result AND actual success status
        successful_reconstructions = []
        failed_reconstructions = []
        
        for r in reconstruction_results:
            reconstruction_result = r.get("reconstruction_result")
            if reconstruction_result:
                # Check if the reconstruction was actually successful
                # Look for success status in advanced_reconstruction_metadata
                advanced_metadata = r.get("advanced_reconstruction_metadata", {})
                actual_success = advanced_metadata.get("success", True)  # Default to True for backward compatibility
                
                if actual_success:
                    successful_reconstructions.append(r)
                else:
                    failed_reconstructions.append(r)
            else:
                # No reconstruction result at all (e.g., missing original text, exceptions)
                failed_reconstructions.append(r)
        
        operation_stats = {}
        deleted_text_lengths = []
        newly_inserted_text_lengths = []
        after_state_lengths = []
        
        for result in successful_reconstructions:
            target_article = result.get("target_article", {})
            operation_type = target_article.get("operation_type")
            if operation_type:
                operation_stats[operation_type] = operation_stats.get(operation_type, 0) + 1
                
            reconstruction = result["reconstruction_result"]
            deleted_text_lengths.append(reconstruction["deleted_text_length"])
            newly_inserted_text_lengths.append(reconstruction.get("newly_inserted_text_length", 0))
            after_state_lengths.append(reconstruction["after_state_length"])
        
        avg_deleted_length = sum(deleted_text_lengths) / len(deleted_text_lengths) if deleted_text_lengths else 0
        avg_newly_inserted_length = sum(newly_inserted_text_lengths) / len(newly_inserted_text_lengths) if newly_inserted_text_lengths else 0
        avg_after_length = sum(after_state_lengths) / len(after_state_lengths) if after_state_lengths else 0
        
        # Analyze failure types for better debugging
        failure_analysis = {
            "no_reconstruction_result": len([r for r in failed_reconstructions if not r.get("reconstruction_result")]),
            "reconstruction_failed": len([r for r in failed_reconstructions if r.get("reconstruction_result")])
        }
        
        # Analyze operation failure details for successful reconstructions
        operation_failure_stats = {}
        for r in successful_reconstructions:
            advanced_metadata = r.get("advanced_reconstruction_metadata", {})
            operations_failed = advanced_metadata.get("operations_failed", 0)
            operations_applied = advanced_metadata.get("operations_applied", 0)
            
            if operations_failed > 0:
                operation_failure_stats[r["chunk_id"]] = {
                    "operations_applied": operations_applied,
                    "operations_failed": operations_failed,
                    "success_rate": operations_applied / (operations_applied + operations_failed) if (operations_applied + operations_failed) > 0 else 0
                }
        
        return {
            "total_chunks": len(reconstruction_results),
            "successful_reconstructions": len(successful_reconstructions),
            "failed_reconstructions": len(failed_reconstructions),
            "success_rate": len(successful_reconstructions) / len(reconstruction_results) if reconstruction_results else 0,
            "operation_type_stats": operation_stats,
            "text_length_stats": {
                "average_deleted_length": avg_deleted_length,
                "average_newly_inserted_length": avg_newly_inserted_length,
                "average_after_state_length": avg_after_length,
                "max_deleted_length": max(deleted_text_lengths) if deleted_text_lengths else 0,
                "max_newly_inserted_length": max(newly_inserted_text_lengths) if newly_inserted_text_lengths else 0,
                "max_after_state_length": max(after_state_lengths) if after_state_lengths else 0,
                "total_deleted_characters": sum(deleted_text_lengths),
                "total_newly_inserted_characters": sum(newly_inserted_text_lengths),
                "total_after_state_characters": sum(after_state_lengths)
            },
            "failure_analysis": failure_analysis,
            "operation_failure_stats": operation_failure_stats,
            "failed_chunks": [r["chunk_id"] for r in failed_reconstructions]
        }

    def _analyze_reference_location_results(self, reference_location_results: List[Dict]) -> Dict:
        """Analyze reference location results."""
        # A chunk is considered successful if it has no error AND no skip_reason
        # skip_reason indicates the chunk was intentionally skipped (e.g., failed reconstruction)
        # Note: Chunks with empty located_references are considered successful - they just have no references to resolve
        successful_locations = [r for r in reference_location_results if not r.get("error") and not r.get("skip_reason")]
        failed_locations = [r for r in reference_location_results if r.get("error") or r.get("skip_reason")]
        
        # Analyze reference types and sources
        total_references = 0
        deletional_references = 0
        definitional_references = 0
        confidence_scores = []
        
        for result in successful_locations:
            located_refs = result.get("located_references", [])
            total_references += len(located_refs)
            
            for ref in located_refs:
                if ref.get("source") == "DELETIONAL":
                    deletional_references += 1
                elif ref.get("source") == "DEFINITIONAL":
                    definitional_references += 1
                
                if "confidence" in ref:
                    confidence_scores.append(ref["confidence"])
            

        
        # Calculate averages
        avg_confidence = sum(confidence_scores) / len(confidence_scores) if confidence_scores else 0
        avg_references_per_chunk = total_references / len(successful_locations) if successful_locations else 0
        
        return {
            "total_chunks": len(reference_location_results),
            "successful_locations": len(successful_locations),
            "failed_locations": len(failed_locations),
            "success_rate": len(successful_locations) / len(reference_location_results) if reference_location_results else 0,
            "reference_stats": {
                "total_references": total_references,
                "deletional_references": deletional_references,
                "definitional_references": definitional_references,
                "average_references_per_chunk": avg_references_per_chunk,
                "average_confidence": avg_confidence,
                "confidence_distribution": {
                    "high_confidence": len([c for c in confidence_scores if c >= 0.8]),
                    "medium_confidence": len([c for c in confidence_scores if 0.5 <= c < 0.8]),
                    "low_confidence": len([c for c in confidence_scores if c < 0.5])
                }
            },

            "failed_chunks": [r["chunk_id"] for r in failed_locations]
        }

    def _analyze_reference_linking_results(self, reference_linking_results: List[Dict]) -> Dict:
        """Analyze reference linking results with proper categorization."""
        # Categorize chunks by their actual status:
        # - successful_processed: chunks processed without errors (with or without references)
        # - failed_processing: chunks with actual processing errors
        # - skipped_no_refs: chunks intentionally skipped because no references were found
        
        successful_processed = []
        failed_processing = []
        skipped_no_refs = []
        
        for result in reference_linking_results:
            if result.get("error"):
                # Actual processing error
                failed_processing.append(result)
            elif result.get("skip_reason"):
                # Intentionally skipped (usually no references found)
                skipped_no_refs.append(result)
            else:
                # Successfully processed (with or without references)
                successful_processed.append(result)
        
        # Analyze reference types and sources from successfully processed chunks
        total_links = 0
        deletional_links = 0
        definitional_links = 0
        confidence_scores = []
        objects_found = []
        resolution_questions_generated = []
        
        for result in successful_processed:
            linked_refs = result.get("linked_references", [])
            total_links += len(linked_refs)
            
            for ref in linked_refs:
                if ref.get("source") == "DELETIONAL":
                    deletional_links += 1
                elif ref.get("source") == "DEFINITIONAL":
                    definitional_links += 1
                
                if "confidence" in ref:
                    confidence_scores.append(ref["confidence"])
                
                if "object" in ref and ref["object"]:
                    objects_found.append(ref["object"])
                
                if "resolution_question" in ref and ref["resolution_question"]:
                    resolution_questions_generated.append(ref["resolution_question"])
        
        # Calculate meaningful averages
        avg_confidence = sum(confidence_scores) / len(confidence_scores) if confidence_scores else 0
        # Only calculate avg_links_per_chunk for chunks that actually had references
        chunks_with_refs = [r for r in successful_processed if r.get("linked_references")]
        avg_links_per_chunk = total_links / len(chunks_with_refs) if chunks_with_refs else 0
        
        # Analyze object types
        unique_objects = list(set(objects_found))
        object_frequency = {}
        for obj in objects_found:
            object_frequency[obj] = object_frequency.get(obj, 0) + 1
        
        # Calculate meaningful success rates
        total_chunks = len(reference_linking_results)
        processing_success_rate = len(successful_processed) / total_chunks if total_chunks else 0
        chunks_with_refs_rate = len(chunks_with_refs) / total_chunks if total_chunks else 0
        
        return {
            "total_chunks": total_chunks,
            "processing_results": {
                "successful_processed": len(successful_processed),
                "failed_processing": len(failed_processing),
                "skipped_no_refs": len(skipped_no_refs),
                "processing_success_rate": processing_success_rate,
                "chunks_with_refs_rate": chunks_with_refs_rate
            },
            "reference_stats": {
                "total_links": total_links,
                "deletional_links": deletional_links,
                "definitional_links": definitional_links,
                "chunks_with_references": len(chunks_with_refs),
                "chunks_without_references": len(successful_processed) - len(chunks_with_refs),
                "average_links_per_chunk_with_refs": avg_links_per_chunk,
                "average_confidence": avg_confidence,
                "confidence_distribution": {
                    "high_confidence": len([c for c in confidence_scores if c >= 0.8]),
                    "medium_confidence": len([c for c in confidence_scores if 0.5 <= c < 0.8]),
                    "low_confidence": len([c for c in confidence_scores if c < 0.5])
                }
            },
            "object_analysis": {
                "total_objects_found": len(objects_found),
                "unique_objects_count": len(unique_objects),
                "unique_objects": unique_objects[:10],  # Top 10 for brevity
                "most_common_objects": sorted(object_frequency.items(), key=lambda x: x[1], reverse=True)[:10]
            },
            "resolution_questions": {
                "total_questions_generated": len(resolution_questions_generated),
                "sample_questions": resolution_questions_generated[:5]  # Sample for review
            },
            "failed_chunks": [r["chunk_id"] for r in failed_processing],
            "skipped_chunks": [r["chunk_id"] for r in skipped_no_refs]
        }

    def _enrich_chunks_with_target_articles(self, chunks: List[BillChunk]) -> List[BillChunk]:
        """Enrich BillChunk objects with their identified target articles."""
        target_lookup = {result["chunk_id"]: result for result in self.target_results}
        
        enriched_chunks = []
        for chunk in chunks:
            enriched_chunk = BillChunk(
                text=chunk.text,
                titre_text=chunk.titre_text,
                article_label=chunk.article_label,
                article_introductory_phrase=chunk.article_introductory_phrase,
                major_subdivision_label=chunk.major_subdivision_label,
                major_subdivision_introductory_phrase=chunk.major_subdivision_introductory_phrase,
                numbered_point_label=chunk.numbered_point_label,
                numbered_point_introductory_phrase=chunk.numbered_point_introductory_phrase,
                lettered_subdivision_label=chunk.lettered_subdivision_label,
                hierarchy_path=chunk.hierarchy_path,
                chunk_id=chunk.chunk_id,
                start_pos=chunk.start_pos,
                end_pos=chunk.end_pos,
                target_article=chunk.target_article,
                inherited_target_article=chunk.inherited_target_article
            )
            
            target_data = target_lookup.get(chunk.chunk_id)
            if target_data and target_data.get("target_article") and not target_data.get("error"):
                target_article_data = target_data["target_article"]
                enriched_chunk.target_article = TargetArticle(
                    operation_type=TargetOperationType[target_article_data["operation_type"]],
                    code=target_article_data["code"],
                    article=target_article_data["article"]
                )
            
            enriched_chunks.append(enriched_chunk)
        
        return enriched_chunks

    def _create_original_texts_lookup(self) -> Dict[str, str]:
        """Create a lookup dictionary from article keys to original texts."""
        lookup = {}
        for result in self.retrieval_results:
            article_key = result["article_key"]
            original_text = result["original_text"]
            lookup[article_key] = original_text
        return lookup

    def _build_article_key(self, code: str, article: str) -> str:
        """Build article key for lookup."""
        return f"{code}::{article}" if code and article else article or ""

    def _chunk_to_dict(self, chunk: BillChunk) -> Dict:
        """Convert BillChunk to dictionary for serialization."""
        return {
            "chunk_id": chunk.chunk_id,
            "text": chunk.text,
            "titre_text": chunk.titre_text,
            "article_label": chunk.article_label,
            "article_introductory_phrase": chunk.article_introductory_phrase,
            "major_subdivision_label": chunk.major_subdivision_label,
            "major_subdivision_introductory_phrase": chunk.major_subdivision_introductory_phrase,
            "numbered_point_label": chunk.numbered_point_label,
            "numbered_point_introductory_phrase": chunk.numbered_point_introductory_phrase,
            "lettered_subdivision_label": chunk.lettered_subdivision_label,
            "hierarchy_path": chunk.hierarchy_path,
            "start_pos": chunk.start_pos,
            "end_pos": chunk.end_pos,
            "target_article": chunk.target_article,
            "inherited_target_article": chunk.inherited_target_article
        }



    # Utility methods from original script
    def _is_exotic_format(self, article: str) -> bool:
        """Check if the article format is too exotic to process."""
        exotic_patterns = ["Titre", "titre", "Livre", "livre", "Chapitre", "chapitre", "Section", "section"]
        return any(pattern in article for pattern in exotic_patterns)



 