"""
Normative Reference Resolver module.

This module provides functionality to detect, classify, and resolve normative references
in French legislative texts.
"""

from bill_parser_engine.core.reference_resolver.bill_splitter import BillSplitter
from bill_parser_engine.core.reference_resolver.target_identifier import TargetArticleIdentifier
from bill_parser_engine.core.reference_resolver.detector import ReferenceDetector
from bill_parser_engine.core.reference_resolver.classifier import ReferenceClassifier
from bill_parser_engine.core.reference_resolver.retriever import TextRetriever
from bill_parser_engine.core.reference_resolver.resolver import ReferenceResolver
from bill_parser_engine.core.reference_resolver.substitutor import TextSubstitutor
from bill_parser_engine.core.reference_resolver.models import (
    Reference, ResolvedReference, FlattenedText, 
    BillChunk, TargetArticle, TargetOperationType, ProcessedChunkResult
)

__all__ = [
    'BillSplitter',
    'TargetArticleIdentifier',
    'ReferenceDetector',
    'ReferenceClassifier',
    'TextRetriever',
    'ReferenceResolver',
    'TextSubstitutor',
    'Reference',
    'ResolvedReference',
    'FlattenedText',
    'BillChunk',
    'TargetArticle',
    'TargetOperationType',
    'complete_reference_pipeline',
]


def complete_reference_pipeline(text: str, mistral_client, max_depth: int = 2, cache_dir: str = './reference_cache'):
    """
    Execute the complete reference resolution pipeline on a legislative text.
    
    This function demonstrates the full pipeline flow using all components:
    1. Split the bill into atomic chunks
    2. Identify target articles for each chunk
    3. Detect normative references within each chunk
    4. Classify references and extract structured components
    5. Retrieve text content for each reference
    6. Resolve references recursively
    7. Substitute resolved references into the original text
    
    Args:
        text: The legislative text to process
        mistral_client: An initialized Mistral client for LLM processing
        max_depth: Maximum recursion depth for reference resolution
        cache_dir: Directory for file-based cache for reference retrieval
        
    Returns:
        A list of ProcessedChunkResult objects (never mutates BillChunk)
    """
    splitter = BillSplitter()
    target_identifier = TargetArticleIdentifier(mistral_client)
    detector = ReferenceDetector(mistral_client)
    classifier = ReferenceClassifier(mistral_client)
    retriever = TextRetriever(cache_dir=cache_dir)
    resolver = ReferenceResolver()
    substitutor = TextSubstitutor()
    
    chunks = splitter.split(text)
    results = []
    
    for chunk in chunks:
        # Step 2: Identify the target article for this chunk (returns a new BillChunk)
        chunk_with_target = target_identifier.identify(chunk)
        
        # Step 3: Detect references within the chunk
        references = detector.detect_from_chunk(chunk_with_target)
        
        if not references:
            # No references to process, add the chunk as is
            results.append(ProcessedChunkResult(chunk=chunk_with_target))
            continue
        
        # Step 4: Classify references and extract components
        classified_references = classifier.classify_batch(references, chunk_with_target)
        
        # Post-processing: Ensure 'code' is present for French code references
        for ref in classified_references:
            if (
                getattr(ref, 'source', None) == TargetArticle.__annotations__.get('source', None) or getattr(ref, 'source', None) == 'french_code' or getattr(ref, 'source', None) == 'FRENCH_CODE'
            ):
                # Accept both enum and string for robustness
                if 'code' not in getattr(ref, 'components', {}):
                    code_val = getattr(chunk_with_target.target_article, 'code', None)
                    if code_val:
                        ref.components['code'] = code_val
        
        resolved_references = []
        
        # Step 5 & 6: Retrieve and resolve each reference
        for ref in classified_references:
            # Ensure version/date is passed if available
            if hasattr(ref, 'version') and ref.version:
                pass  # Already present
            elif hasattr(chunk_with_target.target_article, 'version') and chunk_with_target.target_article.version:
                ref.version = chunk_with_target.target_article.version
            # If a date is available in components, it will be used by the retriever
            content, metadata = retriever.retrieve(ref)
            if content:
                resolved_ref = resolver.resolve(
                    reference=ref,
                    text_content=content,
                    max_depth=max_depth
                )
                resolved_references.append(resolved_ref)
        
        # Step 7: Substitute resolved references into the text
        flattened_text = ""
        if resolved_references:
            flattened = substitutor.substitute(
                original_text=chunk_with_target.text,
                resolved_references=resolved_references
            )
            flattened_text = flattened.flattened_text
        
        results.append(ProcessedChunkResult(
            chunk=chunk_with_target,
            flattened_text=flattened_text,
            resolved_references=resolved_references
        ))
    
    return results 