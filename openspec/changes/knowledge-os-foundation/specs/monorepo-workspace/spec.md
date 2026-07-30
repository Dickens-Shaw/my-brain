## ADDED Requirements

### Requirement: 仓库按语言划分顶层目录

仓库 SHALL 把 Python 导出线与 TypeScript 知识层放在互不重叠的顶层目录下。四条导出线位于 `exporters/<name>/`，TypeScript 包位于 `packages/`，可运行应用位于 `apps/`。`packages/` 与 `apps/` 下 MUST NOT 出现 Python 代码。

#### Scenario: 导出线搬迁后仍可独立运行

- **WHEN** 开发者在 `exporters/wechat-favorites/` 下执行 `bash run.sh`
- **THEN** 管线以搬迁前完全相同的行为运行，产出写入该目录下的 `output/`

#### Scenario: pnpm workspace 不收录 Python 目录

- **WHEN** 执行 `pnpm install`
- **THEN** workspace 只解析 `packages/*` 和 `apps/*`，`exporters/` 下不产生任何 `node_modules`

### Requirement: 导出线保持零第三方依赖

`exporters/` 下的 Python 代码 MUST 只使用标准库。本 change 引入的 npm 依赖 MUST NOT 被导出线依赖，导出线与 TypeScript 层之间只通过文件与 JSON 交互。

#### Scenario: 在没有 Node 的机器上运行导出线

- **WHEN** 一台只装了 Python 3 的机器克隆本仓库并运行任一导出线
- **THEN** 管线正常完成，不需要 `pnpm install` 或任何 npm 依赖

### Requirement: TypeScript 包职责单一且依赖单向

TypeScript 层 SHALL 划分为 `core`（领域模型与 schema）、`config`（配置与密钥读取）、`vault`（Vault 读写）、`ingest`（导入与迁移）、`retrieval`（切块、嵌入、存储、检索）五个包，以及 `apps/mcp` 一个应用。依赖方向 MUST 单向：`apps` 依赖 `packages`，`ingest` 与 `retrieval` 依赖 `vault`/`core`/`config`，`core` MUST NOT 依赖任何其他内部包。

本 change MUST NOT 创建 `apps/api`。后台 API 没有消费者之前不建骨架，它随后台 change 一起引入。

#### Scenario: 检测到循环依赖

- **WHEN** 某个包引入了会形成环的内部依赖
- **THEN** 构建失败并报出该环

#### Scenario: 检索实现只有一份

- **WHEN** 任何调用方（`apps/mcp`、命令行、后续新增的应用）对同一 query 发起检索
- **THEN** 都经由 `packages/retrieval` 的同一个函数，返回一致的结果，检索逻辑 MUST NOT 在调用方处重复实现

### Requirement: 文档路径与搬迁保持同步

搬迁 SHALL 一次性完成，`README.md`、`README.zh-CN.md`、各导出线自己的 README、`.gitignore` 以及 `openspec/config.yaml` 的 context 段 MUST 同步更新到新路径。

#### Scenario: 文档中不残留旧路径

- **WHEN** 在仓库中搜索 `-export/` 这一旧目录后缀
- **THEN** 除变更历史与本 change 的文档外，没有任何文件仍在引用旧路径

### Requirement: Vault 位置由配置指定且不进入本仓库

Vault SHALL 存放在本仓库之外的独立私有仓库，其绝对路径通过根目录 `.env` 的 `VAULT_PATH` 指定。本仓库 MUST NOT 包含任何 Vault 内容文件。

#### Scenario: 未配置 Vault 路径

- **WHEN** `VAULT_PATH` 缺失或指向不存在的目录
- **THEN** 任何需要读写 Vault 的命令启动即失败，并提示应在 `.env` 中配置该项

#### Scenario: 防止 Vault 内容被误提交

- **WHEN** 有人把 Vault 目录复制进本仓库工作区
- **THEN** `.gitignore` 使其不被跟踪
