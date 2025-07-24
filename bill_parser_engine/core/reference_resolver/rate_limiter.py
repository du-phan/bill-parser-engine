"""
Shared rate limiter for Mistral API calls across all pipeline components.

This centralizes rate limiting to prevent different components from stepping on each other
and causing API rate limit errors.
"""

import time
import threading
import random
import json
from typing import Optional, Callable, Any, Dict, List
import logging

try:
    from mistralai import Mistral
except ImportError:
    Mistral = None

# Mistral model configuration
MISTRAL_MODEL = "magistral-medium-2506"

logger = logging.getLogger(__name__)


class SharedRateLimiter:
    """
    Thread-safe shared rate limiter for Mistral API calls.
    
    This ensures that all components respect the same rate limiting window,
    preventing the accumulation of API calls that leads to HTTP 429 errors.
    """
    
    _instance: Optional["SharedRateLimiter"] = None
    _lock = threading.Lock()
    
    def __new__(cls):
        """Singleton pattern to ensure one rate limiter across all components."""
        if cls._instance is None:
            with cls._lock:
                # Double-checked locking: check again after acquiring lock
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self, min_delay_seconds: float = 3.5):
        """
        Initialize the rate limiter (only once due to singleton).
        
        Args:
            min_delay_seconds: Minimum seconds between API calls (increased from 2.0 to 3.5)
        """
        # Use a simple flag to ensure init runs only once
        if not hasattr(self, 'call_lock'):
            self.min_delay_seconds = min_delay_seconds
            self.last_api_call = 0.0
            self.call_lock = threading.Lock()
    
    def wait_if_needed(self, component_name: str = "Unknown") -> None:
        """
        Wait if necessary to respect rate limiting.
        
        Args:
            component_name: Name of the component making the call (for logging)
        """
        with self.call_lock:
            current_time = time.time()
            time_since_last_call = current_time - self.last_api_call
            
            if time_since_last_call < self.min_delay_seconds:
                sleep_time = self.min_delay_seconds - time_since_last_call
                print(f"⏱️ {component_name}: Waiting {sleep_time:.1f}s for rate limiting...")
                time.sleep(sleep_time)
            
            # Update the timestamp after the wait (not before)
            self.last_api_call = time.time()
    
    def update_delay(self, new_delay_seconds: float) -> None:
        """
        Update the minimum delay between API calls.
        
        Args:
            new_delay_seconds: New minimum delay in seconds
        """
        with self.call_lock:
            self.min_delay_seconds = new_delay_seconds
            print(f"📊 Rate limiter updated to {new_delay_seconds}s delay")

    def execute_with_retry(self, api_call: Callable[[], Any], component_name: str = "Unknown", max_retries: int = 3) -> Any:
        """
        Execute an API call with exponential backoff retry logic for 429 errors.
        
        Args:
            api_call: The API call function to execute
            component_name: Name of the component making the call
            max_retries: Maximum number of retry attempts
            
        Returns:
            The result of the API call
            
        Raises:
            The last exception if all retries fail
        """
        last_exception = None
        
        for attempt in range(max_retries + 1):  # +1 for initial attempt
            try:
                # Apply rate limiting before each attempt
                self.wait_if_needed(component_name)
                
                # Execute the API call
                return api_call()
                
            except Exception as e:
                last_exception = e
                
                # Check if this is a 429 rate limit error
                error_message = str(e).lower()
                is_rate_limit_error = (
                    "429" in error_message or 
                    "too many requests" in error_message or
                    "rate limit" in error_message or
                    "service tier capacity exceeded" in error_message
                )
                
                if is_rate_limit_error and attempt < max_retries:
                    # Calculate exponential backoff delay with jitter
                    base_delay = 5.0  # Start with 5 seconds
                    exponential_delay = base_delay * (2 ** attempt)
                    jitter = random.uniform(0.5, 1.5)  # Add 50% jitter
                    retry_delay = exponential_delay * jitter
                    
                    logger.warning(f"{component_name}: Rate limit hit (attempt {attempt + 1}/{max_retries + 1}). "
                                 f"Retrying in {retry_delay:.1f}s...")
                    print(f"🚫 {component_name}: Rate limit exceeded. Retrying in {retry_delay:.1f}s...")
                    
                    time.sleep(retry_delay)
                    
                    # Also increase the global rate limit for subsequent calls
                    new_delay = min(self.min_delay_seconds * 1.5, 10.0)  # Cap at 10 seconds
                    if new_delay > self.min_delay_seconds:
                        self.update_delay(new_delay)
                        
                else:
                    # Either not a rate limit error, or we've exhausted retries
                    break
        
        # If we get here, all retries failed
        if last_exception:
            raise last_exception


# Global instance for easy access
rate_limiter = SharedRateLimiter()

# Alias for backward compatibility
RateLimiter = SharedRateLimiter

def get_rate_limiter() -> SharedRateLimiter:
    """
    Get the global shared rate limiter instance.
    
    Returns:
        The global SharedRateLimiter instance
    """
    return rate_limiter


def call_mistral_json_model(
    client: Mistral,
    rate_limiter: SharedRateLimiter,
    system_prompt: str,
    user_payload: Dict[str, Any],
    component_name: str,
    temperature: float = 0.0,
) -> Optional[Dict[str, Any]]:
    """
    Calls the Mistral API with a JSON response format, handling boilerplate.

    Args:
        client: The Mistral instance.
        rate_limiter: The shared rate limiter.
        system_prompt: The system prompt for the LLM.
        user_payload: A dictionary to be JSON-stringified for the user message.
        component_name: The name of the calling component (for logging).
        temperature: The temperature for the API call.

    Returns:
        The parsed JSON dictionary from the response, or None on failure.
    """
    if not client:
        logger.error("Mistral client not initialized. Cannot make API call.")
        return None

    try:
        user_message = json.dumps(user_payload)

        def llm_call():
            return client.chat.complete(
                model=MISTRAL_MODEL,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                response_format={"type": "json_object"},
            )

        response = rate_limiter.execute_with_retry(llm_call, component_name)

        if response and response.choices:
            content = response.choices[0].message.content
            return json.loads(content)

    except Exception as e:
        logger.error(
            f"Error during LLM call for component '{component_name}': {e}",
            exc_info=True,
        )

    return None


def call_mistral_with_messages(
    client: Mistral,
    rate_limiter: SharedRateLimiter,
    messages: List[Dict[str, str]],
    component_name: str,
    temperature: float = 0.0,
    response_format: Optional[Dict[str, str]] = None,
    tools: Optional[List[Dict]] = None,
    tool_choice: Optional[str] = None,
) -> Optional[Any]:
    """
    Calls the Mistral API with a list of messages, handling boilerplate and rate limiting.

    Args:
        client: The Mistral instance.
        rate_limiter: The shared rate limiter.
        messages: List of message dictionaries with 'role' and 'content' keys.
        component_name: The name of the calling component (for logging).
        temperature: The temperature for the API call.
        response_format: Optional response format specification.
        tools: Optional tools schema for function calling.
        tool_choice: Optional tool choice specification.

    Returns:
        The raw Mistral response, or None on failure.
    """
    if not client:
        logger.error("Mistral client not initialized. Cannot make API call.")
        return None

    try:
        def llm_call():
            kwargs = {
                "model": MISTRAL_MODEL,
                "temperature": temperature,
                "messages": messages,
            }
            
            if response_format:
                kwargs["response_format"] = response_format
            if tools:
                kwargs["tools"] = tools
            if tool_choice:
                kwargs["tool_choice"] = tool_choice
                
            return client.chat.complete(**kwargs)

        return rate_limiter.execute_with_retry(llm_call, component_name)

    except Exception as e:
        logger.error(
            f"Error during LLM call for component '{component_name}': {e}",
            exc_info=True,
        )

    return None 