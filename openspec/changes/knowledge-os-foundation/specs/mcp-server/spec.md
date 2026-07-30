## ADDED Requirements

### Requirement: 以 stdio 传输提供 MCP Server

系统 SHALL 提供一个通过 stdio 传输的 MCP Server，可由 Cursor 等 MCP 客户端直接拉起。启动 MUST NOT 依赖任何常驻后台服务，只需索引文件存在。

#### Scenario: Cursor 拉起服务

- **WHEN** Cursor 依据配置启动该 MCP Server
- **THEN** 服务在客户端超时之前完成握手并列出可用工具

#### Scenario: 索引文件缺失

- **WHEN** 索引文件不存在时启动服务
- **THEN** 服务返回明确的错误说明需要先建索引，而不是崩溃或返回空结果

### Requirement: 只暴露三个工具

MCP Server SHALL 只注册三个工具：`search_knowledge`、`get_document`、`list_topics`。每个工具 MUST 有描述与参数 schema。

`search_knowledge` 接受 `query` 及可选的 `scope`、`topic`、`source`、`limit`（默认 8），返回片段列表，每条含正文、标题路径、文档 id 或路径、文档标题、来源、分数，以及一个 `obsidian://` 链接。`scope` 取值为 `library`、`books`、`notes`、`projects`、`all`（默认 `all`）——`Notes/` 与 `Projects/` 的 `source` 同为 `manual`，用 `source` 无法区分，而「查资料」与「回顾自己写过什么」是两种不同的检索意图。
`get_document` 接受 `id`（手写区文档接受 Vault 内相对路径），返回该文档的完整正文与 frontmatter。
`list_topics` 无参数，返回主题列表及各自的文档数量。

#### Scenario: 检索并展开

- **WHEN** 客户端先调用 `search_knowledge` 得到片段，再用其中的文档 id 调用 `get_document`
- **THEN** 返回该文档全文，id 与片段中标注的一致

#### Scenario: 按主题过滤

- **WHEN** 调用 `search_knowledge` 并指定 `topic`
- **THEN** 返回的片段全部来自该主题下 `Library/` 的文档；小册与手写区未经主题分类，不参与本次召回

#### Scenario: 只搜自己写的笔记

- **WHEN** 调用 `search_knowledge` 并指定 `scope` 为 `notes`
- **THEN** 返回的片段全部来自 `Notes/`，导入的文章与小册不出现在结果中

#### Scenario: 未知 id

- **WHEN** `get_document` 收到一个不存在的 id
- **THEN** 返回明确的未找到错误，不返回空文档

### Requirement: 返回内容适配上下文窗口

`search_knowledge` 的返回 SHALL 控制体量：单条片段超出长度上限时截断并标注，`limit` MUST 有上限以防单次返回过多内容。返回结构 MUST 让模型能直接引用来源。

#### Scenario: 超大 limit

- **WHEN** 客户端传入远超上限的 `limit`
- **THEN** 服务按上限截断并正常返回，不报错

#### Scenario: 片段可被引用

- **WHEN** 模型基于返回结果作答
- **THEN** 每条片段都带有可用于引用的文档标题与来源链接

#### Scenario: 从检索结果跳进 Obsidian

- **WHEN** 用户在 MCP 客户端里看到某条片段并点击其 `obsidian://` 链接
- **THEN** Obsidian 打开 Vault 中对应的那篇文档
