---
name: wechat-export-articles
description: 在 macOS 微信客户端里，把收藏的公众号文章导出成本地 Markdown 和分类索引。Use when the user wants to export WeChat favorites articles, 公众号文章, mp.weixin.qq.com links, fetch_articles.py, or build the article index from exporters/wechat-favorites.
---

# 导出微信收藏文章

工作目录固定为 `exporters/wechat-favorites`。用简体中文引导用户。先检查已有产物，缺什么再补哪一步，不要从头重做。

脚本不在这个 skill 目录里，而在仓库的 `exporters/wechat-favorites/scripts/`。clone 本仓库就有脚本，不要再复制一份进 skill。对方启用 skill 时在仓库根目录运行 `bash scripts/install-skills.sh`。

文章正文来自收藏里的 URL，直接 GET 即可，不需要微信登录态，也不要改用 wechat-article-exporter。

## 进度清单

复制这份清单并逐项勾掉：

```
- [ ] 1. 确认 favorites.json 可用
- [ ] 2. 试抓 20 篇并抽查一篇
- [ ] 3. 按用户范围全量抓取
- [ ] 4. 生成分类索引
- [ ] 5. 汇报成功、失效、出错数量
```

## 1. 确认收藏清单

```bash
cd exporters/wechat-favorites
test -f output/favorites.json && echo OK || echo MISSING
```

`output/favorites.json` 已存在就进入第 2 步。

不存在时，先导出收藏库。微信要保持登录。多个账号目录时加上 `--account SomeName_1a2b`，或让用户写到仓库根目录 `.env` 的 `WECHAT_ACCOUNT`。

```bash
python3 scripts/extract_favorites.py inspect
```

然后按缓存情况分支：

- `~/.wcdb-key-tool/wechat-passphrase.json` 已存在：不要关 SIP，不要让用户重新登录。权限若是 root，先 `sudo chown "$(whoami)" ~/.wcdb-key-tool/wechat-passphrase.json`。然后：

```bash
python3 scripts/extract_favorites.py capture
python3 scripts/extract_favorites.py decrypt
python3 scripts/extract_favorites.py export
```

`capture` 会用缓存 passphrase 派生密钥。只有日志写明校验失败时，才进入下面的首次抓取。

- 没有可用 passphrase：这是首次。停下来让用户做完这三件事，代理不要自己关 SIP，也不要替用户输入确认词。
  1. 恢复模式终端执行 `csrutil disable` 后重启。Apple Silicon：关机，长按电源键，选项 → 实用工具 → 终端。
  2. `bash scripts/resign_wechat.sh`，确认词是 `RESIGN_WECHAT`。这会去掉微信的 Hardened Runtime，自动更新后可能要重做。
  3. 打开微信并登录，然后运行 `bash run.sh`。脚本开始等待后，用户在微信里退出登录再扫码登录，用来触发 `CCKeyDerivationPBKDF`。默认等 180 秒。

导出完成后提醒用户可以回恢复模式执行 `csrutil enable`。`output/` 含隐私，已被 gitignore，不要提交。

成功标准：`output/favorites.json`、`favorites.csv`、`favorites.md` 都在，日志里有收藏总数和带 URL 的条数。

## 2. 试抓

```bash
python3 scripts/fetch_articles.py --limit 20
```

零第三方依赖，用系统 `python3`。打开 `output/articles/` 里新生成的一篇，确认有 YAML frontmatter（`title`、`account`、`source_url`、`favorite_id`）和正文。没有正文就停，不要直接全量。

## 3. 全量抓取

默认抓全部收藏 URL。2016 年及更早的链接基本已失效，用户没指定范围时建议：

```bash
python3 scripts/fetch_articles.py --since 2021-01-01
```

用户要全部年份就去掉 `--since`。其它参数：

| 需求 | 参数 |
| --- | --- |
| 只试 N 条 | `--limit N` |
| 并发，默认 4 | `--workers 4` |
| 请求间隔，默认 0.6 秒；调小可能被限流 | `--interval 0.6` |
| 配图落到 `<年份>/assets/` | `--download-images` |
| 只重试上次网络错误 | `--retry-failed` |
| 连失效链接一起重试 | `--include-dead` |

配图默认只留 `mmbiz.qpic.cn` 远程地址，编辑器里可能因防盗链显示不出来。用户明确要求离线图片再加 `--download-images`。三千篇文章的配图可达数 GB，先说明耗时再执行。

进度在 `output/articles/.fetch_state.json`：

- `ok`：跳过
- `dead`：作者删除或公众号迁移，不重试，除非 `--include-dead`
- `error`：下次自动重试

`Ctrl+C` 会先保存进度。中断后用同一命令接着跑。不要删 `.fetch_state.json`，除非用户要全部重抓。

失效判定：页面同时有 `id="js_content"` 和 `id="activity-name"` 才算有效。失效无法恢复，不要反复重试同一批 dead。

## 4. 分类索引

抓取有新增后再跑：

```bash
python3 scripts/build_article_index.py
```

索引写到 `output/articles/索引/`，只是链接，不移动文章。判不出来的在 `索引/99-待确认.md`。

## 5. 汇报

看 `output/articles/未抓取清单.md` 和 `.fetch_state.json`，告诉用户：

- 成功篇数和目录（按收藏年份）
- dead 数量，以及它们无法恢复
- error 数量；还有 error 时问要不要 `--retry-failed`
- 索引路径

每篇文章的 frontmatter 类似：

```yaml
---
title: "标题"
account: "公众号"
author: "作者"
publish_time: "2025-01-06 07:25:00"
favorite_time: "2025-01-22T18:34:56"
source_url: "http://mp.weixin.qq.com/s?..."
favorite_id: "1980"
---
```
