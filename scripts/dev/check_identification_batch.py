import json
import re
from pathlib import Path
from typing import Optional

from bill_parser_engine.core.reference_resolver.models import BillChunk, TargetArticle, TargetOperationType
from bill_parser_engine.core.reference_resolver.target_identifier import TargetArticleIdentifier


def extract_article_from_intro(intro: str) -> Optional[str]:
    s = (intro or "").replace("’", "'")
    m = re.search(r"(?i)\b([LRD]\.\s*\d[\d\-]*)\b", s)
    if not m:
        return None
    return re.sub(r"\s+", " ", m.group(1).strip())


def main() -> None:
    root = Path("scripts/output/duplomb_chunks_all")
    files = sorted(root.glob("*.json"))
    tested = 0
    ok = 0
    mismatches = []

    for jf in files[:50]:  # limit to first 50 for speed
        meta = json.loads(jf.read_text(encoding="utf-8"))
        intro = meta.get("numbered_point_introductory_phrase") or meta.get("article_introductory_phrase") or ""
        art = extract_article_from_intro(intro)
        if not art:
            continue
        text_path = jf.with_suffix(".txt")
        if not text_path.exists():
            continue
        text = text_path.read_text(encoding="utf-8")
        bc = BillChunk(
            text=text,
            titre_text=meta.get("titre_text", ""),
            article_label=meta.get("article_label", ""),
            article_introductory_phrase=meta.get("article_introductory_phrase"),
            major_subdivision_label=meta.get("major_subdivision_label"),
            major_subdivision_introductory_phrase=meta.get("major_subdivision_introductory_phrase"),
            numbered_point_label=meta.get("numbered_point_label"),
            numbered_point_introductory_phrase=meta.get("numbered_point_introductory_phrase"),
            lettered_subdivision_label=meta.get("lettered_subdivision_label"),
            hierarchy_path=meta.get("hierarchy_path", []),
            chunk_id=meta.get("chunk_id", "::".join(meta.get("hierarchy_path", []))),
            start_pos=meta.get("start_pos", 0),
            end_pos=meta.get("end_pos", 0),
            target_article=None,
            inherited_target_article=TargetArticle(
                operation_type=TargetOperationType.MODIFY,
                code=None,
                article=art,
                confidence=1.0,
            ),
            structural_anchor_hint=meta.get("structural_anchor_hint"),
        )

        identifier = TargetArticleIdentifier(use_cache=False)
        res = identifier.identify(bc)
        tested += 1
        if (res.article or "").strip() == art:
            ok += 1
        else:
            mismatches.append((jf.name, art, res.article))

    print(f"Deterministic identification check: {ok}/{tested} matched")
    if mismatches:
        for name, expected, got in mismatches[:10]:
            print(f"- MISMATCH {name}: expected {expected}, got {got}")


if __name__ == "__main__":
    main()


