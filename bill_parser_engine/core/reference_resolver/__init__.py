"""
Normative Reference Resolver module.

This module provides functionality to process French legislative texts and resolve
normative references using the "Lawyer's Mental Model" architecture.

The main entry point is the BillProcessingPipeline class in pipeline.py.
"""

# Core pipeline components
from bill_parser_engine.core.reference_resolver.bill_splitter import BillSplitter
from bill_parser_engine.core.reference_resolver.target_identifier import TargetArticleIdentifier
from bill_parser_engine.core.reference_resolver.original_text_retriever import OriginalTextRetriever
from bill_parser_engine.core.reference_resolver.legal_amendment_reconstructor import LegalAmendmentReconstructor
from bill_parser_engine.core.reference_resolver.reference_locator import ReferenceLocator
from bill_parser_engine.core.reference_resolver.reference_object_linker import ReferenceObjectLinker
from bill_parser_engine.core.reference_resolver.reference_resolver import ReferenceResolver

# Main pipeline entry point
from bill_parser_engine.core.reference_resolver.pipeline import BillProcessingPipeline

# Data models
from bill_parser_engine.core.reference_resolver.models import (
    BillChunk,
    TargetArticle, 
    TargetOperationType,
    ReferenceSourceType,
    ResolvedReference,
    ReconstructorOutput,
    LocatedReference,
    LinkedReference,
    ResolutionResult,
    # Clean Architecture Models
    OperationType,
    AmendmentOperation,
    ReconstructionResult
)

__all__ = [
    # Main pipeline entry point
    'BillProcessingPipeline',
    
    # Core components
    'BillSplitter',
    'TargetArticleIdentifier',
    'OriginalTextRetriever',
    'LegalAmendmentReconstructor',
    'ReferenceLocator',
    'ReferenceObjectLinker',
    'ReferenceResolver',
    
    # Data models
    'BillChunk',
    'TargetArticle',
    'TargetOperationType', 
    'ReferenceSourceType',
    'ResolvedReference',
    'ReconstructorOutput',
    'LocatedReference',
    'LinkedReference',
    'ResolutionResult',
    'OperationType',
    'AmendmentOperation',
    'ReconstructionResult',
] 