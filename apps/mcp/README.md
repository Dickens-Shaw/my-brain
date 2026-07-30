# @my-brain/mcp

stdio MCP Server for Cursor. Exposes `search_knowledge`, `get_document`, `list_topics`.

## Prerequisites

1. `VAULT_PATH` and `OPENAI_API_KEY` in repo-root `.env`
2. Index built: `pnpm index`

## Cursor config example

Add to Cursor MCP settings (path adjusted to your clone):

```json
{
  "mcpServers": {
    "my-brain": {
      "command": "pnpm",
      "args": ["--dir", "/absolute/path/to/my-brain", "mcp"]
    }
  }
}
```

Or run the compiled entry:

```json
{
  "mcpServers": {
    "my-brain": {
      "command": "npx",
      "args": ["tsx", "/absolute/path/to/my-brain/apps/mcp/src/index.ts"],
      "env": {
        "VAULT_PATH": "/absolute/path/to/vault",
        "OPENAI_API_KEY": "sk-..."
      }
    }
  }
}
```
