## Why

四条导出线已经产出约 4900 篇带 frontmatter 的 Markdown，但它们只是四堆互不相干的文件：frontmatter 字段各说各话，分类靠关键词正则（微信 1540 条、Chrome 715 条落进「99-待确认」），没有任何检索能力。写代码时想找"当年收藏的那篇讲 Vite 冷启动的文章"，只能靠 `grep` 和记忆。

本次改动把这堆文件变成一个可检索的知识库：统一成一套 Vault 规范，建立可重建的索引层，并通过 MCP 暴露给 Cursor。目标是每天写代码时真的用得上，而不是又一个"导出完就再也没打开过"的目录。

## What Changes

- **BREAKING**：仓库重组为 monorepo。四条导出线从 `<name>-export/` 搬到 `exporters/<name>/`，README 与文档中的路径全部更新。
- **BREAKING**：修订两条项目原则，且**仅对新增的 TypeScript 层生效**——
  - 「不引入第三方依赖」：`exporters/` 下的 Python 代码继续保持标准库零依赖；新增的 TS 包正常使用 npm 依赖。
  - 「不上传任何内容」：改为「不上传原始数据文件」。正文会发送给云端 embedding 与 LLM 接口用于向量化、分类和问答，这是检索能力的前提。用户已确认接受。
- 新增 Vault 规范：顶层按「机器维护区」（`Inbox/`、`Library/`、`Books/`、`Maps/`）与「手写区」（`Notes/`、`Projects/`、`Daily/`、`Templates/`）划分，统一 frontmatter schema、永久 id、主题目录树、Maps 索引页。导入的原文进 `Library/`（而非含义含混的 `Knowledge/`），自己写的概念笔记进 `Notes/`，两者只通过链接关联。Vault 存放在**独立的私有仓库**，本仓库只保存代码和指向 Vault 的路径配置——Vault 内容是他人的版权材料（公众号文章、付费小册），不能进入本公开仓库。
- 新增一次性迁移管线：归一化 → 去重 → 主题体系归纳（含人工审核闸门）→ 分类 → 写入 Vault。
- 新增索引与检索层：Markdown AST 切 chunk、云端 embedding、SQLite（`vec0` + FTS5）存储、向量与 BM25 混合召回、检索过程全量留痕。索引覆盖 `Library/`、`Books/`、手写区与 `Inbox/`，排除生成的 `Maps/` 与 `Templates/`。
- 新增 MCP Server：三个工具（`search_knowledge` / `get_document` / `list_topics`），stdio 传输，供 Cursor 拉起，检索结果带 `obsidian://` 跳转链接。
- 明确排除在本 change 之外：`apps/api` 骨架、后台管理界面、AI Chat、增量文件监听、知识图谱、Agent 编排。它们是独立的后续 change，边界与顺序见下节。

## 后续 change 路线图

排序依据是每一个都消费前一个的产出，不是按技术热度。**贯穿所有后续 change 的约束：后台不得成为第二个笔记系统。** 在后台修改一篇文档的 `topics`，动作是写回 Vault 里那个文件的 frontmatter 再触发重新索引，而不是写进数据库某张表；任何时候删掉索引与后台数据，Vault 必须完好无损。

1. **`vault-watcher`** —— chokidar 监听 Vault，debounce 后触发增量索引。排第一是因为最小且立刻见效：本 change 交付后每天在 Obsidian 里写笔记、调分类，手动跑增量索引一定会忘，忘了检索就开始过时。不需要 UI、API 或队列。边界严格限定「Vault 变化 → 更新索引」。
2. **`knowledge-inbox`** —— URL / PDF / GitHub 导入，CLI 优先，产物落 `Inbox/`，复用本 change 的分类能力归入 `Library/`。先不做界面：导入的难点是解析质量（PDF 抽文字、网页去噪、仓库里挑文档），与交互形式无关。不含 OCR 与 RSS。
3. **`admin-console`** —— 引入 `apps/api`（NestJS）与 `apps/web`（Next.js + shadcn/ui）。放在这里是因为到这一步它才有东西可展示：检索留痕（调试台的数据源）、chunk（可视化对象）、导入管线（上传入口要调的能力）、分类结果（要编辑的对象）。第一版四块功能：检索调试台（query → 两路召回 → RRF → 命中 chunk 全程可见，借鉴 RagFlow）、文档浏览与 `topics` 编辑、导入入口、索引状态与重建。不含 Chat 与图谱可视化。
4. **`ai-chat`** —— 带来源引用的问答。放在后台之后，因为 Chat 质量完全由检索质量决定；有调试台才能判断答错是没召回还是排在截断线外，没有就只能盲猜换 prompt。
5. **`agentic-retrieval`** —— LangGraph 编排、query 改写、多轮召回、LangSmith 追踪。**触发条件**：黄金问题集中「需先查 A 再据此查 B」的问题占比超过 20%。不达标不做——代价是每次提问多花数倍 token 与延迟。
6. **`knowledge-graph`** —— 实体与关系。**触发条件**：`Notes/` 积累到数百篇且确实出现大量跨文档关系推理需求。届时先用 SQLite 邻接表，不够再上 Neo4j——这个量级的关系图放一张表毫无压力，直接上图数据库是拿运维成本换一个尚未验证的收益。

明确不做：RSS / Notion 接入、移动端、鉴权、S3/MinIO、Milvus。单人本机使用，这些要么没有消费者，要么是为多用户与规模准备的。

## Capabilities

### New Capabilities

- `monorepo-workspace`: 仓库重组为 pnpm workspace，划定 Python 导出线与 TypeScript 知识层的边界与包职责。
- `vault-schema`: Vault 的目录结构、统一 frontmatter schema、永久 id 规则、id↔path 映射，以及 Vault 作为唯一事实来源（SSOT）的约束。
- `knowledge-migration`: 把四条导出线的产出一次性迁移进 Vault，含字段归一化、跨来源去重、LLM 主题体系归纳与分类。
- `retrieval-index`: 从 Vault 构建可重建的索引，含 AST 切 chunk、embedding、SQLite 存储、混合检索与检索留痕。
- `mcp-server`: 通过 MCP 协议把检索能力暴露给 Cursor 等 MCP 客户端。

### Modified Capabilities

无。`openspec/specs/` 当前为空，四条导出线的行为不变，只改目录位置。

## Impact

**目录与文档**：四条导出线整体搬迁；两份 README、各线自己的 README、`.gitignore`、`openspec/config.yaml` 的 context 都需要同步更新。

**新增依赖与工具链**：pnpm workspace、TypeScript、remark（Markdown AST）、better-sqlite3 + sqlite-vec、`@modelcontextprotocol/sdk` v1.x。要求 SQLite ≥ 3.34（FTS5 的 `trigram` 分词器）。

**外部服务**：任一提供 OpenAI 兼容 `/embeddings` 接口的 embedding 供应商，以及用于主题归纳/分类的 chat 接口，两者可分属不同供应商（DeepSeek 只有 chat、没有 embedding，是这条的直接来由）。按 4802 篇、正文合计约 87MB 估算，全量嵌入一次约 $0.6，chat 部分约 $0.02——成本几乎全在 embedding 上。API Key 沿用根目录 `.env`。

**新增仓库**：一个独立的私有 Vault 仓库，初始约 4900 篇 Markdown 加配图。

**不受影响**：四条导出线的抓取逻辑、断点续传机制、`output/` 产物格式全部不动。迁移管线只读它们的产出。
