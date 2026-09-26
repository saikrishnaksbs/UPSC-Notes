from __future__ import annotations

import posixpath
import re
from pathlib import Path
from urllib.parse import unquote

import pytest

from upsc_notes.models import Candidate, Job, Proposal, Segment, Topic
from upsc_notes.taxonomy import scan_repo
from upsc_notes.writer import RepoWriter


def _job(**kw) -> Job:
    base = dict(
        id="20260923-abcdef12", created_at="2026-09-23T10:00:00", updated_at="2026-09-23T10:00:00",
        tag="Vision IAS September 2026", filename="vision.pdf", input_path="/tmp/x", file_sha256="ab" * 32,
        source_id="2026-09-23-vision-ias-september-2026", model="qwen2.5:7b",
    )
    base.update(kw)
    return Job(**base)


def _topic(tid: int, title: str) -> Topic:
    return Topic(id=tid, title=title, original_title=title, pages="12–13", section="POLITY", chars=100)


def _links_resolve(repo: Path, rel_file: str, text: str) -> list[str]:
    """Return relative links in `text` that do not point to an existing file (ignoring http/file links)."""
    broken = []
    base = posixpath.dirname(rel_file)
    for target in re.findall(r"\]\(([^)]+)\)", text):
        if target.startswith(("http", "file:", "#")):
            continue
        resolved = posixpath.normpath(posixpath.join(base, unquote(target)))
        if not (repo / resolved).exists():
            broken.append(target)
    return broken


def test_create_follows_repo_conventions(cfg, repo):
    tax = scan_repo(repo, cfg.repo.exclude_dirs)
    job = _job()
    t = _topic(3, "Special Intensive Revision (SIR)")
    seg = Segment(id=3, title=t.title, text="### Why in News\n- SIR of electoral rolls.\n")
    p = Proposal(
        id="p001", action="create", subject="Polity", subfolder="25-constitutional-bodies", title="Special Intensive Revision (SIR)",
        topic_ids=[3], content="## Why in News\n- The ECI ordered an SIR.\n", status="approved",
        related=[Candidate(path="Polity/25-constitutional-bodies/01-election-commission-of-india.md", title="Election Commission of India", subject="Polity", subfolder="25-constitutional-bodies")],
    )
    w = RepoWriter(cfg, tax)
    path = w.plan(p, job, {3: t}, {3: seg})
    w.plan_source_index(job, [{"topic": t, "action": "created", "note_path": path, "note_title": p.title}])
    written = w.commit(cfg.data_path / "backups")

    assert path == "Polity/25-constitutional-bodies/02-special-intensive-revision-sir.md"
    note = (repo / path).read_text()
    assert note.startswith("# Special Intensive Revision (SIR)\n\n> **Source**: [Vision IAS September 2026 › Special Intensive Revision (SIR)](")
    assert "> **Tag**: `Vision IAS September 2026` · **Pages**: 12–13" in note
    assert "\n---\n\n## Why in News" in note
    assert "## Related Notes\n\n- [Election Commission of India](01-election-commission-of-india.md)" in note
    assert "<!-- upsc-notes: source=2026-09-23-vision-ias-september-2026 topics=3 -->" in note

    readme = (repo / "Polity/25-constitutional-bodies/README.md").read_text().splitlines()
    assert readme[-1].startswith("| 2 | [Special Intensive Revision (SIR)](02-special-intensive-revision-sir.md) | [Vision IAS September 2026](")
    master = (repo / "Polity/MASTER-README.md").read_text()
    assert "  - [Election Commission of India](file:///Users/x/Upsc/Polity/25-constitutional-bodies/01-election-commission-of-india.md)\n  - [Special Intensive Revision (SIR)](25-constitutional-bodies/02-special-intensive-revision-sir.md)" in master
    comp = (repo / "Polity/25-constitutional-bodies/constitutional-bodies-complete.md").read_text()
    assert "\n## 2. Special Intensive Revision (SIR)\n> Source: [" in comp and comp.rstrip().endswith("- The ECI ordered an SIR.")

    src = "_sources/2026-09-23-vision-ias-september-2026/003-special-intensive-revision-sir.md"
    assert src in written and "SIR of electoral rolls" in (repo / src).read_text()
    idx = (repo / "_sources/2026-09-23-vision-ias-september-2026/README.md").read_text()
    assert idx.startswith("---\ntag: \"Vision IAS September 2026\"") and "| 1 | [Special Intensive Revision (SIR)](003-special-intensive-revision-sir.md) | 12–13 | Created |" in idx
    assert "2026-09-23-vision-ias-september-2026" in (repo / "_sources/README.md").read_text()

    for f in written:  # every relative link we wrote must resolve
        assert _links_resolve(repo, f, (repo / f).read_text()) == [], f
    # the scanner must not treat _sources as a subject
    assert "_sources" not in scan_repo(repo, cfg.repo.exclude_dirs).subjects


def test_update_appends_only_and_is_idempotent(cfg, repo):
    tax = scan_repo(repo, cfg.repo.exclude_dirs)
    job = _job()
    t = _topic(5, "Gaganyaan G1 test flight")
    before = (repo / "Science_and_technology/02-space-technology/10-gaganyaan.md").read_text()
    p = Proposal(
        id="p002", action="update", subject="Science_and_technology", subfolder="02-space-technology", title="Gaganyaan",
        target_path="Science_and_technology/02-space-technology/10-gaganyaan.md", topic_ids=[5],
        content="### Why in News\n- G1 uncrewed flight completed.\n", status="approved",
    )
    w = RepoWriter(cfg, tax)
    w.plan(p, job, {5: t}, {5: Segment(id=5, title=t.title, text="G1 flight text")})
    w.commit(cfg.data_path / "backups")
    after = (repo / "Science_and_technology/02-space-technology/10-gaganyaan.md").read_text()
    assert after.startswith(before.rstrip("\n")), "existing content must be preserved verbatim"
    assert "## 📰 Update — Vision IAS September 2026: Gaganyaan G1 test flight" in after
    assert "### Why in News\n- G1 uncrewed flight completed." in after
    comp = (repo / "Science_and_technology/02-space-technology/space-technology-complete.md").read_text()
    assert "G1 uncrewed flight completed" in comp
    backups = list((cfg.data_path / "backups").rglob("10-gaganyaan.md"))
    assert backups and backups[0].read_text() == before

    w2 = RepoWriter(cfg, scan_repo(repo, cfg.repo.exclude_dirs))
    with pytest.raises(ValueError, match="already contains this update"):
        w2.plan(p, job, {5: t}, {5: Segment(id=5, title=t.title, text="x")})


def test_update_mirrors_into_the_right_compilation_section(cfg, repo):
    tax = scan_repo(repo, cfg.repo.exclude_dirs)
    p = Proposal(
        id="p003", action="update", subject="Polity", subfolder="07-fundamental-rights", title="Fundamental Rights and its features",
        target_path="Polity/07-fundamental-rights/01-fundamental-rights-and-its-features.md", topic_ids=[1],
        content="### Key Updates\n- MARKER-FR-UPDATE\n", status="approved",
    )
    w = RepoWriter(cfg, tax)
    w.plan(p, _job(), {1: _topic(1, "FR news")}, {1: Segment(id=1, title="FR news", text="x")})
    w.commit(cfg.data_path / "backups")
    comp = (repo / "Polity/07-fundamental-rights/fundamental-rights-complete.md").read_text()
    assert comp.index("MARKER-FR-UPDATE") < comp.index("## 2. Article 21"), "update must land inside section 1"


def test_master_readme_count_style(cfg, repo):
    tax = scan_repo(repo, cfg.repo.exclude_dirs)
    p = Proposal(id="p004", action="create", subject="Ancient_history", subfolder="02-Vedic-Period", title="Samaveda", topic_ids=[1], content="## About\n- Chants.\n")
    w = RepoWriter(cfg, tax)
    path = w.plan(p, _job(), {1: _topic(1, "Samaveda")}, {1: Segment(id=1, title="Samaveda", text="x")})
    w.commit(cfg.data_path / "backups")
    assert path == "Ancient_history/02-Vedic-Period/02-samaveda.md"
    assert "- **Sub-Topics**: 2 individual note files" in (repo / "Ancient_history/MASTER-README.md").read_text()


def test_numbering_continues_across_planned_notes(cfg, repo):
    tax = scan_repo(repo, cfg.repo.exclude_dirs)
    w = RepoWriter(cfg, tax)
    paths = []
    for i, title in enumerate(["Right to Protest", "Right to Fair Trial"], 1):
        p = Proposal(id=f"p{i}", action="create", subject="Polity", subfolder="07-fundamental-rights", title=title, topic_ids=[i], content="## A\n- b\n")
        paths.append(w.plan(p, _job(), {i: _topic(i, title)}, {i: Segment(id=i, title=title, text="x")}))
    assert [Path(p).name for p in paths] == ["03-right-to-protest.md", "04-right-to-fair-trial.md"]
    readme = w.overlay["Polity/07-fundamental-rights/README.md"]
    assert "| 3 | [Right to Protest]" in readme and "| 4 | [Right to Fair Trial]" in readme


def test_rejects_paths_outside_repo(cfg, repo):
    tax = scan_repo(repo, cfg.repo.exclude_dirs)
    p = Proposal(id="p9", action="update", subject="Polity", subfolder="x", title="x", target_path="../../etc/passwd", topic_ids=[], content="x")
    with pytest.raises(ValueError):
        RepoWriter(cfg, tax).plan(p, _job(), {}, {})
