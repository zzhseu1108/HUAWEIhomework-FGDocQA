from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass
class WatchConfig:
    src_dir: str
    parsed_dir: str
    index_dir: str
    interval: float = 10.0
    recursive: bool = True
    once: bool = False
    settle_seconds: float = 0.5

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WatchEvent:
    action: str
    path: str
    at: str
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
