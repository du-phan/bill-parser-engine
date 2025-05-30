"""
Reference classification component.
"""

from typing import List
from .models import Reference


class ReferenceClassifier:
    """Classifies normative references by source and type."""

    def __init__(self):
        """Initialize the classifier."""
        pass

    def classify(self, references: List[Reference]) -> List[Reference]:
        """
        Classify the given references by source and type.

        Args:
            references: List of references to classify

        Returns:
            List of classified references
        """
        raise NotImplementedError 