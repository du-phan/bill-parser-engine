# LegalStateSynthesizer (Step 8) — Implementation Spec

Last updated: 2025-08-12

## 1. Objective

Turn the pipeline’s focused deltas into lawyer-usable “before” and “after” fragments with precise, question-resolved references. Keep synthesis deterministic, minimal, and robust across all chunks in `scripts/output/duplomb_chunks_all/`.

Outputs per chunk:

- BeforeState: exact text being changed/removed, annotated with resolved references.
- AfterState: text being inserted/replaced, annotated with resolved references.

No full-article rewriting, no LLM calls. Just deterministic matching and rendering of annotations.

## 2. Inputs and Outputs

Inputs (from earlier steps):

- `BillChunk` (contextual metadata; `chunk_id`, hierarchy, etc.)
- `TargetArticle` (operation type, `code`, `article`)
- `ReconstructorOutput` with three fields:
  - `deleted_or_replaced_text`
  - `newly_inserted_text`
  - `intermediate_after_state_text` (context only; not scanned)
- `ResolutionResult` with:
  - `resolved_deletional_references: List[ResolvedReference]`
  - `resolved_definitional_references: List[ResolvedReference]`
  - `unresolved_references: List[LinkedReference]`

Outputs:

- `LegalAnalysisOutput` with two `LegalState`s and metadata
  - `before_state: LegalState`
  - `after_state: LegalState`
  - `metadata`: `{ chunk_id, target_article, operation_type, counts, timing }`

## 3. Data Models (to add in `models.py`)

```python
@dataclass
class LegalReferenceAnnotation:
    marker_index: int                  # 1-based
    reference_text: str
    object: str                        # from linker
    resolved_content: str              # from resolver
    source: ReferenceSourceType        # DELETIONAL | DEFINITIONAL
    start_offset: int                  # inclusive in annotated text
    end_offset: int                    # exclusive in annotated text (after marker insertion)
    retrieval_metadata: Dict[str, Any] # subset: source, reduction_percentage, hints

@dataclass
class LegalState:
    text: str
    annotations: List[LegalReferenceAnnotation]

@dataclass
class LegalAnalysisOutput:
    before_state: LegalState
    after_state: LegalState
    metadata: Dict[str, Any]

@dataclass
class LegalStateSynthesizerConfig:
    render_mode: str = "footnote"         # footnote | inline | none
    max_resolved_chars: int = 400
    annotate_all_occurrences: bool = False
    normalize_matching: bool = True
    footnote_prefix: str = ""              # optional label before footnotes
```

## 4. Fragment Selection by Operation Type

- REPLACE / REWRITE:
  - Before = `deleted_or_replaced_text`
  - After = `newly_inserted_text`
- DELETE / ABROGATE:
  - Before = `deleted_or_replaced_text`
  - After = ""
- INSERT / ADD:
  - Before = ""
  - After = `newly_inserted_text`

This adheres to the focused scanning contract and ensures deterministic outputs.

## 5. Annotation Sources per Fragment

- BeforeState uses: `resolved_deletional_references`
- AfterState uses: `resolved_definitional_references`

Each `ResolvedReference` carries the original located text and the extracted answer; they are aligned with the respective delta fragment.

## 6. Matching and Annotation Algorithm

Goal: Insert in-text markers [n] next to matched reference substrings and produce a footnote-like section or inline parentheticals.

Per fragment (Before or After):

1. Start with `fragment_text`.
2. Build candidate matches for each resolved reference r:
   - Try exact substring match of `r.linked_reference.reference_text`.
   - If not found and `normalize_matching=True`, try a quote/space-normalized search and a permissive regex variant that tolerates French quotes « » vs " ", non-breaking spaces, and adjacent punctuation.
3. Resolve overlaps using a greedy non-overlapping selection:
   - Sort candidates by start index ascending, then by length descending.
   - Take a match if it doesn’t overlap the last kept one; else prefer the longer span.
4. If `annotate_all_occurrences=False`, keep only the first accepted match per distinct reference text.
5. Insert markers [n] at insertion points:
   - Process accepted matches in increasing start order; track cumulative offset delta as markers expand the string.
   - Record final `start_offset` and `end_offset` for each annotated span, now including the marker.
6. Render annotations according to `render_mode`:
   - footnote (default): append a block after the fragment:
     - `n. reference_text → object: resolved_content (source: DELETIONAL|DEFINITIONAL)`; truncate `resolved_content` if longer than `max_resolved_chars`.
   - inline: replace matched substring with `match + " (object: …)"` (still add [n] if we want correlation; configurable later if needed).
   - none: return unmodified fragment text, but include the `annotations` list in output.

Edge behavior:

- If fragment is empty → return empty text and empty annotations.
- If no references matched → return text unchanged; annotations empty.
- If a reference resolves but isn’t found in the fragment → skip (count in metadata `skipped_not_found`).

Normalization specifics (matching only; text output preserves original):

- Normalize spaces (collapse multiple spaces; convert non-breaking spaces to spaces) when building match variants.
- Normalize quotes (map « », “ ” to ") for variant try; still prefer exact original first.

## 7. Determinism and Performance

- No LLM calls; purely deterministic.
- Complexity proportional to fragment length and number of references; fragments are short (delta), so cost is negligible.
- Marker numbering restarts at 1 per fragment.

## 8. Integration

New module: `bill_parser_engine/core/reference_resolver/legal_state_synthesizer.py`

```python
class LegalStateSynthesizer:
    def __init__(self, config: Optional[LegalStateSynthesizerConfig] = None):
        ...

    def synthesize(
        self,
        chunk: BillChunk,
        target: TargetArticle,
        recon: ReconstructorOutput,
        resolution: ResolutionResult,
    ) -> LegalAnalysisOutput:
        ...
```

Pipeline wiring:

- Add `step_8_synthesize_states()` in `pipeline.py`.
- Extend `save_results()` to persist Step 7 (resolution) and Step 8 (synthesis) together.
- Optional: add a CLI runner for Step 8 only.

## 9. Logging and Metrics

Per chunk, include in `metadata`:

- `before: { refs_resolved, refs_annotated, skipped_not_found }`
- `after:  { refs_resolved, refs_annotated, skipped_not_found }`
- Timing and config snapshot (render_mode, max chars, annotate_all_occurrences).

## 10. Testing Plan (targeting `duplomb_chunks_all/`)

Representative cases:

- INSERT/ADD: 045, 047, 060, 073 → AfterState annotated, BeforeState empty.
- REPLACE/REWRITE: 040, 043, 055, 072 → Both fragments may be non-empty.
- DELETE: 070, 063, 044 → BeforeState annotated, AfterState empty.
- Anaphoric references: 042, 071 → AfterState references align with delta text.
- EU references: 025 → Resolved content truncation and footnote rendering.

Assertions:

- Marker counts equal matched resolved references per fragment.
- Offsets correct after marker insertions.
- Footnotes ordered by marker index and include object + truncated resolved content.
- Unresolved references produce no markers; captured in metadata counts.
- Deterministic output across runs.

## 11. Risks and Mitigations

- Reference text small variations (quotes/spaces): mitigated by normalization + regex fallback.
- Overlapping references: resolved by greedy longest-span preference.
- Very long resolved content: truncation with ellipsis; keep full text in annotation object if needed downstream.
- Idempotence: Step 8 runs on raw delta fragments (not previously annotated) in the pipeline.

## 12. Out-of-Scope / Non-Goals

- No full-article synthesis or context reflow.
- No backward-substitution into `intermediate_after_state_text`.
- No LLM usage in Step 8.

## 13. Implementation Checklist

- [ ] Add data models to `models.py`.
- [ ] Implement `legal_state_synthesizer.py` with matching + rendering.
- [ ] Wire `step_8_synthesize_states()` in `pipeline.py` and extend saving.
- [ ] Add tests for matching, rendering modes, and operation mapping.
- [ ] Validate on a batch from `duplomb_chunks_all/` and inspect output.
