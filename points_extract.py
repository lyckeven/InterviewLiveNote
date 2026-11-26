# -*- coding: utf-8 -*-
"""学生答题要点抽取（「精简要点」浮层用，纯本地规则，不依赖 AI / 网络）

* analyze(text, qtype, stages, ref, question) -> dict
  1) 按【环节】标签或语义信号把学生作答切成关键词短语（每条 <= max_len 字）
  2) 去掉口头禅 / 语气词 / 重复表述 / 整句复述
  3) 按当前题型的答题结构（审题 / 提观点 / 重分析 / 抓落实 / 结尾……）归组，
     题型没配环节时按语义（入题 / 观点 / 分析 / 对策 / 收尾）归组
  4) 与本题参考要点做 2-gram 相似度比对，标注 答到 / 偏题 / 遗漏

设计原则：只做「压缩 + 归并 + 比对」，不改写学生原意；任何规则失效时降级为
按位置归组，绝不抛异常（调用方无需 try）。
"""

import re
from difflib import SequenceMatcher

import simplifier as simp

MAX_LEN = 20          # 每条要点字数上限（硬上限）
SOFT_LEN = 12         # 短要点的目标长度（「10 字左右」）
REF_MAX_LEN = 24      # 参考要点压缩后的字数上限
GRAM = 2              # 相似度用 2-gram

# 功能角色 → 语义兜底分组名（题型没配环节时用）
ROLE_GROUPS = {
    "open": "入题 / 审题",
    "opinion": "观点 / 态度",
    "analyze": "原因 / 影响分析",
    "action": "对策 / 抓落实",
    "close": "总结 / 结尾",
}
ROLE_ORDER = ["open", "opinion", "analyze", "action", "close"]

# 显式环节标签：【审题】… / 【重分析】…
_TAG_RE = re.compile(r"【\s*([^】]{1,14})\s*】")

# 各角色的语义信号词（在「未清洗」的分句上匹配，避免被清理规则吃掉）
CUES = {
    "open": ("题目", "题干", "问的是", "让我", "谈谈", "怎么看", "怎么理解", "身份", "角色",
             "背景", "矛盾", "关键", "核心", "首先明确", "这个现象", "这个事件", "定位", "任务",
             "问题在于", "前提", "本质是"),
    "opinion": ("我认为", "我觉得", "我的观点", "我的看法", "态度", "点赞", "反对", "支持",
                "肯定", "否定", "不值得", "值得", "提倡", "辩证", "两面", "一刀切", "不可取",
                "认可", "赞同", "是好的", "是不好的", "应该批判", "应当批判", "危害很大",
                "意义重大", "我倾向于", "总体来看"),
    "analyze": ("原因", "因为", "由于", "导致", "造成", "影响", "危害", "意义", "好处", "坏处",
                "一方面", "另一方面", "举例", "比如", "例如", "说明", "反映", "背后", "第一",
                "第二", "第三", "首先", "其次", "再次", "数据", "体现", "长此以往", "深层",
                "从根本上", "之所以", "会使得", "会让人", "不利于", "有助于"),
    "action": ("应该", "需要", "加强", "完善", "建立", "健全", "推进", "落实", "监管", "宣传",
               "培训", "考核", "处罚", "问责", "制度", "机制", "措施", "对策", "建议", "要",
               "可以", "必须", "通过", "开展", "优化", "提升", "保障", "引导", "规范", "政府",
               "相关部门", "平台", "学校", "企业要", "抓好", "细化", "多措并举"),
    "close": ("总之", "综上", "总而言之", "最后", "相信", "未来", "希望", "作为", "我会",
              "升华", "展望", "总结", "收尾", "归根结底", "说到底", "让我们"),
}

# 纯垫场 / 客套 / 空指代（整条丢弃，不当作要点）
_NOISE = {
    "各位考官", "考官好", "各位考官好", "谢谢考官", "回答完毕", "以上就是我的回答", "以上是我的回答",
    "关于这道题", "这道题目", "这道题", "这个题目", "对于这道题", "下面我谈", "下面谈谈",
    "谈一下", "谈一下我的看法", "我的看法", "我的观点", "看法", "想法", "意思", "的话",
    "原因", "理由", "一方面", "另一方面", "另一", "首先", "其次", "最后", "然后", "而且",
    "的是", "这是", "这样", "那样", "那么", "嗯", "额", "就是", "这个", "那个", "什么",
    "东西", "时候", "情况", "问题", "方面", "地方", "这里", "那里", "我们", "大家",
    "感觉到", "感觉", "我自己", "自身", "更", "很", "太", "非常",
}

# 需要整体删除的软性修饰（不承载要点信息）
_SOFT_RE = re.compile(
    r"(?:非常|特别|比较|十分|相当|有点儿|一点|一些|有些|许多|很多|各种各样|各种|"
    r"相关|有关|方面|的话|什么的|之类的|等等|这个样子|这样子|那样|怎么样|的话说|"
    r"基本上|大致|大概|可能|似乎|应该是说)"
)

# 分句开头可安全剥离的冗余引导语（可重复剥离）
_LEAD_RE = re.compile(
    r"^(?:(?:所以|因此|那么|而且|并且|同时|另外|此外|然后|接着|其次|再次|首先|最后|"
    r"就是|也就是说|即|"
    r"总之|综上|总而言之|其实|实际上|当然|毕竟|总的来说|总的来说|"
    r"一方面|另一方面|另一|以及|并|而且|再者|"
    r"我觉得|我认为|我想说|我想|我们说|我们知道|大家看|咱们|我们|大家|我|你|"
    r"要|应该|应当|需要|必须|可以|能够|会|得|去|来|还要|也要|又要|继续|进一步|都需要|都能|"
    r"第[一二三四五六七八九十]{1,3}(?![时间步线天期])[、.，:：)）]?)+)"
)

# 分句开头的转述 / 判断动词（不承载要点本身）
_LEAD_VERB_RE = re.compile(r"^(?:说明|体现|反映|可以看出|能看到|意味着|也就是说|可以说|我感觉|感觉|"
                           r"我想说|我想|我认为|我觉得|由此可见|这样的话)")

# 分句开头的第二类冗余引导（「所以说」「就是说」「对于这个问题」）
_LEAD_EXTRA_RE = re.compile(
    r"^(?:(?:所以|那么|就|也|还|都)?说(?=[我咱大你要应可需这那他她它])|"
    r"对于(?:这个|这|该|那个)?(?:问题|题目|现象|事情|事件|情况)|"
    r"关于(?:这个|这|该)?(?:问题|题目|现象|事情|事件|情况)|"
    r"针对(?:这个|这|该)?(?:问题|题目|现象|事情|事件|情况)|"
    r"从(?:总体上|整体上)?(?:来看|来说)|"
    r"作为(?:一名|一个)?(?:公务员|工作人员|基层干部|干部|执法人员))"
)

# 内部「的」压缩：「完善的制度」→「完善制度」，让短语更接近关键词
_DE_RE = re.compile(r"(?<=[\u4e00-\u9fa5])的(?=[\u4e00-\u9fa5]{2})")

# 「X 是 Y」→ 取 Y（X 只是指代 / 铺垫时）
_BE_SPLIT_RE = re.compile(r"^(.{1,6}?)是(.{4,})$")

# 开场的客套 / 指代铺垫（整条丢弃）
_NOISE_HEAD_RE = re.compile(
    r"^(?:各位考官|考官|谢谢|关于这道题|关于这个题|对于这道题|对于这个题|这道题|这个题目|这个现象|"
    r"谈一下|谈一谈|说一下|说一说|讲一下|下面我|接下来我|首先我|我想谈|我谈一下|就这道题)"
)

# 与上一句连读的动词起头（"…让群众对我们工作" + "产生不信任"）
_CONT_HEAD_RE = re.compile(r"^(?:产生|造成|导致|使得|带来|影响|出现|引发|形成|让|使|从而|进而|就会|会|就)")

# 角色判定优先级：同分时更具体的角色优先（分析 / 对策），避免被「入题」抢走
_ROLE_PRIORITY = ["analyze", "action", "opinion", "close", "open"]

# 字符重叠判定时不计入的虚词
_STOP_CHARS = set("的了是在和与及或而就都也还又要会能可以我你他她它这那有个们对把给被从到为以之其很太更最不没")

# 结尾无信息量后缀（「要 / 一下 / 的话 / 是什么」等口语余量，结尾绝不能挂在上面）
_TAIL_RE = re.compile(
    r"(?:的|了|吧|呢|啊|呀|嘛|哦|等|等等|方面|的话|这样|那样|的时候|而已|之类|来说|来看|"
    r"要|得|一下|一下子|行不行|可不可行|好不好|是什么|干什么|什么|怎么样)+$"
)

# 参考要点里的编号前缀
_REF_NO_RE = re.compile(
    r"^(?:(?:[（(][一二三四五六七八九十\d]{1,3}[）)]|"
    r"[一二三四五六七八九十]{1,3}[是、.．:：]|"
    r"第[一二三四五六七八九十\d]{1,3}[点条是、.．:]|"
    r"\d{1,3}[、.．)）]|[①-⑩])\s*)"
)

_CN_RE = re.compile(r"[\u4e00-\u9fa5A-Za-z0-9%]")

STATUS_HIT = "答到"
STATUS_OFF = "偏题"
STATUS_MISS = "遗漏"


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------
def _nz(s):
    """归一化：只留汉字 / 字母 / 数字，用于相似度与去重"""
    return "".join(_CN_RE.findall(str(s or "")))


def _grams(s):
    s = _nz(s)
    if len(s) < GRAM:
        return {s} if s else set()
    return {s[i:i + GRAM] for i in range(len(s) - GRAM + 1)}


def _sim(a, b):
    """2-gram 覆盖率相似度（0~1），短词包含关系直接判满"""
    a, b = _nz(a), _nz(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if len(a) >= 3 and a in b:
        return 1.0
    if len(b) >= 3 and b in a:
        return 1.0
    ga, gb = _grams(a), _grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / max(1, min(len(ga), len(gb)))


def _osim(a, b):
    """字符级覆盖相似度（去掉虚词后），补 2-gram 抓不到的同义近邻（长效机制 ≈ 长效治理）"""
    sa = {c for c in _nz(a) if c not in _STOP_CHARS}
    sb = {c for c in _nz(b) if c not in _STOP_CHARS}
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / max(1, min(len(sa), len(sb)))


def _cut(s, n):
    """截到 n 字以内；优先在虚词 / 介词处断开，避免砍在词中间"""
    s = str(s or "")
    if len(s) <= n:
        return s
    head = s[:n]
    for i in range(len(head) - 1, max(2, int(n * 0.55)) - 1, -1):
        if head[i] in "的了在对把给让是和与跟及":
            head = head[:i]
            break
    head = _TAIL_RE.sub("", head)
    return head.strip()


# --------------------------------------------------------------------------
# 一、清洗与关键词压缩
# --------------------------------------------------------------------------
def _clean(raw):
    """去口头禅 / 语气词 / 结巴（复用精简版的清理规则）"""
    try:
        return (simp._clean_clause(raw or "") or "").strip()
    except Exception:
        return str(raw or "").strip()


def _kw(raw, max_len=MAX_LEN):
    """把一个分句压成关键词短语；压不出有效内容返回空串"""
    c = _clean(raw)
    if not c:
        return ""
    if _NOISE_HEAD_RE.match(c):
        return ""
    for _ in range(4):
        c2 = _LEAD_VERB_RE.sub("", c)
        c2 = _LEAD_EXTRA_RE.sub("", c2)
        c2 = _LEAD_RE.sub("", c2)
        if c2 == c:
            break
        c = c2
    c = c.strip()
    if not c:
        return ""
    # 「这是很不好」这类只有指代 + 评价的词，不够成要点
    m0 = re.match(r"^这(?:就)?是(.+)$", c)
    if m0:
        if len(_nz(m0.group(1))) <= 4:
            return ""
        c = m0.group(1)
    # 「它带来的危害是会影响政府的公信力」→「影响政府公信力」
    m = _BE_SPLIT_RE.match(c)
    if m and m.group(1) not in ("就", "这", "那", "也", "都", "还", "但"):
        c = m.group(2)
    c = _SOFT_RE.sub("", c)
    # 「的时候」→「时」、「的一个体现」→「体现」：口语结构在关键词里没有信息量
    c = c.replace("的时候", "时").replace("的一个", "")
    c = _DE_RE.sub("", c)
    c = _TAIL_RE.sub("", c)
    c = "".join(_CN_RE.findall(c))
    c = _TAIL_RE.sub("", c)
    if c in _NOISE or len(_nz(c)) < 2:
        return ""
    if re.fullmatch(r"[的了是在和与及或而被把给让对从到于也就都还又更很太]", c):
        return ""
    return _cut(c, max_len)


# 「把 X V」→「V X」：让口语表述变成动宾短语
_VERB_WORDS = (
    "控制|处置|处理|解决|化解|落实|抓好|做好|加强|完善|推动|推进|提升|规范|建立|健全|"
    "清理|整治|维护|保障|优化|引导|查处|整改|打击|排查|核实|登记|安抚|疏导|疏散|救治|"
    "上报|沟通|回应|宣传|培训|考核|监督|执行|制定|出台|修订|明确|压实|兜底|稳住|稳控|"
    "管好|办实|赶到|赶赴|到达|组织|协调|安排|部署|汇报|反馈|跟踪|评估|追责|慰问|赔付|"
    "修复|恢复|研究|讨论|决定|督促|检查|调研|走访|回访|公示|公开|发布|解释|劝说|制止|"
    "防止|避免|减少|降低|提高|增强|改善|改进|促进|带动|激发|调动|锁定|制止|"
    "倾听|听|查清|查|询问|问|了解|摸清"
)
_BA_RE = re.compile(
    r"把([\u4e00-\u9fa5]{1,10}?)((?:%s)(?:到位|到|至|入|向|于|住|好|了)?)" % _VERB_WORDS
)
# 把字句转写后的动词规范化（「听群众诉求」→「倾听群众诉求」）
_BA_VERB_FIX = {"听": "倾听", "查": "查清", "问": "询问"}
# 动词以后置介词收尾（「落实到」）时保持原序：「任务落实到部门」
_BA_PREP = ("到", "至", "给", "入", "向", "于")
_BA_SUF_RE = re.compile(r"^(控制|稳控|管|抓|看|压|顶|拦|挡|捂|按)住$")

# 冗余主语 / 人称（不含「自我」）
_SUBJ_RE = re.compile(r"(?<!自)我(?:们)?|咱们|大家|本人|你(?:们)?|咱|他们|她们|它们|(?<!其)他|(?<!其)她")

# 原因 / 铺垫句式的前缀（「原因在于 X」→「X」）
_CAUSE_RE = re.compile(
    r"^(?:原因在于|原因是因为|原因是|原因就是|主要是因为|关键在于|问题的关键在|"
    r"因为|是因为|是由于|是在于)"
)

# 示范 / 指代性铺垫（「这样做会…」里的「这样做」没有信息量）
_DEMO_RE = re.compile(r"^(?:这样做|这么做|这样干|这样一来|如此|这个样子|这样子|"
                      r"这件事情|件事|这个事|这种事|这些事|这件事|这事)")

# 句中弱义修饰（承载语气不承载要点，句中任意位置可删）
_WEAK_MID_RE = re.compile(
    r"(?:认真地|认真的|认真|仔细地|仔细|好好地|好好|尽量|尽可能|亲自|挨个|逐一|耐心地|耐心|"
    r"清楚|到底|究竟|以后|之后)"
)

# 口语余量词（句中任意位置可删，删除后不影响语义）
_RESIDUE_RE = re.compile(r"(?:一下子|一下|一点儿|一点|的话|什么的|之类的|之类的吧)")

# 动词规范化：「会影响到 X」→「影响 X」，避免「到 / 会 / 是」这些成分占字数
_VERB_TIDY = (
    (re.compile(r"(?:会)?影响到"), "影响"),
    (re.compile(r"(?:会)?造成到"), "造成"),
    (re.compile(r"(?:会)?导致到"), "导致"),
    (re.compile(r"^会使得"), "导致"),
    (re.compile(r"^会让"), "使"),
    (re.compile(r"(?<=[\u4e00-\u9fa5])来做(?=[\u4e00-\u9fa5])"), "做"),
    (re.compile(r"(?<=[\u4e00-\u9fa5])去看(?=[\u4e00-\u9fa5])"), "查看"),
    (re.compile(r"反映了"), "反映"),
    (re.compile(r"体现出"), "体现"),
    (re.compile(r"表现出"), "表现"),
    (re.compile(r"工作中(?=[\u4e00-\u9fa5])"), "工作"),
)

# 同句内重复实词 → 近义凝练表达（删除后不成句时才替换，见 _fold_repeat）
_SYN_WORD = {
    "现场": "场面",
    "情况": "局面",
    "事情": "事项",
    "部位": "点位",
}
# 重复词后面紧跟这些字说明已经是另一句 / 另一宾语，不能直接删
_LINK_CHARS = set("跟和与及并然而同对向给把被让使就也还都再从到为以之其")

# 介词性「跟」→「与」（「跟同事沟通」→「与同事沟通」）
_GEN_RE = re.compile(r"跟(?=同事|群众|领导|百姓|家长|上级|部门|单位|对方|企业|居民|村民|记者)")

# 从属分句（「让大家都能重视起来」）：主句已有时不再单独拼接
_SUB_HEAD_RE = re.compile(r"^(?:让|使得|使|令|从而|进而|以此)")

# --------------------------------------------------------------------------
# 并列动宾拆分：一个分句里用连接词串起两套「动词 + 宾语」时，不能合成一条
#   例：「赶到现场把现场控制住跟同事沟通一下情况」（已去掉逗号）
#        → 「赶到现场控制场面」「与同事沟通情况」两条
# 判断依据：句中「与/和/跟/并/同时/还要」等并列连接词，且连接词两侧各自动宾齐全
# --------------------------------------------------------------------------
_COORD_MULTI_RE = re.compile(
    r"(?:同时还要|同时也要|同时还需|同时还|同时|并且|并要|还要|也要|还需|以及|另外|此外|"
    r"再就是|然后|接着|再者|还有|再(?!次|见|来|生|说)|并|、)"
)
# 单字连接词只在明确是「并列两套动宾」时才切（避开 跟进 / 与其 / 同意 等词内字）
_COORD_SINGLE_RE = re.compile(
    r"(?:和(?![谐平])|与(?!其)|跟(?!进|踪|随|着|上|下|据|前)|同(?![时志意样行事])|及(?![时其]))"
)
# 连接词后面紧跟的对象名（说明这是介词用法「跟同事沟通」，不是并列）
_COORD_OBJ_RE = re.compile(
    r"^(?:领导|同事|群众|家属|部门|单位|上级|对方|企业|居民|村民|家长|百姓|记者|医院|公安|"
    r"社区|学校|专家|老师|员工|客户|媒体|相关|有关)"
)
# 切分用动词表：现有动词表 + 口语高频动作（含「维持 / 形成 / 报告」这类易漏词）
_COORD_VERB_WORDS = tuple(dict.fromkeys(
    [w for w in _VERB_WORDS.split("|") if len(w) >= 2] +
    ("维持 形成 完成 开展 进行 跟进 报告 出示 核实 测量 拍照 录像 记录 报警 备案 排查 巡查 "
     "查看 走访 陪同 看望 答复 解答 承诺 兑现 请示 通知 安置 转移 抢救 送医 登记 宣传 引导 解释 "
     "劝说 调解 取证 处罚 检修 更换 补给 增援 支援 值守 待命 联系 拨打 疏散 设置 稳控 提示").split()
))
_COORD_VERB_RE = re.compile(
    "(?:%s)" % "|".join(sorted(_COORD_VERB_WORDS, key=len, reverse=True))
    + r"|问(?!题)|查(?!看)|听(?!到)|讲|说|谈|去|来|做|送|带|找|写|填|签|拦|堵|搬|换|修|收|交|放|拿|抓|管|促|推|"
    + r"减|降|提|增|救|治|劝|请|叫|擦|扫|抬|装|卸|镇|守|护|拉|挂|摆|贴|拍|查|清|清点"
)
# 连接词紧贴对象名时改写成「与 X V」，读起来仍是动宾短语
_COORD_PERSON_RE = re.compile(
    r"^(?:和|跟|与|同)(?=(?:领导|同事|群众|家属|部门|单位|上级|对方|企业|居民|村民|家长|百姓|"
    r"记者|医院|公安|社区|学校|专家|老师|员工|客户|媒体))"
)
# 「疏散人员开」这类把字句残留的「开」收尾
_COORD_TAIL_OPEN_RE = re.compile(r"(?<=[^公开离召展张敞])开$")
# 分句以「跟同事 / 和领导 / 与群众…」起头：这是另一套并列动宾，须单独成条，不能并进上一条
_PREP_HEAD_RE = re.compile(
    r"^(?:跟|和|与|同|对|向|给)(?:领导|同事|群众|家属|部门|单位|上级|对方|企业|居民|村民|家长|"
    r"百姓|记者|医院|公安|社区|学校|专家|老师|员工|客户|媒体|相关|有关|他|她|他们)"
)
# 分句以「并且 / 同时 / 还要…」起头：并列的另一套动宾，同样单独成条
_COORD_HEAD_RE = re.compile(
    r"^(?:并且|并(?![非且])|同时|还有|还要|还|以及|又|再|接着|然后|随后|另外|其次)"
)


def _has_verb(s):
    """判断这一侧是否含动作（用于确认连接词两侧各自动宾齐全）"""
    return bool(_COORD_VERB_RE.search(str(s or "")))


def _split_coord(seg):
    """按并列连接词把分句切成多个动宾片段；两侧不同时含动词时不切"""
    seg = str(seg or "")
    if len(_nz(seg)) < 6:
        return [seg]
    hits = [(m.start(), m.end(), m.group(0)) for m in _COORD_MULTI_RE.finditer(seg)]
    hits += [(m.start(), m.end(), m.group(0)) for m in _COORD_SINGLE_RE.finditer(seg)]
    if not hits:
        return [seg]
    hits.sort()
    parts, last = [], 0
    for idx, (st, en, txt) in enumerate(hits):
        if st <= last:
            continue                        # 连接词在句首或紧贴上一处切点
        left = seg[last:st]
        right = seg[en:]                     # 用剩余全文判定，避开「同时还要跟家属…」叠用连接词
        if len(txt) == 1 and _COORD_OBJ_RE.match(right):
            # 「跟同事沟通」：左侧已有完整动宾时才算并列出新要点
            if not _has_verb(_COORD_OBJ_RE.sub("", right)):
                continue
        if _has_verb(left) and _has_verb(right):
            parts.append(seg[last:st])
            last = st if len(txt) == 1 else en   # 单字连接词保留在右侧（「跟同事沟通」）
    if not parts:
        return [seg]
    tail = seg[last:]
    if _nz(tail):
        parts.append(tail)
    return [x for x in parts if _nz(x)]


def _coord_head_fix(g):
    """「和领导汇报」→「与领导汇报」；非人称呼应时去掉句首连接词"""
    g = str(g or "")
    m = _COORD_PERSON_RE.match(g)
    if m:
        return "与" + g[m.end():]
    g2 = re.sub(r"^(?:和|跟|与|以及|并|还要|同时)+", "", g)
    return g2 if len(_nz(g2)) >= 2 else g


def _brief_multi(raw, max_len=MAX_LEN):
    """把口语长句压成一条或多条要点（含多套并列动宾时拆成多条）"""
    t = _SUBJ_RE.sub("", str(raw or ""))
    raw_segs = [s for s in re.split(r"[，,。；;！!？?：:…\s]+", t) if s.strip()]
    # 分句是否以并列/介词头起头，必须在去口头语之前判定（「然后」「接着」会被 _FILL_RE 去掉）
    heads = [bool(_PREP_HEAD_RE.match(s) or _COORD_HEAD_RE.match(s)) for s in raw_segs]
    pairs = []
    for _s, _h in zip(raw_segs, heads):
        _c = _SAY_RE.sub("", _FILL_RE.sub("", _s))
        if _c.strip():
            pairs.append((_c, _h))
    soft = min(max_len, SOFT_LEN + 4)
    out, buf = [], []

    def flush():
        """把缓冲区里的次要分句合成一条要点落盘"""
        if not buf:
            return
        c = _fold_repeat("".join(buf))
        c = _GEN_RE.sub("与", c)
        if len(_nz(c)) > SOFT_LEN:
            for _ in range(2):
                c2 = _WEAK_MID_RE.sub("", c)
                c2 = _RESIDUE_RE.sub("", c2)
                if c2 == c:
                    break
                c = c2
        c = _TAIL_RE.sub("", c)
        c = _DANGLE_RE.sub("", c)
        c = _cut(c, max_len)
        if c and c not in out:
            out.append(c)
        del buf[:]

    for seg, head in pairs:
        if _SUB_HEAD_RE.match(seg.strip()):
            continue                        # 「让大家都能重视起来」等从属分句不成条
        if head:
            flush()                         # 句首「跟同事…」「并且…」是另一套动宾，先落上一套再单独成条
        parts = _split_coord(seg)
        if len(parts) > 1:
            flush()
            got = []
            for part in parts:
                g = _brief_seg(part)
                if not g:
                    continue
                g = _coord_head_fix(g)
                g = _COORD_TAIL_OPEN_RE.sub(lambda m: m.group(0)[:-1], g)
                if part.startswith("及时") and g.startswith("时"):
                    g = "及" + g
                g = _GEN_RE.sub("与", g)
                g = _fold_repeat(g)
                g = _cut(g, max_len)
                if g and g not in out and g not in got:
                    got.append(g)
            out.extend(got)
        else:
            g = _brief_seg(seg)
            if not g or g in buf:
                continue
            if buf and sum(len(_nz(x)) for x in buf) + len(_nz(g)) > soft:
                continue                    # 超长丢次要分句，不做词中截断
            buf.append(g)
    flush()
    res = []
    for x in out:
        if x and x not in res:
            res.append(x)
    return res

# 疑似指代主语（「它/这/那」起头无信息量）
_PRON_RE = re.compile(r"^(?:它|他|她|这|那)(?=[\u4e00-\u9fa5]{2,})")

# 口头禅 / 垫场词（句中任何位置都能删）
_FILL_RE = re.compile(
    r"(?:嗯+|呃+|额+|啊+|呀+|吧+|呢+|嘛+|哦+|喔+|唉+|哎+|诶+|额|就是|那个|这个|然后呢|然后|"
    r"就是说|反正|其实|基本上|总的来说|的话|什么的|之类的)"
)

# 转述 / 认知动词（「我觉得」「认为」「发现」本身不承载要点）
_SAY_RE = re.compile(r"(?:我个人)?(?:觉得|认为|感觉|发现|看到|知道|听说|想说的是|想说|说是|讲)")

# 分句开头的语气副词 / 情态动词（不承载要点信息，可反复剥离）
_ADV_RE = re.compile(
    r"^(?:先|再|又|还|也|就|才|都|得|要|应该|应当|需要|必须|可以|能够|会|去|来|继续|"
    r"进一步|尽快|马上|立刻|立即|第一时间|主动|积极|认真|全面|切实|严格|坚决|"
    r"持续|不断|统筹|协同|多措并举|总的来说|总体来看|这样|那么|所以|因此|然后|接着|"
    r"因为|由于|而|但|但是|另外|此外|同时|而且|并且)+"
)

# 「把 X V」→「V X」后仍偏长的连接成分
_JOIN_RE = re.compile(r"(?:并且|而且|同时|还要|也要|以及|另外|此外|再|就|也|都|还)+")


# 收尾悬空的单字介词 / 连词（「反馈给」→「反馈」）
_DANGLE_RE = re.compile(r"(?:给|对|向|于|和|与|并|就|也|都|把|被|将|为|从|在)$")


def _brief_seg(seg):
    """压缩单个分句（已去口头语与主语），返回动宾短语"""
    c = _kw(seg, max_len=200)
    if not c:
        return ""
    c = _DEMO_RE.sub("", c)
    # 弱义修饰 / 口语余量先清掉，否则「把群众的诉求认真听一下」拆不出动宾
    for _ in range(2):
        c2 = _WEAK_MID_RE.sub("", c)
        c2 = _RESIDUE_RE.sub("", c2)
        if c2 == c:
            break
        c = c2
    for _re_, _to in _VERB_TIDY:
        c = _re_.sub(_to, c)
    m = _BA_RE.search(c)
    if m:
        obj = m.group(1)
        verb = _BA_SUF_RE.sub(r"\1", m.group(2))
        verb = _BA_VERB_FIX.get(verb, verb)
        rest = c[m.end():]
        if verb.endswith(_BA_PREP):
            # 「把任务落实到具体的部门」→「任务落实到部门」（原序，主干仍清晰）
            c = c[:m.start()] + obj + verb + rest
        else:
            c = c[:m.start()] + verb + obj + rest
    for _ in range(2):
        c2 = _ADV_RE.sub("", c)
        if c2 == c:
            break
        c = c2
    c = _CAUSE_RE.sub("", c)
    c = _PRON_RE.sub("", c)
    c = _JOIN_RE.sub("", c).replace("的时候", "时")
    c = _DE_RE.sub("", c)
    c = _SOFT_RE.sub("", c)
    c = _RESIDUE_RE.sub("", c)
    c = _TAIL_RE.sub("", c)
    c = "".join(_CN_RE.findall(c))
    # 尾部反复清两轮：先剥虚词后缀，再剥悬空介词，避免「…群众要」「…反馈给」
    for _ in range(3):
        c2 = _TAIL_RE.sub("", c)
        c2 = _DANGLE_RE.sub("", c2)
        c2 = _ADV_RE.sub("", c2)
        if c2 == c:
            break
        c = c2
    if len(_nz(c)) < 2 or c in _NOISE:
        return ""
    if re.fullmatch(r"[的了是在和与及或而被把给让对从到于也就都还又更很太]", c):
        return ""
    return c


def _fold_repeat(s, allow_drop=True):
    """同一短语内重复出现的实词：能删则删，删完不成句则换成近义凝练表达

    「到达现场维护现场秩序」→「到达现场维护秩序」
    「赶到现场控制现场」→「赶到现场控制场面」
    allow_drop=False 时只做替换、不删词（跨分句拼接后使用，避免删掉另一分句的宾语）
    """
    s = str(s or "")
    if len(s) < 4:
        return s
    for size in (3, 2):
        i = 0
        while i + size <= len(s):
            w = s[i:i + size]
            if len(set(w)) == 1:
                i += 1
                continue
            j = s.find(w, i + size)
            if j < 0:
                i += 1
                continue
            tail = s[j + size:]
            # 后面还有完整宾语、且不是被连接词接走另一句时，删掉重复词不会破句
            if (allow_drop and len(tail) >= 2 and tail[0] not in _LINK_CHARS
                    and not _TAIL_RE.search(tail)):
                s = s[:j] + tail
                continue
            rep = _SYN_WORD.get(w)
            if rep and rep != w:
                s = s[:j] + rep + tail
            i += size
    return s


def _brief(raw, max_len=MAX_LEN):
    """单条要点：并列动宾已在 _brief_multi 里拆条，这里取第一条兼容旧调用"""
    kws = _brief_multi(raw, max_len)
    return kws[0] if kws else ""


def _split_by_tags(text):
    """按【环节】标签切块 → [(标签, 正文)]"""
    text = str(text or "").replace("\r", "\n")
    parts = _TAG_RE.split(text)
    out = []
    if not parts:
        return ([("", text)] if text.strip() else [])
    out.append(("", parts[0]))
    i = 1
    while i < len(parts):
        tag = (parts[i] or "").strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        out.append((tag, body))
        i += 2
    return out


def _split_clauses(text, soft=True):
    """按标点切分句，返回 [(句, 句末标点)]

    soft=True 时把换行视为「软换行」（仅去空白，不当作句子结束），适合学生逐字稿
    里被硬折行打断的句子；soft=False 时换行算句子结束，适合参考要点逐条一行的形态。
    """
    t = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if soft:
        t = re.sub(r"[ \t\u3000]*\n+[ \t\u3000]*", "", t)
    else:
        t = t.replace("\n", "。")
    out, buf = [], ""
    for ch in t:
        if ch in "，。！？；：、,.!?;:":
            if buf.strip():
                out.append((buf.strip(), ch))
            buf = ""
        else:
            buf += ch
    if buf.strip():
        out.append((buf.strip(), ""))
    return out


def _match_stage(tag, stages):
    """把标签匹配到当前题型的环节名（完全相同 / 互为子串 / 角色一致）"""
    tag = _nz(tag)
    if not tag:
        return ""
    for nm in stages:
        n = _nz(nm)
        if n and (n == tag or n in tag or tag in n):
            return nm
    # 标签只写了「开篇 / 切入」这类同义说法时，按角色映射
    role = simp.role_of(tag)
    if role:
        for nm in stages:
            if simp.role_of(nm) == role:
                return nm
    return ""


def _role_of_text(raw):
    """按语义信号词给分句定角色，返回 (role, score)"""
    t = str(raw or "")
    scores = {r: sum(len(w) for w in CUES[r] if w in t) for r in ROLE_ORDER}
    best, hit = "", 0
    for r in _ROLE_PRIORITY:
        if scores.get(r, 0) > hit:
            best, hit = r, scores[r]
    return best, hit


def _pos_role(ratio):
    """没有任何语义信号时，按出现位置兜底归类"""
    if ratio < 0.18:
        return "opinion"
    if ratio >= 0.85:
        return "close"
    return "analyze" if ratio < 0.62 else "action"


# --------------------------------------------------------------------------
# 二、参考要点
# --------------------------------------------------------------------------
def ref_points(ref, max_len=REF_MAX_LEN):
    """把参考要点原文压成关键词清单（去编号、去修饰、去重）"""
    out = []
    # 单行「1.xxx 2.yyy 3.zzz」用空格分隔时，先把编号前的空格变成分句符，
    # 否则编号会被 _REF_NO_RE 抹掉后残留在上一条要点尾部（如「…情况3」）
    ref = re.sub(r"\s+(?=\d{1,2}\s*[.、)）](?!\d))", "；", str(ref or ""))
    for raw, _p in _split_clauses(ref, soft=False):
        c = _clean(raw)
        if not c:
            continue
        c = _REF_NO_RE.sub("", c).strip()
        for piece in re.split(r"[，,；;、]", c):
            piece = _clean(piece)
            if not piece:
                continue
            piece = _SOFT_RE.sub("", piece)
            piece = re.sub(r"^(?:要|应该|应当|需要|必须|可以|能够|做到|做好|坚持|注重)+", "", piece)
            piece = _TAIL_RE.sub("", piece)
            piece = "".join(_CN_RE.findall(piece))
            if len(_nz(piece)) < 3:
                continue
            piece = _cut(piece, max_len)
            if any(_sim(piece, x) >= 0.82 for x in out):
                continue
            out.append(piece)
    return out


def _dedup_points(items):
    """按相似度去重（保留先出现的、信息量更大的那条）"""
    kept, dropped = [], 0
    for it in items:
        dup = False
        for k in kept:
            if _sim(it["kw"], k["kw"]) >= 0.8:
                dup = True
                if len(_nz(it["kw"])) > len(_nz(k["kw"])) and len(it["kw"]) <= MAX_LEN:
                    k["kw"] = it["kw"]
                break
        if dup:
            dropped += 1
            continue
        kept.append(it)
    return kept, dropped


def _align_pack(qtype, rks, ref_hit, off_list, miss_list):
    """参考要点对齐评分：覆盖率 / 漏答清单 / 偏题清单

    题型或参考要点为空时不做对齐（不报错），只提示「本题未设参考要点，仅做结构分组」。
    """
    total = len(rks or [])
    ok = bool(str(qtype or "").strip()) and total > 0
    if not ok:
        return {
            "ok": False,
            "ref_total": total,
            "ref_hit": 0,
            "coverage": None,
            "missed": [],
            "off": [],
            "note": "本题未设参考要点，仅做结构分组",
        }
    ref_hit = max(0, min(int(ref_hit or 0), total))
    cov = int(round(ref_hit * 100.0 / total))
    off_list = list(off_list or [])
    miss_list = list(miss_list or [])
    return {
        "ok": True,
        "ref_total": total,
        "ref_hit": ref_hit,
        "coverage": cov,
        "missed": miss_list,
        "off": off_list,
        "note": "覆盖率 %d%%（%d/%d）｜漏答 %d 条｜偏题 %d 条"
                % (cov, ref_hit, total, len(miss_list), len(off_list)),
    }


# --------------------------------------------------------------------------
# 三、主流程
# --------------------------------------------------------------------------
def analyze(text, qtype="", stages=None, ref="", question="", max_len=MAX_LEN):
    """学生作答 → 结构化关键词要点

    返回 {ok, qtype, stages, structure, groups:[{stage, role, items:[{text,status,by}]}],
          stats:{...}, ref_points:[...], note}
    """
    stages = [str(x).strip() for x in (stages or []) if str(x or "").strip()]
    stage_roles = {}
    try:
        names, roles = simp._roles_for(stages)
        stage_roles = dict(zip(names, roles))
    except Exception:
        stage_roles = {nm: "" for nm in stages}

    raw_items = []
    clause_total = 0
    for tag, chunk in _split_by_tags(text):
        stage_hit = _match_stage(tag, stages) if tag else ""
        for raw, _p in _split_clauses(chunk, soft=True):
            clause_total += 1
            c = _clean(raw)
            if not c or len(_nz(c)) < 2:
                continue
            kws = _brief_multi(raw, max_len)
            if not kws:
                continue
            role, score = _role_of_text(raw)
            for kw in kws:
                raw_items.append({"kw": kw, "raw": c, "stage": stage_hit, "pend": _p,
                                  "role": role, "score": score, "pos": len(raw_items),
                                  "coord": len(kws) > 1})

    if not raw_items:
        _rks0 = ref_points(ref)
        _miss0 = [_cut(x, max_len) for x in _rks0][:12]
        _al0 = _align_pack(qtype, _rks0, 0, [], _miss0)
        return {"ok": True, "qtype": qtype or "", "stages": stages, "structure": bool(stages),
                "groups": [], "stats": {"chars": len(text or ""), "clauses": clause_total,
                                        "points": 0, "dup_removed": 0, "groups": 0,
                                        "hit": 0, "off": 0, "miss": len(_miss0),
                                        "ref_total": len(_rks0), "ref_hit": 0,
                                        "coverage": _al0["coverage"]},
                "ref_points": _rks0, "align": _al0,
                "note": "没有可用文本，先取精简版或粘贴作答内容"}

    # 位置兜底：无标签、无语义信号的点按出现顺序推断角色
    total = len(raw_items)
    for it in raw_items:
        if not it["stage"] and not it["role"]:
            it["role"] = _pos_role(it["pos"] / max(1, total - 1))
            it["by"] = "pos"
        elif it["stage"]:
            it["by"] = "tag"
            if not it["role"]:
                it["role"] = stage_roles.get(it["stage"], "")
        else:
            it["by"] = "cue"

    raw_items, dup_removed = _dedup_points(raw_items)

    # 连读动词并入上一句：「…让群众对我们工作」+「产生不信任」→ 一条完整要点
    merged = []
    for it in raw_items:
        prev = merged[-1] if merged else None
        if (prev and not prev.get("pend") and not prev.get("coord") and not it.get("coord")
                and len(_nz(it["kw"])) <= 8
                and _CONT_HEAD_RE.match(it["kw"])
                and prev.get("stage") == it.get("stage")
                and len(prev["kw"]) + len(it["kw"]) <= max_len):
            cand = _LEAD_RE.sub("", prev["kw"] + it["kw"])
            cand = _TAIL_RE.sub("", cand)
            if cand and cand != prev["kw"]:
                prev["kw"] = cand
                prev["pend"] = it.get("pend", "")
                continue
        merged.append(it)
    raw_items = merged

    # 与环节名同名的「要点」（如只说了「原因」「首先」）无信息量，丢弃
    stage_nz = {_nz(nm) for nm in stages} | {_nz(x) for x in ROLE_GROUPS.values()}
    raw_items = [it for it in raw_items if _nz(it["kw"]) not in stage_nz]
    if not raw_items:
        _rks1 = ref_points(ref)
        _miss1 = [_cut(x, max_len) for x in _rks1][:12]
        _al1 = _align_pack(qtype, _rks1, 0, [], _miss1)
        return {"ok": True, "qtype": qtype or "", "stages": stages, "structure": bool(stages),
                "groups": [], "stats": {"chars": len(text or ""), "clauses": clause_total,
                                        "points": 0, "dup_removed": dup_removed, "groups": 0,
                                        "hit": 0, "off": 0, "miss": len(_miss1),
                                        "ref_total": len(_rks1), "ref_hit": 0,
                                        "coverage": _al1["coverage"]},
                "ref_points": _rks1, "align": _al1,
                "note": "没有可用的答题要点，先取精简版或粘贴更完整的作答内容"}

    # 归组：有环节用环节，没环节按语义分组
    groups, order = {}, []
    for it in raw_items:
        if it["stage"] and it["stage"] in stages:
            gname = it["stage"]
            role = stage_roles.get(gname, "")
        else:
            role = it["role"] or "analyze"
            gname = ""
            for nm in stages:
                if stage_roles.get(nm) == role:
                    gname = nm
                    break
            if not gname:
                gname = ROLE_GROUPS.get(role, "其他要点")
        if gname not in groups:
            groups[gname] = {"stage": gname, "role": role or "", "items": []}
            order.append(gname)
        groups[gname]["items"].append({"text": it["kw"], "status": "", "by": it.get("by", "")})

    # 与参考要点比对
    rks = ref_points(ref)
    used = [False] * len(rks)
    hit = off = 0
    if rks:
        for g in groups.values():
            for it in g["items"]:
                best, bi, bos = 0.0, -1, 0.0
                for i, rk in enumerate(rks):
                    s = _sim(it["text"], rk)
                    o = _osim(it["text"], rk)
                    if (s, o) > (best, bos):
                        best, bi, bos = s, i, o
                if best >= 0.3 or bos >= 0.45:
                    it["status"] = STATUS_HIT
                    hit += 1
                    if bi >= 0:
                        used[bi] = True
                elif question and _sim(it["text"], question) >= 0.34:
                    it["status"] = ""          # 与题干相关但参考里没写，不算偏题
                elif (g.get("role") in ("analyze", "action")
                      and best < 0.2 and bos < 0.3 and len(_nz(it["text"])) >= 3):
                    # 只对「事实 / 分析 / 对策」类判偏题；态度与结尾表态属于个人表达，不判偏
                    it["status"] = STATUS_OFF
                    off += 1

    miss, miss_kept = [], []
    for i, rk in enumerate(rks):
        if used[i]:
            continue
        if any(_sim(rk, x) >= 0.7 for x in miss_kept):
            continue
        miss_kept.append(rk)
        miss.append(rk)
        if len(miss) >= 12:
            break
    if miss:
        groups["__miss__"] = {"stage": "参考未答到（遗漏）", "role": "", "miss": True,
                              "items": [{"text": _cut(x, max_len), "status": STATUS_MISS, "by": "ref"}
                                        for x in miss]}
        order.append("__miss__")

    # 参考要点对齐评分：覆盖率 / 漏答清单 / 偏题清单
    ref_hit = sum(1 for u in used if u)
    off_list = []
    for gname, g in groups.items():
        if gname == "__miss__":
            continue
        for it in g.get("items") or []:
            if it.get("status") == STATUS_OFF:
                off_list.append(it["text"])
    align = _align_pack(qtype, rks, ref_hit, off_list, [_cut(x, max_len) for x in miss])

    # 分组顺序：先按题型环节顺序，再排语义分组，遗漏固定最后
    s_idx = {nm: i for i, nm in enumerate(stages)}
    head = sorted([k for k in order if k in s_idx], key=lambda k: s_idx[k])
    mid = [k for k in order if k not in s_idx and k != "__miss__"]
    out_groups = [groups[k] for k in (head + mid + (["__miss__"] if "__miss__" in groups else []))
                  if groups[k]["items"]]
    note = ""
    if not stages:
        note = "本题未识别题型结构，已按语义分组（入题 / 观点 / 分析 / 对策 / 收尾）"
    elif not any(g.get("by") == "tag" for g in out_groups):
        note = "按语义信号自动归入本题型的答题环节"
    if not align["ok"]:
        note = align["note"]

    return {
        "ok": True,
        "qtype": qtype or "",
        "stages": stages,
        "structure": bool(stages),
        "groups": out_groups,
        "stats": {
            "chars": len(text or ""),
            "clauses": clause_total,
            "points": len(raw_items),
            "dup_removed": dup_removed,
            "groups": len(out_groups),
            "hit": hit,
            "off": off,
            "miss": len(miss),
            "ref_total": align["ref_total"],
            "ref_hit": align["ref_hit"],
            "coverage": align["coverage"],
        },
        "ref_points": rks,
        "align": align,
        "note": note,
    }


# --------------------------------------------------------------------------
# 四、复制用的文本形态
# --------------------------------------------------------------------------
def format_text(result, mode="bullet", with_status=True):
    """把分析结果转成可复制的文本

    mode="plain"   每组一行：【环节】要点1；要点2
    mode="bullet"  每个要点一行，分行的项目符号
    """
    groups = (result or {}).get("groups") or []
    lines = []
    al = (result or {}).get("align") or {}
    if with_status and al.get("ok") and al.get("coverage") is not None:
        lines.append("【对齐】覆盖率 %d%%（%d/%d）｜漏答 %d 条｜偏题 %d 条"
                     % (al.get("coverage") or 0, al.get("ref_hit") or 0,
                        al.get("ref_total") or 0, len(al.get("missed") or []),
                        len(al.get("off") or [])))
        if mode == "bullet":
            lines.append("")
    for g in groups:
        name = g.get("stage") or "要点"
        items = g.get("items") or []
        if not items:
            continue
        if mode == "plain":
            seg = "；".join(
                (it.get("text", "") + (f"（{it['status']}）" if with_status and it.get("status") else ""))
                for it in items)
            lines.append(f"【{name}】{seg}")
        else:
            lines.append(f"【{name}】")
            for it in items:
                mark = f"（{it['status']}）" if with_status and it.get("status") else ""
                lines.append(f"- {it.get('text', '')}{mark}")
            lines.append("")
    return "\n".join(lines).strip()
