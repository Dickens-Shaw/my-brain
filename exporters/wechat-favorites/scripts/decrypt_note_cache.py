#!/usr/bin/env python3
"""解密「转成笔记」时落下的语音缓存，并转成 mp3。

微信 Mac 4.x 把笔记语音写成
`business/favorite/temp/NoteCache/temp_<时间戳>.silk`，内容是 AES-128-CBC + PKCS7，
不是明文 SILK。key 和 iv 是同一串 16 字节 ASCII：

    MD5(f"{kvcomm_code}{wxid}").hexdigest()[:16]

`kvcomm_code` 来自 `app_data/net/kvcomm/key_<code>_*.statistic`（跳过 0）。
`wxid` 是账号目录名去掉末尾的 `_` 后缀，例如 `JustinTimberlakeShaw_572f`
对应 `JustinTimberlakeShaw`。这和本地图片 V2 缓存用的是同一把 key，只是模式换成 CBC。

用法：
    python3 scripts/decrypt_note_cache.py
    python3 scripts/decrypt_note_cache.py --cache output/note-cache --out output/audio
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import re
import sys
import wave
from ctypes import c_int, c_size_t, c_uint32, c_void_p
from datetime import datetime
from pathlib import Path

XWECHAT = Path(
    "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
).expanduser()
KVCOMM = Path(
    "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/app_data/net/kvcomm"
).expanduser()
SKIP_ACCOUNTS = {"all_users", "global_config", "Backup", "WMPF", "old_backup"}
SILK_MAGIC = b"\x02#!SILK_V3"
RATE = 24000

_lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
_lib.CCCryptorCreate.argtypes = [
    c_uint32, c_uint32, c_uint32, c_void_p, c_size_t, c_void_p, ctypes.POINTER(c_void_p)
]
_lib.CCCryptorCreate.restype = c_int
_lib.CCCryptorUpdate.argtypes = [
    c_void_p, c_void_p, c_size_t, c_void_p, c_size_t, ctypes.POINTER(c_size_t)
]
_lib.CCCryptorUpdate.restype = c_int
_lib.CCCryptorRelease.argtypes = [c_void_p]
_lib.CCCryptorRelease.restype = c_int


def use_local_venv() -> None:
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    site = Path(__file__).resolve().parent.parent / ".venv" / "lib" / version / "site-packages"
    if site.is_dir() and str(site) not in sys.path:
        sys.path.append(str(site))


def aes_ecb_decrypt(key: bytes, data: bytes) -> bytes:
    cryptor = c_void_p()
    status = _lib.CCCryptorCreate(1, 0, 2, key, len(key), None, ctypes.byref(cryptor))
    if status:
        raise RuntimeError(f"CCCryptorCreate 失败: {status}")
    out = ctypes.create_string_buffer(len(data) + 16)
    moved = c_size_t()
    status = _lib.CCCryptorUpdate(cryptor, data, len(data), out, len(out), ctypes.byref(moved))
    _lib.CCCryptorRelease(cryptor)
    if status:
        raise RuntimeError(f"CCCryptorUpdate 失败: {status}")
    return out.raw[: moved.value]


def cbc_pkcs7_decrypt(data: bytes, key: bytes) -> bytes:
    if len(data) == 0 or len(data) % 16:
        raise ValueError(f"密文长度不是 16 的倍数: {len(data)}")
    ecb = aes_ecb_decrypt(key, data)
    plain = bytearray()
    prev = key
    for offset in range(0, len(ecb), 16):
        block = ecb[offset : offset + 16]
        plain.extend(byte ^ prev[i] for i, byte in enumerate(block))
        prev = data[offset : offset + 16]
    pad = plain[-1]
    if not 1 <= pad <= 16 or plain[-pad:] != bytes([pad]) * pad:
        raise ValueError("PKCS7 填充不合法")
    return bytes(plain[:-pad])


def account_dirs() -> list[Path]:
    if not XWECHAT.is_dir():
        return []
    return [
        path
        for path in XWECHAT.iterdir()
        if path.is_dir() and path.name not in SKIP_ACCOUNTS
    ]


def wxid_candidates(account: str | None) -> list[str]:
    names = [account] if account else [path.name for path in account_dirs()]
    found: list[str] = []
    for name in names:
        found.append(name)
        stem, _, suffix = name.rpartition("_")
        if stem and re.fullmatch(r"[0-9a-fA-F]{4,}", suffix):
            found.append(stem)
    # 去重并保持顺序
    return list(dict.fromkeys(found))


def kvcomm_codes() -> list[str]:
    codes: list[str] = []
    if not KVCOMM.is_dir():
        return codes
    for path in KVCOMM.glob("key_*_*.statistic"):
        match = re.match(r"key_(\d+)_", path.name)
        if match and match.group(1) != "0":
            codes.append(match.group(1))
    return list(dict.fromkeys(codes))


def derive_key(code: str, wxid: str) -> bytes:
    return hashlib.md5(f"{code}{wxid}".encode()).hexdigest()[:16].encode("ascii")


def find_key(samples: list[bytes], account: str | None) -> tuple[bytes, str, str]:
    codes = kvcomm_codes()
    wxids = wxid_candidates(account)
    if not codes or not wxids:
        raise SystemExit("找不到 kvcomm code 或账号目录，用 --code 和 --wxid 指定")
    for code in codes:
        for wxid in wxids:
            key = derive_key(code, wxid)
            try:
                plains = [cbc_pkcs7_decrypt(blob, key) for blob in samples]
            except ValueError:
                continue
            if all(plain.startswith(SILK_MAGIC) for plain in plains):
                return key, code, wxid
    raise SystemExit("现有 code/wxid 组合解不开这些缓存。确认账号目录和 kvcomm 是否还在。")


def load_index(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def safe_filename(value: str) -> str:
    value = re.sub(r'[/\\:*?"<>|\r\n\t]', "-", value)
    value = re.sub(r"\s+", " ", value).strip(" .-")
    return value or "unknown"


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / handle.getframerate()


def wav_to_mp3(wav_path: Path, mp3_path: Path, bitrate: int = 64) -> None:
    """把 16-bit PCM wav 编成 mp3。语音用 64 kbps，和 extract_favorite_audio 一致。"""
    import lameenc

    with wave.open(str(wav_path), "rb") as handle:
        channels = handle.getnchannels()
        rate = handle.getframerate()
        width = handle.getsampwidth()
        pcm = handle.readframes(handle.getnframes())
    if width != 2:
        raise RuntimeError(f"{wav_path.name} 不是 16-bit PCM")
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(bitrate)
    encoder.set_in_sample_rate(rate)
    encoder.set_channels(channels)
    encoder.set_quality(2)
    mp3_path.write_bytes(encoder.encode(pcm) + encoder.flush())


def write_index(out_dir: Path, records: list[dict]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "audio-index.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    extracted = [item for item in records if item.get("status") == "ok"]
    missing = [item for item in records if item.get("status") != "ok"]
    total_s = sum(float(item.get("duration_s") or 0) for item in records)
    lines = [
        "# 微信收藏音频清单",
        "",
        f"- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 音频条目: {len(records)} 段，合计 {total_s:.1f} 秒",
        f"- 成功提取: {len(extracted)} 段",
        f"- 未能提取: {len(missing)} 段",
        f"- 采样率: {RATE} Hz",
        "- 来源: 笔记语音缓存，AES-128-CBC 解密后转 mp3",
        "",
    ]
    if extracted:
        lines += [
            "## 已提取",
            "",
            "| 收藏时间 | 发送人 | 时长 | 文件 |",
            "| --- | --- | --- | --- |",
        ]
        for item in extracted:
            when = (item.get("fav_time") or "")[:19]
            lines.append(
                f"| {when} | {item.get('sender') or ''} | {float(item['duration_s']):.1f}s | "
                f"`{item.get('path') or ''}` |"
            )
        lines.append("")
    if missing:
        lines += [
            "## 未能提取",
            "",
            "| 收藏时间 | 发送人 | 时长 | 原因 |",
            "| --- | --- | --- | --- |",
        ]
        for item in missing:
            when = (item.get("fav_time") or "")[:19]
            lines.append(
                f"| {when} | {item.get('sender') or ''} | {float(item.get('duration_s') or 0):.1f}s | "
                f"{item.get('error') or ''} |"
            )
        lines.append("")
    (out_dir / "audio-index.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, help="加密 silk 所在目录，默认 output/note-cache")
    parser.add_argument("--out", type=Path, help="wav 输出目录，默认 output/audio")
    parser.add_argument("--account", help="账号目录名，默认自动探测")
    parser.add_argument("--code", help="kvcomm code，默认从 key_*.statistic 读取")
    parser.add_argument("--wxid", help="不含目录后缀的 wxid")
    args = parser.parse_args()

    base = Path(__file__).resolve().parent.parent
    cache_dir = args.cache or (base / "output" / "note-cache")
    out_dir = args.out or (base / "output" / "audio")
    files = sorted(cache_dir.glob("*.silk"))
    if not files:
        raise SystemExit(f"{cache_dir} 里没有 .silk")

    blobs = [(path, path.read_bytes()) for path in files]
    if args.code and args.wxid:
        key = derive_key(args.code, args.wxid)
        code, wxid = args.code, args.wxid
        plains = [cbc_pkcs7_decrypt(blob, key) for _, blob in blobs]
        if not all(plain.startswith(SILK_MAGIC) for plain in plains):
            raise SystemExit("指定的 code/wxid 解出来不是 SILK")
    else:
        key, code, wxid = find_key([blob for _, blob in blobs], args.account)
        plains = [cbc_pkcs7_decrypt(blob, key) for _, blob in blobs]

    print(f"key 来自 MD5({code}{wxid}) 前 16 位 ASCII，{len(files)} 段全部解出 SILK")

    use_local_venv()
    try:
        import pilk
    except ImportError as exc:
        raise SystemExit(
            "未装 pilk。在 exporters/wechat-favorites 执行 "
            "python3 -m venv .venv && .venv/bin/pip install pilk"
        ) from exc

    index_path = out_dir / "audio-index.json"
    records = load_index(index_path)
    by_md5: dict[str, list[dict]] = {}
    for item in records:
        by_md5.setdefault(item.get("fullmd5") or "", []).append(item)

    written: dict[str, str] = {}
    extras = 0
    for (src, _blob), plain in zip(blobs, plains):
        digest = hashlib.md5(plain).hexdigest()
        matched = by_md5.get(digest) or []
        if matched:
            item = matched[0]
            sender = safe_filename(item.get("sender") or "unknown")
            when = (item.get("fav_time") or "unknown")[:10]
            year = when[:4] if len(when) >= 4 else "unknown"
            duration = float(item["duration_s"])
            filename = f"{when}-{sender}-{duration:.1f}s-fav{item['fav_id']}.mp3"
            dest_dir = out_dir / year
        else:
            extras += 1
            dest_dir = out_dir / "notes"
            filename = f"note-extra-{extras:02d}.mp3"
            item = None
            duration = 0.0
        dest_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(filename).stem
        silk_path = dest_dir / f"{stem}.silk"
        wav_path = dest_dir / f"{stem}.wav"
        mp3_path = dest_dir / f"{stem}.mp3"
        silk_path.write_bytes(plain)
        pilk.silk_to_wav(str(silk_path), str(wav_path), rate=RATE)
        actual = round(wav_duration(wav_path), 1)
        if item is None:
            mp3_path = dest_dir / f"note-extra-{extras:02d}-{actual:.1f}s.mp3"
        wav_to_mp3(wav_path, mp3_path)
        wav_path.unlink()
        silk_path.unlink()
        rel = mp3_path.relative_to(out_dir).as_posix()
        if item is None:
            records.append(
                {
                    "fav_id": "",
                    "fav_time": "",
                    "sender": "",
                    "duration_s": actual,
                    "fullmd5": digest,
                    "fullsize": len(plain),
                    "status": "ok",
                    "path": rel,
                    "source": src.name,
                    "error": "",
                }
            )
        else:
            for record in matched:
                record["status"] = "ok"
                record["path"] = rel
                record["error"] = ""
                written[record.get("fav_id") or rel] = rel
        print(f"{actual:>5.1f}s  {rel}  <= {src.name}")

    write_index(out_dir, records)
    ok = sum(1 for item in records if item.get("status") == "ok")
    print(f"完成：{ok}/{len(records)} 段 mp3 在 {out_dir}")


if __name__ == "__main__":
    main()
