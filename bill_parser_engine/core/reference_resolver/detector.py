"""
Reference detection component.
"""

from typing import List
from .models import Reference


class ReferenceDetector:
    """Detects normative references in legislative text."""

    def __init__(self):
        """Initialize the detector."""
        pass

    def detect(self, text: str) -> List[Reference]:
        """
        Detect all normative references in the given text.

        Args:
            text: The legislative text to process

        Returns:
            List of detected references
        """
        raise NotImplementedError 