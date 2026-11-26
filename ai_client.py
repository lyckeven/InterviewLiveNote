# -*- coding: utf-8 -*-
"""AI 点评客户端：走 OpenAI 兼容协议，可接 DeepSeek / 通义 / Kimi / 本地 vLLM 等

点评口径说明（依据粉笔《点评指导》系列资料）：
1. 整场点评固定三段：一、时间；二、优点；三、问题（问题 + 具体问题地点 + 改进建议）。
2. 实时点评按「段落」进行：每段答完即评本段，先判定段落功能标签，再给本段的优点与问题
   （同样带地点与改进建议）。
3. 诊断优先用两句口诀定性——「这事儿题干问了吗」「这两点能不能合并、这一点该在哪个环节」，
   再落到具体问题名。**答题环节与点评要素一律按当前题型的配置注入**（题型结构管理页可编辑）：
   调用方通过 stage_names / checkpoints / faults 传入；未配置时才回退通用五环节清单。
"""
import json
import os
import re
import time
import requests

from log_util import get_logger, mask_secret, short

# 模块级 logger：AI 请求的模型/地址/超时/耗时/HTTP 状态/返回摘要都落日志，
# 出问题时不用猜是网络、Key 还是模型返回格式的问题。密钥一律脱敏成 sk-***
log = get_logger("ai_client")

# 复用同一条 HTTP 连接（keep-alive）：逐题串行优化时每题都要发一次请求，
# 若每次新建连接就要多付一次 TCP+TLS 握手（约 0.1~0.4s/题），31 题白等十几秒。
# 连接池由 urllib3 保证线程安全，这里只调 post 不改 Session 内的 headers。
_HTTP = requests.Session()
_HTTP.headers.update({"Connection": "keep-alive"})

DEFAULT_CONFIG = {
    "base_url": "https://api.deepseek.com/v1",
    "api_key": "",
    "model": "deepseek-chat",
    "temperature": 0.3,
    "timeout": 120,
    "enable_realtime": False,   # 段落答完自动点评（费 token，默认关）
    "para_pause_gap": 1.5,      # 句间停顿≥该秒数，视为段落分界
    "para_max_sec": 55,         # 单段最长秒数，超过强制分段
    "para_max_chars": 240,      # 单段最长字数，超过强制分段
    "enable_correction": True,  # 精简版段落做 AI 智能纠错（同音字/错别字/口误）
    "points_judge_timeout": 25,  # 精简要点「答到/偏题/遗漏」AI 判定超时（秒），超时静默回退本地
    # ---- 音频输入源记忆（v2.0）----
    "input_kind": "loopback",   # loopback=系统内录 / mic=麦克风
    "input_device_name": "",    # 记住上次用的设备名（换机器时按名字匹配失败会自动回退）
    "input_device_index": -1,   # 设备序号兜底
}

SYSTEM_PROMPT = (
    "你是一位资深公务员面试培训讲师，长期用粉笔体系训练学员，擅长结构化面试的现场听记与分段诊断。"
    "你点评严格、具体、可执行：先说结论，再给依据（必须引用考生原话并标明所在段落与时间），"
    "最后给出可直接照做的替代表达。禁止空泛套话（如“内容不错继续加油”“表达还算流畅”），"
    "凡是判断都要落在具体句子和具体时间段上。"
)

# 通用点评清单（仅在题型未配置点评要素时作为回退坐标；题型配了 checkpoints / faults 就用题型自己的）
CHECK_LIST = (
    "审题环节：题干理解有误 / 重点不清（把背景当核心、忽略设问） / 忽略设问要求；\n"
    "提观点环节：观点不明确 / 观点冗长（复述题干、头重脚轻） / 立场偏差 / 措辞套路化；\n"
    "重分析环节：分析角度不全 / 要点展开不够 / 分析句与对策句杂糅 / 要点杂糅重复（该并不并） / "
    "内容老套 / 缺针对性（像套模板） / 内容本身错误 / 过度引申（硬拔高）；\n"
    "抓落实环节：对策空洞无物（谁做、什么载体、怎么做、达到什么效果四要素缺失） / 解释性语言过多 / "
    "对策角度不全（与前面分析失衡） / 对策方向偏差 / 对策不切实际或过度拔高；\n"
    "结尾环节：结尾太长（当正文写） / 结尾突兀（草草收场）。"
)


def _clean_list(v):
    return [str(x).strip() for x in (v or []) if str(x or "").strip()]


def _stage_env(stage_names=None, checkpoints=None, faults=None):
    """按题型组装「答题环节说明」与「点评要素坐标」

    题型配了环节 → 只允许用这些环节名；配了 checkpoints / faults → 用题型自己的点评要素；
    两者都没配 → 回退通用五环节与粉笔通用清单。
    """
    names = _clean_list(stage_names)
    if names:
        stage_line = ("、".join(names) + "（共 %d 个环节，诊断必须点名到这些环节，"
                      "不得使用其他环节名，也不要自行增补环节）" % len(names))
    else:
        stage_line = "该题型未配置答题环节，按通用环节把握：审题、提观点、重分析、抓落实、结尾。"
    cps, fts = _clean_list(checkpoints), _clean_list(faults)
    if cps or fts:
        parts = []
        if cps:
            parts.append("检查点（逐条看是否做到）：" + "；".join(cps))
        if fts:
            parts.append("常见失分表现（命中就点名）：" + "；".join(fts))
        checklist = "本题型点评要素（只作定名依据，不要照抄原文）：\n" + "\n".join(parts)
    else:
        checklist = "该题型未配置点评要素，参照通用清单定名：\n" + CHECK_LIST
    return stage_line, checklist

REVIEW_TEMPLATE = """请对下面这场面试作答做诊断。输出必须严格是三段结构，不得增删标题、不得改名、不得写前言或结语。

【题目】
{question}

【题干类型】{qtype}
【本题型的答题环节】{stages_line}
【参考要点/评分标准】
{ref}

【作答段落】（按考生作答顺序，每段含起止时间、用时与全文）
{paragraphs}

【逐字稿句子】（用于精确定位原话，格式 [起-止] 原文）
{transcript}

【作答总时长】{duration} 秒

诊断坐标（只作为定名依据，不要照抄）：
{checklist}

输出要求：

## 一、时间
第一行先给时间总评：总时长 X 秒，相对建议区间（结构化面试 180-240 秒）是偏短、合适还是偏长，一句话定性。
然后输出表格，逐段给出用时与分配评价：
| 段落 | 起止 | 用时 | 字数 | 语速(字/分) | 分配评价 |
表格后补 2-4 条时间层面的具体判断：哪一段明显偏长或偏短（写明段落名与秒数）、是否存在前松后紧、开头表态是否占用过多时间、有没有在某个非重点段落反复绕。
只评价时间与节奏，不要在这一段谈内容好坏。

## 二、优点
3-5 条，每条一行，格式固定（竖线分隔，缺一项不可）：
**优点判断词（不超过 8 字）**｜地点：第N段「段落标题」[起-止]｜原话：「逐字照抄不超过 20 字」｜为什么成立：一句话｜怎么复用：一句话
优点必须是有证据的具体做法（如分点信号清晰、某段对策带了主体与载体、某处过渡句自然），
禁止写“表达流畅”“思路清晰”“态度端正”这类无证据的空话。若确实不足 3 条，有几条写几条，不许凑数。

## 三、问题
开头先给一句分层定性，格式固定：
**分层定性**：以上共 N 条问题，其中 X 条属于形式小问题，最大问题在<结构 / 内容 / 审题 / 时间>——<一句话点明主症>。
（若最大问题不在结构，就如实写明真正的主症，不要为了套模板硬说结构。）

然后按严重程度从高到低排列，每条固定三行，缺一不可：
**问题N：<一句话定性，不超过 20 字>**
- 具体问题地点：第N段「段落标题」[起-止]，原话：「逐字照抄考生原话，不超过 30 字」
- 改进建议：<可直接照做的动作或示范句，要给出可背诵的替代说法，禁止写“加强逻辑”“多练习”这类空话>
（若该问题覆盖全场而非单一段落，地点写「全场 / 第X-Y段」，原话仍须引用真实片段。）

定名前先想两句口诀，再定名：
1）这事儿题干问了吗——用于识别审题偏差、答非所问、过度引申；
2）这两点能不能合并、这一点该在哪个环节——用于识别要点重复、分析夹对策、对策放错位置。
"""

PARAGRAPH_TEMPLATE = """考生正在作答，现在请你只针对刚说完的这一段做即时点评。
题目：{question}
题型：{qtype}
本题型答题环节：{stages_line}
本段是第 {idx} 段，起止 [第 {idx} 段]{ts}，用时 {sec} 秒，字数 {chars} 字。
本段全文：{text}
此前段落标签：{prev}

输出格式严格如下五行（不要编号、不要前后缀、总长不超过 200 字）：
【段落标签】<4-10 字，从本题型答题环节里挑最贴合的一个，可加小后缀，如：{stage_examples}>
【定性】<一句话说这一段完成度>
【优点】<一句话，必须引用本段原话片段；确实没有就写“无明显优点”>
【问题】<问题名（不超过 16 字）>｜<具体地点：本段第几层/哪句话>｜<改进建议：给示范句>
（若本段没有明显问题，第四行写：【问题】无）

诊断坐标（只作定名依据）：
{checklist}
"""


SENTENCE_TEMPLATE = """这是一场结构化面试的实时听记，请对下面这一句做极简速评（不展开）。
题目：{question}
本句：{text}

输出严格三行、总长不超过 80 字：
【判断】<亮点 / 硬伤 / 中性>
【问题】<若为硬伤，写问题名，不超过 12 字；否则写“无”>
【建议】<一句可直接照做的动作或示范说法>
"""


POINTS_TEMPLATE = """下面是一名考生的口语化作答（已做精简纠错，但仍带口头禅与重复）。请把它压缩成「答题要点关键词」，供面试老师快速看结构。

题目：{question}
题型：{qtype}
本题答题环节：{stages}
参考要点：{ref}

【输出格式】每个环节一行，必须使用给出的环节名，不得改名、不得新增环节名：
【环节名】要点1；要点2；要点3

【硬性要求】
1. 每条要点只写关键词短语，不超过 20 字，不要整句复述，不要写「我认为」「然后」「就是说」等口头禅；
2. 同一环节内合并重复或同义表述，最多 6 条；
3. 环节按上面给出的顺序输出，考生没说到的环节直接省略该行，不要编造考生没说的内容；
4. 只输出上述【环节名】行，不要写前言、结语、解释或编号说明。
"""


POINTS_JUDGE_TEMPLATE = """你是结构化面试考官。请逐条判定「考生作答要点」是否答到了「本题参考要点」，只做判定，不改写文字。

题目：{question}
题型：{qtype}
本题答题环节：{stages}

【本题参考要点】
{refs}

【考生作答要点（已从口语稿提炼）】
{studs}

【输出格式】只输出严格 JSON，不要解释、不要代码块围栏：
{{"items": [{{"id": "R1", "hit": true, "sid": "S2", "reason": "一句话判定理由", "link": "完整"}}],
  "off": [{{"sid": "S5", "reason": "为什么算偏题"}}],
  "summary": "一句话总评"}}

【判定要求】
1. items 必须逐条覆盖每一条参考要点，id 原样使用参考要点前给出的编号（R1、R2…），不得新增、合并或省略；
2. hit 表示该条参考要点是否被考生答到；答到时 sid 填最对应的考生要点编号（S1、S2…），答不到时 sid 填空字符串；
3. link 表示链路完整性，只能取「完整」「部分」「缺失」三值：完整=动作与对象都说到；部分=只说到对象或只说到动作；缺失=完全没说到；
4. off 列出跑题、与本题无关或方向明显错误的考生要点，用 sid 指向；没有跑题的给空数组；
5. 参考要点与考生要点里出现的括号、序号不要输出到英文键名里；不得输出 Markdown、代码块或任何解释文字。
"""


OPTIMIZE_TEMPLATE = """你是资深公务员面试教研员。下面是一道已经入库的面试题，请在不改变原题设问与本意的前提下做「质量优化」，输出严格 JSON（不要解释、不要代码块围栏）。

【原题数据】
题干：{question}
现标注题型：{qtype}
现有审题要点：{exam}
现有答题环节：
{structure}
现有参考要点：
{ref}

【优化目标，逐项都要做到】
1. 题干规范：补全被截断的句子、改掉 OCR 漏字与错别字、统一标点，去掉题号与「考生作答」「请听题」等非题干文字，保留原始设问与限定条件；
2. 题型校准：按真实设问重新判定题型，只能取：综合分析 / 组织管理 / 应急应变 / 人际关系 / 情景模拟 / 自我认知 / 演讲串词，判断与原标注不一致时要改为正确题型；
3. 审题要点：5-7 条，必须覆盖身份定位、核心任务、对象场景、核心矛盾、限定条件、最容易丢分处，每条一句话、扣住本题关键词；
4. 答题环节与要点：环节名必须与上方「环节清单」里该题型配置的环节一一对应、顺序固定，不得改名或增删；每个环节给出 label（不超过 14 字的具体小标题）与 points（2-4 条具体内容，禁止通用套话）；
5. 参考要点提质：合并重复、删掉空话、补齐缺失角度，写清「做什么 + 对谁做 + 怎么做 + 达到什么效果」四要素；只能提升表达与完整度，不得臆造题目里没有的信息；
6. 关键要点 keys：5-7 条，每条一句话、可直接照做。

【输出 JSON 结构】
{{
  "question": "规范化后的题干",
  "qtype": "校准后的题型",
  "qtype_reason": "一句话说明判定依据",
  "exam": ["审题要点"],
  "structure": [{{"stage": "环节名", "label": "小标题", "points": ["要点"]}}],
  "keys": ["关键要点"],
  "ref": "整理后的参考要点文本，依次含【题型】【审题要点】【答题结构】【关键要点】四段"
}}

【环节清单】
{stage_enum}
"""


QUESTION_TEMPLATE = """请解析下面这道面试题，输出严格 JSON（不要解释文字、不要代码块围栏）。

【题目原文】
{question}

【输出 JSON 结构】
{{
  "question": "规范化后的题干（去掉题号与「考生作答」等提示，保留原始设问）",
  "qtype": "题型，只能取：综合分析 / 组织管理 / 应急应变 / 人际关系 / 情景模拟 / 自我认知 / 演讲串词",
  "qtype_reason": "一句话说明判断依据",
  "exam": ["审题要点 5-7 条，须覆盖：身份定位、核心任务、对象场景、核心矛盾、限定条件、最容易丢分处"],
  "structure": [
    {{"stage": "答题环节，必须取本题型对应的环节名（见下方环节清单）",
      "label": "该环节针对本题的具体小标题（不超过 14 字）",
      "points": ["该环节要说的具体内容 2-4 条，必须扣住本题题干关键词，禁止通用套话"]}}
  ],
  "keys": ["关键得分要点 5-7 条，每条一句话，具体可执行"],
  "ref": "把以上内容整理成可读的「参考要点」文本，依次包含【题型】【审题要点】【答题结构】【关键要点】四段"
}}
答题环节清单（先定题型，再用该题型的环节，禁止混用其他题型的环节名）：
{stage_enum}
要求：structure 的环节必须与所判题型的环节一一对应、顺序固定，不得自行增删或改名；
points 必须结合本题的具体主体、对象与矛盾，不能写成泛化模板；
ref 与其余字段内容一致，不要出现题目原文以外的臆造信息。"""


class AIClient:
    def __init__(self, config_path):
        self.config_path = config_path
        self.cfg = dict(DEFAULT_CONFIG)
        self._mtime = 0.0
        self.last_error = None      # {"status","body","url","model"} 最近一次失败的真实返回
        self.last_req = {}          # {"url","model","base_url"} 最近一次真实请求
        self.reload_count = 0
        self.load()

    def load(self):
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.cfg.update({k: v for k, v in data.items() if v is not None})
            self._mtime = os.path.getmtime(self.config_path)
        except Exception:
            pass
        return self.cfg

    def reload_if_changed(self):
        """设置页改完配置后无需重启：每次调用前按文件 mtime 热更新一次"""
        try:
            mt = os.path.getmtime(self.config_path)
        except Exception:
            return False
        if mt != self._mtime:
            self.load()
            self.reload_count += 1
            return True
        return False

    def source(self):
        """当前实际生效的配置来源（设置页/排查用）"""
        key = (self.cfg.get("api_key") or "").strip()
        masked = (key[:6] + "..." + key[-4:]) if len(key) > 12 else ("已配置" if key else "")
        return {
            "config_path": self.config_path,
            "base_url": self.cfg.get("base_url", ""),
            "model": self.cfg.get("model", ""),
            "api_key_masked": masked,
            "has_key": bool(key),
            "reload_count": self.reload_count,
        }

    def probe(self, timeout=20):
        """真实打一次接口并原样返回状态码与正文，不抛异常，供「测试连通」与导入前自检使用"""
        self.reload_if_changed()
        out = {"ok": False, "url": "", "model": self.cfg.get("model", ""),
               "base_url": self.cfg.get("base_url", ""),
               "config_path": self.config_path, "status": None, "body": "", "content": "",
               "error": "", "api_key_masked": self.source()["api_key_masked"]}
        key = (self.cfg.get("api_key") or "").strip()
        if not key:
            out["error"] = "尚未配置 API Key"
            return out
        url = self.cfg["base_url"].rstrip("/") + "/chat/completions"
        out["url"] = url
        self.last_req = {"url": url, "model": out["model"], "base_url": out["base_url"]}
        body = {"model": out["model"], "messages": [{"role": "user", "content": "只回复两个字：可用"}],
                "temperature": 0, "max_tokens": 64, "stream": False}
        try:
            r = _HTTP.post(url, headers={"Authorization": "Bearer " + key,
                                         "Content-Type": "application/json"},
                           data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                           timeout=timeout)
            out["status"] = r.status_code
            out["body"] = (r.text or "")[:1200]
            if r.status_code == 200:
                try:
                    j = r.json()
                    ch = (j.get("choices") or [{}])[0] or {}
                    out["content"] = ((ch.get("message") or {}).get("content") or "").strip()
                    out["finish_reason"] = ch.get("finish_reason")
                    out["model_echo"] = j.get("model") or ""
                except Exception as e:
                    out["error"] = "返回非 JSON：%s" % e
                out["ok"] = (out["status"] == 200)
                if out["ok"] and not out["content"]:
                    out["error"] = "HTTP 200 但 content 为空（模型疑似推理模型，额度耗在 reasoning）"
            else:
                out["error"] = "HTTP %s" % r.status_code
                self.last_error = {"status": r.status_code, "body": out["body"], "url": url, "model": out["model"]}
        except Exception as e:
            out["error"] = "%s: %s" % (type(e).__name__, str(e)[:300])
            self.last_error = {"status": None, "body": out["error"], "url": url, "model": out["model"]}
        return out

    def save(self, patch: dict):
        for k in DEFAULT_CONFIG:
            if k in patch:
                self.cfg[k] = patch[k]
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.cfg, f, ensure_ascii=False, indent=2)
        return self.cfg

    def public_config(self):
        key = self.cfg.get("api_key") or ""
        masked = (key[:6] + "..." + key[-4:]) if len(key) > 12 else ("已配置" if key else "")
        return {
            "base_url": self.cfg.get("base_url", ""),
            "model": self.cfg.get("model", ""),
            "api_key_masked": masked,
            "has_key": bool(key),
            "enable_realtime": bool(self.cfg.get("enable_realtime", False)),
            "para_pause_gap": float(self.cfg.get("para_pause_gap", 1.5)),
            "para_max_sec": int(self.cfg.get("para_max_sec", 55)),
            "para_max_chars": int(self.cfg.get("para_max_chars", 240)),
            "enable_correction": bool(self.cfg.get("enable_correction", True)),
            "input_kind": self.cfg.get("input_kind") or "loopback",
            "input_device_name": self.cfg.get("input_device_name") or "",
            "input_device_index": int(self.cfg.get("input_device_index", -1)),
        }

    def _chat(self, messages, temperature=None, max_tokens=4096, timeout=None, json_mode=False,
              stream=False, on_delta=None):
        """调用 chat/completions；stream=True 且给了 on_delta 时走流式，
        on_delta(已收到的完整文本) 会被每收到一小段就回调一次，供前端实时显示 AI 正在写的字"""
        self.reload_if_changed()
        key = (self.cfg.get("api_key") or "").strip()
        if not key:
            log.warning("AI 请求被拒：未配置 API Key")
            raise RuntimeError("尚未配置 API Key，请先到「设置」里填写")
        url = self.cfg["base_url"].rstrip("/") + "/chat/completions"
        self.last_req = {"url": url, "model": self.cfg.get("model", ""),
                         "base_url": self.cfg.get("base_url", ""),
                         "config_path": self.config_path}
        _to = self.cfg.get("timeout", 120) if timeout is None else timeout
        _t0 = time.time()
        log.info("AI 请求开始：模型=%s 地址=%s 超时=%ss max_tokens=%s json=%s stream=%s 消息数=%d",
                 self.cfg.get("model", ""), mask_secret(url), _to, max_tokens,
                 bool(json_mode), bool(stream and on_delta), len(messages or []))

        def once(mt, streaming=False):
            body = {
                "model": self.cfg.get("model", "deepseek-chat"),
                "messages": messages,
                "temperature": self.cfg.get("temperature", 0.3) if temperature is None else temperature,
                "max_tokens": mt,
                "stream": bool(streaming),
            }
            if json_mode:
                # 让服务端强制返回合法 JSON 对象，避免推理模型夹带说明文字导致解析失败
                body["response_format"] = {"type": "json_object"}
            try:
                r = _HTTP.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    },
                    data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                    timeout=self.cfg.get("timeout", 120) if timeout is None else timeout,
                    stream=bool(streaming),
                )
            except Exception as e:
                self.last_error = {"status": None, "body": "%s: %s" % (type(e).__name__, str(e)[:300]),
                                   "url": url, "model": self.cfg.get("model", "")}
                log.warning("AI 请求失败（网络/超时，耗时 %.1fs）：%s: %s｜模型=%s 地址=%s 超时=%ss",
                            time.time() - _t0, type(e).__name__, str(e)[:300],
                            self.cfg.get("model", ""), mask_secret(url), _to)
                raise
            if r.status_code != 200:
                self.last_error = {"status": r.status_code, "body": (r.text or "")[:1200],
                                   "url": url, "model": self.cfg.get("model", "")}
                log.warning("AI 返回非 200（耗时 %.1fs）：HTTP %s｜模型=%s 地址=%s 响应=%s",
                            time.time() - _t0, r.status_code, self.cfg.get("model", ""),
                            mask_secret(url), short(r.text or "", 300))
                raise RuntimeError(f"AI 接口返回 {r.status_code}: {r.text[:300]}")
            if streaming:
                return None, r
            return r.json(), None

        # ---------------- 流式：边收边回调，让前端看到 AI 实时返回的文字 ----------------
        if stream and on_delta is not None:
            _buf = []
            _st = {"fin": None, "n": 0, "last": time.time()}
            def _emit():
                if not _buf:
                    return
                try:
                    on_delta("".join(_buf))
                except Exception:
                    pass
            try:
                _, r = once(max_tokens, streaming=True)
                for line in r.iter_lines(decode_unicode=False):
                    if not line:
                        continue
                    s = line.decode("utf-8", "ignore").strip()
                    if s.startswith("data:"):
                        s = s[5:].strip()
                    elif s.startswith(":"):
                        continue
                    if not s:
                        continue
                    if s == "[DONE]":
                        break
                    try:
                        obj = json.loads(s)
                    except Exception:
                        continue
                    ch = (obj.get("choices") or [{}])[0] or {}
                    if ch.get("finish_reason"):
                        _st["fin"] = ch.get("finish_reason")
                    piece = ((ch.get("delta") or {}).get("content") or "")
                    if piece:
                        _buf.append(piece)
                        _st["n"] += 1
                        _st["last"] = time.time()
                        _emit()
                text = "".join(_buf).strip()
                if text:
                    log.info("AI 流式返回完成：耗时 %.1fs 模型=%s 累计 %d 字 分段=%d finish_reason=%s",
                             time.time() - _t0, self.cfg.get("model", ""), len(text),
                             _st["n"], _st["fin"])
                    if _st["fin"] == "length":
                        log.warning("AI 流式返回被 max_tokens 截断（%d 字）：模型=%s",
                                    len(text), self.cfg.get("model", ""))
                    return text
                log.warning("AI 流式返回空内容，回退非流式请求：模型=%s finish_reason=%s",
                            self.cfg.get("model", ""), _st["fin"])
            except Exception as e:
                _txt = "".join(_buf).strip()
                if _txt:
                    log.warning("AI 流式中断（已收 %d 字，耗时 %.1fs）：%s: %s，按已收内容返回",
                                len(_txt), time.time() - _t0, type(e).__name__, str(e)[:200])
                    return _txt
                log.warning("AI 流式请求失败，回退非流式：%s: %s",
                            type(e).__name__, str(e)[:200])

        data, _ = once(max_tokens)
        choice = (data.get("choices") or [{}])[0] or {}
        msg = choice.get("message") or {}
        text = (msg.get("content") or "").strip()
        # 推理型模型（如 deepseek-flash / reasoner）会把额度耗在 reasoning 上，
        # content 为空且 finish_reason=length，此时放大额度重试一次
        if not text and (choice.get("finish_reason") == "length" or msg.get("reasoning_content")):
            bigger = min(max(max_tokens * 4, 4096), 16000)
            data2 = once(bigger)
            choice2 = (data2.get("choices") or [{}])[0] or {}
            text = ((choice2.get("message") or {}).get("content") or "").strip()
            choice = choice2
        # 正常返回但被 max_tokens 截断（finish_reason=length 且 content 非空）：
        # 点评这类长文会被写到一半就断，这里放大额度重取一次更完整的正文
        if text and choice.get("finish_reason") == "length":
            bigger = min(max(max_tokens * 2, 8192), 16000)
            if bigger > max_tokens:
                try:
                    data3, _ = once(bigger)
                    choice3 = (data3.get("choices") or [{}])[0] or {}
                    text3 = ((choice3.get("message") or {}).get("content") or "").strip()
                    if len(text3) > len(text):
                        text = text3
                        choice = choice3
                except Exception:
                    pass
        if not text:
            log.warning("AI 返回空内容（耗时 %.1fs）：模型=%s finish_reason=%s reasoning_len=%d",
                        time.time() - _t0, self.cfg.get("model", ""), choice.get("finish_reason"),
                        len(str(msg.get("reasoning_content") or "")))
            raise RuntimeError("AI 返回空内容（当前模型疑似推理模型且输出额度不足，可在设置中换用非推理模型或重试）")
        log.info("AI 返回成功：HTTP 200 耗时 %.1fs 模型=%s 输出 %d 字 finish_reason=%s｜前 200 字：%s",
                 time.time() - _t0, self.cfg.get("model", ""), len(text),
                 choice.get("finish_reason"), short(text, 200))
        return text

    def test(self):
        txt = self._chat([{"role": "user", "content": "只回复两个字：可用"}], temperature=0, max_tokens=512)
        return txt.strip()

    # ---------------- 整场点评（三段式） ----------------
    def review(self, question, qtype, ref, transcript_lines, duration, paragraphs_text="",
               stage_names=None, checkpoints=None, faults=None):
        stages_line, checklist = _stage_env(stage_names, checkpoints, faults)
        prompt = REVIEW_TEMPLATE.format(
            question=question or "（未填写题目）",
            qtype=qtype or "未标注",
            stages_line=stages_line,
            ref=ref or "（未提供参考要点，请依据通用结构化面试标准评判）",
            paragraphs=paragraphs_text or "（无段落信息，请按逐字稿自行划分）",
            transcript="\n".join(transcript_lines) or "（无识别内容）",
            duration=int(duration),
            checklist=checklist,
        )
        return self._chat(
            [{"role": "system", "content": SYSTEM_PROMPT},
             {"role": "user", "content": prompt}],
            temperature=self.cfg.get("temperature", 0.3),
            max_tokens=8192,
        )

    # ---------------- 学生作答 -> 答题要点关键词 ----------------
    def points(self, text, qtype="", stages=None, ref="", question=""):
        """把口语化作答压缩成按环节分组的关键词要点（失败抛异常，由调用方回退本地规则）"""
        names = [str(x).strip() for x in (stages or []) if str(x).strip()]
        prompt = POINTS_TEMPLATE.format(
            question=(question or "（未填写题目）").strip()[:600],
            qtype=(qtype or "未识别").strip(),
            stages=" / ".join(names) if names else "（未配置，按 审题 / 提观点 / 重分析 / 抓落实 / 结尾）",
            ref=(ref or "（无参考要点）").strip()[:1200],
        )
        return self._chat(
            [{"role": "system", "content": SYSTEM_PROMPT},
             {"role": "user", "content": prompt + "\n\n【考生作答原文】\n" + (text or "").strip()[:6000]}],
            temperature=0.2,
            max_tokens=1200,
        )

    # ---------------- 逐句极简速评 ----------------
    def judge_points(self, refs, studs, qtype="", stages=None, question="",
                     timeout=None):
        """逐条判定参考要点是否被答到，返回 AI 原始文本（JSON 由调用方解析）"""
        refs = [str(x).strip() for x in (refs or []) if str(x).strip()]
        studs = [str(x).strip() for x in (studs or []) if str(x).strip()]
        if not refs or not studs:
            raise RuntimeError("参考要点或考生要点为空，无法判定")
        ref_lines = "\n".join("R%d. %s" % (i + 1, t) for i, t in enumerate(refs))
        stu_lines = "\n".join("S%d. %s" % (i + 1, t) for i, t in enumerate(studs))
        names = [str(x).strip() for x in (stages or []) if str(x).strip()]
        prompt = POINTS_JUDGE_TEMPLATE.format(
            question=(question or "（未提供）").strip(),
            qtype=(qtype or "未判定").strip(),
            stages=" / ".join(names) if names else "（未提供）",
            refs=ref_lines,
            studs=stu_lines,
        )
        return self._chat(
            [{"role": "system", "content": SYSTEM_PROMPT},
             {"role": "user", "content": prompt}],
            temperature=0, max_tokens=1800, timeout=timeout,
        )

    def quick_sentence(self, question, text):
        prompt = SENTENCE_TEMPLATE.format(
            question=question or "（未填写题目）",
            text=text or "（空）",
        )
        return self._chat(
            [{"role": "system", "content": SYSTEM_PROMPT},
             {"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=800,
        )

    # ---------------- 题目解析（粘贴题目 -> 题型/审题/结构/要点） ----------------
    def analyze_question(self, text, qtype_stages=None, checkpoints=None, faults=None, timeout=None):
        """qtype_stages: {题型名: [环节名, ...]}，来自题型结构配置；为空则用通用五环节"""
        enum_lines = []
        for name, stages in (qtype_stages or {}).items():
            names = _clean_list(stages)
            if names:
                enum_lines.append("%s：%s" % (name, " / ".join(names)))
        stage_enum = "\n".join(enum_lines) if enum_lines else \
            "通用环节（该题型未单独配置时使用）：审题 / 提观点 / 重分析 / 抓落实 / 结尾"
        prompt = QUESTION_TEMPLATE.format(
            question=(text or "").strip()[:2000],
            stage_enum=stage_enum,
        )
        return self._chat(
            [{"role": "system", "content": SYSTEM_PROMPT},
             {"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=3000,
            timeout=timeout,
            json_mode=True,
        )

    # ---------------- 已入库题目的 AI 优化（逐题 / 多题） ----------------
    def optimize_question(self, item, qtype_stages=None, timeout=None, on_delta=None):
        """对一道已入库题目做质量优化，返回 AI 原始 JSON 文本（解析交给调用方）
        on_delta：可选回调，收到 AI 正在返回的文字时被调用（收到的是目前累计的全部文本）"""
        item = item or {}
        enum_lines = []
        for name, stages in (qtype_stages or {}).items():
            names = _clean_list(stages)
            if names:
                enum_lines.append("%s：%s" % (name, " / ".join(names)))
        stage_enum = "\n".join(enum_lines) if enum_lines else \
            "通用环节（该题型未单独配置时使用）：审题 / 提观点 / 重分析 / 抓落实 / 结尾"
        st_lines = []
        for st in (item.get("structure") or []):
            if not isinstance(st, dict):
                continue
            st_lines.append("- %s（%s）：%s" % (st.get("stage") or "", st.get("label") or "",
                                              "；".join(_clean_list(st.get("points"))) or "（空）"))
        prompt = OPTIMIZE_TEMPLATE.format(
            question=(item.get("question") or "").strip()[:2000] or "（题干为空，请依据参考要点还原题干）",
            qtype=(item.get("qtype") or "未标注").strip(),
            exam="；".join(_clean_list(item.get("exam"))) or "（空）",
            structure="\n".join(st_lines) or "（空）",
            ref=(item.get("ref") or "").strip()[:2500] or "（空）",
            stage_enum=stage_enum,
        )
        _q = (item.get("question") or "").strip()
        _t0 = time.time()
        log.info("AI 优化题目开始：题型=%s 题干=%s 超时=%ss 题干字数=%d",
                 (item.get("qtype") or "未标注").strip(), short(_q, 60),
                 timeout if timeout is not None else self.cfg.get("timeout", 120), len(_q))
        try:
            raw = self._chat(
                [{"role": "system", "content": SYSTEM_PROMPT},
                 {"role": "user", "content": prompt}],
                temperature=0.25,
                # 一题一次给足输出预算：4096 常被推理模型的思考过程吃掉，导致 finish_reason=length
                # 触发二次重试，反而变慢；9000 基本一次成文
                max_tokens=9000,
                timeout=timeout,
                json_mode=True,
                stream=bool(on_delta),
                on_delta=on_delta,
            )
        except Exception as e:
            log.warning("AI 优化题目失败（耗时 %.1fs）：%s: %s｜题干=%s",
                        time.time() - _t0, type(e).__name__, str(e)[:300], short(_q, 60))
            raise
        log.info("AI 优化题目返回（耗时 %.1fs）：%d 字｜前 200 字：%s",
                 time.time() - _t0, len(raw or ""), short(raw, 200))
        return raw

    # ---------------- 段落级实时点评 ----------------
    def paragraph_review(self, question, qtype, idx, para_text, t_start, t_end, prev_labels="",
                         stage_names=None, checkpoints=None, faults=None):
        sec = max(1, int(round((t_end or 0) - (t_start or 0))))
        chars = len(re.sub(r"[\s，。！？、；：“”‘’（）《》…—]", "", para_text or ""))
        ts = f"（{_mmss(t_start)} 至 {_mmss(t_end)}）"
        stages_line, checklist = _stage_env(stage_names, checkpoints, faults)
        names = _clean_list(stage_names) or ["开场表态", "重分析-原因", "抓落实", "结尾收束"]
        examples = " / ".join(names[:4])
        prompt = PARAGRAPH_TEMPLATE.format(
            question=question or "（未填写题目）",
            qtype=qtype or "未标注",
            stages_line=stages_line,
            stage_examples=examples,
            idx=idx,
            ts=ts,
            sec=sec,
            chars=chars,
            text=para_text or "（空）",
            prev=prev_labels or "（这是第一段）",
            checklist=checklist,
        )
        return self._chat(
            [{"role": "system", "content": SYSTEM_PROMPT},
             {"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=1600,
        )


def _mmss(sec):
    sec = max(0, int(round(sec or 0)))
    return "%02d:%02d" % (sec // 60, sec % 60)


def parse_paragraph_reply(text):
    """把段落点评回复拆成 {label, body}"""
    t = (text or "").strip()
    label = ""
    m = re.search(r"【段落标签】\s*([^\n【]+)", t)
    if m:
        label = m.group(1).strip().strip("。；")
        label = re.sub(r"^[<\s]+|[>\s]+$", "", label)[:14]
    body = re.sub(r"【段落标签】[^\n]*\n?", "", t).strip()
    return label, body
