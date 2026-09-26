"""Ollama backend (https://ollama.com) — the default local runtime."""

from __future__ import annotations

import base64
from typing import Any

import re

import httpx

from .base import ChatModel, ChatResult, Embedder, LLMError


def _raise_for(resp: httpx.Response, model: str) -> None:
    if resp.status_code < 400:
        return
    try:
        detail = resp.json().get("error", resp.text)
    except ValueError:
        detail = resp.text
    if resp.status_code == 404 or "not found" in str(detail).lower():
        raise LLMError(f"Model '{model}' is not installed in Ollama. Run: ollama pull {model}")
    if re.search(r"signal: killed|out of memory|requires more system memory|oom", str(detail), re.I):
        raise LLMError(
            f"Not enough memory to run '{model}' ({detail}). Use a smaller model (e.g. qwen2.5:1.5b) "
            "or give Docker/this machine more RAM."
        )
    raise LLMError(f"Ollama error {resp.status_code}: {detail}")


def list_ollama_models(base_url: str, timeout: float = 3.0) -> list[dict[str, Any]]:
    """Installed models with their capabilities; [] when Ollama is not running."""
    try:
        resp = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=timeout)
        resp.raise_for_status()
    except httpx.HTTPError:
        return []
    out = []
    for m in resp.json().get("models", []):
        details = m.get("details") or {}
        caps = m.get("capabilities") or []
        name = m.get("name", "")
        is_embed = "embedding" in caps or "embed" in name.lower()
        out.append(
            {
                "name": name,
                "size_gb": round((m.get("size") or 0) / 1e9, 2),
                "parameters": details.get("parameter_size"),
                "quantization": details.get("quantization_level"),
                "family": details.get("family"),
                "kind": "embedding" if is_embed else "chat",
                "vision": "vision" in caps,
            }
        )
    return out


class OllamaChat(ChatModel):
    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:11434",
        *,
        num_ctx: int = 16384,
        temperature: float = 0.2,
        keep_alive: str = "30m",
        timeout: float = 900.0,
        think: bool | None = None,
    ) -> None:
        super().__init__()
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.num_ctx = num_ctx
        self.temperature = temperature
        self.keep_alive = keep_alive
        self.think = think
        self._client = httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0))

    def _chat(self, messages, *, schema, temperature, max_tokens) -> ChatResult:
        options: dict[str, Any] = {
            "temperature": self.temperature if temperature is None else temperature,
            "num_ctx": self.num_ctx,
        }
        if max_tokens:
            options["num_predict"] = max_tokens
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": options,
            "keep_alive": self.keep_alive,
        }
        if schema is not None:
            body["format"] = schema
        if self.think is not None:
            body["think"] = self.think
        try:
            resp = self._client.post(f"{self.base_url}/api/chat", json=body)
        except httpx.HTTPError as exc:
            raise LLMError(f"Cannot reach Ollama at {self.base_url} ({exc}). Is `ollama serve` running?") from exc
        _raise_for(resp, self.model)
        data = resp.json()
        return ChatResult(
            text=(data.get("message") or {}).get("content", ""),
            prompt_tokens=data.get("prompt_eval_count"),
            completion_tokens=data.get("eval_count"),
            seconds=(data.get("total_duration") or 0) / 1e9,
        )

    def describe_image(self, image_bytes: bytes, prompt: str, max_tokens: int = 4096) -> str:
        """Vision call (for OCR with a multimodal model such as qwen2.5vl)."""
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt, "images": [base64.b64encode(image_bytes).decode()]}],
            "stream": False,
            "options": {"temperature": 0.0, "num_ctx": self.num_ctx, "num_predict": max_tokens},
            "keep_alive": self.keep_alive,
        }
        try:
            resp = self._client.post(f"{self.base_url}/api/chat", json=body)
        except httpx.HTTPError as exc:
            raise LLMError(f"Cannot reach Ollama at {self.base_url}: {exc}") from exc
        _raise_for(resp, self.model)
        return (resp.json().get("message") or {}).get("content", "")

    def health(self) -> dict:
        models = list_ollama_models(self.base_url)
        names = {m["name"] for m in models}
        installed = self.model in names or f"{self.model}:latest" in names
        return {"ok": bool(models) and installed, "online": bool(models), "installed": installed, "model": self.model,
                "processor": self.processor()}

    def processor(self) -> str | None:
        """Where the loaded model runs, like `ollama ps`: "GPU", "CPU" or "48% GPU" (None when not loaded)."""
        try:
            loaded = self._client.get(f"{self.base_url}/api/ps", timeout=3).json().get("models", [])
        except (httpx.HTTPError, ValueError):
            return None
        m = next((m for m in loaded if m.get("name") in (self.model, f"{self.model}:latest")), None)
        if not m or not m.get("size"):
            return None
        share = m.get("size_vram", 0) / m["size"]
        return "CPU" if share < 0.01 else "GPU" if share > 0.99 else f"{share:.0%} GPU"


class OllamaEmbedder(Embedder):
    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:11434",
        *,
        query_prefix: str = "",
        document_prefix: str = "",
        batch_size: int = 32,
        timeout: float = 300.0,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.query_prefix = query_prefix
        self.document_prefix = document_prefix
        self.batch_size = batch_size
        self._client = httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0))

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            try:
                resp = self._client.post(
                    f"{self.base_url}/api/embed",
                    json={"model": self.model, "input": batch, "truncate": True, "keep_alive": "30m"},
                )
            except httpx.HTTPError as exc:
                raise LLMError(f"Cannot reach Ollama at {self.base_url}: {exc}") from exc
            _raise_for(resp, self.model)
            vecs = resp.json().get("embeddings") or []
            if len(vecs) != len(batch):
                raise LLMError(f"Embedding model returned {len(vecs)} vectors for {len(batch)} inputs")
            out.extend(vecs)
        return out
