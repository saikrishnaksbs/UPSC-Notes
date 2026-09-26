"""Apply approved proposals to the notes repository, following its existing conventions.

Conventions preserved (see e.g. Polity/07-fundamental-rights/):
  * notes are  Subject/NN-module/NN-slug.md  starting with  "# Title", a "> **Source**:" line and "---";
  * every module has a README.md table  "| # | Sub-Topic Note | Source URL |";
  * every module has a <module>-complete.md compilation with "## N. Title" sections;
  * every subject has a MASTER-README.md listing modules and their notes.

Safety rules:
  * existing notes are only ever appended to — never rewritten;
  * every change is planned in memory first (for previews/diffs), then written in one go;
  * originals are backed up before they are touched; nothing is ever deleted;
  * hidden markers make re-applying the same upload a no-op instead of a duplicate.
"""

from __future__ import annotations

import difflib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import AppConfig
from .models import Job, Proposal, Segment, Topic
from .taxonomy import Taxonomy
from .utils import atomic_write_text, ensure_inside, humanize, now_iso, rel_link, slugify, today

MARKER = "<!-- upsc-notes: source={source} topics={topics} -->"
_MARKER_RE = re.compile(r"<!-- upsc-notes: source=(\S+) topics=([\d,]+) -->")


@dataclass
class FileChange:
    path: str
    before: Optional[str]
    after: str
    kind: str

    def diff(self) -> str:
        a = (self.before or "").splitlines(keepends=True)
        b = self.after.splitlines(keepends=True)
        return "".join(difflib.unified_diff(a, b, fromfile=f"a/{self.path}", tofile=f"b/{self.path}", n=2))


def topic_source_path(job: Job, cfg: AppConfig, topic: Topic) -> str:
    return f"{cfg.repo.sources_dir}/{job.source_id}/{topic.id:03d}-{slugify(topic.title, 60)}.md"


class RepoWriter:
    def __init__(self, cfg: AppConfig, tax: Taxonomy) -> None:
        self.cfg = cfg
        self.tax = tax
        self.root = cfg.repo_root
        self.overlay: dict[str, str] = {}
        self.kinds: dict[str, str] = {}
        self.originals: dict[str, Optional[str]] = {}
        self.warnings: list[str] = []
        self.note_paths: dict[str, str] = {}  # proposal id -> note path

    # ------------------------------------------------------------------ virtual file system
    def read(self, rel: str) -> Optional[str]:
        if rel in self.overlay:
            return self.overlay[rel]
        path = ensure_inside(self.root, self.root / rel)
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8", errors="replace")

    def write(self, rel: str, text: str, kind: str) -> None:
        ensure_inside(self.root, self.root / rel)
        if rel not in self.originals:
            self.originals[rel] = self.read(rel)
        self.overlay[rel] = text
        self.kinds.setdefault(rel, kind)

    def exists(self, rel: str) -> bool:
        return rel in self.overlay or (self.root / rel).is_file()

    def changes(self) -> list[FileChange]:
        return [
            FileChange(path=p, before=self.originals.get(p), after=t, kind=self.kinds.get(p, "other"))
            for p, t in self.overlay.items()
            if self.originals.get(p) != t
        ]

    # ------------------------------------------------------------------ planning
    def plan(self, proposal: Proposal, job: Job, topics: dict[int, Topic], segments: dict[int, Segment]) -> str:
        if not proposal.content:
            raise ValueError(f"Proposal {proposal.id} has no content to write")
        ptopics = [topics[i] for i in proposal.topic_ids if i in topics]
        if proposal.action == "update":
            path = self._plan_update(proposal, job, ptopics)
        else:
            path = self._plan_create(proposal, job, ptopics)
        self.note_paths[proposal.id] = path
        for t in ptopics:
            if t.id in segments:
                self._plan_topic_source(job, t, segments[t.id], path, proposal)
        return path

    def _source_links(self, job: Job, note_path: str, ptopics: list[Topic]) -> str:
        links = [f"[{job.tag} › {t.title}]({rel_link(note_path, topic_source_path(job, self.cfg, t))})" for t in ptopics]
        return " · ".join(links) if links else f"`{job.tag}`"

    def _plan_create(self, p: Proposal, job: Job, ptopics: list[Topic]) -> str:
        sf = self.tax.subfolder(p.subject, p.subfolder)
        if sf is None:
            raise ValueError(f"Folder {p.subject}/{p.subfolder} does not exist in the repository")
        folder = f"{p.subject}/{p.subfolder}"
        number, width = self._next_number(folder)
        slug = slugify(p.title, 70)
        filename = f"{number:0{width}d}-{slug}.md" if number is not None else f"{slug}.md"
        k = 2
        while self.exists(f"{folder}/{filename}"):
            filename = f"{number:0{width}d}-{slug}-{k}.md" if number is not None else f"{slug}-{k}.md"
            k += 1
        path = f"{folder}/{filename}"
        pages = ", ".join(t.pages for t in ptopics if t.pages)
        meta = f"> **Tag**: `{job.tag}`" + (f" · **Pages**: {pages}" if pages else "") + f" · **Added**: {today()}"
        body = [
            f"# {p.title}",
            "",
            f"> **Source**: {self._source_links(job, path, ptopics)}",
            ">",
            meta,
            "",
            "---",
            "",
            p.content.strip(),
            "",
        ]
        related = [r for r in p.related if r.path != path][:4]
        if related:
            body += ["## Related Notes", ""]
            body += [f"- [{r.title}]({rel_link(path, r.path)})" for r in related]
            body.append("")
        body += [MARKER.format(source=job.source_id, topics=",".join(str(t.id) for t in ptopics)), ""]
        self.write(path, "\n".join(body), "note-create")

        n_row = self._update_readme(folder, sf.title, p.title, filename, job, path, ptopics)
        if self.cfg.repo.update_master_readme:
            self._update_master_readme(p.subject, p.subfolder, p.title, filename)
        if self.cfg.repo.update_module_compilation:
            self._append_compilation(folder, n_row, p.title, path, job, ptopics, p.content)
        return path

    def _plan_update(self, p: Proposal, job: Job, ptopics: list[Topic]) -> str:
        path = p.target_path or ""
        existing = self.read(path) if path else None
        if existing is None:
            raise ValueError(f"Target note {path!r} no longer exists")
        ids = {t.id for t in ptopics}
        for m in _MARKER_RE.finditer(existing):
            if m.group(1) == job.source_id and ids & {int(x) for x in m.group(2).split(",") if x}:
                raise ValueError(f"{path} already contains this update (applied earlier)")
        titles = "; ".join(t.title for t in ptopics) or p.title
        block = "\n".join(
            [
                "",
                "---",
                "",
                f"## 📰 Update — {job.tag}: {titles}",
                "",
                f"> **Source**: {self._source_links(job, path, ptopics)} · **Added**: {today()}",
                "",
                p.content.strip(),
                "",
                MARKER.format(source=job.source_id, topics=",".join(str(i) for i in sorted(ids))),
                "",
            ]
        )
        self.write(path, existing.rstrip("\n") + "\n" + block, "note-update")
        if self.cfg.repo.update_module_compilation:
            self._mirror_update_in_compilation(path, block)
        return path

    # ------------------------------------------------------------------ module README table
    def _readme_rows(self, text: str) -> list[tuple[int, int, str]]:
        """(line index, row number, linked filename) for every table row."""
        rows = []
        for i, ln in enumerate(text.splitlines()):
            m = re.match(r"^\|\s*(\d+)\s*\|\s*\[[^\]]*\]\(([^)]*)\)", ln)
            if m:
                rows.append((i, int(m.group(1)), m.group(2).rsplit("/", 1)[-1]))
        return rows

    def _update_readme(
        self, folder: str, folder_title: str, title: str, filename: str, job: Job, note_path: str, ptopics: list[Topic]
    ) -> int:
        readme = f"{folder}/README.md"
        text = self.read(readme)
        if text is None:
            text = f"# {folder_title} - Notes Index\n\n| # | Sub-Topic Note | Source URL |\n| :--- | :--- | :--- |\n"
        rows = self._readme_rows(text)
        n = (max(r[1] for r in rows) + 1) if rows else 1
        src = ptopics[0] if ptopics else None
        src_link = (
            f"[{job.tag}]({rel_link(readme, topic_source_path(job, self.cfg, src))})" if src else f"`{job.tag}`"
        )
        row = f"| {n} | [{title}]({filename}) | {src_link} |"
        if not self.cfg.repo.update_readme_index:
            return n
        lines = text.splitlines()
        if rows:
            insert_at = rows[-1][0] + 1
        else:
            seps = [i for i, ln in enumerate(lines) if re.match(r"^\|\s*:?-{3,}", ln)]
            insert_at = seps[0] + 1 if seps else len(lines)
        lines.insert(insert_at, row)
        self.write(readme, "\n".join(lines).rstrip("\n") + "\n", "readme")
        return n

    # ------------------------------------------------------------------ subject MASTER-README
    def _update_master_readme(self, subject: str, subfolder: str, title: str, filename: str) -> None:
        path = f"{subject}/MASTER-README.md"
        text = self.read(path)
        if text is None:
            return
        lines = text.splitlines()
        head = next(
            (i for i, ln in enumerate(lines) if ln.startswith("### ") and (f"/{subfolder}/" in ln or f"({subfolder}/" in ln or f"[{subfolder}]" in ln)),
            None,
        )
        if head is None:
            self.warnings.append(f"{path}: module '{subfolder}' not found; index not updated")
            return
        # Style A: bullet list of notes under the module heading.
        j = head + 1
        last_bullet = None
        while j < len(lines) and not lines[j].startswith("### "):
            if re.match(r"^\s*- \[", lines[j]):
                last_bullet = j
            elif lines[j].strip() and last_bullet is not None:
                break
            j += 1
        count_line = next(
            (k for k in range(head + 1, min(len(lines), head + 8)) if re.search(r"\*\*Sub-Topics\*\*:\s*\d+", lines[k])), None
        )
        if count_line is not None:
            # Style B (Ancient history): "**Sub-Topics**: N individual note files".
            n_files = sum(1 for n in self.tax.subjects[subject].subfolders[subfolder].notes) + sum(
                1 for p in self.overlay if p.startswith(f"{subject}/{subfolder}/") and self.kinds.get(p) == "note-create"
            )
            lines[count_line] = re.sub(r"(\*\*Sub-Topics\*\*:\s*)\d+", lambda m: f"{m.group(1)}{n_files}", lines[count_line])
        elif last_bullet is not None:
            indent = re.match(r"^(\s*)", lines[last_bullet]).group(1)
            lines.insert(last_bullet + 1, f"{indent}- [{title}]({subfolder}/{filename})")
        else:
            lines.insert(head + 1, f"  - [{title}]({subfolder}/{filename})")
        self.write(path, "\n".join(lines).rstrip("\n") + "\n", "master-readme")

    # ------------------------------------------------------------------ module compilation (-complete.md)
    def _compilation_path(self, folder: str) -> Optional[str]:
        subject, sub = folder.split("/", 1)
        sf = self.tax.subfolder(subject, sub)
        return sf.complete_file if sf else None

    def _append_compilation(
        self, folder: str, n: int, title: str, note_path: str, job: Job, ptopics: list[Topic], content: str
    ) -> None:
        comp = self._compilation_path(folder)
        if not comp:
            return
        text = self.read(comp) or ""
        src = f"> Source: {self._source_links(job, comp, ptopics)}"
        section = f"\n## {n}. {title}\n{src}\n\n{content.strip()}\n"
        self.write(comp, text.rstrip("\n") + "\n" + section, "compilation")

    def _mirror_update_in_compilation(self, note_path: str, block: str) -> None:
        folder = note_path.rsplit("/", 1)[0]
        comp = self._compilation_path(folder)
        readme = self.read(f"{folder}/README.md")
        if not comp or readme is None:
            return
        text = self.read(comp)
        if text is None:
            return
        rows = self._readme_rows(readme)
        fname = note_path.rsplit("/", 1)[-1]
        row = next((r for r in rows if r[2] == fname), None)
        if row is None:
            self.warnings.append(f"{comp}: '{fname}' is not in the module README; compilation not updated")
            return
        lines = text.splitlines()
        start = next((i for i, ln in enumerate(lines) if ln.startswith(f"## {row[1]}. ")), None)
        if start is None:
            self.warnings.append(f"{comp}: section '## {row[1]}.' not found; compilation not updated")
            return
        end = next((i for i in range(start + 1, len(lines)) if re.match(r"^## \d+\. ", lines[i])), len(lines))
        lines[end:end] = block.splitlines()
        self.write(comp, "\n".join(lines).rstrip("\n") + "\n", "compilation")

    # ------------------------------------------------------------------ numbering
    def _next_number(self, folder: str) -> tuple[Optional[int], int]:
        names = []
        d = self.root / folder
        if d.is_dir():
            names += [p.name for p in d.iterdir() if p.suffix == ".md"]
        names += [p.rsplit("/", 1)[-1] for p in self.overlay if p.rsplit("/", 1)[0] == folder]
        notes = [n for n in names if n.lower() not in ("readme.md",) and not n.lower().endswith("-complete.md")]
        nums = [re.match(r"^(\d+)-", n) for n in notes]
        numbered = [int(m.group(1)) for m in nums if m]
        if notes and len(numbered) < len(notes) / 2:
            return None, 0
        width = max([2] + [len(m.group(1)) for m in nums if m])
        return (max(numbered) + 1 if numbered else 1), width

    # ------------------------------------------------------------------ source traceability
    def _plan_topic_source(self, job: Job, t: Topic, seg: Segment, note_path: str, p: Proposal) -> None:
        path = topic_source_path(job, self.cfg, t)
        note_title = p.title
        verb = "updated" if p.action == "update" else "created"
        lines = [
            f"# {t.title}",
            "",
            f"> **Tag**: `{job.tag}` · **File**: `{job.filename}`" + (f" · **Pages**: {t.pages}" if t.pages else ""),
        ]
        if t.section:
            lines += [">", f"> **Section in source**: {t.section.split(' › ')[-1]}"]
        if job.source_url:
            lines += [">", f"> **Source URL**: {job.source_url}"]
        lines += [
            ">",
            f"> **Used in**: [{note_title}]({rel_link(path, note_path)}) ({verb} {today()})",
            "",
            "---",
            "",
            seg.text.strip(),
            "",
        ]
        self.write(path, "\n".join(lines), "source")

    def plan_source_index(self, job: Job, entries: list[dict]) -> None:
        """entries: [{topic, pages, action, note_path, note_title}] for every applied topic of this job."""
        folder = f"{self.cfg.repo.sources_dir}/{job.source_id}"
        readme = f"{folder}/README.md"
        fm = [
            "---",
            f"tag: {_yaml(job.tag)}",
            f"file: {_yaml(job.filename)}",
            f"sha256: {job.file_sha256}",
            f"source_url: {_yaml(job.source_url or '')}",
            f"ingested: {job.created_at[:10]}",
            f"model: {_yaml(job.model or '')}",
            f"topics: {len(entries)}",
            "---",
            "",
        ]
        body = [
            f"# {job.tag}",
            "",
            f"> **Original file**: `{job.filename}` · **Ingested**: {job.created_at[:10]} · **Model**: `{job.model or '—'}`",
        ]
        if job.source_url:
            body += [">", f"> **Source URL**: {job.source_url}"]
        body += [
            "",
            "Extracted source text for every topic that was turned into a note, for traceability.",
            "",
            "| # | Topic | Pages | Action | Note |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ]
        for n, e in enumerate(sorted(entries, key=lambda e: e["topic"].id), 1):
            t: Topic = e["topic"]
            src = topic_source_path(job, self.cfg, t).rsplit("/", 1)[-1]
            note_link = rel_link(readme, e["note_path"])
            loc = " › ".join(humanize(x) for x in e["note_path"].split("/")[:2])
            body.append(
                f"| {n} | [{t.title}]({src}) | {t.pages or '—'} | {e['action'].title()} | [{loc} › {e['note_title']}]({note_link}) |"
            )
        self.write(readme, "\n".join(fm + body) + "\n", "source-index")
        self._plan_library_index(job, len(entries))

    def _plan_library_index(self, job: Job, n_topics: int) -> None:
        sources_dir = self.cfg.repo.sources_dir
        path = f"{sources_dir}/README.md"
        rows: dict[str, str] = {}
        base = self.root / sources_dir
        if base.is_dir():
            for d in sorted(base.iterdir()):
                meta = _front_matter(self.read(f"{sources_dir}/{d.name}/README.md") or "")
                if meta:
                    rows[d.name] = _library_row(d.name, meta)
        meta = _front_matter(self.overlay.get(f"{sources_dir}/{job.source_id}/README.md", ""))
        rows[job.source_id] = _library_row(job.source_id, meta or {"tag": job.tag, "file": job.filename, "topics": str(n_topics), "ingested": job.created_at[:10]})
        text = "\n".join(
            [
                "# Source Library",
                "",
                "Extracted text of every uploaded document that notes were generated from. Each folder holds one",
                "Markdown file per topic, linked from the notes that used it.",
                "",
                "| Ingested | Tag | File | Topics | Folder |",
                "| :--- | :--- | :--- | :--- | :--- |",
                *[rows[k] for k in sorted(rows, reverse=True)],
                "",
            ]
        )
        self.write(path, text, "source-index")

    # ------------------------------------------------------------------ commit to disk
    def commit(self, backup_root: Path) -> list[str]:
        changes = self.changes()
        stamp = now_iso().replace(":", "")
        written = []
        for ch in changes:
            target = ensure_inside(self.root, self.root / ch.path)
            if ch.before is not None and target.exists():
                backup = backup_root / stamp / ch.path
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)
            atomic_write_text(target, ch.after)
            written.append(ch.path)
        return written


def _yaml(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _front_matter(text: str) -> dict:
    m = re.match(r"\A---\n(.*?)\n---\n", text, flags=re.S)
    if not m:
        return {}
    out = {}
    for ln in m.group(1).splitlines():
        if ":" in ln:
            k, v = ln.split(":", 1)
            v = v.strip()
            if v.startswith('"') and v.endswith('"'):
                v = v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
            out[k.strip()] = v
    return out


def _library_row(folder: str, meta: dict) -> str:
    return (
        f"| {meta.get('ingested', '')} | {meta.get('tag', '')} | `{meta.get('file', '')}` | {meta.get('topics', '')} "
        f"| [{folder}]({folder}/README.md) |"
    )
