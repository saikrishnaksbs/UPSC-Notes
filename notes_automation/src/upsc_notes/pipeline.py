"""End-to-end orchestration: extract → segment → triage → place → group → write → (review) → apply."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from .classify import PendingNote, locate, triage
from .config import AppConfig
from .generate import write_new_note, write_update
from .gitops import GitError, commit as git_commit, push_and_open_pr, safe_branch_name
from .ingest import extract_document
from .jobs import Cancelled, JobStore
from .llm import ChatModel, LLMError, build_chat_model, build_embedder
from .models import Job, Proposal, Segment, Topic
from .retrieval import NoteIndex
from .segment import resolve_style_roles, segment_document
from .taxonomy import Taxonomy, scan_repo
from .utils import now_iso, tokenize
from .writer import FileChange, RepoWriter

log = logging.getLogger(__name__)


class Context:
    """Shared, lazily built resources (taxonomy + search index) — rebuilt when the repo changes."""

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self._lock = threading.Lock()
        self._tax: Optional[Taxonomy] = None
        self._index: Optional[NoteIndex] = None
        self._stamp: Optional[tuple] = None

    def _repo_stamp(self) -> tuple:
        tax = scan_repo(self.cfg.repo_root, self.cfg.repo.exclude_dirs, self.cfg.repo.sources_dir)
        notes = tax.all_notes()
        return tax, (len(notes), max((n.mtime for n in notes), default=0), sum(n.size for n in notes))

    def taxonomy(self) -> Taxonomy:
        with self._lock:
            if self._tax is None:
                self._tax, self._stamp = self._repo_stamp()
            return self._tax

    def index(self, progress: Optional[Callable[[int, int, str], None]] = None) -> tuple[Taxonomy, NoteIndex]:
        with self._lock:
            tax, stamp = self._repo_stamp()
            if self._index is None or stamp != self._stamp:
                idx = NoteIndex(tax, build_embedder(self.cfg), self.cfg.data_path / "cache")
                idx.build(progress)
                self._tax, self._index, self._stamp = tax, idx, stamp
            return self._tax, self._index  # type: ignore[return-value]

    def invalidate(self) -> None:
        with self._lock:
            self._stamp = None
            self._tax = None


# ------------------------------------------------------------------ running a job
def run_job(cfg: AppConfig, store: JobStore, ctx: Context, job_id: str, cancel: threading.Event) -> None:
    job = store.load(job_id)
    llm = build_chat_model(cfg, job.options.model)

    def check() -> None:
        if cancel.is_set():
            raise Cancelled()

    def progress(stage: str) -> Callable[[int, int, str], None]:
        def cb(done: int, total: int, msg: str) -> None:
            store.update(job_id, lambda j: _set_progress(j, stage, done, total, msg))

        return cb

    health = llm.health()
    if not health.get("ok", True):
        if not health.get("online", True):
            raise LLMError(f"The local model server is not running ({cfg.llm.base_url}). Start it with `ollama serve`.")
        raise LLMError(f"Model '{llm.model}' is not installed. Run: ollama pull {llm.model}")

    # 1. Extract
    if not store.has(job_id, "document"):
        progress("extracting")(0, 1, "Extracting text")
        doc = extract_document(job.input_path, ocr=cfg.ocr, engine=cfg.extraction.engine, pages=job.options.pages, display_name=job.filename, progress=progress("extracting"))
        if not doc.blocks:
            raise ValueError("; ".join(doc.warnings) or "No text could be extracted from this file.")
        resolve_style_roles(doc, llm)
        store.save_document(job_id, doc)
    check()

    # 2. Segment
    if not store.has(job_id, "segments"):
        progress("segmenting")(0, 1, "Splitting into topics")
        doc = store.load_document(job_id)
        segs = segment_document(doc, llm=llm, min_chars=cfg.pipeline.min_segment_chars, max_chars=cfg.pipeline.max_segment_chars)
        store.save_document(job_id, doc)
        store.save_segments(job_id, segs)
        topics = [
            Topic(id=s.id, title=s.title, original_title=s.title, section=s.section, pages=s.pages, chars=len(s.text))
            for s in segs
        ]
        store.save_topics(job_id, topics)
        store.update(job_id, lambda j: j.stats.update({"segments": len(segs), "warnings": doc.warnings}))
    check()

    # 3. Search index over existing notes
    tax, index = ctx.index(progress("indexing"))
    check()

    # 4. Triage + placement, topic by topic
    segments = {s.id: s for s in store.load_segments(job_id)}
    topics = store.load_topics(job_id)
    pending: list[PendingNote] = []
    for t in topics:
        if t.stage in ("located", "done") and t.relevant and not t.match_path and t.match_topic is None and t.dest_subject:
            pending.append(_pending_from(t, index))
    total = len(topics)
    for n, t in enumerate(topics, 1):
        check()
        if t.stage in ("located", "done", "error"):
            continue
        progress("analysing")(n - 1, total, f"Analysing topic {n}/{total}: {t.title[:60]}")
        seg = segments[t.id]
        try:
            analyse_topic(llm, t, seg, tax, index, pending, job, skip_irrelevant=job.options.skip_irrelevant)
        except LLMError as exc:
            if any(k in str(exc).lower() for k in ("not reachable", "cannot reach", "not enough memory", "not installed")):
                raise  # affects every topic: fail the job with a clear message instead of erroring each one
            t.stage, t.error = "error", str(exc)
        with store.lock(job_id):
            fresh = {x.id: x for x in store.load_topics(job_id)}
            fresh[t.id] = t
            store.save_topics(job_id, list(fresh.values()))
    progress("analysing")(total, total, "Topics analysed")

    # 5. Group topics into proposals (one per target note)
    with store.lock(job_id):
        topics = store.load_topics(job_id)
        proposals = store.load_proposals(job_id)
        proposals = group_proposals(topics, proposals, tax)
        store.save_proposals(job_id, proposals)
        store.save_topics(job_id, topics)

    # 6. Write the notes
    todo = [p for p in proposals if p.status in ("pending", "generating")]
    for n, p in enumerate(todo, 1):
        check()
        progress("writing")(n - 1, len(todo), f"Writing note {n}/{len(todo)}: {p.title[:60]}")
        generate_proposal(cfg, store, llm, job, p.id, tax)
    stats = llm.usage
    store.update(
        job_id,
        lambda j: (
            _set_progress(j, "done", 1, 1, "Ready for review"),
            setattr(j, "status", "review"),
            j.stats.update(
                {
                    "llm_calls": j.stats.get("llm_calls", 0) + stats.calls,
                    "llm_seconds": round(j.stats.get("llm_seconds", 0) + stats.seconds, 1),
                    "prompt_tokens": j.stats.get("prompt_tokens", 0) + stats.prompt_tokens,
                    "completion_tokens": j.stats.get("completion_tokens", 0) + stats.completion_tokens,
                }
            ),
        ),
    )


def _set_progress(j: Job, stage: str, done: int, total: int, msg: str) -> None:
    j.stage, j.progress_done, j.progress_total, j.message = stage, done, total, msg


def _pending_from(t: Topic, index: NoteIndex) -> PendingNote:
    text = f"{t.title}. {t.summary or ''} {' '.join(t.keywords)}"
    return PendingNote(
        topic_id=t.id,
        title=t.title,
        summary=t.summary or "",
        subject=t.dest_subject or "",
        subfolder=t.dest_subfolder or "",
        vec=index.query_vector(text),
        tokens=set(tokenize(text)),
    )


def analyse_topic(
    llm: ChatModel,
    t: Topic,
    seg: Segment,
    tax: Taxonomy,
    index: NoteIndex,
    pending: list[PendingNote],
    job: Job,
    *,
    skip_irrelevant: bool = True,
) -> None:
    if t.stage == "new" and t.original_title.startswith("Front matter") and not t.forced:
        t.relevant, t.skip_reason, t.stage = False, "Front matter (cover, contents, editorial)", "done"
        return
    if t.stage == "new":
        tri = triage(llm, seg, tax, job.tag)
        t.title = tri["title"] or t.title
        t.summary, t.keywords = tri["summary"], tri["keywords"]
        t.subject, t.secondary_subject = tri["subject"], tri["secondary_subject"]
        t.relevant = tri["relevant"] or t.forced
        t.skip_reason = None if t.relevant else (tri["reason"] or "Not study material")
        t.stage = "triaged"
    if t.relevant is False and skip_irrelevant and not t.forced:
        t.stage = "done"
        return
    place = locate(llm, t, seg, tax, index, pending)
    t.dest_subject, t.dest_subfolder = place.subject, place.subfolder
    t.match_path, t.match_topic = place.match_path, place.match_topic
    t.confidence, t.reason = place.confidence, place.reason
    t.alternatives, t.related = place.alternatives, place.related
    t.stage = "located"
    if not t.match_path and t.match_topic is None:
        pending.append(
            PendingNote(
                topic_id=t.id,
                title=t.title,
                summary=t.summary or "",
                subject=t.dest_subject,
                subfolder=t.dest_subfolder,
                vec=place.query_vec,
                tokens=set(tokenize(f"{t.title} {t.summary} {' '.join(t.keywords)}")),
            )
        )


def group_proposals(topics: list[Topic], existing: list[Proposal], tax: Taxonomy) -> list[Proposal]:
    """One proposal per target note. Topics about the same thing are merged (duplicate detection)."""
    by_id = {t.id: t for t in topics}
    assigned = {tid for p in existing for tid in p.topic_ids}
    proposals = list(existing)
    by_key: dict[tuple, Proposal] = {}
    for p in proposals:
        key = ("update", p.target_path) if p.action == "update" else ("create", p.topic_ids[0] if p.topic_ids else p.id)
        by_key[key] = p

    def root(t: Topic) -> Topic:
        seen = set()
        while t.match_topic is not None and t.match_topic in by_id and t.id not in seen:
            seen.add(t.id)
            t = by_id[t.match_topic]
        return t

    for t in topics:
        if t.id in assigned or t.stage != "located" or not t.relevant and not t.forced:
            continue
        r = root(t)
        if r.match_path:
            key = ("update", r.match_path)
        else:
            key = ("create", r.id)
        p = by_key.get(key)
        if p is None or p.status not in ("pending", "generating", "ready"):
            if p is not None:  # already applied/approved — start a new proposal for the newcomer
                key = ("create", t.id) if key[0] == "create" else key
            note = tax.note(r.match_path) if r.match_path else None
            p = Proposal(
                id=f"p{len(proposals) + 1:03d}",
                action="update" if r.match_path else "create",
                subject=(note.subject if note else r.dest_subject) or "",
                subfolder=(note.subfolder if note else r.dest_subfolder) or "",
                title=note.title if note else r.title,
                target_path=r.match_path,
                topic_ids=[],
                confidence=r.confidence or 0.0,
            )
            proposals.append(p)
            by_key[key] = p
        elif p.status == "ready":
            p.status = "pending"  # new material joined an existing draft: rewrite it
        p.topic_ids.append(t.id)
        p.confidence = round(min(p.confidence or 1.0, t.confidence or 0.0), 2)
        seen = {c.path for c in p.related}
        p.related += [c for c in t.related if c.path not in seen and c.path != p.target_path][:3]
        t.proposal_id = p.id
        t.stage = "done"
    return proposals


def proposal_source(p: Proposal, segments: dict[int, Segment], topics: dict[int, Topic]) -> str:
    parts = []
    for tid in p.topic_ids:
        seg = segments.get(tid)
        if not seg:
            continue
        t = topics.get(tid)
        head = f"[Article: {t.title if t else seg.title}" + (f", pages {seg.pages}" if seg.pages else "") + "]"
        parts.append(head + "\n" + seg.text.strip())
    return "\n\n".join(parts)


def generate_proposal(cfg: AppConfig, store: JobStore, llm: ChatModel, job: Job, pid: str, tax: Taxonomy) -> Proposal:
    segments = {s.id: s for s in store.load_segments(job.id)}
    topics = {t.id: t for t in store.load_topics(job.id)}
    p = next(x for x in store.load_proposals(job.id) if x.id == pid)
    store.update_proposal(job.id, pid, lambda x: setattr(x, "status", "generating"))
    source = proposal_source(p, segments, topics)
    subj = tax.subjects.get(p.subject)
    sf = subj.subfolders.get(p.subfolder) if subj else None
    location = f"{subj.title if subj else p.subject} › {sf.title if sf else p.subfolder}"
    started = time.perf_counter()
    try:
        if p.action == "update" and p.target_path:
            existing = (cfg.repo_root / p.target_path).read_text(encoding="utf-8", errors="replace")
            gen = write_update(llm, existing=existing, tag=job.tag, source=source, cfg=cfg.pipeline)
        else:
            gen = write_new_note(llm, title=p.title, location=location, source=source, cfg=cfg.pipeline)
        error = None
    except (LLMError, OSError) as exc:
        gen, error = None, str(exc)
    threshold = job.options.auto_approve_threshold or cfg.pipeline.auto_approve_threshold

    def apply(x: Proposal) -> None:
        x.generation_seconds = round(time.perf_counter() - started, 1)
        if error:
            x.status, x.error = "error", error
            return
        x.content = gen.content
        x.flags = gen.flags
        x.no_new_information = gen.no_new_information
        x.edited = False
        x.error = None
        if gen.no_new_information:
            x.flags = ["The existing note already covers everything in this source — nothing new to add."] + x.flags
            x.status = "ready"
        elif threshold is not None and x.confidence >= threshold and not x.flags:
            x.status = "approved"
        else:
            x.status = "ready"

    return store.update_proposal(job.id, pid, apply)


# ------------------------------------------------------------------ review helpers
def plan_changes(
    cfg: AppConfig, store: JobStore, ctx: Context, job_id: str, proposal_ids: list[str]
) -> tuple[RepoWriter, list[Proposal], dict[str, str]]:
    job = store.load(job_id)
    tax = ctx.taxonomy()
    topics = {t.id: t for t in store.load_topics(job_id)}
    segments = {s.id: s for s in store.load_segments(job_id)}
    proposals = store.load_proposals(job_id)
    selected = [p for p in proposals if p.id in proposal_ids]
    writer = RepoWriter(cfg, tax)
    errors: dict[str, str] = {}
    planned: list[Proposal] = []
    for p in selected:
        try:
            writer.plan(p, job, topics, segments)
            planned.append(p)
        except ValueError as exc:
            errors[p.id] = str(exc)
    # Traceability index for this source: previously applied topics + the ones being applied now.
    entries = []
    for p in proposals:
        path = writer.note_paths.get(p.id) or (p.applied_path if p.status == "applied" else None)
        if not path:
            continue
        for tid in p.topic_ids:
            if tid in topics:
                entries.append({"topic": topics[tid], "action": "updated" if p.action == "update" else "created", "note_path": path, "note_title": p.title})
    if entries:
        writer.plan_source_index(job, entries)
    return writer, planned, errors


def preview_proposal(cfg: AppConfig, store: JobStore, ctx: Context, job_id: str, pid: str) -> dict:
    p = next(x for x in store.load_proposals(job_id) if x.id == pid)
    if not p.content:
        return {"note_path": None, "note": None, "changes": [], "error": p.error or "No content yet"}
    writer, planned, errors = plan_changes(cfg, store, ctx, job_id, [pid])
    if pid in errors:
        return {"note_path": None, "note": None, "changes": [], "error": errors[pid]}
    path = writer.note_paths[pid]
    changes: list[FileChange] = writer.changes()
    main = next((c for c in changes if c.path == path), None)
    return {
        "note_path": path,
        "note": main.after if main else None,
        "added": _added_text(main) if main and p.action == "update" else None,
        "changes": [{"path": c.path, "kind": c.kind, "new": c.before is None, "diff": c.diff()} for c in changes],
        "warnings": writer.warnings,
    }


def _added_text(ch: FileChange) -> str:
    before = ch.before or ""
    return ch.after[len(before.rstrip("\n")) :].strip() if ch.after.startswith(before.rstrip("\n")) else ch.after


def apply_job(
    cfg: AppConfig,
    store: JobStore,
    ctx: Context,
    job_id: str,
    *,
    proposal_ids: Optional[list[str]] = None,
    commit: bool = False,
    branch: Optional[str] = None,
    push_pr: bool = False,
) -> dict:
    with store.lock(job_id):
        job = store.load(job_id)
        proposals = store.load_proposals(job_id)
        ids = [
            p.id
            for p in proposals
            if p.status == "approved" and p.content and (proposal_ids is None or p.id in proposal_ids)
        ]
        if not ids:
            return {"written": [], "applied": [], "errors": {}, "warnings": ["No approved proposals with content to apply."]}
        writer, planned, errors = plan_changes(cfg, store, ctx, job_id, ids)
        written = writer.commit(cfg.data_path / "backups")
        stamp = now_iso()
        applied = []
        for p in proposals:
            if p.id in writer.note_paths and p.id not in errors:
                p.status, p.applied_path, p.applied_at = "applied", writer.note_paths[p.id], stamp
                applied.append(p.id)
        store.save_proposals(job_id, proposals)
        ctx.invalidate()
    result: dict = {"written": written, "applied": applied, "errors": errors, "warnings": writer.warnings}
    if commit and written:
        branch_name = branch or f"{cfg.git.branch_prefix}{job.source_id}"
        msg = f"Add UPSC notes from {job.tag}\n\n{len(applied)} note change(s) generated from {job.filename} by upsc-notes ({job.model})."
        try:
            res = git_commit(cfg.repo_root, written, msg, branch=safe_branch_name(branch_name))
            result["commit"] = res
            store.update(job_id, lambda j: j.applied_commits.append(res["commit"]))
            if push_pr:
                body = _pr_body(job, proposals, applied)
                result["pr"] = push_and_open_pr(cfg.repo_root, res["branch"], f"Notes from {job.tag}", body, cfg.git.remote)
        except GitError as exc:
            result["git_error"] = str(exc)
    remaining = [p for p in store.load_proposals(job_id) if p.status in ("pending", "generating", "ready", "approved")]
    if not remaining:
        store.update(job_id, lambda j: setattr(j, "status", "applied"))
    return result


def _pr_body(job: Job, proposals: list[Proposal], applied: list[str]) -> str:
    lines = [f"Notes generated from **{job.tag}** (`{job.filename}`) with local model `{job.model}`.", ""]
    for p in proposals:
        if p.id in applied:
            verb = "Updated" if p.action == "update" else "Added"
            lines.append(f"- {verb} `{p.applied_path}`")
    return "\n".join(lines)
