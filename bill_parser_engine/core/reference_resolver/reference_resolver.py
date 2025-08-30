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
    EU_FILE_MATCHER_SYSTEM_PROMPT,
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
        qa_retry_window_chars: int = 300,
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
        # Parameter controlling the context window length for the guarded QA retry
        self.qa_retry_window_chars = max(50, int(qa_retry_window_chars or 300))

    def resolve_references(
        self,
        linked_references: List[LinkedReference],
        original_article_text: str,
        target_article: Optional[TargetArticle] = None,
        intermediate_after_state_text: str = "",
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
                    ref, original_article_text, target_article, intermediate_after_state_text
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
        intermediate_after_state_text: str = "",
    ) -> Optional[ResolvedReference]:
        """Orchestrates the resolution of a single linked reference."""
        source_content = None
        retrieval_metadata = {}

        try:
            logger.info(f"Resolving reference: {ref.reference_text} (source: {ref.source.value})")
            
            if ref.source == ReferenceSourceType.DELETIONAL:
                # For DELETIONAL, we resolve against the original text, which is simpler.
                source_content = original_article_text
                retrieval_metadata = {"source": "original_article_text"}
                logger.info(f"Using original article text for DELETIONAL reference")
            else:  # DEFINITIONAL
                source_content, retrieval_metadata = self._get_content_for_definitional_ref(
                    ref, target_article, intermediate_after_state_text
                )
                logger.info(f"Retrieved content for DEFINITIONAL reference: {len(source_content) if source_content else 0} chars")

            if not source_content:
                logger.warning(f"No source content found for reference: {ref.reference_text}")
                return None
            
            # Apply subsection extraction if applicable
            # Gate for DELETIONAL: only extract if a clear pattern exists
            if ref.source == ReferenceSourceType.DELETIONAL:
                # Regex-only parse to avoid LLM for DELETIONAL
                parsed = self._parse_subsection_pattern_regex_only(ref.reference_text)
                if parsed:
                    # Regex-only extraction; if it fails, keep full content
                    extracted_content = self._extract_subsection_regex(source_content, parsed) or source_content
                else:
                    extracted_content = source_content
            else:
                extracted_content = self._extract_subsection_if_applicable(
                    source_content, ref.reference_text, retrieval_metadata
                )
            
            resolved_content = self._extract_answer_from_content(
                extracted_content, ref
            )

            # Guarded retry: if we carved down substantially and QA returned empty, try once with a windowed context
            if (
                (resolved_content is None or (isinstance(resolved_content, str) and len(resolved_content.strip()) == 0))
                and ref.source == ReferenceSourceType.DEFINITIONAL
            ):
                try:
                    reduction = (
                        retrieval_metadata.get("subsection_extraction", {}).get("reduction_percentage", 0)
                        if isinstance(retrieval_metadata, dict)
                        else 0
                    )
                except Exception:
                    reduction = 0

                if reduction and reduction >= 50:
                    logger.info(
                        "Applying single guarded retry for QA extraction after large carve (%.1f%% reduction)",
                        reduction,
                    )
                    retry_answer = self._retry_extract_answer_with_subsection_hint(
                        full_source_content=source_content,
                        carved_content=extracted_content,
                        ref=ref,
                        retrieval_metadata=retrieval_metadata,
                    )
                    if retry_answer and retry_answer.strip():
                        resolved_content = retry_answer

            if resolved_content is None or (isinstance(resolved_content, str) and len(resolved_content.strip()) == 0):
                logger.warning(f"Could not extract answer for reference: {ref.reference_text}")
                logger.info(f"Question was: {ref.resolution_question}")
                logger.info(f"Object was: {ref.object}")
                return None

            logger.info(f"Successfully resolved reference: {ref.reference_text}")
            return ResolvedReference(
                linked_reference=ref,
                resolved_content=resolved_content,
                retrieval_metadata=retrieval_metadata,
            )
        except Exception as e:
            logger.error(f"Error resolving reference {ref.reference_text}: {e}", exc_info=True)
            return None

    def _get_content_for_definitional_ref(
        self, ref: LinkedReference, target_article: Optional[TargetArticle] = None, intermediate_after_state_text: str = ""
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

        logger.info(f"🔍 DEBUG: About to retrieve content for code='{code}', article='{article}'")

        # Prefer after-state for references to the newly inserted target article (same chunk)
        try:
            from bill_parser_engine.core.reference_resolver.models import TargetOperationType as _TO
            def _base(a: str) -> str:
                return a.split('(')[0].strip() if a else a
            if (
                target_article is not None
                and target_article.operation_type == _TO.INSERT
                and target_article.code
                and target_article.article
                and code and article
                and _base(target_article.code).lower() == _base(code).lower()
                and _base(target_article.article).lower() == _base(article).lower()
                and intermediate_after_state_text.strip()
            ):
                logger.info("Using intermediate after-state for reference to newly inserted target article")
                return intermediate_after_state_text, {"source": "after_state_insert_target", "success": True}

            # If the reference text contains internal subsection tokens (e.g., du II, au 2° du II), carve from after-state first
            if target_article is not None and intermediate_after_state_text.strip():
                parsed_hint = self._parse_subsection_pattern_regex_only(ref.reference_text)
                if parsed_hint:
                    carved = self._extract_subsection_if_applicable(
                        intermediate_after_state_text, ref.reference_text, {}
                    )
                    if carved and carved.strip():
                        logger.info("✓ Extracted subsection from intermediate after-state for internal reference")
                        return carved, {"source": "after_state_carve", "success": True}
        except Exception:
            # Non-fatal
            pass

        # Prefer deterministic retrieval path first (handles both French codes and EU files)
        logger.info(f"📚 Using OriginalTextRetriever for {ref.reference_text}")
        content_text, meta = self.retriever.fetch_article_text(code, article)
        logger.info(f"🔍 DEBUG: Retrieval result - success: {meta.get('success')}, content length: {len(content_text) if content_text else 0}, source: {meta.get('source')}")
        if meta.get("success") and content_text:
            logger.info(f"🔍 DEBUG: Successfully retrieved content from {meta.get('source')} - first 100 chars: '{content_text[:100]}...'")
            return content_text, meta

        # If deterministic retriever failed and this looks like an EU reference, try LLM-based file matching as fallback
        is_eu_reference = self._is_eu_regulation_reference(ref.reference_text, code)
        if is_eu_reference:
            logger.info("🔍 Deterministic retrieval failed or returned empty; attempting EU LLM file access fallback")
            eu_content = self._try_eu_file_access(ref.reference_text, code, article)
            if eu_content:
                logger.info(f"✓ EU file access successful for {ref.reference_text}")
                return eu_content, {"source": "eu_file", "success": True}

        # Final fallback
        return content_text, meta

    def _retry_extract_answer_with_subsection_hint(
        self,
        *,
        full_source_content: str,
        carved_content: str,
        ref: LinkedReference,
        retrieval_metadata: Dict,
    ) -> Optional[str]:
        """Retry QA extraction once using a slightly expanded windowed context around the carved span.

        Strategy:
        - Find the carved span within the full source content; if found, expand by a fixed window (e.g., 150 chars) on both sides.
        - Re-run the question-guided extraction on this expanded context.
        - If the carved span isn't found, fall back to using the full source content (bounded by max length if needed in the future).
        """
        try:
            if not full_source_content or not carved_content:
                return None

            idx = full_source_content.find(carved_content)
            if idx != -1:
                # Use the configured window size to provide surrounding context
                start = max(0, idx - self.qa_retry_window_chars)
                end = min(
                    len(full_source_content), idx + len(carved_content) + self.qa_retry_window_chars
                )
                window = full_source_content[start:end]
            else:
                # If we cannot locate the carved content, use the original full content
                window = full_source_content

            # Build a slightly more specific question by embedding subsection hints and a compact preview
            try:
                subsection_info = retrieval_metadata.get("subsection_extraction", {}).get("parsed_info")
            except Exception:
                subsection_info = None

            # Create a compact preview from the carved content (first/last 120 chars)
            try:
                head = carved_content[:120].replace("\n", " ")
                tail = carved_content[-120:].replace("\n", " ") if len(carved_content) > 120 else carved_content.replace("\n", " ")
                preview = f"{head} … {tail}" if len(carved_content) > 240 else head
            except Exception:
                preview = ""

            # Augment the question minimally to guide extraction
            augmented_question = ref.resolution_question
            hint_parts = []
            if subsection_info:
                hint_parts.append(f"Sous-section parsée: {json.dumps(subsection_info, ensure_ascii=False)}")
            if preview:
                hint_parts.append(f"Contexte autour de la sous-section: «{preview}»")
            if hint_parts:
                augmented_question = f"{ref.resolution_question} (Indice: {'; '.join(hint_parts)})"

            retry_ref = LinkedReference(
                reference_text=ref.reference_text,
                source=ref.source,
                object=ref.object,
                agreement_analysis=ref.agreement_analysis,
                confidence=ref.confidence,
                resolution_question=augmented_question,
            )

            return self._extract_answer_from_content(window, retry_ref)
        except Exception as e:
            logger.warning(f"Guarded retry for QA extraction failed: {e}")
            return None

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
        logger.info(f"🔍 DEBUG: Parsing reference '{reference_text}'")
        contextual_code = target_article.code if target_article else ""
        parent_article = target_article.article if target_article else ""
        if target_article:
            logger.info(f"🔍 DEBUG: Target article context - code: '{contextual_code}', article: '{parent_article}'")
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
            
            logger.info(f"🔍 DEBUG: Parsed result - code: '{code}', article: '{article}'")

            if self.use_cache and code and article:
                self.cache.set(
                    "reference_resolver_parsing",
                    cache_key_data,
                    {"code": code, "article": article},
                )

            return code, article

        return None, None

    def _is_eu_regulation_reference(self, reference_text: str, code: str) -> bool:
        """
        Intelligently determine if a reference is likely an EU regulation reference.
        
        Args:
            reference_text: The reference text to analyze
            code: The parsed code
            
        Returns:
            True if this appears to be an EU regulation reference
        """
        # EU regulation patterns we support
        eu_patterns = [
            "règlement (CE) n° 1107/2009",
            "règlement (CE) n°1107/2009", 
            "reglement (CE) n° 1107/2009",
            "reglement (CE) n°1107/2009",
            "Directive 2011/92/UE",
            "Directive 2010/75/UE", 
            "Directive 2009/128/CE",
            "du même règlement",
            "du même reglement",
            "précité"  # if context suggests EU regulation
        ]
        
        # Check if the code contains EU regulation patterns
        code_lower = code.lower()
        for pattern in eu_patterns:
            if pattern.lower() in code_lower:
                logger.info(f"✅ EU pattern detected in code: '{pattern}' in '{code}'")
                return True
        
        # Check if the reference text contains EU regulation patterns
        ref_lower = reference_text.lower()
        for pattern in eu_patterns:
            if pattern.lower() in ref_lower:
                logger.info(f"✅ EU pattern detected in reference: '{pattern}' in '{reference_text}'")
                return True
        
        # Check for specific EU regulation number patterns
        if "1107/2009" in reference_text or "1107/2009" in code:
            logger.info(f"✅ EU regulation number detected: 1107/2009")
            return True
            
        if any(year in reference_text for year in ["2011/92", "2010/75", "2009/128"]):
            logger.info(f"✅ EU directive number detected in reference")
            return True
        
        # Internal French law references (NOT EU)
        internal_patterns = [
            "au 3° du II",
            "aux 1° ou 2° du II", 
            "au IV",
            "du II",
            "du même article",
            "du présent code",
            "de ce code",
            "du code",
            "prévu aux articles",
            "mentionnée à l'article",
            "figurant sur la liste"
        ]
        
        for pattern in internal_patterns:
            if pattern.lower() in ref_lower:
                logger.info(f"❌ Internal French law pattern detected: '{pattern}' in '{reference_text}'")
                return False
        
        logger.info(f"❓ Ambiguous reference, defaulting to non-EU: '{reference_text}'")
        return False

    def _scan_eu_law_structure(self) -> str:
        """
        Scan the EU law text directory structure and return a formatted string for the LLM.
        
        Returns:
            Formatted string describing the available EU law text structure
        """
        try:
            logger.info("🔍 Scanning EU law text structure...")
            # Use absolute path relative to project root
            eu_base_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "data", "eu_law_text")
            logger.info(f"🔍 EU base path: {eu_base_path}")
            
            if not os.path.exists(eu_base_path):
                logger.warning(f"❌ EU base path not found: {eu_base_path}")
                return "Aucune structure de fichiers EU disponible"
            
            structure_lines = []
            regulation_count = 0
            article_count = 0
            point_count = 0
            
            for regulation_dir in os.listdir(eu_base_path):
                regulation_path = os.path.join(eu_base_path, regulation_dir)
                if os.path.isdir(regulation_path):
                    regulation_count += 1
                    structure_lines.append(f"\n📁 {regulation_dir}/")
                    logger.info(f"📁 Found regulation: {regulation_dir}")
                    
                    # Scan articles in this regulation
                    for item in os.listdir(regulation_path):
                        item_path = os.path.join(regulation_path, item)
                        if os.path.isdir(item_path) and item.startswith("Article_"):
                            article_num = item.replace("Article_", "")
                            article_count += 1
                            structure_lines.append(f"  📄 Article_{article_num}/")
                            logger.info(f"  📄 Found article: Article_{article_num}")
                            
                            # Scan points in this article
                            points = []
                            has_overview = False
                            for subitem in os.listdir(item_path):
                                if subitem == "overview.md":
                                    has_overview = True
                                    logger.info(f"    📋 Found overview.md for Article_{article_num}")
                                elif subitem.startswith("Point_") and subitem.endswith(".md"):
                                    point_num = subitem.replace("Point_", "").replace(".md", "")
                                    points.append(point_num)
                                    point_count += 1
                            
                            if has_overview:
                                structure_lines.append(f"    📋 overview.md")
                            if points:
                                points.sort(key=lambda x: int(x) if x.isdigit() else 0)
                                structure_lines.append(f"    📌 Points: {', '.join(points)}")
                                logger.info(f"    📌 Found {len(points)} points for Article_{article_num}: {', '.join(points)}")
                        elif item.endswith(".md"):
                            structure_lines.append(f"  📄 {item}")
                            logger.info(f"  📄 Found standalone file: {item}")
            
            structure_summary = "\n".join(structure_lines)
            logger.info(f"✅ EU structure scan complete: {regulation_count} regulations, {article_count} articles, {point_count} points")
            logger.info(f"📊 Structure size: {len(structure_summary)} characters")
            
            return structure_summary
            
        except Exception as e:
            logger.warning(f"❌ Failed to scan EU law structure: {e}")
            return "Erreur lors du scan de la structure EU"

    def _try_eu_file_access_llm(self, reference_text: str, code: str, article: str) -> Optional[str]:
        """
        Use LLM to intelligently match EU references to file structure.
        
        Args:
            reference_text: The original reference text
            code: The parsed code (e.g., "règlement (CE) n° 1107/2009")
            article: The parsed article number
            
        Returns:
            Content if found, None otherwise
        """
        try:
            logger.info(f"🔍 EU LLM file access: reference='{reference_text}'")
            logger.info(f"🔍 EU LLM file access: code='{code}', article='{article}'")
            
            # Since we already filtered for EU references, proceed directly with LLM matching
            logger.info("✅ EU regulation confirmed, using LLM for file matching")
            
            # Get EU file structure
            logger.info("🔍 Getting EU file structure for LLM...")
            eu_structure = self._scan_eu_law_structure()
            logger.info(f"📊 EU structure provided to LLM ({len(eu_structure)} chars)")
            
            # Prepare user payload
            user_payload = {
                "reference_text": reference_text,
                "parsed_code": code,
                "parsed_article": article,
                "context": f"Code parsé: {code}, Article parsé: {article}"
            }
            logger.info(f"📤 LLM payload prepared: {user_payload}")
            
            # Call LLM for file matching
            logger.info("🤖 Calling LLM for EU file matching...")
            result = call_mistral_json_model(
                client=self.mistral_client,
                rate_limiter=self.rate_limiter,
                system_prompt=EU_FILE_MATCHER_SYSTEM_PROMPT.format(eu_file_structure=eu_structure),
                user_payload=user_payload,
                component_name="ReferenceResolver.eu_file_matcher",
            )
            
            logger.info(f"🤖 LLM raw response: {result}")
            logger.info(f"🤖 LLM response type: {type(result)}")
            
            if result and isinstance(result, dict):
                # Try to extract fields with better error handling
                try:
                    file_path = result.get("file_path")
                    file_type = result.get("file_type")
                    confidence = result.get("confidence", 0)
                    explanation = result.get("explanation", "")
                    article_number = result.get("article_number")
                    point_number = result.get("point_number")
                    
                    logger.info(f"🤖 LLM response parsed:")
                    logger.info(f"  📁 File path: {file_path}")
                    logger.info(f"  📄 File type: {file_type}")
                    logger.info(f"  🎯 Article number: {article_number}")
                    logger.info(f"  📌 Point number: {point_number}")
                    logger.info(f"  📊 Confidence: {confidence}")
                    logger.info(f"  💭 Explanation: {explanation}")
                    
                    if file_path and confidence > 0.7:
                        logger.info(f"✅ LLM file match accepted (confidence {confidence} > 0.7)")
                        
                        # Try to read the file
                        eu_base_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "data", "eu_law_text")
                        full_path = os.path.join(eu_base_path, file_path)
                        logger.info(f"📂 Attempting to read file: {full_path}")
                        
                        if os.path.exists(full_path):
                            logger.info(f"✅ File exists, reading content...")
                            with open(full_path, 'r', encoding='utf-8') as f:
                                content = f.read()
                                logger.info(f"📄 Raw file content: {len(content)} characters")
                                
                                # Extract the actual content (skip markdown headers)
                                lines = content.split('\n')
                                # Find the content after the header
                                content_start = 0
                                for i, line in enumerate(lines):
                                    if line.strip() and not line.startswith('#') and not line.startswith('---'):
                                        content_start = i
                                        break
                                extracted_content = '\n'.join(lines[content_start:]).strip()
                                logger.info(f"✅ Successfully extracted content: {len(extracted_content)} characters")
                                logger.info(f"📝 Content preview: {extracted_content[:200]}...")
                                return extracted_content
                        else:
                            logger.warning(f"❌ EU file not found: {full_path}")
                            logger.info(f"🔍 Checking if directory exists: {os.path.dirname(full_path)}")
                            if os.path.exists(os.path.dirname(full_path)):
                                logger.info(f"📁 Directory exists, listing contents:")
                                try:
                                    for item in os.listdir(os.path.dirname(full_path)):
                                        logger.info(f"    📄 {item}")
                                except Exception as e:
                                    logger.warning(f"❌ Failed to list directory contents: {e}")
                            else:
                                logger.warning(f"❌ Directory does not exist: {os.path.dirname(full_path)}")
                    else:
                        logger.info(f"❌ LLM file match rejected: confidence {confidence} too low or no file path")
                        if not file_path:
                            logger.info("❌ No file path provided by LLM")
                        if confidence <= 0.7:
                            logger.info(f"❌ Confidence {confidence} below threshold 0.7")
                except Exception as parse_error:
                    logger.warning(f"❌ Error parsing LLM response fields: {parse_error}")
                    logger.info(f"🔍 Raw result keys: {list(result.keys()) if isinstance(result, dict) else 'Not a dict'}")
            else:
                logger.warning(f"❌ LLM returned invalid result: {result}")
                if result:
                    logger.info(f"🔍 Result type: {type(result)}")
                    logger.info(f"🔍 Result content: {str(result)[:200]}...")
            
            logger.info("❌ LLM file matching failed or no match found")
            return None
            
        except Exception as e:
            logger.warning(f"❌ EU LLM file access failed for {reference_text}: {e}")
            logger.info(f"🔍 Exception details: {type(e).__name__}: {str(e)}")
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
        # Use the new LLM-based approach
        return self._try_eu_file_access_llm(reference_text, code, article)

    def _extract_answer_from_content(
        self, source_content: str, ref: LinkedReference
    ) -> Optional[str]:
        """
        Uses an LLM to extract a specific answer from a source text based on a question.
        """
        logger.info(f"🔍 DEBUG: _extract_answer_from_content called for '{ref.reference_text}'")
        logger.info(f"🔍 DEBUG: Question: '{ref.resolution_question}'")
        logger.info(f"🔍 DEBUG: Source content length: {len(source_content)}")
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
        
        logger.info(f"🔍 DEBUG: LLM extraction result: {result}")
        
        if result:
            extracted = result.get("extracted_answer")
            logger.info(f"🔍 DEBUG: Extracted answer: '{extracted}'")
            return extracted

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
            logger.info(f"🔍 DEBUG: _extract_subsection_if_applicable called for '{reference_text}'")
            logger.info(f"🔍 DEBUG: Retrieval metadata: {retrieval_metadata}")
            
            # Skip subsection extraction for EU references that were already resolved to specific files
            if (retrieval_metadata.get("extraction_method") == "direct_file" or 
                retrieval_metadata.get("source") == "eu_file"):
                logger.info(f"🔍 DEBUG: Skipping subsection extraction for EU reference with direct file access: {reference_text}")
                return source_content
            
            # Parse the reference to identify subsection patterns
            subsection_info = self._parse_subsection_pattern(reference_text)
            logger.info(f"🔍 DEBUG: Subsection pattern parsing result: {subsection_info}")
            
            if not subsection_info:
                logger.info(f"🔍 DEBUG: No subsection pattern found in reference: {reference_text}")
                return source_content
            
            # Extract the specific subsection
            logger.info(f"🔍 DEBUG: About to extract subsection from content (length: {len(source_content)})")
            extracted_content = self._extract_subsection_from_content(
                source_content, subsection_info
            )
            logger.info(f"🔍 DEBUG: Subsection extraction result - length: {len(extracted_content) if extracted_content else 0}")
            
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
                # LLM extraction failed - log and return original content
                logger.warning(f"LLM subsection extraction failed for: {reference_text}")
                logger.debug(f"Subsection pattern was: {subsection_info}")
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
        # Check for common subsection patterns (robust roman numerals and suffixes)
        roman = r"[IVXLCDM]+(?:\s+(?:bis|ter|quater|quinquies|sexies|septies|octies|nonies|decies))?"
        subsection_patterns = [
            rf'au\s+(\d+)°\s+du\s+({roman})',                  # 0: "au 3° du II"
            rf'aux\s+(\d+)°\s+ou\s+(\d+)°\s+du\s+({roman})',  # 1: "aux 1° ou 2° du II"
            rf'aux\s+(\d+)°\s+et\s+(\d+)°\s+du\s+({roman})',  # 2: "aux 1° et 2° du II"
            rf'([a-z])\)\s+du\s+(\d+)°\s+du\s+({roman})',      # 3: "a) du 1° du II"
            rf'du\s+({roman})',                                    # 4: "du II"
            rf'(?i)(premier|deuxième|troisième)\s+alinéa(?:\s+du\s+({roman}))?',  # 5: textual alinéa
        ]

        for idx, pattern in enumerate(subsection_patterns):
            match = re.search(pattern, reference_text, re.IGNORECASE)
            if not match:
                continue
            if idx == 0:
                return {"section": match.group(2), "point": match.group(1), "type": "point"}
            if idx in (1, 2):
                return {"section": match.group(3), "points": [match.group(1), match.group(2)], "type": "multiple_points"}
            if idx == 3:
                return {"section": match.group(3), "point": match.group(2), "subpoint": match.group(1), "type": "subpoint"}
            if idx == 4:
                return {"section": match.group(1), "type": "section_only"}
            if idx == 5:
                al_map = {"premier": 1, "deuxième": 2, "troisième": 3}
                al_token = match.group(1).lower()
                info = {"type": "alinea", "alinea_index": al_map.get(al_token, 1)}
                if match.group(2):
                    info["section"] = match.group(2)
                return info
        
        # If no regex pattern matches, try LLM-based parsing
        return self._parse_subsection_pattern_llm(reference_text)

    def _parse_subsection_pattern_regex_only(self, reference_text: str) -> Optional[Dict]:
        """
        Regex-only variant used to avoid triggering LLM for DELETIONAL gating.
        Enhanced with paragraph pattern recognition for complex subsection references.
        """
        roman = r"[IVXLCDM]+(?:\s+(?:bis|ter|quater|quinquies|sexies|septies|octies|nonies|decies))?"
        
        # French ordinal number mapping
        french_ordinals = {
            "premier": 1, "deuxième": 2, "troisième": 3, "quatrième": 4, 
            "cinquième": 5, "sixième": 6, "septième": 7, "huitième": 8,
            "neuvième": 9, "dixième": 10
        }
        ordinal_pattern = "|".join(french_ordinals.keys())
        
        subsection_patterns = [
            # Enhanced: Paragraph patterns (premier alinéa du VI)
            rf'(?:au\s+)?({ordinal_pattern})\s+alinéa\s+du\s+({roman})',
            # Existing patterns
            rf'au\s+(\d+)°\s+du\s+({roman})',
            rf'aux\s+(\d+)°\s+ou\s+(\d+)°\s+du\s+({roman})',
            rf'aux\s+(\d+)°\s+et\s+(\d+)°\s+du\s+({roman})',
            rf'([a-z])\)\s+du\s+(\d+)°\s+du\s+({roman})',
            rf'du\s+({roman})',
        ]
        
        for pattern in subsection_patterns:
            match = re.search(pattern, reference_text, re.IGNORECASE)
            if match:
                # Handle paragraph patterns
                if 'alinéa' in pattern:
                    ordinal = match.group(1).lower()
                    section = match.group(2)
                    paragraph_num = french_ordinals.get(ordinal)
                    if paragraph_num:
                        logger.info(f"Recognized paragraph pattern: {ordinal} alinéa du {section} → paragraph {paragraph_num}")
                        return {"section": section, "paragraph": paragraph_num, "type": "paragraph"}
                # Handle existing patterns
                elif pattern.startswith('au') and not 'alinéa' in pattern:
                    return {"section": match.group(2), "point": match.group(1), "type": "point"}
                elif pattern.startswith('aux') and 'ou' in pattern:
                    return {"section": match.group(3), "points": [match.group(1), match.group(2)], "type": "multiple_points"}
                elif pattern.startswith('aux') and 'et' in pattern:
                    return {"section": match.group(3), "points": [match.group(1), match.group(2)], "type": "multiple_points"}
                elif pattern.startswith('([a-z]'):
                    return {"section": match.group(3), "point": match.group(2), "subpoint": match.group(1), "type": "subpoint"}
                elif pattern.startswith('du'):
                    return {"section": match.group(1), "type": "section_only"}
        return None

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
        # Handle alinéa-only extraction without explicit section
        if subsection_info.get("type") == "alinea" and not section:
            return self._extract_alinea_from_text(content, subsection_info.get("alinea_index", 1))
        if not section:
            return None
        
        # Look for section patterns like "II.", "II -", "II –" (multiline anchored)
        section_patterns = [
            rf"(?m)^\s*{section}\s*[\.\-–]\s",
        ]
        
        for pattern in section_patterns:
            match = re.search(pattern, content, re.IGNORECASE | re.MULTILINE)
            if match:
                start_pos = match.start()
                
                # Find the end of this section (next section or end of content)
                # Use word boundaries and avoid matching substrings of current section
                next_section_match = re.search(rf"(?m)^\s*(?!{section}[^\w])([IVXLCDM]+(?:\s+(?:bis|ter|quater|quinquies|sexies|septies|octies|nonies|decies))?)\s*[\.\-–]\s", content[start_pos + len(match.group()):])
                if next_section_match:
                    end_pos = start_pos + len(match.group()) + next_section_match.start()
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
                # If we need a specific paragraph (alinéa) within this section
                if subsection_info.get("type") == "paragraph" and "paragraph" in subsection_info:
                    paragraph_content = self._extract_alinea_from_text(
                        section_content, subsection_info["paragraph"]
                    )
                    if paragraph_content:
                        logger.info(f"✓ Extracted paragraph {subsection_info['paragraph']} from section {section}")
                        return paragraph_content
                # If we need a specific alinéa within this section (legacy support)
                if subsection_info.get("type") == "alinea":
                    al_content = self._extract_alinea_from_text(section_content, subsection_info.get("alinea_index", 1))
                    if al_content:
                        return al_content
                
                return section_content
        
        return None

    def _extract_alinea_from_text(self, text: str, alinea_index: int) -> Optional[str]:
        """
        Extract the nth alinéa (paragraph). 
        In French legal terminology, alinéa refers to substantive paragraphs excluding section headers.
        Uses blank-line separation; falls back to sentence split.
        """
        try:
            paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
            if not paragraphs:
                paragraphs = [p.strip() for p in re.split(r"(?<=[.!?])\s+\n?", text) if p.strip()]
            
            # Filter out section headers (paragraphs starting with Roman numerals followed by punctuation)
            substantive_paragraphs = []
            for para in paragraphs:
                # Skip section headers like "VI. –", "II -", etc.
                if re.match(r'^\s*[IVXLCDM]+(?:\s+(?:bis|ter|quater|quinquies|sexies|septies|octies|nonies|decies))?\s*[\.\-–]\s', para):
                    logger.info(f"Skipping section header paragraph: {para[:50]}...")
                    continue
                substantive_paragraphs.append(para)
            
            logger.info(f"Found {len(substantive_paragraphs)} substantive paragraphs (excluding headers)")
            
            if 1 <= alinea_index <= len(substantive_paragraphs):
                selected_paragraph = substantive_paragraphs[alinea_index - 1]
                logger.info(f"Selected alinéa {alinea_index}: {selected_paragraph[:100]}...")
                return selected_paragraph
            return None
        except Exception:
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
        
        # Look for point patterns like "3°" (multiline anchored)
        point_patterns = [
            rf"(?m)^\s*{point}°\b",
            rf"(?m)^\s*{point}\)\b",
            rf"(?m)^\s*{point}\.\b",
        ]
        
        for pattern in point_patterns:
            match = re.search(pattern, section_content, re.MULTILINE)
            if match:
                start_pos = match.start()
                
                # Find the end of this point (next point or end of section)
                next_point_match = re.search(rf"(?m)^\s*\d+°\b|^\s*\d+\)\b|^\s*\d+\.\b", section_content[start_pos + 1:])
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
