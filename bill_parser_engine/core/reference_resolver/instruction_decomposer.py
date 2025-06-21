"""
InstructionDecomposer component for parsing French legal amendment instructions.

This component uses LLM-based parsing to decompose compound amendment instructions
into atomic operations with precise type identification and sequencing.
"""

import json
import logging
import os
import time
from typing import List, Optional

from mistralai import Mistral

from bill_parser_engine.core.reference_resolver.models import AmendmentOperation, OperationType
from bill_parser_engine.core.reference_resolver.cache_manager import SimpleCache
from bill_parser_engine.core.reference_resolver.prompts import INSTRUCTION_DECOMPOSER_SYSTEM_PROMPT
from bill_parser_engine.core.reference_resolver.rate_limiter import rate_limiter

logger = logging.getLogger(__name__)


class InstructionDecomposer:
    """
    Decomposes compound French legal amendment instructions into atomic operations.
    
    Uses sophisticated LLM prompting to:
    - Identify operation types (REPLACE, DELETE, INSERT, ADD, REWRITE, ABROGATE)
    - Extract target text and replacement text
    - Parse position hints and legal positioning
    - Sequence compound operations correctly
    - Handle complex French legal language patterns
    """

    def __init__(self, api_key: Optional[str] = None, cache: Optional[SimpleCache] = None, use_cache: bool = True):
        """
        Initialize the instruction decomposer.

        Args:
            api_key: Mistral API key (defaults to MISTRAL_API_KEY environment variable)
            cache: Cache instance (uses global if None)
            use_cache: Whether to use caching
        """
        self.api_key = api_key or os.getenv("MISTRAL_API_KEY")
        if not self.api_key:
            raise ValueError("MISTRAL_API_KEY environment variable is required")
        
        self.client = Mistral(api_key=self.api_key)
        self.cache = cache or SimpleCache()
        self.use_cache = use_cache
        
        logger.info("InstructionDecomposer initialized with caching: %s", "enabled" if use_cache else "disabled")

    def parse_instruction(self, amendment_instruction: str) -> List[AmendmentOperation]:
        """
        Parse a compound amendment instruction into atomic operations.

        Args:
            amendment_instruction: Raw French legal amendment text

        Returns:
            List of AmendmentOperation objects in execution order

        Raises:
            ValueError: If instruction cannot be parsed
            RuntimeError: If API call fails
        """
        logger.info("Parsing amendment instruction: %.200s...", amendment_instruction)
        
        # Check cache first
        cache_key = f"instruction_decomposer_{hash(amendment_instruction)}"
        if self.use_cache:
            cached_result = self.cache.get("instruction_decomposer", cache_key)
            if cached_result is not None:
                logger.debug("Found cached decomposition result")
                return self._deserialize_operations(cached_result)

        start_time = time.time()
        
        try:
            # Call LLM with sophisticated prompt
            system_prompt = self._build_system_prompt()
            user_message = self._build_user_message(amendment_instruction)
            
            def make_api_call():
                return self.client.chat.complete(
                    model="mistral-large-latest",
                    temperature=0.0,  # Deterministic parsing
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message}
                    ],
                    response_format={"type": "json_object"}
                )
            
            response = rate_limiter.execute_with_retry(make_api_call, "InstructionDecomposer")
            
            # Parse response
            response_content = response.choices[0].message.content
            logger.debug("Raw LLM response: %s", response_content)
            
            result_data = json.loads(response_content)
            operations = self._parse_response(result_data, amendment_instruction)
            
            processing_time = int((time.time() - start_time) * 1000)
            logger.info("Decomposed into %d operations (processing time: %dms)", len(operations), processing_time)
            
            # Cache result
            if self.use_cache:
                serialized_operations = self._serialize_operations(operations)
                self.cache.set("instruction_decomposer", cache_key, serialized_operations)
            
            return operations

        except json.JSONDecodeError as e:
            logger.error("Failed to parse LLM response as JSON: %s", e)
            raise ValueError(f"Invalid LLM response format: {e}")
        except Exception as e:
            logger.error("Failed to decompose instruction: %s", e)
            raise RuntimeError(f"Instruction decomposition failed: {e}")

    def _build_system_prompt(self) -> str:
        """Build the sophisticated system prompt for instruction decomposition."""
        return INSTRUCTION_DECOMPOSER_SYSTEM_PROMPT

    def _build_user_message(self, amendment_instruction: str) -> str:
        """Build the user message with the specific instruction to parse."""
        return f"""Analysez cette instruction d'amendement législatif français et décomposez-la en opérations atomiques :

INSTRUCTION À ANALYSER:
"{amendment_instruction}"

Fournissez votre analyse au format JSON spécifié, en identifiant toutes les opérations distinctes avec leurs types, textes cibles, textes de remplacement, indices de position, et ordres de séquence."""

    def _parse_response(self, result_data: dict, original_instruction: str) -> List[AmendmentOperation]:
        """Parse the LLM response into AmendmentOperation objects."""
        if "operations" not in result_data:
            raise ValueError("Response missing required 'operations' field")
        
        operations_data = result_data["operations"]
        if not isinstance(operations_data, list):
            raise ValueError("'operations' field must be a list")
        
        # Handle empty operations list
        if len(operations_data) == 0:
            logger.warning("LLM returned 0 operations for instruction: %.200s...", original_instruction)
            # Try to infer a simple operation from the instruction
            return self._infer_fallback_operation(original_instruction)
        
        operations = []
        for i, op_data in enumerate(operations_data):
            try:
                # Validate required fields
                if "operation_type" not in op_data:
                    raise ValueError(f"Operation {i} missing 'operation_type'")
                if "position_hint" not in op_data:
                    raise ValueError(f"Operation {i} missing 'position_hint'")
                if "sequence_order" not in op_data:
                    raise ValueError(f"Operation {i} missing 'sequence_order'")
                if "confidence_score" not in op_data:
                    raise ValueError(f"Operation {i} missing 'confidence_score'")
                
                # Parse operation type
                operation_type_str = op_data["operation_type"]
                try:
                    operation_type = OperationType(operation_type_str)
                except ValueError:
                    raise ValueError(f"Invalid operation_type: {operation_type_str}")
                
                # Extract and validate fields based on operation type
                target_text = op_data.get("target_text")
                replacement_text = op_data.get("replacement_text")
                
                # Validate operation-specific requirements
                self._validate_operation_fields(operation_type, target_text, replacement_text, i)
                
                # Create operation
                operation = AmendmentOperation(
                    operation_type=operation_type,
                    target_text=target_text,
                    replacement_text=replacement_text,
                    position_hint=op_data["position_hint"],
                    sequence_order=op_data["sequence_order"],
                    confidence_score=op_data["confidence_score"]
                )
                
                operations.append(operation)
                
            except Exception as e:
                logger.error("Failed to parse operation %d: %s", i, e)
                raise ValueError(f"Invalid operation data at index {i}: {e}")
        
        # Sort by sequence order
        operations.sort(key=lambda op: op.sequence_order)
        
        # Validate sequence numbering
        expected_order = 1
        for op in operations:
            if op.sequence_order != expected_order:
                logger.warning("Non-sequential operation order: expected %d, got %d", expected_order, op.sequence_order)
            expected_order += 1
        
        return operations

    def _validate_operation_fields(self, operation_type: OperationType, target_text: Optional[str], 
                                   replacement_text: Optional[str], index: int) -> None:
        """Validate that operation has required fields based on its type."""
        if operation_type == OperationType.REPLACE:
            if not target_text:
                raise ValueError(f"REPLACE operation requires target_text")
            if not replacement_text:
                raise ValueError(f"REPLACE operation requires replacement_text")
        elif operation_type == OperationType.INSERT:
            if not replacement_text:
                raise ValueError(f"INSERT operation requires replacement_text")
        elif operation_type == OperationType.ADD:
            if not replacement_text:
                raise ValueError(f"ADD operation requires replacement_text")
        elif operation_type == OperationType.REWRITE:
            if not replacement_text:
                raise ValueError(f"REWRITE operation requires replacement_text")
        # DELETE and ABROGATE can have null fields

    def _infer_fallback_operation(self, instruction: str) -> List[AmendmentOperation]:
        """
        Attempt to infer a simple operation when LLM fails to parse properly.
        This is a fallback for common patterns that should be obvious.
        """
        instruction_lower = instruction.lower()
        
        # Remove versioning metadata prefixes (e.g., "1°", "a)", "b) (supprimé)", "c) (nouveau)")
        import re
        # Pattern to match versioning prefixes like "1°", "a)", "b) (supprimé)", "c) (nouveau)", etc.
        versioning_pattern = r'^[a-z]*\d*[°)]*\s*(?:\([^)]*\))?\s*'
        cleaned_instruction = re.sub(versioning_pattern, '', instruction, flags=re.IGNORECASE).strip()
        cleaned_instruction_lower = cleaned_instruction.lower()
        
        logger.info("Cleaned instruction for fallback: '%s' -> '%s'", instruction, cleaned_instruction)
        
        # NEW: Handle multi-step instructions with bullet points
        if "est ainsi modifié" in cleaned_instruction_lower and "–" in cleaned_instruction:
            logger.info("Inferring multi-step REPLACE operations from bullet points")
            return self._parse_multi_step_instruction(cleaned_instruction)
        
        # NEW: Handle large replacement instructions
        if ("est remplacé par" in cleaned_instruction_lower and 
            "ainsi rédigés" in cleaned_instruction_lower):
            logger.info("Inferring large REPLACE operation")
            return self._parse_large_replacement_instruction(cleaned_instruction)
        
        # Pattern: "est remplacé par" - clearly a REPLACE operation
        if "est remplacé par" in cleaned_instruction_lower or "sont remplacés par" in cleaned_instruction_lower:
            logger.info("Inferring REPLACE operation from instruction pattern")
            
            # Try to extract quoted text
            quotes_pattern = r'«\s*([^»]+)\s*»'
            quotes = re.findall(quotes_pattern, cleaned_instruction)
            
            if len(quotes) >= 2:
                target_text = quotes[0].strip()
                replacement_text = quotes[1].strip()
                
                return [AmendmentOperation(
                    operation_type=OperationType.REPLACE,
                    target_text=target_text,
                    replacement_text=replacement_text,
                    position_hint="inferred from instruction",
                    sequence_order=1,
                    confidence_score=0.7  # Lower confidence for fallback
                )]
        
        # Pattern: "sont supprimés" or "est supprimé" - clearly a DELETE operation
        if "sont supprimés" in cleaned_instruction_lower or "est supprimé" in cleaned_instruction_lower or "sont abrogés" in cleaned_instruction_lower or "est abrogé" in cleaned_instruction_lower:
            logger.info("Inferring DELETE operation from instruction pattern")
            return [AmendmentOperation(
                operation_type=OperationType.DELETE,
                target_text=None,
                replacement_text=None,
                position_hint="inferred from instruction",
                sequence_order=1,
                confidence_score=0.8
            )]
        
        # Pattern: Just versioning metadata like "1° (Supprimé)" - DELETE operation
        if cleaned_instruction.strip() == "" and "(supprimé)" in instruction_lower:
            logger.info("Inferring DELETE operation from versioning metadata")
            return [AmendmentOperation(
                operation_type=OperationType.DELETE,
                target_text=None,
                replacement_text=None,
                position_hint="inferred from instruction",
                sequence_order=1,
                confidence_score=0.9
            )]
        
        # If we can't infer, return empty list - better than crashing
        logger.warning("Could not infer operation from instruction: %.200s...", instruction)
        return []

    def _parse_multi_step_instruction(self, instruction: str) -> List[AmendmentOperation]:
        """Parse multi-step instructions with bullet points (–)."""
        import re
        
        # Split by bullet points
        bullet_pattern = r'–\s*'
        parts = re.split(bullet_pattern, instruction)
        
        # First part contains the context (e.g., "Le premier alinéa est ainsi modifié :")
        context = parts[0].strip()
        position_hint = context.replace(" est ainsi modifié :", "").replace(" :", "").strip()
        
        operations = []
        sequence_order = 1
        
        # Process each bullet point
        for part in parts[1:]:
            part = part.strip().rstrip(';').strip()
            if not part:
                continue
                
            # Look for REPLACE patterns
            if "sont remplacés par" in part.lower() or "est remplacé par" in part.lower():
                quotes_pattern = r'«\s*([^»]+)\s*»'
                quotes = re.findall(quotes_pattern, part)
                
                if len(quotes) >= 2:
                    target_text = quotes[0].strip()
                    replacement_text = quotes[1].strip()
                    
                    # Extract position hint from this specific bullet
                    bullet_position = position_hint
                    if "à la fin" in part.lower():
                        bullet_position += ", à la fin"
                    
                    operations.append(AmendmentOperation(
                        operation_type=OperationType.REPLACE,
                        target_text=target_text,
                        replacement_text=replacement_text,
                        position_hint=bullet_position,
                        sequence_order=sequence_order,
                        confidence_score=0.8  # Good confidence for pattern matching
                    ))
                    sequence_order += 1
            
            # Look for DELETE patterns
            elif "sont supprimés" in part.lower() or "est supprimé" in part.lower():
                # Try to extract target text from quotes
                quotes_pattern = r'«\s*([^»]+)\s*»'
                quotes = re.findall(quotes_pattern, part)
                
                target_text = quotes[0].strip() if quotes else None
                
                operations.append(AmendmentOperation(
                    operation_type=OperationType.DELETE,
                    target_text=target_text,
                    replacement_text=None,
                    position_hint=position_hint,
                    sequence_order=sequence_order,
                    confidence_score=0.8
                ))
                sequence_order += 1
        
        logger.info("Parsed %d operations from multi-step instruction", len(operations))
        return operations

    def _parse_large_replacement_instruction(self, instruction: str) -> List[AmendmentOperation]:
        """Parse large replacement instructions like 'Le I est remplacé par des I à I ter ainsi rédigés'."""
        import re
        
        # Pattern to extract what is being replaced
        replace_pattern = r'(.*?)\s+est\s+remplacé\s+par.*?ainsi\s+rédigés?\s*:\s*«\s*(.*?)\s*»'
        match = re.search(replace_pattern, instruction, re.DOTALL | re.IGNORECASE)
        
        if match:
            target_text = match.group(1).strip()
            replacement_text = match.group(2).strip()
            
            logger.info("Parsed large replacement: target='%s...', replacement='%s...'", 
                       target_text[:50], replacement_text[:50])
            
            return [AmendmentOperation(
                operation_type=OperationType.REPLACE,
                target_text=target_text,
                replacement_text=replacement_text,
                position_hint=target_text,
                sequence_order=1,
                confidence_score=0.8
            )]
        
        # Fallback: try simpler pattern
        simple_pattern = r'(.*?)\s+est\s+remplacé\s+par'
        match = re.search(simple_pattern, instruction, re.IGNORECASE)
        
        if match:
            target_text = match.group(1).strip()
            
            # Try to extract replacement text from quotes
            quotes_pattern = r'«\s*(.*?)\s*»'
            quote_match = re.search(quotes_pattern, instruction, re.DOTALL)
            replacement_text = quote_match.group(1).strip() if quote_match else None
            
            if replacement_text:
                logger.info("Parsed simple replacement: target='%s', replacement='%s...'", 
                           target_text, replacement_text[:50])
                
                return [AmendmentOperation(
                    operation_type=OperationType.REPLACE,
                    target_text=target_text,
                    replacement_text=replacement_text,
                    position_hint=target_text,
                    sequence_order=1,
                    confidence_score=0.7
                )]
        
        logger.warning("Could not parse large replacement instruction")
        return []

    def _serialize_operations(self, operations: List[AmendmentOperation]) -> dict:
        """Serialize operations for caching."""
        return {
            "operations": [
                {
                    "operation_type": op.operation_type.value,
                    "target_text": op.target_text,
                    "replacement_text": op.replacement_text,
                    "position_hint": op.position_hint,
                    "sequence_order": op.sequence_order,
                    "confidence_score": op.confidence_score
                }
                for op in operations
            ]
        }

    def _deserialize_operations(self, cached_data: dict) -> List[AmendmentOperation]:
        """Deserialize operations from cache."""
        operations = []
        for op_data in cached_data["operations"]:
            operation = AmendmentOperation(
                operation_type=OperationType(op_data["operation_type"]),
                target_text=op_data["target_text"],
                replacement_text=op_data["replacement_text"],
                position_hint=op_data["position_hint"],
                sequence_order=op_data["sequence_order"],
                confidence_score=op_data["confidence_score"]
            )
            operations.append(operation)
        return operations

    def clear_cache(self) -> int:
        """Clear the instruction decomposer cache."""
        return self.cache.clear_by_prefix("instruction_decomposer")

    def get_cache_stats(self) -> dict:
        """Get cache statistics."""
        return self.cache.get_stats_by_prefix("instruction_decomposer") 