"""High-quality PDF extraction with Docling (IBM, MIT licence), combined with our style analysis.

Docling's layout and table models give the best body text: correct reading order, real tables
(including Word "layout tables" that PyMuPDF scrambles), clean lists, headers/footers removed.
It does not rank headings, though — every heading comes out at one level. So we run the fast
PyMuPDF style analysis on the same pages and use it to decide which Docling headings are
article titles (topics), which are section banners, and which are sub-headings.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Callable, Optional

from rapidfuzz import fuzz

from ..config import OCRConfig
from ..models import Block, ExtractedDocument
from .layout import build_document
from .textdoc import is_generic_heading

log = logging.getLogger(__name__)
_CONVERTERS: dict[bool, object] = {}


def docling_available() -> bool:
    try:
        import docling  # noqa: F401
    except ImportError:
        return False
    return True


def _converter(ocr: bool):
    if ocr not in _CONVERTERS:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        opts = PdfPipelineOptions(do_ocr=ocr, do_table_structure=True)
        opts.table_structure_options.do_cell_matching = True
        if ocr:
            opts.ocr_options = _best_ocr_options()
        from docling.document_converter import ImageFormatOption

        _CONVERTERS[ocr] = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=opts),
                InputFormat.IMAGE: ImageFormatOption(pipeline_options=opts),
            }
        )
    return _CONVERTERS[ocr]


def _best_ocr_options():
    """Apple Vision OCR on macOS (most accurate free option there), else Tesseract."""
    import sys

    from docling.datamodel.pipeline_options import TesseractCliOcrOptions

    if sys.platform == "darwin":
        try:
            import ocrmac  # noqa: F401
            from docling.datamodel.pipeline_options import OcrMacOptions

            return OcrMacOptions(force_full_page_ocr=True, lang=["en-US"])
        except ImportError:
            pass
    return TesseractCliOcrOptions(force_full_page_ocr=True)


def image_document(path: Path, name: str, progress=None) -> ExtractedDocument:
    """Images (clippings, screenshots): Docling layout + OCR; headings become topics unless generic."""
    blocks = docling_blocks(path, [1], ocr=True, progress=progress, single=True)
    _heading_roles_without_styles(blocks)
    return ExtractedDocument(source_name=name, kind="image", page_count=1, pages_processed=[1], blocks=blocks,
                             style_roles_by="docling", ocr_pages=[1])


def _heading_roles_without_styles(blocks: list[Block]) -> None:
    """No font information (OCR): numbered "1.1" titles are articles when present; ALL-CAPS/dates are banners."""
    heads = [b for b in blocks if b.kind == "heading"]
    numbered = [b for b in heads if re.match(r"^\(?\d{1,2}\.\d{1,2}\.?\s", b.text)]
    for b in heads:
        letters = [c for c in b.text if c.isalpha()]
        upper = bool(letters) and sum(c.isupper() for c in letters) / len(letters) > 0.8
        dateish = bool(re.fullmatch(r"[A-Za-z]+\s+\d{4}", b.text.strip()))
        if is_generic_heading(b.text):
            b.role = "subheading"
        elif numbered:
            b.role = "topic" if b in numbered else ("section" if upper or dateish else "subheading")
        else:
            b.role = "section" if (upper or dateish) and len(heads) > 1 else "topic"
        b.size = {"section": 20.0, "topic": 16.0}.get(b.role, 12.0)


def _norm(t: str) -> str:
    t = re.sub(r"^\(?\d{1,2}(\.\d{1,2})*[.)]?\s+", "", t)
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", t.lower())).strip()


def _page_of(item) -> Optional[int]:
    prov = getattr(item, "prov", None)
    return prov[0].page_no if prov else None


def docling_blocks(path: Path, pages: list[int], *, ocr: bool, progress=None, chunk: int = 10, single: bool = False) -> list[Block]:
    """Convert the given pages with Docling, in chunks (for progress + memory)."""
    from docling_core.types.doc import DocItemLabel

    conv = _converter(ocr)
    blocks: list[Block] = []
    ranges: list[tuple[int, int]] = []
    start = prev = pages[0]
    for p in pages[1:]:
        if p != prev + 1 or p - start >= chunk:
            ranges.append((start, prev))
            start = p
        prev = p
    ranges.append((start, prev))
    done = 0
    for a, b in ranges:
        if progress:
            progress(done, len(pages), f"Reading pages {a}–{b} with Docling")
        res = conv.convert(str(path), raises_on_error=False) if single else conv.convert(str(path), page_range=(a, b), raises_on_error=False)
        doc = res.document
        for item, level in doc.iterate_items():
            label = item.label
            page = _page_of(item)
            if label in (DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER, DocItemLabel.PICTURE):
                continue
            if label == DocItemLabel.TABLE:
                md = item.export_to_markdown(doc=doc).strip()
                if md:
                    blocks.append(Block(kind="table", text=md, page=page))
                continue
            text = re.sub(r"\s+", " ", getattr(item, "text", "") or "").strip()
            if not text:
                continue
            if label in (DocItemLabel.SECTION_HEADER, DocItemLabel.TITLE):
                blocks.append(Block(kind="heading", text=text, page=page, role="subheading", style="docling"))
            elif label == DocItemLabel.LIST_ITEM:
                depth = max(0, (level or 1) - 1) if isinstance(level, int) else 0
                blocks.append(Block(kind="bullet", text=re.sub(r"^[•●○◦▪\-–]\s*", "", text), page=page, depth=min(depth, 3)))
            else:
                blocks.append(Block(kind="paragraph", text=text, page=page))
        done += b - a + 1
    if progress:
        progress(len(pages), len(pages), "Docling extraction done")
    return blocks


def _score(a: str, b: str) -> float:
    score = fuzz.ratio(a, b)
    if min(len(a), len(b)) / max(len(a), len(b), 1) >= 0.8:
        score = max(score, fuzz.partial_ratio(a, b) - 3)
    return score


def assign_roles(blocks: list[Block], reference: ExtractedDocument) -> tuple[int, int]:
    """Give Docling blocks the article/section roles found by style analysis, in document order.

    Titles Docling split over several items are matched by joining up to 3 consecutive items;
    titles Docling missed entirely are inserted at their page, so no article is ever lost.
    Returns (matched, inserted)."""
    anchors = [
        (b.page, _norm(b.text), b.role, b.size, b.text)
        for b in reference.blocks
        if b.kind == "heading" and b.role in ("topic", "section")
    ]
    if not anchors:  # no text layer (scanned PDF): infer roles from the heading texts
        _heading_roles_without_styles(blocks)
        return 0, 0
    matched = 0
    placed: dict[int, int] = {}  # anchor index -> block index
    nxt, i = 0, 0
    while i < len(blocks) and nxt < len(anchors):
        blk = blocks[i]
        if blk.kind not in ("heading", "paragraph") or len(blk.text) > 260:
            i += 1
            continue
        hit = None
        for a in range(nxt, min(nxt + 3, len(anchors))):
            page, text, role, size, _ = anchors[a]
            if page and blk.page and abs(page - blk.page) > 1:
                continue
            joined = ""
            for span in range(1, 4):
                j = i + span - 1
                if j >= len(blocks) or blocks[j].kind not in ("heading", "paragraph"):
                    break
                joined = (joined + " " + _norm(blocks[j].text)).strip()
                if _score(joined, text) >= 88:
                    hit = (a, span)
                    break
                if len(joined) > len(text) + 10:
                    break
            if hit:
                break
        if not hit:
            i += 1
            continue
        a, span = hit
        page, text, role, size, original = anchors[a]
        blk.kind, blk.role, blk.size, blk.style = "heading", role, size, f"ref:{role}"  # type: ignore[assignment]
        if span > 1:
            blk.text = original
            del blocks[i + 1 : i + span]
        placed[a] = i
        matched += 1
        nxt = a + 1
        i += 1
    # Insert titles Docling dropped, just before the first block on their page after the previous title.
    inserted = 0
    for a, (page, text, role, size, original) in enumerate(anchors):
        if a in placed:
            continue
        prev_idx = max((placed[k] for k in placed if k < a), default=-1)
        pos = next((k for k in range(prev_idx + 1, len(blocks)) if (blocks[k].page or 0) >= (page or 0)), len(blocks))
        blocks.insert(pos, Block(kind="heading", text=original, page=page, role=role, size=size, style=f"ref:{role}"))  # type: ignore[arg-type]
        placed = {k: (v + 1 if v >= pos else v) for k, v in placed.items()}
        placed[a] = pos
        inserted += 1
    return matched, inserted


def extract_pdf_docling(
    path: Path,
    pages: list[int],
    page_count: int,
    reference: ExtractedDocument,
    *,
    ocr: OCRConfig,
    progress: Optional[Callable[[int, int, str], None]] = None,
) -> ExtractedDocument:
    """Docling body text + reference (PyMuPDF) heading roles."""
    use_ocr = bool(reference.ocr_pages) or not any(b.kind != "heading" for b in reference.blocks)
    blocks = docling_blocks(path, pages, ocr=use_ocr and ocr.engine != "none", progress=progress)
    matched, inserted = assign_roles(blocks, reference)
    ref_topics = sum(1 for b in reference.blocks if b.kind == "heading" and b.role == "topic")
    doc = ExtractedDocument(
        source_name=reference.source_name,
        kind="pdf",
        page_count=page_count,
        pages_processed=pages,
        blocks=blocks,
        styles=reference.styles,
        style_roles_by=f"docling + {reference.style_roles_by}",
        ocr_pages=reference.ocr_pages,
        warnings=list(reference.warnings),
    )
    doc.warnings.append(
        f"Extracted with Docling; matched {matched} article/section headings to the style analysis"
        + (f"; {inserted} title(s) Docling missed were restored" if inserted else "")
        + (f"; {ref_topics} article titles expected" if ref_topics else "")
        + "."
    )
    return doc
