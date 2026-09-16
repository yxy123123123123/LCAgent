from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rag_json_evaluation.context_memory import ContextMemoryStore
from rag_json_evaluation.io_utils import load_json, load_jsonl


class ContextMemoryTests(unittest.TestCase):
    def test_records_raw_turns_and_replaces_compacted_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ContextMemoryStore(Path(temporary) / "G3" / "repeat-01" / "context_memory")
            for turn_id in (1, 2):
                task = {
                    "task_id": f"T{turn_id}",
                    "conversation_id": "conversation-a",
                    "turn_id": turn_id,
                    "user_prompt": f"turn {turn_id}",
                }
                plan = {
                    "objects": [{
                        "object_id": f"road-{turn_id}",
                        "object_type": "road",
                        "operation": "create",
                    }]
                }
                store.record_turn(
                    task,
                    {"change_set": {"turn": turn_id}, "explicit_constraints": [f"c{turn_id}"]},
                    {"query": f"q{turn_id}"},
                    plan,
                    {"valid": True},
                )

            raw_files = list((store.raw_root).glob("*.jsonl"))
            compacted_files = list((store.compacted_root).glob("*.jsonl"))
            self.assertEqual(len(raw_files), 1)
            self.assertEqual(len(compacted_files), 1)
            self.assertEqual(len(load_jsonl(raw_files[0])), 2)
            compacted = load_jsonl(compacted_files[0])
            self.assertEqual(len(compacted), 1)
            self.assertEqual(compacted[0]["turn_end"], 2)
            self.assertEqual(compacted[0]["object_index"][0]["object_id"], "road-2")
            self.assertTrue(compacted[0]["supersedes_prior_turns"])
            self.assertFalse(load_json(store.root / "manifest.json")["llm_used_for_compression"])

    def test_store_roots_do_not_share_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            first = ContextMemoryStore(Path(temporary) / "G1" / "repeat-01" / "context_memory")
            second = ContextMemoryStore(Path(temporary) / "G3" / "repeat-01" / "context_memory")
            task = {"conversation_id": "same-id", "turn_id": 1, "user_prompt": "x"}
            first.record_turn(task, {}, {}, {"objects": []}, {"valid": True})
            self.assertEqual(len(first.get_compacted("same-id")), 1)
            self.assertEqual(second.get_compacted("same-id"), [])


if __name__ == "__main__":
    unittest.main()
