# Round 3 Pipeline Diagnostics: Component-level Findings

Last updated: 2025-08-12

## Scope (tested chunks)

- 071 — `# TITRE IV::Article 8::III::11°::b)` — log: `resolve_round2_071_with_step8.jsonl`
- 072 — `# TITRE IV::Article 8::III::12°::a)` — log: `resolve_round2_072_microedit_fix_v6.jsonl`
- 060 — `# TITRE IV::Article 8::III::3°` — log: `resolve_round2_060_with_step8_v2.jsonl`
- 058 a) — `# TITRE IV::Article 8::III::2°::a)` — log: `resolve_round2_058a_microedit.jsonl`
- 041 — `# TITRE II::Article 4::I::1°::b)` — log: `resolve_round2_041.jsonl`
- 044 — `# TITRE II::Article 4::I::2°` — log: `resolve_round2_044_microedit.jsonl`

Additional prior confirmations: 069, 070, 043 (consistent with Round 2 doc after resolver improvements).

---

## TargetArticleIdentifier

- Observed
  - 071: MODIFY `L. 254-1` (correct)
  - 072: MODIFY `L. 251-14` (correct)
  - 060: INSERT `L. 250-5-1` (new article; correct)
- Analysis: Stable after deterministic intro-based overrides introduced earlier.
- Fixes: None required in this round.

## OriginalTextRetriever

- Observed
  - 071: Retrieved `L. 254-1` from Legifrance (success).
  - 072: Retrieved `L. 251-14` from Legifrance (success).
  - 060: Direct lookup for `L. 250-5-1` returned empty (expected for new article); hierarchical fallback carved `L. 250-5 → 1` and returned the carved text (used downstream by resolver and reconstruction). Labeled `insert_existing_article` for compatibility.
- Analysis: Behavior is correct. The hierarchical carve supports new-article INSERT workflows end-to-end.
- Fixes: None.

## BillSplitter / Chunking Strategy

- Observed: Structural anchors and hierarchy metadata present; no anomalies.
- Analysis: OK.
- Fixes: None.

## InstructionDecomposer

- Observed (this round)
  - 071: Emitted a single REWRITE for the ordinal alinéa (OK; validation VALID 0.95).
  - 072: Emitted REPLACE targeting the end of the sixth alinéa (OK; trivial delta).
  - 060: Emitted ADD for new article insertion (OK).
- New (Round 3)
  - Deterministic inference added for micro-edits of the form: “Après le/les mot(s) « X », la fin du Nᵉ alinéa est ainsi rédigée: « Y »”.
  - Emits `position_hint` with `after_word/after_words`, `token_action: replace_tail`, and `alinea_index` when present.
- Risks
  - LLM can still emit sparse hints for atypical phrasing; deterministic inference mitigates the common forms.

## OperationApplier / Reconstruction

- Observed
  - 071: REWRITE applied; VALID; success=True.
  - 072: REPLACE applied; VALID; success=True.
  - 060: ADD applied; VALID; success=True.
- New (Round 3)
  - Deterministic token-tail rewrite inside an alinéa implemented for REWRITE/REPLACE when `token_action: replace_tail` is present. If `alinea_index` is absent or token not found there, selects a unique/closest paragraph match.
- Analysis: 072 now applies deterministically (no LLM needed for the micro-edit). 058a (token insert) remains stable. 044 reconstruction applied; locator later hit rate limits (not related to reconstruction).

### Chunk-by-chunk legal assessment (new in Round 3)

- 072 (III 12° a)) — Micro-edit tail rewrite

  - Before: sixth alinéa context shows current tail; After: tail replaced by “de l’article L. 251-14.”
  - Deterministic path applied; Step 8 context present; annotation count 0 (no refs in delta). Coherent edit: points tail to L. 251-14.
  - Snippets (from logs):

    - Before (context excerpt):
      "En application du règlement (UE) 2016/2031 du 26 octobre 2016, dans le cadre des contrôles officiels sur les végétaux, produits végétaux et autres objets introd…"
    - After (context excerpt):
      "En application de l'article L. 251-14."

  - Side-by-side (expanded context):

Avant:

```text
… En application du règlement (UE) 2016/2031 du 26 octobre 2016, dans le cadre des contrôles officiels sur les végétaux, produits végétaux et autres objets …
```

Après:

```text
… En application de l'article L. 251-14.
```

- 058 a) (III 2° a)) — Token insert after “titre,”

  - Before: sentence containing “titre,”; After: “du II de l’article L. 201-4,” inserted immediately after.
  - One definitional ref to II of L. 201-4 resolved; coherent scope clarification.
  - Snippets:
    - After (inserted fragment): "du II de l'article L. 201-4,"

- 041 (I 1° b)) — Sentence ADD

  - Before: paragraph without duty to prefect; After: adds sentence about indices on prairies being communicated to the prefect.
  - No refs; reconstruction VALID; coherent additional obligation.
  - Snippets:
    - After (added sentence excerpt):
      "Lorsque les indices portent sur les prairies, ces informations sont également communiquées au représentant de l'État…"

- 015 (I 5° quater) — Sentence ADD to IV of L. 254-3

  - Before: IV without the certificate strategy module sentence.
  - After: adds “Pour la délivrance ou le renouvellement des certificats mentionnés au II, elle contient en outre un module spécifique d'aide à l'élaboration de la stratégie de l'exploitation agricole en matière d'utilisation de produits phytopharmaceutiques.”
  - Snippets:

    - After (added sentence excerpt):
      "Pour la délivrance ou le renouvellement des certificats mentionnés au II, elle contient en outre un module spécifique d'aide à l'élaboration de la stratégie de l'exploitation agricole…"

  - Side-by-side (expanded context):

Avant:

```text
IV. – A compter du 1er janvier 2019, la formation prévue pour la délivrance ou le renouvellement des certificats mentionnés aux I et II contient des modules spécifiques relatifs à l'exigence de sobriété dans l'usage des produits phytopharmaceutiques et aux alternatives disponibles, notamment en matière de biocontrôle.
```

Après:

```text
IV. – A compter du 1er janvier 2019, la formation prévue pour la délivrance ou le renouvellement des certificats mentionnés aux I et II contient des modules spécifiques relatifs à l'exigence de sobriété dans l'usage des produits phytopharmaceutiques et aux alternatives disponibles, notamment en matière de biocontrôle. Pour la délivrance ou le renouvellement des certificats mentionnés au II, elle contient en outre un module spécifique d'aide à l'élaboration de la stratégie de l'exploitation agricole en matière d'utilisation de produits phytopharmaceutiques.
```

- 044 (I 2°) — Tail DELETE (“la fin du III est supprimée”)

  - Before: paragraph showing the III with tail; After: tail removed.
  - Reconstruction VALID; locator hit rate limit, unrelated to reconstruction; change is coherent reduction of scope.

- 046 (TITRE III — Article 5, 1° c)) — IV INSERT (nouveau paragraphe)

  - Before: l'article ne comportait pas de IV.
  - After: un IV est ajouté, avec une référence définitionnelle au CRPM L. 1 A correctement résolue.
  - Snippets:

    - After (extrait): "IV. – Les études relatives à la gestion quantitative de l'eau prennent en compte les dispositions de l'article L. 1 A du code rural et de la pêche maritime[1]."

  - Side-by-side (expanded context):

Avant:

```text
(aucun IV avant l'amendement)
```

Après:

```text
IV. – Les études relatives à la gestion quantitative de l'eau prennent en compte les dispositions de l'article L. 1 A du code rural et de la pêche maritime[1].

À cette fin, elles intègrent une analyse des impacts socio-économiques des reco
```

## ReferenceLocator

- Observed
  - 071: 2 DEFINITIONAL.
  - 072: 0 (expected; innocuous phrasing change).
  - 060: 3 DEFINITIONAL.
- Analysis: OK.
- Fixes: None.

## ReferenceObjectLinker

- Observed
  - 071: 2/2 linked with high-confidence early exits; objects mapped plausibly.
  - 072: n/a (no references).
  - 060: 3/3 linked; objects identified: “agents”, “activités officielles”, “dispositions”.
- Analysis: OK; context narrowing works and time budget keeps latency bounded.
- Fixes: None.

## ReferenceResolver

- Observed (fresh runs with guarded retry + deterministic-first retrieval)
  - 060: Resolved “Art. L. 250-5-1.” using after-state; resolved “du II de l’article L. 201-4” after a large carve (92.3%) and single guarded QA retry; generic EU mention unresolved (expected).
  - 072: No references to resolve (consistent with locator output).
  - 071: For “mentionnés aux 1°, 2° et 5° du I de l'article L. 251-3”, subsection carve succeeded (3185 → 137), but QA extraction returned empty. Because the final carve step showed 0% reduction (137 → 137), the guarded retry did not trigger even though a prior carve was large. The second definitional ref resolved.
- Analysis
  - Guarded retry logic should consider total reduction relative to the original source length, not only the last carve step, to avoid missing legitimate retries when the final extraction window equals the prior carve.
  - Otherwise, resolution quality is sound; unresolved generic EU mentions are acceptable.
- Fixes (implemented)
  - Guarded retry now computes total reduction against original size; triggers when ≥ threshold (default 50%).

## LegalStateSynthesizer (Step 8)

- Observed
  - 060 (INSERT): BeforeState empty (correct); AfterState contains 2 annotations with markers [1], [2]; footnote rendering lists concise, source-tagged answers.
  - 072 (no refs): 0 annotations (correct).
  - 071: With one unresolved definitional ref, AfterState includes a single annotation for the resolved one (expected).
- Analysis
  - Deterministic matching suffices given Step 7 outputs. Added alinéa/token-based context extraction so MODIFY micro-edits always have before/after spans. Log previews are truncated; full spans kept in memory.
- Fixes
  - Addressed a Python regex escape warning (`\\s+`) in the permissive pattern builder.
  - Contextual spans: extracts Nᵉ alinéa and falls back to token-anchor span when needed.
  - Optional future enhancement: inline diff rendering of alinéa pair.

---

## Overall assessment and readiness

- Overall snapshot

  - Stable across tested chunks with the new Step 8 integrated. Resolver improvements removed the major blockers observed in Round 2, except for the nuanced retry trigger in 071.
  - The deterministic-first retrieval path plus single guarded retry delivers robust outcomes without overengineering.

- Component status

  - TargetArticleIdentifier: solid with deterministic overrides.
  - OriginalTextRetriever: robust; hierarchical fallback supports new-article INSERT.
  - InstructionDecomposer: acceptable; structural hints still recommended for token-level edits (future work).
  - OperationApplier/Validator: stable on tested set; future structural/token-level enhancements will increase determinism.
  - ReferenceLocator/ObjectLinker: good performance and accuracy.
  - ReferenceResolver: strong; refine retry trigger to consider total reduction.
  - LegalStateSynthesizer: implemented; outputs coherent, concise annotations aligned with legal expectations.

- Readiness by operation type
  - New-article INSERT: ready.
  - Article-level MODIFY/DELETE/REPLACE: ready.
  - Intra-article INSERT/word-level REPLACE: works with current fallback; determinism will further improve with future Decomposer/Applier enhancements.

## Consolidated action plan

- Resolver

  - Adjust retry trigger to use total reduction vs original length for the carve window (e.g., trigger when ≥ 50%), not only the last-step reduction.

- Decomposer (future)

  - Emit `position_hint` for ordinal alinéa, enumerated point inserts, and token micro-inserts (after/before word) with sentence-span hints.

- Applier (future)

  - Sentence-scoped REPLACE fallback; token-level insertion with Unicode-aware boundaries; paragraph/alinéa structural rewrites.

- Synthesizer (optional polish)
  - Support configurable inline rendering and punctuation-aware marker placement.
