"""
ReferenceResolver: Resolves linked references through question-guided content extraction.
"""

import json
import logging
import os
import re
from typing import Dict, List, Optional, Tuple

from mistralai import Mistral

from bill_parser_engine.core.reference_resolver.cache_manager import SimpleCache, get_cache
from bill_parser_engine.core.reference_resolver.models import (
    LinkedReference,
    ReferenceSourceType,
    ResolutionResult,
    ResolvedReference,
    TargetArticle,
)
from bill_parser_engine.core.reference_resolver.original_text_retriever import OriginalTextRetriever
from bill_parser_engine.core.reference_resolver.prompts import (
    QUESTION_GUIDED_EXTRACTION_SYSTEM_PROMPT,
    SUBSECTION_PARSER_SYSTEM_PROMPT,
    SUBSECTION_EXTRACTION_SYSTEM_PROMPT,
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
        api_key: Optional[str] = None,
        retriever: Optional[OriginalTextRetriever] = None,
        rate_limiter: Optional[RateLimiter] = None,
        cache: Optional[SimpleCache] = None,
        use_cache: bool = True,
    ):
        """
        Initializes the ReferenceResolver.

        Args:
            api_key: Mistral API key (defaults to MISTRAL_API_KEY environment variable)
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
        self.mistral_client = Mistral(api_key=api_key or os.getenv("MISTRAL_API_KEY"))

    def resolve_references(
        self,
        linked_references: List[LinkedReference],
        original_article_text: str,
        target_article: Optional[TargetArticle] = None,
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
                logger.error(f"Error resolving reference {ref.reference_text}: {e}")
                unresolved.append(ref)

        return ResolutionResult(
            resolved_definitional_references=resolved_definitional,
            resolved_deletional_references=resolved_deletional,
            unresolved_references=unresolved,
            resolution_tree={},  # Empty dict for now, can be enhanced later
        )

    def _resolve_single_reference(
        self,
        ref: LinkedReference,
        original_article_text: str,
        target_article: Optional[TargetArticle] = None,
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
            
            # Apply subsection extraction if applicable
            extracted_content = self._extract_subsection_if_applicable(
                source_content, ref.reference_text, retrieval_metadata
            )
            
            resolved_content = self._extract_answer_from_content(
                extracted_content, ref
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
        self, ref: LinkedReference, target_article: Optional[TargetArticle] = None
    ) -> Tuple[Optional[str], Dict]:
        """
        Retrieves the source content for a definitional reference.
        This involves classifying the reference and using the OriginalTextRetriever.
        Enhanced with EU file access optimization.
        """
        code, article = self._classify_and_parse_definitional_ref(
            ref.reference_text, target_article
        )
        if not code or not article:
            logger.warning(
                f"Could not classify or parse definitional ref: {ref.reference_text}"
            )
            return None, {"error": "Classification failed"}

        # Check if this is an EU reference that we can access directly
        eu_content = self._try_eu_file_access(ref.reference_text, code, article)
        if eu_content:
            logger.info(f"✓ EU file access successful for {ref.reference_text}")
            return eu_content, {"source": "eu_file", "success": True}

        # Fallback to OriginalTextRetriever
        return self.retriever.fetch_article_text(code, article)

    def _classify_and_parse_definitional_ref(
        self, reference_text: str, target_article: Optional[TargetArticle] = None
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

    def _get_eu_content_direct(self, regulation: str, article: str, point: str) -> Optional[str]:
        """
        Get EU content via direct file access instead of API.
        
        Args:
            regulation: Regulation name (e.g., "Règlement CE No 1107_2009")
            article: Article number (e.g., "3")
            point: Point number (e.g., "11")
            
        Returns:
            Content from the specific file, or None if not found
        """
        try:
            file_path = f"data/eu_law_text/{regulation}/Article_{article}/Point_{point}.md"
            if os.path.exists(file_path):
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    # Extract the actual content (skip markdown headers)
                    lines = content.split('\n')
                    # Find the content after the header
                    content_start = 0
                    for i, line in enumerate(lines):
                        if line.strip() and not line.startswith('#') and not line.startswith('---'):
                            content_start = i
                            break
                    return '\n'.join(lines[content_start:]).strip()
            return None
        except Exception as e:
            logger.warning(f"Failed to read EU file {file_path}: {e}")
            return None

    def _try_eu_file_access(self, reference_text: str, code: str, article: str) -> Optional[str]:
        """
        Try to access EU content directly from files based on reference patterns.
        
        Args:
            reference_text: The original reference text
            code: The parsed code (e.g., "règlement (CE) n° 1107/2009")
            article: The parsed article number
            
        Returns:
            Content if found, None otherwise
        """
        try:
            # Check if this is an EU regulation reference
            if "règlement" in code.lower() and "1107/2009" in code:
                regulation = "Règlement CE No 1107_2009"
                
                # Extract point number from reference text
                point_match = re.search(r'(\d+)(?:°|\)|\.)', reference_text)
                if point_match:
                    point = point_match.group(1)
                    
                    # Try direct file access
                    content = self._get_eu_content_direct(regulation, article, point)
                    if content:
                        return content
                        
                    # If point not found, try overview file
                    overview_path = f"data/eu_law_text/{regulation}/Article_{article}/overview.md"
                    if os.path.exists(overview_path):
                        with open(overview_path, 'r', encoding='utf-8') as f:
                            return f.read().strip()
            
            return None
        except Exception as e:
            logger.warning(f"EU file access failed for {reference_text}: {e}")
            return None

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

    def _extract_subsection_if_applicable(
        self, 
        source_content: str, 
        reference_text: str, 
        retrieval_metadata: Dict
    ) -> str:
        """
        Extract subsection from source content if the reference contains subsection patterns.
        
        Args:
            source_content: The full source content
            reference_text: The original reference text
            retrieval_metadata: Metadata about the retrieval
            
        Returns:
            Either the extracted subsection or the original content if no subsection pattern found
        """
        try:
            # Parse the reference to identify subsection patterns
            subsection_info = self._parse_subsection_pattern(reference_text)
            
            if not subsection_info:
                logger.debug(f"No subsection pattern found in reference: {reference_text}")
                return source_content
            
            # Extract the specific subsection
            extracted_content = self._extract_subsection_from_content(
                source_content, subsection_info
            )
            
            if extracted_content:
                # Update metadata to reflect subsection extraction
                retrieval_metadata["subsection_extraction"] = {
                    "pattern": reference_text,
                    "parsed_info": subsection_info,
                    "original_size": len(source_content),
                    "extracted_size": len(extracted_content),
                    "reduction_percentage": round((1 - len(extracted_content) / len(source_content)) * 100, 1)
                }
                logger.info(f"✓ Subsection extraction: {len(source_content)} → {len(extracted_content)} chars ({retrieval_metadata['subsection_extraction']['reduction_percentage']}% reduction)")
                return extracted_content
            else:
                logger.warning(f"Subsection pattern found but extraction failed: {reference_text}")
                return source_content
                
        except Exception as e:
            logger.warning(f"Subsection extraction failed for {reference_text}: {e}")
            return source_content

    def _parse_subsection_pattern(self, reference_text: str) -> Optional[Dict]:
        """
        Parse French legal hierarchy patterns to identify subsection information.
        
        Args:
            reference_text: The reference text to parse
            
        Returns:
            Parsed subsection information or None if no pattern found
        """
        # Check for common subsection patterns
        subsection_patterns = [
            r'au (\d+)° du ([IVX]+)',  # "au 3° du II"
            r'aux (\d+)° ou (\d+)° du ([IVX]+)',  # "aux 1° ou 2° du II"
            r'aux (\d+)° et (\d+)° du ([IVX]+)',  # "aux 1° et 2° du II"
            r'([a-z])\) du (\d+)° du ([IVX]+)',  # "a) du 1° du II"
            r'du ([IVX]+)',  # "du II"
        ]
        
        for pattern in subsection_patterns:
            match = re.search(pattern, reference_text, re.IGNORECASE)
            if match:
                if pattern == r'au (\d+)° du ([IVX]+)':
                    return {
                        "section": match.group(2),
                        "point": match.group(1),
                        "type": "point"
                    }
                elif pattern == r'aux (\d+)° ou (\d+)° du ([IVX]+)':
                    return {
                        "section": match.group(3),
                        "points": [match.group(1), match.group(2)],
                        "type": "multiple_points"
                    }
                elif pattern == r'aux (\d+)° et (\d+)° du ([IVX]+)':
                    return {
                        "section": match.group(3),
                        "points": [match.group(1), match.group(2)],
                        "type": "multiple_points"
                    }
                elif pattern == r'([a-z])\) du (\d+)° du ([IVX]+)':
                    return {
                        "section": match.group(3),
                        "point": match.group(2),
                        "subpoint": match.group(1),
                        "type": "subpoint"
                    }
                elif pattern == r'du ([IVX]+)':
                    return {
                        "section": match.group(1),
                        "type": "section_only"
                    }
        
        # If no regex pattern matches, try LLM-based parsing
        return self._parse_subsection_pattern_llm(reference_text)

    def _parse_subsection_pattern_llm(self, reference_text: str) -> Optional[Dict]:
        """
        Use LLM to parse complex subsection patterns that regex can't handle.
        
        Args:
            reference_text: The reference text to parse
            
        Returns:
            Parsed subsection information or None if no pattern found
        """
        try:
            user_payload = {
                "reference_text": reference_text
            }
            
            result = call_mistral_json_model(
                client=self.mistral_client,
                rate_limiter=self.rate_limiter,
                system_prompt=SUBSECTION_PARSER_SYSTEM_PROMPT,
                user_payload=user_payload,
                component_name="ReferenceResolver.subsection_parser",
            )
            
            if result and isinstance(result, dict):
                # Validate that we have at least a section
                if "section" in result:
                    return result
            
            return None
            
        except Exception as e:
            logger.warning(f"LLM subsection parsing failed for {reference_text}: {e}")
            return None

    def _extract_subsection_from_content(self, content: str, subsection_info: Dict) -> Optional[str]:
        """
        Extract the specific subsection from the content based on parsed information.
        
        Args:
            content: The full content to search in
            subsection_info: Parsed subsection information
            
        Returns:
            Extracted subsection content or None if not found
        """
        try:
            # Try regex-based extraction first for common patterns
            extracted = self._extract_subsection_regex(content, subsection_info)
            if extracted:
                return extracted
            
            # Fallback to LLM-based extraction
            return self._extract_subsection_llm(content, subsection_info)
            
        except Exception as e:
            logger.warning(f"Subsection extraction failed: {e}")
            return None

    def _extract_subsection_regex(self, content: str, subsection_info: Dict) -> Optional[str]:
        """
        Extract subsection using regex patterns for common cases.
        
        Args:
            content: The full content
            subsection_info: Parsed subsection information
            
        Returns:
            Extracted content or None
        """
        section = subsection_info.get("section")
        if not section:
            return None
        
        # Look for section patterns like "II.", "II -", "II :"
        section_patterns = [
            rf"{section}\.",
            rf"{section}\s*-",
            rf"{section}\s*:",
            rf"{section}\s*–",  # en dash
        ]
        
        for pattern in section_patterns:
            match = re.search(pattern, content, re.IGNORECASE | re.MULTILINE)
            if match:
                start_pos = match.start()
                
                # Find the end of this section (next section or end of content)
                next_section_match = re.search(rf"^[IVX]+\.", content[start_pos + 1:], re.MULTILINE)
                if next_section_match:
                    end_pos = start_pos + 1 + next_section_match.start()
                else:
                    end_pos = len(content)
                
                section_content = content[start_pos:end_pos].strip()
                
                # If we need a specific point within this section
                if "point" in subsection_info:
                    point_content = self._extract_point_from_section(
                        section_content, subsection_info
                    )
                    if point_content:
                        return point_content
                
                return section_content
        
        return None

    def _extract_point_from_section(self, section_content: str, subsection_info: Dict) -> Optional[str]:
        """
        Extract a specific point from a section.
        
        Args:
            section_content: The section content
            subsection_info: Parsed subsection information
            
        Returns:
            Extracted point content or None
        """
        point = subsection_info.get("point")
        if not point:
            return None
        
        # Look for point patterns like "3°", "3)", "3."
        point_patterns = [
            rf"{point}°",
            rf"{point}\)",
            rf"{point}\.",
        ]
        
        for pattern in point_patterns:
            match = re.search(pattern, section_content, re.MULTILINE)
            if match:
                start_pos = match.start()
                
                # Find the end of this point (next point or end of section)
                next_point_match = re.search(rf"^\d+[°\)\.]", section_content[start_pos + 1:], re.MULTILINE)
                if next_point_match:
                    end_pos = start_pos + 1 + next_point_match.start()
                else:
                    end_pos = len(section_content)
                
                return section_content[start_pos:end_pos].strip()
        
        return None

    def _extract_subsection_llm(self, content: str, subsection_info: Dict) -> Optional[str]:
        """
        Use LLM to extract subsection when regex fails.
        
        Args:
            content: The full content
            subsection_info: Parsed subsection information
            
        Returns:
            Extracted content or None
        """
        try:
            user_payload = {
                "article_text": content,
                "subsection_pattern": json.dumps(subsection_info, ensure_ascii=False)
            }
            
            result = call_mistral_json_model(
                client=self.mistral_client,
                rate_limiter=self.rate_limiter,
                system_prompt=SUBSECTION_EXTRACTION_SYSTEM_PROMPT,
                user_payload=user_payload,
                component_name="ReferenceResolver.subsection_extractor",
            )
            
            if result and isinstance(result, dict):
                extracted_content = result.get("extracted_subsection")
                if extracted_content and len(extracted_content.strip()) > 0:
                    return extracted_content.strip()
            
            return None
            
        except Exception as e:
            logger.warning(f"LLM subsection extraction failed: {e}")
            return None
