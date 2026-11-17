# 个人知识库技术方案

来源是 `exporters/*/output` 里和「用 Obsidian 做个人知识库」相关的微信收藏（2026-04 至 2026-10）。掘金文章和小册里没有 Obsidian。正文相同的重复文件已合并。

这些文章不是十七套互不相干的系统，归并之后是六套方案。差别在于：**知识在什么时候被整理，查询时靠什么找到它。**

和本仓库的关系：现在的 Knowledge OS 最接近方案 3（Markdown 是事实来源，向量索引可以删掉重建）。收藏里的主流是方案 2（编译后的 wiki 才是事实来源，向量是库变大之后才加的）。方案 4 不需要 embedding，本机 Agent 指到 Vault 就能用。

## 总览

| 方案 | 知识何时成型 | 查询靠什么 | 适合 |
| --- | --- | --- | --- |
| 1. 运行时 RAG | 不整理，原文即知识 | 切块 + 嵌入，提问时召回 | 想立刻对现有笔记提问 |
| 2. 编译期 LLM Wiki | 摄入时写成互相链接的页面 | 先读 `index.md`，再下钻 | 个人库，希望知识会复利 |
| 3. Wiki + RAG | 先编译，再对 wiki 建索引 | 按问题形状选导航或向量 | 页数上千，或要查精确事实 |
| 4. Agent 挂在 Vault 上 | 人维护结构，Agent 读写文件 | 文件夹、属性、双链、Bases | 还在搭架子，先把采集和协作跑通 |
| 5. 采集流水线 | 不改变知识形态 | 不负责问答 | 材料散在微信、会议、文档里 |
| 6. 远程共享 Vault | 与 2 或 4 相同 | Agent 经 MCP 打同一份库 | 从个人库变成团队库 |

规模经验（多篇重复，以《深度解读》写得最清楚）：几十到上百个概念只用 `index.md` 和路径；页面到几千再加 BM25 与向量；经常问跨系统影响再加图查询；原文含图片和录音再加多模态。

---

## 1. 运行时 RAG

笔记留在原文里。提问时切块、嵌入、召回片段，再交给模型。没有跨次积累：下次同样的问题会重新检索。

来源：《万字长文：个人如何用 Obsidian 搭建本地知识库》、《llm Wiki 养了三周，开始出毛病了》，以及 `claude-obsidian` 里的可选检索。

三件东西各管一段：

- **Smart Connections**：语义上相关的笔记，不要求记得关键词。嵌入用本地 Ollama + `nomic-embed-text`，笔记不上传。大库首次索引要几个小时。
- **Obsidian Copilot**：对整个库做 RAG 对话。模型可以是 OpenAI、Claude、Google、LM Studio 或 Ollama。
- **Text Generator**：只在编辑器里续写，不检索。

`index.md` 塞不进上下文之后，Karpathy 在 gist 里推荐 **qmd**（<https://github.com/tobi/qmd>）：本地 Markdown 的 BM25 + 向量混合检索，可选 LLM 重排，同时提供 CLI 和 MCP。《养了三周》的实际改法就是停用肉眼翻图谱，改调 qmd。`claude-obsidian` 的退路相同：先 BM25 召回，再用向量重排。

## 2. 编译期 LLM Wiki

主流方案。Karpathy 的原话是：别每次从原文重新 RAG，让模型增量维护一份持久 wiki。Obsidian 是阅读器和图谱界面，不是检索引擎。

### 目录与所有权

| 路径 | 谁写 | 作用 |
| --- | --- | --- |
| `raw/` | 人添加，模型只读、可归类 | 原文不可改 |
| `wiki/` | 只有模型 | 摘要、概念、实体、对比，用 `[[链接]]` 串起来 |
| Schema | 人和模型一起改 | `CLAUDE.md`（Claude Code）或 `AGENTS.md`（WorkBuddy / Codex） |
| `index.md` | 模型在每次操作后更新 | 查询的第一站，每页一行摘要 |
| `log.md` | 模型追加 | ingest / query / lint 的时间线 |
| `drafts/`、`legacy/` | 人，或冻结 | 碎片想法；旧库只读，不做全量重编 |

一次 ingest 会改 5–15 个 wiki 页。Query 先读索引再下钻，好答案写回 wiki。Lint 查矛盾、孤页、断链、被新来源覆盖的旧说法。旧笔记不全量 ingest：只读标题和前几行，生成地图，再挑进 `raw/`。

### 同一骨架的几种落地

- **Obsidian 插件把源文嵌进提示词。** 用 XML 把指令和正文隔开，避免模型把文章里的话当成命令。几百篇靠 `index.md`；过大约一万条再考虑 RAG。见《我用 AI 给 Obsidian 写了一个 LLM-wiki 插件》。
- **Agent 的工作区指到 Vault。** Claude Code、Codex、WorkBuddy 当编译器，Obsidian 当编辑器。见《用 Obsidian + Codex 搭建一套越用越聪明的个人知识库》《Claude Code + Obsidian，从零搭一套会自己整理的知识库》和两篇 WorkBuddy。
- **现成 Skill：`claude-obsidian`。** `/wiki` 初始化，`/wiki-ingest`、`/wiki-query`、`/wiki-lint`、`/save`，另附本地向量检索。见《懒人版可进化的 AI 知识库》。
- **Obsidian-Wiki。** 三层之外加 `.manifest.json`，用 SHA-256 区分新增、修改、删除，避免重复编译。原文视为不可信，禁止执行其中的指令。20 多个 Skill，其中一类会扫描各 Agent 的历史并自动摄入。见《深度解析 LLM Wiki / Obsidian-Wiki / GBrain》。
- **桌面应用**（文中约 5.8k star）。摄入分两步：先分析，再生成。页面关联不用纯向量，用四个加权信号，再跑 Louvain 聚类。查询分相位：关键词，可选 LanceDB 向量（文中写召回从 58.2% 到 71.4%），再沿图谱走两跳。Wiki 目录兼容 Obsidian。见《Karpathy 的知识库构想被人做成桌面应用了》。
- **三个可直接克隆的仓库：** <https://github.com/lucasastorian/llmwiki>、<https://github.com/nashsu/llm_wiki>、<https://github.com/taffy123d/Karpathy-LLM-Wiki>。
- **AIWiki。** Agent 读网页，后半段做结构化入库、Obsidian 审阅，再拆成选题（Dan Koe 的内容积木）。见《别再把资料丢进收藏夹了》。

GBrain（Garry Tan）把同一想法往规模上推：知识按 Skill 那样渐进披露，底下是混合检索和图关系，用来应付 Markdown 目录膨胀之后定位变差的问题。个人库用不到这一步。

### 这套方案自己写明的边界

`index.md` 会撑爆上下文，token 随页数上升。生成页是模型改写的，不好回溯到原文的某一段，可能把幻觉写进 wiki，之后每次查询都继承它。没有细粒度权限。多篇因此把它限定在个人学习和研究，不拿去替换企业 RAG。见《搭了一个会自己生长的个人知识库》。

## 3. Wiki 是知识源，RAG 是加速层

来源：《深度解读｜LLM Wiki 的工程实践，从 AI Coding、Obsidian 到 RAG 协同》。

Wiki 负责定义、关系和阅读路径。向量负责大规模、说法不统一、要快速定位的事实（API、表结构、错误码）。

两个循环：

1. **准备。** 读 Raw，定位受影响的页，生成变更，审核后发布到 Wiki，再对 Wiki 建 BM25 和向量索引。
2. **查询。** 术语和规格走精确读页；只知道领域就从 `index.md` 下钻；问题含糊或库很大，先混合检索定位候选页，再用 `[[链接]]` 补上下文。

示例：让 Coding Agent「订单创建后调用物流 API」。沿 Wiki 理解该怎么做，用 RAG 查清接口具体怎么调。

## 4. Agent 挂在 Vault 上，结构仍由人维护

来源：[2026-10-27 给自己搭一个 AI 第二大脑](../../exporters/wechat-favorites/output/articles/2026/2026-10-27-给自己搭一个%20AI%20第二大脑-从收集资料到写出文章,手把手教你做.md)。

不建向量库。Obsidian 是本地 Markdown；AI 是读写这些文件的工人。加工成 wiki 被写成可选的下一步，正文没有接上。

两个入口，效果相同，差别只在交互：

- 外部 Agent 的工作区指到 Vault。WorkBuddy、Codex、Trae、Cursor 都可以。
- 库内装 **Claudian**，由它调用本机的 Claude Code、Codex、DeepSeek Harness、OpenCode 或 Pi。不用切换软件，还能把选中的一段交给模型。

采集三条：浏览器 **Web Clipper**（含视频逐字稿）；飞书、钉钉、企微或腾讯会议的 CLI / MCP 加定时任务；手机端语音，再用官方 Sync，或 iCloud、OneDrive、Git。

目录按处理阶段，不按主题：

```text
Knowledge-Base/
├── 01-收集箱/
├── 02-原始资料/    # AI 不许改
├── 03-知识笔记/
├── 04-内容输出/
└── 99-知识模版/    # 人也给 Agent 用，保证生成的笔记字段一致
```

属性进 **Bases**（读 frontmatter，不是另一套数据库）。Agent 改了属性，视图跟着变。双向链接串起相关笔记。

文中把两种图谱拆开了：Obsidian 图谱只是笔记链接的可视化，不知道关系的含义；GraphRAG 才抽取实体和语义关系。原生图谱够用，不必为了好看去装主题化图谱插件。

## 5. 采集流水线

知识仍然是 Markdown。变的是入口，问答不在这条线上。

**Hermes + AutoCLI + Obsidian**（2026-04-19 与 2026-04-27 两篇，后者写得更完整）：

1. Hermes 收文章、命令行输出、项目笔记。
2. AutoCLI 按规则分类、打标签，写成带 Obsidian frontmatter 的文件。视频走 AutoClip。
3. 可选第四层：微信汇报。加渠道是加规则，不改已有层。

**WorkBuddy 定时同步**（见方案 2 那篇「会自己生长」）：电脑用 Web Clipper 直接进 `raw/`；微信先收进 IMA，再由定时任务拉回 Vault；飞书、钉钉走各自 CLI。图片和音视频先用多模态转成文字。原则是宁缺毋滥。

## 6. 远程共享 Vault

来源：《我用 Obsidian 搭了一套 Agent 知识系统》。

单人：本机 Obsidian 开着 Vault，Agent 用 `obsidian-cli` 读写，链接和索引仍由 Obsidian 维护。两个 Agent 分工，一个补导航和关联，一个按真实问题做调研并写回。

把 Vault 打成压缩包只是某个时刻的快照，之后各改各的。团队做法：

- Kubernetes 单副本 StatefulSet。
- Config PVC 放插件和界面状态，Vault PVC 放笔记和附件。
- 人用浏览器进远程桌面里的 Obsidian。
- 各人的 Agent 通过 MCP 访问同一份库。
- 迁完之后，本地 Vault 不再承担日常同步。

---

## 文章对照

路径均相对 `exporters/wechat-favorites/output/articles/2026/`。

| 方案 | 文章 |
| --- | --- |
| 1 | `2026-04-30-万字长文-个人如何用 Obsidian 搭建本地知识库——从入门到构建你的「第二大脑」.md` |
| 1 | `2026-04-27-llm Wiki 养了三周,开始出毛病了.md` |
| 2 | `2026-04-08-Andrej Karpathy LLM Wiki-用大语言模型构建个人知识库的范式.md` |
| 2 | `2026-04-16-一篇文章卖了20万,开源CC+Obsidian打造的LLM Wiki 内容创作3.0系统.md` |
| 2 | `2026-04-20-2万字超长实战 Karpathy 的 LLM Wiki !别再把 AI 当聊天工具了,这套 LLM 知识库,才是真正复利的“第二大脑”.md` |
| 2 | `2026-04-21-我用 AI 给 Obsidian 写了一个-LLM-wiki-插件.md`（同文另有 `-2` 副本） |
| 2 | `2026-04-22-LLM Wiki 火爆GitHub , 这才是知识库该有的样子.md` |
| 2 | `2026-04-25-llm_wiki-基于 Karpathy 思想的个人知识库,有人做成桌面应用了!.md` |
| 2 | `2026-04-29-推荐基于Andrej Karpathy LLM Wiki 理论框架的3 个开源,在github上可以拿来即用的个人知识库管理应用.md` |
| 2 | `2026-05-02-聊聊 Karpathy 的 LLM Wiki,以及 V2 为什么值得认真看一眼!.md` |
| 2 | `2026-05-07-Karpathy 的知识库构想被人做成桌面应用了,而且做得相当扎实,已在 Github 上斩获 5.8k+ Star!.md` |
| 2 | `2026-05-13-深度解析LLM Wiki - Obsidian-Wiki - GBrain-Agent时代知识的“自组织”与“自进化”.md` |
| 2 | `2026-05-13-别再把资料丢进收藏夹了,我开源了知识库工具AIWiki,一键拥有Karpathy的LLM Wiki和Dan Koe的内容积木思路的AI知识.md` |
| 2、5 | `2026-06-03-用 Obsidian + Codex 搭建一套越用越聪明的个人知识库.md` |
| 2 | `2026-06-09-Hermes+Obsidian+LLM Wiki搭建本地知识库.md` |
| 2、5 | `2026-08-24-我用 WorkBuddy + Obsidian,搭了一个会自己生长的个人知识库.md` |
| 2、1 | `2026-08-24-我用 WorkBuddy + Obsidian-搭了一套懒人版可进化的AI知识库.md`（同文另有 `-2` 副本） |
| 2 | `2026-09-28-Claude Code + Obsidian 还能这么玩,从零搭一套会自己整理的知识库~.md` |
| 3 | `2026-08-04-深度解读-LLM Wiki 的工程实践,从 AI Coding、Obsidian 到 RAG 协同。.md` |
| 4、5 | `2026-10-27-给自己搭一个 AI 第二大脑-从收集资料到写出文章,手把手教你做.md` |
| 5 | `2026-04-19-Hermes+AutoCLI+Obsidian- 打造自动入库、自动整理、自动微信汇报的知识系统.md` |
| 5 | `2026-04-27-Hermes+AutoCLI+Obsidian-打造自动入库、自动整理、自动微信汇报的知识系统.md` |
| 6 | `2026-08-29-我用 Obsidian 搭了一套 Agent 知识系统,保姆教程来了!.md` |

同题但没有独立架构、未单列：`2026-04-07` Karpathy「10 倍大脑」、`2026-04-14` Claude Code + Obsidian 实战、`2026-08-18` Workbuddy 自动更新知识库、`2026-08-11` 制造业知识库（明确不用 BM25，改走 LLM 和 MCP）。`2026-09-23`《Agent 时代，RAG 怎么选》是通用 RAG 选型，Obsidian 只出现一次。
