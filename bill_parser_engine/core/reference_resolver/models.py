"""
Data models for the reference resolver.
"""

from dataclasses import dataclass
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