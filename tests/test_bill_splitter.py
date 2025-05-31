import pytest
from bill_parser_engine.core.reference_resolver.bill_splitter import BillSplitter
from bill_parser_engine.core.reference_resolver.models import BillChunk

import pathlib

@pytest.fixture(scope="module")
def bill_text():
    # Load the legislative bill example
    path = pathlib.Path("data/legislative_bill_example.md")
    return path.read_text(encoding="utf-8")

def test_article_1_numbered_points(bill_text):
    splitter = BillSplitter()
    chunks = splitter.split(bill_text)
    # Find Article 1 chunks
    article_1_chunks = [c for c in chunks if c.article_label.startswith("Article 1")]
    # Article 1 should have several numbered points (1°, 2°, 3°, 3° bis, etc.)
    labels = [c.numbered_point_label for c in article_1_chunks]
    assert "2°" in labels
    assert any("3° bis" in (lbl or "") for lbl in labels)
    # Check that the introductory phrase is correct
    for c in article_1_chunks:
        assert c.article_introductory_phrase.startswith("Le code rural")
    # Check that chunk text is not empty
    for c in article_1_chunks:
        assert c.text.strip()

def test_article_2_major_subdivisions_and_points(bill_text):
    splitter = BillSplitter()
    chunks = splitter.split(bill_text)
    # Find Article 2, I. numbered points
    article_2_i_chunks = [c for c in chunks if c.article_label.startswith("Article 2") and c.major_subdivision_label == "I"]
    # Should have at least one numbered point in I.
    assert any(c.numbered_point_label for c in article_2_i_chunks)
    # Check that major_subdivision_introductory_phrase is present
    for c in article_2_i_chunks:
        assert c.major_subdivision_introductory_phrase.startswith("Le code de la santé publique") or c.major_subdivision_introductory_phrase == ""

def test_article_4_only_major_subdivisions(bill_text):
    splitter = BillSplitter()
    chunks = splitter.split(bill_text)
    # Article 4 has only major subdivisions (I, II, III)
    article_4_chunks = [c for c in chunks if c.article_label.startswith("Article 4")]
    # Should have 3 chunks, one for each subdivision
    ms_labels = [c.major_subdivision_label for c in article_4_chunks]
    assert set(ms_labels) >= {"I", "II", "III"}
    # None of these should have numbered_point_label
    for c in article_4_chunks:
        assert c.numbered_point_label is None

def test_article_with_no_points_or_subdivisions():
    # Minimal fake example
    text = """
# TITRE I
## Article 99
Ceci est un article sans subdivisions ni points.
"""
    splitter = BillSplitter()
    chunks = splitter.split(text)
    assert len(chunks) == 1
    c = chunks[0]
    assert c.article_label == "Article 99"
    assert c.text.strip().startswith("Ceci est un article")
    assert c.numbered_point_label is None
    assert c.major_subdivision_label is None

def test_numbered_point_range_supprimes(bill_text):
    splitter = BillSplitter()
    chunks = splitter.split(bill_text)
    # Article 3, I. should have a chunk with numbered_point_label_raw '1° à 3°'
    range_chunk = next((c for c in chunks if c.article_label.startswith("Article 3") and c.numbered_point_label_raw and "1° à 3°" in c.numbered_point_label_raw), None)
    assert range_chunk is not None
    assert "Supprimé" in range_chunk.text or "Supprimés" in range_chunk.text or (range_chunk.numbered_point_label_raw and "Supprimé" in range_chunk.numbered_point_label_raw)

def test_special_markings_nouveau_supprime(bill_text):
    splitter = BillSplitter()
    chunks = splitter.split(bill_text)
    # Find a chunk with (nouveau) in the raw label
    nouveau_chunk = next((c for c in chunks if c.numbered_point_label_raw and "(nouveau)" in c.numbered_point_label_raw), None)
    assert nouveau_chunk is not None
    # Find a chunk with (Supprimé) in the text or raw label
    supprime_chunk = next((c for c in chunks if (c.numbered_point_label_raw and "Supprimé" in c.numbered_point_label_raw) or ("Supprimé" in c.text)), None)
    assert supprime_chunk is not None

def test_lettered_subpoints_included(bill_text):
    splitter = BillSplitter()
    chunks = splitter.split(bill_text)
    # Article 1, 2° has lettered subpoints a), b), etc.
    chunk = next((c for c in chunks if c.article_label.startswith("Article 1") and c.numbered_point_label == "2°"), None)
    assert chunk is not None
    # Should contain 'a)' and 'b)' in the text
    assert "a)" in chunk.text
    assert "b)" in chunk.text 