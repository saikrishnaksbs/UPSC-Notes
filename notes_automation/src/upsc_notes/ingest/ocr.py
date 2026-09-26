"""OCR for images and scanned PDF pages.

Default engine: Tesseract (local, open source). Its TSV output gives line boxes, so headline
lines (taller text) can be told apart from body text exactly like font sizes in a PDF.
Optional engine: a local vision model through Ollama (e.g. qwen2.5vl), which returns Markdown.
"""

from __future__ import annotations

import csv
import io
import shutil
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path
from statistics import median

from PIL import Image, ImageOps

from ..config import OCRConfig
from .layout import RawBlock, RawLine, RawPage, Span

VISION_PROMPT = (
    "Transcribe ALL text in this image exactly as it appears, as Markdown. "
    "Use '#' for the main headline(s), '##' for sub-headlines, '-' for bullet points and Markdown tables "
    "for tables. Keep reading order (column by column). Do not summarise, translate or add anything."
)


def tesseract_available(cmd: str = "tesseract") -> bool:
    return shutil.which(cmd) is not None


def _prepare(img: Image.Image) -> tuple[Image.Image, float]:
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("L", "RGB"):
        img = img.convert("RGB")
    img = img.convert("L")
    scale = 1.0
    if img.width < 1600:  # small screenshots/clippings OCR much better upscaled
        scale = 2.0 if img.width >= 800 else 3.0
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    return img, scale


def _run_tesseract(img: Image.Image, cfg: OCRConfig, dpi: int) -> str:
    if not tesseract_available(cfg.tesseract_cmd):
        raise RuntimeError("Tesseract is not installed (macOS: `brew install tesseract`).")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "page.png"
        img.save(src, dpi=(dpi, dpi))
        proc = subprocess.run(
            [cfg.tesseract_cmd, str(src), "stdout", "-l", cfg.languages, "--psm", "3", "--dpi", str(dpi), "tsv"],
            capture_output=True,
            text=True,
            timeout=300,
        )
    if proc.returncode != 0:
        raise RuntimeError(f"tesseract failed: {proc.stderr.strip()[:300]}")
    return proc.stdout


def _tsv_to_page(tsv: str, page_number: int, width_px: int, height_px: int, px_to_pt: float) -> RawPage:
    words_by_line: dict[tuple, list[dict]] = defaultdict(list)
    line_boxes: dict[tuple, tuple] = {}
    reader = csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)
    for row in reader:
        try:
            level = int(row["level"])
            key = (int(row["block_num"]), int(row["par_num"]), int(row["line_num"]))
            box = (int(row["left"]), int(row["top"]), int(row["width"]), int(row["height"]))
        except (KeyError, TypeError, ValueError):
            continue
        if level == 4:
            line_boxes[key] = box
        elif level == 5 and (row.get("text") or "").strip():
            try:
                conf = float(row.get("conf") or -1)
            except ValueError:
                conf = -1
            if conf < 0:
                continue
            words_by_line[key].append({"text": row["text"].strip(), "box": box})

    blocks: dict[tuple, list[RawLine]] = defaultdict(list)
    for key in sorted(words_by_line):
        words = words_by_line[key]
        lx, ly, lw, lh = line_boxes.get(key) or (
            min(w["box"][0] for w in words),
            min(w["box"][1] for w in words),
            max(w["box"][0] + w["box"][2] for w in words) - min(w["box"][0] for w in words),
            max(w["box"][1] + w["box"][3] for w in words) - min(w["box"][1] for w in words),
        )
        heights = [w["box"][3] for w in words]
        size_pt = median(heights) * px_to_pt
        size_pt = round(size_pt / 2) * 2  # OCR heights are noisy; quantise to 2pt steps
        bbox = (lx * px_to_pt, ly * px_to_pt, (lx + lw) * px_to_pt, (ly + lh) * px_to_pt)
        text = " ".join(w["text"] for w in words)
        blocks[key[:2]].append(RawLine(bbox, [Span(text=text, size=size_pt, bbox=bbox)]))

    raw_blocks = []
    for lines in blocks.values():
        xs0 = [ln.bbox[0] for ln in lines]
        ys0 = [ln.bbox[1] for ln in lines]
        xs1 = [ln.bbox[2] for ln in lines]
        ys1 = [ln.bbox[3] for ln in lines]
        raw_blocks.append(RawBlock((min(xs0), min(ys0), max(xs1), max(ys1)), lines))
    return RawPage(page_number, width_px * px_to_pt, height_px * px_to_pt, raw_blocks, [], ocr=True)


def markdown_to_page(markdown: str, page_number: int) -> RawPage:
    """Turn vision-model Markdown into a synthetic RawPage so it flows through the same layout code."""
    sizes = {1: 20.0, 2: 16.0, 3: 13.0}
    lines: list[RawLine] = []
    y = 10.0
    for raw in markdown.splitlines():
        text = raw.rstrip()
        if not text.strip() or text.strip().startswith("```"):
            y += 6
            continue
        size, bold = 10.0, False
        stripped = text.lstrip()
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            size, bold = sizes.get(level, 11.0), True
            text = stripped.lstrip("#").strip()
        elif stripped[:2] in ("- ", "* ", "+ "):
            text = "• " + stripped[2:]
        bbox = (20.0, y, 575.0, y + size)
        lines.append(RawLine(bbox, [Span(text=text, size=size, bold=bold, bbox=bbox)]))
        y += size + 4
    blocks = [RawBlock(ln.bbox, [ln]) for ln in lines]
    return RawPage(page_number, 595.0, max(842.0, y + 20), blocks, [], ocr=True)


def ocr_image_bytes(data: bytes, page_number: int, cfg: OCRConfig, dpi: int = 300) -> RawPage:
    if cfg.engine == "ollama_vision":
        from ..llm.ollama import OllamaChat

        model = OllamaChat(cfg.vision_model, cfg.vision_base_url, num_ctx=8192)
        return markdown_to_page(model.describe_image(data, VISION_PROMPT), page_number)
    img = Image.open(io.BytesIO(data))
    prepared, scale = _prepare(img)
    eff_dpi = int(dpi * scale)
    tsv = _run_tesseract(prepared, cfg, eff_dpi)
    return _tsv_to_page(tsv, page_number, prepared.width, prepared.height, 72.0 / eff_dpi)


def read_image(path: str | Path, cfg: OCRConfig) -> list[RawPage]:
    """A single image (JPG/PNG/WebP/TIFF…). Multi-frame TIFFs become multiple pages."""
    pages = []
    with Image.open(path) as img:
        n_frames = getattr(img, "n_frames", 1)
        for i in range(n_frames):
            img.seek(i)
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="PNG")
            dpi = img.info.get("dpi", (300, 300))[0] or 300
            pages.append(ocr_image_bytes(buf.getvalue(), i + 1, cfg, dpi=int(min(max(dpi, 150), 400))))
    return pages
