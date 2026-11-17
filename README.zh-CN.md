# my-brain

简体中文 | [English](README.md)

把微信收藏、Chrome 书签、掘金点赞/收藏集、掘金小册导出为 Markdown，再迁入独立私有
Obsidian Vault，并提供可重建的检索索引与 Cursor MCP。

收藏是攒下来了，但都锁在各家的 App 里：搜不到、备份不了、账号一没就全没了。
这个仓库先把它们搬回本地，再变成真正能在写代码时用得上的知识库。

长期目标是一个 AI 真正够得着的知识库，分阶段建。**Vault 里的 Markdown 是唯一事实来源，
其余一切——索引、检索、以及后面要建的东西——都是可以删掉重建的派生层。**
现在建成的是地基：四条导出线、一次性迁移、检索索引、MCP Server。
后面还有什么、以及哪些明确不做，见[路线图](#路线图)。

## 仓库布局

```
exporters/          Python 导出线（标准库零依赖）
  wechat-favorites/
  chrome-bookmarks/
  juejin-articles/
  juejin-books/
packages/           TypeScript 知识层
  core/             frontmatter schema、枚举、id 规则
  config/           .env / VAULT_PATH
  vault/            Vault 读写与校验
  ingest/           存量迁移管线
  retrieval/        切块、索引、混合检索
apps/
  mcp/              Cursor MCP Server（stdio）
```

Vault（知识正文）在**独立的私有仓库**里，本公开仓库只保存代码和 `VAULT_PATH` 配置。

由「Vault 是唯一事实来源」推出两条约束，它们约束后面要建的所有东西：

- 改一篇文档，动作是编辑 Vault 里那个文件的 frontmatter 再重新索引，而不是写进某个
  数据库的某张表。这里的任何东西都不许变成第二个笔记系统。
- 任何时候删掉 `.data/index.db` 以及将来的后台数据，Vault 必须完好无损。索引在设计上
  就是一次性的，`pnpm index` 能重建。

## 四条导出线

每个子目录是一条独立的管线，互不依赖，可以只跑其中一条。

| 目录 | 数据源 | 入口 | 当前产出 |
|------|--------|------|----------|
| [exporters/wechat-favorites](exporters/wechat-favorites/) | macOS 微信本地收藏库 `favorite.db` | `bash run.sh` | 3396 条收藏，抓到公众号正文 2894 篇 |
| [exporters/chrome-bookmarks](exporters/chrome-bookmarks/) | Chrome / Edge / Brave / Arc 书签文件 | `bash run.sh` | 去重后 1049 条书签，抓到正文 606 篇 |
| [exporters/juejin-articles](exporters/juejin-articles/) | 掘金「赞过的文章」和「收藏集」 | `bash run.sh` | 点赞 647 篇 + 收藏 37 篇，全部抓到正文 |
| [exporters/juejin-books](exporters/juejin-books/) | 本机攒下的掘金小册 | `bash run.sh` | 39 册 829 章，转出 740 章 Markdown |

四条管线的结构是同一套：分阶段执行，阶段之间用 JSON 衔接，任何一步都能单独重跑。

```
数据源 ──→ 清单 JSON/CSV/MD ──→ 正文 Markdown ──→ （迁移）Vault / 检索索引
```

前三条管线都要联网抓正文，支持断点续传，进度记在 `.fetch_state.json` 里，`Ctrl+C` 会先
存盘再退出，重新运行接着抓。抓不动的条目会归类写进 `未抓取清单.md`，不会静默丢掉。

小册那条不联网：小册不是接口能拉的东西，`input/` 里放的是自己攒下来的下载产物，
这条管线只做本地规整——形态识别、章节序号还原、HTML 转 Markdown、配图归位。

## 快速开始

### 1. 配置

```bash
cp .env.example .env
```

`VAULT_PATH`（独立 Vault 的绝对路径）是知识层所有命令的硬性前提，不填直接启动失败。
`JUEJIN_USER_ID` 是掘金那条线要的，`WECHAT_ACCOUNT` 只有多个微信账号时才需要填。

迁移与索引还需要两种模型能力，**两者可以分属不同供应商**：

| 能力 | 用在哪 | 配置项 |
|---|---|---|
| embedding | 主题聚类，以及全部检索 | `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`EMBEDDING_MODEL`、`EMBEDDING_DIMENSIONS` |
| chat | 给聚类命名、复核边界样本 | `CHAT_API_KEY`、`CHAT_BASE_URL`、`CHAT_MODEL`（不填则回落到 embedding 那套） |

任何提供 OpenAI 兼容 `/embeddings` 接口的供应商都能用。挑之前值得先知道：
**embedding 是硬性依赖、占成本约 95%，chat 只要几分钱。**
有些供应商只有 chat、根本没有 embedding 接口（DeepSeek 就是），这正是两者要分开配的原因。

模型与维度必须跟供应商配套。`EMBEDDING_DIMENSIONS` 在建向量表时就固定下来，填错会污染
整张表——所以首批向量返回时就会校验，不一致会中止并告诉你正确值。之后再改模型或维度都
必须全量重建索引：不同模型的向量不在同一空间，混用不会报错，只会让检索结果莫名变差。

### 2. 跑导出线

```bash
cd exporters/juejin-books && bash run.sh           # 全本地，不联网，几秒跑完
cd exporters/juejin-articles && bash run.sh        # 不需要任何权限和凭证
cd exporters/chrome-bookmarks && FETCH=1 bash run.sh
cd exporters/wechat-favorites && bash run.sh       # 需要先关 SIP 并重签名微信，见其 README
```

四条管线的门槛差别很大：

- **掘金小册**只碰本机文件，不需要账号也不需要联网，唯一的前提是 `input/` 里得先有小册。
- **掘金文章**几个列表接口都是公开的，不需要 Cookie。唯一的例外是私有收藏集，
  那个得配一份 `JUEJIN_COOKIE` 才看得到。
- **Chrome 书签**是明文 JSON，直接读，不需要调整任何系统权限。代价是书签横跨上千个
  站点，抓正文得靠 readability 式打分挑正文节点。
- **微信收藏**最麻烦，库是 SQLCipher 加密的，要关 SIP、重签名微信、用 LLDB 断在
  `CCKeyDerivationPBKDF` 上抓 passphrase。换来的好处是页面结构单一，锁定 `#js_content`
  就能拿到干净正文。

细节都在各自的 README 里：
[微信](exporters/wechat-favorites/README.md) ·
[微信正文抓取](exporters/wechat-favorites/ARTICLES.md) ·
[Chrome 书签](exporters/chrome-bookmarks/README.md) ·
[Chrome 正文抓取](exporters/chrome-bookmarks/PAGES.md) ·
[掘金文章](exporters/juejin-articles/README.md) ·
[掘金小册](exporters/juejin-books/README.md)

### 3. TypeScript 知识层

```bash
pnpm install
pnpm vault:init                    # 在 VAULT_PATH 建顶层目录骨架
pnpm vault:validate                # 校验 frontmatter / 失效图片

pnpm migrate --from normalize      # 归一化 → 去重 → 嵌入 → 归纳主题，然后停下
# 审核 .data/migrate/topic-taxonomy.json，把 status 改为 confirmed
pnpm migrate --stage classify
pnpm migrate --stage write         # 写入 Library/ 与 Books/，生成 Maps/

pnpm index                         # 构建 / 增量更新索引（.data/index.db）
pnpm search -- --query "Vite 冷启动"
pnpm mcp                           # Cursor MCP（stdio）
```

`--from <阶段>` 跑该阶段及其之后所有阶段，`--stage <阶段>` 只跑一个。
阶段之间用 `.data/migrate/` 下的 JSON 衔接，任何一步都能单独重跑。

**`induce` 之后管线会主动停下，这是设计如此。** 聚类给出候选主题体系、LLM 给每个簇命名，
但这套体系决定了约 4800 篇文档的物理目录结构——`write` 之后再改就要大规模移动文件。
所以它会等你读完 `topic-taxonomy.json`（每个叶子的文档数、样本标题、过大过小的告警）
并把 `status` 改成 `confirmed`。在那之前不会分类任何文档。

`embed` 和几个 LLM 阶段要花钱，且边跑边存断点，中断后重跑不会重复付费。

全量跑起来是什么规模，这些是在真实语料上量过的：去重后 4802 篇切出 **82,326 个 chunk**，
索引文件约 **375MB**。全量嵌入约 **$0.6**，耗时主要是网络往返，按几十分钟准备。
建好之后混合检索的两路各约 150ms（向量）和 115ms（BM25）。

黄金问题集在 `eval/golden-questions.json`：须由你本人按真实使用场景填写 `expected_doc_ids`，
再跑 `pnpm eval`——你自己的笔记里什么算正确答案，别人替你定不了。

Vault 顶层分区：

- 机器维护：`Inbox/`、`Library/`、`Books/`、`Maps/`
- 手写：`Notes/`、`Projects/`、`Daily/`、`Templates/`
- `Assets/` 只服务手写区；导入文档的配图随文档放在同目录 `assets/`

## 路线图

排序依据是依赖关系，不是技术热度：每一步都消费前一步的产出。其中几步带明确的**触发条件**
——提前建要付出实打实的成本与延迟，换来的收益却尚未被验证。

1. **`vault-watcher`** —— 监听 Vault，debounce 后触发增量索引。排第一是因为它最小且立刻
   见效：地基交付后你每天在 Obsidian 里写笔记调分类，手动跑索引一定会忘，而过时的检索
   比没有检索更糟。不需要 UI、API 或队列。
2. **`knowledge-inbox`** —— URL / PDF / GitHub 导入，产物落 `Inbox/`，复用现有分类能力归入
   `Library/`。CLI 优先：难点在解析质量（PDF 抽文字、网页去噪、仓库里挑文档），跟有没有
   界面无关。
3. **`admin-console`** —— 引入 `apps/api`（NestJS）与 `apps/web`（Next.js）。故意排这么后，
   因为到这一步它才有东西可展示：检索留痕（可调试）、chunk（可视化）、导入管线（有入口
   可驱动）、分类结果（可编辑）。
4. **`ai-chat`** —— 带来源引用的问答。放在后台之后，因为 Chat 质量完全由检索质量决定；
   没有留痕视图就分不清「压根没召回」和「召回了但排在截断线外」，只能盲改 prompt。
5. **`agentic-retrieval`** —— query 改写、多轮召回、链路追踪。**触发条件**：黄金问题集里
   确实「需先查 A 再据此查 B」的问题超过 20%。达不到就不做——代价是每次提问多花数倍
   token 与延迟。
6. **`knowledge-graph`** —— 实体与关系。**触发条件**：`Notes/` 积累到数百篇，**且**确实出现
   跨文档关系推理的需求。即便到那时也先用 SQLite 邻接表；这个量级直接上图数据库，是拿
   运维成本换一个尚未验证的收益。

明确不做：RSS / Notion 接入、移动端、鉴权、S3/MinIO、Milvus。单人本机使用，这些要么
在这里没有消费者，要么是为多用户与规模准备的。

`apps/api` 现在故意不存在。一个没有消费者的空骨架只是徒增维护面，它会跟需要它的后台
一起进来。

## 环境要求

- macOS（微信管线依赖 macOS 的 LLDB 和微信客户端；另三条导出线跨平台）
- Python 3（导出线只用标准库，不需要 `pip install`）
- Node.js ≥ 20 + pnpm（知识层）

不需要系统装 SQLite：`better-sqlite3` 自带一份已启用 FTS5 的 SQLite（当前 3.53.x），向量检索
来自运行时加载的 `sqlite-vec` 扩展。`better-sqlite3` 安装时要本地编译，所以根 `package.json`
把它列在 `pnpm.onlyBuiltDependencies` 里——不列的话 pnpm 会静默跳过编译，索引根本打不开。
`sqlite-vec` 走预编译二进制，不需要这条。

## 隐私

这些脚本处理的是你的全部收藏历史，请当作隐私数据对待。

- 导出线只读本机数据，不修改微信数据库和浏览器书签文件。
- **不上传原始数据文件**（数据库、Cookie、小册 `input/`）。知识层会把正文发送给云端
  embedding / LLM 接口，用于向量化、分类与检索——这是检索能力的前提。
- 账号 ID、Cookie、API Key 都收在 `.env` 里，仓库里只有 `.env.example` 模板。
- 所有 `exporters/*/output/`、小册 `input/`、`.env`、`*.db` 索引和 `*cookies*.json`
  都已被 `.gitignore` 忽略。提交前确认一下 `git status`。
- Vault 内容是他人版权材料，放在独立私有仓库，不要推进本公开仓库。
- 微信管线要求关闭 SIP，这会降低 macOS 的系统保护等级。取到密钥后建议立刻进恢复模式
  执行 `csrutil enable` 打回来。

## 许可

仓库里的代码用 [MIT](LICENSE)。

这只覆盖工具本身。脚本抓到 `output/` 里的内容版权属于原作者，留着自己用就好，别去
再分发——那个目录被 `.gitignore` 忽略也正是这个原因。小册是付费内容，
`exporters/juejin-books/` 的 `input/` 和 `output/` 都被忽略，尤其别外传。
