"""Split an ExtractedDocument into topic segments.

Strategy:
1. Structured documents (magazines, notes): topic boundaries are headings whose *style* has the
   role "topic". Roles come from layout heuristics; when those look implausible the local LLM
   classifies the styles from examples (one small call per document).
2. Unstructured text (plain text, OCR'd clippings without clear headlines): the LLM marks where
   each article starts, window by window.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from .ingest.textdoc import is_generic_heading
from .llm import ChatModel, LLMError
from .models import Block, ExtractedDocument, Segment, blocks_to_markdown
from .prompts import SPLIT_PROMPT, SPLIT_SCHEMA, STYLE_ROLES_PROMPT, SYSTEM_ANALYST, style_roles_schema

log = logging.getLogger(__name__)

SINGLE_TOPIC_MAX_CHARS = 12000


def clean_title(text: str) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    t = re.sub(r"^\(?\d{1,2}(\.\d{1,2})*[.)]?\s+", "", t)  # "1.1 ", "5.12 ", "3) "
    t = t.strip(" :–—-|•")
    return t or text.strip()


def _roles_plausible(doc: ExtractedDocument) -> bool:
    topics = [b for b in doc.blocks if b.kind == "heading" and b.role == "topic"]
    pages = max(1, len(doc.pages_processed) or doc.page_count)
    total_chars = sum(len(b.text) for b in doc.blocks)
    if not topics:
        return total_chars <= SINGLE_TOPIC_MAX_CHARS
    per_page = len(topics) / pages
    avg_chars = total_chars / len(topics)
    return per_page <= 4 and avg_chars >= 400


def resolve_style_roles(doc: ExtractedDocument, llm: Optional[ChatModel], mode: str = "auto") -> None:
    """Keep heuristic roles when they look sane; otherwise ask the LLM to label the heading styles."""
    if doc.kind not in ("pdf", "image") or not doc.styles:
        return
    if mode == "heuristic" or llm is None or (mode == "auto" and _roles_plausible(doc)):
        return
    styles = sorted(doc.styles, key=lambda s: (-s.size, -s.count))[:14]
    lines = []
    for s in styles:
        ex = " | ".join(f'"{e}"' for e in s.examples[:4])
        lines.append(f"{s.id}: {s.size:g}pt, {'bold' if s.bold else 'regular'}, colour {s.color} — {s.count} occurrences — e.g. {ex}")
    body_size = min((s.size for s in doc.styles), default=10)
    prompt = STYLE_ROLES_PROMPT.format(
        name=doc.source_name, pages=len(doc.pages_processed) or doc.page_count, body_size=body_size, styles="\n".join(lines)
    )
    try:
        data = llm.complete_json(SYSTEM_ANALYST, prompt, style_roles_schema([s.id for s in styles]), task="layout")
    except LLMError as exc:
        doc.warnings.append(f"Layout analysis by LLM failed, using heuristics: {exc}")
        return
    new_roles = {sid: role for sid, role in data.items() if role in ("section", "topic", "subheading", "ignore")}
    if not any(r == "topic" for r in new_roles.values()):
        doc.warnings.append("LLM layout analysis found no article-title style; kept heuristic roles.")
        return
    for s in doc.styles:
        if s.id in new_roles:
            s.role = new_roles[s.id]  # type: ignore[assignment]
    for b in doc.blocks:
        if b.kind == "heading" and b.style in new_roles:
            b.role = new_roles[b.style]  # type: ignore[assignment]
    doc.style_roles_by = "llm"


def segment_document(
    doc: ExtractedDocument,
    *,
    llm: Optional[ChatModel] = None,
    min_chars: int = 150,
    max_chars: int = 40000,
) -> list[Segment]:
    blocks = doc.blocks
    # Generic sub-headings ("Background", "Way Forward") never start a topic.
    for b in blocks:
        if b.kind == "heading" and b.role == "topic" and is_generic_heading(b.text):
            b.role = "subheading"
    has_topics = any(b.kind == "heading" and b.role == "topic" for b in blocks)
    total = sum(len(b.text) for b in blocks)
    if not has_topics:
        if total > SINGLE_TOPIC_MAX_CHARS and llm is not None:
            try:
                return llm_split(doc, llm)
            except LLMError as exc:
                doc.warnings.append(f"LLM topic splitting failed ({exc}); treating the file as one topic.")
        return _single_segment(doc)

    segments: list[Segment] = []
    sections: list[tuple[float, str]] = []  # (size, text) stack
    current: Optional[dict] = None
    preamble: list[Block] = []

    def section_path() -> Optional[str]:
        return " › ".join(t for _, t in sections) or None

    def close() -> None:
        nonlocal current
        if current is not None:
            _emit(segments, current, min_chars)
        current = None

    for b in blocks:
        if b.kind == "heading" and b.role == "section":
            close()
            size = b.size or 0.0
            while sections and sections[-1][0] <= size:
                sections.pop()
            sections.append((size, b.text))
            continue
        if b.kind == "heading" and b.role == "topic":
            close()
            current = {"title": b.text, "section": section_path(), "page": b.page, "blocks": []}
            continue
        if current is None:
            preamble.append(b)
        else:
            current["blocks"].append(b)
    close()

    pre_text = blocks_to_markdown(preamble)
    if len(pre_text) > 300:
        pages = [b.page for b in preamble if b.page]
        segments.insert(
            0,
            Segment(
                id=0,
                title="Front matter (before the first article)",
                page_start=min(pages) if pages else None,
                page_end=max(pages) if pages else None,
                text=pre_text,
            ),
        )
    # Oversized segments are split at sub-heading boundaries.
    final: list[Segment] = []
    for seg in segments:
        final.extend(_split_long(seg, max_chars))
    for i, seg in enumerate(final, 1):
        seg.id = i
    return final


def _emit(segments: list[Segment], cur: dict, min_chars: int) -> None:
    body = cur["blocks"]
    text = blocks_to_markdown(body)
    if len(text) < max(80, min_chars // 2):
        return  # a title with (almost) nothing under it — usually a mis-detected heading
    pages = [b.page for b in body if b.page] + ([cur["page"]] if cur["page"] else [])
    segments.append(
        Segment(
            id=len(segments) + 1,
            title=clean_title(cur["title"]),
            section=cur["section"],
            page_start=min(pages) if pages else None,
            page_end=max(pages) if pages else None,
            text=text,
        )
    )


def _split_long(seg: Segment, max_chars: int) -> list[Segment]:
    if len(seg.text) <= max_chars:
        return [seg]
    parts: list[str] = []
    buf: list[str] = []
    size = 0
    for chunk in re.split(r"(?m)^(?=#{2,4} )", seg.text):
        if size + len(chunk) > max_chars * 0.6 and buf:
            parts.append("".join(buf))
            buf, size = [], 0
        buf.append(chunk)
        size += len(chunk)
    if buf:
        parts.append("".join(buf))
    if len(parts) == 1:
        # No sub-headings: cut on paragraph boundaries.
        paras = seg.text.split("\n\n")
        parts, buf, size = [], [], 0
        for p in paras:
            if size + len(p) > max_chars * 0.6 and buf:
                parts.append("\n\n".join(buf))
                buf, size = [], 0
            buf.append(p)
            size += len(p)
        if buf:
            parts.append("\n\n".join(buf))
    return [
        Segment(id=0, title=f"{seg.title} (part {i}/{len(parts)})", section=seg.section, page_start=seg.page_start, page_end=seg.page_end, text=t)
        for i, t in enumerate(parts, 1)
    ]


def _single_segment(doc: ExtractedDocument) -> list[Segment]:
    text = blocks_to_markdown([b for b in doc.blocks])
    if not text.strip():
        return []
    heads = [b for b in doc.blocks if b.kind == "heading" and b.role != "ignore"]
    if heads:
        title = clean_title(heads[0].text)
        body = [b for b in doc.blocks if b is not heads[0]]
        text = blocks_to_markdown(body) if body else text
    else:
        first = next((b.text for b in doc.blocks if b.text.strip()), doc.source_name)
        title = clean_title(first if len(first) <= 100 else doc.source_name.rsplit(".", 1)[0])
    pages = [b.page for b in doc.blocks if b.page]
    return [Segment(id=1, title=title, page_start=min(pages) if pages else None, page_end=max(pages) if pages else None, text=text)]


def llm_split(doc: ExtractedDocument, llm: ChatModel, window_chars: int = 6000) -> list[Segment]:
    """Ask the LLM where topics start, for documents without usable headings."""
    units = [b for b in doc.blocks if b.text.strip()]
    starts: dict[int, str] = {}
    i = 0
    while i < len(units):
        j, size = i, 0
        while j < len(units) and (size < window_chars or j == i):
            size += min(len(units[j].text), 500)
            j += 1
        numbered = "\n".join(f"[{k}] {units[k].text[:450]}" for k in range(i, j))
        prompt = SPLIT_PROMPT.format(name=doc.source_name, first=i, last=j - 1, paragraphs=numbered)
        data = llm.complete_json(SYSTEM_ANALYST, prompt, SPLIT_SCHEMA, task="split")
        found = False
        for t in data.get("topics", []):
            try:
                k = int(t.get("start"))
            except (TypeError, ValueError):
                continue
            if i <= k < j and str(t.get("title", "")).strip():
                starts.setdefault(k, str(t["title"]).strip())
                found = True
        if i == 0 and 0 not in starts:
            starts[0] = (data.get("topics") or [{}])[0].get("title") or doc.source_name
        if not data.get("first_paragraph_continues_previous_topic", True) and i not in starts and not found:
            starts[i] = f"Untitled topic (p. {units[i].page or '?'})"
        i = j
    ordered = sorted(starts)
    segments: list[Segment] = []
    for n, k in enumerate(ordered):
        end = ordered[n + 1] if n + 1 < len(ordered) else len(units)
        body = units[k:end]
        if body and body[0].kind == "heading":
            body = body[1:]
        text = blocks_to_markdown(body)
        if len(text) < 80:
            continue
        pages = [b.page for b in units[k:end] if b.page]
        segments.append(
            Segment(
                id=len(segments) + 1,
                title=clean_title(starts[k]),
                page_start=min(pages) if pages else None,
                page_end=max(pages) if pages else None,
                text=text,
            )
        )
    return segments or _single_segment(doc)
