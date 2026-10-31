from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from ..doc2md import parse_document
from ..doc2md.parser import detect_doc_type
from ..docbase import build_index
from ..llm import ModelHub
from ..utils import ensure_dir, now_iso, read_json, safe_name, write_json
from .models import WatchConfig, WatchEvent

SUPPORTED = {"pdf", "docx", "pptx", "markdown", "text"}


def _snapshot(root: Path, recursive: bool) -> Dict[str, Tuple[float, int]]:
    iterator = root.rglob("*") if recursive else root.glob("*")
    result: Dict[str, Tuple[float, int]] = {}
    for path in sorted(iterator):
        if not path.is_file() or path.name.startswith("."):
            continue
        if detect_doc_type(path) not in SUPPORTED:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        result[str(path.resolve())] = (stat.st_mtime, stat.st_size)
    return result


def _log(path: Path, event: WatchEvent) -> None:
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")


def _remove_deleted(parsed_dir: Path, deleted: set) -> int:
    removed = 0
    for manifest_path in parsed_dir.glob("**/manifest.json"):
        data = read_json(manifest_path, default={})
        source = str(data.get("task", {}).get("src") or data.get("source") or "")
        if source and source in deleted:
            shutil.rmtree(manifest_path.parent, ignore_errors=True)
            removed += 1
    return removed


def run_watch(
    config: WatchConfig,
    progress: Optional[Callable[[int, int, str], None]] = None,
    max_cycles: Optional[int] = None,
) -> None:
    src = Path(config.src_dir).resolve()
    parsed = ensure_dir(config.parsed_dir)
    index_dir = Path(config.index_dir)
    log_path = Path("data/watch.log")
    if not src.exists():
        raise FileNotFoundError(f"监控目录不存在：{src}")

    print(f"[watch] 监控 {src} → {parsed} → {index_dir}，间隔 {config.interval}s")
    previous: Dict[str, Tuple[float, int]] = {}
    cycles = 0
    while True:
        cycles += 1
        current = _snapshot(src, config.recursive)
        changed = [
            path for path, meta in current.items()
            if path not in previous or previous[path] != meta
        ]
        deleted = set(previous) - set(current)
        if changed or deleted or not (index_dir / "index.json").exists():
            events = []
            for path in changed:
                if progress:
                    progress(0, len(changed), f"解析变更 {Path(path).name}")
                try:
                    result = parse_document(Path(path), parsed / safe_name(Path(path).stem))
                    events.append(
                        WatchEvent("update", path, now_iso(), {"doc_id": result.doc_id, "pages": result.pages})
                    )
                    _log(log_path, events[-1])
                except Exception as exc:
                    event = WatchEvent("error", path, now_iso(), {"error": str(exc)[:300]})
                    events.append(event)
                    _log(log_path, event)
            removed = _remove_deleted(parsed, deleted)
            for path in deleted:
                event = WatchEvent("delete", path, now_iso())
                _log(log_path, event)
                events.append(event)
            try:
                report = build_index(
                    parsed,
                    index_dir,
                    incremental=True,
                    hub=ModelHub(),
                    progress=progress,
                )
                _log(
                    log_path,
                    WatchEvent(
                        "rebuild",
                        str(index_dir),
                        now_iso(),
                        {
                            "version": report["version"],
                            "docs": report["docs"],
                            "chunks": report["chunks"],
                        },
                    ),
                )
                print(
                    f"[watch] {now_iso()} 更新 {len(changed)} 个文档 · 删除 {removed + len(deleted)} 个 · "
                    f"索引 {report['chunks']} 块"
                )
            except Exception as exc:
                print(f"[watch] 建库失败：{exc}")
                _log(log_path, WatchEvent("error", str(index_dir), now_iso(), {"error": str(exc)[:300]}))
        else:
            print(f"[watch] {now_iso()} 无变更")
        previous = current
        if config.once or (max_cycles and cycles >= max_cycles):
            break
        time.sleep(max(1.0, config.interval))
