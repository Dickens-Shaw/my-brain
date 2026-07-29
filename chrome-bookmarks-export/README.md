# Chrome 书签批量导出方案

从本地 Chrome 书签导出清单，可选把书签指向的网页正文抓成 Markdown，再按技术栈整理成知识库目录。

和同目录的 `wechat-favorites-export` 是同一套思路：三个阶段各自独立，中间用 JSON 衔接，任何一步都能单独重跑。

```
Bookmarks 文件 ──→ bookmarks.json ──→ pages/*.md ──→ knowledge-base/
   extract_bookmarks     fetch_pages     build_knowledge_base
```

实测环境：

- 用户数据目录：`~/Library/Application Support/Google/Chrome`
- 两个 Profile 分别 1046 条和 1006 条，高度重合，去重后 1049 条
- 已完整跑通：抓到正文 606 篇（约 676 万字），分类纳入知识库 876 条

## 和微信方案的区别

微信收藏库是 SQLCipher 加密的，要关 SIP、重签名、用 LLDB 抓密钥。Chrome 书签就是一个明文 JSON 文件，直接读就行，**不需要任何系统权限调整**。

代价在抓正文这一步：微信只有一种页面结构，锁定 `#js_content` 就够了；书签横跨上千个站点，所以 `fetch_pages.py` 用了 readability 式的打分来挑正文节点，细节见 [PAGES.md](PAGES.md)。

## 快速开始

```bash
cd chrome-bookmarks-export
bash run.sh                 # 导出书签 + 整理知识库
FETCH=1 bash run.sh         # 连网页正文一起抓
```

产物：

| 文件 | 内容 |
|------|------|
| `output/bookmarks.json` | 完整字段，后面两个阶段的输入 |
| `output/bookmarks.csv` | 表格，方便丢进 Excel 或其他工具 |
| `output/bookmarks.md` | 按原始书签文件夹分节的可读清单 |
| `output/pages/**/*.md` | 抓到的网页正文，按收藏年份分目录 |
| `output/knowledge-base/` | 按技术栈分类后的知识库目录 |

## 阶段一：导出书签清单

```bash
python3 scripts/extract_bookmarks.py inspect      # 列出 Profile 和书签数量
python3 scripts/extract_bookmarks.py export       # 导出全部 Profile
```

常用参数：

```bash
# 只导出某个 Profile（名字用 inspect 看到的目录名或显示名）
python3 scripts/extract_bookmarks.py export --profile Default

# 换浏览器，支持 chrome / chrome-canary / edge / brave / chromium / vivaldi / arc
python3 scripts/extract_bookmarks.py --browser edge export

# 手动指定用户数据目录（比如从别的机器拷过来的）
python3 scripts/extract_bookmarks.py --user-data-dir ~/backup/Chrome export

# 解析「书签管理器 → 导出书签」生成的 HTML，可以处理任意浏览器的导出文件
python3 scripts/extract_bookmarks.py export --html ~/Downloads/bookmarks_2026_7_28.html

# 保留重复链接 / 保留 javascript: 和 chrome:// 之类的非网页书签
python3 scripts/extract_bookmarks.py export --keep-duplicates --keep-non-http
```

每条书签导出这些字段：

```json
{
  "id": "fdbe2dc8-26a4-45de-bfb8-98eab54c74df",
  "type": "url",
  "time": "2025-01-28 00:11:06",
  "title": "DeepSeek",
  "url": "https://chat.deepseek.com/",
  "source": "chat.deepseek.com",
  "folder": "书签栏",
  "root": "bookmark_bar",
  "profile": "Default",
  "browser": "chrome",
  "last_used": "2026-07-01 16:52:09",
  "also_in": "书签栏/AI"
}
```

`id` 用的是 Chrome 的 `guid`，跨机器同步也稳定，所以断点续传的状态文件能一直复用。多个 Profile 或多个文件夹里有同一条链接时，只保留一条，其余位置记在 `also_in` 里。

Chrome 正在运行也能导出，`Bookmarks` 文件不加锁。如果刚在浏览器里改过书签没看到变化，Chrome 有几秒的落盘延迟，等一下再跑。

## 阶段二：抓网页正文

见 [PAGES.md](PAGES.md)。最简形式：

```bash
python3 scripts/fetch_pages.py --limit 20     # 先小批量试跑
python3 scripts/fetch_pages.py                # 全量
```

1049 条书签跑下来的实际分布，可以当作预期参考：

| 结果 | 数量 | 说明 |
|------|------|------|
| 成功 | 606 | 正文中位数 5596 字 |
| 跳过 | 202 | 首页、在线应用、视频站、文件链接 |
| 失效 | 90 | 404，或页面被删 |
| 出错 | 151 | 多数是 SPA 页面提取不到正文，其余是超时和 5xx |

十年前收藏的链接失效率本来就高，2020 年之后的书签成功率明显更好。全量一轮约 6~7 分钟。

其中知乎 92 条一开始全是 403，配了 Cookie 后 91 条抓到（剩 1 条是专栏首页，本来就没有正文），做法见 [PAGES.md](PAGES.md#需要登录的站点)。

## 阶段三：整理知识库

```bash
python3 scripts/build_knowledge_base.py --clean
```

分类信号是「标题 + 书签文件夹路径」。书签文件夹是自己手工分过的类，可靠性和标题相当，这是书签相比微信收藏最大的优势。标题和文件夹都没信号时，先按域名兜底（`github.com` 归开源项目、`developer.mozilla.org` 归前端），再退到 URL 里的英文词。

每条记录都写了「归类依据」，分错了照着依据改 `scripts/build_knowledge_base.py` 顶部的规则表即可。判断不了的进 `99-待确认.md`，生活娱乐类进 `98-已过滤-生活娱乐.md`。

已经抓到正文的条目，知识库里会附上本地 Markdown 的相对链接，所以建议先跑阶段二再跑阶段三。

## 隐私说明

- 脚本只读本机数据，不上传任何内容，不修改浏览器的书签文件。
- 书签里可能有内网地址、带 token 的分享链接、账号页面，`output/` 已经被 `.gitignore` 忽略。
- 如果配了 `--cookies`，那个文件等同于账号凭证，别提交进仓库。
