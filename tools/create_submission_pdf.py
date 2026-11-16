"""Create the two-page course introduction using ReportLab and the current UI image."""
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "pdf" / "FGDocQA_项目介绍.pdf"
FONT_PATH = Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf")
FONT = "SubmissionCJK"
if FONT_PATH.is_file():
    pdfmetrics.registerFont(TTFont(FONT, str(FONT_PATH)))
else:
    FONT = "STSong-Light"
    pdfmetrics.registerFont(UnicodeCIDFont(FONT))

BLUE = colors.HexColor("#365d9d")
INK = colors.HexColor("#314662")
MUTED = colors.HexColor("#6e819b")
PALE = colors.HexColor("#eef4ff")
LINE = colors.HexColor("#dfe8f5")
W = A4[0] - 100
styles = {
    "body": ParagraphStyle("body", fontName=FONT, fontSize=10.2, leading=16, textColor=INK, wordWrap="CJK", spaceAfter=7),
    "small": ParagraphStyle("small", fontName=FONT, fontSize=8.4, leading=13.3, textColor=MUTED, wordWrap="CJK", spaceAfter=5),
    "title": ParagraphStyle("title", fontName=FONT, fontSize=36, leading=45, textColor=BLUE, spaceAfter=6),
    "subtitle": ParagraphStyle("subtitle", fontName=FONT, fontSize=14, leading=22, textColor=BLUE, spaceAfter=6),
    "h2": ParagraphStyle("h2", fontName=FONT, fontSize=22, leading=31, textColor=BLUE, spaceAfter=14),
    "h3": ParagraphStyle("h3", fontName=FONT, fontSize=12.4, leading=19, textColor=BLUE, spaceBefore=12, spaceAfter=7),
    "cell": ParagraphStyle("cell", fontName=FONT, fontSize=8.8, leading=14, textColor=INK, wordWrap="CJK"),
    "center": ParagraphStyle("center", fontName=FONT, fontSize=9.2, leading=15, textColor=BLUE, alignment=TA_CENTER, wordWrap="CJK"),
}


def p(text, style="body"):
    return Paragraph(text, styles[style])


def grid(rows, widths, header=False):
    data = [[p(cell, "cell") for cell in row] for row in rows]
    table = Table(data, colWidths=widths, hAlign="LEFT")
    commands = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 9), ("GRID", (0, 0), (-1, -1), 0.5, LINE)]
    if header:
        commands.append(("BACKGROUND", (0, 0), (-1, 0), PALE))
    table.setStyle(TableStyle(commands))
    return table


def footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.line(44, 34, A4[0] - 44, 34)
    canvas.setFont(FONT, 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(44, 20, "FGDocQA · 课程提交材料 · 2026-11-16")
    canvas.drawRightString(A4[0] - 44, 20, str(doc.page))
    canvas.restoreState()


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=44, rightMargin=44, topMargin=32, bottomMargin=47,
                            title="FGDocQA · 课程项目介绍", author="FGDocQA")
    story = [p("课程项目介绍  /  DOCUMENT QUESTION ANSWERING", "small"), p("FGDocQA", "title"),
             p("让每一份好奇，都有据可循。", "subtitle"),
             p("提交人：见班级个人材料文件夹  ·  源码 / 可执行程序 / 介绍材料", "small"), Spacer(1, 8)]
    story.append(Image(str(ROOT / "assets" / "web-home-v2.jpg"), width=W, height=W * 720 / 1280))
    story += [Spacer(1, 5), p("图 1 · 实际运行首页：DeepSeek 社区鲸鱼娘、示例问题、文库入口与提问框。", "small"),
              p("FGDocQA 是在本机运行的文档问答工作台，将 PDF、Word、PPT、Markdown 和 TXT 的正文与支持格式的图片整理为知识库，先检索证据，再生成附来源的回答。默认提供无需 API Key 的离线 Mock 演示，也支持通过 HTTP 接入真实模型。"),
              Spacer(1, 3)]
    story.append(grid([
        ["多格式文档与图片<br/>解析正文、提取图片，保留文档、页码与图片元信息。", "三路混合检索<br/>稠密向量 + BM25 + 本地 TF-IDF，经 RRF 融合与精排。"],
        ["回答有来源<br/>展示引用片段、原文与图片；证据不足时提示“无据不答”。", "浏览器工作台<br/>分类文库、分页阅读、图片预览、会话、收藏与 Markdown 导出。"],
    ], [W / 2, W / 2]))
    story += [Spacer(1, 9), p("成品包含 Python 运行时和演示资源。完整解压后启动，不需要另外安装 Python，也不会下载本地大模型。", "small"), PageBreak(),
              p("技术路线与使用方式", "h2")]
    flow = Table([[p("doc2md<br/>解析与来源", "center"), p("→", "center"), p("DocBase<br/>索引与检索", "center"), p("→", "center"),
                   p("Agent<br/>编排与引用", "center"), p("→", "center"), p("Web / CLI<br/>问答与阅读", "center")]],
                 colWidths=[(W - 60) / 4, 20, (W - 60) / 4, 20, (W - 60) / 4, 20, (W - 60) / 4])
    flow.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), PALE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                              ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                              ("TOPPADDING", (0, 0), (-1, -1), 12), ("BOTTOMPADDING", (0, 0), (-1, -1), 12)]))
    story += [flow, p("Mock 与 API", "h3"), grid([
        ["对比", "默认 Mock 演示", "HTTP API 模式"],
        ["条件", "无需 Key，适合离线验收", "配置有效端点、模型 ID 与凭据"],
        ["能力", "哈希向量、词面精排、图注规则释义、检索片段式回答", "启用的能力调用真实向量、精排、聊天或视觉服务"],
        ["验证", "程序自检与功能体验", "使用 probe 验证真实返回；填配置不等于接通"],
    ], [W * .13, W * .415, W * .455], header=True),
              p("交付文件与启动入口", "h3"), grid([
        ["01 源码", "02 可执行程序", "03 介绍材料"],
        ["业务代码、前端、示例、测试、依赖声明与构建脚本。高级 API 使用方式见源码 README。", "Windows x64：FGDocQA.exe<br/>Apple Mac：arm64 版<br/>Intel Mac：x64 版<br/>Mac 可双击 .command 启动。", "本介绍 PDF、可编辑说明、使用手册、材料清单、截图和构建验证记录。"],
    ], [W / 3] * 3, header=True),
              p("建议验收步骤", "h3"),
              p("1. 完整解压对应系统版本，保留程序旁的 _internal 目录。<br/>2. 启动后确认鲸鱼娘首页与 Mock 模式；点击示例问题，打开引用核对原文。<br/>3. 体验知识文库、图片、收藏与导出；在程序终端按 Ctrl+C 结束服务。", "small"),
              p("验证与边界", "h3"),
              p("原有 38 项工程测试已通过；新增打包回归与各平台成品自检，以随包《构建验证记录》为准。HTTP 单测使用模拟响应，不代表真实服务鉴权成功。当前稀疏路是 tfidf-local；Mock 不代表真实语义模型效果。", "small"),
              p("面向个人本机，监听 127.0.0.1；没有多用户鉴权。DOCX 页码为估算，扫描 PDF 没有完整 OCR 流程。API 模式会向所配置服务发送相应文本或图片。演示业务语料为虚构场景。", "small"),
              p('源码：<link href="https://github.com/zzhseu1108/HUAWEIhomework-FGDocQA" color="#365d9d">github.com/zzhseu1108/HUAWEIhomework-FGDocQA</link>', "small"),
              p("鲸鱼娘为社区同人素材，署名与 CC BY-NC-SA 4.0 许可随源码保留，详见 ARTWORK-LICENSE.md。", "small")]
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    print(OUT)


if __name__ == "__main__":
    main()
