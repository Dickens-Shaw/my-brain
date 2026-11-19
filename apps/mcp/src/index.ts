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
          "Hybrid search over the personal knowledge Vault (Library, Books, Notes, etc.). Returns snippets with Obsidian deep links.\n\n" +
          "How to search well:\n" +
          "- Use specific words: a person's name, an API, a command, a product name. Vague questions retrieve vague results.\n" +
          "- Chinese queries work best with 2+ character terms.\n" +
          "- If nothing relevant comes back, retry with a synonym, the English term, or a more concrete phrase before concluding the Vault does not cover it.\n" +
          "- Break a complex question into separate searches for each part.\n" +
          "- Use the `topic` filter to narrow to a subject area, and `exclude_doc_ids` to skip documents already returned in an earlier round.",
        inputSchema: {
          type: "object",
          properties: {
            query: { type: "string" },
            scope: {
              type: "string",
              enum: [
                "library",
                "books",
                "notes",
                "projects",
                "daily",
                "inbox",
                "all",
              ],
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
            exclude_doc_ids: {
              type: "array",
              items: { type: "string" },
              description:
                "Document ids to exclude. Use the doc_id values already returned to avoid repeating the same documents across follow-up searches.",
            },
          },
          required: ["query"],
        },
      },
      {
        name: "get_document",
        description:
          "Fetch a document by permanent id (machine docs) or Vault-relative path (handwritten notes). " +
          "Long documents are returned in pages: pass `offset` to continue reading, and `chunk_id` to fetch one specific section.",
        inputSchema: {
          type: "object",
          properties: {
            id: { type: "string" },
            offset: {
              type: "number",
              description: "Character offset to start from when reading a long document.",
            },
            limit: {
              type: "number",
              description:
                "Maximum characters to return. Defaults to 8000. Read the rest with offset.",
            },
            chunk_id: {
              type: "string",
              description: "Fetch only this chunk of the document.",
            },
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

      // A long document would blow up the context window, so it is returned
      // in pages. `chunk_id` fetches one section; `offset`/`limit` walk
      // through the text.
      const chunkId = args.chunk_id ? String(args.chunk_id) : undefined;
      const offset = Number(args.offset ?? 0);
      const limit = Number(args.limit ?? 8000);

      let content: string;
      let truncated = false;
      let nextOffset: number | undefined;

      if (chunkId) {
        const chunk = index.getChunks([chunkId]).get(chunkId);
        content = chunk?.text ?? `Chunk not found: ${chunkId}`;
      } else {
        const bodyStart = raw.indexOf("---", 3);
        const body =
          bodyStart >= 0 ? raw.slice(bodyStart).replace(/^---[\s\S]*?\n---\n/, "") : raw;
        const safeOffset = Number.isFinite(offset) && offset > 0 ? offset : 0;
        const safeLimit = Number.isFinite(limit) && limit > 0 ? Math.min(limit, 20000) : 8000;
        content = body.slice(safeOffset, safeOffset + safeLimit);
        if (safeOffset + safeLimit < body.length) {
          truncated = true;
          nextOffset = safeOffset + safeLimit;
        }
      }

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
                content,
                truncated,
                next_offset: nextOffset,
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
        excludeDocIds: Array.isArray(args.exclude_doc_ids)
          ? (args.exclude_doc_ids as unknown[]).map(String)
          : undefined,
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
