from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from ..llm import ModelHub


class VLDescriber:
    """VL 深度释义适配层：真实端点由 ModelHub 注入，Mock 模式保证离线可跑。"""

    def __init__(self, hub: Optional[ModelHub] = None) -> None:
        self.hub = hub or ModelHub()
        self.calls = 0

    def describe(self, image_path: str | Path, context: Dict[str, Any]) -> str:
        self.calls += 1
        return self.hub.describe_image(image_path, context)

    @property
    def mode(self) -> str:
        return "mock" if self.hub.is_mock_for("vl") else "http"

    @property
    def model(self) -> str:
        return self.hub.config.vl_model
