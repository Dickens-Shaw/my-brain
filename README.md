# my-brain

English | [简体中文](README.zh-CN.md)

Export WeChat favorites, Chrome bookmarks, Juejin liked posts / collections and
Juejin booklets to Markdown, migrate them into a private Obsidian Vault, and
expose a rebuildable retrieval index through Cursor MCP.

You've been saving things for years, but they're locked inside someone else's
app: not searchable, not backup-able, and gone the moment the account is. This
repo pulls them back onto disk, then turns them into a knowledge base you can
actually use while coding.

## Layout

```
exporters/          Python export pipelines (stdlib only)
  wechat-favorites/
  chrome-bookmarks/
  juejin-articles/
  juejin-books/
packages/           TypeScript knowledge layer
  core/             frontmatter schema, enums, id rules
  config/           .env / VAULT_PATH
  vault/            Vault I/O and validation
  ingest/           one-shot migration pipeline
  retrieval/        chunking, indexing, hybrid search
apps/
  mcp/              Cursor MCP Server (stdio)
```

The Vault (the notes themselves) lives in a **separate private repo**. This
public repo only holds code and a `VAULT_PATH` config pointer.

## Four exporters

Each subdirectory is a self-contained pipeline. They don't depend on each
other, so run only the one you need.

| Directory | Source | Entry point | Actual output |
|-----------|--------|-------------|---------------|
| [exporters/wechat-favorites](exporters/wechat-favorites/) | The local WeChat favorites database `favorite.db` on macOS | `bash run.sh` | 3,396 favorites, 2,894 article bodies fetched |
| [exporters/chrome-bookmarks](exporters/chrome-bookmarks/) | Chrome / Edge / Brave / Arc bookmark files | `bash run.sh` | 1,049 bookmarks after dedup, 606 page bodies fetched |
| [exporters/juejin-articles](exporters/juejin-articles/) | Your "liked posts" and collection sets on Juejin | `bash run.sh` | 647 liked + 37 collected posts, all fetched |
| [exporters/juejin-books](exporters/juejin-books/) | Juejin booklets you already have on disk | `bash run.sh` | 39 booklets / 829 chapters, 740 converted to Markdown |

All four share one shape: distinct stages wired together by JSON, so any
single stage can be re-run on its own.

```
source ──→ index JSON/CSV/MD ──→ Markdown bodies ──→ (migrate) Vault / retrieval index
```

The first three pipelines fetch over the network, and that's resumable.
Progress lives in `.fetch_state.json`; `Ctrl+C` saves before exiting, and the
next run picks up where it stopped. Anything that couldn't be fetched is
classified and written to `未抓取清单.md` ("not fetched" list) rather than
silently dropped.

The booklet pipeline never goes online. Booklets aren't something an API hands
you, so `input/` holds files you already collected and the pipeline just
normalizes them locally: detect the format, recover chapter numbering, convert
HTML to Markdown, and relocate images.

## Getting started

### 1. Config

```bash
cp .env.example .env
```

Fill in at least `JUEJIN_USER_ID`, `VAULT_PATH` (absolute path to the private
Vault), and `OPENAI_API_KEY` when you want retrieval / classification.
`WECHAT_ACCOUNT` is only needed if you have multiple WeChat accounts.

### 2. Run exporters

```bash
cd exporters/juejin-books && bash run.sh           # fully local, no network, seconds
cd exporters/juejin-articles && bash run.sh        # no permissions, no credentials
cd exporters/chrome-bookmarks && FETCH=1 bash run.sh
cd exporters/wechat-favorites && bash run.sh       # requires disabling SIP and re-signing WeChat first
```

The four pipelines have very different barriers:

- **Juejin booklets** only touch local files — no account, no network. The only
  prerequisite is that `input/` already has the booklets.
- **Juejin posts** use public list APIs, no Cookie needed. The one exception is
  private collection sets, which need `JUEJIN_COOKIE`.
- **Chrome bookmarks** are plain JSON — no system permissions. The trade-off is
  that bookmarks span thousands of sites, so body extraction relies on
  readability-style scoring.
- **WeChat favorites** are the hardest: the DB is SQLCipher-encrypted, so you
  disable SIP, re-sign WeChat, and catch the passphrase with LLDB on
  `CCKeyDerivationPBKDF`. The payoff is a uniform page structure — lock onto
  `#js_content` and you get a clean body.

Details live in each pipeline's own README:
[WeChat](exporters/wechat-favorites/README.md) ·
[WeChat bodies](exporters/wechat-favorites/ARTICLES.md) ·
[Chrome bookmarks](exporters/chrome-bookmarks/README.md) ·
[Chrome page bodies](exporters/chrome-bookmarks/PAGES.md) ·
[Juejin posts](exporters/juejin-articles/README.md) ·
[Juejin booklets](exporters/juejin-books/README.md)

### 3. TypeScript knowledge layer

```bash
pnpm install
pnpm vault:init                              # create Vault skeleton at VAULT_PATH
pnpm vault:validate                          # validate frontmatter / broken image refs
pnpm migrate --stage normalize               # migrate: normalize (then dedupe → embed → induce)
# Review .data/migrate/topic-taxonomy.json and set status to confirmed
pnpm migrate --stage classify
pnpm migrate --stage write
pnpm index                                   # build / incremental retrieval index (.data/index.db)
pnpm search -- --query "Vite cold start"
pnpm mcp                                     # Cursor MCP (stdio)
```

Golden questions live in `eval/golden-questions.json`. Fill `expected_doc_ids` yourself from real coding questions, then run `pnpm eval`.

Vault top-level layout:

- Machine-maintained: `Inbox/`, `Library/`, `Books/`, `Maps/`
- Handwritten: `Notes/`, `Projects/`, `Daily/`, `Templates/`
- `Assets/` serves handwritten notes only; imported images live next to each
  document under `assets/`

## Requirements

- macOS (WeChat pipeline needs macOS LLDB + the WeChat client; the other three
  exporters are cross-platform)
- Python 3 (exporters use stdlib only — no `pip install`)
- Node.js ≥ 20 + pnpm (knowledge layer)
- SQLite ≥ 3.34 (FTS5 `trigram` tokenizer for the retrieval index)

## Privacy

These scripts handle your entire favorites history. Treat the output as private.

- Exporters are read-only against local data; they never modify the WeChat DB or
  browser bookmark files.
- **Raw data files are never uploaded** (databases, cookies, booklet `input/`).
  The knowledge layer does send document bodies to cloud embedding / LLM APIs
  for vectorization, classification, and retrieval — that is a prerequisite for
  search.
- Account IDs, cookies, and API keys live in `.env`; the repo only ships
  `.env.example`.
- All `exporters/*/output/`, booklet `input/`, `.env`, `*.db` indexes, and
  `*cookies*.json` are gitignored. Check `git status` before committing.
- Vault content is other people's copyrighted material and belongs in a
  separate private repo — never push it into this public one.
- The WeChat pipeline requires disabling SIP, which lowers macOS system
  protections. After capturing the key, go back into Recovery and run
  `csrutil enable`.

## License

Code in this repo is [MIT](LICENSE).

That covers the tools only. Anything the scripts write under `output/` belongs
to the original authors — keep it for personal use, don't redistribute. Those
directories are gitignored for that reason. Booklets are paid content;
`exporters/juejin-books/` `input/` and `output/` are ignored — be especially
careful not to share them.
