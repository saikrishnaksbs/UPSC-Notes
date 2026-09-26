"""Read the notes repository's hierarchy: Subject / NN-module / NN-note.md."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .utils import humanize

# Short descriptions that help the model choose subjects. Unknown subjects fall back to their module names.
SUBJECT_HINTS = {
    "Polity": "Constitution, Parliament & state legislatures, President/Governor, judiciary & court judgments, elections & ECI, federalism, local bodies, constitutional/statutory/regulatory bodies, bills & acts on governance, rights, citizenship, public administration (GS-II)",
    "International_relations": "India's bilateral & multilateral relations, foreign policy, summits & state visits, international organisations & groupings (UN, G20, BRICS, SCO, QUAD), treaties, diaspora, global conflicts (GS-II)",
    "Indian_economy": "Macroeconomy, GDP & inflation, banking & RBI, monetary & fiscal policy, budget, taxes & GST, trade, markets, agriculture economics, industry & MSMEs, infrastructure, energy sector, investment, economic schemes & indices (GS-III)",
    "Environment_and_biodiversity": "Ecology, species & protected areas, biodiversity conservation, pollution & waste, climate change & COP negotiations, environmental laws & conventions, wetlands, forests (GS-III)",
    "Science_and_technology": "Space missions, biotechnology, health research, diseases & vaccines, AI & computing, quantum, nuclear technology, defence technology, new materials, IT & telecom, discoveries (GS-III)",
    "Internal_security": "Terrorism, left-wing extremism, insurgency, border management, cyber security, armed forces, defence exercises & procurement, money laundering, organised crime (GS-III)",
    "Disaster_management": "Floods, droughts, cyclones, earthquakes, landslides, heat waves, GLOFs, industrial & urban disasters, NDMA and disaster-risk frameworks (GS-III)",
    "Social_justice": "Welfare of vulnerable sections (SC/ST, minorities, disabled, elderly, children), poverty, health systems & schemes, education policy & reports, hunger & nutrition, social-sector schemes (GS-II)",
    "Indian_society": "Features of Indian society, women's issues, population, urbanisation, migration, globalisation, communalism, regionalism, secularism, youth & social change (GS-I)",
    "Geography": "Physical geography (landforms, climate, oceans), Indian physiography, rivers & drainage, monsoon, soils, vegetation, resources & minerals, industrial location, places in news (GS-I)",
    "Art_and_culture": "Architecture, sculpture, painting, music, dance, theatre, crafts & GI-tagged crafts, festivals, languages & literature, UNESCO heritage, cultural personalities & awards (GS-I)",
    "Ancient_history": "Prehistory, Indus Valley, Vedic age, Buddhism & Jainism, Mauryas, Guptas, Sangam age, early dynasties, inscriptions & archaeological finds (GS-I)",
    "Medieval_history": "Delhi Sultanate, Vijayanagara & Bahmani, Mughals, Bhakti & Sufi movements, medieval society & culture (GS-I)",
    "Modern_history": "British rule, socio-religious reforms, freedom struggle, national leaders, Gandhian era, independence & partition, anniversaries of modern-history events (GS-I)",
    "World_history": "World events since the Renaissance: revolutions, world wars, Cold War, decolonisation, political ideologies (GS-I)",
    "Ethics": "Ethics, integrity & aptitude: values, attitude, emotional intelligence, moral thinkers, probity & ethics in governance, public-service values, case studies (GS-IV)",
}

_NUM_RE = re.compile(r"^(\d+)-")
_SKIP_FILES = {"readme.md", "master-readme.md"}


@dataclass
class Note:
    path: str  # repo-relative, POSIX
    subject: str
    subfolder: str
    filename: str
    title: str
    number: Optional[int]
    size: int
    mtime: float


@dataclass
class Subfolder:
    subject: str
    name: str
    title: str
    notes: list[Note] = field(default_factory=list)
    has_readme: bool = False
    complete_file: Optional[str] = None

    @property
    def path(self) -> str:
        return f"{self.subject}/{self.name}"


@dataclass
class Subject:
    name: str
    title: str
    subfolders: dict[str, Subfolder] = field(default_factory=dict)
    master_readme: Optional[str] = None

    @property
    def hint(self) -> str:
        if self.name in SUBJECT_HINTS:
            return SUBJECT_HINTS[self.name]
        return "Modules: " + "; ".join(sf.title for sf in list(self.subfolders.values())[:12])


@dataclass
class Taxonomy:
    root: Path
    subjects: dict[str, Subject] = field(default_factory=dict)

    def all_notes(self) -> list[Note]:
        return [n for s in self.subjects.values() for sf in s.subfolders.values() for n in sf.notes]

    def subfolder(self, subject: str, name: str) -> Optional[Subfolder]:
        s = self.subjects.get(subject)
        return s.subfolders.get(name) if s else None

    def note(self, path: str) -> Optional[Note]:
        parts = path.split("/")
        if len(parts) < 3:
            return None
        sf = self.subfolder(parts[0], parts[1])
        if not sf:
            return None
        return next((n for n in sf.notes if n.path == path), None)

    def to_dict(self, include_notes: bool = True) -> dict:
        return {
            "subjects": [
                {
                    "name": s.name,
                    "title": s.title,
                    "subfolders": [
                        {
                            "name": sf.name,
                            "title": sf.title,
                            "count": len(sf.notes),
                            **({"notes": [{"path": n.path, "title": n.title} for n in sf.notes]} if include_notes else {}),
                        }
                        for sf in s.subfolders.values()
                    ],
                }
                for s in self.subjects.values()
            ]
        }


def read_note_title(path: Path) -> Optional[str]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(4096)
    except OSError:
        return None
    for line in head.splitlines():
        if line.startswith("# "):
            return re.sub(r"[*_`]+", "", line[2:]).strip() or None
    return None


def _is_note_file(p: Path) -> bool:
    name = p.name.lower()
    return p.suffix.lower() == ".md" and name not in _SKIP_FILES and not name.endswith("-complete.md") and not name.startswith(".")


def scan_repo(root: Path, exclude_dirs: list[str] | None = None, sources_dir: str = "_sources") -> Taxonomy:
    root = Path(root).resolve()
    excluded = {d.lower() for d in (exclude_dirs or [])} | {sources_dir.lower()}
    tax = Taxonomy(root=root)
    if not root.is_dir():
        return tax
    for sdir in sorted(p for p in root.iterdir() if p.is_dir()):
        if sdir.name.startswith((".", "_")) or sdir.name.lower() in excluded:
            continue
        subject = Subject(name=sdir.name, title=humanize(sdir.name))
        if (sdir / "MASTER-README.md").is_file():
            subject.master_readme = f"{sdir.name}/MASTER-README.md"
        for fdir in sorted(p for p in sdir.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))):
            sf = Subfolder(subject=sdir.name, name=fdir.name, title=humanize(fdir.name))
            sf.has_readme = (fdir / "README.md").is_file()
            completes = sorted(fdir.glob("*-complete.md"))
            if completes:
                sf.complete_file = f"{sdir.name}/{fdir.name}/{completes[0].name}"
            for f in sorted(fdir.rglob("*.md")):
                if not _is_note_file(f):
                    continue
                st = f.stat()
                rel = f.relative_to(root).as_posix()
                m = _NUM_RE.match(f.name)
                sf.notes.append(
                    Note(
                        path=rel,
                        subject=sdir.name,
                        subfolder=fdir.name,
                        filename=f.name,
                        title=read_note_title(f) or humanize(f.stem),
                        number=int(m.group(1)) if m else None,
                        size=st.st_size,
                        mtime=st.st_mtime,
                    )
                )
            if sf.notes or sf.has_readme:
                subject.subfolders[fdir.name] = sf
        if subject.subfolders:
            tax.subjects[sdir.name] = subject
    return tax
