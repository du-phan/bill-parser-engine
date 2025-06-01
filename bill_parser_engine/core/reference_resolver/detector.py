"""
Reference detection component for identifying normative references in legislative text.

This component identifies all normative references within a legislative text chunk,
distinguishing between the primary target article (identified by TargetArticleIdentifier)
and embedded references within the text.
"""

import json
from mistralai import Mistral
from typing import List, Optional, Dict

from bill_parser_engine.core.reference_resolver.models import Reference, BillChunk, TargetArticle, ReferenceType, ReferenceSource
from bill_parser_engine.core.reference_resolver.prompts import REFERENCE_DETECTION_AGENT_PROMPT
from bill_parser_engine.core.reference_resolver.config import MISTRAL_MODEL
from bill_parser_engine.core.reference_resolver.utils import extract_mistral_agent_output_content


class ReferenceDetector:
    """
    Detects normative references in legislative text using a Mistral LLM agent.
    
    This component is designed to work with the output of the TargetArticleIdentifier,
    ensuring that the target article itself is not re-detected as an embedded reference.
    It focuses on identifying references within the text that point to other legal
    provisions, not the primary provision being modified.
    """
    def __init__(self, client: Mistral, agent_id: Optional[str] = None):
        """
        Initialize the detector. If agent_id is not provided, create the agent automatically.

        Args:
            client: An initialized Mistral client
            agent_id: (Optional) The agent ID for the Mistral reference detection agent
        """
        self.client = client
        if agent_id is None:
            self.agent_id = self._create_reference_detection_agent()
        else:
            self.agent_id = agent_id

    def _create_reference_detection_agent(self) -> str:
        """
        Create a Mistral agent for reference detection with the correct configuration.

        Returns:
            The agent ID of the created reference detection agent
        """
        agent = self.client.beta.agents.create(
            model=MISTRAL_MODEL,
            description="Detects embedded normative references in French legislative text, excluding the target article.",
            name="Reference Detection Agent",
            instructions=REFERENCE_DETECTION_AGENT_PROMPT,
        )
        return agent.id

    def detect_from_chunk(self, chunk: BillChunk) -> List[Reference]:
        """
        Detect normative references in a bill chunk, taking into account the target article.
        
        This is the primary method for reference detection in the pipeline. It analyzes
        a chunk of legislative text to find all embedded references, while being aware of
        the target article (if any) to avoid duplicate detections.
        
        Args:
            chunk: A BillChunk object with target_article information
            
        Returns:
            List of detected references (excluding the target article)
        """
        # Create a prompt that includes chunk metadata and target article information
        prompt = self._create_context_aware_prompt(chunk)

        try:
            # Call the Mistral agent
            response = self.client.beta.conversations.start(
                agent_id=self.agent_id,
                inputs=prompt
            )
            
            # Parse the response to extract Reference objects
            return self._parse_response(extract_mistral_agent_output_content(response), chunk)
        except Exception as e:
            raise RuntimeError(f"Mistral agent call failed: {e}")

    def _create_context_aware_prompt(self, chunk: BillChunk) -> str:
        """
        Create a context-aware prompt that includes chunk metadata and target article information.
        
        This helps the LLM understand what to look for and what to exclude.
        
        Args:
            chunk: The BillChunk to analyze
            
        Returns:
            A formatted prompt string
        """
        # Include information about the target article if available
        target_info = ""
        if chunk.target_article and chunk.target_article.operation_type:
            target_info = f"""
Target Article Information:
- Operation: {chunk.target_article.operation_type.value}
- Code: {chunk.target_article.code or 'None'}
- Article: {chunk.target_article.article or 'None'}
- Full Citation: {chunk.target_article.full_citation or 'None'}
- Raw Text: {chunk.target_article.raw_text or 'None'}

Important: Do NOT include the target article itself as an embedded reference. Focus on other references within the text.
"""

        return f"""
Analyze the following chunk of legislative text and identify all embedded normative references.

Chunk Text:
{chunk.text}

Chunk Metadata:
- Title: {chunk.titre_text}
- Article: {chunk.article_label}
- Article Introductory Phrase: {chunk.article_introductory_phrase}
- Major Subdivision: {chunk.major_subdivision_label if chunk.major_subdivision_label else 'None'}
- Numbered Point: {chunk.numbered_point_label if chunk.numbered_point_label else 'None'}

{target_info}

Focus on identifying all normative references that are embedded within the text itself.
These are references to other legal provisions that are mentioned within this chunk.
"""

    def _parse_response(self, response_text: str, chunk: BillChunk) -> List[Reference]:
        """
        Parse the agent's response to extract Reference objects.
        
        Args:
            response_text: The text response from the agent
            chunk: The original chunk for context
            
        Returns:
            List of Reference objects
        """
        # This is a placeholder implementation - in a real system, you would
        # parse the JSON response and create Reference objects
        # For now, we'll return an empty list
        try:
            # Extract JSON from the response
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            if json_start != -1 and json_end != -1:
                json_str = response_text[json_start:json_end]
                data = json.loads(json_str)
                
                references = []
                # Process high-confidence references
                for ref_data in data.get("references", []):
                    references.append(self._create_reference_from_data(ref_data))
                
                # You might also want to process low-confidence references depending on your requirements
                
                return references
            
            # If we can't parse JSON, return an empty list
            return []
        except Exception as e:
            # Log the error and return an empty list
            print(f"Error parsing references from response: {e}")
            return []
    
    def _create_reference_from_data(self, ref_data: Dict) -> Reference:
        """
        Create a Reference object from parsed JSON data.
        
        Args:
            ref_data: Dictionary with reference data from the LLM
            
        Returns:
            Reference object
        """
        # Create and return the Reference object
        return Reference(
            text=ref_data.get("text", ""),
            start_pos=ref_data.get("start_pos", 0),
            end_pos=ref_data.get("end_pos", 0),
            object=ref_data.get("object", ""),
        ) 