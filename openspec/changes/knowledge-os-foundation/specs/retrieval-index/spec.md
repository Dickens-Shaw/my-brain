## ADDED Requirements

### Requirement: 索引完全可重建

索引 SHALL 存放在单个 SQLite 文件中，且 MUST 可以在任何时候删除并从 Vault 完整重建。索引文件 MUST NOT 被提交进任何仓库。

#### Scenario: 全量重建

- **WHEN** 删除索引文件并执行全量索引
- **THEN** 重建成功，文档数与 Vault 中的 Markdown 数量一致

#### Scenario: 全量重建耗时可接受

- **WHEN** 对约 4900 篇文档执行全量重建
- **THEN** 在 30 分钟内完成

### Requirement: 索引范围按目录划定

索引 SHALL 覆盖 `Library/`、`Books/`、`Notes/`、`Projects/`、`Daily/`、`Inbox/`，MUST 排除 `Maps/` 与 `Templates/`。`Maps/` 是生成的主题索引页，正文只有标题清单与链接，索引它会让检索结果被目录页占据。

#### Scenario: 生成的索引页不进检索结果

- **WHEN** 用某个主题名检索，该主题恰好有对应的 Maps 页
- **THEN** 返回的是该主题下的文档片段，Maps 页本身不出现在结果中

#### Scenario: 手写笔记可被检索

- **WHEN** 用户在 `Notes/` 下写了一篇概念笔记并重新索引
- **THEN** 该笔记的片段可以被检索到，与 `Library/` 的文档一同参与召回与融合

### Requirement: 手写区以路径为身份

`Library/`、`Books/`、`Inbox/` 下的文档 SHALL 以 frontmatter 中的 `id` 为身份。`Notes/`、`Projects/`、`Daily/` 下的文件不强制带 frontmatter，索引 SHALL 以其 Vault 内相对路径为身份；重命名或移动视为删除加新增。索引流程 MUST NOT 为补齐 `id` 而写入手写区文件。

#### Scenario: 手写笔记没有 frontmatter

- **WHEN** 索引一篇只有正文、没有任何 frontmatter 的手写笔记
- **THEN** 正常切块与嵌入，以相对路径为键，不报错，也不修改该文件

#### Scenario: 重命名手写笔记

- **WHEN** 用户重命名一篇手写笔记后重新索引
- **THEN** 旧路径的 chunk 被移除、新路径的 chunk 被建立，检索结果指向新路径

### Requirement: 按 Markdown AST 切 chunk

切块 SHALL 解析 Markdown AST，以标题层级为边界，一个 section 一个 chunk。超过 1200 token 的 section MUST 按段落二次切分并保留约 200 token 重叠；不足 100 token 的 chunk MUST 向后合并。代码块 MUST NOT 被切开。

#### Scenario: 代码块完整

- **WHEN** 一个 section 中含有超长代码块
- **THEN** 该代码块完整落在同一个 chunk 中

#### Scenario: 超长章节二次切分

- **WHEN** 一个 section 的正文远超 1200 token
- **THEN** 它被切成多个带重叠的 chunk，且都保留同一个标题路径

### Requirement: chunk 携带上下文头

每个 chunk SHALL 记录其标题路径（文档标题加各级标题，如 `《书名》> 二级标题 > 三级标题`）。生成 embedding 时 MUST 把标题路径拼接在正文之前一起编码；返回给调用方时 MUST 单独提供该字段。

#### Scenario: 孤立片段仍可被检索到

- **WHEN** 某个 chunk 正文中未出现主题词，但其标题路径中出现了
- **THEN** 以该主题词检索能召回这个 chunk

### Requirement: 增量索引按内容哈希判定

索引 SHALL 记录每篇文档的内容哈希。增量索引时 MUST 只处理新增、哈希变化与已删除的文档，未变化的文档 MUST NOT 重新调用 embedding 接口。

#### Scenario: 只改了一篇文档

- **WHEN** 修改一篇文档后执行增量索引
- **THEN** 只有这一篇被重新切块与嵌入，其余文档不产生接口调用

#### Scenario: 只改了 frontmatter 中的主题

- **WHEN** 一篇文档只有 `topics` 变化
- **THEN** 索引中的主题元数据被更新，向量不重新计算

### Requirement: 向量与全文索引共存于同一数据库

存储 SHALL 使用 SQLite，向量存于 `vec0` 虚拟表，全文存于 FTS5 表。`vec0` 表 MUST 带 `doc_id`、`source`、`scope`、`primary_topic` 元数据列以支持在 KNN 查询内直接过滤，`scope` 取值为 `library`、`books`、`notes`、`projects`、`daily`、`inbox`。由于 `vec0` 的元数据列不支持数组，`primary_topic` 只存 `topics` 首项，主题过滤依据主主题进行；只有 `Library/` 下的文档经过主题分类，其余 scope 的 `primary_topic` 为空。FTS5 表 MUST 使用能正确处理中文的分词器（首选内置 `trigram`）。

#### Scenario: 带主题过滤的向量检索

- **WHEN** 检索时限定主题为 `前端/构建工具`
- **THEN** 过滤在 KNN 查询内完成，返回的 chunk 全部来自主主题为该类目的文档

#### Scenario: 主题过滤的已知边界

- **WHEN** 某文档以 `前端/构建工具` 为次要主题、以其他类目为主主题
- **THEN** 按 `前端/构建工具` 过滤时它不会被召回；不加主题过滤时可以正常召回

#### Scenario: 主题过滤只覆盖已分类的文档

- **WHEN** 检索时指定了主题
- **THEN** 只有 `Library/` 下的文档参与召回，小册与手写区因未经主题分类而被排除；需要检索它们时改用 scope 过滤或不加过滤

#### Scenario: 中文全文检索有效

- **WHEN** 用一个中文词组做纯全文检索
- **THEN** 返回包含该词组的 chunk，而不是空结果

### Requirement: embedding 供应商可替换

嵌入模型与维度 SHALL 由配置（`EMBEDDING_MODEL` / `EMBEDDING_DIMENSIONS`）决定，实现只要求供应商提供 OpenAI 兼容的 `/embeddings` 接口。`dimensions` 请求参数 MUST 仅在模型支持 matryoshka 降维时发送（OpenAI `text-embedding-3-*` 系列），其余供应商 MUST NOT 携带该字段。

`vec0` 表在建表时固定向量宽度，维度配错会污染整张表，因此实现 MUST 在收到首批向量后校验其实际维度与配置一致，不一致时立即报错并提示改配置加重建索引。

聊天模型（主题命名与边界分类）SHALL 支持独立于 embedding 的供应商配置，未单独设置时回落到 embedding 的配置。

#### Scenario: 换用非 OpenAI 的嵌入供应商

- **WHEN** 配置指向一个不支持 `dimensions` 参数的 OpenAI 兼容供应商
- **THEN** 请求体中不含 `dimensions`，嵌入正常返回

#### Scenario: 维度配置与模型不符

- **WHEN** 配置的维度与模型实际返回的维度不一致
- **THEN** 首批向量返回时即报错，指出实际维度并提示重建索引，MUST NOT 写入任何向量

#### Scenario: chat 与 embedding 分属不同供应商

- **WHEN** 只配置了 embedding 供应商，未单独配置 chat
- **THEN** chat 回落到 embedding 的 key 与 base URL；反之若单独配置了 chat，则两者各走各的

### Requirement: 双字中文词的全文召回

全文召回 SHALL 对长度不足 3 个字符的查询词同样有效。`trigram` 分词器无法索引短于 3 字符的内容，而「前端」「缓存」「组件」这类双字词是中文检索的主力，若直接交给 `MATCH` 会稳定返回空结果，使混合召回在中文查询下退化为纯向量召回。实现 MUST 将查询切分为词，对短词改走 `trigram` 索引可加速的 `LIKE` 路径，并与 `MATCH` 路径的结果合并。两路分数量纲不同（BM25 与词频），合并 MUST 依据各自的排名位次而非原始分数。

#### Scenario: 双字中文词能召回

- **WHEN** 以「前端」这样的双字中文词检索
- **THEN** BM25 一路返回非空结果，且命中文档确实包含该词

#### Scenario: 中英混合查询两路都生效

- **WHEN** 查询同时包含长词与双字中文词
- **THEN** 两条路径的结果都参与召回，任一路为空不影响另一路

### Requirement: 混合召回与融合

检索 SHALL 同时执行向量召回与 BM25 召回，各取前 50 条，用 RRF 融合后返回前 10 条。返回的每条结果 MUST 包含 chunk 正文、标题路径、所属文档 id 与标题、来源、融合分数，以及在两路召回中各自的排名。

#### Scenario: 一路召回为空

- **WHEN** BM25 召回没有任何结果
- **THEN** 检索仍返回向量召回的结果，不报错

#### Scenario: 结果可溯源

- **WHEN** 任意一次检索返回结果
- **THEN** 每条结果都能定位到 Vault 中的具体文件与标题位置

### Requirement: 检索过程全量留痕

每次检索 SHALL 把 query、两路召回各自的结果、融合后的排序、各阶段耗时写入日志表，供调试查看。留痕 MUST NOT 显著拖慢检索。

#### Scenario: 排查未命中

- **WHEN** 某次检索没有返回预期的文档
- **THEN** 通过日志能看出该文档是未被任一路召回，还是被召回后在融合中排到了截断线之外

### Requirement: 以黄金问题集衡量检索质量

仓库 SHALL 维护一组不少于 20 条的黄金问题，每条标注预期命中的文档 id。SHALL 提供评测命令，输出 top5 与 top10 命中率。调整切块、嵌入或融合策略后 MUST 重新评测。

#### Scenario: 评测给出可比数字

- **WHEN** 执行评测命令
- **THEN** 输出整体 top5、top10 命中率与逐条明细，未命中的问题被单独列出

#### Scenario: 达标线

- **WHEN** 在完整语料上执行评测
- **THEN** top5 命中率不低于 80%，否则视为本 change 未达成
