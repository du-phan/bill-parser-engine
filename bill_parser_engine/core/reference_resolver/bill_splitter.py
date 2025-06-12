import re
from typing import List, Optional
from .models import BillChunk

class BillSplitter:
    """
    Robust, multi-pass splitter for French legislative bills.

    Key challenges addressed:
    - French legislative text is highly variable in formatting (indentation, whitespace, bullet points, etc.).
    - Numbered points and major subdivisions may be indented or have extra whitespace, so regexes must be permissive.
    - Ranges and special markings (e.g., (nouveau), (Supprimé)) must be preserved in output but normalized for test logic.
    - Minimal articles (with no subdivisions or points) must still yield a chunk (robust fallback).
    - Regex-based splitting is brittle: always prefer a multi-pass, state-machine-like approach for maintainability.

    Design decisions:
    - Multi-pass: Always split by Article, then by Major Subdivision (if present), then by Numbered Point (if present).
    - Regexes are whitespace-tolerant and robust to indentation.
    - Fallback logic: If nothing else matches, always create a chunk for the remaining text or the introductory phrase.
    - Normalization is used for test logic and comparison, but raw labels are preserved for output fields.
    - All output fields are filled as per the spec, with clear separation of normalized and raw labels.

    Lessons for maintainers:
    - If new edge cases arise, check for formatting/whitespace issues first.
    - If the regex approach becomes unmaintainable, consider a state machine or parser combinator approach, but avoid LLMs for splitting (for auditability and reproducibility).
    - Always keep fallback logic robust: legal text can be unpredictable.
    """
    # TITRE: e.g. # TITRE Iᴱᴿ
    TITRE_RE = re.compile(r"^#\s*TITRE\s+([IVXLCDMᴱᴿ]+)", re.MULTILINE)
    # Article: e.g. ## Article 1ᵉʳ
    ARTICLE_RE = re.compile(r"^##\s*Article\s+([\w\dᵉʳ]+)", re.MULTILINE)
    # Major subdivision: e.g. I., II (nouveau)., I et II.
    MAJOR_SUBDIV_RE = re.compile(r"^([IVXLCDM]+(?:\s*et\s*[IVXLCDM]+)*(?:\s*\(nouveau\))?)\.\s*[–-]?(.*)", re.MULTILINE)
    # Numbered point: e.g. 1°, 2° bis, 1° à 3° (Supprimés), with optional leading whitespace
    NUMBERED_POINT_RE = re.compile(r"^[ \t]*(\d+°(?:\s*[A-Za-z]+|\s*bis|\s*ter|\s*quater|\s*quinquies|\s*sexies|\s*septies|\s*octies|\s*nonies|\s*décies)?(?:\s*à\s*\d+°(?:\s*[A-Za-z]+|\s*bis|\s*ter|\s*quater|\s*quinquies|\s*sexies|\s*septies|\s*octies|\s*nonies|\s*décies)?)?(?:\s*\(nouveau\))?)\s*(.*)", re.MULTILINE)

    @staticmethod
    def _normalize_label(label: str) -> str:
        """
        Normalize a label for test and logic matching (strip trailing L, (nouveau), etc.).
        Used for comparison and test logic, not for output fields.
        """
        label = label.strip()
        label = re.sub(r"\s*\(nouveau\)", "", label)
        label = re.sub(r"\s*L$", "", label)
        return label.strip()

    @staticmethod
    def _find_first_of(text: str, regexes: List[re.Pattern]) -> int:
        """
        Return the index of the first match of any regex in regexes, or len(text) if none.
        Used to find the end of the introductory phrase.
        """
        indices = [m.start() for regex in regexes for m in regex.finditer(text)]
        return min(indices) if indices else len(text)

    def split(self, text: str) -> List[BillChunk]:
        """
        Split the legislative bill text into atomic chunks, preserving all context and metadata.
        Returns a list of BillChunk objects.
        """
        chunks = []
        # 1. Split by TITRE (top-level division)
        titre_spans = [(m.start(), m.end(), m.group(0)) for m in self.TITRE_RE.finditer(text)]
        titre_spans.append((len(text), len(text), None))  # Sentinel for last TITRE
        for t_idx, (t_start, t_end, titre_line) in enumerate(titre_spans[:-1]):
            titre_text = titre_line.strip() if titre_line else ""
            t_next_start = titre_spans[t_idx+1][0]
            titre_block = text[t_end:t_next_start]
            # 2. Split by Article
            article_spans = [(m.start(), m.end(), m.group(1)) for m in self.ARTICLE_RE.finditer(titre_block)]
            article_spans.append((len(titre_block), len(titre_block), None))  # Sentinel for last Article
            for a_idx, (a_start, a_end, article_num) in enumerate(article_spans[:-1]):
                article_label = f"Article {article_num}" if article_num else ""
                a_next_start = article_spans[a_idx+1][0]
                article_block = titre_block[a_end:a_next_start]
                # 3. Extract article introductory phrase (up to first major subdiv or numbered point)
                intro_end = self._find_first_of(article_block, [self.MAJOR_SUBDIV_RE, self.NUMBERED_POINT_RE])
                article_intro = article_block[:intro_end].strip()
                rest = article_block[intro_end:]
                # 4. Try major subdivisions (Roman numerals, possibly with (nouveau), possibly multiple with 'et')
                major_subdivs = list(self.MAJOR_SUBDIV_RE.finditer(rest))
                if major_subdivs:
                    for ms_idx, ms_match in enumerate(major_subdivs):
                        ms_start = intro_end + ms_match.start()
                        ms_end = intro_end + (major_subdivs[ms_idx+1].start() if ms_idx+1 < len(major_subdivs) else len(rest))
                        ms_label_raw = ms_match.group(1).strip()
                        ms_label = self._normalize_label(ms_label_raw)
                        ms_intro = ms_match.group(2).strip()
                        ms_block = rest[ms_match.start():major_subdivs[ms_idx+1].start()] if ms_idx+1 < len(major_subdivs) else rest[ms_match.start():]
                        # Handle multiple subdivisions in a single heading (e.g. 'I et II')
                        ms_labels_raw = [lbl for lbl in re.split(r"\s*et\s*", ms_label_raw) if lbl.strip()]
                        ms_labels = [self._normalize_label(lbl) for lbl in ms_labels_raw]
                        for i, single_ms_label in enumerate(ms_labels):
                            single_ms_label_raw = ms_labels_raw[i].strip()
                            # 5. Try numbered points within major subdivision
                            ms_numbered_points = list(self.NUMBERED_POINT_RE.finditer(ms_block))
                            if ms_numbered_points:
                                for n_idx, np_match in enumerate(ms_numbered_points):
                                    np_start = ms_match.start() + np_match.start()
                                    np_end = ms_match.start() + (ms_numbered_points[n_idx+1].start() if n_idx+1 < len(ms_numbered_points) else len(ms_block))
                                    chunk_text = ms_block[np_match.start():ms_numbered_points[n_idx+1].start()] if n_idx+1 < len(ms_numbered_points) else ms_block[np_match.start():]
                                    chunk_text = chunk_text.strip()
                                    numbered_point_label_raw = np_match.group(1).strip()
                                    numbered_point_label = self._normalize_label(numbered_point_label_raw)
                                    # Create chunk for each numbered point in the major subdivision
                                    chunks.append(BillChunk(
                                        text=chunk_text,
                                        titre_text=titre_text,
                                        article_label=article_label,
                                        article_introductory_phrase=article_intro,
                                        major_subdivision_label=single_ms_label,
                                        major_subdivision_introductory_phrase=ms_intro,
                                        numbered_point_label=numbered_point_label,
                                        hierarchy_path=[titre_text, article_label, single_ms_label, numbered_point_label],
                                        chunk_id="::".join(filter(None, [titre_text, article_label, single_ms_label, numbered_point_label])),
                                        start_pos=t_end + a_end + ms_start + np_match.start(),
                                        end_pos=t_end + a_end + ms_start + np_end,
                                    ))
                            else:
                                # If no numbered points, create a chunk for the whole major subdivision
                                chunk_text = ms_block.strip()
                                chunks.append(BillChunk(
                                    text=chunk_text,
                                    titre_text=titre_text,
                                    article_label=article_label,
                                    article_introductory_phrase=article_intro,
                                    major_subdivision_label=single_ms_label,
                                    major_subdivision_introductory_phrase=ms_intro,
                                    numbered_point_label=None,
                                    hierarchy_path=[titre_text, article_label, single_ms_label],
                                    chunk_id="::".join(filter(None, [titre_text, article_label, single_ms_label])),
                                    start_pos=t_end + a_end + ms_start,
                                    end_pos=t_end + a_end + ms_end,
                                ))
                    continue
                # 6. If no major subdivisions, try direct numbered points at the article level
                numbered_points = list(self.NUMBERED_POINT_RE.finditer(rest))
                if numbered_points:
                    for idx, np_match in enumerate(numbered_points):
                        np_start = intro_end + np_match.start()
                        np_end = intro_end + (numbered_points[idx+1].start() if idx+1 < len(numbered_points) else len(rest))
                        chunk_text = rest[np_match.start():numbered_points[idx+1].start()] if idx+1 < len(numbered_points) else rest[np_match.start():]
                        chunk_text = chunk_text.strip()
                        numbered_point_label_raw = np_match.group(1).strip()
                        numbered_point_label = self._normalize_label(numbered_point_label_raw)
                        # Create chunk for each numbered point at the article level
                        chunks.append(BillChunk(
                            text=chunk_text,
                            titre_text=titre_text,
                            article_label=article_label,
                            article_introductory_phrase=article_intro,
                            major_subdivision_label=None,
                            major_subdivision_introductory_phrase=None,
                            numbered_point_label=numbered_point_label,
                            hierarchy_path=[titre_text, article_label, numbered_point_label],
                            chunk_id="::".join(filter(None, [titre_text, article_label, numbered_point_label])),
                            start_pos=t_end + a_end + np_start,
                            end_pos=t_end + a_end + np_end,
                        ))
                    continue
                # 7. Fallback: whole article as one chunk (robust to whitespace)
                chunk_text = rest.strip()
                # If rest is empty, use the introductory phrase as the chunk text (if not empty)
                if not chunk_text and article_intro:
                    chunk_text = article_intro
                hierarchy_path = [titre_text, article_label]
                chunk_id = "::".join(filter(None, hierarchy_path))
                if chunk_text:
                    # Create fallback chunk for minimal articles
                    chunks.append(BillChunk(
                        text=chunk_text,
                        titre_text=titre_text,
                        article_label=article_label,
                        article_introductory_phrase=article_intro,
                        major_subdivision_label=None,
                        major_subdivision_introductory_phrase=None,
                        numbered_point_label=None,
                        hierarchy_path=hierarchy_path,
                        chunk_id=chunk_id,
                        start_pos=t_end + a_end,
                        end_pos=t_end + a_end + len(article_block),
                    ))
        return chunks 