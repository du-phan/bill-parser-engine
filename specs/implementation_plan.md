# Normative Reference Resolver: Implementation Plan

## 1. Introduction

This document provides a detailed, step-by-step implementation plan for refactoring the `bill-parser-engine` to align with the architecture and component definitions outlined in `specs/normative_reference_resolver_detailed_spec.md`. The goal is to create a clear and actionable guide for a coding LLM to implement the new pipeline.

**Key Architectural Principles:**

- Mirror the "Lawyer's Mental Model": Generate distinct BeforeState and AfterState outputs
- Use DELETIONAL/DEFINITIONAL reference classification for context-aware processing
- Implement stateless components as tools, with one stateful orchestrator
- Use specific Mistral API modes: JSON Mode for structured outputs, Function Calling for complex analysis
- Ensure robust error handling and validation at each stage

The implementation is divided into four main phases:

1.  **Project Restructuring and Data Model Definition**: Laying the foundation by updating the data structures and cleaning up the project layout.
2.  **Stateless Component Implementation**: Building the core processing tools of the pipeline.
3.  **Stateful Component Implementation**: Implementing the central orchestrator.
4.  **Pipeline Assembly and Finalization**: Wiring all components together and defining the final output.

**Critical Implementation Notes:**

- All LLM calls must use `temperature=0.0` for deterministic outputs
- Implement comprehensive error handling for LLM API failures
- Add validation between pipeline stages to catch data corruption early
- Use structured logging for debugging and performance monitoring

---

## 2. Phase 1: Project Restructuring and Data Model Definition

This phase focuses on establishing the correct project structure and defining the core data models that all components will use.

### Step 2.1: Clean Up Obsolete Files

The new specification makes several old components redundant. To avoid confusion, delete the following files from `bill_parser_engine/core/reference_resolver/`:

- `detector.py`
- `classifier.py`
- `resolver.py`
- `substitutor.py`
- `prompts.py` (if it exists)
- `utils.py` (functionality will be in specific components or a new shared module if needed)

### Step 2.2: Redefine Core Data Models

The data models are the backbone of the pipeline. Update `bill_parser_engine/core/reference_resolver/models.py` to contain the following dataclasses and enums. This replaces the old models entirely.

**File: `bill_parser_engine/core/reference_resolver/models.py`**

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict

# --- Enums ---

class TargetOperationType(Enum):
    INSERT = "INSERT"
    MODIFY = "MODIFY"
    ABROGATE = "ABROGATE"
    RENUMBER = "RENUMBER"
    OTHER = "OTHER"

class ReferenceSourceType(Enum):
    DELETIONAL = "DELETIONAL"
    DEFINITIONAL = "DEFINITIONAL"

# --- Component-Specific Data Structures ---

@dataclass
class BillChunk:
    """Represents an atomic, processable piece of a legislative bill, as output by the BillSplitter."""
    text: str
    titre_text: str
    article_label: str
    article_introductory_phrase: Optional[str]
    major_subdivision_label: Optional[str]
    major_subdivision_introductory_phrase: Optional[str]
    numbered_point_label: Optional[str]
    hierarchy_path: List[str]
    chunk_id: str
    start_pos: int
    end_pos: int

@dataclass
class TargetArticle:
    """Identifies the legal article being targeted by an amendment chunk."""
    operation_type: TargetOperationType
    code: Optional[str]
    article: Optional[str]
    confidence: float
    raw_text: Optional[str]

@dataclass
class ReconstructorOutput:
    """Output from the TextReconstructor, providing the 'before' and 'after' text fragments."""
    deleted_or_replaced_text: str
    intermediate_after_state_text: str

@dataclass
class LocatedReference:
    """Represents a reference found in a text fragment, tagged by its source."""
    reference_text: str
    start_position: int
    end_position: int
    source: ReferenceSourceType
    confidence: float

@dataclass
class LinkedReference:
    """Represents a reference that has been grammatically linked to its object."""
    reference_text: str
    source: ReferenceSourceType
    object: str
    agreement_analysis: str
    confidence: float

@dataclass
class ResolvedReference:
    """Contains a linked reference and its fetched content."""
    linked_reference: LinkedReference
    resolved_content: str
    retrieval_metadata: Dict[str, str]

@dataclass
class ResolutionResult:
    """The complete output from the ResolutionOrchestrator."""
    resolved_deletional_references: List[ResolvedReference]
    resolved_definitional_references: List[ResolvedReference]
    resolution_tree: Dict
    unresolved_references: List[LinkedReference]

# --- Final Pipeline Output ---

@dataclass
class LegalState:
    """Represents a fully resolved legal state (either before or after amendment)."""
    state_text: str
    synthesis_metadata: Dict

@dataclass
class LegalAnalysisOutput:
    """The final, high-level output of the entire pipeline."""
    before_state: LegalState
    after_state: LegalState
    source_chunk: BillChunk
    target_article: TargetArticle
```

---

## 3. Phase 2: Stateless Component Implementation

This phase involves creating the individual, stateless tools that the pipeline orchestrator will use. Each component should be implemented in its own file within `bill_parser_engine/core/reference_resolver/`.

### Step 3.1: Adapt `BillSplitter`

**Purpose**: Deterministically split legislative text into `BillChunk` objects based on its hierarchical structure. This component must remain rule-based (no LLM).

**File: `bill_parser_engine/core/reference_resolver/bill_splitter.py`**

- **Task**: Adapt the existing `BillSplitter` to align with the new `BillChunk` data model.
- **Action**:
  1.  Locate all `BillChunk(...)` instantiation calls within the `split` method.
  2.  For each instantiation, update the arguments to match the new `BillChunk` dataclass definition from Step 2.2.
  3.  Specifically, remove the `major_subdivision_label_raw` and `numbered_point_label_raw` arguments, as they are no longer part of the model.
  4.  Ensure all other fields (`text`, `titre_text`, `article_label`, etc.) are populated correctly according to the parsing logic. The existing regex and multi-pass logic is sound and should be retained.
  5.  No other functional changes are required. This is purely a data model alignment task.

### Step 3.2: Create `TargetArticleIdentifier`

**API Mode**: JSON Mode (structured output with simple schema)
**Purpose**: Infer the primary legal article targeted by an amendment chunk

**File: `bill_parser_engine/core/reference_resolver/target_article_identifier.py`**

**Implementation Algorithm:**

1. Initialize with MistralClient and system prompt containing 2-3 real examples
2. For each chunk, create user prompt with chunk text and metadata context
3. Call Mistral API in JSON mode with strict schema validation
4. Parse response and handle enum conversion with fallback to "OTHER"
5. Return TargetArticle object with confidence scoring

**Key Implementation Details:**

- **System Prompt Requirements**: Include examples for INSERT, MODIFY, and ABROGATE operations
- **Context Enhancement**: Include chunk's article_introductory_phrase in user prompt for better code inference
- **Error Handling**: Wrap JSON parsing in try-catch, return low-confidence TargetArticle on failure
- **Validation**: Ensure operation_type maps to valid enum values

**Pseudo-code for `identify` method:**

```
def identify(chunk: BillChunk) -> TargetArticle:
    user_prompt = f"""
    Chunk: {chunk.text}
    Context: {chunk.article_introductory_phrase}
    Hierarchy: {' > '.join(chunk.hierarchy_path)}
    """

    try:
        response = call_mistral_json_mode(system_prompt, user_prompt)
        content = parse_json(response)
        validate_operation_type(content["operation_type"])
        return create_target_article(content)
    except Exception as e:
        log_error(e)
        return fallback_target_article(confidence=0.1)
```

### Step 3.3: Create `OriginalTextRetriever`

**Purpose**: Fetch current legal text for target articles using hybrid approach (pylegifrance + fallbacks)

**File: `bill_parser_engine/core/reference_resolver/original_text_retriever.py`**

**Implementation Algorithm:**

1. **Primary Strategy**: Use pylegifrance API with proper error handling
2. **Fallback Strategy**: Web search for articles not in pylegifrance
3. **Caching Strategy**: File-based cache to avoid repeated API calls
4. **INSERT Operation Handling**: Return empty string for non-existent articles

**Key Implementation Details:**

- **Method Signature**: `fetch_article_text(code: str, article: str) -> tuple[str, dict]`
- **Return Values**: (article_text, retrieval_metadata)
- **Cache Key Format**: f"{code}_{article}_{current_date}"
- **Error Scenarios**: Network failures, article not found, malformed responses

**Pseudo-code for `fetch_article_text` method:**

```
def fetch_article_text(code: str, article: str) -> tuple[str, dict]:
    cache_key = generate_cache_key(code, article)

    # Check cache first
    if cached_content := check_cache(cache_key):
        return cached_content, {"source": "cache"}

    try:
        # Primary: pylegifrance API
        content = call_pylegifrance_api(code, article)
        if content:
            cache_content(cache_key, content)
            return content, {"source": "pylegifrance", "success": True}
    except Exception as e:
        log_warning(f"pylegifrance failed: {e}")

    try:
        # Fallback: web search
        content = search_web_for_article(code, article)
        if content:
            cache_content(cache_key, content)
            return content, {"source": "web_search", "success": True}
    except Exception as e:
        log_error(f"All retrieval methods failed: {e}")

    return "", {"source": "none", "success": False}
```

**Cache Management:**

- Use file-based cache with expiration (30 days for legal texts)
- Cache structure: `{cache_dir}/{code}_{article}_{date}.txt`
- Implement cache cleanup for old entries

### Step 3.4: Create `TextReconstructor`

**API Mode**: JSON Mode (structured output for deterministic text operations)
**Purpose**: Apply amendment instructions mechanically to original text, producing before/after fragments

**File: `bill_parser_engine/core/reference_resolver/text_reconstructor.py`**

**Critical Component Note**: This is the cornerstone of the "Lawyer's Mental Model" - it creates the fundamental before/after text states that drive all downstream processing.

**Implementation Algorithm:**

1. **Prompt Engineering**: System prompt with 3+ real amendment examples covering:
   - Simple replacements ("les mots X sont remplacés par Y")
   - Deletions ("la phrase X est supprimée")
   - Insertions ("après X, il est inséré Y")
   - Complex multi-step amendments
2. **Input Validation**: Ensure original_article is not empty for MODIFY operations
3. **Deterministic Processing**: Use temperature=0.0 and structured JSON response
4. **Output Validation**: Verify both required fields are present and non-empty

**Key Implementation Details:**

- **System Prompt Structure**: Must include mechanical instruction examples without interpretation
- **Error Handling**: Handle cases where LLM cannot parse amendment instructions
- **Edge Cases**: Handle INSERT operations where original_article may be empty
- **Validation**: Ensure deleted_text appears in original_article for MODIFY operations

**Pseudo-code for `reconstruct` method:**

```
def reconstruct(original_law_article: str, amendment_chunk: BillChunk) -> ReconstructorOutput:
    # Validation phase
    if amendment_chunk.target_article.operation_type == "MODIFY" and not original_law_article:
        raise ValueError("Cannot modify empty article")

    user_prompt = create_structured_prompt(original_law_article, amendment_chunk.text)

    try:
        response = call_mistral_json_mode(system_prompt, user_prompt)
        content = parse_and_validate_json(response)

        # Post-processing validation
        if content["deleted_or_replaced_text"] and original_law_article:
            validate_deleted_text_exists(content["deleted_or_replaced_text"], original_law_article)

        return ReconstructorOutput(
            deleted_or_replaced_text=content["deleted_or_replaced_text"],
            intermediate_after_state_text=content["intermediate_after_state_text"]
        )
    except Exception as e:
        log_error(f"Text reconstruction failed: {e}")
        return fallback_reconstructor_output(original_law_article, amendment_chunk)
```

**System Prompt Requirements:**

- Include examples for: replacement, deletion, insertion, complex multi-step
- Emphasize mechanical application without legal interpretation
- Specify exact JSON schema with required fields
- Handle edge cases like empty deletions or full article replacements

### Step 3.5: Create `ReferenceLocator`

**API Mode**: JSON Mode (structured list output with precise positioning)
**Purpose**: Locate all normative references in before/after text fragments and tag by source type

**File: `bill_parser_engine/core/reference_resolver/reference_locator.py`**

**Core Innovation**: This component implements the DELETIONAL/DEFINITIONAL classification that drives the entire downstream process. DELETIONAL references use original law context, DEFINITIONAL use amended text context.

**Implementation Algorithm:**

1. **Dual Fragment Analysis**: Process both deleted_or_replaced_text and intermediate_after_state_text simultaneously
2. **Reference Pattern Recognition**: Identify French legal reference patterns (articles, codes, regulations, EU law)
3. **Precise Positioning**: Return exact character indices for downstream substitution
4. **Source Classification**: Tag each reference as DELETIONAL or DEFINITIONAL
5. **Confidence Scoring**: Assign confidence based on reference clarity and pattern matching

**Key Implementation Details:**

- **Reference Patterns**: "l'article L. X", "du règlement (CE) n° X", "au X° du Y", "du même article"
- **Position Validation**: Ensure start_position < end_position and indices are within text bounds
- **Edge Cases**: Handle empty fragments, overlapping references, malformed citations
- **Quality Control**: Filter low-confidence references (< 0.7) unless manually verified

**Pseudo-code for `locate` method:**

```
def locate(reconstructor_output: ReconstructorOutput) -> List[LocatedReference]:
    fragments = {
        "DELETIONAL": reconstructor_output.deleted_or_replaced_text,
        "DEFINITIONAL": reconstructor_output.intermediate_after_state_text
    }

    user_prompt = create_dual_fragment_prompt(fragments)

    try:
        response = call_mistral_json_mode(system_prompt, user_prompt)
        content = parse_and_validate_json(response)

        located_refs = []
        for ref_data in content.get("located_references", []):
            # Validation
            if not validate_reference_positioning(ref_data, fragments):
                log_warning(f"Invalid positioning for ref: {ref_data}")
                continue

            located_refs.append(create_located_reference(ref_data))

        return filter_by_confidence(located_refs, min_confidence=0.7)

    except Exception as e:
        log_error(f"Reference location failed: {e}")
        return []
```

**System Prompt Requirements:**

- Examples covering French legal references, EU regulations, internal cross-references
- Clear positioning instructions with character-level precision
- DELETIONAL/DEFINITIONAL tagging examples
- Edge case handling (empty fragments, no references found)

**Validation Logic:**

- Verify reference_text matches text at specified positions
- Ensure source classification is valid enum value
- Check for duplicate or overlapping references
- Validate confidence scores are in [0, 1] range

### Step 3.6: Create `ReferenceObjectLinker`

**API Mode**: Function Calling (complex grammatical analysis requiring structured reasoning)
**Purpose**: Link each located reference to its grammatical object using context-aware French grammatical analysis

**File: `bill_parser_engine/core/reference_resolver/reference_object_linker.py`**

**Core Innovation**: This component implements smart context-switching - DELETIONAL references are analyzed using deleted_or_replaced_text context, while DEFINITIONAL references use intermediate_after_state_text context. This ensures grammatical objects are found in the correct textual environment.

**Implementation Algorithm:**

1. **Context Selection**: For each reference, select appropriate text context based on source type
2. **Grammatical Analysis**: Use Function Calling to perform sophisticated French grammar analysis
3. **Object Identification**: Identify the complete noun phrase that the reference modifies/defines
4. **Agreement Validation**: Verify grammatical agreement (gender, number, proximity)
5. **Confidence Assessment**: Score based on grammatical clarity and distance from object

**Key Implementation Details:**

- **Context Switching Logic**: DELETIONAL → deleted_or_replaced_text, DEFINITIONAL → intermediate_after_state_text
- **Grammatical Patterns**: French reference-object agreement rules (au/à la/aux, du/de la/des, etc.)
- **Function Call Validation**: Ensure all required fields are returned from tool call
- **Error Recovery**: Handle cases where tool calls fail or return malformed data

**Pseudo-code for `link_references` method:**

```
def link_references(located_references: List[LocatedReference], reconstructor_output: ReconstructorOutput) -> List[LinkedReference]:
    linked_references = []

    for ref in located_references:
        # Context switching based on reference source
        context_text = select_context(ref.source, reconstructor_output)

        # Create contextual prompt for grammatical analysis
        prompt = build_grammatical_analysis_prompt(ref, context_text)

        try:
            response = call_mistral_function_calling(prompt, tool_schema)
            tool_call = extract_tool_call(response)

            if validate_tool_call_response(tool_call):
                linked_ref = create_linked_reference(ref, tool_call.arguments)
                linked_references.append(linked_ref)
            else:
                log_warning(f"Invalid tool call response for ref: {ref.reference_text}")

        except Exception as e:
            log_error(f"Failed to link reference {ref.reference_text}: {e}")
            # Continue processing other references

    return linked_references

def select_context(source: ReferenceSourceType, output: ReconstructorOutput) -> str:
    if source == ReferenceSourceType.DELETIONAL:
        return output.deleted_or_replaced_text
    else:
        return output.intermediate_after_state_text
```

**Function Call Schema:**

```json
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
          "description": "Complete noun phrase that the reference modifies (e.g., 'activités', 'producteurs', 'substances')"
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
```

**Edge Cases to Handle:**

- References with no clear grammatical object in context
- Multiple possible objects with equal grammatical validity
- Long-distance grammatical relationships across sentence boundaries
- Pronoun resolution ("celui-ci", "ces derniers", etc.)

---

## 4. Phase 3: Stateful Component Implementation

This phase covers the central, stateful component that drives the recursive resolution process.

### Step 4.1: Create `ResolutionOrchestrator`

**Purpose**: Stateful component managing recursive resolution of linked references using a stack-based approach
**Architecture**: Central orchestrator using other components as stateless tools

**File: `bill_parser_engine/core/reference_resolver/resolution_orchestrator.py`**

**Core Algorithm - Stack-Based Recursive Resolution:**

1. **Initialization**: Create resolution_stack with input linked_references, separate tracking for DELETIONAL/DEFINITIONAL
2. **Main Processing Loop**: While stack not empty, process references with recursion control
3. **Reference Resolution**: Use appropriate retrieval strategy based on reference type
4. **Recursive Sub-Reference Discovery**: For resolved content, find and process new DEFINITIONAL references
5. **Cycle Detection**: Prevent infinite loops through reference cycle tracking
6. **Result Assembly**: Separate resolved references by original type (DELETIONAL/DEFINITIONAL)

**Implementation Algorithm:**

```
class ResolutionOrchestrator:
    def __init__(self, text_retriever, reference_locator, reference_linker, max_depth=3):
        self.tools = {text_retriever, reference_locator, reference_linker}
        self.max_depth = max_depth

    def resolve_references(linked_references: List[LinkedReference]) -> ResolutionResult:
        # Initialize tracking structures
        resolution_stack = deque([(ref, 0) for ref in linked_references])  # (reference, depth)
        resolved_deletional = []
        resolved_definitional = []
        unresolved = []
        seen_references = set()  # Cycle detection
        resolution_tree = {"depth": 0, "nodes": []}

        while resolution_stack and len(resolution_stack) > 0:
            current_ref, depth = resolution_stack.popleft()

            # Depth control
            if depth >= self.max_depth:
                unresolved.append(current_ref)
                continue

            # Cycle detection
            ref_signature = create_reference_signature(current_ref)
            if ref_signature in seen_references:
                log_warning(f"Cycle detected for reference: {current_ref.reference_text}")
                continue
            seen_references.add(ref_signature)

            try:
                # Step 1: Assess relevance (for now, assume all relevant)
                if not assess_relevance(current_ref):
                    continue

                # Step 2: Retrieve content
                content, metadata = retrieve_reference_content(current_ref)
                if not content:
                    unresolved.append(current_ref)
                    continue

                # Step 3: Create resolved reference
                resolved_ref = ResolvedReference(
                    linked_reference=current_ref,
                    resolved_content=content,
                    retrieval_metadata=metadata
                )

                # Step 4: Categorize by original source
                if current_ref.source == ReferenceSourceType.DELETIONAL:
                    resolved_deletional.append(resolved_ref)
                else:
                    resolved_definitional.append(resolved_ref)

                # Step 5: Recursive sub-reference discovery (ONLY for DEFINITIONAL)
                if current_ref.source == ReferenceSourceType.DEFINITIONAL and depth < self.max_depth:
                    sub_references = discover_sub_references(content)
                    for sub_ref in sub_references:
                        if sub_ref.source == ReferenceSourceType.DEFINITIONAL:
                            resolution_stack.append((sub_ref, depth + 1))

            except Exception as e:
                log_error(f"Failed to resolve reference {current_ref.reference_text}: {e}")
                unresolved.append(current_ref)

        return ResolutionResult(
            resolved_deletional_references=resolved_deletional,
            resolved_definitional_references=resolved_definitional,
            resolution_tree=resolution_tree,
            unresolved_references=unresolved
        )
```

**Key Implementation Details:**

- **Relevance Assessment**: For now, implement as simple heuristic (all references relevant), future enhancement point
- **Reference Classification**: Use pattern matching to determine retrieval strategy (French code vs EU regulation vs internal reference)
- **Content Retrieval**: Delegate to OriginalTextRetriever with appropriate parameters based on reference type
- **Sub-Reference Discovery**: Use ReferenceLocator + ReferenceObjectLinker on retrieved content
- **Cycle Detection**: Track reference signatures to prevent infinite recursion

**Cycle Detection Signature Function:**

- Implement a helper function `_create_reference_signature(ref: LinkedReference) -> str`.
- The signature should be a unique, deterministic string for a given reference.
- A robust signature can be created by concatenating key fields: `f"{ref.source.value}:{ref.object}:{ref.reference_text}"`.

**Critical Design Decisions:**

- Only DEFINITIONAL sub-references are added to stack (DELETIONAL references don't spawn recursion)
- Max depth limit prevents infinite recursion
- Failed resolutions don't stop the entire process
- All processing is stateless except for the orchestrator's resolution tracking

**Error Handling Strategy:**

- Individual reference failures don't abort entire resolution process
- Comprehensive logging for debugging failed resolutions
- Graceful degradation when sub-components fail
- Clear separation between resolved and unresolved references

---

## 5. Phase 4: Pipeline Assembly and Finalization

This final phase creates the last component and wires everything together into a single, callable pipeline.

### Step 5.1: Create `LegalStateSynthesizer`

**API Mode**: JSON Mode (structured output for final text synthesis)
**Purpose**: Perform final substitution of resolved references into text fragments to create BeforeState and AfterState

**File: `bill_parser_engine/core/reference_resolver/legal_state_synthesizer.py`**

**Critical Component Note**: This is the final step that creates the lawyer-readable, fully interpretable legal states. Quality of substitution directly impacts pipeline usefulness.

**Implementation Algorithm:**

1. **Dual Synthesis Process**: Create BeforeState and AfterState through separate LLM calls
2. **Context-Aware Substitution**: Use appropriate resolved references for each state
3. **Grammatical Preservation**: Maintain French legal text style and readability
4. **Quality Validation**: Ensure substitutions are grammatically correct and legally coherent

**Key Implementation Details:**

- **BeforeState Logic**: Substitute DELETIONAL references in deleted_or_replaced_text
- **AfterState Logic**: Substitute DEFINITIONAL references in intermediate_after_state_text
- **Substitution Strategy**: Replace reference phrases with resolved content while preserving grammar
- **Quality Control**: Validate that all references have been properly substituted

**Pseudo-code for `synthesize` method:**

```
def synthesize(
    self,
    resolution_result: ResolutionResult,
    reconstructor_output: ReconstructorOutput,
    source_chunk: BillChunk,
    target_article: TargetArticle
) -> LegalAnalysisOutput:
    # Synthesize BeforeState
    before_state = self._synthesize_state(
        base_text=reconstructor_output.deleted_or_replaced_text,
        resolved_refs=resolution_result.resolved_deletional_references,
        state_type="BeforeState"
    )

    # Synthesize AfterState
    after_state = self._synthesize_state(
        base_text=reconstructor_output.intermediate_after_state_text,
        resolved_refs=resolution_result.resolved_definitional_references,
        state_type="AfterState"
    )

    return LegalAnalysisOutput(
        before_state=before_state,
        after_state=after_state,
        source_chunk=source_chunk,
        target_article=target_article
    )

def _synthesize_state(self, base_text: str, resolved_refs: List[ResolvedReference], state_type: str) -> LegalState:
    substitution_map = {ref.linked_reference.reference_text: ref.resolved_content for ref in resolved_refs}

    prompt = self._build_substitution_prompt(
        text=base_text,
        substitutions=substitution_map,
        instruction=f"For the {state_type}, create readable legal text by substituting references with their resolved content."
    )

    # System prompt should be defined in the class __init__
    response = self.client.chat(...) # Use JSON mode
    content = json.loads(response.choices[0].message.content)

    return LegalState(
        state_text=content["synthesized_text"],
        synthesis_metadata=content.get("metadata", {})
    )
```

**System Prompt Requirements:**

- Examples of reference substitution maintaining legal text style
- Instructions for grammatical agreement preservation
- Handling of nested substitutions (when resolved content contains other references)
- Quality guidelines for legal text readability

**Substitution Strategies:**

- **Direct Replacement**: Simple reference → resolved content substitution
- **Grammatical Integration**: Adjust articles, prepositions for French grammar
- **Contextual Formatting**: Use parentheses, bullet points for complex definitions
- **Length Management**: Handle very long resolved content gracefully

**Quality Validation:**

- Verify all reference phrases have been replaced
- Check for grammatical correctness in French
- Ensure legal meaning is preserved
- Validate text readability and coherence

### Step 5.2: Create the Main Pipeline Entrypoint

**Purpose**: Orchestrate the complete pipeline execution, handling errors gracefully and providing comprehensive logging

**File: `bill_parser_engine/core/reference_resolver/__init__.py`**

**Pipeline Flow with Error Handling:**

1. **Component Initialization**: Create all pipeline components with proper configuration
2. **Input Validation**: Validate legislative text format and length
3. **Chunk Processing**: Process each chunk with comprehensive error handling
4. **Stage Validation**: Validate data between pipeline stages
5. **Result Assembly**: Collect successful outputs and error reports

**Key Implementation Details:**

- **Error Isolation**: Failed chunks don't abort entire pipeline
- **Progress Tracking**: Log progress for long-running legislative texts
- **Resource Management**: Properly manage LLM API calls and rate limiting
- **Validation Gates**: Validate data integrity between stages

**Enhanced Pipeline Pseudo-code:**

```python
def run_full_pipeline(legislative_text: str, client: MistralClient, config: PipelineConfig = None) -> PipelineResult:
    """
    Execute complete reference resolution pipeline with comprehensive error handling.

    Returns:
        PipelineResult containing successful outputs, failed chunks, and execution metadata
    """
    # Initialize components with error handling
    try:
        pipeline_components = initialize_pipeline_components(client, config)
    except Exception as e:
        return PipelineResult(success=False, error=f"Component initialization failed: {e}")

    # Input validation
    if not validate_legislative_text(legislative_text):
        return PipelineResult(success=False, error="Invalid legislative text format")

    final_outputs = []
    failed_chunks = []
    execution_metadata = {"total_chunks": 0, "successful": 0, "failed": 0}

    try:
        # Stage 1: Bill Splitting (deterministic, should not fail)
        bill_chunks = pipeline_components.splitter.split(legislative_text)
        execution_metadata["total_chunks"] = len(bill_chunks)

        for i, chunk in enumerate(bill_chunks):
            chunk_context = ChunkProcessingContext(chunk=chunk, index=i)

            try:
                # Process single chunk through entire pipeline
                result = process_single_chunk(chunk_context, pipeline_components)
                if result:
                    final_outputs.append(result)
                    execution_metadata["successful"] += 1
                else:
                    failed_chunks.append(create_failure_record(chunk, "No result produced"))
                    execution_metadata["failed"] += 1

            except Exception as e:
                log_error(f"Chunk {i} processing failed: {e}")
                failed_chunks.append(create_failure_record(chunk, str(e)))
                execution_metadata["failed"] += 1

        return PipelineResult(
            success=True,
            outputs=final_outputs,
            failed_chunks=failed_chunks,
            metadata=execution_metadata
        )

    except Exception as e:
        return PipelineResult(success=False, error=f"Pipeline execution failed: {e}")

def process_single_chunk(context: ChunkProcessingContext, components: PipelineComponents) -> Optional[LegalAnalysisOutput]:
    """Process a single chunk through all pipeline stages with validation."""

    # Stage validation helper
    def validate_stage_output(stage_name: str, output, validator_func):
        if not validator_func(output):
            raise ValueError(f"Stage {stage_name} produced invalid output")
        return output

    # Stage 2: Target Article Identification
    target_article = validate_stage_output(
        "TargetIdentification",
        components.target_identifier.identify(context.chunk),
        lambda x: x.confidence > 0.5 and x.article is not None
    )

    # Early exit for low-confidence or missing targets
    if not target_article.article:
        log_info(f"Skipping chunk {context.index}: No target article identified")
        return None

    # Stage 3: Original Text Retrieval
    original_text, retrieval_metadata = components.text_retriever.fetch_article_text(
        code=target_article.code,
        article=target_article.article
    )

    # Handle INSERT operations (empty original text is acceptable)
    if not original_text and target_article.operation_type != TargetOperationType.INSERT:
        log_warning(f"Skipping chunk {context.index}: Could not retrieve original text")
        return None

    # Stage 4: Text Reconstruction
    reconstructor_output = validate_stage_output(
        "TextReconstruction",
        components.reconstructor.reconstruct(original_text, context.chunk),
        lambda x: len(x.deleted_or_replaced_text) > 0 or len(x.intermediate_after_state_text) > 0
    )

    # Stage 5: Reference Location
    located_references = components.locator.locate(reconstructor_output)
    log_info(f"Chunk {context.index}: Found {len(located_references)} references")

    # Stage 6: Reference Object Linking
    linked_references = components.linker.link_references(located_references, reconstructor_output)

    # Stage 7: Resolution Orchestration
    resolution_result = components.orchestrator.resolve_references(linked_references)

    # Stage 8: Legal State Synthesis
    final_output = components.synthesizer.synthesize(
        resolution_result=resolution_result,
        reconstructor_output=reconstructor_output,
        chunk=context.chunk,
        target_article=target_article
    )

    return final_output
```

**Configuration and Resource Management:**

```python
@dataclass
class PipelineConfig:
    max_resolution_depth: int = 3
    confidence_threshold: float = 0.7
    cache_dir: str = "./reference_cache"
    rate_limit_per_minute: int = 60
    timeout_seconds: int = 300

@dataclass
class PipelineResult:
    success: bool
    outputs: List[LegalAnalysisOutput] = field(default_factory=list)
    failed_chunks: List[Dict] = field(default_factory=list)
    error: Optional[str] = None
    metadata: Dict = field(default_factory=dict)
```

**Critical Implementation Notes:**

- Use structured logging for debugging and performance monitoring
- Implement rate limiting for LLM API calls to avoid quota issues
- Add timeout handling for long-running operations
- Provide detailed error reporting for failed chunks
- Support partial success (some chunks succeed, others fail)

---

## 6. Implementation Priorities and Testing Strategy

### Phase 6.1: Implementation Order

**Priority 1 - Foundation (Week 1):**

1. Update data models (`models.py`) - foundational for all components
2. Clean up obsolete files - prevent confusion during development
3. Adapt `BillSplitter` - only component that's largely reusable

**Priority 2 - Core Pipeline (Week 2-3):**

1. Implement `TargetArticleIdentifier` - critical for article lookup
2. Implement `OriginalTextRetriever` - needed for text reconstruction
3. Implement `TextReconstructor` - cornerstone of the new architecture
4. Implement `ReferenceLocator` - enables DELETIONAL/DEFINITIONAL classification

**Priority 3 - Advanced Components (Week 4-5):**

1. Implement `ReferenceObjectLinker` - complex grammatical analysis
2. Implement simplified `ResolutionOrchestrator` (depth-1 only initially)
3. Implement `LegalStateSynthesizer` - final text generation

**Priority 4 - Integration and Polish (Week 6):**

1. Complete pipeline integration in `__init__.py`
2. Add comprehensive error handling and logging
3. Implement configuration system and resource management

### Phase 6.2: Testing Strategy

**Unit Testing Approach:**

- Each component should have isolated unit tests with mocked dependencies
- Use real French legislative examples from public sources
- Test edge cases: empty inputs, malformed text, API failures
- Validate deterministic behavior (same input → same output)

**Integration Testing:**

- Test component chains: Reconstructor → Locator → Linker
- Validate data flow between components using real legislative texts
- Test error propagation and recovery scenarios

**End-to-End Testing:**

- Use complete legislative bills from actual French law amendments
- Compare pipeline output against manual legal analysis
- Measure performance: processing time, API call counts, memory usage
- Test with different text lengths and complexity levels

**Quality Assurance:**

- **Reference Accuracy**: Verify resolved references match legal sources
- **Grammatical Correctness**: Ensure French legal text quality is maintained
- **Legal Fidelity**: Confirm BeforeState/AfterState accurately represent changes
- **Performance Benchmarks**: Process typical bills within reasonable time limits

### Phase 6.3: Development Guidelines

**LLM Integration Best Practices:**

- Always use structured prompts with multiple examples
- Implement robust error handling for API timeouts and rate limits
- Use deterministic settings (`temperature=0.0`) for reproducible results
- Log all LLM calls for debugging and prompt optimization

**Code Quality Standards:**

- Follow existing project conventions for imports, naming, and structure
- Add comprehensive docstrings for all public methods
- Use type hints extensively for better IDE support and validation
- Implement proper exception handling with specific error types

**Performance Considerations:**

- Cache expensive API calls (legal text retrieval, reference resolution)
- Implement rate limiting to avoid API quota issues
- Use async/await patterns for parallel processing where beneficial
- Monitor memory usage for large legislative texts

**Monitoring and Observability:**

- Add structured logging at INFO level for pipeline progress
- Log ERROR level for component failures with full context
- Include timing metrics for performance optimization
- Track success/failure rates for each pipeline stage

### Phase 6.4: Iterative Enhancement Plan

**Version 1.0 - MVP (Weeks 1-6):**

- Complete linear pipeline without recursion
- Basic error handling and logging
- Simple reference resolution (depth-1 only)

**Version 1.1 - Recursive Resolution (Weeks 7-8):**

- Full recursive resolution in ResolutionOrchestrator
- Cycle detection and infinite recursion prevention
- Enhanced relevance assessment for sub-references

**Version 1.2 - Performance Optimization (Weeks 9-10):**

- Parallel processing for independent references
- Advanced caching strategies
- Rate limiting and API optimization

**Version 1.3 - Quality Enhancement (Weeks 11-12):**

- Improved prompt engineering based on real-world testing
- Enhanced grammatical analysis in ReferenceObjectLinker
- Advanced text synthesis with readability optimization

This phased approach ensures a working pipeline quickly while allowing for iterative improvements based on real-world usage and testing feedback.
