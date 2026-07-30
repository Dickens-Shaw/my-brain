## 1. Monorepo 骨架与导出线搬迁

- [x] 1.1 初始化 pnpm workspace：根 `package.json`、`pnpm-workspace.yaml`（仅 `packages/*` 与 `apps/*`）、`tsconfig.base.json`、根 `.gitignore` 补充 `node_modules`、`dist`、`*.db`、索引产物
- [x] 1.2 将四条导出线搬到 `exporters/<name>/`（`wechat-favorites`、`chrome-bookmarks`、`juejin-articles`、`juejin-books`），确认各线 `bash run.sh` 行为不变
- [x] 1.3 同步更新根 README、各导出线 README、`.gitignore`、`openspec/config.yaml` context、`.env.example`（含 `VAULT_PATH`、`OPENAI_API_KEY`）；修订原则为「不上传原始数据文件」+ TS 层可有 npm 依赖
- [x] 1.4 创建包骨架：`packages/core`、`packages/config`、`packages/vault`、`packages/ingest`、`packages/retrieval`、`apps/mcp`（无 `apps/api`）；配置内部包依赖方向与 `pnpm install` 可通过

## 2. 领域模型与 Vault 读写

- [x] 2.1 在 `packages/core` 定义统一 frontmatter schema（Zod 或等价）、`source`/`type` 枚举、id 前缀表（含预留 `pdf`/`url`/`gh`/`rss`）、`scope` 枚举
- [x] 2.2 在 `packages/config` 实现从根 `.env` 读取 `VAULT_PATH`、OpenAI Key 等；缺失 `VAULT_PATH` 时启动即失败并给出明确提示
- [x] 2.3 在 `packages/vault` 实现 Vault 顶层骨架创建（`Inbox/`、`Library/`、`Books/`、`Maps/`、`Notes/`、`Projects/`、`Daily/`、`Templates/`、`Assets/{images,pdf,attachments}/`）
- [x] 2.4 在 `packages/vault` 实现 frontmatter 读写、路径安全文件名、按 id 读写机器维护区文档；手写区只读、永不写入
- [x] 2.5 实现 Vault 校验命令：机器维护区缺字段/非法枚举报错；手写区无 frontmatter 不报错；失效图片引用逐条列出

## 3. 迁移管线（P0）

- [x] 3.1 归一化：读取四条 `exporters/*/output/`，映射为统一文档记录并分配永久 id；无正文条目记入跳过清单；来源特有字段进 `origin`
- [x] 3.2 去重：按归一化 `source_url` 合并跨来源副本，保留字段最完整一份，其余写入 `origin.aliases`；输出可审计的去重报告
- [x] 3.3 文档级嵌入：标题+摘要+标题大纲 → embedding，结果落盘（与后续 chunk 嵌入分离），支持断点续传
- [x] 3.4 主题体系归纳：聚类 + LLM 命名 → 候选体系文件（含文档数、样本标题、过大/过小叶子提示）；完成后停止并提示人工审核，不自动分类
- [x] 3.5 分类：依据确认后的体系写 `topics`；边界样本 LLM 复核；无法归类进「待确认」；产出分类报告（含待确认占比告警）
- [x] 3.6 写入 Vault：文章 → `Library/<一级>/<二级>/`，小册 → `Books/<册名>/`（配图随文档 `assets/`）；幂等；生成 `Maps/`；输出迁移报告
- [x] 3.7 提供分阶段 CLI（可单独重跑各阶段），阶段间以 JSON 衔接；付费阶段断点续传

## 4. 索引与检索（P1）

- [x] 4.1 定义 `Embedder`、`VectorStore`、`TextIndex` 接口；实现 OpenAI 兼容 Embedder（模型与维度可配，`dimensions` 参数按模型条件发送，首批向量校验维度）与 SQLite（`vec0` + FTS5 trigram）实现
- [x] 4.2 AST 切块：按标题切 section；>1200 token 二次切分（~200 重叠）；<100 token 向后合并；代码块不切开；chunk 带标题路径
- [x] 4.3 全量/增量索引：覆盖 `Library/`、`Books/`、`Notes/`、`Projects/`、`Daily/`、`Inbox/`，排除 `Maps/`、`Templates/`；机器区以 id 为键，手写区以相对路径为键；按内容哈希跳过未变文档；只改 topics 时只更新元数据
- [x] 4.4 混合检索：向量与 BM25 各 top50 → RRF → top10；支持 `scope`/`topic`/`source` 过滤；一路为空不报错；结果可溯源到文件
- [x] 4.5 检索留痕：每次检索写日志表（query、两路结果、融合排序、各阶段耗时），可供排查未命中
- [x] 4.6 索引文件不入库；提供全量重建命令；文档中说明索引路径与重建方式

## 5. 评测与黄金问题集

- [x] 5.1 建立黄金问题集文件格式与不少于 20 条占位结构（问题 + 预期文档 id）；在 README 标明须由使用者本人按真实场景填写，实现者不得照语料反推填完
- [x] 5.2 实现评测命令：输出整体 top5/top10 命中率与逐条明细，未命中单独列出

## 6. MCP Server

- [x] 6.1 用 `@modelcontextprotocol/sdk` v1.x 实现 stdio MCP Server；索引缺失时返回明确错误
- [x] 6.2 注册三工具：`search_knowledge`（含 `scope`/`topic`/`source`/`limit`，结果带 `obsidian://`）、`get_document`（id 或手写区路径）、`list_topics`
- [x] 6.3 控制返回体量（片段截断、`limit` 上限）；提供 Cursor MCP 配置示例

## 7. 收尾与验收准备

- [x] 7.1 根 README / 中文 README 补充：monorepo 布局、Vault 独立仓库、迁移与索引命令、MCP 接入步骤
- [x] 7.2 冒烟：`pnpm install`、各导出线仍可跑、校验命令、迁移 dry-run 或小样本、索引小样本检索、MCP 握手列出工具
- [x] 7.3 确认本 change 未创建 `apps/api`、未引入 Redis/BullMQ/Postgres、未索引 `Maps/`
