#!/usr/bin/env bash
set -euo pipefail

WECHAT_APP="${WECHAT_APP:-/Applications/WeChat.app}"

echo "== 微信重签名准备检查 =="
echo "微信路径: ${WECHAT_APP}"

if [[ ! -d "${WECHAT_APP}" ]]; then
  echo "找不到微信 App: ${WECHAT_APP}" >&2
  exit 1
fi

echo
echo "SIP 状态:"
csrutil status || true

echo
echo "微信版本:"
defaults read "${WECHAT_APP}/Contents/Info.plist" CFBundleShortVersionString || true

echo
echo "当前签名摘要:"
codesign -dv "${WECHAT_APP}" 2>&1 | grep -E "Identifier|TeamIdentifier|Runtime|flags" || true

echo
echo "说明:"
echo "1. macOS 微信 4.1+ 需要 LLDB 附加进程才能抓取数据库 passphrase。"
echo "2. 如果 SIP 仍为 enabled，LLDB 仍会被系统拒绝。请先在恢复模式执行 csrutil disable。"
echo "3. 本脚本会对微信做 ad-hoc 重签名，去掉 Hardened Runtime。微信自动更新后可能要重做。"
echo "4. 重签名会改变 /Applications/WeChat.app 的签名状态，但不修改你的微信数据目录。"
echo
read -r -p "确认执行请输入 RESIGN_WECHAT: " confirm

if [[ "${confirm}" != "RESIGN_WECHAT" ]]; then
  echo "已取消。"
  exit 0
fi

echo
echo "退出微信进程..."
osascript -e 'tell application "WeChat" to quit' >/dev/null 2>&1 || true
sleep 3
pkill -x WeChat >/dev/null 2>&1 || true

echo "执行 ad-hoc 重签名..."
sudo codesign --force --deep --sign - "${WECHAT_APP}"

echo
echo "重签名后签名摘要:"
codesign -dv "${WECHAT_APP}" 2>&1 | grep -E "Identifier|TeamIdentifier|Runtime|flags" || true

echo
echo "完成。请重新打开微信并登录，然后在本目录运行: bash run.sh"
