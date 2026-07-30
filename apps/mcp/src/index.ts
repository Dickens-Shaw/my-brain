#!/usr/bin/env node
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { loadConfig } from "@my-brain/config";
import {
  OpenAIEmbedder,
  SqliteIndex,
  hybridSearch,
} from "@my-brain/retrieval";
import type { Scope } from "@my-brain/core";

const MAX_LIMIT = 12;
const SNIPPET_CHARS = 1200;

function truncate(text: string): string {
  if (text.length <= SNIPPET_CHARS) return text;
  return `${text.slice(0, SNIPPET_CHARS)}\n…[truncated]`;
}

function obsidianLink(vaultPath: string, relPath: string): string {
  // obsidian://open?path=URL-encoded absolute path
  const abs = join(vaultPath, relPath);
  return `obsidian://open?path=${encodeURIComponent(abs)}`;
}

async function main(): Promise<void> {
  let config;
  try {
    config = loadConfig({ requireOpenAI: true });
  } catch (err) {
    console.error(err instanceof Error ? err.message : err);
    process.exit(1);
  }

  if (!existsSync(config.indexPath)) {
    console.error(
      `Index not found at ${config.indexPath}. Run \`pnpm index\` first.`,
    );
    process.exit(1);
  }

  const index = new SqliteIndex(config.indexPath, config.embeddingDimensions);
  const embedder = new OpenAIEmbedder({
    apiKey: config.openaiApiKey!,
    baseUrl: config.openaiBaseUrl,
    model: config.embeddingModel,
    dimensions: config.embeddingDimensions,
  });

  const server = new Server(
    { name: "my-brain", version: "0.1.0" },
    { capabilities: { tools: {} } },
  );

  server.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools: [
      {
        name: "search_knowledge",
        description:
          "Hybrid search over the personal knowledge Vault (Library, Books, Notes, etc.). Returns snippets with Obsidian deep links.",
        inputSchema: {
          type: "object",
          properties: {
            query: { type: "string" },
            scope: {
              type: "string",
              enum: ["library", "books", "notes", "projects", "all"],
              default: "all",
            },
            topic: {
              type: "string",
              description: "Primary topic filter, e.g. 前端/构建工具 (Library only)",
            },
            source: {
              type: "string",
              enum: ["wechat", "chrome", "juejin", "book", "manual"],
            },
            limit: { type: "number", default: 8 },
          },
          required: ["query"],
        },
      },
      {
        name: "get_document",
        description:
          "Fetch a full document by permanent id (machine docs) or Vault-relative path (handwritten notes).",
        inputSchema: {
          type: "object",
          properties: {
            id: { type: "string" },
          },
          required: ["id"],
        },
      },
      {
        name: "list_topics",
        description: "List Library topics with document counts.",
        inputSchema: { type: "object", properties: {} },
      },
    ],
  }));

  server.setRequestHandler(CallToolRequestSchema, async (req) => {
    const name = req.params.name;
    const args = (req.params.arguments ?? {}) as Record<string, unknown>;

    if (name === "list_topics") {
      const topics = index.listTopics();
      return {
        content: [{ type: "text", text: JSON.stringify(topics, null, 2) }],
      };
    }

    if (name === "get_document") {
      const id = String(args.id ?? "");
      if (!id) {
        return {
          content: [{ type: "text", text: "Missing id" }],
          isError: true,
        };
      }
      let doc = index.getDocById(id);
      if (!doc && !id.includes(":")) {
        // try as path
        doc = index.getDocByPath(id);
      }
      if (!doc && id.startsWith("path:")) {
        doc = index.getDocByPath(id.slice("path:".length));
      }
      if (!doc) {
        return {
          content: [{ type: "text", text: `Document not found: ${id}` }],
          isError: true,
        };
      }
      const abs = join(config.vaultPath, doc.relPath);
      if (!existsSync(abs)) {
        return {
          content: [
            {
              type: "text",
              text: `Indexed path missing on disk: ${doc.relPath}`,
            },
          ],
          isError: true,
        };
      }
      const raw = readFileSync(abs, "utf8");
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(
              {
                id: doc.docId,
                path: doc.relPath,
                title: doc.title,
                source: doc.source,
                scope: doc.scope,
                obsidian: obsidianLink(config.vaultPath, doc.relPath),
                content: raw,
              },
              null,
              2,
            ),
          },
        ],
      };
    }

    if (name === "search_knowledge") {
      const query = String(args.query ?? "");
      if (!query) {
        return {
          content: [{ type: "text", text: "Missing query" }],
          isError: true,
        };
      }
      let limit = Number(args.limit ?? 8);
      if (!Number.isFinite(limit) || limit < 1) limit = 8;
      if (limit > MAX_LIMIT) limit = MAX_LIMIT;

      const { hits } = await hybridSearch(index, embedder, {
        query,
        limit,
        scope: (args.scope as Scope | "all" | undefined) ?? "all",
        topic: args.topic ? String(args.topic) : undefined,
        source: args.source ? String(args.source) : undefined,
      });

      const payload = hits.map((h) => ({
        text: truncate(h.text),
        heading_path: h.headingPath,
        doc_id: h.docId,
        title: h.title,
        source: h.source,
        scope: h.scope,
        score: h.score,
        path: h.relPath,
        obsidian: obsidianLink(config.vaultPath, h.relPath),
        vector_rank: h.vectorRank,
        bm25_rank: h.bm25Rank,
      }));

      return {
        content: [{ type: "text", text: JSON.stringify(payload, null, 2) }],
      };
    }

    return {
      content: [{ type: "text", text: `Unknown tool: ${name}` }],
      isError: true,
    };
  });

  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
});
