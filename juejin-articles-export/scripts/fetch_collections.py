#!/usr/bin/env python3
"""拉取掘金个人主页的「收藏集」清单。

两步走：先取收藏文件夹列表 `collectionset/list`，再逐个翻 `collectionset/detail`
拿里面的文章。两个接口都不需要签名，公开收藏集不带 Cookie 也能读。

只有 permission=1 的私有收藏集（默认那个「我的收藏」通常就是）必须登录态才可见，
想连它一起导出就给 JUEJIN_COOKIE 塞一份浏览器 Cookie，见 README。

导出：
    output/collections.json   完整字段，供 fetch_articles.py 使用
    output/collections.csv    表格，多一列所属收藏集
    output/collections.md     按收藏集分组的可读清单

用法：
    python3 scripts/fetch_collections.py                  # 公开收藏集
    python3 scripts/fetch_collections.py --with-cookie    # 带上 Cookie，含私有收藏集
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from fetch_list import (
    CLIENT_AID,
    CLIENT_UUID,
    CSV_COLUMNS,
    default_user_id,
    env_value,
    http_post,
    normalize,
    timestamp_to_text,
    write_csv,
    write_json,
)

LIST_API = "https://api.juejin.cn/interact_api/v2/collectionset/list"
DETAIL_API = "https://api.juejin.cn/interact_api/v2/collectionset/detail"
PAGE_SIZE = 20
QUERY = f"aid={CLIENT_AID}&uuid={CLIENT_UUID}&spider=0"


def post_with_retry(
    url: str,
    payload: dict,
    cookie: str,
    retries: int,
) -> dict | None:
    """失败重试，返回 None 表示这一页彻底拿不到。"""
    headers = {"Cookie": cookie} if cookie else None
    last_error = ""
    for attempt in range(retries + 1):
        try:
            data = http_post(url, payload, extra_headers=headers)
            if data.get("err_no"):
                print(f"  接口返回错误: {data.get('err_no')} {data.get('err_msg')}")
                return None
            return data
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    print(f"  请求失败: {last_error}")
    return None


def fetch_collection_list(user_id: str, cookie: str, interval: float, retries: int) -> list[dict]:
    """收藏文件夹列表。翻页靠接口回的 cursor。"""
    folders: list[dict] = []
    cursor = "0"
    while True:
        payload = {"article_id": "", "user_id": user_id, "cursor": cursor, "limit": PAGE_SIZE}
        data = post_with_retry(f"{LIST_API}?{QUERY}", payload, cookie, retries)
        if data is None:
            break

        items = data.get("data") or []
        folders.extend(items)
        if not data.get("has_more") or not items:
            break
        cursor = str(data.get("cursor") or "")
        if not cursor:
            break
        time.sleep(interval)
    return folders


def fetch_collection_articles(
    collection_id: str,
    cookie: str,
    interval: float,
    retries: int,
) -> list[dict]:
    """某个收藏集里的文章，保持接口返回的顺序（收藏时间倒序）。"""
    entries: list[dict] = []
    cursor = "0"
    while True:
        payload = {"collection_id": collection_id, "limit": PAGE_SIZE, "cursor": cursor}
        data = post_with_retry(f"{DETAIL_API}?{QUERY}", payload, cookie, retries)
        if data is None:
            break

        block = data.get("data") or {}
        items = block.get("articles") or []
        entries.extend(items)
        if not data.get("has_more") or not items:
            break
        cursor = str(data.get("cursor") or len(entries))
        time.sleep(interval)
    return entries


def build_records(collections: list[dict]) -> list[dict]:
    """摊平成 fetch_articles.py 能直接吃的清单。

    一篇文章可以同时在多个收藏集里，这里只留第一次出现的那条，
    其余收藏集名字并到 collections 字段，避免同一篇正文落两份文件。
    """
    records: list[dict] = []
    by_id: dict[str, dict] = {}

    for folder in collections:
        for index, entry in enumerate(folder["entries"], 1):
            record = normalize(entry, index)
            if record is None:
                continue

            existing = by_id.get(record["id"])
            if existing is not None:
                if folder["collection_name"] not in existing["collections"]:
                    existing["collections"].append(folder["collection_name"])
                continue

            record["collection_id"] = folder["collection_id"]
            record["collection_name"] = folder["collection_name"]
            record["collections"] = [folder["collection_name"]]
            by_id[record["id"]] = record
            records.append(record)

    return records


def write_markdown(collections: list[dict], records: list[dict], path: Path) -> None:
    lines = [
        "# 掘金收藏集",
        "",
        f"- 收藏集: {len(collections)}",
        f"- 去重后文章: {len(records)}",
        f"- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "- 排序: 收藏集按接口顺序（最近更新在前），组内保持收藏时间倒序",
        "",
    ]
    for folder in collections:
        title = folder["collection_name"]
        if folder["permission"] == 1:
            title += "（私有）"
        lines.append(f"## {title}（{len(folder['entries'])}）")
        lines.append("")
        if folder["description"]:
            lines.append(f"> {folder['description']}")
            lines.append("")
        if not folder["entries"]:
            lines.append("_空收藏集_")
            lines.append("")
            continue

        for index, entry in enumerate(folder["entries"], 1):
            record = normalize(entry, index)
            if record is None:
                continue
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


def main() -> int:
    parser = argparse.ArgumentParser(description="拉取掘金收藏集里的文章清单")
    parser.add_argument(
        "--user-id",
        default=default_user_id(),
        help="掘金用户 ID，默认取环境变量 JUEJIN_USER_ID 或 .env 里的同名项",
    )
    parser.add_argument(
        "--with-cookie",
        action="store_true",
        help="带上 JUEJIN_COOKIE 请求，私有收藏集只有这样才看得到",
    )
    parser.add_argument("--output", default="output", help="输出目录")
    parser.add_argument("--interval", type=float, default=0.4, help="请求间隔秒数")
    parser.add_argument("--retries", type=int, default=2, help="单次请求失败重试次数")
    args = parser.parse_args()

    if not args.user_id:
        print("错误: 没有掘金用户 ID。")
        print("在仓库根目录 cp .env.example .env 并填好 JUEJIN_USER_ID，")
        print("或者用 --user-id <数字> / JUEJIN_USER_ID=<数字> 临时指定。")
        return 1

    cookie = ""
    if args.with_cookie:
        cookie = env_value("JUEJIN_COOKIE")
        if not cookie:
            print("警告: 指定了 --with-cookie 但 JUEJIN_COOKIE 是空的，只能拿到公开收藏集。")

    base = Path(__file__).resolve().parent.parent
    out_dir = Path(args.output) if Path(args.output).is_absolute() else base / args.output
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"用户: {args.user_id}")
    print(f"输出目录: {out_dir}")
    print(f"Cookie: {'已带上' if cookie else '未使用（私有收藏集会缺失）'}")
    print()

    folders = fetch_collection_list(args.user_id, cookie, args.interval, args.retries)
    if not folders:
        print("没有拉到任何收藏集。")
        return 1
    print(f"收藏集 {len(folders)} 个")
    print()

    collections: list[dict] = []
    for index, folder in enumerate(folders, 1):
        collection_id = str(folder.get("collection_id") or "")
        name = (folder.get("collection_name") or collection_id).strip()
        reported = folder.get("post_article_count") or 0
        print(f"[{index}/{len(folders)}] {name}（接口报告 {reported} 篇）")

        entries = (
            fetch_collection_articles(collection_id, cookie, args.interval, args.retries)
            if reported
            else []
        )
        if reported and len(entries) != reported:
            print(f"  实际拿到 {len(entries)} 篇，差额通常是原文已删除或作者已注销")

        collections.append(
            {
                "collection_id": collection_id,
                "collection_name": name,
                "description": (folder.get("description") or "").strip(),
                "permission": folder.get("permission") or 0,
                "is_default": bool(folder.get("is_default")),
                "post_article_count": reported,
                "update_time": timestamp_to_text(folder.get("update_time")),
                "entries": entries,
            }
        )
        time.sleep(args.interval)

    records = build_records(collections)
    if not records:
        print("\n收藏集里没有任何文章。")
        return 1

    json_path = out_dir / "collections.json"
    write_json(records, json_path)
    write_csv(records, out_dir / "collections.csv", ["collection_name", *CSV_COLUMNS])
    write_markdown(collections, records, out_dir / "collections.md")

    total_entries = sum(len(folder["entries"]) for folder in collections)
    print()
    print("=" * 60)
    print(f"{len(collections)} 个收藏集，共 {total_entries} 条，去重后 {len(records)} 篇")
    if total_entries != len(records):
        print("差额是同一篇文章被放进了多个收藏集，只导出一份，归属记在 collections 字段。")
    print(f"清单: {json_path}")
    print(f"      {out_dir / 'collections.csv'}")
    print(f"      {out_dir / 'collections.md'}")
    print()
    print("下一步抓正文:")
    print("  python3 scripts/fetch_articles.py --input output/collections.json \\")
    print("      --output output/collections --group-by collection")
    return 0


if __name__ == "__main__":
    sys.exit(main())
