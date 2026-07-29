#!/usr/bin/env python3
"""把 output/articles.json 里的掘金文章正文抓下来，保存为本地 Markdown。

掘金文章页是服务端渲染的，正文完整地放在 `div.markdown-body` 里，
标签也是语义化的（pre/code 带 lang、h2 带 data-id），所以不需要登录凭证，
也不用像书签抓取那样靠打分猜正文位置。

零第三方依赖，只用 Python 标准库。

用法：
    python3 scripts/fetch_articles.py --limit 20          # 先小批量试跑
    python3 scripts/fetch_articles.py                     # 全量抓取
    python3 scripts/fetch_articles.py --download-images   # 连图片一起存
    python3 scripts/fetch_articles.py --retry-failed      # 只重试上次出错的
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
BASE_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}

STATUS_OK = "ok"
STATUS_DEAD = "dead"
STATUS_ERROR = "error"

MIN_BODY_CHARS = 60


class RateLimiter:
    """全局请求间隔控制。所有请求都打在掘金一个域名上，所以统一排队。"""

    def __init__(self, interval: float):
        self.interval = interval
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next_at - now)
            self._next_at = max(now, self._next_at) + self.interval
        if delay > 0:
            time.sleep(delay)


def http_get(
    url: str,
    limiter: RateLimiter,
    referer: str = "",
    timeout: int = 25,
    max_bytes: int = 8_000_000,
) -> bytes:
    limiter.wait()
    headers = dict(BASE_HEADERS)
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(max_bytes)
        encoding = (response.headers.get("Content-Encoding") or "").lower()
    if encoding == "gzip":
        raw = gzip.decompress(raw)
    elif encoding == "deflate":
        try:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
        except zlib.error:
            raw = zlib.decompress(raw)
    return raw


# ---------------------------------------------------------------- 轻量 DOM

VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}
IGNORE_TAGS = {"script", "style", "noscript", "svg", "canvas", "template", "head"}
BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "details", "div", "dl", "fieldset",
    "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6",
    "header", "hr", "main", "nav", "ol", "p", "pre", "section", "table", "ul",
}
# 掘金正文里的交互控件，抓下来只是噪音
CHROME_CLASSES = re.compile(
    r"code-block-extension|copy-code|katex-html|anchor-link|table-of-content|"
    r"article-end|recommend|advert",
    re.I,
)

MAX_DOM_DEPTH = 150


class Node:
    __slots__ = ("tag", "attrs", "children", "parent", "text")

    def __init__(
        self,
        tag: str,
        attrs: dict[str, str] | None = None,
        parent: "Node | None" = None,
        text: str = "",
    ):
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list[Node] = []
        self.parent = parent
        self.text = text


class DomBuilder(HTMLParser):
    """容错的 HTML 树构建。对 p / li / td / tr 做隐式闭合，孤立结束标签直接忽略。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.stack: list[Node] = [self.root]

    def _implicit_close(self, tag: str) -> None:
        pairs = {
            "li": ("li", "p"),
            "td": ("td", "th", "p"),
            "th": ("td", "th", "p"),
            "tr": ("td", "th", "tr", "p"),
            "dt": ("dt", "dd", "p"),
            "dd": ("dt", "dd", "p"),
        }
        closable = pairs.get(tag) or (("p",) if tag in BLOCK_TAGS else ())
        while len(self.stack) > 1 and self.stack[-1].tag in closable:
            self.stack.pop()

    def _append(self, tag: str, attrs: list[tuple[str, str | None]]) -> Node:
        attr = {key.lower(): (value or "") for key, value in attrs}
        node = Node(tag, attr, self.stack[-1])
        self.stack[-1].children.append(node)
        return node

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._implicit_close(tag)
        node = self._append(tag, attrs)
        if tag not in VOID_TAGS and len(self.stack) < MAX_DOM_DEPTH:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._append(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_TAGS:
            return
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if not data:
            return
        parent = self.stack[-1]
        parent.children.append(Node("#text", parent=parent, text=data))


def parse_dom(html: str) -> Node:
    builder = DomBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


def iter_nodes(root: Node):
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))


def inner_text(node: Node) -> str:
    if node.tag == "#text":
        return node.text
    if node.tag in IGNORE_TAGS:
        return ""
    return "".join(inner_text(child) for child in node.children)


def find_body(root: Node) -> Node | None:
    """定位 div.markdown-body。同名节点有多个时取文本最多的那个。"""
    best: tuple[int, Node] | None = None
    for node in iter_nodes(root):
        if node.tag != "div" or "markdown-body" not in node.attrs.get("class", ""):
            continue
        length = len(inner_text(node).strip())
        if best is None or length > best[0]:
            best = (length, node)
    return best[1] if best else None


# ---------------------------------------------------------------- HTML → Markdown

HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
INLINE_MARKS = {"strong": "**", "b": "**", "em": "*", "i": "*", "del": "~~", "s": "~~", "code": "`"}
LANG_PATTERN = re.compile(r"(?:language|lang|highlight|brush)[-:]([a-z0-9+#]+)", re.I)
KNOWN_LANGS = {
    "js", "jsx", "ts", "tsx", "javascript", "typescript", "json", "json5", "html", "xml", "css", "scss",
    "sass", "less", "stylus", "vue", "svelte", "python", "py", "java", "kotlin", "scala", "groovy", "go",
    "golang", "rust", "c", "cpp", "c++", "csharp", "cs", "objectivec", "swift", "dart", "php", "ruby",
    "perl", "lua", "r", "matlab", "sql", "graphql", "protobuf", "shell", "bash", "sh", "zsh", "fish",
    "powershell", "dockerfile", "nginx", "yaml", "yml", "toml", "ini", "diff", "patch", "makefile",
    "markdown", "md", "vim", "plaintext", "text", "http",
}


def unwrap_redirect(url: str) -> str:
    """掘金站外链接都套了一层 link.juejin.cn?target=，解开拿到真实地址。

    标题锚点之类的地方 target 是空的或是相对路径，跳转链接本身没有意义，返回空串丢掉。
    """
    if urlparse(url).netloc != "link.juejin.cn":
        return url
    target = (parse_qs(urlparse(url).query).get("target") or [""])[0]
    return target if target.startswith(("http://", "https://")) else ""


def code_language(node: Node) -> str:
    markers = [node.attrs.get("class", ""), node.attrs.get("lang", ""), node.attrs.get("data-lang", "")]
    for child in node.children:
        if child.tag == "code":
            markers += [
                child.attrs.get("class", ""),
                child.attrs.get("lang", ""),
                child.attrs.get("data-lang", ""),
            ]

    joined = " ".join(markers)
    match = LANG_PATTERN.search(joined)
    if match and match.group(1).lower() in KNOWN_LANGS:
        return match.group(1).lower()
    for token in re.split(r"[\s,]+", joined.lower()):
        if token in KNOWN_LANGS:
            return token
    return ""


def tidy_markdown(text: str) -> str:
    """收尾清理，只作用在代码块之外。"""
    chunks = text.split("```")
    for index in range(0, len(chunks), 2):
        chunk = chunks[index]
        for mark in (r"\*\*", "~~"):
            chunk = re.sub(rf"{mark}([ \t]*){mark}", r"\1", chunk)
        chunks[index] = chunk
    return "```".join(chunks)


class MarkdownWriter:
    def __init__(self, base_url: str, images: list[str] | None = None):
        self.base_url = base_url
        self.parts: list[str] = []
        self.images = images if images is not None else []
        self.list_stack: list[dict] = []
        self.marks: list[str] = []

    # -- 工具 -------------------------------------------------------
    def nl(self, count: int = 1) -> None:
        self.parts.append("\n" * count)

    def absolute(self, url: str) -> str:
        url = (url or "").strip()
        if not url or url.startswith(("data:", "javascript:", "#")):
            return ""
        if url.startswith("//"):
            return "https:" + url
        if not url.startswith(("http://", "https://")):
            try:
                url = urljoin(self.base_url, url)
            except ValueError:
                return ""
        return unwrap_redirect(url)

    def capture(self, node: Node) -> str:
        """渲染子节点后把结果取回来，用于链接文字、表格单元格这类内联场景。"""
        mark = len(self.parts)
        for child in node.children:
            self.walk(child)
        text = "".join(self.parts[mark:])
        del self.parts[mark:]
        return text

    # -- 具体标签 ---------------------------------------------------
    def write_image(self, node: Node) -> None:
        src = ""
        for key in ("data-src", "data-original", "src"):
            src = self.absolute(node.attrs.get(key, ""))
            if src:
                break
        if not src:
            return
        self.images.append(src)
        alt = re.sub(r"\s+", " ", node.attrs.get("alt", "")).strip()
        self.nl(2)
        self.parts.append(f"![{alt}]({src})")
        self.nl(2)

    def write_link(self, node: Node) -> None:
        href = self.absolute(node.attrs.get("href", ""))
        text = self.capture(node).strip()
        if not text:
            if href:
                self.parts.append(f"<{href}>")
            return
        if not href:
            self.parts.append(text)
            return
        self.parts.append(f"[{text}]({href})")

    def write_pre(self, node: Node) -> None:
        lang = code_language(node)
        code = inner_text(node).strip("\n")
        if not code.strip():
            return
        self.nl(2)
        self.parts.append(f"```{lang}\n{code}\n```")
        self.nl(2)

    def write_blockquote(self, node: Node) -> None:
        inner = self.capture(node).strip()
        if not inner:
            return
        self.nl(2)
        self.parts.append("\n".join(f"> {line}" if line else ">" for line in inner.split("\n")))
        self.nl(2)

    def write_table(self, node: Node) -> None:
        rows: list[list[str]] = []
        for row in iter_nodes(node):
            if row.tag != "tr":
                continue
            cells = [child for child in row.children if child.tag in ("td", "th")]
            if not cells:
                continue
            rendered = []
            for cell in cells:
                sub = MarkdownWriter(self.base_url, self.images)
                text = sub.capture(cell)
                rendered.append(re.sub(r"\s*\n\s*", " ", text).replace("|", "\\|").strip())
            rows.append(rendered)

        width = max((len(row) for row in rows), default=0)
        if not rows or width < 2:
            for child in node.children:
                self.walk(child)
            return

        self.nl(2)
        for index, row in enumerate(rows):
            padded = row + [""] * (width - len(row))
            self.parts.append("| " + " | ".join(padded) + " |\n")
            if index == 0:
                self.parts.append("| " + " | ".join(["---"] * width) + " |\n")
        self.nl(2)

    def write_list_item(self, node: Node) -> None:
        indent = "  " * max(0, len(self.list_stack) - 1)
        if self.list_stack and self.list_stack[-1]["type"] == "ol":
            self.list_stack[-1]["index"] += 1
            bullet = f"{self.list_stack[-1]['index']}. "
        else:
            bullet = "- "
        inner = self.capture(node).strip()
        if not inner:
            return
        lines = inner.split("\n")
        self.nl()
        self.parts.append(indent + bullet + lines[0])
        for line in lines[1:]:
            self.parts.append("\n" + indent + "  " + line if line else "\n")
        self.nl()

    # -- 主分发 -----------------------------------------------------
    def walk(self, node: Node) -> None:
        tag = node.tag

        if tag == "#text":
            text = node.text.replace("\u200b", "").replace("\xa0", " ")
            if text.strip():
                self.parts.append(re.sub(r"\s+", " ", text))
            elif text and self.parts and not self.parts[-1].endswith((" ", "\n")):
                self.parts.append(" ")
            return

        if tag in IGNORE_TAGS:
            return
        if CHROME_CLASSES.search(node.attrs.get("class", "")):
            return

        if tag == "img":
            self.write_image(node)
            return
        if tag == "br":
            self.nl()
            return
        if tag == "hr":
            self.nl(2)
            self.parts.append("---")
            self.nl(2)
            return
        if tag == "a":
            self.write_link(node)
            return
        if tag == "pre":
            self.write_pre(node)
            return
        if tag == "blockquote":
            self.write_blockquote(node)
            return
        if tag == "table":
            self.write_table(node)
            return

        if tag in HEADINGS:
            text = self.capture(node).strip()
            if text:
                # 文件开头已经用标题占了 h1，正文里的 h1 降一级，保持大纲只有一个顶层
                level = max(2, HEADINGS[tag])
                self.nl(2)
                self.parts.append("#" * level + " " + text)
                self.nl(2)
            return

        if tag in INLINE_MARKS:
            mark = INLINE_MARKS[tag]
            # <strong><strong>x</strong></strong> 重复包裹会变成 ****x****
            if mark in self.marks:
                for child in node.children:
                    self.walk(child)
                return
            self.marks.append(mark)
            text = self.capture(node).strip()
            self.marks.pop()
            if text:
                self.parts.append(f"{mark}{text}{mark}")
            return

        if tag in ("ul", "ol"):
            self.list_stack.append({"type": tag, "index": 0})
            self.nl(2)
            for child in node.children:
                self.walk(child)
            self.list_stack.pop()
            self.nl(2)
            return

        if tag == "li":
            self.write_list_item(node)
            return

        if tag in BLOCK_TAGS:
            self.nl(2)
            for child in node.children:
                self.walk(child)
            self.nl(2)
            return

        for child in node.children:
            self.walk(child)

    def result(self) -> str:
        text = "".join(self.parts)
        text = re.sub(r"[ \t]+\n", "\n", text)
        # 只掐掉整块都是空的代码块。这里必须逐行锚定：不加锚点的 ```\w*\s*``` 会从某个代码块的
        # 结束围栏一路吃到下一个代码块的开始围栏，把两块代码粘成一坨。
        text = re.sub(r"^[ \t]*```\w*[ \t]*\n[ \t]*```[ \t]*$\n?", "", text, flags=re.M)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = text.strip()
        # tidy_markdown 靠围栏个数区分代码内外，围栏不配平会把正文当代码处理，补一个兜底。
        if text.count("```") % 2:
            text += "\n```"
        return tidy_markdown(text)


# ---------------------------------------------------------------- 页面解析

DEAD_HINTS = re.compile(
    r"该内容不存在|内容不存在|文章不存在|已被删除|已删除|审核不通过|无法访问|页面不存在|"
    r"你访问的页面|404",
    re.I,
)


def parse_page(html: str, url: str) -> tuple[str, list[str]] | None:
    """返回 (Markdown 正文, 图片地址)。返回 None 表示这篇抓不到正文。"""
    root = parse_dom(html)
    body_node = find_body(root)
    if body_node is None:
        return None

    writer = MarkdownWriter(url)
    writer.walk(body_node)
    body = writer.result()
    if len(body) < MIN_BODY_CHARS:
        return None
    return body, writer.images


# ---------------------------------------------------------------- 输出


def safe_filename(value: str, limit: int = 80) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r'[/\\:*?"<>|\r\n\t]', "-", value)
    value = re.sub(r"[\x00-\x1f]", "", value)
    value = re.sub(r"\s+", " ", value).strip(" .-")
    if len(value) > limit:
        value = value[:limit].rstrip(" .-")
    return value or "untitled"


def yaml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF8", ".gif"),
    (b"BM", ".bmp"),
)


def sniff_extension(data: bytes, url: str) -> str:
    """掘金图床路径统一是 .awebp，真实格式得看响应内容。"""
    for magic, ext in IMAGE_MAGIC:
        if data.startswith(magic):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:5].lstrip().startswith(b"<svg") or data[:5].lstrip().startswith(b"<?xml"):
        return ".svg"
    match = re.search(r"\.(jpg|jpeg|png|gif|webp|svg|bmp)(?:$|[?#])", urlparse(url).path, re.I)
    return f".{match.group(1).lower()}" if match else ".webp"


def download_images(
    images: list[str],
    body: str,
    asset_dir: Path,
    prefix: str,
    limiter: RateLimiter,
    source_url: str,
) -> tuple[str, int, int]:
    ok = failed = 0
    for index, url in enumerate(dict.fromkeys(images), 1):
        # 掘金图片地址后面挂了 #?w=..&h=.. 这种展示参数，下载时要去掉
        clean_url = url.split("#")[0]
        try:
            data = http_get(clean_url, limiter, referer=source_url, timeout=30)
            if len(data) < 128:
                raise ValueError("图片内容过小")
        except Exception:
            failed += 1
            continue
        name = f"{prefix}-{index:02d}{sniff_extension(data, clean_url)}"
        asset_dir.mkdir(parents=True, exist_ok=True)
        (asset_dir / name).write_bytes(data)
        ok += 1
        body = body.replace(url, f"assets/{name}")
    return body, ok, failed


def render_markdown(item: dict, body: str) -> str:
    title = item.get("title") or item["id"]
    lines = [
        "---",
        f'title: "{yaml_escape(title)}"',
        'site: "juejin.cn"',
    ]
    if item.get("author"):
        lines.append(f'author: "{yaml_escape(item["author"])}"')
    if item.get("author_url"):
        lines.append(f'author_url: "{item["author_url"]}"')
    if item.get("category"):
        lines.append(f'category: "{yaml_escape(item["category"])}"')
    if item.get("tags"):
        lines.append("tags: [" + ", ".join(f'"{yaml_escape(tag)}"' for tag in item["tags"]) + "]")
    if item.get("publish_time"):
        lines.append(f'publish_time: "{item["publish_time"]}"')
    lines.extend(
        [
            f'source_url: "{item["url"]}"',
            f'article_id: "{item["id"]}"',
        ]
    )
    if item.get("collections"):
        lines.append(
            "collections: ["
            + ", ".join(f'"{yaml_escape(name)}"' for name in item["collections"])
            + "]"
        )
        lines.append(f'collection_rank: {item.get("rank", 0)}')
    else:
        lines.append(f'digg_rank: {item.get("rank", 0)}')
    lines.append(f'fetched_at: "{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}"')
    if item.get("read_time"):
        lines.append(f'read_time: "{item["read_time"]}"')
    if item.get("brief"):
        lines.append(f'digest: "{yaml_escape(item["brief"][:180])}"')
    lines.append("---")
    lines.append("")
    lines.append(f"# {title}")
    lines.append("")

    meta = []
    if item.get("author"):
        meta.append(f"作者：{item['author']}")
    if item.get("publish_time"):
        meta.append(f"发布：{item['publish_time']}")
    if item.get("read_time"):
        meta.append(f"阅读时长：{item['read_time']}")
    if meta:
        lines.append("> " + " ｜ ".join(meta))
        lines.append("")

    stats = " ｜ ".join(
        f"{label} {item.get(key, 0)}"
        for label, key in (
            ("阅读", "view_count"),
            ("点赞", "digg_count"),
            ("收藏", "collect_count"),
            ("评论", "comment_count"),
        )
    )
    lines.append(f"> {stats}")
    lines.append("")
    lines.append(f"> 原文：{item['url']}")
    if item.get("tags"):
        lines.append(">")
        lines.append("> 标签：" + " / ".join(item["tags"]))
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(body)
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- 主流程


def load_state(path: Path) -> dict:
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def year_of(item: dict) -> str:
    return (item.get("publish_time") or "")[:4] or "unknown"


def subdir_of(item: dict, group_by: str) -> str:
    if group_by == "collection":
        return safe_filename(item.get("collection_name") or "未分组", 60)
    return year_of(item)


def process(
    item: dict,
    out_dir: Path,
    limiter: RateLimiter,
    want_images: bool,
    retries: int,
    group_by: str = "year",
) -> dict:
    url = item["url"]
    last_error = ""

    for attempt in range(retries + 1):
        try:
            html = http_get(url, limiter, referer="https://juejin.cn/").decode(
                "utf-8", errors="replace"
            )
            parsed = parse_page(html, url)
            if parsed is None:
                if DEAD_HINTS.search(html[:4000]):
                    return {"status": STATUS_DEAD, "error": "文章已删除或不可见"}
                if item.get("link_url"):
                    return {"status": STATUS_DEAD, "error": f"外链转载，正文在 {item['link_url']}"}
                return {"status": STATUS_DEAD, "error": "页面里找不到正文"}

            body, images = parsed
            folder = out_dir / subdir_of(item, group_by)
            folder.mkdir(parents=True, exist_ok=True)

            date_prefix = (item.get("publish_time") or "")[:10] or "0000-00-00"
            stem = safe_filename(f"{date_prefix}-{item.get('title') or item['id']}")
            image_ok = image_failed = 0
            if want_images and images:
                body, image_ok, image_failed = download_images(
                    images, body, folder / "assets", stem, limiter, url
                )

            target = folder / f"{stem}.md"
            counter = 2
            while target.exists():
                target = folder / f"{stem}-{counter}.md"
                counter += 1

            target.write_text(render_markdown(item, body), encoding="utf-8")
            return {
                "status": STATUS_OK,
                "path": str(target.relative_to(out_dir)),
                "title": item.get("title", ""),
                "chars": len(body),
                "images": len(images),
                "images_saved": image_ok,
                "images_failed": image_failed,
            }
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            if exc.code in (403, 404, 410):
                return {"status": STATUS_DEAD, "error": last_error}
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))

    return {"status": STATUS_ERROR, "error": last_error or "未知错误"}


def main() -> int:
    parser = argparse.ArgumentParser(description="批量抓取掘金点赞文章的正文")
    parser.add_argument("--input", default="output/articles.json")
    parser.add_argument("--output", default="output/articles")
    parser.add_argument("--limit", type=int, default=0, help="只处理 N 条，0 表示全部")
    parser.add_argument(
        "--group-by",
        choices=("year", "collection"),
        default="year",
        help="子目录按什么分：year 发布年份（默认），collection 所属收藏集",
    )
    parser.add_argument("--workers", type=int, default=4, help="并发线程数")
    parser.add_argument("--interval", type=float, default=0.5, help="全局请求间隔秒数")
    parser.add_argument("--retries", type=int, default=2, help="失败重试次数")
    parser.add_argument("--download-images", action="store_true", help="把配图下载到本地")
    parser.add_argument("--retry-failed", action="store_true", help="只重试上次出错的条目")
    parser.add_argument("--include-dead", action="store_true", help="连同已判定失效的一起重试")
    parser.add_argument("--since", default="", help="只抓这个发布日期之后的，例如 2020-01-01")
    parser.add_argument("--category", default="", help="只抓某个分类，例如 前端")
    parser.add_argument("--tag", default="", help="只抓带某个标签的，例如 React")
    parser.add_argument("--collection", default="", help="只抓某个收藏集的，例如 前端工程化")
    args = parser.parse_args()

    base = Path(__file__).resolve().parent.parent
    input_path = Path(args.input) if Path(args.input).is_absolute() else base / args.input
    out_dir = Path(args.output) if Path(args.output).is_absolute() else base / args.output

    if not input_path.is_file():
        print(f"错误: 找不到 {input_path}")
        print("先运行: python3 scripts/fetch_list.py")
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    state_path = out_dir / ".fetch_state.json"
    state = load_state(state_path)

    targets = json.loads(input_path.read_text(encoding="utf-8"))
    if args.since:
        targets = [t for t in targets if (t.get("publish_time") or "") >= args.since]
    if args.category:
        targets = [t for t in targets if t.get("category") == args.category]
    if args.tag:
        targets = [t for t in targets if args.tag in (t.get("tags") or [])]
    if args.collection:
        targets = [t for t in targets if args.collection in (t.get("collections") or [])]

    pending = []
    for item in targets:
        record = state.get(item["id"])
        if record:
            if record.get("status") == STATUS_OK:
                continue
            if record.get("status") == STATUS_DEAD and not args.include_dead:
                continue
        elif args.retry_failed:
            continue
        pending.append(item)

    if args.limit:
        pending = pending[: args.limit]

    total = len(pending)
    print(f"待抓取: {total} 篇（清单共 {len(targets)} 篇）")
    if not total:
        print("没有需要处理的条目。")
        return 0
    print(f"输出目录: {out_dir}")
    print(
        f"并发 {args.workers}，请求间隔 {args.interval}s，"
        f"图片下载: {'开' if args.download_images else '关'}"
    )
    print()

    limiter = RateLimiter(args.interval)
    counts = {STATUS_OK: 0, STATUS_DEAD: 0, STATUS_ERROR: 0}
    done = 0
    lock = threading.Lock()
    started = time.time()

    def save_state() -> None:
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                process,
                item,
                out_dir,
                limiter,
                args.download_images,
                args.retries,
                args.group_by,
            ): item
            for item in pending
        }
        try:
            for future in as_completed(futures):
                item = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {"status": STATUS_ERROR, "error": f"{type(exc).__name__}: {exc}"}

                with lock:
                    done += 1
                    result["url"] = item["url"]
                    result["publish_time"] = item.get("publish_time", "")
                    state[item["id"]] = result
                    counts[result["status"]] = counts.get(result["status"], 0) + 1

                    flag = {STATUS_OK: "OK  ", STATUS_DEAD: "失效", STATUS_ERROR: "错误"}[
                        result["status"]
                    ]
                    label = item.get("title") or item["url"]
                    if result["status"] == STATUS_OK:
                        extra = f" ({result['chars']} 字"
                        if result.get("images"):
                            extra += f"，{result.get('images_saved', 0)}/{result['images']} 图"
                        extra += ")"
                    else:
                        extra = f" ({result.get('error', '')})"
                    # 全量抓取要跑几分钟，重定向到文件时也要能实时看到进度
                    print(f"[{done}/{total}] {flag} {label[:52]}{extra}", flush=True)

                    if done % 25 == 0:
                        save_state()
        except KeyboardInterrupt:
            print("\n收到中断信号，正在保存进度...")
            save_state()
            return 130

    save_state()

    elapsed = time.time() - started
    print()
    print("=" * 60)
    print(f"完成 {done} 篇，用时 {elapsed / 60:.1f} 分钟")
    print(f"成功 {counts[STATUS_OK]} / 失效 {counts[STATUS_DEAD]} / 出错 {counts[STATUS_ERROR]}")

    failures = [
        {"id": key, **value}
        for key, value in state.items()
        if value.get("status") in (STATUS_DEAD, STATUS_ERROR)
    ]
    if failures:
        report = out_dir / "未抓取清单.md"
        with report.open("w", encoding="utf-8") as handle:
            handle.write("# 未能抓取的掘金文章\n\n")
            handle.write(f"- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            handle.write(f"- 失效: {sum(1 for x in failures if x['status'] == STATUS_DEAD)}\n")
            handle.write(f"- 出错: {sum(1 for x in failures if x['status'] == STATUS_ERROR)}\n\n")
            handle.write("失效多数是作者删稿或转为仅自己可见，无法恢复。出错的可以用 --retry-failed 重试。\n\n")
            for entry in sorted(failures, key=lambda x: x.get("publish_time") or ""):
                handle.write(
                    f"- [{(entry.get('publish_time') or '')[:10]}] "
                    f"{entry.get('error', '')} — {entry.get('url', '')}\n"
                )
        print(f"未抓取清单: {report}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
