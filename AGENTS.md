# AGENTS.md · DocQA 知识消费约定

> 本文件是 CodeAgent（Claude Code / NGA 等）启动时自动加载的项目级指令文件。
> 它只约定「何时查、怎么查、如何引用」，不包含任何业务代码。把检索经验写进约定，
> 让 Agent 的每次回答都有据可查。

## 1. 存在性声明

- 本仓库配备 **DocBase RAG 知识库**，索引位于 `data/index/`，解析语料位于 `data/parsed/`。
- 回答任何与 FE_HEART / SD5993 / 分组转发 / 运维相关的领域问题前，**必须先检索**。
- 检索入口：`./run.sh query "<问题>"`；完整问答：`./run.sh ask "<问题>"`。
- 不得凭模型记忆直接回答领域事实，不得编造文档名、页码、图号或命令。

## 2. 查询策略

1. **先分类，再检索**
   - 技术名词、型号、命令、接口名（如 `SD5993`、`FE_HEART_Start`、`reset board slot 3`）→ 走精确词检索（BM25 会给出更高权重）。
   - 原理、流程、排查思路 → 走语义检索（稠密向量 + 稀疏向量）。
2. **缩小范围**：如果问题明确指向某类文档，先按分类过滤，再检索。
   - 分类示例：`架构设计`、`呼叫流程`、`命令手册`、`运维手册`、`发布说明`。
   - 命令：`./run.sh query "问题" --category 命令手册`。
3. **多轮补充**：首轮结果覆盖率不足时，允许用文档标题、章节名扩展查询后重试；最多 2 轮。
4. **参数调优**：用 `./run.sh eval` 观察 `recall@k / hit_rate / MRR`，不要凭感觉调 `top_k`。

## 3. 溯源强制

- 每一个结论后必须标注 `[n]`，并在回答末尾列出：
  - 原文链接（`file://...` 或项目内路径）
  - 文档名
  - 页码
  - 图号与图题（如果结论来自图片）
- 一个 `[n]` 只能对应一个文本块；不同文档不得合并编号。
- **无据不答**：检索不到足够依据时，直接说明「无据不答」，并给出最接近的片段与建议，
  不得用常识补全，不得输出未经检索的命令或参数。
- 图片结论必须能回到 `assets/<文档名>_Page<页码>_Fig<图序>_<图题>.png`，并可与原文页码对照。

## 4. 产出形式

- 默认输出中文 Markdown，结构为：**结论 → 关键依据 → 溯源**。
- 流程、时序、状态机类问题：优先输出 Mermaid 流程图，并提示「与原图 Page x · Fig y 对照」。
- 命令、参数类问题：优先给出可直接执行的命令/参数表，再给注意事项。
- 版本、特性类问题：优先用表格列出「特性 / 说明 / 受益场景」。
- 排查类问题：按步骤编号输出，每步说明检查对象与预期结果。
- 回答末尾附一行元信息：`— N 轮检索 · M 条溯源 · 模型模式`。

## 5. 命令速查

```bash
./run.sh demo                                  # 一键跑通 parse → build → ask → eval
./run.sh parse examples/docs --out data/parsed # 解析文档（PDF/Word/PPT/Markdown）
./run.sh build data/parsed --incremental       # 增量建库
./run.sh query "分机注册后怎么转发？" --top-k 6  # 只检索
./run.sh ask "SD5993 怎么复位？" --show-trace   # 检索 + 生成 + 轨迹
./run.sh eval --index data/index               # recall@k / hit_rate / MRR
./run.sh serve --open                          # B/S 问答界面
./run.sh watch --src examples/docs --once      # 监控目录增量更新
```

## 6. 工程边界

- 分层：`web/service → agent → docbase → doc2md → 模型端点`，只允许上层依赖下层。
- 契约：跨层只通过 `models.py` 通信；字段只增不改。
  - `doc2md.models`: `ImageInfo` / `ParseTask` / `ParseResult`
  - `docbase.models`: `Chunk` / `RagIndex` / `QueryResult`
  - `agent.models`: `answer_question` 契约（`answer` / `format` / `citations` / `rounds_used`）
  - `web.models`: `WebQueryResponse` 与 `answer_question` 契约对齐；`WebDocumentResponse` 保留原文响应字段并追加图片清单（`ImageInfo` 元信息 + 本机预览 URL）
- 模型：四类端点由 `docqa/llm.py` 统一注入；`DOCQA_MODEL_MODE` 为默认模式，`DOCQA_EMBED_MODE` / `DOCQA_RERANK_MODE` / `DOCQA_CHAT_MODE` / `DOCQA_VL_MODE` 可单独覆盖；未配置端点时不宣称真实模型已连接。
  HTTP 向量化与精排失败必须明确报错，不以 Mock 向量混入真实索引；各能力失败互不影响。BGE-M3 稀疏检索必须有真实稀疏权重协议依据，当前本地 TF-IDF 稀疏路不得冒称 BGE-M3。
  聊天可通过 `DOCQA_CHAT_BASE_URL` / `DOCQA_CHAT_API_KEY` 独立配置；留空继承通用端点与密钥。密钥不得输出到诊断、日志或前端。
  不允许在业务代码里硬编码模型地址、密钥或厂商 SDK。
- 索引：`data/index/index.json` + `vectors.json` + `sparse.json` 三件套；索引版本可回滚。
- 安全：仅在本机运行；Web 默认绑定 `127.0.0.1`；文档不出本机；日志不打印密钥。

## 7. 维护约定

- 修改跨层接口时，先更新对应 `models.py`，再更新本文件第 6 节，最后改实现。
- 新增文档分类时，同步更新 `examples/test_set.json` 与 `docs/02_技术设计说明书.md`。
- 本文件与 `CLAUDE.md` 保持同步；`CLAUDE.md` 只做入口，不重复约定。
