from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from ..agent.models import Answer, Citation


@dataclass
class WebDocumentResponse:
    """原文阅读契约；图片保留 ImageInfo 元信息并附本机预览 URL。"""

    doc_id: str
    doc_name: str
    category: str
    page_mode: str
    source: str
    source_uri: str
    text: str
    images: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WebQueryResponse:
    """与 answer_question 契约对齐，供 B/S 前端直接消费。"""

    question: str
    answer: str
    format: str
    citations: List[Citation]
    rounds_used: int
    sufficient: bool
    elapsed_ms: float
    index_version: str
    model_mode: str
    trace: List[Dict[str, Any]] = field(default_factory=list)
    mermaid: str = ""
    warnings: List[str] = field(default_factory=list)

    @classmethod
    def from_answer(cls, answer: Answer) -> "WebQueryResponse":
        return cls(
            question=answer.question,
            answer=answer.answer,
            format=answer.format,
            citations=answer.citations,
            rounds_used=answer.rounds_used,
            sufficient=answer.sufficient,
            elapsed_ms=answer.elapsed_ms,
            index_version=answer.index_version,
            model_mode=answer.model_mode,
            trace=answer.trace,
            mermaid=answer.mermaid,
            warnings=answer.warnings,
        )

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["citations"] = [c.to_dict() for c in self.citations]
        return data
