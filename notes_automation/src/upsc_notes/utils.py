"""Small helpers shared across the pipeline."""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

_SMALL_WORDS = {"and", "of", "the", "in", "on", "for", "to", "a", "an", "at", "by", "or", "vs"}


def today() -> str:
    return _dt.date.today().isoformat()


def now_iso() -> str:
    return _dt.datetime.now().replace(microsecond=0).isoformat()


def slugify(text: str, max_len: int = 80) -> str:
    """ASCII, lowercase, hyphen-separated slug cut at a word boundary."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    if len(text) > max_len:
        cut = text[:max_len]
        text = cut.rsplit("-", 1)[0] if "-" in cut else cut
    return text or "note"


def humanize(name: str) -> str:
    """'07-fundamental-rights' -> 'Fundamental Rights', 'Art_and_culture' -> 'Art and Culture'."""
    name = re.sub(r"^\d+[-_]", "", name)
    words = re.split(r"[-_\s]+", name)
    out = []
    for i, w in enumerate(w for w in words if w):
        lw = w.lower()
        out.append(lw if (i and lw in _SMALL_WORDS) else (w if w.isupper() and len(w) > 1 else lw.capitalize()))
    return " ".join(out)


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


_TOKEN_RE = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    """a an the and or of in on at to for from by with as is are was were be been being it its this that these
    those which who whom whose what when where why how not no nor but if then than so such into over under about
    after before between during through within without also may can will shall should would could has have had do
    does did their there they them he she his her we our you your i all any each other more most some only own same
    very new india indian""".split()
)


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in STOPWORDS and (len(t) > 1 or t.isdigit())]


def strip_markdown(text: str) -> str:
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[#>*_`|]+", " ", text)
    text = re.sub(r"^\s*[-:]{3,}\s*$", " ", text, flags=re.M)
    return re.sub(r"\s+", " ", text).strip()


def sha256_file(path: str | os.PathLike) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def atomic_write_text(path: str | os.PathLike, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def atomic_write_json(path: str | os.PathLike, obj: Any) -> None:
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2))


def read_json(path: str | os.PathLike, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def parse_page_spec(spec: str | None, page_count: int) -> list[int] | None:
    """'1-5, 8, 10-' -> [1,2,3,4,5,8,10..page_count] (1-based). None/'' means all pages."""
    if not spec or not spec.strip():
        return None
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d*)\s*-\s*(\d*)", part)
        if m:
            start = int(m.group(1) or 1)
            end = int(m.group(2) or page_count)
        elif part.isdigit():
            start = end = int(part)
        else:
            raise ValueError(f"Invalid page range: {part!r}")
        if start > end:
            raise ValueError(f"Invalid page range: {part!r}")
        pages.update(p for p in range(start, end + 1) if 1 <= p <= page_count)
    if not pages:
        raise ValueError(f"Page range {spec!r} selects no pages (document has {page_count})")
    return sorted(pages)


def format_pages(pages: Iterable[int | None]) -> str:
    nums = sorted({p for p in pages if p})
    if not nums:
        return ""
    return str(nums[0]) if nums[0] == nums[-1] else f"{nums[0]}–{nums[-1]}"


def rel_link(from_file: str, to_file: str) -> str:
    """URL-safe relative link between two repo-relative POSIX paths."""
    rel = os.path.relpath(to_file, start=os.path.dirname(from_file) or ".")
    return quote(rel.replace(os.sep, "/"), safe="/-_.~")


def word_count(text: str) -> int:
    return len(re.findall(r"\w+", text))


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    nl = cut.rfind("\n")
    return (cut[:nl] if nl > limit * 0.7 else cut).rstrip() + "\n…"


def ensure_inside(root: Path, target: Path) -> Path:
    """Resolve target and make sure it stays within root (guards against path traversal)."""
    resolved = target.resolve()
    root = root.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"Refusing to touch a path outside the repository: {target}")
    return resolved
