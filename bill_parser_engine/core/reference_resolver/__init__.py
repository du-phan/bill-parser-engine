"""
Normative Reference Resolver module.

This module provides functionality to detect, classify, and resolve normative references
in French legislative texts.
"""

from .detector import ReferenceDetector
from .classifier import ReferenceClassifier
from .retriever import TextRetriever
from .resolver import ReferenceResolver
from .substitutor import TextSubstitutor
from .models import Reference, ResolvedReference, FlattenedText

__all__ = [
    'ReferenceDetector',
    'ReferenceClassifier',
    'TextRetriever',
    'ReferenceResolver',
    'TextSubstitutor',
    'Reference',
    'ResolvedReference',
    'FlattenedText',
] 