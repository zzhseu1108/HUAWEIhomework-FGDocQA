from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("fgdocqa_submission_launcher", Path(__file__).with_name("launcher.py"))
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)
launcher.configure_source_imports()


class SubmissionLauncherTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fgdocqa-launcher-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {}, clear=False)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        launcher.force_mock_mode()

    def prepare(self, path):
        with contextlib.redirect_stdout(io.StringIO()):
            return launcher.prepare_demo(path)

    def test_mock_binary_does_not_inherit_api_credentials_or_modes(self):
        os.environ.update(DOCQA_API_KEY="never-send-this", DOCQA_MODEL_MODE="http", DOCQA_CHAT_MODE="http", DOCQA_EMBED_DIM="2048")
        launcher.force_mock_mode()
        self.assertNotIn("DOCQA_API_KEY", os.environ)
        self.assertEqual("512", os.environ["DOCQA_EMBED_DIM"])
        self.assertTrue(all(os.environ[f"DOCQA_{name}_MODE"] == "mock" for name in ("EMBED", "RERANK", "CHAT", "VL")))

    def test_workspaces_use_writable_per_user_platform_directories(self):
        home = self.root / "Student With Spaces"
        windows_local = self.root / "Local App Data"
        self.assertEqual(windows_local / "FGDocQA" / "demo-v1", launcher.default_workspace("win32", {"LOCALAPPDATA": str(windows_local)}, home))
        self.assertEqual(home / "AppData" / "Local" / "FGDocQA" / "demo-v1", launcher.default_workspace("win32", {}, home))
        self.assertEqual(home / "Library" / "Application Support" / "FGDocQA" / "demo-v1", launcher.default_workspace("darwin", {}, home))

    def test_relocated_workspace_rebuilds_absolute_document_paths(self):
        from docqa.docbase import load_index

        original = self.root / "original"
        moved = self.root / "relocated with spaces"
        self.prepare(original)
        shutil.copytree(original, moved)
        index_dir = self.prepare(moved)
        index = load_index(index_dir)
        self.assertEqual(5, len(index.docs))
        self.assertTrue(all(Path(doc["abs_path"]).is_relative_to(moved.resolve()) for doc in index.docs))
        marker = json.loads((moved / ".demo-workspace.json").read_text())
        self.assertEqual(str(moved.resolve()), marker["workspace"])

    def test_existing_port_is_not_taken_over(self):
        index_dir = self.prepare(self.root / "demo")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen(1)
            requested = occupied.getsockname()[1]
            server = launcher.create_demo_server(index_dir, requested)
            try:
                self.assertEqual("127.0.0.1", server.server_address[0])
                self.assertNotEqual(requested, server.server_address[1])
            finally:
                server.server_close()

    def test_missing_index_component_triggers_rebuild_instead_of_cached_startup(self):
        from docqa.docbase import load_index

        workspace = self.root / "demo"
        index_dir = self.prepare(workspace)
        for filename in ("vectors.json", "sparse.json", "index.json"):
            with self.subTest(filename=filename):
                (index_dir / filename).unlink()
                rebuilt = self.prepare(workspace)
                self.assertEqual(index_dir, rebuilt)
                self.assertTrue(all((rebuilt / name).is_file() for name in ("index.json", "vectors.json", "sparse.json")))
                self.assertEqual(5, len(load_index(rebuilt).docs))

    def test_smoke_uses_packaged_style_resources_and_closes_http_server(self):
        index_dir = self.prepare(self.root / "demo")
        result = launcher.smoke_test(index_dir)
        self.assertTrue(result["ok"])
        self.assertTrue(result["whale_artwork"])
        self.assertTrue(result["document_preview"])
        self.assertEqual("mock", result["mode"])
        self.assertGreater(result["citations"], 0)


if __name__ == "__main__":
    unittest.main()
