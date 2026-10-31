from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

CST = timezone(timedelta(hours=8))

ASCII_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.\-/#]*")
CJK_RE = re.compile(r"[\u4e00-\u9fff]+")

STOPWORDS = {
    "的", "了", "和", "与", "及", "在", "是", "有", "对", "为", "从", "中", "上", "下",
    "这", "那", "怎么", "如何", "什么", "一个", "我们", "可以", "需要", "进行", "通过",
    "使用", "以及", "并且", "但是", "如果", "因为", "所以", "这个", "那个", "一下",
    "请问", "帮我", "介绍", "说明", "是否", "哪些", "为什么", "时候", "问题", "什么",
    "吗", "呢", "吧", "啊", "与", "或", "等", "被", "把", "让", "给", "就", "都",
}


def now_iso() -> str:
    return datetime.now(CST).replace(microsecond=0).isoformat()


def today() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d")


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def read_text(path: str | Path, default: str = "") -> str:
    p = Path(path)
    if not p.exists():
        return default
    return p.read_text(encoding="utf-8", errors="replace")


def write_text(path: str | Path, text: str) -> Path:
    p = Path(path)
    ensure_dir(p.parent)
    p.write_text(text, encoding="utf-8")
    return p


def read_json(path: str | Path, default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def write_json(path: str | Path, obj: Any, indent: int = 2) -> Path:
    p = Path(path)
    ensure_dir(p.parent)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=indent), encoding="utf-8")
    return p


def sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def file_sha1(path: str | Path, block: int = 1 << 20) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        while True:
            data = fh.read(block)
            if not data:
                break
            h.update(data)
    return h.hexdigest()


def safe_name(text: str, max_len: int = 80) -> str:
    """Make a filesystem-safe name while keeping Chinese characters."""
    text = re.sub(r'[\\/:*?"<>|\n\r\t]+', "_", str(text))
    text = re.sub(r"\s+", " ", text).strip(" ._")
    text = text.strip()
    if len(text) > max_len:
        text = text[:max_len].rstrip(" ._")
    return text or "untitled"


def truncate(text: str, limit: int = 160, suffix: str = "…") -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + suffix


def tokenize(text: str) -> List[str]:
    """Mixed Chinese/English tokenizer: ASCII words + Chinese character bigrams.

    Kept dependency-free and deterministic so the same tokenizer is used by
    BM25, sparse retrieval, hashing embeddings and the offline reranker.
    """
    if not text:
        return []
    text = text.lower()
    tokens: List[str] = []
    for match in ASCII_RE.finditer(text):
        token = match.group(0).strip("._-/")
        if token and token not in STOPWORDS:
            tokens.append(token)
    for run in CJK_RE.findall(text):
        run = run.strip()
        if not run:
            continue
        if len(run) == 1:
            if run not in STOPWORDS:
                tokens.append(run)
            continue
        for i in range(len(run) - 1):
            gram = run[i : i + 2]
            if gram not in STOPWORDS:
                tokens.append(gram)
    return tokens


def unique_tokens(text: str) -> List[str]:
    seen = set()
    out = []
    for token in tokenize(text):
        if token not in seen:
            seen.add(token)
            out.append(token)
    return out


def split_sentences(text: str) -> List[str]:
    """Split Chinese/English prose into sentences, preserving Markdown lines."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.M)
    parts = re.split(r"(?<=[。！？!?；;])\s*|\n+", text)
    out = []
    for part in parts:
        part = part.strip(" \t-•*`")
        if len(part) >= 6:
            out.append(part)
    return out


def strip_markdown(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"[*_`>#]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_front_matter(text: str) -> Tuple[Dict[str, Any], str]:
    """Parse the tiny YAML-ish front matter emitted by doc2md."""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, text
    meta: Dict[str, Any] = {}
    for line in lines[1:end]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            meta[key] = [v.strip() for v in value[1:-1].split(",") if v.strip()]
        elif value.isdigit():
            meta[key] = int(value)
        else:
            meta[key] = value.strip('"').strip("'")
    return meta, "\n".join(lines[end + 1 :]).lstrip("\n")


def to_front_matter(meta: Dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in meta.items():
        if isinstance(value, (list, tuple)):
            value = "[" + ", ".join(str(v) for v in value) + "]"
        lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def normalize_scores(scores: List[float]) -> List[float]:
    if not scores:
        return []
    lo, hi = min(scores), max(scores)
    if hi - lo < 1e-9:
        return [0.0 for _ in scores]
    return [(s - lo) / (hi - lo) for s in scores]


def file_uri(path: str | Path, page: Optional[int] = None, figure: Optional[int] = None) -> str:
    uri = Path(path).resolve().as_uri()
    fragment = []
    if page:
        fragment.append(f"page={page}")
    if figure:
        fragment.append(f"figure={figure}")
    if fragment:
        uri += "#" + "&".join(fragment)
    return uri


def relative_posix(path: str | Path, base: str | Path) -> str:
    try:
        return Path(path).resolve().relative_to(Path(base).resolve()).as_posix()
    except ValueError:
        return Path(path).as_posix()


def format_ms(ms: float) -> str:
    if ms < 1000:
        return f"{ms:.0f} ms"
    return f"{ms / 1000:.2f} s"


def format_bytes(size: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def md_link_target(path: str) -> str:
    """Quote a Markdown link target when it contains spaces or CJK punctuation."""
    if re.search(r"[\s()（）]", path):
        return "<" + path + ">"
    return path


def url_quote_path(path: str) -> str:
    return quote(path, safe="/")
