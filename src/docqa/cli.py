from __future__ import annotations

import argparse
import json
import math
import sys
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional

from .agent import AgentConfig, answer_question
from .doc2md import batch_parse, detect_doc_type, parse_document
from .docbase import Retriever, build_index, evaluate, load_index
from .docbase.indexer import IndexNotFound
from .llm import ModelConfig, ModelHub
from .utils import format_ms, truncate

EXAMPLES = Path("examples")
DEFAULT_PARSED = Path("data/parsed")
DEFAULT_INDEX = Path("data/index")


def _progress(current: int, total: int, message: str) -> None:
    if sys.stderr.isatty():
        width = 24
        ratio = 0 if total <= 0 else min(1.0, current / total)
        filled = int(width * ratio)
        bar = "█" * filled + "░" * (width - filled)
        sys.stderr.write(f"\r  {bar}  {message:<42}")
        if current >= total:
            sys.stderr.write("\n")
        sys.stderr.flush()
    else:
        print(f"[{current}/{total}] {message}", file=sys.stderr)


def _json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_parse(args: argparse.Namespace) -> int:
    hub = ModelHub()
    src = Path(args.src)
    if not src.exists():
        print(f"源路径不存在：{src}", file=sys.stderr)
        return 2
    out_dir = Path(args.out)
    if src.is_file() and out_dir == DEFAULT_PARSED:
        out_dir = out_dir / src.stem
    if src.is_dir():
        results = batch_parse(
            src,
            args.out,
            recursive=not args.no_recursive,
            types=args.types.split(",") if args.types else None,
            category=args.category,
            hub=hub,
            progress=_progress,
        )
    else:
        result = parse_document(src, out_dir, category=args.category, hub=hub)
        results = [result]
    if args.json:
        _json([r.to_dict() for r in results])
        return 0
    print(f"\n解析完成：{len(results)} 个文档")
    for result in results:
        print(
            f"  · {result.doc_name}  {result.pages} 页 · {len(result.images)} 图 · "
            f"{result.stats.get('chars', 0)} 字 · {result.page_mode}"
        )
        print(f"    {result.markdown_path}")
        for image in result.images:
            print(f"      - {image.label()}  {image.title}  ({image.path})")
        for warning in result.warnings:
            print(f"    ! {warning}")
    if hub.warnings:
        for warning in hub.warnings:
            print(f"  ! {warning}")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    hub = ModelHub()
    try:
        report = build_index(
            args.md_dir,
            args.index,
            incremental=args.incremental,
            chunk_size=args.chunk_size,
            overlap=args.overlap,
            hub=hub,
            progress=_progress,
        )
    except Exception as exc:
        print(f"建库失败：{exc}", file=sys.stderr)
        return 1
    if args.json:
        _json(report)
        return 0
    print("\n建库完成")
    print(f"  索引版本：{report['version']}")
    print(f"  文档 / 文本块：{report['docs']} / {report['chunks']}")
    print(f"  新增 / 更新 / 删除：{report['added_docs']} / {report['updated_docs']} / {report['removed_docs']}")
    print(f"  图片：{report['images']} · 模式：{report['mode']} · 耗时：{format_ms(report['elapsed_ms'])}")
    print(f"  索引目录：{report['index_dir']}")
    for warning in report.get("warnings", []):
        print(f"  ! {warning}")
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    hub = ModelHub()
    try:
        retriever = Retriever(args.index, hub=hub)
        result = retriever.search(args.text, top_k=args.top_k, category=args.category)
    except IndexNotFound as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        _json(result.to_dict())
        return 0
    print(f"查询：{result.query}")
    print(f"索引：{result.index_version} · 耗时：{format_ms(result.latency_ms)} · 分类：{result.category or '全部'}")
    if result.warnings:
        for warning in result.warnings:
            print(f"! {warning}")
    for hit in result.hits:
        chunk = hit.chunk
        print(
            f"\n[{hit.rank}] score={hit.score:.3f} rerank={hit.rerank_score:.3f} "
            f"routes={','.join(hit.routes)}"
        )
        print(f"    《{chunk.doc_name}》 Page {chunk.page} · {chunk.heading}")
        print("    " + chunk.text.replace("\n", " ")[:180])
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    hub = ModelHub()
    try:
        answer = answer_question(
            args.question,
            args.index,
            max_rounds=args.max_rounds,
            top_k=args.top_k,
            category=args.category,
            hub=hub,
            config=AgentConfig(min_rerank=args.min_rerank, min_coverage=args.min_coverage),
        )
    except IndexNotFound as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        _json(answer.to_dict())
        return 0
    print(answer.answer)
    print(
        f"\n— {answer.rounds_used} 轮检索 · {len(answer.citations)} 条溯源 · "
        f"{format_ms(answer.elapsed_ms)} · {answer.model_mode.upper()}"
    )
    if args.show_trace:
        print("\n检索轨迹：")
        _json(answer.trace)
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    hub = ModelHub()
    try:
        report = evaluate(
            args.index,
            args.test_set,
            top_k=args.top_k,
            hub=hub,
            report_path=args.report,
        )
    except Exception as exc:
        print(f"评估失败：{exc}", file=sys.stderr)
        return 1
    if args.json:
        _json(report)
        return 0
    summary = report["summary"]
    print("检索质量评估（test 模式）")
    print(f"  用例数：{summary['cases']} · TOP-K：{summary['top_k']} · 索引：{summary['index_version']}")
    print(f"  recall@{summary['top_k']} = {summary.get('recall@' + str(summary['top_k']), 0)}")
    print(f"  hit_rate = {summary['hit_rate']} · MRR = {summary['mrr']}")
    print(f"  keyword_hit_rate = {summary['keyword_hit_rate']} · 平均耗时 {format_ms(summary['avg_latency_ms'])}")
    print("")
    print("  #  命中  排名  recall  question")
    for index, case in enumerate(report["cases"], start=1):
        print(
            f"  {index:<2} {'✓' if case['hit'] else '✗'}    {case['top_rank'] or '-':<4}  "
            f"{case['recall_at_k']:.2f}    {case['question']}"
        )
    if args.report:
        print(f"\n报告已写入：{args.report}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .web.server import serve

    url = f"http://{args.host}:{args.port}"
    if args.open:
        webbrowser.open(url)
    print(f"FGDocQA Web 已启动：{url}  (Ctrl+C 退出)")
    serve(args.index, host=args.host, port=args.port)
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    from .service.watcher import WatchConfig, run_watch

    config = WatchConfig(
        src_dir=args.src,
        parsed_dir=args.parsed,
        index_dir=args.index,
        interval=args.interval,
        recursive=not args.no_recursive,
        once=args.once,
    )
    run_watch(config, progress=_progress)
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    if not EXAMPLES.exists():
        print("未找到 examples/ 目录；请在项目根目录运行", file=sys.stderr)
        return 2
    hub = ModelHub()
    print("FGDocQA Demo · 多模态文档 → 知识库 → Agent 问答\n")
    print("① 解析示例文档（doc2md · Mock VL 释义）")
    parsed = DEFAULT_PARSED
    results = batch_parse(EXAMPLES / "docs", parsed, hub=hub, progress=_progress)
    print(f"   {len(results)} 个文档 · {sum(len(r.images) for r in results)} 张图片")

    print("\n② 构建三路混合索引（DocBase）")
    report = build_index(parsed, DEFAULT_INDEX, hub=hub, progress=_progress)
    print(f"   {report['chunks']} 个文本块 · 版本 {report['version']}")

    print("\n③ Agent 问答（AGENTS.md 约定驱动）")
    questions = [
        "分机注册后，呼叫是怎么转发到中继的？",
        "SD5993 单板怎么复位？命令是什么？",
        "如何申请年假？",
    ]
    for question in questions:
        answer = answer_question(question, DEFAULT_INDEX, hub=hub)
        print("\n" + "─" * 76)
        print("Q:", question)
        print(answer.answer)
        print(f"— {answer.rounds_used} 轮 · {len(answer.citations)} 条溯源 · {answer.model_mode.upper()}")

    print("\n④ 检索质量评估（test 模式）")
    evaluation = evaluate(
        DEFAULT_INDEX,
        EXAMPLES / "test_set.json",
        report_path=Path("data/eval_report.json"),
        hub=hub,
    )
    summary = evaluation["summary"]
    print(
        f"   recall@6={summary['recall@6']}  hit_rate={summary['hit_rate']}  "
        f"MRR={summary['mrr']}  keyword_hit_rate={summary['keyword_hit_rate']}"
    )
    print("\n完成。启动 Web 界面： ./run.sh serve --open")
    return 0


def _model_output(value: Any, config: ModelConfig) -> str:
    """Keep configured credentials out of diagnostic output, including errors."""
    result = str(value)
    keys = {getattr(config, name, "") for name in ("api_key", "chat_api_key")}
    for key in sorted((key for key in keys if key), key=len, reverse=True):
        result = result.replace(key, "[REDACTED]")
    for name in ("base_url", "chat_base_url"):
        base_url = getattr(config, name, "")
        if base_url and ("?" in base_url or "@" in base_url):
            result = result.replace(base_url, "[REDACTED ENDPOINT]")
    return result


def cmd_probe(args: argparse.Namespace) -> int:
    """Only verify capabilities explicitly configured for HTTP, independently."""
    config = ModelConfig.from_env()
    print("模型接口探测（仅验证 HTTP 能力；不显示密钥）")
    failures = passed = skipped = 0

    def run(capability: str, name: str, fn, validate, describe) -> None:
        nonlocal failures, passed, skipped
        model = getattr(config, capability + "_model")
        prefix = _model_output(f"{name} · {model}", config)
        if config.mode_for(capability) != "http":
            skipped += 1
            print(f"  - {prefix}: Mock 配置，跳过真实接口探测")
            return
        hub = ModelHub(config)
        try:
            value = fn(hub)
            validate(value)
            if hub.is_mock_for(capability):
                raise RuntimeError("没有获得真实 HTTP 返回")
            passed += 1
            print(f"  ✓ {prefix}: HTTP 已验证 · {describe(value)}")
        except Exception as exc:
            failures += 1
            print(f"  ✗ {prefix}: {_model_output(exc, config)}")
        for warning in hub.warnings:
            print(f"    ! {_model_output(warning, config)}")

    def vector_ok(value) -> None:
        if not isinstance(value, (list, tuple)) or not value:
            raise RuntimeError("Embedding 返回空向量或格式错误")
        if not all(isinstance(number, (int, float)) and not isinstance(number, bool) and math.isfinite(number) for number in value):
            raise RuntimeError("Embedding 向量包含非数值或非有限值")

    def scores_ok(value) -> None:
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            raise RuntimeError("Reranker 必须返回全部 3 个候选的分数")
        if not all(isinstance(number, (int, float)) and not isinstance(number, bool) and math.isfinite(number) for number in value):
            raise RuntimeError("Reranker 分数包含非数值或非有限值")

    def text_ok(value) -> None:
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError("模型返回空内容或格式错误")

    run("embed", "Embedding", lambda hub: hub.embed_one("FGDocQA connectivity probe"), vector_ok, lambda value: f"dim={len(value)}")
    run("rerank", "Reranker", lambda hub: hub.rerank("document retrieval", ["A document search system.", "A weather forecast.", "A retrieval quality report."]), scores_ok, lambda value: "scores=" + ", ".join(f"{score:.4f}" for score in value))
    run("chat", "Chat", lambda hub: hub.chat([{"role": "user", "content": "只回复两个字：收到"}], temperature=0, max_tokens=16), text_ok, lambda value: f"返回 {len(value.strip())} 字符")
    image = Path(args.image)
    if config.mode_for("vl") == "http" and not image.is_file():
        failures += 1
        print("  ✗ VL: 已配置 HTTP，但测试图片不存在，未能验证")
    else:
        run("vl", "VL", lambda hub: hub.describe_image(image, {"doc_name": "FGDocQA 接口测试", "page": 1, "title": "测试图片", "alt": "图片理解连通性测试"}), text_ok, lambda value: f"返回 {len(value.strip())} 字符")
    if not passed and not failures:
        summary = "未启用 HTTP 能力，真实端点未验证"
    else:
        summary = f"HTTP 已验证 {passed} 项 · 失败 {failures} 项 · Mock 跳过 {skipped} 项"
    print(f"\n结果：{summary}")
    return 0 if failures == 0 else 1


def cmd_doctor(args: argparse.Namespace) -> int:
    print("FGDocQA 环境检查")
    print(f"  Python：{sys.version.split()[0]}")
    print(f"  工作目录：{Path.cwd()}")
    config = ModelConfig.from_env()
    hub = ModelHub(config)
    status = hub.status()
    print(f"  模型模式：{status['mode']}（仅检查配置，不发起模型请求）")
    for capability, label in (("embed", "Embedding"), ("rerank", "Reranker"), ("chat", "Chat"), ("vl", "VL")):
        requested = config.mode_for(capability)
        detail = "HTTP 已配置，尚未验证" if requested == "http" else "Mock / 本地回退"
        model = getattr(config, capability + "_model")
        print("  " + _model_output(f"{label}：{model} · {detail}", config))
    try:
        index = load_index(args.index)
        print(
            f"  索引：OK · 版本 {index.version} · {index.stats.get('doc_count')} 文档 / "
            f"{index.stats.get('chunk_count')} 块"
        )
        print("  " + _model_output(f"索引向量模型：{index.embedding_model} · 精排：{index.rerank_model}", config))
    except Exception as exc:
        print(f"  索引：未就绪（{_model_output(exc, config)}）")
    try:
        import pypdf  # noqa: F401
        print("  PDF 解析：pypdf 已安装")
    except ImportError:
        print("  PDF 解析：未安装 pypdf（pip install pypdf pillow）")
    try:
        import PIL  # noqa: F401
        print("  PDF 取图：Pillow 已安装")
    except ImportError:
        print("  PDF 取图：未安装 Pillow（pip install pillow）")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docqa",
        description="FGDocQA · 基于 AI Agent 的个人本地多模态文档智能问答系统",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_parse = sub.add_parser("parse", help="doc2md：解析 PDF/Word/PPT/Markdown 为结构化 Markdown")
    p_parse.add_argument("src", help="源文件或目录")
    p_parse.add_argument("--out", default=str(DEFAULT_PARSED), help="输出目录")
    p_parse.add_argument("--category", default=None, help="文档分类")
    p_parse.add_argument("--types", default=None, help="目录模式下允许的扩展名，如 md,pdf,docx,pptx")
    p_parse.add_argument("--no-recursive", action="store_true", help="目录模式不递归")
    p_parse.add_argument("--json", action="store_true")
    p_parse.set_defaults(func=cmd_parse)

    p_build = sub.add_parser("build", help="DocBase：构建三路混合检索索引")
    p_build.add_argument("md_dir", nargs="?", default=str(DEFAULT_PARSED), help="Markdown 目录")
    p_build.add_argument("--index", default=str(DEFAULT_INDEX), help="索引目录")
    p_build.add_argument("--incremental", action="store_true", help="增量建库")
    p_build.add_argument("--chunk-size", type=int, default=1200)
    p_build.add_argument("--overlap", type=int, default=120)
    p_build.add_argument("--json", action="store_true")
    p_build.set_defaults(func=cmd_build)

    p_query = sub.add_parser("query", help="DocBase：三路召回 + RRF + 精排")
    p_query.add_argument("text", help="查询文本")
    p_query.add_argument("--index", default=str(DEFAULT_INDEX))
    p_query.add_argument("--top-k", type=int, default=6)
    p_query.add_argument("--category", default=None)
    p_query.add_argument("--json", action="store_true")
    p_query.set_defaults(func=cmd_query)

    p_ask = sub.add_parser("ask", help="Agent：检索增强问答 + 强制溯源")
    p_ask.add_argument("question", help="自然语言问题")
    p_ask.add_argument("--index", default=str(DEFAULT_INDEX))
    p_ask.add_argument("--top-k", type=int, default=6)
    p_ask.add_argument("--max-rounds", type=int, default=2)
    p_ask.add_argument("--category", default=None)
    p_ask.add_argument("--min-rerank", type=float, default=0.20)
    p_ask.add_argument("--min-coverage", type=float, default=0.30)
    p_ask.add_argument("--show-trace", action="store_true")
    p_ask.add_argument("--json", action="store_true")
    p_ask.set_defaults(func=cmd_ask)

    p_eval = sub.add_parser("eval", help="DocBase test 模式：recall@k / hit_rate / MRR")
    p_eval.add_argument("--index", default=str(DEFAULT_INDEX))
    p_eval.add_argument("--test-set", default=str(EXAMPLES / "test_set.json"))
    p_eval.add_argument("--top-k", type=int, default=6)
    p_eval.add_argument("--report", default="data/eval_report.json")
    p_eval.add_argument("--json", action="store_true")
    p_eval.set_defaults(func=cmd_eval)

    p_serve = sub.add_parser("serve", help="可选扩展：启动 B/S Web 问答界面")
    p_serve.add_argument("--index", default=str(DEFAULT_INDEX))
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8770)
    p_serve.add_argument("--open", action="store_true")
    p_serve.set_defaults(func=cmd_serve)

    p_watch = sub.add_parser("watch", help="可选扩展：监控目录增量更新")
    p_watch.add_argument("--src", default=str(EXAMPLES / "docs"))
    p_watch.add_argument("--parsed", default=str(DEFAULT_PARSED))
    p_watch.add_argument("--index", default=str(DEFAULT_INDEX))
    p_watch.add_argument("--interval", type=float, default=10.0)
    p_watch.add_argument("--no-recursive", action="store_true")
    p_watch.add_argument("--once", action="store_true")
    p_watch.set_defaults(func=cmd_watch)

    p_demo = sub.add_parser("demo", help="一键跑通 parse → build → ask → eval")
    p_demo.set_defaults(func=cmd_demo)

    p_doctor = sub.add_parser("doctor", help="检查环境、模型配置与索引状态")
    p_doctor.add_argument("--index", default=str(DEFAULT_INDEX))
    p_doctor.set_defaults(func=cmd_doctor)

    p_probe = sub.add_parser("probe", help="四类模型连通性冒烟测试")
    p_probe.add_argument("--image", default=str(EXAMPLES / "docs" / "assets" / "board.png"))
    p_probe.set_defaults(func=cmd_probe)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
