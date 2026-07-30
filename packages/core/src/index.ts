export {
  DocTypeSchema,
  SourceSchema,
  ScopeSchema,
  IdPrefixSchema,
  SOURCE_ID_PREFIX,
  RESERVED_ID_PREFIXES,
  TopicPathSchema,
  FrontmatterSchema,
  type DocType,
  type Source,
  type Scope,
  type IdPrefix,
  type TopicPath,
  type Frontmatter,
} from "./schema.js";

export {
  embeddingSupportsDimensions,
  assertEmbeddingDimensions,
} from "./embedding.js";
