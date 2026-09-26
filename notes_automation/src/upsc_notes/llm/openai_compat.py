"""Backend for local servers that speak the OpenAI HTTP API.

Works with llama.cpp's `llama-server`, LM Studio, vLLM, MLX-LM's server, LocalAI, etc.
No cloud API is used — point `base_url` at the local server.
"""

from __future__ import annotations

from typing import Any

import httpx

from .base import ChatModel, ChatResult, Embedder, LLMError


def _v1(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base if base.endswith("/v1") else base + "/v1"


class OpenAICompatChat(ChatModel):
    def __init__(
        self,
        model: str,
        base_url: str,
        *,
        temperature: float = 0.2,
        timeout: float = 900.0,
        api_key: str | None = None,
    ) -> None:
        super().__init__()
        self.model = model
        self.base_url = _v1(base_url)
        self.temperature = temperature
        headers = {"Authorization": f"Bearer {api_key or 'local'}"}
        self._client = httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0), headers=headers)
        self._schema_supported = True

    def _post(self, body: dict[str, Any]) -> httpx.Response:
        try:
            return self._client.post(f"{self.base_url}/chat/completions", json=body)
        except httpx.HTTPError as exc:
            raise LLMError(f"Cannot reach model server at {self.base_url}: {exc}") from exc

    def _chat(self, messages, *, schema, temperature, max_tokens) -> ChatResult:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
        }
        if max_tokens:
            body["max_tokens"] = max_tokens
        if schema is not None and self._schema_supported:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": schema, "strict": True},
            }
        resp = self._post(body)
        if resp.status_code == 400 and "response_format" in body:
            # Older servers: fall back to plain JSON mode / prompt-only JSON.
            self._schema_supported = False
            body["response_format"] = {"type": "json_object"}
            resp = self._post(body)
            if resp.status_code == 400:
                body.pop("response_format")
                resp = self._post(body)
        if resp.status_code >= 400:
            raise LLMError(f"Model server error {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        usage = data.get("usage") or {}
        choice = (data.get("choices") or [{}])[0]
        return ChatResult(
            text=(choice.get("message") or {}).get("content") or "",
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )

    def health(self) -> dict:
        try:
            resp = self._client.get(f"{self.base_url}/models", timeout=3.0)
            ok = resp.status_code < 400
        except httpx.HTTPError:
            ok = False
        return {"ok": ok, "online": ok, "installed": ok, "model": self.model}


class OpenAICompatEmbedder(Embedder):
    def __init__(
        self,
        model: str,
        base_url: str,
        *,
        query_prefix: str = "",
        document_prefix: str = "",
        batch_size: int = 32,
        timeout: float = 300.0,
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self.base_url = _v1(base_url)
        self.query_prefix = query_prefix
        self.document_prefix = document_prefix
        self.batch_size = batch_size
        headers = {"Authorization": f"Bearer {api_key or 'local'}"}
        self._client = httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0), headers=headers)

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            try:
                resp = self._client.post(f"{self.base_url}/embeddings", json={"model": self.model, "input": batch})
            except httpx.HTTPError as exc:
                raise LLMError(f"Cannot reach embedding server at {self.base_url}: {exc}") from exc
            if resp.status_code >= 400:
                raise LLMError(f"Embedding server error {resp.status_code}: {resp.text[:300]}")
            items = sorted(resp.json().get("data", []), key=lambda d: d.get("index", 0))
            out.extend(item["embedding"] for item in items)
        return out
