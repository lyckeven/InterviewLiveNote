# -*- coding: utf-8 -*-
"""题目智能解析：粘贴题干 -> 题型 / 审题要点 / 答题结构（各环节）/ 关键要点 / 参考要点

设计原则
1. 本地规则先行：不依赖网络与 API Key，离线可用、结果稳定且可预期；
2. 有 AI 时用 AI 细化题型判定与要点内容，但字段结构必须完整、可编辑；
3. 输出统一字段，前端直接渲染为可编辑表单，确认后一键写入题库。
"""

import json
import re

try:
    import adapt_rules as _adapt     # 本地适配规则（题目识别/答题框架），热更新
except Exception:                    # 适配模块缺失时退回纯内置规则
    _adapt = None

# ---------------- 题型识别 ----------------
# (题型, 权重, 正则)：命中次数 × 权重 得分最高者胜出，同分按 _TYPE_ORDER 顺序取先
_TYPE_RULES = [
    ("情景模拟", 3, r"现场模拟|模拟一下|请模拟|情景模拟|角色扮演|请你以[^。]{0,12}身份|站起来[^。]{0,6}(?:劝说|说说|讲几句)"),
    ("演讲串词", 3, r"演讲|致辞|发言稿|串词|主题词|以[^。]{0,10}为题|发表一段|讲几句话|即兴"),
    ("组织管理", 2, r"组织|开展|策划|筹办|举办|安排|调研|宣传|培训|座谈|接待|评比|试点|方案|负责[^。]{0,10}(?:活动|工作)"),
    ("应急应变", 2, r"突然|忽然|紧急|突发|立即|马上|当场|情绪激动|上访|投诉|围堵|舆情|曝光|冲突|争执|受伤|停电|故障|泄露|事故|值班|如果遇到|出现[^。]{0,8}(?:情况|问题)|隐患|风险|群众反映|居民反映|怎么办|怎么处理|如何处理|怎么解决|如何解决|领导让你[^。]{0,12}(?:处理|负责)"),
    ("人际关系", 2, r"同事|领导|下属|老王|小李|小张|小王|不配合|有情绪|误解|误会|矛盾|抱怨|推诿|排挤|抢功|关系处理"),
    ("自我认知", 2, r"自我介绍|为什么报考|报考动机|缺点|优点|职业规划|岗位匹配|最难忘|最成功|最失败|兴趣爱好|个人经历"),
    ("综合分析", 2, r"你怎么看|怎么看|谈谈你的(?:看法|理解|认识)|启示|有人说|名言|俗语|谚语|政策|现象|如何评价|谈谈认识|说明了什么"),
]
_TYPE_ORDER = ["综合分析", "组织管理", "应急应变", "人际关系", "情景模拟", "自我认知", "演讲串词"]

_TYPE_REASON = {
    "综合分析": "题干要求对某个现象 / 观点 / 政策谈看法，属综合分析类",
    "组织管理": "题干要求策划、组织或推进一项具体工作 / 活动，属组织管理类",
    "应急应变": "题干设置了突发或紧急情形，要求现场处置，属应急应变类",
    "人际关系": "题干围绕与同事、领导、下属或群众的矛盾处理，属人际关系类",
    "情景模拟": "题干要求以特定身份现场对话 / 劝说，属情景模拟类",
    "自我认知": "题干要求谈自身经历、动机或岗位匹配，属自我认知类",
    "演讲串词": "题干要求演讲、致辞或串词，属言语表达类",
}

_TYPE_ALIAS = {
    "综合分析": ["综合分析", "社会现象", "观点分析", "态度观点"],
    "组织管理": ["组织管理", "计划组织", "计划组织协调"],
    "应急应变": ["应急应变", "情景应变", "突发事件"],
    "人际关系": ["人际关系", "人际交往"],
    "情景模拟": ["情景模拟", "情景模拟对话"],
    "自我认知": ["自我认知", "岗位匹配", "求职动机"],
    "演讲串词": ["演讲", "串词", "言语表达"],
}

# ---------------- 答题结构模板 ----------------
# stage 与 simplifier.STAGES 的五环节口径保持一致，便于点评与精简版联动
_TPL = {
    "综合分析": [
        ("审题", "抓住设问与核心争议", [
            "圈定核心话题：{topic}",
            "看清设问是「怎么看」还是「怎么办」，问什么答什么",
            "若出现两种对立说法，先比较合理性再表态",
        ]),
        ("提观点", "亮明态度，一句话定性", [
            "态度要明确：值得肯定 / 需要警惕 / 辩证看待，不含糊",
            "定性理由一句话带过，不复述题干",
            "控制在 1-2 句，避免头重脚轻",
        ]),
        ("重分析", "原因 / 影响 / 本质分层展开", [
            "至少两层角度：个人—社会—政府，或思想—制度—技术",
            "每层落到具体表现，扣住「{topic}」说，不空谈",
            "分析句与对策句分开，不夹带措施",
        ]),
        ("抓落实", "对策给全主体、载体、做法、效果", [
            "每条对策写清谁来做、靠什么载体、怎么做、达到什么效果",
            "对策要与前面分析的问题一一对应，角度数量匹配",
            "补一条可落地的小切口措施，避免只喊口号",
        ]),
        ("结尾", "回扣主题，简短收束", [
            "一句话回扣「{topic}」，不另起新论点",
            "可收在个人行动或长效机制上，1 句即止",
        ]),
    ],
    "组织管理": [
        ("审题", "明确任务目标与限定条件", [
            "明确活动主题、目标人群与规模：{topic}",
            "确认身份与权限边界，别越权承诺",
            "看清限定：时间、经费、形式、必须包含的环节",
        ]),
        ("提观点", "先给整体思路", [
            "一句话说清活动目的与整体打法（如「以调研＋体验＋反馈三步推进」）",
            "点出重点难点，为后面细化做铺垫",
        ]),
        ("重分析", "事前准备：方案、分工、物资", [
            "制定方案：时间地点、流程、预算、责任人",
            "组建工作小组，明确分工与时间节点",
            "做好物资、场地、宣传与通知等保障",
        ]),
        ("抓落实", "事中推进与事后收尾", [
            "事中控流程、控秩序、控突发，设置应急预案",
            "事后收集反馈、形成总结、报到领导",
            "把成果做成可复用的机制或宣传素材",
        ]),
        ("结尾", "回扣效果，简短收束", [
            "用一句效果预期收尾（如「确保活动见效、群众满意」）",
            "可提长效：把一次性活动做成常态机制",
        ]),
    ],
    "应急应变": [
        ("审题", "定性与定级，先稳住", [
            "明确核心矛盾与紧急程度：{topic}",
            "确认自己的身份与可调动资源，不越权",
            "原则：先处理情绪与现场，再处理事情",
        ]),
        ("提观点", "亮明处置原则", [
            "一句话表态：立即响应、以人为本、依法依规",
            "点出处置顺序（先急后缓、先人后事）",
        ]),
        ("重分析", "控制现场，分情况处置", [
            "第一时间到场，稳定情绪、隔离风险、防止扩大",
            "按情况分类处置：能当场解决的当场解决，不能的给时限与承诺",
            "涉及安全、舆情、法律的，及时上报并联动相关部门",
        ]),
        ("抓落实", "善后整改与长效机制", [
            "回访当事人，确认诉求是否解决、是否满意",
            "查原因、堵漏洞，形成书面报告与整改清单",
            "举一反三，把个案问题变成制度性预防",
        ]),
        ("结尾", "回扣处置效果", [
            "一句话收束：问题解决、群众满意、机制完善",
        ]),
    ],
    "人际关系": [
        ("审题", "厘清角色、矛盾与目标", [
            "明确自己的身份与对方的身份：{topic}",
            "找准矛盾点（工作分歧 / 误解 / 利益 / 情绪）",
            "目标：以工作为重，先把事办成，再理顺关系",
        ]),
        ("提观点", "亮明态度", [
            "表态：主动沟通、换位思考、以工作大局为重",
            "不推责、不抱怨、不把矛盾上交",
        ]),
        ("重分析", "找准原因，主动沟通", [
            "从自身找原因：态度、方法、沟通是否到位",
            "选择合适时机与场合当面沟通，先听后说",
            "就事论事，不打标签、不背后议论",
        ]),
        ("抓落实", "落到行动与配合", [
            "明确改进措施：调整分工、补位补台、及时汇报",
            "把关系修复做成持续动作，而非一次表态",
        ]),
        ("结尾", "回扣团结与工作成效", [
            "一句话收束：形成合力、共同把工作做好",
        ]),
    ],
    "情景模拟": [
        ("审题", "定身份、定对象、定目标", [
            "明确我是谁、面对谁、要达成什么：{topic}",
            "判断对方情绪与真实诉求",
            "注意称呼、语气与场合，符合身份",
        ]),
        ("提观点", "开场共情，先接情绪", [
            "用称呼＋共情开场（如「老王，我理解你现在的心情」）",
            "不急于讲道理，先让对方愿意听",
        ]),
        ("重分析", "讲清利害，给出办法", [
            "说明情况与依据，讲清利弊与后果",
            "给出 1-2 个具体可行的解决办法",
            "遇到拒绝时退一步给条件，逐步达成一致",
        ]),
        ("抓落实", "争取承诺，落到行动", [
            "明确下一步怎么做、什么时候做、找谁对接",
            "请对方确认或提出补充意见",
        ]),
        ("结尾", "自然收尾，礼节到位", [
            "用一句得体的话收束，不喊口号",
        ]),
    ],
    "自我认知": [
        ("审题", "看清问的是动机、能力还是规划", [
            "明确设问方向：{topic}",
            "所有回答都要落到「与岗位的匹配」上",
        ]),
        ("提观点", "先给一句总判断", [
            "用一句话概括自己与岗位的匹配点",
        ]),
        ("重分析", "用经历与能力作证", [
            "举 1-2 段真实经历（情境—任务—行动—结果）",
            "把经历提炼成可迁移的能力",
            "结合岗位要求说明为什么适配",
        ]),
        ("抓落实", "正视差距与改进", [
            "坦诚说一条真实短板及正在做的改进",
            "给出入职后的具体努力方向",
        ]),
        ("结尾", "表态简短有力", [
            "一句话表态：愿扎根岗位、踏实做事",
        ]),
    ],
    "演讲串词": [
        ("审题", "定主题、定对象、定场合", [
            "明确主题与身份：{topic}",
            "判断听众与场合，确定语气",
        ]),
        ("提观点", "开场抓人", [
            "用设问、场景或一句金句开场，快速入题",
            "点明主旨，不超过两句",
        ]),
        ("重分析", "主体分层展开", [
            "分 2-3 层展开，每层一句观点＋一个例证",
            "层与层之间用过渡句衔接",
        ]),
        ("抓落实", "落到行动号召", [
            "把主题落到具体行动上，可操作",
            "面向听众发出号召，语气真诚",
        ]),
        ("结尾", "升华收束", [
            "用一句凝练的话升华，呼应开头",
            "不拖沓，30 秒内收住",
        ]),
    ],
}

# ---------------- 关键要点模板 ----------------
_KEYS_TPL = {
    "综合分析": [
        "态度句一句话定性，直接回扣核心话题，不复述题干",
        "分析至少两层角度，每层说清「是什么表现—为什么—影响谁」",
        "对策四要素齐全：主体＋载体＋做法＋效果，且与分析一一对应",
        "结尾一句回扣主题，不另起新论点",
    ],
    "组织管理": [
        "先讲整体思路，再讲事前、事中、事后，顺序不能反",
        "方案要有时间、地点、人员、经费、流程",
        "分工到人、节点到天，避免「大家齐心协力」这类空话",
        "结尾落到效果与长效机制",
    ],
    "应急应变": [
        "先稳情绪与现场，再处置具体事项",
        "分情况处置：能办的马上办，不能办的给时限与承诺",
        "该上报的立即上报，该联动的联动",
        "事后回访＋查原因＋堵漏洞，形成闭环",
    ],
    "人际关系": [
        "以工作为重，先把事情办成，再理顺关系",
        "先从自身找原因，主动沟通，不推责",
        "沟通讲时机、场合与方法，先听后说",
        "落到具体行动与后续配合上",
    ],
    "情景模拟": [
        "称呼＋共情开场，先接住对方情绪",
        "讲清依据与利害，不硬压",
        "给出具体办法，逐条可执行",
        "收尾自然得体，争取对方明确表态",
    ],
    "自我认知": [
        "所有内容都要落到岗位匹配上",
        "经历用情境—任务—行动—结果四步讲透",
        "短板要真实，改进要具体",
        "表态简短，不喊口号",
    ],
    "演讲串词": [
        "开场三十秒内入题，点明主旨",
        "主体 2-3 层，每层观点＋例证",
        "过渡句自然，避免生硬罗列",
        "结尾升华并回扣开头",
    ],
}

# ---------------- 通用易错点 ----------------
_PITFALLS = {
    "综合分析": ["只答背景不答设问", "分析里夹带对策", "对策空洞无主体无载体", "表态含糊、观点不亮"],
    "组织管理": ["只讲流程不讲目标", "环节堆砌没有重点", "缺少分工与应急预案", "结尾没有效果与总结"],
    "应急应变": ["先讲原因后处置、顺序颠倒", "没有分情况处置", "越权承诺、擅作主张", "处置完没有善后与整改"],
    "人际关系": ["只认错不解决问题", "把矛盾上交领导", "背后议论、情绪化表达", "没有落到后续配合"],
    "情景模拟": ["开口就讲道理，不接情绪", "称呼、语气不符合身份", "只劝不安置、没有具体办法", "收尾生硬、没有达成一致"],
    "自我认知": ["通篇形容词没有事实", "经历讲了但不提炼能力", "回避短板显得不真诚", "与岗位要求脱节"],
    "演讲串词": ["开场过长、迟迟不入题", "层次不清、堆砌例子", "语言书面化、不像讲话", "结尾拖沓"],
}


_SIM_HARD_RE = re.compile(r"现场模拟|请模拟|模拟一下|情景模拟|角色扮演")


def detect_qtype(q):
    """按关键词命中次数 × 权重判定题型，返回 (题型, 各类命中明细)"""
    # 「请现场模拟」是硬信号：即使题干里有投诉、情绪激动等应急词，也按情景模拟作答
    if _SIM_HARD_RE.search(q or ""):
        return "情景模拟", {"情景模拟": 9}
    hits = {}
    for name, weight, pat in _TYPE_RULES:
        n = len(re.findall(pat, q or ""))
        if n:
            hits[name] = n * weight
    # 适配规则包的题目识别规则：四步判别命中给硬分，题型关键词按权重加分
    if _adapt is not None:
        try:
            for name, add in (_adapt.detect_bonus(q) or {}).items():
                if name in _TYPE_ORDER:
                    hits[name] = hits.get(name, 0) + float(add)
        except Exception:
            pass
    best, best_score = "", 0
    for name in _TYPE_ORDER:
        score = hits.get(name, 0)
        if score > best_score:
            best, best_score = name, score
    if not best:
        best = "综合分析"
    return best, hits


# ---------------- 文本清洗与要素抽取 ----------------
_QNUM_RE = re.compile(r"^\s*(?:\d{1,3}\s*[\.、)．]|[（(]\s*\d{1,3}\s*[）)]|第\s*\d{1,3}\s*题)\s*")
_TAIL_NOISE_RE = re.compile(r"(考生作答|考生开始答题|考生答题完毕|参考答案|参考要点|答[:：])\s*$")


def clean_question(text):
    """去掉题号、作答提示等噪声，得到干净题干"""
    t = (text or "").replace("\r", "")
    lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
    if not lines:
        return ""
    lines[0] = _QNUM_RE.sub("", lines[0]).strip()
    lines = [ln for ln in lines if not re.match(r"^(?:考生作答|考生开始答题|考生答题完毕|答题开始|答题结束)[:：]?$", ln)]
    out = lines[0]
    for ln in lines[1:]:
        if _TAIL_NOISE_RE.search(ln) and len(ln) < 12:
            continue
        if re.match(r"^(?:参考要点|参考答案|答题思路|要点|类型|题型)\s*[:：]", ln):
            continue
        out += (" " if out and not out.endswith(("。", "？", "！", "；")) else "") + ln
    out = re.sub(r"\s{2,}", " ", out).strip()
    out = re.sub(r"^(?:题目|题干)\s*[:：]\s*", "", out)
    return out.strip()


def count_questions(text):
    """粗判粘贴文本里含几道题（按行首编号 / === 分隔）"""
    t = (text or "").strip()
    if not t:
        return 0
    marks = re.findall(r"(?:^|\n)\s*(?:\d{1,3}\s*[\.、)．]|第\s*\d{1,3}\s*题)", t)
    seps = len(re.findall(r"\n\s*={3,}\s*\n", t))
    return max(len(marks), seps + 1 if seps else 0, 1)


_IDENTITY_RE = [
    re.compile(r"(?:假如|如果|假设)?你是\s*([^，。；;、\n]{2,18}?)(?=[，。；;、\n]|$|的)"),
    re.compile(r"(?:作为|身为)\s*([^，。；;、\n]{2,18}?)(?=[，。；;、\n]|$|的)"),
]
_ASK_RE = [
    ("怎么看", r"你怎么看|怎么看|谈谈你的(?:看法|理解|认识)|如何评价|谈谈认识"),
    ("怎么办", r"怎么办|怎么处理|如何处理|怎么解决|如何解决|你会怎么做|你该怎么做"),
    ("怎么组织", r"怎么组织|如何组织|如何开展|怎么开展|如何策划|请你负责|由你负责"),
    ("现场模拟", r"现场模拟|请模拟|模拟一下"),
    ("谈启发", r"启示|说明了什么|有什么感悟"),
]
_LIMIT_RE = re.compile(r"(不超过\s*\d+\s*分钟|时间\s*\d+\s*分钟|\d+\s*分钟|结合(?:实际|自身|岗位)|联系实际|请分点|请举例|请举一个例子|请现场模拟)[^。；;\n]{0,10}")
_TARGET_RE = re.compile(r"(群众|居民|村民|同事|领导|下属|记者|媒体|企业|家长|学生|顾客|来访群众|办事群众|办事人员|窗口人员|朋友|亲戚)")
_SCENE_RE = re.compile(r"(社区|街道|乡镇|窗口|网络|单位|会场|学校|医院|企业|农村|直播间|信访|执法现场|政务大厅|小区|工地)")
_CONFLICT_RE = re.compile(r"(投诉|上访|情绪激动|不配合|推诿|误解|误会|质疑|冲突|争执|差评|舆情|曝光|形式主义|一刀切|弄虚作假|数据造假|躺平|加码|负担|安全隐患|资金短缺|人手不足|矛盾纠纷)")
_PAIR_RE = re.compile(r"(有人说|有人认为|一种观点|两种看法|一边倒|有赞成的也有反对的)")


def _first(pat_list, text, default=""):
    for rx in pat_list:
        m = (rx if hasattr(rx, "search") else re.compile(rx)).search(text or "")
        if m:
            return (m.group(1) if m.groups() else m.group(0)).strip()
    return default


def _ask_kind(q):
    for name, pat in _ASK_RE:
        if re.search(pat, q):
            return name
    return "怎么看"


_ASK_CUT_RE = re.compile(
    r"(?:你|请问)?(?:怎么看|怎么办|怎么处理|如何处理|怎么解决|如何解决|怎么组织|如何组织|如何开展|怎么开展|"
    r"谈谈你的|谈谈你|请你谈谈|请现场模拟|请模拟|模拟一下|你会怎么做|你该怎么做|有什么启示|给你的启示|你怎么理解)")
_OPINION_CLAUSE_RE = re.compile(r"^(?:有人说|有人认为|一种观点|有赞成的|也有人说)")


def _topic_of(q):
    """取题干核心话题：去掉身份前缀与设问句，取第一个实义分句"""
    s = (q or "").strip()
    s = re.sub(r"^(?:假如|如果|假设)?你是[^，。；;]{2,18}[，,]\s*", "", s)
    m = _ASK_CUT_RE.search(s)
    if m and m.start() >= 4:
        s = s[:m.start()]
    parts = [p.strip(" ，,、；;") for p in re.split(r"[，,；;。！!？?]", s)]
    cand = [p for p in parts if len(p) >= 4 and not _OPINION_CLAUSE_RE.match(p)]
    topic = cand[0] if cand else (parts[0] if parts and parts[0] else s)
    return topic[:28] or s[:28]


def _role_of(name):
    """环节名 → 功能角色（open/opinion/analyze/action/close）"""
    try:
        import simplifier as _simp
        return _simp.role_of(name)
    except Exception:
        return ""


def stage_order(stages=None):
    """当前题型生效的环节顺序；未配置则回退通用五环节"""
    names = [str(x).strip() for x in (stages or []) if str(x or "").strip()]
    if names:
        return names
    try:
        import simplifier as _simp
        return list(_simp.DEFAULT_STAGES)
    except Exception:
        return ["审题", "提观点", "重分析", "抓落实", "结尾"]


def _norm_defs(stage_defs):
    """题型环节定义标准化：[{name, what, criteria, points}]"""
    out = []
    for d in stage_defs or []:
        if isinstance(d, str):
            d = {"name": d}
        if not isinstance(d, dict):
            continue
        name = str(d.get("name") or d.get("stage") or d.get("环节") or "").strip()
        if not name:
            continue
        out.append({
            "name": name,
            "what": str(d.get("what") or d.get("讲什么") or "").strip(),
            "criteria": str(d.get("criteria") or d.get("判断依据") or "").strip(),
            "points": [str(x).strip() for x in (d.get("points") or d.get("常见要点") or []) if str(x).strip()],
        })
    return out


def _fmt(tpl, ctx):
    """模板占位符格式化（未知占位符原样保留，不抛错）"""
    try:
        return str(tpl).format(**ctx)
    except Exception:
        return str(tpl)


def _stage_def_dict(stage_defs):
    out = {}
    for d in _norm_defs(stage_defs):
        out.setdefault(d["name"], d)
    return out


def _ctx(q, qtype):
    identity = _first(_IDENTITY_RE, q)
    target = (re.search(_TARGET_RE, q).group(1) if _TARGET_RE.search(q) else "")
    scene = (re.search(_SCENE_RE, q).group(1) if _SCENE_RE.search(q) else "")
    conflict = (re.search(_CONFLICT_RE, q).group(1) if _CONFLICT_RE.search(q) else "")
    limit = (re.search(_LIMIT_RE, q).group(1) if _LIMIT_RE.search(q) else "")
    return {
        "topic": _topic_of(q),
        "identity": identity,
        "target": target,
        "scene": scene,
        "conflict": conflict,
        "limit": limit,
        "ask": _ask_kind(q),
        "paired": bool(_PAIR_RE.search(q)),
        "qtype": qtype,
    }


def _exam_points(ctx, faults=None, checkpoints=None):
    """审题要点

    faults      : 当前题型配置的「常见失分表现」（题型结构管理里可编辑），优先用作失分提示
    checkpoints : 当前题型配置的「点评检查点」（评价维度），作为逐条自查提示
    """
    pts = []
    pts.append("身份定位：" + (ctx["identity"] if ctx["identity"] else "题干未明确，按一般公职 / 工作人员身份把握，不越权承诺"))
    ask_map = {
        "怎么看": "要求谈看法，重点在分析与观点，不能只讲怎么做",
        "怎么办": "要求给办法，重点在处置与落实，不能只谈原因",
        "怎么组织": "要求组织推进，重点在流程与分工，不能只表态",
        "现场模拟": "要求现场对话，必须用第一人称开口说，不能变成客观分析",
        "谈启发": "要求谈启发，重点在提炼道理并联系实际",
    }
    pts.append("核心任务：" + ask_map.get(ctx["ask"], "看清设问方向，问什么答什么"))
    if ctx["target"] or ctx["scene"]:
        pts.append("对象与场景：" + " / ".join(x for x in (ctx["target"], ctx["scene"]) if x) + "——语言要贴合该对象")
    if ctx["conflict"]:
        pts.append("核心矛盾：「" + ctx["conflict"] + "」是题眼，分析与对策都要围绕它")
    if ctx["paired"]:
        pts.append("题干含对立观点：先比较双方合理与片面之处，再给出自己的立场，不能只挑一边")
    pts.append("限定条件：" + (ctx["limit"] if ctx["limit"] else
                              "未特别限定，按结构化面试 3-4 分钟、%d 个环节完整把握" % len(stage_order(ctx.get("stages")))))
    pts.append("最容易丢分：" + "、".join(
        _as_list_soft(faults)[:3] or _PITFALLS.get(ctx["qtype"], [])[:2]))
    if _as_list_soft(checkpoints):
        pts.append("本题型点评维度：" + "；".join(_as_list_soft(checkpoints)[:4]))
    return pts


def _as_list_soft(v):
    """把字符串 / 列表统一成去空的字符串列表（题型配置里数组与多行文本都兼容）"""
    if isinstance(v, str):
        v = re.split(r"[\n；;]+", v)
    if not isinstance(v, (list, tuple)):
        return []
    return [str(x).strip() for x in v if str(x or "").strip()]


def _structure(ctx, stages=None, stage_defs=None):
    """按题型生效的环节生成答题结构

    stages     : 该题型的答题环节名列表（题型结构管理里配置）
    stage_defs : 该题型的环节定义 [{name, what, criteria, points}]
    两者都没有时回退老的内置模板（通用五环节），保证任何题型都能出结构。
    """
    defs = _norm_defs(stage_defs)
    if defs:
        out = []
        for d in defs:
            pts = []
            if d["what"]:
                pts.append("该环节讲什么：" + _fmt(d["what"], ctx))
            if d["criteria"]:
                pts.append("判断依据：" + _fmt(d["criteria"], ctx))
            for p in d["points"]:
                pts.append(_fmt(p, ctx))
            out.append({"stage": d["name"], "label": d["what"][:24], "points": pts})
        return _merge_adapt_points(out, ctx["qtype"], [d["name"] for d in defs])

    order = stage_order(stages)
    tpl = _TPL.get(ctx["qtype"]) or _TPL["综合分析"]
    base = [{"stage": st, "label": lb, "points": [_fmt(p, ctx) for p in pts]}
            for st, lb, pts in tpl]
    # 未配置环节定义但有环节名：把内置模板按功能角色映射到该题型的环节名
    if stages:
        mapped = []
        for st in order:
            role = _role_of(st)
            hit = None
            for item in base:
                if _role_of(item["stage"]) == role and item not in mapped:
                    hit = item
                    break
            if hit:
                mapped.append({"stage": st, "label": hit["label"], "points": list(hit["points"])})
            else:
                mapped.append({"stage": st, "label": "", "points": []})
        return _merge_adapt_points(mapped, ctx["qtype"], order)
    return _merge_adapt_points(base, ctx["qtype"], order)


def _key_points(ctx):
    keys = list(_KEYS_TPL.get(ctx["qtype"], _KEYS_TPL["综合分析"]))
    ctx = dict(ctx)
    keys.insert(0, "开头一句话点明「" + ctx["topic"] + "」的定性，避免复述题干")
    if ctx["paired"]:
        keys.append("对立观点题必须两边都评，再亮自己的判断，缺一边就是审题不全")
    if ctx["limit"]:
        keys.append("题干有明确限定（" + ctx["limit"] + "），作答必须体现，不能忽略")
    return keys


def analysis_to_ref(a):
    """把结构化解析拼成一份可读、可编辑的「参考要点」文本"""
    lines = []
    lines.append("【题型】" + (a.get("qtype") or "未标注"))
    if a.get("qtype_reason"):
        lines.append("【判定依据】" + a["qtype_reason"])
    lines.append("")
    lines.append("【审题要点】")
    for p in a.get("exam") or []:
        lines.append("· " + p)
    lines.append("")
    lines.append("【答题结构】")
    _CN = ["一", "二", "三", "四", "五", "六", "七"]
    for i, s in enumerate(a.get("structure") or []):
        num = _CN[i] if i < len(_CN) else str(i + 1)
        lines.append(num + "、" + (s.get("stage") or "") + " · " + (s.get("label") or ""))
        for p in s.get("points") or []:
            lines.append("   - " + p)
    lines.append("")
    lines.append("【关键要点】")
    for k in a.get("keys") or []:
        lines.append("· " + k)
    return "\n".join(lines).strip()


def analyze_local(text, qtype_name="", stages=None, stage_defs=None,
                  checkpoints=None, faults=None):
    """纯本地规则解析，永远可用

    qtype_name / stages / stage_defs：当前题目的题型及其答题环节配置，
    用于让「答题结构」按题型走（不再固定五环节）。
    checkpoints / faults：当前题型的点评要素（评价维度 / 常见失分表现），
    用于让「审题要点」里的失分提示与点评维度按题型取值。
    """
    q = clean_question(text)
    qtype, hits = detect_qtype(q)
    if str(qtype_name or "").strip():
        qtype = str(qtype_name).strip()
    ctx = _ctx(q, qtype)
    ctx["stages"] = stage_order(stages)
    data = {
        "question": q,
        "qtype": qtype,
        "qtype_reason": _TYPE_REASON.get(qtype, "") + (
            "（关键词命中：" + "、".join(sorted(hits.keys())) + "）" if hits else "（未命中类型关键词，按综合分析兜底）"),
        "exam": _exam_points(ctx, faults=faults, checkpoints=checkpoints),
        "structure": _structure(ctx, stages=stages, stage_defs=stage_defs),
        "keys": _key_points(ctx),
        "source": "local",
        "extra_questions": max(0, count_questions(text) - 1),
    }
    data["ref"] = analysis_to_ref(data)
    return data


# ---------------- AI 结果合并 ----------------
def _as_list(v):
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str):
        return [x.strip(" ·-—\t") for x in re.split(r"[\n；;]+", v) if x.strip(" ·-—\t")]
    return []


def _as_structure(v):
    out = []
    if not isinstance(v, list):
        return out
    for s in v:
        if not isinstance(s, dict):
            continue
        stage = str(s.get("stage") or s.get("环节") or "").strip()
        label = str(s.get("label") or s.get("小标题") or "").strip()
        points = _as_list(s.get("points") or s.get("要点"))
        if stage or label or points:
            out.append({"stage": stage or "环节", "label": label, "points": points})
    return out


def _extract_json(raw):
    t = (raw or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S)
    try:
        return json.loads(t)
    except Exception:
        pass
    m = re.search(r"\{.*\}", t, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


def _pad_structure(structure, qtype, stages=None, stage_defs=None):
    """把 AI 给出的环节按「当前题型的环节顺序」重排，缺失环节用该题型环节定义补齐

    题型没有配置环节时回退通用五环节顺序。
    """
    order = stage_order(stages)
    defs = _stage_def_dict(stage_defs)
    tpl = _TPL.get(qtype) or _TPL.get("综合分析") or []
    given = {}
    for s in structure or []:
        st = str(s.get("stage") or "").strip()
        if st:
            given.setdefault(st, s)
    # AI 可能仍按老五环节作答：按功能角色把它们映射到当前题型的环节名
    if stages:
        mapped = {}
        for st, item in given.items():
            if st in order:
                mapped.setdefault(st, item)
                continue
            role = _role_of(st)
            for cand in order:
                if _role_of(cand) == role and cand not in mapped:
                    mapped.setdefault(cand, dict(item, stage=cand))
                    break
        given = mapped
    out = []
    for st in order:
        if st in given:
            out.append(dict(given[st]))
            continue
        d = defs.get(st)
        if d:
            pts = []
            if d["what"]:
                pts.append("该环节讲什么：" + d["what"])
            if d["criteria"]:
                pts.append("判断依据：" + d["criteria"])
            pts.extend(d["points"])
            out.append({"stage": st, "label": d["what"][:24], "points": pts})
            continue
        for t in tpl:
            tst, tlabel, tpoints = (list(t) + ["", "", []])[:3]
            if tst == st:
                out.append({"stage": st, "label": tlabel, "points": list(tpoints or [])})
                break
    extra = [s for st, s in given.items() if st not in order]
    out = _merge_adapt_points(out, qtype, order)
    return (out + extra)[:8]


def _merge_adapt_points(structure, qtype, order=None):
    """把适配规则包里该题型的框架要点补进各环节（已有的不重复，每环节最多补 6 条）"""
    if _adapt is None or not structure:
        return structure
    try:
        addons = _adapt.stage_point_addons(qtype, order or [s.get("stage") for s in structure])
    except Exception:
        return structure
    if not addons:
        return structure
    for item in structure:
        st = str(item.get("stage") or "").strip()
        extra = addons.get(st) or []
        if not extra:
            continue
        pts = list(item.get("points") or [])
        added = 0
        for p in extra:
            p = str(p or "").strip()
            if not p or p in pts:
                continue
            if any(p in x or x in p for x in pts):
                continue
            pts.append(p)
            added += 1
            if added >= 6:
                break
        item["points"] = pts
        if added:
            item["adapt"] = True
    return structure


def adapt_hints(qtype, limit=6):
    """该题型的框架提示（环节结构 + 说明），供解析卡与 AI 提示词引用"""
    if _adapt is None:
        return []
    try:
        return (_adapt.framework_notes(qtype) or [])[:limit]
    except Exception:
        return []


def merge_ai(text, raw, qtype_name="", stages=None, stage_defs=None,
             checkpoints=None, faults=None):
    """AI 返回 JSON 字符串 -> 与本地结构合并；解析失败则退回本地结果

    qtype_name / stages / stage_defs：当前题目的题型与其答题环节配置，
    合并时按该题型的环节顺序重排并补齐缺环。
    checkpoints / faults：当前题型的点评要素，本地兜底结果同样按题型取值。
    """
    local = analyze_local(text, qtype_name=qtype_name, stages=stages, stage_defs=stage_defs,
                          checkpoints=checkpoints, faults=faults)
    data = _extract_json(raw)
    if not isinstance(data, dict):
        local["ai_error"] = "AI 返回内容不是合法 JSON，已回退本地解析"
        return local
    qtype = str(data.get("qtype") or "").strip()
    if qtype:
        for std, aliases in _TYPE_ALIAS.items():
            if any(a in qtype for a in aliases):
                qtype = std
                break
        else:
            qtype = local["qtype"]
    else:
        qtype = local["qtype"]
    if str(qtype_name or "").strip():
        qtype = str(qtype_name).strip()      # 以题库/会话里设定的题型为准
    exam = _as_list(data.get("exam") or data.get("审题要点"))
    structure = _pad_structure(_as_structure(data.get("structure") or data.get("答题结构")),
                               qtype, stages=stages, stage_defs=stage_defs)
    keys = _as_list(data.get("keys") or data.get("关键要点"))
    out = {
        "question": clean_question(data.get("question") or text) or local["question"],
        "qtype": qtype,
        "qtype_reason": str(data.get("qtype_reason") or data.get("判定依据") or "").strip() or local["qtype_reason"],
        "exam": exam or local["exam"],
        "structure": structure or local["structure"],
        "keys": keys or local["keys"],
        "source": "ai",
        "extra_questions": local.get("extra_questions", 0),
    }
    out["ref"] = str(data.get("ref") or "").strip() or analysis_to_ref(out)
    return out


def normalize(payload):
    """前端回传的（用户编辑过的）解析结果统一成入库结构"""
    if not isinstance(payload, dict):
        return None
    out = {
        "question": clean_question(payload.get("question") or ""),
        "qtype": str(payload.get("qtype") or "").strip(),
        "qtype_reason": str(payload.get("qtype_reason") or "").strip(),
        "exam": _as_list(payload.get("exam")),
        "structure": _as_structure(payload.get("structure")),
        "keys": _as_list(payload.get("keys")),
        "source": str(payload.get("source") or "edited"),
    }
    out["ref"] = str(payload.get("ref") or "").strip() or analysis_to_ref(out)
    return out
