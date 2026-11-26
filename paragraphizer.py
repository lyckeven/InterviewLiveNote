# -*- coding: utf-8 -*-
"""段落化模块：把逐句识别结果聚合为「按段落计时」的段落结构

规则（可在 data/config.json 中调）：
1. 句与句之间的停顿 >= para_pause_gap 秒 -> 切新段落（换气/思考的分界）；
2. 当前段落时长 >= para_max_sec 秒 -> 强制切段；
3. 当前段落字数 >= para_max_chars 字 -> 强制切段。
每个段落记录 t_start / t_end，前端按「起-止」显示，AI 点评也以段落为单位。
"""
import re
import uuid

FILLER_STRIP = "，。！？、；：“”‘’（）《》…—,.!?;: \n\t"

# 段落标题识别：按功能角色（open/opinion/analyze/action/close）+ 小标题，
# 再映射成当前题型实际生效的环节名（题型未配环节时即通用五环节）
TAG_RULES = [
    ("open", "", r"(各位考官|考官好|考生开始答题|下面我开始|我开始回答)"),
    ("opinion", "表态", r"(我认为|我的看法|我觉得|总体来看|对此|对于这道题|我个人认为|在我看来|我的观点)"),
    ("analyze", "原因", r"(原因|之所以|根源|究其原因|问题出在)"),
    ("analyze", "影响", r"(危害|影响|意义|好处|不良后果|负面|积极作用)"),
    ("action", "对策", r"(对策|措施|建议|抓好|落实|加强|完善|一方面要|首先要|第一要|需要)"),
    ("close", "收束", r"(总之|综上|以上就是|回答完毕|因此我认为|总之我认为)"),
]


def mmss(sec):
    sec = max(0, int(round(sec or 0)))
    return "%02d:%02d" % (sec // 60, sec % 60)


def span_text(t_start, t_end):
    return "%s-%s" % (mmss(t_start), mmss(t_end))


def guess_title(text, fallback_idx=1):
    """按段落开头 40 字内的结构词判断这一段属于哪个答题环节

    返回的是「当前题型」实际生效的环节名（环节由题型结构配置驱动），
    题型未配置环节时回退通用五环节（审题/提观点/重分析/抓落实/结尾）。
    """
    head = (text or "").strip()[:40]
    for role, _sub, pat in TAG_RULES:
        if re.search(pat, head):
            try:
                import simplifier as _simp
                return _simp._stage_for_role(role, fallback_idx)
            except Exception:
                return {"open": "审题", "opinion": "提观点", "analyze": "重分析",
                        "action": "抓落实", "close": "结尾"}.get(role, "")
    first = re.split(r"[。！？；]", (text or "").strip())[0]
    first = first.strip()
    if not first:
        return "第%d段" % fallback_idx
    return (first[:12] + "…") if len(first) > 12 else first


def char_count(text):
    return len("".join(ch for ch in (text or "") if ch not in FILLER_STRIP))


def new_para(idx, t_start):
    return {
        "id": uuid.uuid4().hex[:8],
        "idx": idx,
        "t_start": float(t_start or 0),
        "t_end": float(t_start or 0),
        "title": "",
        "text": "",
        "chars": 0,
        "seg_ids": [],
        "ai_note": "",
        "closed": False,
    }


def need_split(para, seg, cfg):
    """判断新句子是否应开启新段落"""
    if not para:
        return True
    gap = float(seg.get("t_start") or 0) - float(para.get("t_end") or 0)
    if gap >= float(cfg.get("para_pause_gap", 1.5)):
        return True
    if (float(seg.get("t_end") or 0) - float(para.get("t_start") or 0)) >= float(cfg.get("para_max_sec", 55)):
        return True
    if char_count((para.get("text") or "") + (seg.get("text") or "")) >= int(cfg.get("para_max_chars", 240)):
        return True
    return False


def append_seg(para, seg):
    if not para.get("text"):
        para["text"] = seg.get("text") or ""
    else:
        para["text"] += (seg.get("text") or "")
    para["t_end"] = float(seg.get("t_end") or para.get("t_end") or 0)
    para["chars"] = char_count(para["text"])
    para["seg_ids"].append(seg.get("id"))
    if not para.get("title"):
        para["title"] = guess_title(para["text"], para.get("idx", 1))
    return para


def close_para(para):
    if para:
        para["closed"] = True
        if not para.get("title"):
            para["title"] = guess_title(para.get("text"), para.get("idx", 1))
    return para


def build_from_segments(segments, cfg=None):
    """由句子列表重建段落结构（用于老记录、离线复核）"""
    cfg = cfg or {}
    paras = []
    cur = None
    for seg in segments or []:
        if need_split(cur, seg, cfg):
            if cur:
                cur = close_para(cur)
                paras.append(cur)
            cur = new_para(len(paras) + 1, seg.get("t_start") or 0)
        append_seg(cur, seg)
        seg["pid"] = cur["id"]
    if cur:
        close_para(cur)
        paras.append(cur)
    return paras


def render_for_ai(paras):
    """给 AI 的段落文本块"""
    lines = []
    for p in paras or []:
        lines.append(
            "【第%d段｜%s｜%s｜用时 %d 秒｜%d 字】%s"
            % (
                p.get("idx", 0),
                p.get("title") or "未命名",
                span_text(p.get("t_start"), p.get("t_end")),
                int(round((p.get("t_end") or 0) - (p.get("t_start") or 0))),
                char_count(p.get("text")),
                p.get("text") or "",
            )
        )
    return "\n".join(lines)


def para_of_seg(paras, seg):
    for p in paras or []:
        if seg.get("id") in (p.get("seg_ids") or []):
            return p
    return None
