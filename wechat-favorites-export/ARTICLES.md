# 微信文章正文抓取

把 `output/favorites.json` 里的公众号文章链接批量抓下来，转成本地 Markdown。
脚本：`scripts/fetch_articles.py`，零第三方依赖，只用 Python 标准库。

## 快速开始

```bash
cd wechat-favorites-export

python3 scripts/fetch_articles.py --limit 20   # 先试跑 20 篇
python3 scripts/fetch_articles.py              # 全量抓取
```

结果按收藏年份分目录：

```
output/articles/
├── 2017/
│   └── 2017-08-14-使用 Headless Chrome 进行自动化测试.md
├── 2025/
├── 2026/
├── 索引/                  # 分类索引，见下文
├── .fetch_state.json     # 进度记录，删掉会全部重抓
└── 未抓取清单.md          # 失效和出错的条目
```

## 分类索引

```bash
python3 scripts/build_article_index.py
```

在 `output/articles/索引/` 下生成一层按技术方向分类的索引，链接指向本地文章文件，
文章本体不会被移动。分类规则直接复用 `build_knowledge_base.py`，
所以索引结果和 `knowledge-base/` 目录保持一致。

收藏元数据里信息太少、无法定性的条目，会用抓回来的文章摘要再判一次，
仍判不出来的进 `索引/99-待确认.md`。

重新抓取文章后再跑一次这个脚本即可刷新索引。

每篇文章带 YAML frontmatter，方便 Obsidian / Logseq 之类的工具索引：

```yaml
---
title: "TypeScript 技巧：让代码库整洁 10 倍"
account: "前端小石匠"
author: "Amaresh Adak"
publish_time: "2025-01-06 07:25:00"
favorite_time: "2025-01-22T18:34:56"
source_url: "http://mp.weixin.qq.com/s?..."
favorite_id: "1980"
---
```

## 常用参数

| 参数 | 说明 |
| --- | --- |
| `--limit N` | 只处理 N 条，用来试跑 |
| `--since 2021-01-01` | 只抓这个日期之后收藏的（2016 年前的基本都失效了） |
| `--workers 4` | 并发线程数，默认 4 |
| `--interval 0.5` | 全局请求间隔秒数，调小有被限流的风险 |
| `--download-images` | 把配图存到 `<年份>/assets/`，正文引用改为本地路径 |
| `--retry-failed` | 只重试上次网络出错的条目 |
| `--include-dead` | 连同判定失效的一起重试 |

## 断点续传

进度存在 `output/articles/.fetch_state.json`，按收藏 ID 记录状态：

- `ok` —— 已成功，再次运行会跳过
- `dead` —— 文章已被删除，不会重试，除非加 `--include-dead`
- `error` —— 网络或解析出错，下次运行自动重试

中途 `Ctrl+C` 会先保存进度再退出，重新运行接着抓。

## 关于失效链接

微信文章链接失效后只返回一个固定的提示页，没有正文。实测各年份情况：

| 收藏年份 | 可抓取情况 |
| --- | --- |
| 2014–2016 | 基本全部失效 |
| 2017–2020 | 部分失效 |
| 2021 至今 | 基本都能抓到 |

判定方式是看页面里有没有 `id="js_content"` 和 `id="activity-name"`，
两者同时存在才算有效文章。这类失效是文章被作者删除或公众号迁移导致的，无法恢复。

## 图片

默认只在 Markdown 里保留 `mmbiz.qpic.cn` 的远程地址。这些地址有防盗链，
在部分 Markdown 编辑器里可能显示不出来。想离线保存就加 `--download-images`，
脚本会带上 Referer 下载，图片存到对应年份的 `assets/` 目录。

3000 多篇文章配图总量在几 GB 量级，抓取时间也会明显变长，按需开启。

## 实现说明

- 文章页直接 GET 就能拿到完整 HTML，不需要登录凭证，也不需要 wechat-article-exporter
  那套代理。那个项目解决的是「按公众号批量翻页」的问题，需要登录态；
  已知单篇 URL 的情况下用不上。
- 正文在 `<div id="js_content">` 里，图片用 `data-src` 而不是 `src`。
- 发布时间从页面里的 `var ct = "时间戳"` 取。
- HTML 转 Markdown 是脚本内自己实现的，因为微信正文全是 `section` 套 `section`
  加行内样式，没有语义化结构，通用转换库效果也一般。
