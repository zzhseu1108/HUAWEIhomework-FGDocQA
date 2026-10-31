from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ImageInfo:
    """一张被解析图片的完整溯源信息（字段只增不改）。"""

    image_id: str
    doc_name: str
    page: int
    figure_index: int
    title: str
    path: str
    kind: str = "bitmap"          # bitmap | vector | rendered
    width: int = 0
    height: int = 0
    description: str = ""
    caption: str = ""
    source_ref: str = ""
    source_uri: str = ""
    vl_model: str = ""
    vl_mode: str = "mock"

    def label(self) -> str:
        return f"Page {self.page} · Fig {self.figure_index}"

    def display_title(self) -> str:
        return self.title or f"图 {self.figure_index}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ImageInfo":
        fields = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in fields})


@dataclass
class ParseTask:
    task_id: str
    src: str
    out_dir: str
    doc_type: str
    category: str = "未分类"
    vl_mode: str = "mock"
    options: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ParseTask":
        fields = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in fields})


@dataclass
class ParseResult:
    task: ParseTask
    doc_id: str
    doc_name: str
    markdown_path: str
    manifest_path: str
    assets_dir: str
    images: List[ImageInfo] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    pages: int = 1
    page_mode: str = "exact"       # exact | estimated
    source_uri: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["images"] = [img.to_dict() for img in self.images]
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ParseResult":
        images = [ImageInfo.from_dict(i) for i in data.get("images", [])]
        task = ParseTask.from_dict(data.get("task", {}))
        fields = cls.__dataclass_fields__.keys()
        payload = {k: v for k, v in data.items() if k in fields and k not in {"task", "images"}}
        return cls(task=task, images=images, **payload)
