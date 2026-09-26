"""Turn an uploaded file (PDF / image / Markdown / text) into an ExtractedDocument."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from ..config import OCRConfig
from ..models import ExtractedDocument
from ..utils import parse_page_spec
from .layout import build_document
from .textdoc import parse_markdown, parse_text

PDF_EXTS = {".pdf"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".gif"}
MARKDOWN_EXTS = {".md", ".markdown"}
TEXT_EXTS = {".txt", ".text"}
SUPPORTED_EXTS = PDF_EXTS | IMAGE_EXTS | MARKDOWN_EXTS | TEXT_EXTS


def read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def extract_document(
    path: str | Path,
    *,
    ocr: OCRConfig,
    engine: str = "pymupdf",
    pages: Optional[str] = None,
    display_name: Optional[str] = None,
    progress: Optional[Callable[[int, int, str], None]] = None,
) -> ExtractedDocument:
    path = Path(path)
    ext = path.suffix.lower()
    name = display_name or path.name
    if ext in PDF_EXTS:
        from .pdf import pdf_page_count, read_pdf

        total = pdf_page_count(path)
        wanted = parse_page_spec(pages, total)
        raw_pages, total, warnings = read_pdf(path, pages=wanted, ocr=ocr, progress=progress)
        doc = build_document(raw_pages, source_name=name, kind="pdf", page_count=total)
        doc.warnings += warnings
        if engine == "docling":
            doc = _with_docling(path, doc, wanted or list(range(1, total + 1)), total, ocr, progress)
        return doc
    if ext in IMAGE_EXTS and engine == "docling":
        try:
            from .docling_engine import docling_available, image_document

            if docling_available():
                doc = image_document(path, name, progress)
                if doc.blocks:
                    if len([b for b in doc.blocks if b.role == "topic"]) > 1 and sum(len(b.text) for b in doc.blocks) < 12000:
                        pass  # several headlines on one clipping: keep them as separate topics
                    return doc
        except Exception as exc:  # fall back to Tesseract
            import logging

            logging.getLogger(__name__).warning("Docling OCR failed, using Tesseract: %s", exc)
    if ext in IMAGE_EXTS:
        from .ocr import read_image

        if progress:
            progress(0, 1, "Running OCR")
        raw_pages = read_image(path, ocr)
        doc = build_document(
            raw_pages, source_name=name, kind="image", page_count=len(raw_pages), strip_running_heads=len(raw_pages) > 2
        )
        if not doc.blocks:
            doc.warnings.append("OCR found no text in the image.")
        return doc
    if ext in MARKDOWN_EXTS:
        return ExtractedDocument(
            source_name=name, kind="markdown", blocks=parse_markdown(read_text_file(path)), style_roles_by="markup"
        )
    if ext in TEXT_EXTS:
        return ExtractedDocument(source_name=name, kind="text", blocks=parse_text(read_text_file(path)), style_roles_by="markup")
    raise ValueError(f"Unsupported file type '{ext}'. Supported: {', '.join(sorted(SUPPORTED_EXTS))}")


def _with_docling(path: Path, reference, pages: list[int], total: int, ocr: OCRConfig, progress):
    """Docling text + style-analysis headings; falls back to the PyMuPDF result if Docling fails."""
    from .docling_engine import docling_available, extract_pdf_docling

    if not docling_available():
        reference.warnings.append("Docling is not installed; used the fast PyMuPDF extractor.")
        return reference
    try:
        doc = extract_pdf_docling(path, pages, total, reference, ocr=ocr, progress=progress)
    except Exception as exc:  # never lose a job because of the optional engine
        reference.warnings.append(f"Docling failed ({exc}); used the PyMuPDF extractor instead.")
        return reference
    expected = sum(1 for b in reference.blocks if b.kind == "heading" and b.role == "topic")
    found = sum(1 for b in doc.blocks if b.kind == "heading" and b.role == "topic")
    if expected and found < 0.8 * expected:
        reference.warnings.append(
            f"Docling kept only {found} of {expected} article titles; used the PyMuPDF extractor instead."
        )
        return reference
    return doc
