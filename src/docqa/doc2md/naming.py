from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from ..utils import safe_name


def figure_filename(doc_name: str, page: int, figure_index: int, title: str, ext: str) -> str:
    """溯源命名规范：文档名_Page{页码}_Fig{图序}_{Figure标题}.ext"""
    title = safe_name(title or "figure", 48).replace(" ", "_")
    doc = safe_name(doc_name, 40).replace(" ", "_")
    ext = ext if ext.startswith(".") else "." + ext
    return f"{doc}_Page{page}_Fig{figure_index}_{title}{ext}"


def normalize_ext(filename: str, default: str = ".png") -> str:
    ext = Path(filename).suffix.lower()
    if ext in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".emf", ".wmf", ".svg"}:
        return ext
    return default


def figure_title(alt: str, fallback: str, limit: int = 48) -> str:
    text = re.sub(r"\s+", " ", (alt or "").strip())
    text = re.sub(
        r"^(图|figure|fig\.?)\s*\d+(?:\s*[-—–]\s*\d+)?[\s:：._-]*",
        "",
        text,
        flags=re.I,
    )
    text = re.split(r"[。；;]|\s\|\s", text)[0].strip()
    if not text:
        text = fallback
    return text[:limit].rstrip("，,、 ")


def figure_source_ref(doc_name: str, page: int, figure_index: int) -> str:
    return f"{doc_name}#page={page}&figure={figure_index}"


def page_anchor(page: int) -> str:
    return f'<a id="page-{page}"></a>'
