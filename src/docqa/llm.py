from __future__ import annotations

import base64
import hashlib
import json
import math
import mimetypes
import os
import re
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

from .utils import clamp, strip_markdown, tokenize, truncate

DEFAULT_CHAT_MODEL = "GLM-5.1"
DEFAULT_VL_MODEL = "Qwen3.6-27B-VL"
DEFAULT_EMBED_MODEL = "Qwen3-Embedding-8B"
DEFAULT_RERANK_MODEL = "Qwen3-Reranker-8B"


@dataclass
class ModelConfig:
    """四类模型服务的统一端点配置；未配置时自动进入可复现的 Mock 模式。"""

    mode: str = "mock"
    base_url: str = ""
    api_key: str = field(default="", repr=False)
    chat_model: str = DEFAULT_CHAT_MODEL
    vl_model: str = DEFAULT_VL_MODEL
    embed_model: str = DEFAULT_EMBED_MODEL
    rerank_model: str = DEFAULT_RERANK_MODEL
    timeout: float = 30.0
    embedding_dim: int = 512
    embed_mode: str = ""
    rerank_mode: str = ""
    chat_mode: str = ""
    vl_mode: str = ""
    chat_base_url: str = ""
    chat_api_key: str = field(default="", repr=False)

    @classmethod
    def from_env(cls) -> "ModelConfig":
        return cls(
            mode=os.getenv("DOCQA_MODEL_MODE", "mock").strip().lower(),
            base_url=os.getenv("DOCQA_BASE_URL", "").strip(),
            api_key=os.getenv("DOCQA_API_KEY", "").strip(),
            chat_model=os.getenv("DOCQA_CHAT_MODEL", DEFAULT_CHAT_MODEL).strip(),
            vl_model=os.getenv("DOCQA_VL_MODEL", DEFAULT_VL_MODEL).strip(),
            embed_model=os.getenv("DOCQA_EMBED_MODEL", DEFAULT_EMBED_MODEL).strip(),
            rerank_model=os.getenv("DOCQA_RERANK_MODEL", DEFAULT_RERANK_MODEL).strip(),
            timeout=float(os.getenv("DOCQA_TIMEOUT", "30") or 30),
            embedding_dim=int(os.getenv("DOCQA_EMBED_DIM", "512") or 512),
            embed_mode=os.getenv("DOCQA_EMBED_MODE", "").strip().lower(),
            rerank_mode=os.getenv("DOCQA_RERANK_MODE", "").strip().lower(),
            chat_mode=os.getenv("DOCQA_CHAT_MODE", "").strip().lower(),
            vl_mode=os.getenv("DOCQA_VL_MODE", "").strip().lower(),
            chat_base_url=os.getenv("DOCQA_CHAT_BASE_URL", "").strip(),
            chat_api_key=os.getenv("DOCQA_CHAT_API_KEY", "").strip(),
        )

    def mode_for(self, capability: str) -> str:
        if capability not in {"embed", "rerank", "chat", "vl"}:
            raise ValueError("未知模型能力")
        mode = getattr(self, capability + "_mode") or self.mode
        if mode not in {"mock", "http"}:
            raise ValueError(f"{capability} 模式必须为 mock 或 http")
        return mode

    def base_url_for(self, capability: str) -> str:
        return (self.chat_base_url or self.base_url) if capability == "chat" else self.base_url

    def api_key_for(self, capability: str) -> str:
        return (self.chat_api_key or self.api_key) if capability == "chat" else self.api_key

    def public_base_url(self, capability: str = "embed") -> str:
        """Only expose the origin/path; URL credentials and query are private."""
        try:
            parts = urlsplit(self.base_url_for(capability))
            host = parts.hostname or ""
            if ":" in host:
                host = "[" + host + "]"
            if parts.port:
                host += ":" + str(parts.port)
            return urlunsplit((parts.scheme, host, parts.path, "", ""))
        except ValueError:
            return "（无效地址）"

    def api(self, path: str, capability: str = "embed") -> str:
        base = self.base_url_for(capability).rstrip("/")
        return base + "/" + path.lstrip("/")


def _hash_embedding(text: str, dim: int = 512) -> List[float]:
    """Deterministic, dependency-free hashing embedding used in Mock mode.

    It preserves lexical overlap (and therefore gives a usable offline demo),
    but it is NOT a semantic model. Production should point DOCQA_BASE_URL at
    Qwen3-Embedding-8B.
    """
    vector = [0.0] * dim
    counts = Counter(tokenize(text))
    if not counts:
        return vector
    for token, count in counts.items():
        digest = hashlib.md5(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "little") % dim
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[index] += sign * (1.0 + math.log(count))
    norm = math.sqrt(sum(v * v for v in vector))
    if norm > 1e-12:
        vector = [v / norm for v in vector]
    return vector


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b:
        return 0.0
    size = min(len(a), len(b))
    dot = sum(a[i] * b[i] for i in range(size))
    na = math.sqrt(sum(v * v for v in a[:size]))
    nb = math.sqrt(sum(v * v for v in b[:size]))
    if na < 1e-12 or nb < 1e-12:
        return 0.0
    return dot / (na * nb)


class ModelHub:
    """四类模型能力的统一入口，支持真实端点与 Mock 双模式。

    端点约定（OpenAI-compatible）：
        POST {base}/chat/completions   Chat / VL
        POST {base}/embeddings         Embedding
        POST {base}/rerank             Reranker
    """

    def __init__(self, config: Optional[ModelConfig] = None) -> None:
        self.config = config or ModelConfig.from_env()
        self.warnings: List[str] = []
        self._failed_capabilities: set = set()
        self._verified_capabilities: set = set()
        for capability in ("embed", "rerank", "chat", "vl"):
            if self.config.mode_for(capability) == "http" and not self.config.base_url_for(capability):
                self.warnings.append(f"{capability} 已选择 HTTP，但未配置 DOCQA_BASE_URL")

    def is_mock_for(self, capability: str) -> bool:
        return (
            self.config.mode_for(capability) != "http"
            or not self.config.base_url_for(capability)
            or capability in self._failed_capabilities
        )

    @property
    def is_mock(self) -> bool:
        return all(self.is_mock_for(capability) for capability in ("embed", "rerank", "chat", "vl"))

    @property
    def mode(self) -> str:
        modes = {"mock" if self.is_mock_for(capability) else "http" for capability in ("embed", "rerank", "chat", "vl")}
        return next(iter(modes)) if len(modes) == 1 else "mixed"

    def status(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "base_url": self.config.public_base_url() if not self.is_mock else "",
            "chat_model": self.config.chat_model,
            "vl_model": self.config.vl_model,
            "embed_model": self.config.embed_model,
            "rerank_model": self.config.rerank_model,
            "warnings": list(self.warnings),
            "capabilities": {
                capability: {
                    "mode": "mock" if self.is_mock_for(capability) else "http",
                    "model": getattr(self.config, capability + "_model"),
                    "base_url": self.config.public_base_url(capability),
                    "state": (
                        "degraded" if capability in self._failed_capabilities
                        else "mock" if self.config.mode_for(capability) == "mock"
                        else "verified" if capability in self._verified_capabilities
                        else "configured"
                    ),
                }
                for capability in ("embed", "rerank", "chat", "vl")
            },
        }

    # ------------------------------------------------------------------ HTTP
    def _post(self, path: str, payload: Dict[str, Any], capability: str = "embed") -> Dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        key = self.config.api_key_for(capability)
        request = urllib.request.Request(
            self.config.api(path, capability),
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}" if key else "none",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:500]
            raise RuntimeError(self._redact(f"HTTP {exc.code}: {body}")) from None
        except Exception as exc:  # network, timeout, JSON
            raise RuntimeError(self._redact(str(exc))) from None

    def _redact(self, value: str) -> str:
        for key in sorted({self.config.api_key, self.config.chat_api_key}, key=len, reverse=True):
            if key:
                value = value.replace(key, "[REDACTED]")
        return value

    def _mark_http_failure(self, capability: str, exc: Exception) -> None:
        self._failed_capabilities.add(capability)
        self.warnings.append(f"{capability} 模型端点调用失败：{truncate(self._redact(str(exc)), 180)}")

    def _require_http(self, capability: str) -> None:
        if not self.config.base_url_for(capability):
            raise RuntimeError(f"{capability} HTTP 端点未配置")
        if capability in self._failed_capabilities:
            raise RuntimeError(f"{capability} HTTP 端点本次调用已失败，请检查配置后重试")

    # -------------------------------------------------------------- embedding
    def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        if self.config.mode_for("embed") == "http":
            try:
                self._require_http("embed")
                payload = {"model": self.config.embed_model, "input": texts, "encoding_format": "float"}
                data = self._post("embeddings", payload)
                rows = data.get("data") or data.get("embeddings") or []
                if len(rows) != len(texts):
                    raise RuntimeError("embedding 响应条数不匹配")
                vectors: List[Optional[List[float]]] = [None] * len(texts)
                for position, row in enumerate(rows):
                    if isinstance(row, dict):
                        index = row.get("index", position)
                        values = row.get("embedding", [])
                    else:
                        index, values = position, row
                    if type(index) is not int or not 0 <= index < len(texts) or vectors[index] is not None:
                        raise RuntimeError("embedding 响应 index 无效或重复")
                    if not isinstance(values, list) or not values:
                        raise RuntimeError("embedding 响应缺少浮点向量")
                    vector = [float(value) for value in values]
                    if not all(math.isfinite(value) for value in vector):
                        raise RuntimeError("embedding 响应包含非有限数值")
                    vectors[index] = vector
                if any(vector is None for vector in vectors) or len({len(vector) for vector in vectors if vector}) != 1:
                    raise RuntimeError("embedding 响应向量维度不一致")
                self._verified_capabilities.add("embed")
                return [vector for vector in vectors if vector is not None]
            except Exception as exc:
                self._mark_http_failure("embed", exc)
                raise RuntimeError(self._redact(str(exc))) from None
        dim = self.config.embedding_dim
        return [_hash_embedding(text, dim) for text in texts]

    def embed_one(self, text: str) -> List[float]:
        rows = self.embed([text])
        return rows[0] if rows else []

    # ------------------------------------------------------------------- VL
    def describe_image(self, image_path: str | Path, context: Dict[str, Any]) -> str:
        if self.config.mode_for("vl") == "http":
            try:
                self._require_http("vl")
                result = self._http_describe(image_path, context)
                self._verified_capabilities.add("vl")
                return result
            except Exception as exc:
                self._mark_http_failure("vl", exc)
        return _mock_vl(image_path, context)

    def _http_describe(self, image_path: str | Path, context: Dict[str, Any]) -> str:
        path = Path(image_path)
        mime = mimetypes.guess_type(str(path))[0] or "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        prompt = (
            "你是技术文档图片解析助手。请用中文客观描述这张图，重点说明："
            "图中模块/角色、连线与箭头方向、编号或步骤、关键文字与数值、"
            "以及它在文档中的作用。不要臆造图中不存在的信息。控制在 220 字内。"
            f"\n文档：{context.get('doc_name', '')}"
            f"\n页码：{context.get('page', '')}"
            f"\n图题：{context.get('title', '')}"
            f"\n图注：{context.get('alt', '')}"
        )
        payload = {
            "model": self.config.vl_model,
            "temperature": 0.1,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{encoded}"},
                        },
                    ],
                }
            ],
        }
        data = self._post("chat/completions", payload)
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if isinstance(content, list):
            content = " ".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("VL 模型返回空内容")
        return str(content).strip()

    # -------------------------------------------------------------- reranker
    def rerank(self, query: str, documents: List[str]) -> List[float]:
        if not documents:
            return []
        if self.config.mode_for("rerank") == "http":
            try:
                self._require_http("rerank")
                payload = {
                    "model": self.config.rerank_model,
                    "query": query,
                    "documents": documents,
                    "top_n": len(documents),
                }
                data = self._post("rerank", payload)
                rows = data.get("results") or data.get("data") or []
                if len(rows) != len(documents):
                    raise RuntimeError("rerank 响应未覆盖全部候选文档")
                scores: List[Optional[float]] = [None] * len(documents)
                for pos, row in enumerate(rows):
                    if isinstance(row, dict):
                        index = row.get("index", pos)
                        score = row.get("relevance_score", row.get("score", 0.0))
                    else:
                        index, score = pos, row
                    if type(index) is not int or not 0 <= index < len(scores) or scores[index] is not None:
                        raise RuntimeError("rerank 响应 index 无效或重复")
                    score = float(score)
                    if not math.isfinite(score):
                        raise RuntimeError("rerank 响应包含非有限分数")
                    scores[index] = score
                self._verified_capabilities.add("rerank")
                return [score for score in scores if score is not None]
            except Exception as exc:
                self._mark_http_failure("rerank", exc)
                raise RuntimeError(self._redact(str(exc))) from None
        return [_mock_rerank(query, doc) for doc in documents]

    # ------------------------------------------------------------------ chat
    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.2, max_tokens: int = 1200) -> Optional[str]:
        if self.config.mode_for("chat") == "mock":
            return None
        try:
            self._require_http("chat")
            payload = {
                "model": self.config.chat_model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            data = self._post("chat/completions", payload, capability="chat")
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if isinstance(content, list):
                content = " ".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
            if not isinstance(content, str) or not content.strip():
                raise RuntimeError("Chat 模型返回空内容")
            self._verified_capabilities.add("chat")
            return content.strip()
        except Exception as exc:
            self._mark_http_failure("chat", exc)
            return None


def _mock_vl(image_path: str | Path, context: Dict[str, Any]) -> str:
    """Offline VL substitute: uses the author-provided alt text/caption + layout hints."""
    title = str(context.get("title") or "").strip()
    alt = str(context.get("alt") or "").strip()
    nearby = strip_markdown(str(context.get("nearby") or ""))
    filename = Path(image_path).name.lower()

    core = alt or title
    if not core:
        if "architecture" in filename or "架构" in filename:
            core = "系统总体架构图，自上而下分为解析、索引与消费三层"
        elif "flow" in filename or "流程" in filename:
            core = "业务处理流程图，多个处理节点按箭头顺序串联"
        elif "board" in filename or "单板" in filename:
            core = "单板面板与端口布局示意图"
        elif "topo" in filename or "组网" in filename:
            core = "设备组网拓扑图，标注了链路与网元角色"
        else:
            core = "技术文档中的说明性插图"
    core = core.rstrip("。；; ")
    if len(core) > 180:
        core = core[:178] + "…"

    detail = (
        "图中以模块框、编号和有向连线组织信息，箭头表示处理顺序或数据流向，"
        "编号与正文步骤相互对应"
    )
    if any(word in core for word in ["架构", "分层"]):
        detail = "图中自上而下呈现分层结构，层间以箭头表示调用关系，各层内部再细分职责模块"
    elif any(word in core for word in ["流程", "时序", "接续", "步骤"]):
        detail = "图中用有向箭头串联多个处理节点，节点编号与正文步骤一一对应，分支表示异常或回退路径"
    elif any(word in core for word in ["端口", "面板", "单板", "硬件"]):
        detail = "图中给出面板分区、端口编号与指示灯位置，标注了上行口、业务口和管理口的相对关系"

    suffix = f"该图位于《{context.get('doc_name', '')}》第 {context.get('page', '')} 页，图题为“{title}”。" if title else ""
    context_hint = f"结合上下文，{nearby[:60]}。" if nearby and len(nearby) > 12 else ""
    return f"{core}。{detail}。{suffix}{context_hint}（离线 Mock 释义）".strip()


def _mock_rerank(query: str, document: str) -> float:
    """Offline cross-encoder substitute based on token overlap and exact terms."""
    q_tokens = set(tokenize(query))
    if not q_tokens:
        return 0.0
    lowered = document.lower()
    d_tokens = set(tokenize(document))
    overlap = len(q_tokens & d_tokens) / max(1, len(q_tokens))
    ascii_terms = [t for t in q_tokens if re.search(r"[a-z0-9]", t) and len(t) >= 3]
    exact = 0.0
    if ascii_terms:
        exact = sum(1 for term in ascii_terms if term in lowered) / len(ascii_terms)
    head = 1.0 if any(token in lowered[:120] for token in q_tokens) else 0.0
    return clamp(0.62 * overlap + 0.28 * exact + 0.10 * head)


def cosine_similarity(a: List[float], b: List[float]) -> float:
    return _cosine(a, b)


def mock_embedding(text: str, dim: int = 512) -> List[float]:
    return _hash_embedding(text, dim)


def model_hub_from_env() -> ModelHub:
    return ModelHub(ModelConfig.from_env())
