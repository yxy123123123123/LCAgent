from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "LCA"))

from model_gateway import (  # noqa: E402
    ModelConfigurationError,
    ModelGateway,
    ModelInvocationError,
    ModelProfile,
    ModelProfileStore,
)


class FakeChatModel:
    def __init__(self, reply: str = "OK"):
        self.reply = reply
        self.messages = None

    def invoke(self, messages):
        self.messages = messages
        return SimpleNamespace(
            content=self.reply,
            usage_metadata={"input_tokens": 3, "output_tokens": 1},
            response_metadata={"finish_reason": "stop"},
        )


class ModelGatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.profiles_path = root / "profiles.json"
        self.env_path = root / ".env"
        self.store = ModelProfileStore(self.profiles_path, self.env_path)
        self.profile = ModelProfile(
            profile_id="deepseek-test",
            display_name="DeepSeek Test",
            provider="deepseek",
            model="deepseek-flash",
            base_url="https://api.deepseek.com",
            api_key_env="MODEL_DEEPSEEK_TEST_API_KEY",
        )

    def tearDown(self):
        os.environ.pop("MODEL_DEEPSEEK_TEST_API_KEY", None)
        self.temp_dir.cleanup()

    def test_profile_json_never_contains_api_key(self):
        self.store.upsert(self.profile, api_key="secret-value", set_default=True)

        profile_document = self.profiles_path.read_text(encoding="utf-8")
        env_document = self.env_path.read_text(encoding="utf-8")
        self.assertNotIn("secret-value", profile_document)
        self.assertIn("MODEL_DEEPSEEK_TEST_API_KEY=secret-value", env_document)
        self.assertEqual("deepseek-test", json.loads(profile_document)["default_profile_id"])

    def test_gateway_uses_selected_profile_and_returns_metadata(self):
        fake_model = FakeChatModel("connected")
        captured = {}

        def factory(profile, api_key):
            captured["profile"] = profile
            captured["api_key"] = api_key
            return fake_model

        self.store.upsert(self.profile, api_key="secret-value", set_default=True)
        reply = ModelGateway(self.store, model_factory=factory).chat(
            [{"role": "user", "content": "hello"}]
        )

        self.assertEqual("connected", reply.content)
        self.assertEqual("deepseek-test", reply.profile_id)
        self.assertEqual("secret-value", captured["api_key"])
        self.assertEqual("DeepSeek Test", captured["profile"].display_name)
        self.assertEqual(3, reply.usage["input_tokens"])

    def test_invalid_profile_id_is_rejected(self):
        invalid = ModelProfile(
            profile_id="Bad Profile",
            display_name="Bad",
            provider="deepseek",
            model="deepseek-flash",
            base_url="https://api.deepseek.com",
            api_key_env="BAD_API_KEY",
        )
        with self.assertRaises(ModelConfigurationError):
            self.store.upsert(invalid)

    def test_invalid_context_compression_settings_are_rejected(self):
        invalid = ModelProfile(
            profile_id="invalid-context",
            display_name="Invalid Context",
            provider="deepseek",
            model="deepseek-chat",
            base_url="https://api.deepseek.com",
            api_key_env="INVALID_CONTEXT_API_KEY",
            context_window_tokens=2048,
            compression_trigger_ratio=0.95,
            keep_recent_turns=0,
        )
        with self.assertRaises(ModelConfigurationError):
            self.store.upsert(invalid)

    def test_context_compression_settings_are_persisted(self):
        configured = ModelProfile(
            **{
                **self.profile.__dict__,
                "context_window_tokens": 128000,
                "compression_trigger_ratio": 0.7,
                "keep_recent_turns": 8,
            }
        )
        self.store.upsert(configured, api_key="secret-value", set_default=True)

        loaded = self.store.get(configured.profile_id)
        self.assertEqual(128000, loaded.context_window_tokens)
        self.assertEqual(0.7, loaded.compression_trigger_ratio)
        self.assertEqual(8, loaded.keep_recent_turns)

    def test_deleting_profile_can_remove_secret(self):
        self.store.upsert(self.profile, api_key="secret-value", set_default=True)
        self.store.delete(self.profile.profile_id, delete_secret=True)

        self.assertNotIn("MODEL_DEEPSEEK_TEST_API_KEY", self.env_path.read_text(encoding="utf-8"))
        self.assertEqual([], self.store.list_profiles())

    def test_deleting_profile_preserves_shared_secret(self):
        second_profile = ModelProfile(
            profile_id="deepseek-backup",
            display_name="DeepSeek Backup",
            provider="deepseek",
            model="deepseek-flash",
            base_url="https://api.deepseek.com",
            api_key_env=self.profile.api_key_env,
        )
        self.store.upsert(self.profile, api_key="secret-value", set_default=True)
        self.store.upsert(second_profile)
        self.store.delete(self.profile.profile_id, delete_secret=True)

        self.assertIn("MODEL_DEEPSEEK_TEST_API_KEY=secret-value", self.env_path.read_text(encoding="utf-8"))
        self.assertEqual(["deepseek-backup"], [item.profile_id for item in self.store.list_profiles()])

    def test_model_discovery_returns_sorted_unique_ids(self):
        captured = {}

        class FakeModels:
            @staticmethod
            def list():
                return SimpleNamespace(
                    data=[
                        SimpleNamespace(id="model-z"),
                        SimpleNamespace(id="model-a"),
                        SimpleNamespace(id="model-z"),
                    ]
                )

        def client_factory(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(models=FakeModels())

        models = ModelGateway.discover_models(
            "openai_compatible",
            "https://models.example.com/v1/",
            "secret-value",
            client_factory=client_factory,
        )

        self.assertEqual(["model-a", "model-z"], models)
        self.assertEqual("https://models.example.com/v1", captured["base_url"])
        self.assertEqual("secret-value", captured["api_key"])

    def test_model_discovery_rejects_insecure_remote_url(self):
        with self.assertRaises(ModelConfigurationError):
            ModelGateway.discover_models(
                "openai_compatible",
                "http://models.example.com/v1",
                "secret-value",
                client_factory=lambda **_: None,
            )

    def test_model_discovery_allows_local_http(self):
        client = SimpleNamespace(
            models=SimpleNamespace(
                list=lambda: {"data": [{"id": "local-model"}]}
            )
        )

        models = ModelGateway.discover_models(
            "openai_compatible",
            "http://127.0.0.1:11434/v1",
            "secret-value",
            client_factory=lambda **_: client,
        )

        self.assertEqual(["local-model"], models)

    def test_model_discovery_empty_response_uses_manual_fallback_error(self):
        client = SimpleNamespace(
            models=SimpleNamespace(list=lambda: SimpleNamespace(data=[]))
        )

        with self.assertRaisesRegex(ModelInvocationError, "手工填写"):
            ModelGateway.discover_models(
                "deepseek",
                "https://api.deepseek.com",
                "secret-value",
                client_factory=lambda **_: client,
            )

    def test_model_discovery_masks_key_in_provider_error(self):
        class FailingModels:
            @staticmethod
            def list():
                raise RuntimeError("request failed for secret-value")

        client = SimpleNamespace(models=FailingModels())

        with self.assertRaises(ModelInvocationError) as raised:
            ModelGateway.discover_models(
                "deepseek",
                "https://api.deepseek.com",
                "secret-value",
                client_factory=lambda **_: client,
            )
        self.assertNotIn("secret-value", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
