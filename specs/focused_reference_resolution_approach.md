# Specification: Focused Reference Resolution

## 1. Executive Summary

This document outlines a revised, hybrid approach for the reference resolution pipeline. The goal is to maximize **processing efficiency** by scanning only changed text fragments ("the delta") while ensuring **linking accuracy** and **interpretability** by using full contextual documents where necessary. This change is projected to improve reference location performance by over 30x.

## 2. Component Data Contracts

### **TextReconstructor (Updated)**

**Input**:

```python
{
    "original_law_article": str,  # Full original article text
    "amendment_chunk": BillChunk  # Amendment instruction chunk
}
```

**Output**:

```python
{
    "deleted_or_replaced_text": str,      # Exact text that was removed/replaced
    "newly_inserted_text": str,           # Exact text that was added (empty string for pure deletions)
    "intermediate_after_state_text": str  # Full article text after amendment (for context)
}
```

### **ReferenceLocator (Updated)**

**Input**:

```python
{
    "deleted_or_replaced_text": str,  # From TextReconstructor
    "newly_inserted_text": str       # From TextReconstructor
}
```

**Output**:

```python
[
    {
        "reference_text": str,        # E.g., "aux articles L. 254-6-2 et L. 254-6-3"
        "source": str,                # "DELETIONAL" or "DEFINITIONAL"
        "confidence": float           # 0.0 to 1.0
    },
    # ... more references
]
```

### **ReferenceObjectLinker (Updated)**

**Input**:

```python
{
    "located_references": [           # From ReferenceLocator
        {
            "reference_text": str,
            "source": str,            # "DELETIONAL" or "DEFINITIONAL"
            "confidence": float
        }
    ],
    "original_law_article": str,      # Full original article (for DELETIONAL context)
    "intermediate_after_state_text": str  # Full intermediate article (for DEFINITIONAL context)
}
```

**Output**:

```python
[
    {
        "reference_text": str,           # E.g., "aux articles L. 254-6-2 et L. 254-6-3"
        "source": str,                   # "DELETIONAL" or "DEFINITIONAL"
        "object": str,                   # E.g., "conseil"
        "agreement_analysis": str,        # Grammatical reasoning
        "confidence": float,             # 0.0 to 1.0
        "resolution_question": str       # E.g., "Quelles sont les dispositions spécifiques prévues aux articles L. 254-6-2 et L. 254-6-3 concernant les modalités du conseil ?"
    },
    # ... more linked references
]
```

## 3. Step-by-Step Implementation Plan

### **Step 1: Update `TextReconstructor` Data Contract**

**Objective**: Modify the `TextReconstructor` component to explicitly separate the `newly_inserted_text` from the full `intermediate_after_state_text`.

**Action**: The LLM prompt and response format for the `TextReconstructor` must be updated.

**Current JSON Output Schema**:

```json
{
  "deleted_or_replaced_text": "...",
  "intermediate_after_state_text": "..."
}
```

**Required NEW JSON Output Schema**:

```json
{
  "deleted_or_replaced_text": "The exact text that was removed or replaced.",
  "newly_inserted_text": "The exact text that was added. For pure deletions, this is an empty string.",
  "intermediate_after_state_text": "The full text of the article *after* the amendment has been applied (for context)."
}
```

**Rationale**: This change is critical. It provides the small, targeted `newly_inserted_text` fragment needed for the `ReferenceLocator`'s focused scanning, while preserving the full `intermediate_after_state_text` required for contextual analysis by the `ReferenceObjectLinker`.

---

### **Step 2: Update `ReferenceLocator` Scanning Logic**

**Objective**: Drastically reduce scanning overhead by pointing the `ReferenceLocator` at the delta fragments instead of the full article text.

**Action**:

1.  **Modify Input**: The `ReferenceLocator` will now take `deleted_or_replaced_text` and `newly_inserted_text` as its primary inputs.
2.  **Update Scanning Logic**:
    - Scan `deleted_or_replaced_text` to find references, and tag them with `source: DELETIONAL`.
    - Scan `newly_inserted_text` to find references, and tag them with `source: DEFINITIONAL`.
3.  **Performance Gain**: This focuses the LLM on ~80 characters of changed text instead of 3000+ characters of the full article.

---

### **Step 3: Update `ReferenceObjectLinker` Context & Question Logic**

**Objective**: Implement smart context-switching for accurate grammatical linking and combine object detection with contextual question generation in a single LLM call.

**Actions**:

1.  **Provide Full Context**: Ensure the `ReferenceObjectLinker` receives access to both the `original_law_article` and the full `intermediate_after_state_text`.
2.  **Implement Context-Switching Logic**:

    ```python
    # Inside the main loop of ReferenceObjectLinker, for each reference:
    if ref.source == ReferenceSourceType.DELETIONAL:
        # DELETIONAL refs need the *original* context to find their object
        context_for_linking = original_law_article
    else: # ref.source == ReferenceSourceType.DEFINITIONAL
        # DEFINITIONAL refs need the *new* context to find their object
        context_for_linking = intermediate_after_state_text

    # ... proceed with LLM call for combined object linking + question generation ...
    ```

3.  **Update Function Calling Schema**: Modify the existing function to include question generation:

    ```python
    {
        "type": "function",
        "function": {
            "name": "link_reference_and_generate_question",
            "description": "Analyze French grammatical structure to link a legal reference to its object AND generate a precise resolution question",
            "parameters": {
                "type": "object",
                "properties": {
                    "object": {
                        "type": "string",
                        "description": "Complete noun phrase that the reference modifies (e.g., 'conseil', 'producteurs', 'la liste')"
                    },
                    "agreement_analysis": {
                        "type": "string",
                        "description": "Grammatical reasoning explaining the object identification"
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Confidence 0-1 for the object identification"
                    },
                    "resolution_question": {
                        "type": "string",
                        "description": "Precise French question asking what the reference legally defines/establishes concerning the identified object"
                    }
                },
                "required": ["object", "agreement_analysis", "confidence", "resolution_question"]
            }
        }
    }
    ```

4.  **Enhanced System Prompt**: Update the system prompt to handle both tasks:

    ```
    You are a French legal text analysis expert. Your task has two parts:

    1) OBJECT LINKING: Identify the complete noun phrase that this legal reference modifies, defines, or clarifies using French grammatical analysis.

    2) QUESTION GENERATION: Generate a precise French question to resolve what this reference legally establishes concerning the identified object.

    For object linking, consider:
    - French grammatical agreement (gender, number)
    - Proximity and logical relationship
    - Semantic meaning in legal context
    - Preposition patterns (au/à la/aux, du/de la/des, etc.)

    For question generation, consider:
    - Use formal legal French
    - Be specific about what legal aspect needs clarification
    - Whether this is DELETIONAL (being removed) or DEFINITIONAL (being added)
    - Focus on concrete legal content (definitions, procedures, restrictions)
    - Start with "Que", "Quelles sont", "Comment", etc.

    The object you identify MUST make sense for the question you generate. If you can't generate a meaningful legal question about an object, reconsider your object identification.

    Examples:
    - Reference "aux articles L. 254-6-2 et L. 254-6-3" in context mentioning "conseil" → Object: "conseil", Question: "Quelles sont les dispositions spécifiques prévues aux articles L. 254-6-2 et L. 254-6-3 concernant les modalités du conseil ?"
    - Reference "au sens de l'article 3 du règlement (CE) n° 1107/2009" in context defining "producteurs" → Object: "producteurs", Question: "Comment l'article 3 du règlement (CE) n° 1107/2009 définit-il précisément les 'producteurs' ?"
    ```

## 4. Rationale and Benefits

- **Efficiency**: Reduces scanning load from ~3200 chars to ~83 chars in the example case (>38x improvement).
- **Accuracy**: Uses the correct contextual document (`original` vs. `intermediate`) for more reliable grammatical object linking.
- **Targeted Resolution**: Focuses the expensive resolution step only on references that were actually part of the amendment.
- **Clarity**: The explicit `resolution_question` makes the final resolution step deterministic and easier to debug.
- **Alignment with Mental Model**: Mirrors a lawyer's workflow: focus on the change, but use full context to understand it.
