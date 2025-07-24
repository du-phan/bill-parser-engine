"""
ReferenceResolver: Resolves linked references through question-guided content extraction.
"""

import json
import logging
from typing import Dict, List, Optional, Tuple

from mistralai import Mistral

from bill_parser_engine.core.reference_resolver.cache_manager import SimpleCache, get_cache
from bill_parser_engine.core.reference_resolver.models import (
    LinkedReference,
    ReferenceSourceType,
    ResolutionResult,
    ResolvedReference,
)
from bill_parser_engine.core.reference_resolver.original_text_retriever import OriginalTextRetriever
from bill_parser_engine.core.reference_resolver.prompts import (
    QUESTION_GUIDED_EXTRACTION_SYSTEM_PROMPT,
    REFERENCE_PARSER_SYSTEM_PROMPT,
)
from bill_parser_engine.core.reference_resolver.rate_limiter import RateLimiter, get_rate_limiter
from bill_parser_engine.core.reference_resolver.rate_limiter import call_mistral_json_model

logger = logging.getLogger(__name__)


class ReferenceResolver:
    """
    Resolves linked references through targeted, question-guided content extraction.

    This component performs a focused two-step process:
    1.  Retrieve the full content that the reference points to using different
        strategies for DELETIONAL vs. DEFINITIONAL references.
    2.  Extract the specific part that answers the resolution question about the
        referenced object using an LLM.
    """

    def __init__(
        self,
        retriever: Optional[OriginalTextRetriever] = None,
        rate_limiter: Optional[RateLimiter] = None,
        cache: Optional[SimpleCache] = None,
        use_cache: bool = True,
    ):
        """
        Initializes the ReferenceResolver.

        Args:
            retriever: An instance of OriginalTextRetriever. If None, a new one is created.
            rate_limiter: Rate limiter for LLM calls.
            cache: Cache for storing intermediate results.
            use_cache: Whether to use caching.
        """
        self.rate_limiter = rate_limiter or get_rate_limiter()
        self.cache = cache or get_cache()
        self.use_cache = use_cache
        self.retriever = retriever or OriginalTextRetriever(
            rate_limiter=self.rate_limiter, cache=self.cache, use_cache=self.use_cache
        )
        self.mistral_client = Mistral()

    def resolve_references(
        self,
        linked_references: List[LinkedReference],
        original_article_text: str,
        target_article: Optional["TargetArticle"] = None,
    ) -> ResolutionResult:
        """
        Resolves a list of linked references serially.

        Args:
            linked_references: The list of references to resolve.
            original_article_text: The full original text of the law article,
                                   used for resolving DELETIONAL references.
            target_article: The target article being modified, which provides context.

        Returns:
            A ResolutionResult object containing the resolved and unresolved references.
        """
        resolved_definitional = []
        resolved_deletional = []
        unresolved = []

        for ref in linked_references:
            logger.info(f"Resolving reference: {ref.reference_text} ({ref.source.value})")
            try:
                resolved_ref = self._resolve_single_reference(
                    ref, original_article_text, target_article
                )
                if resolved_ref:
                    if ref.source == ReferenceSourceType.DEFINITIONAL:
                        resolved_definitional.append(resolved_ref)
                    else:
                        resolved_deletional.append(resolved_ref)
                else:
                    logger.warning(f"Failed to resolve reference: {ref.reference_text}")
                    unresolved.append(ref)
            except Exception as e:
                logger.error(
                    f"Exception processing reference '{ref.reference_text}': {e}",
                    exc_info=True,
                )
                unresolved.append(ref)

        return ResolutionResult(
            resolved_deletional_references=resolved_deletional,
            resolved_definitional_references=resolved_definitional,
            resolution_tree={},  # Placeholder for now
            unresolved_references=unresolved,
        )

    def _resolve_single_reference(
        self,
        ref: LinkedReference,
        original_article_text: str,
        target_article: Optional["TargetArticle"] = None,
    ) -> Optional[ResolvedReference]:
        """Orchestrates the resolution of a single linked reference."""
        source_content = None
        retrieval_metadata = {}

        try:
            if ref.source == ReferenceSourceType.DELETIONAL:
                # For DELETIONAL, we resolve against the original text, which is simpler.
                source_content = original_article_text
                retrieval_metadata = {"source": "original_article_text"}
            else:  # DEFINITIONAL
                source_content, retrieval_metadata = self._get_content_for_definitional_ref(
                    ref, target_article
                )

            if not source_content:
                logger.warning(f"No source content found for reference: {ref.reference_text}")
                return None
            
            resolved_content = self._extract_answer_from_content(
                source_content, ref
            )

            if resolved_content is None:
                logger.warning(f"Could not extract answer for reference: {ref.reference_text}")
                return None

            return ResolvedReference(
                linked_reference=ref,
                resolved_content=resolved_content,
                retrieval_metadata=retrieval_metadata,
            )
        except Exception as e:
            logger.error(f"Error resolving reference {ref.reference_text}: {e}", exc_info=True)
            return None

    def _get_content_for_definitional_ref(
        self, ref: LinkedReference, target_article: Optional["TargetArticle"] = None
    ) -> Tuple[Optional[str], Dict]:
        """
        Retrieves the source content for a definitional reference.
        This involves classifying the reference and using the OriginalTextRetriever.
        """
        code, article = self._classify_and_parse_definitional_ref(
            ref.reference_text, target_article
        )
        if not code or not article:
            logger.warning(
                f"Could not classify or parse definitional ref: {ref.reference_text}"
            )
            return None, {"error": "Classification failed"}

        return self.retriever.fetch_article_text(code, article)

    def _classify_and_parse_definitional_ref(
        self, reference_text: str, target_article: Optional["TargetArticle"] = None
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Parses a reference string to extract the code and article using an LLM.

        Args:
            reference_text: The reference text to parse.
            target_article: The containing target article, used for context.

        Returns:
            A tuple of (code, article).
        """
        contextual_code = target_article.code if target_article else ""
        parent_article = target_article.article if target_article else ""
        cache_key_data = {
            "reference_text": reference_text,
            "contextual_code": contextual_code,
            "parent_article": parent_article,
            "prompt": REFERENCE_PARSER_SYSTEM_PROMPT,
        }

        # Caching logic
        if self.use_cache:
            cached_result = self.cache.get("reference_resolver_parsing", cache_key_data)
            if cached_result:
                logger.info("✓ Found cached parsed reference.")
                return cached_result.get("code"), cached_result.get("article")

        user_payload = {
            "reference_text": reference_text,
            "contextual_code": contextual_code,
            "parent_article_for_context": parent_article,
        }

        result = call_mistral_json_model(
            client=self.mistral_client,
            rate_limiter=self.rate_limiter,
            system_prompt=REFERENCE_PARSER_SYSTEM_PROMPT,
            user_payload=user_payload,
            component_name="ReferenceResolver.parser",
        )

        if result:
            code = result.get("code")
            article = result.get("article")

            if self.use_cache and code and article:
                self.cache.set(
                    "reference_resolver_parsing",
                    cache_key_data,
                    {"code": code, "article": article},
                )

            return code, article

        return None, None

    def _extract_answer_from_content(
        self, source_content: str, ref: LinkedReference
    ) -> Optional[str]:
        """
        Uses an LLM to extract a specific answer from a source text based on a question.
        """
        user_payload = {
            "source_text": source_content,
            "question": ref.resolution_question,
            "reference_text": ref.reference_text,
            "referenced_object": ref.object,
        }

        result = call_mistral_json_model(
            client=self.mistral_client,
            rate_limiter=self.rate_limiter,
            system_prompt=QUESTION_GUIDED_EXTRACTION_SYSTEM_PROMPT,
            user_payload=user_payload,
            component_name="ReferenceResolver.extractor",
        )
            
        if result:
            return result.get("extracted_answer")

        return None
