"""
Reference object linking component.

This component links each located reference to its grammatical object using
context-aware French grammatical analysis. It implements smart context-switching:
DELETIONAL references are analyzed using deleted_or_replaced_text context,
while DEFINITIONAL references use intermediate_after_state_text context.

Uses Mistral Chat API with Function Calling for complex grammatical analysis.

ENHANCED PROMPTING (v2.0):
The system prompt has been significantly enhanced with:
- Comprehensive French grammatical patterns (basic + complex prepositional constructions)
- 5 detailed reference-object relationship types with examples
- French legal text conventions and article structure guidance
- 4-step systematic analysis methodology
- Detailed confidence scoring guidelines (0.4-1.0 range with clear criteria)
- Robust edge case handling (multiple objects, ambiguous cases, long-distance relationships)
- 6 comprehensive examples covering simple to complex scenarios
- Quality assurance rules for consistent, accurate analysis

This enhanced prompting addresses the complexity of real French legislative text
and provides the LLM with sufficient context to handle sophisticated grammatical
analysis tasks accurately and consistently.
"""

import json
import logging
import os
from typing import List, Optional

from mistralai import Mistral

from bill_parser_engine.core.reference_resolver.config import MISTRAL_MODEL
from bill_parser_engine.core.reference_resolver.models import (
    LinkedReference,
    LocatedReference,
    ReconstructorOutput,
    ReferenceSourceType,
)
from bill_parser_engine.core.reference_resolver.prompts import (
    REFERENCE_OBJECT_LINKER_SYSTEM_PROMPT,
    REFERENCE_OBJECT_LINKER_EVALUATOR_SYSTEM_PROMPT,
)
from bill_parser_engine.core.reference_resolver.cache_manager import get_cache
from bill_parser_engine.core.reference_resolver.rate_limiter import rate_limiter

logger = logging.getLogger(__name__)


class ReferenceObjectLinker:
    """
    Links each located reference to its grammatical object using French grammar analysis.
    
    This component implements smart context-switching - DELETIONAL references are analyzed
    using deleted_or_replaced_text context, while DEFINITIONAL references use 
    intermediate_after_state_text context. This ensures grammatical objects are found
    in the correct textual environment.
    
    Uses Mistral Chat API with Function Calling for complex grammatical analysis.
    
    EMPIRICALLY-VALIDATED ITERATIVE APPROACH:
    Based on testing, single-pass evaluation is insufficient for complex French legal references.
    The iterative evaluator-optimizer pattern provides:
    - High-confidence results terminate early (>90% confidence)
    - Low-confidence results get iterative refinement (up to 2 iterations by default)
    - Evaluator mistakes are caught and corrected by subsequent iterations
    - Most references will succeed on first pass, minimizing performance impact
    
    OPTIMIZATIONS:
    - Early termination for high-confidence results
    - Reduced default iterations (2 instead of 3)
    - Comprehensive error handling and fallback strategies
    - Full iteration history tracking for debugging
    
    CACHING:
    - Individual reference results are cached to avoid redundant LLM calls
    - Cache keys include reference text, source type, context, and prompt version
    - Cache can be disabled by setting use_cache=False in constructor
    - Call clear_cache() to invalidate all cached results for this component
    """

    def __init__(self, api_key: Optional[str] = None, use_cache: bool = True, max_iterations: int = 5, high_confidence_threshold: float = 0.9):
        """
        Initialize the reference object linker with Mistral client.

        Args:
            api_key: Mistral API key (defaults to MISTRAL_API_KEY environment variable)
            use_cache: Whether to use caching for LLM calls (default: True)
            max_iterations: Maximum number of evaluator-optimizer iterations (default: 2)
            high_confidence_threshold: Confidence threshold for early termination (default: 0.9)
        """
        self.client = Mistral(api_key=api_key or os.getenv("MISTRAL_API_KEY"))
        self.system_prompt = REFERENCE_OBJECT_LINKER_SYSTEM_PROMPT
        self.tool_schema = self._create_tool_schema()
        self.use_cache = use_cache
        self.cache = get_cache() if use_cache else None
        self.evaluator_tool_schema = self._create_evaluator_tool_schema()
        self.optimizer_feedback_tool_schema = self._create_optimizer_feedback_tool_schema()
        self.max_iterations = max(1, max_iterations)
        self.high_confidence_threshold = high_confidence_threshold

    def _create_tool_schema(self) -> List[dict]:
        """
        Create the function calling tool schema for grammatical analysis.
        
        Returns:
            The tool schema for the link_reference_to_object function
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": "link_reference_to_object",
                    "description": "Analyze French grammatical structure to link a legal reference to its object",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "object": {
                                "type": "string",
                                "description": "Complete noun phrase that the reference modifies (e.g., 'activités', 'producteurs', 'la liste')"
                            },
                            "agreement_analysis": {
                                "type": "string",
                                "description": "Grammatical reasoning (e.g., 'Masculine plural agreement with activités mentioned 3 words before')"
                            },
                            "confidence": {
                                "type": "number",
                                "description": "Confidence 0-1, lower for ambiguous cases or distant grammatical relationships"
                            }
                        },
                        "required": ["object", "agreement_analysis", "confidence"]
                    }
                }
            }
        ]

    def _create_evaluator_tool_schema(self) -> List[dict]:
        """
        Create the function calling tool schema for evaluator-optimizer pattern.
        
        Returns:
            The tool schema for the evaluate_and_correct_link function
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": "evaluate_and_correct_link",
                    "description": "Evaluate and potentially correct a reference-object link",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "is_correct": {
                                "type": "boolean",
                                "description": "Whether the original linking is correct"
                            },
                            "corrected_object": {
                                "type": "string",
                                "description": "Corrected object if original was wrong, or original object if correct"
                            },
                            "corrected_agreement_analysis": {
                                "type": "string",
                                "description": "Corrected or confirmed agreement analysis"
                            },
                            "corrected_confidence": {
                                "type": "number",
                                "description": "Updated confidence score (0-1)"
                            },
                            "evaluation_reasoning": {
                                "type": "string",
                                "description": "Explanation of the evaluation and any corrections made"
                            }
                        },
                        "required": ["is_correct", "corrected_object", "corrected_agreement_analysis", "corrected_confidence", "evaluation_reasoning"]
                    }
                }
            }
        ]

    def _create_optimizer_feedback_tool_schema(self) -> List[dict]:
        """
        Create the function calling tool schema for optimizer to improve based on feedback.
        
        Returns:
            The tool schema for the improve_reference_linking function
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": "improve_reference_linking",
                    "description": "Improve reference linking based on evaluator feedback",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "improved_object": {
                                "type": "string",
                                "description": "Improved noun phrase that the reference modifies (e.g., 'activités', 'producteurs', 'la liste')"
                            },
                            "improved_agreement_analysis": {
                                "type": "string",
                                "description": "Improved grammatical reasoning based on feedback"
                            },
                            "improved_confidence": {
                                "type": "number",
                                "description": "Updated confidence 0-1 after considering feedback"
                            },
                            "improvement_reasoning": {
                                "type": "string",
                                "description": "Explanation of how the feedback was addressed"
                            }
                        },
                        "required": ["improved_object", "improved_agreement_analysis", "improved_confidence", "improvement_reasoning"]
                    }
                }
            }
        ]

    def link_references(
        self, 
        located_references: List[LocatedReference], 
        reconstructor_output: ReconstructorOutput,
        original_law_article: str
    ) -> List[LinkedReference]:
        """
        Link located references to their grammatical objects using French grammar analysis.

        Args:
            located_references: List of references found by ReferenceLocator
            reconstructor_output: Output from TextReconstructor with context texts
            original_law_article: The full original law article text for DELETIONAL reference context

        Returns:
            List of LinkedReference objects with grammatical objects identified

        Raises:
            ValueError: If input validation fails
        """
        # Input validation
        if not isinstance(located_references, list):
            raise ValueError("located_references must be a list")
        
        if not isinstance(reconstructor_output, ReconstructorOutput):
            raise ValueError("reconstructor_output must be a ReconstructorOutput object")
            
        if not isinstance(original_law_article, str):
            raise ValueError("original_law_article must be a string")

        linked_references = []

        for ref in located_references:
            try:
                # Context switching based on reference source
                context_text = self._select_context(ref.source, reconstructor_output, original_law_article)
                
                # Skip if no context available
                if not context_text.strip():
                    logger.warning(f"No context available for reference: {ref.reference_text}")
                    continue

                # Try cache first if enabled
                linked_ref = None
                if self.use_cache and self.cache:
                    cache_input = self._create_cache_input(ref, context_text)
                    cached_result = self.cache.get("reference_object_linker", cache_input)
                    if cached_result:
                        linked_ref = self._dict_to_linked_reference(cached_result)
                        logger.debug(f"Cache HIT for reference: {ref.reference_text}")
                
                # If not in cache, process with LLM
                if linked_ref is None:
                    # Create grammatical analysis prompt
                    user_prompt = self._build_grammatical_analysis_prompt(ref, context_text)

                    # Use shared rate limiter with retry logic for 429 errors
                    def make_api_call():
                        return self.client.chat.complete(
                            model=MISTRAL_MODEL,
                            temperature=0.0,
                            messages=[
                                {
                                    "role": "system",
                                    "content": self.system_prompt
                                },
                                {
                                    "role": "user",
                                    "content": user_prompt
                                }
                            ],
                            tools=self.tool_schema,
                            tool_choice="any"
                        )
                    
                    response = rate_limiter.execute_with_retry(make_api_call, "ReferenceObjectLinker")

                    # Extract and validate tool call
                    tool_call = self._extract_tool_call(response)
                    if tool_call and self._validate_tool_call_response(tool_call):
                        linked_ref = self._create_linked_reference(ref, tool_call["arguments"])
                        
                        # Apply evaluator-optimizer pattern for quality assurance
                        if linked_ref:
                            # Early termination check: if initial confidence is very high, skip iteration
                            if linked_ref.confidence >= self.high_confidence_threshold:
                                logger.info(f"High confidence ({linked_ref.confidence:.3f}) - skipping iteration for reference: {linked_ref.reference_text}")
                            else:
                                linked_ref = self._evaluate_and_correct_link(linked_ref, context_text)
                        
                        # Cache the result if caching is enabled
                        if self.use_cache and self.cache and linked_ref:
                            cache_input = self._create_cache_input(ref, context_text)
                            cache_result = self._linked_reference_to_dict(linked_ref)
                            self.cache.set("reference_object_linker", cache_input, cache_result)
                            logger.debug(f"Cache SET for reference: {ref.reference_text}")
                        
                        if linked_ref:
                            logger.info(f"Successfully linked reference: {ref.reference_text} → {linked_ref.object}")
                    else:
                        logger.warning(f"Invalid tool call response for ref: {ref.reference_text}")
                        continue
                
                # Add to results if we have a valid linked reference
                if linked_ref:
                    linked_references.append(linked_ref)

            except Exception as e:
                logger.error(f"Failed to link reference {ref.reference_text}: {e}")
                # Continue processing other references

        logger.info(f"Successfully linked {len(linked_references)} out of {len(located_references)} references")
        return linked_references

    def _select_context(self, source: ReferenceSourceType, output: ReconstructorOutput, original_law_article: str) -> str:
        """
        Select appropriate text context based on reference source type.

        CRITICAL: This implements the core design principle of context-switching:
        - DELETIONAL references use original law article context to understand what's being removed
        - DEFINITIONAL references use intermediate after-state context to understand what's being added

        Args:
            source: The source type of the reference (DELETIONAL or DEFINITIONAL)
            output: ReconstructorOutput containing both text contexts
            original_law_article: The full original law article text

        Returns:
            The appropriate context text for analysis
        """
        if source == ReferenceSourceType.DELETIONAL:
            # DELETIONAL references need the full original article context to understand 
            # what the references in the deleted text refer to (e.g., "aux 1° ou 2° du II" 
            # referring to "activités" that appear elsewhere in the original article)
            return original_law_article
        else:
            # DEFINITIONAL references use the intermediate state after amendment
            # because they need to understand the new context where the reference appears
            return output.intermediate_after_state_text

    def _build_grammatical_analysis_prompt(self, ref: LocatedReference, context_text: str) -> str:
        """
        Build a contextual prompt for grammatical analysis.

        Args:
            ref: The located reference to analyze
            context_text: The appropriate context text

        Returns:
            Formatted prompt for grammatical analysis
        """
        return f"""
Analyze this French legal reference and identify its grammatical object:

REFERENCE TO ANALYZE: "{ref.reference_text}"

FULL CONTEXT: "{context_text}"

REFERENCE SOURCE: {ref.source.value}

Please identify the complete noun phrase that this reference modifies, defines, or clarifies. Consider:
1. French grammatical agreement (gender, number)
2. Proximity and logical relationship
3. Semantic meaning in legal context
4. Preposition patterns (au/à la/aux, du/de la/des, etc.)

Use the function call to provide your analysis.
"""

    def _extract_tool_call(self, response, expected_function_name: str = "link_reference_to_object") -> Optional[dict]:
        """
        Extract tool call from Mistral response.

        Args:
            response: The Mistral API response
            expected_function_name: The expected function name (default: "link_reference_to_object")

        Returns:
            The tool call dictionary, or None if no valid tool call found
        """
        try:
            if not response.choices:
                logger.warning("No choices in Mistral response")
                return None

            choice = response.choices[0]
            if not hasattr(choice.message, 'tool_calls') or not choice.message.tool_calls:
                logger.warning("No tool calls in response")
                return None

            tool_call = choice.message.tool_calls[0]
            
            if tool_call.function.name != expected_function_name:
                logger.warning(f"Unexpected function name: {tool_call.function.name}, expected: {expected_function_name}")
                return None

            # Parse the arguments
            arguments = json.loads(tool_call.function.arguments) if isinstance(tool_call.function.arguments, str) else tool_call.function.arguments
            
            return {
                "name": tool_call.function.name,
                "arguments": arguments
            }

        except Exception as e:
            logger.error(f"Error extracting tool call: {e}")
            return None

    def _validate_tool_call_response(self, tool_call: dict) -> bool:
        """
        Validate that tool call response contains required fields.

        Args:
            tool_call: The extracted tool call dictionary

        Returns:
            True if valid, False otherwise
        """
        try:
            arguments = tool_call.get("arguments", {})
            required_fields = ["object", "agreement_analysis", "confidence"]
            
            for field in required_fields:
                if field not in arguments:
                    logger.warning(f"Missing required field in tool call: {field}")
                    return False
                    
                # Validate types
                if field == "confidence":
                    confidence = arguments[field]
                    if not isinstance(confidence, (int, float)) or not (0 <= confidence <= 1):
                        logger.warning(f"Invalid confidence value: {confidence}")
                        return False
                elif not isinstance(arguments[field], str) or not arguments[field].strip():
                    logger.warning(f"Invalid {field} value: {arguments[field]}")
                    return False

            return True

        except Exception as e:
            logger.error(f"Error validating tool call: {e}")
            return False

    def _create_linked_reference(self, ref: LocatedReference, arguments: dict) -> LinkedReference:
        """
        Create a LinkedReference object from the tool call results.

        Args:
            ref: The original located reference
            arguments: The validated tool call arguments

        Returns:
            A LinkedReference object
        """
        return LinkedReference(
            reference_text=ref.reference_text,
            source=ref.source,
            object=arguments["object"].strip(),
            agreement_analysis=arguments["agreement_analysis"].strip(),
            confidence=float(arguments["confidence"])
        )

    def _evaluate_and_correct_link(self, linked_ref: LinkedReference, context_text: str) -> Optional[LinkedReference]:
        """
        Apply iterative evaluator-optimizer pattern to validate and improve the linking.
        
        This implements the Anthropic best practice of iterative refinement where:
        1. Evaluator assesses current result
        2. If unsatisfactory, provides feedback to optimizer
        3. Optimizer improves the result based on feedback
        4. Process repeats until satisfactory or max_iterations reached
        
        Args:
            linked_ref: The initial linked reference from the first LLM
            context_text: The context text used for analysis
            
        Returns:
            Improved LinkedReference or None if evaluation fails
        """
        current_result = linked_ref
        iteration_history = []
        
        for iteration in range(self.max_iterations):
            try:
                logger.debug(f"Evaluator-optimizer iteration {iteration + 1}/{self.max_iterations} for reference: {linked_ref.reference_text}")
                
                # Build evaluation prompt with iteration history
                evaluation_prompt = self._build_evaluation_prompt(current_result, context_text, iteration_history)
                
                # Call evaluator LLM with retry logic
                def make_evaluator_call():
                    return self.client.chat.complete(
                        model=MISTRAL_MODEL,
                        temperature=0.0,
                        messages=[
                            {
                                "role": "system", 
                                "content": self._get_evaluator_system_prompt()
                            },
                            {
                                "role": "user",
                                "content": evaluation_prompt
                            }
                        ],
                        tools=self.evaluator_tool_schema,
                        tool_choice="any"
                    )
                
                response = rate_limiter.execute_with_retry(make_evaluator_call, "ReferenceObjectLinker-Evaluator")
                
                # Extract evaluation result
                tool_call = self._extract_tool_call(response, expected_function_name="evaluate_and_correct_link")
                if not tool_call or not self._validate_evaluator_response(tool_call):
                    logger.warning(f"Invalid evaluator response at iteration {iteration + 1} for ref: {linked_ref.reference_text}")
                    break
                
                args = tool_call["arguments"]
                
                # If evaluator says result is correct, we're done
                if args["is_correct"]:
                    logger.info(f"Evaluator approved result after {iteration + 1} iteration(s) for reference: {linked_ref.reference_text}")
                    return LinkedReference(
                        reference_text=current_result.reference_text,
                        source=current_result.source,
                        object=args["corrected_object"].strip(),
                        agreement_analysis=args["corrected_agreement_analysis"].strip(),
                        confidence=float(args["corrected_confidence"])
                    )
                
                # If it's the last iteration, return the evaluator's correction
                if iteration == self.max_iterations - 1:
                    logger.info(f"Final evaluator correction after {self.max_iterations} iterations for reference: {linked_ref.reference_text}")
                    logger.info(f"  Original: {linked_ref.object}")
                    logger.info(f"  Final: {args['corrected_object']}")
                    return LinkedReference(
                        reference_text=current_result.reference_text,
                        source=current_result.source,
                        object=args["corrected_object"].strip(),
                        agreement_analysis=args["corrected_agreement_analysis"].strip(),
                        confidence=float(args["corrected_confidence"])
                    )
                
                # Otherwise, send feedback to optimizer for improvement
                logger.info(f"Evaluator requesting improvement at iteration {iteration + 1}: {args['evaluation_reasoning']}")
                
                # Store this iteration in history
                iteration_history.append({
                    "iteration": iteration + 1,
                    "result": current_result,
                    "evaluator_feedback": args["evaluation_reasoning"],
                    "evaluator_correction": args["corrected_object"]
                })
                
                # Get optimizer improvement
                improved_result = self._get_optimizer_improvement(
                    original_ref=linked_ref,
                    current_result=current_result,
                    evaluator_feedback=args["evaluation_reasoning"],
                    context_text=context_text,
                    iteration_history=iteration_history
                )
                
                if improved_result:
                    current_result = improved_result
                else:
                    logger.warning(f"Optimizer failed to improve at iteration {iteration + 1}, using evaluator correction")
                    # Fall back to evaluator's correction
                    return LinkedReference(
                        reference_text=current_result.reference_text,
                        source=current_result.source,
                        object=args["corrected_object"].strip(),
                        agreement_analysis=args["corrected_agreement_analysis"].strip(),
                        confidence=float(args["corrected_confidence"])
                    )
                    
            except Exception as e:
                logger.error(f"Evaluator-optimizer iteration {iteration + 1} failed for reference {linked_ref.reference_text}: {e}")
                break
        
        # If we get here, something went wrong - return the current best result
        logger.warning(f"Evaluator-optimizer process incomplete for reference: {linked_ref.reference_text}")
        return current_result

    def _build_evaluation_prompt(self, linked_ref: LinkedReference, context_text: str, iteration_history: List[dict] = None) -> str:
        """
        Build prompt for the evaluator LLM.
        
        Args:
            linked_ref: The linked reference to evaluate
            context_text: The context text
            iteration_history: History of previous iterations (optional)
            
        Returns:
            Formatted evaluation prompt
        """
        history_text = ""
        if iteration_history:
            history_text = f"\n\nITERATION HISTORY:\n"
            for hist in iteration_history:
                history_text += f"Iteration {hist['iteration']}: Object '{hist['result'].object}' "
                history_text += f"→ Feedback: {hist['evaluator_feedback']}\n"
            history_text += f"\nThis is now iteration {len(iteration_history) + 1}.\n"
        
        return f"""
ORIGINAL TASK: Analyze this French legal reference and identify its grammatical object:

REFERENCE TO ANALYZE: "{linked_ref.reference_text}"
FULL CONTEXT: "{context_text}"
REFERENCE SOURCE: {linked_ref.source.value}

ORIGINAL INSTRUCTIONS GIVEN TO FIRST LLM:
Please identify the complete noun phrase that this reference modifies, defines, or clarifies. Consider:
1. French grammatical agreement (gender, number)
2. Proximity and logical relationship
3. Semantic meaning in legal context
4. Preposition patterns (au/à la/aux, du/de la/des, etc.)

CURRENT RESULT TO EVALUATE:
- OBJECT: "{linked_ref.object}"
- AGREEMENT ANALYSIS: "{linked_ref.agreement_analysis}"
- CONFIDENCE: {linked_ref.confidence}

{history_text}

YOUR TASK: Evaluate if the current linking is correct. You have the same context and instructions that were given to the LLM. Apply the same analytical framework to judge if the result is correct.

Key evaluation points:
1. Is the identified object actually present in the context?
2. Is it a concrete legal entity (activité, producteur, substance, etc.) rather than an abstract reference?
3. Does the grammatical relationship make sense?
4. Does the object make logical sense in the legal context?
5. Is the confidence score reasonable?
6. If this is a later iteration, has the result improved from previous attempts?

Use the evaluation function to provide your assessment.
"""

    def _get_evaluator_system_prompt(self) -> str:
        """
        Get the system prompt for the evaluator LLM.
        
        Returns:
            System prompt for evaluation
        """
        return REFERENCE_OBJECT_LINKER_EVALUATOR_SYSTEM_PROMPT

    def _validate_evaluator_response(self, tool_call: dict) -> bool:
        """
        Validate evaluator tool call response.
        
        Args:
            tool_call: The extracted tool call dictionary
            
        Returns:
            True if valid, False otherwise
        """
        try:
            arguments = tool_call.get("arguments", {})
            
            required_fields = ["is_correct", "corrected_object", "corrected_agreement_analysis", 
                             "corrected_confidence", "evaluation_reasoning"]
            
            for field in required_fields:
                if field not in arguments:
                    logger.warning(f"Missing required field in evaluator response: {field}")
                    return False
                    
            # Validate types
            if not isinstance(arguments["is_correct"], bool):
                logger.warning(f"Invalid is_correct type: {type(arguments['is_correct'])}, value: {arguments['is_correct']}")
                return False
                
            confidence = arguments["corrected_confidence"]
            if not isinstance(confidence, (int, float)) or not (0 <= confidence <= 1):
                logger.warning(f"Invalid corrected_confidence value: {confidence}")
                return False
                
            for field in ["corrected_object", "corrected_agreement_analysis", "evaluation_reasoning"]:
                if not isinstance(arguments[field], str) or not arguments[field].strip():
                    logger.warning(f"Invalid {field} value: {arguments[field]}")
                    return False

            return True

        except Exception as e:
            logger.error(f"Error validating evaluator response: {e}")
            return False

    def _create_cache_input(self, ref: LocatedReference, context_text: str) -> dict:
        """
        Create cache input data for a reference linking operation.

        Args:
            ref: The located reference to link
            context_text: The context text for analysis

        Returns:
            Dictionary suitable for cache key generation
        """
        return {
            "reference_text": ref.reference_text,
            "source": ref.source.value,
            "context_text": context_text,
            "system_prompt_hash": hash(self.system_prompt) % (10**8)  # Include prompt version
        }

    def _linked_reference_to_dict(self, linked_ref: LinkedReference) -> dict:
        """
        Convert LinkedReference to dictionary for caching.

        Args:
            linked_ref: LinkedReference object to serialize

        Returns:
            Dictionary representation
        """
        return {
            "reference_text": linked_ref.reference_text,
            "source": linked_ref.source.value,
            "object": linked_ref.object,
            "agreement_analysis": linked_ref.agreement_analysis,
            "confidence": linked_ref.confidence
        }

    def _dict_to_linked_reference(self, data: dict) -> LinkedReference:
        """
        Convert dictionary back to LinkedReference object.

        Args:
            data: Dictionary representation from cache

        Returns:
            LinkedReference object
        """
        return LinkedReference(
            reference_text=data["reference_text"],
            source=ReferenceSourceType(data["source"]),
            object=data["object"],
            agreement_analysis=data["agreement_analysis"],
            confidence=data["confidence"]
        )

    def clear_cache(self) -> int:
        """
        Clear all cached results for this component.

        Returns:
            Number of cache entries cleared
        """
        if self.cache:
            return self.cache.invalidate("reference_object_linker")
        return 0

    def _get_optimizer_improvement(
        self,
        original_ref: LinkedReference,
        current_result: LinkedReference,
        evaluator_feedback: str,
        context_text: str,
        iteration_history: List[dict]
    ) -> Optional[LinkedReference]:
        """
        Get improved result from optimizer based on evaluator feedback.
        
        Args:
            original_ref: The original reference from first LLM call
            current_result: The current linking result to improve
            evaluator_feedback: Feedback from the evaluator
            context_text: The context text for analysis
            iteration_history: History of previous iterations
            
        Returns:
            Improved LinkedReference or None if optimization fails
        """
        try:
            # Build optimizer improvement prompt
            improvement_prompt = self._build_optimizer_improvement_prompt(
                original_ref, current_result, evaluator_feedback, context_text, iteration_history
            )
            
            # Call optimizer LLM with retry logic
            def make_optimizer_call():
                return self.client.chat.complete(
                    model=MISTRAL_MODEL,
                    temperature=0.0,
                    messages=[
                        {
                            "role": "system",
                            "content": self._get_optimizer_system_prompt()
                        },
                        {
                            "role": "user",
                            "content": improvement_prompt
                        }
                    ],
                    tools=self.optimizer_feedback_tool_schema,
                    tool_choice="any"
                )
            
            response = rate_limiter.execute_with_retry(make_optimizer_call, "ReferenceObjectLinker-Optimizer")
            
            # Extract improvement result
            tool_call = self._extract_tool_call(response, expected_function_name="improve_reference_linking")
            if tool_call and self._validate_optimizer_response(tool_call):
                args = tool_call["arguments"]
                
                logger.debug(f"Optimizer improvement: {args['improvement_reasoning']}")
                
                return LinkedReference(
                    reference_text=original_ref.reference_text,
                    source=original_ref.source,
                    object=args["improved_object"].strip(),
                    agreement_analysis=args["improved_agreement_analysis"].strip(),
                    confidence=float(args["improved_confidence"])
                )
            else:
                logger.warning(f"Invalid optimizer response for ref: {original_ref.reference_text}")
                return None
                
        except Exception as e:
            logger.error(f"Optimizer improvement failed for reference {original_ref.reference_text}: {e}")
            return None

    def _build_optimizer_improvement_prompt(
        self,
        original_ref: LinkedReference,
        current_result: LinkedReference,
        evaluator_feedback: str,
        context_text: str,
        iteration_history: List[dict]
    ) -> str:
        """
        Build prompt for the optimizer to improve based on evaluator feedback.
        
        Args:
            original_ref: The original reference 
            current_result: Current linking result
            evaluator_feedback: Feedback from evaluator
            context_text: The context text
            iteration_history: History of previous iterations
            
        Returns:
            Formatted improvement prompt
        """
        history_text = ""
        if iteration_history:
            history_text = "\n\nPREVIOUS ITERATIONS:\n"
            for hist in iteration_history:
                history_text += f"Iteration {hist['iteration']}: "
                history_text += f"Object: '{hist['result'].object}' "
                history_text += f"→ Feedback: {hist['evaluator_feedback']}\n"
        
        return f"""
TASK: Improve your reference linking based on evaluator feedback.

ORIGINAL ANALYSIS TASK:
Reference to analyze: "{original_ref.reference_text}"
Full context: "{context_text}"
Reference source: {original_ref.source.value}

CURRENT LINKING RESULT:
- Object: "{current_result.object}"
- Agreement analysis: "{current_result.agreement_analysis}"
- Confidence: {current_result.confidence}

EVALUATOR FEEDBACK:
{evaluator_feedback}

{history_text}

INSTRUCTIONS:
The evaluator has identified issues with your current linking. Please:
1. Carefully consider the evaluator's feedback
2. Re-examine the context text with this feedback in mind
3. Apply the same French grammatical analysis principles:
   - French grammatical agreement (gender, number)
   - Proximity and logical relationship
   - Semantic meaning in legal context
   - Preposition patterns (au/à la/aux, du/de la/des, etc.)
4. Provide an improved linking that addresses the evaluator's concerns

Use the improvement function to provide your revised analysis.
"""

    def _get_optimizer_system_prompt(self) -> str:
        """
        Get system prompt for the optimizer LLM.
        
        Returns:
            System prompt for optimization
        """
        return """You are a French legal text analysis expert specializing in grammatical object linking.

Your task is to improve reference-object linking based on evaluator feedback. You have the same expertise as the original analyzer, but now you have additional guidance from an evaluator who has identified potential issues.

Key principles:
1. LISTEN TO FEEDBACK: The evaluator has carefully reviewed your work and provided specific guidance
2. RE-EXAMINE: Look at the context again with fresh eyes, considering the evaluator's perspective
3. APPLY FRENCH GRAMMAR: Use proper French grammatical analysis
4. BE CONCRETE: Focus on actual legal entities (activités, producteurs, substances) not abstract references
5. IMPROVE CONFIDENCE: If you're more certain after the feedback, increase confidence; if still uncertain, be honest

Your goal is to produce a better linking result that addresses the evaluator's concerns while maintaining grammatical accuracy."""

    def _validate_optimizer_response(self, tool_call: dict) -> bool:
        """
        Validate optimizer tool call response.
        
        Args:
            tool_call: The extracted tool call dictionary
            
        Returns:
            True if valid, False otherwise
        """
        try:
            arguments = tool_call.get("arguments", {})
            
            required_fields = ["improved_object", "improved_agreement_analysis", 
                             "improved_confidence", "improvement_reasoning"]
            
            for field in required_fields:
                if field not in arguments:
                    logger.warning(f"Missing required field in optimizer response: {field}")
                    return False
                    
            # Validate types
            confidence = arguments["improved_confidence"]
            if not isinstance(confidence, (int, float)) or not (0 <= confidence <= 1):
                logger.warning(f"Invalid improved_confidence value: {confidence}")
                return False
                
            for field in ["improved_object", "improved_agreement_analysis", "improvement_reasoning"]:
                if not isinstance(arguments[field], str) or not arguments[field].strip():
                    logger.warning(f"Invalid {field} value: {arguments[field]}")
                    return False

            return True

        except Exception as e:
            logger.error(f"Error validating optimizer response: {e}")
            return False 