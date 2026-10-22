from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "LCA"))

from embedding_gateway import (  # noqa: E402
    EmbeddingGateway,
    EmbeddingProfile,
    EmbeddingProfileStore,
)


class EmbeddingGatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.store = EmbeddingProfileStore(root / "profiles.json", root / ".env")
        self.profile = EmbeddingProfile(
            profile_id="embedding-test",
            display_name="Embedding Test",
            provider="openai_compatible",
            model="embed-model",
            base_url="https://models.example.com/v1",
            api_key_env="EMBEDDING_TEST_API_KEY",
            dimensions=3,
            batch_size=2,
        )

    def tearDown(self):
        os.environ.pop("EMBEDDING_TEST_API_KEY", None)
        self.temp_dir.cleanup()

    def test_profile_secret_is_separate_and_vectors_keep_input_order(self):
        calls = []

        class Embeddings:
            @staticmethod
            def create(**kwargs):
                calls.append(kwargs)
                return SimpleNamespace(
                    data=[
                        SimpleNamespace(index=index, embedding=[float(len(text)), index, 1.0])
                        for index, text in enumerate(kwargs["input"])
                    ]
                )

        self.store.upsert(self.profile, api_key="secret", set_default=True)
        gateway = EmbeddingGateway(
            self.store,
            client_factory=lambda **_: SimpleNamespace(embeddings=Embeddings()),
        )
        vectors = gateway.embed_texts(["a", "bb", "ccc"])

        self.assertEqual([[1.0, 0.0, 1.0], [2.0, 1.0, 1.0], [3.0, 0.0, 1.0]], vectors)
        self.assertEqual(2, len(calls))
        self.assertEqual(3, calls[0]["dimensions"])
        self.assertNotIn("secret", self.store.profiles_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

