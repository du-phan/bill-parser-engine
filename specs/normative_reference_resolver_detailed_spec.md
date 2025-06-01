# Normative Reference Resolver: Detailed Specifications

## 1. Overview

The Normative Reference Resolver is a key component of the bill-parser-engine that processes legislative text, identifies normative references, resolves them recursively, and produces a fully interpretable, self-contained version of the text. The system leverages LLM agents for core processing tasks while maintaining a robust architecture for handling recursive references and ensuring performance.

## 1.1 French Legislative Bill Hierarchy Structure

French legislative bills follow a well-defined hierarchical structure, which is essential for both parsing and reference resolution. The main levels are:

1. **Document Header**: Contains metadata such as bill number, session, date, and introductory statements.
2. **TITRE (Title)**: Thematic divisions grouping related articles (e.g., "TITRE Iᴱᴿ").
3. **Article**: The primary logical unit (e.g., "Article 1ᵉʳ").
4. **Major Subdivision**: Roman numerals (I, II, III, etc.) for large sections within an article (may be absent).
5. **Numbered Point**: Arabic numerals with degree sign (1°, 2°, 3°, etc.) for atomic legal provisions within a subdivision or article. This is the atomic level for splitting.
6. **Lettered Subdivision**: Lowercase letters (a), b), c), etc.) for further breakdown within a numbered point.
7. **Indented/Hyphenated Text**: Additional detail, often using hyphens or indentation, for lists or clarifications.

**Reference Tracking and Context Preservation:**

- Each atomic unit (numbered point) must retain its full hierarchical path (TITRE > Article > Subdivision > Numbered Point) for context.
- Cross-references (e.g., "du même article", "au 3° du II") require a mapping between units and their positions in the hierarchy.
- When splitting, metadata about parent TITRE, Article, and all ancestor levels must be preserved for each chunk.

## 2. Core Components

### 2.0 BillSplitter

**Component**: `BillSplitter`

**Responsibility**: Deterministically split the legislative bill into atomic, manageable pieces for downstream LLM processing, following the legal document's hierarchy. The splitting logic is strictly rule-based (not LLM-powered) and ensures robust context and reference tracking.

**Splitting Rules (Hierarchical and Deterministic):**

1. **For each Article:**

   - Identify the `article_introductory_phrase`: the text immediately following the Article heading, before any major subdivision or numbered point. This may be empty if not present.
   - **If the Article contains direct child numbered points (1°, 2°, etc.):**
     - Each numbered point becomes a chunk.
     - Each chunk's context includes the `article_introductory_phrase`.
   - **If the Article contains direct child major subdivisions (Roman numerals: I, II, etc.):**
     - For each major subdivision:
       - Identify the `major_subdivision_introductory_phrase`: the text immediately following the major subdivision heading, before any numbered point. This may be empty if not present.
       - **If the major subdivision contains numbered points:**
         - Each numbered point within the major subdivision becomes a chunk.
         - Each chunk's context includes both the `article_introductory_phrase` and the `major_subdivision_introductory_phrase`.
       - **If the major subdivision contains no numbered points:**
         - The entire major subdivision becomes a single chunk.
         - Its context includes the `article_introductory_phrase` and the `major_subdivision_introductory_phrase`.
   - **If the Article contains neither direct child numbered points nor major subdivisions:**
     - The entire Article content becomes a single chunk.
     - Its context is the `article_introductory_phrase` (if any).

2. **Handling Numbered Point Ranges:**

   - If a range is specified (e.g., "1° à 3° (Supprimés)"), treat the entire range as a single chunk. The `numbered_point_label` should be the full range (e.g., "1° à 3°").
   - The chunk's text is the content following the range label (e.g., "(Supprimés)").

3. **Introductory Phrase Handling:**

   - Always capture both `article_introductory_phrase` and, if applicable, `major_subdivision_introductory_phrase` as separate fields in the metadata.
   - These phrases provide essential legal context (e.g., which code is being amended) and must be preserved for all child chunks.

4. **Parent Metadata for Each Chunk (Explicit Field Definitions):**

   - `titre_text`: The full TITRE (Title) heading under which the Article appears.
   - `article_label`: The Article number and heading (e.g., "Article 2").
   - `article_introductory_phrase`: The phrase immediately following the Article heading (may be empty).
   - `major_subdivision_label`: The Roman numeral and its heading/phrase, if present (e.g., "I.", "II (nouveau)."), otherwise null.
   - `major_subdivision_introductory_phrase`: The phrase immediately following the major subdivision heading (may be empty).
   - `numbered_point_label`: The label of the numbered point (e.g., "1°", "2°", "1° à 3°"), if present, otherwise null.
   - `hierarchy_path`: List of all parent headings/identifiers in order (e.g., [TITRE, Article, Major Subdivision, Numbered Point]).
   - `chunk_id`: Unique identifier for the chunk (e.g., concatenation of TITRE, Article, subdivision, and point numbers/labels).
   - `start_pos`, `end_pos`: Character positions in the original text.
   - `cross_references`: (Optional) List of detected references to other units for advanced tracking.

5. **Chunk Content:**

   - The chunk's `text` is the full content of the numbered point or major subdivision, including any lettered subpoints (a), b), etc.) and indented/hyphenated lists that belong to it.
   - **Do not** include parent headings or introductory phrases as a preamble in the chunk text. All context must be provided via metadata fields.

6. **Special Markings:**

   - Labels such as "(nouveau)", "(Supprimé)" must be included as part of the relevant label fields (e.g., `article_label`, `major_subdivision_label`, `numbered_point_label`) or as part of the chunk text, as they appear in the source.

7. **Extremely Long Chunks:**

   - If a chunk exceeds a practical token limit (e.g., >2000 tokens), consider further splitting at lettered subpoints (a), b), etc.), but only as a last resort and never across parent boundaries.

8. **Examples:**

   - **Article with direct numbered points:**
     - Article 1: `article_introductory_phrase` + 1°, 2°, 3°, ...
     - Each chunk = one numbered point, with parent metadata including the introductory phrase.
   - **Article with major subdivisions containing numbered points:**
     - Article 2: I. (`major_subdivision_introductory_phrase` + 1°, 2°, ...), II. (...)
     - Each chunk = one numbered point within a major subdivision, with full parent metadata.
   - **Article with only major subdivisions (no numbered points):**
     - Article 4: I., II., III.
     - Each chunk = one major subdivision, with parent metadata.
   - **Article with neither:**
     - Article X: Just a paragraph of text
     - Single chunk, with parent metadata.
   - **Numbered point range:**
     - "1° à 3° (Supprimés)" is a single chunk, with `numbered_point_label` = "1° à 3°" and text = "(Supprimés)".

9. **Implementation Notes:**

   - Use regular expressions or a state machine to detect TITREs, Articles, introductory phrases, major subdivisions (Roman numerals), and numbered points (1°, 2°, etc.).
   - Always associate each chunk with its full parent context for accurate reference resolution.
   - Never use an LLM for this splitting step; deterministic parsing is required for reliability and reproducibility.

10. **Integration:**
    - The BillSplitter is the first step in the pipeline, preceding reference detection.
    - Downstream components (ReferenceDetector, etc.) operate on these atomic chunks.

### 2.1 Target Article Identification

**Component**: `TargetArticleIdentifier`

**Implementation**: Stateless LLM Agent with prompt engineering

**Responsibility**: For each chunk produced by the BillSplitter, infer the primary legal article, section, or code provision that is the _target_ of the modification, insertion, or abrogation described in the chunk. This is the legal reference that the chunk is intended to create, update, or remove (the "target article"). This is distinct from references _within_ the chunk's text, which are handled by the ReferenceDetector.

**Inputs**:

- `chunk: BillChunk` — The chunk of legislative text to analyze (with all BillSplitter metadata)

**Outputs**:

- `TargetArticle` — Structured information about the target legal article/section, including:
  - `operation_type: TargetOperationType` (e.g., INSERT, MODIFY, ABROGATE, etc.)
  - `code: str` (e.g., "code rural et de la pêche maritime")
  - `article: str` (e.g., "L. 411-2-2")
  - `full_citation: str` (e.g., "article L. 411-2-2 du code de l'environnement")
  - `confidence: float`
  - `raw_text: str` (the exact phrase in the chunk that led to this inference, if any)
  - `version: str` (for future extensibility)

**Operation Types**:

- `INSERT`: The chunk creates a new article/section
- `MODIFY`: The chunk modifies an existing article/section
- `ABROGATE`: The chunk abrogates (removes) an article/section
- `RENUMBER`: The chunk renumbers an article/section
- `OTHER`: Any other operation (fallback)

**Key Features**:

- LLM-powered extraction of the _target_ legal article/section for each chunk
- Distinguishes between the target ("canvas") and embedded references ("children references")
- Handles cases where no explicit target is present (e.g., general provisions)
- Provides a confidence score for the inference

**Prompt Structure**:

```
You are a legal bill analysis agent. For the following chunk of legislative text, identify the main legal article, section, or code provision that is the *target* of the modification, insertion, or abrogation described. Return the operation type (INSERT, MODIFY, ABROGATE, etc.), the code, the article/section identifier, the full citation, and the exact phrase in the chunk that led to this inference (if any). If no explicit target is present, return nulls and set operation_type to OTHER.

Chunk:
{chunk.text}

Metadata:
{chunk metadata fields}
```

**Example**:

For the chunk:

```
7° (nouveau) Après l'article L. 411-2-1, il est inséré un article L. 411-2-2 ainsi rédigé :

	« Art. L. 411-2-2. – Sont présumés répondre à une raison impérative d'intérêt public majeur, au sens du c du 4° du I de l'article L. 411-2, ... »
```

- TargetArticle output:
  - operation_type: INSERT
  - code: "code de l'environnement"
  - article: "L. 411-2-2"
  - full_citation: "article L. 411-2-2 du code de l'environnement"
  - confidence: 0.98
  - raw_text: "il est inséré un article L. 411-2-2"

For a chunk with no explicit target (e.g., general policy statement):

- operation_type: OTHER
- code: null
- article: null
- full_citation: null
- confidence: 0.7
- raw_text: null

**Integration**:

- The TargetArticleIdentifier is called immediately after BillSplitter, before ReferenceDetector.
- The output is attached to each BillChunk as a new field: `target_article: Optional[TargetArticle]`

**Special Case: Chunks With No Explicit Legal Reference (General Provisions)**

- Some chunks do not create, modify, or abrogate a specific article, section, or code provision. These are typically general provisions, policy statements, mandates, or reporting requirements (e.g., "L'État met en place un plan pluriannuel ...").
- In such cases, the TargetArticleIdentifier should output:
  - `operation_type: OTHER`
  - `code: None`
  - `article: None`
  - `full_citation: None`
  - `confidence: < 1.0` (reflecting the absence of an explicit target)
  - `raw_text: None`
- These chunks are still legally binding and must be included in the output, but are not mapped to a code article. Downstream components should process them as standalone provisions.

**Summary Table:**

| Chunk Type               | TargetArticleIdentifier Output | Downstream Handling                 |
| ------------------------ | ------------------------------ | ----------------------------------- |
| Code amendment/insertion | code/article/citation present  | Map to code, update/insert/abrogate |
| General provision        | None, operation_type=OTHER     | Include as standalone, no mapping   |

**Example (General Provision):**

For a chunk like:

```
III (nouveau). – L'État met en place un plan pluriannuel de renforcement de l'offre d'assurance récolte destinée aux prairies. ...
```

- TargetArticle output:
  - operation_type: OTHER
  - code: None
  - article: None
  - full_citation: None
  - confidence: 0.7
  - raw_text: None

### 2.1 Reference Detection

**Component**: `ReferenceDetector`

**Implementation**: Stateless LLM Agent with specialized prompt engineering

**Responsibility**: Parse legislative text and identify all normative references using LLM-based understanding. Only return references that are essential for understanding the legislative text—specifically, those that define, constrain, or clarify a key noun/concept (object) within the chunk. The goal is to help users understand changes by specifying how notions in the text are defined or affected by other legal provisions.

**Inputs**:

- `text: str` — The legislative text to analyze

**Outputs**:

- `List[Reference]` — List of detected references, each with:
  - `text: str` (reference text)
  - `start_pos: int`, `end_pos: int` (positions in input)
  - `object: str` (the noun/concept in the chunk that the reference helps define, constrain, or clarify)
  - `confidence: float`
  - `reference_type: ReferenceType`
  - Additional metadata

**Reference Detection Purpose and Object Field**:

- The primary goal is to identify references that are essential for understanding the legislative text, especially those that define, constrain, or clarify key nouns/concepts (objects) within the current chunk.
- The **object** is the specific noun or noun phrase _within the current chunk's text_ that the reference directly defines, constrains, or clarifies.
- For references to annexes, directives, or regulations, the object is typically the entity, process, or item being regulated or described by that external text (e.g., "les installations d'élevage" are defined/constrained by the EU directive).
- Prefer the most specific phrase. For example, if a reference clarifies "produits phytopharmaceutiques", and the text says "l'utilisation de produits phytopharmaceutiques", the object is "produits phytopharmaceutiques".
- If the object is ambiguous, return the most plausible candidate.

**Example of 'object' linked to a definitional reference:**

- Text: "...mentionnées à l'annexe I bis de la directive 2010/75/UE du Parlement européen et du Conseil..."
- Reference: "l'annexe I bis de la directive 2010/75/UE du Parlement européen et du Conseil"
- Object: "les installations d'élevage" (because the directive defines which installations are covered)

Another example:

- Text: "...respecte les principes généraux de la lutte intégrée contre les ennemis des cultures mentionnée à l'article L. 253‑6."
- Reference: "l'article L. 253‑6"
- Object: "principes généraux de la lutte intégrée contre les ennemis des cultures"
  (Because L. 253-6 _defines_ or _details_ these principles)

**Ambiguous/Overlapping References:**

- If references overlap or are nested, each should be returned as a separate entry, with a `parent_reference` field if applicable.
- References with confidence below the threshold (default: 0.6) must be returned in a separate `low_confidence_references` field for manual review.

**Reference Types** (v0 Scope):

1. **Explicit References**:
   - Direct citations with clear identifiers (e.g., "l'article L. 254-1")
   - Specific section references (e.g., "au 3° du II")
   - Complete citations with source (e.g., "règlement (CE) n° 1107/2009")
2. **Simple Implicit References**:
   - Contextual references (e.g., "du même article" referring to previously mentioned article)
   - Relative references (e.g., "l'article précédent")
   - Abbreviated references (e.g., "ledit article" or "ce même article")

**Key Features**:

- LLM-powered detection of explicit and implicit references
- Context-aware understanding of legal language
- Handling of abbreviated and implicit references
- Detection of embedded references within text
- Confidence scoring for each detected reference
- Only return references that are essential for understanding or defining a concept in the chunk

**Prompt Structure**:

```
You are a specialized legal reference detection agent for French legislative texts. Your task is to analyze a given legislative chunk and extract ONLY the embedded normative references—any mention (explicit or implicit) of another legal text, article, code, regulation, decree, or section that is referenced within the text.

The primary goal is to identify references that are essential for understanding the legislative text, especially those that define, constrain, or clarify key nouns/concepts (objects) within the current chunk. Only return references that are directly linked to an "object" in the text—i.e., references that help explain, define, or limit the meaning or scope of a specific noun or concept in the chunk.

For each reference, include:
- text: The exact reference string as it appears in the input.
- start_pos: The starting character index of the reference in the input text.
- end_pos: The ending character index (exclusive) of the reference in the input text.
- object: The noun/concept in the chunk that the reference helps define, constrain, or clarify.
- confidence: A float between 0.0 and 1.0 indicating your confidence in the detection.
- reference_type: One of: explicit_direct, explicit_section, explicit_complete, implicit_contextual, implicit_relative, implicit_abbreviated.
- If the reference is nested or overlaps with another, include a parent_reference field (the text of the parent reference).
- If your confidence is below 0.6, include the reference in a separate low_confidence_references list.

Example:
Text: "...mentionnées à l'annexe I bis de la directive 2010/75/UE du Parlement européen et du Conseil..."
Output:
{
  "references": [
    {
      "text": "l'annexe I bis de la directive 2010/75/UE du Parlement européen et du Conseil",
      "start_pos": 204,
      "end_pos": 264,
      "object": "les installations d'élevage",
      ...
    }
  ],
  "low_confidence_references": []
}
```

**Note:** The 'object' is the specific entity or concept in the initial text that the reference points to or helps define. This field provides essential context for downstream components, especially for relevance determination in recursive resolution.

### 2.2 Reference Classification

**Component**: `ReferenceClassifier`

**Implementation**: Stateless LLM Agent with domain-specific knowledge

**Responsibility**: Categorize identified references by source and type using LLM understanding.

**Inputs**:

- `reference: Reference` — The detected reference object
- `surrounding_text: str` — Context window from the original input (extracted by orchestration layer)

**Outputs**:

- `Reference` — The same reference object, enriched with:

  - `source: ReferenceSource`
  - `reference_type: ReferenceType`
  - `components: Dict[str, str]` (parsed components)
  - `confidence: float` (classification confidence)
  - Additional metadata

- The `components` parameter is a dictionary that breaks down the reference into its meaningful subparts (e.g., code, article, section, paragraph). This structured representation enables precise retrieval, disambiguation, and traceability.
- **Purpose:**
  - Disambiguation: Clarifies exactly which part of the law is referenced.
  - Retrieval: Allows the TextRetriever agent to construct precise API queries.
  - Traceability: Facilitates mapping between the original text and the resolved content.
- **Examples:**
  - For `"l'article L. 254-1 du code rural et de la pêche maritime"`:
    ```python
    {
        "code": "code rural et de la pêche maritime",
        "article": "L. 254-1"
    }
    ```
  - For `"au 3° du II de l'article L. 254-1 du code rural"`:
    ```python
    {
        "code": "code rural",
        "article": "L. 254-1",
        "section": "II",
        "paragraph": "3°"
    }
    ```

**Key Features**:

- LLM-powered classification of references
- Understanding of legal document hierarchies
- Handling of ambiguous cases
- Source verification and validation

**Prompt Structure**:

```
You are a specialized legal reference classification agent for French legislative texts. For each reference:
1. Identify the source (French code, EU regulation, etc.)
2. Determine the reference type (article, section, paragraph, etc.)
3. Extract specific components (article numbers, sections, etc.) with their hierarchical structure
4. Provide classification confidence (0.0-1.0)
5. If applicable, identify the version/date of the referenced text

Reference: {reference_text}
Context: {surrounding_text}
```

**Inputs**:

- The reference text
- The 'surrounding_text' (context window from the original input)

**Note:** The 'surrounding_text' is extracted by the orchestration layer (or a dedicated utility function) using the start and end positions provided by the ReferenceDetector. This ensures the ReferenceClassifier receives both the reference and its relevant context for accurate classification.

### 2.3 Text Retrieval

**Component**: `TextRetriever`

**Implementation**: Hybrid approach combining direct API integration (using pylegifrance) with web search fallback

**Responsibility**: Fetch and process the full text of referenced items from authoritative sources, then extract the specific relevant portion pertaining to the referenced object (not the entire source document).

**Inputs**:

- `reference: Reference` — The classified reference object (with source/type/components)

**Outputs**:

- `str` — The extracted relevant text content (specifically about the referenced object)
- `str` — The broader context (for reference and validation)
- `Dict` — Metadata including:
  - Retrieval method (API, web search, cache)
  - Validation results
  - Source URL or API endpoint
  - Cache status
  - Error information (if applicable)
  - Content scope (article, section, paragraph, etc.)

**Legifrance API Integration (pylegifrance):**

- Use the `pylegifrance` package for direct API access. Example usage:

  ```python
  import os
  from pylegifrance import recherche_code
  from pylegifrance.models.constants import CodeNom

  # Set environment variables for authentication
  os.environ["LEGIFRANCE_CLIENT_ID"] = LEGIFRANCE_CLIENT_ID
  os.environ["LEGIFRANCE_CLIENT_SECRET"] = LEGIFRANCE_CLIENT_SECRET

  # Use the correct code_name and search value, CREDLPM = "Code rural et de la pêche maritime"
  res = recherche_code(code_name=CodeNom.CREDLPM, search="L254‑6‑2")
  ```

- The `code_name` must be mapped from the reference's `components["code"]` using the `CodeNom` enum.
- The `search` parameter should be the article or section identifier as it appears in the text (try multiple formats if needed).
- If the API call fails or returns no result, fallback to web search using the reference text and object as the query.

**Cache Key Construction:**

- Cache keys must include reference, code, article, and (if available) version/date and format (e.g., `"CREDLPM@L254-6-2@2024-06-01@hyphen"`).

**Error Handling:**

- If the API call fails (network, auth, not found), log the error and fallback to web search.
- If both API and web search fail, return a structured error object with the reference and error details.
- All errors must be logged with a unique correlation ID.

**Web Search Fallback:**

- Construct queries using all available components and the 'object' field.
- Validate results by checking for the presence of expected legal terms and structure.

**Performance:**

- API calls should have a timeout (default: 10s).
- Use in-memory cache for frequent references.

### 2.4 Reference Resolution

**Component**: `ReferenceResolver`

**Responsibility**: Extract the precise subpart of text referred to, determine which nested references need resolution, and handle recursive resolution based on relevance to the original reference.

**Inputs**:

- `reference: Reference` — The classified reference object
- `text_content: str` — The retrieved text content for the reference
- `max_depth: int` — Maximum recursion depth
- `resolution_path: List[Reference]` — (For tracking recursion/circularity)

**Outputs**:

- `ResolvedReference` —
  - `reference: Reference`
  - `content: str` (resolved content)
  - `sub_references: List[ResolvedReference]` (nested references that were resolved)
  - `unresolved_sub_references: List[Reference]` (nested references deemed not necessary)
  - `resolution_path: List[Reference]`
  - `resolution_status: ResolutionStatus`
  - `relevance_metadata: Dict[str, Any]`

**Relevance Determination Algorithm:**

- Use a decision tree:
  1. Is the nested reference directly required to define or constrain the 'object'? (Yes → essential)
  2. Is it only supplementary or tangential? (Yes → non-essential)
  3. Is it ambiguous? (Flag for manual review)
- Add `resolution_warnings: List[str]` to `ResolvedReference` for circularity, max depth, or partial failures.

**Example:**
Original text:
"Le conseil mentionné au 3° du II de l'article L. 254‑1 couvre toute recommandation d'utilisation de produits phytopharmaceutiques. Il est formalisé par écrit. La prestation est effectuée à titre onéreux. Il s'inscrit dans un objectif de réduction de l'usage et des impacts des produits phytopharmaceutiques et respecte les principes généraux de la lutte intégrée contre les ennemis des cultures mentionnée à l'article L. 253‑6."

Detected references:

- "3° du II de l'article L. 254‑1" (defines the scope of the council)
- "article L. 253‑6" (provides principles for integrated pest management)

Relevance determination:

- **Essential:**
  - "3° du II de l'article L. 254‑1" — Essential, as it defines the main object (the council's scope).
  - "article L. 253‑6" — Essential, as it provides the legal principles that the council must respect (directly constrains the object).
- **Non-Essential:**
  - If the text referenced other articles about, for example, general administrative procedures or unrelated background, these would be considered non-essential and could be skipped in the recursive resolution.

Explanation:

- Both references are essential because they are directly required to understand the legal requirements and scope of the council described in the original text. If a reference only provided supplementary context (e.g., a general definition not directly constraining the object), it would be marked as non-essential.

### 2.5 Text Substitution

**Component**: `TextSubstitutor`

**Implementation**: Stateless LLM Agent with legal text simplification expertise

**Responsibility**: Replace references with their resolved content and slightly reformulate that part of the text if needed to be clear and understandable while maintaining absolute legal accuracy.

**Inputs**:

- `original_text: str` — The input legislative text
- `resolved_references: List[ResolvedReference]` — All resolved references and their content

**Outputs**:

- `FlattenedText` —

  - `original_text: str`
  - `flattened_text: str` (with all references resolved/substituted)
  - `reference_map: Dict[Reference, ResolvedReference]`
  - `unresolved_references: List[Reference]`
  - `confidence_score: float`
  - `processing_metadata: Dict[str, Any]`
  - `validation_status: str` ("validated", "fallback", "manual_review")

- If the LLM's confidence in the rewritten text is below 0.8, fall back to the original reference and flag for manual review.

### 2.6 Versioning Management

**Note:** Versioning management is out of scope for v0. All references are resolved against the latest available version. This section is reserved for future extension.

## 3. Data Models

**Note:** Data models and enums are aligned with the codebase. All models include a `version: str` field for future extensibility.

```python
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Any

class ReferenceType(Enum):
    EXPLICIT_DIRECT = "explicit_direct"
    EXPLICIT_SECTION = "explicit_section"
    EXPLICIT_COMPLETE = "explicit_complete"
    IMPLICIT_CONTEXTUAL = "implicit_contextual"
    IMPLICIT_RELATIVE = "implicit_relative"
    IMPLICIT_ABBREVIATED = "implicit_abbreviated"

class ReferenceSource(Enum):
    CODE_RURAL = "code_rural"
    CODE_ENVIRONNEMENT = "code_environnement"
    EU_REGULATION = "eu_regulation"
    DECREE = "decree"
    ARRETE = "arrete"
    LAW = "law"
    OTHER = "other"

@dataclass
class Reference:
    text: str
    start_pos: int
    end_pos: int
    object: str
    reference_type: ReferenceType
    source: ReferenceSource
    components: Dict[str, str]
    confidence: float
    parent_reference: Optional[str] = None
    version: str = "v0"

@dataclass
class ResolvedReference:
    reference: Reference
    content: str
    sub_references: List["ResolvedReference"]
    unresolved_sub_references: List[Reference]
    resolution_path: List[Reference]
    resolution_status: str
    relevance_metadata: Dict[str, Any]
    resolution_warnings: List[str] = None
    version: str = "v0"

@dataclass
class FlattenedText:
    original_text: str
    flattened_text: str
    reference_map: Dict[Reference, ResolvedReference]
    unresolved_references: List[Reference]
    confidence_score: float
    processing_metadata: Dict[str, Any]
    validation_status: str
    version: str = "v0"

class TargetOperationType(Enum):
    INSERT = "insert"
    MODIFY = "modify"
    ABROGATE = "abrogate"
    RENUMBER = "renumber"
    OTHER = "other"

@dataclass
class TargetArticle:
    operation_type: TargetOperationType
    code: Optional[str]
    article: Optional[str]
    full_citation: Optional[str]
    confidence: float  # < 1.0 if no explicit target
    raw_text: Optional[str]
    version: str = "v0"

@dataclass
class BillChunk:
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
    cross_references: Optional[List[str]] = None
    target_article: Optional[TargetArticle] = None
    version: str = "v0"
```

## 4. Processing Pipeline

1. **Input**: Legislative text paragraph
2. **Bill Splitting**: Deterministic splitting into atomic chunks (BillSplitter)
3. **Target Article Identification**: For each chunk, infer the main legal article/section being created, modified, or abrogated (TargetArticleIdentifier)
4. **Reference Detection**: Stateless LLM agent identifies all references _within_ the chunk text (ReferenceDetector)
5. **Context Extraction**: The orchestration layer extracts 'surrounding_text' for each reference using start/end positions
6. **Reference Classification**: Stateless LLM agent categorizes each reference, using both the reference and its context
7. **For each reference**:
   a. Text Retrieval: Fetch and extract the specific relevant portion from the source (using pylegifrance, fallback to web search)
   b. Relevance Analysis: Use the 'object' field to identify which nested references are essential for understanding
   c. Recursive Resolution: Only resolve nested references that are necessary for understanding the original reference/object
   d. If essential nested references exist, recursively resolve them
8. **Text Substitution**: Replace references with their resolved content
9. **Output**: Flattened text and reference metadata

## 5. LLM Agent Management

- All agents are stateless for v0 (automated, non-interactive usage, no persistent conversation required).
- Use the latest Mistral Python SDK (>=1.8.1, Python 3.10+ required for agent features).
- Example agent usage:

  ```python
  from mistralai import Mistral
  import os

  with Mistral(api_key=os.getenv("MISTRAL_API_KEY", "")) as mistral:
      res = mistral.agents.complete(
          messages=[{"role": "user", "content": "Detect all references in the following text: ..."}],
          agent_id="<agent_id>"
      )
  ```

- For each step (detection, classification, substitution), call the agent in a stateless, automated, non-interactive manner.
- No persistent conversation or agent memory is required for the current use case.

## 6. Error Handling

- Log ambiguous references and errors with a unique correlation ID.
- Provide confidence scores for uncertain matches.
- Set minimum confidence threshold (default: 0.6).
- Fallback to web search if API retrieval fails.
- Return structured error objects for unresolved references.

## 7. Performance, Security, and Resource Management

- **Resource Management:**
  - Set timeouts for all external API calls (default: 10s).
  - Monitor memory and CPU usage for large documents.
- **Security:**
  - Store all API keys (Mistral, Legifrance) securely using environment variables or a secrets manager.
  - Validate all input data to prevent injection or malformed requests.
  - Rate limit external API calls to avoid abuse.
  - Ensure compliance with GDPR and French data privacy laws for legal texts.
