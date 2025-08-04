# Legal Amendment Text Reconstructor: LLM-Based Architecture

## Problem Statement

French legal amendments contain complex, multi-operation instructions that current approaches fail to handle reliably:

1. **Format Mismatches**: Amendment text (markdown) vs original law text (pylegifrance API) have different formatting
2. **Complex Position Specifications**: "À la fin de la première phrase du premier alinéa du I" requires legal document understanding
3. **Multiple Operation Types**: 6 distinct operation types with varying complexity
4. **Compound Instructions**: Single amendments often contain multiple sequential operations

## Core Insight: Legal Amendments Are Structured Instructions

French legal amendments provide:

- **Exact text to modify**: `les mots : « specific text »`
- **Precise operation**: `sont remplacés par`, `sont supprimés`, `il est inséré`
- **Position specification**: `au 2°`, `à la fin de la première phrase`
- **Sequential operations**: Connected by `et`, `;`, or bullet points

**Key Challenge**: Format differences between sources make exact string matching unreliable.

## Implementation Plan: Clean Slate Approach

### Phase 0: Complete Cleanup of Legacy Implementation

Before implementing the new architecture, we must completely remove all legacy text reconstruction components to start from a clean slate.

#### Files to Remove:

```bash
# Legacy text reconstruction implementations
bill_parser_engine/core/reference_resolver/text_reconstructor.py
bill_parser_engine/core/reference_resolver/enhanced_text_reconstructor.py
bill_parser_engine/core/reference_resolver/amendment_parser.py
bill_parser_engine/core/reference_resolver/atomic_text_applier.py
bill_parser_engine/core/reference_resolver/operation_sequencer.py

# Legacy tests (if any)
tests/core/reference_resolver/test_text_reconstructor.py
tests/core/reference_resolver/test_enhanced_text_reconstructor.py
tests/core/reference_resolver/test_amendment_parser.py
tests/core/reference_resolver/test_atomic_text_applier.py
tests/core/reference_resolver/test_operation_sequencer.py
```

#### Code Updates Required:

**1. Update `pipeline.py`:**

```python
# Remove these imports:
from bill_parser_engine.core.reference_resolver.enhanced_text_reconstructor import EnhancedTextReconstructor

# Replace with:
from bill_parser_engine.core.reference_resolver.legal_amendment_reconstructor import LegalAmendmentReconstructor

# In __init__ method, replace:
self.text_reconstructor = EnhancedTextReconstructor(use_cache=use_cache)

# With:
self.text_reconstructor = LegalAmendmentReconstructor(api_key=None)  # Uses MISTRAL_API_KEY env var
```

**2. Update method calls in `pipeline.py`:**

```python
# Replace in step_4_reconstruct_texts():
reconstruction_output = self.text_reconstructor.reconstruct(original_text, chunk)

# With:
reconstruction_result = self.text_reconstructor.reconstruct_amendment(
    original_law_article=original_text,
    amendment_instruction=chunk.text,
    target_article_reference=f"{chunk.target_article.code}::{chunk.target_article.article}"
)

# Update result processing to match new ReconstructionResult format
```

**3. Update cache management in `pipeline.py`:**

```python
# Replace cache clearing logic to match new interface
elif component_name == "text_reconstructor" and hasattr(self.text_reconstructor, 'clear_all_caches'):
    self.text_reconstructor.clear_all_caches()
```

**4. Update `run_pipeline.py` (no changes needed):**
The pipeline script should work unchanged since it only calls the pipeline interface.

## Solution: 3-Step LLM-Based Architecture

### Step 1: InstructionDecomposer (LLM-based)

**Purpose**: Parse compound amendment instructions into atomic operations with type identification

**Input**: Raw amendment text
**Output**: List of atomic operations with operation type, target text, position hints

**Operation Types Identified**:

- **REPLACE**: `"les mots X sont remplacés par les mots Y"`
- **DELETE**: `"sont supprimés"`, `"est supprimé"`, `"(Supprimé)"`
- **INSERT**: `"après le mot X, il est inséré Y"`
- **ADD**: `"Il est ajouté un II ainsi rédigé"`
- **REWRITE**: `"est ainsi rédigée"`, `"est remplacée par"`
- **ABROGATE**: `"sont abrogés"`, `"est abrogé"`

**Example Decomposition**:

```
Input: "au 2°, les mots : « A » sont remplacés par les mots : « B » et, à la fin, les mots : « C » sont supprimés"

Output: [
  {
    "operation_type": "REPLACE",
    "position_hint": "au 2°",
    "target_text": "A",
    "replacement_text": "B",
    "sequence": 1
  },
  {
    "operation_type": "DELETE",
    "position_hint": "au 2°, à la fin",
    "target_text": "C",
    "sequence": 2
  }
]
```

### Step 2: OperationApplier (LLM-based)

**Purpose**: Apply each atomic operation using operation-specific LLM prompts

**Strategy**: Use specialized prompts for each operation type that handle:

- Format differences between amendment and original text
- Complex position specifications
- Legal document structure preservation
- Proper formatting and punctuation

**Core Algorithm for Each Operation Type**:

#### REPLACE Operations:

```python
def apply_replace_operation(original_text: str, operation: AtomicOperation) -> str:
    prompt = f"""
    Apply this replacement in the legal document:

    Original document: "{original_text}"
    Find text: "{operation.target_text}"
    Replace with: "{operation.replacement_text}"
    Position: "{operation.position_hint}"

    The target text may have different formatting (quotes, spaces, accents).
    Return the complete modified document with the replacement applied.
    Maintain proper legal document formatting.
    """
```

#### DELETE Operations:

```python
def apply_delete_operation(original_text: str, operation: AtomicOperation) -> str:
    prompt = f"""
    Delete text from this legal document:

    Original document: "{original_text}"
    Text to delete: "{operation.target_text}"
    Position: "{operation.position_hint}"

    Remove the specified text completely.
    Ensure proper punctuation and formatting after deletion.
    """
```

#### INSERT Operations:

```python
def apply_insert_operation(original_text: str, operation: AtomicOperation) -> str:
    prompt = f"""
    Insert text into this legal document:

    Original document: "{original_text}"
    Text to insert: "{operation.insertion_text}"
    Position: "{operation.position_hint}"

    Insert at the exact specified position.
    Maintain proper spacing and punctuation.
    """
```

#### ADD Operations:

```python
def apply_add_operation(original_text: str, operation: AtomicOperation) -> str:
    prompt = f"""
    Add new section to this legal document:

    Original document: "{original_text}"
    New content: "{operation.new_content}"
    Position: "{operation.position_hint}"

    Add the new section at the appropriate location.
    Follow legal document formatting conventions.
    """
```

### Step 3: ResultValidator (LLM-based)

**Purpose**: Verify the final result maintains legal coherence and formatting

**Validation Checks**:

- All operations were applied correctly
- Legal document structure preserved
- Proper punctuation and formatting
- No unintended modifications

## Real-World Examples from Legislative Bill

### Example 1: Simple Position-Specific Replacement

```
Amendment: "À la fin de la première phrase du premier alinéa du I, les mots : « auprès desquelles la redevance pour pollutions diffuses est exigible, mentionnées au IV de l'article L. 213-10-8 du code de l'environnement » sont remplacés par les mots : « exerçant les activités mentionnées au 1° du II de l'article L. 254-1 »"

Decomposition: [Single REPLACE operation]
- operation_type: "REPLACE"
- position_hint: "À la fin de la première phrase du premier alinéa du I"
- target_text: "auprès desquelles la redevance pour pollutions diffuses..."
- replacement_text: "exerçant les activités mentionnées au 1° du II..."

LLM Application: Finds target text despite formatting differences, applies replacement at correct position
```

### Example 2: Compound Operations with Multiple Types

```
Amendment: "– à la première phrase, après le mot : « prévoit », il est inséré le mot : « notamment » ; – la dernière phrase est ainsi rédigée : « Il précise les modalités... »"

Decomposition: [
  {
    "operation_type": "INSERT",
    "position_hint": "à la première phrase, après le mot : « prévoit »",
    "insertion_text": "notamment",
    "sequence": 1
  },
  {
    "operation_type": "REWRITE",
    "position_hint": "la dernière phrase",
    "replacement_text": "Il précise les modalités...",
    "sequence": 2
  }
]

Sequential Application:
1. INSERT operation applied first
2. REWRITE operation applied to modified text
3. Final validation ensures coherence
```

### Example 3: Complex Position with DELETE

```
Amendment: "Les deuxième et troisième alinéas du II sont supprimés"

Decomposition: [Single DELETE operation]
- operation_type: "DELETE"
- position_hint: "du II"
- target_text: "deuxième et troisième alinéas"

LLM Application: Identifies section II, finds second and third paragraphs, removes completely
```

## Error Handling & Recovery

### 1. Operation Parsing Failures

- If decomposition fails, fall back to single-operation processing
- Flag for manual review if operation type cannot be determined

### 2. Application Failures

- If operation fails, provide detailed error with suggestions
- Return partial results with clear indication of what succeeded/failed
- Flag for manual review with specific error context

### 3. Validation Failures

- If final result doesn't pass validation, flag all issues found
- Provide suggestions for correction
- Option to accept with warnings or reject for manual processing

### Phase 1: Implement New Clean Architecture

Once cleanup is complete, implement the 3-step LLM-based architecture:

## Clean Architecture Design

```python
@dataclass
class AmendmentOperation:
    """Single atomic amendment operation"""
    operation_type: OperationType  # REPLACE, DELETE, INSERT, ADD, REWRITE, ABROGATE
    target_text: Optional[str]     # Text to find/modify
    replacement_text: Optional[str] # New text (for REPLACE, INSERT, ADD)
    position_hint: str             # Legal position specification
    sequence_order: int            # Order in compound operations
    confidence_score: float        # Decomposition confidence

@dataclass
class ReconstructionResult:
    """Complete reconstruction result with detailed tracking"""
    success: bool
    final_text: str
    operations_applied: List[AmendmentOperation]
    operations_failed: List[Tuple[AmendmentOperation, str]]  # operation, error
    original_text_length: int
    final_text_length: int
    processing_time_ms: int
    validation_warnings: List[str]

class LegalAmendmentReconstructor:
    """Clean, purpose-built legal amendment processor"""

    def __init__(self, api_key: Optional[str] = None):
        self.decomposer = InstructionDecomposer(api_key)
        self.applier = OperationApplier(api_key)
        self.validator = ResultValidator(api_key)

    def reconstruct_amendment(
        self,
        original_law_article: str,
        amendment_instruction: str,
        target_article_reference: str
    ) -> ReconstructionResult:
        """
        Apply amendment instructions to original legal text.

        Args:
            original_law_article: Full text of the target legal article
            amendment_instruction: Raw amendment instruction text
            target_article_reference: Legal reference (e.g., "L. 254-1")

        Returns:
            Complete reconstruction result with success tracking
        """

        start_time = time.time()

        # Step 1: Decompose compound instructions
        operations = self.decomposer.parse_instruction(amendment_instruction)

        # Step 2: Apply operations sequentially
        current_text = original_law_article
        operations_applied = []
        operations_failed = []

        for operation in operations:
            try:
                result = self.applier.apply_single_operation(current_text, operation)
                if result.success:
                    current_text = result.modified_text
                    operations_applied.append(operation)
                else:
                    operations_failed.append((operation, result.error_message))
            except Exception as e:
                operations_failed.append((operation, str(e)))

        # Step 3: Validate final result
        validation = self.validator.validate_legal_coherence(
            original_text=original_law_article,
            modified_text=current_text,
            operations=operations_applied
        )

        processing_time = int((time.time() - start_time) * 1000)

                 return ReconstructionResult(
             success=len(operations_failed) == 0,
             final_text=current_text,
             operations_applied=operations_applied,
             operations_failed=operations_failed,
             original_text_length=len(original_law_article),
             final_text_length=len(current_text),
             processing_time_ms=processing_time,
             validation_warnings=validation.warnings
         )
```

### Phase 2: Integration and Pipeline Updates

**Update pipeline integration to use new interface:**

```python
# In pipeline.py step_4_reconstruct_texts() method:
reconstruction_result = self.text_reconstructor.reconstruct_amendment(
    original_law_article=original_text,
    amendment_instruction=chunk.text,
    target_article_reference=f"{chunk.target_article.code}::{chunk.target_article.article}"
)

# Convert ReconstructionResult to pipeline format:
if reconstruction_result.success:
    result_entry = {
        "chunk_id": chunk.chunk_id,
        "reconstruction_result": {
            "deleted_or_replaced_text": self._extract_deleted_text(reconstruction_result),
            "intermediate_after_state_text": reconstruction_result.final_text,
            "deleted_text_length": len(self._extract_deleted_text(reconstruction_result)),
            "after_state_length": len(reconstruction_result.final_text)
        },
        "operations_applied": len(reconstruction_result.operations_applied),
        "operations_failed": len(reconstruction_result.operations_failed),
        "processing_time_ms": reconstruction_result.processing_time_ms
    }
else:
    # Handle failed reconstruction with detailed error info
    result_entry = {
        "chunk_id": chunk.chunk_id,
        "reconstruction_result": None,
        "error": f"Failed operations: {len(reconstruction_result.operations_failed)}",
        "failed_operations": [
            {"operation": op.operation_type.value, "error": error}
            for op, error in reconstruction_result.operations_failed
        ]
    }
```

## Why This Clean Architecture Is Optimal

### 1. **Purpose-Built for Legal Amendments**

- **Clean data models**: `AmendmentOperation` and `ReconstructionResult` designed specifically for legal text
- **Legal-aware processing**: Each component understands French legal document structure
- **Comprehensive operation support**: Handles all 6 real operation types found in legislative amendments

### 2. **LLM-Powered Intelligence**

- **Format robustness**: Handles differences between amendment markdown and pylegifrance API text
- **Position understanding**: LLMs parse complex legal position specifications naturally
- **Contextual processing**: Maintains legal document formatting and hierarchy automatically

### 3. **Transparent Operation Tracking**

- **Detailed results**: Every operation tracked with success/failure status
- **Error isolation**: Failed operations don't abort entire process
- **Performance metrics**: Processing time and validation warnings included
- **Debuggability**: Clear audit trail of what was applied and what failed

### 4. **Robust Error Handling**

- **Graceful degradation**: Partial success with clear error reporting
- **Operation-level failures**: Individual operation failures isolated
- **Validation warnings**: Legal coherence issues flagged but not blocking
- **Exception safety**: Proper error handling at every level

### 5. **Clean Interface Design**

- **Single responsibility**: Each component has one clear job
- **Rich return types**: Detailed results instead of simple success/fail
- **No legacy baggage**: Purpose-built without backwards compatibility constraints
- **Extensible**: Easy to add new operation types or validation rules

This architecture leverages **LLM intelligence for complex legal understanding** while maintaining **deterministic processing** and **transparent error handling** - exactly what's needed for reliable legal text reconstruction.
