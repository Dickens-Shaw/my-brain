# my-brain

简体中文 | [English](README.en.md)

把微信收藏、Chrome 书签、掘金点赞批量导出为带 frontmatter 的本地 Markdown 知识库。
只读本机数据，纯 Python 标准库，零第三方依赖。

收藏是攒下来了，但都锁在各家的 App 里：搜不到、备份不了、账号一没就全没了。
这个仓库把它们一次性搬回本地，变成可以用 Obsidian / Logseq / ripgrep 直接检索的
Markdown 文件。

## 三条管线

每个子目录是一条独立的管线，互不依赖，可以只跑其中一条。

| 目录 | 数据源 | 入口 | 当前产出 |
|------|--------|------|----------|
| [wechat-favorites-export](wechat-favorites-export/) | macOS 微信本地收藏库 `favorite.db` | `bash run.sh` | 3396 条收藏，抓到公众号正文 2894 篇 |
| [chrome-bookmarks-export](chrome-bookmarks-export/) | Chrome / Edge / Brave / Arc 书签文件 | `bash run.sh` | 去重后 1049 条书签，抓到正文 606 篇 |
| [juejin-articles-export](juejin-articles-export/) | 掘金「赞过的文章」 | `bash run.sh` | 647 篇，全部抓到正文 |

三条管线的结构是同一套：分阶段执行，阶段之间用 JSON 衔接，任何一步都能单独重跑。

```
数据源 ──→ 清单 JSON/CSV/MD ──→ 正文 Markdown ──→ 分类索引 / 知识库目录
```

正文抓取都支持断点续传，进度记在 `.fetch_state.json` 里，`Ctrl+C` 会先存盘再退出，
重新运行接着抓。抓不动的条目会归类写进 `未抓取清单.md`，不会静默丢掉。

## 快速开始

先把本机配置填好。真实值只留在本地，`.env` 已被 `.gitignore` 忽略：

```bash
cp .env.example .env
```

里面就两项要填：掘金用户 ID（拉点赞列表用），以及微信账号目录名（只有一个微信账号时留空即可）。
三个 `run.sh` 启动时会自动读根目录的 `.env`，也接受同名环境变量临时覆盖。

```bash
cd juejin-articles-export && bash run.sh        # 最省事，不需要任何权限和凭证
cd chrome-bookmarks-export && FETCH=1 bash run.sh
cd wechat-favorites-export && bash run.sh       # 需要先关 SIP 并重签名微信，见其 README
```

三条管线的门槛差别很大：

- **掘金**最简单，用的是公开接口，不需要 Cookie。
- **Chrome 书签**是明文 JSON，直接读，不需要调整任何系统权限。代价是书签横跨上千个
  站点，抓正文得靠 readability 式打分挑正文节点。
- **微信收藏**最麻烦，库是 SQLCipher 加密的，要关 SIP、重签名微信、用 LLDB 断在
  `CCKeyDerivationPBKDF` 上抓 passphrase。换来的好处是页面结构单一，锁定 `#js_content`
  就能拿到干净正文。

细节都在各自的 README 里：
[微信](wechat-favorites-export/README.md) ·
[微信正文抓取](wechat-favorites-export/ARTICLES.md) ·
[Chrome 书签](chrome-bookmarks-export/README.md) ·
[Chrome 正文抓取](chrome-bookmarks-export/PAGES.md) ·
[掘金](juejin-articles-export/README.md)

## 产出长什么样

每篇正文是一个带 YAML frontmatter 的 Markdown，按年份分目录，文件名是
`日期-标题.md`：

```yaml
---
title: "TypeScript 技巧：让代码库整洁 10 倍"
account: "前端小石匠"
publish_time: "2025-01-06 07:25:00"
favorite_time: "2025-01-22T18:34:56"
source_url: "http://mp.weixin.qq.com/s?..."
---
```

在此之上还会生成一层按技术方向分类的索引（`knowledge-base/`、`索引/`），
链接指向本地文件，正文本体不会被移动，所以重新分类不影响已抓的内容。

## 环境要求

- macOS（微信管线依赖 macOS 的 LLDB 和微信客户端；另两条管线跨平台）
- Python 3，只用标准库，不需要 `pip install`

## 隐私

这些脚本处理的是你的全部收藏历史，请当作隐私数据对待。

- 只读本机数据，不上传任何内容，不修改微信数据库和浏览器书签文件。
- 账号 ID 之类的个人标识都收在 `.env` 里，仓库里只有 `.env.example` 模板。
- 所有 `output/`、`.env`、`*.json`、`*.db` 和 `*cookies*.json` 都已被 `.gitignore` 忽略。
  提交前确认一下 `git status`，别把导出结果或 Cookie 带进仓库——浏览器里复制的
  Cookie 里那串 `sessionid` 等价于账号密码。
- 微信管线要求关闭 SIP，这会降低 macOS 的系统保护等级。取到密钥后建议立刻进恢复模式
  执行 `csrutil enable` 打回来。

## 许可

仓库里的代码用 [MIT](LICENSE)。

这只覆盖工具本身。脚本抓到 `output/` 里的内容版权属于原作者，留着自己用就好，别去
再分发——那个目录被 `.gitignore` 忽略也正是这个原因。
