"""Shared fixtures: a miniature notes repository with the real repo's conventions, and a scripted LLM."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from upsc_notes.config import AppConfig
from upsc_notes.llm.base import ChatModel, ChatResult


def _w(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def build_fixture_repo(root: Path) -> Path:
    # Polity: MASTER-README style A (bullet list per module), absolute file:// links like the real repo.
    _w(root, "Polity/MASTER-README.md", """# Polity: Master Study Notes Index

> **Source**: [https://compass.rauias.com/polity/](https://compass.rauias.com/polity/)

---
## 📚 Modules Overview

### 07. [Fundamental Rights](file:///Users/x/Upsc/Polity/07-fundamental-rights/README.md)
  - [Fundamental Rights and its features](file:///Users/x/Upsc/Polity/07-fundamental-rights/01-fundamental-rights-and-its-features.md)
  - [Article 21](file:///Users/x/Upsc/Polity/07-fundamental-rights/02-article-21.md)

### 25. [Constitutional Bodies](file:///Users/x/Upsc/Polity/25-constitutional-bodies/README.md)
  - [Election Commission of India](file:///Users/x/Upsc/Polity/25-constitutional-bodies/01-election-commission-of-india.md)
""")
    _w(root, "Polity/07-fundamental-rights/README.md", """# Fundamental Rights - Notes Index

| # | Sub-Topic Note | Source URL |
| :--- | :--- | :--- |
| 1 | [Fundamental Rights and its features](file:///Users/x/Upsc/Polity/07-fundamental-rights/01-fundamental-rights-and-its-features.md) | [Fundamental Rights and its features](https://compass.rauias.com/polity/fundamental-rights-features/) |
| 2 | [Article 21](file:///Users/x/Upsc/Polity/07-fundamental-rights/02-article-21.md) | [Article 21](https://compass.rauias.com/polity/article-21/) |
""")
    _w(root, "Polity/07-fundamental-rights/01-fundamental-rights-and-its-features.md", """# Fundamental Rights and its features

> **Source**: [Fundamental Rights and its features](https://compass.rauias.com/polity/fundamental-rights-features/)

---

## Fundamental Rights and its features

- Fundamental Rights are enshrined in Part III of the Constitution (Articles 12 to 35).
- They are justiciable and enforceable through Article 32.
""")
    _w(root, "Polity/07-fundamental-rights/02-article-21.md", """# Article 21

> **Source**: [Article 21](https://compass.rauias.com/polity/article-21/)

---

## Article 21 (Right to Life and Liberty)

- No person shall be deprived of his life or personal liberty except according to procedure established by law.
- The right to a speedy trial is part of Article 21 as held in Hussainara Khatoon case.
""")
    _w(root, "Polity/07-fundamental-rights/fundamental-rights-complete.md", """# Fundamental Rights: Complete Module Notes
> **Source Subject**: [Polity](https://compass.rauias.com/polity/)
---
## 1. Fundamental Rights and its features
> Source: [https://compass.rauias.com/polity/fundamental-rights-features/](https://compass.rauias.com/polity/fundamental-rights-features/)

## Fundamental Rights and its features

- Fundamental Rights are enshrined in Part III of the Constitution.

## 2. Article 21
> Source: [https://compass.rauias.com/polity/article-21/](https://compass.rauias.com/polity/article-21/)

## Article 21 (Right to Life and Liberty)

- No person shall be deprived of his life or personal liberty.
""")
    _w(root, "Polity/25-constitutional-bodies/README.md", """# Constitutional Bodies - Notes Index

| # | Sub-Topic Note | Source URL |
| :--- | :--- | :--- |
| 1 | [Election Commission of India](file:///Users/x/Upsc/Polity/25-constitutional-bodies/01-election-commission-of-india.md) | [Election Commission of India](https://compass.rauias.com/polity/eci/) |
""")
    _w(root, "Polity/25-constitutional-bodies/01-election-commission-of-india.md", """# Election Commission of India

> **Source**: [Election Commission of India](https://compass.rauias.com/polity/eci/)

---

## Election Commission of India

- Article 324 vests superintendence of elections in the Election Commission.
- It prepares electoral rolls and conducts elections to Parliament and state legislatures.
""")
    _w(root, "Polity/25-constitutional-bodies/constitutional-bodies-complete.md", """# Constitutional Bodies: Complete Module Notes
---
## 1. Election Commission of India
> Source: [eci](https://compass.rauias.com/polity/eci/)

- Article 324 vests superintendence of elections in the Election Commission.
""")
    # Science & tech: an existing Gaganyaan note (update target).
    _w(root, "Science_and_technology/MASTER-README.md", """# Science and technology: Master Study Notes Index

---
## 📚 Modules Overview

### 02. [Space Technology](file:///Users/x/Upsc/Science_and_technology/02-space-technology/README.md)
  - [Gaganyaan](file:///Users/x/Upsc/Science_and_technology/02-space-technology/10-gaganyaan.md)
""")
    _w(root, "Science_and_technology/02-space-technology/README.md", """# Space Technology - Notes Index

| # | Sub-Topic Note | Source URL |
| :--- | :--- | :--- |
| 10 | [Gaganyaan](file:///Users/x/Upsc/Science_and_technology/02-space-technology/10-gaganyaan.md) | [Gaganyaan](https://compass.rauias.com/science-technology/gaganyaan/) |
""")
    _w(root, "Science_and_technology/02-space-technology/10-gaganyaan.md", """# Gaganyaan

> **Source**: [Gaganyaan](https://compass.rauias.com/science-technology/gaganyaan/)

---

## Gaganyaan Mission

- Gaganyaan is India's human spaceflight programme led by ISRO.
- It aims to send a crew of three astronauts to low earth orbit of 400 km.
""")
    _w(root, "Science_and_technology/02-space-technology/space-technology-complete.md", """# Space Technology: Complete Module Notes
---
## 10. Gaganyaan
> Source: [g](https://compass.rauias.com/science-technology/gaganyaan/)

- Gaganyaan is India's human spaceflight programme led by ISRO.
""")
    # Ancient history: MASTER-README style B (counts), mixed-case folder names.
    _w(root, "Ancient_history/MASTER-README.md", """# Ancient Indian History: Master UPSC Study Notes Index

## 📚 Syllabus & Folder Navigation

### [02-Vedic-Period](02-Vedic-Period/README.md)
**Topic**: Vedic Period
- **Master Module Note**: [02-Vedic-Period-complete.md](02-Vedic-Period/vedic-period-complete.md)
- **Sub-Topics**: 1 individual note files inside [02-Vedic-Period](02-Vedic-Period/)
""")
    _w(root, "Ancient_history/02-Vedic-Period/README.md", """# Vedic Period - Notes Index

| # | Sub-Topic Note | Source URL |
| :--- | :--- | :--- |
| 1 | [Rigveda](file:///Users/x/Upsc/Ancient_history/02-Vedic-Period/01-rigveda.md) | [Rigveda](https://compass.rauias.com/ancient-history/rigveda/) |
""")
    _w(root, "Ancient_history/02-Vedic-Period/01-rigveda.md", "# Rigveda\n\n> **Source**: [Rigveda](https://x)\n\n---\n\n## Rigveda\n\n- 10 mandals and 1028 suktas.\n")
    _w(root, "Ancient_history/02-Vedic-Period/vedic-period-complete.md", "# Vedic Period: Complete\n---\n## 1. Rigveda\n> Source: [x](https://x)\n\n- 10 mandals.\n")
    # Things the scanner must ignore.
    _w(root, "notes_automation/README.md", "# tool\n")
    _w(root, "Polity/Polity.md", "# Indian Polity & Governance: Complete UPSC Notes\n")
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
    return root


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    return build_fixture_repo(tmp_path / "notes")


@pytest.fixture()
def cfg(repo: Path, tmp_path: Path) -> AppConfig:
    c = AppConfig()
    c.base_dir = str(tmp_path)
    c.repo.path = str(repo)
    c.data_dir = str(tmp_path / "data")
    c.embeddings.provider = "none"
    return c


class FakeLLM(ChatModel):
    """Deterministic stand-in for the local model; answers from simple keyword rules."""

    def __init__(self) -> None:
        super().__init__()
        self.model = "fake"
        self.calls: list[str] = []

    def health(self) -> dict:
        return {"ok": True, "online": True, "installed": True, "model": self.model}

    def _chat(self, messages, *, schema, temperature, max_tokens) -> ChatResult:
        prompt = messages[-1]["content"]
        self.calls.append(prompt[:60])
        if schema is None:
            return ChatResult(text=self._write(prompt))
        props = schema.get("properties", {})
        if "not_relevant_reason" in props:
            return ChatResult(text=json.dumps(self._triage(prompt, props)))
        if "folder" in props:
            return ChatResult(text=json.dumps(self._locate(prompt, props)))
        if "topics" in props:
            return ChatResult(text=json.dumps({"first_paragraph_continues_previous_topic": False, "topics": [{"start": 0, "title": "Whole text"}]}))
        return ChatResult(text=json.dumps({k: (v.get("enum") or ["topic"])[0] for k, v in props.items()}))

    def _triage(self, prompt: str, props: dict) -> dict:
        heading = re.search(r"HEADING: (.*)", prompt).group(1)
        body = re.search(r"<<<\n(.*?)\n>>>", prompt, flags=re.S)
        low = (heading + " " + (body.group(1) if body else "")).lower()
        subjects = props["subject"]["enum"]
        subject = "Polity"
        if "gaganyaan" in low or "isro" in low or "spacecraft" in low:
            subject = "Science_and_technology"
        elif "rigveda" in low or "vedic" in low:
            subject = "Ancient_history"
        relevant = not ("admission open" in low or "enrol now" in low)
        return {
            "title": heading.strip(),
            "summary": f"About {heading.strip()}.",
            "keywords": [w for w in re.findall(r"[A-Z][a-z]+", heading)][:5],
            "relevant": relevant,
            "not_relevant_reason": "" if relevant else "Advertisement",
            "subject": subject if subject in subjects else subjects[0],
            "secondary_subject": "none",
        }

    def _locate(self, prompt: str, props: dict) -> dict:
        topic = re.search(r"TOPIC: (.*)", prompt).group(1).lower()
        folders = re.findall(r"^(F\d+)\. (.*)$", prompt, flags=re.M)
        notes = re.findall(r'^([NB]\d+)\. .*?"(.*?)"', prompt, flags=re.M)
        same = "none"
        for nid, title in notes:
            words = {w for w in re.findall(r"[a-z]{5,}", title.lower())}
            if words and words & set(re.findall(r"[a-z]{5,}", topic)) and nid in props["same_as"]["enum"]:
                same = nid
                break
        folder = folders[0][0]
        for fid, line in folders:
            if ("election" in topic and "Constitutional Bodies" in line) or ("rights" in topic and "Fundamental Rights" in line):
                folder = fid
        return {"reason": "keyword match", "folder": folder, "same_as": same, "confidence": 0.9}

    def _write(self, prompt: str) -> str:
        if "An existing UPSC note already covers this topic" in prompt:
            return (
                "### Why in News\n- ISRO completed the first uncrewed orbital test flight G1 in 2026.\n"
                "- Gaganyaan is India's human spaceflight programme led by ISRO.\n"
                "### Key Updates\n- The crew module splashed down in the Bay of Bengal.\n"
            )
        if "This is part" in prompt:
            return "- condensed fact 2026"
        return (
            "```markdown\n# Title the model should not write\n\n"
            "## Why in News\n- The Election Commission began a Special Intensive Revision (SIR) of electoral rolls in 2026.\n\n"
            "## Key Facts for Prelims\n- Article 324 empowers the Election Commission.\n- It covers 12 States.\n\n"
            "## Challenges and Concerns\n- Not mentioned in the source.\n\n"
            "## Way Forward\n```"
        )


@pytest.fixture()
def fake_llm() -> FakeLLM:
    return FakeLLM()
