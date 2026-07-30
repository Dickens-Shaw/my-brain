import { z } from "zod";

/** Document type for machine-written frontmatter. */
export const DocTypeSchema = z.enum(["article", "book-chapter", "note", "daily"]);
export type DocType = z.infer<typeof DocTypeSchema>;

/** Content source. */
export const SourceSchema = z.enum(["wechat", "chrome", "juejin", "book", "manual"]);
export type Source = z.infer<typeof SourceSchema>;

/** Index / search scope derived from Vault directory. */
export const ScopeSchema = z.enum([
  "library",
  "books",
  "notes",
  "projects",
  "daily",
  "inbox",
]);
export type Scope = z.infer<typeof ScopeSchema>;

/** Allocated id prefixes for machine-written docs. */
export const IdPrefixSchema = z.enum(["wx", "cb", "jj", "bk", "mn"]);
export type IdPrefix = z.infer<typeof IdPrefixSchema>;

/** Reserved for future import sources — must not be reassigned. */
export const RESERVED_ID_PREFIXES = ["pdf", "url", "gh", "rss"] as const;

export const SOURCE_ID_PREFIX: Record<Source, IdPrefix | null> = {
  wechat: "wx",
  chrome: "cb",
  juejin: "jj",
  book: "bk",
  manual: "mn",
};

/** `一级类目/二级类目` */
export const TopicPathSchema = z
  .string()
  .regex(/^[^/\s]+\/[^/\s]+$/, "topics item must be 一级类目/二级类目");
export type TopicPath = z.infer<typeof TopicPathSchema>;

const IdSchema = z
  .string()
  .regex(
    /^(wx|cb|jj|bk|mn)-\d{6}$/,
    "id must be <prefix>-<6-digit> e.g. wx-000001",
  );

/**
 * Frontmatter required for machine-written docs (Library / Books / Inbox).
 * Handwritten notes are not required to carry this schema.
 */
export const FrontmatterSchema = z
  .object({
    id: IdSchema,
    title: z.string().min(1),
    type: DocTypeSchema,
    source: SourceSchema,
    source_url: z.string().optional(),
    published_at: z.string().optional(),
    saved_at: z.string().optional(),
    topics: z.array(TopicPathSchema).default([]),
    tags: z.array(z.string()).default([]),
    summary: z.string().optional(),
    lang: z.string().optional(),
    imported_at: z.string().optional(),
    author: z.string().optional(),
    publisher: z.string().optional(),
    book: z.string().optional(),
    chapter: z.union([z.number(), z.string()]).optional(),
    chapter_total: z.number().optional(),
    origin: z.record(z.unknown()).optional(),
  })
  .superRefine((val, ctx) => {
    if (val.type === "book-chapter") {
      for (const key of ["book", "chapter", "chapter_total"] as const) {
        if (val[key] === undefined) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            message: `book-chapter requires ${key}`,
            path: [key],
          });
        }
      }
    }
  });

export type Frontmatter = z.infer<typeof FrontmatterSchema>;
