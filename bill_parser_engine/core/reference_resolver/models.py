"""
Data models for the reference resolver.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


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


class TargetOperationType(Enum):
    """Types of operations on target articles."""
    INSERT = "insert"
    MODIFY = "modify"
    ABROGATE = "abrogate"
    RENUMBER = "renumber"
    OTHER = "other"


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
    """
    Represents the primary legal article/section that is the target 
    of a modification, insertion, or abrogation in a legislative bill chunk.
    """
    operation_type: TargetOperationType
    code: Optional[str]
    article: Optional[str]
    full_citation: Optional[str]
    confidence: float  # < 1.0 if no explicit target
    raw_text: Optional[str]
    version: str = "v0"


@dataclass
class ResolvedReference:
    """Represents a resolved reference with its content."""
    reference: Reference
    content: str
    sub_references: List["ResolvedReference"]
    resolution_path: List[Reference]
    resolution_status: ResolutionStatus


@dataclass
class FlattenedText:
    """Represents the final flattened text with all references resolved."""
    original_text: str
    flattened_text: str
    reference_map: Dict[Reference, ResolvedReference]
    unresolved_references: List[Reference]


@dataclass
class BillChunk:
    """
    Represents an atomic chunk of a legislative bill, with all relevant context and metadata for downstream processing.
    """
    text: str
    titre_text: str
    article_label: str
    article_introductory_phrase: str
    major_subdivision_label: Optional[str]
    major_subdivision_label_raw: Optional[str] = None
    major_subdivision_introductory_phrase: Optional[str] = None
    numbered_point_label: Optional[str] = None
    numbered_point_label_raw: Optional[str] = None
    hierarchy_path: List[str] = field(default_factory=list)
    chunk_id: str = ""
    start_pos: int = 0
    end_pos: int = 0
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