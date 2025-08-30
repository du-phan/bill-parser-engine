#!/usr/bin/env python3
import argparse
import json
from pprint import pprint
from typing import List, Tuple
from pathlib import Path

from dotenv import load_dotenv
from bill_parser_engine.core.reference_resolver.original_text_retriever import OriginalTextRetriever


def run_cases(cases: List[Tuple[str, str]]) -> None:
    retriever = OriginalTextRetriever(use_cache=False)
    for code, article in cases:
        print(f"\n=== {code} :: {article}")
        text, meta = retriever.fetch_article_text(code, article)
        pprint(meta)
        print("len=", len(text))
        preview = text[:200] + ("…" if len(text) > 200 else "")
        print("preview=", preview)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run OriginalTextRetriever on code/article pairs")
    parser.add_argument("--pair", action="append", help="Pair as JSON: {\"code\":...,\"article\":...}")
    args = parser.parse_args()

    # Load env for Legifrance and Mistral
    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env.local")
    load_dotenv(project_root / ".env")

    cases: List[Tuple[str, str]] = []
    if args.pair:
        for p in args.pair:
            obj = json.loads(p)
            cases.append((obj["code"], obj["article"]))
    else:
        # Default historically problematic patterns
        cases = [
            ("code de l'environnement", "L. 1 A"),
            ("code rural et de la pêche maritime", "L. 253-1-1"),
            ("code de l'environnement", "L. 211-1-2"),
        ]

    run_cases(cases)


if __name__ == "__main__":
    main()


