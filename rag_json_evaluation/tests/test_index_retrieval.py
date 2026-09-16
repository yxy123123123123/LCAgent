from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rag_json_evaluation.config import load_config
from rag_json_evaluation.index_store import build_indexes
from rag_json_evaluation.retrieval import RetrievalAgent


class FakeEmbedder:
    model = "fake-local-test"

    def embed(self, texts):
        vectors = []
        for text in texts:
            vectors.append([
                float(text.count("道路") + text.count("road")),
                float(text.count("规范")),
                float(text.count("保持") + text.count("context")),
                1.0,
            ])
        return vectors


class IndexRetrievalTests(unittest.TestCase):
    def test_g3_index_and_runtime_context_without_api(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            corpus = root / "corpus"
            for directory in ("resources", "standards", "context"):
                (corpus / directory).mkdir(parents=True, exist_ok=True)
            (corpus / "resources" / "resources.jsonl").write_text(
                '{"resource_id":"road1","knowledge_type":"resource","name":"道路","ue_path":"/Game/Road"}\n',
                encoding="utf-8",
            )
            (corpus / "standards" / "standards.jsonl").write_text(
                '{"clause_id":"s1","knowledge_type":"standard","text":"道路规范"}\n',
                encoding="utf-8",
            )
            config = load_config("G3")
            config["paths"]["corpus_root"] = str(corpus)
            config["paths"]["indexes_root"] = str(root / "indexes")
            config["retrieval"]["top_k"] = 5
            embedder = FakeEmbedder()
            manifest = build_indexes(config, embedder)
            self.assertEqual(manifest["indexes"]["resource"]["chunk_count"], 1)

            agent = RetrievalAgent(config, embedder)
            result = agent.retrieve(
                {"task_id": "t2", "turn_id": 2, "user_prompt": "保持已有道路不变"},
                [{"context_id": "c1", "knowledge_type": "context", "scene_plan_snapshot": {"objects": []}}],
            )
            self.assertIn("context", result["routes"])
            self.assertTrue(any("context" in item["metadata"]["knowledge_types"] for item in result["results"]))


if __name__ == "__main__":
    unittest.main()

