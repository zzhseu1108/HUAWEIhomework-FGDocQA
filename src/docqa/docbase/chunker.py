from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ..utils import file_uri, parse_front_matter, sha1_text, split_sentences
from .models import Chunk

PAGE_RE = re.compile(r"<!--\s*page\s*:\s*(\d+)\s*-->", re.I)
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\n]+)\)")
META_IMAGE_RE = re.compile(r"<!--\s*docqa:image\s+([^>]*?)-->", re.I)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def _image_key(path: str) -> str:
    path = path.strip().strip("<>")
    path = path.split(' "')[0]
    if path.startswith("./"):
        path = path[2:]
    return path.replace("\\", "/")


def chunk_markdown(
    text: str,
    doc_meta: Dict[str, Any],
    images: Optional[Iterable[Dict[str, Any]]] = None,
    chunk_size: int = 800,
    overlap: int = 120,
) -> List[Chunk]:
    meta, body = parse_front_matter(text)
    merged = dict(meta)
    merged.update({k: v for k, v in doc_meta.items() if v not in (None, "")})
    doc_name = str(merged.get("doc_name") or "未命名文档")
    doc_id = str(merged.get("doc_id") or sha1_text(doc_name)[:12])
    category = str(merged.get("category") or "未分类")
    source = str(merged.get("source") or "")
    pages = int(merged.get("pages") or 1)

    image_map: Dict[str, Dict[str, Any]] = {}
    for image in images or []:
        if not isinstance(image, dict):
            image = image.__dict__
        key = _image_key(str(image.get("path", "")))
        if key:
            image_map[key] = image
            image_map[Path(key).name] = image

    # Split into sections at headings and page boundaries. A chunk never spans
    # two pages, which keeps citations page-accurate.
    sections: List[Dict[str, Any]] = []
    heading_stack: List[tuple[int, str]] = []
    current = {"lines": [], "page": 1, "headings": []}
    page = 1

    def flush() -> None:
        nonlocal current
        content = "\n".join(current["lines"]).strip()
        if content:
            sections.append(
                {
                    "content": content,
                    "page": current["page"],
                    "headings": list(current["headings"]),
                }
            )
        current = {"lines": [], "page": page, "headings": list(current["headings"])}

    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        page_match = PAGE_RE.search(line)
        if page_match:
            new_page = int(page_match.group(1))
            if current["lines"] and new_page != page:
                flush()
            page = new_page
            current["page"] = page
            continue
        line = META_IMAGE_RE.sub("", line)
        heading_match = HEADING_RE.match(line)
        if heading_match:
            flush()
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()
            heading_stack = [(lvl, txt) for lvl, txt in heading_stack if lvl < level]
            heading_stack.append((level, title))
            current["headings"] = [txt for _, txt in heading_stack]
            current["page"] = page
            continue
        if line.strip():
            current["lines"].append(line)
        elif current["lines"] and current["lines"][-1] != "":
            current["lines"].append("")
    flush()

    chunks: List[Chunk] = []
    order = 0
    for section in sections:
        pieces = _split_content(section["content"], chunk_size, overlap)
        for piece in pieces:
            piece = piece.strip()
            if len(piece) < 20:
                continue
            heading = " / ".join(section["headings"])
            prefix = f"【{heading}】\n" if heading else ""
            content = prefix + piece
            refs = _collect_images(content, image_map)
            for image in refs:
                if not image.get("source_uri") and image.get("page"):
                    image["source_uri"] = file_uri(source, int(image["page"]), int(image.get("figure_index", 0)))
            source_uri = file_uri(source, section["page"]) if source else ""
            chunks.append(
                Chunk(
                    chunk_id=sha1_text(f"{doc_id}:{order}:{content}")[:16],
                    doc_id=doc_id,
                    doc_name=doc_name,
                    heading=heading,
                    text=content,
                    page=int(section["page"]),
                    category=category,
                    source_path=source,
                    source_uri=source_uri,
                    order=order,
                    images=refs,
                    content_hash=sha1_text(content)[:16],
                    char_count=len(content),
                )
            )
            order += 1
    return chunks


def _split_content(content: str, chunk_size: int, overlap: int) -> List[str]:
    paragraphs = re.split(r"\n\s*\n", content)
    pieces: List[str] = []
    buffer = ""

    def push(value: str) -> None:
        nonlocal buffer
        value = value.strip()
        if value:
            pieces.append(value)
        buffer = ""

    for paragraph in paragraphs:
        paragraph = paragraph.strip("\n")
        if not paragraph.strip():
            continue
        is_code = paragraph.lstrip().startswith("```")
        if is_code:
            # Keep Mermaid/code blocks atomic, but merge them with the preceding
            # prose when they fit: a flow chunk should carry its own diagram.
            if buffer and len(buffer) + len(paragraph) + 2 <= chunk_size:
                buffer = buffer + "\n\n" + paragraph
                continue
            if buffer:
                push(buffer)
            if len(paragraph) <= chunk_size * 2:
                pieces.append(paragraph)
            else:
                for part in _hard_split(paragraph, chunk_size):
                    pieces.append(part)
            continue
        if len(buffer) + len(paragraph) + 2 <= chunk_size:
            buffer = (buffer + "\n\n" + paragraph).strip() if buffer else paragraph
            continue
        if buffer:
            push(buffer)
        if len(paragraph) <= chunk_size:
            buffer = paragraph
        else:
            for part in _split_long_paragraph(paragraph, chunk_size):
                pieces.append(part)

    if buffer.strip():
        pieces.append(buffer.strip())

    if overlap > 0 and len(pieces) > 1:
        with_overlap = [pieces[0]]
        for piece in pieces[1:]:
            previous = with_overlap[-1]
            tail = previous[-overlap:]
            cut = max(tail.rfind("。"), tail.rfind("\n"), tail.rfind("；"))
            if cut > 0:
                tail = tail[cut + 1 :]
            tail = tail.strip()
            if tail and tail not in piece:
                with_overlap.append(tail + "\n" + piece)
            else:
                with_overlap.append(piece)
        pieces = with_overlap
    return pieces


def _split_long_paragraph(paragraph: str, chunk_size: int) -> List[str]:
    sentences = split_sentences(paragraph) or [paragraph]
    out: List[str] = []
    buffer = ""
    for sentence in sentences:
        if len(buffer) + len(sentence) + 1 <= chunk_size:
            buffer = buffer + sentence if not buffer else buffer + "\n" + sentence
        else:
            if buffer:
                out.append(buffer)
            if len(sentence) <= chunk_size:
                buffer = sentence
            else:
                for i in range(0, len(sentence), chunk_size):
                    out.append(sentence[i : i + chunk_size])
                buffer = ""
    if buffer:
        out.append(buffer)
    return out


def _hard_split(text: str, chunk_size: int) -> List[str]:
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


def _collect_images(content: str, image_map: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for match in IMAGE_RE.finditer(content):
        key = _image_key(match.group(2))
        image = image_map.get(key) or image_map.get(Path(key).name)
        if not image:
            continue
        image_id = str(image.get("image_id", key))
        if image_id in seen:
            continue
        seen.add(image_id)
        out.append(
            {
                "image_id": image_id,
                "path": image.get("path", key),
                "page": image.get("page", 0),
                "figure_index": image.get("figure_index", 0),
                "title": image.get("title", ""),
                "description": image.get("description", ""),
                "source_uri": image.get("source_uri", ""),
            }
        )
    return out
