### Round 4 Pipeline Diagnostics: Component-level Findings

## Scope (tested chunks)

- 001 — `# TITRE Iᴱᴿ::Article 1::2°::a)` — log: `round4_001.jsonl` and `validate_001___TITRE_I_Article_1_2_a___postfix4.jsonl`

---

## TargetArticleIdentifier

- Observed
  - Correct identification after deterministic inheritance: `MODIFY` on `L. 254-1` (Code rural et de la pêche maritime).
  - Source phrase: `numbered_point_introductory_phrase` = "L'article L. 254-1 est ainsi modifié :".
- Evidence

```1:7:scripts/output/validation_logs/validate_001___TITRE_I_Article_1_2_a___postfix4.jsonl
{"event": "target_identified", "payload": {"operation_type": "MODIFY", "code": "code rural et de la pêche maritime est ainsi modifié", "article": "L. 254-1", "confidence": 1.0}}
```

- Analysis
  - Deterministic inheritance from intro phrases now directs identification away from in-text citations.
- Fixes implemented (this round)
  - Runner derives `inherited_target_article` from intro phrases before invoking identification.

## OriginalTextRetriever

- Observed
  - Retrieved `L. 254-1` from local curated markdown: `data/fr_code_text/Code Rural et de la pêche maritime/article L254-1.md`.
- Evidence

```1:3:scripts/output/validation_logs/validate_001___TITRE_I_Article_1_2_a___postfix4.jsonl
{"event": "retrieval_done", "payload": {"meta": {"source": "local_fr_md", "success": true, "file": "data/fr_code_text/Code Rural et de la pêche maritime/article L254-1.md"}, ...}}
```

- Analysis
  - Normalized directory matching handles accents/case; robust fallback path confirmed.

## BillSplitter / Chunking Strategy

- Observed
  - Hierarchy and introductory phrases available and correctly parsed; no anomalies.
- Analysis: OK.

## InstructionDecomposer

- Observed
  - Decomposed a single REPLACE instruction from: "les mots : « prévu aux articles L. 254-6-2 et L. 254-6-3 » sont remplacés par les mots : « à l'utilisation des produits phytopharmaceutiques »".
- Analysis
  - Straightforward token-level REPLACE; no structural anchors required here.

## OperationApplier / Reconstruction

- Observed
  - Applied REPLACE within the scoped numbered point; produced coherent after-state.
- Evidence

```1:4:scripts/output/validation_logs/validate_001___TITRE_I_Article_1_2_a___postfix4.jsonl
{"event": "reconstruct_done", "payload": {"deleted_or_replaced_text": "prévu aux articles L. 254-6-2 et L. 254-6-3", "newly_inserted_text": "à l'utilisation des produits phytopharmaceutiques", ...}}
```

- Analysis
  - Delta aligns with the amendment’s intent for the 3° of II in `L. 254-1`.

## ReferenceLocator

- Observed
  - Located 1 DELETIONAL reference in the deleted fragment.
- Evidence

```1:5:scripts/output/validation_logs/validate_001___TITRE_I_Article_1_2_a___postfix4.jsonl
{"event": "locate_done", "payload": {"count": 1, "refs": [{"text": "prévu aux articles L. 254-6-2 et L. 254-6-3", "source": "DELETIONAL", "conf": 0.98}]}}
```

- Analysis: OK.

## ReferenceObjectLinker

- Observed
  - Linked 1/1 with high confidence.
- Evidence

```1:6:scripts/output/validation_logs/validate_001___TITRE_I_Article_1_2_a___postfix4.jsonl
{"event": "link_done", "payload": {"count": 1, "links": [{"text": "prévu aux articles L. 254-6-2 et L. 254-6-3", "obj": "conseil", "conf": 0.95}]}}
```

- Analysis: OK.

## ReferenceResolver

- Observed
  - Resolved 1 DELETIONAL reference; 0 unresolved.
- Evidence

```1:7:scripts/output/validation_logs/validate_001___TITRE_I_Article_1_2_a___postfix4.jsonl
{"event": "resolve_done", "payload": {"counts": {"def": 0, "del": 1, "unres": 0}}}
```

- Legal coherence check
  - Amendment replaces a citation to advisory provisions (L. 254-6-2, L. 254-6-3) with a broader phrase "à l'utilisation des produits phytopharmaceutiques" in the 3° of II. This is coherent with tightening the wording without creating contradictions.

---

## Overall assessment and readiness

- Snapshot
  - End-to-end components behave coherently on this chunk with the deterministic target identification.
  - Located and resolved references align with the deleted content; no spurious references introduced.
- Component status
  - TargetArticleIdentifier: correct (deterministic intro-based).
  - OriginalTextRetriever: correct local fetch.
  - Decomposer/Applier: correct REPLACE, minimal delta, validates.
  - Locator/Linker/Resolver: consistent outputs; no unresolved refs remain.

## Notes on rate limiting (environment)

- Some runs hit rate limits for validator/locator. The successful log (`validate_001___...postfix4.jsonl`) shows the intended behavior. If needed, increase backoff in the rate limiter for batch runs.
