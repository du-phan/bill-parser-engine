"""
Legal state synthesis component.

This component performs the final substitution of resolved references into text fragments
to create BeforeState and AfterState. It uses Mistral API in JSON Mode for structured
output with grammatically correct text synthesis.
"""

import json
import logging
import os
from typing import Dict, List, Optional

from mistralai import Mistral

from bill_parser_engine.core.reference_resolver.config import MISTRAL_MODEL
from bill_parser_engine.core.reference_resolver.models import (
    BillChunk,
    LegalAnalysisOutput,
    LegalState,
    ReconstructorOutput,
    ResolutionResult,
    ResolvedReference,
    TargetArticle,
)
from bill_parser_engine.core.reference_resolver.rate_limiter import rate_limiter

logger = logging.getLogger(__name__)


class LegalStateSynthesizer:
    """
    Performs final substitution of resolved references into text fragments.
    
    This is the final step that creates the lawyer-readable, fully interpretable legal states.
    Quality of substitution directly impacts pipeline usefulness.
    
    Uses Mistral Chat API in JSON Mode for structured output with grammatically correct
    text synthesis maintaining French legal text style and readability.
    """

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the synthesizer with Mistral client.

        Args:
            api_key: Mistral API key (defaults to MISTRAL_API_KEY environment variable)
        """
        self.client = Mistral(api_key=api_key or os.getenv("MISTRAL_API_KEY"))
        self.system_prompt = self._create_system_prompt()

    def _create_system_prompt(self) -> str:
        """
        Create the system prompt for legal state synthesis.
        
        Returns:
            The system prompt with reference substitution examples
        """
        return """
You are a legal text synthesizer. Given text fragments and their resolved references, substitute each reference with its resolved content to create fully interpretable legal states.

For each reference, replace the reference phrase with its resolved content while maintaining:
- Grammatical correctness in French
- Legal text style and readability  
- Proper punctuation and formatting
- Natural flow and coherence

Return a JSON object with:
- synthesized_text: the text with all references substituted (string)
- synthesis_metadata: information about the substitution process (object)

EXAMPLE 1:
Input text: "incompatible avec celui des activités mentionnées aux 1° ou 2° du II"
Resolved references:
- "aux 1° ou 2° du II" -> "la vente et la distribution de produits phytopharmaceutiques"
Output:
{
  "synthesized_text": "incompatible avec celui des activités de vente et de distribution de produits phytopharmaceutiques",
  "synthesis_metadata": {
    "references_substituted": 1,
    "substitutions": ["aux 1° ou 2° du II"],
    "quality": "high"
  }
}

EXAMPLE 2:
Input text: "interdit aux producteurs au sens du 11 de l'article 3 du règlement (CE) n° 1107/2009"
Resolved references:
- "du 11 de l'article 3 du règlement (CE) n° 1107/2009" -> "toute personne physique ou morale qui fabrique une substance active, un phytoprotecteur, un synergiste ou un produit phytopharmaceutique, ou qui fait fabriquer de telles substances ou de tels produits et les commercialise sous son nom"
Output:
{
  "synthesized_text": "interdit aux personnes physiques ou morales qui fabriquent une substance active, un phytoprotecteur, un synergiste ou un produit phytopharmaceutique, ou qui font fabriquer de telles substances ou de tels produits et les commercialisent sous leur nom",
  "synthesis_metadata": {
    "references_substituted": 1,
    "substitutions": ["du 11 de l'article 3 du règlement (CE) n° 1107/2009"],
    "quality": "high"
  }
}

EXAMPLE 3 (Complex substitution):
Input text: "les produits de biocontrôle figurant sur la liste mentionnée à l'article L. 253-5 du présent code"
Resolved references:
- "à l'article L. 253-5 du présent code" -> "la liste des produits de biocontrôle dont l'usage est autorisé, établie par l'autorité administrative compétente et régulièrement mise à jour"
Output:
{
  "synthesized_text": "les produits de biocontrôle figurant sur la liste des produits de biocontrôle dont l'usage est autorisé (établie par l'autorité administrative compétente et régulièrement mise à jour)",
  "synthesis_metadata": {
    "references_substituted": 1,
    "substitutions": ["à l'article L. 253-5 du présent code"],
    "quality": "high",
    "formatting_applied": "parenthetical_clarification"
  }
}

Rules for substitution:
- Replace reference phrases with their resolved content
- Adjust articles and prepositions for French grammatical agreement
- Use parentheses for long definitions to maintain readability
- Preserve the legal meaning and structure
- Handle nested substitutions if resolved content contains other references
- Maintain proper capitalization and punctuation
"""

    def synthesize(
        self,
        resolution_result: ResolutionResult,
        reconstructor_output: ReconstructorOutput,
        source_chunk: BillChunk,
        target_article: TargetArticle
    ) -> LegalAnalysisOutput:
        """
        Perform final synthesis by substituting resolved references into text fragments.

        Args:
            resolution_result: Complete output from ResolutionOrchestrator
            reconstructor_output: Before/after text fragments from TextReconstructor  
            source_chunk: Original BillChunk being processed
            target_article: Target article identification

        Returns:
            LegalAnalysisOutput with fully resolved BeforeState and AfterState

        Raises:
            ValueError: If required inputs are missing or invalid
        """
        # Input validation
        if not reconstructor_output.deleted_or_replaced_text and not reconstructor_output.intermediate_after_state_text:
            raise ValueError("ReconstructorOutput must have at least one non-empty text fragment")

        # Synthesize BeforeState from DELETIONAL references
        before_state = self._synthesize_state(
            base_text=reconstructor_output.deleted_or_replaced_text,
            resolved_refs=resolution_result.resolved_deletional_references,
            state_type="BeforeState"
        )

        # Synthesize AfterState from DEFINITIONAL references  
        after_state = self._synthesize_state(
            base_text=reconstructor_output.intermediate_after_state_text,
            resolved_refs=resolution_result.resolved_definitional_references,
            state_type="AfterState"
        )

        return LegalAnalysisOutput(
            before_state=before_state,
            after_state=after_state,
            source_chunk=source_chunk,
            target_article=target_article
        )

    def _synthesize_state(
        self, 
        base_text: str, 
        resolved_refs: List[ResolvedReference], 
        state_type: str
    ) -> LegalState:
        """
        Synthesize a single legal state by substituting resolved references.

        Args:
            base_text: Base text to substitute references in
            resolved_refs: List of resolved references to substitute
            state_type: Type of state being synthesized ("BeforeState" or "AfterState")

        Returns:
            LegalState with synthesized text and metadata

        Raises:
            ValueError: If synthesis fails
        """
        # Handle empty base text
        if not base_text.strip():
            logger.info(f"Empty base text for {state_type}, returning empty state")
            return LegalState(
                state_text="",
                synthesis_metadata={
                    "state_type": state_type,
                    "references_found": len(resolved_refs),
                    "references_substituted": 0,
                    "empty_input": True
                }
            )

        # Handle no references to substitute
        if not resolved_refs:
            logger.info(f"No references to substitute for {state_type}")
            return LegalState(
                state_text=base_text,
                synthesis_metadata={
                    "state_type": state_type,
                    "references_found": 0,
                    "references_substituted": 0,
                    "no_substitutions_needed": True
                }
            )

        # Create substitution map
        substitution_map = {
            ref.linked_reference.reference_text: ref.resolved_content 
            for ref in resolved_refs
        }

        user_prompt = self._build_substitution_prompt(
            text=base_text,
            substitutions=substitution_map,
            instruction=f"For the {state_type}, create readable legal text by substituting references with their resolved content."
        )

        try:
            # Use shared rate limiter across all components
            rate_limiter.wait_if_needed("LegalStateSynthesizer")
            
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
            self._validate_synthesis_response(content)

            # Add metadata about the synthesis process
            synthesis_metadata = content.get("synthesis_metadata", {})
            synthesis_metadata.update({
                "state_type": state_type,
                "references_found": len(resolved_refs),
                "input_length": len(base_text),
                "output_length": len(content["synthesized_text"])
            })

            return LegalState(
                state_text=content["synthesized_text"],
                synthesis_metadata=synthesis_metadata
            )

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response for {state_type}: {e}")
            return self._create_fallback_state(base_text, resolved_refs, state_type)
        except Exception as e:
            logger.error(f"Synthesis failed for {state_type}: {e}")
            return self._create_fallback_state(base_text, resolved_refs, state_type)

    def _build_substitution_prompt(
        self, 
        text: str, 
        substitutions: Dict[str, str], 
        instruction: str
    ) -> str:
        """
        Build a user prompt for reference substitution.

        Args:
            text: Base text to substitute references in
            substitutions: Map of reference text to resolved content
            instruction: Specific instruction for this substitution

        Returns:
            Formatted user prompt string
        """
        return json.dumps({
            "instruction": instruction,
            "text": text,
            "substitutions": substitutions
        })

    def _validate_synthesis_response(self, content: dict) -> None:
        """
        Validate that the synthesis response contains required fields.

        Args:
            content: Parsed JSON response from Mistral

        Raises:
            ValueError: If required fields are missing
        """
        required_fields = ["synthesized_text", "synthesis_metadata"]
        for field in required_fields:
            if field not in content:
                raise ValueError(f"Missing required field: {field}")

        if not isinstance(content["synthesized_text"], str):
            raise ValueError("synthesized_text must be a string")

        if not isinstance(content["synthesis_metadata"], dict):
            raise ValueError("synthesis_metadata must be a dictionary")

    def _create_fallback_state(
        self, 
        base_text: str, 
        resolved_refs: List[ResolvedReference], 
        state_type: str
    ) -> LegalState:
        """
        Create a fallback state when synthesis fails.

        Args:
            base_text: Original base text
            resolved_refs: List of resolved references that failed to substitute
            state_type: Type of state being synthesized

        Returns:
            LegalState with original text and error metadata
        """
        logger.warning(f"Using fallback synthesis for {state_type}")
        
        # Simple fallback: try basic string replacement
        fallback_text = base_text
        substitutions_made = 0
        
        for ref in resolved_refs:
            if ref.linked_reference.reference_text in fallback_text:
                # Simple replacement - may not be grammatically perfect
                fallback_text = fallback_text.replace(
                    ref.linked_reference.reference_text,
                    f"[{ref.resolved_content}]"  # Brackets to indicate fallback substitution
                )
                substitutions_made += 1

        return LegalState(
            state_text=fallback_text,
            synthesis_metadata={
                "state_type": state_type,
                "references_found": len(resolved_refs),
                "references_substituted": substitutions_made,
                "synthesis_quality": "fallback",
                "error": "LLM synthesis failed, used simple replacement"
            }
        ) 