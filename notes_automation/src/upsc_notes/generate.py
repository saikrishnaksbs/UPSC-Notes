"""Write UPSC notes from source text, and post-check what the model wrote."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Optional

from rapidfuzz import fuzz, process

from .config import PipelineConfig
from .llm import ChatModel
from .prompts import CONDENSE_PROMPT, NOTE_PROMPT, SYSTEM_WRITER, UPDATE_PROMPT
from .retrieval import note_body
from .utils import normalize_text, tokenize, truncate, word_count

NO_NEW = "NO_NEW_INFORMATION"
_NUM_RE = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")
_EMPTY_LINE_RE = re.compile(
    r"^\s*[-*]?\s*(\*\*)?("
    r"(not|is not|are not|was not) (explicitly )?(mentioned|specified|available|provided|given|discussed|covered)[^.]*"
    r"|no (specific |further |additional )?(information|details|data|mention)[^.]*"
    r"|the (source|text|article) (does not|doesn't|did not) (mention|provide|specify|discuss)[^.]*"
    r"|n/?a|none|nil|not applicable"
    r")\.?(\*\*)?\s*$",
    re.I,
)


@dataclass
class Generation:
    content: Optional[str]
    flags: list[str] = field(default_factory=list)
    seconds: float = 0.0
    no_new_information: bool = False


# ------------------------------------------------------------------ helpers
def _norm_num(s: str) -> str:
    s = s.replace(",", "")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def unverified_numbers(content: str, source: str) -> list[str]:
    """Numbers in the note that never appear in the source — a cheap hallucination check."""
    src = {_norm_num(n) for n in _NUM_RE.findall(source)}
    missing: list[str] = []
    for n in _NUM_RE.findall(content):
        k = _norm_num(n)
        if len(k) <= 1 or k in src or k in missing:
            continue
        missing.append(k)
    return missing


def postprocess(text: str, *, level: int) -> str:
    """Normalise model Markdown: strip fences/titles, fix heading levels, drop empty sections."""
    t = text.strip()
    m = re.fullmatch(r"```(?:markdown|md)?\s*\n(.*?)\n?```", t, flags=re.S | re.I)
    if m:
        t = m.group(1).strip()
    lines = t.splitlines()
    while lines and (lines[0].startswith("# ") or not lines[0].strip()):
        lines.pop(0)
    heads = [len(re.match(r"^(#+)\s", ln).group(1)) for ln in lines if re.match(r"^#{1,6}\s", ln)]
    shift = (level - min(heads)) if heads else 0
    out: list[str] = []
    for ln in lines:
        hm = re.match(r"^(#{1,6})(\s+.*)$", ln)
        if hm:
            new_level = max(level, min(6, len(hm.group(1)) + shift))
            ln = "#" * new_level + hm.group(2).rstrip(" :")
        else:
            ln = re.sub(r"^(\s*)[*•●▪]\s+", r"\1- ", ln)
        out.append(ln.rstrip())
    # Drop "not mentioned in the source" lines, then sections left empty.
    out = [ln for ln in out if not _EMPTY_LINE_RE.match(ln)]
    cleaned: list[str] = []
    i = 0
    while i < len(out):
        ln = out[i]
        if re.match(r"^#{1,6}\s", ln):
            j = i + 1
            while j < len(out) and not out[j].strip():
                j += 1
            nxt_is_head = j >= len(out) or (
                re.match(r"^#{1,6}\s", out[j]) and len(re.match(r"^(#+)", out[j]).group(1)) <= len(re.match(r"^(#+)", ln).group(1))
            )
            if nxt_is_head:
                i = j
                continue
        cleaned.append(ln)
        i += 1
    result = "\n".join(cleaned)
    result = re.sub(r"\n{3,}", "\n\n", result).strip()
    return result


def remove_known_bullets(content: str, existing: str, threshold: int = 92) -> tuple[str, int]:
    """Drop bullets from an update that the existing note already states."""
    known = [normalize_text(ln) for ln in existing.splitlines() if len(ln.strip()) > 20]
    known = [k for k in known if k]
    if not known:
        return content, 0
    out, removed = [], 0
    for ln in content.splitlines():
        m = re.match(r"^\s*-\s+(.*)$", ln)
        if m:
            t = normalize_text(m.group(1))
            if len(t) > 25:
                best = process.extractOne(t, known, scorer=fuzz.token_set_ratio)
                if best and best[1] >= threshold:
                    removed += 1
                    continue
        out.append(ln)
    return postprocess("\n".join(out), level=3) if removed else content, removed


def select_existing_context(existing: str, source: str, max_chars: int) -> str:
    """For long notes, keep the sections most related to the new source (plus the opening)."""
    body = note_body(existing)
    if len(body) <= max_chars:
        return body
    sections = re.split(r"(?m)^(?=#{2,4} )", body)
    src_tokens = set(tokenize(source))
    scored = []
    for i, sec in enumerate(sections):
        toks = set(tokenize(sec))
        overlap = len(toks & src_tokens) / (1 + len(toks) ** 0.5)
        scored.append((i == 0, overlap, i))
    keep, used = set(), 0
    for first, _, i in sorted(scored, key=lambda t: (not t[0], -t[1])):
        if used + len(sections[i]) > max_chars and keep:
            continue
        keep.add(i)
        used += len(sections[i])
    parts, prev = [], -1
    for i in sorted(keep):
        if i != prev + 1:
            parts.append("\n[… other sections of the existing note omitted …]\n")
        parts.append(sections[i][: max_chars])
        prev = i
    headings = re.findall(r"(?m)^#{2,4} .*$", body)
    outline = "Outline of the full note: " + " | ".join(h.lstrip("# ").strip() for h in headings[:40])
    return truncate(outline + "\n\n" + "".join(parts), max_chars + 2000)


def condense(llm: ChatModel, title: str, source: str, chunk_chars: int = 12000) -> str:
    """Map step for very long sources: extract the facts of each chunk as bullets."""
    paras = source.split("\n\n")
    chunks, buf = [], ""
    for p in paras:
        if len(buf) + len(p) > chunk_chars and buf:
            chunks.append(buf)
            buf = ""
        buf += p + "\n\n"
    if buf.strip():
        chunks.append(buf)
    notes = []
    for i, ch in enumerate(chunks, 1):
        prompt = CONDENSE_PROMPT.format(part=i, parts=len(chunks), title=title, chunk=ch)
        notes.append(llm.complete_text(SYSTEM_WRITER, prompt, task="condense", max_tokens=1500))
    return "\n\n".join(notes)


# ------------------------------------------------------------------ generation
def write_new_note(
    llm: ChatModel, *, title: str, location: str, source: str, cfg: PipelineConfig
) -> Generation:
    started = time.perf_counter()
    flags: list[str] = []
    material = source
    if len(source) > cfg.max_generation_source_chars:
        material = condense(llm, title, source)
        flags.append("Long source was condensed before writing; check nothing important was lost.")
    words = word_count(material)
    target = max(cfg.min_note_words, min(cfg.max_note_words, int(words * 0.35)))
    prompt = NOTE_PROMPT.format(
        title=title, location=location, words=words, source=material, target_words=target, max_words=cfg.max_note_words
    )
    raw = llm.complete_text(SYSTEM_WRITER, prompt, task="write", max_tokens=int(cfg.max_note_words * 2.2))
    content = postprocess(raw, level=2)
    flags += _quality_flags(content, source)
    return Generation(content=content, flags=flags, seconds=time.perf_counter() - started)


def write_update(
    llm: ChatModel, *, existing: str, tag: str, source: str, cfg: PipelineConfig
) -> Generation:
    started = time.perf_counter()
    flags: list[str] = []
    material = source
    if len(source) > cfg.max_generation_source_chars:
        material = condense(llm, tag, source)
        flags.append("Long source was condensed before writing; check nothing important was lost.")
    context = select_existing_context(existing, material, cfg.max_existing_note_chars)
    prompt = UPDATE_PROMPT.format(existing=context, tag=tag, source=material, max_words=min(600, cfg.max_note_words))
    raw = llm.complete_text(SYSTEM_WRITER, prompt, task="update", max_tokens=1600)
    if NO_NEW in raw and len(raw.strip()) < len(NO_NEW) + 40:
        return Generation(content=None, flags=flags, seconds=time.perf_counter() - started, no_new_information=True)
    content = postprocess(raw.replace(NO_NEW, ""), level=3)
    content, removed = remove_known_bullets(content, existing)
    if removed:
        flags.append(f"Removed {removed} bullet(s) already present in the existing note.")
    if word_count(content) < 25:
        return Generation(content=None, flags=flags, seconds=time.perf_counter() - started, no_new_information=True)
    flags += _quality_flags(content, source)
    return Generation(content=content, flags=flags, seconds=time.perf_counter() - started)


def _quality_flags(content: str, source: str) -> list[str]:
    flags = []
    missing = unverified_numbers(content, source)
    if missing:
        flags.append("Figures not found in the source (verify): " + ", ".join(missing[:12]))
    if word_count(content) < 60:
        flags.append("Very short note — the source may have been mostly non-textual (images/tables).")
    return flags
