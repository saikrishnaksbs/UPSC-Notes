"""Factories that build the configured local model clients."""

from __future__ import annotations

from ..config import AppConfig
from .base import ChatModel, ChatResult, Embedder, LLMError
from .ollama import OllamaChat, OllamaEmbedder, list_ollama_models
from .openai_compat import OpenAICompatChat, OpenAICompatEmbedder

__all__ = [
    "ChatModel",
    "ChatResult",
    "Embedder",
    "LLMError",
    "build_chat_model",
    "build_embedder",
    "list_ollama_models",
]


def build_chat_model(cfg: AppConfig, model: str | None = None) -> ChatModel:
    c = cfg.llm
    name = model or c.model
    if c.provider == "ollama":
        return OllamaChat(
            name,
            c.base_url,
            num_ctx=c.num_ctx,
            temperature=c.temperature,
            keep_alive=c.keep_alive,
            timeout=c.timeout,
            think=c.think,
        )
    return OpenAICompatChat(name, c.base_url, temperature=c.temperature, timeout=c.timeout, api_key=c.api_key)


def build_embedder(cfg: AppConfig) -> Embedder | None:
    e = cfg.embeddings
    if e.provider == "none":
        return None
    if e.provider == "ollama":
        return OllamaEmbedder(
            e.model,
            e.base_url,
            query_prefix=e.query_prefix,
            document_prefix=e.document_prefix,
            batch_size=e.batch_size,
            timeout=e.timeout,
        )
    return OpenAICompatEmbedder(
        e.model,
        e.base_url,
        query_prefix=e.query_prefix,
        document_prefix=e.document_prefix,
        batch_size=e.batch_size,
        timeout=e.timeout,
        api_key=e.api_key,
    )
