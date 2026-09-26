"""Local web UI + JSON API (FastAPI). Bound to 127.0.0.1 by default."""

from __future__ import annotations

import logging
import shutil
from contextlib import asynccontextmanager
import tempfile
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from markdown_it import MarkdownIt
from pydantic import BaseModel

from .. import __version__
from ..config import AppConfig, load_config
from ..gitops import status as git_status
from ..ingest import SUPPORTED_EXTS
from ..ingest.ocr import tesseract_available
from ..jobs import JobStore, Worker
from ..llm import build_chat_model, list_ollama_models
from ..models import JobOptions, Proposal
from ..pipeline import Context, analyse_topic, apply_job, generate_proposal, group_proposals, preview_proposal, run_job

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
_md = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table").enable("strikethrough")


class ProposalPatch(BaseModel):
    status: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    subject: Optional[str] = None
    subfolder: Optional[str] = None
    action: Optional[str] = None
    target_path: Optional[str] = None


class ApplyRequest(BaseModel):
    proposal_ids: Optional[list[str]] = None
    commit: bool = False
    branch: Optional[str] = None
    push_pr: bool = False


class ApproveRequest(BaseModel):
    min_confidence: float = 0.0
    include_flagged: bool = False


class RenderRequest(BaseModel):
    markdown: str


def create_app(cfg: Optional[AppConfig] = None, start_worker: bool = True) -> FastAPI:
    cfg = cfg or load_config()
    store = JobStore(cfg)
    ctx = Context(cfg)
    worker = Worker(store, lambda job_id, cancel: run_job(cfg, store, ctx, job_id, cancel))
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if start_worker:
            worker.start()
        yield

    app = FastAPI(title="UPSC Notes Studio", version=__version__, lifespan=lifespan)
    app.state.cfg, app.state.store, app.state.worker, app.state.ctx = cfg, store, worker, ctx
    busy: dict[str, str] = {}  # proposal/topic -> running background action

    # ------------------------------------------------------------------ status
    @app.get("/api/status")
    def get_status() -> dict:
        models = list_ollama_models(cfg.llm.base_url) if cfg.llm.provider == "ollama" else []
        llm = build_chat_model(cfg)
        health = llm.health()
        return {
            "version": __version__,
            "llm": {"provider": cfg.llm.provider, "base_url": cfg.llm.base_url, "model": cfg.llm.model, **health},
            "embeddings": {"provider": cfg.embeddings.provider, "model": cfg.embeddings.model},
            "models": [m for m in models if m["kind"] == "chat"],
            "ocr": {"engine": cfg.ocr.engine, "tesseract": tesseract_available(cfg.ocr.tesseract_cmd)},
            "repo": {"path": str(cfg.repo_root), **git_status(cfg.repo_root)},
            "worker": {"current": worker.current, "queued": worker.queue.qsize()},
            "accepts": sorted(SUPPORTED_EXTS),
            "defaults": {"auto_approve_threshold": cfg.pipeline.auto_approve_threshold},
        }

    @app.get("/api/taxonomy")
    def get_taxonomy(notes: bool = True) -> dict:
        return ctx.taxonomy().to_dict(include_notes=notes)

    # ------------------------------------------------------------------ jobs
    @app.get("/api/jobs")
    def list_jobs() -> list[dict]:
        out = []
        for j in store.list():
            props = store.load_proposals(j.id)
            out.append(
                {
                    **j.model_dump(exclude={"input_path"}),
                    "counts": _counts(props),
                }
            )
        return out

    @app.post("/api/jobs")
    async def create_jobs(
        files: list[UploadFile] = File(...),
        tag: str = Form(...),
        source_url: str = Form(""),
        pages: str = Form(""),
        model: str = Form(""),
        skip_irrelevant: bool = Form(True),
        auto_approve_threshold: str = Form(""),
    ) -> list[dict]:
        if not tag.strip():
            raise HTTPException(400, "A source tag is required (e.g. 'Vision IAS September 2026').")
        threshold = float(auto_approve_threshold) if auto_approve_threshold.strip() else None
        jobs = []
        for up in files:
            ext = Path(up.filename or "").suffix.lower()
            if ext not in SUPPORTED_EXTS:
                raise HTTPException(400, f"{up.filename}: unsupported file type {ext or '(none)'}")
            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                shutil.copyfileobj(up.file, tmp)
                tmp_path = Path(tmp.name)
            opts = JobOptions(
                model=model.strip() or None,
                pages=pages.strip() or None,
                skip_irrelevant=skip_irrelevant,
                auto_approve_threshold=threshold,
            )
            job = store.create(tmp_path, up.filename or f"upload{ext}", tag, source_url, opts, move=True)
            worker.submit(job.id)
            jobs.append(job.model_dump(exclude={"input_path"}))
        return jobs

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        job = _job(job_id)
        doc = store.load_document(job_id) if store.has(job_id, "document") else None
        props = store.load_proposals(job_id)
        return {
            "job": job.model_dump(exclude={"input_path"}),
            "topics": [t.model_dump() for t in store.load_topics(job_id)],
            "proposals": [p.model_dump() for p in props],
            "counts": _counts(props),
            "document": {
                "kind": doc.kind,
                "page_count": doc.page_count,
                "pages_processed": len(doc.pages_processed),
                "warnings": doc.warnings,
                "styles": [s.model_dump() for s in doc.styles],
                "style_roles_by": doc.style_roles_by,
                "ocr_pages": doc.ocr_pages,
            }
            if doc
            else None,
            "busy": {k: v for k, v in busy.items() if k.startswith(job_id)},
        }

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict:
        _job(job_id)
        worker.cancel(job_id)
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/resume")
    def resume_job(job_id: str) -> dict:
        job = _job(job_id)
        if job.status in ("running", "queued") and (worker.current == job_id or store.is_running_elsewhere(job_id)):
            raise HTTPException(409, "Job is already running")
        worker.submit(job_id)
        return {"ok": True}

    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: str) -> dict:
        _job(job_id)
        if worker.current == job_id:
            raise HTTPException(409, "Cancel the job before deleting it")
        worker.cancel(job_id)
        store.delete(job_id)
        return {"ok": True}

    @app.get("/api/jobs/{job_id}/segments/{sid}")
    def get_segment(job_id: str, sid: int) -> dict:
        _job(job_id)
        seg = next((s for s in store.load_segments(job_id) if s.id == sid), None)
        if not seg:
            raise HTTPException(404, "Segment not found")
        return {**seg.model_dump(), "html": _md.render(seg.text)}

    @app.get("/api/jobs/{job_id}/document")
    def get_document(job_id: str) -> JSONResponse:
        _job(job_id)
        doc = store.load_document(job_id)
        if not doc:
            raise HTTPException(404, "Not extracted yet")
        return JSONResponse({"markdown": doc.to_markdown()})

    # ------------------------------------------------------------------ proposals
    @app.patch("/api/jobs/{job_id}/proposals/{pid}")
    def patch_proposal(job_id: str, pid: str, patch: ProposalPatch) -> dict:
        _job(job_id)
        tax = ctx.taxonomy()
        needs_regen = False

        def apply(p: Proposal) -> None:
            nonlocal needs_regen
            if p.status == "applied":
                raise HTTPException(409, "This proposal was already applied")
            if patch.title is not None and patch.title.strip():
                p.title = patch.title.strip()
            if patch.content is not None:
                p.content, p.edited = patch.content, True
                p.no_new_information = False
            if patch.action is not None or patch.target_path is not None or patch.subject is not None or patch.subfolder is not None:
                action = patch.action or p.action
                if action == "update":
                    target = patch.target_path or p.target_path
                    note = tax.note(target or "")
                    if note is None:
                        raise HTTPException(400, f"Existing note not found: {target}")
                    if target != p.target_path or p.action != "update":
                        needs_regen = True
                    p.action, p.target_path, p.subject, p.subfolder, p.title = "update", note.path, note.subject, note.subfolder, note.title
                else:
                    subject = patch.subject or p.subject
                    subfolder = patch.subfolder or p.subfolder
                    if tax.subfolder(subject, subfolder) is None:
                        raise HTTPException(400, f"Folder not found: {subject}/{subfolder}")
                    if p.action != "create":
                        needs_regen = True
                        topics = {t.id: t for t in store.load_topics(job_id)}
                        first = topics.get(p.topic_ids[0]) if p.topic_ids else None
                        p.title = first.title if first else p.title
                    p.action, p.target_path, p.subject, p.subfolder = "create", None, subject, subfolder
            if patch.status is not None:
                if patch.status not in ("approved", "rejected", "ready"):
                    raise HTTPException(400, "status must be approved, rejected or ready")
                if patch.status == "approved" and not p.content:
                    raise HTTPException(400, "Nothing to approve — this proposal has no content")
                p.status = patch.status  # type: ignore[assignment]
            if needs_regen:
                p.status, p.content = "pending", None

        p = store.update_proposal(job_id, pid, apply)
        if needs_regen:
            _background(job_id, pid, lambda: _regenerate(job_id, pid))
        return p.model_dump()

    @app.post("/api/jobs/{job_id}/proposals/{pid}/regenerate")
    def regenerate(job_id: str, pid: str) -> dict:
        _job(job_id)
        store.update_proposal(job_id, pid, lambda p: setattr(p, "status", "pending"))
        _background(job_id, pid, lambda: _regenerate(job_id, pid))
        return {"ok": True}

    @app.get("/api/jobs/{job_id}/proposals/{pid}/preview")
    def preview(job_id: str, pid: str) -> dict:
        _job(job_id)
        try:
            res = preview_proposal(cfg, store, ctx, job_id, pid)
        except StopIteration:
            raise HTTPException(404, "Proposal not found")
        if res.get("note"):
            res["html"] = _md.render(res.get("added") or res["note"])
        return res

    @app.post("/api/jobs/{job_id}/topics/{tid}/include")
    def include_topic(job_id: str, tid: int) -> dict:
        """Process a topic that triage skipped as irrelevant."""
        _job(job_id)

        def fn(t) -> None:
            t.forced, t.relevant, t.skip_reason = True, True, None
            if t.stage in ("done", "error") and not t.proposal_id:
                t.stage = "triaged" if t.subject else "new"

        store.update_topic(job_id, tid, fn)
        _background(job_id, f"t{tid}", lambda: _include(job_id, tid))
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/approve")
    def approve_many(job_id: str, req: ApproveRequest) -> dict:
        _job(job_id)
        changed = []
        with store.lock(job_id):
            props = store.load_proposals(job_id)
            for p in props:
                if p.status == "ready" and p.content and p.confidence >= req.min_confidence and (req.include_flagged or not p.flags):
                    p.status = "approved"
                    changed.append(p.id)
            store.save_proposals(job_id, props)
        return {"approved": changed}

    @app.post("/api/jobs/{job_id}/apply")
    def apply(job_id: str, req: ApplyRequest) -> dict:
        _job(job_id)
        if worker.current == job_id:
            raise HTTPException(409, "Wait for the job to finish before applying")
        return apply_job(
            cfg, store, ctx, job_id, proposal_ids=req.proposal_ids, commit=req.commit, branch=req.branch, push_pr=req.push_pr
        )

    @app.post("/api/render")
    def render(req: RenderRequest) -> dict:
        return {"html": _md.render(req.markdown)}

    # ------------------------------------------------------------------ helpers
    def _job(job_id: str):
        try:
            return store.load(job_id)
        except KeyError:
            raise HTTPException(404, "Job not found")

    def _background(job_id: str, key: str, fn) -> None:
        busy_key = f"{job_id}:{key}"
        if busy_key in busy:
            raise HTTPException(409, "Already working on this item")
        busy[busy_key] = "running"

        def run() -> None:
            try:
                fn()
            except Exception as exc:  # surface in the proposal/topic state
                log.exception("Background task failed")
                if key.startswith("p"):
                    store.update_proposal(job_id, key, lambda p: (setattr(p, "status", "error"), setattr(p, "error", str(exc))))
            finally:
                busy.pop(busy_key, None)

        threading.Thread(target=run, daemon=True).start()

    def _regenerate(job_id: str, pid: str) -> None:
        job = store.load(job_id)
        llm = build_chat_model(cfg, job.options.model)
        generate_proposal(cfg, store, llm, job, pid, ctx.taxonomy())

    def _include(job_id: str, tid: int) -> None:
        job = store.load(job_id)
        llm = build_chat_model(cfg, job.options.model)
        tax, index = ctx.index()
        segs = {s.id: s for s in store.load_segments(job_id)}
        topic = next(t for t in store.load_topics(job_id) if t.id == tid)
        analyse_topic(llm, topic, segs[tid], tax, index, [], job, skip_irrelevant=False)
        with store.lock(job_id):
            topics = store.load_topics(job_id)
            topics = [topic if t.id == tid else t for t in topics]
            props = group_proposals(topics, store.load_proposals(job_id), tax)
            store.save_topics(job_id, topics)
            store.save_proposals(job_id, props)
        pid = next(t.proposal_id for t in topics if t.id == tid)
        if pid:
            generate_proposal(cfg, store, llm, job, pid, tax)

    # ------------------------------------------------------------------ static UI
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    @app.middleware("http")
    async def no_cache_static(request, call_next):
        resp = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    return app


def _counts(props: list[Proposal]) -> dict:
    counts = {"total": len(props), "create": 0, "update": 0}
    for p in props:
        counts[p.action] += 1
        counts[p.status] = counts.get(p.status, 0) + 1
        if p.status == "ready" and (p.flags or p.confidence < 0.6):
            counts["needs_review"] = counts.get("needs_review", 0) + 1
    return counts
