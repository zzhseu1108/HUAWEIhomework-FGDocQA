from __future__ import annotations

import json
import math
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..llm import ModelHub
from ..utils import (
    ensure_dir,
    file_sha1,
    format_ms,
    now_iso,
    parse_front_matter,
    read_json,
    read_text,
    sha1_text,
    tokenize,
    write_json,
)
from .chunker import chunk_markdown
from .models import Chunk, RagIndex

ProgressFn = Optional[Callable[[int, int, str], None]]


class IndexNotFound(RuntimeError):
    pass


def _load_manifest(doc_dir: Path) -> List[Dict[str, Any]]:
    data = read_json(doc_dir / "manifest.json", default={})
    images = data.get("images", []) if isinstance(data, dict) else []
    return images if isinstance(images, list) else []


def _sparse_vector(tokens: List[str], df: Dict[str, int], total_docs: int) -> Tuple[List[List[Any]], Dict[str, int]]:
    counts = Counter(tokens)
    weights: List[List[Any]] = []
    for token, count in counts.items():
        idf = math.log(1 + (total_docs - df.get(token, 0) + 0.5) / (df.get(token, 0) + 0.5))
        weights.append([token, (1 + math.log(count)) * idf])
    norm = math.sqrt(sum(w * w for _, w in weights))
    if norm > 1e-12:
        weights = [[token, weight / norm] for token, weight in weights]
    return weights, dict(counts)


def build_index(
    md_dir: str | Path,
    index_dir: str | Path = "data/index",
    incremental: bool = False,
    chunk_size: int = 1200,
    overlap: int = 120,
    hub: Optional[ModelHub] = None,
    progress: ProgressFn = None,
) -> Dict[str, Any]:
    started = time.perf_counter()
    md_root = Path(md_dir).resolve()
    if not md_root.exists():
        raise FileNotFoundError(f"Markdown 目录不存在：{md_root}")
    index_path = ensure_dir(index_dir)
    hub = hub or ModelHub()
    started_mock = hub.is_mock_for("embed")
    effective_embed_model = (
        f"mock-hash-{hub.config.embedding_dim}" if started_mock else hub.config.embed_model
    )
    effective_rerank_model = (
        "mock-lexical" if hub.is_mock_for("rerank") else hub.config.rerank_model
    )

    def report(current: int, total: int, message: str) -> None:
        if progress:
            progress(current, total, message)

    files = sorted(
        p for p in md_root.rglob("*.md")
        if p.is_file() and not p.name.startswith(".") and p.name not in {"AGENTS.md", "CLAUDE.md"}
    )
    report(0, max(1, len(files)), f"扫描到 {len(files)} 个 Markdown 文档")

    old_index: Optional[RagIndex] = None
    old_dense: Dict[str, List[float]] = {}
    if incremental:
        try:
            old_index = load_index(index_path)
            store = read_json(index_path / "vectors.json", default={}) or {}
            if store.get("model") == effective_embed_model:
                old_dense = store.get("vectors", {})
        except IndexNotFound:
            old_index = None
    if old_index is None:
        old_dense = {}

    old_chunks_by_doc: Dict[str, List[Chunk]] = {}
    if old_index:
        for chunk in old_index.chunks:
            old_chunks_by_doc.setdefault(chunk.doc_id, []).append(chunk)
    old_doc_by_id = {d.get("doc_id"): d for d in (old_index.docs if old_index else [])}

    docs: List[Dict[str, Any]] = []
    chunks: List[Chunk] = []
    added = updated = removed = unchanged = 0
    current_ids = set()

    for number, md_file in enumerate(files, start=1):
        report(number - 1, max(1, len(files)), f"切分 {md_file.name}")
        text = read_text(md_file)
        meta, _ = parse_front_matter(text)
        rel_path = md_file.relative_to(md_root).as_posix()
        doc_id = str(meta.get("doc_id") or sha1_text(rel_path)[:12])
        if doc_id in current_ids:
            doc_id = sha1_text(rel_path + str(number))[:12]
        current_ids.add(doc_id)
        doc_hash = sha1_text(text)
        doc_name = str(meta.get("doc_name") or md_file.stem)
        category = str(meta.get("category") or md_file.parent.name or "未分类")
        source = str(meta.get("source") or md_file)
        pages = int(meta.get("pages") or 1)
        source_uri = str(meta.get("source_uri") or "")

        old_doc = old_doc_by_id.get(doc_id)
        if incremental and old_doc and old_doc.get("hash") == doc_hash and doc_id in old_chunks_by_doc:
            doc_chunks = old_chunks_by_doc[doc_id]
            unchanged += 1
        else:
            images = _load_manifest(md_file.parent)
            doc_meta = {
                "doc_id": doc_id,
                "doc_name": doc_name,
                "category": category,
                "source": source,
                "source_uri": source_uri,
                "pages": pages,
                "page_mode": str(meta.get("page_mode") or "exact"),
            }
            doc_chunks = chunk_markdown(text, doc_meta, images, chunk_size=chunk_size, overlap=overlap)
            if old_doc:
                updated += 1
            else:
                added += 1
        chunks.extend(doc_chunks)
        docs.append(
            {
                "doc_id": doc_id,
                "doc_name": doc_name,
                "category": category,
                "path": rel_path,
                "abs_path": str(md_file),
                "source": source,
                "source_uri": source_uri,
                "pages": pages,
                "page_mode": str(meta.get("page_mode") or "exact"),
                "hash": doc_hash,
                "chunks": len(doc_chunks),
                "images": int(meta.get("images") or 0),
                "updated_at": now_iso(),
            }
        )

    if old_index:
        old_ids = set(old_doc_by_id)
        removed = len(old_ids - current_ids)

    report(len(files), max(1, len(files)), "向量化文本块")
    chunks.sort(key=lambda c: (c.doc_name, c.order))
    missing = [c for c in chunks if c.chunk_id not in old_dense]
    dense = {cid: vec for cid, vec in old_dense.items() if cid in {c.chunk_id for c in chunks}}
    if missing:
        batch_size = 16
        for start in range(0, len(missing), batch_size):
            batch = missing[start : start + batch_size]
            vectors = hub.embed([chunk.text for chunk in batch])
            for chunk, vector in zip(batch, vectors):
                dense[chunk.chunk_id] = vector
            report(
                min(len(files), len(files)),
                max(1, len(files)),
                f"向量化 {min(start + batch_size, len(missing))}/{len(missing)}",
            )
    if not dense:
        raise RuntimeError("索引为空：没有可向量化的文本块")
    if not started_mock and hub.is_mock_for("embed"):
        raise RuntimeError(
            "Embedding/模型端点在建库过程中失败，已中止建库以避免 Mock 与真实向量混用；"
            "请检查 DOCQA_BASE_URL / DOCQA_API_KEY 后重试。"
        )

    report(len(files), max(1, len(files)), "构建 BM25 / 稀疏索引")
    total_docs = len(chunks)
    df: Dict[str, int] = {}
    token_lists: Dict[str, List[str]] = {}
    for chunk in chunks:
        tokens = tokenize(chunk.text)
        token_lists[chunk.chunk_id] = tokens
        for token in set(tokens):
            df[token] = df.get(token, 0) + 1
    avgdl = sum(len(tokens) for tokens in token_lists.values()) / max(1, total_docs)

    sparse_vectors: Dict[str, List[List[Any]]] = {}
    tf_vectors: Dict[str, List[List[Any]]] = {}
    for chunk in chunks:
        weights, counts = _sparse_vector(token_lists[chunk.chunk_id], df, total_docs)
        sparse_vectors[chunk.chunk_id] = weights
        tf_vectors[chunk.chunk_id] = [[token, count] for token, count in counts.items()]

    report(len(files), max(1, len(files)), "写入索引文件")
    version = time.strftime("%Y.%m.%d-%H%M%S")
    created = old_index.created_at if old_index else now_iso()
    categories: Dict[str, int] = {}
    for doc in docs:
        categories[doc["category"]] = categories.get(doc["category"], 0) + 1
    index = RagIndex(
        version=version,
        created_at=created,
        updated_at=now_iso(),
        embedding_model=effective_embed_model,
        rerank_model=effective_rerank_model,
        chunk_size=chunk_size,
        overlap=overlap,
        docs=docs,
        chunks=chunks,
        stats={
            "doc_count": len(docs),
            "chunk_count": len(chunks),
            "image_count": sum(doc["images"] for doc in docs),
            "total_chars": sum(chunk.char_count for chunk in chunks),
            "avg_chars": round(sum(chunk.char_count for chunk in chunks) / max(1, len(chunks)), 1),
            "categories": categories,
            "embedding_dim": len(next(iter(dense.values()))) if dense else 0,
            "mode": "mock" if hub.is_mock_for("embed") else "http",
            "sparse_model": "tfidf-local",
            "sparse_kind": "lexical",
        },
    )
    write_json(index_path / "index.json", index.to_dict())
    write_json(
        index_path / "vectors.json",
        {
            "model": effective_embed_model,
            "dim": index.stats["embedding_dim"],
            "vectors": dense,
        },
    )
    write_json(
        index_path / "sparse.json",
        {
            "model": "tfidf-local",
            "kind": "lexical",
            "N": total_docs,
            "avgdl": round(avgdl, 4),
            "df": df,
            "vectors": sparse_vectors,
            "tf": tf_vectors,
        },
    )
    elapsed = (time.perf_counter() - started) * 1000
    report(len(files), max(1, len(files)), f"建库完成 · {format_ms(elapsed)}")
    return {
        "version": version,
        "index_dir": str(index_path),
        "docs": len(docs),
        "chunks": len(chunks),
        "added_docs": added,
        "updated_docs": updated,
        "removed_docs": removed,
        "unchanged_docs": unchanged,
        "images": index.stats["image_count"],
        "elapsed_ms": round(elapsed, 2),
        "mode": index.stats["mode"],
        "warnings": hub.warnings,
    }


def load_index(index_dir: str | Path) -> RagIndex:
    path = Path(index_dir) / "index.json"
    data = read_json(path)
    if not data:
        raise IndexNotFound(f"索引不存在：{path}，请先运行 docqa build")
    index = RagIndex.from_dict(data)
    if not index.chunks:
        raise IndexNotFound(f"索引为空：{path}")
    return index


def load_vectors(index_dir: str | Path) -> Dict[str, List[float]]:
    data = read_json(Path(index_dir) / "vectors.json", default={})
    return data.get("vectors", {}) if isinstance(data, dict) else {}


def load_sparse(index_dir: str | Path) -> Dict[str, Any]:
    data = read_json(Path(index_dir) / "sparse.json", default={})
    if not data:
        raise IndexNotFound(f"稀疏索引不存在：{Path(index_dir) / 'sparse.json'}")
    return data
