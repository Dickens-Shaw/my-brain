#!/usr/bin/env python3
"""把微信收藏导出结果整理成个人知识库目录结构。

流程：
1. 从 favorites.json 提取标题、摘要、标签、链接（分类信号不含 URL 随机串）。
2. 过滤掉生活娱乐类内容，单独存档。
3. 剩余内容按技术栈分到一级目录，前端/AI/后端/计算机基础再切二级文件。

用法：
    python3 scripts/build_knowledge_base.py
    python3 scripts/build_knowledge_base.py --input output/favorites.json --output output/knowledge-base
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse


def norm(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def read_tag(raw: str, *names: str) -> str:
    for name in names:
        match = re.search(rf"<{name}[^>]*>(.*?)</{name}>", raw or "", re.I | re.S)
        if match:
            return html.unescape(norm(match.group(1)))
    return ""


def all_tags(raw: str) -> list[str]:
    values = re.findall(r"<tag>(.*?)</tag>", raw or "", re.I | re.S)
    return [html.unescape(norm(value)) for value in values if norm(value)]


def infer_url(item: dict) -> str:
    url = item.get("url") or ""
    if url:
        return url
    for field in ("desc", "raw_content"):
        match = re.search(r"https?://[^\s<>\"]+", item.get(field) or "")
        if match:
            return html.unescape(match.group(0)).rstrip("，。；;)）]】》")
    return ""


def host_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
    except Exception:
        return ""


def strip_urls(text: str) -> str:
    """去掉正文里内嵌的链接。有些收藏的 desc 本身就是一条 URL，
    留着会让 http、html 之类的词凭空命中。"""
    return re.sub(r"https?://\S+", " ", text or "")


def enrich(item: dict) -> dict:
    raw = item.get("raw_content") or ""
    title = item.get("title") or read_tag(raw, "pagetitle", "title", "datatitle")
    desc = item.get("desc") or read_tag(raw, "pagedesc", "desc", "digest", "datadesc")
    tags = all_tags(raw)
    url = infer_url(item)
    signal = strip_urls(" ".join([title, desc, " ".join(tags)]))
    return {
        "id": item.get("id", ""),
        "time": item.get("time", ""),
        "title": title,
        "desc": desc,
        "tags": tags,
        "url": url,
        "host": host_of(url),
        # 分类信号只用人写的文本，URL 随机串会造成大量误判
        "signal": norm(signal),
    }


def w(*words: str) -> str:
    """给英文缩写加边界，避免 baidu 里的 ai、campaign 里的 ai 被命中。"""
    return "|".join(rf"(?<![a-z0-9]){re.escape(x)}(?![a-z0-9])" for x in words)


STRONG_TECH_DOMAINS = {
    "github.com", "gitee.com", "stackoverflow.com", "segmentfault.com",
    "juejin.cn", "juejin.im", "www.w3cplus.com", "w3cplus.com",
    "blog.csdn.net", "csdn.net", "developer.mozilla.org",
    "developers.google.cn", "time.geekbang.org", "www.gitbook.cn",
    "gitbook.cn", "justjavac.com", "www.yinwang.org", "fex.baidu.com",
    "www.npmjs.com", "npmjs.com", "ifyour.github.io",
}

DROP_RULES = {
    "美食餐饮": r"美食|餐厅|餐馆|好吃|火锅|烧烤|小吃|菜谱|做法|食谱|吃货|探店|外卖|奶茶|甜品|面馆|蛋糕|大闸蟹|螃蟹|宵夜|厨",
    "旅游出行": r"旅游|旅行|景点|自驾|民宿|酒店|机票|签证|游记|度假|海岛|温泉|青旅|漫游|礼宾|行李员",
    "育儿家庭": r"育儿|宝宝|孩子|幼儿园|早教|辅食|怀孕|产后|亲子|婆婆|老公|老婆|婚礼|结婚|相亲|恋爱|离婚|父亲节|母亲节",
    "健康医疗": r"养生|中医|疾病|症状|治疗|医院|医生|减肥|健身|瑜伽|睡眠|体检|保健|腺样体",
    "娱乐八卦": r"明星|娱乐圈|综艺|电视剧|八卦|演唱会|偶像|追星|搞笑|段子|冷笑话|愚人节|整人|喷子|练习室|亚索|国服",
    "情感鸡汤": r"人生感悟|心灵|鸡汤|做人|正能量|励志|人性|修养|借钱|星座|运势",
    "购物优惠": r"优惠|打折|团购|秒杀|红包|领券|促销|特价|抽奖|免费领|限时|福利|流量荒|宜家",
    "房产装修": r"装修|买房|房价|楼盘|户型|家居|家具|软装|卧室|客厅|沙发|餐具",
    "本地生活": r"成都|厦门|重庆|同城|周边游|地铁|公交|车牌|驾照|社保|公积金|落户|护照|通行证|街道|雾霾|九眼桥",
    "金融理财": r"理财|基金|股票|炒股|保险|银行|贷款|信用卡|黄金|期货|众筹",
    "语言考试": r"英语口语|雅思|托福|四六级|日语|考研|公务员|教师资格",
}

# 大类定义。priority 越小越优先（用于分数相同时的取舍）。
# weight 用于压低弱信号类别，避免「教程」这种泛词抢走归属。
CATEGORIES: list[dict] = [
    {
        "slug": "08-Web3与区块链",
        "priority": 1,
        "weight": 1.0,
        "pattern": r"区块链|比特币|以太坊|智能合约|去中心化|nft|defi|solidity|加密货币|链上|数字货币|" + w("web3", "dao", "dapp"),
        "note": "区块链、智能合约、加密货币相关。",
    },
    {
        "slug": "02-AI",
        "priority": 2,
        "weight": 1.0,
        "pattern": (
            r"人工智能|机器学习|深度学习|神经网络|大模型|大语言模型|chatgpt|gpt-?[0-9o]|openai|claude|gemini|llama|"
            r"transformer|提示词|prompt|langchain|向量数据库|aigc|stable diffusion|midjourney|智能体|模型训练|微调|"
            r"deepseek|通义|文心|豆包|kimi|智谱|moonshot|ollama|coze|扣子|dify|copilot|cursor|windsurf|trae|"
            r"灵码|多模态|机器人|算力|推理|蒸馏|" + w("ai", "llm", "nlp", "rag", "agent", "mcp", "sft", "cot")
        ),
        "children": [
            {"slug": "01-AI编程工具", "pattern": r"cursor|copilot|claude code|codex|windsurf|trae|灵码|编程助手|ai编程|写代码|代码生成|vibe coding|" + w("ide", "mcp")},
            {"slug": "02-提示词工程", "pattern": r"提示词|prompt|指令|咒语|调教|系统提示|上下文工程|" + w("cot")},
            {"slug": "03-大模型应用", "pattern": r"应用|落地|实战|案例|工作流|自动化|智能体|" + w("agent", "rag") + r"|知识库|问答|客服|coze|扣子|dify|搭建|接入",},
            {"slug": "04-模型原理与训练", "pattern": r"原理|架构|transformer|注意力|训练|微调|参数|论文|蒸馏|量化|推理优化|" + w("sft", "rlhf")},
            {"slug": "05-AI行业动态", "pattern": r"发布|上线|融资|开源|榜单|评测|对比|趋势|访谈|观点|大会|报告|估值|竞争"},
        ],
        "note": "AI、大模型、提示词、AI 编程工具与行业动态。",
    },
    {
        "slug": "07-移动端",
        "priority": 3,
        "weight": 1.0,
        "pattern": r"android|安卓|ios开发|swift|kotlin|flutter|react native|移动开发|app开发|鸿蒙|harmony|uni-?app|taro|" + w("rn", "apk", "ipa"),
        "note": "Android、iOS、跨端框架与移动开发。",
    },
    {
        "slug": "01-前端",
        "priority": 4,
        "weight": 1.0,
        "pattern": (
            r"前端|css3?|html5?|javascript|typescript|react|vue|angular|svelte|jquery|webpack|vite|rollup|babel|es6|"
            r"浏览器|回流|重绘|响应式|flex布局|grid布局|小程序|node\.?js|微前端|状态管理|redux|vuex|pinia|next\.?js|nuxt|"
            r"组件库|组件封装|脚手架|跨域|首屏|低代码|可视化|echarts|图表|表单|富文本|预渲染|路由|埋点|前端监控|"
            r"web\s?worker|web\s?component|web核心|web性能|web开发|多端框架|自动化构建|electron|tauri|"
            r"第\s*\d{3,4}\s*期|" + w("js", "ts", "sass", "less", "scss", "npm", "yarn", "pnpm", "dom", "bom", "spa", "ssr", "pwa", "h5", "jsx", "canvas", "svg", "swr", "pm2")
        ),
        "children": [
            {"slug": "01-JavaScript与TypeScript", "pattern": r"javascript|typescript|es6|es2015|es20[0-9]{2}|闭包|原型|异步|promise|async|事件循环|作用域|模块化|函数式|装饰器|" + w("js", "ts")},
            {"slug": "02-React生态", "pattern": r"react|hooks|redux|next\.?js|umi|dva|mobx|" + w("jsx")},
            {"slug": "03-Vue生态", "pattern": r"vue|vuex|pinia|nuxt|composition api|响应式原理|element"},
            {"slug": "04-CSS与样式", "pattern": r"css|样式|布局|flex|grid|动画|响应式|tailwind|bem|适配|" + w("sass", "less", "scss", "rem", "px")},
            {"slug": "05-工程化与构建", "pattern": r"webpack|vite|rollup|babel|eslint|构建|打包|工程化|脚手架|monorepo|模块联邦|微前端|部署|发布|低代码|埋点|前端监控|" + w("npm", "yarn", "pnpm", "cli")},
            {"slug": "06-浏览器与性能", "pattern": r"浏览器|渲染|性能|优化|首屏|加载|缓存|内存|回流|重绘|事件|跨域|同源|安全|xss|csrf|" + w("dom", "bom")},
            {"slug": "07-小程序与H5", "pattern": r"小程序|移动端页面|公众号开发|uni-?app|taro|跨端|" + w("h5")},
            {"slug": "08-Node与全栈", "pattern": r"node|express|koa|nest|服务端渲染|全栈|" + w("ssr", "bff")},
        ],
        "note": "前端语言、框架、样式、工程化、浏览器与性能。",
    },
    {
        "slug": "03-后端与架构",
        "priority": 5,
        "weight": 1.0,
        "pattern": (
            r"后端|服务端|java|golang|go语言|python|php|rust|c\+\+|spring|django|flask|gin|微服务|分布式|中间件|kafka|"
            r"rabbitmq|redis|mysql|mongodb|postgres|elasticsearch|数据库|高并发|负载均衡|nginx|docker|kubernetes|k8s|"
            r"运维|linux|服务器|架构|系统设计|消息队列|集群|容器|限流|熔断|" + w("api", "rpc", "orm", "jvm", "sql", "ddd")
        ),
        "children": [
            {"slug": "01-语言与框架", "pattern": r"java|golang|go语言|python|php|rust|c\+\+|c#|\.net|spring|django|flask|gin|" + w("jvm")},
            {"slug": "02-数据库", "pattern": r"mysql|redis|mongodb|postgres|elasticsearch|clickhouse|数据库|索引|事务|分库分表|" + w("sql", "orm")},
            {"slug": "03-分布式与中间件", "pattern": r"分布式|微服务|中间件|kafka|rabbitmq|消息队列|集群|一致性|服务治理|注册中心|高并发|限流|熔断|降级|" + w("cap", "rpc")},
            {"slug": "04-运维与部署", "pattern": r"docker|kubernetes|k8s|nginx|linux|运维|部署|容器|监控|日志|devops|jenkins|服务器|ci/cd"},
            {"slug": "05-架构与系统设计", "pattern": r"架构|系统设计|技术选型|技术方案|领域驱动|高可用|可扩展|" + w("ddd", "api")},
        ],
        "note": "服务端语言、数据库、分布式、运维与架构设计。",
    },
    {
        "slug": "04-计算机基础",
        "priority": 6,
        "weight": 1.0,
        "pattern": (
            r"算法|数据结构|leetcode|时间复杂度|空间复杂度|设计模式|操作系统|计算机网络|编译原理|三次握手|状态码|"
            r"二叉树|链表|动态规划|排序算法|递归|哈希|进程|线程|死锁|字符编码|正则表达式|面向对象|编程范式|"
            r"websocket|" + w("http", "https", "tcp", "udp", "dns", "cdn", "solid")
        ),
        "children": [
            {"slug": "01-算法与数据结构", "pattern": r"算法|数据结构|leetcode|复杂度|二叉树|链表|动态规划|排序|递归|哈希|图论|贪心|回溯|双指针"},
            {"slug": "02-网络与协议", "pattern": r"websocket|网络|协议|请求|状态码|三次握手|抓包|" + w("http", "https", "tcp", "udp", "dns", "cdn")},
            {"slug": "03-操作系统与并发", "pattern": r"操作系统|进程|线程|内存管理|文件系统|调度|内核|并发|锁|死锁|协程"},
            {"slug": "04-设计模式与范式", "pattern": r"设计模式|面向对象|函数式|编程范式|单例|工厂|观察者|" + w("solid")},
        ],
        "note": "算法、网络协议、操作系统、设计模式等基础知识。",
    },
    {
        "slug": "05-开源项目",
        "priority": 7,
        "weight": 1.0,
        "pattern": r"github|开源项目|开源库|开源框架|开源社区|awesome|项目推荐|轮子|star数|" + w("sdk", "repo"),
        "note": "GitHub 项目、开源库与项目推荐。",
    },
    {
        "slug": "06-工具与效率",
        "priority": 8,
        "weight": 1.0,
        "pattern": r"vscode|sublime|jetbrains|idea|终端|命令行|chrome扩展|浏览器插件|效率工具|生产力|快捷键|自动化脚本|软件推荐|工具推荐|神器|" + w("vim", "git", "cli", "ide", "mac", "shell"),
        "note": "编辑器、命令行、插件与效率工具。",
    },
    {
        "slug": "09-产品与设计",
        "priority": 9,
        "weight": 1.0,
        "pattern": r"产品经理|产品设计|交互设计|用户体验|需求分析|信息架构|设计规范|设计系统|design system|视觉设计|排版|配色|" + w("ui", "ux", "figma", "sketch", "axure"),
        "note": "产品方法、交互设计与用户体验。",
    },
    {
        "slug": "11-代码质量与工程实践",
        "priority": 10,
        "weight": 1.0,
        "pattern": r"代码规范|代码质量|命名规范|重构|整洁|可读性|注释|技术债|单元测试|自动化测试|测试用例|代码审查|版本控制|git flow|编码规范|程序员|码农|开发者|工程师|编程习惯|调试技巧",
        "note": "编码规范、重构、测试与工程实践。",
    },
    {
        "slug": "10-职业与行业",
        "priority": 11,
        "weight": 0.6,
        "pattern": r"面试|简历|跳槽|offer|涨薪|职业规划|晋升|校招|社招|大厂|职场|互联网公司|科技公司|创业|融资|上市|裁员|行业观察|技术趋势|技术大会|字节跳动|阿里巴巴|腾讯|美团|谷歌|微软|工资|薪资|副业|自由职业|远程办公|独立开发",
        "note": "面试、职业发展与行业动态。",
    },
    {
        "slug": "12-学习资料与书单",
        "priority": 12,
        "weight": 0.35,
        "pattern": r"教程|入门|指南|实战|详解|原理|深入理解|从零开始|学习路线|知识体系|读书笔记|技术书|课程|干货|最佳实践|踩坑|经验分享|书单|总结|笔记|资料|合集|清单|手册|速查",
        "note": "综合教程、书单与学习资料合集。",
    },
]

FILTERED_SLUG = "98-已过滤-生活娱乐"
PENDING_SLUG = "99-待确认"


def count_hits(pattern: str, text: str) -> int:
    """统计命中的不同关键词数量，命中越多说明归属越明确。"""
    found = re.findall(pattern, text, re.I)
    unique = {item.lower() if isinstance(item, str) else str(item) for item in found}
    unique.discard("")
    return len(unique)


def classify_primary(item: dict) -> dict | None:
    text = item["signal"]
    if not text:
        return None

    best: tuple[float, int, dict] | None = None
    for category in CATEGORIES:
        hits = count_hits(category["pattern"], text)
        if not hits:
            continue
        score = hits * category["weight"]
        candidate = (score, -category["priority"], category)
        if best is None or (candidate[0], candidate[1]) > (best[0], best[1]):
            best = candidate

    if best:
        return best[2]

    if item["host"] in STRONG_TECH_DOMAINS:
        return next(c for c in CATEGORIES if c["slug"] == "05-开源项目")
    return None


def classify_secondary(category: dict, item: dict) -> str:
    children = category.get("children")
    if not children:
        return ""

    text = item["signal"]
    best_slug = ""
    best_score = 0
    for child in children:
        hits = count_hits(child["pattern"], text)
        if hits > best_score:
            best_score = hits
            best_slug = child["slug"]
    return best_slug or "99-其他"


def drop_reasons(item: dict) -> list[str]:
    return [name for name, pattern in DROP_RULES.items() if re.search(pattern, item["signal"], re.I)]


def safe(value: str) -> str:
    return value.replace("\n", " ").strip()


def render_items(path: Path, title: str, note: str, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(items, key=lambda x: x["time"] or "")
    with path.open("w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n")
        f.write(f"- 数量: {len(ordered)}\n")
        if note:
            f.write(f"- 说明: {note}\n")
        f.write("\n")
        for index, item in enumerate(ordered, 1):
            heading = safe(item["title"]) or safe(item["url"]) or f"收藏 {item['id']}"
            f.write(f"## {index}. {heading}\n\n")
            if item["time"]:
                f.write(f"- 时间: {item['time']}\n")
            if item["url"]:
                f.write(f"- 链接: {item['url']}\n")
            if item["host"]:
                f.write(f"- 来源: {item['host']}\n")
            if item["tags"]:
                f.write(f"- 标签: {', '.join(item['tags'])}\n")
            f.write(f"- 原始 ID: {item['id']}\n")
            desc = safe(item["desc"])
            if desc and desc != safe(item["url"]):
                f.write(f"\n{desc}\n")
            f.write("\n")


def build_readme(path: Path, tree: dict[str, dict], totals: dict[str, int], hosts: Counter) -> None:
    with path.open("w", encoding="utf-8") as f:
        f.write("# 个人知识库：微信收藏整理\n\n")
        f.write(f"- 收藏总数: {totals['total']}\n")
        f.write(f"- 纳入知识库: {totals['kept']}\n")
        f.write(f"- 已过滤(生活娱乐): {totals['filtered']}\n")
        f.write(f"- 待确认: {totals['pending']}\n")
        f.write("- 原始导出保留在 `../favorites.md` 和 `../favorites.json`\n\n")

        f.write("## 目录结构\n\n")
        for slug in sorted(tree):
            node = tree[slug]
            if node["children"]:
                f.write(f"- `{slug}/` {node['note']} 共 {node['count']} 条\n")
                for child_slug in sorted(node["children"]):
                    f.write(f"  - `{child_slug}.md` {node['children'][child_slug]} 条\n")
            else:
                f.write(f"- `{slug}.md` {node['note']} 共 {node['count']} 条\n")

        f.write("\n## 来源域名 Top 15\n\n")
        for host, count in hosts.most_common(15):
            f.write(f"- {host}: {count}\n")

        f.write("\n## 分类规则\n\n")
        f.write("1. 分类信号只取标题、摘要、标签，不使用 URL 随机串，避免 `.html`、`baidu` 这类字符误命中。\n")
        f.write("2. 英文缩写（ai、ui、js 等）带词边界匹配，不会命中 `campaign`、`Dui1tIBc` 之类的片段。\n")
        f.write("3. 先判定是否属于知识库范围，生活娱乐类进入 `98-已过滤-生活娱乐.md`。\n")
        f.write("4. 大类按命中关键词数量打分选出，分数相同时按 Web3 > AI > 移动端 > 前端 > 后端 > 基础 的顺序取舍。\n")
        f.write("5. 「学习资料」「职业与行业」是弱信号类，权重被压低，只有在没有更明确归属时才会命中。\n")
        f.write("6. 信号不足以判断的进入 `99-待确认.md`，需要人工过一遍。\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="把微信收藏整理成知识库目录")
    parser.add_argument("--input", default="output/favorites.json")
    parser.add_argument("--output", default="output/knowledge-base")
    parser.add_argument("--clean", action="store_true", help="生成前清空输出目录")
    args = parser.parse_args()

    base = Path(__file__).resolve().parent.parent
    input_path = (base / args.input).resolve() if not Path(args.input).is_absolute() else Path(args.input)
    output_dir = (base / args.output).resolve() if not Path(args.output).is_absolute() else Path(args.output)

    if not input_path.is_file():
        print(f"错误: 找不到输入文件 {input_path}")
        return 1

    if args.clean and output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    data = json.loads(input_path.read_text(encoding="utf-8"))
    items = [enrich(entry) for entry in data]

    buckets: dict[str, dict] = {}
    filtered: list[dict] = []
    pending: list[dict] = []

    for item in items:
        category = classify_primary(item)
        if category is None:
            reasons = drop_reasons(item)
            if reasons:
                item["drop_reason"] = ", ".join(reasons)
                filtered.append(item)
            else:
                pending.append(item)
            continue

        slug = category["slug"]
        node = buckets.setdefault(slug, {"category": category, "items": [], "children": {}})
        child_slug = classify_secondary(category, item)
        if child_slug:
            node["children"].setdefault(child_slug, []).append(item)
        else:
            node["items"].append(item)

    tree: dict[str, dict] = {}
    kept = 0
    for slug, node in buckets.items():
        category = node["category"]
        child_counts: dict[str, int] = {}

        if node["children"]:
            for child_slug, child_items in node["children"].items():
                render_items(
                    output_dir / slug / f"{child_slug}.md",
                    f"{slug} / {child_slug}",
                    category["note"],
                    child_items,
                )
                child_counts[child_slug] = len(child_items)
                kept += len(child_items)
            if node["items"]:
                render_items(
                    output_dir / slug / "99-其他.md",
                    f"{slug} / 99-其他",
                    category["note"],
                    node["items"],
                )
                child_counts["99-其他"] = child_counts.get("99-其他", 0) + len(node["items"])
                kept += len(node["items"])
        else:
            render_items(output_dir / f"{slug}.md", slug, category["note"], node["items"])
            kept += len(node["items"])

        tree[slug] = {
            "note": category["note"],
            "count": sum(child_counts.values()) if child_counts else len(node["items"]),
            "children": child_counts,
        }

    with (output_dir / f"{FILTERED_SLUG}.md").open("w", encoding="utf-8") as f:
        f.write("# 已过滤：生活娱乐类\n\n")
        f.write(f"- 数量: {len(filtered)}\n")
        f.write("- 说明: 这些内容与技术知识库无关，单独存档备查，可以直接删除。\n\n")
        for index, item in enumerate(sorted(filtered, key=lambda x: x["time"] or ""), 1):
            heading = safe(item["title"]) or safe(item["url"]) or f"收藏 {item['id']}"
            f.write(f"## {index}. {heading}\n\n")
            f.write(f"- 过滤原因: {item.get('drop_reason', '')}\n")
            if item["time"]:
                f.write(f"- 时间: {item['time']}\n")
            if item["url"]:
                f.write(f"- 链接: {item['url']}\n")
            f.write(f"- 原始 ID: {item['id']}\n\n")

    render_items(
        output_dir / f"{PENDING_SLUG}.md",
        "待确认",
        "标题和摘要信号不足，无法自动归类，需要人工过一遍。",
        pending,
    )

    hosts = Counter(item["host"] or "(无域名)" for item in items)
    totals = {
        "total": len(items),
        "kept": kept,
        "filtered": len(filtered),
        "pending": len(pending),
    }
    build_readme(output_dir / "README.md", tree, totals, hosts)

    print(f"输出目录: {output_dir}")
    print(f"总数 {totals['total']} / 纳入 {totals['kept']} / 过滤 {totals['filtered']} / 待确认 {totals['pending']}")
    print()
    for slug in sorted(tree):
        node = tree[slug]
        print(f"{node['count']:5d}  {slug}")
        for child_slug in sorted(node["children"]):
            print(f"       {node['children'][child_slug]:5d}  {child_slug}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
