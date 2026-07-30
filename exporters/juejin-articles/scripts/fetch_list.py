#!/usr/bin/env python3
"""拉取掘金个人主页的「赞过的文章」清单。

掘金的互动列表接口是公开的：不带 Cookie、不带 a_bogus 签名也能正常翻页，
所以这里只保留 aid / uuid 两个必需的查询参数，避免把账号凭证写进仓库。

导出：
    output/articles.json   完整字段，供 fetch_articles.py 使用
    output/articles.csv    表格，方便丢进 Excel 或其他工具
    output/articles.md     按年份分组的可读清单

用户 ID 从 --user-id、环境变量 JUEJIN_USER_ID 或 .env 里取，三者都没有就报错退出。

用法：
    python3 scripts/fetch_list.py                    # 全量拉取
    python3 scripts/fetch_list.py --max-pages 3      # 先试跑三页
    python3 scripts/fetch_list.py --item-type 4      # 换成沸点列表
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zlib
from datetime import datetime
from pathlib import Path

API_URL = "https://api.juejin.cn/interact_api/v1/digg/query_page"
# aid 是掘金 Web 端的固定应用标识，uuid 是设备号，随便一个合法值都能通过
CLIENT_AID = "2608"
CLIENT_UUID = "7595598414958757395"
PAGE_SIZE = 10

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
BASE_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Content-Type": "application/json",
    "Origin": "https://juejin.cn",
    "Referer": "https://juejin.cn/",
}

ITEM_TYPE_NAMES = {2: "文章", 4: "沸点", 15: "视频"}


def http_post(
    url: str,
    payload: dict,
    timeout: int = 25,
    extra_headers: dict[str, str] | None = None,
) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {**BASE_HEADERS, **(extra_headers or {})}
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        encoding = (response.headers.get("Content-Encoding") or "").lower()
    if encoding == "gzip":
        raw = gzip.decompress(raw)
    elif encoding == "deflate":
        try:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
        except zlib.error:
            raw = zlib.decompress(raw)
    return json.loads(raw.decode("utf-8", errors="replace"))


def timestamp_to_text(value: str | int | None) -> str:
    """掘金的时间字段是秒级时间戳字符串。"""
    try:
        seconds = int(value or 0)
    except (TypeError, ValueError):
        return ""
    if seconds <= 0:
        return ""
    return datetime.fromtimestamp(seconds).strftime("%Y-%m-%d %H:%M:%S")


def normalize(entry: dict, rank: int) -> dict | None:
    info = entry.get("article_info") or {}
    article_id = str(entry.get("article_id") or info.get("article_id") or "").strip()
    if not article_id:
        return None

    author = entry.get("author_user_info") or {}
    category = entry.get("category") or {}
    tags = [tag.get("tag_name", "") for tag in (entry.get("tags") or []) if tag.get("tag_name")]
    publish_time = timestamp_to_text(info.get("ctime"))

    return {
        "rank": rank,
        "id": article_id,
        "title": (info.get("title") or "").strip(),
        "url": f"https://juejin.cn/post/{article_id}",
        "brief": re.sub(r"\s+", " ", info.get("brief_content") or "").strip(),
        "author": (author.get("user_name") or "").strip(),
        "author_id": str(author.get("user_id") or ""),
        "author_url": f"https://juejin.cn/user/{author.get('user_id')}" if author.get("user_id") else "",
        "category": (category.get("category_name") or "").strip(),
        "tags": tags,
        "publish_time": publish_time,
        # 点赞列表不返回点赞时间，统一用发布时间作为排序和归档依据
        "time": publish_time,
        "update_time": timestamp_to_text(info.get("mtime")),
        "view_count": info.get("view_count") or 0,
        "digg_count": info.get("digg_count") or 0,
        "collect_count": info.get("collect_count") or 0,
        "comment_count": info.get("comment_count") or 0,
        "read_time": (info.get("read_time") or "").strip(),
        "cover_image": (info.get("cover_image") or "").strip(),
        # 外链转载的文章正文不在掘金站内，抓取时要特殊处理
        "link_url": (info.get("link_url") or "").strip(),
        "status": info.get("status"),
    }


def fetch_all(
    user_id: str,
    item_type: int,
    sort_type: int,
    interval: float,
    max_pages: int,
    retries: int,
) -> tuple[list[dict], int]:
    records: list[dict] = []
    seen: set[str] = set()
    cursor = "0"
    total = 0
    page = 0

    while True:
        page += 1
        if max_pages and page > max_pages:
            print(f"已达到 --max-pages {max_pages}，停止翻页。")
            break

        payload = {
            "cursor": cursor,
            "user_id": user_id,
            "item_type": item_type,
            "sort_type": sort_type,
        }
        url = f"{API_URL}?aid={CLIENT_AID}&uuid={CLIENT_UUID}&spider=0"

        data = None
        last_error = ""
        for attempt in range(retries + 1):
            try:
                data = http_post(url, payload)
                break
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < retries:
                    time.sleep(1.5 * (attempt + 1))
        if data is None:
            print(f"第 {page} 页请求失败: {last_error}")
            break

        if data.get("err_no"):
            print(f"第 {page} 页接口返回错误: {data.get('err_no')} {data.get('err_msg')}")
            break

        items = data.get("data") or []
        total = data.get("count") or total
        for entry in items:
            record = normalize(entry, len(records) + 1)
            if record is None or record["id"] in seen:
                continue
            seen.add(record["id"])
            records.append(record)

        print(f"第 {page} 页: 本页 {len(items)} 条，累计 {len(records)}/{total or '?'}")

        if not data.get("has_more") or not items:
            break
        cursor = str(data.get("cursor") or (page * PAGE_SIZE))
        time.sleep(interval)

    return records, total


# ---------------------------------------------------------------- 输出


def write_json(records: list[dict], path: Path) -> None:
    path.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")


CSV_COLUMNS = [
    "rank", "id", "title", "url", "author", "category", "tags",
    "publish_time", "view_count", "digg_count", "collect_count",
    "comment_count", "read_time",
]


def write_csv(records: list[dict], path: Path, columns: list[str] | None = None) -> None:
    columns = columns or CSV_COLUMNS
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for record in records:
            row = []
            for key in columns:
                value = record.get(key, "")
                row.append(", ".join(value) if isinstance(value, list) else value)
            writer.writerow(row)


def write_markdown(records: list[dict], path: Path, item_label: str, total: int) -> None:
    by_year: dict[str, list[dict]] = {}
    for record in records:
        by_year.setdefault((record["publish_time"] or "未知")[:4], []).append(record)

    lines = [
        f"# 掘金赞过的{item_label}",
        "",
        f"- 总数: {len(records)}",
        f"- 接口报告总数: {total}",
        f"- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "- 排序: 按文章发布年份分组，组内保持点赞列表顺序",
        "",
    ]
    for year in sorted(by_year, reverse=True):
        group = by_year[year]
        lines.append(f"## {year}（{len(group)}）")
        lines.append("")
        for record in group:
            lines.append(f"1. [{record['title'] or record['id']}]({record['url']})")
            meta = [record["author"] or "佚名"]
            if record["category"]:
                meta.append(record["category"])
            if record["tags"]:
                meta.append(" / ".join(record["tags"][:3]))
            if record["publish_time"]:
                meta.append(record["publish_time"][:10])
            lines.append(f"   - {' ｜ '.join(meta)}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def env_value(name: str) -> str:
    """按 环境变量 → 仓库根目录 .env → 本目录 .env 的顺序取配置项。

    直接 python3 跑脚本时不会经过 run.sh 的 source，所以这里自己认一次 .env。
    解析只支持 KEY=VALUE、# 注释和可选的外层引号，够用了。
    """
    from_env = os.environ.get(name, "").strip()
    if from_env:
        return from_env

    project = Path(__file__).resolve().parent.parent
    for path in (project.parent / ".env", project / ".env"):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, raw = line.partition("=")
            if key.strip() == name:
                value = raw.strip().strip("\"'")
                if value:
                    return value
    return ""


def default_user_id() -> str:
    return env_value("JUEJIN_USER_ID")


def main() -> int:
    parser = argparse.ArgumentParser(description="拉取掘金点赞过的文章清单")
    parser.add_argument(
        "--user-id",
        default=default_user_id(),
        help="掘金用户 ID，默认取环境变量 JUEJIN_USER_ID 或 .env 里的同名项",
    )
    parser.add_argument("--item-type", type=int, default=2, help="2=文章 4=沸点 15=视频")
    parser.add_argument("--sort-type", type=int, default=2, help="接口排序方式，2 是默认的按点赞时间倒序")
    parser.add_argument("--output", default="output", help="输出目录")
    parser.add_argument("--interval", type=float, default=0.4, help="翻页间隔秒数")
    parser.add_argument("--max-pages", type=int, default=0, help="最多翻多少页，0 表示不限")
    parser.add_argument("--retries", type=int, default=2, help="单页失败重试次数")
    args = parser.parse_args()

    if not args.user_id:
        print("错误: 没有掘金用户 ID。")
        print("在仓库根目录 cp .env.example .env 并填好 JUEJIN_USER_ID，")
        print("或者用 --user-id <数字> / JUEJIN_USER_ID=<数字> 临时指定。")
        print("ID 就是个人主页地址 https://juejin.cn/user/<数字> 里的那串数字。")
        return 1

    base = Path(__file__).resolve().parent.parent
    out_dir = Path(args.output) if Path(args.output).is_absolute() else base / args.output
    out_dir.mkdir(parents=True, exist_ok=True)

    item_label = ITEM_TYPE_NAMES.get(args.item_type, f"item_type={args.item_type}")
    print(f"用户: {args.user_id}")
    print(f"类型: {item_label}")
    print(f"输出目录: {out_dir}")
    print()

    records, total = fetch_all(
        args.user_id, args.item_type, args.sort_type, args.interval, args.max_pages, args.retries
    )
    if not records:
        print("没有拉到任何条目。")
        return 1

    json_path = out_dir / "articles.json"
    write_json(records, json_path)
    write_csv(records, out_dir / "articles.csv")
    write_markdown(records, out_dir / "articles.md", item_label, total)

    print()
    print("=" * 60)
    print(f"共 {len(records)} 条（接口报告 {total} 条）")
    if total and len(records) != total and not args.max_pages:
        print("数量和接口报告不一致，通常是原文已删除或作者已注销，属正常现象。")
    print(f"清单: {json_path}")
    print(f"      {out_dir / 'articles.csv'}")
    print(f"      {out_dir / 'articles.md'}")
    print()
    print("下一步抓正文: python3 scripts/fetch_articles.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
