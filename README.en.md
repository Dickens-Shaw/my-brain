# my-brain

English | [简体中文](README.md)

Export WeChat favorites, Chrome bookmarks and Juejin liked posts into a local
Markdown knowledge base. Local-only, stdlib-only Python, zero dependencies.

You've been saving things for years, but they're all locked inside someone
else's app: not searchable, not backup-able, and gone the moment the account
is. This repo pulls them back onto your disk as Markdown files you can grep or
open in Obsidian / Logseq.

## Three pipelines

Each subdirectory is a self-contained pipeline. They don't depend on each
other, so run only the one you need.

| Directory | Source | Entry point | Actual output |
|-----------|--------|-------------|---------------|
| [wechat-favorites-export](wechat-favorites-export/) | The local WeChat favorites database `favorite.db` on macOS | `bash run.sh` | 3,396 favorites, 2,894 article bodies fetched |
| [chrome-bookmarks-export](chrome-bookmarks-export/) | Chrome / Edge / Brave / Arc bookmark files | `bash run.sh` | 1,049 bookmarks after dedup, 606 page bodies fetched |
| [juejin-articles-export](juejin-articles-export/) | Your "liked posts" on Juejin | `bash run.sh` | 647 posts, all fetched |

All three share one shape: distinct stages wired together by JSON, so any
single stage can be re-run on its own.

```
source ──→ index JSON/CSV/MD ──→ Markdown bodies ──→ categorized index / knowledge base
```

Body fetching is resumable. Progress lives in `.fetch_state.json`; `Ctrl+C`
saves before exiting, and the next run picks up where it stopped. Anything that
couldn't be fetched is classified and written to `未抓取清单.md` ("not fetched"
list) rather than silently dropped.

## Getting started

Fill in your machine-local config first. Real values stay on your disk —
`.env` is gitignored:

```bash
cp .env.example .env
```

Only two fields matter: your Juejin user ID (needed to read the liked-posts
list) and your WeChat account directory name (leave it empty if you've only
ever signed into one WeChat account). All three `run.sh` scripts read the
repo-root `.env` on startup, and environment variables of the same name
override it.

```bash
cd juejin-articles-export && bash run.sh        # easiest: no permissions, no credentials
cd chrome-bookmarks-export && FETCH=1 bash run.sh
cd wechat-favorites-export && bash run.sh       # requires disabling SIP and re-signing WeChat first
```

The three differ a lot in how much trouble they are:

- **Juejin** is the easy one. The interaction-list endpoint is public, so no
  cookies are needed.
- **Chrome bookmarks** are a plaintext JSON file — just read it, no system
  permission changes at all. The cost shows up when fetching bodies: bookmarks
  span thousands of sites, so the fetcher uses readability-style scoring to
  pick the content node.
- **WeChat favorites** is the painful one. The database is SQLCipher-encrypted,
  so you have to disable SIP, re-sign WeChat, and break on
  `CCKeyDerivationPBKDF` with LLDB to capture the passphrase. The upside is
  that every page has the same structure, so locking onto `#js_content` yields
  clean content.

Details live in each pipeline's own docs (Chinese):
[WeChat](wechat-favorites-export/README.md) ·
[WeChat bodies](wechat-favorites-export/ARTICLES.md) ·
[Chrome bookmarks](chrome-bookmarks-export/README.md) ·
[Chrome page bodies](chrome-bookmarks-export/PAGES.md) ·
[Juejin](juejin-articles-export/README.md)

## What the output looks like

Every article becomes one Markdown file with YAML frontmatter, filed by year,
named `date-title.md`:

```yaml
---
title: "TypeScript 技巧：让代码库整洁 10 倍"
account: "前端小石匠"
publish_time: "2025-01-06 07:25:00"
favorite_time: "2025-01-22T18:34:56"
source_url: "http://mp.weixin.qq.com/s?..."
---
```

On top of that, a categorized index is generated (`knowledge-base/`, `索引/`)
with links pointing at the local files. The articles themselves are never
moved, so re-categorizing never touches what you already fetched.

## Requirements

- macOS (the WeChat pipeline needs macOS LLDB and the WeChat client; the other
  two are cross-platform)
- Python 3, standard library only — no `pip install`

## Privacy

These scripts touch your entire saving history. Treat the results as private
data.

- Local reads only. Nothing is uploaded, and neither the WeChat database nor
  the browser's bookmark file is modified.
- Personal identifiers such as account IDs live in `.env`; the repo only ships
  the `.env.example` template.
- `output/`, `.env`, `*.json`, `*.db` and `*cookies*.json` are all gitignored.
  Still, check `git status` before committing — don't let exported content or
  cookies into the repo. The `sessionid` inside a cookie string copied from
  your browser is as good as your password.
- The WeChat pipeline requires SIP to be off, which lowers the protection level
  of macOS itself. Boot into recovery mode and run `csrutil enable` as soon as
  you've captured the key.

## License

[MIT](LICENSE) for the code in this repository.

Note that this covers the tooling only. Anything the scripts fetch into
`output/` remains the copyright of its original authors — keep it local, and
don't redistribute it. That directory is gitignored for exactly this reason.
