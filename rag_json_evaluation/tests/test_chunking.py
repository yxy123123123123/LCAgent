from __future__ import annotations

import unittest

from rag_json_evaluation.chunking import build_group_chunks
from rag_json_evaluation.config import load_config


class ChunkingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.corpus = {
            "resource": [
                {"resource_id": "r1", "knowledge_type": "resource", "name": "道路", "ue_path": "/Game/Road"},
                {"resource_id": "r2", "knowledge_type": "resource", "name": "地形", "ue_path": "E:/dem.tif"},
            ],
            "standard": [{
                "standard_id": "s1",
                "knowledge_type": "standard",
                "title": "测试规范",
                "clauses": [
                    {"clause_id": "s1.1", "text": "道路宽度约束"},
                    {"clause_id": "s1.2", "text": "建筑间距约束"},
                ],
            }],
            "context": [{"context_id": "c1", "knowledge_type": "context", "scene_plan_snapshot": {"objects": []}}],
        }

    def test_g0_has_no_chunks(self) -> None:
        self.assertEqual(build_group_chunks(load_config("G0"), self.corpus), {})

    def test_g1_has_one_mixed_index(self) -> None:
        chunks = build_group_chunks(load_config("G1"), self.corpus)
        self.assertEqual(set(chunks), {"mixed"})
        types = {kind for chunk in chunks["mixed"] for kind in chunk["metadata"]["knowledge_types"]}
        self.assertEqual(types, {"resource", "standard", "context"})

    def test_g2_has_three_uniform_indexes(self) -> None:
        chunks = build_group_chunks(load_config("G2"), self.corpus)
        self.assertEqual(set(chunks), {"resource", "standard", "context"})
        self.assertTrue(all(
            chunk["metadata"]["chunking"] == "uniform_character_window"
            for values in chunks.values() for chunk in values
        ))

    def test_g3_preserves_entities_and_clauses(self) -> None:
        chunks = build_group_chunks(load_config("G3"), self.corpus)
        self.assertEqual(len(chunks["resource"]), 2)
        self.assertEqual(len(chunks["standard"]), 2)
        self.assertEqual(len(chunks["context"]), 1)
        self.assertEqual(chunks["standard"][0]["metadata"]["source_ids"], ["s1.1"])


if __name__ == "__main__":
    unittest.main()

