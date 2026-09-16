from __future__ import annotations

import unittest

from rag_json_evaluation.model_client import extract_json_object


class JsonExtractionTests(unittest.TestCase):
    def test_plain_json(self) -> None:
        self.assertEqual(extract_json_object('{"ok": true}'), {"ok": True})

    def test_fenced_json(self) -> None:
        self.assertEqual(extract_json_object('```json\n{"ok": true}\n```'), {"ok": True})

    def test_non_json_fails(self) -> None:
        with self.assertRaises(ValueError):
            extract_json_object("not json")


if __name__ == "__main__":
    unittest.main()

