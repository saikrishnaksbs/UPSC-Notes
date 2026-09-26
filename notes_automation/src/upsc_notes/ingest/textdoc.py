"""Markdown and plain-text inputs → Blocks with heading roles."""

from __future__ import annotations

import re
from collections import Counter

from ..models import Block
from ..utils import normalize_text

# Headings that structure an article internally and must never start a new topic.
GENERIC_HEADINGS = {
    "about", "about the", "analysis", "advantages", "background", "benefits", "causes", "challenges",
    "challenges and concerns", "concerns", "conclusion", "constitutional provisions", "context", "criticism",
    "current status", "data", "details", "disadvantages", "effects", "evolution", "facts", "features",
    "findings", "government initiatives", "highlights", "history", "impact", "implications", "importance",
    "initiatives", "introduction", "issues", "judgement", "judgment", "key facts", "key features",
    "key findings", "key highlights", "key points", "key provisions", "key recommendations", "key takeaways",
    "know more", "legal provisions", "limitations", "mains practice question", "measures", "measures taken",
    "need", "objectives", "observations", "opportunities", "origin", "overview", "practice question",
    "prelims practice question", "provisions", "reasons", "recommendations", "references", "related information",
    "significance", "source", "sources", "statistics", "steps taken", "summary", "takeaways", "the way ahead",
    "timeline", "verdict", "way ahead", "way forward", "what is the issue", "what lies ahead", "what next",
    "why in news", "why in the news", "in news", "news", "concerns and challenges", "significance and challenges",
}


def is_generic_heading(text: str) -> bool:
    t = normalize_text(re.sub(r"^\(?\d+(\.\d+)*[.)]?\s*", "", text))
    if not t:
        return True
    if t in GENERIC_HEADINGS:
        return True
    return any(t.startswith(g + " ") and len(t) <= len(g) + 18 for g in ("why in news", "way forward", "key", "about"))


_BULLET_RE = re.compile(r"^(\s*)([-*+•●▪◦]|\d{1,2}[.)])\s+(.*)$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def _markdown_raw_blocks(text: str) -> list[tuple[str, int, str]]:
    """(kind, level_or_depth, text) where kind in heading/bullet/table/paragraph."""
    text = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S)  # YAML front matter
    lines = text.splitlines()
    out: list[tuple[str, int, str]] = []
    para: list[str] = []
    in_code = False

    def flush() -> None:
        if para:
            out.append(("paragraph", 0, " ".join(p.strip() for p in para).strip()))
            para.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            in_code = not in_code
            para.append(line)
            i += 1
            continue
        if in_code:
            para.append(line)
            i += 1
            continue
        m = _HEADING_RE.match(line)
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if m:
            flush()
            out.append(("heading", len(m.group(1)), m.group(2).strip()))
        elif line.strip() and re.fullmatch(r"=+\s*", nxt) and not para:
            out.append(("heading", 1, line.strip()))
            i += 1
        elif line.strip() and re.fullmatch(r"-{3,}\s*", nxt) and not para:
            out.append(("heading", 2, line.strip()))
            i += 1
        elif line.lstrip().startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append(lines[i].strip())
                i += 1
            out.append(("table", 0, "\n".join(rows)))
            continue
        elif (bm := _BULLET_RE.match(line)) is not None:
            flush()
            depth = len(bm.group(1).replace("\t", "    ")) // 2
            out.append(("bullet", min(depth, 3), bm.group(3).strip()))
        elif not line.strip():
            flush()
        elif re.fullmatch(r"\s*([-*_]\s*){3,}", line):
            flush()
        elif out and out[-1][0] == "bullet" and line.startswith((" ", "\t")) and not para:
            k, d, t = out[-1]
            out[-1] = (k, d, t + " " + line.strip())
        else:
            para.append(line)
        i += 1
    flush()
    return out


def parse_markdown(text: str) -> list[Block]:
    raw = _markdown_raw_blocks(text)
    levels = Counter(lvl for kind, lvl, t in raw if kind == "heading")
    topic_level = _choose_topic_level(raw, levels)
    sizes = {1: 24.0, 2: 20.0, 3: 16.0, 4: 14.0, 5: 12.0, 6: 11.0}
    blocks: list[Block] = []
    for kind, lvl, t in raw:
        if kind == "heading":
            if topic_level is None:
                role = "subheading" if lvl > 1 else "section"
            elif lvl < topic_level:
                role = "section"
            elif lvl == topic_level:
                role = "subheading" if is_generic_heading(t) else "topic"
            else:
                role = "subheading"
            blocks.append(Block(kind="heading", text=_clean_inline(t), role=role, style=f"H{lvl}", size=sizes[lvl]))  # type: ignore[arg-type]
        elif kind == "bullet":
            blocks.append(Block(kind="bullet", text=t, depth=lvl))
        elif kind == "table":
            blocks.append(Block(kind="table", text=t))
        elif t:
            blocks.append(Block(kind="paragraph", text=t))
    return blocks


def _clean_inline(t: str) -> str:
    return re.sub(r"[*_`]+", "", t).strip()


def _choose_topic_level(raw: list[tuple[str, int, str]], levels: Counter) -> int | None:
    """Which heading level separates topics? None → the whole file is one topic."""
    if not levels:
        return None
    for lvl in sorted(levels):
        heads = [t for kind, level, t in raw if kind == "heading" and level == lvl]
        specific = [h for h in heads if not is_generic_heading(h)]
        if len(specific) >= 2:
            return lvl
        if len(heads) == 1 and lvl == min(levels):
            continue  # a single document title — look one level deeper
    return None


def parse_text(text: str) -> list[Block]:
    """Plain text: paragraphs split on blank lines; short title-like lines become headings."""
    blocks: list[Block] = []
    paras = re.split(r"\n\s*\n", text.replace("\r\n", "\n"))
    for para in paras:
        lines = [ln.rstrip() for ln in para.split("\n") if ln.strip()]
        if not lines:
            continue
        first = lines[0].strip()
        if _looks_like_heading(first) and (len(lines) == 1 or len(first) < 100):
            blocks.append(Block(kind="heading", text=first.strip("#*: ").strip(), role="topic", style="T", size=14.0))
            lines = lines[1:]
        body: list[str] = []
        for ln in lines:
            bm = _BULLET_RE.match(ln)
            if bm:
                if body:
                    blocks.append(Block(kind="paragraph", text=" ".join(body)))
                    body = []
                blocks.append(Block(kind="bullet", text=bm.group(3).strip()))
            else:
                body.append(ln.strip())
        if body:
            blocks.append(Block(kind="paragraph", text=" ".join(body)))
    heads = [b for b in blocks if b.kind == "heading"]
    for b in heads:
        if is_generic_heading(b.text):
            b.role = "subheading"
    if sum(1 for b in heads if b.role == "topic") < 2:
        # One (or no) title: treat everything as a single topic; keep headings as structure.
        for b in heads[1:]:
            b.role = "subheading"
    return blocks


def _looks_like_heading(line: str) -> bool:
    if len(line) > 120 or len(line) < 3 or line.endswith((".", ",", ";")):
        return False
    words = re.findall(r"[A-Za-z][A-Za-z'’-]*", line)
    if not words or len(words) > 16:
        return False
    caps = sum(1 for w in words if w[0].isupper())
    return line.isupper() or caps / len(words) >= 0.6 or line.startswith("#")
