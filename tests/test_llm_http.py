from __future__ import annotations

import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from docqa.llm import ModelConfig, ModelHub


class ModelHTTPTest(unittest.TestCase):
    def hub(self, **kwargs):
        return ModelHub(ModelConfig(mode="http", base_url="https://models.example/v1", api_key="private-test-token", **kwargs))

    def test_embedding_uses_response_indices(self):
        hub = self.hub()
        with patch.object(hub, "_post", return_value={"data": [
            {"index": 1, "embedding": [0.0, 1.0]},
            {"index": 0, "embedding": [1.0, 0.0]},
        ]}):
            self.assertEqual(hub.embed(["first", "second"]), [[1.0, 0.0], [0.0, 1.0]])
        self.assertEqual(hub.status()["capabilities"]["embed"]["state"], "verified")

    def test_invalid_embedding_never_becomes_mock_vector(self):
        invalid = [
            [{"index": 0, "embedding": [1.0]}, {"index": 0, "embedding": [2.0]}],
            [{"index": 0, "embedding": [1.0]}, {"index": 1, "embedding": [1.0, 2.0]}],
            [{"index": 0, "embedding": [float("nan")]}, {"index": 1, "embedding": [1.0]}],
        ]
        for rows in invalid:
            with self.subTest(rows=rows):
                hub = self.hub()
                with patch.object(hub, "_post", return_value={"data": rows}):
                    with self.assertRaises(RuntimeError):
                        hub.embed(["one", "two"])
                self.assertTrue(hub.is_mock_for("embed"))
                with self.assertRaises(RuntimeError):
                    hub.embed(["retry"])

    def test_rerank_requests_all_candidates_and_restores_order(self):
        hub = self.hub()
        documents = ["a", "b", "c", "d"]
        with patch.object(hub, "_post", return_value={"results": [
            {"index": 3, "relevance_score": 0.8},
            {"index": 1, "relevance_score": 0.6},
            {"index": 0, "relevance_score": 0.2},
            {"index": 2, "relevance_score": 0.0},
        ]}) as post:
            self.assertEqual(hub.rerank("question", documents), [0.2, 0.6, 0.0, 0.8])
        self.assertEqual(post.call_args.args[1]["top_n"], len(documents))

    def test_rerank_rejects_incomplete_response(self):
        hub = self.hub()
        with patch.object(hub, "_post", return_value={"results": [{"index": 0, "relevance_score": 0.8}]}):
            with self.assertRaisesRegex(RuntimeError, "全部候选"):
                hub.rerank("question", ["a", "b"])

    def test_chat_failure_does_not_disable_embeddings(self):
        hub = self.hub()
        with patch.object(hub, "_post", side_effect=[RuntimeError("chat down"), {"data": [{"index": 0, "embedding": [0.1, 0.2]}]}]):
            self.assertIsNone(hub.chat([{"role": "user", "content": "hello"}]))
            self.assertEqual(hub.embed(["text"]), [[0.1, 0.2]])
        self.assertTrue(hub.is_mock_for("chat"))
        self.assertFalse(hub.is_mock_for("embed"))
        self.assertEqual(hub.mode, "mixed")

    def test_empty_chat_is_a_failed_api_call(self):
        hub = self.hub()
        with patch.object(hub, "_post", return_value={"choices": [{"message": {"content": ""}}]}):
            self.assertIsNone(hub.chat([{"role": "user", "content": "hello"}]))
        self.assertEqual(hub.status()["capabilities"]["chat"]["state"], "degraded")

    def test_chat_uses_separate_key_and_endpoint(self):
        hub = self.hub(chat_base_url="https://chat.example/v1", chat_api_key="private-chat-token")
        result = io.BytesIO(json.dumps({"choices": [{"message": {"content": "ready"}}]}).encode())
        with patch("urllib.request.urlopen", return_value=result) as request:
            self.assertEqual(hub.chat([{"role": "user", "content": "hello"}]), "ready")
        sent = request.call_args.args[0]
        self.assertEqual(sent.full_url, "https://chat.example/v1/chat/completions")
        self.assertEqual(sent.get_header("Authorization"), "Bearer private-chat-token")

    def test_http_errors_and_repr_redact_both_keys(self):
        hub = self.hub(chat_api_key="private-chat-token")
        body = io.BytesIO(b"private-test-token private-chat-token")
        error = urllib.error.HTTPError("https://models.example/v1/embeddings", 401, "Unauthorized", {}, body)
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(RuntimeError) as caught:
                hub.embed(["test"])
        for private in ("private-test-token", "private-chat-token"):
            self.assertNotIn(private, str(caught.exception))
            self.assertNotIn(private, str(hub.warnings))
            self.assertNotIn(private, repr(hub.config))

    def test_per_capability_mode_and_sanitized_status(self):
        hub = self.hub(chat_mode="mock", vl_mode="mock", chat_base_url="https://user:password@chat.example/v1?secret=hidden")
        self.assertEqual(hub.mode, "mixed")
        self.assertIsNone(hub.chat([]))
        self.assertEqual(hub.status()["capabilities"]["chat"]["base_url"], "https://chat.example/v1")
        self.assertNotIn("password", str(hub.status()))


if __name__ == "__main__":
    unittest.main()
