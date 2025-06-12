"""
Reference location component.

This component identifies all normative references in before/after text fragments
and tags them by source type. It uses Mistral API in JSON Mode for structured
output with precise positioning.
"""

import json
import logging
import os
import time
from typing import List

from mistralai import Mistral

from bill_parser_engine.core.reference_resolver.config import MISTRAL_MODEL
from bill_parser_engine.core.reference_resolver.models import (
    LocatedReference,
    ReconstructorOutput,
    ReferenceSourceType,
)
from bill_parser_engine.core.reference_resolver.rate_limiter import rate_limiter

logger = logging.getLogger(__name__)


class ReferenceLocator:
    """
    Locates all normative references in text fragments and tags by source type.
    
    This component implements the DELETIONAL/DEFINITIONAL classification that drives
    the entire downstream process. DELETIONAL references use original law context,
    DEFINITIONAL use amended text context.
    
    Uses Mistral Chat API in JSON Mode for structured list output with precise positioning.
    """

    def __init__(self, api_key: str = None):
        """
        Initialize the reference locator with Mistral client.

        Args:
            api_key: Mistral API key (defaults to MISTRAL_API_KEY environment variable)
        """
        self.client = Mistral(api_key=api_key or os.getenv("MISTRAL_API_KEY"))
        self.system_prompt = self._create_system_prompt()
        self.min_confidence = 0.5  # Lowered from 0.7 to allow more references for testing

    def _create_system_prompt(self) -> str:
        """
        Create the system prompt for reference location.
        
        Returns:
            The system prompt with real French legal reference examples
        """
        return """
You are a legal reference locator for French legislative texts. Given two text fragments from a legislative amendment process:
- deleted_or_replaced_text: the text that was deleted or replaced (tag references as 'DELETIONAL')
- intermediate_after_state_text: the text after the amendment (tag references as 'DEFINITIONAL')

Identify all normative references (to articles, codes, regulations, decrees, etc.) in both fragments. For each reference, return:
- reference_text: the exact phrase as it appears in the text
- start_position: character index in the relevant fragment (0-based)
- end_position: character index (exclusive, 0-based)
- source: 'DELETIONAL' or 'DEFINITIONAL' 
- confidence: 0-1 confidence score

Return a JSON object with a single field 'located_references', which is a list of these objects.

FRENCH LEGAL REFERENCE PATTERNS TO IDENTIFY:
- Internal cross-references: "aux 1° ou 2° du II", "au IV", "du même article", "au 3° du II de l'article L. 254-1"
- Code articles: "l'article L. 254-1", "à l'article L. 253-5 du présent code", "aux articles L. 254-6-2 et L. 254-6-3"
- EU regulations: "du règlement (CE) n° 1107/2009", "de l'article 3 du règlement (CE) n° 1107/2009"
- Relative references: "du même règlement", "dudit article"
- Numbered provisions: "au sens du 11 de l'article 3", "au sens de l'article 23"

EXAMPLE 1:
deleted_or_replaced_text: "incompatible avec celui des activités mentionnées aux 1° ou 2° du II ou au IV."
intermediate_after_state_text: "interdit aux producteurs au sens du 11 de l'article 3 du règlement (CE) n° 1107/2009, sauf lorsque la production concerne des produits de biocontrôle figurant sur la liste mentionnée à l'article L. 253-5 du présent code, des produits composés uniquement de substances de base au sens de l'article 23 du règlement (CE) n° 1107/2009 ou de produits à faible risque au sens de l'article 47 du même règlement (CE) n° 1107/2009 et des produits dont l'usage est autorisé dans le cadre de l'agriculture biologique"

Output:
{
  "located_references": [
    {
      "reference_text": "aux 1° ou 2° du II",
      "start_position": 44,
      "end_position": 63,
      "source": "DELETIONAL",
      "confidence": 0.98
    },
    {
      "reference_text": "au IV",
      "start_position": 67,
      "end_position": 72,
      "source": "DELETIONAL",
      "confidence": 0.95
    },
    {
      "reference_text": "du 11 de l'article 3 du règlement (CE) n° 1107/2009",
      "start_position": 28,
      "end_position": 81,
      "source": "DEFINITIONAL",
      "confidence": 0.99
    },
    {
      "reference_text": "à l'article L. 253-5 du présent code",
      "start_position": 162,
      "end_position": 199,
      "source": "DEFINITIONAL",
      "confidence": 0.97
    },
    {
      "reference_text": "au sens de l'article 23 du règlement (CE) n° 1107/2009",
      "start_position": 265,
      "end_position": 320,
      "source": "DEFINITIONAL",
      "confidence": 0.98
    },
    {
      "reference_text": "au sens de l'article 47 du même règlement (CE) n° 1107/2009",
      "start_position": 344,
      "end_position": 404,
      "source": "DEFINITIONAL",
      "confidence": 0.98
    }
  ]
}

EXAMPLE 2:
deleted_or_replaced_text: "Les modalités sont fixées par décret."
intermediate_after_state_text: "Les modalités sont fixées par arrêté."

Output:
{
  "located_references": []
}

EXAMPLE 3:
deleted_or_replaced_text: "prévu aux articles L. 254-6-2 et L. 254-6-3"
intermediate_after_state_text: "à l'utilisation des produits phytopharmaceutiques"

Output:
{
  "located_references": [
    {
      "reference_text": "aux articles L. 254-6-2 et L. 254-6-3",
      "start_position": 6,
      "end_position": 43,
      "source": "DELETIONAL",
      "confidence": 0.99
    }
  ]
}

IMPORTANT RULES:
- Be precise with character positions (0-based indexing)
- Only identify legal/normative references, not general mentions
- Include prepositions when they're part of the reference phrase
- Confidence should reflect clarity and precision of the reference
- Empty result list is valid when no references are found
"""

    def locate(self, reconstructor_output: ReconstructorOutput) -> List[LocatedReference]:
        """
        Locate all normative references in the before/after text fragments.

        Args:
            reconstructor_output: Output from TextReconstructor with before/after text fragments

        Returns:
            List of LocatedReference objects with precise positioning and source tagging

        Raises:
            ValueError: If input validation fails
        """
        # Input validation
        if not isinstance(reconstructor_output, ReconstructorOutput):
            raise ValueError("Input must be a ReconstructorOutput object")

        fragments = {
            "DELETIONAL": reconstructor_output.deleted_or_replaced_text,
            "DEFINITIONAL": reconstructor_output.intermediate_after_state_text
        }

        user_prompt = self._create_user_prompt(fragments)

        try:
            # Use shared rate limiter across all components
            rate_limiter.wait_if_needed("ReferenceLocator")
            
            response = self.client.chat.complete(
                model=MISTRAL_MODEL,
                temperature=0.0,
                messages=[
                    {
                        "role": "system",
                        "content": self.system_prompt
                    },
                    {
                        "role": "user",
                        "content": user_prompt
                    }
                ],
                response_format={"type": "json_object"}
            )

            content = json.loads(response.choices[0].message.content)
            self._validate_response(content)

            located_refs = []
            for ref_data in content.get("located_references", []):
                # Validation
                if not self._validate_reference_positioning(ref_data, fragments):
                    logger.warning(f"Invalid positioning for ref: {ref_data}")
                    continue

                located_ref = self._create_located_reference(ref_data)
                located_refs.append(located_ref)

            # Filter by confidence threshold
            return self._filter_by_confidence(located_refs, self.min_confidence)

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {e}")
            raise RuntimeError(f"ReferenceLocator failed to parse API response: {e}") from e
        except Exception as e:
            logger.error(f"Reference location failed: {e}")
            raise RuntimeError(f"ReferenceLocator API call failed: {e}") from e

    def _create_user_prompt(self, fragments: dict) -> str:
        """
        Create a user prompt with the text fragments.

        Args:
            fragments: Dictionary with DELETIONAL and DEFINITIONAL text fragments

        Returns:
            Formatted user prompt string
        """
        return json.dumps({
            "deleted_or_replaced_text": fragments["DELETIONAL"],
            "intermediate_after_state_text": fragments["DEFINITIONAL"]
        })

    def _validate_response(self, content: dict) -> None:
        """
        Validate that the response contains required structure.

        Args:
            content: Parsed JSON response from Mistral

        Raises:
            ValueError: If response structure is invalid
        """
        if "located_references" not in content:
            raise ValueError("Missing required field: located_references")

        if not isinstance(content["located_references"], list):
            raise ValueError("located_references must be a list")

    def _validate_reference_positioning(self, ref_data: dict, fragments: dict) -> bool:
        """
        Validate that reference positioning is correct with flexible position correction.

        Args:
            ref_data: Reference data from LLM response
            fragments: Text fragments to validate against

        Returns:
            True if positioning is valid, False otherwise
        """
        try:
            # Required fields check
            required_fields = ["reference_text", "start_position", "end_position", "source", "confidence"]
            for field in required_fields:
                if field not in ref_data:
                    logger.warning(f"Missing required field: {field}")
                    return False

            reference_text = ref_data["reference_text"]
            start_pos = ref_data["start_position"]
            end_pos = ref_data["end_position"]
            source = ref_data["source"]

            # Basic validation
            if start_pos < 0 or end_pos <= start_pos:
                logger.warning(f"Invalid positions: start={start_pos}, end={end_pos}")
                return False

            # Source validation
            if source not in ["DELETIONAL", "DEFINITIONAL"]:
                logger.warning(f"Invalid source: {source}")
                return False

            # Confidence validation
            confidence = ref_data["confidence"]
            if not (0 <= confidence <= 1):
                logger.warning(f"Invalid confidence: {confidence}")
                return False

            # Text existence and flexible position validation
            fragment_text = fragments[source]
            
            # Try exact position first
            if end_pos <= len(fragment_text):
                actual_text = fragment_text[start_pos:end_pos]
                if actual_text == reference_text:
                    return True

            # If exact position doesn't work, try to find the reference text nearby
            corrected_pos = self._find_reference_in_text(reference_text, fragment_text, start_pos)
            if corrected_pos is not None:
                # Update the positions in ref_data for downstream use
                ref_data["start_position"] = corrected_pos
                ref_data["end_position"] = corrected_pos + len(reference_text)
                logger.info(f"Corrected position for '{reference_text}' from {start_pos} to {corrected_pos}")
                return True

            # If we can't find it anywhere, log the issue but don't fail completely
            # Check if the reference exists anywhere in the text (loose validation)
            if reference_text in fragment_text:
                # Find the actual position
                actual_pos = fragment_text.find(reference_text)
                ref_data["start_position"] = actual_pos
                ref_data["end_position"] = actual_pos + len(reference_text)
                logger.info(f"Found '{reference_text}' at position {actual_pos} (LLM suggested {start_pos})")
                return True

            logger.warning(f"Reference text '{reference_text}' not found in {source} fragment")
            return False

        except Exception as e:
            logger.warning(f"Error validating reference positioning: {e}")
            return False

    def _find_reference_in_text(self, reference_text: str, fragment_text: str, suggested_pos: int, search_window: int = 50) -> int:
        """
        Find reference text within a search window around the suggested position.

        Args:
            reference_text: The reference text to find
            fragment_text: The text fragment to search in
            suggested_pos: The LLM-suggested position
            search_window: How many characters around suggested_pos to search

        Returns:
            Corrected start position or None if not found
        """
        # Define search bounds
        start_search = max(0, suggested_pos - search_window)
        end_search = min(len(fragment_text), suggested_pos + search_window + len(reference_text))
        
        # Search in the window
        search_text = fragment_text[start_search:end_search]
        local_pos = search_text.find(reference_text)
        
        if local_pos != -1:
            return start_search + local_pos
        
        return None

    def _create_located_reference(self, ref_data: dict) -> LocatedReference:
        """
        Create a LocatedReference object from validated reference data.

        Args:
            ref_data: Validated reference data from LLM

        Returns:
            LocatedReference object
        """
        return LocatedReference(
            reference_text=ref_data["reference_text"],
            start_position=ref_data["start_position"],
            end_position=ref_data["end_position"],
            source=ReferenceSourceType(ref_data["source"]),
            confidence=ref_data["confidence"]
        )

    def _filter_by_confidence(self, located_refs: List[LocatedReference], min_confidence: float) -> List[LocatedReference]:
        """
        Filter references by minimum confidence threshold.

        Args:
            located_refs: List of located references
            min_confidence: Minimum confidence threshold

        Returns:
            Filtered list of references
        """
        filtered_refs = [ref for ref in located_refs if ref.confidence >= min_confidence]
        
        if len(filtered_refs) < len(located_refs):
            logger.info(f"Filtered {len(located_refs) - len(filtered_refs)} low-confidence references")

        return filtered_refs 