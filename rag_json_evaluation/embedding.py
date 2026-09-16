"""OpenAI-compatible embedding adapter. Instantiation is lazy and API-free."""

from __future__ import annotations

from typing import Any

from openai import OpenAI

from .config import env_value, require_env


class EmbeddingClient:
    def __init__(self, config: dict[str, Any]) -> None:
        section = config["embedding"]
        api_key = require_env(section["api_key_env"], "DASHSCOPE_API_KEY")
        base_url = env_value(section["base_url_env"], "DASHSCOPE_BASE_URL")
        model = env_value(section["model_env"], "DASHSCOPE_EMBED_MODEL", default=section["default_model"])
        self.model = str(model)
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=section["timeout_seconds"],
            max_retries=section["max_retries"],
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self.client.embeddings.create(model=self.model, input=texts)
        ordered = sorted(response.data, key=lambda item: item.index)
        return [list(item.embedding) for item in ordered]
