from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..docbase.models import QueryHit, QueryResult
from ..docbase.retriever import Retriever, merge_results
from ..llm import ModelHub
from ..utils import (
    ASCII_RE,
    STOPWORDS,
    split_sentences,
    strip_markdown,
    tokenize,
    truncate,
    unique_tokens,
)
from .models import Answer, Citation

AGENT_SYSTEM_PROMPT = """你是 FGDocQA（基于 AI Agent 的个人本地多模态文档智能问答系统）。
你必须严格遵守 AGENTS.md 的四类约定：
1. 存在性：本仓库配备 DocBase RAG 知识库，回答领域问题前必须先检索；
2. 查询策略：先判断问题类型，技术名词优先精确检索，原理/流程问题优先语义检索；
3. 溯源强制：每个结论必须标注 [n]，并给出文档名、页码、图号；资料不足时明确回答“无据不答”，禁止编造；
4. 产出形式：流程类问题尽量给出 Mermaid 流程图，并提示与原始图片对照。
只依据提供的资料回答，不要引入资料之外的事实。"""

QUESTION_STOP_PHRASES = [
    "请问", "帮我", "麻烦", "我想知道", "想知道", "怎么", "如何", "是什么", "有什么",
    "有哪些", "为什么", "是否", "可以", "需要", "一下", "介绍", "说明", "告诉我",
    "吗", "呢", "吧", "的", "了", "是", "在", "中",
]

FLOW_WORDS = ["流程", "步骤", "怎么走", "如何", "时序", "转发", "接续", "处理过程", "顺序", "走向"]
COMMAND_WORDS = ["命令", "参数", "配置", "操作", "cli", "接口", "指令"]
VERSION_WORDS = ["版本", "特性", "发布", "更新", "变更"]
TROUBLE_WORDS = ["故障", "排查", "失败", "告警", "异常", "定位"]


@dataclass
class AgentConfig:
    top_k: int = 6
    max_rounds: int = 2
    min_rerank: float = 0.20
    min_coverage: float = 0.30


def answer_question(
    question: str,
    index_dir: str | Path,
    max_rounds: Optional[int] = None,
    top_k: Optional[int] = None,
    category: Optional[str] = None,
    hub: Optional[ModelHub] = None,
    config: Optional[AgentConfig] = None,
) -> Answer:
    started = time.perf_counter()
    config = config or AgentConfig()
    top_k = top_k or config.top_k
    max_rounds = max_rounds or config.max_rounds
    hub = hub or ModelHub()
    retriever = Retriever(index_dir, hub=hub)
    question = question.strip()
    if not question:
        raise ValueError("问题不能为空")

    intent = classify_intent(question)
    queries = build_queries(question)
    trace: List[Dict[str, Any]] = []
    warnings: List[str] = []
    rounds = 0
    merged: Optional[QueryResult] = None
    used_queries: List[str] = []

    while rounds < max_rounds:
        rounds += 1
        current_queries = queries if rounds == 1 else [queries[-1]]
        results = []
        for query in current_queries:
            if query not in used_queries:
                used_queries.append(query)
            results.append(retriever.search(query, top_k=top_k, category=category))
        merged = merge_results(results, top_k=top_k)
        warnings.extend(merged.warnings)
        coverage = coverage_of(question, merged)
        top_rerank = merged.hits[0].rerank_score if merged.hits else 0.0
        sufficient = judge(merged, coverage, config)
        trace.append(
            {
                "round": rounds,
                "action": "retrieve",
                "queries": current_queries,
                "hits": len(merged.hits),
                "top_doc": merged.hits[0].chunk.doc_name if merged.hits else "",
                "top_rerank": round(top_rerank, 4),
                "coverage": round(coverage, 4),
                "sufficient": sufficient,
                "latency_ms": round(merged.latency_ms, 2),
            }
        )
        if sufficient:
            break
        if rounds >= max_rounds:
            break
        if not merged.hits or coverage < 0.12:
            break
        expansion = build_expansion(question, merged)
        if not expansion or expansion in queries:
            break
        queries.append(expansion)

    assert merged is not None
    coverage = coverage_of(question, merged)
    sufficient = judge(merged, coverage, config)

    if not sufficient:
        answer_text = _refusal(question, merged, coverage, config)
        trace.append({"action": "refuse", "reason": "无据不答", "coverage": round(coverage, 4)})
        return Answer(
            question=question,
            answer=answer_text,
            format="markdown",
            citations=[],
            rounds_used=rounds,
            sufficient=False,
            queries=used_queries,
            trace=trace,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            index_version=merged.index_version,
            model_mode=hub.mode,
            warnings=list(dict.fromkeys(warnings + hub.warnings)),
        )

    primary = select_primary(merged)
    citations = build_citations(primary)
    llm_answer = _synthesize_with_llm(question, primary, intent, hub)
    if llm_answer:
        answer_text = llm_answer
        mermaid = extract_mermaid(answer_text)
        trace.append({"action": "synthesize", "model": hub.config.chat_model})
    else:
        answer_text, mermaid = _extractive_answer(question, intent, citations, primary)
        trace.append({"action": "synthesize", "model": "extractive-fallback"})

    answer_text, citations = renumber_citations(answer_text, citations)
    return Answer(
        question=question,
        answer=answer_text,
        format="markdown",
        citations=citations,
        rounds_used=rounds,
        sufficient=True,
        queries=used_queries,
        trace=trace,
        mermaid=mermaid,
        elapsed_ms=(time.perf_counter() - started) * 1000,
        index_version=merged.index_version,
        model_mode=hub.mode,
        warnings=list(dict.fromkeys(warnings + hub.warnings)),
    )


def classify_intent(question: str) -> str:
    lowered = question.lower()
    if any(word in lowered for word in COMMAND_WORDS):
        return "command"
    if any(word in lowered for word in FLOW_WORDS):
        return "flow"
    if any(word in lowered for word in VERSION_WORDS):
        return "version"
    if any(word in lowered for word in TROUBLE_WORDS):
        return "troubleshoot"
    if ASCII_RE.search(question):
        return "term"
    return "concept"


def build_queries(question: str) -> List[str]:
    queries = [question]
    cleaned = question
    for phrase in QUESTION_STOP_PHRASES:
        cleaned = cleaned.replace(phrase, " ")
    cleaned = re.sub(r"[，,；;：:（）()【】\[\]？?！!。\.]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    ascii_terms = [m.group(0) for m in ASCII_RE.finditer(question) if len(m.group(0)) >= 3]
    rewritten = cleaned
    if ascii_terms:
        rewritten = (rewritten + " " + " ".join(dict.fromkeys(ascii_terms))).strip()
    if rewritten and rewritten != question and len(rewritten) >= 2:
        queries.append(rewritten)
    return queries


def build_expansion(question: str, result: QueryResult) -> str:
    if not result.hits:
        return ""
    top = result.hits[0].chunk
    parts = [question, top.heading, top.doc_name]
    for hit in result.hits[:3]:
        for token in unique_tokens(hit.chunk.heading):
            if token not in parts and len(token) >= 2:
                parts.append(token)
    return " ".join(dict.fromkeys(p.strip() for p in parts if p and p.strip()))


def coverage_of(question: str, result: QueryResult) -> float:
    q_tokens = set(tokenize(question)) - STOPWORDS
    if not q_tokens:
        return 0.0
    doc_tokens = set()
    for hit in result.hits[:3]:
        doc_tokens |= set(tokenize(hit.chunk.text))
    return len(q_tokens & doc_tokens) / max(1, len(q_tokens))


def judge(result: QueryResult, coverage: float, config: AgentConfig) -> bool:
    if not result.hits:
        return False
    top = result.hits[0]
    if top.rerank_score < config.min_rerank:
        return False
    if coverage < config.min_coverage:
        return False
    return True


def select_primary(result: QueryResult, limit: int = 3) -> List[QueryHit]:
    """Keep only the strongest, non-marginal hits so answers stay on-topic."""
    if not result.hits:
        return []
    top_score = result.hits[0].score
    threshold = max(0.52 * top_score, 0.12)
    primary = [hit for hit in result.hits if hit.score >= threshold][:limit]
    return primary or result.hits[:1]


def build_citations(hits: List[QueryHit]) -> List[Citation]:
    citations: List[Citation] = []
    seen = set()
    for hit in hits:
        if hit.chunk.chunk_id in seen:
            continue
        seen.add(hit.chunk.chunk_id)
        figure = ""
        page = hit.chunk.page
        source_uri = hit.chunk.source_uri
        if hit.chunk.images:
            image = hit.chunk.images[0]
            page = int(image.get("page") or page)
            figure = (
                f"Page {image.get('page', page)} · Fig {image.get('figure_index', '')}"
                f" · {image.get('title', '')}"
            ).strip(" ·")
            source_uri = str(image.get("source_uri") or source_uri)
        citations.append(
            Citation(
                ref=len(citations) + 1,
                doc_id=hit.chunk.doc_id,
                doc_name=hit.chunk.doc_name,
                page=page,
                heading=hit.chunk.heading,
                snippet=truncate(strip_markdown(hit.chunk.text), 140),
                source_uri=source_uri,
                source_path=hit.chunk.source_path,
                figure=figure,
            )
        )
    return citations


# ---------------------------------------------------------------- synthesis
def _clean_line(text: str) -> str:
    text = re.sub(r"<!--.*?-->", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"^\s*>\s?", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -•\t")
    return text


def _numbered_steps(text: str) -> List[str]:
    steps: List[str] = []
    for line in text.splitlines():
        match = re.match(r"^\s*(\d+)[.、)]\s+(.+)$", line)
        if not match:
            continue
        value = _clean_line(match.group(2))
        if len(value) >= 6:
            steps.append(value)
    return steps


def _table_rows(text: str) -> List[str]:
    rows: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [_clean_line(cell) for cell in stripped.strip("|").split("|")]
        cells = [cell for cell in cells if cell]
        if not cells:
            continue
        if all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells):
            continue
        if all(len(cell) <= 4 for cell in cells):
            continue
        if len(cells) == 3:
            rows.append(f"{cells[0]}：{cells[1]}（{cells[2]}）")
        elif len(cells) == 2:
            rows.append(f"{cells[0]}：{cells[1]}")
        else:
            rows.append("；".join(cells))
    return rows


def _find_lead(text: str) -> str:
    for line in text.splitlines():
        value = _clean_line(line)
        if len(value) < 8 or value.startswith(("图 ", "|", "!", "【", "#")):
            continue
        if any(word in value for word in ["如下", "以下", "步骤", "流程", "按"]):
            return value
    return ""


def _sentence_candidates(hit: QueryHit, intent: str, q_tokens: set) -> List[Tuple[float, str]]:
    text = re.sub(r"【[^】]*】", " ", hit.chunk.text)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    out: List[Tuple[float, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("|") or stripped.startswith("!["):
            continue
        if stripped.startswith("图 ") and len(stripped) < 60:
            continue
        if "释义" in stripped and stripped.startswith(">"):
            continue
        for sentence in split_sentences(stripped):
            sentence = sentence.strip(" >")
            if len(sentence) < 12:
                continue
            cleaned = _clean_line(sentence)
            if len(cleaned) < 12:
                continue
            tokens = set(tokenize(cleaned))
            overlap = len(q_tokens & tokens) / max(1, len(q_tokens))
            score = 0.62 * overlap
            if intent == "command" and ("命令" in cleaned or "`" in cleaned or "reset" in cleaned.lower()):
                score += 0.20
            if intent == "flow" and any(word in cleaned for word in ["步骤", "首先", "然后", "接着", "最后", "→"]):
                score += 0.10
            if intent == "version" and any(word in cleaned for word in ["新增", "特性", "支持", "优化", "修复"]):
                score += 0.18
            if intent == "troubleshoot" and any(word in cleaned for word in ["排查", "检查", "确认", "步骤", "原因"]):
                score += 0.16
            if re.match(r"^(发布日期|适用版本|升级方式|版本概述)", cleaned):
                score -= 0.12
            out.append((score, cleaned))
    return out


def _extractive_answer(
    question: str,
    intent: str,
    citations: List[Citation],
    hits: List[QueryHit],
) -> Tuple[str, str]:
    refs = {hit.chunk.chunk_id: index + 1 for index, hit in enumerate(hits)}
    q_tokens = set(tokenize(question)) - STOPWORDS
    lead = ""
    lead_ref = 0
    bullets: List[Tuple[str, int]] = []
    mermaid = ""

    if intent in {"flow", "troubleshoot"}:
        for hit in hits:
            steps = _numbered_steps(hit.chunk.text)
            if len(steps) >= 3:
                lead = _find_lead(hit.chunk.text)
                if not lead:
                    lead = (
                        "针对该问题，建议按以下步骤排查："
                        if intent == "troubleshoot"
                        else "原文给出的处理步骤如下："
                    )
                lead_ref = refs[hit.chunk.chunk_id]
                bullets = [(step, refs[hit.chunk.chunk_id]) for step in steps[:6]]
                break

    if intent == "version" and not bullets:
        for hit in hits:
            rows = _table_rows(hit.chunk.text)
            if len(rows) >= 2:
                version = re.search(r"v?\d+\.\d+(?:\.\d+)?", question)
                label = version.group(0) if version else "该版本"
                lead = f"{label} 的主要新增特性如下："
                lead_ref = refs[hit.chunk.chunk_id]
                bullets = [(row, refs[hit.chunk.chunk_id]) for row in rows[:5]]
                break

    if not bullets and any(word in question for word in ["参数", "配置", "默认值"]):
        best: Optional[Tuple[float, str, int]] = None
        for hit in hits:
            for score, sentence in _sentence_candidates(hit, intent, q_tokens):
                if any(word in sentence for word in ["参数", "用于", "包含", "作用"]):
                    if best is None or score > best[0]:
                        best = (score, sentence, refs[hit.chunk.chunk_id])
        for hit in hits:
            rows = _table_rows(hit.chunk.text)
            if len(rows) >= 2:
                label = re.sub(r"^\d+(?:\.\d+)*\s*", "", hit.chunk.heading.split(" / ")[-1])
                label = label.replace("参数", "").strip()
                lead = best[1] if best else f"{label} 的关键参数如下："
                lead_ref = best[2] if best else refs[hit.chunk.chunk_id]
                bullets = [(row, refs[hit.chunk.chunk_id]) for row in rows[:5]]
                break

    if intent == "command" and not bullets:
        best: Optional[Tuple[float, str, int]] = None
        for hit in hits:
            for score, sentence in _sentence_candidates(hit, intent, q_tokens):
                if best is None or score > best[0]:
                    best = (score, sentence, refs[hit.chunk.chunk_id])
        if best:
            lead = best[1]
            lead_ref = best[2]
            for hit in hits:
                for sentence in _numbered_steps(hit.chunk.text)[:2]:
                    bullets.append((sentence, refs[hit.chunk.chunk_id]))
            for other in hits:
                for score, sentence in _sentence_candidates(other, intent, q_tokens):
                    if len(bullets) >= 3:
                        break
                    if sentence != lead and any(
                        word in sentence for word in ["执行前", "必须", "中断", "注意", "禁止", "需要"]
                    ):
                        bullets.append((sentence, refs[other.chunk.chunk_id]))
            lead = lead if lead.endswith(("。", "：", "！", "？")) else lead + "。"

    if not lead:
        candidates: List[Tuple[float, str, int]] = []
        for hit in hits:
            for score, sentence in _sentence_candidates(hit, intent, q_tokens):
                candidates.append((score, sentence, refs[hit.chunk.chunk_id]))
        candidates.sort(key=lambda item: (item[0], -len(item[1])), reverse=True)
        selected: List[Tuple[float, str, int]] = []
        seen = set()
        for item in candidates:
            prefix = re.sub(r"\W+", "", item[1])[:24]
            if prefix in seen:
                continue
            seen.add(prefix)
            selected.append(item)
            if len(selected) >= 4:
                break
        if not selected:
            return "**无据不答**：检索到的片段不足以抽取有效结论。", ""
        lead = truncate(selected[0][1], 190)
        lead_ref = selected[0][2]
        bullets = [(truncate(item[1], 200), item[2]) for item in selected[1:3]]

    if intent == "flow":
        for hit in hits:
            mermaid = extract_mermaid(hit.chunk.text)
            if mermaid:
                break

    if not bullets:
        bullets = [("相关原文与页码见下方溯源列表。", citations[0].ref if citations else 1)]

    conclusion_ref = lead_ref or bullets[0][1]
    lines = [f"**结论**：{lead} [{conclusion_ref}]", "", "**关键依据**"]
    for index, (text, ref) in enumerate(bullets[:6], start=1):
        lines.append(f"{index}. {truncate(text, 220)} [{ref}]")

    if intent == "flow" and mermaid:
        lines += ["", "**流程重绘（Mermaid）**", "", "```mermaid", mermaid.strip(), "```"]
        image_hit = next((h for h in hits if h.chunk.images), None)
        if image_hit:
            image = image_hit.chunk.images[0]
            lines.append(
                f"\n> 上图按知识库中的 Mermaid 源重绘，可与《{image_hit.chunk.doc_name}》"
                f"Page {image.get('page')} · Fig {image.get('figure_index')}"
                f"「{image.get('title', '')}」原图对照验证。"
            )

    lines += ["", "**溯源**"]
    for citation in citations:
        lines.append(
            f"- [{citation.ref}] 《{citation.doc_name}》 · Page {citation.page}"
            + (f" · {citation.heading}" if citation.heading else "")
            + f" · [打开原文]({citation.source_uri})"
        )
    return "\n".join(lines), mermaid


def renumber_citations(answer_text: str, citations: List[Citation]) -> Tuple[str, List[Citation]]:
    """Keep only citations used in the answer body and number them by first use."""
    marker = "\n**溯源**"
    if marker in answer_text:
        body, _, _ = answer_text.partition(marker)
    else:
        body = answer_text
    by_ref = {citation.ref: citation for citation in citations}
    order: List[int] = []
    for match in re.finditer(r"\[(\d+)\]", body):
        ref = int(match.group(1))
        if ref in by_ref and ref not in order:
            order.append(ref)
    if not order:
        return answer_text, citations
    mapping = {old: new for new, old in enumerate(order, start=1)}

    def replace(match: re.Match) -> str:
        ref = int(match.group(1))
        return f"[{mapping[ref]}]" if ref in mapping else match.group(0)

    body = re.sub(r"\[(\d+)\]", replace, body).rstrip()
    result: List[Citation] = []
    lines = ["**溯源**"]
    for old in order:
        citation = by_ref[old]
        citation.ref = mapping[old]
        result.append(citation)
        lines.append(
            f"- [{citation.ref}] 《{citation.doc_name}》 · Page {citation.page}"
            + (f" · {citation.heading}" if citation.heading else "")
            + f" · [打开原文]({citation.source_uri})"
        )
    return body + "\n\n" + "\n".join(lines), result


def _synthesize_with_llm(
    question: str,
    hits: List[QueryHit],
    intent: str,
    hub: ModelHub,
) -> Optional[str]:
    if hub.is_mock_for("chat") or not hits:
        return None
    context_parts = []
    for index, hit in enumerate(hits, start=1):
        chunk = hit.chunk
        context_parts.append(
            f"[{index}] 《{chunk.doc_name}》 Page {chunk.page} · {chunk.heading}\n{chunk.text}"
        )
    user = (
        f"问题：{question}\n\n"
        f"问题类型：{intent}\n\n"
        f"检索资料：\n" + "\n\n".join(context_parts) + "\n\n"
        "请用中文 Markdown 回答，结论必须标注 [n]，并在结尾给出溯源说明。"
        "若资料不足，只回答“无据不答”并说明缺少什么。"
    )
    return hub.chat(
        [
            {"role": "system", "content": AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        temperature=0.15,
        max_tokens=1400,
    )


def extract_mermaid(text: str) -> str:
    match = re.search(r"```mermaid\s*(.*?)```", text or "", re.S)
    return match.group(1).strip() if match else ""


def _refusal(question: str, result: QueryResult, coverage: float, config: AgentConfig) -> str:
    top = max(result.hits, key=lambda hit: hit.rerank_score) if result.hits else None
    lines = [
        "**无据不答**：当前知识库中没有检索到足以支撑该问题的原文依据。",
        "",
        f"- 已检索：{len(result.hits)} 个文本块，最高语义覆盖率 {coverage:.0%}（阈值 {config.min_coverage:.0%}）",
    ]
    if top:
        if top.rerank_score > 0:
            detail = f"相关度 {top.rerank_score:.2f}"
        elif top.dense_score > 0:
            detail = f"向量相似度 {top.dense_score:.2f}"
        else:
            detail = "未达引用阈值"
        lines.append(
            f"- 最接近的片段：《{top.chunk.doc_name}》Page {top.chunk.page}"
            f" · {top.chunk.heading}（{detail}）"
        )
    lines += [
        "- 建议：补充相关文档后重新 build，或换用更具体的文档名/术语提问。",
        "",
        "> AGENTS.md 约定：必须先检索、无据不答；回答必须附原文链接、文档名、页码、图号。",
    ]
    return "\n".join(lines)
