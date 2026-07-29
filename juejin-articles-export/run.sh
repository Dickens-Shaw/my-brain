#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

for env_file in "${SCRIPT_DIR}/../.env" "${SCRIPT_DIR}/.env"; do
  if [ -f "${env_file}" ]; then
    set -a
    # shellcheck source=/dev/null
    . "${env_file}"
    set +a
  fi
done

OUTPUT_DIR="${OUTPUT_DIR:-output}"
USER_ID="${USER_ID:-${JUEJIN_USER_ID:-}}"
FETCH="${FETCH:-1}"
LIMIT="${LIMIT:-0}"
WORKERS="${WORKERS:-4}"
IMAGES="${IMAGES:-0}"
DIGG="${DIGG:-1}"
COLLECTIONS="${COLLECTIONS:-1}"

if [ -z "${USER_ID}" ]; then
  echo "错误: 没有掘金用户 ID。"
  echo "在仓库根目录执行 cp .env.example .env 并填好 JUEJIN_USER_ID，"
  echo "或者临时指定: USER_ID=<你的数字 ID> bash run.sh"
  echo "ID 就是个人主页地址 https://juejin.cn/user/<数字> 里的那串数字。"
  exit 1
fi

IMAGE_FLAG=()
if [ "${IMAGES}" = "1" ]; then
  IMAGE_FLAG=(--download-images)
fi

echo "== 掘金导出 =="
echo "用户 ID: ${USER_ID}"
echo "输出目录: ${OUTPUT_DIR}"
echo "范围: 点赞=${DIGG} 收藏集=${COLLECTIONS}"

if [ "${DIGG}" = "1" ]; then
  echo
  echo "---- 点赞文章 ----"
  echo "拉取点赞清单:"
  python3 scripts/fetch_list.py --user-id "${USER_ID}" --output "${OUTPUT_DIR}"

  if [ "${FETCH}" = "1" ]; then
    echo
    echo "抓取文章正文（走网络，耗时最长，可以随时 Ctrl+C，下次继续）:"
    python3 scripts/fetch_articles.py \
      --input "${OUTPUT_DIR}/articles.json" \
      --output "${OUTPUT_DIR}/articles" \
      --limit "${LIMIT}" \
      --workers "${WORKERS}" \
      ${IMAGE_FLAG[@]+"${IMAGE_FLAG[@]}"}
  fi
fi

if [ "${COLLECTIONS}" = "1" ]; then
  echo
  echo "---- 收藏集 ----"
  # 私有收藏集只有带登录态才可见，没配 JUEJIN_COOKIE 就只导公开的
  COOKIE_FLAG=()
  if [ -n "${JUEJIN_COOKIE:-}" ]; then
    COOKIE_FLAG=(--with-cookie)
  else
    echo "（未配置 JUEJIN_COOKIE，私有收藏集会缺失）"
  fi

  echo "拉取收藏集清单:"
  python3 scripts/fetch_collections.py \
    --user-id "${USER_ID}" \
    --output "${OUTPUT_DIR}" \
    ${COOKIE_FLAG[@]+"${COOKIE_FLAG[@]}"}

  if [ "${FETCH}" = "1" ]; then
    echo
    echo "抓取收藏集文章正文:"
    python3 scripts/fetch_articles.py \
      --input "${OUTPUT_DIR}/collections.json" \
      --output "${OUTPUT_DIR}/collections" \
      --group-by collection \
      --limit "${LIMIT}" \
      --workers "${WORKERS}" \
      ${IMAGE_FLAG[@]+"${IMAGE_FLAG[@]}"}
  fi
fi

if [ "${FETCH}" != "1" ]; then
  echo
  echo "跳过正文抓取。想连正文一起抓，用: FETCH=1 bash run.sh"
fi
