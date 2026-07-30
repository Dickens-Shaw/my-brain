# 掘金点赞与收藏集批量导出

把掘金个人主页「赞过的文章」和「收藏集」整个拉下来，正文转成本地 Markdown。
零第三方依赖，只用 Python 标准库。

两条线共用同一套正文抓取逻辑，区别只在清单来源和归档方式：点赞的按发布年份分目录，
收藏集的按收藏集名分目录。

## 快速开始

先填好自己的掘金用户 ID，就是个人主页地址 `https://juejin.cn/user/<数字>` 里的那串数字：

```bash
cp ../../.env.example ../../.env      # 然后编辑 JUEJIN_USER_ID=
```

```bash
cd exporters/juejin-articles
bash run.sh                                 # 点赞 + 收藏集，一把梭
```

拆开跑也一样，四步各自独立：

```bash
python3 scripts/fetch_list.py               # 点赞清单
python3 scripts/fetch_articles.py           # 点赞正文

python3 scripts/fetch_collections.py        # 收藏集清单
python3 scripts/fetch_articles.py --input output/collections.json \
    --output output/collections --group-by collection
```

只想跑一条线：

```bash
COLLECTIONS=0 bash run.sh                   # 只要点赞
DIGG=0 bash run.sh                          # 只要收藏集
```

`run.sh` 和两个清单脚本都会自己读仓库根目录的 `.env`，也认环境变量：

```bash
JUEJIN_USER_ID=123456 bash run.sh           # 不想建 .env 就临时指定
```

## 产出结构

```
output/
├── articles.json          # 点赞清单，完整字段，fetch_articles.py 的输入
├── articles.csv           # 点赞清单，表格版
├── articles.md            # 点赞清单，按发布年份分组的可读版
├── articles/
│   ├── 2018/
│   ├── 2024/
│   │   └── 2024-11-17-为什么组件库打包用 Rollup 而不是 Webpack.md
│   ├── .fetch_state.json  # 进度记录，删掉会全部重抓
│   └── 未抓取清单.md       # 失效和出错的条目
├── collections.json       # 收藏集清单，摊平成一篇一条
├── collections.csv        # 同上，首列是所属收藏集
├── collections.md         # 按收藏集分组的可读版
└── collections/
    ├── 前端工程化/
    │   └── 2021-08-31-Webpack 原理系列九-Tree-Shaking 实现原理.md
    ├── ES6/
    └── .fetch_state.json
```

点赞文章按**发布年份**分目录，收藏集文章按**收藏集名**分目录，文件名都是
`发布日期-标题.md`。

点赞列表接口不返回点赞时间，所以只能用发布时间归档；点赞顺序记在 frontmatter 的
`digg_rank` 里，也保留在 `articles.md` 的组内排序中。收藏集同理，组内顺序记在
`collection_rank`。

一篇文章可以同时在多个收藏集里。这种只导出一份文件（放在第一个收藏集下），
其余归属记进 frontmatter 的 `collections` 数组，不重复占地方。
点赞和收藏集是两套独立的输出目录和进度文件，两边都有的文章会各存一份。

每篇带 YAML frontmatter，方便 Obsidian / Logseq 索引：

```yaml
---
title: "为什么组件库打包用 Rollup 而不是 Webpack?"
site: "juejin.cn"
author: "神光的幸福生活公号"
author_url: "https://juejin.cn/user/2788017216685118"
category: "前端"
tags: ["前端", "JavaScript", "Webpack"]
publish_time: "2024-11-17 11:31:05"
source_url: "https://juejin.cn/post/7437903515169325082"
article_id: "7437903515169325082"
digg_rank: 1
fetched_at: "2026-07-28 18:12:26"
read_time: "7分钟"
digest: "Rolup 是一个打包工具，类似 Webpack。……"
---
```

收藏集导出的这两行换成归属信息，其余字段一致：

```yaml
collections: ["前端工程化"]
collection_rank: 3
```

## Cookie 只有私有收藏集才需要

三个列表接口都是公开的：点赞 `interact_api/v1/digg/query_page`、收藏集列表
`interact_api/v2/collectionset/list`、收藏集详情 `interact_api/v2/collectionset/detail`。
实测去掉 `a_bogus` 签名、`msToken`、`x-secsdk-csrf-token` 和整串 Cookie 之后照样能翻页，
只留 `aid` / `uuid` 两个查询参数就够。

文章页 `https://juejin.cn/post/<id>` 是服务端渲染的，正文完整放在
`div.markdown-body` 里，也不需要登录态。

唯一的例外是 `permission=1` 的私有收藏集——掘金默认建的那个「我的收藏」通常就是私有的，
不带登录态时它压根不出现在列表里。想连它一起导出，就在 `.env` 里放一份浏览器 Cookie：

```bash
JUEJIN_COOKIE="sessionid=...; sid_tt=...; uid_tt=..."
```

`run.sh` 检测到这个变量就自动给收藏集清单加 `--with-cookie`。三个字段就够了，
不用把整串 Cookie 都贴进来。

不填也能跑，只是私有收藏集会缺失，脚本会明确提示一句。仓库里不存任何账号凭证，
`.env` 已被 `.gitignore` 忽略——别把 Cookie 提交进来，那串 `sessionid` 等价于账号密码。

## 常用参数

### scripts/fetch_list.py

| 参数 | 说明 |
| --- | --- |
| `--user-id` | 掘金用户 ID，默认取 `JUEJIN_USER_ID` 或 `.env` |
| `--item-type` | `2` 文章（默认）、`4` 沸点、`15` 视频 |
| `--max-pages N` | 只翻 N 页，用来试跑 |
| `--interval 0.4` | 翻页间隔秒数 |

### scripts/fetch_collections.py

| 参数 | 说明 |
| --- | --- |
| `--user-id` | 同上 |
| `--with-cookie` | 带上 `JUEJIN_COOKIE` 请求，私有收藏集只有这样才看得到 |
| `--interval 0.4` | 请求间隔秒数 |

### scripts/fetch_articles.py

| 参数 | 说明 |
| --- | --- |
| `--input` | 清单路径，默认 `output/articles.json`，收藏集用 `output/collections.json` |
| `--output` | 输出目录，默认 `output/articles` |
| `--group-by` | `year` 按发布年份分目录（默认）、`collection` 按收藏集分目录 |
| `--limit N` | 只处理 N 条，用来试跑 |
| `--workers 4` | 并发线程数，默认 4 |
| `--interval 0.5` | 全局请求间隔秒数，调小有被限流的风险 |
| `--since 2020-01-01` | 只抓这个发布日期之后的 |
| `--category 前端` | 只抓某个分类 |
| `--tag React` | 只抓带某个标签的 |
| `--collection 前端工程化` | 只抓某个收藏集的 |
| `--download-images` | 把配图存到 `<子目录>/assets/`，正文引用改为本地路径 |
| `--retry-failed` | 只重试上次网络出错的条目 |
| `--include-dead` | 连同判定失效的一起重试 |

`run.sh` 认这几个环境变量：`JUEJIN_USER_ID`（或等价的 `USER_ID`）、`JUEJIN_COOKIE`、
`OUTPUT_DIR`、`LIMIT`、`WORKERS`、`IMAGES=1`（下载图片）、`FETCH=0`（只拉清单不抓正文）、
`DIGG=0`（跳过点赞）、`COLLECTIONS=0`（跳过收藏集）。写在仓库根目录 `.env` 里等效。

## 断点续传

进度存在各自输出目录下的 `.fetch_state.json`（`output/articles/`、`output/collections/`），
按 article_id 记录状态：

- `ok` —— 已成功，再次运行会跳过
- `dead` —— 文章已删除或转为不可见，不会重试，除非加 `--include-dead`
- `error` —— 网络或解析出错，下次运行自动重试

中途 `Ctrl+C` 会先保存进度再退出，重新运行接着抓。

## 关于抓不到的文章

点赞清单接口报告 650 条，实际能枚举出 647 条，差额是作者注销或文章被彻底删除，
接口直接不返回这一条。这 647 条实测全部抓到了正文，没有失效条目。

收藏集这边 15 个集子共 37 篇（其中两个是空集），条数和接口报告的
`post_article_count` 完全对得上，也全部抓到了正文。

真遇到抓不动的会归成三类，写进对应输出目录的 `未抓取清单.md`：

- 作者删稿或改成仅自己可见，页面里没有 `div.markdown-body`
- 外链转载（`link_url` 非空），掘金站内只有摘要，正文在原站
- HTTP 404 / 403

连续抓几百篇之后掘金会短暂限流，表现是文章页只回一个 2KB 左右的空壳、
不含 `markdown-body`。这种会记成 `error`，等一会儿重跑就能补上。

## 图片

默认只在 Markdown 里保留 `p*-juejin.byteimg.com` 的远程地址，这些地址没有
防盗链，正常渲染。想离线保存就加 `--download-images`。

掘金图床路径统一是 `.awebp`，真实格式要看响应内容，脚本按文件头判断后缀。
地址尾部的 `#?w=816&h=674&s=127704&e=png` 是展示参数，下载时会去掉。

## 实现说明

- 正文容器固定是 `div.markdown-body`，不用像 `exporters/chrome-bookmarks`
  那样靠 readability 打分猜位置。
- 掘金渲染出来的 HTML 是语义化的：`pre > code` 带 `lang` 属性，标题是真的
  `h2`/`h3`，表格是真的 `table`，所以转 Markdown 保真度很高，代码块的语言标注
  也能带过来。
- 高亮用的 `span.hljs-*` 在取 `pre` 内文本时自然被剥掉，代码是干净的。
- 站外链接掘金都套了一层 `link.juejin.cn?target=`，脚本会解开还原真实地址。
  标题锚点那种 `target` 为空的跳转链接没有意义，直接丢掉只留文字。
- 正文里的 `h1` 统一降级成 `h2`，保证一篇文章的大纲只有一个顶层标题。
- 少数文章作者自己的 markdown 就是坏的（比如把说明文字写进了代码块），
  脚本按源站渲染结果如实转换，不做猜测性修复。
- `collectionset/detail` 会顺手把作者的 markdown 原稿塞在 `mark_content` 里，看着诱人，
  但里面混着掘金的 `theme:` 头和站内推广段落，而且点赞接口没有这个字段。
  为了两条线的产出格式完全一致，收藏集也照样走文章页 → Markdown 这条路。

## 产出体检

点赞 647 篇跑完约 4.3 分钟，正文合计约 1026 万字符，含 11932 个代码块、178 张表格、
7283 处图片引用。

收藏集 37 篇跑完 20 秒，约 68 万字符，含 796 个代码块、18 张表格、473 处图片引用。

两边校验脚本结论一致：代码块围栏全部配平、无残留跳转链接、无空正文。
