#!/usr/bin/env python3
"""批量抓取书签指向的网页正文，保存为本地 Markdown。

和微信方案的区别：书签横跨上千个站点，没法像公众号那样锁定一个固定的 DOM 结构，
所以这里用 readability 式的打分挑正文节点，再叠加常见技术站点的选择器提示。

特点：
- 零第三方依赖，只用 Python 标准库。
- 断点续传：已成功的页面不会重复抓取。
- 按域名限速，不同站点之间互不排队。
- 自动跳过应用站、视频站和二进制文件这类抓了也没内容的链接。
- 可选把正文配图下载到本地。

用法：
    python3 scripts/fetch_pages.py --limit 20          # 先小批量试跑
    python3 scripts/fetch_pages.py                     # 全量抓取
    python3 scripts/fetch_pages.py --folder 书签栏/进阶  # 只抓某个书签文件夹
    python3 scripts/fetch_pages.py --download-images   # 连图片一起存
    python3 scripts/fetch_pages.py --retry-failed      # 只重试上次出错的
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import ssl
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
STATUS_SKIP = "skip"
STATUS_ERROR = "error"

MIN_BODY_CHARS = 160

# 需要登录的站点（知乎、语雀等）可以按域名配一份 Cookie，见 --cookies
COOKIES: dict[str, str] = {}


def cookie_for(host: str) -> str:
    if host in COOKIES:
        return COOKIES[host]
    for domain, value in COOKIES.items():
        if host == domain or host.endswith("." + domain.lstrip(".")):
            return value
    return ""


class RateLimiter:
    """按域名限速。书签跨很多站点，全局排队会白白浪费时间，
    但对同一个域名仍然要保持间隔，避免被判定成爬虫。"""

    def __init__(self, interval: float, global_interval: float = 0.05):
        self.interval = interval
        self.global_interval = global_interval
        self._lock = threading.Lock()
        self._hosts: dict[str, float] = {}
        self._next_global = 0.0

    def wait(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            ready_at = max(self._hosts.get(host, 0.0), self._next_global)
            delay = max(0.0, ready_at - now)
            start = max(now, ready_at)
            self._hosts[host] = start + self.interval
            self._next_global = max(now, self._next_global) + self.global_interval
        if delay > 0:
            time.sleep(delay)


def build_ssl_contexts() -> tuple[ssl.SSLContext, ssl.SSLContext]:
    strict = ssl.create_default_context()
    relaxed = ssl.create_default_context()
    relaxed.check_hostname = False
    relaxed.verify_mode = ssl.CERT_NONE
    return strict, relaxed


SSL_STRICT, SSL_RELAXED = build_ssl_contexts()


def http_get(
    url: str,
    limiter: RateLimiter,
    referer: str = "",
    timeout: int = 25,
    max_bytes: int = 5_000_000,
) -> tuple[bytes, str, str]:
    """返回 (原始字节, 重定向后的最终 URL, Content-Type)。"""
    host = host_of(url)
    limiter.wait(host)
    headers = dict(BASE_HEADERS)
    if referer:
        headers["Referer"] = referer
    cookie = cookie_for(host)
    if cookie:
        headers["Cookie"] = cookie
    request = urllib.request.Request(url, headers=headers)

    try:
        response = urllib.request.urlopen(request, timeout=timeout, context=SSL_STRICT)
    except urllib.error.URLError as exc:
        # 老博客的证书过期是常态，降级重试一次，别为这个丢掉内容
        if not isinstance(exc.reason, ssl.SSLError):
            raise
        response = urllib.request.urlopen(request, timeout=timeout, context=SSL_RELAXED)

    with response:
        raw = response.read(max_bytes)
        encoding = (response.headers.get("Content-Encoding") or "").lower()
        content_type = response.headers.get("Content-Type") or ""
        final_url = response.geturl()

    if encoding == "gzip":
        raw = gzip.decompress(raw)
    elif encoding == "deflate":
        try:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
        except zlib.error:
            raw = zlib.decompress(raw)
    return raw, final_url, content_type


CHARSET_ALIASES = {"utf8": "utf-8", "gb2312": "gb18030", "gbk": "gb18030", "windows-936": "gb18030"}


def decode_html(raw: bytes, content_type: str) -> str:
    charset = ""
    match = re.search(r"charset=[\"']?([\w\-]+)", content_type or "", re.I)
    if match:
        charset = match.group(1)
    if not charset:
        head = raw[:4096].decode("ascii", errors="ignore")
        match = re.search(r"<meta[^>]+charset=[\"']?([\w\-]+)", head, re.I)
        if match:
            charset = match.group(1)

    charset = CHARSET_ALIASES.get((charset or "utf-8").lower(), (charset or "utf-8").lower())
    try:
        text = raw.decode(charset, errors="replace")
    except LookupError:
        text = raw.decode("utf-8", errors="replace")

    # 声明 utf-8 实际是 GBK 的页面不少，乱码率高就换一种再来
    if text.count("\ufffd") > max(20, len(text) // 500) and charset != "gb18030":
        try:
            text = raw.decode("gb18030", errors="replace")
        except LookupError:
            pass
    return text


# ---------------------------------------------------------------- 轻量 DOM

VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}
# 这些标签的内容永远不属于正文
IGNORE_TAGS = {"script", "style", "noscript", "svg", "canvas", "template", "head"}
BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "details", "div", "dl", "fieldset",
    "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6",
    "header", "hr", "main", "nav", "ol", "p", "pre", "section", "table", "ul",
}
# 正文里出现就说明是导航或边栏的容器
CHROME_TAGS = {"nav", "aside", "footer", "header", "form", "button", "select", "textarea", "label", "fieldset"}

MAX_DOM_DEPTH = 150


class Node:
    __slots__ = ("tag", "attrs", "children", "parent", "text")

    def __init__(self, tag: str, attrs: dict[str, str] | None = None, parent: "Node | None" = None, text: str = ""):
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list[Node] = []
        self.parent = parent
        self.text = text


class DomBuilder(HTMLParser):
    """把 HTML 解析成一棵容错的树。真实网页标签闭合极不规范，
    所以这里对 p / li / td / tr 做隐式闭合，遇到孤立的结束标签直接忽略。"""

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
            "option": ("option",),
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


def class_id(node: Node) -> str:
    return f"{node.attrs.get('class', '')} {node.attrs.get('id', '')} {node.attrs.get('role', '')}"


def detach(node: Node) -> None:
    parent = node.parent
    if parent is not None and node in parent.children:
        parent.children.remove(node)
        node.parent = None


def measure(root: Node) -> tuple[dict[int, int], dict[int, int]]:
    """一次遍历算出每个节点的文本长度和其中的链接文本长度。"""
    text_len: dict[int, int] = {}
    link_len: dict[int, int] = {}
    for node in reversed(list(iter_nodes(root))):
        if node.tag == "#text":
            text_len[id(node)] = len(node.text.strip())
            link_len[id(node)] = 0
            continue
        if node.tag in IGNORE_TAGS:
            text_len[id(node)] = 0
            link_len[id(node)] = 0
            continue
        total = sum(text_len.get(id(child), 0) for child in node.children)
        links = sum(link_len.get(id(child), 0) for child in node.children)
        text_len[id(node)] = total
        link_len[id(node)] = total if node.tag == "a" else links
    return text_len, link_len


# ---------------------------------------------------------------- 正文定位

POSITIVE = re.compile(
    r"article|content|entry|hentry|main|post|text|blog|story|markdown|rich[_-]?media|note|body|detail|readme",
    re.I,
)
NEGATIVE = re.compile(
    r"comment|contact|foot|masthead|meta|outbrain|promo|related|scroll|shoutbox|sidebar|sponsor|shopping|"
    r"tags|widget|nav|menu|banner|social|share|breadcrumb|pagination|recommend|advert|popup|modal|login|"
    r"subscribe|copyright|header|toolbar|extra|hidden|catalog|sidebar|aside|author-info|toc",
    re.I,
)
TAG_WEIGHTS = {
    "article": 30, "main": 20, "section": 8, "div": 5, "pre": 3, "td": 3, "blockquote": 3,
    "address": -3, "ol": -3, "ul": -3, "dl": -3, "dd": -3, "dt": -3, "li": -3, "form": -20,
    "h1": -5, "h2": -5, "h3": -5, "h4": -5, "h5": -5, "h6": -5, "th": -5,
}
PARAGRAPH_TAGS = {"p", "pre", "td", "blockquote", "div", "section", "li"}
PUNCT = re.compile(r"[,，、。；;：:！!？?]")

# 常见技术站点的正文容器，命中就直接用，比打分准
CONTENT_HINTS = (
    "js_content",            # 微信公众号
    "rich_media_content",    # 微信公众号
    "content_views",         # CSDN
    "cnblogs_post_body",     # 博客园
    "markdown-body",         # GitHub / 掘金
    "article-content",
    "articleContent",
    "post-content",
    "entry-content",
    "post_content",
    "show-content",          # 简书
    "article fmt",           # SegmentFault
    "RichText",              # 知乎
    "Post-RichTextContainer",
    "article-detail",
    "article-body",
    "post-body",
    "answer-content",
    "content-wrapper",
    "main-content",
    "articleCont",
    "theme-default-content",  # VuePress
    "md-content",
    "doc-content",
)


HINT_PATTERN = re.compile("|".join(re.escape(hint) for hint in CONTENT_HINTS), re.I)


def hint_node(root: Node, text_len: dict[int, int]) -> Node | None:
    """命中越靠前的提示越优先，同一个提示下取文本最多的那个节点。"""
    best: tuple[int, int, Node] | None = None
    for node in iter_nodes(root):
        if node.tag == "#text" or node.tag in IGNORE_TAGS:
            continue
        marker = f"{node.attrs.get('class', '')} {node.attrs.get('id', '')}"
        if not marker.strip() or not HINT_PATTERN.search(marker):
            continue
        length = text_len.get(id(node), 0)
        if length < MIN_BODY_CHARS:
            continue
        lowered = marker.lower()
        rank = next((i for i, hint in enumerate(CONTENT_HINTS) if hint.lower() in lowered), len(CONTENT_HINTS))
        candidate = (-rank, length, node)
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    return best[2] if best else None


def score_nodes(root: Node, text_len: dict[int, int], link_len: dict[int, int]) -> Node | None:
    """readability 的简化版：段落给分，分数往上传给父节点，再按链接密度打折。"""
    scored: dict[int, list] = {}

    def bootstrap(node: Node) -> list:
        entry = scored.get(id(node))
        if entry is not None:
            return entry
        weight = TAG_WEIGHTS.get(node.tag, 0)
        marker = class_id(node)
        if POSITIVE.search(marker):
            weight += 25
        if NEGATIVE.search(marker):
            weight -= 25
        entry = [node, float(weight)]
        scored[id(node)] = entry
        return entry

    for node in iter_nodes(root):
        if node.tag not in PARAGRAPH_TAGS:
            continue
        length = text_len.get(id(node), 0)
        if length < 25:
            continue
        # 容器型 div/section 只有在自己直接装着文字时才算段落
        if node.tag in ("div", "section") and any(child.tag in BLOCK_TAGS for child in node.children):
            continue

        base = 1.0 + min(length / 100.0, 3.0) + min(len(PUNCT.findall(inner_text(node))) / 4.0, 3.0)

        ancestor = node.parent
        for level in range(3):
            if ancestor is None or ancestor.tag == "#root":
                break
            bootstrap(ancestor)[1] += base / (level + 1)
            ancestor = ancestor.parent

    best: tuple[float, Node] | None = None
    for entry in scored.values():
        node, score = entry
        total = text_len.get(id(node), 0)
        if total < MIN_BODY_CHARS:
            continue
        density = link_len.get(id(node), 0) / total
        final = score * (1.0 - density)
        if best is None or final > best[0]:
            best = (final, node)
    return best[1] if best else None


def fallback_node(root: Node, text_len: dict[int, int]) -> Node | None:
    for tag in ("article", "main", "body"):
        candidates = [n for n in iter_nodes(root) if n.tag == tag]
        if candidates:
            richest = max(candidates, key=lambda n: text_len.get(id(n), 0))
            if text_len.get(id(richest), 0) >= MIN_BODY_CHARS:
                return richest
    return None


def clean_content(node: Node, text_len: dict[int, int], link_len: dict[int, int]) -> None:
    """把选中的正文里残留的分享栏、相关阅读、评论区摘掉。"""
    for child in list(iter_nodes(node)):
        if child is node or child.parent is None or child.tag == "#text":
            continue
        if child.tag in IGNORE_TAGS or child.tag in CHROME_TAGS:
            detach(child)
            continue
        total = text_len.get(id(child), 0)
        marker = class_id(child)
        if NEGATIVE.search(marker) and not POSITIVE.search(marker):
            density = link_len.get(id(child), 0) / total if total else 1.0
            if total < 200 or density > 0.5:
                detach(child)
                continue
        # 纯链接堆（相关文章、标签云）
        if child.tag in ("ul", "ol", "div") and total > 60:
            density = link_len.get(id(child), 0) / total
            if density > 0.85:
                detach(child)


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
# 站内跳转包装，解开才能拿到真正的目标地址
REDIRECT_PARAMS = {
    "link.juejin.cn": ("target",),
    "link.zhihu.com": ("target",),
    "link.csdn.net": ("target",),
    "www.jianshu.com": ("url",),
    "www.google.com": ("q", "url"),
}


def unwrap_redirect(url: str) -> str:
    keys = REDIRECT_PARAMS.get(host_of(url))
    if not keys:
        return url
    query = parse_qs(urlparse(url).query)
    for key in keys:
        value = (query.get(key) or [""])[0]
        if value.startswith(("http://", "https://")):
            return value
    return url


def code_language(node: Node) -> str:
    """从 pre / code 上的 class、lang、data-lang 里猜代码语言。"""
    markers = [node.attrs.get("class", ""), node.attrs.get("lang", ""), node.attrs.get("data-lang", "")]
    for child in node.children:
        if child.tag == "code":
            markers += [child.attrs.get("class", ""), child.attrs.get("lang", ""), child.attrs.get("data-lang", "")]

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
        # 紧挨着的两段加粗会渲染成 `**a****b**`，合成一段
        for mark in (r"\*\*", "~~"):
            chunk = re.sub(rf"{mark}([ \t]*){mark}", r"\1", chunk)
        # 正文里的 h1 都降级成了 h2，所以行首出现的单个 # 一定是装饰性锚点带出来的
        chunk = re.sub(r"^# ", r"\\# ", chunk, flags=re.M)
        chunks[index] = chunk
    return "```".join(chunks)


class MarkdownWriter:
    """把选中的正文节点渲染成 Markdown。"""

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
        """渲染子节点并把结果取回来，用于链接文字、表格单元格这类需要内联的场景。"""
        mark = len(self.parts)
        for child in node.children:
            self.walk(child)
        text = "".join(self.parts[mark:])
        del self.parts[mark:]
        return text

    # -- 具体标签 ---------------------------------------------------
    def write_image(self, node: Node) -> None:
        src = ""
        for key in ("data-src", "data-original", "data-actualsrc", "data-lazy-src", "src"):
            src = self.absolute(node.attrs.get(key, ""))
            if src:
                break
        if not src and node.attrs.get("srcset"):
            src = self.absolute(node.attrs["srcset"].split(",")[0].strip().split(" ")[0])
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
            # 单列表格通常只是排版容器，按普通块渲染
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
        depth = max(0, len(self.list_stack) - 1)
        indent = "  " * depth
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

        if tag in IGNORE_TAGS or tag in CHROME_TAGS:
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
            # <strong><strong>x</strong></strong> 这种嵌套很常见，重复包裹会变成 ****x****
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
        text = re.sub(r"```\w*\s*```", "", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return tidy_markdown(text.strip())


def to_markdown(node: Node, base_url: str) -> tuple[str, list[str]]:
    writer = MarkdownWriter(base_url)
    writer.walk(node)
    return writer.result(), writer.images


# ---------------------------------------------------------------- 元信息

DEAD_HINTS = re.compile(
    r"404\s*(not\s*found|错误)|页面不存在|内容不存在|文章不存在|该页面|已被删除|已删除|无法访问|访问受限|"
    r"页面走丢|你访问的页面|page not found|no longer available|has been removed",
    re.I,
)
LOGIN_HINTS = re.compile(r"登录后|请先登录|扫码登录|sign in to continue|log in to continue|需要登录", re.I)


def collect_meta(root: Node) -> dict[str, str]:
    meta: dict[str, str] = {}
    for node in iter_nodes(root):
        if node.tag == "meta":
            key = (
                node.attrs.get("property")
                or node.attrs.get("name")
                or node.attrs.get("itemprop")
                or ""
            ).lower()
            content = re.sub(r"\s+", " ", node.attrs.get("content", "")).strip()
            if key and content and key not in meta:
                meta[key] = content
        elif node.tag == "title" and "#title" not in meta:
            meta["#title"] = re.sub(r"\s+", " ", inner_text(node)).strip()
    return meta


def read_jsonld(root: Node) -> dict:
    wanted = {"article", "newsarticle", "blogposting", "techarticle", "webpage"}
    for node in iter_nodes(root):
        if node.tag != "script" or "ld+json" not in node.attrs.get("type", "").lower():
            continue
        raw = "".join(child.text for child in node.children if child.tag == "#text")
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        queue = data if isinstance(data, list) else [data]
        while queue:
            entry = queue.pop(0)
            if not isinstance(entry, dict):
                continue
            if "@graph" in entry and isinstance(entry["@graph"], list):
                queue.extend(entry["@graph"])
                continue
            kind = entry.get("@type", "")
            kinds = kind if isinstance(kind, list) else [kind]
            if any(str(k).lower() in wanted for k in kinds):
                return entry
    return {}


def normalize_time(value: str) -> str:
    match = re.search(
        r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?", value or ""
    )
    if not match:
        return ""
    year, month, day, hour, minute, second = match.groups()
    stamp = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    if hour is not None:
        stamp += f" {int(hour):02d}:{minute}:{second or '00'}"
    return stamp


def author_of(jsonld: dict, meta: dict[str, str]) -> str:
    author = jsonld.get("author")
    if isinstance(author, dict):
        author = author.get("name", "")
    elif isinstance(author, list) and author:
        first = author[0]
        author = first.get("name", "") if isinstance(first, dict) else str(first)
    if isinstance(author, str) and author.strip():
        return re.sub(r"\s+", " ", author).strip()
    for key in ("author", "article:author", "twitter:creator", "og:article:author"):
        if meta.get(key) and not meta[key].startswith("http"):
            return meta[key]
    return ""


def clean_title(title: str, site: str) -> str:
    title = re.sub(r"\s+", " ", title or "").strip()
    if site and len(title) > len(site) + 4:
        title = re.sub(rf"\s*[-|_–—·]\s*{re.escape(site)}\s*$", "", title).strip()
    return title


def parse_page(html: str, url: str, host: str) -> dict:
    root = parse_dom(html)
    meta = collect_meta(root)
    jsonld = read_jsonld(root)
    text_len, link_len = measure(root)

    content = hint_node(root, text_len) or score_nodes(root, text_len, link_len) or fallback_node(root, text_len)
    if content is None:
        return {"dead": True, "retryable": True, "reason": "没找到正文区域"}

    clean_content(content, text_len, link_len)
    body, images = to_markdown(content, url)

    site = meta.get("og:site_name") or meta.get("application-name") or host
    title = clean_title(
        meta.get("og:title") or jsonld.get("headline") or meta.get("twitter:title") or meta.get("#title", ""),
        site,
    )
    if not title:
        heading = next((n for n in iter_nodes(content) if n.tag in ("h1", "h2")), None)
        title = re.sub(r"\s+", " ", inner_text(heading)).strip() if heading else ""

    publish_time = normalize_time(
        jsonld.get("datePublished", "")
        or meta.get("article:published_time", "")
        or meta.get("og:release_date", "")
        or meta.get("publishdate", "")
        or meta.get("date", "")
    )
    if not publish_time and host.endswith("weixin.qq.com"):
        stamp = re.search(r'var\s+ct\s*=\s*"?(\d{9,})"?', html)
        if stamp:
            publish_time = datetime.fromtimestamp(int(stamp.group(1))).strftime("%Y-%m-%d %H:%M:%S")

    digest = meta.get("og:description") or meta.get("description") or jsonld.get("description", "")

    if len(body) < MIN_BODY_CHARS:
        head = f"{title} {body[:300]}"
        if LOGIN_HINTS.search(head):
            return {"dead": True, "reason": "需要登录才能查看"}
        # 站点在压力下偶尔会返回 JS 空壳页，所以正文太短算可重试
        return {"dead": True, "retryable": True, "reason": f"正文过短（{len(body)} 字）"}
    if DEAD_HINTS.search(title) and len(body) < 400:
        return {"dead": True, "reason": "页面已失效"}

    return {
        "dead": False,
        "title": title,
        "site": re.sub(r"\s+", " ", site).strip(),
        "author": author_of(jsonld, meta),
        "publish_time": publish_time,
        "digest": re.sub(r"\s+", " ", digest).strip(),
        "body": body,
        "images": images,
    }


# ---------------------------------------------------------------- 跳过规则

SKIP_HOSTS = {
    # 聊天与在线应用，抓下来只有一个空壳
    "chat.openai.com", "chatgpt.com", "claude.ai", "gemini.google.com", "chat.deepseek.com",
    "www.doubao.com", "chatglm.cn", "tongyi.aliyun.com", "yuanbao.tencent.com", "kimi.moonshot.cn",
    "www.perplexity.ai", "poe.com", "aistudio.google.com",
    "mail.google.com", "drive.google.com", "docs.google.com", "calendar.google.com",
    "www.google.com", "www.baidu.com",
    "www.figma.com", "figma.com", "www.canva.cn", "web.halochat.io", "app.slack.com",
    "localhost", "127.0.0.1",
    # 登录后才有内容的协作与控制台类站点
    "www.yuque.com", "yuque.com", "lanhuapp.com", "www.processon.com", "shimo.im",
    "console.aliyun.com", "sls.console.aliyun.com", "console.cloud.tencent.com",
    "leetcode.cn", "leetcode-cn.com", "leetcode.com",
    # 视频与社交
    "www.youtube.com", "youtube.com", "youtu.be", "www.bilibili.com", "b23.tv", "space.bilibili.com",
    "twitter.com", "x.com", "www.weibo.com", "weibo.com", "www.douyin.com", "www.xiaohongshu.com",
    "www.facebook.com", "www.instagram.com", "www.tiktok.com", "music.163.com",
    # 电商
    "www.taobao.com", "item.taobao.com", "detail.tmall.com", "www.jd.com", "item.jd.com",
    "www.amazon.com", "www.amazon.cn",
}
SKIP_EXTENSIONS = (
    ".pdf", ".zip", ".rar", ".7z", ".gz", ".tgz", ".dmg", ".pkg", ".exe", ".apk", ".ipa",
    ".mp4", ".mp3", ".mov", ".avi", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
    ".xlsx", ".docx", ".pptx", ".csv", ".json", ".xml",
)


def host_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
    except ValueError:
        return ""


def skip_reason(url: str, extra_hosts: set[str], skip_homepage: bool) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"非网页链接（{parsed.scheme or '无协议'}）"
    host = host_of(url)
    if host in SKIP_HOSTS or host in extra_hosts:
        return f"站点在跳过名单里（{host}）"
    if host.endswith(".local") or re.match(r"^(127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)", host):
        return f"本地或内网地址（{host}）"
    if parsed.path.lower().endswith(SKIP_EXTENSIONS):
        return "指向文件而不是网页"
    if skip_homepage and parsed.path in ("", "/") and not parsed.query:
        # 首页型书签抓下来只会得到导航和推荐位，链接本身才是有价值的部分
        return "站点首页，不是文章"
    return ""


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
    return (value or "").replace("\\", "\\\\").replace('"', '\\"')


IMAGE_MAGIC = (
    (b"\x89PNG", ".png"),
    (b"GIF8", ".gif"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"RIFF", ".webp"),
    (b"<svg", ".svg"),
    (b"<?xml", ".svg"),
)


def image_extension(url: str, data: bytes) -> str:
    for magic, ext in IMAGE_MAGIC:
        if data.startswith(magic):
            return ext
    match = re.search(r"\.(jpg|jpeg|png|gif|webp|svg|bmp)(?:$|[?#])", urlparse(url).path, re.I)
    if match:
        ext = match.group(1).lower()
        return ".jpg" if ext == "jpeg" else f".{ext}"
    match = re.search(r"wx_fmt=([a-zA-Z0-9]+)", url)
    if match and match.group(1).lower() in ("jpeg", "jpg", "png", "gif", "webp"):
        return ".jpg" if match.group(1).lower() == "jpeg" else f".{match.group(1).lower()}"
    return ".jpg"


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
        try:
            data, _, _ = http_get(url, limiter, referer=source_url, timeout=30, max_bytes=8_000_000)
            if len(data) < 128:
                raise ValueError("图片内容过小")
            name = f"{prefix}-{index:02d}{image_extension(url, data)}"
            asset_dir.mkdir(parents=True, exist_ok=True)
            (asset_dir / name).write_bytes(data)
        except Exception:
            failed += 1
            continue
        ok += 1
        body = body.replace(url, f"assets/{name}")
    return body, ok, failed


def render_markdown(page: dict, item: dict, body: str, final_url: str) -> str:
    lines = [
        "---",
        f'title: "{yaml_escape(page["title"] or item.get("title") or "无标题")}"',
        f'site: "{yaml_escape(page["site"])}"',
    ]
    if page["author"]:
        lines.append(f'author: "{yaml_escape(page["author"])}"')
    if page["publish_time"]:
        lines.append(f'publish_time: "{page["publish_time"]}"')
    if item.get("time"):
        lines.append(f'bookmark_time: "{item["time"]}"')
    if item.get("folder"):
        lines.append(f'folder: "{yaml_escape(item["folder"])}"')
    lines.append(f'source_url: "{item["url"]}"')
    if final_url and final_url != item["url"]:
        lines.append(f'final_url: "{final_url}"')
    lines.extend(
        [
            f'bookmark_id: "{item.get("id", "")}"',
            f'fetched_at: "{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}"',
        ]
    )
    if page["digest"]:
        lines.append(f'digest: "{yaml_escape(page["digest"][:180])}"')
    lines.append("---")
    lines.append("")
    lines.append(f"# {page['title'] or '无标题'}")
    lines.append("")

    meta = []
    if page["site"]:
        meta.append(f"来源：{page['site']}")
    if page["author"]:
        meta.append(f"作者：{page['author']}")
    if page["publish_time"]:
        meta.append(f"发布：{page['publish_time']}")
    if meta:
        lines.append("> " + " ｜ ".join(meta))
        lines.append("")
    lines.append(f"> 原文：{item['url']}")
    if item.get("folder"):
        lines.append(">")
        lines.append(f"> 书签位置：{item['folder']}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(body)
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- 主流程


def load_targets(json_path: Path) -> list[dict]:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    targets = []
    seen: set[str] = set()
    for entry in data:
        url = (entry.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        targets.append(
            {
                "id": entry.get("id", ""),
                "time": entry.get("time", ""),
                "title": entry.get("title", ""),
                "url": url,
                "folder": entry.get("folder", ""),
                "host": entry.get("source") or host_of(url),
            }
        )
    targets.sort(key=lambda x: x["time"] or "")
    return targets


def load_state(path: Path) -> dict:
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def year_of(item: dict) -> str:
    return (item.get("time") or "unknown")[:4] or "unknown"


def fetch_once(item: dict, out_dir: Path, limiter: RateLimiter, want_images: bool) -> dict:
    """抓一次。返回 error 状态表示值得重试，dead 表示别再试了。"""
    try:
        raw, final_url, content_type = http_get(item["url"], limiter)
        if content_type and not re.search(r"text/html|application/xhtml|text/plain", content_type, re.I):
            return {"status": STATUS_SKIP, "error": f"不是网页（{content_type.split(';')[0]}）"}

        page = parse_page(decode_html(raw, content_type), final_url, host_of(final_url))
        if page.get("dead"):
            status = STATUS_ERROR if page.get("retryable") else STATUS_DEAD
            return {"status": status, "error": page["reason"]}
    except urllib.error.HTTPError as exc:
        status = STATUS_DEAD if exc.code in (401, 403, 404, 410, 451) else STATUS_ERROR
        return {"status": status, "error": f"HTTP {exc.code}"}
    except Exception as exc:
        return {"status": STATUS_ERROR, "error": f"{type(exc).__name__}: {exc}"[:120]}

    folder = out_dir / year_of(item)
    folder.mkdir(parents=True, exist_ok=True)

    date_prefix = (item.get("time") or "")[:10] or "0000-00-00"
    stem = safe_filename(f"{date_prefix}-{page['title'] or item.get('title') or item['id']}")
    body = page["body"]
    image_ok = image_failed = 0

    if want_images and page["images"]:
        body, image_ok, image_failed = download_images(
            page["images"], body, folder / "assets", stem, limiter, final_url
        )

    target = folder / f"{stem}.md"
    counter = 2
    while target.exists():
        target = folder / f"{stem}-{counter}.md"
        counter += 1

    target.write_text(render_markdown(page, item, body, final_url), encoding="utf-8")
    return {
        "status": STATUS_OK,
        "path": str(target.relative_to(out_dir)),
        "title": page["title"],
        "chars": len(body),
        "images": len(page["images"]),
        "images_saved": image_ok,
        "images_failed": image_failed,
    }


def process(
    item: dict,
    out_dir: Path,
    limiter: RateLimiter,
    want_images: bool,
    retries: int,
    extra_skip: set[str],
    skip_homepage: bool,
) -> dict:
    reason = skip_reason(item["url"], extra_skip, skip_homepage)
    if reason:
        return {"status": STATUS_SKIP, "error": reason}

    for attempt in range(retries + 1):
        result = fetch_once(item, out_dir, limiter, want_images)
        if result["status"] != STATUS_ERROR or attempt == retries:
            return result
        time.sleep(1.5 * (attempt + 1))
    return {"status": STATUS_ERROR, "error": "未知错误"}


def main() -> int:
    parser = argparse.ArgumentParser(description="批量抓取书签网页正文并转成 Markdown")
    parser.add_argument("--input", default="output/bookmarks.json")
    parser.add_argument("--output", default="output/pages")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 条，0 表示全部")
    parser.add_argument("--workers", type=int, default=8, help="并发线程数")
    parser.add_argument("--interval", type=float, default=1.5, help="同一域名的请求间隔秒数")
    parser.add_argument("--retries", type=int, default=1, help="失败重试次数")
    parser.add_argument("--download-images", action="store_true", help="把配图下载到本地")
    parser.add_argument("--retry-failed", action="store_true", help="只重试上次出错的条目")
    parser.add_argument("--include-dead", action="store_true", help="连同已判定失效的一起重试")
    parser.add_argument("--include-skipped", action="store_true", help="连同已跳过的一起重试")
    parser.add_argument("--since", default="", help="只抓该日期之后收藏的书签，例如 2023-01-01")
    parser.add_argument("--folder", default="", help="只抓某个书签文件夹，按前缀匹配")
    parser.add_argument("--host", default="", help="只抓某个域名，按子串匹配")
    parser.add_argument("--skip-hosts", default="", help="额外跳过的域名，逗号分隔")
    parser.add_argument("--no-skip-list", action="store_true", help="忽略内置跳过名单，全部尝试抓取")
    parser.add_argument("--include-homepage", action="store_true", help="连站点首页也抓（默认跳过）")
    parser.add_argument("--cookies", default="", help="Cookie 配置文件，JSON 格式 {\"www.zhihu.com\": \"a=b; c=d\"}")
    args = parser.parse_args()

    base = Path(__file__).resolve().parent.parent
    input_path = Path(args.input) if Path(args.input).is_absolute() else base / args.input
    out_dir = Path(args.output) if Path(args.output).is_absolute() else base / args.output

    if not input_path.is_file():
        print(f"错误: 找不到 {input_path}，请先运行 extract_bookmarks.py export")
        return 1

    if args.no_skip_list:
        SKIP_HOSTS.clear()
    extra_skip = {h.strip().lower() for h in args.skip_hosts.split(",") if h.strip()}

    if args.cookies:
        cookie_path = Path(args.cookies).expanduser()
        if not cookie_path.is_file():
            print(f"错误: 找不到 Cookie 文件 {cookie_path}")
            return 1
        COOKIES.update(json.loads(cookie_path.read_text(encoding="utf-8")))
        print(f"已加载 {len(COOKIES)} 个域名的 Cookie")

    out_dir.mkdir(parents=True, exist_ok=True)
    state_path = out_dir / ".fetch_state.json"
    state = load_state(state_path)

    targets = load_targets(input_path)
    if args.since:
        targets = [t for t in targets if (t["time"] or "") >= args.since]
    if args.folder:
        targets = [t for t in targets if t["folder"].startswith(args.folder)]
    if args.host:
        targets = [t for t in targets if args.host.lower() in t["host"]]

    pending = []
    for item in targets:
        record = state.get(item["id"])
        if record:
            status = record.get("status")
            if status == STATUS_OK:
                continue
            if status == STATUS_DEAD and not args.include_dead:
                continue
            if status == STATUS_SKIP and not args.include_skipped:
                continue
        elif args.retry_failed:
            continue
        pending.append(item)

    if args.limit:
        pending = pending[: args.limit]

    total = len(pending)
    print(f"待抓取: {total} 条（筛选后共 {len(targets)} 条书签）")
    if not total:
        print("没有需要处理的条目。")
        return 0
    print(f"输出目录: {out_dir}")
    print(f"并发 {args.workers}，同域名间隔 {args.interval}s，图片下载: {'开' if args.download_images else '关'}")
    print()

    limiter = RateLimiter(args.interval)
    counts = {STATUS_OK: 0, STATUS_DEAD: 0, STATUS_SKIP: 0, STATUS_ERROR: 0}
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
                extra_skip,
                not args.include_homepage,
            ): item
            for item in pending
        }
        try:
            for future in as_completed(futures):
                item = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {"status": STATUS_ERROR, "error": f"{type(exc).__name__}: {exc}"[:120]}

                with lock:
                    done += 1
                    result["url"] = item["url"]
                    result["time"] = item["time"]
                    result["folder"] = item["folder"]
                    state[item["id"]] = result
                    counts[result["status"]] = counts.get(result["status"], 0) + 1

                    flag = {"ok": "OK  ", "dead": "失效", "skip": "跳过", "error": "错误"}[result["status"]]
                    label = result.get("title") or item.get("title") or item["url"]
                    if result["status"] == STATUS_OK:
                        extra = f" [{result['chars']} 字]"
                        if result.get("images_saved"):
                            extra += f"[{result['images_saved']}/{result['images']} 图]"
                    else:
                        extra = f" ({result.get('error', '')})"
                    print(f"[{done}/{total}] {flag} {label[:48]}{extra}")

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
    print(f"完成 {done} 条，用时 {elapsed / 60:.1f} 分钟")
    print(
        f"成功 {counts[STATUS_OK]} / 失效 {counts[STATUS_DEAD]} / "
        f"跳过 {counts[STATUS_SKIP]} / 出错 {counts[STATUS_ERROR]}"
    )

    failures = [
        {"id": key, **value}
        for key, value in state.items()
        if value.get("status") in (STATUS_DEAD, STATUS_ERROR)
    ]
    if failures:
        report = out_dir / "未抓取清单.md"
        with report.open("w", encoding="utf-8") as f:
            f.write("# 未能抓取的书签\n\n")
            f.write(f"- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"- 失效: {sum(1 for x in failures if x['status'] == STATUS_DEAD)}\n")
            f.write(f"- 出错: {sum(1 for x in failures if x['status'] == STATUS_ERROR)}\n\n")
            f.write("失效多半是页面被删、需要登录或站点已经关停。出错的可以用 --retry-failed 重试。\n\n")
            for entry in sorted(failures, key=lambda x: x.get("time") or ""):
                f.write(f"- [{(entry.get('time') or '')[:10]}] {entry.get('error', '')} — {entry.get('url', '')}\n")
        print(f"未抓取清单: {report}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
