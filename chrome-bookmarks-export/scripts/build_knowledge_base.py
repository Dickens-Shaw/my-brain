#!/usr/bin/env python3
"""把 Chrome 书签导出结果整理成个人知识库目录结构。

和微信收藏不同，书签自带用户手工分的文件夹，这是比标题更可靠的信号，
所以分类信号取「标题 + 文件夹路径」，两者都没命中时才退回域名和 URL 里的英文词。

流程：
1. 从 bookmarks.json 提取标题、文件夹、域名作为分类信号。
2. 过滤掉生活娱乐类内容，单独存档。
3. 剩余内容按技术栈分到一级目录，前端/AI/后端/计算机基础再切二级文件。
4. 已经抓到正文的条目，附上本地 Markdown 的相对路径。

用法：
    python3 scripts/build_knowledge_base.py
    python3 scripts/build_knowledge_base.py --clean
    python3 scripts/build_knowledge_base.py --input output/bookmarks.json --output output/knowledge-base
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse


def norm(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def host_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
    except ValueError:
        return ""


# 常见的无意义 URL 片段，放进弱信号只会添乱
URL_NOISE = {
    "http", "https", "html", "htm", "php", "asp", "aspx", "index", "www", "com", "cn", "net", "org",
    "post", "posts", "article", "articles", "detail", "details", "page", "pages", "blog", "news",
    "zh", "en", "docs", "doc", "view", "show", "content", "topic", "item", "list", "tab", "readme",
    "utm", "source", "share", "from", "ref", "id", "amp", "the", "and", "for", "with",
}


def url_tokens(url: str) -> str:
    """URL 里的英文单词。只有标题和文件夹都没命中时才用，所以要洗得干净些。"""
    parsed = urlparse(url)
    words = re.split(r"[^a-zA-Z\u4e00-\u9fa5]+", f"{parsed.path} {parsed.fragment}")
    kept = []
    for word in words:
        lowered = word.lower()
        if len(word) < 3 or lowered in URL_NOISE:
            continue
        # 形如 6844903985338400782、a1b2c3d4 的随机串没有语义
        if re.fullmatch(r"[0-9a-f]{8,}", lowered):
            continue
        kept.append(word)
    return " ".join(kept)


def enrich(item: dict) -> dict:
    url = item.get("url") or ""
    title = norm(item.get("title"))
    folder = norm(item.get("folder"))
    return {
        "id": item.get("id", ""),
        "time": item.get("time", ""),
        "title": title,
        "url": url,
        "host": item.get("source") or host_of(url),
        "folder": folder,
        # 文件夹路径是用户亲手分的类，和标题一样是强信号
        "signal": norm(f"{title} {folder.replace('/', ' ')}"),
        "weak_signal": url_tokens(url),
    }


def w(*words: str) -> str:
    """给英文缩写加边界，避免 baidu 里的 ai、campaign 里的 ai 被命中。"""
    return "|".join(rf"(?<![a-z0-9]){re.escape(x)}(?![a-z0-9])" for x in words)


# 域名本身就说明了归属的站点。只在标题和文件夹都没信号时兜底。
DOMAIN_FALLBACK = {
    "05-开源项目": (
        "github.com", "github.io", "gitee.com", "gitlab.com", "npmjs.com", "sourceforge.net",
    ),
    "01-前端": (
        "developer.mozilla.org", "caniuse.com", "css-tricks.com", "w3schools.com", "w3school.com.cn",
        "w3cplus.com", "es6.ruanyifeng.com", "reactjs.org", "react.dev", "vuejs.org", "cn.vuejs.org",
        "webpack.js.org", "vitejs.dev", "cn.vitejs.dev", "typescriptlang.org", "eslint.org",
        "ant.design", "element-plus.org", "nodejs.org", "nextjs.org", "tailwindcss.com",
    ),
    "04-计算机基础": ("leetcode.cn", "leetcode.com", "leetcode-cn.com", "visualgo.net", "hello-algo.com"),
    "09-产品与设计": (
        "dribbble.com", "behance.net", "figma.com", "uisdc.com", "zcool.com.cn",
        "iconfont.cn", "unsplash.com", "pixabay.com",
    ),
    "06-工具与效率": (
        "chrome.google.com", "microsoftedge.microsoft.com", "marketplace.visualstudio.com",
        "regex101.com", "regexr.com", "json.cn", "tool.lu", "carbon.now.sh",
    ),
    "03-后端与架构": (
        "docker.com", "hub.docker.com", "kubernetes.io", "nginx.org", "redis.io",
        "help.aliyun.com", "cloud.tencent.com", "docs.aws.amazon.com",
    ),
    "02-AI": (
        "huggingface.co", "openai.com", "platform.openai.com", "docs.anthropic.com",
        "modelscope.cn", "civitai.com", "arxiv.org",
    ),
}

DROP_RULES = {
    "美食餐饮": r"美食|餐厅|餐馆|好吃|火锅|烧烤|小吃|菜谱|做法|食谱|吃货|探店|外卖|奶茶|甜品|面馆|蛋糕|宵夜|下厨",
    "旅游出行": r"旅游|旅行|景点|自驾|民宿|酒店|机票|签证|游记|度假|海岛|温泉|航班|租车",
    "育儿家庭": r"育儿|宝宝|孩子|幼儿园|早教|辅食|怀孕|产后|亲子|婚礼|结婚|相亲|恋爱|离婚",
    "健康医疗": r"养生|中医|疾病|症状|治疗|医院|挂号|减肥|健身|瑜伽|体检|保健",
    "娱乐八卦": r"明星|娱乐圈|综艺|电视剧|八卦|演唱会|偶像|追星|搞笑|段子|电影票|追剧|小说|漫画|游戏攻略",
    "情感鸡汤": r"人生感悟|心灵|鸡汤|正能量|励志|星座|运势",
    "购物优惠": r"优惠|打折|团购|秒杀|领券|促销|特价|抽奖|免费领|限时|比价|优惠券|折扣|京东|淘宝|天猫|拼多多|亚马逊|唯品会|闲鱼",
    "房产装修": r"装修|买房|房价|楼盘|户型|家居|家具|软装",
    "本地生活": r"同城|周边游|地铁|公交|车牌|驾照|社保|公积金|落户|护照|通行证|快递|物流查询",
    "金融理财": r"理财|基金|股票|炒股|保险|贷款|信用卡|黄金|期货|众筹|记账",
    "语言考试": r"英语口语|雅思|托福|四六级|日语|考研|公务员|教师资格",
}

# 大类定义。priority 越小越优先（用于分数相同时的取舍）。
# weight 用于压低弱信号类别，避免「教程」这种泛词抢走归属。
CATEGORIES: list[dict] = [
    {
        "slug": "08-Web3与区块链",
        "priority": 1,
        "weight": 1.0,
        "pattern": r"区块链|比特币|以太坊|智能合约|去中心化|nft|defi|solidity|加密货币|链上|钱包|" + w("web3", "dao", "dapp"),
        "note": "区块链、智能合约、加密货币相关。",
    },
    {
        "slug": "02-AI",
        "priority": 2,
        "weight": 1.0,
        "pattern": (
            r"人工智能|机器学习|深度学习|神经网络|大模型|大语言模型|chatgpt|gpt-?[0-9o]|openai|claude|gemini|llama|"
            r"transformer|提示词|prompt|langchain|向量数据库|aigc|stable diffusion|midjourney|智能体|模型训练|微调|"
            r"deepseek|通义|文心|豆包|kimi|智谱|moonshot|ollama|coze|扣子|dify|copilot|cursor|windsurf|trae|codex|"
            r"灵码|多模态|算力|蒸馏|huggingface|" + w("ai", "llm", "nlp", "rag", "agent", "mcp", "sft", "cot", "sdd")
        ),
        "children": [
            {"slug": "01-AI编程工具", "pattern": r"cursor|copilot|claude code|codex|windsurf|trae|灵码|编程助手|ai编程|代码生成|vibe coding|skill|" + w("ide", "mcp", "cli")},
            {"slug": "02-提示词工程", "pattern": r"提示词|prompt|指令|咒语|系统提示|上下文工程|" + w("cot", "sdd")},
            {"slug": "03-大模型应用", "pattern": r"应用|落地|实战|案例|工作流|自动化|智能体|" + w("agent", "rag") + r"|知识库|问答|客服|coze|扣子|dify|搭建|接入"},
            {"slug": "04-模型原理与训练", "pattern": r"原理|架构|transformer|注意力|训练|微调|参数|论文|蒸馏|量化|推理优化|" + w("sft", "rlhf")},
            {"slug": "05-AI产品与动态", "pattern": r"发布|上线|融资|开源|榜单|评测|对比|趋势|访谈|观点|报告|竞争|chatgpt|gemini|claude|deepseek|豆包|通义"},
        ],
        "note": "AI、大模型、提示词、AI 编程工具与行业动态。",
    },
    {
        "slug": "07-移动端与桌面端",
        "priority": 3,
        "weight": 1.0,
        "pattern": (
            r"android|安卓|ios开发|swift|kotlin|flutter|react native|移动开发|app开发|鸿蒙|harmony|uni-?app|taro|"
            r"electron|tauri|桌面端|客户端开发|" + w("rn", "apk", "ipa")
        ),
        "note": "Android、iOS、跨端框架、Electron 桌面端。",
    },
    {
        "slug": "01-前端",
        "priority": 4,
        "weight": 1.0,
        "pattern": (
            r"前端|css3?|html5?|javascript|typescript|react|vue|angular|svelte|jquery|webpack|vite|rollup|babel|es6|"
            r"浏览器|回流|重绘|响应式|flex布局|grid布局|小程序|node\.?js|微前端|状态管理|redux|vuex|pinia|next\.?js|nuxt|"
            r"组件库|组件封装|脚手架|跨域|首屏|低代码|可视化|echarts|图表|表单|富文本|路由|埋点|前端监控|threejs|three\.js|"
            r"web\s?worker|web\s?component|webgl|webgpu|canvas|动画|拖拽|瀑布流|骨架屏|懒加载|无限滚动|"
            r"第\s*\d{3,4}\s*期|" + w("js", "ts", "sass", "less", "scss", "npm", "yarn", "pnpm", "dom", "bom", "spa", "ssr", "pwa", "h5", "jsx", "svg", "swr")
        ),
        "children": [
            {"slug": "01-JavaScript与TypeScript", "pattern": r"javascript|typescript|es6|es20[0-9]{2}|闭包|原型|异步|promise|async|事件循环|作用域|模块化|函数式|装饰器|正则|" + w("js", "ts")},
            {"slug": "02-React生态", "pattern": r"react|hooks|redux|next\.?js|umi|dva|mobx|" + w("jsx")},
            {"slug": "03-Vue生态", "pattern": r"vue|vuex|pinia|nuxt|composition api|响应式原理|element"},
            {"slug": "04-CSS与样式", "pattern": r"css|样式|布局|flex|grid|动画|响应式|tailwind|适配|主题|暗黑模式|" + w("sass", "less", "scss", "rem", "px")},
            {"slug": "05-工程化与构建", "pattern": r"webpack|vite|rollup|babel|eslint|构建|打包|工程化|脚手架|monorepo|模块联邦|微前端|部署|发布|低代码|" + w("npm", "yarn", "pnpm", "cli")},
            {"slug": "06-浏览器与性能", "pattern": r"浏览器|渲染|性能|优化|首屏|加载|缓存|内存|回流|重绘|跨域|同源|xss|csrf|监控|埋点|lighthouse|" + w("dom", "bom")},
            {"slug": "07-图形与可视化", "pattern": r"canvas|webgl|webgpu|threejs|three\.js|可视化|echarts|图表|动画|游戏|着色器|shader|3d|" + w("svg")},
            {"slug": "08-小程序与跨端", "pattern": r"小程序|公众号开发|uni-?app|taro|跨端|" + w("h5")},
            {"slug": "09-Node与全栈", "pattern": r"node|express|koa|nest|服务端渲染|全栈|" + w("ssr", "bff")},
            # 放在最后：只有前面的技术方向都没命中时，才当成综合资源
            {"slug": "10-资源与社区", "pattern": r"资源|导航|社区|博客|专栏|团队|周刊|外刊|大全|收藏|学习路径|学习指南|awesome|插件库|组件库"},
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
            r"运维|linux|服务器|架构|系统设计|消息队列|集群|容器|限流|熔断|云原生|阿里云|腾讯云|" + w("api", "rpc", "orm", "jvm", "sql", "ddd")
        ),
        "children": [
            {"slug": "01-语言与框架", "pattern": r"java|golang|go语言|python|php|rust|c\+\+|c#|\.net|spring|django|flask|gin|" + w("jvm")},
            {"slug": "02-数据库", "pattern": r"mysql|redis|mongodb|postgres|elasticsearch|clickhouse|sqlite|数据库|索引|事务|分库分表|" + w("sql", "orm")},
            {"slug": "03-分布式与中间件", "pattern": r"分布式|微服务|中间件|kafka|rabbitmq|消息队列|集群|一致性|服务治理|注册中心|高并发|限流|熔断|降级|" + w("cap", "rpc")},
            {"slug": "04-运维与云服务", "pattern": r"docker|kubernetes|k8s|nginx|linux|运维|部署|容器|监控|日志|devops|jenkins|服务器|ci/cd|阿里云|腾讯云|云原生|域名|证书"},
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
            r"二叉树|链表|动态规划|排序算法|递归|哈希|进程|线程|死锁|字符编码|面向对象|编程范式|加密|签名|"
            r"websocket|" + w("http", "https", "tcp", "udp", "dns", "cdn", "solid")
        ),
        "children": [
            {"slug": "01-算法与数据结构", "pattern": r"算法|数据结构|leetcode|复杂度|二叉树|链表|动态规划|排序|递归|哈希|图论|贪心|回溯|双指针"},
            {"slug": "02-网络与协议", "pattern": r"websocket|网络|协议|请求|状态码|三次握手|抓包|代理|" + w("http", "https", "tcp", "udp", "dns", "cdn")},
            {"slug": "03-操作系统与并发", "pattern": r"操作系统|进程|线程|内存管理|文件系统|调度|内核|并发|锁|死锁|协程"},
            {"slug": "04-设计模式与范式", "pattern": r"设计模式|面向对象|函数式|编程范式|单例|工厂|观察者|发布订阅|" + w("solid")},
            {"slug": "05-安全与加密", "pattern": r"加密|解密|签名|证书|安全|漏洞|逆向|破解|token|jwt|oauth|鉴权|防护"},
        ],
        "note": "算法、网络协议、操作系统、设计模式与安全基础。",
    },
    {
        "slug": "05-开源项目",
        "priority": 7,
        "weight": 0.9,
        "pattern": r"github|开源项目|开源库|开源框架|开源社区|awesome|项目推荐|轮子|star|" + w("sdk", "repo"),
        "note": "GitHub 项目、开源库与项目推荐。",
    },
    {
        "slug": "06-工具与效率",
        "priority": 8,
        "weight": 1.0,
        "pattern": (
            r"vscode|sublime|jetbrains|idea|webstorm|终端|命令行|chrome扩展|浏览器插件|效率工具|生产力|快捷键|"
            r"自动化脚本|软件推荐|工具推荐|神器|在线工具|格式化|抓包工具|charles|postman|图床|" +
            w("vim", "git", "cli", "ide", "mac", "shell", "zsh", "brew", "ssh", "docker")
        ),
        "note": "编辑器、命令行、插件与效率工具。",
    },
    {
        "slug": "09-产品与设计",
        "priority": 9,
        "weight": 1.0,
        "pattern": (
            r"产品经理|产品设计|交互设计|用户体验|需求分析|信息架构|设计规范|设计系统|design system|视觉设计|排版|配色|"
            r"素材|图标|字体|原型|走查|" + w("ui", "ux", "figma", "sketch", "axure")
        ),
        "note": "产品方法、交互设计、视觉与设计素材。",
    },
    {
        "slug": "11-代码质量与工程实践",
        "priority": 10,
        "weight": 1.0,
        "pattern": (
            r"代码规范|代码质量|命名规范|重构|整洁|可读性|注释|技术债|单元测试|自动化测试|测试用例|代码审查|"
            r"code review|版本控制|git flow|编码规范|提交规范|调试技巧|排查|踩坑|最佳实践"
        ),
        "note": "编码规范、重构、测试、Code Review 与工程实践。",
    },
    {
        "slug": "10-职业与行业",
        "priority": 11,
        "weight": 0.6,
        "pattern": (
            r"面试|简历|跳槽|offer|涨薪|职业规划|晋升|校招|社招|大厂|职场|互联网公司|创业|融资|上市|裁员|"
            r"行业观察|技术趋势|技术大会|字节跳动|阿里巴巴|腾讯|美团|工资|薪资|副业|自由职业|远程办公|独立开发|八股|"
            r"领英|linkedin|boss直聘|拉勾|猎聘|招聘"
        ),
        "note": "面试、职业发展与行业动态。",
    },
    {
        "slug": "12-学习资料与文档",
        "priority": 12,
        "weight": 0.35,
        "pattern": (
            r"教程|入门|指南|实战|详解|原理|深入理解|从零开始|学习路线|知识体系|读书笔记|技术书|课程|干货|"
            r"文档|手册|速查|api文档|官网|官方文档|书单|总结|笔记|资料|合集|清单|课件|视频教程"
        ),
        "note": "综合教程、官方文档、书单与学习资料。",
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


def best_category(text: str) -> dict | None:
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
    return best[2] if best else None


def category_by_slug(slug: str) -> dict:
    return next(c for c in CATEGORIES if c["slug"] == slug)


def domain_category(host: str) -> dict | None:
    for slug, domains in DOMAIN_FALLBACK.items():
        for domain in domains:
            if host == domain or host.endswith("." + domain):
                return category_by_slug(slug)
    return None


def classify_primary(item: dict) -> tuple[dict, str] | None:
    """返回 (大类, 判定依据)。依据写进输出，方便人工复核分类是否合理。"""
    category = best_category(item["signal"])
    if category:
        return category, "标题与文件夹"

    category = domain_category(item["host"])
    if category:
        return category, f"域名 {item['host']}"

    category = best_category(item["weak_signal"])
    if category:
        return category, "URL 关键词"
    return None


def classify_secondary(category: dict, item: dict) -> str:
    children = category.get("children")
    if not children:
        return ""

    text = f"{item['signal']} {item['weak_signal']}"
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


def load_fetched(pages_dir: Path) -> dict[str, str]:
    """读抓取状态，给已经存下正文的条目附上本地文件路径。"""
    state_path = pages_dir / ".fetch_state.json"
    if not state_path.is_file():
        return {}
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {
        key: value["path"]
        for key, value in state.items()
        if value.get("status") == "ok" and value.get("path")
    }


def render_items(path: Path, title: str, note: str, items: list[dict], pages_dir: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(items, key=lambda x: x["time"] or "")
    with path.open("w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n")
        f.write(f"- 数量: {len(ordered)}\n")
        if note:
            f.write(f"- 说明: {note}\n")
        f.write("\n")
        for index, item in enumerate(ordered, 1):
            heading = item["title"] or item["url"] or f"书签 {item['id']}"
            f.write(f"## {index}. {heading}\n\n")
            f.write(f"- 链接: {item['url']}\n")
            if item["host"]:
                f.write(f"- 来源: {item['host']}\n")
            if item["folder"]:
                f.write(f"- 书签位置: {item['folder']}\n")
            if item["time"]:
                f.write(f"- 收藏时间: {item['time']}\n")
            if item.get("local"):
                # 正文文件名里有空格，链接要用尖括号包住才不会断
                target = Path(os.path.relpath(pages_dir, path.parent)) / item["local"]
                f.write(f"- 本地正文: [{item['local']}](<{target.as_posix()}>)\n")
            if item.get("reason"):
                f.write(f"- 归类依据: {item['reason']}\n")
            f.write("\n")


def build_readme(path: Path, tree: dict[str, dict], totals: dict[str, int], hosts: Counter) -> None:
    with path.open("w", encoding="utf-8") as f:
        f.write("# 个人知识库：Chrome 书签整理\n\n")
        f.write(f"- 书签总数: {totals['total']}\n")
        f.write(f"- 纳入知识库: {totals['kept']}\n")
        f.write(f"- 已过滤(生活娱乐): {totals['filtered']}\n")
        f.write(f"- 待确认: {totals['pending']}\n")
        f.write(f"- 已抓到本地正文: {totals['fetched']}\n")
        f.write("- 原始导出保留在 `../bookmarks.md` 和 `../bookmarks.json`\n")
        f.write("- 正文 Markdown 在 `../pages/`\n\n")

        f.write("## 目录结构\n\n")
        for slug in sorted(tree):
            node = tree[slug]
            if node["children"]:
                f.write(f"- `{slug}/` {node['note']} 共 {node['count']} 条\n")
                for child_slug in sorted(node["children"]):
                    f.write(f"  - `{child_slug}.md` {node['children'][child_slug]} 条\n")
            else:
                f.write(f"- `{slug}.md` {node['note']} 共 {node['count']} 条\n")

        f.write("\n## 来源域名 Top 20\n\n")
        for host, count in hosts.most_common(20):
            f.write(f"- {host}: {count}\n")

        f.write("\n## 分类规则\n\n")
        f.write("1. 分类信号是「标题 + 书签文件夹路径」，文件夹是用户手工分的类，可靠性和标题相当。\n")
        f.write("2. 标题和文件夹都没信号时，先按域名兜底（github.com 归开源项目、MDN 归前端），再退到 URL 里的英文词。\n")
        f.write("3. 英文缩写（ai、ui、js 等）带词边界匹配，不会命中 `campaign`、随机串里的片段。\n")
        f.write("4. 生活娱乐类进入 `98-已过滤-生活娱乐.md`，可以直接删除。\n")
        f.write("5. 大类按命中关键词数量打分选出，分数相同时按 Web3 > AI > 移动端 > 前端 > 后端 > 基础 的顺序取舍。\n")
        f.write("6. 「学习资料与文档」「职业与行业」是弱信号类，权重被压低，只有没有更明确归属时才会命中。\n")
        f.write("7. 每条都写了「归类依据」，分错了照着依据调 `scripts/build_knowledge_base.py` 里的规则表即可。\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="把 Chrome 书签整理成知识库目录")
    parser.add_argument("--input", default="output/bookmarks.json")
    parser.add_argument("--output", default="output/knowledge-base")
    parser.add_argument("--pages", default="output/pages", help="正文抓取目录，用于关联本地 Markdown")
    parser.add_argument("--clean", action="store_true", help="生成前清空输出目录")
    args = parser.parse_args()

    base = Path(__file__).resolve().parent.parent
    resolve = lambda value: Path(value) if Path(value).is_absolute() else (base / value).resolve()
    input_path = resolve(args.input)
    output_dir = resolve(args.output)
    pages_dir = resolve(args.pages)

    if not input_path.is_file():
        print(f"错误: 找不到输入文件 {input_path}，请先运行 extract_bookmarks.py export")
        return 1

    if args.clean and output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    fetched = load_fetched(pages_dir)
    data = json.loads(input_path.read_text(encoding="utf-8"))
    items = []
    for entry in data:
        item = enrich(entry)
        item["local"] = fetched.get(item["id"], "")
        items.append(item)

    buckets: dict[str, dict] = {}
    filtered: list[dict] = []
    pending: list[dict] = []

    for item in items:
        result = classify_primary(item)
        if result is None:
            reasons = drop_reasons(item)
            if reasons:
                item["drop_reason"] = ", ".join(reasons)
                filtered.append(item)
            else:
                pending.append(item)
            continue

        category, item["reason"] = result
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
                    pages_dir,
                )
                child_counts[child_slug] = len(child_items)
                kept += len(child_items)
            if node["items"]:
                render_items(
                    output_dir / slug / "99-其他.md",
                    f"{slug} / 99-其他",
                    category["note"],
                    node["items"],
                    pages_dir,
                )
                child_counts["99-其他"] = child_counts.get("99-其他", 0) + len(node["items"])
                kept += len(node["items"])
        else:
            render_items(output_dir / f"{slug}.md", slug, category["note"], node["items"], pages_dir)
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
            f.write(f"## {index}. {item['title'] or item['url']}\n\n")
            f.write(f"- 过滤原因: {item.get('drop_reason', '')}\n")
            f.write(f"- 链接: {item['url']}\n")
            if item["folder"]:
                f.write(f"- 书签位置: {item['folder']}\n")
            if item["time"]:
                f.write(f"- 收藏时间: {item['time']}\n")
            f.write("\n")

    render_items(
        output_dir / f"{PENDING_SLUG}.md",
        "待确认",
        "标题、文件夹和域名都不足以判断归属，需要人工过一遍。",
        pending,
        pages_dir,
    )

    hosts = Counter(item["host"] or "(无域名)" for item in items)
    totals = {
        "total": len(items),
        "kept": kept,
        "filtered": len(filtered),
        "pending": len(pending),
        "fetched": sum(1 for item in items if item["local"]),
    }
    build_readme(output_dir / "README.md", tree, totals, hosts)

    print(f"输出目录: {output_dir}")
    print(
        f"总数 {totals['total']} / 纳入 {totals['kept']} / 过滤 {totals['filtered']} / "
        f"待确认 {totals['pending']} / 已有正文 {totals['fetched']}"
    )
    print()
    for slug in sorted(tree):
        node = tree[slug]
        print(f"{node['count']:5d}  {slug}")
        for child_slug in sorted(node["children"]):
            print(f"       {node['children'][child_slug]:5d}  {child_slug}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
