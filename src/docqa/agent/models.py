from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Citation:
    ref: int
    doc_id: str
    doc_name: str
    page: int
    heading: str
    snippet: str
    source_uri: str
    source_path: str = ""
    figure: str = ""

    def label(self) -> str:
        return f"《{self.doc_name}》 · Page {self.page}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Answer:
    """answer_question 的稳定契约：answer / format / citations / rounds_used。"""

    question: str
    answer: str
    format: str = "markdown"
    citations: List[Citation] = field(default_factory=list)
    rounds_used: int = 1
    sufficient: bool = True
    queries: List[str] = field(default_factory=list)
    trace: List[Dict[str, Any]] = field(default_factory=list)
    mermaid: str = ""
    elapsed_ms: float = 0.0
    index_version: str = ""
    model_mode: str = "mock"
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "answer": self.answer,
            "format": self.format,
            "citations": [c.to_dict() for c in self.citations],
            "rounds_used": self.rounds_used,
            "sufficient": self.sufficient,
            "queries": self.queries,
            "trace": self.trace,
            "mermaid": self.mermaid,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "index_version": self.index_version,
            "model_mode": self.model_mode,
            "warnings": self.warnings,
        }
