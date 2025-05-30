# Normative Reference Resolver: Detailed Specifications

## 1. Overview

The Normative Reference Resolver is a key component of the bill-parser-engine that processes legislative text, identifies normative references, resolves them recursively, and produces a fully interpretable, self-contained version of the text. The system leverages LLM agents for core processing tasks while maintaining a robust architecture for handling recursive references and ensuring performance.

## 2. Core Components

### 2.1 Reference Detection

**Component**: `ReferenceDetector`

**Implementation**: LLM Agent with specialized prompt engineering

**Responsibility**: Parse legislative text and identify all normative references using LLM-based understanding.

**Reference Types** (v0 Scope):

1. **Explicit References**:

   - Direct citations with clear identifiers (e.g., "l'article L. 254-1")
   - Specific section references (e.g., "au 3° du II")
   - Complete citations with source (e.g., "règlement (CE) n° 1107/2009")

2. **Simple Implicit References**:
   - Contextual references (e.g., "du même article" referring to previously mentioned article)
   - Relative references (e.g., "l'article précédent")
   - Abbreviated references (e.g., "ledit article" or "ce même article")

**Future Scope** (post v0):

- References to concepts defined elsewhere (e.g., "les produits phytopharmaceutiques" without direct citation)
- Historical reference tracking
- Cross-code concept mapping
- Definition evolution tracking

**Rationale for v0 Scope**:

1. **Reliability**: Focus on references that can be reliably detected and resolved
2. **Performance**: Optimize for common reference patterns
3. **Validation**: Ensure accurate resolution before expanding scope
4. **User Value**: Deliver core functionality first

**Key Features**:

- LLM-powered detection of explicit and implicit references
- Context-aware understanding of legal language
- Handling of abbreviated and implicit references
- Detection of embedded references within text
- Confidence scoring for each detected reference

**Prompt Structure**:

```
You are a specialized legal reference detection agent. Your task is to identify all normative references in the following legislative text. For each reference:
1. Extract the exact text of the reference
2. Note its position in the text
3. Provide a confidence score
4. Indicate if it's an implicit or explicit reference

Text: {input_text}
```

**Outputs**: List of detected references with their locations, confidence scores, and metadata

### 2.2 Reference Classification

**Component**: `ReferenceClassifier`

**Implementation**: LLM Agent with domain-specific knowledge

**Responsibility**: Categorize identified references by source and type using LLM understanding.

**Key Features**:

- LLM-powered classification of references
- Understanding of legal document hierarchies
- Handling of ambiguous cases
- Source verification and validation

**Prompt Structure**:

```
You are a specialized legal reference classification agent. For each reference:
1. Identify the source (French code, EU regulation, etc.)
2. Determine the reference type
3. Extract any specific components (article numbers, sections, etc.)
4. Provide classification confidence

Reference: {reference_text}
Context: {surrounding_text}
```

**Outputs**: Classified reference objects with metadata about source and type

### 2.3 Text Retrieval

**Component**: `TextRetriever`

**Implementation**: Hybrid approach combining direct API integration with LLM web search capabilities

**Responsibility**: Fetch and process the full text of referenced items from authoritative sources using a multi-layered approach.

**Architecture**:

1. **Primary Method**: Direct API Integration

   - Legifrance API as primary source
   - Structured error handling and validation
   - Caching for frequently accessed texts
   - Version control and tracking

2. **Fallback Method**: LLM Web Search
   - Used when API fails or returns invalid results
   - Handles ambiguous or complex references
   - Provides cross-referencing and validation
   - Flexible reference format handling

**Implementation Details**:

```python
class TextRetriever:
    def __init__(self):
        self.api_client = LegifranceAPIClient()
        self.cache = TextCache()
        self.llm_client = LLMClient()

    def retrieve_text(self, reference: Reference) -> str:
        try:
            # Try API first
            text = self._retrieve_from_api(reference)
            if self._validate_text(text, reference):
                self.cache.store(reference, text)
                return text
        except APIError:
            # Fallback to web search
            text = self._retrieve_from_web_search(reference)
            if self._validate_text(text, reference):
                self.cache.store(reference, text)
                return text
            else:
                raise TextRetrievalError("Could not retrieve valid text")

    def _retrieve_from_api(self, reference: Reference) -> str:
        # API-specific implementation
        pass

    def _retrieve_from_web_search(self, reference: Reference) -> str:
        # Web search implementation using LLM capabilities
        pass

    def _validate_text(self, text: str, reference: Reference) -> bool:
        # Validation logic
        pass
```

**Key Features**:

- Multi-layered retrieval strategy
- Robust error handling
- Intelligent caching
- Version management
- Cross-reference validation
- Context-aware processing

**Error Handling**:

1. **API Errors**:

   - Authentication failures
   - Rate limiting
   - Network issues
   - Invalid responses

2. **Web Search Errors**:

   - No results found
   - Ambiguous results
   - Outdated information
   - Invalid references

3. **Validation Errors**:
   - Text mismatch
   - Version mismatch
   - Format issues
   - Context inconsistencies

**Caching Strategy**:

- In-memory cache for frequent references
- Disk-based cache for persistence
- Cache invalidation based on:
  - Time-based expiration
  - Version changes
  - Reference updates
  - Manual invalidation

**Outputs**:

- Structured text content
- Metadata about retrieval method used
- Validation results
- Cache status
- Error information if applicable

**Performance Considerations**:

- API calls are prioritized for speed
- Web search is used only when necessary
- Caching reduces redundant retrievals
- Parallel processing for multiple references
- Batch processing for similar references

### 2.4 Reference Resolution

**Component**: `ReferenceResolver`

**Responsibility**: Extract the precise subpart of text referred to and handle recursive resolution.

**Key Features**:

- Orchestration of LLM agents for detection and classification
- Recursive resolution management
- Circular reference detection
- Versioning awareness

**Resolution Flow**:

1. Call ReferenceDetector LLM agent
2. For each detected reference:
   a. Call ReferenceClassifier LLM agent
   b. Retrieve text via TextRetriever
   c. If nested references exist:
   - Recursively call resolution process
   - Track resolution path
   - Handle circular references

**Outputs**: Resolved text content with all nested references processed

### 2.5 Text Substitution

**Component**: `TextSubstitutor`

**Implementation**: LLM Agent with legal text simplification expertise

**Responsibility**: Replace references with their resolved content and rewrite the text to be clear, concise, and easily understandable while maintaining legal accuracy.

**Key Features**:

- LLM-powered text rewriting for clarity
- Maintains legal accuracy while improving readability
- Handles complex nested references elegantly
- Preserves important legal terminology
- Generates natural, flowing text

**Prompt Structure**:

```
You are a specialized legal text simplification agent. Your task is to rewrite the following text, which contains resolved references, to make it clear and concise while maintaining legal accuracy.

Guidelines:
1. Replace technical references with their clear meanings
2. Maintain all legal requirements and conditions
3. Use plain language where possible
4. Keep important legal terms that cannot be simplified
5. Ensure the text flows naturally
6. Preserve the original intent and scope

Original text: {original_text}
Resolved references: {resolved_references}

Please provide:
1. The rewritten, clear text
2. A list of any terms that were kept in their original form (with explanation)
3. A confidence score for the simplification
```

**Example Transformation**:

```
Input: "Les produits définis à l'article L. 253-5 du code rural et de la pêche maritime, à l'exception de ceux mentionnés au 3° du II de l'article L. 254-1"

Output: "Les produits autorisés en agriculture, à l'exception de ceux nécessitant des procédures particulières en raison de leur impact potentiel sur l'environnement"
```

**Outputs**:

- Clear, rewritten text
- Metadata about kept terms and simplification decisions
- Confidence score for the transformation

## 3. Data Models

### 3.1 Reference

```python
@dataclass
class Reference:
    text: str  # Original reference text
    start_pos: int  # Start position in original text
    end_pos: int  # End position in original text
    reference_type: ReferenceType  # Enum of reference types
    source: ReferenceSource  # Source classification
    components: Dict[str, str]  # Parsed components (e.g., article, paragraph)
```

### 3.2 ResolvedReference

```python
@dataclass
class ResolvedReference:
    reference: Reference  # Original reference
    content: str  # Resolved content
    sub_references: List["ResolvedReference"]  # Nested references
    resolution_path: List[Reference]  # Path of resolution for traceability
    resolution_status: ResolutionStatus  # Success, partial, failed
```

### 3.3 FlattendText

```python
@dataclass
class FlattenedText:
    original_text: str  # Input text
    flattened_text: str  # Output text with all references resolved
    reference_map: Dict[Reference, ResolvedReference]  # Mapping for traceability
    unresolved_references: List[Reference]  # References that couldn't be resolved
```

## 4. Processing Pipeline

1. **Input**: Legislative text paragraph
2. **Reference Detection**: LLM agent identifies all references
3. **Reference Classification**: LLM agent categorizes each reference
4. **For each reference**:
   a. Text Retrieval: Fetch the referenced content
   b. Extract the specific subpart referenced
   c. Reference Resolution: Check for nested references
   d. If nested references exist, recursively resolve them
5. **Text Substitution**: Replace references with resolved content
6. **Output**: Flattened text and reference metadata

## 5. LLM Agent Management

### 5.1 Agent Configuration

- Model selection based on task requirements
- Temperature settings for each agent
- Token limits and cost optimization
- Fallback strategies for API failures

### 5.2 Prompt Management

- Version control for prompts
- A/B testing of prompt variations
- Performance monitoring
- Continuous improvement based on results

### 5.3 Cost Optimization

- Batch processing of references
- Caching of common reference resolutions
- Token usage optimization
- Fallback to simpler models when appropriate

## 6. Error Handling

### 6.1 Reference Detection Errors

- Log ambiguous references for manual review
- Provide confidence scores for uncertain matches

### 6.2 Text Retrieval Errors

- Graceful degradation when API sources are unavailable
- Fallback to local cache when possible
- Clear error messages for unresolvable references

### 6.3 Resolution Errors

- Partial resolution when some nested references cannot be resolved
- Detailed logging of resolution failures

## 7. Performance Considerations

### 7.1 Parallel Processing

- Process independent references in parallel where possible
- Implement worker pool for API requests

### 7.2 Caching

- Multi-level cache (in-memory, disk-based)
- Preemptive caching for commonly referenced texts

### 7.3 Batch Processing

- Group similar references to reduce API calls
- Optimize text retrieval for bulk operations

## 8. API Interface

### 8.1 Main Function

```python
def resolve_references(
    text: str,
    max_depth: int = 5,
    include_metadata: bool = True
) -> Union[str, Tuple[str, Dict]]:
    """
    Resolve all normative references in the provided text.

    Args:
        text: The legislative text to process
        max_depth: Maximum recursion depth for nested references
        include_metadata: Whether to return reference metadata

    Returns:
        If include_metadata is False, returns the flattened text.
        Otherwise, returns a tuple of (flattened_text, metadata).
    """
```

### 8.2 Utility Functions

```python
def extract_references(text: str) -> List[Reference]:
    """Extract all references from text without resolving them."""

def get_reference_tree(text: str, reference: str) -> ResolvedReference:
    """Get the full resolution tree for a specific reference."""

def resolve_single_reference(reference: str) -> str:
    """Resolve a single reference string to its content."""
```

## 9. Implementation Approach

1. Start with reference detection patterns for most common cases
2. Implement basic text retrieval from Legifrance API
3. Build simple reference resolution for non-nested cases
4. Add recursive resolution with proper safeguards
5. Enhance detection patterns to cover edge cases
6. Optimize performance with caching and parallel processing
7. Implement comprehensive error handling and logging

## 10. Test Approach

1. Unit tests for pattern matching with various reference formats
2. Integration tests with mock API responses
3. Regression tests using known legislative examples
4. Performance benchmarks for large texts
5. Edge case tests for circular references and maximum depth scenarios

## 11. LLM-Specific Considerations

### 11.1 Performance Optimization

- Parallel processing of independent references
- Batch processing of similar references
- Caching of common reference patterns
- Token usage optimization

### 11.2 Error Handling

- LLM API failure handling
- Fallback to simpler detection methods
- Retry strategies with exponential backoff
- Graceful degradation

### 11.3 Monitoring and Metrics

- Token usage tracking
- Response time monitoring
- Accuracy metrics
- Cost tracking
- Error rate monitoring

### 11.4 Continuous Improvement

- Prompt engineering optimization
- Model performance tracking
- A/B testing of different approaches
- Regular review of edge cases
