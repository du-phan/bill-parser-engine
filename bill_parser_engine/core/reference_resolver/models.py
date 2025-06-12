"""
Data models for the reference resolver.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


# --- Enums ---

class TargetOperationType(Enum):
    INSERT = "INSERT"
    MODIFY = "MODIFY"
    ABROGATE = "ABROGATE"
    RENUMBER = "RENUMBER"
    OTHER = "OTHER"

class ReferenceSourceType(Enum):
    DELETIONAL = "DELETIONAL"
    DEFINITIONAL = "DEFINITIONAL"

class ReferenceType(Enum):
    """
    Types of normative references, as per detailed spec.

    - EXPLICIT_DIRECT: Direct citation of a legal provision (e.g., "l'article L. 254-1").
    - EXPLICIT_SECTION: Reference to a specific section/paragraph within a provision (e.g., "au 3° du II de l'article X").
    - EXPLICIT_COMPLETE: Complete citation with source (e.g., "règlement (CE) n° 1107/2009").
    - IMPLICIT_CONTEXTUAL: Contextual reference (e.g., "du même article").
    - IMPLICIT_RELATIVE: Relative reference (e.g., "l'article précédent").
    - IMPLICIT_ABBREVIATED: Abbreviated reference (e.g., "ledit article").
    - OTHER: Fallback for unmappable or unknown reference types.
    """
    EXPLICIT_DIRECT = "explicit_direct"  # e.g., "l'article L. 254-1"
    EXPLICIT_SECTION = "explicit_section"  # e.g., "au 3° du II de l'article X"
    EXPLICIT_COMPLETE = "explicit_complete" # e.g., "règlement (CE) n° 1107/2009"
    IMPLICIT_CONTEXTUAL = "implicit_contextual" # e.g., "du même article"
    IMPLICIT_RELATIVE = "implicit_relative" # e.g., "l'article précédent"
    IMPLICIT_ABBREVIATED = "implicit_abbreviated" # e.g., "ledit article"
    OTHER = "other"  # Fallback for unmappable or unknown reference types
    # Consider adding OTHER for robustness if needed


class ReferenceSource(Enum):
    """Sources of normative references."""
    FRENCH_CODE = "french_code"
    EU_REGULATION = "eu_regulation"
    NATIONAL_LAW = "national_law"
    DECREE = "decree"


class ResolutionStatus(Enum):
    """Status of reference resolution."""
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"





@dataclass
class Reference:
    """Represents a normative reference in the text."""
    text: str
    start_pos: int
    end_pos: int
    object: str  # The noun/concept being referenced
    reference_type: Optional[ReferenceType] = None
    source: Optional[ReferenceSource] = None
    components: Dict[str, str] = field(default_factory=dict)


@dataclass
class TargetArticle:
    """Identifies the legal article being targeted by an amendment chunk."""
    operation_type: TargetOperationType
    code: Optional[str]
    article: Optional[str]
    confidence: float
    raw_text: Optional[str]
    full_citation: Optional[str] = None
    version: Optional[str] = None

@dataclass
class ReconstructorOutput:
    """Output from the TextReconstructor, providing the 'before' and 'after' text fragments."""
    deleted_or_replaced_text: str
    intermediate_after_state_text: str

@dataclass
class LocatedReference:
    """Represents a reference found in a text fragment, tagged by its source."""
    reference_text: str
    start_position: int
    end_position: int
    source: ReferenceSourceType
    confidence: float

@dataclass
class LinkedReference:
    """Represents a reference that has been grammatically linked to its object."""
    reference_text: str
    source: ReferenceSourceType
    object: str
    agreement_analysis: str
    confidence: float

@dataclass
class ResolutionResult:
    """The complete output from the ResolutionOrchestrator."""
    resolved_deletional_references: List["ResolvedReference"]
    resolved_definitional_references: List["ResolvedReference"]
    resolution_tree: Dict
    unresolved_references: List[LinkedReference]

@dataclass
class LegalState:
    """Represents a fully resolved legal state (either before or after amendment)."""
    state_text: str
    synthesis_metadata: Dict

@dataclass
class LegalAnalysisOutput:
    """The final, high-level output of the entire pipeline."""
    before_state: LegalState
    after_state: LegalState
    source_chunk: "BillChunk"
    target_article: TargetArticle


@dataclass
class ResolvedReference:
    """Contains a linked reference and its fetched content."""
    linked_reference: LinkedReference
    resolved_content: str
    retrieval_metadata: Dict[str, str]


@dataclass
class FlattenedText:
    """Represents the final flattened text with all references resolved."""
    original_text: str
    flattened_text: str
    reference_map: Dict[Reference, ResolvedReference]
    unresolved_references: List[Reference]


@dataclass
class BillChunk:
    """Represents an atomic, processable piece of a legislative bill, as output by the BillSplitter."""
    text: str
    titre_text: str
    article_label: str
    article_introductory_phrase: Optional[str]
    major_subdivision_label: Optional[str]
    major_subdivision_introductory_phrase: Optional[str]
    numbered_point_label: Optional[str]
    hierarchy_path: List[str]
    chunk_id: str
    start_pos: int
    end_pos: int
    # Backwards compatibility for existing BillSplitter
    cross_references: List[str] = field(default_factory=list)
    target_article: Optional[TargetArticle] = None


@dataclass
class ProcessedChunkResult:
    """
    Holds the result of processing a chunk in the pipeline, including:
    - The BillChunk (with target_article, but never mutated)
    - The flattened text (if any)
    - The resolved references (if any)
    """
    chunk: BillChunk
    flattened_text: str = ""
    resolved_references: list = field(default_factory=list) 