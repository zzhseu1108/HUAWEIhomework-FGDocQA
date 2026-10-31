from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from ..llm import ModelHub
from ..utils import now_iso, read_json, write_json
from .retriever import Retriever


def evaluate(
    index_dir: str | Path,
    test_set: str | Path | List[Dict[str, Any]],
    top_k: int = 6,
    hub: Optional[ModelHub] = None,
    report_path: Optional[str | Path] = None,
) -> Dict[str, Any]:
    cases = read_json(test_set, default=[]) if isinstance(test_set, (str, Path)) else test_set
    if not isinstance(cases, list) or not cases:
        raise ValueError("测试集为空或格式错误：应为 [{question, expected_docs, ...}]")
    retriever = Retriever(index_dir, hub=hub)
    rows: List[Dict[str, Any]] = []
    for case in cases:
        question = str(case.get("question", "")).strip()
        expected = [str(x) for x in case.get("expected_docs", [])]
        keywords = [str(x).lower() for x in case.get("expected_keywords", [])]
        category = case.get("category")
        result = retriever.search(question, top_k=top_k, category=category)
        found = set()
        first_rank = 0
        for hit in result.hits:
            doc_name = hit.chunk.doc_name
            if any(exp == doc_name or exp in doc_name or doc_name in exp for exp in expected):
                found.add(doc_name)
                if not first_rank:
                    first_rank = hit.rank
        recall = len(found) / max(1, len(expected)) if expected else 0.0
        hit_rate = 1.0 if first_rank else 0.0
        rr = 1.0 / first_rank if first_rank else 0.0
        joined = "\n".join(hit.chunk.text.lower() for hit in result.hits)
        matched_keywords = [kw for kw in keywords if kw in joined]
        rows.append(
            {
                "question": question,
                "expected_docs": expected,
                "top_docs": [hit.chunk.doc_name for hit in result.hits],
                "top_rank": first_rank,
                "hit": bool(first_rank),
                "recall_at_k": round(recall, 4),
                "rr": round(rr, 4),
                "keyword_hit_rate": round(len(matched_keywords) / max(1, len(keywords)), 4) if keywords else None,
                "latency_ms": round(result.latency_ms, 3),
                "warnings": result.warnings,
            }
        )
    total = len(rows)
    summary = {
        "cases": total,
        "top_k": top_k,
        f"recall@{top_k}": round(sum(row["recall_at_k"] for row in rows) / total, 4),
        "hit_rate": round(sum(1.0 for row in rows if row["hit"]) / total, 4),
        "mrr": round(sum(row["rr"] for row in rows) / total, 4),
        "keyword_hit_rate": round(
            sum(row["keyword_hit_rate"] or 0.0 for row in rows) / total, 4
        ),
        "avg_latency_ms": round(sum(row["latency_ms"] for row in rows) / total, 3),
        "evaluated_at": now_iso(),
        "index_version": retriever.index.version,
    }
    report = {"summary": summary, "cases": rows}
    if report_path:
        write_json(report_path, report)
    return report
