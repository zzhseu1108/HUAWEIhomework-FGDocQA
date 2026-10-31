from __future__ import annotations

import argparse
import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from docqa.cli import cmd_probe


def config_for(**modes):
    config = SimpleNamespace(
        mode="http", base_url="https://models.example/v1", api_key="test-secret",
        chat_api_key="", chat_base_url="",
        embed_model="embedding-test", rerank_model="rerank-test",
        chat_model="chat-test", vl_model="vision-test",
    )
    config.mode_for = lambda capability: modes.get(capability, "mock")
    return config


class ModelProbeTest(unittest.TestCase):
    def run_probe(self, config, hub_factory):
        output = io.StringIO()
        with patch("docqa.cli.ModelConfig.from_env", return_value=config), patch("docqa.cli.ModelHub", side_effect=hub_factory), redirect_stdout(output):
            result = cmd_probe(argparse.Namespace(image="missing-test-image.png"))
        return result, output.getvalue()

    def test_probe_skips_mock_capabilities(self):
        calls = []
        config = config_for(embed="http", rerank="http")

        class Hub:
            warnings = []

            def __init__(self, unused):
                pass

            def is_mock_for(self, capability):
                return config.mode_for(capability) == "mock"

            def embed_one(self, text):
                calls.append("embed")
                return [0.1, 0.2]

            def rerank(self, query, documents):
                calls.append("rerank")
                return [0.9, 0.1, 0.8]

            def chat(self, *args, **kwargs):
                raise AssertionError("Mock Chat must not be called")

            def describe_image(self, *args, **kwargs):
                raise AssertionError("Mock VL must not be called")

        result, output = self.run_probe(config, Hub)
        self.assertEqual(result, 0)
        self.assertEqual(calls, ["embed", "rerank"])
        self.assertIn("HTTP 已验证 2 项", output)
        self.assertIn("Mock 跳过 2 项", output)

    def test_probe_rejects_empty_http_chat(self):
        config = config_for(chat="http")
        hub = SimpleNamespace(warnings=[], chat=lambda *args, **kwargs: None, is_mock_for=lambda capability: False)
        result, output = self.run_probe(config, lambda unused: hub)
        self.assertEqual(result, 1)
        self.assertIn("模型返回空内容", output)
        self.assertIn("失败 1 项", output)
        self.assertNotIn("HTTP 已验证 · 返回", output)

    def test_probe_redacts_credentials_from_failures(self):
        config = config_for(embed="http")
        config.base_url = "https://user:private-password@models.example/v1?token=private-token"

        def failed_embed(text):
            raise RuntimeError(f"request failed: {config.api_key} at {config.base_url}")

        hub = SimpleNamespace(
            warnings=[f"upstream error: {config.api_key} {config.base_url}"],
            embed_one=failed_embed, is_mock_for=lambda capability: False,
        )
        result, output = self.run_probe(config, lambda unused: hub)
        self.assertEqual(result, 1)
        self.assertIn("[REDACTED]", output)
        for secret in (config.api_key, "private-password", "private-token"):
            self.assertNotIn(secret, output)

    def test_probe_redacts_separate_chat_credentials(self):
        config = config_for(chat="http")
        config.chat_api_key = "chat-only-private-key"
        config.chat_base_url = "https://chat-user:chat-password@chat.example/v1?token=chat-query-token"

        def failed_chat(*args, **kwargs):
            raise RuntimeError(f"chat request failed: {config.chat_api_key} at {config.chat_base_url}")

        hub = SimpleNamespace(
            warnings=[f"keys: {config.api_key}, {config.chat_api_key}"],
            chat=failed_chat, is_mock_for=lambda capability: False,
        )
        result, output = self.run_probe(config, lambda unused: hub)
        self.assertEqual(result, 1)
        self.assertIn("[REDACTED]", output)
        for secret in (config.api_key, config.chat_api_key, "chat-password", "chat-query-token"):
            self.assertNotIn(secret, output)


if __name__ == "__main__":
    unittest.main()
