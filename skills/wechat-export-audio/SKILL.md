---
name: wechat-export-audio
description: 在 macOS 微信客户端里，把收藏语音导出成可播放的 mp3。Use when the user wants to export WeChat favorite voice messages, 收藏语音, 语音条, silk, NoteCache, or decrypt_note_cache.py from exporters/wechat-favorites.
---

# 导出微信收藏语音

工作目录固定为 `exporters/wechat-favorites`。用简体中文引导用户。先看本机已经有什么，再决定要不要让用户操作微信。

脚本不在这个 skill 目录里，而在仓库的 `exporters/wechat-favorites/scripts/`。clone 本仓库就有脚本，不要再复制一份进 skill。对方启用 skill 时在仓库根目录运行 `bash scripts/install-skills.sh`。两个微信 skill 共用这一套脚本和 `output/`，不要拆开各放一份。

收藏库只有 CDN 描述符，没有音频本体。在微信里播放不会落盘。能离线拿到文件的只有两条路：本机消息库里还留着的近期语音，以及「转成笔记」时短暂写下的加密 silk。加密 silk 的 key 按账号现算，不要去扫内存或断 AES。

## 进度清单

```
- [ ] 1. 确认收藏库已解密
- [ ] 2. 扫描收藏里有哪些语音
- [ ] 3. 能从消息库命中的先导出
- [ ] 4. 缺失的改走笔记缓存
- [ ] 5. 解密并转成 mp3，对照清单验收
```

## 1. 确认收藏库

`output/decrypted/favorite_decrypted.db` 已在就进入第 2 步。

不在时先导出收藏。微信保持登录。多账号加 `--account SomeName_1a2b`，或写入仓库根 `.env` 的 `WECHAT_ACCOUNT`。

```bash
cd exporters/wechat-favorites
python3 scripts/extract_favorites.py inspect
```

- 已有 `~/.wcdb-key-tool/wechat-passphrase.json`：不要关 SIP，不要重新登录。若文件属主是 root：`sudo chown "$(whoami)" ~/.wcdb-key-tool/wechat-passphrase.json`。然后 `capture`、`decrypt`、`export`。`capture` 校验失败才算缓存失效。
- 没有可用 passphrase：停下来，让用户自己做。代理不要关 SIP，也不要替用户输入确认词。
  1. 恢复模式执行 `csrutil disable` 并重启。
  2. `bash scripts/resign_wechat.sh`，确认词 `RESIGN_WECHAT`。
  3. 微信登录后运行 `bash run.sh`。等待开始后，用户退出登录再扫码登录。

完成后可以建议用户 `csrutil enable`。`output/` 含隐私，不要提交。

## 2. 扫描

```bash
python3 scripts/extract_favorite_audio.py scan
```

读 `output/audio/audio-index.md`。记下条数、时长、发送人。这条命令不碰消息库。

## 3. 消息库里还在的语音

```bash
python3 scripts/extract_favorite_audio.py export --format mp3
```

消息库 `media_*.db` 的 `VoiceInfo.voice_data` 是明文 SILK（`\x02#!SILK_V3`）。脚本先按 msgid，再按 `fullmd5` 匹配。Mac 微信不回溯历史，重新登录过的机器往往只有登录日之后的语音；原消息被删的也没有。

这条命令会解密 `media_*.db`，必要时还会碰到含完整聊天记录的 `message_*.db`。默认解密完就删。用户没明确要求时不要加 `--keep-cache`。

`export` 的 mp3 依赖 ffmpeg。没有 ffmpeg 时它会留下 wav 或 silk。笔记缓存这条路不依赖 ffmpeg，最终格式以第 5 步为准。

命中的保留。清单里仍是缺失的，进入第 4 步。全部命中就跳到第 5 步验收，不要让用户转笔记。

## 4. 缺失的语音：转成笔记并抓住缓存

已经是笔记，不等于还要再转一次。先看备份：

```bash
ls output/note-cache/*.silk
```

有 `temp_*.silk` 就直接第 5 步。微信自己的 `NoteCache/` 转完会被清空，这是正常的。

备份也没有时，先启动监视，再让用户操作。缓存文件几秒内会被删掉，监视必须先跑：

```bash
python3 scripts/watch_note_cache.py 600
```

默认盯 `business/favorite/` 和 `cache/`，新文件复制到 `output/note-cache/`。多账号加 `--account`。

然后告诉用户，在这段监视时间内：

1. 打开微信收藏里缺失的那几条语音。
2. 每条使用「转成笔记」，不要只播放。播放只在内存里解码。
3. 转完打开该笔记，让语音条渲染出来。
4. 监视输出里出现对应的 `temp_*.silk` 再停下。开头不是 `#!SILK` 是预期的，那是密文。

已经在笔记里、但 `output/note-cache/` 没有副本的，同样要在监视开启后再转一次。同一段重转会得到同一份密文。

## 5. 解密并转 mp3

依赖装在本目录 venv。`python` / `python3` 软链接可能指到已卸载的 Homebrew Python，用 `python3.11`：

```bash
python3 -m venv .venv
.venv/bin/python3.11 -m pip install pilk lameenc
.venv/bin/python3.11 scripts/decrypt_note_cache.py
```

脚本会自己找 kvcomm code 和 wxid：

- code：`~/Library/Containers/com.tencent.xinWeChat/Data/Documents/app_data/net/kvcomm/key_<code>_*.statistic`，跳过 `0`
- wxid：账号目录名去掉末尾 `_` 加十六进制后缀，如 `JustinTimberlakeShaw_572f` → `JustinTimberlakeShaw`
- key 和 iv 都是 `MD5(f"{code}{wxid}").hexdigest()[:16]` 的 16 个 ASCII 字符，不要 unhex
- AES-128-CBC + PKCS7，再经 pilk 以 24000 Hz 解码，lameenc 编成 64 kbps 单声道 mp3

自动探测失败时才加 `--code` 和 `--wxid`。验收标准是明文以 `\x02#!SILK_V3` 开头，并且 md5 对得上收藏里的 `fullmd5`。

产物：

```
output/audio/
├── 2024/
│   └── 2024-02-04-发送人-36.2s-fav1651.mp3
├── notes/                  # 笔记里多出来、对不上收藏条目的语音
├── audio-index.md
└── audio-index.json
```

同一 `fullmd5` 只写一个 mp3，清单里两条都列。脚本转完会删掉旁边的 wav 和明文 silk。加密原件仍在 `output/note-cache/`。

用 `afinfo` 抽查最长和最短的两首：单声道、24000 Hz、约 64000 bps，时长和文件名接近。然后把 `audio-index.md` 的路径和缺失原因告诉用户。

## 不要做的事

- 不要把「在微信里播放」当成导出。播放不写文件。
- 不要为了这把 key 去 dump 微信内存、断 `CCCrypt`，或关着 SIP 挂调试器。key 是上面的 MD5。
- 不要提交 `output/`、passphrase、解密后的消息库。
- 消息库一条都匹配不上时，不要反复 `export`。改走第 4 步。
