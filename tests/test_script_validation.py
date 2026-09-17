from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "LCA"))

from script_validation import GeneratedScriptValidationError, validate_generated_python  # noqa: E402


class GeneratedScriptValidationTests(unittest.TestCase):
    def test_valid_terrain_wrapper_is_accepted(self):
        validate_generated_python(
            "generated_terrain_task.py",
            "from create_terrain import main\nraise SystemExit(main())\n",
        )

    def test_syntax_error_is_rejected(self):
        with self.assertRaises(GeneratedScriptValidationError):
            validate_generated_python("generated_terrain_task.py", "if True print('x')")

    def test_direct_socket_implementation_is_rejected(self):
        with self.assertRaises(GeneratedScriptValidationError):
            validate_generated_python(
                "generated_terrain_task.py",
                "import socket\nfrom create_terrain import main\n",
            )

    def test_terrain_script_must_use_fixed_client(self):
        with self.assertRaises(GeneratedScriptValidationError):
            validate_generated_python("generated_terrain_task.py", "print('terrain')\n")


if __name__ == "__main__":
    unittest.main()
