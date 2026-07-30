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

INPUT_DIR="${INPUT_DIR:-input}"
OUTPUT_DIR="${OUTPUT_DIR:-output}"
BUILD="${BUILD:-1}"
IMAGES="${IMAGES:-0}"
BOOK="${BOOK:-}"

if [ ! -d "${INPUT_DIR}" ]; then
  echo "错误: 找不到 ${INPUT_DIR}/。"
  echo "把小册目录放进 ${INPUT_DIR}/，一本一个文件夹，文件夹名就是册名。"
  exit 1
fi

echo "== 掘金小册整理 =="
echo "输入目录: ${INPUT_DIR}"
echo "输出目录: ${OUTPUT_DIR}"

echo
echo "---- 盘点册和章节 ----"
python3 scripts/scan_books.py --input "${INPUT_DIR}" --output "${OUTPUT_DIR}"

if [ "${BUILD}" != "1" ]; then
  echo
  echo "跳过转换。想转成 Markdown，用: BUILD=1 bash run.sh"
  exit 0
fi

BUILD_FLAGS=()
if [ "${IMAGES}" = "1" ]; then
  # 只影响正文里的远程图床地址，册内的本地配图一律会搬进 assets/
  BUILD_FLAGS+=(--download-images)
fi
if [ -n "${BOOK}" ]; then
  BUILD_FLAGS+=(--book "${BOOK}")
fi

echo
echo "---- 转成 Markdown ----"
python3 scripts/build_books.py \
  --input "${INPUT_DIR}" \
  --index "${OUTPUT_DIR}/books.json" \
  --output "${OUTPUT_DIR}/books" \
  ${BUILD_FLAGS[@]+"${BUILD_FLAGS[@]}"}
