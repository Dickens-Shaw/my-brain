# 抓取书签网页正文

`scripts/fetch_pages.py` 读 `output/bookmarks.json`，把每条书签指向的网页抓下来转成 Markdown，存到 `output/pages/<收藏年份>/<日期>-<标题>.md`。

## 用法

```bash
python3 scripts/fetch_pages.py --limit 20            # 先小批量试跑
python3 scripts/fetch_pages.py                      # 全量抓取
python3 scripts/fetch_pages.py --folder 书签栏/进阶   # 只抓某个书签文件夹
python3 scripts/fetch_pages.py --host juejin.cn      # 只抓某个域名
python3 scripts/fetch_pages.py --since 2023-01-01    # 只抓这之后收藏的
python3 scripts/fetch_pages.py --download-images     # 连配图一起存到本地
python3 scripts/fetch_pages.py --retry-failed        # 只重试上次出错的
python3 scripts/fetch_pages.py --workers 8 --interval 1.5
```

抓取过程可以随时 Ctrl+C，进度写在 `output/pages/.fetch_state.json`，下次接着跑。

## 正文是怎么找出来的

微信公众号只有一种页面结构，锁定 `#js_content` 就行。书签跨上千个站点，没有这种确定性，所以分三层：

1. **选择器提示**：先按一份常见容器名单找，`js_content`、`markdown-body`、`content_views`、`RichText` 之类，覆盖微信、掘金、CSDN、博客园、简书、SegmentFault、知乎、GitHub、VuePress 这些高频来源。命中就直接用，这条路最准。
2. **readability 打分**：没命中就退到通用算法。给每个段落按文本长度和标点数量打分，往上传给父节点（父节点全额、祖父一半、曾祖三分之一），再按类名（`article`、`content` 加分，`sidebar`、`comment`、`share` 减分）和链接密度调整，取最高分的节点。
3. **兜底**：还不行就依次试 `<article>`、`<main>`、`<body>` 里文本最多的那个。

选中之后还要清一遍：摘掉 `nav`、`aside`、`footer`、`form`，摘掉类名明显是推荐位又没多少正文的块，摘掉链接密度超过 85% 的纯链接堆（相关阅读、标签云）。

HTML→Markdown 是自己写的，处理了标题、列表嵌套、代码块语言标注、表格、引用、图片懒加载属性（`data-src` / `data-original` / `srcset`），并且会把 `link.juejin.cn?target=` 这类站内跳转还原成真实地址。

## 状态与跳过规则

每条书签有四种结果：

| 状态 | 含义 | 会不会自动重试 |
|------|------|----------------|
| `ok` | 抓到正文并写盘 | 不会 |
| `dead` | 页面已删除、需要登录、正文过短、返回 403/404 | 加 `--include-dead` 才会 |
| `skip` | 主动跳过，不是页面本身的问题 | 加 `--include-skipped` 才会 |
| `error` | 网络错误、超时、5xx | 会（下次直接跑就重试） |

默认跳过这几类，因为抓了也没有正文：

- **站点首页**：路径是 `/` 且没有查询参数。书签指向首页时，链接本身才是价值所在，抓下来只会得到导航和推荐位。想抓的话加 `--include-homepage`。
- **在线应用**：ChatGPT、Gemini、Figma、Gmail 这类，页面内容全靠登录后的 JS 渲染。
- **视频和社交**：B 站、YouTube、微博、小红书等。
- **电商**：淘宝、京东、亚马逊等。
- **文件链接**：`.pdf`、`.zip`、`.mp4`、图片等。
- **内网地址**：`localhost`、`127.*`、`192.168.*`、`.local`。

名单在 `fetch_pages.py` 的 `SKIP_HOSTS`。临时增删：

```bash
python3 scripts/fetch_pages.py --skip-hosts example.com,foo.cn
python3 scripts/fetch_pages.py --no-skip-list     # 忽略内置名单，全部尝试
```

## 需要登录的站点

知乎、语雀这类站点不带 Cookie 会直接返回 403。可以写一个 JSON 文件按域名配 Cookie：

```json
{
  "zhihu.com": "z_c0=xxx; d_c0=yyy",
  "yuque.com": "_yuque_session=zzz"
}
```

域名是后缀匹配的，写 `zhihu.com` 就同时覆盖 `www.zhihu.com` 和 `zhuanlan.zhihu.com`，不用分开列。Cookie 只发给匹配的域名，不会随请求乱发。

怎么拿到 Cookie：

1. 在 Chrome 里打开并登录 <https://www.zhihu.com>。
2. F12 → Network，刷新页面，点第一条 `www.zhihu.com` 请求。
3. 在 Request Headers 里找 `Cookie`，整行值复制出来。比一个个挑 `z_c0`、`d_c0` 更稳，有些站点还依赖别的字段。
4. 存成 JSON 文件，路径放在仓库外面。

然后先小批量验证，再跑全量：

```bash
# 这些条目上次被判为 403 失效，所以要加 --include-dead
python3 scripts/fetch_pages.py --cookies ~/.chrome-bookmarks-cookies.json --host zhihu --include-dead --limit 3
python3 scripts/fetch_pages.py --cookies ~/.chrome-bookmarks-cookies.json --host zhihu --include-dead
```

这个文件等同于账号凭证，别提交进仓库（`.gitignore` 已经忽略了 `*cookies*.json`）。知乎的 `z_c0` 有效期大概几个月，过期后重新复制一次即可。

## 限速

书签跨很多域名，所以限速是**按域名**的：同一个域名的请求间隔 `--interval` 秒（默认 1.5），不同域名之间并行。这样 8 个线程跑起来既不会把某个站点打爆，也不用像全局排队那样白等。

抓不到东西通常不是限速问题，而是这几种：CSDN 会返回 521 反爬、知乎需要登录、老博客证书过期（脚本会自动降级重试一次）、站点已经关停。跑完看 `output/pages/未抓取清单.md`。

## 输出格式

```markdown
---
title: "React Hooks 详解 【近 1W 字】+ 项目实战"
site: "juejin.cn"
author: "秋天不落叶"
publish_time: "2019-11-01 08:08:44"
bookmark_time: "2021-01-11 13:33:42"
folder: "书签栏/进阶/React"
source_url: "https://juejin.cn/post/6844903985338400782"
bookmark_id: "6a603b57-fd49-45b6-9b71-102329618afd"
fetched_at: "2026-07-28 16:39:11"
digest: "如果你在编写函数组件并意识到需要向其添加一些 state..."
---

# React Hooks 详解 【近 1W 字】+ 项目实战

> 来源：juejin.cn ｜ 作者：秋天不落叶 ｜ 发布：2019-11-01 08:08:44

> 原文：https://juejin.cn/post/6844903985338400782
>
> 书签位置：书签栏/进阶/React

---

（正文）
```

`publish_time` 依次从 JSON-LD 的 `datePublished`、`article:published_time`、`og:release_date` 里取，取不到就留空。正文里的 `h1` 会降成 `h2`，保证一个文件只有一个顶层标题。
