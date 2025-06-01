def extract_mistral_agent_output_content(response):
    """
    Extract the main content string from a Mistral agent ConversationResponse.
    Raises a RuntimeError if no outputs are present.
    Args:
        response: The ConversationResponse object returned by the Mistral agent
    Returns:
        The content string from the first output message
    """
    if not hasattr(response, 'outputs') or not response.outputs:
        raise RuntimeError("Mistral agent returned no outputs.")
    return response.outputs[0].content 