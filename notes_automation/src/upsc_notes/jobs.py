"""Persistent job store and the background worker that runs the pipeline.

Each upload is a job folder under .data/jobs/<id>/ holding the input file and JSON state for every
stage, written after each topic — so a crash or restart resumes where it stopped.
"""

from __future__ import annotations

import logging
import os
import queue
import re
import shutil
import threading
import traceback
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Optional

from .config import AppConfig
from .models import ExtractedDocument, Job, JobOptions, Proposal, Segment, Topic
from .utils import atomic_write_json, now_iso, read_json, sha256_file, slugify, today
from .writer import _front_matter

log = logging.getLogger(__name__)


class JobStore:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.root = cfg.data_path / "jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.RLock] = {}
        self._guard = threading.Lock()

    # ------------------------------------------------------------------ locking
    @contextmanager
    def lock(self, job_id: str) -> Iterator[None]:
        with self._guard:
            lk = self._locks.setdefault(job_id, threading.RLock())
        with lk:
            yield

    def dir(self, job_id: str) -> Path:
        if not re.fullmatch(r"[a-z0-9-]{6,64}", job_id):
            raise KeyError(job_id)
        return self.root / job_id

    # ------------------------------------------------------------------ jobs
    def create(
        self,
        src_path: Path,
        filename: str,
        tag: str,
        source_url: Optional[str],
        options: JobOptions,
        move: bool = False,
    ) -> Job:
        job_id = f"{today().replace('-', '')}-{uuid.uuid4().hex[:8]}"
        d = self.dir(job_id)
        (d / "input").mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^\w.\- ()]+", "_", Path(filename).name).strip() or "upload"
        dest = d / "input" / safe_name
        (shutil.move if move else shutil.copy2)(str(src_path), str(dest))
        sha = sha256_file(dest)
        job = Job(
            id=job_id,
            created_at=now_iso(),
            updated_at=now_iso(),
            tag=tag.strip(),
            source_url=(source_url or "").strip() or None,
            filename=safe_name,
            input_path=str(dest),
            file_sha256=sha,
            source_id=self._unique_source_id(tag),
            options=options,
            model=options.model or self.cfg.llm.model,
        )
        job.duplicate_of = self.find_previous_ingestion(sha, exclude=job_id)
        self.save(job)
        return job

    def _unique_source_id(self, tag: str) -> str:
        base = f"{today()}-{slugify(tag, 50)}"
        taken = {j.source_id for j in self.list()}
        if self.cfg.sources_root.is_dir():
            taken |= {p.name for p in self.cfg.sources_root.iterdir()}
        sid, n = base, 2
        while sid in taken:
            sid, n = f"{base}-{n}", n + 1
        return sid

    def find_previous_ingestion(self, sha: str, exclude: str = "") -> Optional[str]:
        for j in self.list():
            if j.id != exclude and j.file_sha256 == sha and j.status != "cancelled":
                return f"job {j.id} ({j.tag})"
        if self.cfg.sources_root.is_dir():
            for d in self.cfg.sources_root.iterdir():
                readme = d / "README.md"
                if readme.is_file() and _front_matter(readme.read_text(encoding="utf-8", errors="replace")).get("sha256") == sha:
                    return f"{self.cfg.repo.sources_dir}/{d.name}"
        return None

    def save(self, job: Job) -> None:
        job.updated_at = now_iso()
        atomic_write_json(self.dir(job.id) / "job.json", job.model_dump())

    def load(self, job_id: str) -> Job:
        data = read_json(self.dir(job_id) / "job.json")
        if data is None:
            raise KeyError(job_id)
        return Job.model_validate(data)

    def update(self, job_id: str, fn: Callable[[Job], None]) -> Job:
        with self.lock(job_id):
            job = self.load(job_id)
            fn(job)
            self.save(job)
            return job

    def list(self) -> list[Job]:
        jobs = []
        for d in self.root.iterdir() if self.root.exists() else []:
            data = read_json(d / "job.json")
            if data:
                try:
                    jobs.append(Job.model_validate(data))
                except ValueError:
                    continue
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)

    def delete(self, job_id: str) -> None:
        shutil.rmtree(self.dir(job_id), ignore_errors=True)

    # ------------------------------------------------------------------ cross-process run lock
    def acquire_run_lock(self, job_id: str) -> bool:
        """Only one process (web server or CLI) may run a job at a time."""
        path = self.dir(job_id) / ".running"
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                pid = int(path.read_text().strip() or 0)
            except (OSError, ValueError):
                pid = 0
            if pid and pid != os.getpid() and _pid_alive(pid):
                return False
            path.unlink(missing_ok=True)
            return self.acquire_run_lock(job_id)
        with os.fdopen(fd, "w") as fh:
            fh.write(str(os.getpid()))
        return True

    def release_run_lock(self, job_id: str) -> None:
        path = self.dir(job_id) / ".running"
        try:
            if int(path.read_text().strip() or 0) == os.getpid():
                path.unlink()
        except (OSError, ValueError):
            pass

    def is_running_elsewhere(self, job_id: str) -> bool:
        path = self.dir(job_id) / ".running"
        try:
            pid = int(path.read_text().strip() or 0)
        except (OSError, ValueError):
            return False
        return bool(pid) and pid != os.getpid() and _pid_alive(pid)


    # ------------------------------------------------------------------ stage artefacts
    def has(self, job_id: str, name: str) -> bool:
        return (self.dir(job_id) / f"{name}.json").exists()

    def save_document(self, job_id: str, doc: ExtractedDocument) -> None:
        atomic_write_json(self.dir(job_id) / "document.json", doc.model_dump())

    def load_document(self, job_id: str) -> Optional[ExtractedDocument]:
        data = read_json(self.dir(job_id) / "document.json")
        return ExtractedDocument.model_validate(data) if data else None

    def save_segments(self, job_id: str, segs: list[Segment]) -> None:
        atomic_write_json(self.dir(job_id) / "segments.json", [s.model_dump() for s in segs])

    def load_segments(self, job_id: str) -> list[Segment]:
        return [Segment.model_validate(s) for s in read_json(self.dir(job_id) / "segments.json", [])]

    def save_topics(self, job_id: str, topics: list[Topic]) -> None:
        atomic_write_json(self.dir(job_id) / "topics.json", [t.model_dump() for t in topics])

    def load_topics(self, job_id: str) -> list[Topic]:
        return [Topic.model_validate(t) for t in read_json(self.dir(job_id) / "topics.json", [])]

    def save_proposals(self, job_id: str, proposals: list[Proposal]) -> None:
        atomic_write_json(self.dir(job_id) / "proposals.json", [p.model_dump() for p in proposals])

    def load_proposals(self, job_id: str) -> list[Proposal]:
        return [Proposal.model_validate(p) for p in read_json(self.dir(job_id) / "proposals.json", [])]

    def update_proposal(self, job_id: str, pid: str, fn: Callable[[Proposal], None]) -> Proposal:
        with self.lock(job_id):
            props = self.load_proposals(job_id)
            for p in props:
                if p.id == pid:
                    fn(p)
                    self.save_proposals(job_id, props)
                    return p
        raise KeyError(pid)

    def update_topic(self, job_id: str, tid: int, fn: Callable[[Topic], None]) -> Topic:
        with self.lock(job_id):
            topics = self.load_topics(job_id)
            for t in topics:
                if t.id == tid:
                    fn(t)
                    self.save_topics(job_id, topics)
                    return t
        raise KeyError(tid)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Cancelled(Exception):
    pass


class Worker:
    """Runs one job at a time (the local LLM is the bottleneck) on a background thread."""

    def __init__(self, store: JobStore, runner: Callable[[str, threading.Event], None]) -> None:
        self.store = store
        self.runner = runner
        self.queue: "queue.Queue[str]" = queue.Queue()
        self.cancel_flags: dict[str, threading.Event] = {}
        self.current: Optional[str] = None
        self._thread = threading.Thread(target=self._loop, name="upsc-notes-worker", daemon=True)
        self._started = False

    def start(self, resume: bool = True) -> None:
        if self._started:
            return
        self._started = True
        self._thread.start()
        if resume:
            for job in reversed(self.store.list()):
                if job.status in ("queued", "running") and not self.store.is_running_elsewhere(job.id):
                    self.submit(job.id)

    def submit(self, job_id: str) -> None:
        self.cancel_flags[job_id] = threading.Event()
        self.store.update(job_id, lambda j: (setattr(j, "status", "queued"), setattr(j, "error", None)))
        self.queue.put(job_id)

    def cancel(self, job_id: str) -> None:
        flag = self.cancel_flags.get(job_id)
        if flag:
            flag.set()
        if self.current != job_id:
            self.store.update(job_id, lambda j: setattr(j, "status", "cancelled"))

    def _loop(self) -> None:
        while True:
            job_id = self.queue.get()
            flag = self.cancel_flags.setdefault(job_id, threading.Event())
            try:
                job = self.store.load(job_id)
            except KeyError:
                continue
            if flag.is_set() or job.status == "cancelled":
                continue
            if not self.store.acquire_run_lock(job_id):
                log.warning("Job %s is being processed by another process; not starting it here", job_id)
                continue
            self.current = job_id
            try:
                self.store.update(job_id, lambda j: setattr(j, "status", "running"))
                self.runner(job_id, flag)
            except Cancelled:
                self.store.update(job_id, lambda j: (setattr(j, "status", "cancelled"), setattr(j, "message", "Cancelled")))
            except Exception as exc:  # keep the worker alive
                log.error("Job %s failed: %s\n%s", job_id, exc, traceback.format_exc())
                msg = str(exc) or exc.__class__.__name__
                self.store.update(job_id, lambda j: (setattr(j, "status", "failed"), setattr(j, "error", msg)))
            finally:
                self.current = None
                self.store.release_run_lock(job_id)
