"""
Reference object linking component.

This component links each located reference to its grammatical object using
context-aware French grammatical analysis. It implements smart context-switching:
DELETIONAL references are analyzed using deleted_or_replaced_text context,
while DEFINITIONAL references use intermediate_after_state_text context.

Uses Mistral Chat API with Function Calling for complex grammatical analysis.
"""

import json
import logging
import os
from typing import List, Optional

from mistralai import Mistral

from bill_parser_engine.core.reference_resolver.config import MISTRAL_MODEL
from bill_parser_engine.core.reference_resolver.models import (
    LinkedReference,
    LocatedReference,
    ReconstructorOutput,
    ReferenceSourceType,
)

logger = logging.getLogger(__name__)


class ReferenceObjectLinker:
    """
    Links each located reference to its grammatical object using French grammar analysis.
    
    This component implements smart context-switching - DELETIONAL references are analyzed
    using deleted_or_replaced_text context, while DEFINITIONAL references use 
    intermediate_after_state_text context. This ensures grammatical objects are found
    in the correct textual environment.
    
    Uses Mistral Chat API with Function Calling for complex grammatical analysis.
    """

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the reference object linker with Mistral client.

        Args:
            api_key: Mistral API key (defaults to MISTRAL_API_KEY environment variable)
        """
        self.client = Mistral(api_key=api_key or os.getenv("MISTRAL_API_KEY"))
        self.system_prompt = self._create_system_prompt()
        self.tool_schema = self._create_tool_schema()

    def _create_system_prompt(self) -> str:
        """
        Create the system prompt for French grammatical analysis.
        
        Returns:
            The system prompt with French legal reference-object linking examples
        """
        return """
You are a French legal text grammatical analyst. Your task is to link normative references to their grammatical objects using French grammatical analysis.

Given a legal reference and its surrounding context, identify the complete noun phrase that the reference modifies, defines, or clarifies. Pay careful attention to French grammatical agreement patterns:

FRENCH GRAMMATICAL PATTERNS:
- Masculine singular: "au sens du" → links to masculine singular nouns (e.g., "producteur")
- Feminine singular: "à la liste mentionnée à" → links to feminine singular nouns (e.g., "la liste")
- Masculine plural: "aux activités mentionnées aux" → links to masculine plural nouns (e.g., "activités")
- Feminine plural: "aux substances mentionnées aux" → links to feminine plural nouns (e.g., "substances")

REFERENCE-OBJECT RELATIONSHIP TYPES:
1. Definition references: "au sens de l'article X" → defines the meaning of a preceding noun
2. Specification references: "mentionnées aux articles X et Y" → specifies which items from a category
3. Location references: "figurant sur la liste mentionnée à" → indicates where something is found
4. Scope references: "dans le cadre de" → defines the scope or context

ANALYSIS APPROACH:
1. Examine the grammatical agreement between the reference and surrounding nouns
2. Consider the semantic relationship (what concept is being defined/specified)
3. Look for proximity and logical connections
4. Account for French legal text structure and conventions

EXAMPLES:

Example 1:
Reference: "aux 1° ou 2° du II"
Context: "incompatible avec celui des activités mentionnées aux 1° ou 2° du II ou au IV"
Analysis: "mentionnées" (feminine plural past participle) agrees with "activités" (feminine plural noun)
Object: "activités"

Example 2:
Reference: "du 11 de l'article 3 du règlement (CE) n° 1107/2009"
Context: "interdit aux producteurs au sens du 11 de l'article 3 du règlement (CE) n° 1107/2009"
Analysis: "au sens du" construction defines the meaning of "producteurs" (masculine plural noun)
Object: "producteurs"

Example 3:
Reference: "à l'article L. 253-5 du présent code"
Context: "produits de biocontrôle figurant sur la liste mentionnée à l'article L. 253-5"
Analysis: "mentionnée" (feminine singular past participle) agrees with "la liste" (feminine singular noun)
Object: "la liste"

Use the provided function to return your analysis with the grammatical object, reasoning, and confidence score.
"""

    def _create_tool_schema(self) -> List[dict]:
        """
        Create the function calling tool schema for grammatical analysis.
        
        Returns:
            The tool schema for the link_reference_to_object function
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": "link_reference_to_object",
                    "description": "Analyze French grammatical structure to link a legal reference to its object",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "object": {
                                "type": "string",
                                "description": "Complete noun phrase that the reference modifies (e.g., 'activités', 'producteurs', 'la liste')"
                            },
                            "agreement_analysis": {
                                "type": "string",
                                "description": "Grammatical reasoning (e.g., 'Masculine plural agreement with activités mentioned 3 words before')"
                            },
                            "confidence": {
                                "type": "number",
                                "description": "Confidence 0-1, lower for ambiguous cases or distant grammatical relationships"
                            }
                        },
                        "required": ["object", "agreement_analysis", "confidence"]
                    }
                }
            }
        ]

    def link_references(
        self, 
        located_references: List[LocatedReference], 
        reconstructor_output: ReconstructorOutput
    ) -> List[LinkedReference]:
        """
        Link located references to their grammatical objects using French grammar analysis.

        Args:
            located_references: List of references found by ReferenceLocator
            reconstructor_output: Output from TextReconstructor with context texts

        Returns:
            List of LinkedReference objects with grammatical objects identified

        Raises:
            ValueError: If input validation fails
        """
        # Input validation
        if not isinstance(located_references, list):
            raise ValueError("located_references must be a list")
        
        if not isinstance(reconstructor_output, ReconstructorOutput):
            raise ValueError("reconstructor_output must be a ReconstructorOutput object")

        linked_references = []

        for ref in located_references:
            try:
                # Context switching based on reference source
                context_text = self._select_context(ref.source, reconstructor_output)
                
                # Skip if no context available
                if not context_text.strip():
                    logger.warning(f"No context available for reference: {ref.reference_text}")
                    continue

                # Create grammatical analysis prompt
                user_prompt = self._build_grammatical_analysis_prompt(ref, context_text)

                # Call Mistral function calling
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
                    tools=self.tool_schema,
                    tool_choice="any"
                )

                # Extract and validate tool call
                tool_call = self._extract_tool_call(response)
                if tool_call and self._validate_tool_call_response(tool_call):
                    linked_ref = self._create_linked_reference(ref, tool_call["arguments"])
                    linked_references.append(linked_ref)
                    logger.info(f"Successfully linked reference: {ref.reference_text} → {linked_ref.object}")
                else:
                    logger.warning(f"Invalid tool call response for ref: {ref.reference_text}")

            except Exception as e:
                logger.error(f"Failed to link reference {ref.reference_text}: {e}")
                # Continue processing other references

        logger.info(f"Successfully linked {len(linked_references)} out of {len(located_references)} references")
        return linked_references

    def _select_context(self, source: ReferenceSourceType, output: ReconstructorOutput) -> str:
        """
        Select appropriate text context based on reference source type.

        Args:
            source: The source type of the reference (DELETIONAL or DEFINITIONAL)
            output: ReconstructorOutput containing both text contexts

        Returns:
            The appropriate context text for analysis
        """
        if source == ReferenceSourceType.DELETIONAL:
            return output.deleted_or_replaced_text
        else:
            return output.intermediate_after_state_text

    def _build_grammatical_analysis_prompt(self, ref: LocatedReference, context_text: str) -> str:
        """
        Build a contextual prompt for grammatical analysis.

        Args:
            ref: The located reference to analyze
            context_text: The appropriate context text

        Returns:
            Formatted prompt for grammatical analysis
        """
        return f"""
Analyze this French legal reference and identify its grammatical object:

REFERENCE TO ANALYZE: "{ref.reference_text}"

FULL CONTEXT: "{context_text}"

REFERENCE POSITION: characters {ref.start_position}-{ref.end_position}

REFERENCE SOURCE: {ref.source.value}

Please identify the complete noun phrase that this reference modifies, defines, or clarifies. Consider:
1. French grammatical agreement (gender, number)
2. Proximity and logical relationship
3. Semantic meaning in legal context
4. Preposition patterns (au/à la/aux, du/de la/des, etc.)

Use the function call to provide your analysis.
"""

    def _extract_tool_call(self, response) -> Optional[dict]:
        """
        Extract tool call from Mistral response.

        Args:
            response: The Mistral API response

        Returns:
            The tool call dictionary, or None if no valid tool call found
        """
        try:
            if not response.choices:
                logger.warning("No choices in Mistral response")
                return None

            choice = response.choices[0]
            if not hasattr(choice.message, 'tool_calls') or not choice.message.tool_calls:
                logger.warning("No tool calls in response")
                return None

            tool_call = choice.message.tool_calls[0]
            if tool_call.function.name != "link_reference_to_object":
                logger.warning(f"Unexpected function name: {tool_call.function.name}")
                return None

            # Parse the arguments
            arguments = json.loads(tool_call.function.arguments) if isinstance(tool_call.function.arguments, str) else tool_call.function.arguments
            
            return {
                "name": tool_call.function.name,
                "arguments": arguments
            }

        except Exception as e:
            logger.error(f"Error extracting tool call: {e}")
            return None

    def _validate_tool_call_response(self, tool_call: dict) -> bool:
        """
        Validate that tool call response contains required fields.

        Args:
            tool_call: The extracted tool call dictionary

        Returns:
            True if valid, False otherwise
        """
        try:
            arguments = tool_call.get("arguments", {})
            required_fields = ["object", "agreement_analysis", "confidence"]
            
            for field in required_fields:
                if field not in arguments:
                    logger.warning(f"Missing required field in tool call: {field}")
                    return False
                    
                # Validate types
                if field == "confidence":
                    confidence = arguments[field]
                    if not isinstance(confidence, (int, float)) or not (0 <= confidence <= 1):
                        logger.warning(f"Invalid confidence value: {confidence}")
                        return False
                elif not isinstance(arguments[field], str) or not arguments[field].strip():
                    logger.warning(f"Invalid {field} value: {arguments[field]}")
                    return False

            return True

        except Exception as e:
            logger.error(f"Error validating tool call: {e}")
            return False

    def _create_linked_reference(self, ref: LocatedReference, arguments: dict) -> LinkedReference:
        """
        Create a LinkedReference object from the tool call results.

        Args:
            ref: The original located reference
            arguments: The validated tool call arguments

        Returns:
            A LinkedReference object
        """
        return LinkedReference(
            reference_text=ref.reference_text,
            source=ref.source,
            object=arguments["object"].strip(),
            agreement_analysis=arguments["agreement_analysis"].strip(),
            confidence=float(arguments["confidence"])
        ) 