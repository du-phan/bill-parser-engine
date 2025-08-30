### Pipeline diagnostics: findings and issue tracker

This document tracks observations, issues, and proposed fixes from running the diagnostic script on single chunks of the sample legislative bill. It will be updated as we discover new items.

### Overall assessment (current state) — refreshed after re-export and re-validation

Based on end-to-end tests over 30+ real chunks (spanning Article 1, 3, 4, 5, and Title IV Articles 7–8), the pipeline demonstrates promising capabilities but is not yet robust enough for broad, unsupervised use. It works well on certain patterns but exhibits systemic weaknesses that must be addressed for reliability.

- Strengths

  - EU regulation resolution via local files remains accurate and fast. In multiple runs, definitional references such as “au sens de l'article 47 du même règlement” were matched to the correct EU file and content extracted reliably.
  - French code article retrieval from Legifrance continues to be dependable when the target is well-formed (e.g., `L. 254-1`, `L. 251-3`).
  - INSERT scenarios now validate cleanly end-to-end on newly exported chunks. Example: `# TITRE IV::Article 6::I::3°` (insertion of `L. 174-3`) decomposed to a single ADD, applied successfully, and proceeded through linking/resolution (with caveats below for internal cross-references).
  - Reference location and object linking are generally coherent, with high-confidence early exits observed. Rate limiter behavior is visible and functional (with occasional 429s handled by backoff).

- Critical weaknesses affecting robustness

  - Target article identification gating and schema robustness have been implemented. Pure versioning or suppressed-enumeration points (e.g., `3° (Supprimé)`, `3. Est supprimé.`) are now classified as `OTHER` with a reason and are skipped before retrieval. For operational chunks, confidence is propagated and low-confidence results are gated. On the sampled set (Title IV Articles 7–8; Title I/II cases), previously missing `code`/`article` were correctly returned with high confidence. Keep monitoring Arabic-numbered headings that are not pure versioning; the user-prompt hinting is in place and performed well in sampled runs.
  - Retrieval still lacks deterministic handling for letter-suffixed and hyphenated sub-articles (`L. 1 A`, `L. X-Y-Z`). Not directly exercised in this pass, but unchanged code paths imply the weakness remains until explicit canonicalization + parent carve are implemented.
  - Internal French cross-references to newly inserted articles are not resolved deterministically against the intermediate after-state. For `# TITRE IV::Article 6::I::3°`, definitional refs like “au I”, “au IV”, and “au présent article” triggered fresh Legifrance fetch attempts for `'L. 174-3 (au I)'`, which (predictably) failed; only external definitional refs (e.g., `L. 172-1`) were resolved cleanly. This indicates the planned after-state anchoring/carving is still missing.
  - Validator/console semantics: Step 4 is printed as PASS even when zero operations are applied (e.g., `# TITRE Iᴱᴿ::Article 1::2°::a)` and `# TITRE Iᴱᴿ::Article 1::4°::a)` both showed 0/1 operations applied). The final summary also prints “ALL STEPS PASS” because the current validator does not treat partial application as a failure. This continues to mask reconstruction issues.

- Pertinence and coverage

  - The architectural decomposition is pertinent: the pipeline’s steps and data model are aligned with legal drafting patterns (articles, subdivisions, alinéas).
  - Coverage gaps remain for frequent, real-world forms (Arabic enumerations; nested/compound sub-articles). These are addressable with targeted, deterministic logic.

- Readiness verdict
  - Not production-ready for general runs. Suitable for guided/offline analysis and for a narrow slice of cases (simple article-level modifications; EU definitional references; clear alinéa references) provided human supervision.
  - After re-export and spot re-validation, strengths are reinforced on EU references and INSERT flows, but the Arabic-enumeration identification, Step 4 success semantics, and internal-after-state carving remain blocking for broader reliability.

### Issues by component (with impacted chunks)

Severity legend

- High: frequently reproduced across many chunks in a family; blocks downstream steps; impacts core accuracy.
- Medium: reproduced in multiple chunks; degrades reliability or causes intermittent failures.
- Low: edge cases or presentation issues.

#### TargetArticleIdentifier

- Status: Medium severity (residual edge cases). Schema validation, retries, confidence gating, Arabic-heading hinting, and suppressed-enumeration handling are implemented and working in sampled runs.

- Implemented

  - Schema enforcement with up to 2 retries (stricter instructions + abbreviated prompt).
  - Confidence propagation and gating (skip if OTHER, missing fields, or confidence < 0.6).
  - Arabic-numbered heading hint using `hierarchy_path`; stricter system guidance on retries.
  - Suppressed-enumeration detection (returns OTHER with `reason="suppressed_enumeration"`; skipped before retrieval).
  - Strict OTHER sanitization (no stray `code`/`article`).

- Sample outcomes (identifier-only)

  - `# TITRE IV::Article 8::III::11°::b)` → MODIFY `L. 251-3` (conf 0.95)
  - `# TITRE IV::Article 8::III::1°` → MODIFY `L. 250-1` (conf 0.98)
  - `# TITRE IV::Article 8::III::2°::b)` → MODIFY `L. 254-1` (conf 0.95)
  - `# TITRE IV::Article 8::III::6°` → MODIFY `L. 251-9` (conf 0.98)
  - `# TITRE IV::Article 8::III::12°::a)` → MODIFY `L. 251-14` (conf 0.95)
  - `# TITRE IV::Article 8::III::3°` → INSERT `L. 250-5-1` (conf 0.98)
  - `# TITRE Iᴱᴿ::Article 2::II::3°` → ABROGATE `L. 253-8-3` (conf 0.98)
  - `# TITRE IV::Article 7::1°` → initial schema rejection (structural node); retry succeeded with MODIFY `L. 254-1` (conf 0.95)

- Impact

  - OTHER (versioning/suppressed) chunks are skipped early (no retrieval).
  - Operational chunks return (`code`, `article`) with high confidence, reducing downstream empty-retrieval failures.

- Remaining improvements

  - Extend suppressed-enumeration regex to accept trailing semicolons/variants.
  - Optional multi-target support for statements like “Les articles X et Y…”.
  - Additional article canonicalization (strip leading “Article ” if emitted by LLM).
  - Surface `reason`/`skipped_noop` in pipeline outputs/logs for observability.
  - Monitor/tune `confidence` threshold; consider a minimum-text guard for ultra-short fragments.

- Acceptance
  - Previously problematic Title IV 7–8 and Title I/II samples return valid (`code`, `article`) with high confidence; OTHER/no-op chunks are skipped. Misclassifications on first attempt are corrected by the retry path.

#### OriginalTextRetriever

- Status: Medium → trending Low for French codes (core gaps addressed; minor polish remains)

- What was wrong

  - Letter-suffixed articles (e.g., `L. 1 A`) failed to match pylegifrance because the API expects a specific token form.
  - Sub-articles (e.g., `L. 211-1-2`) needed reliable hierarchical fallback with deterministic subsection carving; LLM-only fallback was brittle.
  - Structural nodes (e.g., “chapitre”, “titre”) occasionally triggered API calls.
  - No local persistence for successful fetches; retries lacked determinism.

- Fixes implemented

  - Legifrance token variants
    - Search variant generator now includes the empirically correct tokens for letter-suffix articles, notably `L1 A` (and `L 1 A`), alongside compacted and hyphenated forms. This aligns with probe results where `L1 A` returned the correct article.
  - Accent-insensitive code mapping
    - Code names are normalized (diacritics stripped, punctuation normalized) before mapping to `CodeNom.*`. This avoids mismatches like `pêche` vs `peche` coming from different sources.
  - Hierarchical fallback with deterministic carve
    - For `L. X-Y-Z`, fetch parent `L. X-Y` and attempt a deterministic carve for subsection `Z` using robust regexes; fall back to a single LLM extraction only if needed.
    - Structural guard
    - Early returns for structural nodes to avoid pointless API calls and clearer error reporting.
  - Local read-first/write-through store
    - Successful French code fetches are persisted under `data/fr_code_text/<code_slug>/<article>.txt|.json`. On API failure, the store is checked as a deterministic fallback.

- Evidence (live runs)

  - `L. 211-1-2` (code de l'environnement): direct variants return zero; hierarchical fallback fetched `L. 211-1` and LLM carve extracted subsection 2 successfully (≈460 chars).
  - `L. 1 A` (code rural et de la pêche maritime): probe script shows `L1 A` works with pylegifrance; the retriever includes `L1 A` in its variant list. A CLI mojibake surfaced once (“p��che”); the retriever now normalizes diacritics for mapping, but shell-encoding issues can still produce replacement characters that defeat normalization at the CLI boundary. Within the pipeline (pure Python), this should not occur.
  - INSERT cases (`L. 250-5-1`) correctly yield empty original text; standard articles (`L. 254-1`, `L. 251-3`) remain successful.

- Remaining improvements (minor)

  - Harden code-name normalization against replacement characters introduced by mis-encoded CLI inputs (not observed inside the pipeline). Optional: map any non-ASCII letter to its ASCII fallback or remove entirely with fuzzy match on token sets.
  - When hierarchical carve succeeds via LLM, optionally persist the parent text to the local store as well to speed up future subsection carves across runs (parent persistence is already enabled after parent fetch).
  - Add a small telemetry field in metadata indicating which variant matched (e.g., `matched_token='L1 A'`).

- Acceptance
  - Letter-suffixes: supported via `L1 A` and mapping normalization; behaves correctly in normal pipeline runs.
  - Sub-articles: succeed via parent+carve; `L. 211-1-2` verified end-to-end.
  - Structural nodes: gated out with clear errors.
  - Local store: active (write-through on success; read-first on failures).
  - We are ready to move on to the next section; monitor for rare encoding anomalies at the CLI boundary and address if they appear in non-CLI contexts.

#### BillSplitter / Chunking Strategy

- Issue: Some INSERT/REWRITE operations reference context outside the current chunk (e.g., “Après le 5° bis du I”), causing the reconstructor to fail to apply operations because the anchor is absent in the chunk text.
- Impacted chunks: 053 (`Article 5::3.`) where INSERT “Après le 5° bis du I” could not be applied (Applied 0/1).
- Fixes (implemented now, minimal and cleaner):
  - Structured anchor hints (no change to chunk text)
    - `BillSplitter` detects structural anchor phrases in numbered-point and subdivision intros (e.g., “Après le 5° bis du I”, “Avant le 3° du II”, “À la fin du III”, “Au début du II”).
    - Emits a normalized `structural_anchor_hint` on each `BillChunk` with fields like: `placement` (after|before|at_end|at_start), `section`, `section_suffix`, `point`, `point_suffix`, and `raw`.
    - This keeps chunks focused while providing deterministic anchor data for reconstruction against the original article.
  - Serialization
    - Exporter writes `structural_anchor_hint` in sidecar JSON; validator loader consumes it.
  - Lettered subdivision splitting remains unchanged and compatible with hints.
  - Rationale
    - Avoids inflating chunk text with external context; preserves separation of concerns: splitter parses structure, reconstructor applies anchors to original text.
  - Acceptance (splitter scope)
    - Re-exported all chunks; files now include `structural_anchor_hint` wherever applicable (e.g., 058 `Article 8 III 2° a)`: phrase begins with “Après le mot …”, hint present when phrase matches structural forms; INSERT anchors spanning sections use hints).
    - Ready for the next section to consume these hints in reconstruction for deterministic anchor placement.

#### InstructionDecomposer

- Severity: Medium (intermittent on complex MODIFY/REWRITE)

- Implemented

  - Corrective downgrade inside `_parse_response()`
    - REPLACE without `target_text` but with `replacement_text` is downgraded to REWRITE before validation to avoid hard failure.
  - Non-fatal per-operation parsing
    - Invalid operations are skipped rather than aborting the entire list. If all are invalid, an empty list is returned (downstream will surface failure at reconstruction).
  - Existing strict validation retained for other cases (INSERT/ADD/REWRITE require `replacement_text`).

- Evidence/impact

  - Reduces Step 4 failures originating from malformed single operations emitted by the LLM on complex blocks.
  - Preserves successful paths where a single REWRITE applies cleanly (e.g., Title IV 8 III 7° a)).

- Remaining improvements (minor)

  - Surface per-op downgrade as a warning in Step 4 logs (not yet wired to outputs).
  - When zero operations are produced, add a short instruction preview in Step 4 diagnostics (handled in reconstruction/validator section).

- Acceptance
  - Current changes are sufficient to proceed; decomposition no longer fails the entire step due to one malformed REPLACE, and REWRITE downgrades keep useful `replacement_text` flows alive.

#### OperationApplier / ResultValidator (Reconstruction)

- Severity: Medium (reporting alignment; not systemic)

- Issue: Partial or zero application still reported VALID and Step 4 shown as PASS.
- Impact: Masks amendment application failures.
- Impacted in current run (examples):
  - `# TITRE Iᴱᴿ::Article 1::2°::a)` — 0/1 operations applied; Step 4 printed PASS; final “ALL STEPS PASS”.
  - `# TITRE Iᴱᴿ::Article 1::4°::a)` — 0/1 operations applied; Step 4 printed PASS; final “ALL STEPS PASS”.
  - `# TITRE IV::Article 8::III::11°::b)` — 0/1 operations applied; Step 4 printed PASS; final “ALL STEPS PASS”.
    - `# TITRE IV::Article 7::2°::c)` — 0/1 operations applied (expected intra-article insertion), Step 4 printed PASS; final “ALL STEPS PASS”.
- Fixes:
  - Implemented: success semantics
    - `LegalAmendmentReconstructor` now reports success only if: no failed ops, all ops applied, and validator status is `VALID`.
  - Existing: anchor diagnostics (input validation)
    - `OperationApplier._validate_operation_input()` returns a clear misalignment message when a position_hint refers to a non-existent section/point.
  - Remaining (minor): validator guardrail
    - Optionally treat zero-ops cases as `ERRORS` at validator level when the instruction contains operation keywords (current reconstructor success semantics already prevent PASS).
  - Acceptance
    - Partial/zero application is no longer reported as PASS. Downstream logs already show applied/failed counts; failure now surfaces correctly.

#### ReferenceLocator

- Status: Stable; correct counts in runs. No blocking issues observed.
- Minor hardening:
  - Clamp `min_confidence` in constructor to [0,1].
  - Add simple length guard: ignore references shorter than 4 chars post-trim.
  - Additional confirmation: On `# TITRE IV::Article 8::III::7°::a)`, 4 definitional references were located as expected from the rewritten alinéa.
  - Additional confirmation: On `# TITRE IV::Article 8::III::3°`, 2 definitional references were located in the inserted article body.

#### ReferenceObjectLinker

- Severity: Medium (was) → Lower (implemented iteration and anchoring controls)

- Implemented

  - Iteration policy
  - Default `max_iterations` reduced to 2. High-confidence early-exit retained (≥ 0.9). Added per-reference time budget (3000 ms) to cap latency under rate limits.
    - Deterministic internal-structure anchoring (after-state)
  - For DEFINITIONAL refs, when the reference mentions sections/points/alinéas (e.g., “du II”, “du 2° du II”, “premier alinéa”), the linker first narrows the context to a small window around the detected anchor inside the after-state, then runs the LLM. Falls back to full context when no anchor found.
  - Caching remains unchanged.

- Expected impact

  - Fewer iterative passes on internal structural references; more stable and faster linking. Bounded latency per reference even under 429s.

- Remaining considerations (minor)

  - Deletional pointers like “la fin du III” still rely on original-text availability; a dedicated deletion-target linker using reconstruction diffs could further improve recall (not in scope here).

- Acceptance
  - The linker now applies deterministic context narrowing and bounded iteration/time. Existing high-confidence early exits persist. Ready to proceed to next components.

#### ReferenceResolver

- Status: Medium → Lower (deterministic carving added; after-state preference; stricter empty-answer guard)

- Implemented

  - After-state preference for new articles
    - When a DEFINITIONAL reference targets the newly inserted target article (same code and article), the resolver now uses `intermediate_after_state_text` directly as source content.
  - Deterministic subsection carving before LLM
    - If the reference text contains internal structure tokens (e.g., `du II`, `au 2° du II`, `premier alinéa`), the resolver parses with regex-only and attempts a carve from the after-state (when available) or from retrieved content before calling the QA model.
    - Regex parsing expanded to include textual alinéa and robust roman numerals; extraction supports points and alinéas deterministically (paragraph-based, with sentence fallback).
  - Empty-answer guard (kept)
    - If the answer extraction returns empty/whitespace, the resolver counts the ref as unresolved.
  - EU detection (kept)
    - Remains conservative; LLM-assisted local file matching attempted only when likely; French code fallback otherwise.

- Expected impact

  - Internal references in the same article (sections/points/alinéas) resolve more reliably by using the after-state and deterministic carving. Generic or underspecified references remain unresolved (as desired).

- Acceptance
  - The resolver now prefers after-state for new-article references, deterministically carves internal subsections, and avoids reporting empty answers. Ready to proceed.

#### OperationApplier / ResultValidator (additional observation)

- Stable on complex REPLACE/alinéa references: `Article 4 I 10.` (chunk 043) applied REPLACE, validated VALID, and resolved both DELETIONAL and DEFINITIONAL alinéa-based references correctly.

#### Diagnostics and validator script

- Issue: Step 4 reporting still does not fail on partial-application failures (prints PASS despite 0/N operations applied); overall “ALL STEPS PASS” is printed if no hard exceptions occur.
- Impacted chunks: 007 (partial application), 010 (decomposition critical failure).
- Issue: Missing structured durations and rate-limit waits in JSONL (visible in console prints but not standardized).
  - Issue: Validator accepts non-.txt inputs (e.g., `.json`) and proceeds; should guard and error early.
  - Negative test: passed `.json` (009) and pipeline still ran; add input-type validation.
  - New: Step 2 should fail-fast when identifier emits missing `code`/`article` or non-article structural ids; do not proceed to retrieval.
  - Fixes:
    - Step status semantics
      - Treat Step 4 as FAIL if `operations_applied < total_operations` or validator returns `ERRORS`.
    - Exception surfacing
      - When InstructionDecomposer raises or yields invalid operations (e.g., REPLACE without `target_text`), propagate a structured error up to the JSONL and halt further steps for that chunk.
    - Telemetry
      - Add per-step `duration_ms`, `rate_limit_wait_ms` (from shared rate limiter), and `warnings` arrays to JSONL records.
    - Input validation
      - Guard input type: accept only `.txt` chunk paths; reject others with a clear message.
    - Suppressed-enumeration skip policy
      - When a chunk text matches the suppressed-enumeration regex (see TargetArticleIdentifier), mark the run as `SKIPPED (suppressed_enumeration)` and short-circuit Steps 3–7. Log `skipped_noop=true` in JSONL.
      - Exclude skipped chunks from failure-rate statistics in summary reports.
    - Empty-answer guard
      - The current validator already asserts non-empty resolved content in Step 7. Keep this guard and add a symmetric guard inside `ReferenceResolver._resolve_single_reference()` so the core never emits empty resolved content.
  - Acceptance:
    - JSONL logs include durations and rate-limit waits; Step 4 results reflect partial/failed reconstructions; non-.txt inputs are rejected early.
    - Suppressed-enumeration chunks are clearly marked as skipped and no longer counted as failures.
