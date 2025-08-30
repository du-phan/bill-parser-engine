"""
InstructionDecomposer component for parsing French legal amendment instructions.

This component uses LLM-based parsing to decompose compound amendment instructions
into atomic operations with precise type identification and sequencing.
"""

import json
import re
import logging
import os
import time
from typing import List, Optional, Dict, Any

from mistralai import Mistral

from bill_parser_engine.core.reference_resolver.models import AmendmentOperation, OperationType
from bill_parser_engine.core.reference_resolver.cache_manager import SimpleCache
from bill_parser_engine.core.reference_resolver.prompts import INSTRUCTION_DECOMPOSER_SYSTEM_PROMPT
from bill_parser_engine.core.reference_resolver.rate_limiter import rate_limiter, call_mistral_with_messages

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
        if self.use_cache:
            cache_key_data = {'amendment_instruction': amendment_instruction}
            cached_result = self.cache.get("instruction_decomposer", cache_key_data)
            if cached_result is not None:
                logger.info("Found cached decomposition result")
                return self._deserialize_operations(cached_result)

        start_time = time.time()
        
        try:
            # Call LLM with sophisticated prompt
            system_prompt = self._build_system_prompt()
            user_message = self._build_user_message(amendment_instruction)
            
            response = call_mistral_with_messages(
                client=self.client,
                rate_limiter=rate_limiter,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                component_name="InstructionDecomposer",
                temperature=0.0,  # Deterministic parsing
                response_format={"type": "json_object"}
            )
            
            # Parse response
            response_content = response.choices[0].message.content
            logger.debug("Raw LLM response: %s", response_content)
            
            result_data = json.loads(response_content)
            operations = self._parse_response(result_data, amendment_instruction)

            # Normalization: enrich operations with structured, machine-usable position hints
            operations = self._normalize_operations(operations, amendment_instruction)
            
            processing_time = int((time.time() - start_time) * 1000)
            logger.info("Decomposed into %d operations", len(operations))
            
            # Cache result
            if self.use_cache:
                serialized_operations = self._serialize_operations(operations)
                self.cache.set("instruction_decomposer", cache_key_data, serialized_operations)
            
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
                
                # Extract and normalize fields based on operation type
                target_text = op_data.get("target_text")
                replacement_text = op_data.get("replacement_text")

                # Corrective downgrade: REPLACE without target_text → REWRITE
                if operation_type == OperationType.REPLACE and (not target_text) and replacement_text:
                    logger.warning("Downgrading operation %d from REPLACE to REWRITE due to missing target_text", i)
                    operation_type = OperationType.REWRITE

                # Validate operation-specific requirements (after potential downgrade)
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
                # Skip invalid operations instead of aborting the entire decomposition
                logger.error("Skipping invalid operation %d: %s", i, e)
                continue
        
        # Sort by sequence order
        operations.sort(key=lambda op: op.sequence_order)
        
        # Validate sequence numbering
        expected_order = 1
        for op in operations:
            if op.sequence_order != expected_order:
                logger.warning("Non-sequential operation order: expected %d, got %d", expected_order, op.sequence_order)
            expected_order += 1
        
        return operations

    # --- Normalization helpers ---
    _ORDINAL_TO_INDEX = {
        "premier": 1, "première": 1,
        "deuxième": 2, "second": 2, "seconde": 2,
        "troisième": 3,
        "quatrième": 4,
        "cinquième": 5,
        "sixième": 6,
        "septième": 7,
        "huitième": 8,
        "neuvième": 9,
        "dixième": 10,
    }

    _STRUCTURAL_ANCHOR_PATTERNS = [
        # Après le 5° bis du I
        re.compile(r"(?i)après\s+le\s+(?P<point>\d+)°(?:\s+(?P<point_suffix>bis|ter|quater|quinquies|sexies|septies|octies|nonies|d[ée]cies))?\s+du\s+(?P<section>[IVXLCDM]+)(?:\s+(?P<section_suffix>bis|ter|quater|quinquies|sexies|septies|octies|nonies|d[ée]cies))?\b"),
        # Avant le 3° du II
        re.compile(r"(?i)avant\s+le\s+(?P<point>\d+)°(?:\s+(?P<point_suffix>bis|ter|quater|quinquies|sexies|septies|octies|nonies|d[ée]cies))?\s+du\s+(?P<section>[IVXLCDM]+)(?:\s+(?P<section_suffix>bis|ter|quater|quinquies|sexies|septies|octies|nonies|d[ée]cies))?\b"),
        # Au 3° du II (generic in-place scope)
        re.compile(r"(?i)au\s+(?P<point>\d+)°(?:\s+(?P<point_suffix>bis|ter|quater|quinquies|sexies|septies|octies|nonies|décies))?\s+du\s+(?P<section>[IVXLCDM]+)(?:\s+(?P<section_suffix>bis|ter|quater|quinquies|sexies|septies|octies|nonies|décies))?\b"),
        # À la fin du III / Au début du II
        re.compile(r"(?i)à\s+la\s+fin\s+du\s+(?P<section>[IVXLCDM]+)(?:\s+(?P<section_suffix>bis|ter|quater|quinquies|sexies|septies|octies|nonies|d[ée]cies))?\b"),
        re.compile(r"(?i)au\s+début\s+du\s+(?P<section>[IVXLCDM]+)(?:\s+(?P<section_suffix>bis|ter|quater|quinquies|sexies|septies|octies|nonies|d[ée]cies))?\b"),
    ]

    def _normalize_operations(self, operations: List[AmendmentOperation], instruction: str) -> List[AmendmentOperation]:
        """
        Convert natural-language hints into structured, machine-usable anchors encoded as JSON in position_hint.
        - Ordinal alinéa → REWRITE with {"type":"alinea","index":N|"last"|"prev"}
        - Token micro-insert → {"type":"token","after_word"|"before_word":"X","scope":"sentence"}
        - Structural anchors (points/sections) → {"type":"structure", ... , "placement":"after|before|at_end|at_start"}
        - Sentence scoping → add {"sentence_position":"first|second|last"}
        """
        normalized: List[AmendmentOperation] = []
        instr = (instruction or "").replace("’", "'")

        # Pre-detect instruction-level anchors
        alinea_anchor = self._detect_alinea_anchor(instr)
        sentence_position = self._detect_sentence_position(instr)
        structural_anchor = self._detect_structural_anchor(instr)
        token_anchor = self._detect_token_anchor(instr)
        relative_alinea = self._detect_relative_alinea(instr)

        for op in operations:
            pos_data: Dict[str, Any] = {}

            # Prefer explicit token anchor for micro-inserts
            if token_anchor is not None and op.operation_type in (OperationType.INSERT, OperationType.REPLACE, OperationType.ADD, OperationType.REWRITE):
                # Keep explicit token keys (after_word/after_words/before_word/before_words)
                pos_data.update(token_anchor)
                # Prefer scope at sentence level for micro-edits
                pos_data["scope"] = pos_data.get("scope", "sentence")
                # Heuristic: if instruction mentions rewriting the end of an alinéa, set token_action
                if re.search(r"(?i)la\s+fin\s+du\s+.*alinéa", instr):
                    pos_data["token_action"] = pos_data.get("token_action", "replace_tail")

            # Ordinal alinéa normalization
            if self._looks_like_alinea_target(op, instr) or alinea_anchor or relative_alinea:
                # Convert REPLACE of a full alinéa label into REWRITE of that alinéa
                if op.operation_type == OperationType.REPLACE and self._is_full_alinea_target(op.target_text or ""):
                    op.operation_type = OperationType.REWRITE
                    op.target_text = None
                # Store explicit alinéa index
                if alinea_anchor and "index" in alinea_anchor:
                    pos_data["alinea_index"] = alinea_anchor["index"]
                elif relative_alinea:
                    pos_data["alinea_index"] = relative_alinea

            # Structural anchor (points/sections)
            if structural_anchor is not None:
                pos_data = {**pos_data, **structural_anchor}

            # Sentence-level scoping
            if sentence_position is not None:
                pos_data.setdefault("sentence_position", sentence_position)

            # Deterministic inference for common micro-edit form:
            # "Après le mot : « X », la fin du Nᵉ alinéa est ainsi rédigée : « Y »"
            if ("après le mot" in instr.lower() or "après les mots" in instr.lower()) and "la fin du" in instr.lower() and "alinéa" in instr.lower():
                pos_data.setdefault("token_action", "replace_tail")
                if alinea_anchor and "index" in alinea_anchor:
                    pos_data.setdefault("alinea_index", alinea_anchor["index"])

            # If we collected any structured data, encode into position_hint
            if pos_data:
                try:
                    op.position_hint = json.dumps(pos_data, ensure_ascii=False)
                except Exception:
                    # Fallback to string format if JSON encoding fails
                    op.position_hint = ", ".join(f"{k}={v}" for k, v in pos_data.items())

            normalized.append(op)

        return normalized

    def _looks_like_alinea_target(self, op: AmendmentOperation, instruction: str) -> bool:
        if not op:
            return False
        text = (op.target_text or "") + " " + (op.position_hint or "") + " " + instruction
        return bool(re.search(r"(?i)\balinéa\b", text))

    def _detect_alinea_anchor(self, instruction: str) -> Optional[Dict[str, Any]]:
        # Examples: "Le cinquième alinéa", "Le premier alinéa", "Au deuxième alinéa"
        m = re.search(r"(?i)(?:le|au|du)\s+([a-zéèêîôûàç]+)\s+alinéa", instruction)
        if m:
            word = m.group(1).lower()
            index = self._ORDINAL_TO_INDEX.get(word)
            if index:
                return {"type": "alinea", "index": index}
        # "dernier alinéa"
        if re.search(r"(?i)dernier\s+alinéa", instruction):
            return {"type": "alinea", "index": "last"}
        return None

    def _detect_relative_alinea(self, instruction: str) -> Optional[str]:
        # e.g., "l'alinéa précédent"
        if re.search(r"(?i)alinéa\s+précédent", instruction):
            return "prev"
        return None

    def _is_full_alinea_target(self, target_text: str) -> bool:
        """Return True if target_text denotes a full alinéa selection like 'Le cinquième alinéa'."""
        if not target_text:
            return False
        t = target_text.strip().lower()
        # Normalize apostrophes
        t = t.replace("’", "'")
        # Match forms like 'Le cinquième alinéa', 'Le premier alinéa'
        m = re.match(r"^(?:le|la)\s+([a-zéèêîôûàç]+)\s+alinéa\s*$", t)
        if not m:
            return False
        word = m.group(1)
        # Only ordinal words, not 'précédent'
        return word in self._ORDINAL_TO_INDEX or word == "dernier"

    def _detect_sentence_position(self, instruction: str) -> Optional[str]:
        s = instruction.lower()
        if "première phrase" in s:
            return "first"
        if "seconde phrase" in s or "deuxième phrase" in s:
            return "second"
        if "dernière phrase" in s:
            return "last"
        if "début de la première phrase" in s:
            return "first_start"
        if "à la fin de la dernière phrase" in s:
            return "last_end"
        return None

    def _detect_structural_anchor(self, instruction: str) -> Optional[Dict[str, Any]]:
        for pat in self._STRUCTURAL_ANCHOR_PATTERNS:
            m = pat.search(instruction)
            if m:
                d = {k: v for k, v in m.groupdict().items() if v}
                placement = ""
                p = pat.pattern.lower()
                if p.startswith("(?i)après"):
                    placement = "after"
                elif p.startswith("(?i)avant"):
                    placement = "before"
                elif "à\\s+la\\s+fin" in p:
                    placement = "at_end"
                elif "au\\s+début" in p:
                    placement = "at_start"
                # Generic in-place scoping like "au 3° du II"
                elif re.search(r"\(\?i\)au\\s+\(\?P<point>", p):
                    placement = "at"
                d["type"] = "structure"
                d["placement"] = placement
                return d
        return None

    def _detect_token_anchor(self, instruction: str) -> Optional[Dict[str, Any]]:
        # Après le mot : « X »
        m = re.search(r"(?i)après\s+le\s+mot\s*:\s*«\s*([^»]+)\s*»", instruction)
        if m:
            return {"after_word": m.group(1).strip()}
        # Après les mots : « X »
        m = re.search(r"(?i)après\s+les\s+mots\s*:\s*«\s*([^»]+)\s*»", instruction)
        if m:
            return {"after_words": m.group(1).strip()}
        # Avant le mot / Avant les mots
        m = re.search(r"(?i)avant\s+le\s+mot\s*:\s*«\s*([^»]+)\s*»", instruction)
        if m:
            return {"before_word": m.group(1).strip()}
        m = re.search(r"(?i)avant\s+les\s+mots\s*:\s*«\s*([^»]+)\s*»", instruction)
        if m:
            return {"before_words": m.group(1).strip()}
        return None

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