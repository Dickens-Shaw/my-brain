# my-brain

English | [简体中文](README.zh-CN.md)

Export WeChat favorites, Chrome bookmarks, Juejin liked posts / collections and
Juejin booklets to Markdown, migrate them into a private Obsidian Vault, and
expose a rebuildable retrieval index through Cursor MCP.

You've been saving things for years, but they're locked inside someone else's
app: not searchable, not backup-able, and gone the moment the account is. This
repo pulls them back onto disk, then turns them into a knowledge base you can
actually use while coding.

The long-term goal is a knowledge base that AI can actually reach into, built in
stages. **Markdown in the Vault is the single source of truth; everything else —
index, search, and whatever gets built later — is a derived layer that can be
deleted and rebuilt from scratch.** What exists today is the foundation: the
four exporters, the one-shot migration, the retrieval index, and the MCP server.
See [Roadmap](#roadmap) for what comes next and what deliberately won't be
built.

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

Two rules follow from the Vault being the source of truth, and they bind
everything built later:

- Changing a document means editing the frontmatter of that file in the Vault and
  re-indexing — never writing to a table in some database instead. Nothing here
  may become a second note system.
- Delete `.data/index.db` and any future backend state at any time, and the Vault
  must be intact. The index is disposable by design; `pnpm index` rebuilds it.

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

`VAULT_PATH` (absolute path to the private Vault) is required by every command in
the knowledge layer — they fail immediately without it. `JUEJIN_USER_ID` is needed
for the Juejin exporter, and `WECHAT_ACCOUNT` only if you have several WeChat
accounts.

Migration and indexing additionally need two model capabilities, and **they may
come from different providers**:

| Capability | Used for | Config |
|---|---|---|
| Embeddings | Topic clustering and all retrieval | `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS` |
| Chat | Naming topic clusters, classifying edge cases | `CHAT_API_KEY`, `CHAT_BASE_URL`, `CHAT_MODEL` (falls back to the embedding provider) |

Any provider with an OpenAI-compatible `/embeddings` endpoint works. Worth knowing
before you pick one: **embeddings are the hard requirement and roughly 95% of the
cost; chat is a few cents.** Some providers — DeepSeek among them — offer chat only
and have no embeddings endpoint at all, which is exactly why the two are
configurable separately.

Model and dimension must match your provider. `EMBEDDING_DIMENSIONS` is baked into
the vector table when it is created, so getting it wrong would corrupt the index —
the first batch of vectors is checked and the run aborts with the correct value if
they disagree. Changing either one later requires a full rebuild, because vectors
from different models are not comparable.

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
pnpm vault:init                    # create Vault skeleton at VAULT_PATH
pnpm vault:validate                # validate frontmatter / broken image refs

pnpm migrate --from normalize      # normalize → dedupe → embed → induce, then stops
# Review .data/migrate/topic-taxonomy.json, set status to confirmed
pnpm migrate --stage classify
pnpm migrate --stage write         # writes into Library/ and Books/, generates Maps/

pnpm index                         # build / update the index (.data/index.db)
pnpm search -- --query "Vite cold start"
pnpm mcp                           # Cursor MCP (stdio)
```

`--from <stage>` runs that stage and everything after it; `--stage <stage>` runs
exactly one. Stages hand off through JSON in `.data/migrate/`, so any stage can be
re-run alone.

#### One-shot pipeline

`scripts/full-pipeline.sh` runs the whole chain with a preflight check:

```bash
bash scripts/full-pipeline.sh            # normalize → embed → induce, then stops for review
bash scripts/full-pipeline.sh --from classify   # classify → write → index → eval
bash scripts/full-pipeline.sh --check    # only verify the embedding API works
```

The embedding API is the hard dependency, so the script checks it before
anything else — a dead key fails in two seconds instead of after the migration
has already paid for document embeddings. It also checks that the model
returns the dimension `EMBEDDING_DIMENSIONS` claims, which catches a
mismatched model before the index is built.

`pnpm smoke` runs the retrieval pipeline against a local deterministic
embedding, so the chunking, indexing and search code paths can be verified
without an API key.

`scripts/mock-embeddings.mjs` is the mock endpoint that makes this possible —
it is for testing the plumbing, not for real retrieval quality.

**Migration stops on purpose after `induce`.** Clustering proposes a topic
taxonomy and the LLM names each cluster, but that taxonomy decides the physical
directory layout of ~4.8k documents — changing it after `write` means moving
files en masse. So it waits for you to read `topic-taxonomy.json` (document counts,
sample titles, warnings about clusters that are too large or too small) and set
`status: confirmed`. Nothing is classified until you do.

`embed` and the LLM stages cost money and checkpoint as they go, so an interrupted
run resumes instead of paying twice.

What to expect at full scale, measured on the real corpus: 4,802 documents after
dedup produce **82,326 chunks** and an index file of about **375MB**. Embedding all
of it costs roughly **$0.6** and is dominated by network round trips, so budget
tens of minutes. Once built, hybrid search is about 150ms for the vector half and
115ms for BM25.

Golden questions live in `eval/golden-questions.json`. Fill `expected_doc_ids`
yourself from real coding questions, then run `pnpm eval` — nobody else can decide
what a correct answer looks like for your own notes.

Vault top-level layout:

- Machine-maintained: `Inbox/`, `Library/`, `Books/`, `Maps/`
- Handwritten: `Notes/`, `Projects/`, `Daily/`, `Templates/`
- `Assets/` serves handwritten notes only; imported images live next to each
  document under `assets/`

## Roadmap

Ordered by dependency, not by how interesting each part sounds: every step
consumes what the previous one produced. Several have explicit trigger
conditions, because building them early costs real money and latency for a
benefit that hasn't been demonstrated yet.

1. **`vault-watcher`** — watch the Vault, debounce, trigger incremental indexing.
   First because it's the smallest and pays off immediately: once you're editing
   notes in Obsidian daily, you *will* forget to re-index, and stale retrieval is
   worse than none. No UI, no API, no queue.
2. **`knowledge-inbox`** — import URLs, PDFs and GitHub repos into `Inbox/`, then
   reuse the existing classifier to file them into `Library/`. CLI first: the hard
   part is extraction quality, which has nothing to do with having a UI.
3. **`admin-console`** — `apps/api` (NestJS) + `apps/web` (Next.js). Deliberately
   this late, because only now does it have anything to show: retrieval traces to
   debug, chunks to visualise, an import pipeline to drive, classifications to
   edit.
4. **`ai-chat`** — answers with source citations. After the console, because chat
   quality is entirely determined by retrieval quality; without a trace viewer you
   can't tell "never retrieved" from "retrieved but ranked below the cutoff", and
   you end up blindly rewriting prompts.
5. **`agentic-retrieval`** — query rewriting, multi-hop retrieval, tracing.
   **Trigger:** more than 20% of the golden questions genuinely need "look up A,
   then use it to look up B". Below that it multiplies tokens and latency for
   nothing.
6. **`knowledge-graph`** — entities and relations. **Trigger:** `Notes/` reaches a
   few hundred entries *and* real cross-document reasoning shows up. Even then,
   start with an adjacency table in SQLite; a graph database at that scale is
   paying operational cost for an unproven benefit.

Explicitly not planned: RSS / Notion import, mobile apps, authentication, S3 /
MinIO, Milvus. This is single-user and local — those either have no consumer here
or exist to solve multi-user scale.

`apps/api` intentionally does not exist yet. An empty skeleton with no consumer is
just maintenance surface; it arrives with the console that needs it.

## Requirements

- macOS (WeChat pipeline needs macOS LLDB + the WeChat client; the other three
  exporters are cross-platform)
- Python 3 (exporters use stdlib only — no `pip install`)
- Node.js ≥ 20 + pnpm (knowledge layer)

No system SQLite needed: `better-sqlite3` ships its own build with FTS5 enabled
(currently 3.53.x), and vector search comes from the `sqlite-vec` extension loaded
at runtime. `better-sqlite3` compiles natively on install, so the root
`package.json` lists it under `pnpm.onlyBuiltDependencies` — without that entry
pnpm silently skips the build and the index can't be opened. `sqlite-vec` ships
prebuilt binaries and needs no such entry.

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
