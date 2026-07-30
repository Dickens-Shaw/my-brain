#!/usr/bin/env python3
"""导出微信收藏里的语音（音频）资源。

收藏库 favorite.db 只存音频的元数据和 CDN 描述符，音频二进制不在里面，
本机也没有以文件形式落盘。真正的语音数据在消息库 db_storage/message/media_*.db
里，所以流程是：

1. 从解密后的 favorite.db 解析出所有音频条目（datafmt 为 silk 或 amr）。
2. 用同一个 passphrase 解密 media_*.db —— 每个库用自己的 salt 派生密钥。
3. 按 msgid 把语音 blob 对回收藏条目，写成 .silk 文件。
4. 装了 pilk 就顺手转 wav，再有 ffmpeg 就转 mp3；都没装就只留 .silk。

密钥派生和解密逻辑复用 extract_favorites.py，所以要先跑过一次
`extract_favorites.py capture`，让 passphrase 落到
~/.wcdb-key-tool/wechat-passphrase.json。

用法：
    python3 scripts/extract_favorite_audio.py scan      # 只列出收藏里的音频条目
    python3 scripts/extract_favorite_audio.py export    # 提取并转码
    python3 scripts/extract_favorite_audio.py export --format silk
    python3 scripts/extract_favorite_audio.py probe     # 排查媒体库结构
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract_favorites as wxfav  # noqa: E402

AUDIO_MAGICS = (b"#!SILK", b"\x02#!SILK", b"#!AMR", b"\x02#!AMR")
AUDIO_FORMATS = {"silk", "amr", "speex"}
DEFAULT_RATE = 24000

DATAITEM_RE = re.compile(r"<dataitem\b[^>]*>.*?</dataitem>", re.S)


@dataclass
class AudioItem:
    """收藏里的一段音频。一条收藏可能带多段（笔记、聊天记录）。"""

    fav_id: str
    fav_type: str
    fav_time: str
    fromusr: str
    realchatname: str
    msgid: str
    dataid: str
    datafmt: str
    duration_ms: int
    fullmd5: str
    fullsize: int
    cdn_datakey: str
    candidate_ids: list[str] = field(default_factory=list)

    @property
    def sender(self) -> str:
        return self.realchatname or self.fromusr or "unknown"

    @property
    def year(self) -> str:
        return (self.fav_time or "unknown")[:4] or "unknown"

    @property
    def duration_s(self) -> float:
        return round(self.duration_ms / 1000, 1)

    @property
    def dedupe_key(self) -> str:
        """同一段语音可能既是独立收藏、又被收进笔记，按内容指纹去重。"""
        return self.fullmd5 or self.dataid

    @property
    def lookup_keys(self) -> list[str]:
        """去消息库索引里查这段音频用的键：先 msgid，再退到内容 md5。"""
        keys = list(self.candidate_ids)
        if self.fullmd5:
            keys.append(self.fullmd5)
        return keys


# ---------------------------------------------------------------- 收藏侧解析


def tag_text(xml: str, name: str) -> str:
    match = re.search(rf"<{name}>(.*?)</{name}>", xml, re.S)
    return html.unescape(match.group(1).strip()) if match else ""


def tag_attr(xml: str, name: str) -> str:
    match = re.search(rf'\b{name}="([^"]*)"', xml)
    return html.unescape(match.group(1)) if match else ""


def to_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def parse_audio_items(db_path: Path) -> list[AudioItem]:
    """从解密后的 favorite.db 里挑出所有音频 dataitem。"""
    # immutable=1 免得 sqlite 想在只读目录里建 -wal 文件
    conn = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
    try:
        rows = conn.execute(
            "select local_id, type, update_time, content, source_id, fromusr, realchatname "
            "from fav_db_item order by update_time"
        ).fetchall()
    except sqlite3.Error as exc:
        raise RuntimeError(f"读取 fav_db_item 失败: {exc}") from exc
    finally:
        conn.close()

    items: list[AudioItem] = []
    for local_id, fav_type, update_time, content, source_id, fromusr, realchatname in rows:
        content = wxfav.decode_value(content)
        if not content or "datafmt" not in content:
            continue

        item_msgid = tag_text(content, "msgid") or (source_id or "")
        for block in DATAITEM_RE.findall(content):
            datafmt = tag_text(block, "datafmt").lower().lstrip(".")
            datatype = tag_attr(block, "datatype")
            if datafmt not in AUDIO_FORMATS and datatype != "3":
                continue

            msgid = (
                tag_text(block, "fromnewmsgid")
                or tag_attr(block, "datasourceid")
                or item_msgid
            )
            # 聊天记录里的 datasourceid 形如 "Ty___Sweet#5639726251572310566"
            candidates = {
                part
                for raw in (msgid, item_msgid, tag_attr(block, "datasourceid"))
                for part in re.split(r"[#|]", raw or "")
                if part.isdigit() and len(part) >= 10
            }

            items.append(
                AudioItem(
                    fav_id=str(local_id),
                    fav_type=str(fav_type),
                    fav_time=wxfav.unix_time(update_time),
                    fromusr=fromusr or "",
                    realchatname=realchatname or "",
                    msgid=msgid,
                    dataid=tag_attr(block, "dataid"),
                    datafmt=datafmt or "silk",
                    duration_ms=to_int(tag_text(block, "duration")),
                    fullmd5=tag_text(block, "fullmd5"),
                    fullsize=to_int(tag_text(block, "fullsize")),
                    cdn_datakey=tag_text(block, "cdn_datakey"),
                    candidate_ids=sorted(candidates),
                )
            )
    return items


# ---------------------------------------------------------------- 媒体库侧


def is_audio_blob(value: bytes) -> bool:
    return value.startswith(AUDIO_MAGICS)


def looks_like_msgid(value: object) -> bool:
    if isinstance(value, int):
        return abs(value) >= 10**9
    if isinstance(value, str) and value.isdigit():
        return len(value) >= 10
    return False


def find_message_dbs(db_storage: Path) -> list[Path]:
    """按命中可能性排序：语音一般在 media_*.db，找不到才去翻消息库。

    message_[0-9]*.db 的写法是为了跳开 message_fts.db —— 那是全文索引，
    12 MB 白解一遍没意义。
    """
    message_dir = db_storage / "message"
    if not message_dir.is_dir():
        return []

    groups = [
        sorted(message_dir.glob("media_*.db")),
        [message_dir / "message_resource.db"],
        sorted(message_dir.glob("message_[0-9]*.db")),
    ]
    ordered: list[Path] = []
    for group in groups:
        for path in group:
            if path.is_file() and path not in ordered:
                ordered.append(path)
    return ordered


def decrypt_with_passphrase(src: Path, passphrase: str, out_dir: Path) -> Path | None:
    """用 passphrase + 该库自己的 salt 派生密钥并解密。校验不过返回 None。"""
    page1 = wxfav.read_page1(src)
    enc_key = wxfav.hashlib.pbkdf2_hmac(
        "sha512",
        bytes.fromhex(passphrase),
        page1[: wxfav.SALT_SIZE],
        256000,
        dklen=wxfav.KEY_SIZE,
    )
    if not wxfav.verify_key(enc_key, page1):
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / src.name
    wxfav.decrypt_database(src, enc_key.hex(), target)
    return target


def blob_fingerprints(blob: bytes) -> list[str]:
    """blob 的内容指纹，用来和收藏里的 fullmd5 对账。

    微信的 silk 前面会多一个 \\x02 标记字节，收藏记的 fullmd5 有可能算的是
    去掉标记后的内容，所以两种都算上。
    """
    digests = [hashlib.md5(blob).hexdigest()]
    if blob[:1] == b"\x02":
        digests.append(hashlib.md5(blob[1:]).hexdigest())
    return digests


def row_timestamps(row: tuple) -> list[int]:
    """挑出行里像 unix 秒级时间戳的值，用来推算本机语音缓存的时间覆盖。

    2001-09 ~ 2033-05 之间的整数当时间戳，避开 msgid 那种 19 位数。
    """
    return [value for value in row if isinstance(value, int) and 10**9 <= value < 2 * 10**9]


def index_audio_blobs(db_path: Path) -> tuple[dict[str, bytes], list[str], list[int]]:
    """扫出所有音频 blob，按同行出现的各种 id 和内容 md5 建索引。

    微信各版本的表名列名都变过，所以不写死 schema：命中音频 magic 的 blob
    就把同一行里所有像 msgid 的值都当索引键。再补一份内容 md5 索引，
    这样即使 msgid 对不上（转发、重新收藏）也还有机会认出同一段音频。
    附带返回命中位置，方便排查。
    """
    index: dict[str, bytes] = {}
    hits: list[str] = []
    stamps: list[int] = []
    conn = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
    try:
        tables = [
            row[0]
            for row in conn.execute("select name from sqlite_master where type='table'")
        ]
        for table in tables:
            try:
                cursor = conn.execute(f'select * from "{table}"')
            except sqlite3.Error:
                continue

            found = 0
            for row in cursor:
                blob = next(
                    (
                        bytes(value)
                        for value in row
                        if isinstance(value, (bytes, bytearray)) and is_audio_blob(bytes(value))
                    ),
                    None,
                )
                if blob is None:
                    continue
                found += 1
                for value in row:
                    if looks_like_msgid(value):
                        index.setdefault(str(value), blob)
                for digest in blob_fingerprints(blob):
                    index.setdefault(digest, blob)
                stamps.extend(row_timestamps(row))
            if found:
                hits.append(f"{db_path.name}.{table}: {found} 段音频")
    finally:
        conn.close()
    return index, hits, stamps


# ---------------------------------------------------------------- 输出


def safe_filename(value: str, limit: int = 60) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r'[/\\:*?"<>|\r\n\t]', "-", value)
    value = re.sub(r"[\x00-\x1f]", "", value)
    value = re.sub(r"\s+", " ", value).strip(" .-")
    if len(value) > limit:
        value = value[:limit].rstrip(" .-")
    return value or "unknown"


def use_local_venv() -> None:
    """把本目录 .venv 的 site-packages 挂进 sys.path。

    Homebrew 的 python3 受 PEP 668 保护，装不了 pilk，硬绕有搞坏 Homebrew 的风险。
    所以转码依赖单独装在 exporters/wechat-favorites/.venv 里，这样用系统 python3
    直接跑脚本也能找到 pilk，不必先 activate。C 扩展是按 Python 版本编译的，
    所以只挂当前解释器对应的那个目录。
    """
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    site = Path(__file__).resolve().parent.parent / ".venv" / "lib" / version / "site-packages"
    if site.is_dir() and str(site) not in sys.path:
        sys.path.append(str(site))


def convert_audio(silk_path: Path, want: str, rate: int) -> tuple[str, str]:
    """把 silk 转成可播放格式。返回 (产物相对名, 说明)；失败时产物为空。"""
    if want == "silk":
        return "", ""

    use_local_venv()
    try:
        import pilk
    except ImportError:
        return "", "未装 pilk，跳过转码。在本目录执行 python3 -m venv .venv && .venv/bin/pip install pilk"

    wav_path = silk_path.with_suffix(".wav")
    try:
        pilk.silk_to_wav(str(silk_path), str(wav_path), rate=rate)
    except Exception as exc:
        return "", f"pilk 转码失败: {type(exc).__name__}: {exc}"

    if want == "wav":
        return wav_path.name, ""

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return wav_path.name, "未安装 ffmpeg，只生成了 wav（brew install ffmpeg）"

    mp3_path = silk_path.with_suffix(".mp3")
    result = subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-i", str(wav_path), "-b:a", "64k", str(mp3_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return wav_path.name, f"ffmpeg 转 mp3 失败: {result.stderr.strip()[:120]}"
    wav_path.unlink(missing_ok=True)
    return mp3_path.name, ""


def write_index(out_dir: Path, records: list[dict], rate: int, coverage: str) -> None:
    json_path = out_dir / "audio-index.json"
    json_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    extracted = [r for r in records if r["status"] == "ok"]
    missing = [r for r in records if r["status"] != "ok"]
    total_s = sum(r["duration_s"] for r in records)

    md_path = out_dir / "audio-index.md"
    with md_path.open("w", encoding="utf-8") as f:
        f.write("# 微信收藏音频清单\n\n")
        f.write(f"- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"- 收藏中的音频条目: {len(records)} 段，合计 {total_s:.1f} 秒\n")
        f.write(f"- 成功提取: {len(extracted)} 段\n")
        f.write(f"- 未能提取: {len(missing)} 段\n")
        f.write(f"- 采样率: {rate} Hz\n")
        if coverage:
            f.write(f"- 本机语音缓存覆盖: {coverage}\n")
        f.write("\n")

        if extracted:
            f.write("## 已提取\n\n")
            f.write("| 收藏时间 | 发送人 | 时长 | 文件 |\n")
            f.write("| --- | --- | --- | --- |\n")
            for r in extracted:
                f.write(
                    f"| {r['fav_time'][:19]} | {r['sender']} | {r['duration_s']:.1f}s | "
                    f"`{r['path']}` |\n"
                )
            f.write("\n")

        if missing:
            f.write("## 未能提取\n\n")
            f.write("收藏库只存音频的 CDN 描述符，音频本体要靠本机消息库里的副本。\n")
            if coverage:
                f.write(
                    f"本机语音缓存只覆盖 {coverage} —— 早于这个区间的收藏，"
                    "语音数据从来没在这台机器上落过盘（Mac 微信不会回溯同步历史聊天）。\n"
                )
            f.write("落在区间内却仍然缺失的，是那条聊天消息后来被删掉了。\n")
            f.write("这两种情况都没有离线还原的办法。\n\n")
            f.write("| 收藏时间 | 发送人 | 时长 | msgid | 原因 |\n")
            f.write("| --- | --- | --- | --- | --- |\n")
            for r in missing:
                f.write(
                    f"| {r['fav_time'][:19]} | {r['sender']} | {r['duration_s']:.1f}s | "
                    f"`{r['msgid']}` | {r['error']} |\n"
                )
            f.write("\n")

    wxfav.log(f"已导出: {json_path}")
    wxfav.log(f"已导出: {md_path}")


# ---------------------------------------------------------------- 命令


def resolve_favorite_db(args: argparse.Namespace, base: Path) -> Path:
    if args.decrypted:
        path = Path(args.decrypted).expanduser()
    else:
        path = base / args.output_root / "decrypted" / "favorite_decrypted.db"
    if not path.is_file():
        raise RuntimeError(
            f"找不到解密后的收藏库: {path}\n先跑一次 `bash run.sh` 或 "
            "`python3 scripts/extract_favorites.py decrypt`。"
        )
    return path


def read_passphrase() -> str:
    try:
        passphrase = wxfav.load_passphrase()
    except PermissionError:
        raise RuntimeError(
            f"没有权限读取 {wxfav.DEFAULT_PASSPHRASE_FILE}。\n"
            "它是之前 sudo 抓密钥时留下的 root 文件，执行一次即可：\n"
            f'  sudo chown "$(whoami)" {wxfav.DEFAULT_PASSPHRASE_FILE}'
        ) from None
    if not passphrase:
        raise RuntimeError(
            "没有可用的 passphrase 缓存。先执行：\n"
            "  sudo python3 scripts/extract_favorites.py capture"
        )
    return passphrase


def command_scan(args: argparse.Namespace, base: Path) -> int:
    items = parse_audio_items(resolve_favorite_db(args, base))
    if not items:
        wxfav.log("收藏里没有音频条目。")
        return 0

    total = sum(item.duration_ms for item in items) / 1000
    unique = {item.fullmd5 for item in items if item.fullmd5}
    wxfav.log(f"收藏音频条目: {len(items)} 段，去重后 {len(unique)} 段，合计 {total:.1f} 秒")
    wxfav.log("")
    for item in items:
        wxfav.log(
            f"  fav#{item.fav_id:<6} type={item.fav_type:<3} {item.fav_time[:19]}  "
            f"{item.duration_s:>6.1f}s  {item.datafmt:<5} {item.sender[:24]:<24} "
            f"msgid={item.msgid}"
        )
    return 0


def build_blob_index(
    args: argparse.Namespace, wanted: set[str], stop_early: bool
) -> tuple[dict[str, bytes], list[str], str]:
    """逐个解密消息库并建音频索引。

    解密产物含完整聊天记录，索引完立刻删掉，除非显式 --keep-cache。
    第三个返回值是本机语音缓存的时间覆盖，收藏音频落在区间外就基本没救。
    """
    passphrase = read_passphrase()
    db = wxfav.choose_favorite_db(None, args.account)
    keep_cache = getattr(args, "keep_cache", False)
    cache_dir = Path(args.cache).expanduser()

    index: dict[str, bytes] = {}
    hits: list[str] = []
    stamps: list[int] = []

    for src in find_message_dbs(db.db_storage):
        wxfav.log(f"  解密 {src.name} ({src.stat().st_size / 1024 / 1024:.1f} MB) ...")
        try:
            decrypted = decrypt_with_passphrase(src, passphrase, cache_dir)
        except Exception as exc:
            wxfav.log(f"  {src.name}: 解密出错，跳过（{type(exc).__name__}: {exc}）")
            continue
        if decrypted is None:
            wxfav.log(f"  {src.name}: 密钥校验不通过，跳过")
            continue

        try:
            found, where, seen = index_audio_blobs(decrypted)
        finally:
            if not keep_cache:
                decrypted.unlink(missing_ok=True)

        hits.extend(where)
        stamps.extend(seen)
        for key, blob in found.items():
            index.setdefault(key, blob)
        wxfav.log(f"  {src.name}: 音频索引 {len(found)} 个键")

        if stop_early and wanted and wanted <= set(index):
            wxfav.log("  待匹配的 msgid 已全部命中，后面的库不再解密")
            break

    if not keep_cache and cache_dir.is_dir() and not any(cache_dir.iterdir()):
        cache_dir.rmdir()

    coverage = ""
    if stamps:
        coverage = f"{wxfav.unix_time(min(stamps))[:10]} ~ {wxfav.unix_time(max(stamps))[:10]}"

    return index, hits, coverage


def command_probe(args: argparse.Namespace, base: Path) -> int:
    items = parse_audio_items(resolve_favorite_db(args, base))
    wanted = {key for item in items for key in item.lookup_keys}
    wxfav.log(f"收藏音频 {len(items)} 段，待匹配 id {len(wanted)} 个")
    wxfav.log("")
    wxfav.log("扫描消息库:")
    index, hits, coverage = build_blob_index(args, wanted, stop_early=False)
    wxfav.log("")
    if coverage:
        wxfav.log(f"本机语音缓存覆盖: {coverage}")
    wxfav.log("命中位置:")
    for line in hits or ["  （没有任何表含 silk/amr 音频 blob）"]:
        wxfav.log(f"  {line}")
    matched = wanted & set(index)
    wxfav.log("")
    wxfav.log(f"可对上的收藏音频 id: {len(matched)}/{len(wanted)}")
    for cid in sorted(wanted):
        mark = "命中" if cid in index else "缺失"
        wxfav.log(f"  {mark} {cid}")
    return 0


def command_export(args: argparse.Namespace, base: Path) -> int:
    items = parse_audio_items(resolve_favorite_db(args, base))
    if not items:
        wxfav.log("收藏里没有音频条目。")
        return 0

    out_dir = base / args.output_root / "audio"
    out_dir.mkdir(parents=True, exist_ok=True)

    wanted = {key for item in items for key in item.lookup_keys}
    wxfav.log(f"收藏音频条目: {len(items)} 段")
    wxfav.log("扫描本机消息库里的语音数据:")
    index, _, coverage = build_blob_index(args, wanted, stop_early=True)
    if coverage:
        wxfav.log(f"本机语音缓存覆盖: {coverage}")
    wxfav.log("")

    records: list[dict] = []
    written: dict[str, str] = {}
    warned: set[str] = set()

    for item in items:
        record = {
            "fav_id": item.fav_id,
            "fav_type": item.fav_type,
            "fav_time": item.fav_time,
            "sender": item.sender,
            "fromusr": item.fromusr,
            "msgid": item.msgid,
            "dataid": item.dataid,
            "datafmt": item.datafmt,
            "duration_s": item.duration_s,
            "fullsize": item.fullsize,
            "fullmd5": item.fullmd5,
            "status": "missing",
            "path": "",
            "error": "",
        }

        if item.dedupe_key and item.dedupe_key in written:
            record["status"] = "ok"
            record["path"] = written[item.dedupe_key]
            record["error"] = "与其他收藏是同一段音频，未重复写文件"
            records.append(record)
            continue

        blob = next((index[key] for key in item.lookup_keys if key in index), None)
        if blob is None:
            if coverage and item.fav_time[:10] < coverage[:10]:
                record["error"] = f"收藏时间早于本机语音缓存（{coverage}），本机从未存过"
            else:
                record["error"] = "本机消息库里没有这段语音，原消息应该已被删除"
            records.append(record)
            wxfav.log(f"  缺失  fav#{item.fav_id} {item.fav_time[:10]} {item.duration_s}s")
            continue

        folder = out_dir / item.year
        folder.mkdir(parents=True, exist_ok=True)
        stem = safe_filename(
            f"{item.fav_time[:10]}-{item.sender}-{item.duration_s:.0f}s-fav{item.fav_id}"
        )
        silk_path = folder / f"{stem}.silk"
        silk_path.write_bytes(blob)

        produced, warning = convert_audio(silk_path, args.format, args.rate)
        if warning and warning not in warned:
            wxfav.log(f"  提示: {warning}")
            warned.add(warning)

        final = folder / produced if produced else silk_path
        if produced and not args.keep_silk:
            silk_path.unlink(missing_ok=True)

        relative = str(final.relative_to(out_dir))
        record["status"] = "ok"
        record["path"] = relative
        record["error"] = warning
        written[item.dedupe_key] = relative
        records.append(record)
        wxfav.log(f"  OK    fav#{item.fav_id} {item.fav_time[:10]} {item.duration_s}s -> {relative}")

    wxfav.log("")
    write_index(out_dir, records, args.rate, coverage)
    ok = sum(1 for r in records if r["status"] == "ok")
    wxfav.log("")
    wxfav.log(f"完成: 成功 {ok} / 缺失 {len(records) - ok}，输出目录 {out_dir}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="导出微信收藏里的语音资源")
    parser.add_argument("--account", help="账号目录名，形如 SomeName_1a2b；只有一个账号时可省略")
    parser.add_argument("--output-root", default="output", help="输出根目录，默认 output")
    parser.add_argument("--decrypted", help="解密后的 favorite_decrypted.db 路径")
    parser.add_argument(
        "--cache",
        default="/tmp/wechat-audio-cache",
        help="解密消息库时的临时目录，默认 /tmp/wechat-audio-cache",
    )
    parser.add_argument(
        "--keep-cache",
        action="store_true",
        help="保留解密后的消息库。注意它含完整聊天记录，默认用完即删",
    )

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", help="只列出收藏里的音频条目，不碰消息库")

    export = sub.add_parser("export", help="提取音频并按需转码")
    export.add_argument(
        "--format",
        choices=("mp3", "wav", "silk"),
        default="mp3",
        help="目标格式，默认 mp3；缺依赖时自动退回 wav 或 silk",
    )
    export.add_argument("--rate", type=int, default=DEFAULT_RATE, help="silk 采样率，默认 24000")
    export.add_argument("--keep-silk", action="store_true", help="转码后保留原始 .silk")

    sub.add_parser("probe", help="排查消息库结构和 msgid 匹配情况")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    base = Path(__file__).resolve().parent.parent

    try:
        if args.command == "scan":
            return command_scan(args, base)
        if args.command == "export":
            return command_export(args, base)
        if args.command == "probe":
            return command_probe(args, base)
        parser.error("未知命令")
    except KeyboardInterrupt:
        wxfav.log("\n已中断。")
        return 130
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
