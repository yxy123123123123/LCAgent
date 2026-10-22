from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "LCA"))

from Agent import AgentCancelledError, TraceStep  # noqa: E402
from run_manager import RunManager, RunManagerError  # noqa: E402
from run_models import RunStatus, TerminationReason  # noqa: E402
from run_store import RunStore  # noqa: E402


class FakeResult:
    final_answer = "离线运行完成"
    model_steps = 1
    dry_run = True

    def as_dict(self):
        return {
            "final_answer": self.final_answer,
            "trace": [],
            "model_steps": self.model_steps,
            "dry_run": self.dry_run,
        }


class SuccessfulAgent:
    def __init__(self, **kwargs):
        self.trace_callback = kwargs["trace_callback"]

    def run_with_trace(self, task_text):
        self.trace_callback(TraceStep(1, "ANALYZE", None, "running", {"task": task_text}))
        self.trace_callback(TraceStep(2, "FINISH", "finish", "success", {}))
        return FakeResult()


class RunManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.store = RunStore(self.root / "lca.db", artifacts_root=self.root / "runs")

    def tearDown(self):
        if hasattr(self, "manager"):
            self.manager.shutdown()
        self.temp_dir.cleanup()

    def test_background_run_persists_result_events_and_artifact(self):
        self.manager = RunManager(self.store, agent_factory=SuccessfulAgent)
        run = self.manager.submit(
            workspace_id="workspace-test",
            workspace_path=str(self.workspace),
            task_text="测试",
            dry_run=True,
        )

        result = self.manager.wait(run.run_id, timeout=5)
        saved = self.store.get_run(run.run_id)
        events = self.store.list_events(run.run_id)

        self.assertEqual("离线运行完成", result["final_answer"])
        self.assertEqual(RunStatus.SUCCEEDED.value, saved.status)
        self.assertEqual(TerminationReason.DRY_RUN_COMPLETED.value, saved.termination_reason)
        self.assertEqual(list(range(1, len(events) + 1)), [item.sequence for item in events])
        self.assertEqual("agent_result.json", self.store.list_artifacts(run.run_id)[0]["artifact_id"])

    def test_cancelled_run_stops_before_new_agent_action(self):
        started = threading.Event()

        class CancellableAgent:
            def __init__(inner_self, **kwargs):
                inner_self.should_cancel = kwargs["should_cancel"]

            def run_with_trace(inner_self, _task_text):
                started.set()
                while not inner_self.should_cancel():
                    time.sleep(0.005)
                raise AgentCancelledError("cancelled")

        self.manager = RunManager(self.store, agent_factory=CancellableAgent)
        run = self.manager.submit(
            workspace_id="workspace-test",
            workspace_path=str(self.workspace),
            task_text="等待取消",
        )
        self.assertTrue(started.wait(2))

        self.manager.cancel(run.run_id)
        with self.assertRaisesRegex(RunManagerError, "已取消"):
            self.manager.wait(run.run_id, timeout=5)

        saved = self.store.get_run(run.run_id)
        self.assertEqual(RunStatus.CANCELLED.value, saved.status)
        self.assertEqual(TerminationReason.CANCELLED.value, saved.termination_reason)


if __name__ == "__main__":
    unittest.main()
