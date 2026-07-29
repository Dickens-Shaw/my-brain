#!/usr/bin/env python3
"""按 output/books.json 把小册转成统一格式的本地 Markdown。

每册一个目录，章节文件名统一成 `序号-标题.md`，开头补 YAML frontmatter，
再生成一份 `00-目录.md` 当册内导航。做的事按形态分：

- Markdown 册：原文照搬，只补 frontmatter 和标题，顺手修掉 `img\\1\\1.image`
  这种 Windows 反斜杠路径
- HTML 册：走 html_to_markdown 转换，同目录的配图搬进 assets/
- PDF 册：标准库解析不了，只在未转换清单里登记，原始文件留在 input/ 不动

本地配图会按文件头判断真实格式重命名——小册里存着一堆 `.image`、`.awebp`
后缀甚至没有后缀的图，直接引用在 Obsidian 里是渲染不出来的。

导出：
    output/books/<册名>/00-目录.md
    output/books/<册名>/01-xxx.md
    output/books/<册名>/assets/
    output/books/未转换清单.md

用法：
    python3 scripts/build_books.py                       # 全量转换
    python3 scripts/build_books.py --book Babel          # 只转书名含 Babel 的
    python3 scripts/build_books.py --download-images     # 远程配图也存到本地
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import shutil
import sys
import time
import unicodedata
import urllib.request
import zlib
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import unquote, urlparse

from html_to_markdown import html_to_markdown

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
IMAGE_REF = re.compile(r"(!\[[^\]]*\]\()([^)\s]+)((?:\s+\"[^\"]*\")?\))")
IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF8", ".gif"),
    (b"BM", ".bmp"),
)
EXT_PATTERN = re.compile(r"\.(jpg|jpeg|png|gif|webp|svg|bmp)$", re.I)


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


def sniff_extension(data: bytes, name: str) -> str:
    """小册里的图片后缀基本不可信（.image / .awebp / 干脆没有），看文件头。"""
    for magic, ext in IMAGE_MAGIC:
        if data.startswith(magic):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    head = data[:64].lstrip()
    if head.startswith(b"<svg") or head.startswith(b"<?xml"):
        return ".svg"
    match = EXT_PATTERN.search(name)
    return f".{match.group(1).lower()}" if match else ".webp"


def http_get(url: str, timeout: int = 30) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Referer": "https://juejin.cn/",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(8_000_000)
        encoding = (response.headers.get("Content-Encoding") or "").lower()
    if encoding == "gzip":
        raw = gzip.decompress(raw)
    elif encoding == "deflate":
        try:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
        except zlib.error:
            raw = zlib.decompress(raw)
    return raw


# ---------------------------------------------------------------- 配图


class AssetStore:
    """一册共用一个 assets/ 目录，按来源路径去重。"""

    def __init__(self, asset_dir: Path):
        self.asset_dir = asset_dir
        self.local_map: dict[Path, str] = {}
        self.remote_map: dict[str, str] = {}
        self.copied = 0
        self.downloaded = 0
        self.missing: list[str] = []
        self.failed: list[str] = []

    def _write(self, relative: str, data: bytes) -> str:
        """写入并返回引用路径。

        HTML 册的配图文件名是压平的图床 URL，前 60 个字符往往完全一样，截断后
        会互相覆盖，所以撞名了就补序号。
        """
        stem, _, ext = relative.rpartition(".")
        candidate = relative
        index = 1
        while True:
            target = self.asset_dir / candidate
            if not target.exists() or target.read_bytes() == data:
                break
            index += 1
            candidate = f"{stem}-{index}.{ext}"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return f"assets/{candidate}"

    def take_local(self, chapter_dir: Path, relative_hint: str) -> str | None:
        """把册内配图搬进 assets/，返回改写后的引用路径。"""
        source = resolve_local_asset(chapter_dir, relative_hint)
        if source is None:
            self.missing.append(relative_hint)
            return None
        source = source.resolve()
        if source in self.local_map:
            return self.local_map[source]
        data = source.read_bytes()
        if not data:
            self.missing.append(relative_hint)
            return None

        stem = re.sub(r"\.(image|awebp)$", "", relative_hint, flags=re.I)
        relative = safe_filename_path(stem) + sniff_extension(data, relative_hint)
        reference = self._write(relative, data)
        self.local_map[source] = reference
        self.copied += 1
        return reference

    def take_remote(self, url: str, name_hint: str) -> str | None:
        if url in self.remote_map:
            return self.remote_map[url]
        clean = url.split("#")[0]
        try:
            data = http_get(clean)
            if len(data) < 128:
                raise ValueError("图片内容过小")
        except Exception:
            self.failed.append(url)
            return None
        relative = f"remote/{name_hint}{sniff_extension(data, urlparse(clean).path)}"
        reference = self._write(relative, data)
        self.remote_map[url] = reference
        self.downloaded += 1
        return reference


def resolve_local_asset(chapter_dir: Path, relative: str) -> Path | None:
    """在册目录里找这张图。

    保存网页的工具把图床 URL 压平成文件名时，正文引用留下了 `?` 结尾的查询串，
    磁盘上的文件名却没有，差一个字符就找不到，所以要按几种写法轮着试。
    """
    candidates = [relative]
    for cut in ("?", "#"):
        if cut in relative:
            candidates.append(relative.split(cut, 1)[0])
    decoded = unquote(relative)
    if decoded != relative:
        candidates.append(decoded)
        candidates.append(decoded.split("?", 1)[0])

    for candidate in candidates:
        if not candidate:
            continue
        path = chapter_dir / candidate
        if path.is_file():
            return path
    return None


def safe_filename_path(value: str) -> str:
    """逐段清洗相对路径，保留目录层级。空格和括号一律换掉，免得 Markdown 链接断在半路。"""
    parts = []
    for part in re.split(r"[\\/]+", value):
        if part in ("", ".", ".."):
            continue
        parts.append(re.sub(r"[\s()]+", "-", safe_filename(part, 60)).strip("-") or "asset")
    return "/".join(parts) or "asset"


def rewrite_images(
    body: str,
    chapter_dir: Path,
    store: AssetStore,
    order: int,
    want_remote: bool,
) -> str:
    counter = {"remote": 0}

    def replace(match: re.Match) -> str:
        head, url, tail = match.group(1), match.group(2), match.group(3)
        raw = url.strip()
        if raw.startswith(("http://", "https://", "//", "data:")):
            if not want_remote or raw.startswith(("//", "data:")):
                return match.group(0)
            counter["remote"] += 1
            reference = store.take_remote(raw, f"{order:03d}-{counter['remote']:02d}")
            return f"{head}{reference}{tail}" if reference else match.group(0)

        # 小册里混着 Windows 风格的 `img\1\1.image`，先掰成正常路径
        relative = raw.replace("\\", "/")
        reference = store.take_local(chapter_dir, relative)
        return f"{head}{reference}{tail}" if reference else match.group(0)

    return IMAGE_REF.sub(replace, body)


# ---------------------------------------------------------------- 章节


HEADING_LINE = re.compile(r"^(#{1,3})\s+(.*\S)\s*$")
# 存网页时把「复制代码」按钮的文字一起卷进了代码块首行，形如 ```js\njs复制代码import ...
COPY_BUTTON_NOISE = re.compile(r"(?m)^(```[^\n]*\n)[A-Za-z0-9+#.\-]*复制代码")


def normalize_title(value: str) -> str:
    """比标题时忽略序号、空白和标点，只看实质内容。"""
    value = re.sub(r"^[\s\d.、,\-_]+", "", value)
    return re.sub(r"[\s\W_]+", "", value, flags=re.UNICODE).lower()


def split_title(body: str, fallback: str, fallback_source: str) -> tuple[str, str, str]:
    """正文首行就是标题时，用它替掉文件名推出来的标题，并把这一行从正文里摘掉。

    文件名会丢字符——`ES5/6/7/8` 存成文件只能写作 `ES5[6[7[8`，冒号问号也一样，
    所以只要首行标题和文件名标题说的是同一件事，文件里那份更可信。
    """
    stripped = body.lstrip()
    match = HEADING_LINE.match(stripped.split("\n", 1)[0])
    if not match:
        return fallback, body, fallback_source

    heading = match.group(2).strip()
    rest = stripped.split("\n", 1)[1].lstrip() if "\n" in stripped else ""
    left, right = normalize_title(heading), normalize_title(fallback)
    if left and right and (left == right or SequenceMatcher(None, left, right).ratio() >= 0.75):
        return heading, rest, "文件内标题"
    return fallback, body, fallback_source


def demote_headings(body: str) -> str:
    """正文里的 h1 降一级，保证一个文件只有开头那个顶层标题。代码块内不动。"""
    lines = body.split("\n")
    inside_fence = False
    for index, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            inside_fence = not inside_fence
            continue
        if not inside_fence and re.match(r"^#\s+\S", line):
            lines[index] = "#" + line
    return "\n".join(lines)


def render_chapter(book: dict, chapter: dict, title: str, body: str, title_source: str) -> str:
    lines = [
        "---",
        f'title: "{yaml_escape(title)}"',
        'site: "juejin.cn"',
        f'book: "{yaml_escape(book["book"])}"',
        f'chapter: {chapter["order"]}',
        f'chapter_total: {book["chapter_count"]}',
        f'source_format: "{chapter["kind"]}"',
        f'order_source: "{book["toc_source"]}"',
        f'title_source: "{title_source}"',
        f'source_file: "{yaml_escape(chapter["file"])}"',
        f'converted_at: "{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}"',
        "---",
        "",
        f"# {title}",
        "",
        f"> {book['book']} · 第 {chapter['order']} 章",
        "",
        "---",
        "",
        body.strip(),
        "",
    ]
    return "\n".join(lines)


def chapter_filename(order: int, title: str, padding: int) -> str:
    return f"{order:0{padding}d}-{safe_filename(title)}.md"


def write_toc(book: dict, book_dir: Path, entries: list[tuple[dict, str]], skipped: list[dict]) -> None:
    lines = [
        f"# {book['book']}",
        "",
        f"- 章节: {len(entries)} / {book['chapter_count']}",
        f"- 原始形态: {book['format']}",
    ]
    if any(chapter["title_from"] == "目录.txt" for chapter in book["chapters"]):
        lines.append("- 章节标题: 原始文件名只有编号，标题取自册内 目录.txt")
    if any(chapter["title_from"] == "none" for chapter in book["chapters"]):
        lines.append(
            "- 章节标题: 原始文件名只有编号，册内也没有 目录.txt，标题恢复不了，"
            "只能写成「第 N 章」（正文里的首个标题是节标题，拿来当章标题会错）"
        )
    if book["toc_source"] == "sequence":
        lines.append("- 章节顺序: 原始文件名没有序号，这里按文件名排序，实际阅读顺序请以标题为准")
    elif book["toc_source"] == "filename-partial":
        lines.append("- 章节顺序: 部分文件名没有序号，无序号的排在带序号的之后")
    lines.append(f"- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    for chapter, filename in entries:
        # 标题里常带方括号（`[视频时长统计] xxx`），文件名带空格和圆括号，
        # 不处理的话链接会当场散架。百分号编码能用但没法读，用尖括号包住地址。
        text = chapter["title"].replace("[", "\\[").replace("]", "\\]")
        target = f"<{filename}>" if re.search(r"[ ()]", filename) else filename
        lines.append(f"{chapter['order']}. [{text}]({target})")
    if skipped:
        lines.append("")
        lines.append("## 缺失章节")
        lines.append("")
        for chapter in skipped:
            lines.append(f"- 第 {chapter['order']} 章 {chapter['title']}（源文件是 0 字节）")
    lines.append("")
    (book_dir / "00-目录.md").write_text("\n".join(lines), encoding="utf-8")


def build_book(
    book: dict,
    input_dir: Path,
    out_root: Path,
    want_remote: bool,
) -> dict:
    book_dir = out_root / safe_filename(book["book"], 100)
    book_dir.mkdir(parents=True, exist_ok=True)
    store = AssetStore(book_dir / "assets")

    padding = max(2, len(str(max((c["order"] for c in book["chapters"]), default=1))))
    entries: list[tuple[dict, str]] = []
    skipped: list[dict] = []
    failures: list[tuple[dict, str]] = []

    for chapter in book["chapters"]:
        if chapter["status"] != "ok":
            skipped.append(chapter)
            continue

        source = input_dir / chapter["file"]
        raw = source.read_text(encoding="utf-8", errors="replace")

        if chapter["kind"] == "html":
            converted = html_to_markdown(raw)
            if converted is None:
                failures.append((chapter, "HTML 里找不到正文容器"))
                continue
            body = COPY_BUTTON_NOISE.sub(r"\1", converted[0])
        else:
            body = raw.strip()
            if not body:
                failures.append((chapter, "文件里没有正文"))
                continue
            body = demote_headings(body)

        fallback_source = {"目录.txt": "目录.txt", "none": "缺失"}.get(chapter["title_from"], "文件名")
        title, body, title_source = split_title(body, chapter["title"], fallback_source)
        body = rewrite_images(body, source.parent, store, chapter["order"], want_remote)
        filename = chapter_filename(chapter["order"], title, padding)
        (book_dir / filename).write_text(
            render_chapter(book, chapter, title, body, title_source), encoding="utf-8"
        )
        entries.append(({**chapter, "title": title}, filename))

    write_toc(book, book_dir, entries, skipped)
    return {
        "book": book["book"],
        "written": len(entries),
        "skipped": skipped,
        "failures": failures,
        "assets_copied": store.copied,
        "assets_downloaded": store.downloaded,
        "assets_missing": store.missing,
        "assets_failed": store.failed,
    }


def write_report(results: list[dict], pdf_books: list[dict], path: Path) -> None:
    lines = [
        "# 未转换的小册内容",
        "",
        f"- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    if pdf_books:
        total = sum(book["chapter_count"] for book in pdf_books)
        lines.append(f"## PDF（{len(pdf_books)} 册 / {total} 个文件）")
        lines.append("")
        lines.append("纯标准库解析不了 PDF，为了不给这个仓库引入第三方依赖，这些只登记不转换。")
        lines.append("原始文件留在 `input/` 里没有动过。")
        lines.append("")
        for book in pdf_books:
            lines.append(f"- **{book['book']}** —— {book['chapter_count']} 个文件，`input/{book['dir']}/`")
        lines.append("")

    empties = [(item["book"], chapter) for item in results for chapter in item["skipped"]]
    if empties:
        lines.append(f"## 空文件（{len(empties)} 章）")
        lines.append("")
        lines.append("源文件就是 0 字节，下载时没落全，无法恢复。")
        lines.append("")
        for name, chapter in empties:
            lines.append(f"- {name} · 第 {chapter['order']} 章 {chapter['title']}")
        lines.append("")

    failures = [(item["book"], chapter, reason) for item in results for chapter, reason in item["failures"]]
    if failures:
        lines.append(f"## 转换失败（{len(failures)} 章）")
        lines.append("")
        for name, chapter, reason in failures:
            lines.append(f"- {name} · 第 {chapter['order']} 章 {chapter['title']} —— {reason}")
        lines.append("")

    missing = [(item["book"], ref) for item in results for ref in item["assets_missing"]]
    if missing:
        lines.append(f"## 配图找不到（{len(missing)} 处）")
        lines.append("")
        lines.append("Markdown 里引用了本地图片，但 input/ 里没有这个文件，引用保持原样没有改写。")
        lines.append("")
        for name, ref in missing[:200]:
            lines.append(f"- {name} · `{ref}`")
        if len(missing) > 200:
            lines.append(f"- …… 还有 {len(missing) - 200} 处")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="把小册转成统一格式的 Markdown")
    parser.add_argument("--input", default="input", help="小册原始文件目录")
    parser.add_argument("--index", default="output/books.json", help="scan_books.py 的产出")
    parser.add_argument("--output", default="output/books", help="输出目录")
    parser.add_argument("--book", default="", help="只转书名包含这个词的册")
    parser.add_argument("--download-images", action="store_true", help="把远程配图也存到本地")
    parser.add_argument("--interval", type=float, default=0.3, help="下载远程配图的请求间隔秒数")
    args = parser.parse_args()

    base = Path(__file__).resolve().parent.parent
    input_dir = Path(args.input) if Path(args.input).is_absolute() else base / args.input
    index_path = Path(args.index) if Path(args.index).is_absolute() else base / args.index
    out_root = Path(args.output) if Path(args.output).is_absolute() else base / args.output

    if not index_path.is_file():
        print(f"错误: 找不到 {index_path}")
        print("先运行: python3 scripts/scan_books.py")
        return 1

    books = json.loads(index_path.read_text(encoding="utf-8"))
    if args.book:
        books = [book for book in books if args.book.lower() in book["book"].lower()]
        if not books:
            print(f"没有书名包含「{args.book}」的册。")
            return 1

    pdf_books = [book for book in books if book["format"] == "pdf"]
    convertible = [book for book in books if book["format"] != "pdf"]

    out_root.mkdir(parents=True, exist_ok=True)
    print(f"输入目录: {input_dir}")
    print(f"输出目录: {out_root}")
    print(f"待转换: {len(convertible)} 册；PDF 只登记: {len(pdf_books)} 册")
    print(f"远程配图下载: {'开' if args.download_images else '关'}")
    print()

    if args.download_images and args.interval > 0:
        # 下载走的是掘金图床，慢一点稳一点
        original = http_get

        def throttled(url: str, timeout: int = 30) -> bytes:
            time.sleep(args.interval)
            return original(url, timeout)

        globals()["http_get"] = throttled

    results = []
    for index, book in enumerate(convertible, 1):
        result = build_book(book, input_dir, out_root, args.download_images)
        results.append(result)
        note = f"{result['written']}/{book['chapter_count']} 章"
        if result["assets_copied"]:
            note += f"，搬入 {result['assets_copied']} 图"
        if result["assets_downloaded"]:
            note += f"，下载 {result['assets_downloaded']} 图"
        if result["skipped"]:
            note += f"，跳过 {len(result['skipped'])} 空章"
        if result["failures"]:
            note += f"，失败 {len(result['failures'])}"
        print(f"[{index}/{len(convertible)}] {book['book']}（{note}）", flush=True)

    report = out_root / "未转换清单.md"
    write_report(results, pdf_books, report)

    written = sum(item["written"] for item in results)
    print()
    print("=" * 60)
    print(f"转换 {len(results)} 册，共 {written} 章")
    print(
        f"配图: 搬入 {sum(item['assets_copied'] for item in results)}，"
        f"下载 {sum(item['assets_downloaded'] for item in results)}，"
        f"缺失 {sum(len(item['assets_missing']) for item in results)}，"
        f"失败 {sum(len(item['assets_failed']) for item in results)}"
    )
    print(f"输出: {out_root}")
    print(f"未转换清单: {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
