#!/usr/bin/env python3
"""导出 Chrome 书签清单，生成 JSON / CSV / Markdown。

特点：
- 零第三方依赖，只用 Python 标准库。
- 直接读浏览器本地的 Bookmarks 文件，不需要解密，也不用装扩展。
- 支持多 Profile，同时兼容 Edge / Brave / Chromium / Vivaldi / Arc 等 Chromium 系浏览器。
- 也能解析「书签管理器 → 导出书签」生成的 Netscape HTML 文件，方便处理别的机器导出的书签。

用法：
    python3 scripts/extract_bookmarks.py inspect                 # 先看有哪些 Profile
    python3 scripts/extract_bookmarks.py export                  # 导出全部 Profile
    python3 scripts/extract_bookmarks.py export --profile Default
    python3 scripts/extract_bookmarks.py --browser edge export
    python3 scripts/extract_bookmarks.py export --html ~/Downloads/bookmarks.html
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

# Chromium 的时间戳是从 1601-01-01 起算的微秒数
CHROME_EPOCH_OFFSET = 11644473600

BROWSERS: dict[str, tuple[str, ...]] = {
    "chrome": (
        "~/Library/Application Support/Google/Chrome",
        "~/.config/google-chrome",
        "~/AppData/Local/Google/Chrome/User Data",
    ),
    "chrome-canary": ("~/Library/Application Support/Google/Chrome Canary",),
    "edge": (
        "~/Library/Application Support/Microsoft Edge",
        "~/.config/microsoft-edge",
        "~/AppData/Local/Microsoft/Edge/User Data",
    ),
    "brave": (
        "~/Library/Application Support/BraveSoftware/Brave-Browser",
        "~/.config/BraveSoftware/Brave-Browser",
    ),
    "chromium": (
        "~/Library/Application Support/Chromium",
        "~/.config/chromium",
    ),
    "vivaldi": (
        "~/Library/Application Support/Vivaldi",
        "~/.config/vivaldi",
    ),
    "arc": ("~/Library/Application Support/Arc/User Data",),
}

ROOT_NAMES = {
    "bookmark_bar": "书签栏",
    "other": "其他书签",
    "synced": "移动设备书签",
}

# 这些目录里的 Bookmarks 不是用户书签
SKIP_PROFILE_DIRS = {"System Profile", "Guest Profile"}


def log(message: str) -> None:
    print(message, flush=True)


# ---------------------------------------------------------------- 浏览器与 Profile


def browser_base(name: str, override: str = "") -> Path:
    if override:
        base = Path(override).expanduser()
        if not base.is_dir():
            raise FileNotFoundError(f"用户数据目录不存在: {base}")
        return base

    if name not in BROWSERS:
        raise ValueError(f"不认识的浏览器: {name}，可选 {', '.join(sorted(BROWSERS))}")
    for candidate in BROWSERS[name]:
        base = Path(candidate).expanduser()
        if base.is_dir():
            return base
    raise FileNotFoundError(f"没找到 {name} 的用户数据目录，可用 --user-data-dir 手动指定")


def profile_labels(base: Path) -> dict[str, str]:
    """从 Local State 里读 Profile 的显示名，找不到就返回空表。"""
    state_path = base / "Local State"
    if not state_path.is_file():
        return {}
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    info = state.get("profile", {}).get("info_cache", {})
    return {key: (value.get("name") or "") for key, value in info.items() if isinstance(value, dict)}


def discover_profiles(base: Path) -> list[dict]:
    """列出所有带 Bookmarks 文件的 Profile 目录。"""
    labels = profile_labels(base)
    found = []
    for child in sorted(base.iterdir()):
        if not child.is_dir() or child.name in SKIP_PROFILE_DIRS:
            continue
        bookmarks = child / "Bookmarks"
        if not bookmarks.is_file():
            continue
        found.append(
            {
                "dir": child.name,
                "label": labels.get(child.name, ""),
                "path": bookmarks,
                "size": bookmarks.stat().st_size,
                "mtime": datetime.fromtimestamp(bookmarks.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    # Default 排在最前，其余按名字
    found.sort(key=lambda x: (x["dir"] != "Default", x["dir"]))
    return found


# ---------------------------------------------------------------- 解析


def chrome_time(value: object) -> str:
    try:
        micros = int(str(value))
    except (TypeError, ValueError):
        return ""
    if micros <= 0:
        return ""
    seconds = micros / 1_000_000 - CHROME_EPOCH_OFFSET
    if seconds <= 0:
        return ""
    try:
        return datetime.fromtimestamp(seconds).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return ""


def unix_time(value: object) -> str:
    try:
        seconds = int(str(value))
    except (TypeError, ValueError):
        return ""
    if seconds <= 0:
        return ""
    try:
        return datetime.fromtimestamp(seconds).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return ""


def host_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
    except ValueError:
        return ""


def fallback_id(url: str, title: str) -> str:
    return hashlib.sha1(f"{url}\n{title}".encode("utf-8")).hexdigest()[:16]


def make_item(
    *,
    node_id: str,
    title: str,
    url: str,
    added: str,
    last_used: str,
    folder: str,
    root: str,
    profile: str,
    browser: str,
) -> dict[str, str]:
    return {
        "id": node_id or fallback_id(url, title),
        "type": "url",
        "time": added,
        "title": re.sub(r"\s+", " ", title or "").strip(),
        "url": url,
        "source": host_of(url),
        "folder": folder,
        "root": root,
        "profile": profile,
        "browser": browser,
        "last_used": last_used,
    }


def parse_bookmarks_file(path: Path, profile: str, browser: str) -> list[dict[str, str]]:
    """解析 Chromium 的 Bookmarks JSON，展平成书签列表，folder 保留完整路径。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    roots = data.get("roots", {})
    items: list[dict[str, str]] = []

    def walk(node: dict, root_key: str, trail: list[str]) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") == "url":
            items.append(
                make_item(
                    node_id=node.get("guid") or "",
                    title=node.get("name") or "",
                    url=node.get("url") or "",
                    added=chrome_time(node.get("date_added")),
                    last_used=chrome_time(node.get("date_last_used")),
                    folder="/".join(trail),
                    root=root_key,
                    profile=profile,
                    browser=browser,
                )
            )
            return
        for child in node.get("children") or []:
            if child.get("type") == "url":
                walk(child, root_key, trail)
            else:
                walk(child, root_key, trail + [child.get("name") or "未命名文件夹"])

    for root_key, root_node in roots.items():
        if not isinstance(root_node, dict) or not root_node.get("children"):
            continue
        root_label = ROOT_NAMES.get(root_key, root_node.get("name") or root_key)
        walk(root_node, root_key, [root_label])

    return items


class NetscapeBookmarkParser(HTMLParser):
    """解析「导出书签」生成的 Netscape HTML。

    这种格式没有闭合标签，靠 <DL> 的进出维护文件夹层级：
    <DT><H3>文件夹</H3><DL><p><DT><A HREF="...">标题</A>...</DL><p>
    """

    def __init__(self, profile: str, browser: str):
        super().__init__(convert_charrefs=True)
        self.profile = profile
        self.browser = browser
        self.items: list[dict[str, str]] = []
        self.trail: list[str] = []
        self.pending_folder = ""
        self.capture: str | None = None
        self.buffer: list[str] = []
        self.attrs: dict[str, str] = {}
        self.depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.lower(): (value or "") for key, value in attrs}
        if tag == "dl":
            self.depth += 1
            # 顶层 DL 只是根容器，没有对应的 H3，所以占位不占名字
            self.trail.append(self.pending_folder)
            self.pending_folder = ""
        elif tag == "h3":
            self.capture = "h3"
            self.buffer = []
            self.attrs = attr
        elif tag == "a":
            self.capture = "a"
            self.buffer = []
            self.attrs = attr

    def handle_endtag(self, tag: str) -> None:
        if tag == "dl":
            if self.trail:
                self.trail.pop()
            self.depth = max(0, self.depth - 1)
        elif tag == "h3" and self.capture == "h3":
            self.pending_folder = re.sub(r"\s+", " ", "".join(self.buffer)).strip()
            self.capture = None
        elif tag == "a" and self.capture == "a":
            url = self.attrs.get("href", "").strip()
            title = re.sub(r"\s+", " ", "".join(self.buffer)).strip()
            self.capture = None
            if not url:
                return
            self.items.append(
                make_item(
                    node_id="",
                    title=title,
                    url=url,
                    added=unix_time(self.attrs.get("add_date")),
                    last_used=unix_time(self.attrs.get("last_modified")),
                    # Chrome 把「其他书签」直接平铺在根容器里，没有对应的文件夹节点
                    folder="/".join(part for part in self.trail if part) or "其他书签",
                    root="html",
                    profile=self.profile,
                    browser=self.browser,
                )
            )

    def handle_data(self, data: str) -> None:
        if self.capture:
            self.buffer.append(data)


def parse_netscape_html(path: Path, browser: str) -> list[dict[str, str]]:
    parser = NetscapeBookmarkParser(profile=path.stem, browser=browser)
    parser.feed(path.read_text(encoding="utf-8", errors="ignore"))
    return parser.items


# ---------------------------------------------------------------- 去重与导出


def normalize_url(url: str) -> str:
    """去重用的 key：忽略协议、www 前缀、末尾斜杠和常见跟踪参数。"""
    cleaned = re.sub(r"[?&](utm_[a-z_]+|spm|from|source|share_source|ref)=[^&#]*", "", url, flags=re.I)
    cleaned = re.sub(r"^https?://", "", cleaned.strip(), flags=re.I)
    cleaned = re.sub(r"^www\.", "", cleaned, flags=re.I)
    cleaned = re.sub(r"[?&]+$", "", cleaned)
    return cleaned.rstrip("/#").lower()


def dedup(items: list[dict[str, str]]) -> tuple[list[dict[str, str]], int]:
    seen: dict[str, dict[str, str]] = {}
    ordered: list[dict[str, str]] = []
    duplicates = 0
    for item in items:
        key = normalize_url(item["url"])
        if not key:
            ordered.append(item)
            continue
        if key in seen:
            duplicates += 1
            # 同一条书签在多个 Profile / 文件夹里都存在时，把其他位置记下来
            kept = seen[key]
            extra = item.get("folder", "")
            if extra and extra != kept.get("folder"):
                kept["also_in"] = "; ".join(filter(None, [kept.get("also_in", ""), extra]))
            continue
        seen[key] = item
        ordered.append(item)
    return ordered, duplicates


CSV_COLUMNS = ["id", "time", "title", "url", "source", "folder", "root", "profile", "browser", "last_used"]


def write_exports(items: list[dict[str, str]], out_dir: Path, summary: list[str]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "bookmarks.json"
    csv_path = out_dir / "bookmarks.csv"
    md_path = out_dir / "bookmarks.md"

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for item in items:
            writer.writerow({key: item.get(key, "") for key in CSV_COLUMNS})

    # Markdown 保留原始文件夹层级，方便直接当目录浏览
    groups: dict[str, list[dict[str, str]]] = {}
    for item in items:
        groups.setdefault(item.get("folder") or "(未分类)", []).append(item)

    with md_path.open("w", encoding="utf-8") as f:
        f.write("# Chrome 书签导出\n\n")
        f.write(f"- 总数: {len(items)}\n")
        f.write(f"- 文件夹数: {len(groups)}\n")
        f.write(f"- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        for line in summary:
            f.write(f"- {line}\n")
        f.write("\n")
        for folder in sorted(groups):
            f.write(f"## {folder}\n\n")
            for index, item in enumerate(sorted(groups[folder], key=lambda x: x["time"] or ""), 1):
                title = item["title"] or item["url"]
                f.write(f"{index}. [{title}]({item['url']})\n")
                meta = [part for part in (item["source"], item["time"][:10]) if part]
                if meta:
                    f.write(f"   - {' ｜ '.join(meta)}\n")
            f.write("\n")

    log(f"已导出: {json_path}")
    log(f"已导出: {csv_path}")
    log(f"已导出: {md_path}")


# ---------------------------------------------------------------- 命令


def command_inspect(args: argparse.Namespace) -> None:
    base = browser_base(args.browser, args.user_data_dir)
    log(f"浏览器: {args.browser}")
    log(f"用户数据目录: {base}")
    profiles = discover_profiles(base)
    if not profiles:
        log("没找到任何带书签的 Profile。")
        return
    log("")
    for profile in profiles:
        items = parse_bookmarks_file(profile["path"], profile["dir"], args.browser)
        folders = {item["folder"] for item in items}
        label = f"（{profile['label']}）" if profile["label"] else ""
        log(f"- {profile['dir']}{label}")
        log(f"  文件: {profile['path']}")
        log(f"  书签 {len(items)} 条，文件夹 {len(folders)} 个，最后修改 {profile['mtime']}")


def collect_items(args: argparse.Namespace) -> tuple[list[dict[str, str]], list[str]]:
    items: list[dict[str, str]] = []
    summary: list[str] = []

    if args.html:
        for raw in args.html:
            path = Path(raw).expanduser()
            if not path.is_file():
                raise FileNotFoundError(f"书签 HTML 文件不存在: {path}")
            parsed = parse_netscape_html(path, args.browser)
            items.extend(parsed)
            summary.append(f"来源: {path.name}（HTML 导出），{len(parsed)} 条")
        return items, summary

    base = browser_base(args.browser, args.user_data_dir)
    profiles = discover_profiles(base)
    if args.profile:
        wanted = set(args.profile)
        profiles = [p for p in profiles if p["dir"] in wanted or p["label"] in wanted]
        if not profiles:
            raise FileNotFoundError(f"没找到指定的 Profile: {', '.join(args.profile)}")
    if not profiles:
        raise FileNotFoundError(f"{base} 下没有任何带书签的 Profile")

    for profile in profiles:
        parsed = parse_bookmarks_file(profile["path"], profile["dir"], args.browser)
        items.extend(parsed)
        label = f"（{profile['label']}）" if profile["label"] else ""
        summary.append(f"来源: {args.browser} / {profile['dir']}{label}，{len(parsed)} 条")
    return items, summary


def command_export(args: argparse.Namespace) -> None:
    items, summary = collect_items(args)
    total = len(items)

    if not args.keep_duplicates:
        items, duplicates = dedup(items)
        if duplicates:
            summary.append(f"去重: 合并了 {duplicates} 条重复链接")

    if not args.keep_non_http:
        before = len(items)
        items = [item for item in items if item["url"].lower().startswith(("http://", "https://"))]
        dropped = before - len(items)
        if dropped:
            summary.append(f"已跳过 {dropped} 条非 http 链接（javascript:、chrome:// 等）")

    base_dir = Path(__file__).resolve().parent.parent
    out_dir = Path(args.output) if Path(args.output).is_absolute() else base_dir / args.output
    write_exports(items, out_dir, summary)

    log("")
    for line in summary:
        log(line)
    log(f"原始 {total} 条，最终导出 {len(items)} 条")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="导出 Chrome 书签清单")
    parser.add_argument(
        "--browser",
        default="chrome",
        choices=sorted(BROWSERS),
        help="浏览器类型，默认 chrome",
    )
    parser.add_argument("--user-data-dir", default="", help="手动指定用户数据目录")
    parser.add_argument("--output", default="output", help="输出目录，默认 output")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("inspect", help="列出可用的 Profile 和书签数量")

    export = sub.add_parser("export", help="导出书签为 JSON / CSV / Markdown")
    export.add_argument("--profile", action="append", help="只导出指定 Profile，可重复")
    export.add_argument("--html", action="append", help="改为解析书签管理器导出的 HTML 文件，可重复")
    export.add_argument("--keep-duplicates", action="store_true", help="保留重复链接")
    export.add_argument("--keep-non-http", action="store_true", help="保留 javascript:、chrome:// 等非网页链接")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "inspect":
            command_inspect(args)
        elif args.command == "export":
            command_export(args)
        else:
            parser.error("未知命令")
        return 0
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
