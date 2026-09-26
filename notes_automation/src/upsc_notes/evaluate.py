"""Benchmark a local model on *your* repository — no hand labelling needed.

Existing notes already sit in the right folder, so each sampled note becomes a test case:
  * mode "new":        the note is hidden from the search index; the model must place its text in the
                       note's own subject and module folder (and should not claim it duplicates another note).
  * mode "duplicate":  the note stays in the index; the model should recognise it as the same topic.
Compare models with e.g.  upsc-notes eval --model qwen2.5:7b  vs  --model llama3.1:8b.
"""

from __future__ import annotations

import random
import time
from collections import defaultdict
from typing import Callable, Optional

from .classify import locate, triage
from .config import AppConfig
from .llm import LLMError, build_chat_model, build_embedder
from .models import Segment, Topic
from .retrieval import NoteIndex, note_body
from .taxonomy import Note, scan_repo


def _sample(notes: list[Note], n: int, seed: int) -> list[Note]:
    rng = random.Random(seed)
    by_subject: dict[str, list[Note]] = defaultdict(list)
    for note in notes:
        if note.size > 800:
            by_subject[note.subject].append(note)
    for lst in by_subject.values():
        rng.shuffle(lst)
    out: list[Note] = []
    while len(out) < n and any(by_subject.values()):
        for subj in sorted(by_subject):
            if by_subject[subj] and len(out) < n:
                out.append(by_subject[subj].pop())
    return out


def evaluate(
    cfg: AppConfig,
    *,
    model: Optional[str] = None,
    sample: int = 40,
    seed: int = 7,
    mode: str = "new",
    log: Callable[[str], None] = print,
) -> dict:
    tax = scan_repo(cfg.repo_root, cfg.repo.exclude_dirs, cfg.repo.sources_dir)
    index = NoteIndex(tax, build_embedder(cfg), cfg.data_path / "cache")
    index.build()
    llm = build_chat_model(cfg, model)
    cases = _sample(tax.all_notes(), sample, seed)
    res = defaultdict(float)
    rows = []
    started = time.perf_counter()
    for i, note in enumerate(cases, 1):
        raw = (cfg.repo_root / note.path).read_text(encoding="utf-8", errors="replace")
        seg = Segment(id=i, title=note.title, text=note_body(raw)[:4000])
        exclude = (note.path,) if mode == "new" else ()
        hits = index.search(f"{note.title}. {seg.text[:600]}", k=3, exclude=exclude)
        retrieval_ok = bool(hits) and (hits[0].note.subject, hits[0].note.subfolder) == (note.subject, note.subfolder)
        try:
            tri = triage(llm, seg, tax, "evaluation")
            topic = Topic(
                id=i, title=tri["title"], original_title=note.title, summary=tri["summary"], keywords=tri["keywords"],
                subject=tri["subject"], secondary_subject=tri["secondary_subject"],
            )
            place = locate(llm, topic, seg, tax, index, [], exclude_paths=exclude)
        except LLMError as exc:
            log(f"  [{i}/{len(cases)}] error: {exc}")
            res["errors"] += 1
            continue
        subject_ok = tri["subject"] == note.subject
        folder_ok = (place.subject, place.subfolder) == (note.subject, note.subfolder)
        res["subject"] += subject_ok
        res["folder"] += folder_ok
        res["retrieval_folder"] += retrieval_ok
        if mode == "new":
            res["false_duplicate"] += place.match_path is not None
        else:
            res["duplicate_found"] += place.match_path == note.path
        rows.append((note.path, subject_ok, folder_ok, f"{place.subject}/{place.subfolder}", place.match_path))
        mark = "✓" if folder_ok else ("~" if subject_ok else "✗")
        log(f"  [{i}/{len(cases)}] {mark} {note.path}  →  {place.subject}/{place.subfolder}" + (f"  (same as {place.match_path})" if place.match_path else ""))
    n = max(1, len(cases) - int(res["errors"]))
    elapsed = time.perf_counter() - started
    report = {
        "model": llm.model,
        "mode": mode,
        "cases": len(cases),
        "subject_accuracy": round(res["subject"] / n, 3),
        "folder_accuracy": round(res["folder"] / n, 3),
        "retrieval_only_folder_accuracy": round(res["retrieval_folder"] / n, 3),
        "seconds_per_case": round(elapsed / max(1, len(cases)), 1),
        "errors": int(res["errors"]),
        "llm_calls": llm.usage.calls,
    }
    if mode == "new":
        report["false_duplicate_rate"] = round(res["false_duplicate"] / n, 3)
    else:
        report["duplicate_recall"] = round(res["duplicate_found"] / n, 3)
    report["rows"] = rows
    return report
