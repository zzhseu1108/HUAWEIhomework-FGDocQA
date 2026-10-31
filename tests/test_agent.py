from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from docqa.agent import answer_question, classify_intent

from helpers import build_workspace


class AgentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temp.name)
        _, cls.parsed, cls.index = build_workspace(cls.base)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def test_flow_answer_with_mermaid_and_citation(self) -> None:
        answer = answer_question("分机注册后，呼叫是怎么转发的？", self.index)
        self.assertTrue(answer.sufficient)
        self.assertIn("关键依据", answer.answer)
        self.assertTrue(answer.citations)
        self.assertIn("Page 2", answer.citations[0].label())
        self.assertTrue(answer.mermaid.startswith("flowchart"))
        self.assertIn("[1]", answer.answer)
        self.assertEqual(answer.format, "markdown")

    def test_command_answer(self) -> None:
        answer = answer_question("SD5993 单板怎么复位？", self.index)
        self.assertTrue(answer.sufficient)
        self.assertIn("reset board slot 7", answer.answer)
        self.assertEqual(answer.citations[0].doc_name, "命令手册")

    def test_no_evidence_refuses(self) -> None:
        answer = answer_question("如何申请年假？", self.index)
        self.assertFalse(answer.sufficient)
        self.assertEqual(answer.citations, [])
        self.assertIn("无据不答", answer.answer)

    def test_contract_fields(self) -> None:
        answer = answer_question("ACME_Start 是什么？", self.index)
        payload = answer.to_dict()
        for key in ["answer", "format", "citations", "rounds_used"]:
            self.assertIn(key, payload)
        self.assertIsInstance(payload["citations"], list)
        if payload["citations"]:
            citation = payload["citations"][0]
            for key in ["ref", "doc_id", "doc_name", "page", "source_uri"]:
                self.assertIn(key, citation)

    def test_intent_classification(self) -> None:
        self.assertEqual(classify_intent("复位命令是什么？"), "command")
        self.assertEqual(classify_intent("呼叫转发流程怎么走？"), "flow")
        self.assertEqual(classify_intent("v2.3.0 有什么新特性？"), "version")
        self.assertEqual(classify_intent("分机注册失败怎么排查？"), "troubleshoot")


if __name__ == "__main__":
    unittest.main()
