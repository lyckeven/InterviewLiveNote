# -*- coding: utf-8 -*-
"""
精简版智能纠错模块

职责：把语音识别出来的同音字、明显错别字、口误，按上下文还原成考生真实想表达的意思。
* correct_local(text)              纯本地词典 + 语病规则（毫秒级，永远可用）
* correct_parts(...)               对一个段落的若干句子做「本地 + AI」两级纠错
* parse_correct_reply(raw, n)      解析 AI 返回的 JSON（带兜底）
* build_prompt(sentences)          AI 纠错提示词

设计原则：
1. 只做「纠错」不做「润色」——不改观点、不增删信息、不改句式风格；
2. 本地词典只收录「在任何语境下都明确错误」的写法，避免把正确用词改坏；
3. AI 失败时一律退回本地结果，绝不丢原文。
"""

import json
import re

# --------------------------------------------------------------------------
# 一、本地纠错词典（同音字 / 形近字 / 成语误写）
#     只收「错了就一定错」的写法，宁可少改也不能改错。
# --------------------------------------------------------------------------
LOCAL_PAIRS = (
    # 身份与岗位
    ("公物员", "公务员"),
    ("公务园", "公务员"),
    ("公武员", "公务员"),
    ("公雾员", "公务员"),
    ("事业单为", "事业单位"),
    ("事业单味", "事业单位"),
    ("选调声", "选调生"),
    # 组织与治理
    ("基成", "基层"),
    ("基岑", "基层"),
    ("基曾", "基层"),
    ("基晨", "基层"),
    ("群从", "群众"),
    ("群重", "群众"),
    ("群粽", "群众"),
    ("干布", "干部"),
    ("干部群重", "干部群众"),
    ("形式主议", "形式主义"),
    ("官僚主议", "官僚主义"),
    ("本位主议", "本位主义"),
    ("官本位" , "官本位"),
    ("属地管里", "属地管理"),
    ("网格化管里", "网格化管理"),
    ("治理体细", "治理体系"),
    ("治理能里", "治理能力"),
    # 常见搭配 / 成语
    ("急难仇盼", "急难愁盼"),
    ("急难愁判", "急难愁盼"),
    ("攻艰克难", "攻坚克难"),
    ("攻坚刻难", "攻坚克难"),
    ("贯切落实", "贯彻落实"),
    ("贯策落实", "贯彻落实"),
    ("贯彻落实" , "贯彻落实"),
    ("多错并举", "多措并举"),
    ("多措并举", "多措并举"),
    ("标本兼制", "标本兼治"),
    ("标本兼冶", "标本兼治"),
    ("统筹兼故", "统筹兼顾"),
    ("统筹兼固", "统筹兼顾"),
    ("举一返三", "举一反三"),
    ("举一反山", "举一反三"),
    ("真抓时干", "真抓实干"),
    ("务求时效", "务求实效"),
    ("务求时较", "务求实效"),
    ("刻不容暖", "刻不容缓"),
    ("任重到远", "任重道远"),
    ("按步就班", "按部就班"),
    ("迫不急待", "迫不及待"),
    ("相辅相承", "相辅相成"),
    ("一以惯之", "一以贯之"),
    ("防犯于未然", "防患于未然"),
    ("防范于未然", "防患于未然"),
    ("捋起袖子", "撸起袖子"),
    ("凝心聚立", "凝心聚力"),
    ("因地治宜", "因地制宜"),
    ("因人实策", "因人施策"),
    ("精准实策", "精准施策"),
    ("因才施策", "因材施策"),
    ("对症下要", "对症下药"),
    ("水到渠成" , "水到渠成"),
    ("循序渐近", "循序渐进"),
    ("潜移默化" , "潜移默化"),
    # 名词 / 术语
    ("案列", "案例"),
    ("安例", "案例"),
    ("倾刻", "顷刻"),
    ("一如继往", "一如既往"),
    ("再接再励", "再接再厉"),
    ("变本加利", "变本加厉"),
    ("走头无路", "走投无路"),
    ("情不自尽", "情不自禁"),
    ("不径而走", "不胫而走"),
    ("严竣", "严峻"),
    ("分岐", "分歧"),
    ("拖贫攻坚", "脱贫攻坚"),
    ("精准扶平", "精准扶贫"),
    ("政务服物", "政务服务"),
    ("公共服物", "公共服务"),
    ("民生保彰", "民生保障"),
    ("社会保彰", "社会保障"),
    ("夯时", "夯实"),
    ("夯石", "夯实"),
    ("证结", "症结"),
    ("证结所在", "症结所在"),
    ("平颈", "瓶颈"),
    ("瓶径", "瓶颈"),
    ("短版", "短板"),
    ("短拌", "短板"),
    ("产业练", "产业链"),
    ("一盘旗", "一盘棋"),
    ("长效机治", "长效机制"),
    ("考核机治", "考核机制"),
    ("监督机治", "监督机制"),
    ("信访" , "信访"),
    ("信访积案" , "信访积案"),
    ("舆情引道", "舆情引导"),
    ("应急处秃", "应急处突"),
    ("台账" , "台账"),
    ("闭环管里", "闭环管理"),
    ("痛点和堵点" , "痛点和堵点"),
)

# 常见错别字（虚词 / 常用词）
WORD_PAIRS = (
    ("我觉的", "我觉得"),
    ("感决", "感觉"),
    ("因该", "应该"),
    ("应刻", "应该"),
    ("形该", "应该"),
    ("印该", "应该"),
    ("做为", "作为"),
    ("即然", "既然"),
    ("以经", "已经"),
    ("在次", "再次"),
    ("那末", "那么"),
    ("那里" , "那里"),
    ("必需" , "必需"),
    ("不清楚" , "不清楚"),
    ("象这样", "像这样"),
    ("象我们", "像我们"),
    ("从新", "重新"),
    ("溶入", "融入"),
    ("溶合", "融合"),
    ("反应问题", "反映问题"),
    ("反应情况", "反映情况"),
    ("部署" , "部署"),
    ("布署", "部署"),
    ("峻工", "竣工"),
    ("讯速", "迅速"),
    ("迅束", "迅速"),
    ("广泛" , "广泛"),
    ("广范", "广泛"),
)

_SORTED_PAIRS = tuple(
    sorted(((w, r) for w, r in (LOCAL_PAIRS + WORD_PAIRS) if w and w != r),
           key=lambda x: -len(x[0]))
)

# 需要合并的重复虚词（的的 / 了了 / 是是 …）
_DUP_CHARS = "的了是在和就都也把被让给向从对"

# 明显口误：说错后立刻改口的固定搭配（"不对不对" / "我是说"）
_SELF_FIX_RE = re.compile(r"(不对不对|不不不|说错了说错了|不是不是)[，,]?")
_CLICHE_RE = re.compile(r"(那个那个|这个这个|就是就是|然后然后|我们我们|我我)")


def _dedup_func_words(text):
    """合并重复虚词：的的 -> 的"""
    for ch in _DUP_CHARS:
        text = re.sub(ch + "{2,}", ch, text)
    return text


def correct_local(text):
    """本地纠错，返回 (新文本, 变更列表)"""
    if not text:
        return text or "", []
    out = text
    changes = []
    for wrong, right in _SORTED_PAIRS:
        if wrong in out:
            out = out.replace(wrong, right)
            changes.append({"from": wrong, "to": right,
                            "reason": "同音字/错别字", "src": "local"})
    fixed = _dedup_func_words(out)
    if fixed != out:
        changes.append({"from": "重复虚词", "to": "合并", "reason": "口误重复", "src": "local"})
        out = fixed
    # 自我改口：「不对不对，我是说…」这种保留后半句更接近真实表达
    m = _SELF_FIX_RE.search(out)
    if m:
        tail = out[m.end():].strip()
        if len(tail) >= 6:
            changes.append({"from": m.group(1), "to": "（口误删除）",
                            "reason": "口误改口", "src": "local"})
            out = tail
    out = _CLICHE_RE.sub(lambda m: (m.group(1)[:2] if len(m.group(1)) >= 4 else m.group(1)[:1]), out)
    return out, changes


# --------------------------------------------------------------------------
# 二、AI 纠错
# --------------------------------------------------------------------------
CORRECT_SYSTEM = (
    "你是中文语音转写校对员，专门处理结构化面试的实时逐字稿。"
    "你只做文字纠正，不做内容加工：不许润色、不许改写观点、不许增删信息、不许调整句式风格。"
)

CORRECT_TEMPLATE = """下面是一个考生作答段落的逐字稿句子列表（JSON 数组）。请只做三类纠正：

1) 同音字 / 近音字错误（如「公物员」应为「公务员」、「基成」应为「基层」）；
2) 明显错别字；
3) 口误（说错后立刻改口、明显重复、吞字导致的语病），按上下文还原成考生真实想表达的意思。

严格约束：
- 句子数量必须与输入完全一致，顺序不变，一句对一句；
- 不得合并句子、不得拆分句子、不得增删句子；
- 不改语气、不改口语风格、不缩写、不扩写、不补观点；
- 拿不准的地方宁可原样保留。

只输出 JSON，不要解释文字、不要代码块围栏：
{{"sentences": ["修正后的第1句", "修正后的第2句"], "changes": [{{"i": 0, "from": "原片段", "to": "修正片段", "reason": "同音字"}}]}}

题目（仅供参考语境，不影响纠错口径）：{question}

逐字稿句子列表：
{sentences}
"""


def _strip_fence(raw):
    t = (raw or "").strip()
    t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    return t.strip()


def parse_correct_reply(raw, expect_n):
    """解析 AI 返回；返回 (sentences 或 None, changes)"""
    t = _strip_fence(raw)
    if not t:
        return None, []
    data = None
    try:
        data = json.loads(t)
    except Exception:
        m = re.search(r"\{.*\}", t, re.S)
        if m:
            try:
                data = json.loads(m.group(0))
            except Exception:
                data = None
    if not isinstance(data, dict):
        return None, []
    sents = data.get("sentences")
    if not isinstance(sents, list):
        return None, []
    out = []
    for s in sents:
        out.append(str(s or "").strip())
    if len(out) != expect_n or any(not s for s in out):
        return None, []
    changes = []
    for c in data.get("changes") or []:
        if not isinstance(c, dict):
            continue
        changes.append({
            "from": str(c.get("from") or ""),
            "to": str(c.get("to") or ""),
            "reason": str(c.get("reason") or "AI 纠正"),
            "src": "ai",
            "i": c.get("i"),
        })
    return out, changes


def build_prompt(sentences, question=""):
    return CORRECT_TEMPLATE.format(
        question=(question or "（未填写）")[:200],
        sentences=json.dumps(list(sentences), ensure_ascii=False, indent=1),
    )


def ai_correct(ai_client, sentences, question=""):
    """调用 AI 纠错，返回 (sentences 或 None, changes)；任何异常都不抛出"""
    if not sentences:
        return None, []
    try:
        raw = ai_client._chat(
            [{"role": "system", "content": CORRECT_SYSTEM},
             {"role": "user", "content": build_prompt(sentences, question)}],
            temperature=0.0,
            max_tokens=min(max(len("".join(sentences)) * 3, 2048), 8000),
        )
        return parse_correct_reply(raw, len(sentences))
    except Exception:
        return None, []


def correct_parts(ai_client, parts, question="", use_ai=True):
    """对一个段落的句子列表做纠错

    parts: [{"sid":..., "text": "..."}]
    返回 (新 parts, changes) —— changes 里带 sid 便于前端定位
    """
    src = [str((p or {}).get("text") or "") for p in (parts or [])]
    if not src:
        return [], [], "local"
    # 1) 本地规则
    local_out, local_changes = [], []
    for i, s in enumerate(src):
        fixed, chs = correct_local(s)
        local_out.append(fixed or s)
        for c in chs:
            c["sid"] = (parts[i] or {}).get("sid")
            local_changes.append(c)
    # 2) AI 纠错（失败即回退本地）
    if use_ai and ai_client is not None:
        ai_out, ai_changes = ai_correct(ai_client, local_out, question)
        if ai_out:
            for c in ai_changes:
                idx = c.get("i")
                if isinstance(idx, int) and 0 <= idx < len(parts):
                    c["sid"] = (parts[idx] or {}).get("sid")
            final = ai_out
            changes = local_changes + ai_changes
            src_tag = "ai"
        else:
            final = local_out
            changes = local_changes
            src_tag = "local"
    else:
        final = local_out
        changes = local_changes
        src_tag = "local"
    out_parts = []
    for i, p in enumerate(parts):
        text = final[i] if i < len(final) and final[i] else src[i]
        out_parts.append({"sid": (p or {}).get("sid"), "text": text})
    return out_parts, changes, src_tag
