# 微信收藏批量导出方案（macOS WeChat 4.1+）

本目录用于从 macOS 微信本地收藏数据库 `favorite.db` 导出收藏清单。

实测环境：

- 微信版本：`4.1.11`
- 收藏数据库：`~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<账号目录>/db_storage/favorite/favorite.db`
- 数据库大小：约 `13 MB`（3396 条收藏）

`<账号目录>` 形如 `SomeName_1a2b`，只登录过一个账号时脚本会自动找到，不用管。
有多个账号目录时用 `--account` 指定，或填到仓库根目录 `.env` 的 `WECHAT_ACCOUNT` 里。

## 原理简述

WeChat 4.1+ 不再把可直接使用的数据库 raw key 缓存在内存里，而是在登录时调用 macOS 的 `CCKeyDerivationPBKDF` 派生密钥。脚本会用 LLDB 断在这个系统函数上，捕获 32 字节 passphrase，再用 `favorite.db` 自己的 16 字节 salt 做 `PBKDF2-HMAC-SHA512`（256000 轮）派生出该库密钥，并通过 SQLCipher4 的 HMAC 校验确认密钥正确。

解密完成后，脚本会从解密后的 SQLite 里解析收藏表：

- WeChat 4.x：`fav_db_item`
- WeChat 3.x 兼容：`FavItems`

导出格式：

- `output/favorites.json`
- `output/favorites.csv`
- `output/favorites.md`

## 重要风险

路线 B 必须满足两个条件：

1. 关闭 SIP：恢复模式中执行 `csrutil disable`。
2. 重签名微信：执行本目录的 `scripts/resign_wechat.sh`，去掉 Hardened Runtime。

影响说明：

- 关闭 SIP 会降低 macOS 系统保护等级。取完密钥后建议立即进恢复模式执行 `csrutil enable` 打开回来。
- 重签名会改变 `/Applications/WeChat.app` 的签名状态。微信自动更新后可能恢复原签名，需要重新执行。
- 脚本只读取本机数据，不上传内容，不修改微信数据库。
- 生成的 `favorite_key.json`、解密后的数据库和导出结果可能包含隐私信息，已被 `.gitignore` 忽略。

## 使用步骤

### 1. 关闭 SIP

Apple Silicon：

1. 关机。
2. 长按电源键进入启动选项。
3. 进入「选项」→「实用工具」→「终端」。
4. 执行：

```bash
csrutil disable
```

5. 重启。

### 2. 重签名微信

回到系统后，在本目录执行：

```bash
cd exporters/wechat-favorites
bash scripts/resign_wechat.sh
```

脚本会要求输入确认词：`RESIGN_WECHAT`。

### 3. 重新打开微信

打开微信并保持登录。首次抓取时，脚本会提示你在微信里「退出登录 → 重新扫码登录」，这是为了触发 `CCKeyDerivationPBKDF`。

### 4. 一键导出

```bash
cd exporters/wechat-favorites
bash run.sh
```

首次成功后，passphrase 会缓存到：

```bash
~/.wcdb-key-tool/wechat-passphrase.json
```

后续如果账号和微信数据未变，可直接复用缓存，不必再次退出登录。

## 常用命令

只检查环境和数据库路径：

```bash
python3 scripts/extract_favorites.py inspect
```

只抓密钥：

```bash
sudo python3 scripts/extract_favorites.py capture --timeout 180
```

只解密：

```bash
python3 scripts/extract_favorites.py decrypt --keys output/favorite_key.json
```

只导出：

```bash
python3 scripts/extract_favorites.py export --decrypted output/decrypted/favorite_decrypted.db
```

如果发现多个账号目录，用 `--account` 指定（`inspect` 会把候选目录名都列出来）：

```bash
python3 scripts/extract_favorites.py --account SomeName_1a2b inspect
```

嫌每次都要带参数，就把它写进仓库根目录的 `.env`：`WECHAT_ACCOUNT=SomeName_1a2b`，
`run.sh` 会自动读。

## 导出后抓公众号正文

如果收藏主要是公众号文章，`favorites.csv` 里的 `url` 列可以继续交给公众号文章抓取工具批量下载成 Markdown。建议后续用 `wechat-notebank import` 或把 URL 列转成 `wechat2md` 支持的链接列表。

注意：`favorite.db` 通常保存的是文章链接、标题、摘要、来源等元数据；图片、视频、文件原始内容可能在微信 CDN 或加密缓存里，不保证能直接从收藏库还原。

## 导出收藏里的语音

见 [AUDIO.md](AUDIO.md)。`scripts/extract_favorite_audio.py` 会列出收藏里的所有语音条目，
并尝试从本机消息库 `media_*.db` 还原成 `.silk` / `wav` / `mp3`。

能不能还原取决于本机消息库还留着多久的记录：`media_*.db` 是滚动的，重新登录过的机器
也不会回溯同步历史聊天。还原不了的会在清单里标注具体原因。

## 重新开启 SIP

导出完成后建议恢复 SIP：

```bash
csrutil enable
```

同样需要在恢复模式终端执行，然后重启。
