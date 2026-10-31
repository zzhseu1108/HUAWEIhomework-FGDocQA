from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Chunk:
    """知识库最小检索单元，所有字段均携带溯源信息（字段只增不改）。"""

    chunk_id: str
    doc_id: str
    doc_name: str
    heading: str
    text: str
    page: int
    category: str
    source_path: str
    source_uri: str
    order: int = 0
    images: List[Dict[str, Any]] = field(default_factory=list)
    content_hash: str = ""
    char_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Chunk":
        fields = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in fields})


@dataclass
class RagIndex:
    """索引元数据 + chunk 清单；向量/稀疏矩阵分别落盘。"""

    version: str
    created_at: str
    updated_at: str
    embedding_model: str
    rerank_model: str
    chunk_size: int
    overlap: int
    docs: List[Dict[str, Any]] = field(default_factory=list)
    chunks: List[Chunk] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    schema_version: str = "1.0"

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["chunks"] = [c.to_dict() for c in self.chunks]
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RagIndex":
        fields = cls.__dataclass_fields__.keys()
        chunks = [Chunk.from_dict(c) for c in data.get("chunks", [])]
        payload = {k: v for k, v in data.items() if k in fields and k != "chunks"}
        return cls(chunks=chunks, **payload)


@dataclass
class QueryHit:
    chunk: Chunk
    score: float = 0.0
    rerank_score: float = 0.0
    dense_score: float = 0.0
    bm25_score: float = 0.0
    sparse_score: float = 0.0
    rrf_score: float = 0.0
    rank: int = 0
    routes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk": self.chunk.to_dict(),
            "score": round(self.score, 6),
            "rerank_score": round(self.rerank_score, 6),
            "dense_score": round(self.dense_score, 6),
            "bm25_score": round(self.bm25_score, 6),
            "sparse_score": round(self.sparse_score, 6),
            "rrf_score": round(self.rrf_score, 6),
            "rank": self.rank,
            "routes": self.routes,
        }


@dataclass
class QueryResult:
    query: str
    hits: List[QueryHit] = field(default_factory=list)
    latency_ms: float = 0.0
    route_stats: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    index_version: str = ""
    category: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "hits": [h.to_dict() for h in self.hits],
            "latency_ms": round(self.latency_ms, 3),
            "route_stats": self.route_stats,
            "warnings": self.warnings,
            "index_version": self.index_version,
            "category": self.category,
        }
