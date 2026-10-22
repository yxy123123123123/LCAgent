from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from jsonschema import Draft202012Validator


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "LCA"))

from run_models import RunStatus, TaskRun  # noqa: E402
from run_store import RunStore  # noqa: E402


class RunStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.store = RunStore(root / "lca.db", artifacts_root=root / "runs")
        self.run = TaskRun(
            run_id="run_20261022_storetest",
            workspace_id="workspace-test",
            task_text="测试运行持久化",
            model_profile_id="model-test",
            embedding_profile_id=None,
            execution_mode="dry_run",
        )
        self.store.create_run(self.run)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_run_survives_store_reopen(self):
        self.store.update_run(
            self.run.run_id,
            status=RunStatus.SUCCEEDED.value,
            final_answer="完成",
        )

        reopened = RunStore(self.store.db_path, artifacts_root=self.store.artifacts_root)
        loaded = reopened.get_run(self.run.run_id)

        self.assertEqual(RunStatus.SUCCEEDED.value, loaded.status)
        self.assertEqual("完成", loaded.final_answer)

    def test_event_sequences_are_strictly_increasing(self):
        first = self.store.append_event(
            self.run.run_id,
            event_type="first",
            phase="ANALYZE",
            status="running",
        )
        second = self.store.append_event(
            self.run.run_id,
            event_type="second",
            phase="FINISH",
            status="success",
        )

        self.assertEqual(1, first.sequence)
        self.assertEqual(2, second.sequence)
        self.assertEqual(
            [2],
            [item.sequence for item in self.store.list_events(self.run.run_id, after_sequence=1)],
        )

    def test_run_and_event_match_published_contracts(self):
        event = self.store.append_event(
            self.run.run_id,
            event_type="contract_test",
            phase="ANALYZE",
            status="success",
        )
        run_schema = json.loads(
            (REPO_ROOT / "contracts" / "run" / "task_run.schema.json").read_text(
                encoding="utf-8"
            )
        )
        event_schema = json.loads(
            (REPO_ROOT / "contracts" / "run" / "run_event.schema.json").read_text(
                encoding="utf-8"
            )
        )

        Draft202012Validator(run_schema).validate(self.store.get_run(self.run.run_id).as_dict())
        Draft202012Validator(event_schema).validate(event.as_dict())

    def test_cancel_request_is_persisted(self):
        updated = self.store.request_cancel(self.run.run_id)

        self.assertTrue(updated.cancel_requested)
        self.assertEqual(RunStatus.CANCELLING.value, updated.status)
        self.assertEqual("cancel_requested", self.store.list_events(self.run.run_id)[0].event_type)

    def test_json_artifact_is_hashed_indexed_and_redacted(self):
        record = self.store.write_json_artifact(
            self.run.run_id,
            "result.json",
            {
                "answer": "ok",
                "api_key": "real-secret-value",
                "nested": {"authorization": "Bearer private"},
            },
        )

        loaded = self.store.read_json_artifact(self.run.run_id, "result.json")
        artifacts = self.store.list_artifacts(self.run.run_id)

        self.assertEqual("***REDACTED***", loaded["api_key"])
        self.assertEqual("***REDACTED***", loaded["nested"]["authorization"])
        self.assertEqual(64, len(record["sha256"]))
        self.assertEqual("result.json", artifacts[0]["artifact_id"])

    def test_export_contains_run_events_artifacts_and_hash_manifest(self):
        self.store.append_event(
            self.run.run_id,
            event_type="export_test",
            phase="FINISH",
            status="success",
        )
        self.store.write_json_artifact(
            self.run.run_id,
            "result.json",
            {"answer": "ok", "api_key": "must-not-leak"},
        )

        exported = self.store.export_run(self.run.run_id)

        self.assertEqual(64, len(exported["sha256"]))
        archive_path = Path(exported["path"])
        self.assertTrue(archive_path.is_file())
        with zipfile.ZipFile(archive_path) as archive:
            self.assertEqual(
                {
                    "artifacts/result.json",
                    "events.jsonl",
                    "manifest.json",
                    "run.json",
                },
                set(archive.namelist()),
            )
            manifest = json.loads(archive.read("manifest.json"))
            artifact = json.loads(archive.read("artifacts/result.json"))
        self.assertTrue(manifest["redacted"])
        self.assertEqual("***REDACTED***", artifact["api_key"])


if __name__ == "__main__":
    unittest.main()
