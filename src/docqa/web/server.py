from __future__ import annotations

import json
import mimetypes
import posixpath
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlencode, urlparse

from ..agent import answer_question
from ..docbase import IndexNotFound, Retriever, load_index
from ..llm import ModelHub
from ..utils import format_ms, read_text
from .models import WebDocumentResponse, WebQueryResponse

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_BODY = 1 << 20
IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


def _json_bytes(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


class DocQAHandler(BaseHTTPRequestHandler):
    index_dir: Path = Path("data/index")
    server_version = "FGDocQA/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:  # keep console clean
        return

    # ------------------------------------------------------------------ utils
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, data: Any, status: int = 200) -> None:
        self._send(status, _json_bytes(data), "application/json; charset=utf-8")

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"error": message}, status=status)

    def _static(self, rel: str) -> None:
        rel = unquote(rel).lstrip("/") or "index.html"
        target = (STATIC_DIR / rel).resolve()
        try:
            target.relative_to(STATIC_DIR.resolve())
        except ValueError:
            self._error("非法路径", 403)
            return
        if not target.exists() or not target.is_file():
            if rel == "index.html":
                self._error("前端资源缺失", 500)
            else:
                self._error("Not Found", 404)
            return
        mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), mime)

    def _index(self):
        return load_index(self.index_dir)

    # -------------------------------------------------------------------- GET
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path == "/api/health":
                self._json({"ok": True, "service": "FGDocQA", "index_dir": str(self.index_dir)})
            elif path == "/api/stats":
                self._stats()
            elif path == "/api/doc":
                self._doc(query)
            elif path == "/api/asset":
                self._asset(query)
            elif path == "/api/search":
                self._search(query)
            elif path in ("/", "/index.html"):
                self._static("index.html")
            elif path == "/favicon.ico":
                self._static("favicon.ico")
            elif path == "/favicon.svg":
                self._static("favicon.svg")
            elif path.startswith("/static/"):
                self._static(path[len("/static/") :])
            else:
                self._error("Not Found", 404)
        except IndexNotFound as exc:
            self._error(f"索引未就绪：{exc}。请先运行 ./run.sh demo 或 ./run.sh build", 503)
        except Exception as exc:
            self._error(f"服务异常：{exc}", 500)

    def _stats(self) -> None:
        index = self._index()
        hub = ModelHub()
        docs = [
            {
                "doc_id": doc.get("doc_id"),
                "doc_name": doc.get("doc_name"),
                "category": doc.get("category"),
                "pages": doc.get("pages"),
                "chunks": doc.get("chunks"),
                "images": doc.get("images"),
                "page_mode": doc.get("page_mode"),
            }
            for doc in index.docs
        ]
        categories = sorted({doc["category"] for doc in docs if doc.get("category")})
        self._json(
            {
                "version": index.version,
                "updated_at": index.updated_at,
                "stats": index.stats,
                "docs": docs,
                "categories": categories,
                "model": hub.status(),
            }
        )

    def _doc(self, query: Dict[str, list]) -> None:
        doc_id = (query.get("doc_id") or [""])[0]
        mode = (query.get("mode") or ["parsed"])[0]
        index = self._index()
        doc = next((item for item in index.docs if item.get("doc_id") == doc_id), None)
        if not doc:
            self._error("文档不存在", 404)
            return
        parsed_path = Path(doc.get("abs_path", ""))
        text = read_text(parsed_path)
        source = doc.get("source", "")
        if mode == "source" and Path(source).suffix.lower() in {".md", ".markdown", ".txt", ".text"}:
            text = read_text(source)
        images = self._document_images(index, doc_id)
        for image in images:
            path = str(image.get("path") or "")
            if self._asset_path(doc, path) is not None:
                image["url"] = "/api/asset?" + urlencode({"doc_id": doc_id, "path": path})
        self._json(WebDocumentResponse(
            doc_id=doc_id,
            doc_name=doc.get("doc_name"),
            category=doc.get("category"),
            page_mode=doc.get("page_mode"),
            source=source,
            source_uri=doc.get("source_uri"),
            text=text,
            images=images,
        ).to_dict())

    @staticmethod
    def _document_images(index, doc_id: str) -> List[Dict[str, Any]]:
        images: Dict[str, Dict[str, Any]] = {}
        for chunk in index.chunks:
            if chunk.doc_id != doc_id:
                continue
            for image in chunk.images:
                path = str(image.get("path") or "")
                if path and path not in images:
                    images[path] = dict(image)
        return list(images.values())

    @staticmethod
    def _asset_path(doc: Dict[str, Any], image_path: str) -> Optional[Path]:
        """Accept only canonical bitmap paths contained in this document's assets/."""
        relative = PurePosixPath(image_path)
        if (
            not image_path or "\\" in image_path or "\x00" in image_path
            or relative.is_absolute() or ".." in relative.parts
            or len(relative.parts) < 2 or relative.parts[0] != "assets"
            or relative.suffix.lower() not in IMAGE_TYPES
            or relative.as_posix() != image_path
        ):
            return None
        try:
            parsed_root = Path(doc["abs_path"]).resolve().parent
            target = (parsed_root / image_path).resolve()
            # Keep assets_root unresolved so symlinks escaping assets/ are rejected.
            target.relative_to(parsed_root / "assets")
            if target.suffix.lower() not in IMAGE_TYPES:
                return None
        except (KeyError, OSError, RuntimeError, ValueError):
            return None
        return target

    def _asset(self, query: Dict[str, list]) -> None:
        doc_id = (query.get("doc_id") or [""])[0]
        image_path = (query.get("path") or [""])[0]
        index = self._index()
        doc = next((item for item in index.docs if item.get("doc_id") == doc_id), None)
        if not doc:
            self._error("文档不存在", 404)
            return
        images = self._document_images(index, doc_id)
        target = self._asset_path(doc, image_path)
        if target is None or not any(image.get("path") == image_path for image in images):
            self._error("图片不在该文档的可预览资源中", 403)
            return
        if not target.is_file():
            self._error("图片文件不存在", 404)
            return
        self._send(200, target.read_bytes(), IMAGE_TYPES[target.suffix.lower()])

    def _search(self, query: Dict[str, list]) -> None:
        text = (query.get("q") or [""])[0].strip()
        if not text:
            self._error("缺少查询词 q")
            return
        top_k = max(1, min(20, int((query.get("top_k") or ["6"])[0])))
        category = (query.get("category") or [None])[0]
        result = Retriever(self.index_dir).search(text, top_k=top_k, category=category)
        self._json(result.to_dict())

    # ------------------------------------------------------------------- POST
    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/ask":
            self._error("Not Found", 404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY:
                self._error("请求体为空或过大", 413)
                return
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            question = str(payload.get("question", "")).strip()
            if not question:
                self._error("question 不能为空")
                return
            if len(question) > 2000:
                self._error("question 过长（最多 2000 字）")
                return
            top_k = max(1, min(16, int(payload.get("top_k", 6))))
            max_rounds = max(1, min(2, int(payload.get("max_rounds", 2))))
            category = payload.get("category") or None
            answer = answer_question(
                question,
                self.index_dir,
                top_k=top_k,
                max_rounds=max_rounds,
                category=category,
            )
            self._json(WebQueryResponse.from_answer(answer).to_dict())
        except IndexNotFound as exc:
            self._error(f"索引未就绪：{exc}。请先运行 ./run.sh demo", 503)
        except json.JSONDecodeError:
            self._error("请求体不是合法 JSON")
        except Exception as exc:
            self._error(f"问答失败：{exc}", 500)


def create_server(
    index_dir: str | Path = "data/index",
    host: str = "127.0.0.1",
    port: int = 8770,
) -> ThreadingHTTPServer:
    handler = type("BoundDocQAHandler", (DocQAHandler,), {"index_dir": Path(index_dir)})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def serve(index_dir: str | Path = "data/index", host: str = "127.0.0.1", port: int = 8770) -> None:
    server = create_server(index_dir, host, port)
    print(f"FGDocQA · http://{host}:{server.server_address[1]}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
