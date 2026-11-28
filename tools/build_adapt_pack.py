# -*- coding: utf-8 -*-
"""把 adapt_draft 里的 4 份素材 JSON 组装成软件内置适配规则包 adapt/"""
import json, os, re, io, sys

DRAFT = r"C:\Users\23576\AppData\Roaming\Tencent\Marvis\User\oAN1i2aHcBbJZcYHAnwVMiilQDpQ\workspace\conv_d6ce7376ecbe415d824e4b2dd6472477\temp\adapt_draft"
OUT = r"E:\Software\InterviewLiveNote\adapt"
VER = "2026.11.28"

os.makedirs(OUT, exist_ok=True)


def load(fn):
    return json.load(open(os.path.join(DRAFT, fn), encoding="utf-8"))


def dump(fn, obj):
    p = os.path.join(OUT, fn)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    print("  %-22s %8d B" % (fn, os.path.getsize(p)))


# 笔记题型名 -> 软件题型库名（data/question_types.json 的 13 种）
QT_MAP = {
    "社会现象": "社会现象",
    "态度观点": "态度观点",
    "自我认知": "自我认知",
    "组织管理": "组织管理",
    "应急应变": "应急应变",
    "人际关系": "人际关系",
    "情景模拟": "现场模拟",
    "漫画": "漫画",
    "演讲": "演讲",
    "反驳": "态度观点",      # 反驳题按态度观点框架作答，作为变体保留
    "串词": "串词题",
    "材料题": "材料题",
    "排序统筹": "排序",
    "排序": "排序",
}

# 各题型的判别正则（硬信号，用于 4 步判别落地成可执行规则）
JUDGE_PATTERN = {
    "情景模拟": r"请\s*(?:现场)?模拟|情景模拟|模拟一下|角色扮演|请你以[^，。]{0,12}身份",
    "材料题": r"根据(?:上述|以下)?材料|阅读材料|给定材料|材料中(?:反映|提到)",
    "漫画": r"漫画|这幅图|图片中|看漫画",
    "串词": r"用[^。]{0,30}(?:几个|下列|以下)?(?:词|词语)[^。]{0,10}(?:串|连|编|讲)",
    "反驳": r"请?(?:逐一)?反驳|反驳上述|反驳以下",
    "演讲": r"(?:以|围绕)[^。]{0,20}(?:为题|为主题)[^。]{0,10}(?:演讲|发言)|发表(?:一段)?(?:演讲|讲话)|即兴",
    "排序": r"排序|统筹安排|先后顺序|合理安排(?:时间|顺序)",
    "态度观点": r"名言|警句|俗语|谚语|领导人(?:讲话|说过)|哲理|寓言|小故事|有人说[^。]{0,30}(?:谈谈|怎么看)",
    "社会现象": r"社会(?:现象|事件)|最近|近年来|某地|新政|出台|引发(?:热议|关注)|你怎么看",
    "组织管理": r"组织|开展|举办|策划|筹备|调研|宣传|培训|座谈会|评比|试点|方案|由你负责",
    "应急应变": r"突然|紧急|突发|立即|马上|情绪激动|投诉|上访|舆情|冲突|争执|停水|停电|故障|事故|隐患",
    "人际关系": r"(?:同事|领导|下属|老同事|老同志|小李|小王|小张)[^。？]{0,24}(?:不配合|误解|误会|矛盾|抱怨|推诿|抢功|批评|责备|议论|不支持|有情绪|意见|争执)|(?:不配合|误解|误会|矛盾|抱怨|推诿|抢功|议论|有情绪)[^。？]{0,20}(?:同事|领导|下属)",
    "自我认知": r"自我介绍|为什么报考|报考动机|优缺点|职业规划|岗位匹配|最难忘记|最成功|最失败|兴趣爱好",
}

# 判别顺序：硬信号优先（第 1 步），形式特征其次（第 2 步），内容性质再次（第 3 步），场景归属最后（第 4 步）
JUDGE_STEPS = [
    (1, "设问含“请现场模拟 / 情景模拟”→ 情景模拟；这是硬信号，题干里即使有投诉、情绪激动等应急词也按情景模拟", "情景模拟"),
    (2, "题目形态：附材料→材料题；图片漫画→漫画；给定 3~7 个词→串词；要求“请反驳 / 逐一反驳”→反驳；要求“以…为题演讲”→演讲；要求“排序 / 具体安排”→排序", "材料题"),
    (2, "题目形态：附材料→材料题；图片漫画→漫画；给定 3~7 个词→串词；要求“请反驳 / 逐一反驳”→反驳；要求“以…为题演讲”→演讲；要求“排序 / 具体安排”→排序", "漫画"),
    (2, "题目形态：附材料→材料题；图片漫画→漫画；给定 3~7 个词→串词；要求“请反驳 / 逐一反驳”→反驳；要求“以…为题演讲”→演讲；要求“排序 / 具体安排”→排序", "串词"),
    (2, "题目形态：附材料→材料题；图片漫画→漫画；给定 3~7 个词→串词；要求“请反驳 / 逐一反驳”→反驳；要求“以…为题演讲”→演讲；要求“排序 / 具体安排”→排序", "反驳"),
    (2, "题目形态：附材料→材料题；图片漫画→漫画；给定 3~7 个词→串词；要求“请反驳 / 逐一反驳”→反驳；要求“以…为题演讲”→演讲；要求“排序 / 具体安排”→排序", "演讲"),
    (2, "题目形态：附材料→材料题；图片漫画→漫画；给定 3~7 个词→串词；要求“请反驳 / 逐一反驳”→反驳；要求“以…为题演讲”→演讲；要求“排序 / 具体安排”→排序", "排序"),
    (3, "看主体内容是抽象道理（名言、俗语、领导人讲话）→ 态度观点；是具体社会事件 → 社会现象", "态度观点"),
    (3, "看主体内容是抽象道理（名言、俗语、领导人讲话）→ 态度观点；是具体社会事件 → 社会现象", "社会现象"),
    (4, "要求开展某项活动→组织管理；紧急消极变化、急需解决→应急应变；人与人矛盾、沟通劝说→人际关系；围绕个人情况与岗位→自我认知", "组织管理"),
    (4, "要求开展某项活动→组织管理；紧急消极变化、急需解决→应急应变；人与人矛盾、沟通劝说→人际关系；围绕个人情况与岗位→自我认知", "应急应变"),
    (4, "要求开展某项活动→组织管理；紧急消极变化、急需解决→应急应变；人与人矛盾、沟通劝说→人际关系；围绕个人情况与岗位→自我认知", "人际关系"),
    (4, "要求开展某项活动→组织管理；紧急消极变化、急需解决→应急应变；人与人矛盾、沟通劝说→人际关系；围绕个人情况与岗位→自我认知", "自我认知"),
]


def build_question_detect():
    src = load("question_detect.json")
    # 跨题型重复的关键词没有区分度（如「启示」「结合自身」），命中只会让判定互相打架
    cnt = {}
    for t in src.get("types") or []:
        for k in (t.get("keywords") or []):
            k = str(k).strip()
            if k:
                cnt[k] = cnt.get(k, 0) + 1
    dup = {k for k, v in cnt.items() if v > 1}

    merged, order = {}, []
    for t in src.get("types") or []:
        raw = str(t.get("题型") or "").strip()
        name = QT_MAP.get(raw)
        if not name:
            continue
        kws = [str(k).strip() for k in (t.get("keywords") or [])
               if str(k).strip() and str(k).strip() not in dup]
        if name in merged:
            host = merged[name]
            for k in kws:
                if k not in host["keywords"]:
                    host["keywords"].append(k)
            for key in ("题干特征", "设问特征"):
                for x in (t.get(key) or []):
                    if x not in host[key]:
                        host[key].append(x)
            if t.get("判别要点"):
                host["判别要点"] = (host["判别要点"] + " " + str(t["判别要点"])).strip()
            if t.get("易混区分") and t["易混区分"] not in host["易混区分"]:
                host["易混区分"] = (host["易混区分"] + " " + str(t["易混区分"])).strip()
            if raw != name:
                host["别名"] = (host["别名"] + "、" + raw).strip("、")
            continue
        merged[name] = {
            "题型": name,
            "别名": raw if raw != name else "",
            "weight": 1,
            "keywords": kws,
            "patterns": [],
            "题干特征": list(t.get("题干特征") or []),
            "设问特征": list(t.get("设问特征") or []),
            "判别要点": str(t.get("判别要点") or ""),
            "易混区分": str(t.get("易混区分") or ""),
            "source": t.get("source") or "",
        }
        order.append(name)
    types = [merged[n] for n in order]
    seen = {t["题型"] for t in types}
    judge = []
    for step, when, raw in JUDGE_STEPS:
        name = QT_MAP.get(raw)
        if not name:
            continue
        judge.append({
            "步骤": step,
            "判断": when,
            "题型": name,
            "pattern": JUDGE_PATTERN.get(raw, ""),
            "source": "2026.11.06面试理论课基础知识汇总·题型判别四步",
        })
    obj = {
        "version": VER,
        "title": "题目识别规则（粉笔体系·笔记提炼）",
        "_说明": "types 里的 keywords 命中一次按 weight 加分，patterns 命中按 1.5 倍加分；judge_order 按步骤顺序命中即定题型。题型名请使用软件题型库里的名字（社会现象/态度观点/人际关系/组织管理/应急应变/现场模拟/自我认知/材料题/串词题/演讲/漫画/排序）。",
        "question_markers": [
            r"^\s*\d{1,3}\s*[\.、．)）]\s*",
            r"^\s*[（(]\s*\d{1,3}\s*[）)]\s*",
            r"^\s*第\s*\d{1,3}\s*题[）)]?\s*[:：、]?\s*",
            r"^\s*【\s*(?:题目|真题)\s*】\s*",
        ],
        "types": types,
        "score_rules": [],
        "judge_order": judge,
    }
    dump("question_detect.json", obj)
    return seen


def build_frameworks():
    src = load("frameworks.json")
    by_name, order = {}, []
    for t in src.get("types") or []:
        raw = str(t.get("题型") or "").strip()
        name = QT_MAP.get(raw)
        if not name:
            continue
        if raw == "反驳":
            # 反驳并入态度观点，作为变体保留
            host = by_name.get("态度观点")
            if host is not None:
                fr = t.get("框架")
                heads, pts = [], []
                if isinstance(fr, list):
                    for s in fr:
                        if isinstance(s, dict):
                            lb = s.get("label") or s.get("name")
                            if lb:
                                heads.append(str(lb))
                            for p in (s.get("points") or []):
                                if str(p).strip():
                                    pts.append(str(p).strip())
                variants = host.get("变体")
                if not isinstance(variants, dict):
                    variants = {}
                variants["反驳题"] = {"结构": heads, "要点": pts[:12],
                                     "参考": str(t.get("共性与总纲") or t.get("总框架") or "").strip()}
                host["变体"] = variants
                if t.get("反驳方法"):
                    host["反驳方法"] = t["反驳方法"]
            continue
        item = {k: v for k, v in t.items() if k != "题型"}
        item["题型"] = name
        item["别名"] = raw if raw != name else ""
        # 原名保留，便于追溯笔记口径
        item["笔记题型"] = raw
        if name in by_name:
            host = by_name[name]
            variants = host.get("变体")
            if not isinstance(variants, dict):
                variants = {}
            fr = t.get("框架") or t.get("总框架")
            variants[raw + "-补充"] = {"结构": [], "要点": [], "参考": str(t.get("总思路") or t.get("共性与总纲") or "")}
            host["变体"] = variants
            continue
        by_name[name] = item
        order.append(name)
    obj = {
        "version": VER,
        "title": "题型答题框架（粉笔体系·笔记提炼）",
        "_说明": "每题型支持：框架/总框架/作答思路（[{label,points}] 或 {变体名:{结构,要点,参考}}）、审题（dict 或 list，作为第一环节）、变体/类型变体、注意事项/占比要求/总结提升等说明字段。",
        "types": [by_name[n] for n in order],
    }
    dump("frameworks.json", obj)


def build_comment_rules():
    src = load("comment_rules.json")
    obj = {
        "version": VER,
        "title": "点评方式与学员常见问题（粉笔体系·点评集中培训 + 学员常见问题手册）",
        "_说明": "method 为点评方法论条目；problems 为按题型归集的常见问题，字段：题型/环节/问题/表现/举例/原因/建议。本地点评会按题型命中优先取用。",
        "method": src.get("method") or [],
        "problems": src.get("problems") or [],
    }
    dump("comment_rules.json", obj)


def build_segment_rules():
    obj = {
        "version": VER,
        "title": "段落环节识别规则",
        "_说明": "role 取 open/opinion/analyze/action/close；pattern 为正则，命中即认为该段落属于该环节。适配规则优先于内置规则。",
        "rules": [
            {"role": "open", "sub": "开场", "pattern": r"(各位考官|考官好|考生开始答题|下面我开始|我开始回答|我先谈谈|首先谈谈)", "source": "通用"},
            {"role": "opinion", "sub": "表态", "pattern": r"(我认为|我的看法|我的态度是|我的观点|我觉得|总体来看|对此|对于这道题|在我看来|个人认为)", "source": "通用"},
            {"role": "analyze", "sub": "原因", "pattern": r"(原因|之所以|根源|究其原因|问题出在|背后的|深层次)", "source": "通用"},
            {"role": "analyze", "sub": "影响", "pattern": r"(危害|影响|意义|好处|不良后果|负面|积极作用|不利于|有利于)", "source": "通用"},
            {"role": "action", "sub": "对策", "pattern": r"(对策|措施|建议|抓好|落实|加强|完善|一方面要|首先要|第一要|可以从.{0,8}方面|具体做法|下一步)", "source": "通用"},
            {"role": "close", "sub": "收束", "pattern": r"(总之|综上|以上就是|回答完毕|因此我认为|总之我认为|我相信)", "source": "通用"},
        ],
    }
    dump("segment_rules.json", obj)


def build_text_fix():
    obj = {
        "version": VER,
        "title": "文字处理词表（口误纠正 + 口头禅）",
        "_说明": "pairs 为 [错, 对] 或 {错, 对} 的替换对，转写文本会优先按适配替换对纠正；fillers 为口头禅词表，用于统计与净化。只写确实会出现的口误，避免误改正常表达。",
        "pairs": [
            {"错": "面试官", "对": "考官", "source": "通用"},
            {"错": "考生开始回答", "对": "考生开始答题", "source": "通用"},
            {"错": "同学们", "对": "各位考官", "source": "通用"},
        ],
        "fillers": ["嗯", "呃", "啊", "那个", "这个", "就是说", "然后", "所以说", "对吧", "怎么说呢", "反正"],
    }
    dump("text_fix.json", obj)


def build_bank_parse():
    obj = {
        "version": VER,
        "title": "笔记取题规则（从课堂笔记 / 题本 / 逐字稿里抽题）",
        "_说明": "question_start 命中即认为新题开始；answer_start 之后的内容归入该题参考要点；section_heads 识别分节标题（分节名可用于推题型）；noise 为页码等噪声行；ask_words 用于识别没编号的问句；min_len/max_len 控制题干长度范围。",
        "question_start": [
            r"^\s*\d{1,3}\s*[\.、．]\s*",
            r"^\s*[（(]\s*\d{1,3}\s*[）)]\s*",
            r"^\s*第\s*\d{1,3}\s*题[）)]?\s*[:：、]?\s*",
            r"^\s*【\s*(?:题目|真题|例题)\s*】\s*",
            r"^\s*[（(]\s*(?:19|20)\d{2}[^）)]{0,40}[）)]\s*\S+[。？]",
        ],
        "answer_start": [
            r"^\s*【?\s*(?:参考答案|参考要点|答题示范|示范作答|答题思路|解析|作答参考|三分钟版本|三分钟作答)\s*】?\s*[:：]?",
            r"^\s*(?:考生作答|考生开始答题|考生答题完毕)\s*[:：]?",
        ],
        "section_heads": [
            r"^\s*[一二三四五六七八九十]+[、\.]\s*\S{2,20}$",
            r"^\s*【[^】]{2,20}】\s*$",
            r"^\s*#+\s*\S{2,20}$",
            r"^\s*(?:说课|理论|点评|集中培训|学员常见问题)[^。？]{0,20}$",
        ],
        "qtype_hints": {
            "社会现象": ["社会现象", "现象类", "现象"],
            "态度观点": ["态度观点", "观点类", "观点分析", "反驳"],
            "组织管理": ["组织管理", "计划组织"],
            "应急应变": ["应急应变", "情景应变", "应变"],
            "人际关系": ["人际关系", "人际交往", "人际沟通"],
            "现场模拟": ["情景模拟", "现场模拟", "模拟"],
            "自我认知": ["自我认知", "岗位匹配"],
            "材料题": ["材料题", "材料"],
            "串词题": ["串词"],
            "演讲": ["演讲", "致辞"],
            "漫画": ["漫画"],
            "排序": ["排序", "统筹"],
        },
        "noise": [
            r"^\s*第\s*\d+\s*页\s*$",
            r"^\s*\d{1,3}\s*/\s*\d{1,3}\s*$",
            r"^\s*[—\-=_·\*]{3,}\s*$",
            r"^\s*\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日\s*$",
            r"^\s*(?:微信公众号|小红书|知乎|粉笔)[^\n]{0,20}$",
        ],
        "ask_words": ["怎么看", "谈谈", "怎么办", "如何", "为什么", "请你", "假如", "如果",
                      "你作为", "你怎么", "请现场模拟", "有什么启示", "请问", "对此"],
        "min_len": 12,
        "max_len": 1500,
    }
    dump("bank_parse.json", obj)


def build_bank_items():
    src = load("bank_extract.json")
    items = []
    for it in src.get("items") or []:
        q = str(it.get("question") or "").strip()
        if not q:
            continue
        raw = str(it.get("qtype") or "").strip()
        name = QT_MAP.get(raw, "")
        if not name:
            name = raw
        exam = it.get("exam")
        if isinstance(exam, str):
            exam = [exam] if exam.strip() else []
        items.append({
            "question": q,
            "qtype": name,
            "qtype_reason": str(it.get("qtype_reason") or ""),
            "exam": exam or [],
            "structure": it.get("structure") or [],
            "keys": it.get("keys") or [],
            "ref": str(it.get("ref") or ""),
            "source": str(it.get("source") or "") or ("分节：" + str(it.get("section") or "")),
        })
    obj = {
        "name": "适配包真题（粉笔体系·2026.11.28）",
        "version": VER,
        "note": "从《2026年结构化新人培训题本【题干版+出处】》等笔记中规则抽取，conf 需人工复核；在「本地适配」页点「导入题库」才真正入库。",
        "items": items,
    }
    dump("bank_items.json", obj)
    return len(items)


def build_manifest():
    obj = {
        "id": "fby-adapt-20261128",
        "name": "粉笔体系适配规则包",
        "version": VER,
        "author": "慕夕制作",
        "scene": "公考结构化面试·粉笔体系",
        "min_app_version": "2.7.0",
        "built_at": "2026-11-28",
        "note": "由《2026.11.06面试理论课基础知识汇总》《2026年结构化新人培训题本》《学员常见问题手册》《点评集中培训-社会现象》等笔记提炼。题目识别规则、答题框架、点评方式、笔记取题规则四类内容可独立更新，无需重装软件。",
        "sections": ["question_detect.json", "frameworks.json", "comment_rules.json",
                     "segment_rules.json", "text_fix.json", "bank_parse.json", "bank_items.json"],
    }
    dump("manifest.json", obj)


if __name__ == "__main__":
    print("生成内置适配规则包 ->", OUT)
    build_manifest()
    names = build_question_detect()
    print("    识别题型:", len(names))
    build_frameworks()
    build_comment_rules()
    build_segment_rules()
    build_text_fix()
    build_bank_parse()
    n = build_bank_items()
    print("    题库条目:", n)
    total = sum(os.path.getsize(os.path.join(OUT, f)) for f in os.listdir(OUT))
    print("合计 %.1f KB" % (total / 1024.0))
