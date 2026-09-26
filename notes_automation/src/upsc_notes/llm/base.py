"""Provider-agnostic interfaces for local chat and embedding models.

Everything else in the application talks to these classes only, so swapping Qwen for
Llama/Mistral (or Ollama for a llama.cpp / LM Studio / vLLM server) is a config change.
"""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from .jsonutil import parse_json_loose


class LLMError(RuntimeError):
    """Raised when the local model server is unreachable or returns an error."""


@dataclass
class ChatResult:
    text: str
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    seconds: float = 0.0


@dataclass
class UsageStats:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    by_task: dict[str, float] = field(default_factory=dict)

    def add(self, res: ChatResult, task: str | None = None) -> None:
        self.calls += 1
        self.prompt_tokens += res.prompt_tokens or 0
        self.completion_tokens += res.completion_tokens or 0
        self.seconds += res.seconds
        if task:
            self.by_task[task] = self.by_task.get(task, 0.0) + res.seconds


_THINK_RE = re.compile(r"<think>.*?</think>", re.S)


class ChatModel(ABC):
    model: str

    def __init__(self) -> None:
        self.usage = UsageStats()

    @abstractmethod
    def _chat(
        self,
        messages: list[dict[str, str]],
        *,
        schema: dict | None,
        temperature: float | None,
        max_tokens: int | None,
    ) -> ChatResult: ...

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        schema: dict | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        task: str | None = None,
    ) -> ChatResult:
        started = time.perf_counter()
        res = self._chat(messages, schema=schema, temperature=temperature, max_tokens=max_tokens)
        res.text = _THINK_RE.sub("", res.text).strip()
        res.seconds = res.seconds or (time.perf_counter() - started)
        self.usage.add(res, task)
        return res

    def complete_text(self, system: str, user: str, *, task: str | None = None, **kw: Any) -> str:
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        return self.chat(msgs, task=task, **kw).text

    def complete_json(
        self,
        system: str,
        user: str,
        schema: dict,
        *,
        task: str | None = None,
        retries: int = 1,
        **kw: Any,
    ) -> dict:
        """Ask for JSON matching `schema` (constrained decoding when the server supports it)."""
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        # Our JSON answers are short; a cap stops small models from generating endlessly (very slow on CPU).
        kw.setdefault("max_tokens", 2048)
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            res = self.chat(msgs, schema=schema, task=task, **kw)
            try:
                data = parse_json_loose(res.text)
                if isinstance(data, dict):
                    return data
                raise ValueError("expected a JSON object")
            except ValueError as exc:
                last_err = exc
                msgs = msgs[:2] + [
                    {"role": "assistant", "content": res.text[:2000]},
                    {"role": "user", "content": "That was not valid JSON for the schema. Reply with the JSON object only."},
                ]
        raise LLMError(f"Model did not return valid JSON: {last_err}")

    def health(self) -> dict:
        return {"ok": True, "model": self.model}


class Embedder(ABC):
    model: str
    query_prefix: str = ""
    document_prefix: str = ""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed([self.document_prefix + t for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self.embed([self.query_prefix + text])[0]
