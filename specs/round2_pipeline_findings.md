### Round 2 Pipeline Diagnostics: Component-level Findings

## Scope (tested chunks)

- 071 — `# TITRE IV::Article 8::III::11°::b)` — log: `round2_071.jsonl`
- 060 — `# TITRE IV::Article 8::III::3°` — log: `round2_060.jsonl`
- 058 a) — `# TITRE IV::Article 8::III::2°::a)` — log: `round2_058a.jsonl`
- 053 — `# TITRE IV::Article 7::1°` — log: `round2_053.jsonl`
- 054 — `# TITRE IV::Article 7::2°::a)` — log: `round2_054.jsonl`
- 055 — `# TITRE IV::Article 7::2°::b)` — log: `round2_055.jsonl`
- 056 — `# TITRE IV::Article 7::2°::c)` — log: `round2_056.jsonl`
- 057 — `# TITRE IV::Article 8::III::1°` — log: `round2_057.jsonl`
- 059 b) — `# TITRE IV::Article 8::III::2°::b)` — log: `round2_059b.jsonl`
- 061 — `# TITRE IV::Article 8::III::4°` — log: `round2_061.jsonl`
- 072 — `# TITRE IV::Article 8::III::12°::a)` — log: `round2_072.jsonl`
- 025 — `# TITRE Iᴱᴿ::Article 2::II::1° B` — log: `round2_025.jsonl`
- 040 — `# TITRE II::Article 4::I::1°::a)` — log: `round2_040.jsonl`
- 045 — `# TITRE III::Article 5::1°::a)` — log: `round2_045.jsonl`
- 042 — `# TITRE II::Article 4::I::1° bis` — log: `round2_042.jsonl`
- 043 — `# TITRE II::Article 4::I::1° ter` — log: `round2_043.jsonl`
- 038 — `# TITRE II::Article 3::I::5°::a)` — log: `round2_038.jsonl`
- 041 — `# TITRE II::Article 4::I::1°::b)` — log: `round2_041.jsonl`
- 044 — `# TITRE II::Article 4::I::2°` — log: `round2_044.jsonl`
- 047 — `# TITRE III::Article 5::1° bis` — log: `round2_047.jsonl`

---

## TargetArticleIdentifier

- Observed
  - Correct identifications on 071 (MODIFY L. 254-1), 060 (INSERT L. 250-5-1), 057 (MODIFY L. 250-1), 059 b) (MODIFY L. 254-1), 061 (MODIFY L. 250-9).
  - 072 (MODIFY L. 251-14).
  - 025 (INSERT L. 253-1-1, nouvel article).
  - 040 (MODIFY L. 361-4-6).
  - 044 (MODIFY L. 361-4-6).
  - 047 (INSERT L. 211-1-2, nouvel article).
  - 053 classified as OTHER after schema-guarded retries (pure heading) — correct.
- Analysis: Misidentifications detected on 058 a) and 056 in this batch (LLM drifted to a referenced/new-article label instead of the parent intro target). Fixed by deterministic use of the parent introductory phrase.
- Fixes implemented:
  - Deterministic guard: prefer `inherited_target_article` from the introductory phrase (e.g., “L'article L. 250-3 est ainsi modifié :”, “L'article L. 258-1 est ainsi modifié :”).
  - For INSERT intros like “Après l'article X, il est inséré un article Y…”, extract Y deterministically; otherwise treat as intra-article insert on the inherited article.

## OriginalTextRetriever

- Observed
  - French code retrieval succeeded consistently (071, 057, 059 b), 061).
  - INSERT (060) handled with empty original; parent carve later used in resolver.
  - INSERT (058 a), 056 were intra-article inserts but Step 3 returned empty original because we passed INSERT to `fetch_article_for_target()` which unconditionally returns empty for INSERT.
  - 072: Retrieved L. 251-14 successfully from Legifrance.
  - 025: New-article INSERT correctly returned empty base (expected).
  - 040: Retrieved L. 361-4-6 successfully from Legifrance.
  - 045: Treated as new-article INSERT; empty base (should be intra-article enumerated insertion after “5° bis du I”).
  - 042: INSERT treated as new-article; empty base caused partial failures (2/4 applied later as standalone insertions).
  - 043: Retrieved L. 361-4-6 successfully from Legifrance.
  - 038: INSERT treated as new-article; empty base prevented token/alinéa insertion.
  - 041: Treated as new-article INSERT; empty base (ADD applied as standalone sentence addition).
  - 044: Retrieved L. 361-4-6 successfully from Legifrance.
  - 047: Direct lookup for L. 211-1-2 failed as expected; hierarchical fallback L. 211-1 → subsection 2 succeeded (content carved).
- Deep analysis and corrections for 058 a) and 056
  - 058 a) — The modified article is L. 250-3 (per numbered point intro), inserting a reference to L. 201-4. Previously misinterpreted as targeting L. 201-4. Now identified deterministically as MODIFY L. 250-3.
  - 056 — The modified article is L. 258-1; intra-article insertion of a new alinéa. Previously marked as “nouvel article”. Now identified deterministically as MODIFY L. 258-1.
  - Distinguishing signal: intra-article anchors (“Après le mot”, “Avant le dernier alinéa”) with an existing `(code, article)` predicate intra-article INSERT.
- Fixes implemented (code-level)
  - In `fetch_article_for_target()`: INSERT now probes for `(code, article)` existence. If it exists, returns current article text (intra-article); else returns empty (new article).
  - Runner now populates inheritance hints so deterministic guard is effective in single-chunk tests.

## BillSplitter / Chunking Strategy

- Observed
  - Structural anchor hints (e.g., “Après le …”, ordinal sections) are present in metadata; no anomalies.
- Analysis: OK.
- Fixes: None.

## InstructionDecomposer

- Issues (chunk-specific)
  - 071 — Ordinal alinéa target: emitted REPLACE with `target_text` like “Le cinquième alinéa…”, which is not a literal.
  - 058 a) — Micro-insert: “Après le mot « titre, » …” into an existing article; lacks explicit token-scoped `position_hint` for deterministic insertion.
  - 054, 055 — Small-token REPLACE (e.g., “cet organisme”, “l’alinéa précédent”) fragile after earlier REWRITE.
- Deep analysis for 058 a)
  - The LLM decomposed as INSERT with `target_text="titre,"` and `replacement_text="du II de l'article L. 201-4,"`. While this identifies the tokens, it does not supply a precise anchor for the applier. Relying on a plain literal search is brittle if punctuation/spacing differs or if there are multiple occurrences.
  - We need to convert the instruction’s phrasing (“Après le mot : « X »”) into a machine-usable anchor: `position_hint={ after_word: X }` and optionally `within_sentence_of: <quoted sentence from instruction>`.
- Deep analysis for 054/055
  - Multi-step amendments (“le début de la première phrase …”, “cet organisme …”, “l’alinéa précédent …”) imply dependencies: a REWRITE changes the sentence, then subsequent REPLACE operates within the modified sentence. Literal target detection ignores this dependency and fails once the text drifts.
  - We need to attach context from the instruction to the operation: `within_sentence_of: <the sentence fragment being rewritten>` or `within_paragraph_of: <ordinal alinéa anchor>` for anaphoric targets like “l’alinéa précédent”.
- Proposed change (code-level)
  - Normalization pass in `_parse_response()`:
    - Ordinal alinéa → `REWRITE` + `position_hint={ alinea_index: N }`, `target_text=None` (071).
    - Micro-insert → `position_hint={ after_word: X }` (058 a)); optionally `within_sentence_of` extracted from instruction.
    - Small-token REPLACE → include `within_sentence_of` anchor to support applier’s sentence-scoped fallback (054, 055). For “l’alinéa précédent”, map to `position_hint={ alinea_index: prev }`.

## OperationApplier / Reconstruction

- Observed

  - 072 — REWRITE 1/1 applied; VALID; success=True.
  - 025 — 2/2 ADD applied; VALID; success=True.
  - 040 — REPLACE 1/1 applied; VALID; success=True.
  - 043 — REPLACE 1/1 applied; VALID; success=True.
  - 041 — ADD 1/1 applied; VALID; success=True.
  - 044 — DELETE 1/1 applied; VALID; success=True.
  - 047 — INSERT 1/1 applied; VALID; success=True.
  - 058 a) — INSERT 1/1 applied; VALID; success=True. (micro-insert)
  - 056 — INSERT 1/1 applied; VALID; success=True. (alinéa insertion)
  - 045 — INSERT 1/1 applied; VALID; success=True. (enumerated point 5° ter)
  - 055 — 3/3 applied; VALID; success=True. (first-sentence REWRITE + small-token REPLACEs)
  - 071 — REWRITE 1/1 applied; VALID; success=True. (ordinal alinéa handled via robust apply; deterministic path falls back to LLM when index is invalid)
  - Fresh validation (no cache):
    - 070 (III 11° a)) — 2/2 DELETE applied; VALID.
    - 069 (III 10°) — REPLACE 1/1 applied; VALID.
    - 063 (III 6°) — DELETE 1/1 applied; VALID.
    - 066 (III 7° c)) — REWRITE 1/1 applied; VALID (deterministic alinéa rewrite path).
    - 068 (III 9°) — ADD 1/1 applied; VALID.
    - 073 (III 12° b)) — ADD 1/1 applied; VALID.

- Issues
  - Previously failing cases now succeed with upstream fixes and normalized hints (verified with fresh runs), or via robust fallback when deterministic hints are absent:
    - 058 a) micro-insert now applies on retrieved base.
    - 056 alinéa insertion now applies on retrieved base.
    - 045 enumerated point insertion applies.
    - 055 small-token REPLACEs apply after REWRITE with sentence/alinéa scoping.
    - 071 ordinal alinéa now applies (fallback to LLM path used when deterministic index is not resolvable).
- Deep analysis for 058 a)
  - Base text: the reconstructor set `original_law_article=""` because INSERT was assumed to be new-article; the applier cannot perform token insertion without a base string. Even with a base, naive `str.find("titre,")` is brittle across punctuation/quotes normalization.
  - Deterministic insertion requires: (1) sentence boundary detection; (2) word-boundary regex that respects French quotes and punctuation; (3) precise placement after the matched token instance.
- Deep analysis for 054/055
  - After a REWRITE of the first sentence, small-token targets should be searched within that sentence span, not the whole article. A sentence-scoped, punctuation/space-insensitive replace avoids drift and false positives.
  - For anaphoric targets (“l’alinéa précédent”), structural mapping resolves ambiguity: compute the ordinal of the current alinéa from context (e.g., provided by splitter’s hints or decomposer’s `position_hint`), then apply the change to `alinea_index-1`.
- Proposed change (code-level)
  - Base text selection: for intra-article INSERT, supply the retrieved original to reconstruction.
  - Token-level insertion: implement `after_word`/`before_word` with regex like `r"\b{}\b"` over a normalized sentence span that preserves French quotes (e.g., handle « » and trailing commas) and Unicode-aware word boundaries.
  - Sentence-scoped REPLACE fallback: locate the sentence using `within_sentence_of` or the most recent REWRITE operation’s target span; apply a whitespace/punctuation-normalized replace with distance bound (e.g., Levenshtein distance ≤ k) to avoid overreaching.
  - Structural paragraph REWRITE: paragraph segmentation by blank lines; if none, fall back to sentence chunking; replace paragraph `N` with `replacement_text` (multi-paragraph allowed).
  - Runner: short-circuit SKIP on OTHER (053) and print Step 4 `success` and `applied/failed` counts for clarity.

## ReferenceLocator

- Observed
  - 060: 3 DEFINITIONAL; 057: 1 DEFINITIONAL; 055: 1 DEFINITIONAL; others 0 (expected).
  - 025: 2 DEFINITIONAL (EU).
  - 040: 0 (expected).
  - 045: 0 (expected).
  - 042: 2 DEFINITIONAL (article-internal anaphora to the same article’s premier alinéa).
  - 043: 2 (1 DELETIONAL, 1 DEFINITIONAL).
  - 041: 0 (expected).
  - 044: 1 DELETIONAL.
  - 047: 1 DEFINITIONAL.
- Analysis: OK.
- Fixes: None.

## ReferenceObjectLinker

- Observed
  - 060: linked 3/3 (context narrowed; time budget capped iteration).
  - 057: linked 1/1 (high-confidence early exit).
  - 055: linked 1/1 (narrowed context for “Par dérogation au premier alinéa”).
  - 072: linked 1/1 (time budget exhausted once; acceptable early return).
  - 025: linked 2/2 (EU refs), high-confidence early exits.
  - 040: n/a (no references).
  - 045: n/a (no references).
  - 042: linked 2/2 (both intra-article anaphoric refs), one used full time budget iteration.
  - 043: linked 2/2 (one high-confidence early exit).
  - 041: n/a (no references).
  - 044: linked 1/1 (DELETIONAL phrase).
  - 047: linked 1/1 (new article reference), high-confidence.
- Analysis: OK; deterministic narrowing effective, latency bounded by time budget.
- Fixes: None.

## ReferenceResolver

- Observed (fresh runs after fixes)
  - 060: resolves “Art. L. 250-5-1.” (parent carve) and, after guarded retry, resolves “du II de l’article L. 201-4”. Generic EU mention remains unresolved (expected).
  - 072: resolves “de l’article L. 251-14”.
  - 043: resolves both DELETIONAL and DEFINITIONAL refs as before.
  - 069, 070: targeted refs resolved as expected with focused scanning.
  - 057, 047: still may depend on QA outcomes; guarded retry now applies when carve is large.
  - 040, 045, 041: none (no references).
  - 042: resolves two intra-article anaphoric refs using full-article context (0% carve).
  - 044: resolves DELETIONAL (“la fin du III”) from original text.
- Implementation (code-level)
  - Added a single guarded retry when `retrieval_metadata.subsection_extraction.reduction_percentage ≥ 50` and the first QA extraction returns empty.
  - The retry uses a parameterized window around the carved span (`qa_retry_window_chars`, default 300) and minimally augments the question with:
    - Parsed subsection info
    - A compact preview of the carved content (head/tail)
  - Retrieval path prefers deterministic fetch via `OriginalTextRetriever.fetch_article_text`; EU LLM file matching is now a fallback only if deterministic retrieval fails.

---

## Overall assessment and readiness

- Overall snapshot

  - Solid on MODIFY/ADD/DELETE and new-article INSERT; brittle on intra-article INSERT and short-token REPLACE chains post-REWRITE.
  - Component wiring, gating, and logging are reliable; diagnostics are actionable.

- Component status

  - TargetArticleIdentifier: accurate after deterministic intro-based override; OTHER policy effective.
  - OriginalTextRetriever: robust retrieval and normalization; now distinguishes intra-article INSERT vs new article with existence probe.
  - InstructionDecomposer: needs structural `position_hint` for ordinal alinéa, enumerated point inserts (e.g., 5° ter), token-level anchors (after/before word), and anaphora mapping; sentence-span hints for short-token REPLACE.
  - OperationApplier/Validator: succeeds with proper base; needs deterministic paragraph/point replacement, token-level insertion over sentence spans, and sentence-scoped fuzzy REPLACE fallback.
  - ReferenceLocator/ObjectLinker: performing well with context narrowing and time budgets.
  - ReferenceResolver: good on French/EU flows; single guarded retry after large carve implemented and parameterized; deterministic-first retrieval path in place.

- Readiness by operation type
  - New-article INSERT: ready.
  - Article-level MODIFY/DELETE/REPLACE: ready.
  - Intra-article INSERT (alinéa/word-level): base selection ready; applier still needs token-level anchors and sentence-scoped insertion/replace.
  - Small-token REPLACE (after REWRITE): needs strengthening.

## Consolidated action plan

- Decomposer:
  - Ordinal alinéa → structural REWRITE with `alinéa_index` (071).
  - Token micro-insert → `after_word`/`before_word` hints (058 a)).
  - Small-token REPLACE → sentence-span hint (054, 055).
- Applier (Reconstruction):
  - Paragraph-level structural REWRITE; token-level insertions; sentence-scoped REPLACE fallback.
  - Intra-article INSERT operates on retrieved original (056, 058 a)).
- Retriever/pipeline:
  - INSERT existence probe to distinguish intra-article insert vs new article for Step 3 base text selection.
- Runner:
  - SKIP on OTHER; show success and op counts.
- Resolver:
  - Guarded QA retry after large-reduction carve IMPLEMENTED (configurable `qa_retry_window_chars`, default 300). Deterministic-first retrieval preferred; EU LLM file matching used as fallback.
