"""Application settings.

Settings are read from ``config.yaml`` next to the ``notes_automation`` folder (or the
file named by ``UPSC_NOTES_CONFIG``).  Every value has a default, so the file is optional.
Relative paths are resolved against the directory that holds the config file.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field

APP_DIR = Path(__file__).resolve().parents[2]  # .../notes_automation


class LLMConfig(BaseModel):
    """Chat model used for triage, classification and note writing."""

    provider: Literal["ollama", "openai_compatible"] = "ollama"
    base_url: str = "http://localhost:11434"
    model: str = "qwen2.5:7b"
    # Keep one context size for every call: Ollama reloads the model when num_ctx changes.
    num_ctx: int = 16384
    temperature: float = 0.2
    keep_alive: str = "30m"
    timeout: float = 900.0
    # Only sent when set; useful for "thinking" models such as qwen3 (set to false).
    think: Optional[bool] = None
    api_key: Optional[str] = None


class EmbeddingConfig(BaseModel):
    """Embedding model used to find similar existing notes."""

    provider: Literal["ollama", "openai_compatible", "none"] = "ollama"
    base_url: str = "http://localhost:11434"
    model: str = "nomic-embed-text"
    query_prefix: str = "search_query: "
    document_prefix: str = "search_document: "
    batch_size: int = 32
    timeout: float = 300.0
    api_key: Optional[str] = None


class OCRConfig(BaseModel):
    engine: Literal["tesseract", "ollama_vision", "none"] = "tesseract"
    # auto: OCR image-only pages only when the PDF is mostly scanned; always: OCR every image-only page.
    mode: Literal["auto", "always"] = "auto"
    tesseract_cmd: str = "tesseract"
    languages: str = "eng"
    dpi: int = 300
    # A PDF page with fewer extractable characters than this (and an image) is OCR'd.
    min_text_chars: int = 80
    vision_model: str = "qwen2.5vl:7b"
    vision_base_url: str = "http://localhost:11434"


class ExtractionConfig(BaseModel):
    # docling: best quality (layout + table AI models, ~5 s/page); pymupdf: fast (~0.05 s/page).
    engine: Literal["docling", "pymupdf"] = "docling"
    # Pages per Docling batch (progress granularity / memory).
    docling_chunk_pages: int = 10


class RepoConfig(BaseModel):
    path: str = ".."
    sources_dir: str = "_sources"
    exclude_dirs: list[str] = Field(
        default_factory=lambda: ["notes_automation", "app", "node_modules", "venv", ".venv"]
    )
    update_readme_index: bool = True
    update_master_readme: bool = True
    update_module_compilation: bool = True


class PipelineConfig(BaseModel):
    skip_irrelevant: bool = True
    # Proposals at or above this confidence (and without warnings) are pre-approved.
    auto_approve_threshold: Optional[float] = None
    max_segment_chars: int = 40000
    min_segment_chars: int = 150
    # Source text above this size is condensed chunk-by-chunk before writing the note.
    max_generation_source_chars: int = 26000
    max_existing_note_chars: int = 20000
    max_note_words: int = 900
    min_note_words: int = 120


class GitConfig(BaseModel):
    branch_prefix: str = "notes/"
    remote: str = "origin"


class AppConfig(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    embeddings: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    ocr: OCRConfig = Field(default_factory=OCRConfig)
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)
    repo: RepoConfig = Field(default_factory=RepoConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    git: GitConfig = Field(default_factory=GitConfig)
    data_dir: str = ".data"
    host: str = "127.0.0.1"
    port: int = 8765

    # Directory used to resolve relative paths (set by load_config).
    base_dir: str = str(APP_DIR)

    @property
    def repo_root(self) -> Path:
        return (Path(self.base_dir) / self.repo.path).resolve()

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir)
        if p.is_absolute():
            return p.resolve()
        if (self.repo_root / "notes_automation").is_dir():
            return (self.repo_root / "notes_automation" / self.data_dir).resolve()
        return (Path(self.base_dir) / self.data_dir).resolve()

    @property
    def sources_root(self) -> Path:
        return self.repo_root / self.repo.sources_dir


def load_config(path: str | os.PathLike | None = None) -> AppConfig:
    """Load settings from YAML, falling back to defaults when no file exists."""
    candidate = Path(path or os.environ.get("UPSC_NOTES_CONFIG") or APP_DIR / "config.yaml")
    data: dict = {}
    if candidate.is_file():
        with candidate.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        base_dir = candidate.resolve().parent
    else:
        base_dir = APP_DIR
    cfg = AppConfig.model_validate(data)
    cfg.base_dir = str(base_dir)
    # Environment overrides for the most common knobs.
    if os.environ.get("UPSC_NOTES_MODEL"):
        cfg.llm.model = os.environ["UPSC_NOTES_MODEL"]
    if os.environ.get("UPSC_NOTES_REPO"):
        cfg.repo.path = os.environ["UPSC_NOTES_REPO"]
        cfg.base_dir = str(Path.cwd()) if not Path(cfg.repo.path).is_absolute() else cfg.base_dir
    if os.environ.get("OLLAMA_BASE_URL"):
        url = os.environ["OLLAMA_BASE_URL"]
        cfg.llm.base_url = url
        cfg.embeddings.base_url = url
        cfg.ocr.vision_base_url = url
    if os.environ.get("UPSC_NOTES_LLM_URL"):
        cfg.llm.base_url = os.environ["UPSC_NOTES_LLM_URL"]
    if os.environ.get("UPSC_NOTES_EMBEDDINGS_URL"):
        cfg.embeddings.base_url = os.environ["UPSC_NOTES_EMBEDDINGS_URL"]
    if os.environ.get("UPSC_NOTES_HOST"):
        cfg.host = os.environ["UPSC_NOTES_HOST"]
    if os.environ.get("UPSC_NOTES_PORT"):
        try:
            cfg.port = int(os.environ["UPSC_NOTES_PORT"])
        except ValueError:
            pass
    if os.environ.get("UPSC_NOTES_DATA_DIR"):
        cfg.data_dir = os.environ["UPSC_NOTES_DATA_DIR"]
    if os.environ.get("UPSC_NOTES_EXTRACTION_ENGINE"):
        cfg.extraction.engine = os.environ["UPSC_NOTES_EXTRACTION_ENGINE"]
    if os.environ.get("UPSC_NOTES_OCR_ENGINE"):
        cfg.ocr.engine = os.environ["UPSC_NOTES_OCR_ENGINE"]
    return cfg
