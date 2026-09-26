"""End-to-end with the scripted LLM: upload → proposals → approve → apply → git commit."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest

import upsc_notes.pipeline as pipeline_mod
from upsc_notes.gitops import commit as git_commit
from upsc_notes.jobs import JobStore
from upsc_notes.models import JobOptions
from upsc_notes.pipeline import Context, apply_job, run_job

SOURCE = """# Current Affairs — September 2026

## Special Intensive Revision of Electoral Rolls
The Election Commission began a Special Intensive Revision (SIR) of electoral rolls in 12 States in 2026.
Article 324 empowers the Election Commission to superintend elections. Critics raised concerns about exclusion of voters.

## Gaganyaan Uncrewed Test Flight
ISRO completed the first uncrewed orbital test flight G1 of the Gaganyaan spacecraft in 2026.
The crew module splashed down in the Bay of Bengal after two days in orbit.

## Admission Open for Prelims Test Series
Enrol now for our flagship test series! Admission open, limited seats, call today.
"""


@pytest.fixture()
def patched(monkeypatch, fake_llm):
    monkeypatch.setattr(pipeline_mod, "build_chat_model", lambda cfg, model=None: fake_llm)
    return fake_llm


def _run(cfg, tmp_path: Path, text: str = SOURCE, **opts):
    src = tmp_path / "ca.md"
    src.write_text(text)
    store, ctx = JobStore(cfg), Context(cfg)
    job = store.create(src, src.name, "Vision IAS September 2026", None, JobOptions(**opts))
    run_job(cfg, store, ctx, job.id, threading.Event())
    return store, ctx, job


def test_pipeline_end_to_end(cfg, repo, tmp_path, patched):
    store, ctx, job = _run(cfg, tmp_path)
    job = store.load(job.id)
    assert job.status == "review"
    topics = {t.title: t for t in store.load_topics(job.id)}
    assert topics["Admission Open for Prelims Test Series"].relevant is False
    props = store.load_proposals(job.id)
    by_action = {p.action: p for p in props}
    assert set(by_action) == {"create", "update"}
    upd = by_action["update"]
    assert upd.target_path == "Science_and_technology/02-space-technology/10-gaganyaan.md"
    assert "led by ISRO" not in upd.content, "bullet already in the note must be removed"
    assert any("already present" in f for f in upd.flags)
    new = by_action["create"]
    assert (new.subject, new.subfolder) == ("Polity", "25-constitutional-bodies")
    assert new.content.startswith("## Why in News") and "Not mentioned" not in new.content
    assert all(p.status == "ready" for p in props)

    for p in props:
        store.update_proposal(job.id, p.id, lambda x: setattr(x, "status", "approved"))
    res = apply_job(cfg, store, ctx, job.id, commit=True, branch="notes/test-run")
    assert not res["errors"], res
    assert res["commit"]["branch"] == "notes/test-run"
    created = [w for w in res["written"] if w.startswith("Polity/25-constitutional-bodies/02-")]
    assert created, res["written"]
    log = subprocess.run(["git", "-C", str(repo), "show", "--stat", "--format=%s", "HEAD"], capture_output=True, text=True).stdout
    assert "Add UPSC notes from Vision IAS September 2026" in log
    assert all(p.status == "applied" for p in store.load_proposals(job.id))
    assert store.load(job.id).status == "applied"

    # Re-ingesting the same file is flagged, and the note it already updated gets nothing twice.
    store2, ctx2, job2 = _run(cfg, tmp_path)
    assert job2.duplicate_of is not None
    props2 = store2.load_proposals(job2.id)
    # the SIR note created by the first run is now found and updated instead of duplicated
    assert any(p.action == "update" and p.target_path == created[0] for p in props2)
    upd2 = next(p for p in props2 if p.target_path == "Science_and_technology/02-space-technology/10-gaganyaan.md")
    assert upd2.no_new_information and upd2.content is None
    for p in store2.load_proposals(job2.id):
        if p.content:
            store2.update_proposal(job2.id, p.id, lambda x: setattr(x, "status", "approved"))
    apply_job(cfg, store2, ctx2, job2.id)
    gagan = (repo / "Science_and_technology/02-space-technology/10-gaganyaan.md").read_text()
    assert gagan.count("## 📰 Update") == 1


def test_in_batch_duplicates_are_merged(cfg, repo, tmp_path, patched):
    text = (
        "# Digest\n\n## Special Intensive Revision in Bihar\nThe Election Commission started the Special Intensive Revision of rolls in Bihar.\n"
        + "Voters must submit documents. " * 3
        + "\n\n## Special Intensive Revision: Supreme Court Hearing\nThe Supreme Court heard petitions on the Special Intensive Revision of electoral rolls.\n"
        + "Aadhaar was accepted as proof. " * 3
    )
    store, ctx, job = _run(cfg, tmp_path, text)
    props = store.load_proposals(job.id)
    assert len(props) == 1 and len(props[0].topic_ids) == 2, [(p.title, p.topic_ids) for p in props]


def test_resume_after_crash_keeps_finished_topics(cfg, repo, tmp_path, patched, monkeypatch):
    calls = {"n": 0}
    real = pipeline_mod.generate_proposal

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("power cut")
        return real(*a, **k)

    monkeypatch.setattr(pipeline_mod, "generate_proposal", flaky)
    src = tmp_path / "ca.md"
    src.write_text(SOURCE)
    store, ctx = JobStore(cfg), Context(cfg)
    job = store.create(src, src.name, "Tag", None, JobOptions())
    with pytest.raises(RuntimeError):
        run_job(cfg, store, ctx, job.id, threading.Event())
    analysed = patched.calls.copy()
    run_job(cfg, store, ctx, job.id, threading.Event())
    assert store.load(job.id).status == "review"
    # triage/locate were not repeated on resume: only note writing happened again
    new_calls = patched.calls[len(analysed):]
    assert all(not c.startswith("Classify this piece") for c in new_calls)


def test_git_commit_leaves_other_staged_files_alone(cfg, repo):
    (repo / "unrelated.txt").write_text("user's own staged work")
    subprocess.run(["git", "-C", str(repo), "add", "unrelated.txt"], check=True)
    (repo / "Polity/new-note.md").write_text("# New\n")
    res = git_commit(repo, ["Polity/new-note.md"], "Add note")
    files = subprocess.run(["git", "-C", str(repo), "show", "--name-only", "--format=", res["commit"]], capture_output=True, text=True).stdout.split()
    assert files == ["Polity/new-note.md"]
    staged = subprocess.run(["git", "-C", str(repo), "diff", "--cached", "--name-only"], capture_output=True, text=True).stdout.split()
    assert staged == ["unrelated.txt"]
