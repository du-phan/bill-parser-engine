"""
Normative Reference Resolver module.

This module provides functionality to process French legislative texts and resolve
normative references using the "Lawyer's Mental Model" architecture.

The main entry point is the complete_reference_pipeline function which orchestrates
the entire processing pipeline from legislative text to fully resolved legal states.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from mistralai import Mistral

from bill_parser_engine.core.reference_resolver.bill_splitter import BillSplitter
from bill_parser_engine.core.reference_resolver.target_identifier import TargetArticleIdentifier
from bill_parser_engine.core.reference_resolver.original_text_retriever import OriginalTextRetriever
# from bill_parser_engine.core.reference_resolver.text_reconstructor import TextReconstructor  # TODO: Replace with LegalAmendmentReconstructor
from bill_parser_engine.core.reference_resolver.reference_locator import ReferenceLocator
from bill_parser_engine.core.reference_resolver.reference_object_linker import ReferenceObjectLinker
from bill_parser_engine.core.reference_resolver.resolution_orchestrator import ResolutionOrchestrator
from bill_parser_engine.core.reference_resolver.legal_state_synthesizer import LegalStateSynthesizer
from bill_parser_engine.core.reference_resolver.models import (
    BillChunk,
    TargetArticle, 
    TargetOperationType,
    ReferenceSource,
    ReferenceSourceType,
    Reference,
    ResolvedReference,
    ReconstructorOutput,
    LocatedReference,
    LinkedReference,
    ResolutionResult,
    LegalState,
    LegalAnalysisOutput,
    FlattenedText,
    ProcessedChunkResult,
    # Clean Architecture Models
    OperationType,
    AmendmentOperation,
    ReconstructionResult
)

logger = logging.getLogger(__name__)


@dataclass
class PipelineConfig:
    """Configuration for the reference resolution pipeline."""
    max_resolution_depth: int = 3
    confidence_threshold: float = 0.7
    cache_dir: str = "./reference_cache"
    rate_limit_per_minute: int = 60
    timeout_seconds: int = 300


@dataclass
class PipelineResult:
    """Result of the complete reference resolution pipeline."""
    success: bool
    outputs: List[LegalAnalysisOutput] = field(default_factory=list)
    failed_chunks: List[Dict] = field(default_factory=list)
    error: Optional[str] = None
    metadata: Dict = field(default_factory=dict)


@dataclass
class PipelineComponents:
    """Container for all pipeline components."""
    splitter: BillSplitter
    target_identifier: TargetArticleIdentifier
    text_retriever: OriginalTextRetriever
    reconstructor: Optional[object]  # TextReconstructor  # TODO: Replace with LegalAmendmentReconstructor
    locator: ReferenceLocator
    linker: ReferenceObjectLinker
    orchestrator: ResolutionOrchestrator
    synthesizer: LegalStateSynthesizer


@dataclass
class ChunkProcessingContext:
    """Context for processing a single chunk."""
    chunk: BillChunk
    index: int


def initialize_pipeline_components(client: Mistral, config: Optional[PipelineConfig] = None) -> PipelineComponents:
    """
    Initialize all pipeline components with proper configuration.
    
    Args:
        client: Mistral client for LLM API calls
        config: Pipeline configuration (uses defaults if None)
        
    Returns:
        PipelineComponents container with all initialized components
        
    Raises:
        Exception: If component initialization fails
    """
    if config is None:
        config = PipelineConfig()
    
    try:
        # Initialize all components
        splitter = BillSplitter()
        target_identifier = TargetArticleIdentifier()
        text_retriever = OriginalTextRetriever()
        # reconstructor = TextReconstructor()  # TODO: Replace with LegalAmendmentReconstructor
        reconstructor = None
        locator = ReferenceLocator()
        linker = ReferenceObjectLinker()
        
        # Initialize orchestrator with other components
        orchestrator = ResolutionOrchestrator(
            text_retriever=text_retriever,
            reference_locator=locator,
            reference_linker=linker,
            max_depth=config.max_resolution_depth
        )
        
        synthesizer = LegalStateSynthesizer()
        
        return PipelineComponents(
            splitter=splitter,
            target_identifier=target_identifier,
            text_retriever=text_retriever,
            reconstructor=reconstructor,
            locator=locator,
            linker=linker,
            orchestrator=orchestrator,
            synthesizer=synthesizer
        )
        
    except Exception as e:
        logger.error(f"Failed to initialize pipeline components: {e}")
        raise


def validate_legislative_text(legislative_text: str) -> bool:
    """
    Validate legislative text format and length.
    
    Args:
        legislative_text: Input legislative text
        
    Returns:
        True if text is valid, False otherwise
    """
    if not legislative_text or not legislative_text.strip():
        return False
    
    # Check for minimum length
    if len(legislative_text.strip()) < 50:
        return False
    
    # Check for basic French legislative patterns
    legislative_patterns = [
        "article", "Article", "ARTICLE",
        "alinéa", "titre", "TITRE",
        "modifié", "remplacé", "supprimé", "inséré"
    ]
    
    text_lower = legislative_text.lower()
    if not any(pattern.lower() in text_lower for pattern in legislative_patterns):
        return False
    
    return True


def process_single_chunk(context: ChunkProcessingContext, components: PipelineComponents) -> Optional[LegalAnalysisOutput]:
    """
    Process a single chunk through all pipeline stages with validation.
    
    Args:
        context: Chunk processing context
        components: Initialized pipeline components
        
    Returns:
        LegalAnalysisOutput if successful, None if chunk should be skipped
        
    Raises:
        Exception: If processing fails
    """
    
    def validate_stage_output(stage_name: str, output, validator_func):
        """Helper to validate stage outputs."""
        if not validator_func(output):
            raise ValueError(f"Stage {stage_name} produced invalid output")
        return output

    try:
        # Stage 1: Target Article Identification
        logger.info(f"Processing chunk {context.index}: {context.chunk.chunk_id}")
        
        target_article = validate_stage_output(
            "TargetIdentification",
            components.target_identifier.identify(context.chunk),
            lambda x: x.operation_type is not None  # Basic validation - operation_type should be set
        )

        # Early exit for pure versioning metadata (OTHER operation type)
        if target_article.operation_type == TargetOperationType.OTHER:
            logger.info(f"Skipping chunk {context.index}: Pure versioning metadata detected")
            return None

        # Early exit for low-confidence or missing targets
        if not target_article.article:
            logger.info(f"Skipping chunk {context.index}: No target article identified")
            return None

        # Assign target article to chunk (needed for downstream components)
        context.chunk.target_article = target_article

        # Stage 2: Original Text Retrieval
        original_text, retrieval_metadata = components.text_retriever.fetch_article_text(
            code=target_article.code,
            article=target_article.article
        )

        # Handle INSERT operations (empty original text is acceptable)
        if not original_text and target_article.operation_type != TargetOperationType.INSERT:
            logger.warning(f"Skipping chunk {context.index}: Could not retrieve original text")
            return None

        # Stage 3: Text Reconstruction
        reconstructor_output = validate_stage_output(
            "TextReconstruction",
            components.reconstructor.reconstruct(original_text, context.chunk),
            lambda x: len(x.deleted_or_replaced_text) > 0 or len(x.intermediate_after_state_text) > 0
        )

        # Stage 4: Reference Location
        located_references = components.locator.locate(reconstructor_output)
        logger.info(f"Chunk {context.index}: Found {len(located_references)} references")

        # Stage 5: Reference Object Linking
        linked_references = components.linker.link_references(located_references, reconstructor_output)
        logger.info(f"Chunk {context.index}: Linked {len(linked_references)} references")

        # Stage 6: Resolution Orchestration
        resolution_result = components.orchestrator.resolve_references(linked_references)
        logger.info(f"Chunk {context.index}: Resolved {len(resolution_result.resolved_deletional_references)} deletional and {len(resolution_result.resolved_definitional_references)} definitional references")

        # Stage 7: Legal State Synthesis
        final_output = components.synthesizer.synthesize(
            resolution_result=resolution_result,
            reconstructor_output=reconstructor_output,
            source_chunk=context.chunk,
            target_article=target_article
        )

        logger.info(f"Chunk {context.index}: Successfully processed")
        return final_output

    except Exception as e:
        logger.error(f"Failed to process chunk {context.index}: {e}")
        raise


def create_failure_record(chunk: BillChunk, error_message: str) -> Dict:
    """
    Create a failure record for a chunk that failed processing.
    
    Args:
        chunk: The chunk that failed
        error_message: Error message describing the failure
        
    Returns:
        Dictionary with failure information
    """
    return {
        "chunk_id": chunk.chunk_id,
        "chunk_text": chunk.text[:200] + "..." if len(chunk.text) > 200 else chunk.text,
        "error": error_message,
        "hierarchy_path": chunk.hierarchy_path
    }


def complete_reference_pipeline(
    legislative_text: str, 
    client: Mistral, 
    config: Optional[PipelineConfig] = None
) -> PipelineResult:
    """
    Execute complete reference resolution pipeline with comprehensive error handling.
    
    This is the main entry point for the reference resolution system. It processes
    French legislative text through the complete pipeline, handling errors gracefully
    and providing comprehensive logging.
    
    Args:
        legislative_text: Raw legislative text to process
        client: Mistral client for LLM API calls
        config: Pipeline configuration (optional, uses defaults if None)
        
    Returns:
        PipelineResult containing successful outputs, failed chunks, and execution metadata
        
    Example:
        >>> from mistralai import Mistral
        >>> client = Mistral(api_key="your_api_key")
        >>> result = complete_reference_pipeline(legislative_text, client)
        >>> if result.success:
        ...     for output in result.outputs:
        ...         print(f"Before: {output.before_state.state_text}")
        ...         print(f"After: {output.after_state.state_text}")
    """
    # Initialize components with error handling
    try:
        pipeline_components = initialize_pipeline_components(client, config)
        logger.info("Pipeline components initialized successfully")
    except Exception as e:
        return PipelineResult(success=False, error=f"Component initialization failed: {e}")

    # Input validation
    if not validate_legislative_text(legislative_text):
        return PipelineResult(success=False, error="Invalid legislative text format")

    final_outputs = []
    failed_chunks = []
    execution_metadata = {"total_chunks": 0, "successful": 0, "failed": 0}

    try:
        # Stage 1: Bill Splitting (deterministic, should not fail)
        logger.info("Starting bill splitting")
        bill_chunks = pipeline_components.splitter.split(legislative_text)
        execution_metadata["total_chunks"] = len(bill_chunks)
        logger.info(f"Split into {len(bill_chunks)} chunks")

        for i, chunk in enumerate(bill_chunks):
            chunk_context = ChunkProcessingContext(chunk=chunk, index=i)

            try:
                # Process single chunk through entire pipeline
                result = process_single_chunk(chunk_context, pipeline_components)
                if result:
                    final_outputs.append(result)
                    execution_metadata["successful"] += 1
                else:
                    failed_chunks.append(create_failure_record(chunk, "No result produced"))
                    execution_metadata["failed"] += 1

            except Exception as e:
                logger.error(f"Chunk {i} processing failed: {e}")
                failed_chunks.append(create_failure_record(chunk, str(e)))
                execution_metadata["failed"] += 1

        logger.info(f"Pipeline completed: {execution_metadata['successful']} successful, {execution_metadata['failed']} failed")
        
        return PipelineResult(
            success=True,
            outputs=final_outputs,
            failed_chunks=failed_chunks,
            metadata=execution_metadata
        )

    except Exception as e:
        logger.error(f"Pipeline execution failed: {e}")
        return PipelineResult(success=False, error=f"Pipeline execution failed: {e}")


__all__ = [
    # Components
    'BillSplitter',
    'TargetArticleIdentifier',
    'OriginalTextRetriever',
    'TextReconstructor',
    'ReferenceLocator',
    'ReferenceObjectLinker',
    'ResolutionOrchestrator',
    'LegalStateSynthesizer',
    
    # Data Models
    'BillChunk',
    'TargetArticle',
    'TargetOperationType', 
    'ReferenceSource',
    'ReferenceSourceType',
    'Reference',
    'ResolvedReference',
    'ReconstructorOutput',
    'LocatedReference',
    'LinkedReference',
    'ResolutionResult',
    'LegalState',
    'LegalAnalysisOutput',
    'FlattenedText',
    'ProcessedChunkResult',
    
    # Pipeline Classes
    'PipelineConfig',
    'PipelineResult',
    'PipelineComponents',
    'ChunkProcessingContext',
    
    # Main Functions
    'complete_reference_pipeline',
    'initialize_pipeline_components',
    'validate_legislative_text',
    'process_single_chunk',
    'create_failure_record',
] 