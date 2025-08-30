"""
In-memory registry for newly created articles during bill processing.

Used to satisfy references to articles inserted earlier in the same bill run
before they exist in the local store on disk.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, Tuple, Optional


class NewArticleRegistry:
    def __init__(self) -> None:
        self._map: Dict[Tuple[str, str], str] = {}

    def _norm(self, s: str) -> str:
        t = unicodedata.normalize("NFKD", (s or "").strip().lower())
        t = "".join(ch for ch in t if not unicodedata.combining(ch))
        t = t.replace("’", "'").replace("\u2019", "'")
        t = re.sub(r"\s+", " ", t)
        return t

    def _norm_code(self, code: str) -> str:
        t = self._norm(code)
        t = re.sub(r"^code\s+", "", t)
        return t

    def _norm_article(self, article: str) -> str:
        # Normalize similar to local-file matching: collapse spaces/dots and hyphens
        raw = (article or "").strip()
        v = re.sub(r"\s*\.\s*", ".", raw)  # tidy dots like 'L. 254-1'
        v = re.sub(r"\s*-\s*", "-", v)
        v = re.sub(r"([0-9])\s+([A-Z])$", r"\1\2", v)  # '1 A' -> '1A'
        return self._norm(v)

    def set_text(self, code: str, article: str, text: str) -> None:
        key = (self._norm_code(code), self._norm_article(article))
        self._map[key] = text or ""

    def get_text(self, code: str, article: str) -> Optional[str]:
        key = (self._norm_code(code), self._norm_article(article))
        return self._map.get(key)

    def __len__(self) -> int:  # pragma: no cover
        return len(self._map)


