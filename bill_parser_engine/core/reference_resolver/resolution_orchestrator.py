"""
Resolution orchestrator component.

This is the central stateful component that manages the entire recursive resolution process.
It uses other components as stateless tools to process a queue of linked references,
determine their relevance, handle recursion, and gracefully manage errors.

Core features:
- Stack-based recursive resolution with depth control
- Smart context-switching for DELETIONAL vs DEFINITIONAL references
- Cycle detection to prevent infinite recursion
- Graceful error handling and individual reference failure isolation
- Reference classification for appropriate retrieval strategies
"""

import logging
import re
from collections import deque
from typing import List, Dict, Tuple, Optional, Set

from bill_parser_engine.core.reference_resolver.models import (
    LinkedReference,
    ResolutionResult,
    ResolvedReference,
    ReferenceSourceType,
    LocatedReference,
    ReconstructorOutput,
)
from bill_parser_engine.core.reference_resolver.original_text_retriever import OriginalTextRetriever
from bill_parser_engine.core.reference_resolver.reference_locator import ReferenceLocator
from bill_parser_engine.core.reference_resolver.reference_object_linker import ReferenceObjectLinker

logger = logging.getLogger(__name__)


class ResolutionOrchestrator:
    """
    Central orchestrator managing recursive resolution of linked references.
    
    This stateful component uses other components as stateless tools to process
    a queue of linked references, handling recursion, cycle detection, and errors.
    
    Key algorithm features:
    - Stack-based processing with depth control (max_depth=3)
    - Only DEFINITIONAL sub-references spawn recursion
    - Cycle detection through reference signatures
    - Individual failure isolation
    - Reference type classification for retrieval strategies
    """

    def __init__(
        self,
        text_retriever: Optional[OriginalTextRetriever] = None,
        reference_locator: Optional[ReferenceLocator] = None,
        reference_linker: Optional[ReferenceObjectLinker] = None,
        max_depth: int = 3
    ):
        """
        Initialize the resolution orchestrator with component tools.

        Args:
            text_retriever: Component for retrieving legal article text
            reference_locator: Component for locating references in text
            reference_linker: Component for linking references to objects
            max_depth: Maximum recursion depth to prevent infinite loops
        """
        self.text_retriever = text_retriever or OriginalTextRetriever()
        self.reference_locator = reference_locator or ReferenceLocator()
        self.reference_linker = reference_linker or ReferenceObjectLinker()
        self.max_depth = max_depth

    def resolve_references(self, linked_references: List[LinkedReference]) -> ResolutionResult:
        """
        Recursively resolve all linked references using stack-based processing.

        Args:
            linked_references: List of linked references from ReferenceObjectLinker

        Returns:
            ResolutionResult containing resolved references separated by type,
            resolution tree, and unresolved references

        Raises:
            ValueError: If input validation fails
        """
        # Input validation
        if not isinstance(linked_references, list):
            raise ValueError("linked_references must be a list")

        logger.info(f"Starting resolution of {len(linked_references)} linked references")

        # Initialize tracking structures
        resolution_stack = deque([(ref, 0) for ref in linked_references])  # (reference, depth)
        resolved_deletional = []
        resolved_definitional = []
        unresolved = []
        seen_references: Set[str] = set()  # Cycle detection
        resolution_tree = {"depth": 0, "nodes": [], "total_processed": 0}

        while resolution_stack:
            current_ref, depth = resolution_stack.popleft()
            resolution_tree["total_processed"] += 1

            logger.debug(f"Processing reference at depth {depth}: {current_ref.reference_text}")

            # Depth control
            if depth >= self.max_depth:
                logger.warning(f"Max depth reached for reference: {current_ref.reference_text}")
                unresolved.append(current_ref)
                continue

            # Cycle detection
            ref_signature = self._create_reference_signature(current_ref)
            if ref_signature in seen_references:
                logger.warning(f"Cycle detected for reference: {current_ref.reference_text}")
                continue
            seen_references.add(ref_signature)

            try:
                # Step 1: Assess relevance (for now, assume all relevant)
                if not self._assess_relevance(current_ref):
                    logger.debug(f"Reference deemed non-essential: {current_ref.reference_text}")
                    continue

                # Step 2: Retrieve content
                content, metadata = self._retrieve_reference_content(current_ref)
                if not content:
                    logger.warning(f"Could not retrieve content for: {current_ref.reference_text}")
                    unresolved.append(current_ref)
                    continue

                # Step 3: Create resolved reference
                resolved_ref = ResolvedReference(
                    linked_reference=current_ref,
                    resolved_content=content,
                    retrieval_metadata=metadata
                )

                # Step 4: Categorize by original source
                if current_ref.source == ReferenceSourceType.DELETIONAL:
                    resolved_deletional.append(resolved_ref)
                    logger.debug(f"Resolved DELETIONAL reference: {current_ref.reference_text}")
                else:
                    resolved_definitional.append(resolved_ref)
                    logger.debug(f"Resolved DEFINITIONAL reference: {current_ref.reference_text}")

                # Step 5: Recursive sub-reference discovery (ONLY for DEFINITIONAL)
                if current_ref.source == ReferenceSourceType.DEFINITIONAL and depth < self.max_depth - 1:
                    sub_references = self._discover_sub_references(content)
                    new_definitional_refs = [
                        ref for ref in sub_references 
                        if ref.source == ReferenceSourceType.DEFINITIONAL
                    ]
                    
                    if new_definitional_refs:
                        logger.debug(f"Found {len(new_definitional_refs)} sub-references in resolved content")
                        for sub_ref in new_definitional_refs:
                            resolution_stack.append((sub_ref, depth + 1))

            except Exception as e:
                logger.error(f"Failed to resolve reference {current_ref.reference_text}: {e}")
                unresolved.append(current_ref)

        # Update resolution tree metadata
        resolution_tree["depth"] = max([depth for _, depth in [(ref, 0) for ref in linked_references]] + [0])
        resolution_tree["nodes"] = [
            {
                "type": "deletional",
                "count": len(resolved_deletional),
                "references": [ref.linked_reference.reference_text for ref in resolved_deletional]
            },
            {
                "type": "definitional", 
                "count": len(resolved_definitional),
                "references": [ref.linked_reference.reference_text for ref in resolved_definitional]
            },
            {
                "type": "unresolved",
                "count": len(unresolved),
                "references": [ref.reference_text for ref in unresolved]
            }
        ]

        logger.info(f"Resolution complete: {len(resolved_deletional)} DELETIONAL, "
                   f"{len(resolved_definitional)} DEFINITIONAL, {len(unresolved)} unresolved")

        return ResolutionResult(
            resolved_deletional_references=resolved_deletional,
            resolved_definitional_references=resolved_definitional,
            resolution_tree=resolution_tree,
            unresolved_references=unresolved
        )

    def _create_reference_signature(self, ref: LinkedReference) -> str:
        """
        Create a unique signature for cycle detection.
        
        Args:
            ref: LinkedReference to create signature for
            
        Returns:
            Unique string signature for the reference
        """
        return f"{ref.source.value}:{ref.object}:{ref.reference_text}"

    def _assess_relevance(self, ref: LinkedReference) -> bool:
        """
        Assess whether a reference is essential for resolution.
        
        For now, implements a simple heuristic (all references relevant).
        Future enhancement point for more sophisticated relevance assessment.
        
        Args:
            ref: LinkedReference to assess
            
        Returns:
            True if reference should be resolved, False otherwise
        """
        # Simple heuristic: all references are considered relevant
        # Future enhancements could include:
        # - Pattern-based filtering (e.g., skip very generic references)
        # - Context-based relevance (e.g., based on reference object)
        # - User-configurable relevance rules
        return True

    def _retrieve_reference_content(self, ref: LinkedReference) -> Tuple[str, Dict]:
        """
        Retrieve content for a linked reference using appropriate strategy.
        
        Args:
            ref: LinkedReference to retrieve content for
            
        Returns:
            Tuple of (content, metadata)
        """
        # Classify reference type for retrieval strategy
        ref_type = self._classify_reference(ref.reference_text)
        
        if ref_type == "french_code":
            return self._retrieve_french_code_reference(ref)
        elif ref_type == "eu_regulation":
            return self._retrieve_eu_regulation_reference(ref)
        elif ref_type == "internal_reference":
            return self._retrieve_internal_reference(ref)
        else:
            # Fallback: try pylegifrance approach
            return self._retrieve_generic_reference(ref)

    def _classify_reference(self, reference_text: str) -> str:
        """
        Classify reference type for appropriate retrieval strategy.
        
        Args:
            reference_text: The reference text to classify
            
        Returns:
            Classification string: 'french_code', 'eu_regulation', 'internal_reference', 'other'
        """
        # EU regulations pattern
        if re.search(r'règlement\s*\(CE\)\s*n°?\s*\d+/\d+', reference_text, re.IGNORECASE):
            return "eu_regulation"
            
        # French code article pattern
        if re.search(r'article\s+L\.\s*\d+', reference_text, re.IGNORECASE):
            return "french_code"
            
        # Internal reference patterns (within same code)
        if re.search(r'aux?\s+\d+°\s+(?:ou\s+\d+°\s+)?du\s+[IVX]+', reference_text, re.IGNORECASE):
            return "internal_reference"
            
        return "other"

    def _retrieve_french_code_reference(self, ref: LinkedReference) -> Tuple[str, Dict]:
        """
        Retrieve content for French code article references.
        
        Args:
            ref: LinkedReference with French code reference
            
        Returns:
            Tuple of (content, metadata)
        """
        # Extract code and article from reference text
        code, article = self._parse_french_code_reference(ref.reference_text)
        
        if not code or not article:
            logger.warning(f"Could not parse French code reference: {ref.reference_text}")
            return "", {"source": "parse_error", "success": False, "error": "Could not parse reference"}
        
        # Use OriginalTextRetriever
        content, metadata = self.text_retriever.fetch_article_text(code, article)
        metadata["reference_type"] = "french_code"
        metadata["parsed_code"] = code
        metadata["parsed_article"] = article
        
        return content, metadata

    def _retrieve_eu_regulation_reference(self, ref: LinkedReference) -> Tuple[str, Dict]:
        """
        Retrieve content for EU regulation references.
        
        Args:
            ref: LinkedReference with EU regulation reference
            
        Returns:
            Tuple of (content, metadata)
        """
        # For EU regulations, we'll use web search fallback since they're not in pylegifrance
        logger.info(f"EU regulation reference detected: {ref.reference_text}")
        
        # Try to extract regulation number and article
        regulation_match = re.search(r'règlement\s*\(CE\)\s*n°?\s*(\d+/\d+)', ref.reference_text, re.IGNORECASE)
        article_match = re.search(r'(?:article|du)\s+(\d+)', ref.reference_text, re.IGNORECASE)
        
        if regulation_match:
            regulation_number = regulation_match.group(1)
            article_number = article_match.group(1) if article_match else None
            
            # Use web search approach from OriginalTextRetriever
            try:
                content = self.text_retriever._search_web_for_article(
                    f"règlement CE {regulation_number}",
                    f"article {article_number}" if article_number else ""
                )
                
                if content:
                    return content, {
                        "source": "web_search",
                        "success": True,
                        "reference_type": "eu_regulation",
                        "regulation_number": regulation_number,
                        "article_number": article_number
                    }
            except Exception as e:
                logger.warning(f"Web search failed for EU regulation: {e}")
        
        return "", {
            "source": "eu_regulation_fallback",
            "success": False,
            "error": "EU regulation retrieval not yet implemented",
            "reference_type": "eu_regulation"
        }

    def _retrieve_internal_reference(self, ref: LinkedReference) -> Tuple[str, Dict]:
        """
        Retrieve content for internal references (within same article/code).
        
        Args:
            ref: LinkedReference with internal reference
            
        Returns:
            Tuple of (content, metadata)
        """
        # Internal references require context of the original article
        # For now, return a placeholder indicating this needs context resolution
        logger.info(f"Internal reference detected: {ref.reference_text}")
        
        return "", {
            "source": "internal_reference",
            "success": False,
            "error": "Internal reference resolution requires article context",
            "reference_type": "internal_reference",
            "note": "Needs context from parent article for resolution"
        }

    def _retrieve_generic_reference(self, ref: LinkedReference) -> Tuple[str, Dict]:
        """
        Generic retrieval fallback for unclassified references.
        
        Args:
            ref: LinkedReference with generic reference
            
        Returns:
            Tuple of (content, metadata)
        """
        logger.info(f"Generic reference fallback: {ref.reference_text}")
        
        # Try to extract any code/article patterns
        code_match = re.search(r'code\s+[a-zA-Zé\s]+', ref.reference_text, re.IGNORECASE)
        article_match = re.search(r'article\s+([A-Z]\.?\s*\d+[-\d]*)', ref.reference_text, re.IGNORECASE)
        
        if code_match and article_match:
            code = code_match.group(0).strip()
            article = article_match.group(1).strip()
            
            content, metadata = self.text_retriever.fetch_article_text(code, article)
            metadata["reference_type"] = "generic"
            return content, metadata
        
        return "", {
            "source": "generic_fallback",
            "success": False,
            "error": "Could not classify or retrieve reference",
            "reference_type": "generic"
        }

    def _parse_french_code_reference(self, reference_text: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Parse French code reference to extract code name and article number.
        
        Args:
            reference_text: Reference text to parse
            
        Returns:
            Tuple of (code_name, article_number) or (None, None) if parsing fails
        """
        # Extract article number (e.g., "L. 253-5", "L. 254-1")
        article_match = re.search(r'article\s+([A-Z]\.?\s*\d+[-\d]*)', reference_text, re.IGNORECASE)
        if not article_match:
            return None, None
        
        article = article_match.group(1).strip()
        
        # Check for explicit code mention
        if "présent code" in reference_text.lower():
            # Context-dependent: assume code rural for our use case
            return "code rural et de la pêche maritime", article
        
        # Look for explicit code name
        code_patterns = [
            (r'code\s+rural\s+et\s+de\s+la\s+pêche\s+maritime', "code rural et de la pêche maritime"),
            (r'code\s+de\s+l\'environnement', "code de l'environnement"),
            (r'code\s+civil', "code civil"),
            (r'code\s+pénal', "code pénal"),
        ]
        
        for pattern, code_name in code_patterns:
            if re.search(pattern, reference_text, re.IGNORECASE):
                return code_name, article
        
        # Default to code rural for our legislative bill context
        return "code rural et de la pêche maritime", article

    def _discover_sub_references(self, content: str) -> List[LinkedReference]:
        """
        Discover sub-references in retrieved content using existing components.
        
        Args:
            content: Retrieved content to analyze for sub-references
            
        Returns:
            List of LinkedReference objects found in the content
        """
        try:
            # Early exit for very short content (unlikely to contain meaningful references)
            if len(content.strip()) < 50:
                logger.debug("Content too short for sub-reference discovery")
                return []
            
            # Use simple regex patterns first to check if content likely contains references
            # This avoids expensive API calls for content without references
            reference_patterns = [
                r'\bl\'\s*article\s+[LRD]\.\s*\d+',  # l'article L.123, R.456, etc.
                r'\baux?\s+articles?\s+[LRD]\.\s*\d+',  # aux articles L.123
                r'\bdu\s+règlement\s+\([CE]\)\s*n°\s*\d+',  # du règlement (CE) n° 1234
                r'\bà\s+l\'\s*article\s+\d+',  # à l'article 23
                r'\bau\s+sens\s+de\s+l\'\s*article',  # au sens de l'article
                r'\baux\s+\d+°?\s*(?:ou\s+\d+°?\s*)*(?:du\s+[IVX]+)?',  # aux 1° ou 2° du II
            ]
            
            has_potential_references = any(
                re.search(pattern, content, re.IGNORECASE) 
                for pattern in reference_patterns
            )
            
            if not has_potential_references:
                logger.debug("No potential reference patterns found in content")
                return []
            
            # Create a dummy ReconstructorOutput with the retrieved content
            # We only care about DEFINITIONAL references in retrieved content
            dummy_output = ReconstructorOutput(
                deleted_or_replaced_text="",  # Empty for sub-reference discovery
                intermediate_after_state_text=content
            )
            
            # Use ReferenceLocator to find references in the content
            located_refs = self.reference_locator.locate(dummy_output)
            
            # Filter to only DEFINITIONAL references (new content)
            definitional_refs = [
                ref for ref in located_refs 
                if ref.source == ReferenceSourceType.DEFINITIONAL
            ]
            
            if not definitional_refs:
                return []
            
            # Use ReferenceObjectLinker to link them to objects
            linked_refs = self.reference_linker.link_references(definitional_refs, dummy_output)
            
            logger.debug(f"Discovered {len(linked_refs)} sub-references in retrieved content")
            return linked_refs
            
        except Exception as e:
            logger.warning(f"Failed to discover sub-references in content: {e}")
            return []