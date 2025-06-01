"""
Reference classification component.

This component takes references detected by the ReferenceDetector and extracts
structured components (code, article, section, paragraph) needed for retrieval
via pylegifrance or other sources.
"""

import json
from mistralai import Mistral
from typing import Dict, List, Optional, Tuple
from copy import deepcopy
import logging

from .models import Reference, ReferenceType, ReferenceSource, BillChunk
from .utils import extract_mistral_agent_output_content
from .prompts import REFERENCE_CLASSIFICATION_AGENT_PROMPT
from .config import MISTRAL_MODEL


class ReferenceClassifier:
    """
    Classifies normative references and extracts structured components for retrieval.
    
    This component is responsible for:
    1. Parsing reference text into structured components (code, article, section, etc.)
    2. Validating and enhancing reference_type and source classification
    3. Ensuring references can be properly queried using pylegifrance or other sources
    
    Note: This class never mutates input Reference objects. It always returns new objects.
    """

    def __init__(self, client: Mistral, agent_id: Optional[str] = None):
        """
        Initialize the classifier.
        
        Args:
            client: An initialized Mistral client
            agent_id: (Optional) The agent ID for the Mistral reference classification agent
        """
        self.client = client
        if agent_id is None:
            self.agent_id = self._create_classification_agent()
        else:
            self.agent_id = agent_id
        
    def _create_classification_agent(self) -> str:
        """
        Create a Mistral agent for reference classification with the correct configuration.

        Returns:
            The agent ID of the created classification agent
        """
        agent = self.client.beta.agents.create(
            model=MISTRAL_MODEL,
            description="Classifies normative references in French legislative text and extracts structured components.",
            name="Reference Classification Agent",
            instructions=REFERENCE_CLASSIFICATION_AGENT_PROMPT,
        )
        return agent.id

    def classify(self, reference: Reference, chunk: BillChunk) -> Reference:
        """
        Classify a reference and extract its structured components.
        Never mutates the input Reference; always returns a new Reference object.
        
        Args:
            reference: The reference to classify
            chunk: The BillChunk containing the reference (for context)
            
        Returns:
            A new Reference object, enriched with detailed classification and components
        """
        # Extract surrounding text for context
        surrounding_text = self._extract_surrounding_text(chunk.text, reference.start_pos, reference.end_pos)
        
        # Use LLM for classification
        components, ref_type, source = self._llm_based_classification(reference.text, surrounding_text, chunk)
        
        # Create a new Reference object (deepcopy to preserve all fields)
        new_reference = deepcopy(reference)
        new_reference.components = components
        if ref_type:
            new_reference.reference_type = ref_type
        if source:
            new_reference.source = source
        
        return new_reference
    
    def classify_batch(self, references: List[Reference], chunk: BillChunk) -> List[Reference]:
        """
        Classify a batch of references from the same chunk.
        Never mutates the input Reference objects; always returns new Reference objects.
        
        Args:
            references: List of references to classify
            chunk: The BillChunk containing the references
            
        Returns:
            List of new, classified Reference objects
        """
        return [self.classify(ref, chunk) for ref in references]
    
    def _extract_surrounding_text(self, text: str, start_pos: int, end_pos: int, 
                                 window_size: int = 200) -> str:
        """
        Extract text surrounding a reference for context.
        
        Args:
            text: The full text
            start_pos: Start position of the reference
            end_pos: End position of the reference
            window_size: Number of characters to include before and after
            
        Returns:
            Text window surrounding the reference
        """
        start = max(0, start_pos - window_size)
        end = min(len(text), end_pos + window_size)
        
        return text[start:end]
    
    def _llm_based_classification(self, reference_text: str, surrounding_text: str, 
                                 chunk: BillChunk) -> Tuple[Dict[str, str], 
                                                          Optional[ReferenceType], 
                                                          Optional[ReferenceSource]]:
        """
        Use LLM to classify reference patterns with chunk context.
        
        Args:
            reference_text: The reference text to classify
            surrounding_text: The surrounding context
            chunk: The BillChunk containing the reference
            
        Returns:
            Tuple of (components dict, reference_type, source)
        """
        # Create a context-aware prompt with chunk metadata
        target_info = ""
        if chunk.target_article and chunk.target_article.operation_type:
            target_info = f"""
Target Article Information:
- Operation: {chunk.target_article.operation_type.value}
- Code: {chunk.target_article.code or 'None'}
- Article: {chunk.target_article.article or 'None'}
- Full Citation: {chunk.target_article.full_citation or 'None'}
- Raw Text: {chunk.target_article.raw_text or 'None'}
"""

        prompt = f"""
Analyze the following legal reference and extract structured components for retrieval:

Reference: {reference_text}

Surrounding Text: {surrounding_text}

Chunk Metadata:
- Title: {chunk.titre_text}
- Article: {chunk.article_label}
- Article Introductory Phrase: {chunk.article_introductory_phrase}
- Major Subdivision: {chunk.major_subdivision_label if chunk.major_subdivision_label else 'None'}
- Numbered Point: {chunk.numbered_point_label if chunk.numbered_point_label else 'None'}

{target_info}

Provide your analysis as a JSON object with component fields and classification.
"""
        
        try:
            # Call the Mistral agent
            response = self.client.beta.conversations.start(
                agent_id=self.agent_id,
                inputs=prompt
            )
            
            # Extract and parse the response
            content = extract_mistral_agent_output_content(response)
            
            # Extract JSON from the response
            json_start = content.find('{')
            json_end = content.rfind('}') + 1
            if json_start != -1 and json_end != -1:
                json_str = content[json_start:json_end]
                data = json.loads(json_str)
                
                components = {}
                # Extract all component fields except type/source
                for key, value in data.items():
                    if key not in ["reference_type", "source"] and value:
                        components[key] = value
                
                # Robust mapping for reference_type
                ref_type_str = data.get("reference_type")
                ref_type = None
                if ref_type_str:
                    ref_type_str_norm = ref_type_str.strip().lower()
                    for enum_val in ReferenceType:
                        if ref_type_str_norm == enum_val.value:
                            ref_type = enum_val
                            break
                    if ref_type is None:
                        # If not mappable, use OTHER and log a warning
                        logging.warning(f"Unknown reference_type string from LLM: '{ref_type_str}'. Mapping to ReferenceType.OTHER.")
                        ref_type = ReferenceType.OTHER
                
                # Map the source string to ReferenceSource enum
                source_str = data.get("source")
                source = None
                if source_str:
                    source_str_norm = source_str.strip().upper()
                    for enum_val in ReferenceSource:
                        if source_str_norm == enum_val.name:
                            source = enum_val
                            break
                
                return components, ref_type, source
            
            # Fallback to empty results if parsing fails
            return {}, None, None
            
        except Exception as e:
            logging.error(f"Error in LLM classification: {e}")
            return {}, None, None 