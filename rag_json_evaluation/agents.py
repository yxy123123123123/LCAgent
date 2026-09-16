"""Decision and coding agents retained as explicit stages of the main chain."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .io_utils import load_json
from .model_client import JsonResponse, ModelClient


class DecisionAgent:
    def __init__(self, config: dict[str, Any], model: ModelClient) -> None:
        self.model = model
        self.prompt = (Path(__file__).parent / "prompts" / "decision_system.txt").read_text(encoding="utf-8")

    def run(self, task: dict[str, Any]) -> JsonResponse:
        return self.model.chat_json(self.prompt, {
            "task_metadata": task,
            "user_prompt": task["user_prompt"],
        })


class CodingAgent:
    def __init__(self, config: dict[str, Any], model: ModelClient) -> None:
        self.model = model
        self.prompt = (Path(__file__).parent / "prompts" / "coding_system.txt").read_text(encoding="utf-8")
        self.plugin_contract = load_json(config["paths"]["plugin_contract_path"])

    def run(
        self,
        task: dict[str, Any],
        decision_plan: dict[str, Any],
        retrieval: dict[str, Any],
    ) -> JsonResponse:
        return self.model.chat_json(self.prompt, {
            "task_metadata": task,
            "decision_plan": decision_plan,
            "retrieval_evidence": {
                "chunks": retrieval["results"],
                "context_text": retrieval["context_text"],
            },
            "ue_plugin_contract": self.plugin_contract,
        })
