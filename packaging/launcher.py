"""Standalone, offline submission launcher (also runnable from source).

This entry point never reads .env. Its dedicated demo workspace is separate from
the source checkout and from the read-only PyInstaller resource directory.
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Dict, Mapping, Optional, Sequence


APP_VERSION = "1.0.0-submission"
WORKSPACE_VERSION = "demo-v1"


def resource_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS).resolve()  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[1]


def configure_source_imports() -> None:
    source = resource_root() / "src"
    if source.is_dir() and str(source) not in sys.path:
        sys.path.insert(0, str(source))


def force_mock_mode() -> None:
    """Do not inherit accidental real-model configuration in the demo binary."""
    for key in list(os.environ):
        if key.startswith("DOCQA_"):
            os.environ.pop(key, None)
    os.environ["DOCQA_MODEL_MODE"] = "mock"
    for capability in ("EMBED", "RERANK", "CHAT", "VL"):
        os.environ[f"DOCQA_{capability}_MODE"] = "mock"
    os.environ["DOCQA_EMBED_DIM"] = "512"


def default_workspace(
    platform_name: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
) -> Path:
    platform_name = platform_name or sys.platform
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else home
    if platform_name == "win32":
        base = Path(environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    elif platform_name == "darwin":
        base = home / "Library" / "Application Support"
    else:
        base = Path(environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    return base / "FGDocQA" / WORKSPACE_VERSION


def demo_fingerprint(examples: Path) -> str:
    digest = hashlib.sha256(APP_VERSION.encode("utf-8"))
    for path in sorted(examples.rglob("*")):
        if path.is_file() and not path.name.startswith("."):
            digest.update(path.relative_to(examples).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def prepare_demo(workspace: Path, bundled_root: Optional[Path] = None) -> Path:
    """Rebuild demo paths after a move; never ship a developer's cached index."""
    from docqa.doc2md import batch_parse
    from docqa.docbase import build_index, load_index
    from docqa.llm import ModelHub

    bundled_root = resource_root() if bundled_root is None else bundled_root
    examples = bundled_root / "examples"
    if not (examples / "docs").is_dir():
        raise RuntimeError("Bundled example documents are missing; keep the entire application folder.")
    workspace = workspace.expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    index_dir = workspace / "data" / "index"
    marker = workspace / ".demo-workspace.json"
    expected = {
        "version": APP_VERSION,
        "fingerprint": demo_fingerprint(examples),
        "workspace": str(workspace),
    }
    # A fresh parse generation prevents old absolute paths from producing a
    # second set of document directories after the workspace is moved.
    generation = hashlib.sha256(json.dumps(expected, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    parsed = workspace / "data" / "parsed" / generation
    expected["parsed"] = str(parsed)
    try:
        previous = json.loads(marker.read_text(encoding="utf-8"))
        index = load_index(index_dir)
        files_ready = all((index_dir / name).is_file() for name in ("index.json", "vectors.json", "sparse.json"))
        if previous == expected and files_ready and index.embedding_model == "mock-hash-512":
            if index.docs and all(Path(doc["abs_path"]).is_file() for doc in index.docs):
                return index_dir
    except (OSError, ValueError, KeyError, RuntimeError):
        pass

    print("Preparing offline Mock demonstration...")
    shutil.copytree(examples, workspace / "examples", dirs_exist_ok=True)
    hub = ModelHub()
    batch_parse(workspace / "examples" / "docs", parsed, hub=hub)
    report = build_index(parsed, index_dir, hub=hub)
    marker.write_text(json.dumps(expected, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Ready: {report['docs']} documents, {report['chunks']} chunks. Mode: MOCK.")
    return index_dir


def create_demo_server(index_dir: Path, preferred_port: int = 8770):
    from docqa.web.server import create_server

    try:
        return create_server(index_dir, "127.0.0.1", preferred_port)
    except OSError as exc:
        if preferred_port and (exc.errno in {errno.EADDRINUSE, 10048} or getattr(exc, "winerror", None) == 10048):
            return create_server(index_dir, "127.0.0.1", 0)
        raise


def smoke_test(index_dir: Path) -> Dict[str, object]:
    """Verify real packaged HTTP/static/citation paths, then close the server."""
    server = create_demo_server(index_dir, preferred_port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def fetch(path: str):
        with urllib.request.urlopen(base + path, timeout=15) as response:
            return response.read()

    try:
        health = json.loads(fetch("/api/health"))
        assert health.get("ok") is True, "health endpoint failed"
        page = fetch("/").decode("utf-8")
        assert "FGDocQA" in page and "whale-guide.png" in page, "front-end resources are missing"
        whale = fetch("/static/whale-guide.png")
        assert whale.startswith(b"\x89PNG\r\n\x1a\n"), "whale artwork is missing"
        stats = json.loads(fetch("/api/stats"))
        assert stats["model"]["mode"] == "mock", "offline executable unexpectedly enabled an API"
        assert len(stats["docs"]) >= 5, "demo documents are missing"
        request = urllib.request.Request(
            base + "/api/ask",
            data=json.dumps({"question": "SD5993 单板怎么复位？", "top_k": 6}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            answer = json.loads(response.read())
        assert answer.get("citations"), "demo answer has no citations"
        assert answer.get("model_mode") == "mock", "answer did not use Mock mode"
        doc_id = next(doc["doc_id"] for doc in stats["docs"] if doc.get("images"))
        document = json.loads(fetch("/api/doc?" + urllib.parse.urlencode({"doc_id": doc_id})))
        assert document.get("images"), "document reader has no images"
        preview = fetch(document["images"][0]["url"])
        assert preview, "document asset cannot be opened"
        return {
            "ok": True,
            "mode": "mock",
            "documents": len(stats["docs"]),
            "citations": len(answer["citations"]),
            "web_assets": True,
            "whale_artwork": True,
            "document_preview": True,
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def run_web(index_dir: Path, port: int, open_browser: bool) -> int:
    server = create_demo_server(index_dir, preferred_port=port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"FGDocQA {APP_VERSION} | Offline MOCK demonstration")
    print(f"Open: {url}")
    print("Keep this window open. Press Ctrl+C to stop.")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            print("Browser could not be opened automatically; open the URL above.")
    try:
        while thread.is_alive():
            thread.join(timeout=0.5)
    except KeyboardInterrupt:
        print("\nStopping FGDocQA...")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FGDocQA standalone offline demonstration; no Python installation or API Key required.")
    parser.add_argument("--version", action="version", version=f"FGDocQA {APP_VERSION}")
    parser.add_argument("--data-dir", type=Path, help="Dedicated writable demo workspace; defaults to the user's application-data directory.")
    parser.add_argument("--port", type=int, default=8770, help="Preferred local HTTP port; uses a free port if occupied. Default: 8770.")
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically open the browser.")
    parser.add_argument("--smoke-test", action="store_true", help="Build demo data, test local HTTP/static/answer paths, and exit. Uses temporary data unless --data-dir is supplied.")
    parser.add_argument("--cli", nargs=argparse.REMAINDER, help="Run the original command-line interface in this demo workspace (Mock only). Use absolute paths for your input documents.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    if not 0 <= args.port <= 65535:
        raise SystemExit("--port must be between 0 and 65535")
    configure_source_imports()
    force_mock_mode()
    temporary = tempfile.TemporaryDirectory(prefix="fgdocqa-smoke-") if args.smoke_test and args.data_dir is None else None
    workspace = Path(temporary.name) if temporary else (args.data_dir or default_workspace())
    try:
        index_dir = prepare_demo(workspace)
        if args.smoke_test:
            print(json.dumps(smoke_test(index_dir), ensure_ascii=True, sort_keys=True))
            return 0
        if args.cli is not None:
            from docqa import cli

            previous = Path.cwd()
            try:
                os.chdir(workspace.expanduser().resolve())
                metadata = json.loads((Path.cwd() / ".demo-workspace.json").read_text(encoding="utf-8"))
                cli.DEFAULT_PARSED = Path(metadata["parsed"])
                return cli.main(args.cli)
            finally:
                os.chdir(previous)
        return run_web(index_dir, args.port, not args.no_browser)
    except Exception as exc:
        print(f"FGDocQA could not start: {exc}", file=sys.stderr)
        return 1
    finally:
        if temporary:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
