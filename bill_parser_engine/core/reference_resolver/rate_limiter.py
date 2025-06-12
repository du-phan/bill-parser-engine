"""
Shared rate limiter for Mistral API calls across all pipeline components.

This centralizes rate limiting to prevent different components from stepping on each other
and causing API rate limit errors.
"""

import time
import threading
from typing import Optional


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
    
    def __init__(self, min_delay_seconds: float = 2.0):
        """
        Initialize the rate limiter (only once due to singleton).
        
        Args:
            min_delay_seconds: Minimum seconds between API calls
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


# Global instance for easy access
rate_limiter = SharedRateLimiter() 