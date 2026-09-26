"""Hybrid search over existing notes: BM25 (exact terms like "Article 371") + local embeddings.

Embeddings are cached on disk per note (keyed by mtime and size), so only new or edited notes
are re-embedded. If the embedding model is unavailable, search silently degrades to BM25.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

import numpy as np

from .llm import Embedder, LLMError
from .taxonomy import Note, Taxonomy
from .utils import atomic_write_text, slugify, strip_markdown, tokenize

log = logging.getLogger(__name__)


def note_body(text: str) -> str:
    """Note text without the title/source header block."""
    lines = text.splitlines()
    out, in_header = [], True
    for ln in lines:
        if in_header and (ln.startswith("# ") or ln.startswith(">") or not ln.strip() or re.fullmatch(r"-{3,}", ln.strip())):
            continue
        in_header = False
        out.append(ln)
    return "\n".join(out)


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.4, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.n = len(docs)
        self.lengths = np.array([len(d) for d in docs], dtype=np.float32)
        self.avgdl = float(self.lengths.mean()) if self.n else 0.0
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for i, d in enumerate(docs):
            for tok, tf in Counter(d).items():
                self.postings[tok].append((i, tf))
        self.idf = {t: math.log(1 + (self.n - len(p) + 0.5) / (len(p) + 0.5)) for t, p in self.postings.items()}

    def scores(self, query: Iterable[str]) -> np.ndarray:
        out = np.zeros(self.n, dtype=np.float32)
        if not self.n:
            return out
        for tok in set(query):
            idf = self.idf.get(tok)
            if idf is None:
                continue
            for i, tf in self.postings[tok]:
                denom = tf + self.k1 * (1 - self.b + self.b * self.lengths[i] / (self.avgdl or 1))
                out[i] += idf * tf * (self.k1 + 1) / denom
        return out


@dataclass
class Hit:
    note: Note
    score: float
    cosine: float
    bm25: float
    excerpt: str


class NoteIndex:
    def __init__(self, taxonomy: Taxonomy, embedder: Optional[Embedder], cache_dir: Path) -> None:
        self.tax = taxonomy
        self.embedder = embedder
        self.cache_dir = Path(cache_dir)
        self.notes: list[Note] = []
        self.excerpts: list[str] = []
        self.bm25: Optional[BM25] = None
        self.vectors: Optional[np.ndarray] = None
        self.embedding_error: Optional[str] = None

    # ------------------------------------------------------------------ build
    def build(self, progress: Optional[Callable[[int, int, str], None]] = None) -> None:
        self.notes = self.tax.all_notes()
        self.excerpts = []
        docs_tokens, embed_texts = [], []
        for n in self.notes:
            try:
                raw = (self.tax.root / n.path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                raw = ""
            body = strip_markdown(note_body(raw))
            self.excerpts.append(body[:400])
            subject_t = self.tax.subjects[n.subject].title
            sub_t = self.tax.subjects[n.subject].subfolders[n.subfolder].title
            docs_tokens.append(tokenize(f"{n.title} " * 3 + f"{sub_t} {body[:6000]}"))
            embed_texts.append(f"{n.title}. {subject_t} — {sub_t}. {body[:1200]}")
        self.bm25 = BM25(docs_tokens)
        if self.embedder is not None:
            try:
                self.vectors = self._load_or_embed(embed_texts, progress)
            except LLMError as exc:
                self.embedding_error = str(exc)
                log.warning("Embeddings unavailable, using keyword search only: %s", exc)
                self.vectors = None

    def _cache_paths(self) -> tuple[Path, Path]:
        slug = slugify(self.embedder.model if self.embedder else "none", 60)
        return self.cache_dir / f"emb-{slug}.npy", self.cache_dir / f"emb-{slug}.json"

    def _load_or_embed(self, texts: list[str], progress) -> np.ndarray:
        vec_path, meta_path = self._cache_paths()
        cached: dict[str, int] = {}
        old = None
        if vec_path.exists() and meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                old = np.load(vec_path)
                cached = {f"{m[0]}|{m[1]}|{m[2]}": i for i, m in enumerate(meta)}
            except (OSError, ValueError):
                cached, old = {}, None
        keys = [f"{n.path}|{n.mtime:.0f}|{n.size}" for n in self.notes]
        todo = [i for i, k in enumerate(keys) if k not in cached]
        new_vecs: dict[int, list[float]] = {}
        batch = 32
        for start in range(0, len(todo), batch):
            idx = todo[start : start + batch]
            if progress:
                progress(start, len(todo), f"Indexing notes for search {start}/{len(todo)}")
            vecs = self.embedder.embed_documents([texts[i] for i in idx])  # type: ignore[union-attr]
            new_vecs.update(zip(idx, vecs))
        dim = len(next(iter(new_vecs.values()))) if new_vecs else (old.shape[1] if old is not None else 0)
        mat = np.zeros((len(keys), dim), dtype=np.float32)
        for i, k in enumerate(keys):
            if i in new_vecs:
                mat[i] = new_vecs[i]
            elif old is not None:
                mat[i] = old[cached[k]]
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        mat = mat / np.where(norms == 0, 1, norms)
        if todo or len(cached) != len(keys):
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            np.save(vec_path, mat)
            atomic_write_text(meta_path, json.dumps([[n.path, f"{n.mtime:.0f}", n.size] for n in self.notes]))
        return mat

    # ------------------------------------------------------------------ query
    def query_vector(self, text: str) -> Optional[np.ndarray]:
        if self.vectors is None or self.embedder is None:
            return None
        try:
            v = np.asarray(self.embedder.embed_query(text), dtype=np.float32)
        except LLMError as exc:
            self.embedding_error = str(exc)
            return None
        n = np.linalg.norm(v)
        return v / n if n else v

    def search(
        self,
        text: str,
        *,
        subjects: Optional[set[str]] = None,
        exclude: Iterable[str] = (),
        k: int = 8,
        qvec: Optional[np.ndarray] = None,
    ) -> list[Hit]:
        if not self.notes or self.bm25 is None:
            return []
        excluded = set(exclude)
        bm = self.bm25.scores(tokenize(text))
        cos = None
        if self.vectors is not None:
            q = qvec if qvec is not None else self.query_vector(text)
            if q is not None and q.shape[0] == self.vectors.shape[1]:
                cos = self.vectors @ q
        mask = np.array(
            [(subjects is None or n.subject in subjects) and n.path not in excluded for n in self.notes], dtype=bool
        )
        idx = np.nonzero(mask)[0]
        if not len(idx):
            return []
        # Reciprocal-rank fusion is robust to the very different score scales.
        fused = np.zeros(len(self.notes), dtype=np.float32)
        bm_order = idx[np.argsort(-bm[idx], kind="stable")]
        for r, i in enumerate(bm_order):
            fused[i] += (1.0 / (60 + r)) if bm[i] > 0 else 0.0
        if cos is not None:
            cos_order = idx[np.argsort(-cos[idx], kind="stable")]
            for r, i in enumerate(cos_order):
                fused[i] += 1.0 / (60 + r)
        top = idx[np.argsort(-fused[idx], kind="stable")][:k]
        return [
            Hit(self.notes[i], float(fused[i]), float(cos[i]) if cos is not None else 0.0, float(bm[i]), self.excerpts[i])
            for i in top
        ]

    def subfolder_scores(self, hits: list[Hit]) -> dict[tuple[str, str], float]:
        scores: dict[tuple[str, str], float] = defaultdict(float)
        for rank, h in enumerate(hits):
            key = (h.note.subject, h.note.subfolder)
            scores[key] = max(scores[key], 1.0 / (1 + rank))
        return dict(scores)
