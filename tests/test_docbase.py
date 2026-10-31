from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docqa.docbase import Retriever, build_index, evaluate, load_index
from docqa.llm import ModelConfig, ModelHub, mock_embedding

from helpers import build_workspace


class DocBaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temp.name)
        _, cls.parsed, cls.index = build_workspace(cls.base)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def test_index_has_two_docs(self) -> None:
        index = load_index(self.index)
        self.assertEqual(len(index.docs), 2)
        self.assertGreaterEqual(len(index.chunks), 4)
        names = {doc["doc_name"] for doc in index.docs}
        self.assertEqual(names, {"流程手册", "命令手册"})

    def test_three_way_retrieval_and_rerank(self) -> None:
        retriever = Retriever(self.index)
        result = retriever.search("呼叫是怎么转发的？", top_k=4)
        self.assertTrue(result.hits)
        self.assertEqual(result.hits[0].chunk.doc_name, "流程手册")
        self.assertEqual(result.hits[0].chunk.page, 2)
        self.assertTrue(result.hits[0].routes)
        self.assertIn("dense", result.route_stats)
        self.assertIn("bm25", result.route_stats)
        self.assertIn("sparse", result.route_stats)

    def test_exact_term_beats_semantic_noise(self) -> None:
        retriever = Retriever(self.index)
        result = retriever.search("reset board slot 7 是什么命令？", top_k=4)
        self.assertEqual(result.hits[0].chunk.doc_name, "命令手册")
        self.assertIn("reset board slot 7", result.hits[0].chunk.text)
        self.assertEqual(result.hits[0].chunk.page, 2)

    def test_category_filter(self) -> None:
        retriever = Retriever(self.index)
        result = retriever.search("SD5993", top_k=4, category="流程")
        self.assertTrue(all(hit.chunk.category == "流程" for hit in result.hits))

    def test_evaluate(self) -> None:
        cases = [
            {"question": "分机注册后怎么转发？", "expected_docs": ["流程手册"], "expected_keywords": ["号码分析"]},
            {"question": "SD5993 单板复位命令", "expected_docs": ["命令手册"], "expected_keywords": ["reset board slot 7"]},
        ]
        report = evaluate(self.index, cases, top_k=4)
        self.assertEqual(report["summary"]["hit_rate"], 1.0)
        self.assertEqual(report["summary"]["mrr"], 1.0)
        self.assertEqual(report["summary"]["cases"], 2)

    def test_incremental_build(self) -> None:
        report = build_index(self.parsed, self.index, incremental=True, chunk_size=1200)
        self.assertEqual(report["unchanged_docs"], 2)
        self.assertEqual(report["updated_docs"], 0)
        parsed_doc = self.parsed / "02_命令手册" / "document.md"
        text = parsed_doc.read_text(encoding="utf-8")
        parsed_doc.write_text(
            text + "\n\n## 3. 热升级\n\n支持 SD5993 在线热升级，业务运行期间完成固件升级，不影响正在进行的通话。\n",
            encoding="utf-8",
        )
        report = build_index(self.parsed, self.index, incremental=True, chunk_size=1200)
        self.assertEqual(report["updated_docs"], 1)
        result = Retriever(self.index).search("在线热升级", top_k=4)
        self.assertEqual(result.hits[0].chunk.doc_name, "命令手册")
        self.assertIn("热升级", result.hits[0].chunk.text)

    def test_dense_rejects_different_model_even_with_same_dimension(self) -> None:
        hub = ModelHub(ModelConfig(
            mode="http", base_url="https://models.example.test/v1",
            embed_model="other-embedding-512", rerank_mode="mock",
            chat_mode="mock", vl_mode="mock",
        ))
        with patch.object(hub, "embed_one", return_value=mock_embedding("SD5993", 512)) as embed:
            result = Retriever(self.index, hub=hub).search("SD5993 单板复位命令", top_k=4)
        embed.assert_not_called()
        self.assertTrue(result.hits)
        self.assertTrue(result.route_stats["dense"]["degraded"])
        self.assertTrue(any("全量重建索引" in warning for warning in result.warnings))
        self.assertTrue(all("dense" not in hit.routes for hit in result.hits))

    def test_http_build_failure_preserves_existing_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "index"
            build_index(self.parsed, target)
            before = {name: (target / name).read_bytes() for name in ("index.json", "vectors.json", "sparse.json")}
            hub = ModelHub(ModelConfig(
                mode="http", base_url="https://models.example.test/v1",
                embed_model="Qwen3-Embedding-8B", chat_mode="mock", vl_mode="mock",
            ))
            with patch.object(hub, "embed", side_effect=RuntimeError("embedding service unavailable")):
                with self.assertRaisesRegex(RuntimeError, "embedding service unavailable"):
                    build_index(self.parsed, target, hub=hub)
            after = {name: (target / name).read_bytes() for name in before}
            self.assertEqual(before, after)

    def test_http_embedding_metadata_with_mock_chat(self) -> None:
        hub = ModelHub(ModelConfig(
            mode="http", base_url="https://models.example.test/v1",
            embed_model="Qwen3-Embedding-8B", rerank_model="Qwen3-Reranker-8B",
            chat_mode="mock", vl_mode="mock",
        ))
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "index"
            with patch.object(hub, "embed", side_effect=lambda texts: [mock_embedding(text, 32) for text in texts]):
                report = build_index(self.parsed, target, hub=hub)
            index = load_index(target)
            sparse = json.loads((target / "sparse.json").read_text())
            self.assertEqual(report["mode"], "http")
            self.assertEqual(index.embedding_model, "Qwen3-Embedding-8B")
            self.assertEqual(index.rerank_model, "Qwen3-Reranker-8B")
            self.assertEqual(index.stats["embedding_dim"], 32)
            self.assertEqual(sparse["model"], "tfidf-local")
            self.assertEqual(sparse["kind"], "lexical")


if __name__ == "__main__":
    unittest.main()
