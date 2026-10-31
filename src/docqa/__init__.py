"""DocQA · 基于 AI Agent 的个人本地多模态文档智能问答系统。

三层解耦：
    doc2md  -> 文档解析层（多模态文档 -> 结构化 Markdown）
    docbase -> 知识库层（三路混合检索 RAG）
    agent   -> 消费层（AGENTS.md 驱动的 CodeAgent 问答）
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
