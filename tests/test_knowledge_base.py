from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "LCA"))

from knowledge_base import KnowledgeBaseError, KnowledgeBaseManager  # noqa: E402


class FakeEmbeddingGateway:
    @staticmethod
    def _vector(text: str):
        lowered = text.casefold()
        return [
            float(lowered.count("道路") + lowered.count("road")),
            float(lowered.count("地形") + lowered.count("terrain")),
            1.0,
        ]

    def embed_texts(self, texts, *, profile_id=None):
        return [self._vector(text) for text in texts]

    def embed_query(self, text, *, profile_id=None):
        return self._vector(text)


class KnowledgeBaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manager = KnowledgeBaseManager(
            "workspace-test",
            data_root=Path(self.temp_dir.name),
            embedding_gateway=FakeEmbeddingGateway(),
        )
        self.base = self.manager.create_base("规范库")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_text_ingestion_search_and_citation(self):
        result = self.manager.ingest_bytes(
            self.base["id"],
            "道路规范.txt",
            "道路纵坡应结合地形条件进行设计。\n其他说明。".encode("utf-8"),
            embedding_profile_id="embedding-test",
        )
        matches = self.manager.search(
            "道路设计",
            kb_ids=[self.base["id"]],
            embedding_profile_id="embedding-test",
        )

        self.assertEqual(1, result["chunk_count"])
        self.assertEqual("道路规范.txt", matches[0]["citation"])
        self.assertIn("道路纵坡", matches[0]["text"])

    def test_same_content_is_deduplicated_within_base(self):
        content = b"same content"
        self.manager.ingest_bytes(
            self.base["id"], "a.txt", content, embedding_profile_id="embedding-test"
        )
        with self.assertRaisesRegex(KnowledgeBaseError, "已经入库"):
            self.manager.ingest_bytes(
                self.base["id"], "b.txt", content, embedding_profile_id="embedding-test"
            )


if __name__ == "__main__":
    unittest.main()

