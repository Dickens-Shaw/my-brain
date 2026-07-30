# 微信收藏音频导出

把收藏里的语音（音频）条目导出成清单，并尽可能还原成可播放文件。
脚本：`scripts/extract_favorite_audio.py`。

**先说结论：本机这份数据里，收藏语音一段都还原不出来。** 收藏库只存 CDN 描述符，
本机消息库的语音缓存又不覆盖这些收藏的时间；在微信里直接播放也不落盘（实测过）。
细节见下面「为什么可能拿不到音频」。脚本仍然会生成完整清单，
说明每一段的时间、发送人、时长和缺失原因。

**但有一个新突破口：把收藏语音「转成笔记」后，微信会在本地落一份加密的 silk 缓存。**
格式已经完全摸清（AES 分组 + PKCS7，跨会话同一把固定 key），10 段密文都抓到了，
只差那把 key —— 而 key 用的是微信自带（静态编译）的 AES，不走系统库，暂时没取到。
详见下面「转笔记会落一份加密语音缓存」。

## 快速开始

```bash
cd exporters/wechat-favorites

python3 scripts/extract_favorite_audio.py scan     # 只列清单，不碰消息库
python3 scripts/extract_favorite_audio.py export   # 提取音频并转码
python3 scripts/extract_favorite_audio.py probe    # 排查消息库结构和匹配情况
```

前置条件：先跑过一次 `bash run.sh`（或 `extract_favorites.py capture` + `decrypt`），
因为本脚本要用解密后的 `output/decrypted/favorite_decrypted.db`，
以及缓存在 `~/.wcdb-key-tool/wechat-passphrase.json` 的 passphrase。

如果那个 passphrase 文件是 `root` 所有（`run.sh` 用 sudo 抓密钥会导致这个），
执行一次改成自己的，之后都不用 sudo：

```bash
sudo chown "$(whoami)" ~/.wcdb-key-tool/wechat-passphrase.json
```

## 音频数据在哪

收藏库 `favorite.db` 的 `fav_db_item.content` 里只有元数据和 CDN 描述符，
没有音频本体：

```xml
<dataitem datatype="3" dataid="3e6692c8fbcd4be66a0a00393f24f8f2">
  <datafmt>silk</datafmt>
  <cdn_dataurl>305f02010204583056...</cdn_dataurl>
  <cdn_datakey>a9d5fbc2048261e9bd7867e120eece1b</cdn_datakey>
  <duration>36245</duration>
  <fullmd5>d29c0f2f3b8da16cfb084a1c84e5695c</fullmd5>
  <fullsize>58319</fullsize>
</dataitem>
```

`sourcedatapath` 和 `ext_buf` 都是空的，`msg/attach/` 下面也只有 `Img` 没有 Voice。
真正能离线拿到的音频，是本机消息库里的那份副本：

```
db_storage/message/media_0.db  →  表 VoiceInfo
CREATE TABLE VoiceInfo(chat_name_id INTEGER, create_time INTEGER, local_id INTEGER,
                       svr_id INTEGER, voice_data BLOB, data_index TEXT DEFAULT '0')
```

`voice_data` 是 SILK v3，开头是 `\x02#!SILK_V3`（微信在标准 SILK 前多加一个 `\x02`）。

所以脚本的流程是：解析收藏 → 解密 `media_*.db` → 按 msgid 或内容 md5 对回收藏 →
写 `.silk` → 可选转码。

## 匹配策略

先按 msgid 匹配：收藏 XML 里的 `fromnewmsgid` / `datasourceid` 对应 `VoiceInfo.svr_id`。
这两者的编号体系是一致的（抽样 40 个 `VoiceInfo.svr_id`，在 `message_0.db` 的
`Msg_*.server_id` 里 40/40 命中）。

msgid 对不上时（转发、重新收藏等）再退到内容 md5：把 `VoiceInfo.voice_data` 的 md5
和收藏里的 `fullmd5` 对账，`\x02` 标记字节去掉和不去掉两种都算。

表结构没有写死。微信各版本改过表名列名，所以 `index_audio_blobs` 直接扫所有表的所有
BLOB 列，命中 SILK/AMR magic 就把同一行里所有像 msgid 的值都当索引键。

## 为什么可能拿不到音频

`media_0.db` 是滚动的，只保留近期语音。本机实测覆盖 `2026-01-20 ~ 2026-07-29`，
共 376 段；整个 `message_0.db` 的 71164 条消息也都在 2026-01-20 之后 ——
这台 Mac 是那天重新登录初始化的，而 Mac 微信不会回溯同步历史聊天记录。

于是 9 个收藏音频条目（去重后 8 段）全部落空：

| 情况 | 数量 | 说明 |
| --- | --- | --- |
| 收藏时间早于缓存区间 | 7 | 语音数据从来没在这台机器上落过盘 |
| 在区间内但仍缺失 | 2 | 原聊天消息后来被删了 |

两条都验证过：按 msgid 查 0/8，按内容 md5 绕开 msgid 再查也是 0/9。

剩下理论上还有音频的地方只有微信 CDN（`cdn_dataurl` + `cdn_datakey`）。

**在微信里播放不会留下任何本地文件。** 实测过：播放那段 36.2 秒的语音（确认听到完整声音，
说明微信成功从 CDN 取到了数据），播放前后对比 ——

- `VoiceInfo` 376 行没变，一段没加
- 账号目录 21673 个文件零新增，只有 `favorite.db` 和它的 WAL 改了 mtime、大小没变
- 整个 `com.tencent.xinWeChat` 容器加系统临时目录共 69216 个文件，SILK 文件数为 0
- 最近改动的 129 个文件全是内置 webview（`app_data/radium/`）的缓存

结论是音频从 CDN 取下来后只在内存里解码播放，全程不落盘。

（顺带记一下：`business/favorite/data/<xx>/<hash>` 下面确实有加密缓存，但那是图片和笔记
HTML，不是音频。以下格式分析对图片有效，对音频没用，留作参考。
从本机 12 个缓存文件反推，size 校验 12/12 通过：）

```
0..5    magic  07 08 56 32 08 07        // "\x07\x08V2\x08\x07"
6..9    uint32 aes_size                 // 走 AES 的明文字节数，上限 1024
10..13  uint32 raw_size                 // 其后的字节数
14      0x01                            // 版本/标志
15..    AES(PKCS7(前 aes_size 字节)) || 其余 raw_size 字节
```

超过 1024 字节的文件只有前 1024 字节走 AES，本来以为后面是明文、可以白捡大部分内容，
但实测那部分字节分布完全均匀（256/256 唯一值、JPEG 文件末尾也没有 `ffd9`），
同样是加密的。

密钥不在磁盘上：把 `config/`、`db_storage/MMKV/`、`all_users/` 共 44 个文件
871828 字节里所有 16 和 32 字节窗口都拿去试过（用已知是图片的缓存文件当校验 oracle），
AES-128 和 AES-256 各 37 万多个候选全部不中。要拿这个密钥只能扫 WeChat 进程内存。

## 转笔记会落一份加密语音缓存

直接播放不落盘，但把收藏语音**「转成笔记」**会不一样：微信为了在笔记里渲染语音条，
会把每段语音从 CDN 取下来、加密后写进本地缓存：

```
business/favorite/temp/NoteCache/temp_<时间戳>.silk
```

用一个高频文件监视器（`scripts/watch_note_cache.py`）在转笔记 + 播放的过程中盯着这个目录，
10 段语音的缓存文件全都抓到了（备份在 `output/note-cache/`）。

**这些文件是密文，不是明文 SILK。** 明文 SILK 开头应是 `02 23 21 53 49 4c 4b`（`\x02#!SILK`），
实际抓到的开头是 `d2 1b 71 c3 a3 17 3e 32 ...`。

格式判定为 **AES 分组模式（ECB 或 CBC）+ PKCS7 填充**，证据很硬：

- 每个文件大小 = 该段语音明文长度（笔记元数据里的 `fullsize`）向上取整到 16 的倍数，
  补的字节数 1~16 和 PKCS7 完全对得上，10/10 吻合。其中那段 14.7 秒的语音明文正好是
  16 的整数倍（21680 字节），于是补满整整一块 16 个 `0x10`（21696 字节）。
- 10 个文件里有 9 个密文首块完全相同（`d2 1b 71 c3 ...`）——同一把 key、
  且明文首块相同（都是同样的 SILK 头）；只有那段来自群聊的 18.2 秒语音首块不同。
  说明是**全局固定 key**（ECB，或 CBC 且 IV 固定）。
- **key 跨会话不变**：换个微信会话把同一段重新转笔记，生成的密文和上次**逐字节相同**
  （md5 一致）。所以 key 是按账号确定性派生的，不是每次随机。

有了「明文正好补满整块」的那个文件，就有了一个近乎零误报的爆破校验：它的末块解密后
必须是 16 个 `0x10`；而末块在 CBC 里是内部块、与 IV 无关，所以不需要知道 IV 就能验 key
（更强的判据是「10 个文件的末块解出来都得是合法 PKCS7」）。

### 取 key 的尝试与现状

| 手段 | 结果 |
| --- | --- |
| lldb 断点截获（播放时挂在 `CCCrypt` / `CCCryptorCreate` / `CCCryptorCreateWithMode` / `EVP_DecryptInit_ex` / `AES_set_decrypt_key`） | 只有 `CCCryptorCreate` 触发，抓到 5 个 32 字节 key，**全部验不过**（按 10 文件末块 PKCS7 判据，与 IV 无关） |
| 扫内存可写区（540 MB）找连续 16/24/32 字节 key | 0 命中 |
| 扫完整 6.8 GB dump | 未跑完（手动中断） |

结论：语音解密走的是**微信自带、静态编译进主程序的 AES**，不经过系统加密库，
所以系统库断点抓不到；raw key 也不以连续字节存在于可写内存（很可能内存里只剩
展开后的 AES 轮密钥表，没有原始 key）。

还没试、理论上可行的两条路：

1. **扫完整 6.8 GB 内存 dump**（`process save-core -s full`），用上面「10 文件末块全合法
   PKCS7」当判据。命中即能一次性解开全部 10 段。
2. **AES 轮密钥表探测**：即便内存里只有展开后的轮密钥表，也能靠密钥编排的固定递推关系
   把它认出来，并反推回原始 key。比逐字节爆破更快，且能应对「只剩解密轮密钥表」的情况。

准备工作（都已就绪，以后接着用）：SIP 已在恢复模式关闭；`/Applications/WeChat.app`
已用 `codesign` 重签并加上 `com.apple.security.get-task-allow`，可以干净地被 lldb 附加
（微信自动更新后要重做重签）。

## 本机数据之外还剩什么办法

既然音频能在微信里正常播放，说明 CDN 上还在，只是拿不到文件。可行的只有录音：

1. 装一个环回音频设备把系统输出接进录音端，比如 [BlackHole](https://github.com/ExistentialAudio/BlackHole)
   （开源免费，`brew install blackhole-2ch`），建一个「多输出设备」同时接扬声器和 BlackHole，
   这样能一边听一边录。
2. 用 ffmpeg 或 QuickTime 从 BlackHole 录制，在微信里逐段播放。
3. 按 `audio-index.md` 里的时间、发送人、时长给录出来的文件命名。

macOS 自带的截屏工具（`Cmd+Shift+5`）只能录麦克风，不装环回设备录不到系统内部声音。

另一条路是趁 SIP 关着、微信已重签名，用 LLDB 断在 SILK 解码函数上把内存缓冲区 dump 出来。
理论可行但工作量远大于 8 段共 110 秒语音的价值。

## 转码

`.silk` 播放器基本不认，转码依赖 [pilk](https://github.com/foyoux/pilk)（Python 绑定的
SILK 编解码器）。Homebrew 的 `python3` 受 PEP 668 保护装不了三方包，硬绕有搞坏 Homebrew
的风险，所以装在本目录的 venv 里：

```bash
cd exporters/wechat-favorites
python3 -m venv .venv
.venv/bin/pip install pilk
```

脚本会自动把 `.venv` 的 `site-packages` 挂进 `sys.path`，所以照旧用系统 `python3`
跑就行，不必先 activate。C 扩展是按 Python 版本编译的，换了解释器版本要重建 venv。

想要 mp3 还需要 ffmpeg（`brew install ffmpeg`）。缺依赖时会自动降级并提示：
`mp3` → `wav` → 只留 `.silk`。

默认采样率 24000 Hz。实测一段 620 ms 的语音转出 29804 字节 WAV，
正好等于 620ms × 24000Hz × 2 字节 + 44 字节 WAV 头，说明这个采样率是对的。

## 输出

```
output/audio/
├── 2026/
│   ├── 2026-02-16-张三-5s-fav2584.mp3
│   └── ...
├── audio-index.md      # 人看的清单，含未能提取的原因
└── audio-index.json    # 结构化清单，含 msgid、fullmd5、fullsize
```

同一段语音可能既是独立收藏、又被收进笔记（本机 fav#1950 和 fav#1951 就是同一段
18.2 秒语音），按 `fullmd5` 去重只写一个文件，清单里两条都列、第二条标注指向同一文件。

## 常用参数

| 参数 | 说明 |
| --- | --- |
| `--format mp3\|wav\|silk` | 目标格式，默认 mp3；缺依赖自动降级 |
| `--rate 24000` | SILK 采样率 |
| `--keep-silk` | 转码后保留原始 `.silk` |
| `--account NAME_1a2b` | 多账号时指定账号目录 |
| `--keep-cache` | 保留解密后的消息库，排查用 |

## 隐私说明

`export` 过程要解密 `media_*.db` 和 `message_*.db`，后者含**完整聊天记录**，
比收藏本身敏感得多。脚本默认索引完立刻删掉解密产物，只有显式 `--keep-cache` 才保留
（保留时在 `/tmp/wechat-audio-cache`，用完请自己删）。

为了少解密，脚本按 `media_*.db` → `message_resource.db` → `message_[0-9]*.db` 的顺序
逐个试，msgid 全部命中就提前停。正常情况下只会解 5.3 MB 的 `media_0.db`。

`output/` 已被 `.gitignore` 忽略。
