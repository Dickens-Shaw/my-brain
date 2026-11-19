#!/usr/bin/env bash
#
# One-shot pipeline: exporters → Vault → index → baseline evaluation.
#
# The embedding API is the hard dependency. This script checks it first, so a
# dead key fails in two seconds instead of after the migration has already
# spent money on document embeddings.
#
# Usage:
#   bash scripts/full-pipeline.sh              # full run
#   bash scripts/full-pipeline.sh --from write # resume from the write stage
#   bash scripts/full-pipeline.sh --check      # only check the embedding API
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

FROM="normalize"
CHECK_ONLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --from) FROM="$2"; shift 2 ;;
    --check) CHECK_ONLY=1; shift ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

step() { printf '\n\033[1m▶ %s\033[0m\n' "$*"; }
warn() { printf '\033[33m! %s\033[0m\n' "$*"; }
die()  { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- preflight
step "Preflight"

[[ -f .env ]] || die ".env not found. Copy .env.example to .env and fill it in."
set -a
# shellcheck disable=SC1091
source .env
set +a

: "${VAULT_PATH:?VAULT_PATH is not set}"
: "${OPENAI_API_KEY:?OPENAI_API_KEY is not set}"
: "${OPENAI_BASE_URL:?OPENAI_BASE_URL is not set}"
: "${EMBEDDING_MODEL:?EMBEDDING_MODEL is not set}"
: "${EMBEDDING_DIMENSIONS:?EMBEDDING_DIMENSIONS is not set}"

EMBED_BASE="${OPENAI_BASE_URL%/}"
echo "  vault:    $VAULT_PATH"
echo "  embed:    ${EMBED_BASE} (${EMBEDDING_MODEL}, ${EMBEDDING_DIMENSIONS}d)"
echo "  chat:     ${CHAT_BASE_URL:-${EMBED_BASE}} (${CHAT_MODEL:-unset})"

if [[ ! -d "$VAULT_PATH" ]]; then
  warn "VAULT_PATH does not exist yet — creating the skeleton"
  pnpm vault:init
fi

step "Checking the embedding API"
EMBED_CHECK=$(node -e '
const base = process.env.OPENAI_BASE_URL.replace(/\/+$/, "");
const res = await fetch(base + "/embeddings", {
  method: "POST",
  headers: {
    Authorization: "Bearer " + process.env.OPENAI_API_KEY,
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    model: process.env.EMBEDDING_MODEL,
    input: ["ping"],
    dimensions: Number(process.env.EMBEDDING_DIMENSIONS),
  }),
});
const text = await res.text();
if (!res.ok) {
  console.error("  HTTP " + res.status + " " + text.slice(0, 200));
  process.exit(1);
}
const json = JSON.parse(text);
const dim = json.data?.[0]?.embedding?.length;
console.log("  OK, returned " + dim + " dimensions");
if (String(dim) !== String(process.env.EMBEDDING_DIMENSIONS)) {
  console.error("  Dimension mismatch: model returned " + dim +
    " but EMBEDDING_DIMENSIONS is " + process.env.EMBEDDING_DIMENSIONS);
  process.exit(1);
}
' 2>&1) || die "Embedding API is not usable.
$EMBED_CHECK
Fix the key or the base URL in .env, then run this script again."

echo "$EMBED_CHECK"

# The chat API is only needed for naming clusters and classifying edge cases.
# A missing chat provider degrades the taxonomy quality but does not stop the
# run, so it is a warning rather than an error.
step "Checking the chat API"
if [[ -n "${CHAT_API_KEY:-}" ]]; then
  CHAT_CHECK=$(node -e '
const base = (process.env.CHAT_BASE_URL || process.env.OPENAI_BASE_URL).replace(/\/+$/, "");
const res = await fetch(base + "/chat/completions", {
  method: "POST",
  headers: {
    Authorization: "Bearer " + process.env.CHAT_API_KEY,
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    model: process.env.CHAT_MODEL,
    messages: [{ role: "user", content: "ping" }],
    max_tokens: 1,
  }),
});
console.log(res.ok ? "  OK" : "  HTTP " + res.status);
process.exit(res.ok ? 0 : 1);
' 2>&1) && echo "$CHAT_CHECK" || warn "Chat API is not usable — theme names will fall back to generic labels:
$CHAT_CHECK"
else
  warn "No CHAT_API_KEY — chat falls back to the embedding provider"
fi

if [[ "$CHECK_ONLY" == "1" ]]; then
  step "Preflight only — nothing else was run"
  exit 0
fi

# --------------------------------------------------------------- pipeline
run_from() {
  local stage="$1"
  echo
  step "migrate --stage $stage"
  pnpm migrate --stage "$stage"
}

case "$FROM" in
  normalize|dedupe|embed|induce)
    # `induce` stops for human review: the taxonomy decides the physical
    # directory layout of every document, so it is not changed automatically.
    run_from normalize
    run_from dedupe
    run_from embed
    run_from induce

    step "Review the taxonomy"
    echo "  .data/migrate/topic-taxonomy.json"
    node -e '
const tax = require("./.data/migrate/topic-taxonomy.json");
console.log("  leaves: " + tax.leaves.length +
  "  too_large: " + tax.leaves.filter(l => l.too_large).length +
  "  too_small: " + tax.leaves.filter(l => l.too_small).length);
console.log("");
for (const leaf of tax.leaves.slice(0, 100)) {
  console.log("    " + leaf.name.padEnd(24) + String(leaf.doc_count).padStart(5) +
    (leaf.too_large ? "  (too large)" : leaf.too_small ? "  (too small)" : ""));
}
'
    echo ""
    warn "The taxonomy needs review before classifying."
    echo "  Edit the file, set status to \"confirmed\", then run:"
    echo "    bash scripts/full-pipeline.sh --from classify"
    exit 0
    ;;
  classify)
    run_from classify
    ;;
  write) ;;
  *) die "Unknown stage: $FROM" ;;
esac

if [[ "$FROM" == "normalize" || "$FROM" == "dedupe" || "$FROM" == "embed" || "$FROM" == "induce" || "$FROM" == "classify" ]]; then
  echo
  warn "Stopping after $FROM. Review the taxonomy, then:"
  echo "  bash scripts/full-pipeline.sh --from classify"
  exit 0
fi

step "write — into the Vault"
pnpm migrate --stage write
pnpm vault:validate || warn "Vault validation reported problems"

step "index — build the retrieval index"
# The chunker signature changed when the heading handling was added, so an
# existing index is rebuilt rather than mixed with vectors from old chunks.
pnpm index -- --full

step "eval — baseline numbers"
pnpm eval

step "Done"
echo "  index:   .data/index.db"
echo "  eval:    .data/eval-report.json"
echo ""
echo "Next: fill in the taxonomy review if you have not, and read"
echo ".data/eval-report.json — the three rows (hybrid / bm25 / vector) are"
echo "the baseline every later retrieval change is measured against."
