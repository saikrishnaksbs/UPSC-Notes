"""Layout analysis shared by PDF text extraction and OCR.

Input: pages of raw blocks → lines → styled spans (from PyMuPDF or Tesseract).
Output: an ExtractedDocument whose blocks are in reading order, with bullets, tables and
headings identified. Headings carry a *style id*; every style gets a role
(section / topic / subheading / ignore) that drives topic segmentation.

Handles the things real UPSC magazines do:
  * two-column pages with full-width banners in the middle of the page,
  * running headers/footers repeated on every page,
  * bullet glyphs and "1.1" numbers emitted as separate spans,
  * article titles that are the same size as body text but bold + coloured.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Optional

from ..models import Block, ExtractedDocument, StyleInfo

BBox = tuple[float, float, float, float]

# Glyphs used as list bullets by Word/Corel/InDesign exports (incl. Symbol/Wingdings private-use chars).
BULLET_GLYPHS = set("•●○◦▪▫■□◆◇►▶➢➤✓✔❖⦿⁃∙·➣➔→⮚") | {
    "", "", "", "", "", "", "", "", "", "", "", "",
}
DASH_BULLETS = {"-", "–", "—", "*", "o"}  # only when emitted as a separate span / followed by a space
_NUMBERED_RE = re.compile(r"^\(?\d{1,2}(\.\d{1,2})+\.?\s|^\d{1,2}\.\d{1,2}\b")
_SECTION_NUM_RE = re.compile(r"^\d{1,2}\s*[.)]\s+\S")
_TERMINAL = tuple(".!?:;)\"'”’")


@dataclass
class Span:
    text: str
    size: float
    bold: bool = False
    italic: bool = False
    color: int = 0
    font: str = ""
    bbox: BBox = (0.0, 0.0, 0.0, 0.0)


@dataclass
class RawLine:
    bbox: BBox
    spans: list[Span]

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)


@dataclass
class RawBlock:
    bbox: BBox
    lines: list[RawLine]


@dataclass
class RawPage:
    number: int  # 1-based
    width: float
    height: float
    blocks: list[RawBlock] = field(default_factory=list)
    tables: list[tuple[BBox, str]] = field(default_factory=list)
    ocr: bool = False


@dataclass
class VLine:
    """A visual line: spans that sit on one baseline within one column."""

    page: int
    bbox: BBox
    spans: list[Span]
    block_id: int
    bullet: bool = False
    glyph_x: Optional[float] = None
    depth: int = 0
    table_md: Optional[str] = None
    col: str = "F"

    @property
    def text(self) -> str:
        return re.sub(r"\s+", " ", "".join(s.text for s in self.spans)).strip()

    @property
    def height(self) -> float:
        return max(1.0, self.bbox[3] - self.bbox[1])


def _color_class(color: int) -> str:
    r, g, b = (color >> 16) & 255, (color >> 8) & 255, color & 255
    if max(r, g, b) < 70:
        return "black"
    if min(r, g, b) > 225:
        return "white"
    return f"#{color:06x}"


def _style_key(span: Span, quantum: float) -> tuple[float, bool, str]:
    return (round(span.size / quantum) * quantum, span.bold, _color_class(span.color))


def _union(boxes: list[BBox]) -> BBox:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


# ---------------------------------------------------------------- headers & footers
def remove_headers_footers(pages: list[RawPage], zone: float = 0.085) -> None:
    """Drop lines that repeat in the top/bottom margin across pages (running heads, page numbers)."""

    def key(text: str) -> str:
        return re.sub(r"\d+", "#", re.sub(r"\s+", " ", text.strip().lower()))

    seen: dict[str, set[int]] = defaultdict(set)
    for p in pages:
        for b in p.blocks:
            for ln in b.lines:
                y0, y1 = ln.bbox[1], ln.bbox[3]
                if y1 <= p.height * zone or y0 >= p.height * (1 - zone):
                    k = key(ln.text)
                    if k:
                        seen[k].add(p.number)
    threshold = max(2, int(0.25 * len(pages) + 0.999))
    repeated = {k for k, pg in seen.items() if len(pg) >= threshold}
    for p in pages:
        for b in p.blocks:
            kept = []
            for ln in b.lines:
                y0, y1 = ln.bbox[1], ln.bbox[3]
                in_zone = y1 <= p.height * zone or y0 >= p.height * (1 - zone)
                t = ln.text.strip()
                if in_zone and (key(t) in repeated or re.fullmatch(r"(page\s*)?\d{1,4}", t.lower() or "x")):
                    continue
                kept.append(ln)
            b.lines = kept
        p.blocks = [b for b in p.blocks if any(ln.text.strip() for ln in b.lines)]
        for b in p.blocks:
            b.bbox = _union([ln.bbox for ln in b.lines])


# ---------------------------------------------------------------- reading order
def _order_blocks(page: RawPage) -> list[tuple[RawBlock, str]]:
    blocks = [b for b in page.blocks if b.lines]
    if not blocks:
        return []
    mid = page.width / 2
    tol = page.width * 0.03
    cols: dict[int, str] = {}
    chars = Counter()
    for i, b in enumerate(blocks):
        x0, _, x1, _ = b.bbox
        c = "L" if x1 <= mid + tol else "R" if x0 >= mid - tol else "F"
        cols[i] = c
        chars[c] += sum(len(ln.text) for ln in b.lines)
    total = sum(chars.values()) or 1
    n_l = sum(1 for c in cols.values() if c == "L")
    n_r = sum(1 for c in cols.values() if c == "R")
    two_col = chars["L"] >= 0.2 * total and chars["R"] >= 0.2 * total and n_l >= 2 and n_r >= 2

    def yc(b: RawBlock) -> float:
        return (b.bbox[1] + b.bbox[3]) / 2

    if not two_col:
        return [(b, "F") for b in _row_sort(blocks)]

    full = sorted((b for i, b in enumerate(blocks) if cols[i] == "F"), key=yc)
    centers = [yc(b) for b in full]
    ordered: list[tuple[RawBlock, str]] = []
    for band in range(len(full) + 1):
        lo = centers[band - 1] if band > 0 else float("-inf")
        hi = centers[band] if band < len(full) else float("inf")
        members = [(i, b) for i, b in enumerate(blocks) if cols[i] in "LR" and lo <= yc(b) < hi]
        ordered += [(b, "L") for _, b in sorted((m for m in members if cols[m[0]] == "L"), key=lambda m: m[1].bbox[1])]
        ordered += [(b, "R") for _, b in sorted((m for m in members if cols[m[0]] == "R"), key=lambda m: m[1].bbox[1])]
        if band < len(full):
            ordered.append((full[band], "F"))
    return ordered


def _row_sort(blocks: list[RawBlock], tol: float = 3.0) -> list[RawBlock]:
    """Top-to-bottom; blocks starting within `tol` points of each other are read left-to-right."""
    rows: list[list[RawBlock]] = []
    for b in sorted(blocks, key=lambda b: b.bbox[1]):
        if rows and b.bbox[1] - rows[-1][0].bbox[1] < tol:
            rows[-1].append(b)
        else:
            rows.append([b])
    return [b for row in rows for b in sorted(row, key=lambda b: b.bbox[0])]


def _is_glyph(text: str) -> bool:
    t = text.strip()
    return bool(t) and (t in BULLET_GLYPHS or t in DASH_BULLETS or all(ch in BULLET_GLYPHS for ch in t))


def _short_prefix(text: str) -> bool:
    """A lone bullet glyph or a numbering like '1.1' / '12.' that belongs to the following span."""
    t = text.strip()
    return _is_glyph(t) or bool(re.fullmatch(r"\(?\d{1,2}(\.\d{1,2})*[.)]?", t))


def page_vlines(page: RawPage) -> list[VLine]:
    """Blocks → visual lines in reading order (tables inserted as single pseudo-lines)."""
    table_boxes = [bb for bb, _ in page.tables]
    blocks: list[RawBlock] = []
    for b in page.blocks:
        lines = [
            ln
            for ln in b.lines
            if not any(
                bb[0] - 1 <= (ln.bbox[0] + ln.bbox[2]) / 2 <= bb[2] + 1 and bb[1] - 1 <= (ln.bbox[1] + ln.bbox[3]) / 2 <= bb[3] + 1
                for bb in table_boxes
            )
        ]
        if lines:
            blocks.append(RawBlock(_union([ln.bbox for ln in lines]), lines))
    for bb, md in page.tables:
        blocks.append(RawBlock(bb, [RawLine(bb, [Span(text="\x00TABLE\x00" + md, size=0)])]))
    page = RawPage(page.number, page.width, page.height, blocks, [], page.ocr)

    out: list[VLine] = []
    for block_id, (b, col) in enumerate(_order_blocks(page)):
        first = b.lines[0].spans[0].text if b.lines and b.lines[0].spans else ""
        if first.startswith("\x00TABLE\x00"):
            out.append(VLine(page.number, b.bbox, [], block_id, table_md=first[len("\x00TABLE\x00") :], col=col))
            continue
        lines = sorted(b.lines, key=lambda ln: (round((ln.bbox[1] + ln.bbox[3]) / 4), ln.bbox[0]))
        for ln in lines:
            spans = [s for s in ln.spans if s.text]
            if not spans or not "".join(s.text for s in spans).strip():
                continue
            prev = out[-1] if out else None
            if prev is not None and prev.table_md is None and prev.page == page.number:
                same_base = abs((prev.bbox[1] + prev.bbox[3]) / 2 - (ln.bbox[1] + ln.bbox[3]) / 2) <= max(2.5, 0.3 * prev.height)
                gap = ln.bbox[0] - prev.bbox[2]
                if same_base and -2 <= gap <= 30 and (prev.block_id == block_id or _short_prefix(prev.text)):
                    prev.spans += [Span(" ", spans[0].size, spans[0].bold, False, spans[0].color)] + spans
                    prev.bbox = _union([prev.bbox, ln.bbox])
                    continue
            out.append(VLine(page.number, ln.bbox, list(spans), block_id, col=col))
    return out


# ---------------------------------------------------------------- bullets
def detect_bullets(vlines: list[VLine]) -> None:
    for v in vlines:
        if v.table_md is not None or not v.spans:
            continue
        first = v.spans[0]
        stripped = first.text.strip()
        if stripped and (_is_glyph(stripped) and (len(v.spans) > 1 or len(stripped) > 1 or stripped in BULLET_GLYPHS)):
            v.bullet = True
            v.glyph_x = first.bbox[0] if first.bbox[2] > 0 else v.bbox[0]
            v.spans = v.spans[1:]
            while v.spans and not v.spans[0].text.strip():
                v.spans = v.spans[1:]
        elif stripped and stripped[0] in BULLET_GLYPHS and len(stripped) > 1 and stripped[1:2].isspace():
            v.bullet = True
            v.glyph_x = v.bbox[0]
            first.text = first.text.lstrip()[1:].lstrip()
        elif re.match(r"^[-–*]\s+\S", first.text) and len(v.spans) >= 1:
            v.bullet = True
            v.glyph_x = v.bbox[0]
            first.text = re.sub(r"^[-–*]\s+", "", first.text)
    # Depth: rank of the glyph's x position among bullets in the same page column.
    by_page: dict[tuple, list[VLine]] = defaultdict(list)
    for v in vlines:
        if v.bullet:
            by_page[(v.page, v.col)].append(v)
    for items in by_page.values():
        xs = sorted({round(v.glyph_x or v.bbox[0]) for v in items})
        clusters: list[float] = []
        for x in xs:
            if not clusters or x - clusters[-1] > 8:
                clusters.append(x)
        for v in items:
            x = v.glyph_x or v.bbox[0]
            v.depth = min(3, max(i for i, c in enumerate(clusters) if x >= c - 8) if clusters else 0)


# ---------------------------------------------------------------- styles & headings
@dataclass
class _Heading:
    key: tuple
    text: str
    page: int
    size: float
    lines: list[VLine]


def _line_style(v: VLine, quantum: float) -> tuple[Optional[tuple], float]:
    counts: Counter = Counter()
    spans = v.spans
    # Ignore a leading "1.1" / glyph span: magazines often colour the number differently.
    if len(spans) > 1 and _short_prefix(spans[0].text):
        spans = spans[1:]
    for s in spans:
        n = len(s.text.strip())
        if n:
            counts[_style_key(s, quantum)] += n
    if not counts:
        return None, 0.0
    key, n = counts.most_common(1)[0]
    return key, n / sum(counts.values())


def _wrapped_continuation(vlines: list[VLine], i: int, items: list) -> bool:
    """Is vlines[i] the wrapped tail of the previous line (e.g. a bold phrase spanning two lines)?"""
    v = vlines[i]
    text = v.text
    if text[:1].islower() or text[:1] in ",;)":
        return True
    if not items or items[-1][0] != "line":
        return False
    prev: VLine = items[-1][1]
    if prev.page != v.page or prev.table_md is not None:
        return False
    gap = v.bbox[1] - prev.bbox[3]
    if gap > 0.6 * prev.height or gap < -prev.height:
        return False
    prev_text = prev.text.rstrip()
    if not prev_text or prev_text.endswith((".", "?", "!", ":", ";")):
        return False
    last_span = next((sp for sp in reversed(prev.spans) if sp.text.strip()), None)
    return bool(last_span and last_span.bold)


def _prominent(key: tuple, body: tuple) -> bool:
    size, bold, color = key
    return size >= body[0] + 0.75 or bold or (color not in ("black", body[2]))


def _md_text(v: VLine) -> str:
    """Span text with **bold** runs (only when the line is not uniformly bold)."""
    spans = [s for s in v.spans if s.text]
    if not spans:
        return ""
    all_bold = all(s.bold for s in spans if s.text.strip())
    if all_bold:
        return re.sub(r"\s+", " ", "".join(s.text for s in spans)).strip()
    out, run, run_bold = [], [], None
    for s in spans:
        b = s.bold and bool(s.text.strip())
        if run and b != run_bold and s.text.strip():
            out.append(("b" if run_bold else "n", "".join(run)))
            run = []
        if not run:
            run_bold = b
        run.append(s.text)
    if run:
        out.append(("b" if run_bold else "n", "".join(run)))
    parts = []
    for kind, txt in out:
        if kind == "b" and re.search(r"\w", txt):
            lead = txt[: len(txt) - len(txt.lstrip())]
            trail = txt[len(txt.rstrip()) :]
            parts.append(f"{lead}**{txt.strip()}**{trail}")
        else:
            parts.append(txt)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


_TOC_LINE_RE = re.compile(r"(\.{4,}|…{2,}|_{4,})\s*\d{1,4}\b|\S\s{3,}\d{1,4}\s*$|[A-Za-z)]\s?\d{1,3}$")


def _toc_pages(vlines: list[VLine]) -> set[int]:
    """Pages that look like a table of contents (dot leaders / trailing page numbers)."""
    per_page: dict[int, list[VLine]] = defaultdict(list)
    for v in vlines:
        if v.table_md is None:
            per_page[v.page].append(v)
    out = set()
    for page, lines in per_page.items():
        hits = sum(1 for v in lines if _TOC_LINE_RE.search(v.text) and len(v.text) > 8)
        leaders = sum(1 for v in lines if re.search(r"\.{5,}", v.text))
        numbered = sum(1 for v in lines if re.match(r"^\(?\d{1,3}[.)]\s*\S", v.text))
        ranges = sum(1 for v in lines if re.fullmatch(r"\d{1,3}\s*[-–]\s*\d{1,3}", v.text.strip()))
        named = any(re.search(r"\b(contents|index)\b", v.text, re.I) for v in lines if len(v.text) < 40)
        if (
            leaders >= 5
            or (len(lines) >= 8 and hits / len(lines) >= 0.3)
            or (len(lines) >= 8 and numbered / len(lines) >= 0.45 and (ranges >= 1 or named))
        ):
            out.add(page)
    return out


def build_document(
    pages: list[RawPage],
    *,
    source_name: str,
    kind: str,
    page_count: int,
    quantum: float = 0.5,
    strip_running_heads: bool = True,
) -> ExtractedDocument:
    if strip_running_heads and len(pages) >= 2:
        remove_headers_footers(pages)
    vlines: list[VLine] = []
    for p in pages:
        vlines.extend(page_vlines(p))
    detect_bullets(vlines)

    # Body style = the style carrying most characters.
    style_chars: Counter = Counter()
    for v in vlines:
        for s in v.spans:
            n = len(s.text.strip())
            if n:
                style_chars[_style_key(s, quantum)] += n
    body = style_chars.most_common(1)[0][0] if style_chars else (10.0, False, "black")

    # Group heading-like lines into heading instances.
    toc = _toc_pages(vlines)
    items: list[tuple[str, object]] = []
    i = 0
    while i < len(vlines):
        v = vlines[i]
        key, purity = (None, 0.0) if (v.bullet or v.table_md is not None) else _line_style(v, quantum)
        text = v.text
        is_head = (
            v.page not in toc
            and not re.search(r"\.{5,}", text)
            and key is not None
            and key != body
            and purity >= 0.85
            and _prominent(key, body)
            and 2 <= len(text) <= 200
            and re.search(r"[A-Za-z]{2}", text) is not None
        )
        if is_head and key[0] < body[0] + 0.75 and _wrapped_continuation(vlines, i, items):
            is_head = False  # a bold phrase wrapping inside a paragraph, not a heading
        if not is_head:
            items.append(("line", v))
            i += 1
            continue
        group = [v]
        j = i + 1
        while j < len(vlines):
            w = vlines[j]
            if w.bullet or w.table_md is not None or w.page != v.page:
                break
            wkey, wpur = _line_style(w, quantum)
            last = group[-1]
            gap = w.bbox[1] - last.bbox[3]
            joined = len(" ".join(x.text for x in group)) + len(w.text)
            aligned = abs(w.bbox[0] - group[0].bbox[0]) <= 6 or w.block_id == last.block_id
            tight = gap <= 0.5 * last.height and gap >= -0.5 * last.height
            if wkey != key or wpur < 0.85 or not tight or not aligned or joined > 260 or last.text.rstrip().endswith("?"):
                break
            group.append(w)
            j += 1
        htext = re.sub(r"\s+", " ", " ".join(x.text for x in group)).strip()
        items.append(("heading", _Heading(key, htext, v.page, key[0], group)))
        i = j

    # Style statistics.
    stats: dict[tuple, dict] = {}
    for kind_, obj in items:
        if kind_ != "heading":
            continue
        h: _Heading = obj  # type: ignore[assignment]
        st = stats.setdefault(h.key, {"count": 0, "examples": [], "upper": 0, "numbered": 0, "pages": set()})
        st["count"] += 1
        st["pages"].add(h.page)
        letters = [c for c in h.text if c.isalpha()]
        if letters and sum(c.isupper() for c in letters) / len(letters) >= 0.8:
            st["upper"] += 1
        if _NUMBERED_RE.match(h.text):
            st["numbered"] += 1
            st.setdefault("depths", []).append(len(re.match(r"^\(?([\d.]+)", h.text).group(1).strip(".").split(".")))
        ex = h.text[:90]
        if ex not in st["examples"] and len(st["examples"]) < 6:
            st["examples"].append(ex)

    def prominence(k: tuple) -> tuple:
        size, bold, color = k
        return (-size, color == "black", not bold, stats[k]["count"])

    ordered_keys = sorted(stats, key=prominence)
    style_ids = {k: f"S{n + 1}" for n, k in enumerate(ordered_keys)}
    styles = [
        StyleInfo(
            id=style_ids[k],
            size=k[0],
            bold=k[1],
            color=k[2],
            count=stats[k]["count"],
            examples=stats[k]["examples"],
            upper_ratio=round(stats[k]["upper"] / stats[k]["count"], 2),
            numbered_ratio=round(stats[k]["numbered"] / stats[k]["count"], 2),
            numbering_depth=round(sum(stats[k].get("depths", [0])) / max(1, len(stats[k].get("depths", []))), 2),
        )
        for k in ordered_keys
    ]
    roles = heuristic_style_roles(styles, body_size=body[0], n_pages=max(1, len(pages)))
    for s in styles:
        s.role = roles.get(s.id, "subheading")

    blocks = _assemble(items, style_ids, {s.id: s.role for s in styles})
    return ExtractedDocument(
        source_name=source_name,
        kind=kind,  # type: ignore[arg-type]
        page_count=page_count,
        pages_processed=[p.number for p in pages],
        blocks=blocks,
        styles=styles,
        style_roles_by="heuristic",
        ocr_pages=[p.number for p in pages if p.ocr],
        warnings=[f"Pages treated as table of contents: {', '.join(map(str, sorted(toc)))}"] if toc else [],
    )


def heuristic_style_roles(styles: list[StyleInfo], body_size: float, n_pages: int) -> dict[str, str]:
    """Deterministic role guess. Larger fonts are titles unless they look like rare ALL-CAPS banners."""
    roles: dict[str, str] = {}
    bigger = [s for s in styles if s.size >= body_size + 1.0]
    same = [s for s in styles if s.size < body_size + 1.0]
    for s in bigger:
        banner = s.upper_ratio >= 0.6 and s.numbered_ratio < 0.5
        roles[s.id] = "section" if banner else "topic"
    if bigger and not any(r == "topic" for r in roles.values()):
        # Every large style looks like a banner. If there are many headings in total, the
        # smallest large style is probably the article title.
        numbered_same = [s for s in same if s.numbered_ratio >= 0.5 and s.count >= 2]
        if not numbered_same:
            smallest = min(bigger, key=lambda s: (s.size, -s.count))
            if smallest.count >= 3 and len(bigger) > 1:
                roles[smallest.id] = "topic"
    for s in same:
        roles[s.id] = "topic" if (s.numbered_ratio >= 0.5 and s.count >= 2 and s.color != "black") else "subheading"
    if not any(r == "topic" for r in roles.values()):
        for s in same:
            if s.numbered_ratio >= 0.5 and s.count >= 2:
                roles[s.id] = "topic"
    # "1.1.1 Background"-style headings are sub-headings of "1.1 Title" articles.
    numbered_topics = [s for s in styles if roles.get(s.id) == "topic" and s.numbered_ratio >= 0.5]
    if len(numbered_topics) >= 2:
        shallowest = min(s.numbering_depth for s in numbered_topics)
        for s in numbered_topics:
            if s.numbering_depth >= shallowest + 1:
                roles[s.id] = "subheading"
    return roles


def _assemble(items: list[tuple[str, object]], style_ids: dict, roles: dict[str, str]) -> list[Block]:
    blocks: list[Block] = []
    cur: Optional[Block] = None
    cur_last: Optional[VLine] = None
    cur_text_x: float = 0.0

    def flush() -> None:
        nonlocal cur, cur_last
        if cur is not None and cur.text.strip():
            cur.text = cur.text.strip()
            blocks.append(cur)
        cur, cur_last = None, None

    for kind, obj in items:
        if kind == "heading":
            flush()
            h: _Heading = obj  # type: ignore[assignment]
            sid = style_ids[h.key]
            blocks.append(Block(kind="heading", text=h.text, page=h.page, role=roles.get(sid, "subheading"), style=sid, size=h.size))  # type: ignore[arg-type]
            continue
        v: VLine = obj  # type: ignore[assignment]
        if v.table_md is not None:
            flush()
            md = v.table_md.replace("<br>", " ").strip()
            if md:
                blocks.append(Block(kind="table", text=md, page=v.page))
            continue
        text = _md_text(v)
        if not text:
            continue
        if v.bullet:
            flush()
            cur = Block(kind="bullet", text=text, page=v.page, depth=v.depth)
            cur_last, cur_text_x = v, v.bbox[0]
            continue
        if cur is not None and cur_last is not None and _continues(cur, cur_last, cur_text_x, v, text):
            joiner = "" if (cur.text.endswith("-") and cur.text[-2:-1].isalpha() and text[:1].islower()) else " "
            cur.text += joiner + text
            cur_last = v
            continue
        flush()
        cur = Block(kind="paragraph", text=text, page=v.page)
        cur_last, cur_text_x = v, v.bbox[0]
    flush()
    return blocks


def _continues(cur: Block, last: VLine, text_x: float, v: VLine, text: str) -> bool:
    if v.page == last.page and v.block_id == last.block_id or (v.page == last.page and abs(v.bbox[0] - last.bbox[0]) < 40):
        gap = v.bbox[1] - last.bbox[3]
        if -last.height * 0.5 <= gap <= last.height * 0.6:
            if cur.kind == "bullet" and v.bbox[0] < text_x - 6:
                return False
            return True
    # Paragraph flowing into the next column / page.
    return not cur.text.rstrip().endswith(_TERMINAL) and text[:1].islower()
