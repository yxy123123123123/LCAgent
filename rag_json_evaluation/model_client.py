"""JSON-only chat adapter with raw-response preservation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from .config import env_value, require_env


@dataclass
class JsonResponse:
    value: dict[str, Any]
    raw_text: str
    usage: dict[str, Any]


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    candidates = [stripped]
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        candidates.append(fenced.group(1))
    first, last = stripped.find("{"), stripped.rfind("}")
    if first >= 0 and last > first:
        candidates.append(stripped[first:last + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("Model response does not contain one parseable JSON object")


class ModelClient:
    def __init__(self, config: dict[str, Any]) -> None:
        section = config["model"]
        api_key = require_env(section["api_key_env"], "DEEPSEEK_API_KEY")
        base_url = env_value(section["base_url_env"], "DEEPSEEK_BASE_URL", default=section["default_base_url"])
        self.model = str(env_value(section["model_env"], "DEEPSEEK_MODEL", default=section["default_model"]))
        self.temperature = section["temperature"]
        self.top_p = section["top_p"]
        self.max_tokens = section["max_tokens"]
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=section["timeout_seconds"],
            max_retries=section["max_retries"],
        )

    def chat_json(self, system_prompt: str, payload: dict[str, Any]) -> JsonResponse:
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            top_p=self.top_p,
            max_tokens=self.max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        )
        raw_text = response.choices[0].message.content or ""
        usage = response.usage.model_dump() if response.usage else {}
        return JsonResponse(extract_json_object(raw_text), raw_text, usage)
