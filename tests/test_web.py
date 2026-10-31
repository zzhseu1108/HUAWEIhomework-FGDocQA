from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

from docqa.web.server import create_server

from helpers import PNG_1X1, build_workspace


class WebTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temp.name)
        _, cls.parsed, cls.index = build_workspace(cls.base)
        cls.server = create_server(cls.index, host="127.0.0.1", port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp.cleanup()

    def _get(self, path: str):
        with urllib.request.urlopen(self.base_url + path, timeout=5) as response:
            return response.status, response.read()

    def test_static_page(self) -> None:
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("FGDocQA", body.decode("utf-8"))

    def test_stats(self) -> None:
        status, body = self._get("/api/stats")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(len(data["docs"]), 2)
        self.assertGreater(data["stats"]["chunk_count"], 0)

    def test_ask_and_doc(self) -> None:
        payload = json.dumps({"question": "SD5993 单板怎么复位？"}).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/api/ask",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read())
        self.assertTrue(data["sufficient"])
        self.assertTrue(data["citations"])
        doc_id = data["citations"][0]["doc_id"]
        status, body = self._get(f"/api/doc?doc_id={doc_id}")
        self.assertEqual(status, 200)
        document = json.loads(body)
        self.assertIn("reset board slot 7", document["text"])

    def _flow_document(self):
        _, body = self._get("/api/stats")
        docs = json.loads(body)["docs"]
        doc_id = next(doc["doc_id"] for doc in docs if doc["doc_name"] == "流程手册")
        _, body = self._get("/api/doc?" + urlencode({"doc_id": doc_id}))
        return json.loads(body)

    def test_document_images_preview_with_traceability(self) -> None:
        document = self._flow_document()
        self.assertEqual(len(document["images"]), 1)
        image = document["images"][0]
        self.assertEqual(image["page"], 2)
        self.assertEqual(image["figure_index"], 1)
        self.assertTrue(image["title"])
        self.assertIn("figure=1", image["source_uri"])
        with urllib.request.urlopen(self.base_url + image["url"], timeout=5) as response:
            self.assertEqual(response.headers["Content-Type"], "image/png")
            self.assertEqual(response.read(), PNG_1X1)

    def test_asset_access_is_limited_to_registered_document_images(self) -> None:
        document = self._flow_document()
        paths = ["../document.md", "assets/../../document.md", "assets/unregistered.png", "/etc/passwd"]
        for path in paths:
            with self.subTest(path=path):
                url = "/api/asset?" + urlencode({"doc_id": document["doc_id"], "path": path})
                with self.assertRaises(urllib.error.HTTPError) as error:
                    self._get(url)
                self.assertEqual(error.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self._get("/api/asset?doc_id=missing&path=assets/image.png")
        self.assertEqual(error.exception.code, 404)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self._get("/api/doc?doc_id=missing")
        self.assertEqual(error.exception.code, 404)

    def test_asset_rejects_unsafe_indexed_image_symlinks(self) -> None:
        document = self._flow_document()
        image = document["images"][0]
        index_data = json.loads((self.index / "index.json").read_text())
        registered = next(doc for doc in index_data["docs"] if doc["doc_id"] == document["doc_id"])
        path = Path(registered["abs_path"]).parent / image["path"]
        original = path.read_bytes()
        outside = self.base / "outside.png"
        outside.write_bytes(b"private content")
        inside_text = path.parent / "private.txt"
        inside_text.write_text("private content")
        path.unlink()
        try:
            for destination in (outside, inside_text):
                with self.subTest(destination=destination.name):
                    path.symlink_to(destination)
                    try:
                        with self.assertRaises(urllib.error.HTTPError) as error:
                            self._get(image["url"])
                        self.assertEqual(error.exception.code, 403)
                    finally:
                        path.unlink()
        finally:
            path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
