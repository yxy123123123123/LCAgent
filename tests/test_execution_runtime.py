from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "LCA"))

from execution_runtime import ExecutionRuntime, ExecutionRuntimeError  # noqa: E402


class ExecutionRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.runtime = ExecutionRuntime(scripts_dir=Path(self.temp_dir.name))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_script_is_validated_written_and_executed_without_shell(self):
        self.runtime.write_generated_script(
            "generated_echo.py",
            "import json\nprint(json.dumps({'status': 'ok'}))\n",
        )
        result = self.runtime.execute_generated_script("generated_echo.py", dry_run=True)
        self.assertEqual("success", result["status"])
        self.assertEqual("ok", json.loads(result["stdout"])["status"])
        self.assertIn("--dry-run", result["command"])

    def test_filename_cannot_escape_generated_script_directory(self):
        with self.assertRaises(ExecutionRuntimeError):
            self.runtime.write_generated_script("../generated_bad.py", "print('x')")


if __name__ == "__main__":
    unittest.main()

