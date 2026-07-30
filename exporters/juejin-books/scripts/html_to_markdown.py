#!/usr/bin/env python3
"""把保存下来的掘金小册 HTML 转成 Markdown。

这是 `exporters/juejin-articles/scripts/fetch_articles.py` 里那套 DOM + Markdown
转换器的移植版。仓库里每条管线都自带一份转换器（`exporters/chrome-bookmarks`
也是），刻意不抽公共库，代价是重复，好处是任何一条管线可以单独拷走就能跑。

和文章版的区别只有一处：文章版处理的是线上页面，图片地址一律补成绝对 URL；
小册这边的 HTML 是存在本机的，配图是同目录下的本地文件，所以相对地址原样保留，
由 build_books.py 负责把它们搬进 assets/ 并改写引用。
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

MIN_BODY_CHARS = 60

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
        """站外链接解掉掘金的跳转壳；相对地址是本机配图，原样留给调用方改写。"""
        url = (url or "").strip()
        if not url or url.startswith(("data:", "javascript:", "#")):
            return ""
        if url.startswith("//"):
            return "https:" + url
        if url.startswith(("http://", "https://")):
            return unwrap_redirect(url)
        return url

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

# ---------------------------------------------------------------- 对外接口


def html_to_markdown(html: str) -> tuple[str, list[str]] | None:
    """返回 (Markdown 正文, 图片地址列表)。找不到正文容器时返回 None。"""
    root = parse_dom(html)
    body_node = find_body(root)
    if body_node is None:
        return None

    writer = MarkdownWriter("")
    writer.walk(body_node)
    body = writer.result()
    if len(body) < MIN_BODY_CHARS:
        return None
    return body, writer.images
