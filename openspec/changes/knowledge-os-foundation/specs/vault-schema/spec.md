## ADDED Requirements

### Requirement: Vault 是唯一事实来源

Vault 中的 Markdown 文件 SHALL 是知识内容的唯一权威副本。索引层 MUST NOT 存储任何无法从 Vault 重新推导出的内容。删除索引后，重建 MUST 能得到与删除前等价的检索结果。

#### Scenario: 删除索引后完全重建

- **WHEN** 删除 `index.db` 并重新执行全量索引
- **THEN** 重建后的文档数、chunk 数与同一组查询的命中结果，与删除前一致

#### Scenario: 索引层不得反向写入内容

- **WHEN** 索引或检索流程运行
- **THEN** 除生成产物外不修改 Vault 中任何 Markdown 的正文

### Requirement: Vault 顶层目录按读写归属划分

Vault SHALL 采用固定的顶层结构，并按「机器维护」与「手写」两类区域划分：

- 机器维护区：`Inbox/`（新导入未分类的暂存区）、`Library/`（导入的文章，按主题两级分类）、`Books/`（小册，保持册内章节顺序）、`Maps/`（生成的主题索引页）
- 手写区：`Notes/`（概念笔记，一个概念一篇）、`Projects/`（项目上下文）、`Daily/`（日记）、`Templates/`
- `Assets/`：只服务手写区

机器写入 MUST 只发生在 `Inbox/`、`Library/`、`Books/`、`Maps/` 四处。

#### Scenario: 手写区不被机器改动

- **WHEN** 重新运行迁移或索引流程
- **THEN** `Notes/`、`Projects/`、`Daily/`、`Templates/` 下的文件不被创建、修改或删除

#### Scenario: 资料与笔记互不混杂

- **WHEN** 用户浏览 `Notes/` 下关于某个概念的笔记
- **THEN** 该目录只有自己写的笔记，导入的原文全部在 `Library/`，两者仅通过链接关联

#### Scenario: 小册保持整册连续

- **WHEN** 一册小册被写入 Vault
- **THEN** 该册全部章节位于 `Books/<册名>/` 下，文件名带零填充的章节序号，章节不因主题不同而被拆散

### Requirement: 统一的 frontmatter schema

每个机器写入的 Markdown 文件 MUST 带 YAML frontmatter，包含以下字段：`id`、`title`、`type`、`source`、`source_url`、`published_at`、`saved_at`、`topics`、`tags`、`summary`、`lang`、`imported_at`。`author` 与 `publisher` 在原始数据缺失时可以省略。来源特有的字段 MUST 收进 `origin` 子对象，MUST NOT 平铺在顶层。

手写区的文件不受此要求约束（见「id 永久且全局唯一」）；`note`、`daily`、`manual` 这几个取值供用户自愿在手写文件中标注时使用，校验时以「若存在则必须合法」的方式检查，而非要求必须存在。

`type` 取值限定为 `article`、`book-chapter`、`note`、`daily`。`source` 取值限定为 `wechat`、`chrome`、`juejin`、`book`、`manual`。`book-chapter` 类型 MUST 额外带 `book`、`chapter`、`chapter_total`。

`topics` 的每一项 MUST 是 `一级类目/二级类目` 形式的完整路径（例如 `前端/构建工具`），而不是分散的类目名。数组首项为主主题。

#### Scenario: frontmatter 校验

- **WHEN** 校验命令扫描整个 Vault
- **THEN** 任何缺少必填字段、字段类型不符或枚举取值非法的文件都被列出，退出码非零

#### Scenario: 来源特有字段不污染顶层

- **WHEN** 一篇微信来源的文档写入 Vault
- **THEN** `favorite_id` 出现在 `origin` 下，顶层字段集与其他来源的文档完全一致

### Requirement: 时间字段在读写往返中保持稳定

`published_at`、`saved_at`、`imported_at` 在 schema 中是字符串，但 YAML 解析器会把 `2024-01-15` 与带时区的时间戳自动转成日期对象。frontmatter 解析 MUST 在交给 schema 校验前把日期对象转回字符串，且纯日期 MUST 仍为 `YYYY-MM-DD` 而非被展开成完整时间戳——否则每次读写都会改写文件内容，破坏幂等与基于内容哈希的增量索引。

#### Scenario: 解析后校验不因日期失败

- **WHEN** 读取一篇 `published_at: 2024-01-15` 的文档并做 schema 校验
- **THEN** 校验通过，该文档正常进入索引，而不是被当作非法 frontmatter 跳过

#### Scenario: 读写往返不改写文件

- **WHEN** 读取一篇文档再原样写回
- **THEN** 文件内容逐字节不变

### Requirement: id 永久且全局唯一

每个机器写入的文档 SHALL 拥有一个永不变更的 `id`，格式为 `<来源前缀>-<六位零填充序号>`，来源前缀为 `wx`、`cb`、`jj`、`bk`、`mn` 之一。文件被移动、重命名或重新分类时 `id` MUST 保持不变。对这些文档，索引、检索引用、去重与跨文档链接 MUST 以 `id` 而非路径为键。

手写区（`Notes/`、`Projects/`、`Daily/`）的文件 MUST NOT 被要求带 `id`，改以 Vault 内相对路径为身份——它们不会被机器重新分类，因而不存在「移动后引用失效」的问题，而强制带 `id` 会迫使索引流程反向写入手写区，违反上述边界。用户若想要稳定 `id`，可在 `Templates/` 中放置带该字段的模板，属于用户自愿而非系统强制。

#### Scenario: 手写笔记没有 id

- **WHEN** 校验命令扫描到一篇 `Notes/` 下没有 frontmatter 的笔记
- **THEN** 不报错、不写入该文件，该笔记以相对路径参与索引

#### Scenario: 重新分类后引用仍然有效

- **WHEN** 一篇文档的 `topics` 被修改并因此移动到另一个主题目录
- **THEN** 其 `id` 不变，重新索引后既有的检索引用仍指向同一篇文档

#### Scenario: id 冲突

- **WHEN** 写入流程尝试分配一个已存在的 `id`
- **THEN** 写入中止并报错，不覆盖既有文件

### Requirement: 来源枚举与 id 前缀可扩展

接入新的导入来源时 SHALL 向 `source` 枚举与 id 前缀表各追加一项，MUST NOT 重排或改写任何已分配的 id。后续来源的前缀预留为：PDF 用 `pdf`、网页 URL 用 `url`、GitHub 仓库用 `gh`、RSS 用 `rss`。

#### Scenario: 接入新来源不影响既有 id

- **WHEN** 后续 change 接入 PDF 导入并从 `pdf-000001` 开始分配 id
- **THEN** 既有的 `wx-*`、`cb-*`、`jj-*`、`bk-*`、`mn-*` 全部保持不变，索引与既有引用不受影响

### Requirement: 维护 id 到路径的映射

系统 SHALL 维护一份 `id → 当前路径 + 内容哈希` 的映射，存放在索引数据库中。扫描机器维护区时，MUST 依据文件 frontmatter 中的 `id` 更新映射，而不是依赖路径推断身份。手写区的文件以相对路径作为映射键。

#### Scenario: 在 Obsidian 中手工移动文件

- **WHEN** 用户在 Obsidian 里把一篇文档拖到另一个目录，随后重新扫描
- **THEN** 映射中该 `id` 的路径被更新，文档不会被当作新增，也不会留下失效条目

#### Scenario: 文档被删除

- **WHEN** 一篇文档从 Vault 中删除后重新扫描
- **THEN** 其 chunk 与向量从索引中移除，映射中的对应条目被清除

### Requirement: 主题目录由 topics 首项决定

文档的物理目录 SHALL 由 `topics` 数组的第一项（主主题）决定：路径为 `Library/<一级类目>/<二级类目>/`。其余主题只体现在 frontmatter 与 Maps 索引页中。SHALL 为每个二级类目生成 `Maps/<一级类目>/<二级类目>.md`，列出全部以该类目为任一主题的文档，包含主主题为其他类目的文档。

#### Scenario: 一篇文档属于多个主题

- **WHEN** 一篇文档的 `topics` 为 `[前端/构建工具, 前端/性能优化]`
- **THEN** 文件落在 `Library/前端/构建工具/` 下，同时出现在 `Maps/前端/构建工具.md` 与 `Maps/前端/性能优化.md` 两个索引页中

#### Scenario: Maps 可重新生成

- **WHEN** 删除 `Maps/` 后重新生成
- **THEN** 内容与删除前一致，生成过程只读取各文档的 frontmatter

### Requirement: 配图随文档存放

导入文档引用的图片 SHALL 存放在其所在目录的 `assets/` 子目录中，通过相对路径引用，MUST NOT 集中存放到中央目录——上万张配图落进单个扁平目录会拖慢 Obsidian 索引，且删除文档时无法判断哪些图已无人引用。中央 `Assets/` 目录（`images/`、`pdf/`、`attachments/`）只服务手写区。所有图片引用 MUST 在 Obsidian 中可正常显示。

#### Scenario: 检查失效图片引用

- **WHEN** 校验命令扫描全部图片引用
- **THEN** 指向不存在文件的引用被逐条列出
