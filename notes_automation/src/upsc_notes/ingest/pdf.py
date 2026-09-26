"""PDF → RawPages using PyMuPDF, with per-page OCR fallback for scanned pages."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

import pymupdf

from ..config import OCRConfig
from .layout import RawBlock, RawLine, RawPage, Span

log = logging.getLogger(__name__)

_TEXT_FLAGS = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES


def _table_markdown(page: pymupdf.Page) -> list[tuple[tuple, str]]:
    """Real data tables only; magazines built in Word often use page-sized layout tables."""
    try:
        found = page.find_tables()
    except Exception as exc:  # table detection is best-effort
        log.debug("find_tables failed on page %s: %s", page.number + 1, exc)
        return []
    out = []
    ph = page.rect.height
    for tb in found.tables:
        try:
            rows = tb.extract()
        except Exception:
            continue
        cells = [c for r in rows for c in r]
        filled = [c for c in cells if c and c.strip()]
        if tb.row_count < 2 or tb.col_count < 2 or not filled:
            continue
        empty_ratio = 1 - len(filled) / max(1, len(cells))
        longest = max(len(c) for c in filled)
        height = tb.bbox[3] - tb.bbox[1]
        if empty_ratio > 0.4 or longest > 400 or height > 0.8 * ph:
            continue
        md = _rows_to_markdown(rows)
        if md:
            out.append((tuple(tb.bbox), md))
    return out


def _rows_to_markdown(rows: list[list]) -> str:
    def cell(c) -> str:
        return " ".join(str(c or "").split()).replace("|", "\\|")

    rows = [[cell(c) for c in r] for r in rows if any(cell(c) for c in r)]
    if len(rows) < 2:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def _page_from_pymupdf(page: pymupdf.Page, detect_tables: bool) -> RawPage:
    d = page.get_text("dict", flags=_TEXT_FLAGS)
    blocks: list[RawBlock] = []
    for b in d.get("blocks", []):
        if b.get("type") != 0:
            continue
        lines: list[RawLine] = []
        for ln in b.get("lines", []):
            spans = [
                Span(
                    text=s.get("text", ""),
                    size=float(s.get("size", 0.0)),
                    bold=bool(s.get("flags", 0) & 16) or "bold" in s.get("font", "").lower(),
                    italic=bool(s.get("flags", 0) & 2),
                    color=int(s.get("color", 0)),
                    font=s.get("font", ""),
                    bbox=tuple(s.get("bbox", (0, 0, 0, 0))),
                )
                for s in ln.get("spans", [])
                if s.get("text")
            ]
            if spans and "".join(s.text for s in spans).strip():
                lines.append(RawLine(tuple(ln["bbox"]), spans))
        if lines:
            blocks.append(RawBlock(tuple(b["bbox"]), lines))
    tables = _table_markdown(page) if detect_tables else []
    return RawPage(page.number + 1, page.rect.width, page.rect.height, blocks, tables)


def _looks_scanned(page: pymupdf.Page, min_chars: int) -> bool:
    text = page.get_text("text").strip()
    if len(text) >= min_chars:
        return False
    return bool(page.get_images(full=False))


def pdf_page_count(path: str | Path) -> int:
    with pymupdf.open(path) as doc:
        return doc.page_count


def read_pdf(
    path: str | Path,
    *,
    pages: Optional[list[int]] = None,
    ocr: Optional[OCRConfig] = None,
    detect_tables: bool = True,
    progress: Optional[Callable[[int, int, str], None]] = None,
) -> tuple[list[RawPage], int, list[str]]:
    """Returns (raw pages, total page count, warnings)."""
    from .ocr import ocr_image_bytes  # local import: OCR is optional

    warnings: list[str] = []
    raw_pages: list[RawPage] = []
    with pymupdf.open(path) as doc:
        if doc.needs_pass:
            raise ValueError("This PDF is password-protected; remove the password and upload again.")
        total = doc.page_count
        wanted = pages or list(range(1, total + 1))
        scanned = {pno for pno in wanted if ocr and ocr.engine != "none" and _looks_scanned(doc[pno - 1], ocr.min_text_chars)}
        # In a born-digital PDF, image-only pages are covers, adverts or infographics: OCR adds noise.
        born_digital = (len(wanted) - len(scanned)) >= max(2, 0.5 * len(wanted))
        if born_digital and scanned and not (ocr and ocr.mode == "always"):
            warnings.append(
                f"Skipped OCR for {len(scanned)} image-only page(s) in a text PDF (covers/adverts?): "
                + ", ".join(map(str, sorted(scanned)[:20]))
            )
            scanned = set()
        for n, pno in enumerate(wanted, 1):
            page = doc[pno - 1]
            if progress and (n == 1 or n % 10 == 0 or n == len(wanted)):
                progress(n, len(wanted), f"Reading page {pno}/{total}")
            if pno in scanned:
                pix = page.get_pixmap(dpi=ocr.dpi)
                try:
                    rp = ocr_image_bytes(pix.tobytes("png"), pno, ocr, dpi=ocr.dpi)
                    raw_pages.append(rp)
                except Exception as exc:
                    warnings.append(f"OCR failed on page {pno}: {exc}")
                continue
            raw_pages.append(_page_from_pymupdf(page, detect_tables))
    empty = [p.number for p in raw_pages if not p.blocks]
    if raw_pages and len(empty) == len(raw_pages):
        warnings.append("No text could be extracted. If this is a scanned PDF, make sure Tesseract is installed.")
    return raw_pages, total, warnings
