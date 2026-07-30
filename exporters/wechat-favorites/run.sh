#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

for env_file in "${SCRIPT_DIR}/../../.env" "${SCRIPT_DIR}/.env"; do
  if [ -f "${env_file}" ]; then
    set -a
    # shellcheck source=/dev/null
    . "${env_file}"
    set +a
  fi
done

OUTPUT_DIR="${OUTPUT_DIR:-output}"
TIMEOUT="${TIMEOUT:-180}"
WECHAT_ACCOUNT="${WECHAT_ACCOUNT:-}"

# sudo 不会带过去环境变量，所以账号只能走命令行参数
ACCOUNT_FLAG=()
if [ -n "${WECHAT_ACCOUNT}" ]; then
  ACCOUNT_FLAG=(--account "${WECHAT_ACCOUNT}")
fi

echo "== 微信收藏导出 =="
echo "输出目录: ${OUTPUT_DIR}"
if [ -n "${WECHAT_ACCOUNT}" ]; then
  echo "账号目录: ${WECHAT_ACCOUNT}"
fi
echo
echo "先做环境检查:"
python3 scripts/extract_favorites.py ${ACCOUNT_FLAG[@]+"${ACCOUNT_FLAG[@]}"} --output "${OUTPUT_DIR}" inspect
echo
echo "开始抓密钥、解密并导出。"
echo "提示：如果是首次抓取，脚本运行后请在微信里退出登录，再扫码登录。"
echo
sudo python3 scripts/extract_favorites.py ${ACCOUNT_FLAG[@]+"${ACCOUNT_FLAG[@]}"} --output "${OUTPUT_DIR}" all --timeout "${TIMEOUT}"
