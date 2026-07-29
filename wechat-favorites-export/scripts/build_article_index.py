#!/usr/bin/env python3
"""给已抓取的文章生成分类索引，不移动任何文件。

分类规则直接复用 build_knowledge_base.py，保证索引和 knowledge-base/
下的归类结果一致。文章原地留在 output/articles/<年份>/ 里，
索引只是一层指向本地文件的链接。

用法：
    python3 scripts/build_article_index.py
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_knowledge_base import (  # noqa: E402
    CATEGORIES,
    PENDING_SLUG,
    classify_primary,
    classify_secondary,
    enrich,
    norm,
    strip_urls,
)

INDEX_DIRNAME = "索引"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def relative_link(from_dir: Path, target: Path) -> str:
    """算出索引文件指向文章的相对路径，并做 URL 编码。"""
    rel = os.path.relpath(target, from_dir)
    return quote(rel.replace("\\", "/"))


def label(slug: str) -> str:
    """目录 slug 自带数字前缀用于排序，显示时去掉。"""
    return re.sub(r"^\d+-", "", slug) or slug


def main() -> int:
    base = Path(__file__).resolve().parent.parent
    articles_dir = base / "output" / "articles"
    state_path = articles_dir / ".fetch_state.json"
    favorites_path = base / "output" / "favorites.json"

    if not state_path.is_file():
        print(f"错误: 找不到 {state_path}，先运行 fetch_articles.py")
        return 1

    state = load_json(state_path)
    favorites = {str(entry.get("id", "")): entry for entry in load_json(favorites_path)}

    index_root = articles_dir / INDEX_DIRNAME
    if index_root.exists():
        for path in sorted(index_root.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
    index_root.mkdir(parents=True, exist_ok=True)

    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    accounts: Counter = Counter()
    missing_file = 0

    for fav_id, record in state.items():
        if record.get("status") != "ok":
            continue
        rel_path = record.get("path")
        if not rel_path:
            continue
        target = articles_dir / rel_path
        if not target.is_file():
            missing_file += 1
            continue

        favorite = favorites.get(str(fav_id), {"id": fav_id, "title": record.get("title", "")})
        item = enrich(favorite)

        # 抓回来的摘要比收藏元数据更完整，仅在原信号无法定性时用来补充判断，
        # 这样已归类的条目保持和 knowledge-base 一致。
        category = classify_primary(item)
        if category is None:
            digest = read_frontmatter_field(target, "digest")
            if digest:
                item = dict(item, signal=norm(strip_urls(f"{item['signal']} {digest}")))
                category = classify_primary(item)

        if category is None:
            key = (PENDING_SLUG, "")
        else:
            key = (category["slug"], classify_secondary(category, item))

        buckets[key].append(
            {
                "title": record.get("title") or item.get("title") or rel_path,
                "path": target,
                "time": record.get("time", ""),
                "chars": record.get("chars", 0),
                "url": record.get("url", ""),
                "account": read_frontmatter_field(target, "account"),
            }
        )
        accounts[read_frontmatter_field(target, "account")] += 1

    total = sum(len(v) for v in buckets.values())
    print(f"已索引文章 {total} 篇" + (f"（{missing_file} 篇记录对应的文件已不存在）" if missing_file else ""))

    tree: dict[str, dict[str, int]] = defaultdict(dict)
    for (parent, child), items in sorted(buckets.items()):
        items.sort(key=lambda x: x["time"], reverse=True)
        if parent == PENDING_SLUG:
            path = index_root / f"{PENDING_SLUG}.md"
            title = "待确认"
        elif child:
            path = index_root / parent / f"{child}.md"
            title = f"{label(parent)} / {label(child)}"
        else:
            path = index_root / f"{parent}.md"
            title = label(parent)

        write_index(path, title, items)
        tree[parent][child or "-"] = len(items)

    write_root_index(index_root / "README.md", tree, total, accounts, articles_dir)
    print(f"索引已生成: {index_root}")
    return 0


_frontmatter_cache: dict[Path, dict[str, str]] = {}


def read_frontmatter_field(path: Path, field: str) -> str:
    """只读文件头部的 frontmatter，避免把整篇正文读进内存。"""
    if path not in _frontmatter_cache:
        data: dict[str, str] = {}
        with path.open(encoding="utf-8") as f:
            if f.readline().strip() != "---":
                _frontmatter_cache[path] = data
                return ""
            for _ in range(20):
                line = f.readline()
                if not line or line.strip() == "---":
                    break
                key, _, value = line.partition(":")
                data[key.strip()] = value.strip().strip('"')
        _frontmatter_cache[path] = data
    return _frontmatter_cache[path].get(field, "")


def write_index(path: Path, title: str, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# {title}", "", f"共 {len(items)} 篇，按收藏时间倒序。", ""]
    for item in items:
        link = relative_link(path.parent, item["path"])
        meta = []
        if item["time"]:
            meta.append(item["time"][:10])
        if item["account"]:
            meta.append(item["account"])
        if item["chars"]:
            meta.append(f"{item['chars'] // 1000}k字" if item["chars"] >= 1000 else f"{item['chars']}字")
        suffix = f"　`{' · '.join(meta)}`" if meta else ""
        lines.append(f"- [{item['title']}]({link}){suffix}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_root_index(
    path: Path,
    tree: dict[str, dict[str, int]],
    total: int,
    accounts: Counter,
    articles_dir: Path,
) -> None:
    lines = [
        "# 文章分类索引",
        "",
        f"- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 已抓取正文: {total} 篇",
        "",
        "文章本体按收藏年份存放在 `../<年份>/` 下，本目录只是分类索引，",
        "链接指向本地文件。分类规则和 `knowledge-base/` 一致。",
        "",
        "## 分类",
        "",
    ]

    ordered = [c["slug"] for c in CATEGORIES if c["slug"] in tree]
    if PENDING_SLUG in tree:
        ordered.append(PENDING_SLUG)

    for parent in ordered:
        children = tree[parent]
        count = sum(children.values())
        name = "待确认" if parent == PENDING_SLUG else label(parent)
        if list(children) == ["-"]:
            lines.append(f"- [{name}]({quote(parent + '.md')}) — {count} 篇")
            continue
        lines.append(f"- **{name}** — {count} 篇")
        for child, child_count in sorted(children.items()):
            if child == "-":
                continue
            target = quote(f"{parent}/{child}.md")
            lines.append(f"  - [{label(child)}]({target}) — {child_count} 篇")

    lines.extend(["", "## 高频公众号", ""])
    for account, count in accounts.most_common(20):
        if account:
            lines.append(f"- {account} — {count} 篇")

    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
