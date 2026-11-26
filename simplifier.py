# -*- coding: utf-8 -*-
"""
精简版（去冗余逐字稿）生成模块

* simplify(text)  去掉语气词、口头禅、无意义重复与结巴，输出干净通顺的文字
* set_stages(...) 设定当前题型生效的答题环节（来自题型结构配置，可变）
* stage_of(...)   判定该段属于哪个答题环节（按题型环节名输出）
* apply(para)     把精简结果写回段落字典（plain / plain_stage / plain_sub / plain_label）
* plain_text(...) 把多段精简版拼成一整篇（带结构环节标注、不带时间戳）

设计原则：全部为本地规则，不调用 AI；判定结构时优先沿用段落已有的标签
（paragraphizer.guess_title / AI paragraph_review 写回的 label），保证与既有逻辑一致。

环节是「按题型驱动」的：语义信号统一按 5 个功能角色（open/opinion/analyze/action/close）
打分，再映射到当前题型的实际环节名（如组织管理的「前期准备 / 中期开展」都属 action 角色）。
题型没配环节时回退通用五环节（审题/提观点/重分析/抓落实/结尾）。
"""

import re

# 通用兜底环节（题型未配置环节时使用）
DEFAULT_STAGES = ("审题", "提观点", "重分析", "抓落实", "结尾")

# 当前生效的环节列表（进程级，由 server 按会话题型调用 set_stages 刷新）
STAGES = list(DEFAULT_STAGES)
_ROLE_OF_STAGE = {}

# 环节名 → 功能角色 的关键词表（越靠前越先匹配，避免「前期准备」被判成 open）
_ROLE_RULES = (
    ("action", r"准备|开展|推进|实施|处置|善后|解决|处理|落实|对策|措施|做法|具体|行动|执行|整改"),
    ("close", r"结尾|收束|收尾|总结|结语|提升|升华|展望"),
    ("analyze", r"重分析|分析|剖析|论证|理由|原因|影响|展开|分点|细节|说明|主体"),
    ("opinion", r"提观点|观点|表态|态度|亮明|主题|立意"),
    ("open", r"审题|入题|切入|开场|开头|引入|描述|呈现|搭|框架"),
)


def role_of(stage_name):
    """环节名 → 功能角色（open/opinion/analyze/action/close）"""
    s = str(stage_name or "").strip()
    if not s:
        return ""
    if s in _ROLE_OF_STAGE:
        return _ROLE_OF_STAGE[s]
    for role, pat in _ROLE_RULES:
        if re.search(pat, s):
            _ROLE_OF_STAGE[s] = role
            return role
    return ""


def _roles_for(stages):
    """按顺序给每个环节定角色；关键词判不出的按位置兜底（首个=open，末个=close，中间=analyze/action）"""
    names = [str(x).strip() for x in (stages or []) if str(x or "").strip()]
    out = []
    for i, n in enumerate(names):
        r = role_of(n)
        if not r:
            if i == 0:
                r = "open"
            elif i == len(names) - 1:
                r = "close"
            else:
                r = "analyze" if i <= len(names) // 2 else "action"
        out.append(r)
    return names, out


def set_stages(stages=None):
    """设置当前题型生效的环节（顺序即作答顺序），返回实际生效的列表"""
    global STAGES
    names = [str(x).strip() for x in (stages or []) if str(x or "").strip()]
    seen, clean = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            clean.append(n)
    STAGES = clean or list(DEFAULT_STAGES)
    return STAGES


def stage_role_map():
    """当前环节 → 角色 映射"""
    names, roles = _roles_for(STAGES)
    return dict(zip(names, roles))

# --------------------------------------------------------------------------
# 一、去冗余
# --------------------------------------------------------------------------

# 单字语气词
VOICE_WORDS = "嗯啊呃额哦喔噢唉哎呀嘛呗哈嘿呐"

# 口头禅 / 无实义连接词（整块删除）
FILLER_PARTS = (
    "就是说", "也就是说", "然后就是", "然后呢", "接下来呢", "这个呢", "那个呢",
    "怎么说呢", "怎么讲呢", "你知道吧", "说白了",
    "然后", "那么", "反正", "对吧", "是吧", "对不对", "好不好", "行不行",
)
# 注：「这个 / 那个」后接名词时是实义指代，不能一律删除，
#     只在独立出现（后接标点或重复）时按口头禅处理，见 _clean_clause

# 句中可安全删除的单字语气词（"额"会出现在“额外/名额”里，故不参与删除）
_MID_VOICE = "嗯呃哦喔噢唉哎诶欸"

# 任何位置都可删的感叹/语气字（识别文本里常夹在词间，如“讲解诶这个”）
_ANY_VOICE = "诶欸哎啊呀哦喔噢呐唉哈嘿"

# 可安全从分句开头剥离的语气词（刻意不含“额”，避免“额外”→“外”）
_LEAD_VOICE = "嗯啊呃哦喔噢唉哎呀嘛呗哈嘿呐诶欸"

# 去掉冗余后只剩这些内容的分句一律丢弃
_STOP_CORES = {
    "是", "就是", "也就是说", "就是说", "说是", "那么", "然后", "这个", "那个",
    "反正", "对吧", "是吧", "对不对", "我", "我们", "大家", "嗯", "额", "啊", "说",
}

# 易结巴的重复单字
_STUTTER_CHARS = "我你他她它您就是这那的了很太还又都也才再"

_PUNCT = "，。！？；：、,.!?;:"


def _split_clauses(t):
    """按标点切成 [(分句正文, 尾随标点)]"""
    tokens = re.split(r"([%s])" % _PUNCT, t)
    out, buf = [], ""
    for tok in tokens:
        if tok and tok in _PUNCT:
            out.append((buf, tok))
            buf = ""
        else:
            buf += tok or ""
    if buf.strip():
        out.append((buf, "。"))
    return out


def _dedup(c):
    """删掉结巴与无意义重复"""
    # 三连以上重复单字 → 一个
    c = re.sub(r"([\u4e00-\u9fa5])\1{2,}", r"\1", c)
    # 两连重复：仅针对易结巴的字（保留“看看/慢慢/刚刚”等正常叠词）
    c = re.sub(r"([%s])\1" % _STUTTER_CHARS, r"\1", c)
    # 连续重复的词（2~6 字）→ 只留一次
    prev = None
    while prev != c:
        prev = c
        c = re.sub(r"([\u4e00-\u9fa5]{2,6}?)\1+", r"\1", c)
    return c


def _clean_clause(c):
    """清理单个分句：去口头禅、去语气词、去结巴"""
    c = c.strip()
    # 指示代词 + “是”：「这个是名额问题」→「这是名额问题」，避免删成「是名额问题」
    c = re.sub(r"(这个|那个)(?=是)", lambda m: m.group(1)[0], c)
    # 指示代词只作口头禅时才删：后接标点（「这个，我们…」）或本身重复（「这个这个」）
    c = re.sub(r"(这个|那个)(?:[，,、；;]|$)", "", c)
    c = re.sub(r"((这个|那个))\1+", r"\1", c)
    # 「这个 + 数量词」多为口头禅垫字（「这个一个原因」→「一个原因」）
    c = re.sub(r"(这个|那个)(?=[一二三四五六七八九十几些种样点])", "", c)
    c = _dedup(c)
    # 分句开头的冗余连接词：「就是我觉得」→「我觉得」
    c = re.sub(r"^(?:也就是说|就是说|就是|然后|那么)+(?=[我你他大家咱]|说|因为|所以|要|$)", "", c)
    for w in FILLER_PARTS:
        c = c.replace(w, "")
    c = _dedup(c)
    # 句中单字语气词
    c = re.sub(r"[%s]" % _MID_VOICE, "", c)
    # 夹在词间的感叹/语气字（识别文本常见：「讲解诶这个」「网购员啊街道办」）
    c = re.sub(r"[%s]" % _ANY_VOICE, "", c)
    c = c.strip()
    # 首尾语气词
    c = re.sub(r"^[%s\s]+" % _LEAD_VOICE, "", c)
    c = re.sub(r"[呢吧嘛啊呀哦哈]+$", "", c)
    c = c.strip("".join(ch for ch in _PUNCT))
    return c.strip()


def simplify(text):
    """把一句/一段逐字稿精简成干净文字（可能返回空串）"""
    t = (text or "").replace("\u3000", " ").strip()
    if not t:
        return ""
    t = re.sub(r"\s+", "", t)
    t = re.sub(r"[，,]{2,}", "，", t)

    kept = []       # [(text, punct)]
    for raw, punct in _split_clauses(t):
        core = _clean_clause(raw)
        if not core or core in _STOP_CORES:
            continue
        # 与上一分句完全重复（无意义重复）→ 丢弃
        if kept and kept[-1][0] == core:
            continue
        # 后一分句是前一分句的子串且很短（复读）→ 丢弃
        if kept and len(core) >= 4 and core in kept[-1][0]:
            continue
        kept.append((core, punct))

    if not kept:
        return ""

    out = []
    for i, (core, punct) in enumerate(kept):
        last = i == len(kept) - 1
        if last:
            p = punct if punct in "。！？!?" else "。"
        else:
            # 前一分句仅剩单字（如「要」「是」）时，不要再补逗号，避免「要，加强…」
            p = "，" if (punct not in "。！？!?" and len(core) > 1) else (
                punct if punct in "。！？!?" else "")
        out.append(core + p)
    s = "".join(out)
    s = re.sub(r"[，,]{2,}", "，", s)
    s = re.sub(r"[。]{2,}", "。", s)
    s = re.sub(r"，。", "。", s)
    return s.strip()


# --------------------------------------------------------------------------
# 二、答题结构环节判定
# --------------------------------------------------------------------------

# 段落已有标签 → 五环节（与 paragraphizer.TAG_RULES / AI 回写的【段落标签】保持一致）
_LABEL_MAP = (
    ("open", r"审题"),
    ("opinion", r"提观点|表态|开场|观点|立场|亮明"),
    ("analyze", r"重分析|分析|原因|影响|意义|危害|必要性"),
    ("action", r"抓落实|对策|措施|建议|做法|落实|举措"),
    ("close", r"结尾|收束|总结|综上|升华"),
)

# --------------------------------------------------------------------------
# 二、结构环节判定（按内容语义，不按位置兜底）
# --------------------------------------------------------------------------
# 每条语义信号 = (环节, 权重, 正则, 依据名)
# 权重依据「该信号对环节的指示强度」设定：明确表态 3.0 > 剖析成因 2.4 > 泛化连接 1.4
_SIGNALS = (
    # ---------------- 审题：复述题干 / 引述材料 / 题干式设问 ----------------
    ("open", 3.0, r"^(?:好的[，,]?|那么[，,]?|首先[，,]?)?(?:各位考官(?:好)?[，,]?)?"
                  r"(?:题干|题目|这道题|这个题目|这道题目|题中|题目中|题目当中|材料中|这段话|这段话讲)", "复述题干"),
    ("open", 2.6, r"(如何看(?:待)?|怎么看|怎么理解|谈谈你的?(?:理解|看法|认识|想法)|你的?看法是|请问你|"
                  r"你(?:会)?怎么办|你怎么做|你该如何|对这一现象|对这个问题)", "题干设问"),
    ("open", 2.2, r"(有人说|有人认为|有人觉得|有观点认为|网上有|据报道|材料显示|背景是|"
                  r"最近(?:出现|发生)|近年来出现)", "引述题目材料"),
    ("open", 1.6, r"(结合(?:上述)?材料|根据材料|联系实际|结合(?:你)?的?(?:工作|经历|岗位))", "材料指向"),
    # ---------------- 提观点：明确表态 / 价值评判 ----------------
    ("opinion", 3.2, r"(我认为|我觉得|在我看来|我的看法|我的观点|个人认为|我的态度是|"
                   r"我(?:的)?判断是|我认为这|在我看来这)", "明确表态"),
    ("opinion", 2.6, r"(值得(?:肯定|提倡|点赞|鼓励)|应予(?:以)?(?:肯定|支持|鼓励)|不可取|"
                   r"坚决反对|不能(?:容忍|接受|提倡|姑息)|应当(?:支持|反对|肯定)|"
                   r"是对的|是不对的|这是好的|令人(?:担忧|欣慰|痛心)|必须(?:旗帜鲜明)?(?:反对|支持))", "价值评判"),
    ("opinion", 1.8, r"(总体(?:来看|而言|上说)|客观(?:地)?(?:看|来讲)|从本质上看|"
                   r"归根结底(?:来说)?|一句话概括|概括(?:起来)?说)", "总体判断"),
    # 判断句式：用「不是…而是…」「关键在…」直接给出立场，即使不含表态动词
    ("opinion", 2.8, r"(?:不(?:在|是)[^，。；]{1,14}?而(?:在于|是|在)|不是[^，。；]{1,14}?而是|"
                   r"关键(?:在于|是)|根本(?:在于|是)|说白了|说到底(?:是|在))", "判断句式"),
    # 亮明态度：摆态度 / 说必须改，属于表态而非对策
    ("opinion", 2.6, r"(态度(?:是|摆|亮)|先表(?:个)?态|我的态度|必须(?:改|变|动)|"
                   r"(?:是|属)(?:错|对)的|不该这样|我不认(?:同|为))", "亮明态度"),
    # ---------------- 重分析：成因 / 影响 / 分层论证 ----------------
    ("analyze", 2.4, r"(原因(?:是|在于|有)|之所以|根源(?:在|是)|本质(?:上|是|在于)|"
                   r"深层(?:次)?(?:原因|问题)|背后(?:的)?(?:原因|逻辑)|问题(?:的)?症结)", "剖析成因"),
    ("analyze", 2.4, r"(影响(?:是|在于)|意义(?:是|在于|重大)|危害(?:是|在于)?|后果(?:是)?|"
                   r"好处|弊端|不利于|有利于|导致|造成|使得|进而|长此以往|久而久之)", "分析影响"),
    ("analyze", 1.6, r"(一方面|另一方面|首先|其次|再次|第一[，,、]|第二[，,、]|第三[，,、])", "分层论述"),
    ("analyze", 1.4, r"(本质|逻辑|关系|矛盾|短板|瓶颈|困境|难题|缺位|失位|失衡|脱节|"
                   r"不匹配|不均衡|不充分)", "点明问题"),
    # 摆事实：用数字、转折、反问铺陈困境，属论证手段而非对策
    ("analyze", 2.0, r"(却|但|然而|可是)[^，。；]{0,18}(要|得|只有|一共|仅|竟|还)", "摆事实析困境"),
    ("analyze", 2.0, r"(谁(?:还|会|愿意)|怎么(?:可能|会)|何谈|谈何)", "反问析影响"),
    ("analyze", 1.8, r"(一共|总共|仅有?|只剩下?|十几个|几十个|三五?个)[^，。；]{0,10}"
                   r"(人|部门|报表|会议|材料|表)", "数据化困境"),
    # ---------------- 抓落实：对策 / 手段 / 责任主体 ----------------
    # 具体动作：以「把/将 + 受事 + 动作」骨架落地，即使没写"措施"二字
    ("action", 2.8, r"(?:把|将)[^，。；]{1,12}?(?:搬|移|收|核|减|改|建|设|列|清|"
                   r"整合|打通|前移|下沉|统一|取消|合并|压缩)(?!得)", "具体动作"),
    ("action", 2.4, r"(搬到|迁移|收齐|一次(?:性)?告知|一站式|首问负责|上门(?:办|服务)|"
                   r"代办|下沉|分片|联办|当场办|限时办结|一窗(?:受理|办理))", "落地做法"),
    ("action", 2.8, r"(?:要|应|应当|应该|需要|必须|可以|得)(?:进一步|切实|着力|持续|及时)?"
                   r"(?:加强|完善|健全|推进|落实|建立|出台|加大|压实|优化|规范|强化|提升|"
                   r"拓宽|创新|细化|补齐|抓好|做好|坚持|注重|推动|深化|改进|畅通)", "提出对策"),
    ("action", 2.4, r"(对策|措施|建议|举措|抓手|路径|办法|下一步|具体做法|"
                   r"怎么(?:落实|解决|办)|如何(?:落实|解决|破解))", "对策表述"),
    ("action", 1.8, r"(监管|监督|问责|执法|考核|宣传|培训|教育引导|制度|机制|"
                   r"法规|政策|试点|台账|闭环|清单|标准|流程)", "治理手段"),
    ("action", 1.6, r"(从(?:政府|学校|企业|个人|社会|家庭|媒体|行业)(?:层面|角度|入手)|"
                   r"多措并举|多方联动|形成合力|一盘棋|协同(?:推进|发力))", "责任主体"),
    # ---------------- 结尾：总结收束 / 展望 ----------------
    ("close", 3.2, r"(总之|综上(?:所述)?|总而言之|总的来说|一句话|说到底|以上就是我|"
                  r"回答完毕|我的回答(?:完毕|结束)|最后我想|简单总结)", "总结收束"),
    ("close", 1.8, r"(相信(?:在|只要)|希望(?:在|能)|期待|只要我们|只要(?:我们)?(?:坚持|共同))", "展望收束"),
    # 呼应收束 / 价值升华：短句给出判断或升华，即使不含"总之""综上"
    ("close", 2.6, r"(唯有|只有)[\s\S]{0,16}?(?:才(?:能|会|是|不|算)|方能|方才)", "呼应收束"),
    ("close", 2.2, r"(终究|最终(?:会|要)?|归根到底|说到底)", "归结收束"),
    ("close", 1.8, r"(不是[^，。；]{0,10}?(?:口号|空话|形式)|才有(?:温度|底色|分量)|"
                  r"让[^，。；]{0,10}?(?:更有|真正)|说了算)", "价值升华"),
)

# 依据展示用：环节 → 中文小标题（供前端提示，不参与判定）
CONF_TEXT = {"high": "明确", "mid": "较明确", "low": "推断"}

_SUB_RULES = (
    ("原因", r"(原因|之所以|根源|因为|由于|源于|不到位|缺位|缺失|不足|滞后|薄弱)"),
    ("影响", r"(影响|意义|危害|好处|坏处|不利于|有利于)"),
    ("对策", r"(对策|措施|建议|做法|举措|落实)"),
    ("监管", r"(监管|监督|问责|执法|考核)"),
    ("宣传", r"(宣传|引导|教育|培训)"),
    ("制度", r"(制度|机制|法规|规则)"),
    ("表态", r"(我认为|我觉得|我的看法|值得肯定|应该否定|支持|反对)"),
    ("收束", r"(总之|综上|以上就是|回答完毕)"),
)

_COUNT_RE = re.compile(r".")
_MODAL_RE = re.compile(r"(要|应|应当|应该|需要|必须|可以|得)")
_EXAMPLE_RE = re.compile(r"(比如|例如|举个例子|譬如|有一次|曾经|案例|事例|据报道|数据显示)")
# 祈使 + 治理动作成对出现，才算对策骨架（光有"要"字不算）
_MODAL_ACT_RE = re.compile(
    r"(?:要|应|应当|应该|需要|必须|可以|得|将会)[^，。；]{0,10}"
    r"(?:加强|完善|建立|健全|推进|落实|开展|组织|制定|出台|排查|核对|清理|整合|"
    r"打通|规范|优化|压实|提升|培训|宣传|上门|下沉|压缩|取消|合并|明确)")
# 「把 / 将 + 受事 + 动作」动作骨架
_ACTION_RE = re.compile(
    r"(?:把|将)[^，。；]{0,14}(?:搬|移|收|核|减|改|建|设|列|清|整合|打通|前移|下沉|"
    r"统一|取消|合并|压缩|上门)")
# 事实铺陈：数字、转折、反问
_FACT_RE = re.compile(r"(\d|[一二三四五六七八九十几]+(?:个|人|次|趟|份|张|项|条)|"
                      r"却|但是|然而|可是|谁还|怎么会)")
# 短句价值收束
_CLOSE_RE = re.compile(r"(才(?:是|能|会|不|算)|终究|归根到底|说了算|温度|底色|分量)")


def _map_label(label):
    l = (label or "").strip()
    if not l:
        return ""
    # 标签本身是当前题型的环节名 → 直接取其功能角色
    if l in STAGES:
        return role_of(l)
    for role, pat in _LABEL_MAP:
        if re.search(pat, l):
            return role
    return role_of(l)


def _count(text, pat):
    return len(re.findall(pat, text or ""))


_ROLE_ORDER = ("open", "opinion", "analyze", "action", "close")

# 某角色在该题型里不存在时的替代顺序（如现场模拟没有「落实」环节，优先并入分析）
_ROLE_FALLBACK = {
    "open": ("opinion", "analyze"),
    "opinion": ("open", "analyze"),
    "analyze": ("action", "opinion", "open"),
    "action": ("analyze", "close"),
    "close": ("analyze", "action", "opinion"),
}


def _sig_score(text):
    """按功能角色累加语义信号得分，返回 (score, why)"""
    t = text or ""
    score = dict((r, 0.0) for r in _ROLE_ORDER)
    why = {}
    for role, w, pat, name in _SIGNALS:
        n = len(re.findall(pat, t))
        if not n:
            continue
        score[role] += w * min(n, 3)     # 同类信号最多计 3 次，防止堆词刷分
        why.setdefault(role, []).append(name if n == 1 else "%s×%d" % (name, n))
    return score, why


def _stage_for_role(role, idx=1, is_last=False, prev_stage=""):
    """把功能角色映射到当前题型的实际环节名

    同一角色对应多个环节时（如组织管理「前期准备 / 中期开展」都属 action），
    按位置与上一段环节取最贴合的一个；该题型没有这个角色时退到相邻角色。
    """
    names, roles = _roles_for(STAGES)
    if not names:
        names, roles = _roles_for(DEFAULT_STAGES)
    cands = [i for i, r in enumerate(roles) if r == role]
    if not cands:
        for alt in _ROLE_FALLBACK.get(role, ()) or ():
            cands = [i for i, r in enumerate(roles) if r == alt]
            if cands:
                break
        if not cands:
            return names[-1] if is_last else names[0]
    prev_idx = -1
    if prev_stage:
        for i, n in enumerate(names):
            if n == prev_stage:
                prev_idx = i
                break
    if prev_idx >= 0:
        later = [i for i in cands if i > prev_idx]
        if role in ("analyze", "action") and later:
            return names[later[0]]
    if role == "close":
        return names[cands[-1]]
    if role == "open":
        return names[cands[0]]
    if role in ("analyze", "action") and len(cands) > 1:
        # 同角色多环节（如「前期准备 / 中期开展」）：按段落先后依次推进
        k = min(max(int(idx or 1) - 1, 0), len(cands) - 1)
        return names[cands[k]]
    if is_last and len(cands) > 1 and cands[-1] < len(names) - 1:
        return names[cands[-1]]
    return names[cands[0]]


def judge(text="", label="", idx=1, is_last=False, prev_stage=""):
    """按内容语义判定答题环节（返回当前题型的环节名）

    返回 (stage, sub, reason, conf)
      stage  : 当前题型生效的环节名（题型未配环节时即通用五环节）
      sub    : 小标题（原因 / 影响 / 对策 / 表态 / 收束 …）
      reason : 判定依据（命中哪些语义信号），用于界面展示与人工复核
      conf   : high / mid / low

    判定完全基于正文语义信号（复述题干、明确表态、剖析成因、分析影响、
    提出对策、总结收束）。段落位置与上一段环节只在「语义信号缺失/打平」时
    参与次序校正，并且会把这件事写进依据里，绝不作为主判据。
    """
    t = (text or "").strip()
    if not t:
        return _stage_for_role("analyze", idx, is_last, prev_stage), "", "空段落，无内容可判定", "low"
    # 有效字数太少（多是语气词/碎句）时不做臆测，交由人工确认
    eff = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", t)
    if len(eff) < 6:
        # 过短的碎句多是开场问候或过渡语，首段按开场处理，其余按内容环节
        role = "open" if int(idx or 1) <= 1 else "analyze"
        return (_stage_for_role(role, idx, is_last, prev_stage), "",
                "有效内容仅 %d 字，不足以判定环节，可右键手动指定" % len(eff), "low")

    score, why = _sig_score(t)

    # 段落自带标签：仅当文本信号里也有同向线索时才加分，避免标签带偏
    lab = _map_label(label)
    if lab:
        if score[lab] > 0:
            score[lab] += 1.2
            why.setdefault(lab, []).append("段落标签同向")
        elif max(score.values()) <= 0:
            score[lab] += 1.2
            why.setdefault(lab, []).append("段落标签")

    ranked = sorted(_ROLE_ORDER, key=lambda r: (-score[r], _ROLE_ORDER.index(r)))
    best = ranked[0]
    best_s, second_s = score[best], score[ranked[1]]

    # 1) 语义信号足够强且领先 → 直接判定
    if best_s >= 1.6 and (best_s - second_s) >= 0.6:
        conf = "high" if best_s >= 2.8 else "mid"
        stage = _stage_for_role(best, idx, is_last, prev_stage)
        return stage, _default_sub(stage, t), "依据：" + "、".join(why.get(best, [])), conf

    # 2) 有信号但偏弱 / 两项接近 → 用次序作微调，但仍以语义为先
    if best_s > 0:
        cand = best
        if second_s > 0 and (best_s - second_s) < 0.6:
            tie = {best, ranked[1]}
            # 总结语与收尾语在场时，收尾优先
            if "close" in tie and re.search(r"(总之|综上|总而言之|回答完毕|唯有|只有|终究|才是|说到底)", t):
                cand = "close"
            # 表态句式 vs 动作骨架打平：表态优先（"把门槛设得太高"是评价不是做法）
            elif tie == {"action", "opinion"}:
                cand = "opinion" if score["opinion"] >= score["action"] else "action"
            # 对策骨架 vs 泛论述打平：对策优先
            elif tie == {"action", "analyze"}:
                cand = "action"
        stage = _stage_for_role(cand, idx, is_last, prev_stage)
        return (stage, _default_sub(stage, t),
                "依据：" + "、".join(why.get(cand, []) or why.get(best, [])), "low")

    # 3) 完全没有结构词：按文体性质推断（仍是内容判断，不看第几段）
    if _EXAMPLE_RE.search(t):
        return (_stage_for_role("analyze", idx, is_last, prev_stage), "举例论证",
                "依据：举例论证（无结构词，按论证段归入分析环节）", "low")
    if _ACTION_RE.search(t):
        return (_stage_for_role("action", idx, is_last, prev_stage), "对策",
                "依据：以「把/将+动作」的具体做法为主（无对策关键词）", "low")
    if _MODAL_ACT_RE.search(t):
        return (_stage_for_role("action", idx, is_last, prev_stage), "对策",
                "依据：祈使表述与治理动作成对出现（无对策关键词）", "low")
    if _FACT_RE.search(t):
        return (_stage_for_role("analyze", idx, is_last, prev_stage), "影响",
                "依据：以数字/转折事实铺陈现状（无分析关键词）", "low")
    if len(t) <= 26 and _CLOSE_RE.search(t):
        return (_stage_for_role("close", idx, is_last, prev_stage), "收束",
                "依据：短句给出收束性判断（无总结关键词）", "low")
    prev_role = stage_role_map().get(prev_stage, "") if prev_stage else ""
    if prev_role in ("open", "opinion"):
        return (_stage_for_role("analyze", idx, is_last, prev_stage), "原因",
                "依据：语义信号不足，依前后次序（上一环节为「%s」）推为分析" % prev_stage, "low")
    if prev_role == "action":
        return (_stage_for_role("analyze", idx, is_last, prev_stage), "影响",
                "依据：语义信号不足，依前后次序（上一环节已进落实段）推为分析", "low")
    stage = _stage_for_role("analyze", idx, is_last, prev_stage)
    return (stage, _default_sub(stage, t),
            "依据：以论述为主，无明确结构信号，暂按「%s」（可右键手动指定）" % stage, "low")


def stage_of(text="", label="", idx=1, is_last=False, prev_stage=""):
    """兼容旧调用：返回 (环节, 小标题)"""
    st, sub, _why, _conf = judge(text, label, idx, is_last, prev_stage)
    return st, sub


def _default_sub(stage, text=""):
    t = text or ""
    role = role_of(stage) or "analyze"
    best, best_n = "", 0
    for name, pat in _SUB_RULES:
        n = _count(t, pat)
        if n > best_n:
            best, best_n = name, n
    if role == "open":
        return "审题"
    if role == "close":
        return "收束"
    if role == "opinion":
        return "表态"
    if role == "analyze":
        if best in ("原因", "影响"):
            return best
        return "原因" if re.search(r"(原因|之所以|根源|因为|由于)", t) else "影响"
    if role == "action":
        if best in ("对策", "监管", "宣传", "制度"):
            return best
        return "对策"
    if best:
        return best
    return "分析" if role == "analyze" else "对策"


def stage_label(stage, sub=""):
    """展示用标签：“重分析-影响”“提观点”"""
    if not stage:
        return ""
    if sub and sub not in (stage, "", "分析", "对策", "表态", "审题", "收束", "举例论证"):
        return "%s-%s" % (stage, sub)
    return stage


# --------------------------------------------------------------------------
# 三、写回段落 / 拼整篇
# --------------------------------------------------------------------------

def parts_to_plain(parts):
    """句子级精简片段拼成整段精简文字"""
    txt = "".join(str((p or {}).get("text") or "") for p in (parts or []))
    return re.sub(r"\s+", "", txt).strip()


def _parts_of(para, seg_map=None):
    """把一个段落拆成「句子级精简片段」，用于在段落内按句子定位标记

    seg_map: {seg_id: seg_dict}，有它就能保证片段与逐字稿句子一一对应；
    没有（老存档）则退化为整段精简成一条。
    """
    parts = []
    if seg_map:
        for sid in para.get("seg_ids") or []:
            seg = seg_map.get(sid)
            if not seg:
                continue
            t = simplify(seg.get("text") or "")
            if t:
                parts.append({"sid": sid, "text": t})
    if parts:
        return parts
    flat = simplify(para.get("text") or "")
    return [{"sid": None, "text": flat}] if flat else []


def apply(para, prev_label="", seg_map=None, prev_stage=""):
    """给单个段落补上精简版字段，返回该段落（原地修改）

    结构环节优先级：手动指定（stage_manual）> 内容语义判定。
    """
    if not isinstance(para, dict):
        return para
    text = para.get("text") or ""
    label = para.get("label") or para.get("title") or ""
    # 段落标签若只是“第N段”这类占位，视作没有标签
    if re.match(r"^第\s*\d+\s*段", str(label)):
        label = ""
    parts = _parts_of(para, seg_map)
    plain = parts_to_plain(parts) if parts else simplify(text)
    man = para.get("stage_manual") or {}
    man_stage = man.get("stage") if isinstance(man, dict) else ""
    man_stage = man_stage if man_stage in STAGES else ""
    if man_stage:
        stage = man_stage
        sub = (man.get("sub") or "") if isinstance(man, dict) else ""
        sub = sub or _default_sub(stage, text)
        reason = "手动指定"
        conf = "manual"
    else:
        stage, sub, reason, conf = judge(text, label, para.get("idx", 1),
                                         bool(para.get("_is_last")), prev_stage)
    para["plain_parts"] = parts
    para["plain"] = plain
    para["plain_stage"] = stage
    para["plain_sub"] = sub
    para["plain_label"] = stage_label(stage, sub)
    para["plain_reason"] = reason
    para["plain_conf"] = conf
    return para


def apply_all(paragraphs, seg_map=None):
    """批量为段落列表补精简版（旧会话 / 历史记录兼容）"""
    paras = sorted(paragraphs or [], key=lambda p: p.get("idx", 0))
    prev_stage = ""
    for i, p in enumerate(paras):
        p["_is_last"] = (i == len(paras) - 1)
        apply(p, prev_label=(paras[i - 1].get("label") if i else ""),
              seg_map=seg_map, prev_stage=prev_stage)
        p.pop("_is_last", None)
        prev_stage = p.get("plain_stage") or ""
    return paras


def plain_text(paragraphs, with_stage=True):
    """拼成整篇精简版（不带任何时间戳 / 时长）"""
    lines = []
    paras = sorted([p for p in (paragraphs or []) if (p.get("plain") or "").strip()],
                   key=lambda p: p.get("idx", 0))
    for p in paras:
        txt = (p.get("plain") or "").strip()
        if not txt:
            continue
        if with_stage:
            tag = p.get("plain_label") or p.get("plain_stage") or ""
            lines.append("【%s】%s" % (tag or "未标注", txt))
        else:
            lines.append(txt)
    return "\n".join(lines)


def pending_count(paragraphs):
    """还有多少段没生成精简版"""
    return sum(1 for p in (paragraphs or []) if (p.get("text") or "").strip()
               and not (p.get("plain") or "").strip())
