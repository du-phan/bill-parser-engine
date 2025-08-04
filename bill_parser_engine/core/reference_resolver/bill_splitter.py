import re
from typing import List, Optional
from .models import BillChunk, TargetArticle, TargetOperationType

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
    - TARGET IDENTIFICATION: This component ONLY splits text. Target article identification is handled by TargetArticleIdentifier.

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
    # Numbered point: e.g. 1°, 2° bis, 1°A, 1° à 3° (Supprimés), with optional leading whitespace
    NUMBERED_POINT_RE = re.compile(r"^[ \t]*(\d+°[A-Z]?(?:\s*(?:bis|ter|quater|quinquies|sexies|septies|octies|nonies|décies))?(?:\s*à\s*\d+°[A-Z]?(?:\s*(?:bis|ter|quater|quinquies|sexies|septies|octies|nonies|décies))?)?(?:\s*\(nouveau\))?)\s*(.*)", re.MULTILINE)
    
    # NEW: Lettered subdivision pattern - handles a), b), aa), aaa), "aa, a et b)", etc.
    # This regex only captures the label, content extraction is handled separately
    LETTERED_SUBDIV_RE = re.compile(
        r"^[ \t]*([a-z]+\)|aaa\)|aa\)|[a-z]+(?:,\s*[a-z]+)*\s+et\s+[a-z]+\))(?:\s*\(nouveau\)|\s*\(Supprimés?\))?",
        re.MULTILINE | re.IGNORECASE
    )
    
    # NEW: Hyphenated sub-operation pattern (for complex lettered subdivisions)
    HYPHENATED_OPERATION_RE = re.compile(
        r"^[ \t]*[-–]\s*(.*)",
        re.MULTILINE
    )

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

    def _extract_code_from_article_intro(self, intro_text: str) -> Optional[str]:
        """Extract code name from article or major subdivision introductory phrase."""
        if not intro_text:
            return None
            
        # Patterns for different formats:
        # "Le code ... est ainsi modifié"
        # "I. – Le code ... est ainsi modifié :"
        # "Le titre V du livre II du code ... est ainsi modifié"
        code_patterns = [
            # Basic pattern: "Le code ... est ainsi modifié"
            r"Le\s+(code\s+[\w\s,\-'àâäéèêëïîôöùûüÿç]+?)\s+est\s+ainsi\s+modifié",
            r"Le\s+(code\s+[\w\s,\-'àâäéèêëïîôöùûüÿç]+?)\s+est\s+modifié",
            # Major subdivision pattern: "I. – Le code ... est ainsi modifié"
            r"[IVX]+\.\s*[–-]?\s*Le\s+(code\s+[\w\s,\-'àâäéèêëïîôöùûüÿç]+?)\s+est\s+ainsi\s+modifié",
            r"[IVX]+\.\s*[–-]?\s*Le\s+(code\s+[\w\s,\-'àâäéèêëïîôöùûüÿç]+?)\s+est\s+modifié",
            # Complex pattern: "Le titre ... du code ... est ainsi modifié"
            r"Le\s+[\w\s'àâäéèêëïîôöùûüÿç]+\s+du\s+(code\s+[\w\s,\-'àâäéèêëïîôöùûüÿç]+?)\s+est\s+ainsi\s+modifié",
            r"Le\s+[\w\s'àâäéèêëïîôöùûüÿç]+\s+du\s+(code\s+[\w\s,\-'àâäéèêëïîôöùûüÿç]+?)\s+est\s+modifié"
        ]
        
        for pattern in code_patterns:
            match = re.search(pattern, intro_text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def _create_inheritance_hint(self, text: str, code: Optional[str]) -> Optional[TargetArticle]:
        """
        Create a simple inheritance hint for target article identification.
        
        This is ONLY a hint for TargetArticleIdentifier to use. The actual identification
        is done by TargetArticleIdentifier using LLM analysis.
        
        Args:
            text: The text to analyze for inheritance hints
            code: The code context if available
            
        Returns:
            TargetArticle hint or None
        """
        if not text:
            return None
            
        # Simple patterns for inheritance hints only
        inheritance_patterns = [
            # Pattern 1: "L'article L. 254-1 est ainsi modifié"
            (r"L'article\s+(L\.\s*[\d\-]+)\s+est\s+ainsi\s+modifié", TargetOperationType.MODIFY),
            # Pattern 1b: "'article L. 254-1 est ainsi modifié" (missing leading L)
            (r"'article\s+(L\.\s*[\d\-]+)\s+est\s+ainsi\s+modifié", TargetOperationType.MODIFY),
            # Pattern 2: "Après l'article L. 254-1, il est inséré"
            (r"Après\s+l'article\s+(L\.\s*[\d\-]+),\s+il\s+est\s+inséré", TargetOperationType.INSERT),
            # Pattern 2b: "Après 'article L. 254-1, il est inséré" (missing leading L)
            (r"Après\s+'article\s+(L\.\s*[\d\-]+),\s+il\s+est\s+inséré", TargetOperationType.INSERT),
            # Pattern 3: "L'article L. 254-1 est abrogé"
            (r"L'article\s+(L\.\s*[\d\-]+)\s+est\s+abrogé", TargetOperationType.ABROGATE),
            # Pattern 3b: "'article L. 254-1 est abrogé" (missing leading L)
            (r"'article\s+(L\.\s*[\d\-]+)\s+est\s+abrogé", TargetOperationType.ABROGATE),
            # Pattern 4: "Les articles L. 254-6-2 et L. 254-6-3 sont abrogés"
            (r"Les\s+articles\s+(L\.\s*[\d\-]+).*sont\s+abrogés", TargetOperationType.ABROGATE),
        ]

        for pattern, operation_type in inheritance_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                # Clean up the article reference (remove extra spaces)
                article = re.sub(r'\s+', ' ', match.group(1).strip())
                return TargetArticle(
                    operation_type=operation_type,
                    code=code,  # Use provided code context
                    article=article
                )
        return None

    def _consolidate_hyphenated_operations(self, lettered_subdiv_content: str) -> str:
        """Consolidate hyphenated sub-operations into coherent text."""
        lines = lettered_subdiv_content.split('\n')
        consolidated_lines = []

        for line in lines:
            line = line.strip()
            if self.HYPHENATED_OPERATION_RE.match(line):
                # This is a hyphenated sub-operation
                operation_text = self.HYPHENATED_OPERATION_RE.match(line).group(1)
                consolidated_lines.append(operation_text)
            else:
                consolidated_lines.append(line)

        return ' '.join(filter(None, consolidated_lines))

    def _split_lettered_subdivisions(self, numbered_point_content: str, inherited_target: Optional[TargetArticle], 
                                   context_info: dict) -> List[BillChunk]:
        """Split numbered point content into lettered subdivision chunks."""
        lettered_subdivs = list(self.LETTERED_SUBDIV_RE.finditer(numbered_point_content))

        if not lettered_subdivs:
            # No lettered subdivisions - return numbered point as single chunk
            return self._create_single_numbered_point_chunk(numbered_point_content, inherited_target, context_info)

        chunks = []
        for idx, subdiv_match in enumerate(lettered_subdivs):
            # Extract lettered subdivision content
            subdiv_label = subdiv_match.group(1).strip()
            subdiv_start = subdiv_match.start()
            subdiv_end = lettered_subdivs[idx + 1].start() if idx + 1 < len(lettered_subdivs) else len(numbered_point_content)
            subdiv_content = numbered_point_content[subdiv_start:subdiv_end].strip()

            # Handle hyphenated sub-operations within lettered subdivisions
            subdiv_content = self._consolidate_hyphenated_operations(subdiv_content)

            # Build hierarchy path and chunk ID
            hierarchy_path = context_info['hierarchy_path'] + [subdiv_label]
            chunk_id = "::".join(filter(None, hierarchy_path))

            # Create chunk with inherited target article
            chunk = BillChunk(
                text=subdiv_content,
                titre_text=context_info['titre_text'],
                article_label=context_info['article_label'],
                article_introductory_phrase=context_info['article_introductory_phrase'],
                major_subdivision_label=context_info['major_subdivision_label'],
                major_subdivision_introductory_phrase=context_info['major_subdivision_introductory_phrase'],
                numbered_point_label=context_info['numbered_point_label'],
                numbered_point_introductory_phrase=context_info['numbered_point_introductory_phrase'],
                lettered_subdivision_label=subdiv_label,
                hierarchy_path=hierarchy_path,
                chunk_id=chunk_id,
                start_pos=context_info['base_start_pos'] + subdiv_start,
                end_pos=context_info['base_start_pos'] + subdiv_end,
                inherited_target_article=inherited_target
            )
            chunks.append(chunk)

        return chunks

    def _create_single_numbered_point_chunk(self, numbered_point_content: str, inherited_target: Optional[TargetArticle],
                                           context_info: dict) -> List[BillChunk]:
        """Create a single chunk for a numbered point without lettered subdivisions."""
        chunk = BillChunk(
            text=numbered_point_content.strip(),
            titre_text=context_info['titre_text'],
            article_label=context_info['article_label'],
            article_introductory_phrase=context_info['article_introductory_phrase'],
            major_subdivision_label=context_info['major_subdivision_label'],
            major_subdivision_introductory_phrase=context_info['major_subdivision_introductory_phrase'],
            numbered_point_label=context_info['numbered_point_label'],
            numbered_point_introductory_phrase=context_info['numbered_point_introductory_phrase'],
            lettered_subdivision_label=None,
            hierarchy_path=context_info['hierarchy_path'],
            chunk_id=context_info['chunk_id'],
            start_pos=context_info['base_start_pos'],
            end_pos=context_info['base_end_pos'],
            inherited_target_article=inherited_target
        )
        return [chunk]

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
                
                # Extract code information from article introductory phrase for inheritance
                article_code = self._extract_code_from_article_intro(article_intro)
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
                                # Extract code from major subdivision intro, fallback to article code
                                ms_code = self._extract_code_from_article_intro(ms_intro) or article_code
                                
                                for n_idx, np_match in enumerate(ms_numbered_points):
                                    np_start = ms_match.start() + np_match.start()
                                    np_end = ms_match.start() + (ms_numbered_points[n_idx+1].start() if n_idx+1 < len(ms_numbered_points) else len(ms_block))
                                    chunk_text = ms_block[np_match.start():ms_numbered_points[n_idx+1].start()] if n_idx+1 < len(ms_numbered_points) else ms_block[np_match.start():]
                                    chunk_text = chunk_text.strip()
                                    numbered_point_label_raw = np_match.group(1).strip()
                                    numbered_point_label = self._normalize_label(numbered_point_label_raw)
                                    numbered_point_intro = np_match.group(2).strip() if len(np_match.group(2).strip()) > 0 else None
                                    
                                    # Create inheritance hint for TargetArticleIdentifier
                                    inherited_target = self._create_inheritance_hint(numbered_point_intro or "", ms_code)
                                    
                                    # Phase 2: Split lettered subdivisions if present
                                    context_info = {
                                        'titre_text': titre_text,
                                        'article_label': article_label,
                                        'article_introductory_phrase': article_intro,
                                        'major_subdivision_label': single_ms_label,
                                        'major_subdivision_introductory_phrase': ms_intro,
                                        'numbered_point_label': numbered_point_label,
                                        'numbered_point_introductory_phrase': numbered_point_intro,
                                        'hierarchy_path': [titre_text, article_label, single_ms_label, numbered_point_label],
                                        'chunk_id': "::".join(filter(None, [titre_text, article_label, single_ms_label, numbered_point_label])),
                                        'base_start_pos': t_end + a_end + ms_start + np_match.start(),
                                        'base_end_pos': t_end + a_end + ms_start + np_end
                                    }
                                    
                                    lettered_chunks = self._split_lettered_subdivisions(chunk_text, inherited_target, context_info)
                                    chunks.extend(lettered_chunks)
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
                                    numbered_point_introductory_phrase=None,
                                    lettered_subdivision_label=None,
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
                        numbered_point_intro = np_match.group(2).strip() if len(np_match.group(2).strip()) > 0 else None
                        
                        # Create inheritance hint for TargetArticleIdentifier
                        inherited_target = self._create_inheritance_hint(numbered_point_intro or "", article_code)
                        
                        # Phase 2: Split lettered subdivisions if present
                        context_info = {
                            'titre_text': titre_text,
                            'article_label': article_label,
                            'article_introductory_phrase': article_intro,
                            'major_subdivision_label': None,
                            'major_subdivision_introductory_phrase': None,
                            'numbered_point_label': numbered_point_label,
                            'numbered_point_introductory_phrase': numbered_point_intro,
                            'hierarchy_path': [titre_text, article_label, numbered_point_label],
                            'chunk_id': "::".join(filter(None, [titre_text, article_label, numbered_point_label])),
                            'base_start_pos': t_end + a_end + np_start,
                            'base_end_pos': t_end + a_end + np_end
                        }
                        
                        lettered_chunks = self._split_lettered_subdivisions(chunk_text, inherited_target, context_info)
                        chunks.extend(lettered_chunks)
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
                        numbered_point_introductory_phrase=None,
                        lettered_subdivision_label=None,
                        hierarchy_path=hierarchy_path,
                        chunk_id=chunk_id,
                        start_pos=t_end + a_end,
                        end_pos=t_end + a_end + len(article_block),
                    ))
        return chunks 