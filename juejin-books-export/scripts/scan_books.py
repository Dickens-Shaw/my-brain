#!/usr/bin/env python3
"""盘一遍 input/ 里的掘金小册，输出一份册和章节的清单。

这些小册不是脚本抓来的，是攒在本机的一堆下载产物，形态很杂：

- 26 册是 Markdown，图片有的是远程 byteimg 地址，有的在册内 images/ 或 img/ 目录
- 5 册是保存下来的 HTML，配图是压平成文件名的本地图片
- 3 册加一堆散本是 PDF，标准库解析不了，只登记不转换

章节序号和标题的写法更杂，`1.开篇：xxx.md`、`01.md`、`10、xxx.pdf`、
`9 xxx.pdf`、`21中级进阶 --- xxx.html` 都有。这一步的任务就是把这些差异
收敛成统一的 (order, title)，转换阶段只管照着清单干活。

导出：
    output/books.json   完整字段，供 build_books.py 使用
    output/books.md     人读的总目录，含形态、章节数和缺失登记

用法：
    python3 scripts/scan_books.py
    python3 scripts/scan_books.py --input input --output output
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

CHAPTER_SUFFIXES = {".md": "markdown", ".html": "html", ".htm": "html", ".pdf": "pdf"}
# write.js 是保存 HTML 时带下来的原始数据，目录.txt 会被当成标题表读掉，都不算章节
SKIP_NAMES = {"write.js", "目录.txt", ".DS_Store"}
ASSET_DIR_NAMES = {"images", "img", "image", "assets"}
# PDF 册常把文件塞在册内的 pdf/ 子目录里
NESTED_CHAPTER_DIRS = {"pdf"}
# 「10响应式原理：xxx学习时长20分14秒」这种尾巴是播放时长，不是标题的一部分。
# 写法有 20分14秒 / 1小时11分 / 6分46秒 好几种，按「数字+单位」重复匹配
DURATION_TAIL = re.compile(r"\s*学习时长[:：]?\s*(?:\d+\s*(?:小时|时|分钟|分|秒))+\s*$")
ORDER_PREFIX = re.compile(r"^(\d{1,3})\s*[.、,\-_]?\s*(.*)$", re.S)
TOC_LINE = re.compile(r"^(\d{1,3})\s*[.、,\-_]?\s*(.+)$")


def natural_key(text: str) -> list:
    """让 2 排在 10 前面。"""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


def load_toc(book_dir: Path) -> dict[int, str]:
    """有些册的文件名只有编号（01.md），标题得从同目录的 目录.txt 里捞。"""
    toc_path = book_dir / "目录.txt"
    if not toc_path.is_file():
        return {}
    titles: dict[int, str] = {}
    for line in toc_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = TOC_LINE.match(line.strip())
        if match:
            titles.setdefault(int(match.group(1)), match.group(2).strip())
    return titles


def parse_chapter_name(stem: str) -> tuple[int | None, str]:
    """从文件名主干里拆出 (序号, 标题)。拆不出序号就返回 None。"""
    stem = DURATION_TAIL.sub("", stem).strip()
    match = ORDER_PREFIX.match(stem)
    if not match:
        return None, stem
    order = int(match.group(1))
    title = DURATION_TAIL.sub("", match.group(2)).strip(" .、-_")
    return order, title


def chapter_kind(path: Path) -> str | None:
    """认得的章节返回格式名。

    还要认一种怪东西：Babel 那册的 17~23 章下载时只落了 `23.实战案例` 这样的
    0 字节文件，扩展名被标题里的顿号切歪了。这些是缺章的证据，不能默认丢掉。
    """
    kind = CHAPTER_SUFFIXES.get(path.suffix.lower())
    if kind:
        return kind
    if path.stat().st_size == 0 and ORDER_PREFIX.match(path.stem):
        return "unknown"
    return None


def collect_chapter_files(book_dir: Path) -> list[tuple[Path, str]]:
    files = []
    for path in sorted(book_dir.iterdir()):
        if not path.is_file() or path.name in SKIP_NAMES:
            continue
        kind = chapter_kind(path)
        if kind:
            files.append((path, kind))
    if files:
        return files
    # 顶层没有章节文件时往下找一层，覆盖 从 0 到 1 落地前端工程化/pdf/*.pdf 这种
    for name in NESTED_CHAPTER_DIRS:
        nested = book_dir / name
        if not nested.is_dir():
            continue
        for path in sorted(nested.iterdir()):
            kind = CHAPTER_SUFFIXES.get(path.suffix.lower()) if path.is_file() else None
            if kind:
                files.append((path, kind))
    return files


def dedupe_by_content(parsed: list[dict]) -> tuple[list[dict], list[str]]:
    """同一册里同一章存了两份的情况（Vue 项目构建那册每章都有编号版和无编号版），
    内容一模一样，留带序号的那份。"""
    kept: list[dict] = []
    dropped: list[str] = []
    by_digest: dict[str, dict] = {}

    for item in parsed:
        path: Path = item["path"]
        if path.stat().st_size == 0:
            kept.append(item)
            continue
        digest = hashlib.sha1(path.read_bytes()).hexdigest()
        previous = by_digest.get(digest)
        if previous is None:
            by_digest[digest] = item
            kept.append(item)
        elif previous["order"] is None and item["order"] is not None:
            kept[kept.index(previous)] = item
            by_digest[digest] = item
            dropped.append(previous["path"].name)
        else:
            dropped.append(path.name)
    return kept, dropped


def scan_book(book_dir: Path, input_dir: Path) -> dict:
    files = collect_chapter_files(book_dir)
    toc = load_toc(book_dir)

    parsed = []
    for path, kind in files:
        # 空壳文件的「扩展名」其实是标题的一截，得拿整个文件名去解析
        order, title = parse_chapter_name(path.name if kind == "unknown" else path.stem)
        parsed.append({"path": path, "kind": kind, "order": order, "title": title})

    parsed, duplicates = dedupe_by_content(parsed)

    # 一册里只要有文件带序号，就按序号排；整册都没有序号只能退回文件名排序
    numbered = [item for item in parsed if item["order"] is not None]
    if numbered and len(numbered) == len(parsed):
        parsed.sort(key=lambda item: (item["order"], natural_key(item["path"].name)))
        toc_source = "filename"
    elif numbered:
        parsed.sort(key=lambda item: (item["order"] if item["order"] is not None else 10**6,
                                      natural_key(item["path"].name)))
        toc_source = "filename-partial"
    else:
        parsed.sort(key=lambda item: natural_key(item["path"].name))
        toc_source = "sequence"

    chapters = []
    missing = 0
    for index, item in enumerate(parsed, 1):
        path: Path = item["path"]
        size = path.stat().st_size
        order = item["order"] if item["order"] is not None else index
        title = item["title"]
        title_from = "filename"
        if not title:
            title = toc.get(order, "")
            title_from = "目录.txt" if title else "none"
        if not title:
            title = f"第 {order} 章"
        status = "ok" if size > 0 else "empty"
        if status == "empty":
            missing += 1
        chapters.append(
            {
                "order": order,
                "title": title,
                "title_from": title_from,
                "file": str(path.relative_to(input_dir)),
                "kind": item["kind"],
                "bytes": size,
                "status": status,
            }
        )

    # 形态只看有内容的章节，空壳文件的扩展名不可信
    kinds = {chapter["kind"] for chapter in chapters if chapter["status"] == "ok"}
    book_format = kinds.pop() if len(kinds) == 1 else ("mixed" if kinds else "empty")

    asset_dirs = sorted(
        str(path.relative_to(input_dir))
        for path in book_dir.iterdir()
        if path.is_dir() and path.name.lower() in ASSET_DIR_NAMES
    )

    return {
        "book": book_dir.name,
        "dir": str(book_dir.relative_to(input_dir)),
        "format": book_format,
        "chapter_count": len(chapters),
        "missing_count": missing,
        "duplicate_count": len(duplicates),
        "toc_source": toc_source,
        "asset_dirs": asset_dirs,
        "chapters": chapters,
    }


def scan_loose_pdfs(input_dir: Path) -> dict | None:
    """input/pdf/ 不是一本小册，是攒的一堆散本 PDF，单独记成一组。"""
    loose_dir = input_dir / "pdf"
    if not loose_dir.is_dir():
        return None
    files = sorted(
        (path for path in loose_dir.iterdir() if path.is_file() and path.suffix.lower() == ".pdf"),
        key=lambda path: natural_key(path.name),
    )
    if not files:
        return None
    return {
        "book": "散本 PDF",
        "dir": "pdf",
        "format": "pdf",
        "chapter_count": len(files),
        "missing_count": 0,
        "duplicate_count": 0,
        "toc_source": "sequence",
        "asset_dirs": [],
        "chapters": [
            {
                "order": index,
                "title": path.stem,
                "title_from": "filename",
                "file": str(path.relative_to(input_dir)),
                "kind": "pdf",
                "bytes": path.stat().st_size,
                "status": "ok",
            }
            for index, path in enumerate(files, 1)
        ],
    }


FORMAT_LABELS = {
    "markdown": "Markdown",
    "html": "HTML",
    "pdf": "PDF",
    "mixed": "混合",
    "empty": "空",
}


def write_markdown(books: list[dict], path: Path) -> None:
    by_format: dict[str, list[dict]] = {}
    for book in books:
        by_format.setdefault(book["format"], []).append(book)

    total_chapters = sum(book["chapter_count"] for book in books)
    total_missing = sum(book["missing_count"] for book in books)

    lines = [
        "# 掘金小册清单",
        "",
        f"- 册数: {len(books)}",
        f"- 章节: {total_chapters}",
        f"- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    if total_missing:
        lines.append(f"- 空文件（下载时就是坏的）: {total_missing}")
    lines.append("")
    lines.append("| 形态 | 册数 | 章节 | 说明 |")
    lines.append("| --- | --- | --- | --- |")
    notes = {
        "markdown": "直接补 frontmatter 归一命名",
        "html": "转成 Markdown，配图搬进 assets/",
        "pdf": "标准库解析不了，只登记不转换",
        "mixed": "同一册里混了多种格式",
        "empty": "没找到任何章节文件",
    }
    for fmt in ("markdown", "html", "pdf", "mixed", "empty"):
        group = by_format.get(fmt)
        if not group:
            continue
        lines.append(
            f"| {FORMAT_LABELS[fmt]} | {len(group)} | "
            f"{sum(book['chapter_count'] for book in group)} | {notes[fmt]} |"
        )
    lines.append("")

    for fmt in ("markdown", "html", "pdf", "mixed", "empty"):
        group = by_format.get(fmt)
        if not group:
            continue
        lines.append(f"## {FORMAT_LABELS[fmt]}（{len(group)} 册）")
        lines.append("")
        for book in sorted(group, key=lambda item: natural_key(item["book"])):
            meta = [f"{book['chapter_count']} 章"]
            if book["missing_count"]:
                meta.append(f"{book['missing_count']} 章是空文件")
            if book["duplicate_count"]:
                meta.append(f"去掉 {book['duplicate_count']} 份重复")
            if any(chapter["title_from"] == "目录.txt" for chapter in book["chapters"]):
                meta.append("标题取自 目录.txt")
            if book["toc_source"] == "sequence":
                meta.append("文件名无序号，按文件名排序")
            elif book["toc_source"] == "filename-partial":
                meta.append("部分章节无序号")
            if book["asset_dirs"]:
                meta.append("本地图片 " + " / ".join(book["asset_dirs"]))
            lines.append(f"- **{book['book']}** —— {' ｜ '.join(meta)}")
        lines.append("")

    empties = [
        (book["book"], chapter)
        for book in books
        for chapter in book["chapters"]
        if chapter["status"] == "empty"
    ]
    if empties:
        lines.append("## 空文件登记")
        lines.append("")
        lines.append("这些文件本身就是 0 字节，源头没下全，转换阶段会跳过：")
        lines.append("")
        for name, chapter in empties:
            fallback = f"第 {chapter['order']} 章"
            suffix = "" if chapter["title"] == fallback else f" {chapter['title']}"
            lines.append(f"- {name} · {fallback}{suffix}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="盘点 input/ 里的掘金小册")
    parser.add_argument("--input", default="input", help="小册原始文件目录")
    parser.add_argument("--output", default="output", help="输出目录")
    args = parser.parse_args()

    base = Path(__file__).resolve().parent.parent
    input_dir = Path(args.input) if Path(args.input).is_absolute() else base / args.input
    out_dir = Path(args.output) if Path(args.output).is_absolute() else base / args.output

    if not input_dir.is_dir():
        print(f"错误: 找不到 {input_dir}")
        print("把小册目录放到 input/ 下，每本一个文件夹。")
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"输入目录: {input_dir}")
    print(f"输出目录: {out_dir}")
    print()

    books: list[dict] = []
    for book_dir in sorted(
        (path for path in input_dir.iterdir() if path.is_dir() and path.name != "pdf"),
        key=lambda path: natural_key(path.name),
    ):
        book = scan_book(book_dir, input_dir)
        books.append(book)
        note = f"{book['chapter_count']} 章"
        if book["missing_count"]:
            note += f"，{book['missing_count']} 空"
        print(f"{FORMAT_LABELS[book['format']]:<9} {book['book']}（{note}）")

    loose = scan_loose_pdfs(input_dir)
    if loose:
        books.append(loose)
        print(f"{'PDF':<9} {loose['book']}（{loose['chapter_count']} 本）")

    if not books:
        print("input/ 下没有找到任何小册。")
        return 1

    json_path = out_dir / "books.json"
    json_path.write_text(json.dumps(books, ensure_ascii=False, indent=1), encoding="utf-8")
    write_markdown(books, out_dir / "books.md")

    print()
    print("=" * 60)
    print(f"{len(books)} 册，共 {sum(book['chapter_count'] for book in books)} 章")
    print(f"清单: {json_path}")
    print(f"      {out_dir / 'books.md'}")
    print()
    print("下一步转换: python3 scripts/build_books.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
