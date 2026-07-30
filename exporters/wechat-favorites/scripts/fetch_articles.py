#!/usr/bin/env python3
"""批量抓取微信收藏里的公众号文章正文，保存为本地 Markdown。

特点：
- 零第三方依赖，只用 Python 标准库。
- 断点续传：已成功的文章不会重复抓取。
- 自动识别失效链接（2016 年以前的收藏基本都已失效）。
- 可选把文章配图下载到本地。

用法：
    python3 scripts/fetch_articles.py --limit 20          # 先小批量试跑
    python3 scripts/fetch_articles.py                     # 全量抓取
    python3 scripts/fetch_articles.py --download-images   # 连图片一起存
    python3 scripts/fetch_articles.py --retry-failed      # 只重试上次失败的
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
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
BASE_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

STATUS_OK = "ok"
STATUS_DEAD = "dead"
STATUS_ERROR = "error"


class RateLimiter:
    """全局请求间隔控制，避免并发下把请求打得太密。"""

    def __init__(self, interval: float):
        self.interval = interval
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._next_at:
                delay = self._next_at - now
            else:
                delay = 0.0
            self._next_at = max(now, self._next_at) + self.interval
        if delay > 0:
            time.sleep(delay)


def http_get(url: str, limiter: RateLimiter, referer: str = "", timeout: int = 25) -> bytes:
    limiter.wait()
    headers = dict(BASE_HEADERS)
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        encoding = (response.headers.get("Content-Encoding") or "").lower()
    if encoding == "gzip":
        raw = gzip.decompress(raw)
    elif encoding == "deflate":
        raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw


# ---------------------------------------------------------------- HTML 解析

BLOCK_TAGS = {
    "p", "div", "section", "article", "blockquote", "figure", "figcaption",
    "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "table", "pre", "hr", "br",
}
SKIP_TAGS = {"script", "style", "svg", "noscript", "iframe", "video", "audio"}


class ContentExtractor(HTMLParser):
    """抽取指定 id 的节点内部 HTML。微信正文层层套着 section，按深度配平最稳。"""

    def __init__(self, target_id: str):
        super().__init__(convert_charrefs=False)
        self.target_id = target_id
        self.depth = 0
        self.capturing = False
        self.parts: list[str] = []
        self.found = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key: (value or "") for key, value in attrs}
        if not self.capturing:
            if attr_map.get("id") == self.target_id:
                self.capturing = True
                self.found = True
                self.depth = 1
            return

        if tag not in ("br", "img", "hr", "input", "meta", "link"):
            self.depth += 1
        self.parts.append(self.get_starttag_text() or "")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.capturing:
            self.parts.append(self.get_starttag_text() or "")

    def handle_endtag(self, tag: str) -> None:
        if not self.capturing:
            return
        if tag not in ("br", "img", "hr", "input", "meta", "link"):
            self.depth -= 1
            if self.depth <= 0:
                self.capturing = False
                return
        self.parts.append(f"</{tag}>")

    @property
    def html(self) -> str:
        return "".join(self.parts)

    def handle_data(self, data: str) -> None:
        if self.capturing:
            self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        if self.capturing:
            self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self.capturing:
            self.parts.append(f"&#{name};")


class MarkdownConverter(HTMLParser):
    """把微信正文 HTML 转成 Markdown。

    微信正文几乎全是 section/p 嵌套加行内样式，没有语义化标签，
    所以这里只处理确实带信息的标签，其余当作普通块处理。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip_depth = 0
        self.list_stack: list[dict] = []
        self.in_li = 0
        self.in_pre = False
        self.link_href: str | None = None
        self.link_text: list[str] = []
        self.images: list[str] = []
        self.pending_heading: str | None = None

    # -- 工具 -------------------------------------------------------
    def emit(self, text: str) -> None:
        if self.link_href is not None:
            self.link_text.append(text)
        else:
            self.out.append(text)

    def newline(self, count: int = 1) -> None:
        if self.link_href is not None:
            return
        while self.out and self.out[-1] == "\n" and count > 0:
            break
        self.out.append("\n" * count)

    # -- 标签 -------------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: (value or "") for key, value in attrs}

        if tag in SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return

        if tag == "img":
            src = attr.get("data-src") or attr.get("src") or ""
            if src.startswith("//"):
                src = "https:" + src
            if src and not src.startswith("data:"):
                self.images.append(src)
                alt = attr.get("alt", "").strip()
                if not self.in_li:
                    self.newline(2)
                self.out.append(f"![{alt}]({src})")
                if not self.in_li:
                    self.newline(2)
            return

        if tag == "br":
            self.newline()
            return

        if tag == "hr":
            self.newline(2)
            self.out.append("---")
            self.newline(2)
            return

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.newline(2)
            self.out.append("#" * int(tag[1]) + " ")
            return

        if tag in ("strong", "b"):
            self.emit("**")
            return
        if tag in ("em", "i"):
            self.emit("*")
            return
        if tag in ("del", "s"):
            self.emit("~~")
            return

        if tag == "code" and not self.in_pre:
            self.emit("`")
            return

        if tag == "pre":
            self.in_pre = True
            self.newline(2)
            self.out.append("```\n")
            return

        if tag == "blockquote":
            self.newline(2)
            self.out.append("> ")
            return

        if tag in ("ul", "ol"):
            self.list_stack.append({"type": tag, "index": 0})
            self.newline(2)
            return

        if tag == "li":
            self.in_li += 1
            self.newline()
            if self.list_stack:
                node = self.list_stack[-1]
                indent = "  " * (len(self.list_stack) - 1)
                if node["type"] == "ol":
                    node["index"] += 1
                    self.out.append(f"{indent}{node['index']}. ")
                else:
                    self.out.append(f"{indent}- ")
            else:
                self.out.append("- ")
            return

        if tag == "a":
            href = attr.get("href", "").strip()
            if href and not href.startswith("javascript:"):
                self.link_href = href
                self.link_text = []
            return

        if tag in BLOCK_TAGS and not self.in_li:
            self.newline(2)

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth:
            return

        if tag in ("strong", "b"):
            self.emit("**")
            return
        if tag in ("em", "i"):
            self.emit("*")
            return
        if tag in ("del", "s"):
            self.emit("~~")
            return
        if tag == "code" and not self.in_pre:
            self.emit("`")
            return
        if tag == "pre":
            self.in_pre = False
            self.newline()
            self.out.append("```")
            self.newline(2)
            return
        if tag in ("ul", "ol"):
            if self.list_stack:
                self.list_stack.pop()
            self.newline(2)
            return
        if tag == "a":
            if self.link_href is not None:
                text = "".join(self.link_text).strip()
                href = self.link_href
                self.link_href = None
                self.link_text = []
                if text:
                    self.out.append(f"[{text}]({href})")
                elif href:
                    self.out.append(f"<{href}>")
            return
        if tag == "li":
            self.in_li = max(0, self.in_li - 1)
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.newline(2)
            return
        if tag in BLOCK_TAGS and not self.in_li:
            self.newline(2)

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if self.in_pre:
            self.out.append(data)
            return
        text = data.replace("\u200b", "").replace("\xa0", " ")
        if not text.strip():
            if text and self.out and not self.out[-1].endswith((" ", "\n")):
                self.emit(" ")
            return
        self.emit(re.sub(r"[ \t]+", " ", text))

    def result(self) -> str:
        text = "".join(self.out)
        text = re.sub(r"[ \t]+\n", "\n", text)
        # 微信正文里常有空的 pre / 装饰性空段落，转换后会留下空代码块
        text = re.sub(r"```\s*```", "", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


# ---------------------------------------------------------------- 字段提取


def pick(pattern: str, html: str, group: int = 1) -> str:
    match = re.search(pattern, html, re.S)
    return unescape(re.sub(r"\s+", " ", match.group(group))).strip() if match else ""


def strip_tags(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value).strip()


def parse_article(html: str) -> dict | None:
    """解析文章页。返回 None 表示这个链接已经失效。"""
    if 'id="js_content"' not in html or 'id="activity-name"' not in html:
        return None

    extractor = ContentExtractor("js_content")
    extractor.feed(html)
    if not extractor.found:
        return None

    body_html = extractor.html
    converter = MarkdownConverter()
    converter.feed(body_html)
    body = converter.result()
    if len(body) < 20:
        return None

    title = strip_tags(pick(r'<h1[^>]*id="activity-name"[^>]*>(.*?)</h1>', html))
    account = strip_tags(pick(r'id="js_name"[^>]*>(.*?)</a>', html)) or strip_tags(
        pick(r'id="js_name"[^>]*>(.*?)<', html)
    )
    author = pick(r'id="js_author_name"[^>]*>(.*?)<', html) or pick(
        r'var\s+author\s*=\s*"(.*?)"', html
    )
    timestamp = pick(r'var\s+ct\s*=\s*"?(\d{9,})"?', html)
    publish_time = ""
    if timestamp:
        publish_time = datetime.fromtimestamp(int(timestamp)).strftime("%Y-%m-%d %H:%M:%S")
    digest = pick(r'name="description"\s+content="(.*?)"', html)

    return {
        "title": title,
        "account": account,
        "author": author,
        "publish_time": publish_time,
        "digest": digest,
        "body": body,
        "images": converter.images,
    }


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


def image_extension(url: str) -> str:
    match = re.search(r"wx_fmt=([a-zA-Z0-9]+)", url)
    if match:
        ext = match.group(1).lower()
        if ext in ("jpeg", "jpg", "png", "gif", "webp", "svg", "bmp"):
            return ".jpg" if ext == "jpeg" else f".{ext}"
    path = urlparse(url).path
    match = re.search(r"\.(jpg|jpeg|png|gif|webp|svg|bmp)$", path, re.I)
    if match:
        return f".{match.group(1).lower()}"
    return ".jpg"


def download_images(
    article: dict,
    body: str,
    asset_dir: Path,
    prefix: str,
    limiter: RateLimiter,
    source_url: str,
) -> tuple[str, int, int]:
    ok = failed = 0
    for index, url in enumerate(dict.fromkeys(article["images"]), 1):
        name = f"{prefix}-{index:02d}{image_extension(url)}"
        target = asset_dir / name
        if not target.exists():
            try:
                data = http_get(url, limiter, referer=source_url, timeout=30)
                if len(data) < 128:
                    raise ValueError("图片内容过小")
                asset_dir.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            except Exception:
                failed += 1
                continue
        ok += 1
        body = body.replace(url, f"assets/{name}")
    return body, ok, failed


def render_markdown(article: dict, item: dict, body: str) -> str:
    lines = [
        "---",
        f'title: "{yaml_escape(article["title"] or item.get("title") or "无标题")}"',
        f'account: "{yaml_escape(article["account"])}"',
    ]
    if article["author"]:
        lines.append(f'author: "{yaml_escape(article["author"])}"')
    if article["publish_time"]:
        lines.append(f'publish_time: "{article["publish_time"]}"')
    if item.get("time"):
        lines.append(f'favorite_time: "{item["time"]}"')
    lines.extend(
        [
            f'source_url: "{item["url"]}"',
            f'favorite_id: "{item.get("id", "")}"',
            f'fetched_at: "{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}"',
        ]
    )
    if article["digest"]:
        lines.append(f'digest: "{yaml_escape(article["digest"][:180])}"')
    lines.append("---")
    lines.append("")
    lines.append(f"# {article['title'] or '无标题'}")
    lines.append("")
    meta = []
    if article["account"]:
        meta.append(f"公众号：{article['account']}")
    if article["publish_time"]:
        meta.append(f"发布：{article['publish_time']}")
    if meta:
        lines.append("> " + " ｜ ".join(meta))
        lines.append("")
    lines.append(f"> 原文：{item['url']}")
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
        if "mp.weixin.qq.com" not in url:
            continue
        key = re.sub(r"[?&]chksm=[^&]*", "", url)
        if key in seen:
            continue
        seen.add(key)
        targets.append(
            {
                "id": entry.get("id", ""),
                "time": entry.get("time", ""),
                "title": entry.get("title", ""),
                "url": url,
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


def process(
    item: dict,
    out_dir: Path,
    limiter: RateLimiter,
    want_images: bool,
    retries: int,
) -> dict:
    url = item["url"]
    last_error = ""

    for attempt in range(retries + 1):
        try:
            raw = http_get(url, limiter)
            html = raw.decode("utf-8", errors="ignore")
            article = parse_article(html)
            if article is None:
                return {"status": STATUS_DEAD, "error": "链接已失效或内容已删除"}

            year = year_of(item)
            folder = out_dir / year
            folder.mkdir(parents=True, exist_ok=True)

            date_prefix = (item.get("time") or "")[:10] or "0000-00-00"
            stem = safe_filename(f"{date_prefix}-{article['title'] or item.get('title') or item['id']}")
            body = article["body"]
            image_ok = image_failed = 0

            if want_images and article["images"]:
                body, image_ok, image_failed = download_images(
                    article, body, folder / "assets", stem, limiter, url
                )

            target = folder / f"{stem}.md"
            counter = 2
            while target.exists():
                target = folder / f"{stem}-{counter}.md"
                counter += 1

            target.write_text(render_markdown(article, item, body), encoding="utf-8")
            return {
                "status": STATUS_OK,
                "path": str(target.relative_to(out_dir)),
                "title": article["title"],
                "chars": len(body),
                "images": len(article["images"]),
                "images_saved": image_ok,
                "images_failed": image_failed,
            }
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            if exc.code in (403, 404):
                break
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))

    return {"status": STATUS_ERROR, "error": last_error or "未知错误"}


def main() -> int:
    parser = argparse.ArgumentParser(description="批量抓取微信收藏中的公众号文章")
    parser.add_argument("--input", default="output/favorites.json")
    parser.add_argument("--output", default="output/articles")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 条，0 表示全部")
    parser.add_argument("--workers", type=int, default=4, help="并发线程数")
    parser.add_argument("--interval", type=float, default=0.6, help="全局请求间隔秒数")
    parser.add_argument("--retries", type=int, default=2, help="失败重试次数")
    parser.add_argument("--download-images", action="store_true", help="把配图下载到本地")
    parser.add_argument("--retry-failed", action="store_true", help="只重试上次出错的条目")
    parser.add_argument("--include-dead", action="store_true", help="连同已判定失效的一起重试")
    parser.add_argument("--since", default="", help="只抓取该日期之后的收藏，例如 2021-01-01")
    args = parser.parse_args()

    base = Path(__file__).resolve().parent.parent
    input_path = Path(args.input) if Path(args.input).is_absolute() else base / args.input
    out_dir = Path(args.output) if Path(args.output).is_absolute() else base / args.output

    if not input_path.is_file():
        print(f"错误: 找不到 {input_path}")
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    state_path = out_dir / ".fetch_state.json"
    state = load_state(state_path)

    targets = load_targets(input_path)
    if args.since:
        targets = [t for t in targets if (t["time"] or "") >= args.since]

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
    print(f"待抓取: {total} 篇（收藏中的公众号文章共 {len(targets)} 篇）")
    if not total:
        print("没有需要处理的条目。")
        return 0
    print(f"输出目录: {out_dir}")
    print(f"并发 {args.workers}，请求间隔 {args.interval}s，图片下载: {'开' if args.download_images else '关'}")
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
            pool.submit(process, item, out_dir, limiter, args.download_images, args.retries): item
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
                    result["time"] = item["time"]
                    state[item["id"]] = result
                    counts[result["status"]] = counts.get(result["status"], 0) + 1

                    flag = {"ok": "OK  ", "dead": "失效", "error": "错误"}[result["status"]]
                    label = result.get("title") or item.get("title") or item["url"]
                    extra = ""
                    if result["status"] == STATUS_OK and result.get("images"):
                        extra = f" [{result.get('images_saved', 0)}/{result['images']} 图]"
                    elif result["status"] != STATUS_OK:
                        extra = f" ({result.get('error', '')})"
                    print(f"[{done}/{total}] {flag} {label[:52]}{extra}")

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
        with report.open("w", encoding="utf-8") as f:
            f.write("# 未能抓取的收藏\n\n")
            f.write(f"- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"- 失效: {sum(1 for x in failures if x['status'] == STATUS_DEAD)}\n")
            f.write(f"- 出错: {sum(1 for x in failures if x['status'] == STATUS_ERROR)}\n\n")
            f.write("失效通常是文章被作者删除或公众号迁移，无法恢复。出错的可以用 --retry-failed 重试。\n\n")
            for entry in sorted(failures, key=lambda x: x.get("time") or ""):
                f.write(f"- [{entry.get('time', '')[:10]}] {entry.get('error', '')} — {entry.get('url', '')}\n")
        print(f"未抓取清单: {report}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
