from __future__ import annotations

import math
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..llm import ModelHub, cosine_similarity
from ..utils import normalize_scores, tokenize
from .indexer import IndexNotFound, load_index, load_sparse, load_vectors
from .models import Chunk, QueryHit, QueryResult, RagIndex

RRF_K = 60


class Retriever:
    """三路混合检索：稠密向量 + BM25 + 稀疏向量，RRF 融合后交叉编码精排。"""

    def __init__(self, index_dir: str | Path, hub: Optional[ModelHub] = None) -> None:
        self.index_dir = Path(index_dir)
        self.hub = hub or ModelHub()
        self.index: RagIndex = load_index(self.index_dir)
        self.dense: Dict[str, List[float]] = load_vectors(self.index_dir)
        self.sparse: Dict[str, Any] = load_sparse(self.index_dir)
        self.chunks: Dict[str, Chunk] = {c.chunk_id: c for c in self.index.chunks}
        self.tf: Dict[str, Dict[str, int]] = {
            cid: {str(token): int(count) for token, count in pairs}
            for cid, pairs in (self.sparse.get("tf") or {}).items()
        }

    def search(
        self,
        query: str,
        top_k: int = 6,
        category: Optional[str] = None,
        route_limit: int = 50,
    ) -> QueryResult:
        started = time.perf_counter()
        warnings: List[str] = []
        chunks = [
            chunk for chunk in self.index.chunks
            if not category or chunk.category == category
        ]
        if not chunks:
            return QueryResult(
                query=query,
                warnings=[f"分类「{category}」下没有可检索的文档"],
                latency_ms=(time.perf_counter() - started) * 1000,
                index_version=self.index.version,
                category=category,
            )
        chunk_ids = {c.chunk_id for c in chunks}
        route_stats: Dict[str, Any] = {}
        route_maps: Dict[str, Dict[str, float]] = {}

        for name, fn in (
            ("dense", self._dense_route),
            ("bm25", self._bm25_route),
            ("sparse", self._sparse_route),
        ):
            route_started = time.perf_counter()
            try:
                scores = fn(query, chunk_ids, route_limit)
                route_maps[name] = scores
                route_stats[name] = {
                    "count": len(scores),
                    "elapsed_ms": round((time.perf_counter() - route_started) * 1000, 3),
                    "degraded": False,
                }
            except Exception as exc:
                route_maps[name] = {}
                route_stats[name] = {
                    "count": 0,
                    "elapsed_ms": round((time.perf_counter() - route_started) * 1000, 3),
                    "degraded": True,
                    "error": str(exc)[:160],
                }
                warnings.append(f"{name} 路召回异常，已自动降级：{exc}")

        if not any(route_maps.values()):
            return QueryResult(
                query=query,
                warnings=warnings + ["三路召回均无结果"],
                latency_ms=(time.perf_counter() - started) * 1000,
                route_stats=route_stats,
                index_version=self.index.version,
                category=category,
            )

        rrf: Dict[str, float] = defaultdict(float)
        route_hits: Dict[str, List[str]] = defaultdict(list)
        for name, scores in route_maps.items():
            for rank, (chunk_id, _) in enumerate(sorted(scores.items(), key=lambda kv: kv[1], reverse=True), start=1):
                if rank > route_limit:
                    break
                rrf[chunk_id] += 1.0 / (RRF_K + rank)
                route_hits[chunk_id].append(name)

        candidates = [
            (cid, score) for cid, score in sorted(rrf.items(), key=lambda kv: kv[1], reverse=True)
            if cid in self.chunks
        ][: max(24, top_k * 4)]
        candidate_chunks = [self.chunks[cid] for cid, _ in candidates]
        try:
            rerank_scores = self.hub.rerank(query, [chunk.text for chunk in candidate_chunks])
        except Exception as exc:
            rerank_scores = []
            warnings.append(f"Reranker 异常，已回退 RRF：{exc}")
        if len(rerank_scores) != len(candidate_chunks):
            rerank_scores = [0.0] * len(candidate_chunks)

        rerank_norm = normalize_scores(rerank_scores)
        rrf_values = [score for _, score in candidates]
        rrf_norm = normalize_scores(rrf_values)
        hits: List[QueryHit] = []
        for pos, (chunk_id, rrf_score) in enumerate(candidates):
            chunk = self.chunks.get(chunk_id)
            if chunk is None:
                continue
            rerank_score = float(rerank_scores[pos])
            final = 0.72 * rerank_norm[pos] + 0.28 * rrf_norm[pos]
            if max(rerank_norm) < 1e-9:
                final = rrf_norm[pos]
            hit = QueryHit(
                chunk=chunk,
                score=final,
                rerank_score=rerank_score,
                dense_score=float(route_maps["dense"].get(chunk_id, 0.0)),
                bm25_score=float(route_maps["bm25"].get(chunk_id, 0.0)),
                sparse_score=float(route_maps["sparse"].get(chunk_id, 0.0)),
                rrf_score=rrf_score,
                routes=sorted(route_hits.get(chunk_id, [])),
            )
            hits.append(hit)
        hits.sort(key=lambda h: (h.score, h.rerank_score, h.rrf_score), reverse=True)
        for rank, hit in enumerate(hits[:top_k], start=1):
            hit.rank = rank
        hits = hits[:top_k]
        if hits:
            route_stats["top_doc"] = hits[0].chunk.doc_name
            route_stats["top_heading"] = hits[0].chunk.heading
        return QueryResult(
            query=query,
            hits=hits,
            latency_ms=(time.perf_counter() - started) * 1000,
            route_stats=route_stats,
            warnings=warnings,
            index_version=self.index.version,
            category=category,
        )

    # ------------------------------------------------------------------ routes
    def _dense_route(self, query: str, allowed: set, limit: int) -> Dict[str, float]:
        model = (
            f"mock-hash-{self.hub.config.embedding_dim}"
            if self.hub.config.mode_for("embed") == "mock"
            else self.hub.config.embed_model
        )
        if self.index.embedding_model != model:
            raise RuntimeError(
                f"查询 Embedding 模型 {model} 与索引模型 {self.index.embedding_model} 不一致；"
                "请使用同一模型，或全量重建索引"
            )
        query_vector = self.hub.embed_one(query)
        if not query_vector:
            raise RuntimeError("查询向量为空")
        expected = {len(vector) for vector in self.dense.values() if vector}
        if expected and len(query_vector) not in expected:
            raise RuntimeError(
                f"查询向量维度 {len(query_vector)} 与索引维度 {sorted(expected)} 不一致；"
                "请确认 Embedding 端点与索引一致，或全量重建索引"
            )
        scores: Dict[str, float] = {}
        for cid in allowed:
            vector = self.dense.get(cid)
            if vector:
                score = cosine_similarity(query_vector, vector)
                if score > 0:
                    scores[cid] = score
        return dict(sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:limit])

    def _bm25_route(self, query: str, allowed: set, limit: int) -> Dict[str, float]:
        tokens = tokenize(query)
        if not tokens:
            return {}
        df = self.sparse.get("df", {})
        total = float(self.sparse.get("N", len(self.index.chunks)) or 1)
        avgdl = float(self.sparse.get("avgdl", 1.0) or 1.0)
        k1, b = 1.5, 0.75
        query_counts = Counter(tokens)
        scores: Dict[str, float] = {}
        for cid in allowed:
            tf = self.tf.get(cid)
            if not tf:
                continue
            doc_len = sum(tf.values())
            score = 0.0
            for token, qtf in query_counts.items():
                freq = tf.get(token, 0)
                if not freq:
                    continue
                doc_freq = df.get(token, 0)
                idf = math.log(1 + (total - doc_freq + 0.5) / (doc_freq + 0.5))
                denom = freq + k1 * (1 - b + b * doc_len / max(1e-9, avgdl))
                score += idf * freq * (k1 + 1) / denom * qtf
            if score > 0:
                scores[cid] = score
        return dict(sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:limit])

    def _sparse_route(self, query: str, allowed: set, limit: int) -> Dict[str, float]:
        tokens = tokenize(query)
        if not tokens:
            return {}
        df = self.sparse.get("df", {})
        total = float(self.sparse.get("N", len(self.index.chunks)) or 1)
        counts = Counter(tokens)
        query_weights: Dict[str, float] = {}
        for token, count in counts.items():
            doc_freq = df.get(token, 0)
            idf = math.log(1 + (total - doc_freq + 0.5) / (doc_freq + 0.5))
            query_weights[token] = (1 + math.log(count)) * idf
        norm = math.sqrt(sum(w * w for w in query_weights.values()))
        if norm < 1e-12:
            return {}
        query_weights = {token: weight / norm for token, weight in query_weights.items()}
        vectors = self.sparse.get("vectors", {})
        scores: Dict[str, float] = {}
        for cid in allowed:
            pairs = vectors.get(cid) or []
            if not pairs:
                continue
            score = sum(query_weights.get(str(token), 0.0) * float(weight) for token, weight in pairs)
            if score > 0:
                scores[cid] = score
        return dict(sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:limit])


def merge_results(results: List[QueryResult], top_k: int = 6) -> QueryResult:
    """Merge multi-query retrieval results by chunk_id (max score + summed RRF)."""
    if not results:
        return QueryResult(query="")
    merged: Dict[str, QueryHit] = {}
    all_queries = []
    warnings: List[str] = []
    for result in results:
        all_queries.append(result.query)
        warnings.extend(result.warnings)
        for hit in result.hits:
            cid = hit.chunk.chunk_id
            if cid not in merged:
                merged[cid] = hit
                continue
            existing = merged[cid]
            existing.rerank_score = max(existing.rerank_score, hit.rerank_score)
            if hit.score > existing.score:
                existing.score = hit.score
            existing.rrf_score += hit.rrf_score
            existing.routes = sorted(set(existing.routes) | set(hit.routes))
            existing.dense_score = max(existing.dense_score, hit.dense_score)
            existing.bm25_score = max(existing.bm25_score, hit.bm25_score)
            existing.sparse_score = max(existing.sparse_score, hit.sparse_score)
    hits = sorted(merged.values(), key=lambda h: (h.score, h.rrf_score), reverse=True)[:top_k]
    for rank, hit in enumerate(hits, start=1):
        hit.rank = rank
    return QueryResult(
        query=" | ".join(all_queries),
        hits=hits,
        latency_ms=sum(r.latency_ms for r in results),
        route_stats={"queries": [r.route_stats for r in results]},
        warnings=list(dict.fromkeys(warnings)),
        index_version=results[0].index_version,
        category=results[0].category,
    )


def retriever_for(index_dir: str | Path, hub: Optional[ModelHub] = None) -> Retriever:
    try:
        return Retriever(index_dir, hub=hub)
    except IndexNotFound:
        raise
