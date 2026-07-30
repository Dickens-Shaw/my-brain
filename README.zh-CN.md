# my-brain

简体中文 | [English](README.md)

把微信收藏、Chrome 书签、掘金点赞/收藏集、掘金小册导出为 Markdown，再迁入独立私有
Obsidian Vault，并提供可重建的检索索引与 Cursor MCP。

收藏是攒下来了，但都锁在各家的 App 里：搜不到、备份不了、账号一没就全没了。
这个仓库先把它们搬回本地，再变成真正能在写代码时用得上的知识库。

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

至少填：`JUEJIN_USER_ID`（掘金列表）、`VAULT_PATH`（独立 Vault 绝对路径）、
以及要用检索/分类时的 `OPENAI_API_KEY`。微信账号目录名只有多个账号时才需要填。

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
pnpm vault:init                              # 在 VAULT_PATH 建顶层目录骨架
pnpm vault:validate                          # 校验 frontmatter / 失效图片
pnpm migrate --stage normalize               # 迁移：归一化（其后 dedupe → embed → induce）
# 审核 .data/migrate/topic-taxonomy.json，把 status 改为 confirmed
pnpm migrate --stage classify
pnpm migrate --stage write
pnpm index                                   # 构建 / 增量更新检索索引（.data/index.db）
pnpm search -- --query "Vite 冷启动"
pnpm mcp                                     # Cursor MCP（stdio）
```

黄金问题集在 `eval/golden-questions.json`：须由你本人按真实使用场景填写 `expected_doc_ids`，再跑 `pnpm eval`。

Vault 顶层分区：

- 机器维护：`Inbox/`、`Library/`、`Books/`、`Maps/`
- 手写：`Notes/`、`Projects/`、`Daily/`、`Templates/`
- `Assets/` 只服务手写区；导入文档的配图随文档放在同目录 `assets/`

## 环境要求

- macOS（微信管线依赖 macOS 的 LLDB 和微信客户端；另三条导出线跨平台）
- Python 3（导出线只用标准库，不需要 `pip install`）
- Node.js ≥ 20 + pnpm（知识层）
- SQLite ≥ 3.34（检索索引的 FTS5 trigram）

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
