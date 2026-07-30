#!/usr/bin/env python3
"""高频监视微信收藏的 temp 目录，把任何新出现/变化的文件立刻复制走。

用途：把收藏语音「转成笔记」时，微信会往
`business/favorite/temp/NoteCache/` 写入每段语音的缓存文件（当前版本是加密的
`temp_<时间戳>.silk`）。这些文件可能很快被覆盖或删除，所以这里用轮询 + 立即复制，
而不是事后对比快照。

用法：
    python3 scripts/watch_note_cache.py [持续秒数] [--account NAME_1a2b] [--out DIR]

启动后回到微信，逐条把收藏语音转成笔记并点开播放；抓到的文件会复制到 --out
（默认 output/note-cache/）。开头带 `#!SILK` 的是明文语音，会额外标注。

背景与已知格式细节见 AUDIO.md 的「转笔记会落一份加密语音缓存」。
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import time
from datetime import datetime

XWECHAT = pathlib.Path(
    "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
).expanduser()
MAGICS = (b"#!SILK", b"\x02#!SILK", b"#!AMR", b"\x02#!AMR")


def find_account(name: str | None) -> pathlib.Path:
    if name:
        p = XWECHAT / name
        if not p.is_dir():
            raise SystemExit(f"找不到账号目录: {p}")
        return p
    candidates = [
        d
        for d in XWECHAT.iterdir()
        if d.is_dir() and d.name not in {"all_users", "global_config"}
    ]
    if not candidates:
        raise SystemExit(f"在 {XWECHAT} 下没找到账号目录，用 --account 指定")
    if len(candidates) > 1:
        names = ", ".join(d.name for d in candidates)
        raise SystemExit(f"有多个账号目录（{names}），用 --account 指定一个")
    return candidates[0]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("duration", nargs="?", type=int, default=300, help="监视持续秒数")
    ap.add_argument("--account", help="账号目录名，如 NAME_1a2b；不填则自动探测")
    ap.add_argument("--out", type=pathlib.Path, help="副本输出目录")
    ap.add_argument("--interval", type=float, default=0.12, help="轮询间隔秒")
    args = ap.parse_args()

    account = find_account(args.account)
    watch = [account / "business" / "favorite", account / "cache"]
    out = args.out or (pathlib.Path(__file__).resolve().parent.parent / "output" / "note-cache")
    out.mkdir(parents=True, exist_ok=True)

    print(f"账号: {account.name}")
    print(f"监视: {', '.join(str(w) for w in watch)}")
    print(f"输出: {out}")
    print(f"持续 {args.duration}s —— 现在回微信把收藏语音转成笔记并播放\n", flush=True)

    seen: dict[str, tuple[int, float]] = {}
    captured = 0
    first_pass = True
    deadline = time.time() + args.duration

    while time.time() < deadline:
        for root in watch:
            if not root.exists():
                continue
            try:
                paths = list(root.rglob("*"))
            except OSError:
                continue
            for path in paths:
                try:
                    if not path.is_file():
                        continue
                    st = path.stat()
                except OSError:
                    continue
                key = str(path)
                state = (st.st_size, st.st_mtime)
                if seen.get(key) == state:
                    continue
                seen[key] = state
                if first_pass:
                    continue
                try:
                    head = path.open("rb").read(16)
                    captured += 1
                    stamp = datetime.now().strftime("%H%M%S_%f")[:-3]
                    dest = out / f"{stamp}_{captured:03d}_{path.name}"
                    shutil.copy2(path, dest)
                except OSError as exc:
                    print(f"[{datetime.now():%H:%M:%S}] 抓取失败 {path.name}: {exc}", flush=True)
                    continue
                flag = "  <<< 明文 SILK!" if head.startswith(MAGICS) else ""
                print(
                    f"[{datetime.now():%H:%M:%S}] {st.st_size:>8}B  {head[:12].hex(' ')}  "
                    f"{path.name}{flag}",
                    flush=True,
                )
        first_pass = False
        time.sleep(args.interval)

    print(f"\n监视结束，共抓到 {captured} 个变化，副本在 {out}", flush=True)


if __name__ == "__main__":
    main()
