"""
Data models for the reference resolver.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class ReferenceType(Enum):
    """Types of normative references."""
    ARTICLE = "article"
    CODE = "code"
    REGULATION = "regulation"
    DECREE = "decree"
    LAW = "law"


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
    reference_type: ReferenceType
    source: ReferenceSource
    components: Dict[str, str]


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