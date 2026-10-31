from __future__ import annotations

import base64
from pathlib import Path
from typing import Tuple

from docqa.doc2md import batch_parse
from docqa.docbase import build_index

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)

FLOW_DOC = """---
doc_name: 流程手册
category: 流程
pages: 2
---

<!-- page: 1 -->
# 流程手册

ACME_Start 是启动分组转发实例的入口参数，包含实例编号和中继组。

<!-- page: 2 -->
## 呼叫转发流程

分机注册后，呼叫转发按以下步骤执行：

1. 分机向网关发送 REGISTER 完成注册。
2. 网关对分机进行鉴权并写入在线表。
3. 号码分析匹配被叫前缀并选择中继组。
4. 媒体协商分配 SD5993 资源并建立通话。

![呼叫转发流程图：注册、鉴权、号码分析与媒体协商四个步骤以箭头串联](assets/flow.png)

图 1 呼叫转发流程

```mermaid
flowchart LR
    A[注册] --> B[鉴权]
    B --> C[号码分析]
    C --> D[媒体协商]
```
"""

COMMAND_DOC = """---
doc_name: 命令手册
category: 命令
pages: 2
---

<!-- page: 1 -->
# SD5993 命令手册

SD5993 是媒体处理单板，提供放音、录音和混音能力。

<!-- page: 2 -->
## 复位单板

复位 SD5993 单板使用命令 `reset board slot 7`，其中 7 为槽位号。

执行复位前必须确认该槽位没有正在进行的录音业务，否则会导致媒体中断。

复位后执行 `display board 7` 确认单板状态为 Normal。
"""


def build_workspace(base: Path) -> Tuple[Path, Path, Path]:
    raw = base / "raw"
    parsed = base / "parsed"
    index = base / "index"
    (raw / "assets").mkdir(parents=True, exist_ok=True)
    (raw / "assets" / "flow.png").write_bytes(PNG_1X1)
    (raw / "01_流程手册.md").write_text(FLOW_DOC, encoding="utf-8")
    (raw / "02_命令手册.md").write_text(COMMAND_DOC, encoding="utf-8")
    batch_parse(raw, parsed)
    build_index(parsed, index, chunk_size=1200)
    return raw, parsed, index
