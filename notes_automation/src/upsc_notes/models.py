"""Data structures that flow through the pipeline and are persisted per job."""

from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field

BlockKind = Literal["heading", "paragraph", "bullet", "table"]
HeadingRole = Literal["section", "topic", "subheading", "ignore"]


class Block(BaseModel):
    """One structural unit of an extracted document, already in reading order."""

    kind: BlockKind
    text: str
    page: Optional[int] = None
    role: Optional[HeadingRole] = None  # headings only
    depth: int = 0  # bullet nesting depth
    style: Optional[str] = None  # heading style id (see layout analysis)
    size: Optional[float] = None


class StyleInfo(BaseModel):
    id: str
    size: float
    bold: bool
    color: str
    count: int
    examples: list[str]
    role: Optional[HeadingRole] = None
    upper_ratio: float = 0.0
    numbered_ratio: float = 0.0
    numbering_depth: float = 0.0


class ExtractedDocument(BaseModel):
    source_name: str
    kind: Literal["pdf", "image", "markdown", "text"]
    page_count: int = 1
    pages_processed: list[int] = Field(default_factory=list)
    blocks: list[Block] = Field(default_factory=list)
    styles: list[StyleInfo] = Field(default_factory=list)
    style_roles_by: Optional[str] = None  # "llm" | "heuristic" | "markup"
    ocr_pages: list[int] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def to_markdown(self) -> str:
        return blocks_to_markdown(self.blocks, heading_base=1)


class Segment(BaseModel):
    """A contiguous chunk of the source that should become (part of) one note."""

    id: int
    title: str
    section: Optional[str] = None
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    text: str

    @property
    def pages(self) -> str:
        if not self.page_start:
            return ""
        if not self.page_end or self.page_end == self.page_start:
            return str(self.page_start)
        return f"{self.page_start}–{self.page_end}"


class Candidate(BaseModel):
    path: str
    title: str
    subject: str
    subfolder: str
    score: float = 0.0


class Topic(BaseModel):
    """Per-segment analysis state (triage + placement)."""

    id: int
    title: str
    original_title: str
    section: Optional[str] = None
    pages: str = ""
    chars: int = 0
    stage: Literal["new", "triaged", "located", "done", "error"] = "new"
    relevant: Optional[bool] = None
    skip_reason: Optional[str] = None
    summary: Optional[str] = None
    keywords: list[str] = Field(default_factory=list)
    subject: Optional[str] = None
    secondary_subject: Optional[str] = None
    # Placement
    dest_subject: Optional[str] = None
    dest_subfolder: Optional[str] = None
    match_path: Optional[str] = None  # existing note that already covers this topic
    match_topic: Optional[int] = None  # earlier topic in this upload that covers it
    confidence: Optional[float] = None
    reason: Optional[str] = None
    alternatives: list[Candidate] = Field(default_factory=list)
    related: list[Candidate] = Field(default_factory=list)
    proposal_id: Optional[str] = None
    error: Optional[str] = None
    forced: bool = False  # user asked to include a topic the triage skipped


ProposalStatus = Literal["pending", "generating", "ready", "approved", "rejected", "applied", "error"]


class Proposal(BaseModel):
    """One change to the notes repository: a new note, or an update to an existing note."""

    id: str
    action: Literal["create", "update"]
    subject: str
    subfolder: str
    title: str
    target_path: Optional[str] = None  # existing note (update) — repo-relative
    topic_ids: list[int] = Field(default_factory=list)
    status: ProposalStatus = "pending"
    content: Optional[str] = None  # generated Markdown body
    edited: bool = False
    confidence: float = 0.0
    flags: list[str] = Field(default_factory=list)
    related: list[Candidate] = Field(default_factory=list)
    no_new_information: bool = False
    applied_path: Optional[str] = None
    applied_at: Optional[str] = None
    error: Optional[str] = None
    generation_seconds: Optional[float] = None


class JobOptions(BaseModel):
    model: Optional[str] = None
    pages: Optional[str] = None
    skip_irrelevant: bool = True
    auto_approve_threshold: Optional[float] = None


JobStatus = Literal["queued", "running", "review", "applied", "failed", "cancelled"]


class Job(BaseModel):
    id: str
    created_at: str
    updated_at: str
    status: JobStatus = "queued"
    stage: Optional[str] = None
    progress_done: int = 0
    progress_total: int = 0
    message: Optional[str] = None
    tag: str
    source_url: Optional[str] = None
    filename: str
    input_path: str
    file_sha256: str
    source_id: str
    options: JobOptions = Field(default_factory=JobOptions)
    model: Optional[str] = None
    error: Optional[str] = None
    stats: dict = Field(default_factory=dict)
    duplicate_of: Optional[str] = None  # earlier source ingested from the same file
    applied_commits: list[str] = Field(default_factory=list)


def blocks_to_markdown(blocks: list[Block], heading_base: int = 1) -> str:
    """Render blocks as Markdown. heading_base=1 maps sections→#, topics→##, subheadings→###."""
    levels = {"section": 0, "topic": 1, "subheading": 2}
    out: list[str] = []
    prev_kind = None
    for b in blocks:
        if b.kind == "heading":
            if b.role == "ignore":
                out.append(f"**{b.text}**")
            else:
                lvl = min(6, heading_base + levels.get(b.role or "subheading", 2))
                out.append("\n" + "#" * lvl + " " + b.text)
        elif b.kind == "bullet":
            if prev_kind not in ("bullet",):
                out.append("")
            out.append("  " * b.depth + "- " + b.text)
        elif b.kind == "table":
            out.append("\n" + b.text.strip() + "\n")
        else:
            out.append("\n" + b.text)
        prev_kind = b.kind
    text = "\n".join(out).strip() + "\n"
    return re.sub(r"\n{3,}", "\n\n", text)
