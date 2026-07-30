#!/usr/bin/env python3
"""macOS 微信 4.1+ 收藏导出工具。

用途：
1. 自动定位当前账号的 favorite.db。
2. 用 LLDB 捕获 WeChat 4.1+ 的 passphrase。
3. 通过 favorite.db 自身 salt 派生并校验 SQLCipher4 密钥。
4. 解密 favorite.db，并导出收藏清单。

安全边界：
- 本脚本只读取本机当前用户的微信数据目录。
- 不修改微信数据库。
- 不上传任何内容。
"""
from __future__ import annotations

import argparse
import csv
import ctypes
import ctypes.util
import hashlib
import hmac
import html
import json
import os
import pathlib
import platform
import re
import select
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from typing import Any

PAGE_SIZE = 4096
KEY_SIZE = 32
SALT_SIZE = 16
IV_SIZE = 16
HMAC_SIZE = 64
RESERVE_SIZE = 80
SQLITE_HEADER = b"SQLite format 3\x00"

DEFAULT_ACCOUNT_ROOT = pathlib.Path(
    "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
).expanduser()
DEFAULT_PASSPHRASE_FILE = pathlib.Path("~/.wcdb-key-tool/wechat-passphrase.json").expanduser()

_lib_system = ctypes.CDLL(ctypes.util.find_library("System"))
_lib_system.CCCrypt.argtypes = [
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_char_p,
    ctypes.c_size_t,
    ctypes.c_char_p,
    ctypes.c_char_p,
    ctypes.c_size_t,
    ctypes.c_char_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
_lib_system.CCCrypt.restype = ctypes.c_int32

_KCC_DECRYPT = 1
_KCC_ALGORITHM_AES = 0


@dataclass(frozen=True)
class FavoriteDb:
    account: str
    db_storage: pathlib.Path
    db_path: pathlib.Path
    size: int
    salt_hex: str


def log(message: str) -> None:
    print(message, flush=True)


def run_text(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    return (result.stdout or "") + (result.stderr or "")


def read_page1(db_path: pathlib.Path) -> bytes:
    with db_path.open("rb") as f:
        page = f.read(PAGE_SIZE)
    if len(page) < PAGE_SIZE:
        raise RuntimeError(f"数据库太小，无法读取第一页: {db_path}")
    return page


def discover_favorite_dbs(root: pathlib.Path = DEFAULT_ACCOUNT_ROOT) -> list[FavoriteDb]:
    if not root.exists():
        return []

    found: list[FavoriteDb] = []
    for account_dir in sorted(root.iterdir()):
        db_storage = account_dir / "db_storage"
        db_path = db_storage / "favorite" / "favorite.db"
        if not db_path.is_file():
            continue
        page1 = read_page1(db_path)
        found.append(
            FavoriteDb(
                account=account_dir.name,
                db_storage=db_storage,
                db_path=db_path,
                size=db_path.stat().st_size,
                salt_hex=page1[:SALT_SIZE].hex(),
            )
        )
    return found


def choose_favorite_db(explicit_db: str | None, account: str | None) -> FavoriteDb:
    if explicit_db:
        db_path = pathlib.Path(explicit_db).expanduser().resolve()
        if not db_path.is_file():
            raise RuntimeError(f"favorite.db 不存在: {db_path}")
        db_storage = db_path.parents[1] if db_path.parent.name == "favorite" else db_path.parent
        page1 = read_page1(db_path)
        return FavoriteDb(
            account=db_path.parts[-4] if len(db_path.parts) >= 4 else "manual",
            db_storage=db_storage,
            db_path=db_path,
            size=db_path.stat().st_size,
            salt_hex=page1[:SALT_SIZE].hex(),
        )

    candidates = discover_favorite_dbs()
    if account:
        candidates = [item for item in candidates if item.account == account]
    if not candidates:
        raise RuntimeError("未找到 favorite.db，请确认已登录 macOS 微信且打开过收藏。")
    if len(candidates) > 1:
        detail = "\n".join(f"- {item.account}: {item.db_path}" for item in candidates)
        raise RuntimeError(f"发现多个账号，请用 --account 指定一个:\n{detail}")
    return candidates[0]


def aes_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    out_buf = ctypes.create_string_buffer(len(data) + 32)
    out_len = ctypes.c_size_t(0)
    status = _lib_system.CCCrypt(
        _KCC_DECRYPT,
        _KCC_ALGORITHM_AES,
        0,
        key,
        len(key),
        iv,
        data,
        len(data),
        out_buf,
        len(out_buf),
        ctypes.byref(out_len),
    )
    if status != 0:
        raise RuntimeError(f"CommonCrypto AES 解密失败: status={status}")
    return out_buf.raw[: out_len.value]


def mac_key_for(enc_key: bytes, salt: bytes) -> bytes:
    mac_salt = bytes(b ^ 0x3A for b in salt)
    return hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=KEY_SIZE)


def verify_key(enc_key: bytes, page1: bytes) -> bool:
    mac_key = mac_key_for(enc_key, page1[:SALT_SIZE])
    hmac_data = page1[SALT_SIZE : PAGE_SIZE - RESERVE_SIZE + IV_SIZE]
    stored = page1[PAGE_SIZE - HMAC_SIZE : PAGE_SIZE]
    digest = hmac.new(mac_key, hmac_data, hashlib.sha512)
    digest.update(struct.pack("<I", 1))
    return hmac.compare_digest(digest.digest(), stored)


def derive_favorite_key(passphrase_hex: str, favorite_db: FavoriteDb) -> str:
    passphrase = bytes.fromhex(passphrase_hex)
    salt = bytes.fromhex(favorite_db.salt_hex)
    enc_key = hashlib.pbkdf2_hmac("sha512", passphrase, salt, 256000, dklen=KEY_SIZE)
    if not verify_key(enc_key, read_page1(favorite_db.db_path)):
        raise RuntimeError("passphrase 可读取，但派生出的 favorite.db 密钥未通过 HMAC 校验。")
    return enc_key.hex()


def load_passphrase(path: pathlib.Path = DEFAULT_PASSPHRASE_FILE) -> str | None:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        passphrase = data.get("passphrase")
        return passphrase if isinstance(passphrase, str) else None
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_passphrase(passphrase_hex: str, path: pathlib.Path = DEFAULT_PASSPHRASE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump({"passphrase": passphrase_hex}, f, indent=2)
    os.chmod(path, 0o600)


def check_capture_prerequisites() -> list[str]:
    issues: list[str] = []
    if not shutil.which("lldb"):
        issues.append("未检测到 lldb，请先运行 xcode-select --install")

    sip_status = run_text(["csrutil", "status"])
    if "enabled" in sip_status.lower():
        issues.append("SIP 仍为 enabled，请先进恢复模式执行 csrutil disable 后重启")

    sign_info = run_text(["codesign", "-dv", "/Applications/WeChat.app"])
    if "flags=0x10000(runtime)" in sign_info:
        issues.append("微信仍带 Hardened Runtime，请先运行 scripts/resign_wechat.sh")

    return issues


def find_wechat_pid() -> int | None:
    result = subprocess.run(["pgrep", "-x", "WeChat"], capture_output=True, text=True, check=False)
    pids = [int(item) for item in result.stdout.split() if item.isdigit()]
    return pids[0] if pids else None


def parse_passphrases_from_lldb(output: str) -> list[str]:
    bytes_out: list[str] = []
    for line in output.splitlines():
        match = re.match(r"\s*0x[0-9a-f]+:\s+((?:0x[0-9a-f]{2}\s*)+)$", line)
        if match:
            bytes_out.extend(re.findall(r"0x([0-9a-f]{2})", match.group(1)))

    candidates: list[str] = []
    for offset in range(0, len(bytes_out), 32):
        chunk = bytes_out[offset : offset + 32]
        if len(chunk) != 32:
            continue
        candidate = "".join(chunk)
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


def parse_passphrase_from_lldb(output: str) -> str | None:
    candidates = parse_passphrases_from_lldb(output)
    return candidates[0] if candidates else None


def cleanup_lldb() -> None:
    for command in (["pkill", "-9", "-x", "lldb"], ["pkill", "-9", "-f", "LLDB.framework.*debugserver"]):
        subprocess.run(command, capture_output=True, timeout=5, check=False)


def capture_valid_passphrase(favorite_db: FavoriteDb, timeout: int) -> str:
    issues = check_capture_prerequisites()
    if issues:
        raise RuntimeError("当前环境不能抓取 passphrase:\n" + "\n".join(f"- {item}" for item in issues))

    pid = find_wechat_pid()
    if not pid:
        raise RuntimeError("未找到微信主进程，请先启动微信。")

    is_arm = platform.machine() in ("arm64", "aarch64")
    pw_reg, len_reg = ("x1", "x2") if is_arm else ("rsi", "rdx")
    lldb_script = (
        "settings set target.preload-symbols false\n"
        f"process attach -p {pid}\n"
        f"breakpoint set -n CCKeyDerivationPBKDF -c '${len_reg} == 32'\n"
        "breakpoint command add 1\n"
        f"memory read --size 1 --count 32 --format x ${pw_reg}\n"
        "process continue\n"
        "DONE\n"
        "process continue\n"
    )

    fd, script_path = tempfile.mkstemp(prefix="wechat_fav_", suffix=".lldb")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(lldb_script)

    log("LLDB 已准备。请在微信里退出登录，然后重新扫码登录以触发密钥派生。")
    log(f"等待时间: {timeout} 秒")

    proc = subprocess.Popen(
        ["bash", "-c", f"( cat {script_path}; sleep {timeout} ) | TERM=dumb lldb"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    buf = ""
    checked: set[str] = set()
    valid_passphrase: str | None = None
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            if proc.stdout is None:
                break
            readable, _, _ = select.select([proc.stdout], [], [], 3.0)
            if not readable:
                continue
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue
            buf += line
            for candidate in parse_passphrases_from_lldb(buf):
                if candidate in checked:
                    continue
                checked.add(candidate)
                try:
                    derive_favorite_key(candidate, favorite_db)
                except RuntimeError:
                    log(f"捕获到候选 passphrase，但未通过 favorite.db 校验，继续等待。候选摘要: {candidate[:8]}...{candidate[-8:]}")
                    continue
                valid_passphrase = candidate
                break
            if valid_passphrase:
                break
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        pathlib.Path(script_path).unlink(missing_ok=True)
        cleanup_lldb()

    if not checked:
        raise RuntimeError("未捕获到 passphrase。请确认期间完成了退出登录并重新登录。")
    if not valid_passphrase:
        raise RuntimeError(
            f"捕获到 {len(checked)} 个候选 passphrase，但没有任何一个能解开 favorite.db。"
            "请重新运行并确保重新登录后手动打开一次微信「收藏」。"
        )
    return valid_passphrase


def decrypt_page(enc_key: bytes, page: bytes, page_number: int) -> bytes:
    iv = page[PAGE_SIZE - RESERVE_SIZE : PAGE_SIZE - RESERVE_SIZE + IV_SIZE]
    if page_number == 1:
        encrypted = page[SALT_SIZE : PAGE_SIZE - RESERVE_SIZE]
        decrypted = aes_cbc_decrypt(enc_key, iv, encrypted)
        return SQLITE_HEADER + decrypted + (b"\x00" * RESERVE_SIZE)

    encrypted = page[: PAGE_SIZE - RESERVE_SIZE]
    decrypted = aes_cbc_decrypt(enc_key, iv, encrypted)
    return decrypted + (b"\x00" * RESERVE_SIZE)


def decrypt_database(db_path: pathlib.Path, enc_key_hex: str, out_path: pathlib.Path) -> None:
    enc_key = bytes.fromhex(enc_key_hex)
    page1 = read_page1(db_path)
    if not verify_key(enc_key, page1):
        raise RuntimeError("favorite.db 密钥校验失败，拒绝解密。")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    page_count = (db_path.stat().st_size + PAGE_SIZE - 1) // PAGE_SIZE
    with db_path.open("rb") as fin, out_path.open("wb") as fout:
        for page_number in range(1, page_count + 1):
            page = fin.read(PAGE_SIZE)
            if not page:
                break
            if len(page) < PAGE_SIZE:
                page += b"\x00" * (PAGE_SIZE - len(page))
            fout.write(decrypt_page(enc_key, page, page_number))


def table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("select name from sqlite_master where type='table'").fetchall()
    return {row[0] for row in rows}


def unix_time(value: Any) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return ""
    if number <= 0:
        return ""
    if number > 10_000_000_000:
        number = number // 1000
    return datetime.fromtimestamp(number).isoformat(timespec="seconds")


def decode_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        for encoding in ("utf-8", "utf-16", "latin1"):
            try:
                return value.decode(encoding, errors="ignore")
            except UnicodeDecodeError:
                continue
        return ""
    return str(value)


def first_match(text: str, names: list[str]) -> str:
    for name in names:
        patterns = [
            rf"<{name}[^>]*>(.*?)</{name}>",
            rf'{name}="([^"]+)"',
            rf"{name}='([^']+)'",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
            if match:
                return html.unescape(match.group(1).strip())
    return ""


def parse_xml_fields(text: str) -> dict[str, str]:
    fields = {
        "title": first_match(text, ["title", "datatitle"]),
        "url": first_match(text, ["url", "dataurl", "weburl", "link"]),
        "source": first_match(text, ["sourcename", "sourceusername", "publisher", "author"]),
        "desc": first_match(text, ["desc", "digest", "datadesc"]),
        "thumb": first_match(text, ["thumburl", "cdnthumburl"]),
    }

    # 微信收藏 XML 经常缺声明或外层不统一，先用正则兜底，再尝试 ElementTree。
    if fields["title"] and fields["url"]:
        return fields

    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return fields

    lowered: dict[str, str] = {}
    for elem in root.iter():
        key = elem.tag.lower().split("}")[-1]
        value = (elem.text or "").strip()
        if value:
            lowered.setdefault(key, html.unescape(value))
        for attr_key, attr_value in elem.attrib.items():
            lowered.setdefault(attr_key.lower(), html.unescape(attr_value))

    fields["title"] = fields["title"] or lowered.get("title", "") or lowered.get("datatitle", "")
    fields["url"] = fields["url"] or lowered.get("url", "") or lowered.get("dataurl", "") or lowered.get("weburl", "")
    fields["source"] = fields["source"] or lowered.get("sourcename", "") or lowered.get("sourceusername", "")
    fields["desc"] = fields["desc"] or lowered.get("desc", "") or lowered.get("digest", "")
    fields["thumb"] = fields["thumb"] or lowered.get("thumburl", "") or lowered.get("cdnthumburl", "")
    return fields


def parse_fav_db_item(conn: sqlite3.Connection) -> list[dict[str, str]]:
    cursor = conn.execute("select * from fav_db_item")
    columns = [item[0] for item in cursor.description]
    rows: list[dict[str, str]] = []

    for raw in cursor.fetchall():
        item = dict(zip(columns, raw))
        content = ""
        for key, value in item.items():
            text = decode_value(value)
            if "<" in text and ">" in text and ("fav" in text.lower() or "record" in text.lower() or "url" in text.lower()):
                content = text
                break
        fields = parse_xml_fields(content)
        rows.append(
            {
                "id": decode_value(item.get("id") or item.get("local_id") or item.get("localId") or item.get("fav_local_id")),
                "type": decode_value(item.get("type") or item.get("fav_type") or item.get("item_type")),
                "time": unix_time(item.get("update_time") or item.get("updateTime") or item.get("time") or item.get("create_time")),
                "title": fields["title"],
                "url": fields["url"],
                "source": fields["source"],
                "desc": fields["desc"],
                "thumb": fields["thumb"],
                "raw_content": content,
            }
        )
    return rows


def parse_legacy_tables(conn: sqlite3.Connection) -> list[dict[str, str]]:
    cursor = conn.execute("select * from FavItems")
    columns = [item[0] for item in cursor.description]
    rows: list[dict[str, str]] = []
    for raw in cursor.fetchall():
        item = dict(zip(columns, raw))
        joined = "\n".join(decode_value(value) for value in item.values())
        fields = parse_xml_fields(joined)
        rows.append(
            {
                "id": decode_value(item.get("localId") or item.get("favLocalId") or item.get("id")),
                "type": decode_value(item.get("type") or item.get("sourceType")),
                "time": unix_time(item.get("updateTime") or item.get("createTime")),
                "title": fields["title"],
                "url": fields["url"],
                "source": fields["source"],
                "desc": fields["desc"],
                "thumb": fields["thumb"],
                "raw_content": joined,
            }
        )
    return rows


def parse_decrypted_favorites(db_path: pathlib.Path) -> list[dict[str, str]]:
    conn = sqlite3.connect(str(db_path))
    try:
        names = table_names(conn)
        if "fav_db_item" in names:
            return parse_fav_db_item(conn)
        if "FavItems" in names:
            return parse_legacy_tables(conn)
        raise RuntimeError(f"未识别收藏表结构。当前表: {', '.join(sorted(names))}")
    finally:
        conn.close()


def write_exports(items: list[dict[str, str]], out_dir: pathlib.Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "favorites.json"
    csv_path = out_dir / "favorites.csv"
    md_path = out_dir / "favorites.md"

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)

    columns = ["id", "type", "time", "title", "url", "source", "desc", "thumb"]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for item in items:
            writer.writerow({key: item.get(key, "") for key in columns})

    with md_path.open("w", encoding="utf-8") as f:
        f.write("# 微信收藏导出\n\n")
        f.write(f"- 总数: {len(items)}\n")
        f.write(f"- 生成时间: {datetime.now().isoformat(timespec='seconds')}\n\n")
        for index, item in enumerate(items, 1):
            title = item.get("title") or item.get("url") or f"收藏 {index}"
            f.write(f"## {index}. {title}\n\n")
            if item.get("url"):
                f.write(f"- 链接: {item['url']}\n")
            if item.get("source"):
                f.write(f"- 来源: {item['source']}\n")
            if item.get("time"):
                f.write(f"- 时间: {item['time']}\n")
            if item.get("desc"):
                f.write(f"\n{item['desc']}\n")
            f.write("\n")

    log(f"已导出: {json_path}")
    log(f"已导出: {csv_path}")
    log(f"已导出: {md_path}")


def write_key_file(path: pathlib.Path, favorite_db: FavoriteDb, enc_key_hex: str) -> None:
    data = {
        "_db_dir": str(favorite_db.db_storage),
        "favorite/favorite.db": {
            "enc_key": enc_key_hex,
            "salt": favorite_db.salt_hex,
            "size_mb": round(favorite_db.size / 1024 / 1024, 2),
        },
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.chmod(path, 0o600)


def command_inspect(args: argparse.Namespace) -> None:
    db = choose_favorite_db(args.db, args.account)
    log(f"账号: {db.account}")
    log(f"db_storage: {db.db_storage}")
    log(f"favorite.db: {db.db_path}")
    log(f"大小: {db.size / 1024 / 1024:.2f} MB")
    log(f"salt: {db.salt_hex}")
    log(f"SIP: {run_text(['csrutil', 'status']).strip()}")
    log(f"LLDB: {run_text(['lldb', '--version']).splitlines()[0] if shutil.which('lldb') else '未安装'}")


def command_capture(args: argparse.Namespace) -> None:
    db = choose_favorite_db(args.db, args.account)
    out_dir = pathlib.Path(args.output).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    keys_path = out_dir / "favorite_key.json"

    passphrase = None if args.refresh else load_passphrase()
    enc_key_hex: str | None = None
    if passphrase:
        log("使用已缓存 passphrase 尝试派生 favorite.db 密钥。")
        try:
            enc_key_hex = derive_favorite_key(passphrase, db)
        except RuntimeError:
            log("已缓存 passphrase 未通过 favorite.db 校验，将重新捕获。")
            passphrase = None

    if not passphrase:
        passphrase = capture_valid_passphrase(db, args.timeout)
        enc_key_hex = derive_favorite_key(passphrase, db)
        save_passphrase(passphrase)
        log("passphrase 已通过 favorite.db 校验，并缓存到 ~/.wcdb-key-tool/wechat-passphrase.json（权限 600）。")

    if enc_key_hex is None:
        enc_key_hex = derive_favorite_key(passphrase, db)
    write_key_file(keys_path, db, enc_key_hex)
    log(f"favorite.db 密钥已保存: {keys_path}")
    log(f"密钥摘要: {enc_key_hex[:8]}...{enc_key_hex[-8:]}")


def load_favorite_key(path: pathlib.Path) -> str:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    value = data.get("favorite/favorite.db", {}).get("enc_key")
    if not isinstance(value, str):
        raise RuntimeError(f"密钥文件格式不正确: {path}")
    return value


def command_decrypt(args: argparse.Namespace) -> pathlib.Path:
    db = choose_favorite_db(args.db, args.account)
    out_dir = pathlib.Path(args.output).expanduser()
    keys_path = pathlib.Path(args.keys).expanduser() if args.keys else out_dir / "favorite_key.json"
    decrypted_path = out_dir / "decrypted" / "favorite_decrypted.db"
    enc_key_hex = load_favorite_key(keys_path)
    decrypt_database(db.db_path, enc_key_hex, decrypted_path)
    log(f"已解密: {decrypted_path}")
    return decrypted_path


def command_export(args: argparse.Namespace) -> None:
    out_dir = pathlib.Path(args.output).expanduser()
    decrypted_path = pathlib.Path(args.decrypted).expanduser() if args.decrypted else out_dir / "decrypted" / "favorite_decrypted.db"
    if not decrypted_path.is_file():
        raise RuntimeError(f"解密后的数据库不存在: {decrypted_path}")
    items = parse_decrypted_favorites(decrypted_path)
    write_exports(items, out_dir)
    with_url = sum(1 for item in items if item.get("url"))
    log(f"收藏总数: {len(items)}，带 URL: {with_url}")


def command_all(args: argparse.Namespace) -> None:
    command_capture(args)
    decrypted = command_decrypt(args)
    args.decrypted = str(decrypted)
    command_export(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="macOS 微信收藏批量导出工具")
    parser.add_argument("--account", help="账号目录名，形如 SomeName_1a2b；只有一个账号时可省略")
    parser.add_argument("--db", help="favorite.db 的显式路径")
    parser.add_argument("--output", default="output", help="输出目录，默认 output")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("inspect", help="只检查本机 favorite.db 和环境")

    capture = sub.add_parser("capture", help="抓取/复用 passphrase 并生成 favorite_key.json")
    capture.add_argument("--timeout", type=int, default=180, help="等待重新登录触发断点的秒数")
    capture.add_argument("--refresh", action="store_true", help="忽略已缓存 passphrase，重新抓取")

    decrypt = sub.add_parser("decrypt", help="用 favorite_key.json 解密 favorite.db")
    decrypt.add_argument("--keys", help="密钥文件路径，默认 output/favorite_key.json")

    export = sub.add_parser("export", help="把解密后的 favorite.db 导出 JSON/CSV/Markdown")
    export.add_argument("--decrypted", help="解密后的 favorite_decrypted.db 路径")

    all_cmd = sub.add_parser("all", help="抓密钥、解密、导出一口气执行")
    all_cmd.add_argument("--timeout", type=int, default=180, help="等待重新登录触发断点的秒数")
    all_cmd.add_argument("--refresh", action="store_true", help="忽略已缓存 passphrase，重新抓取")
    all_cmd.add_argument("--keys", help=argparse.SUPPRESS)
    all_cmd.add_argument("--decrypted", help=argparse.SUPPRESS)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "inspect":
            command_inspect(args)
        elif args.command == "capture":
            command_capture(args)
        elif args.command == "decrypt":
            command_decrypt(args)
        elif args.command == "export":
            command_export(args)
        elif args.command == "all":
            command_all(args)
        else:
            parser.error("未知命令")
        return 0
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
