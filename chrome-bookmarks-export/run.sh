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
BROWSER="${BROWSER:-chrome}"
FETCH="${FETCH:-0}"
LIMIT="${LIMIT:-0}"

echo "== Chrome 书签导出 =="
echo "浏览器: ${BROWSER}"
echo "输出目录: ${OUTPUT_DIR}"
echo
echo "先做环境检查:"
python3 scripts/extract_bookmarks.py --browser "${BROWSER}" --output "${OUTPUT_DIR}" inspect
echo
echo "导出书签清单:"
python3 scripts/extract_bookmarks.py --browser "${BROWSER}" --output "${OUTPUT_DIR}" export

if [ "${FETCH}" = "1" ]; then
  echo
  echo "抓取网页正文（这一步走网络，耗时最长，可以随时 Ctrl+C，下次继续）:"
  python3 scripts/fetch_pages.py \
    --input "${OUTPUT_DIR}/bookmarks.json" \
    --output "${OUTPUT_DIR}/pages" \
    --limit "${LIMIT}"
else
  echo
  echo "跳过正文抓取。想连正文一起抓，用: FETCH=1 bash run.sh"
fi

echo
echo "整理知识库目录:"
python3 scripts/build_knowledge_base.py \
  --input "${OUTPUT_DIR}/bookmarks.json" \
  --output "${OUTPUT_DIR}/knowledge-base" \
  --pages "${OUTPUT_DIR}/pages" \
  --clean
