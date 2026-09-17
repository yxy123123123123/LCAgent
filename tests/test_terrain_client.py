from __future__ import annotations

import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "LCA"))

from terrain_client import (  # noqa: E402
    TerrainCommandError,
    TerrainTransportError,
    TerrainValidationError,
    build_terrain_command,
    create_terrain,
)


class OneShotMCPServer:
    def __init__(self, response: dict, *, chunks: int = 1, delay: float = 0.0):
        self.response = response
        self.chunks = chunks
        self.delay = delay
        self.received: dict | None = None
        self.error: Exception | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        self._thread.start()
        if not self._ready.wait(timeout=2):
            raise RuntimeError("Fake MCP server failed to start")
        return self

    def __exit__(self, exc_type, exc, traceback):
        self._thread.join(timeout=2)
        if exc_type is None and self.error:
            raise self.error

    def _run(self):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
                server.bind(("127.0.0.1", 0))
                server.listen(1)
                self.port = server.getsockname()[1]
                self._ready.set()
                connection, _ = server.accept()
                with connection:
                    connection.settimeout(2)
                    payload = bytearray()
                    while True:
                        payload.extend(connection.recv(4096))
                        try:
                            self.received = json.loads(payload.decode("utf-8").strip())
                            break
                        except json.JSONDecodeError:
                            continue

                    if self.delay:
                        time.sleep(self.delay)
                    encoded = json.dumps(self.response).encode("utf-8")
                    chunk_size = max(1, len(encoded) // self.chunks)
                    for offset in range(0, len(encoded), chunk_size):
                        try:
                            connection.sendall(encoded[offset : offset + chunk_size])
                        except OSError:
                            break
        except Exception as exc:  # pragma: no cover - surfaced by __exit__
            self.error = exc
            self._ready.set()


class TerrainClientTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".tif", delete=False)
        handle.close()
        self.tif_path = Path(handle.name)

    def tearDown(self):
        self.tif_path.unlink(missing_ok=True)

    def build(self, **overrides):
        return build_terrain_command(
            overrides.pop("file_paths", [str(self.tif_path)]),
            overrides.pop("material_path", "/Game/Materials/M_Terrain.M_Terrain"),
            request_id=overrides.pop("request_id", "terrain-test-001"),
            **overrides,
        )

    def test_command_matches_json_schema(self):
        schema = json.loads(
            (REPO_ROOT / "contracts" / "unreal_mcp" / "terrain_create.schema.json").read_text(
                encoding="utf-8"
            )
        )
        errors = list(Draft202012Validator(schema).iter_errors(self.build()))
        self.assertEqual([], errors)

    def test_command_uses_atomic_create_and_path_array(self):
        command = self.build()
        self.assertEqual("Landscaping", command["type"])
        self.assertEqual("Create", command["params"]["command_type"])
        self.assertEqual([str(self.tif_path.resolve())], command["params"]["file_paths"])

    def test_missing_file_is_rejected_before_socket_connection(self):
        with self.assertRaises(TerrainValidationError):
            self.build(file_paths=[str(self.tif_path.with_name("missing.tif"))])

    def test_invalid_material_path_is_rejected(self):
        with self.assertRaises(TerrainValidationError):
            self.build(material_path="Content/Materials/M_Terrain")

    def test_chunked_success_response_is_parsed(self):
        response = {"status": "success", "message": "Terrain created", "data": {"file_count": 1}}
        with OneShotMCPServer(response, chunks=7) as server:
            result = create_terrain(
                [str(self.tif_path)],
                "/Game/Materials/M_Terrain.M_Terrain",
                request_id="terrain-test-002",
                port=server.port,
                response_timeout=1,
            )
        self.assertEqual(response, result)
        self.assertEqual("Create", server.received["params"]["command_type"])

    def test_error_response_is_not_reported_as_success(self):
        response = {
            "status": "error",
            "code": "MATERIAL_LOAD_FAILED",
            "stage": "material",
            "message": "Material could not be loaded",
        }
        with OneShotMCPServer(response) as server:
            with self.assertRaises(TerrainCommandError) as context:
                create_terrain(
                    [str(self.tif_path)],
                    "/Game/Materials/Missing.Missing",
                    port=server.port,
                    response_timeout=1,
                )
        self.assertEqual("material", context.exception.response["stage"])

    def test_mismatched_response_request_id_is_rejected(self):
        response = {
            "status": "success",
            "request_id": "another-request",
            "protocol_version": "1.0.0",
        }
        with OneShotMCPServer(response) as server:
            with self.assertRaises(TerrainTransportError):
                create_terrain(
                    [str(self.tif_path)],
                    "/Game/Materials/M_Terrain.M_Terrain",
                    request_id="terrain-test-003",
                    port=server.port,
                    response_timeout=1,
                )

    def test_response_timeout_is_reported(self):
        with OneShotMCPServer({"status": "success"}, delay=0.2) as server:
            with self.assertRaises(TerrainTransportError):
                create_terrain(
                    [str(self.tif_path)],
                    "/Game/Materials/M_Terrain.M_Terrain",
                    port=server.port,
                    response_timeout=0.05,
                )


if __name__ == "__main__":
    unittest.main()
