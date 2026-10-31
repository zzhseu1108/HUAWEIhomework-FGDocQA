from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from docqa.doc2md import detect_doc_type, parse_document
from docqa.utils import read_json, read_text

from helpers import PNG_1X1


class Doc2mdTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "assets").mkdir()
        (self.root / "assets" / "flow.png").write_bytes(PNG_1X1)
        self.source = self.root / "说明文档.md"
        self.source.write_text(
            """---
doc_name: 说明文档
category: 测试分类
pages: 2
---
<!-- page: 1 -->
# 说明文档

ACME_Start 是启动参数。

![呼叫流程图：注册、鉴权、转发三个步骤以箭头串联](assets/flow.png)

图 1 呼叫流程

<!-- page: 2 -->
## 复位

使用 `reset board 3` 复位单板。
""",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_parse_markdown_keeps_traceability(self) -> None:
        out = self.root / "parsed"
        result = parse_document(self.source, out, category="测试分类")
        self.assertEqual(result.doc_name, "说明文档")
        self.assertEqual(result.pages, 2)
        self.assertEqual(len(result.images), 1)
        image = result.images[0]
        self.assertEqual(image.page, 1)
        self.assertEqual(image.figure_index, 1)
        self.assertEqual(image.title, "呼叫流程")
        self.assertTrue(image.path.startswith("assets/说明文档_Page1_Fig1_"))
        self.assertTrue(image.source_uri.startswith("file://"))
        self.assertIn("page=1", image.source_uri)
        self.assertIn("figure=1", image.source_uri)
        self.assertIn("注册", image.description)
        self.assertTrue((out / "document.md").exists())
        self.assertTrue((out / image.path).exists())
        manifest = read_json(out / "manifest.json", default={})
        self.assertEqual(manifest["doc_id"], result.doc_id)
        self.assertEqual(manifest["task"]["src"], str(self.source.resolve()))
        generated = read_text(out / "document.md")
        self.assertIn("doc_name: 说明文档", generated)
        self.assertIn("图 1-1 · 呼叫流程", generated)
        self.assertIn("<!-- page: 2 -->", generated)

    def test_reparse_is_stable(self) -> None:
        out = self.root / "parsed"
        first = parse_document(self.source, out)
        second = parse_document(self.source, out)
        self.assertEqual(first.doc_id, second.doc_id)
        self.assertEqual(len(list((out / "assets").glob("*.png"))), 1)

    def test_parse_plain_text(self) -> None:
        source = self.root / "note.txt"
        source.write_text("第一段\n\n第二段", encoding="utf-8")
        result = parse_document(source, self.root / "txt")
        self.assertEqual(result.pages, 1)
        self.assertIn("第一段", read_text(result.markdown_path))

    def test_parse_minimal_pptx(self) -> None:
        pptx = self.root / "demo.pptx"
        slide_xml = """<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
       xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld><p:spTree><p:sp><p:txBody>
    <a:p><a:r><a:t>单板复位</a:t></a:r></a:p>
    <a:p><a:r><a:t>使用 reset board 命令</a:t></a:r></a:p>
  </p:txBody></p:sp></p:spTree></p:cSld>
</p:sld>"""
        with zipfile.ZipFile(pptx, "w") as zf:
            zf.writestr("ppt/slides/slide1.xml", slide_xml)
        self.assertEqual(detect_doc_type(pptx), "pptx")
        result = parse_document(pptx, self.root / "pptx")
        self.assertEqual(result.pages, 1)
        generated = read_text(result.markdown_path)
        self.assertIn("单板复位", generated)
        self.assertIn("## Slide 1", generated)

    def test_parse_minimal_docx(self) -> None:
        docx = self.root / "demo.docx"
        document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>测试标题</w:t></w:r></w:p>
    <w:p><w:r><w:t>正文内容</w:t></w:r></w:p>
  </w:body>
</w:document>"""
        with zipfile.ZipFile(docx, "w") as zf:
            zf.writestr("word/document.xml", document_xml)
        self.assertEqual(detect_doc_type(docx), "docx")
        result = parse_document(docx, self.root / "docx")
        self.assertEqual(result.pages, 1)
        generated = read_text(result.markdown_path)
        self.assertIn("## 测试标题", generated)
        self.assertIn("正文内容", generated)


if __name__ == "__main__":
    unittest.main()
