#!/usr/bin/env python3
"""Minimal Markdown -> print-friendly HTML for DocQA docs.

Usage:
    python3 tools/md2html.py docs/01_项目申报书.md docs/01_项目申报书.html
    /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \
        --headless=new --no-sandbox --no-pdf-header-footer \
        --print-to-pdf=docs/01_项目申报书.pdf docs/01_项目申报书.html
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path
from typing import List


def inline(text: str) -> str:
    text = html.escape(text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text


def convert(markdown: str) -> str:
    lines = markdown.replace("\r\n", "\n").split("\n")
    out: List[str] = []
    i = 0
    in_list = None
    in_code = False
    code_lines: List[str] = []
    table: List[List[str]] = []

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append(f"</{in_list}>")
            in_list = None

    def flush_table() -> None:
        if not table:
            return
        rows = [row for row in table if not all(re.fullmatch(r":?-{2,}:?", c.strip()) for c in row)]
        if not rows:
            table.clear()
            return
        out.append("<table>")
        out.append("<thead><tr>" + "".join(f"<th>{inline(c.strip())}</th>" for c in rows[0]) + "</tr></thead>")
        out.append("<tbody>")
        for row in rows[1:]:
            out.append("<tr>" + "".join(f"<td>{inline(c.strip())}</td>" for c in row) + "</tr>")
        out.append("</tbody></table>")
        table.clear()

    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            if not in_code:
                close_list()
                flush_table()
                in_code = True
                code_lines = []
            else:
                out.append('<pre><code>' + html.escape("\n".join(code_lines)) + "</code></pre>")
                in_code = False
            i += 1
            continue
        if in_code:
            code_lines.append(line)
            i += 1
            continue
        if line.strip().startswith("|") and line.strip().endswith("|"):
            close_list()
            table.append([c for c in line.strip().strip("|").split("|")])
            i += 1
            continue
        flush_table()
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            close_list()
            level = len(heading.group(1))
            content = inline(heading.group(2))
            anchor = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", heading.group(2)).strip("-")
            out.append(f'<h{level} id="{anchor}">{content}</h{level}>')
            i += 1
            continue
        if re.match(r"^\s*---+\s*$", line):
            close_list()
            out.append("<hr>")
            i += 1
            continue
        if line.startswith(">"):
            close_list()
            quote = []
            while i < len(lines) and lines[i].startswith(">"):
                quote.append(lines[i].lstrip(">").strip())
                i += 1
            out.append("<blockquote>" + "<br>".join(inline(q) for q in quote) + "</blockquote>")
            continue
        bullet = re.match(r"^\s*[-*]\s+(.*)$", line)
        numbered = re.match(r"^\s*\d+\.\s+(.*)$", line)
        if bullet or numbered:
            kind = "ul" if bullet else "ol"
            if in_list != kind:
                close_list()
                out.append(f"<{kind}>")
                in_list = kind
            out.append(f"<li>{inline((bullet or numbered).group(1))}</li>")
            i += 1
            continue
        if not line.strip():
            close_list()
            i += 1
            continue
        close_list()
        out.append(f"<p>{inline(line)}</p>")
        i += 1
    close_list()
    flush_table()
    return "\n".join(out)


TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  @page {{ size: A4; margin: 18mm 16mm 18mm 18mm; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; color: #1b2f42; background: #fff;
    font-family: "Songti SC", "STSong", "SimSun", serif;
    font-size: 10.6pt; line-height: 1.9;
  }}
  .cover {{
    min-height: 245mm; display: flex; flex-direction: column; justify-content: center;
    text-align: center; page-break-after: always; padding: 22mm 10mm;
    background: linear-gradient(150deg, #f7fbfe, #eaf4fb);
    border-top: 6px solid #1f6fb2; border-bottom: 6px solid #0f8a7e;
  }}
  .cover .award {{ font-size: 11pt; color: #1f6fb2; letter-spacing: 1px; margin-bottom: 14mm; }}
  .cover h1 {{
    font-family: "PingFang SC", "Hiragino Sans GB", sans-serif;
    font-size: 26pt; line-height: 1.4; color: #0b2a5b; margin: 0 0 8mm;
  }}
  .cover .sub {{ font-size: 12pt; color: #3d6788; margin-bottom: 18mm; }}
  .cover .abstract {{
    text-align: left; font-family: "PingFang SC", "Hiragino Sans GB", sans-serif;
    font-size: 9.6pt; line-height: 1.95; color: #2f4f68;
    border: 1px solid #a9cde6; background: rgba(255,255,255,.72);
    padding: 7mm 9mm; border-radius: 3mm;
  }}
  .cover .abstract b {{ color: #0f7a71; }}
  .cover .meta {{ margin-top: 20mm; font-size: 9.5pt; color: #607c92; line-height: 2; }}
  h1, h2, h3, h4 {{
    font-family: "PingFang SC", "Hiragino Sans GB", sans-serif;
    color: #0b2a5b; line-height: 1.45; page-break-after: avoid;
  }}
  h1 {{ font-size: 20pt; margin: 0 0 6mm; padding-bottom: 3mm; border-bottom: 2px solid #1f6fb2; }}
  h2 {{ font-size: 14pt; margin: 9mm 0 3mm; padding-left: 3mm; border-left: 3px solid #0f8a7e; }}
  h3 {{ font-size: 12pt; margin: 6mm 0 2mm; }}
  h4 {{ font-size: 10.8pt; margin: 4mm 0 1.5mm; }}
  p {{ margin: 2.2mm 0; text-align: justify; }}
  strong {{ color: #0b3d6b; }}
  a {{ color: #1f6fb2; text-decoration: none; }}
  ul, ol {{ margin: 2mm 0 2mm 6mm; padding-left: 5mm; }}
  li {{ margin: 1mm 0; }}
  blockquote {{
    margin: 3mm 0; padding: 2.5mm 4mm; border-left: 3px solid #8fc0e2;
    background: #f3f9fd; color: #3d5d75; font-size: 9.8pt;
  }}
  code {{
    font-family: "SFMono-Regular", Consolas, monospace; font-size: 9pt;
    background: #f0f6fa; color: #17619d; padding: 0.3mm 1.2mm; border-radius: 1mm;
  }}
  pre {{
    background: #f6f9fb; border: 1px solid #dbe8f2; border-radius: 2mm;
    padding: 3mm 4mm; overflow: auto; page-break-inside: avoid;
  }}
  pre code {{ background: none; padding: 0; color: #2f4f68; font-size: 8.4pt; line-height: 1.55; }}
  table {{
    width: 100%; border-collapse: collapse; margin: 3mm 0; font-size: 9pt;
    page-break-inside: avoid;
  }}
  th, td {{ border: 1px solid #c9dcea; padding: 1.6mm 2.2mm; text-align: left; vertical-align: top; }}
  th {{ background: #eaf4fc; color: #12456d; font-family: "PingFang SC", sans-serif; font-weight: 600; }}
  tr:nth-child(even) td {{ background: #fbfdff; }}
  hr {{ border: 0; border-top: 1px dashed #c9dcea; margin: 5mm 0; }}
  .footer-note {{ margin-top: 12mm; padding-top: 3mm; border-top: 1px solid #dbe8f2;
                  font-size: 8.4pt; color: #7a90a2; font-family: "PingFang SC", sans-serif; }}
  @media print {{ .cover {{ min-height: 250mm; }} }}
</style>
</head>
<body>
{cover}
{body}
<div class="footer-note">DocQA · 基于 AI Agent 的个人本地多模态文档智能问答系统 · 井永乐 | 00587807 · 2026-07-30</div>
</body>
</html>
"""


def cover_html(title: str) -> str:
    return f"""<section class="cover">
  <div class="award">2026 年数通第八届爱迪生创新大赛 · 入围作品</div>
  <h1>{title}</h1>
  <div class="sub">doc2md × DocBase × AGENTS.md —— 让沉睡的本地文档“开口说话”</div>
  <div class="abstract"><b>摘　要</b><br>个人本地沉淀了大量 PDF、Word、PPT 等多模态文档，一旦忘记存放位置与文件名，仅凭模糊的内容记忆极难找回。本创意通过 AI Agent（CodeAgent）自动开发 Python 脚本，结合 RAG、Embedding、Reranker、Chat、VL 等大模型能力，预解析本地文档并构建 RAG 数据库，再以 AGENTS.md 指导 Agent 消费该库，实现“内容即入口”的智能问答与精准溯源。</div>
  <div class="meta">创意提交人：井永乐 | 00587807<br>转发开发部 [ICT BG] · 2026-07-30<br>技术栈：CodeAgent · RAG · Embedding · Reranker · Chat LLM · VL</div>
</section>"""


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: md2html.py input.md output.html [title]")
        return 2
    args = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    no_cover = "--no-cover" in sys.argv
    source = Path(args[0])
    target = Path(args[1])
    title = args[2] if len(args) > 2 else source.stem
    text = source.read_text(encoding="utf-8")
    if no_cover:
        body_md = text
        cover = ""
    else:
        # Strip the Markdown title and first blockquote from the body; the cover
        # repeats the title and submission metadata.
        lines = text.splitlines()
        body_start = 0
        for index, line in enumerate(lines):
            if index > 0 and line.startswith("## "):
                body_start = index
                break
        body_md = "\n".join(lines[body_start:])
        cover = cover_html(title)
    body = convert(body_md)
    html_text = TEMPLATE.format(title=html.escape(title), cover=cover, body=body)
    target.write_text(html_text, encoding="utf-8")
    print(f"written {target} ({len(html_text)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
