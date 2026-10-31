from __future__ import annotations

import json
import posixpath
import re
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from ..llm import ModelHub
from ..utils import (
    ensure_dir,
    file_sha1,
    file_uri,
    now_iso,
    read_text,
    safe_name,
    sha1_text,
    strip_markdown,
    truncate,
    write_json,
    write_text,
)
from .image_extractor import guess_image_ext, image_dimensions, save_image
from .models import ImageInfo, ParseResult, ParseTask
from .naming import figure_filename, figure_source_ref, figure_title
from .vl_client import VLDescriber

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
}
VL_SUPPORTED = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\n]+)\)")
PAGE_RE = re.compile(r"<!--\s*page\s*:\s*(\d+)\s*-->", re.I)


def qn(prefix: str, tag: str) -> str:
    return f"{{{NS[prefix]}}}{tag}"


def _clean_title(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


class DocParser:
    def __init__(
        self,
        out_dir: str | Path,
        category: Optional[str] = None,
        hub: Optional[ModelHub] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.assets_dir = ensure_dir(self.out_dir / "assets")
        self.category = category or "未分类"
        self.hub = hub or ModelHub()
        self.vl = VLDescriber(self.hub)
        self.options = options or {}
        self.images: List[ImageInfo] = []
        self.warnings: List[str] = []
        self.fig_counters: Dict[int, int] = {}
        self.doc_name = ""
        self.src: Optional[Path] = None
        self.source_uri = ""

    # --------------------------------------------------------------- helpers
    def _next_figure(self, page: int) -> int:
        self.fig_counters[page] = self.fig_counters.get(page, 0) + 1
        return self.fig_counters[page]

    def _ingest_image(
        self,
        data: bytes,
        original_name: str,
        page: int,
        title: str,
        alt: str,
        nearby: str,
        kind: str = "bitmap",
    ) -> Optional[ImageInfo]:
        ext = guess_image_ext(original_name, data)
        if ext in {".emf", ".wmf", ".tif", ".tiff", ".svg"}:
            self.warnings.append(
                f"跳过暂不支持直接读图的 {ext} 图片：{original_name}（需先渲染为位图）"
            )
            return None
        index = self._next_figure(page)
        title = _clean_title(title) or f"图 {index}"
        filename = figure_filename(self.doc_name, page, index, title, ext)
        dest, width, height = save_image(data, self.assets_dir, filename)
        context = {
            "doc_name": self.doc_name,
            "page": page,
            "title": title,
            "alt": alt,
            "nearby": nearby,
        }
        description = self.vl.describe(dest, context)
        source_uri = file_uri(self.src, page, index) if self.src else ""
        image = ImageInfo(
            image_id=sha1_text(f"{self.doc_name}:{page}:{index}:{filename}")[:16],
            doc_name=self.doc_name,
            page=page,
            figure_index=index,
            title=title,
            path=f"assets/{filename}",
            kind=kind,
            width=width,
            height=height,
            description=description,
            caption=alt,
            source_ref=figure_source_ref(self.doc_name, page, index),
            source_uri=source_uri,
            vl_model=self.vl.model,
            vl_mode=self.vl.mode,
        )
        self.images.append(image)
        return image

    def _image_block(self, image: ImageInfo) -> str:
        desc = re.sub(r"\s+", " ", image.description).strip()
        title = image.title.replace("-->", "—>")
        return (
            f"![{title}]({image.path})\n\n"
            f"> **图 {image.page}-{image.figure_index} · {title}**"
            f"（VL 释义）：{desc}\n\n"
            f"<!-- docqa:image path={image.path} page={image.page} "
            f"figure={image.figure_index} title={title} -->"
        )

    # -------------------------------------------------------------- dispatch
    def parse(self, src: str | Path) -> ParseResult:
        started = time.perf_counter()
        src = Path(src).resolve()
        if not src.exists():
            raise FileNotFoundError(f"源文件不存在：{src}")
        self.src = src
        self.doc_name = src.stem
        doc_type = detect_doc_type(src)
        existing_meta, _ = _read_front_matter(src) if doc_type == "markdown" else ({}, "")
        if existing_meta.get("doc_name"):
            self.doc_name = str(existing_meta["doc_name"])
        if existing_meta.get("category"):
            self.category = str(existing_meta["category"])

        if doc_type == "markdown":
            body, pages, page_mode = self._parse_markdown(src)
        elif doc_type == "pdf":
            body, pages, page_mode = self._parse_pdf(src)
        elif doc_type == "docx":
            body, pages, page_mode = self._parse_docx(src)
        elif doc_type == "pptx":
            body, pages, page_mode = self._parse_pptx(src)
        elif doc_type == "text":
            body, pages, page_mode = self._parse_text(src)
        else:
            raise ValueError(f"不支持的文件类型：{src.suffix}（支持 PDF / Word / PPT / Markdown / TXT）")

        pages = max(1, pages)
        doc_id = str(existing_meta.get("doc_id") or sha1_text(str(src))[:12])
        source_uri = file_uri(src)
        meta = {
            "doc_id": doc_id,
            "doc_name": self.doc_name,
            "source": str(src),
            "source_uri": source_uri,
            "category": self.category,
            "pages": pages,
            "page_mode": page_mode,
            "generator": "docqa.doc2md/1.0",
            "parsed_at": now_iso(),
            "images": len(self.images),
            "vl_mode": self.vl.mode,
        }
        from ..utils import to_front_matter

        document = to_front_matter(meta) + "\n" + body.strip() + "\n"
        markdown_path = write_text(self.out_dir / "document.md", document)

        elapsed = (time.perf_counter() - started) * 1000
        task = ParseTask(
            task_id=sha1_text(f"{src}:{time.time()}")[:12],
            src=str(src),
            out_dir=str(self.out_dir),
            doc_type=doc_type,
            category=self.category,
            vl_mode=self.vl.mode,
            options=self.options,
            created_at=now_iso(),
        )
        stats = {
            "chars": len(strip_markdown(body)),
            "images": len(self.images),
            "pages": pages,
            "elapsed_ms": round(elapsed, 2),
            "vl_calls": self.vl.calls,
            "source_sha1": file_sha1(src),
        }
        result = ParseResult(
            task=task,
            doc_id=doc_id,
            doc_name=self.doc_name,
            markdown_path=str(markdown_path),
            manifest_path=str(self.out_dir / "manifest.json"),
            assets_dir=str(self.assets_dir),
            images=self.images,
            warnings=self.warnings,
            stats=stats,
            pages=pages,
            page_mode=page_mode,
            source_uri=source_uri,
        )
        write_json(result.manifest_path, result.to_dict())
        return result

    # -------------------------------------------------------------- markdown
    def _parse_markdown(self, src: Path) -> Tuple[str, int, str]:
        text = read_text(src)
        _, text = _read_front_matter_text(text)
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        out: List[str] = []
        page = 1
        max_page = 1
        for idx, line in enumerate(lines):
            page_match = PAGE_RE.search(line)
            if page_match:
                page = int(page_match.group(1))
                max_page = max(max_page, page)
            if not IMAGE_RE.search(line):
                out.append(line)
                continue

            matches = list(IMAGE_RE.finditer(line))
            new_line = line
            for match in reversed(matches):
                alt = match.group(1).strip()
                raw_path = match.group(2).strip()
                raw_path = raw_path.split(' "')[0].strip()
                if raw_path.startswith("<") and raw_path.endswith(">"):
                    raw_path = raw_path[1:-1]
                if re.match(r"^(https?:|data:)", raw_path, re.I):
                    continue
                local = (src.parent / unquote(raw_path)).resolve()
                if not local.exists():
                    self.warnings.append(f"图片不存在，保留原引用：{raw_path}")
                    continue
                caption = _caption_after(lines, idx)
                title = figure_title(caption or alt, f"Page {page} 图", 48)
                nearby = "\n".join(out[-3:])
                image = self._ingest_image(
                    local.read_bytes(),
                    local.name,
                    page,
                    title,
                    alt,
                    nearby,
                )
                if image is None:
                    continue
                block = self._image_block(image)
                new_line = new_line[: match.start()] + "\n\n" + block + "\n\n" + new_line[match.end() :]
            out.append(new_line)
        max_page = max(max_page, max([int(m.group(1)) for m in PAGE_RE.finditer("\n".join(lines))] or [1]))
        return "\n".join(out), max_page, "exact"

    # ------------------------------------------------------------------- pdf
    def _parse_pdf(self, src: Path) -> Tuple[str, int, str]:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("解析 PDF 需要 pypdf，请运行：python3 -m pip install pypdf") from exc

        reader = PdfReader(str(src))
        out: List[str] = []
        for page_no, page in enumerate(reader.pages, start=1):
            out.append(f"<!-- page: {page_no} -->")
            try:
                page_text = page.extract_text() or ""
            except Exception as exc:
                page_text = ""
                self.warnings.append(f"PDF 第 {page_no} 页取文失败：{exc}")
            if page_text.strip():
                out.append(page_text.strip())
            lines = [line.strip() for line in page_text.splitlines() if line.strip()]
            caption = ""
            for line in reversed(lines[-5:]):
                if re.match(r"^(图|figure|fig\.?)\s*\d+", line, re.I):
                    caption = line
                    break
            try:
                pypdf_images = list(page.images)
            except Exception as exc:
                pypdf_images = []
                self.warnings.append(f"PDF 第 {page_no} 页取图失败：{exc}")
            for img in pypdf_images:
                try:
                    data = img.data
                    name = getattr(img, "name", "") or f"page{page_no}.png"
                except Exception as exc:
                    self.warnings.append(f"PDF 第 {page_no} 页图片解码失败：{exc}")
                    continue
                if not data or len(data) < 128:
                    continue
                title = figure_title(caption, f"Page {page_no} 图", 48)
                image = self._ingest_image(
                    data,
                    name,
                    page_no,
                    title,
                    caption or title,
                    page_text[-240:],
                )
                if image:
                    out.append(self._image_block(image))
        return "\n\n".join(out), len(reader.pages), "exact"

    # ------------------------------------------------------------------ docx
    def _parse_docx(self, src: Path) -> Tuple[str, int, str]:
        with zipfile.ZipFile(src) as zf:
            names = set(zf.namelist())
            if "word/document.xml" not in names:
                raise ValueError("不是有效的 .docx 文件（缺少 word/document.xml）")
            root = ET.fromstring(zf.read("word/document.xml"))
            rels = _read_rels(zf, "word/document.xml")
            body = root.find(qn("w", "body"))
            if body is None:
                raise ValueError("Word 文档缺少正文")
            out: List[str] = []
            page = 1
            for element in list(body):
                tag = _local(element.tag)
                if tag == "p":
                    text = _docx_paragraph_text(element)
                    style = _docx_style(element)
                    if style:
                        level = _heading_level(style)
                        if level:
                            if text:
                                out.append("#" * min(level + 1, 6) + " " + text)
                        elif text:
                            out.append(text)
                    elif text:
                        out.append(text)
                    for image_node in _docx_drawings(element):
                        rid, alt, title_hint = image_node
                        target = rels.get(rid, "")
                        data = _read_zip_part(zf, target)
                        if not data:
                            self.warnings.append(f"Word 图片关系缺失：{rid}")
                            continue
                        title = figure_title(title_hint or alt, f"Page {page} 图", 48)
                        image = self._ingest_image(
                            data,
                            Path(target).name,
                            page,
                            title,
                            alt,
                            text[-240:],
                        )
                        if image:
                            out.append(self._image_block(image))
                    if _docx_has_page_break(element):
                        page += 1
                        out.append(f"<!-- page: {page} -->")
                elif tag == "tbl":
                    table = _docx_table(element)
                    if table:
                        out.append(table)
            pages = page
        return "\n\n".join(part for part in out if part.strip()), pages, "estimated"

    # ------------------------------------------------------------------ pptx
    def _parse_pptx(self, src: Path) -> Tuple[str, int, str]:
        with zipfile.ZipFile(src) as zf:
            slides = []
            for name in zf.namelist():
                match = re.match(r"ppt/slides/slide(\d+)\.xml$", name)
                if match:
                    slides.append((int(match.group(1)), name))
            if not slides:
                raise ValueError("不是有效的 .pptx 文件（缺少 ppt/slides）")
            slides.sort()
            out: List[str] = []
            for page, name in slides:
                root = ET.fromstring(zf.read(name))
                paragraphs = _pptx_paragraphs(root)
                title = paragraphs[0] if paragraphs else f"Slide {page}"
                out.append(f"<!-- page: {page} -->")
                out.append(f"## Slide {page}：{title}")
                for paragraph in paragraphs[1:]:
                    out.append(f"- {paragraph}")
                rels = _read_rels(zf, name)
                for rid, target in rels.items():
                    if not _is_image_target(target):
                        continue
                    data = _read_zip_part(zf, target)
                    if not data:
                        continue
                    image = self._ingest_image(
                        data,
                        Path(target).name,
                        page,
                        figure_title(title, f"Page {page} 图", 48),
                        title,
                        " / ".join(paragraphs[:3]),
                    )
                    if image:
                        out.append(self._image_block(image))
        return "\n\n".join(out), len(slides), "exact"

    # ------------------------------------------------------------------- txt
    def _parse_text(self, src: Path) -> Tuple[str, int, str]:
        text = read_text(src)
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        return "\n\n".join(paragraphs), 1, "exact"


def detect_doc_type(path: str | Path) -> str:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in {".md", ".markdown"}:
        return "markdown"
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".docx":
        return "docx"
    if suffix == ".pptx":
        return "pptx"
    if suffix in {".txt", ".text", ".log"}:
        return "text"
    try:
        head = p.read_bytes()[:8]
    except Exception:
        return "unknown"
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK"):
        try:
            with zipfile.ZipFile(p) as zf:
                names = set(zf.namelist())
            if "word/document.xml" in names:
                return "docx"
            if any(name.startswith("ppt/slides/") for name in names):
                return "pptx"
        except Exception:
            pass
    return "unknown"


def _read_front_matter(path: Path) -> Tuple[Dict[str, str], str]:
    return _read_front_matter_text(read_text(path))


def _read_front_matter_text(text: str) -> Tuple[Dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            meta: Dict[str, str] = {}
            for line in lines[1:i]:
                if ":" in line:
                    key, value = line.split(":", 1)
                    meta[key.strip()] = value.strip()
            return meta, "\n".join(lines[i + 1 :]).lstrip("\n")
    return {}, text


def _caption_after(lines: List[str], index: int) -> str:
    for line in lines[index + 1 : index + 3]:
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^(图|figure|fig\.?)\s*\d+", stripped, re.I):
            return stripped
        break
    return ""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _read_rels(zf: zipfile.ZipFile, part: str) -> Dict[str, str]:
    rels_path = posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")
    if rels_path not in zf.namelist():
        return {}
    root = ET.fromstring(zf.read(rels_path))
    out: Dict[str, str] = {}
    for rel in root:
        rid = rel.get("Id")
        target = rel.get("Target")
        if not rid or not target or rel.get("TargetMode") == "External":
            continue
        out[rid] = target
    return out


def _read_zip_part(zf: zipfile.ZipFile, target: str) -> bytes:
    if not target:
        return b""
    target = target.lstrip("/")
    candidates = [
        target,
        posixpath.normpath(target),
        posixpath.normpath(posixpath.join("word", target)),
        posixpath.normpath(posixpath.join("ppt/slides", target)),
        posixpath.normpath(posixpath.join("ppt", target)),
    ]
    for candidate in candidates:
        if candidate in zf.namelist():
            return zf.read(candidate)
    return b""


def _docx_paragraph_text(paragraph: ET.Element) -> str:
    chunks = []
    for node in paragraph.iter():
        if _local(node.tag) == "t" and node.text:
            chunks.append(node.text)
    return re.sub(r"\s+", " ", "".join(chunks)).strip()


def _docx_style(paragraph: ET.Element) -> str:
    style = paragraph.find(".//" + qn("w", "pStyle"))
    if style is None:
        return ""
    return style.get(qn("w", "val")) or ""


def _heading_level(style: str) -> int:
    lowered = style.lower()
    if "heading" in lowered or style.startswith("标题"):
        digits = re.findall(r"\d+", style)
        return int(digits[0]) if digits else 2
    return 0


def _docx_drawings(paragraph: ET.Element) -> List[Tuple[str, str, str]]:
    out: List[Tuple[str, str, str]] = []
    for container in paragraph.iter():
        if _local(container.tag) not in {"inline", "anchor"}:
            continue
        doc_pr = None
        blip = None
        for node in container.iter():
            local = _local(node.tag)
            if local == "docPr" and doc_pr is None:
                doc_pr = node
            elif local == "blip" and blip is None:
                blip = node
        if blip is None:
            continue
        rid = blip.get(qn("r", "embed")) or blip.get(qn("r", "link")) or ""
        alt = ""
        title = ""
        if doc_pr is not None:
            alt = doc_pr.get("descr") or ""
            title = doc_pr.get("title") or ""
        if rid:
            out.append((rid, alt, title))
    return out


def _docx_has_page_break(paragraph: ET.Element) -> bool:
    for node in paragraph.iter():
        if _local(node.tag) == "br" and node.get(qn("w", "type")) == "page":
            return True
        if _local(node.tag) == "lastRenderedPageBreak":
            return True
    return False


def _docx_table(table: ET.Element) -> str:
    rows: List[List[str]] = []
    for row in table.findall(".//" + qn("w", "tr")):
        cells = []
        for cell in row.findall(qn("w", "tc")):
            text = " ".join(
                _docx_paragraph_text(p) for p in cell.findall(".//" + qn("w", "p"))
            ).strip()
            cells.append(text.replace("|", "\\|") or " ")
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [" "] * (width - len(row)) for row in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "|".join([" --- "] * width) + "|"]
    for row in rows[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _pptx_paragraphs(root: ET.Element) -> List[str]:
    paragraphs = []
    for paragraph in root.iter(qn("a", "p")):
        chunks = []
        for node in paragraph.iter():
            if _local(node.tag) == "t" and node.text:
                chunks.append(node.text)
        text = re.sub(r"\s+", " ", "".join(chunks)).strip()
        if text:
            paragraphs.append(text)
    return paragraphs


def _is_image_target(target: str) -> bool:
    return Path(target).suffix.lower() in VL_SUPPORTED | {".emf", ".wmf", ".svg", ".tif", ".tiff"}


def parse_document(
    src: str | Path,
    out_dir: Optional[str | Path] = None,
    category: Optional[str] = None,
    hub: Optional[ModelHub] = None,
    options: Optional[Dict[str, Any]] = None,
) -> ParseResult:
    src_path = Path(src).resolve()
    if out_dir is None:
        out_dir = Path("data/parsed") / safe_name(src_path.stem)
    parser = DocParser(out_dir, category=category, hub=hub, options=options)
    return parser.parse(src_path)


def batch_parse(
    root: str | Path,
    out_root: str | Path,
    recursive: bool = True,
    types: Optional[Iterable[str]] = None,
    category: Optional[str] = None,
    hub: Optional[ModelHub] = None,
    progress: Optional[Any] = None,
) -> List[ParseResult]:
    root = Path(root).resolve()
    out_root = Path(out_root)
    allowed = set(types or ["md", "markdown", "pdf", "docx", "pptx", "txt"])
    iterator = root.rglob("*") if recursive else root.glob("*")
    files = [
        p for p in sorted(iterator)
        if p.is_file() and p.suffix.lower().lstrip(".") in allowed and not p.name.startswith(".")
    ]
    results = []
    for index, path in enumerate(files, start=1):
        if progress:
            progress(index - 1, len(files), f"解析 {path.name}")
        out_dir = out_root / safe_name(path.stem)
        manifest = out_dir / "manifest.json"
        if out_dir.exists() and (out_dir / "document.md").exists():
            existing = json.loads(manifest.read_text("utf-8")) if manifest.exists() else {}
            if existing.get("task", {}).get("src") != str(path.resolve()):
                # Avoid mixing assets when two source files share a stem.
                out_dir = out_root / safe_name(f"{path.stem}_{sha1_text(str(path))[:6]}")
        results.append(
            parse_document(
                path,
                out_dir=out_dir,
                category=category or path.parent.name,
                hub=hub,
            )
        )
    if progress:
        progress(len(files), len(files), "解析完成")
    return results
