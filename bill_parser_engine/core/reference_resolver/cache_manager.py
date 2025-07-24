"""
Centralized caching for Mistral API calls to avoid rate limit issues.

This module provides a simple, centralized caching mechanism specifically designed
to cache expensive Mistral API calls across all pipeline components. This prevents
redundant API calls when processing the same inputs multiple times.

Key Features:
- Single cache instance shared across all components
- Focused on caching Mistral API responses only
- Simple key generation based on input parameters
- Automatic cache invalidation and cleanup
"""

import hashlib
import json
import pickle
import time
from pathlib import Path
from typing import Any, Dict, Optional
import logging

logger = logging.getLogger(__name__)


class MistralAPICache:
    """
    Centralized cache for Mistral API calls.
    
    This cache is designed specifically to avoid redundant Mistral API calls
    when processing the same inputs multiple times. It's shared across all
    pipeline components that make Mistral API calls.
    
    Features:
    - Single cache instance for all components
    - Simple key generation based on input parameters
    - Disk persistence with compression
    - Automatic cache cleanup
    """
    
    def __init__(self, cache_dir: str = "cache"):
        """Initialize the cache with a directory."""
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger(__name__)

    def _generate_cache_key(self, component: str, input_data: Any) -> str:
        """Generate a unique cache key for component and input."""
        # Create a deterministic hash of the input
        data_str = json.dumps(input_data, sort_keys=True, default=str)
        combined = f"{component}:{data_str}"
        hash_value = hashlib.sha256(combined.encode()).hexdigest()
        return f"mistral_{component}_{hash_value[:16]}"

    def get(self, component: str, input_data: Any) -> Optional[Any]:
        """
        Retrieve cached Mistral API result for component and input.
        
        Args:
            component: Component name making the API call
            input_data: Input data that determines the API call
            
        Returns:
            Cached API result if found, None otherwise
        """
        cache_key = self._generate_cache_key(component, input_data)
        cache_file = self.cache_dir / f"{cache_key}.pkl"
        
        if not cache_file.exists():
            return None
        
        try:
            with open(cache_file, 'rb') as f:
                cached_data = pickle.load(f)
            
            self.logger.debug(f"Mistral API cache HIT for {component}: {cache_key}")
            return cached_data['result']
            
        except Exception as e:
            self.logger.error(f"Failed to load cache entry: {e}")
            # Remove corrupted cache file
            cache_file.unlink(missing_ok=True)
            return None

    def set(self, component: str, input_data: Any, result: Any) -> None:
        """
        Store Mistral API result in cache.
        
        Args:
            component: Component name that made the API call
            input_data: Input data that produced the result
            result: API result to cache
        """
        cache_key = self._generate_cache_key(component, input_data)
        cache_file = self.cache_dir / f"{cache_key}.pkl"
        
        try:
            cached_data = {
                'component': component,
                'timestamp': time.time(),
                'result': result
            }
            
            with open(cache_file, 'wb') as f:
                pickle.dump(cached_data, f)
            
            self.logger.debug(f"Mistral API cache SET for {component}: {cache_key}")
            
        except Exception as e:
            self.logger.error(f"Failed to save cache entry: {e}")

    def clear(self) -> int:
        """
        Clear all cached Mistral API results.
        
        Returns:
            Number of entries cleared
        """
        cache_files = list(self.cache_dir.glob("mistral_*.pkl"))
        
        for cache_file in cache_files:
            cache_file.unlink(missing_ok=True)
        
        count = len(cache_files)
        if count > 0:
            self.logger.info(f"Cleared {count} Mistral API cache entries")
        
        return count

    def clear_by_component(self, component: str) -> int:
        """
        Clear cached Mistral API results for a specific component.
        
        Args:
            component: Component name whose cache entries should be cleared
            
        Returns:
            Number of entries cleared for the component
        """
        cache_files = list(self.cache_dir.glob(f"mistral_{component}_*.pkl"))
        
        for cache_file in cache_files:
            cache_file.unlink(missing_ok=True)
        
        count = len(cache_files)
        if count > 0:
            self.logger.info(f"Cleared {count} Mistral API cache entries for component '{component}'")
        
        return count

    def get_stats(self, component: Optional[str] = None) -> dict:
        """
        Get cache statistics.
        
        Args:
            component: Optional component name to get stats for. If None, returns stats for all components.
        
        Returns:
            Dictionary with cache statistics
        """
        if component is None:
            # Get stats for all components
            cache_files = list(self.cache_dir.glob("mistral_*.pkl"))
            
            # Group by component
            component_stats = {}
            for cache_file in cache_files:
                # Extract component name from filename
                filename = cache_file.stem
                if filename.startswith("mistral_"):
                    parts = filename.split("_", 2)
                    if len(parts) >= 3:
                        comp = parts[1]
                        component_stats[comp] = component_stats.get(comp, 0) + 1
            
            return {
                "total_entries": len(cache_files),
                "total_size_bytes": sum(f.stat().st_size for f in cache_files if f.exists()),
                "component_breakdown": component_stats
            }
        else:
            # Get stats for specific component
            cache_files = list(self.cache_dir.glob(f"mistral_{component}_*.pkl"))
            
            return {
                "component": component,
                "entries": len(cache_files),
                "size_bytes": sum(f.stat().st_size for f in cache_files if f.exists())
            }


# Global cache instance
_global_mistral_cache: Optional[MistralAPICache] = None

def get_mistral_cache() -> MistralAPICache:
    """Get or create the global Mistral API cache instance."""
    global _global_mistral_cache
    if _global_mistral_cache is None:
        _global_mistral_cache = MistralAPICache()
    return _global_mistral_cache


# Backward compatibility aliases
SimpleCache = MistralAPICache
get_cache = get_mistral_cache 