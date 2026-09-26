"""Topic triage (relevance, title, subject) and placement (folder + duplicate detection)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .llm import ChatModel
from .models import Candidate, Segment, Topic
from .prompts import LOCATE_PROMPT, SYSTEM_ANALYST, TRIAGE_PROMPT, locate_schema, triage_schema
from .retrieval import Hit, NoteIndex
from .taxonomy import Taxonomy
from .utils import tokenize, truncate

TRIAGE_TEXT_CHARS = 3500
LOCATE_EXCERPT_CHARS = 1200


def _subjects_block(tax: Taxonomy) -> str:
    return "\n".join(f"- {s.name}: {s.hint}" for s in tax.subjects.values())


def triage(llm: ChatModel, seg: Segment, tax: Taxonomy, tag: str) -> dict:
    text = seg.text
    truncated = len(text) > TRIAGE_TEXT_CHARS
    section = (seg.section or "—").split(" › ")[-1]
    prompt = TRIAGE_PROMPT.format(
        tag=tag,
        section=section,
        heading=seg.title,
        text=truncate(text, TRIAGE_TEXT_CHARS),
        truncated=" (first part)" if truncated else "",
        subjects=_subjects_block(tax),
    )
    subjects = list(tax.subjects)
    data = llm.complete_json(SYSTEM_ANALYST, prompt, triage_schema(subjects), task="triage")
    subject = data.get("subject") if data.get("subject") in tax.subjects else subjects[0]
    secondary = data.get("secondary_subject")
    return {
        "title": (str(data.get("title") or seg.title).strip() or seg.title)[:160],
        "summary": str(data.get("summary") or "").strip(),
        "keywords": [str(k).strip() for k in (data.get("keywords") or []) if str(k).strip()][:10],
        "relevant": bool(data.get("relevant", True)),
        "reason": str(data.get("not_relevant_reason") or "").strip(),
        "subject": subject,
        "secondary_subject": secondary if secondary in tax.subjects and secondary != subject else None,
    }


@dataclass
class PendingNote:
    """A note that an earlier topic of the same upload will create (for in-batch duplicates)."""

    topic_id: int
    title: str
    summary: str
    subject: str
    subfolder: str
    vec: Optional[np.ndarray] = None
    tokens: set[str] = field(default_factory=set)


@dataclass
class Placement:
    subject: str
    subfolder: str
    match_path: Optional[str]
    match_topic: Optional[int]
    confidence: float
    reason: str
    alternatives: list[Candidate]
    related: list[Candidate]
    query_vec: Optional[np.ndarray] = None


def _query_text(topic: Topic) -> str:
    return f"{topic.title}. {topic.summary or ''} {' '.join(topic.keywords)}".strip()


def locate(
    llm: ChatModel,
    topic: Topic,
    seg: Segment,
    tax: Taxonomy,
    index: NoteIndex,
    pending: list[PendingNote],
    exclude_paths: tuple[str, ...] = (),
) -> Placement:
    query = _query_text(topic)
    qvec = index.query_vector(query)
    global_hits = index.search(query, k=12, qvec=qvec, exclude=exclude_paths)
    considered = [topic.subject] if topic.subject else []
    if topic.secondary_subject:
        considered.append(topic.secondary_subject)
    for h in global_hits[:3]:
        if h.note.subject not in considered:
            considered.append(h.note.subject)
            break
    considered = [s for s in considered if s in tax.subjects] or list(tax.subjects)[:1]
    hits = index.search(query, subjects=set(considered), k=8, qvec=qvec, exclude=exclude_paths)
    sf_score = index.subfolder_scores(global_hits + hits)

    # Folder list: every module of the primary subject + the best modules of the other candidates.
    folders: list[tuple[str, str]] = []
    primary = tax.subjects[considered[0]]
    ranked_primary = sorted(primary.subfolders, key=lambda n: -sf_score.get((primary.name, n), 0.0))
    folders += [(primary.name, n) for n in ranked_primary]
    for sname in considered[1:]:
        subj = tax.subjects[sname]
        best = sorted(subj.subfolders, key=lambda n: -sf_score.get((sname, n), 0.0))[:4]
        folders += [(sname, n) for n in best]
    folders = folders[:45]
    folder_ids = [f"F{i + 1}" for i in range(len(folders))]
    hit_titles: dict[tuple[str, str], list[str]] = {}
    for h in global_hits + hits:
        hit_titles.setdefault((h.note.subject, h.note.subfolder), []).append(h.note.title)
    folder_lines = []
    for fid, (sname, fname) in zip(folder_ids, folders):
        sf = tax.subjects[sname].subfolders[fname]
        examples = list(dict.fromkeys(hit_titles.get((sname, fname), []) + [n.title for n in sf.notes]))[:5]
        folder_lines.append(
            f"{fid}. {tax.subjects[sname].title} › {sf.title} — " + ("; ".join(examples) if examples else "(empty)")
        )

    # Existing-note candidates + notes this upload will already create.
    note_hits: list[Hit] = hits[:6]
    note_ids = [f"N{i + 1}" for i in range(len(note_hits))]
    note_lines = [
        f"{nid}. [{tax.subjects[h.note.subject].title} › {tax.subjects[h.note.subject].subfolders[h.note.subfolder].title}] "
        f"\"{h.note.title}\" — {h.excerpt[:160]}"
        for nid, h in zip(note_ids, note_hits)
    ]
    batch = _similar_pending(pending, qvec, topic)
    batch_ids = [f"B{i + 1}" for i in range(len(batch))]
    note_lines += [
        f"{bid}. [created earlier from this same upload] \"{p.title}\" — {p.summary[:160]}" for bid, p in zip(batch_ids, batch)
    ]
    prompt = LOCATE_PROMPT.format(
        title=topic.title,
        summary=topic.summary or "—",
        keywords=", ".join(topic.keywords) or "—",
        excerpt=truncate(seg.text, LOCATE_EXCERPT_CHARS),
        folders="\n".join(folder_lines),
        notes="\n".join(note_lines) or "(none)",
    )
    data = llm.complete_json(SYSTEM_ANALYST, prompt, locate_schema(folder_ids, note_ids + batch_ids), task="locate")

    fid = data.get("folder")
    subject, subfolder = folders[folder_ids.index(fid)] if fid in folder_ids else folders[0]
    same = data.get("same_as") or "none"
    match_path = match_topic = None
    support = 0.4
    if same in note_ids:
        h = note_hits[note_ids.index(same)]
        match_path = h.note.path
        subject, subfolder = h.note.subject, h.note.subfolder
        rank = note_ids.index(same)
        support = 1.0 if rank == 0 else 0.8 if rank <= 2 else 0.5
    elif same in batch_ids:
        p = batch[batch_ids.index(same)]
        match_topic = p.topic_id
        subject, subfolder = p.subject, p.subfolder
        support = 0.8
    else:
        top_folders = [(h.note.subject, h.note.subfolder) for h in global_hits[:3]]
        if top_folders and (subject, subfolder) == top_folders[0]:
            support = 1.0
        elif (subject, subfolder) in top_folders:
            support = 0.75
    try:
        llm_conf = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        llm_conf = 0.5
    if llm_conf > 1:
        llm_conf /= 100.0
    llm_conf = min(1.0, max(0.0, llm_conf))
    if topic.subject and subject != topic.subject:
        support *= 0.85
    confidence = round(0.55 * llm_conf + 0.45 * support, 2)

    alternatives: list[Candidate] = []
    for (sname, fname), score in sorted(sf_score.items(), key=lambda kv: -kv[1]):
        if (sname, fname) != (subject, subfolder) and len(alternatives) < 4:
            sf = tax.subjects[sname].subfolders[fname]
            alternatives.append(Candidate(path=sf.path, title=sf.title, subject=sname, subfolder=fname, score=round(score, 3)))
    related = _related(global_hits + hits, exclude={match_path} if match_path else set())
    return Placement(
        subject=subject,
        subfolder=subfolder,
        match_path=match_path,
        match_topic=match_topic,
        confidence=confidence,
        reason=str(data.get("reason") or "").strip(),
        alternatives=alternatives,
        related=related,
        query_vec=qvec,
    )


def _similar_pending(pending: list[PendingNote], qvec: Optional[np.ndarray], topic: Topic, k: int = 3) -> list[PendingNote]:
    if not pending:
        return []
    tokens = set(tokenize(_query_text(topic)))
    scored = []
    for p in pending:
        if qvec is not None and p.vec is not None and p.vec.shape == qvec.shape:
            sim = float(p.vec @ qvec)
            if sim >= 0.62:
                scored.append((sim, p))
        else:
            overlap = len(tokens & p.tokens) / max(1, min(len(tokens), len(p.tokens)))
            if overlap >= 0.3:
                scored.append((overlap, p))
    return [p for _, p in sorted(scored, key=lambda t: -t[0])[:k]]


def _related(hits: list[Hit], exclude: set[str], k: int = 3) -> list[Candidate]:
    out: list[Candidate] = []
    seen: set[str] = set(exclude)
    for h in sorted(hits, key=lambda h: -(h.cosine or 0)):
        if h.note.path in seen:
            continue
        strong = h.cosine >= 0.77 if h.cosine else h.bm25 >= 18
        if not strong:
            continue
        seen.add(h.note.path)
        out.append(
            Candidate(path=h.note.path, title=h.note.title, subject=h.note.subject, subfolder=h.note.subfolder, score=round(h.cosine or h.bm25, 3))
        )
        if len(out) >= k:
            break
    return out
