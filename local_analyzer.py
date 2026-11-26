# -*- coding: utf-8 -*-
"""本地量化诊断：不依赖任何 API，直接由逐字稿算出面试答题的硬指标。

点评口径与 AI 点评保持一致（固定三段）：
一、时间（总时长 / 逐段用时 / 节奏分配）
二、优点（带具体地点与原话）
三、问题（问题 + 具体问题地点 + 改进建议，先分层定性）

用于 AI 未配置 / 调用失败时的兜底点评，也作为 AI 点评的客观数据底座。
"""
import re

import paragraphizer as pz

FILLERS = ["嗯", "呃", "啊", "那个", "这个", "就是说", "然后", "所以说", "对吧",
           "其实", "反正", "那么", "这样的", "的话", "怎么说呢", "我个人认为"]

STRUCTURE_WORDS = {
    "总起": ["我认为", "我的看法", "总体来看", "概括来说"],
    "分点": ["第一", "第二", "第三", "首先", "其次", "再次", "最后", "一是", "二是", "三是"],
    "并列": ["一方面", "另一方面", "与此同时", "同时"],
    "因果": ["因为", "所以", "因此", "正因如此", "之所以"],
    "转折": ["但是", "然而", "不过", "反过来说"],
    "收束": ["总之", "综上所述", "回答完毕", "因此我认为"],
}

PUNCT = "，。！？、；：“”‘’（）《》…—,.!?;:\"'() \n\t"

DIVIDER_RE = re.compile(r"(?:^|[，。！？；、\s])(第一|第二|第三|第四|首先|其次|再次|最后|一是|二是|三是)")


def _clean(text):
    return "".join(ch for ch in text if ch not in PUNCT)


def _quote(text, key, limit=20):
    """从段落里摘出含关键词的原话片段"""
    for sen in re.split(r"(?<=[。！？；])", text or ""):
        if key in sen:
            s = sen.strip()
            return s[:limit] + ("…" if len(s) > limit else "")
    s = (text or "").strip()
    return s[:limit] + ("…" if len(s) > limit else "")


def _loc(p, extra=""):
    if not p:
        return "全场"
    return "第%d段「%s」[%s]%s" % (
        p.get("idx", 0), p.get("title") or "未命名",
        pz.span_text(p.get("t_start"), p.get("t_end")), extra)


def _para_metrics(p):
    sec = max(0.0, (p.get("t_end") or 0) - (p.get("t_start") or 0))
    chars = p.get("chars") or _clean(p.get("text") or "")
    chars = len(chars) if isinstance(chars, str) else chars
    speed = round(chars / sec * 60) if sec > 0 else 0
    return round(sec, 1), chars, speed


def _stage_by_role(stages, role):
    """取当前题型中承担某功能角色的环节名（无则返回空）"""
    try:
        import simplifier as _simp
        names, roles = _simp._roles_for(stages or _simp.STAGES)
    except Exception:
        return ""
    for n, r in zip(names, roles):
        if r == role:
            return n
    return ""


# ---------------- 按题型点评维度（点评要素由题型配置驱动） ----------------
# 拆词时先剥掉判断性/程度性虚词，留下可验证的实体词（如「应急预案是否到位」→「应急预案」）
_DIM_WEAK_RE = re.compile(
    r"(是否|有没有|有无|能否|够不够|清晰|明确|合理|到位|充分|准确|得当|有效|规范|自然|得体)")


def _kw_of(s):
    """从一条点评要素里抽出可验证的关键词（长度 ≥2 的实体词）"""
    t = _DIM_WEAK_RE.sub(" ", str(s or ""))
    out = []
    for w in re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]{2,}", t):
        if w not in out:
            out.append(w)
    return out


def _kw_hit(kws, text):
    return [k for k in kws if k and k in text]


def _stages_seen(paras, stages):
    """已讲到的环节（按题型环节顺序，依据精简版结构判定结果）"""
    seen = []
    for s in (stages or []):
        for p in paras:
            if (p.get("plain_stage") or "").strip() == s:
                seen.append(s)
                break
    return seen


def _review_lines(paras, full, stages, checkpoints, faults):
    """生成「按题型点评维度」章节：环节完整性 + 检查点逐条 + 常见失分逐条

    点评要素来自题型配置（review.checkpoints / review.faults），题型未配时
    只输出环节完整性，并提示去设置里补点评要素。
    """
    names = [str(x).strip() for x in (stages or []) if str(x or "").strip()]
    cps = [str(x).strip() for x in (checkpoints or []) if str(x or "").strip()]
    fts = [str(x).strip() for x in (faults or []) if str(x or "").strip()]
    if not names and not cps and not fts:
        return [], {"stage_seen": [], "stage_missing": [], "checkpoint_hit": {}, "fault_hit": {}}
    L = ["## 四、按题型点评维度", ""]
    cp_hit = {}
    f_hit = {}
    seen = _stages_seen(paras, names)
    missing = [n for n in names if n not in seen]
    if names:
        L.append("**环节完整性**：本题型共 %d 个环节（%s），已讲到 %d 个%s。"
                 % (len(names), " / ".join(names), len(seen),
                    ("；缺「%s」，该环节缺失会直接拉低结构分" % "」「".join(missing)) if missing else "，结构完整"))
        L.append("")
    if cps:
        L.append("**评价维度（对照自评）**")
        L.append("")
        L.append("| 点评维度 | 检测结果 | 依据 |")
        L.append("| --- | --- | --- |")
        cp_hit = {}
        for c in cps:
            kws = _kw_of(c)
            hit = _kw_hit(kws, full)
            cp_hit[c] = hit
            if hit:
                L.append("| %s | 已涉及 | 全文出现「%s」等表述 |" % (c, "、".join(hit[:3])))
            elif kws:
                L.append("| %s | 未检出相关表述 | 未出现「%s」等词，回听核对是否遗漏 |"
                         % (c, "、".join(kws[:3])))
            else:
                L.append("| %s | 需人工核对 | 该维度无可用关键词 |" % c)
        L.append("")
    f_hit = {}
    if fts:
        L.append("**本题型常见失分（逐条自查）**")
        L.append("")
        for f in fts:
            kws = _kw_of(f)
            hit = _kw_hit(kws, full)
            f_hit[f] = hit
            if hit:
                L.append("- %s：**已检出相关表述**（「%s」），重点核查这一段" % (f, "、".join(hit[:3])))
            else:
                L.append("- %s：未检出相关表述" % f)
        L.append("")
    if not cps and not fts:
        L.append("> 该题型还没配置点评要素（评价维度 / 常见失分），可在「题型结构」里补上，本地点评就能逐条对照。")
        L.append("")
    return L, {"stage_seen": seen, "stage_missing": missing,
               "checkpoint_hit": cp_hit, "fault_hit": f_hit}


def analyze(question, segments, duration, paragraphs=None, stages=None,
            checkpoints=None, faults=None):
    """本地规则点评

    stages      : 当前题目题型生效的答题环节；题型没有收尾类环节时不再强求「收束句」
    checkpoints : 该题型配置的点评检查点（评价维度），逐条输出是否涉及
    faults      : 该题型的常见失分表现，逐条给出是否检出相关表述
    """
    segments = segments or []
    paras = paragraphs or pz.build_from_segments(segments, {})
    full = "".join(s.get("text") or "" for s in segments)
    clean = _clean(full)
    n_chars = len(clean)
    dur = max(1, int(duration or (segments[-1]["t_end"] if segments else 1)))
    speed = round(n_chars / dur * 60)

    # 口头禅
    fillers = {}
    for w in FILLERS:
        c = full.count(w)
        if c:
            fillers[w] = c
    f_total = sum(fillers.values())
    f_rate = round(f_total / (dur / 60), 1)
    top_filler = max(fillers.items(), key=lambda x: x[1])[0] if fillers else ""
    # 口头禅位置：命中次数最多的段落
    f_para, f_best = None, -1
    for p in paras:
        c = sum((p.get("text") or "").count(w) * n for w, n in fillers.items())
        if c > f_best:
            f_best, f_para = c, p

    # 结构信号词
    struct_hit = {}
    for k, ws in STRUCTURE_WORDS.items():
        if k == "分点":
            hit = sorted(set(DIVIDER_RE.findall(full)))
        else:
            hit = [w for w in ws if w in full]
        if hit:
            struct_hit[k] = hit

    # 停顿
    gaps = []
    for a, b in zip(segments, segments[1:]):
        gaps.append(max(0.0, b["t_start"] - a["t_end"]))
    long_pause = round(max(gaps), 1) if gaps else 0
    pause_at = None
    if gaps:
        i = gaps.index(max(gaps))
        pause_at = paras and pz.para_of_seg(paras, segments[i])

    # 题干关键词覆盖
    q_words = [w for w in re.findall(r"[\u4e00-\u9fa5]{2,}", question or "")]
    covered = [w for w in q_words if w in full]
    cover_rate = round(len(covered) / len(q_words) * 100) if q_words else None

    # ---------- 段落时间表 ----------
    rows, secs = [], []
    for p in paras:
        sec, chars, pspeed = _para_metrics(p)
        secs.append(sec)
        rows.append((p, sec, chars, pspeed))
    total_sec = sum(secs) or dur
    longest = max(rows, key=lambda r: r[1]) if rows else None

    def alloc_judge(r):
        p, sec, chars, pspeed = r
        ratio = sec / total_sec if total_sec else 0
        if ratio >= 0.45:
            return "占比过高（%.0f%%），挤占其他环节" % (ratio * 100)
        if ratio <= 0.08:
            return "占比过低（%.0f%%），交代不足" % (ratio * 100)
        if pspeed > 330:
            return "语速偏快"
        if pspeed and pspeed < 170:
            return "语速偏慢、拖沓"
        return "分配合理"

    # ---------- 分层定性 ----------
    formal_issues, content_issues = [], []
    if speed < 180:
        formal_issues.append(("语速偏慢", "全文语速 %d 字/分，低于 180 的下限" % speed))
    elif speed > 330:
        formal_issues.append(("语速偏快", "全文语速 %d 字/分，超过 330 的上限" % speed))
    if f_rate > 6:
        formal_issues.append(("口头禅密度高", "%.1f 次/分，共 %d 次" % (f_rate, f_total)))
    elif f_rate > 3:
        formal_issues.append(("口头禅偏多", "%.1f 次/分" % f_rate))
    if "分点" not in struct_hit:
        formal_issues.append(("没有分点信号", "全篇未出现“第一/第二/首先/其次”"))
    close_stage = _stage_by_role(stages, "close")
    if close_stage and "收束" not in struct_hit:
        formal_issues.append(("%s没有收束句" % close_stage, "缺“总之/回答完毕”类收束"))
    if dur < 150:
        content_issues.append(("作答时长短于常规", "%d 秒，低于 180-240 秒的建议区间" % dur))
    elif dur > 330:
        content_issues.append(("作答时长偏长", "%d 秒，超出建议区间" % dur))
    if long_pause > 6:
        formal_issues.append(("长停顿卡壳", "%.1f 秒空场" % long_pause))
    if cover_rate is not None and cover_rate < 40:
        content_issues.append(("题干关键词覆盖不足", "仅 %d%%，有跑题风险" % cover_rate))
    if longest and longest[1] / total_sec >= 0.45 and len(rows) >= 3:
        formal_issues.append(("段落时间分配失衡", "最长段占 %.0f%%" % (longest[1] / total_sec * 100)))

    if not formal_issues and not content_issues:
        main_problem = "形式与时间都达标，主症需要落到内容深度上（本地规则无法判断，请开启 AI 点评）"
        major = "内容"
    elif content_issues and not formal_issues:
        main_problem = "形式基本干净，最大问题在内容——%s" % content_issues[0][1]
        major = "内容"
    else:
        main_problem = "最大问题在结构——形式层面的 %s 会让考官抓不住层次，内容再好也打折" % (
            "、".join(x[0] for x in formal_issues[:2]))
        major = "结构"

    # ---------- 优点（只写有证据的） ----------
    pros = []
    if "分点" in struct_hit:
        p = next((x for x in paras if DIVIDER_RE.search(x.get("text") or "")), None)
        pros.append(("分点信号清晰", p, _quote(p.get("text") if p else full, struct_hit["分点"][0]),
                     "用“%s”把层次撑起来了，考官能顺着序号记要点" % struct_hit["分点"][0],
                     "把每个分点的首句压成 12 字以内的判断句，序号后面直接上结论"))
    if "总起" in struct_hit:
        p = paras[0] if paras else None
        pros.append(("开口即表态", p, _quote(p.get("text") if p else full, struct_hit["总起"][0]),
                     "第一段就给出立场，没有绕背景", "表态句固定成“我认为……，理由有三”的句式，保持住"))
    if "收束" in struct_hit:
        p = paras[-1] if paras else None
        pros.append(("有明确收束", p, _quote(p.get("text") if p else full, struct_hit["收束"][0]),
                     "%s没有戛然而止，形式完整" % (close_stage or "结尾"),
                     "收束句控制在三句内，别把收尾段当正文写"))
    if f_total == 0:
        pros.append(("语气干净", None, "", "全篇没有出现“嗯/那个/然后”类口头禅", "继续保持，用停顿代替语气词"))
    if len(rows) >= 3 and longest and longest[1] / total_sec < 0.45:
        pros.append(("段落节奏均衡", None, "", "最长段占比 %.0f%%，没有某一段拖住全场" % (longest[1] / total_sec * 100),
                     "维持当前分段习惯，主体分析段保持 2-3 个"))
    pros = pros[:5]

    # ---------- 生成 Markdown ----------
    L = []
    L.append("## 一、时间")
    L.append("")
    if dur < 150:
        tjudge = "偏短"
    elif dur > 330:
        tjudge = "偏长"
    else:
        tjudge = "在建议区间内"
    L.append("**时间总评**：总时长 %d 秒（%s），相对结构化面试建议的 180-240 秒%s；共 %d 段，平均每段 %.0f 秒。"
             % (dur, pz.mmss(dur), tjudge, len(paras), (total_sec / len(paras)) if paras else 0))
    L.append("")
    L.append("| 段落 | 起止 | 用时 | 字数 | 语速(字/分) | 分配评价 |")
    L.append("| --- | --- | --- | --- | --- | --- |")
    for p, sec, chars, pspeed in rows:
        L.append("| 第%d段 %s | %s | %.0f 秒 | %d | %d | %s |"
                 % (p.get("idx", 0), p.get("title") or "-",
                    pz.span_text(p.get("t_start"), p.get("t_end")), sec, chars, pspeed, alloc_judge((p, sec, chars, pspeed))))
    L.append("")
    if longest:
        L.append("- 最长的一段是%s，用时 %.0f 秒，占全场 %.0f%%。"
                 % (_loc(longest[0]), longest[1], longest[1] / total_sec * 100))
    if rows and rows[0][1] / total_sec > 0.3:
        L.append("- 开头段就占了 %.0f%%，属于前松后紧，表态应压到 20 秒内。" % (rows[0][1] / total_sec * 100))
    if long_pause > 6:
        L.append("- %s前后出现 %.1f 秒空场，是全篇最大的节奏断点。"
                 % (_loc(pause_at), long_pause))
    if len(rows) <= 1:
        L.append("- 全篇只有 1 段，说明从头到尾没有停顿换气，考官无法感知层次；训练时刻意在有意识的分界处停 1 秒。")
    L.append("")

    L.append("## 二、优点")
    L.append("")
    if pros:
        for i, (name, p, quote, why, how) in enumerate(pros, 1):
            loc = _loc(p) if p else "全场"
            q = ("｜原话：「%s」" % quote) if quote else ""
            L.append("%d. **%s**｜地点：%s%s｜为什么成立：%s｜怎么复用：%s" % (i, name, loc, q, why, how))
    else:
        L.append("本场未检测到可复用的形式优点，先按下面的问题清单逐条改，改完优点会自然出现。")
    L.append("")

    L.append("## 三、问题")
    L.append("")
    n_all = len(formal_issues) + len(content_issues)
    L.append("**分层定性**：以上共 %d 条问题，其中 %d 条属于形式小问题，最大问题在%s——%s。"
             % (max(n_all, 1), len(formal_issues), major, main_problem))
    L.append("")
    items = []
    n_formal = len(formal_issues)
    for idx, (name, detail) in enumerate(formal_issues + content_issues, 1):
        p, quote, fix = None, "", ""
        if name == "语速偏慢":
            p, fix = longest[0] if longest else None, "把最长的段落切小，每层压到 40 秒内；朗读训练每 200 字控制在 45 秒"
            quote = _quote(p.get("text") if p else full, "。")
        elif name == "语速偏快":
            p = longest[0] if longest else None
            fix = "在每个分点之间留半秒，把 %d 字/分降到 280 以内" % speed
            quote = _quote(p.get("text") if p else full, "。")
        elif name.startswith("口头禅"):
            p = f_para
            quote = _quote(p.get("text") if p else full, top_filler)
            fix = "把“%s”全部删掉，换成 0.5 秒停顿；每天朗读 3 分钟录音回听" % top_filler
        elif name == "没有分点信号":
            p = longest[0] if longest else None
            quote = _quote(p.get("text") if p else full, "。")
            fix = "在这一段开头加“第一/第二”，示范：这一段我谈三层，第一是……，第二是……，第三是……"
        elif name.endswith("收束句"):
            p = paras[-1] if paras else None
            quote = _quote(p.get("text") if p else full, "。")
            fix = "%s固定一句“以上就是我的看法，回答完毕”，控制在三句内" % name[:-3]
        elif name == "长停顿卡壳":
            p = pause_at
            quote = ""
            fix = "准备 3 句万能过渡：“这一点可以从两个层面看”“换句话说”“更关键的是”"
        elif name.startswith("段落时间分配失衡"):
            p = longest[0] if longest else None
            quote = _quote(p.get("text") if p else full, "。")
            fix = "把这段拆成两段，或在其中插一句“说完原因，我再说影响”做切分"
        elif name == "作答时长短于常规":
            p = None
            quote = ""
            fix = "每个要点补一句具体例子（谁、在哪、做了什么），把时长撑到 180 秒以上"
        elif name == "作答时长偏长":
            p = longest[0] if longest else None
            fix = "砍掉重复解释，每个要点只留“判断句+一个例子”"
            quote = _quote(p.get("text") if p else full, "。")
        elif name == "题干关键词覆盖不足":
            p = paras[0] if paras else None
            quote = _quote(p.get("text") if p else full, "。")
            fix = "开头先把题干的核心词念一遍再表态，确保回答锁定题干"
        items.append((name, detail, p, quote, fix))

    for i, (name, detail, p, quote, fix) in enumerate(items, 1):
        L.append("**问题%d：%s**" % (i, name))
        L.append("- 具体问题地点：%s%s" % (_loc(p) if p else "全场", ("，原话：「%s」" % quote) if quote else "，%s" % detail))
        L.append("- 改进建议：%s" % fix)
        L.append("")

    if not items:
        L.append("**问题1：内容深度无法本地判断**")
        L.append("- 具体问题地点：全场")
        L.append("- 改进建议：开启 AI 点评，按「问题+地点+建议」逐条给出内容层面的诊断")
        L.append("")

    rev_lines, rev_metrics = _review_lines(paras, full, stages, checkpoints, faults)
    if rev_lines:
        L.extend(rev_lines)

    return "\n".join(L), {
        "score": _score(speed, f_rate, struct_hit, dur, long_pause),
        "speed": speed,
        "chars": n_chars,
        "fillers": fillers,
        "filler_total": f_total,
        "duration": dur,
        "long_pause": long_pause,
        "structure": list(struct_hit.keys()),
        "para_count": len(paras),
        "major": major,
        "stage_names": [str(x).strip() for x in (stages or []) if str(x or "").strip()],
        "stage_missing": rev_metrics.get("stage_missing") or [],
        "checkpoints": [str(x).strip() for x in (checkpoints or []) if str(x or "").strip()],
        "checkpoint_hit": rev_metrics.get("checkpoint_hit") or {},
        "faults": [str(x).strip() for x in (faults or []) if str(x or "").strip()],
        "fault_hit": rev_metrics.get("fault_hit") or {},
    }


def _score(speed, f_rate, struct_hit, dur, long_pause):
    score = 75
    if speed < 180 or speed > 330:
        score -= 6
    if f_rate > 6:
        score -= 8
    elif f_rate > 3:
        score -= 4
    if "分点" not in struct_hit:
        score -= 8
    if "收束" not in struct_hit and _stage_by_role(None, "close"):
        score -= 3
    if dur < 150:
        score -= 5
    elif dur > 330:
        score -= 4
    if long_pause > 6:
        score -= 5
    return max(45, min(92, score))
