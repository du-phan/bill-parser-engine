"""
Target article identification component.

This component analyzes chunks of legislative text and identifies the primary legal article,
section, or code provision that is the target of modification, insertion, or abrogation.
"""

import json
from mistralai import Mistral
from typing import List, Optional
from dataclasses import replace

from bill_parser_engine.core.reference_resolver.models import BillChunk, TargetArticle, TargetOperationType
from bill_parser_engine.core.reference_resolver.prompts import TARGET_ARTICLE_IDENTIFICATION_AGENT_PROMPT
from bill_parser_engine.core.reference_resolver.config import MISTRAL_MODEL
from bill_parser_engine.core.reference_resolver.utils import extract_mistral_agent_output_content


class TargetArticleIdentifier:
    """
    Identifies the primary legal article or section that is the target of modification,
    insertion, or abrogation in each chunk of a legislative bill.
    
    This component analyzes each chunk to determine:
    1. The main legal article/section being affected (target article)
    2. The operation type (INSERT, MODIFY, ABROGATE, RENUMBER, or OTHER)
    3. The relevant code and article identifiers
    
    It distinguishes between the target article (the "canvas" being modified) and
    embedded references within the chunk text (which are handled by ReferenceDetector).
    """
    
    def __init__(self, client: Mistral, agent_id: Optional[str] = None):
        """
        Initialize the identifier. If agent_id is not provided, create the agent automatically.

        Args:
            client: An initialized Mistral client
            agent_id: (Optional) The agent ID for the Mistral target article identification agent
        """
        self.client = client
        if agent_id is None:
            self.agent_id = self._create_target_article_identification_agent()
        else:
            self.agent_id = agent_id

    def _create_target_article_identification_agent(self) -> str:
        """
        Create a Mistral agent for target article identification with the correct configuration.

        Returns:
            The agent ID of the created target article identification agent
        """
        agent = self.client.beta.agents.create(
            model=MISTRAL_MODEL,
            description="Identifies the primary legal article, section, or code provision that is the target of modification, insertion, or abrogation in legislative text.",
            name="Target Article Identification Agent",
            instructions=TARGET_ARTICLE_IDENTIFICATION_AGENT_PROMPT,
        )
        return agent.id

    def identify(self, chunk: BillChunk) -> BillChunk:
        """
        Identify the target article for a bill chunk using the LLM agent.

        Args:
            chunk: The BillChunk to analyze

        Returns:
            A new BillChunk object with the identified target_article field set
        """
        # Create a prompt with the chunk text and relevant metadata
        prompt = self._create_prompt(chunk)

        try:
            # Call the Mistral agent
            response = self.client.beta.conversations.start(
                agent_id=self.agent_id,
                inputs=prompt
            )
            target_article = self._parse_response(extract_mistral_agent_output_content(response), chunk)
            # Return a new BillChunk with target_article set
            return replace(chunk, target_article=target_article)
        except Exception as e:
            raise RuntimeError(f"Mistral agent call failed: {e}")

    def identify_batch(self, chunks: List[BillChunk]) -> List[BillChunk]:
        """
        Process multiple chunks and enrich them with target article information.
        
        This method identifies the target article for each chunk and returns a new list
        of BillChunk objects with the target_article field set. Never mutates input chunks.

        Args:
            chunks: List of BillChunk objects to process

        Returns:
            A new list of BillChunk objects, each with target_article set
        """
        return [self.identify(chunk) for chunk in chunks]

    def _create_prompt(self, chunk: BillChunk) -> str:
        """
        Create a prompt for the Mistral agent based on the chunk and its metadata.

        Args:
            chunk: The BillChunk to analyze

        Returns:
            A formatted prompt string
        """
        return f"""
Analyze the following chunk of legislative text and identify the target article:

Chunk Text:
{chunk.text}

Chunk Metadata:
- Title: {chunk.titre_text}
- Article: {chunk.article_label}
- Article Introductory Phrase: {chunk.article_introductory_phrase}
- Major Subdivision: {chunk.major_subdivision_label if chunk.major_subdivision_label else 'None'}
- Numbered Point: {chunk.numbered_point_label if chunk.numbered_point_label else 'None'}
"""

    def _parse_response(self, response_text: str, chunk: BillChunk) -> TargetArticle:
        """
        Parse the agent's response to extract the TargetArticle information.

        Args:
            response_text: The text response from the agent
            chunk: The original chunk (for fallback information if parsing fails)

        Returns:
            TargetArticle object
        """
        try:
            # Extract JSON from the response
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            if json_start != -1 and json_end != -1:
                json_str = response_text[json_start:json_end]
                data = json.loads(json_str)
                
                # Convert operation_type string to enum
                operation_type_str = data.get("operation_type", "OTHER").upper()
                try:
                    operation_type = TargetOperationType[operation_type_str]
                except KeyError:
                    # Default to OTHER if the operation type is invalid
                    operation_type = TargetOperationType.OTHER
                
                # Create TargetArticle object
                return TargetArticle(
                    operation_type=operation_type,
                    code=data.get("code"),
                    article=data.get("article"),
                    full_citation=data.get("full_citation"),
                    confidence=float(data.get("confidence", 0.5)),
                    raw_text=data.get("raw_text"),
                    version="v0"
                )
            
            # Fallback: If we can't parse the JSON, return a default TargetArticle
            return TargetArticle(
                operation_type=TargetOperationType.OTHER,
                code=None,
                article=None,
                full_citation=None,
                confidence=0.1,  # Very low confidence
                raw_text=None,
                version="v0"
            )
        except Exception as e:
            # Log the error and return a default TargetArticle
            print(f"Error parsing target article from response: {e}")
            return TargetArticle(
                operation_type=TargetOperationType.OTHER,
                code=None,
                article=None,
                full_citation=None,
                confidence=0.1,  # Very low confidence
                raw_text=None,
                version="v0"
            ) 