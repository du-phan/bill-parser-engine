"""
Reference detection component.
"""

from mistralai import Mistral
from typing import List, Optional
from bill_parser_engine.core.reference_resolver.models import Reference
from bill_parser_engine.core.reference_resolver.prompts import REFERENCE_DETECTION_AGENT_PROMPT
from bill_parser_engine.core.reference_resolver.config import MISTRAL_MODEL


class ReferenceDetector:
    """
    Detects normative references in legislative text using a Mistral LLM agent.
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
            description="Detects all normative references in French legislative text, both explicit and implicit, with context-aware understanding.",
            name="Reference Detection Agent",
            instructions=REFERENCE_DETECTION_AGENT_PROMPT,
        )
        return agent.id

    def detect(self, text: str) -> List[Reference]:
        """
        Detect all normative references in the given text using the LLM agent.

        Args:
            text: The legislative text to process

        Returns:
            List of detected references
        """
        prompt = f"Analyze the following text and extract the normative references : {text}"

        try:
            response = self.client.beta.conversations.start(
                agent_id=self.agent_id,
                inputs=prompt
            )
            return response
            # The actual output format will depend on the agent's configuration and response
            # Placeholder: print or inspect response to design the parser
            # print(response)
        except Exception as e:
            raise RuntimeError(f"Mistral agent call failed: {e}")

        # TODO: Parse response to extract Reference objects
        #raise NotImplementedError("Parsing of LLM output to Reference objects is not yet implemented.") 