"""
Text substitution component.
"""

from typing import Dict, List
from .models import Reference, ResolvedReference, FlattenedText


class TextSubstitutor:
    """Substitutes references with their resolved content."""

    def __init__(self):
        """Initialize the substitutor."""
        pass

    def substitute(
        self,
        text: str,
        resolved_references: Dict[Reference, ResolvedReference],
        unresolved_references: List[Reference]
    ) -> FlattenedText:
        """
        Substitute references with their resolved content.

        Args:
            text: The original text
            resolved_references: Dictionary of resolved references
            unresolved_references: List of unresolved references

        Returns:
            The flattened text with all references substituted
        """
        raise NotImplementedError 