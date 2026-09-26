from __future__ import annotations

import shutil
from pathlib import Path

import pymupdf
import pytest

from upsc_notes.config import OCRConfig
from upsc_notes.ingest import extract_document
from upsc_notes.ingest.textdoc import is_generic_heading, parse_markdown, parse_text
from upsc_notes.segment import segment_document


def _magazine_pdf(path: Path) -> Path:
    """Two pages, running header/footer, big article titles, bold sub-headings and bullets."""
    doc = pymupdf.open()
    articles = [
        ("India-Japan Annual Summit 2026", ["India and Japan held the 16th annual summit in Tokyo.", "The leaders signed 12 agreements on defence and trade."]),
        ("Special Intensive Revision of Electoral Rolls", ["The Election Commission ordered an SIR in 12 States.", "Article 324 gives the Commission superintendence of elections."]),
    ]
    for n, (title, bullets) in enumerate(articles, 1):
        page = doc.new_page(width=595, height=842)
        page.insert_text((40, 30), "Monthly Magazine (August 2026)", fontsize=9)
        page.insert_text((290, 820), str(n), fontsize=9)
        page.insert_text((40, 90), title, fontsize=18, fontname="hebo")
        page.insert_text((40, 125), "Why in News?", fontsize=11, fontname="hebo")
        y = 145
        for b in bullets:
            page.insert_text((40, y), "•", fontsize=11)
            page.insert_text((56, y), b, fontsize=11)
            y += 18
        page.insert_text((40, y + 10), "Way Forward", fontsize=11, fontname="hebo")
        page.insert_text((40, y + 30), "Continued engagement is needed for regional stability and growth.", fontsize=11)
    doc.save(path)
    return path


def _two_column_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    for i in range(6):
        page.insert_text((40, 120 + 16 * i), f"Left column top line {i} about the first article text.", fontsize=10)
        page.insert_text((320, 120 + 16 * i), f"Right column top line {i} continues the first article.", fontsize=10)
    page.insert_text((150, 300), "FULL WIDTH SECTION BANNER ACROSS THE PAGE", fontsize=16, fontname="hebo")
    for i in range(6):
        page.insert_text((40, 360 + 16 * i), f"Left column bottom line {i} of the second part here.", fontsize=10)
        page.insert_text((320, 360 + 16 * i), f"Right column bottom line {i} of the second part here.", fontsize=10)
    doc.save(path)
    return path


def test_pdf_topics_headers_and_bullets(tmp_path):
    doc = extract_document(_magazine_pdf(tmp_path / "mag.pdf"), ocr=OCRConfig(engine="none"))
    md = doc.to_markdown()
    assert "Monthly Magazine" not in md, "running header should be removed"
    topics = [b.text for b in doc.blocks if b.kind == "heading" and b.role == "topic"]
    assert topics == ["India-Japan Annual Summit 2026", "Special Intensive Revision of Electoral Rolls"]
    bullets = [b.text for b in doc.blocks if b.kind == "bullet"]
    assert "India and Japan held the 16th annual summit in Tokyo." in bullets
    segs = segment_document(doc)
    assert [s.title for s in segs] == topics
    assert segs[1].page_start == 2
    assert "### Why in News?" in segs[0].text


def test_two_column_reading_order(tmp_path):
    doc = extract_document(_two_column_pdf(tmp_path / "cols.pdf"), ocr=OCRConfig(engine="none"))
    text = doc.to_markdown()
    order = [text.index(s) for s in ("Left column top line 0", "Right column top line 0", "FULL WIDTH SECTION BANNER", "Left column bottom line 0", "Right column bottom line 0")]
    assert order == sorted(order), text[:600]


def test_page_range(tmp_path):
    doc = extract_document(_magazine_pdf(tmp_path / "mag.pdf"), ocr=OCRConfig(engine="none"), pages="2")
    assert doc.pages_processed == [2]
    assert [b.text for b in doc.blocks if b.role == "topic"] == ["Special Intensive Revision of Electoral Rolls"]


def test_markdown_compilation_vs_single_topic():
    compilation = "# August 2026\n\n## Topic A\ntext a\n\n### Background\nmore\n\n## Topic B\ntext b\n"
    roles = [(b.text, b.role) for b in parse_markdown(compilation) if b.kind == "heading"]
    assert roles == [("August 2026", "section"), ("Topic A", "topic"), ("Background", "subheading"), ("Topic B", "topic")]
    single = "# Gaganyaan\n\n## Background\nx\n\n## Significance\ny\n"
    blocks = parse_markdown(single)
    assert not any(b.role == "topic" for b in blocks)


def test_plain_text_and_generic_headings():
    blocks = parse_text("GAGANYAAN MISSION\n\nISRO completed a test.\n\nWay Forward\n\nMore tests needed.\n")
    assert blocks[0].kind == "heading" and blocks[0].text == "GAGANYAAN MISSION"
    assert is_generic_heading("Way Forward") and is_generic_heading("1.2 Why in News?")
    assert not is_generic_heading("Gaganyaan Mission")


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
def test_image_ocr_detects_headline(tmp_path):
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1400, 700), "white")
    d = ImageDraw.Draw(img)
    try:
        big = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 64)
        small = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 28)
    except OSError:
        pytest.skip("system fonts unavailable")
    d.text((40, 40), "Monsoon Floods in Assam", font=big, fill="black")
    for i, line in enumerate([
        "Heavy rainfall caused the Brahmaputra to rise above the danger mark.",
        "The State Disaster Management Authority evacuated 40,000 people.",
        "NDMA guidelines recommend early warning systems for floods.",
    ]):
        d.text((40, 170 + 50 * i), line, font=small, fill="black")
    path = tmp_path / "clip.png"
    img.save(path)
    doc = extract_document(path, ocr=OCRConfig())
    md = doc.to_markdown()
    assert "Brahmaputra" in md
    segs = segment_document(doc)
    assert len(segs) == 1 and "Monsoon Floods" in segs[0].title
