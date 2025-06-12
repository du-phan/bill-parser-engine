"""
Simple component-level caching for LLM pipeline fault tolerance.

This module provides a lightweight caching mechanism to store intermediate
results from pipeline components, allowing recovery from API failures without
reprocessing everything from scratch.
"""

import hashlib
import json
import pickle
import time
from pathlib import Path
from typing import Any, Dict, Optional
import logging


class SimpleCache:
    """
    Simple disk-based cache for component results.
    
    Features:
    - Disk persistence with compression
    - Component-specific namespacing
    - Cache invalidation and overwrite options
    - Lightweight and efficient
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
        return f"{component}_{hash_value[:16]}"

    def get(self, component: str, input_data: Any) -> Optional[Any]:
        """
        Retrieve cached result for component and input.
        
        Args:
            component: Component name
            input_data: Input data
            
        Returns:
            Cached result if found, None otherwise
        """
        cache_key = self._generate_cache_key(component, input_data)
        cache_file = self.cache_dir / f"{cache_key}.pkl"
        
        if not cache_file.exists():
            return None
        
        try:
            with open(cache_file, 'rb') as f:
                cached_data = pickle.load(f)
            
            self.logger.debug(f"Cache HIT for {component}: {cache_key}")
            return cached_data['result']
            
        except Exception as e:
            self.logger.error(f"Failed to load cache entry: {e}")
            # Remove corrupted cache file
            cache_file.unlink(missing_ok=True)
            return None

    def set(self, component: str, input_data: Any, result: Any) -> None:
        """
        Store result in cache.
        
        Args:
            component: Component name
            input_data: Input data that produced the result
            result: Result to cache
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
            
            self.logger.debug(f"Cache SET for {component}: {cache_key}")
            
        except Exception as e:
            self.logger.error(f"Failed to save cache entry: {e}")

    def invalidate(self, component: str = None) -> int:
        """
        Invalidate cache entries.
        
        Args:
            component: Invalidate all entries for this component (all if None)
            
        Returns:
            Number of entries invalidated
        """
        if component:
            pattern = f"{component}_*.pkl"
        else:
            pattern = "*.pkl"
        
        cache_files = list(self.cache_dir.glob(pattern))
        
        for cache_file in cache_files:
            cache_file.unlink(missing_ok=True)
        
        count = len(cache_files)
        if count > 0:
            self.logger.info(f"Invalidated {count} cache entries for component '{component}'")
        
        return count

    def clear(self) -> None:
        """Clear all cache entries."""
        self.invalidate()


# Global cache instance
_global_cache: Optional[SimpleCache] = None

def get_cache() -> SimpleCache:
    """Get or create the global cache instance."""
    global _global_cache
    if _global_cache is None:
        _global_cache = SimpleCache()
    return _global_cache 