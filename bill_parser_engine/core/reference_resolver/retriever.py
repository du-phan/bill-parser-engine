"""
Text retrieval component for normative references.

This component retrieves text content for classified references using pylegifrance
API and web search fallback.
"""

import os
import json
import logging
import hashlib
import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple, Any
import requests
from mistralai import Mistral

from .models import Reference, ReferenceSource, ReferenceType
from .prompts import LLM_SECTION_EXTRACTION_PROMPT
from .config import MISTRAL_MODEL

# Import pylegifrance conditionally to handle cases where it's not installed
try:
    from pylegifrance import recherche_code
    from pylegifrance.models.constants import CodeNom
    PYLEGIFRANCE_AVAILABLE = True
except ImportError:
    PYLEGIFRANCE_AVAILABLE = False
    logging.warning("pylegifrance package not available. API retrieval will be disabled.")


class TextRetriever:
    """
    Retrieves text content for normative references using pylegifrance API with web search fallback.
    
    This component is responsible for:
    1. Fetching text content for classified references
    2. Extracting the specific relevant portion from the source
    3. Validating the retrieved content
    4. Handling fallbacks when primary retrieval methods fail
    """

    def __init__(self, cache_dir: Optional[str] = None):
        """
        Initialize the retriever.
        
        Args:
            cache_dir: Optional directory for caching retrieved content
        """
        # Set up the cache directory
        self.cache_dir = cache_dir
        if self.cache_dir:
            cache_path = Path(self.cache_dir)
            cache_path.mkdir(parents=True, exist_ok=True)
        
        self.cache = {}  # In-memory cache
        
        # Check for API credentials
        self.api_available = (
            PYLEGIFRANCE_AVAILABLE and 
            os.environ.get("LEGIFRANCE_CLIENT_ID") and 
            os.environ.get("LEGIFRANCE_CLIENT_SECRET")
        )
        
        if not self.api_available:
            logging.warning(
                "Legifrance API credentials not found. Set LEGIFRANCE_CLIENT_ID and "
                "LEGIFRANCE_CLIENT_SECRET environment variables for API access."
            )
            
        # Initialize code name mapping for pylegifrance
        self._initialize_code_mapping()

    def retrieve(self, reference: Reference) -> Tuple[Optional[str], Dict[str, Any]]:
        """
        Retrieve the text content for a given reference.
        
        This is the main entry point for text retrieval. It first checks the cache,
        then tries the API, and finally falls back to web search if needed.

        Args:
            reference: The classified reference to retrieve text for

        Returns:
            Tuple of (retrieved text content or None, metadata dictionary)
        """
        # Check if we have this reference in memory cache
        cache_key = self._create_cache_key(reference)
        if cache_key in self.cache:
            logging.debug(f"Cache hit (memory): {cache_key}")
            return self.cache[cache_key], {"source": "memory_cache", "cache_key": cache_key}
        
        # Check if we have this reference in file cache
        if self.cache_dir:
            file_cache_result = self._check_file_cache(cache_key)
            if file_cache_result:
                logging.debug(f"Cache hit (file): {cache_key}")
                content, metadata = file_cache_result
                # Also update memory cache
                self.cache[cache_key] = content
                return content, {**metadata, "source": "file_cache", "cache_key": cache_key}
        
        # Prepare metadata dict
        metadata = {
            "reference_text": reference.text,
            "reference_type": reference.reference_type.value if reference.reference_type else None,
            "reference_source": reference.source.value if reference.source else None,
            "components": reference.components,
            "retrieval_method": None,
            "error": None,
            "retrieval_timestamp": datetime.datetime.now().isoformat(),
            "reference_object": reference.object if hasattr(reference, "object") else None
        }
        
        # Try API first if available
        if self.api_available:
            try:
                content, api_metadata = self._retrieve_from_api(reference)
                if content:
                    metadata.update(api_metadata)
                    metadata["retrieval_method"] = "api"
                    # Cache the result
                    self._cache_result(cache_key, content, metadata)
                    return content, metadata
            except Exception as e:
                logging.error(f"API retrieval failed: {e}")
                metadata["error"] = str(e)
        
        # Fallback to web search
        try:
            content, web_metadata = self._retrieve_from_web_search(reference)
            if content:
                metadata.update(web_metadata)
                metadata["retrieval_method"] = "web_search"
                # Cache the result
                self._cache_result(cache_key, content, metadata)
                return content, metadata
        except Exception as e:
            logging.error(f"Web search retrieval failed: {e}")
            metadata["error"] = str(e) if "error" not in metadata else f"{metadata['error']}; {e}"
        
        # If all retrieval methods fail, return None
        return None, metadata

    def _retrieve_from_api(self, reference: Reference) -> Tuple[Optional[str], Dict[str, Any]]:
        """
        Retrieve text from the Legifrance API using pylegifrance.
        
        Args:
            reference: The classified reference
            
        Returns:
            Tuple of (retrieved text content or None, metadata dictionary)
        """
        metadata = {
            "api_source": "legifrance",
            "reference_object": reference.object if hasattr(reference, "object") else None
        }
        
        # Add version information if available
        if hasattr(reference, "version") and reference.version:
            metadata["reference_version"] = reference.version
            
        # Add date information if available
        if hasattr(reference, "date") and reference.date:
            metadata["reference_date"] = reference.date
        
        # We need at least a code or article to query
        if not reference.components:
            return None, {"error": "No components available for API retrieval"}
        
        # Handle different reference sources
        if reference.source in [ReferenceSource.FRENCH_CODE, ReferenceSource.CODE_ENVIRONNEMENT]:
            return self._retrieve_french_code(reference, metadata)
        elif reference.source == ReferenceSource.EU_REGULATION:
            return self._retrieve_eu_regulation(reference, metadata)
        elif reference.source == ReferenceSource.DECREE:
            return self._retrieve_decree(reference, metadata)
        elif reference.source == ReferenceSource.LAW:
            return self._retrieve_law(reference, metadata)
        else:
            return None, {"error": f"Unsupported reference source: {reference.source}"}
    
    def _retrieve_french_code(self, reference: Reference, metadata: Dict[str, Any]) -> Tuple[Optional[str], Dict[str, Any]]:
        """
        Retrieve text from a French legal code using pylegifrance.
        Handles EXPLICIT_DIRECT, EXPLICIT_COMPLETE, and EXPLICIT_SECTION reference types.
        For IMPLICIT_* types, returns an error indicating they are not handled here.
        """
        components = reference.components or {}
        code_name = components.get("code")
        article_id = components.get("article")
        section = components.get("section")
        paragraph = components.get("paragraph")

        if not code_name:
            return None, {**metadata, "error": "Missing 'code' in reference components"}
        if not article_id:
            return None, {**metadata, "error": "Missing 'article' in reference components"}

        code_enum = self._map_code_to_enum(code_name)
        if not code_enum:
            return None, {**metadata, "error": f"Unknown code: {code_name}"}
        metadata["api_code_enum"] = code_enum.name

        # Handle explicit types for external retrieval
        if reference.reference_type in [
            ReferenceType.EXPLICIT_DIRECT,
            ReferenceType.EXPLICIT_COMPLETE,
            ReferenceType.EXPLICIT_SECTION,
        ]:
            try:
                search_article = self._clean_article_id(article_id)
                logging.info(f"recherche_code params: code_enum={code_enum}, search_article={search_article}")
                result = recherche_code(code_name=code_enum, search=search_article)

                # Check if result is valid (should be a list)
                if not result or not isinstance(result, list) or len(result) == 0:
                    return None, {**metadata, "error": f"No results found for article '{article_id}' in '{code_name}'"}

                article_contents = []
                article_ids = []
                # Store HTML for the first article if available
                first_article_html = None

                for idx, item in enumerate(result):
                    article_data = item.get("article", {})
                    if not article_data:
                        continue
                    article_text = article_data.get("texte", "")
                    if article_text:
                        article_contents.append(article_text)
                    article_ids.append(article_data.get("id"))
                    if idx == 0:
                        metadata["article_id"] = article_data.get("id")
                        metadata["article_num"] = article_data.get("num")
                        metadata["article_version"] = article_data.get("versionArticle")
                        metadata["article_date_debut"] = article_data.get("dateDebut")
                        if article_data.get("texteHtml"):
                            first_article_html = article_data.get("texteHtml")
                if first_article_html:
                    metadata["article_html"] = first_article_html

                if not article_contents:
                    return None, {**metadata, "error": f"No text content found for article '{article_id}' in '{code_name}'"}

                content = "\n\n".join(article_contents)

                # Extract section/paragraph if present
                if section or paragraph:
                    content = self._extract_section_or_paragraph_llm(
                        content,
                        section,
                        paragraph,
                        reference.object if hasattr(reference, "object") else None
                    )

                metadata["api_response"] = "success"
                metadata["found_articles"] = article_ids
                metadata["article_count"] = len(article_ids)

                return content.strip(), metadata

            except Exception as e:
                logging.error(f"API retrieval error: {e}")
                return None, {**metadata, "error": f"API retrieval error: {str(e)}"}

        # For implicit types, return a clear error/status
        elif reference.reference_type in [
            ReferenceType.IMPLICIT_CONTEXTUAL,
            ReferenceType.IMPLICIT_RELATIVE,
            ReferenceType.IMPLICIT_ABBREVIATED,
        ]:
            return None, {**metadata, "error": f"ReferenceType '{reference.reference_type.value}' is implicit and must be resolved by the ReferenceResolver, not the TextRetriever."}
        else:
            return None, {**metadata, "error": f"Unsupported reference type: {reference.reference_type}"}
    
    def _retrieve_eu_regulation(self, reference: Reference, metadata: Dict[str, Any]) -> Tuple[Optional[str], Dict[str, Any]]:
        """
        Retrieve text from an EU regulation.
        
        Args:
            reference: The classified reference
            metadata: Metadata dictionary to update
            
        Returns:
            Tuple of (retrieved text content or None, metadata dictionary)
        """
        # This would need to be implemented with a different API or web search
        # For now, we'll fall back to web search through the main retrieve method
        return None, {**metadata, "error": "EU regulations not directly supported by Legifrance API"}
    
    def _retrieve_decree(self, reference: Reference, metadata: Dict[str, Any]) -> Tuple[Optional[str], Dict[str, Any]]:
        """
        Retrieve text from a French decree.
        
        Args:
            reference: The classified reference
            metadata: Metadata dictionary to update
            
        Returns:
            Tuple of (retrieved text content or None, metadata dictionary)
        """
        # For decrees, we would need to use a different endpoint of the Legifrance API
        # For now, we'll fall back to web search through the main retrieve method
        return None, {**metadata, "error": "Decree retrieval not yet implemented"}
    
    def _retrieve_law(self, reference: Reference, metadata: Dict[str, Any]) -> Tuple[Optional[str], Dict[str, Any]]:
        """
        Retrieve text from a French law.
        
        Args:
            reference: The classified reference
            metadata: Metadata dictionary to update
            
        Returns:
            Tuple of (retrieved text content or None, metadata dictionary)
        """
        # For laws, we would need to use a different endpoint of the Legifrance API
        # For now, we'll fall back to web search through the main retrieve method
        return None, {**metadata, "error": "Law retrieval not yet implemented"}

    def _retrieve_from_web_search(self, reference: Reference) -> Tuple[Optional[str], Dict[str, Any]]:
        """
        Retrieve text from web search as fallback.

        Args:
            reference: The classified reference

        Returns:
            Tuple of (retrieved text content or None, metadata dictionary)
        """
        metadata = {
            "search_source": "web",
            "reference_object": reference.object if hasattr(reference, "object") else None
        }
        
        # Construct search query
        query_components = []
        
        # Add the reference text if available
        if reference.text:
            query_components.append(reference.text)
        
        # Add components if available
        if reference.components:
            if "code" in reference.components:
                query_components.append(reference.components["code"])
            if "article" in reference.components:
                query_components.append(reference.components["article"])
            if "section" in reference.components:
                query_components.append(f'section {reference.components["section"]}')
            if "paragraph" in reference.components:
                query_components.append(f'paragraphe {reference.components["paragraph"]}')
                
        # Add the object if available
        if hasattr(reference, "object") and reference.object:
            query_components.append(f'"{reference.object}"')
            
        # Add a source qualifier if available
        if reference.source:
            source_name = reference.source.value if hasattr(reference.source, "value") else str(reference.source)
            query_components.append(source_name)
            
        # Construct the final query
        query = " ".join(query_components)
        metadata["search_query"] = query
        
        # This is a placeholder - in a real implementation, you would use a web search API
        # For now, we'll just return a mock response for testing purposes
        mock_content = f"[Mock content for {query}]"
        
        # In a real implementation, you would:
        # 1. Call a search API (Google Custom Search, Bing, etc.)
        # 2. Get the top results
        # 3. Scrape the content from the most authoritative source
        # 4. Extract the relevant portion
        
        # For now, we'll add this placeholder logic
        try:
            # Mock implementation - this should be replaced with actual web search logic
            content = mock_content
            
            # Validate the retrieved content
            is_valid = self._validate_text(content, reference)
            metadata["is_valid"] = is_valid
            
            if not is_valid:
                logging.warning(f"Retrieved content may not be valid for reference: {reference.text}")
                metadata["warning"] = "Retrieved content may not be valid"
                
            return content, metadata
            
        except Exception as e:
            logging.error(f"Web search failed: {e}")
            return None, {**metadata, "error": str(e)}

    def _validate_text(self, text: str, reference: Reference) -> bool:
        """
        Validate the retrieved text to ensure it matches the reference and is relevant
        to the object being defined or constrained.
        
        Args:
            text: The retrieved text
            reference: The reference object
            
        Returns:
            True if valid, False otherwise
        """
        if not text:
            return False
        
        # Basic validation: check if the text contains keywords from the reference
        components = reference.components or {}
        validation_checks = []
        
        # Check for article number
        if "article" in components:
            article_check = components["article"] in text
            validation_checks.append(article_check)
        
        # Check for code name
        if "code" in components:
            code_check = components["code"].lower() in text.lower()
            validation_checks.append(code_check)
            
        # Check for section/paragraph if specified
        if "section" in components:
            section_check = components["section"] in text
            validation_checks.append(section_check)
            
        if "paragraph" in components:
            paragraph_check = components["paragraph"] in text
            validation_checks.append(paragraph_check)
            
        # Check for the object if available
        if hasattr(reference, "object") and reference.object:
            # Check if the object or related terms appear in the text
            object_terms = reference.object.lower().split()
            # The object check passes if at least half of the terms are found
            object_matches = sum(1 for term in object_terms if term.lower() in text.lower())
            object_check = object_matches >= max(1, len(object_terms) // 2)
            validation_checks.append(object_check)
            
        # Consider valid if at least 60% of checks pass
        # This is a heuristic and can be adjusted based on observed performance
        if not validation_checks:
            return False
            
        return sum(validation_checks) / len(validation_checks) >= 0.6
    
    def _cache_result(self, cache_key: str, content: str, metadata: Dict[str, Any]) -> None:
        """
        Cache the retrieval result in memory and file system if configured.
        
        Args:
            cache_key: The cache key
            content: The retrieved content
            metadata: Metadata about the retrieval
        """
        # Update memory cache
        self.cache[cache_key] = content
        
        # Update file cache if configured
        if self.cache_dir:
            try:
                cache_path = Path(self.cache_dir) / f"{cache_key}.json"
                with open(cache_path, 'w', encoding='utf-8') as f:
                    json.dump({
                        "content": content,
                        "metadata": metadata,
                        "timestamp": datetime.datetime.now().isoformat()
                    }, f, ensure_ascii=False, indent=2)
                logging.debug(f"Cached to file: {cache_key}")
            except Exception as e:
                logging.error(f"Failed to write to file cache: {e}")
    
    def _check_file_cache(self, cache_key: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        """
        Check if a result exists in the file cache.
        
        Args:
            cache_key: The cache key
            
        Returns:
            Tuple of (content, metadata) if found, None otherwise
        """
        if not self.cache_dir:
            return None
            
        cache_path = Path(self.cache_dir) / f"{cache_key}.json"
        if not cache_path.exists():
            return None
            
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data["content"], data["metadata"]
        except Exception as e:
            logging.error(f"Failed to read from file cache: {e}")
            return None
        
    def _create_cache_key(self, reference: Reference) -> str:
        """
        Create a cache key for a reference.
        
        Args:
            reference: The reference object
            
        Returns:
            Cache key string
        """
        components = reference.components or {}
        code = components.get("code", "unknown")
        article = components.get("article", "unknown")
        section = components.get("section", "")
        paragraph = components.get("paragraph", "")
        
        # Add version/date information if available
        version = ""
        if hasattr(reference, "version") and reference.version:
            version = f"@{reference.version}"
        elif hasattr(reference, "date") and reference.date:
            version = f"@{reference.date}"
            
        # Create a base key
        base_key = f"{code}@{article}@{section}@{paragraph}{version}".replace(" ", "_")
        
        # For very long or complex keys, use a hash
        if len(base_key) > 100:
            # Create a hash of the full reference text + components
            ref_hash = hashlib.md5(
                (reference.text + str(components)).encode('utf-8')
            ).hexdigest()
            return f"{code[:30]}_{article[:20]}_{ref_hash[:10]}"
        
        return base_key
    
    def _initialize_code_mapping(self):
        """Initialize mapping between code names and pylegifrance CodeNom enum values."""
        self.code_to_enum = {}
        
        if PYLEGIFRANCE_AVAILABLE:
            # Map common code names to CodeNom enum values (only valid ones)
            self.code_to_enum = {
                "code civil": CodeNom.CCIV,
                "code de procédure civile": CodeNom.CPRCIV,
                "code de commerce": CodeNom.CCOM,
                "code du travail": CodeNom.CTRAV,
                "code de la propriété intellectuelle": CodeNom.CPI,
                "code pénal": CodeNom.CPEN,
                "code de procédure pénale": CodeNom.CPP,
                "code des assurances": CodeNom.CASSUR,
                "code de la consommation": CodeNom.CCONSO,
                "code de la sécurité intérieure": CodeNom.CSI,
                "code de la santé publique": CodeNom.CSP,
                "code de la sécurité sociale": CodeNom.CSS,
                "code de l'entrée et du séjour des étrangers et du droit d'asile": CodeNom.CESEDA,
                "code général des collectivités territoriales": CodeNom.CGCT,
                "code des postes et des communications électroniques": CodeNom.CPCE,
                "code de l'environnement": CodeNom.CENV,
                "code de justice administrative": CodeNom.CJA,
                "code rural et de la pêche maritime": CodeNom.CREDLPM,
            }
    
    def _map_code_to_enum(self, code_name: str) -> Optional[CodeNom]:
        """
        Map a code name to its corresponding CodeNom enum value.
        
        Args:
            code_name: The name of the code
            
        Returns:
            CodeNom enum value or None if not found
        """
        if not PYLEGIFRANCE_AVAILABLE:
            return None
            
        # Try exact match
        if code_name.lower() in self.code_to_enum:
            return self.code_to_enum[code_name.lower()]
        
        # Try partial match
        for key, value in self.code_to_enum.items():
            if key.lower() in code_name.lower() or code_name.lower() in key.lower():
                return value
        
        return None
    
    def _clean_article_id(self, article_id: str) -> str:
        """
        Clean an article ID for searching.
        
        Args:
            article_id: The article identifier
            
        Returns:
            Cleaned article ID
        """
        # Remove spaces, normalize hyphens
        cleaned = article_id.replace(" ", "").replace("‑", "-")
        
        # If it starts with "L." or similar, ensure proper format
        if "." in cleaned and cleaned[0].isalpha():
            parts = cleaned.split(".")
            if len(parts) == 2:
                cleaned = f"{parts[0]}{parts[1]}"
        
        return cleaned
    
    def _extract_section_or_paragraph_llm(self, content: str, section: str = None, paragraph: str = None, reference_object: str = None) -> str:
        """
        Extract a specific section or paragraph from content using LLM, focused on relevance to a particular object.
        """
        section_label = f"section {section}" if section else ""
        paragraph_label = f", paragraph {paragraph}" if paragraph else ""
        object_label = reference_object or "the main subject"
        prompt = LLM_SECTION_EXTRACTION_PROMPT.format(
            section_label=section_label,
            paragraph_label=paragraph_label,
            object=object_label,
            article_text=content
        )
        # Call the LLM (placeholder implementation)
        return self._call_llm(prompt).strip()

    def _call_llm(self, prompt: str) -> str:
        """
        Call the Mistral LLM using the official Python client for chat completion.
        """
        api_key = os.getenv("MISTRAL_API_KEY", "")
        if not api_key:
            raise RuntimeError("MISTRAL_API_KEY environment variable is not set.")

        with Mistral(api_key=api_key) as mistral:
            res = mistral.chat.complete(
                model=MISTRAL_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
            )
            # Return the content of the first choice
            return res.choices[0].message.content.strip() if res.choices and res.choices[0].message.content else ""

    def _extract_section_or_paragraph(self, content: str, section: Optional[str], 
                                     paragraph: Optional[str]) -> str:
        """
        Extract a specific section or paragraph from content.
        
        Args:
            content: The full content
            section: Section identifier (e.g., "II")
            paragraph: Paragraph identifier (e.g., "3°")
            
        Returns:
            Extracted content section
        """
        # If neither section nor paragraph specified, return full content
        if not section and not paragraph:
            return content
            
        lines = content.split("\n")
        result = []
        in_section = not section  # If no section specified, consider always in section
        in_paragraph = not paragraph  # If no paragraph specified, consider always in paragraph
        current_section = None
        current_paragraph = None
        
        # Regular expressions could be used for more precise matching
        # For now, we'll use a simple approach with string matching
        
        for i, line in enumerate(lines):
            line_stripped = line.strip()
            
            # Check for section headers (Roman numerals like "I.", "II.", "III.")
            if section and not in_section:
                # Look for Roman numerals followed by period or dash
                if line_stripped.startswith(section) and (
                    line_stripped.startswith(f"{section}.") or 
                    line_stripped.startswith(f"{section} -") or
                    line_stripped.startswith(f"{section}.-") or
                    line_stripped == section
                ):
                    in_section = True
                    current_section = section
                    # Reset paragraph tracking when entering a new section
                    in_paragraph = not paragraph
                    current_paragraph = None
            
            # Check for paragraph markers (like "1°", "2°", "3°")
            if paragraph and in_section and not in_paragraph:
                if paragraph in line_stripped and (
                    line_stripped.startswith(paragraph) or
                    f" {paragraph} " in line_stripped or
                    f"({paragraph})" in line_stripped
                ):
                    in_paragraph = True
                    current_paragraph = paragraph
            
            # Check for the end of the current section
            if in_section and current_section and i < len(lines) - 1:
                next_line = lines[i + 1].strip()
                # If we see a new Roman numeral section, we've left the current section
                for roman in ["I.", "II.", "III.", "IV.", "V.", "VI.", "VII.", "VIII.", "IX.", "X."]:
                    if (roman != f"{current_section}." and 
                        (next_line.startswith(roman) or next_line.startswith(f"{roman[:-1]} "))):
                        in_section = section is None  # Only exit if we were looking for a specific section
                        if in_section:
                            current_section = None
                            in_paragraph = not paragraph  # Reset paragraph tracking
                
            # Check for the end of the current paragraph
            if in_paragraph and current_paragraph and i < len(lines) - 1:
                next_line = lines[i + 1].strip()
                # If we see a new numbered paragraph, we've left the current paragraph
                if current_paragraph.endswith("°"):
                    paragraph_num = int(current_paragraph[:-1]) if current_paragraph[:-1].isdigit() else 0
                    next_paragraph = f"{paragraph_num + 1}°"
                    if next_line.startswith(next_paragraph) or f" {next_paragraph} " in next_line:
                        in_paragraph = paragraph is None  # Only exit if we were looking for a specific paragraph
                        
            # If we're in both the right section and paragraph, collect the line
            if in_section and in_paragraph:
                result.append(line)
        
        if not result:
            # If we couldn't find the specific section/paragraph, return the full content
            # This is a fallback to ensure we don't lose information
            logging.warning(f"Could not extract section={section}, paragraph={paragraph}. Returning full content.")
            return content
            
        return "\n".join(result) 