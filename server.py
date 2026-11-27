# -*- coding: utf-8 -*-
"""
面试实时听记与点评助手 - 本地服务
* 系统内录 -> 实时逐字稿（按段落计时）-> 段落级即时点评 -> 整场三段式点评 -> 存档
启动：python server.py  ，然后浏览器访问 http://127.0.0.1:7788
"""
import os
import re
import sys
import io
import json
import time
import uuid
import queue
import socket
import base64
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
import traceback
import difflib
import zipfile
from datetime import datetime
from xml.etree import ElementTree as ET

from flask import Flask, request, jsonify, Response, send_from_directory, g
from werkzeug.exceptions import HTTPException
import requests

APP_NAME = "面试点评器"
APP_AUTHOR = "慕夕制作"
APP_VERSION = "2.6.0"
IS_FROZEN = bool(getattr(sys, "frozen", False))

# ---- 项目地址与更新检查（设置 → 关于 → 检查更新）----
PROJECT_URL = "https://github.com/lyckeven/InterviewLiveNote"
GITHUB_REPO = "lyckeven/InterviewLiveNote"
GITHUB_API = "https://api.github.com/repos/" + GITHUB_REPO
UPDATE_TIMEOUT = 8           # 查询 GitHub 的超时秒数，超时即判定检查失败
# 允许用系统浏览器打开的外链域名白名单（只放行 GitHub 项目页与 Release 资源）
OPEN_URL_HOSTS = (
    "github.com",
    "www.github.com",
    "api.github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
)


def _resource_base():
    """打包后资源根目录（PyInstaller _MEIPASS），开发时为源码目录"""
    if IS_FROZEN:
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.abspath(__file__))


def _user_data_dir():
    """用户数据目录：打包后放 %LOCALAPPDATA%\\InterviewReviewer\\data，卸载不丢失。
    IR_DATA_DIR 在所有模式下都优先（开发态与安装态可用同一份设置，避免改了设置页却不生效）"""
    env = os.environ.get("IR_DATA_DIR")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    if IS_FROZEN:
        root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(root, "InterviewReviewer", "data")
    return os.path.join(BASE, "data")


BASE = _resource_base()
sys.path.insert(0, BASE)

from asr_engine import AsrEngine          # noqa: E402
from ai_client import AIClient, parse_paragraph_reply   # noqa: E402
import local_analyzer                     # noqa: E402
import paragraphizer as pz                # noqa: E402
import simplifier as simp                 # noqa: E402
import question_analyzer as qa            # noqa: E402
import corrector                          # noqa: E402
import points_extract as poi              # noqa: E402
import cert_util                          # noqa: E402  (手机端 HTTPS 自签证书)
import log_util                            # noqa: E402  (统一日志：文件+控制台)
import firewall_util                       # noqa: E402  (防火墙一键放行：手机端接入)
from log_util import get_logger            # noqa: E402

# 模块级 logger：所有后端日志写 %LOCALAPPDATA%\InterviewReviewer\logs\app-YYYYMMDD.log
log = get_logger("server")
# 把主线程 / 子线程里没人接的异常也写进日志（打包成 exe 后不装这两个钩子会静默丢异常）
try:
    log_util.install_excepthooks(log)
except Exception:
    pass

DATA = _user_data_dir()
REC_DIR = os.path.join(DATA, "records")
BANK_DIR = os.path.join(DATA, "bank")
for d in (DATA, REC_DIR, BANK_DIR):
    os.makedirs(d, exist_ok=True)

CFG_PATH = os.path.join(DATA, "config.json")
SESSION_PATH = os.path.join(DATA, "session_current.json")


def _seed_user_data():
    """首次运行时把随包内置的配置/题型复制到用户数据目录（不覆盖已有文件）

    题库不随软件内置：不再向新用户复制任何题目数据，首次启动题库为空，
    需用户自行导入或新建；用户手动导入或新建的题库照常保存在 data/bank 下。
    """
    seed = os.path.join(BASE, "default_data")
    if not os.path.isdir(seed):
        return
    import shutil
    skip_top = {"bank"}
    for root, dirs, files in os.walk(seed):
        rel = os.path.relpath(root, seed)
        top = "" if rel in (".", "") else rel.split(os.sep)[0]
        if top in skip_top:
            dirs[:] = []
            continue
        tgt = DATA if rel in (".", "") else os.path.join(DATA, rel)
        os.makedirs(tgt, exist_ok=True)
        for fn in files:
            tp = os.path.join(tgt, fn)
            if not os.path.exists(tp):
                try:
                    shutil.copy2(os.path.join(root, fn), tp)
                except Exception:
                    pass


if IS_FROZEN:
    _seed_user_data()

# 模型目录：优先使用随包内置模型，找不到时回退到开发机绝对路径
_DEV_MODEL_DIR = r"E:\Software\TMSpeech\external-recognizer\sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09"
_DEV_VAD = r"E:\Software\TMSpeech\external-recognizer\silero_vad.onnx"
_bundled_models = os.path.join(BASE, "models")
if os.path.isfile(os.path.join(_bundled_models, "sense-voice", "model.int8.onnx")):
    _sense = os.environ.get("IR_MODEL_DIR") or os.path.join(_bundled_models, "sense-voice")
    _vad = os.environ.get("IR_VAD_MODEL") or os.path.join(_bundled_models, "silero_vad.onnx")
else:
    _sense = os.environ.get("IR_MODEL_DIR") or _DEV_MODEL_DIR
    _vad = os.environ.get("IR_VAD_MODEL") or _DEV_VAD

# 标点恢复模型（CT-Transformer），缺失时引擎自动退回轻量规则
_punct = os.environ.get("IR_PUNCT_MODEL") or os.path.join(_bundled_models, "punct", "model.onnx")
if not os.path.isfile(_punct):
    for _cand in (r"E:\Software\TMSpeech\external-recognizer\punct_int8\model.onnx",
                  r"E:\Software\TMSpeech\external-recognizer\punct\model.onnx"):
        if os.path.isfile(_cand):
            _punct = _cand
            break


def _ascii_ok(p):
    """sherpa-onnx 的 C++ 侧按窄字符串打开文件，含中文等非 ASCII 字符会读不到模型"""
    try:
        os.fsencode(p).decode("ascii")
        return True
    except Exception:
        return False


def _model_file(sense_src):
    """优先 fp32 模型，其次 int8（目录里放哪个用哪个）"""
    for fn in ("model.onnx", "model.int8.onnx"):
        if os.path.isfile(os.path.join(sense_src, fn)):
            return fn
    return "model.int8.onnx"


def _migrate_models(dst_root, sense_src, vad_src, punct_src=""):
    """把模型复制到纯 ASCII 路径下（仅在随包路径含非 ASCII 时触发）"""
    sense_dst = os.path.join(dst_root, "sense-voice")
    vad_dst = os.path.join(dst_root, "silero_vad.onnx")
    punct_dst = os.path.join(dst_root, "punct", "model.onnx")
    stamp = os.path.join(dst_root, ".stamp")
    mf = _model_file(sense_src)
    try:
        key = "|".join(str(os.path.getsize(p)) for p in (
            os.path.join(sense_src, mf),
            os.path.join(sense_src, "tokens.txt"), vad_src))
        key += "|punct:" + (str(os.path.getsize(punct_src)) if punct_src and os.path.isfile(punct_src) else "0")
    except Exception:
        key = ""
    try:
        if os.path.isfile(stamp):
            with open(stamp, "r", encoding="utf-8") as f:
                done = f.read().strip()
            if done == key and os.path.isfile(os.path.join(sense_dst, mf)) \
                    and os.path.isfile(vad_dst) \
                    and (not punct_src or os.path.isfile(punct_dst)):
                return sense_dst, vad_dst, punct_dst
        import shutil
        os.makedirs(sense_dst, exist_ok=True)
        for fn in (mf, "tokens.txt"):
            s = os.path.join(sense_src, fn)
            t = os.path.join(sense_dst, fn)
            if os.path.isfile(s) and (not os.path.isfile(t) or os.path.getsize(t) != os.path.getsize(s)):
                shutil.copy2(s, t)
        if os.path.isfile(vad_src) and (not os.path.isfile(vad_dst)
                                       or os.path.getsize(vad_dst) != os.path.getsize(vad_src)):
            shutil.copy2(vad_src, vad_dst)
        if punct_src and os.path.isfile(punct_src):
            os.makedirs(os.path.dirname(punct_dst), exist_ok=True)
            if not os.path.isfile(punct_dst) or os.path.getsize(punct_dst) != os.path.getsize(punct_src):
                shutil.copy2(punct_src, punct_dst)
        with open(stamp, "w", encoding="utf-8") as f:
            f.write(key)
        return sense_dst, vad_dst, punct_dst
    except Exception:
        return sense_src, vad_src, punct_src


if not (_ascii_ok(_sense) and _ascii_ok(_vad) and (not _punct or _ascii_ok(_punct))):
    print("[MODELS] 模型路径含非 ASCII 字符，迁移到 ASCII 缓存目录", flush=True)
    _mf_name = _model_file(_sense)
    for _cand in (
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "InterviewReviewer", "models"),
        os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "InterviewReviewer", "models"),
        os.path.join(os.environ.get("PUBLIC", r"C:\Users\Public"), "InterviewReviewer", "models"),
    ):
        if not _cand or not _ascii_ok(_cand):
            continue
        _s2, _v2, _p2 = _migrate_models(_cand, _sense, _vad, _punct)
        if os.path.isfile(os.path.join(_s2, _mf_name)) and os.path.isfile(_v2):
            _sense, _vad = _s2, _v2
            if _p2:
                _punct = _p2
            print("[MODELS] 已使用 ASCII 模型路径：" + _s2, flush=True)
            break
PUNCT_MODEL = _punct if (_punct and os.path.isfile(_punct)) else ""
MODEL_DIR, VAD_MODEL = _sense, _vad

app = Flask(__name__, static_folder=os.path.join(BASE, "static"), static_url_path="/static")
app.config["JSON_AS_ASCII"] = False

ai = AIClient(CFG_PATH)
engine = AsrEngine(MODEL_DIR, VAD_MODEL, num_threads=2, punct_model=PUNCT_MODEL)

_lock = threading.Lock()
_clients = []            # 每个 SSE 连接一个 queue.Queue

# 每位学生各自独立的数据字段：读写 session[...] 时自动路由到「当前学生」
_STUDENT_KEYS = ("segments", "paragraphs", "review", "review_mode", "duration", "notes")


def _new_student(name="", idx=1):
    return {
        "id": "st" + uuid.uuid4().hex[:8],
        "name": (name or "").strip() or ("学生%d" % idx),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "segments": [],
        "paragraphs": [],
        "review": "",
        "review_mode": "",
        "duration": 0,
        "acc": 0.0,
        "bench": None,
        "q_analysis": {},
        "question": "",
        "qtype": "",
        "ref": "",
        "notes": [],
    }


class SessionStore(dict):
    """会话存储：全局字段直接读写，学生个人字段按 cur_student 自动路由

    这样原有 30 多处 session["segments"] / session["paragraphs"] 的写法
    无需改动，就自动变成「当前学生的逐字稿 / 段落」。
    """

    def _cur(self):
        sts = dict.get(self, "students")
        if not isinstance(sts, list):
            sts = []
            dict.__setitem__(self, "students", sts)
        cid = dict.get(self, "cur_student")
        cur = next((s for s in sts if s.get("id") == cid), None)
        if cur is None:
            base = _new_student("学生1", 1)
            if sts:
                base = sts[0]
            else:
                sts.append(base)
            dict.__setitem__(self, "cur_student", base["id"])
            cur = base
        return cur

    def __getitem__(self, k):
        if k in _STUDENT_KEYS:
            return self._cur().get(k)
        return dict.__getitem__(self, k)

    def __setitem__(self, k, v):
        if k in _STUDENT_KEYS:
            self._cur()[k] = v
            return
        dict.__setitem__(self, k, v)

    def get(self, k, default=None):
        try:
            v = self[k]
        except KeyError:
            return default
        return default if v is None else v

    def setdefault(self, k, default=None):
        if k in _STUDENT_KEYS:
            cur = self._cur()
            if cur.get(k) is None:
                cur[k] = default
            return cur.get(k)
        if not dict.__contains__(self, k):
            dict.__setitem__(self, k, default)
        return dict.__getitem__(self, k)

    def __contains__(self, k):
        if k in _STUDENT_KEYS:
            return True
        return dict.__contains__(self, k)


session = SessionStore({
    "id": datetime.now().strftime("%Y%m%d_%H%M%S"),
    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "question": "",
    "qtype": "",
    "ref": "",
    "candidate": "",
    "status": "idle",
    "device": "",
    "device_kind": "loopback",
    "device_name": "",
    "source": "",
    "students": [],
    "cur_student": "",
})
session["students"] = [_new_student("学生1", 1)]
session["cur_student"] = session["students"][0]["id"]

_t0 = {"v": None}


# ---------------- 学生（同一场次内可多人作答） ----------------
def _students():
    return dict.get(session, "students") or []


def _cur_student():
    return session._cur()


def _find_student(sid):
    for s in _students():
        if s.get("id") == sid:
            return s
    return None


def _st_elapsed(st):
    acc = float(st.get("acc") or 0)
    b = st.get("bench")
    if b:
        acc += max(0.0, time.time() - float(b))
    return int(acc)


def _st_pause(st):
    if st and st.get("bench"):
        try:
            st["acc"] = float(st.get("acc") or 0) + max(0.0, time.time() - float(st["bench"]))
        except Exception:
            pass
        st["bench"] = None
        st["duration"] = int(float(st.get("acc") or 0))


def _st_resume(st):
    if st and not st.get("bench"):
        st["bench"] = time.time()


def _st_brief(st):
    return {
        "id": st.get("id"),
        "name": st.get("name") or "",
        "segments": len(st.get("segments") or []),
        "paragraphs": len(st.get("paragraphs") or []),
        "duration": _st_elapsed(st) if st.get("bench") else int(st.get("acc") or 0),
        "has_review": bool((st.get("review") or "").strip()),
        "marks": len([s for s in (st.get("segments") or [])
                      if s.get("tags") or s.get("ctags") or s.get("note")]),
        "question": (st.get("question") or "").strip(),
    }


def _students_event():
    return {
        "type": "students",
        "students": [_st_brief(s) for s in _students()],
        "cur": dict.get(session, "cur_student") or "",
    }


def _switch_student(sid, resume_if_running=True):
    """切到指定学生 = 只改变「正在查看谁」

    录音归属由 _REC_SID 在开始录音那一刻锁定，切换视图不得平移计时、
    不得暂停/重启计时：每位学生的时长始终按自己的录音开始绝对时刻计算。
    """
    st = _find_student(sid)
    if not st:
        return False
    session["cur_student"] = st["id"]
    return True


# 本次录音归属的学生 ID（开始录音那一刻锁定，停止时才清空）
_REC_SID = {"v": ""}


def _rec_target():
    """录音中的转写/段落/时长一律落到这里；未录音时退化为当前学生"""
    sid = _REC_SID.get("v") or ""
    st = _find_student(sid) if sid else None
    return st or _cur_student()


def _session_view(st=None):
    """会话快照：全局字段 + 当前学生的逐字稿/段落/点评/时长

    对前端保持 v1.x 的 session 结构（segments / paragraphs / review 直接可取），
    同时附带全部学生的概要，便于前端画学生切换条与对比。
    """
    cur = st or _cur_student()
    out = {}
    for k, v in dict.items(session):
        if k in ("students",):
            continue
        out[k] = v
    for k in _STUDENT_KEYS:
        out[k] = cur.get(k)
    out["student_id"] = cur.get("id") or ""
    out["student_name"] = cur.get("name") or ""
    out["students"] = [_st_brief(s) for s in _students()]
    out["cur_student"] = dict.get(session, "cur_student") or ""
    # 录音归属（开始录音瞬间锁定）与"当前查看谁"可能不是同一个人
    out["rec_student"] = _REC_SID.get("v") or ""
    out["rec_student_name"] = (_find_student(_REC_SID.get("v") or "") or {}).get("name") or ""
    out["rec_running"] = bool(engine.is_running())
    _rsid = _REC_SID.get("v") or ""
    _rbench = 0
    for _x in _students():
        if _x.get("id") == _rsid:
            _rbench = float(_x.get("bench") or 0)
    out["rec_t0"] = _rbench          # 录音起点绝对时间（计时只认它，不受切视图影响）
    out["view_only"] = False
    return out


# ---------------- 段落工具 ----------------
def _para_cfg():
    c = ai.cfg
    return {
        "para_pause_gap": float(c.get("para_pause_gap", 1.5) or 1.5),
        "para_max_sec": float(c.get("para_max_sec", 55) or 55),
        "para_max_chars": int(c.get("para_max_chars", 240) or 240),
    }


def _rebuild_paragraphs(keep_last_open=False):
    """句子被删改后重建段落，尽量沿用原有段落点评"""
    # 段落标题与精简版环节都得按当前题型的环节取值
    _sync_stages()
    old = session.get("paragraphs") or []
    segs = session.get("segments") or []
    if not segs:
        session["paragraphs"] = []
        return
    new = pz.build_from_segments(segs, _para_cfg())
    for p in new:
        pset = set(p.get("seg_ids") or [])
        for o in old:
            oset = set(o.get("seg_ids") or [])
            if pset and pset.issubset(oset):
                if o.get("ai_note"):
                    p["ai_note"] = o["ai_note"]
                if o.get("label"):
                    p["label"] = o["label"]
                    p["title"] = o["label"]
                elif o.get("title"):
                    p["title"] = o["title"]
                # 沿用已有的纠错结果（按句子 id 对齐，句子还在就仍然有效）
                oc = o.get("plain_correct") or {}
                if oc.get("by_sid"):
                    p["plain_correct"] = json.loads(json.dumps(oc, ensure_ascii=False))
                break
    for p in new[:-1]:
        p["closed"] = True
    if new:
        new[-1]["closed"] = not keep_last_open
    session["paragraphs"] = new
    _plain_all(new)


def _find_para(pid):
    for p in session.get("paragraphs") or []:
        if p.get("id") == pid:
            return p
    return None


def _prev_labels(para, n=3):
    out = []
    for p in session.get("paragraphs") or []:
        if p.get("idx", 0) < para.get("idx", 1):
            lab = p.get("label") or p.get("title") or ""
            if lab:
                out.append(lab)
    return "、".join(out[-n:]) if out else ""


# ---------------- 精简版（去冗余） ----------------
def _prev_label_of(para):
    """上一段的标签，供结构环节判定用"""
    prev = ""
    for p in session.get("paragraphs") or []:
        if p.get("idx", 0) < para.get("idx", 1):
            prev = p.get("label") or p.get("title") or ""
    return prev


def _prev_stage_of(para):
    """上一段的结构环节（仅在语义信号缺失/打平时用于次序校正）"""
    prev = ""
    for p in session.get("paragraphs") or []:
        if p.get("idx", 0) < para.get("idx", 1):
            prev = p.get("plain_stage") or ""
    return prev


def _seg_map():
    return {s["id"]: s for s in (session.get("segments") or [])}


def _part_key(p, i):
    """句子片段的唯一键：原始句用 seg_id，拆分生成的新句用 #n 键"""
    k = (p or {}).get("sid")
    if k:
        return str(k)
    return "#n%d" % i


def _edits(para, create=False):
    """句子级手动编辑（拆句 / 合并 / 改字 / 删除 / 改归属）

    统一存在 para["plain_edits"]，优先级高于原始片段与纠错结果：
      items  [{key, seg, text}]  人工编辑后的最终句子清单（一旦出现即以它为准）
      stage  {key: 环节名}       句子级手动归属（优先于逐字稿 seg.stage_manual）
      seq    int                 编辑序号，用于前端判断新鲜度
    """
    if not isinstance(para, dict):
        return {}
    ed = para.get("plain_edits")
    if not isinstance(ed, dict):
        ed = {}
        if create:
            para["plain_edits"] = ed
    if create:
        if not isinstance(ed.get("stage"), dict):
            ed["stage"] = {}
        if "items" in ed and not isinstance(ed.get("items"), list):
            ed.pop("items", None)
        if not isinstance(ed.get("seq"), int):
            ed["seq"] = 0
    return ed


def _parts_final(para):
    """原始精简片段 + 纠错结果 + 句子级手动编辑 → 最终句子片段

    返回 [{sid(唯一键), seg(逐字稿句 id), text, edited}]
    """
    ed = _edits(para)
    items = ed.get("items")
    if isinstance(items, list):
        out = []
        for it in items:
            if not isinstance(it, dict):
                continue
            k = str(it.get("key") or "").strip()
            t = re.sub(r"\s+", "", str(it.get("text") or ""))
            if not k or not t:
                continue
            out.append({"sid": k, "seg": it.get("seg") or None, "text": t, "edited": True})
        return out
    raw = para.get("plain_parts") or []
    fix = ((para.get("plain_correct") or {}).get("by_sid") or {})
    out = []
    for i, p in enumerate(raw):
        if not isinstance(p, dict):
            continue
        seg = p.get("sid")
        t = fix.get(seg) if seg else None
        if t is None:
            t = p.get("text") or ""
        t = re.sub(r"\s+", "", str(t or "")).strip()
        if not t:
            continue
        out.append({"sid": _part_key(p, i), "seg": seg, "text": t, "edited": False})
    return out


def _items_of(para):
    """当前最终句子的可编辑副本（人工编辑后即以编辑结果为准）"""
    return [{"key": p["sid"], "seg": p.get("seg"), "text": p.get("text") or ""}
            for p in _parts_final(para)]


def _items_save(para, items):
    """写回人工编辑结果，并立即刷新段落精简文字与结构环节"""
    ed = _edits(para, create=True)
    ed["items"] = [{"key": str(i.get("key") or ""), "seg": i.get("seg"),
                    "text": re.sub(r"\s+", "", str(i.get("text") or ""))}
                   for i in (items or [])]
    ed["seq"] = int(ed.get("seq") or 0) + 1
    para["plain"] = _para_plain(para)
    _restage(para)
    return para


def _restage(para):
    """人工编辑后按编辑稿重新判定结构环节（段落已手动指定时不动）"""
    if not isinstance(para, dict):
        return para
    if ((para.get("stage_manual") or {}).get("stage")):
        return para
    txt = (para.get("plain") or "").strip()
    if not txt:
        return para
    try:
        paras = session.get("paragraphs") or []
        is_last = bool(paras) and para.get("idx") == max([p.get("idx", 0) for p in paras] or [0])
        stage, sub, why, conf = simp.judge(txt, para.get("label") or "", para.get("idx", 1),
                                           is_last, _prev_stage_of(para))
        if stage:
            para["plain_stage"] = stage
            para["plain_sub"] = sub
            para["plain_label"] = simp.stage_label(stage, sub)
            para["plain_reason"] = why
            para["plain_conf"] = conf
    except Exception:
        traceback.print_exc()
    return para


def _para_plain(para):
    return simp.parts_to_plain(_parts_final(para))


def _correct_local(para):
    """本地纠错（毫秒级）：同音字 / 错别字 / 口误，结果存入 para['plain_correct']"""
    raw = para.get("plain_parts") or []
    if not raw:
        return para
    try:
        out, changes, _src = corrector.correct_parts(None, raw, use_ai=False)
        by_sid = {}
        for p in out:
            if p.get("sid"):
                by_sid[p["sid"]] = p.get("text") or ""
        old = para.get("plain_correct") or {}
        merged = dict(old.get("by_sid") or {})
        merged.update(by_sid)          # 本地结果永远覆盖
        para["plain_correct"] = {
            "state": old.get("state") if old.get("state") == "ai" else "local",
            "by_sid": merged,
            "changes": changes if old.get("state") != "ai" else (old.get("changes") or []),
            "src": "local",
        }
        para["plain"] = _para_plain(para)
    except Exception:
        traceback.print_exc()
    return para


def _resync(para):
    """人工编辑过的段落：plain 与结构环节都以编辑稿为准（覆盖 simp.apply 的结果）"""
    if isinstance(para, dict) and isinstance((_edits(para) or {}).get("items"), list):
        try:
            para["plain"] = _para_plain(para)
            _restage(para)
        except Exception:
            traceback.print_exc()
    return para


def _rich_para(para):
    """给前端回传的段落副本：plain_parts 换成最终句子（含拆句/合并/改字/改归属）"""
    if not isinstance(para, dict):
        return para
    try:
        ev = _plain_event(para)
        cp = dict(para)
        cp["plain_parts"] = ev.get("plain_parts") or []
        cp["plain"] = ev.get("plain") or ""
        cp["plain_stage"] = ev.get("plain_stage") or ""
        cp["plain_label"] = ev.get("plain_label") or ""
        cp["plain_reason"] = ev.get("plain_reason") or ""
        cp["plain_conf"] = ev.get("plain_conf") or ""
        cp["plain_sub"] = ev.get("plain_sub") or ""
        cp["stage_manual"] = ev.get("stage_manual")
        return cp
    except Exception:
        traceback.print_exc()
        return para


def _apply_plain(para):
    """给单段生成/刷新精简版与本段所属结构环节（纯本地规则，毫秒级）"""
    if not isinstance(para, dict):
        return para
    try:
        paras = session.get("paragraphs") or []
        para["_is_last"] = bool(paras) and para.get("idx") == max(
            [p.get("idx", 0) for p in paras] or [0])
        _sync_stages()
        simp.apply(para, prev_label=_prev_label_of(para), seg_map=_seg_map(),
                   prev_stage=_prev_stage_of(para))
        para.pop("_is_last", None)
        _correct_local(para)
        _resync(para)          # 人工编辑过的段落：plain / 环节按编辑稿为准
    except Exception:
        traceback.print_exc()
    return para


def _plain_all(paras=None, seg_map=None):
    """批量刷新精简版（旧存档 / 历史记录兼容）

    seg_map 不传时用当前会话的逐字稿；回看历史记录时必须显式传入该学生自己的
    逐字稿映射，避免把当前实时会话的句子归属套到历史存档上。
    """
    paras = paras if paras is not None else (session.get("paragraphs") or [])
    sm = seg_map if seg_map is not None else _seg_map()
    try:
        _sync_stages()
        simp.apply_all(paras, seg_map=sm)
        for p in paras:
            _correct_local(p)
            _resync(p)          # 人工编辑过的段落：plain / 环节按编辑稿为准
    except Exception:
        traceback.print_exc()
    return paras


def _plain_event(para):
    """精简版单段事件（带整段快照与句子片段，便于前端按句渲染标记）"""
    parts = _parts_final(para)
    seg_map = _seg_map()
    ed = _edits(para)
    kstage = ed.get("stage") if isinstance(ed.get("stage"), dict) else {}
    rich = []
    for p in parts:
        sid = p.get("sid")
        seg_id = p.get("seg") or sid
        seg = seg_map.get(seg_id) or {}
        man = kstage.get(sid) or ((seg.get("stage_manual") or {}).get("stage") if seg else "") or ""
        rich.append({
            "sid": sid,
            "seg": seg_id,
            "text": p.get("text") or "",
            "tags": seg.get("tags") or [],
            "ctags": seg.get("ctags") or [],
            "note": seg.get("note") or "",
            "t_start": seg.get("t_start"),
            "stage_manual": {"stage": man} if man else None,
            "edited": bool(p.get("edited")),
        })
    ev = {
        "type": "plain",
        "pid": para.get("id"),
        "idx": para.get("idx"),
        "edit_seq": int((ed or {}).get("seq") or 0),
        "plain": para.get("plain") or "",
        "plain_stage": para.get("plain_stage") or "",
        "plain_sub": para.get("plain_sub") or "",
        "plain_label": para.get("plain_label") or "",
        "plain_reason": para.get("plain_reason") or "",
        "plain_conf": para.get("plain_conf") or "",
        "stage_manual": para.get("stage_manual") or None,
        "plain_parts": rich,
        "plain_correct": {
            "state": (para.get("plain_correct") or {}).get("state") or "raw",
            "changes": (para.get("plain_correct") or {}).get("changes") or [],
        },
        "closed": bool(para.get("closed")),
    }
    # 前端会用 ev.para 整段替换本地副本，必须给「最终句子」而不是原始片段，
    # 否则拆句 / 合并 / 改字一刷新就被旧的 plain_parts 覆盖回去。
    cp = dict(para)
    cp["plain_parts"] = rich
    cp["plain"] = ev["plain"]
    cp["plain_stage"] = ev["plain_stage"]
    cp["plain_sub"] = ev["plain_sub"]
    cp["plain_label"] = ev["plain_label"]
    cp["plain_reason"] = ev["plain_reason"]
    cp["plain_conf"] = ev["plain_conf"]
    ev["para"] = cp
    return ev


# ---------------- 段落智能纠错（AI，后台线程） ----------------
_correct_lock = threading.Lock()
_correct_inflight = set()


def _ai_correct_para(pid, force=False):
    """对单个段落做 AI 智能纠错（同音字/错别字/口误），失败保留本地结果"""
    para = _find_para(pid)
    if not para:
        return {"ok": False, "msg": "未找到该段落"}
    cfg = ai.public_config()
    if not cfg.get("has_key"):
        return {"ok": False, "msg": "尚未配置 API Key，已保留本地纠错结果"}
    if not cfg.get("enable_correction", True) and not force:
        return {"ok": False, "msg": "设置中已关闭智能纠错"}
    raw = para.get("plain_parts") or []
    if not raw:
        return {"ok": False, "msg": "该段落还没有内容"}
    with _correct_lock:
        if pid in _correct_inflight:
            return {"ok": False, "msg": "该段落正在纠错中"}
        _correct_inflight.add(pid)
    try:
        out, changes, src = corrector.correct_parts(
            ai, raw, session.get("question") or "", use_ai=True)
        if src != "ai":
            return {"ok": False, "msg": "AI 纠错未返回可用结果，已保留本地结果"}
        by_sid = {}
        for p in out:
            if p.get("sid"):
                by_sid[p["sid"]] = p.get("text") or ""
        old = para.get("plain_correct") or {}
        para["plain_correct"] = {
            "state": "ai",
            "by_sid": by_sid,
            "changes": changes,
            "src": "ai",
        }
        para["plain"] = _para_plain(para)
        persist()
        broadcast(_plain_event(para))
        return {"ok": True, "para": para, "changes": changes}
    except Exception as e:
        return {"ok": False, "msg": str(e)[:200]}
    finally:
        with _correct_lock:
            _correct_inflight.discard(pid)


# ---------------- 广播与持久化 ----------------
def broadcast(event: dict):
    dead = []
    for q in _clients:
        try:
            q.put_nowait(event)
        except Exception:
            dead.append(q)
    for q in dead:
        try:
            _clients.remove(q)
        except ValueError:
            pass


def persist():
    try:
        with open(SESSION_PATH, "w", encoding="utf-8") as f:
            json.dump(session, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_session():
    global session
    if os.path.exists(SESSION_PATH):
        try:
            with open(SESSION_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                sts = data.get("students")
                base = {k: v for k, v in data.items()
                        if k not in _STUDENT_KEYS and k != "students"}
                if not isinstance(sts, list) or not sts:
                    # v1.x 老存档：顶层 segments/paragraphs 迁成「学生1」
                    old = _new_student("学生1", 1)
                    old["segments"] = data.get("segments") or []
                    old["paragraphs"] = data.get("paragraphs") or []
                    old["review"] = data.get("review") or ""
                    old["review_mode"] = data.get("review_mode") or ""
                    old["duration"] = int(data.get("duration") or 0)
                    old["acc"] = float(old["duration"])
                    sts = [old]
                for st in sts:
                    st.setdefault("acc", float(st.get("duration") or 0))
                    st.setdefault("bench", None)
                    st.setdefault("notes", [])
                base["students"] = sts
                if not base.get("cur_student"):
                    base["cur_student"] = sts[0]["id"]
                session.clear()
                dict.update(session, base)
                if session.get("status") == "listening":
                    session["status"] = "stopped"
        except Exception:
            traceback.print_exc()
    # 逐学生补齐段落 / 精简版 / 本地纠错
    for st in _students():
        try:
            if not st.get("paragraphs") and st.get("segments"):
                st["paragraphs"] = pz.build_from_segments(st["segments"], _para_cfg())
            if st.get("paragraphs") and simp.pending_count(st["paragraphs"]):
                _sync_stages()
                simp.apply_all(st["paragraphs"],
                               seg_map={s["id"]: s for s in st.get("segments") or []})
            for p in st.get("paragraphs") or []:
                if not (p.get("plain_correct") or {}).get("by_sid"):
                    _correct_local(p)
        except Exception:
            traceback.print_exc()


# ---------------- 引擎回调 ----------------
def _auto_quick(seg):
    """逐句 AI 速评（后台线程，不阻塞识别）"""
    try:
        note = ai.quick_sentence(session["question"], seg["text"]).strip()
        if note:
            seg["ai_note"] = note
            persist()
            broadcast({"type": "ai_note", "id": seg["id"], "ai_note": note})
    except Exception:
        pass


def _auto_para_review(pid):
    """段落讲完后的即时点评（直接调用=同步跑一遍；实时链路走去抖串行队列）"""
    _run_para_review(pid)


# ---------------- 段落点评：误切校准 + 去抖 + 串行队列 ----------------
# 学生停顿时长（换气/思考）会让同一段被切成两段，从而对同一内容重复点评；
# 相邻两段又常常属于同一答题环节。这里统一在「请求 AI 之前」做三道校准：
#   1) dup   ：两段文本几乎一致（同一段被停顿切分）-> 不重复点评，只标注指向上一段；
#   2) merge ：两段环节归属一致且语义相近 / 后段只是换气碎片 -> 标注「与上一段同环节，合并评价」；
#   3) queue ：同一段的点评请求去抖 + 串行，避免并发重复请求同一段。
_RV_LOCK = threading.Lock()
_RV_Q = queue.Queue()
_RV_PENDING = set()
_RV_STATS = {"queued": 0, "coalesced": 0, "done": 0, "skip_dup": 0, "skip_merged": 0,
             "skip_cached": 0}
_RV_DEBOUNCE = 0.7          # 去抖窗口（秒）：同一批收段在窗口内只发一次点评
_RV_WORKER = None
_RV_NOTE_DUP = "与上一段重复（同一段被停顿切成两段），不重复点评"
_RV_NOTE_MERGE = "与上一段同环节，合并评价"


def _rv_norm(t):
    return re.sub(r"[\s，。！？、；：“”‘’（）《》…—,.!?;:]+", "", str(t or ""))


def _rv_sim(a, b):
    a, b = _rv_norm(a), _rv_norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _rv_sig(para):
    """段落点评指纹：同一段文本改过（纠错/合并）才允许重新点评"""
    t = _rv_norm(para.get("text"))
    return "%d:%d:%s" % (len(t), para.get("idx", 0), t[:24])


def _para_prev(para):
    prev = None
    for p in session.get("paragraphs") or []:
        if p.get("id") == para.get("id"):
            continue
        if p.get("idx", 0) < para.get("idx", 1):
            prev = p
    return prev


def _para_stage_of(para):
    return str(para.get("plain_stage") or para.get("label") or para.get("title") or "").strip()


def para_review_mode(para):
    """判断这一段要不要点评：返回 (mode, prev, sim)

    mode: ""（正常点评） / "dup"（同一段被停顿切成两段） / "merge"（与上一段同环节）
    """
    prev = _para_prev(para)
    if not prev:
        return "", None, 0.0
    a, b = prev.get("text") or "", para.get("text") or ""
    sim = _rv_sim(a, b)
    ca, cb = pz.char_count(a), pz.char_count(b)
    # 1) 同一段被停顿切成两段：文本几乎一致，或后段只是前段的换气碎片
    if sim >= 0.9 or (min(ca, cb) <= 10 and sim >= 0.55) or (cb >= 6 and _rv_norm(a).startswith(_rv_norm(b))):
        return "dup", prev, sim
    # 2) 相邻两段环节归属一致：后段是换气碎片或语义相近 -> 合并评价
    sa, sb = _para_stage_of(prev), _para_stage_of(para)
    if sa and sa == sb and (sim >= 0.42 or cb <= 20):
        return "merge", prev, sim
    return "", prev, sim


def _rv_annotate(para, mode, prev):
    """被判定为误切/同环节时，直接写标注并同步到前端（不发 AI 请求）"""
    if mode == "dup":
        para["ai_note"] = _RV_NOTE_DUP
        para["review_mode"] = "dup"
        para["merge_with"] = prev.get("id") if prev else ""
        with _RV_LOCK:
            _RV_STATS["skip_dup"] += 1
    elif mode == "merge":
        para["ai_note"] = _RV_NOTE_MERGE
        para["review_mode"] = "merge"
        para["merge_with"] = prev.get("id") if prev else ""
        with _RV_LOCK:
            _RV_STATS["skip_merged"] += 1
    else:
        return para
    para["ai_sig"] = _rv_sig(para)
    try:
        _apply_plain(para)
    except Exception:
        traceback.print_exc()
    persist()
    broadcast({"type": "paragraph", "para": para})
    broadcast(_plain_event(para))
    return para


def _rv_worker_loop():
    while True:
        pid = _RV_Q.get()
        try:
            time.sleep(_RV_DEBOUNCE)        # 去抖窗口：把同一批收段攒在一起处理
            _run_para_review(pid)
        except Exception:
            traceback.print_exc()
        finally:
            with _RV_LOCK:
                _RV_PENDING.discard(pid)
            _RV_Q.task_done()


def _ensure_rv_worker():
    global _RV_WORKER
    with _RV_LOCK:
        if _RV_WORKER is not None and _RV_WORKER.is_alive():
            return _RV_WORKER
        _RV_WORKER = threading.Thread(target=_rv_worker_loop, name="para-review",
                                      daemon=True)
        _RV_WORKER.start()
        return _RV_WORKER


def enqueue_para_review(pid):
    """把一段的点评请求投进串行队列（同段在队列里只保留一条，重复投递被合并）"""
    if not pid:
        return False
    with _RV_LOCK:
        if pid in _RV_PENDING:
            _RV_STATS["coalesced"] += 1
            return False
        _RV_PENDING.add(pid)
        _RV_STATS["queued"] += 1
    _ensure_rv_worker()
    _RV_Q.put(pid)
    return True


def rv_stats():
    with _RV_LOCK:
        out = dict(_RV_STATS)
        out["pending"] = len(_RV_PENDING)
        out["qsize"] = _RV_Q.qsize()
    return out


def _run_para_review(pid):
    """真正发起一段点评：先做误切/同环节校准，再决定是否请求 AI"""
    para = _find_para(pid)
    if not para or not (para.get("text") or "").strip():
        return None
    # 已经点评过且文本没变 -> 不重复请求
    if para.get("ai_note") and para.get("ai_sig") == _rv_sig(para):
        with _RV_LOCK:
            _RV_STATS["skip_cached"] += 1
        return None
    mode, prev, _sim = para_review_mode(para)
    if mode:
        _rv_annotate(para, mode, prev)
        return None
    try:
        cp, ft = _qtype_review_parts(session.get("qtype"))
        txt = ai.paragraph_review(
            session["question"], session["qtype"], para.get("idx", 1), para.get("text", ""),
            para.get("t_start", 0), para.get("t_end", 0), _prev_labels(para),
            stage_names=_stage_options(), checkpoints=cp, faults=ft)
        label, body = parse_paragraph_reply(txt)
        # AI 请求期间这段可能被后续句子续写 -> 以最新文本指纹落库
        para = _find_para(pid) or para
        if label:
            para["label"] = label
            para["title"] = label
        para["ai_note"] = body or txt.strip()
        para["ai_raw"] = txt.strip()
        para["review_mode"] = "ai"
        para["ai_sig"] = _rv_sig(para)
        _apply_plain(para)                 # 标签更新后同步刷新结构环节
        persist()
        with _RV_LOCK:
            _RV_STATS["done"] += 1
        broadcast({"type": "paragraph", "para": para})
        broadcast(_plain_event(para))
        return para
    except Exception as e:
        broadcast({"type": "para_error", "pid": pid, "msg": str(e)})
        return None


def _on_final(text, t_start, t_end):
    # 归属锁定：写入「这次录音的学生」，与当前正在查看谁完全无关
    tgt = _rec_target()
    seg = {
        "id": uuid.uuid4().hex[:8],
        "idx": len(tgt.get("segments") or []) + 1,
        "t_start": t_start,
        "t_end": t_end,
        "text": text,
        "tags": [],
        "note": "",
        "ai_note": "",
        "pid": "",
        "sid": tgt.get("id") or "",
    }
    closed = []
    with _lock:
        tgt.setdefault("segments", []).append(seg)
        tgt["duration"] = _st_elapsed(tgt)
        paras = tgt.setdefault("paragraphs", [])
        cfg = _para_cfg()
        cur = paras[-1] if paras and not paras[-1].get("closed") else None
        if cur is None or pz.need_split(cur, seg, cfg):
            if cur is not None:
                pz.close_para(cur)
                closed.append(cur["id"])
            cur = pz.new_para(len(paras) + 1, seg.get("t_start") or 0)
            paras.append(cur)
        pz.append_seg(cur, seg)
        seg["pid"] = cur["id"]
        if closed:
            _plain_all(paras)              # 收段后统一重排，结构判定随整场上下文更准
        else:
            _apply_plain(cur)              # 当前段边听边刷新（不落时间戳）
        snap = json.loads(json.dumps(cur, ensure_ascii=False))
        fresh = [json.loads(json.dumps(p, ensure_ascii=False)) for p in paras]
    persist()
    broadcast({"type": "segment", "seg": seg, "duration": tgt["duration"],
               "para": snap, "student": tgt.get("id") or "",
               "rec_student": _REC_SID.get("v") or ""})
    if closed:
        # 收段：把全部段落的精简版与结构环节推给前端（前面的段落可能被重新判定）
        for p in fresh:
            broadcast(_plain_event(p))
        closed_paras = [p for p in fresh if p.get("id") in closed]
    else:
        closed_paras = []
    for p in closed_paras:
        cfg_ai = ai.public_config()
        # 收段后：本地纠错已完成，再排一次 AI 智能纠错（同音字/错别字/口误按上下文还原）
        if cfg_ai.get("enable_correction", True) and cfg_ai.get("has_key"):
            threading.Thread(target=_ai_correct_para, args=(p["id"],), daemon=True).start()
        # 点评投进去抖串行队列：同一段重复投递被合并，避免并发重复请求
        if cfg_ai.get("enable_realtime") and cfg_ai.get("has_key"):
            enqueue_para_review(p["id"])


def _on_status(st):
    session["status"] = st.get("state", session["status"])
    session["device"] = st.get("device", session.get("device", ""))
    if st.get("msg"):
        session["last_error"] = st["msg"]
    persist()
    broadcast({"type": "status", **st})


def _on_partial(text, t_start, t_end, key=0):
    """中间结果：只推前端、不落库（说话过程中每 0.3 秒刷新一次，边听边出字）"""
    broadcast({"type": "partial", "text": text, "t_start": t_start,
               "t_end": t_end, "key": key})


engine.on_final = _on_final
engine.on_status = _on_status
engine.on_partial = _on_partial


# ---------------- 页面 ----------------
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/state")
def api_state():
    # view=<sid>：只读查看某位学生的记录，绝不改动 cur_student 与任何会话状态
    vw = (request.args.get("view") or "").strip()
    view = None
    if vw:
        st = _find_student(vw)
        if st and vw != (dict.get(session, "cur_student") or ""):
            view = _session_view(st)
            view["view_only"] = True
            view["view_student"] = vw
    return jsonify({
        "session": view or _session_view(),
        "students": [_st_brief(s) for s in _students()],
        "cur_student": dict.get(session, "cur_student") or "",
        "config": ai.public_config(),
        "running": engine.is_running(),
        "device": engine.device_name,
        "device_kind": getattr(engine, "device_kind", "loopback"),
        "error": engine.error,
    })


@app.route("/api/stream")
def api_stream():
    def gen():
        q = queue.Queue(maxsize=400)
        _clients.append(q)
        try:
            yield "retry: 3000\n\n"
            while True:
                try:
                    ev = q.get(timeout=15)
                    yield "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
                except queue.Empty:
                    yield ": ping\n\n"
        finally:
            try:
                _clients.remove(q)
            except ValueError:
                pass

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------- 录音控制 ----------------
@app.route("/api/devices")
def api_devices():
    """返回全部输入源：系统内录（loopback）+ 麦克风（mic）

    ?kind=loopback|mic 只取一类；不传则两类都返回（前端一个下拉框统一展示）
    """
    kind = (request.args.get("kind") or "").strip().lower()
    try:
        out = []
        kinds = [kind] if kind in ("loopback", "mic") else ["loopback", "mic"]
        for k in kinds:
            for d in engine.list_devices(k):
                d = dict(d)
                d["kind"] = k
                out.append(d)
        return jsonify({"ok": True, "devices": out,
                        "saved": {"kind": ai.cfg.get("input_kind") or "loopback",
                                  "name": ai.cfg.get("input_device_name") or "",
                                  "index": int(ai.cfg.get("input_device_index", -1))}})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@app.route("/api/level")
def api_level():
    """实时输入电平：供麦克风/内录可用性自检（录音中才有值）"""
    running = engine.is_running()
    lv = getattr(engine, "last_level", None)
    try:
        lv = float(lv) if lv is not None else 0.0
    except Exception:
        lv = 0.0
    if not running:
        lv = 0.0
    return jsonify({"ok": True, "running": running,
                    "kind": getattr(engine, "device_kind", "loopback"),
                    "device": engine.device_name, "level": round(lv, 2),
                    "gain": round(float(getattr(getattr(engine, "_agc", None), "last_gain", 1.0)), 2)})


@app.route("/api/start", methods=["POST"])
def api_start():
    """开始采集：body 可带 kind / device（序号） / name（设备名），缺省用已记忆的设备"""
    body = request.get_json(force=True, silent=True) or {}
    kind = (body.get("kind") or ai.cfg.get("input_kind") or "loopback").strip().lower()
    if kind not in ("loopback", "mic"):
        kind = "loopback"
    dev = body.get("device")
    name = (body.get("name") or "").strip()
    if dev in ("", None) and not name:
        # 未显式指定 -> 用记忆里的设备
        name = (ai.cfg.get("input_device_name") or "").strip()
        try:
            dev = int(ai.cfg.get("input_device_index", -1))
        except Exception:
            dev = -1
    try:
        dev_i = int(dev) if dev not in ("", None) else None
    except Exception:
        dev_i = None
    try:
        res = engine.start(dev_i, kind=kind, device_name=name)
        # 引擎没真正起来（例如手机端 remote 录音进行中）时不要改写会话状态，
        # 否则界面会显示「正在录音」但实际没有音频，这里原样返回引擎结果。
        if not res.get("ok"):
            return jsonify(res)
        _t0["v"] = time.time()
        session["status"] = "listening"
        session["device_kind"] = kind
        session["device_name"] = getattr(engine, "device_name", "") or name
        # 桌面端启动同样记录对外口径的音源，便于手机端状态回显保持一致
        session["source"] = _KIND_TO_SOURCE.get(kind, "")
        # 记住本次选择，下次启动自动沿用
        try:
            ai.save({"input_kind": kind,
                     "input_device_name": getattr(engine, "device_name", "") or name,
                     "input_device_index": dev_i if dev_i is not None else -1})
        except Exception:
            pass
        _st_resume(_cur_student())
        # 归属锁定点：这一刻的学生就是整段录音的唯一归属，之后切视图不改它
        _REC_SID["v"] = (_cur_student() or {}).get("id") or ""
        persist()
        broadcast({"type": "status", "state": "listening",
                   "device": getattr(engine, "device_name", ""), "kind": kind,
                   "rec_student": _REC_SID["v"]})
        return jsonify(res)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "msg": str(e)}), 500


@app.route("/api/stop", methods=["POST"])
def api_stop():
    try:
        engine.stop()
        rec = _rec_target()                      # 这次录音归属的学生（开始录音时锁定）
        rec_sid = (_REC_SID.get("v") or "") or (rec.get("id") or "")
        prev_cur = dict.get(session, "cur_student") or ""
        moved = False
        if rec.get("id") and rec["id"] != prev_cur:
            # 收尾/存档统一作用在录音学生名下，跟此刻正在看谁无关
            dict.__setitem__(session, "cur_student", rec["id"])
            moved = True
        try:
            return _stop_finish(rec, rec_sid)
        finally:
            _REC_SID["v"] = ""
            if moved and prev_cur:
                dict.__setitem__(session, "cur_student", prev_cur)
                persist()
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


def _stop_finish(rec, rec_sid):
    _st_pause(rec)
    _t0["v"] = None
    session["status"] = "stopped"
    # 收尾：关闭这次录音所属学生的最后一段
    with _lock:
        paras = rec.get("paragraphs") or []
        if paras:
            pz.close_para(paras[-1])
            _plain_all(paras)          # 收尾统一刷新精简版与结构环节
    persist()
    saved = ""
    if rec.get("segments"):
        try:
            saved = _save_record()
        except Exception:
            pass
    dur = int(rec.get("duration") or 0)
    broadcast({"type": "status", "state": "stopped", "duration": dur,
               "rec_student": ""})
    if rec.get("paragraphs"):
        broadcast({"type": "paragraph", "para": rec["paragraphs"][-1],
                   "student": rec_sid})
        broadcast(_plain_event(rec["paragraphs"][-1]))
    # 停止后把最后一段（未走过收段流程的）也补一次 AI 纠错
    paras = rec.get("paragraphs") or []
    if paras and ai.public_config().get("has_key"):
        threading.Thread(target=_ai_correct_para, args=(paras[-1]["id"],),
                         daemon=True).start()
    return jsonify({"ok": True, "duration": dur, "saved": saved,
                    "student": rec_sid})


# ---------------- 手机 / 平板网页端（remote 输入源） ----------------
# 链路：手机浏览器采集自身麦克风 -> 重采样为 16kHz 单声道 16bit PCM 定长小分片
#       -> POST /api/mobile/audio -> AsrEngine.push_pcm() -> _audio_q
# 服务端自 VAD 起完全复用原有链路（增量上屏 / 定稿 / 段落切分 / 精简版 / SSE 推送）。
MOBILE_SAMPLE_RATE = 16000
MOBILE_CHUNK_MS = 100                                   # 建议分片时长
MOBILE_CHUNK_BYTES = int(MOBILE_SAMPLE_RATE * MOBILE_CHUNK_MS / 1000) * 2   # 3200
MOBILE_PAGE_PATH = "/mobile"
MOBILE_PAGE_FILE = "mobile.html"         # 手机端页面（static/ 下，桌面 index.html/app.js 不动）
MOBILE_PAGE_READY = True
SCAN_PAGE_PATH = "/scan"                 # 电脑端“扫码入口”独立页（不改桌面 index.html / app.js）
SCAN_PAGE_FILE = "scan.html"

# ---- 手机端三选一声音来源（电脑内录 / 电脑麦克风 / 手机麦克风）----
# 对外统一口径 source，内部映射到引擎 kind：
#   pc_loopback -> loopback（打开本机回环设备，采集系统输出）
#   pc_mic      -> mic     （打开本机麦克风）
#   phone_mic   -> remote  （不打开本机设备，音频由 /api/mobile/audio 推流）
MOBILE_SOURCES = {
    "pc_loopback": {"kind": "loopback", "side": "pc", "label": "电脑内录", "need_push": False},
    "pc_mic": {"kind": "mic", "side": "pc", "label": "电脑麦克风", "need_push": False},
    "phone_mic": {"kind": "remote", "side": "phone", "label": "手机麦克风", "need_push": True},
}
MOBILE_SOURCE_DEFAULT = "phone_mic"      # 不传 source 时沿用改造前的行为（remote 推流）
MOBILE_SOURCE_ALIAS = {
    "pc_loopback": "pc_loopback", "pcloopback": "pc_loopback", "loopback": "pc_loopback",
    "pc_loop": "pc_loopback", "system": "pc_loopback", "speaker": "pc_loopback",
    "pc_in": "pc_loopback", "desktop": "pc_loopback", "desktop_loopback": "pc_loopback",
    "pc_mic": "pc_mic", "pcmic": "pc_mic", "mic": "pc_mic", "microphone": "pc_mic",
    "pc_microphone": "pc_mic", "pc_micro": "pc_mic", "desktop_mic": "pc_mic",
    "phone_mic": "phone_mic", "phonemic": "phone_mic", "phone": "phone_mic",
    "remote": "phone_mic", "mobile": "phone_mic", "mobile_mic": "phone_mic",
}
_KIND_TO_SOURCE = {"loopback": "pc_loopback", "mic": "pc_mic", "remote": "phone_mic"}
_MOB_DEV_CACHE = {"ts": 0.0, "data": None}
_MOB_DEV_TTL = 5.0


def _norm_mobile_source(raw, default=MOBILE_SOURCE_DEFAULT):
    """手机端 source 归一化：兼容 loopback / mic / remote 等别名，非法值回落默认"""
    s = str(raw or "").strip().lower()
    if not s:
        return default
    return MOBILE_SOURCE_ALIAS.get(s, default)


def _mobile_source_info(src):
    src = src if src in MOBILE_SOURCES else MOBILE_SOURCE_DEFAULT
    d = MOBILE_SOURCES[src]
    return {"source": src, "kind": d["kind"], "side": d["side"],
            "label": d["label"], "need_push": bool(d["need_push"])}


def _cur_mobile_source():
    """当前音源：运行中以引擎实际 kind 为准；空闲时保留上次启动记录（从未启动返回空）"""
    if engine.is_running():
        k = str(getattr(engine, "device_kind", "") or "").strip().lower()
        return _KIND_TO_SOURCE.get(k, "")
    s = str(session.get("source") or "").strip().lower()
    return s if s in MOBILE_SOURCES else ""


def _mobile_pc_devices(force=False):
    """本机可用音频设备（loopback / mic），供手机页面选择具体设备；带短缓存避免轮询时反复枚举"""
    now = time.time()
    if not force and _MOB_DEV_CACHE["data"] is not None and (now - _MOB_DEV_CACHE["ts"]) < _MOB_DEV_TTL:
        return _MOB_DEV_CACHE["data"]
    data = {"loopback": [], "mic": []}
    for k in ("loopback", "mic"):
        try:
            for d in (engine.list_devices(k) or []):
                data[k].append({
                    "index": d.get("index"),
                    "name": d.get("name") or "",
                    "rate": int(d.get("rate") or 0),
                    "channels": int(d.get("channels") or 0),
                    "kind": k,
                    "source": _KIND_TO_SOURCE[k],
                })
        except Exception:
            traceback.print_exc()
    _MOB_DEV_CACHE["data"] = data
    _MOB_DEV_CACHE["ts"] = now
    return data


def _mobile_state_payload(with_devices=False):
    """手机端音源/运行状态回显（status、info、start、stop 共用一个口径）"""
    src = _cur_mobile_source()
    if src:
        st = _mobile_source_info(src)
    else:
        st = {"source": "", "kind": str(getattr(engine, "device_kind", "") or ""),
              "side": "", "label": "", "need_push": False}
    out = {
        "running": engine.is_running(),
        "source": st["source"],
        "source_label": st["label"],
        "source_side": st["side"],
        "device_kind": st["kind"],
        "device": getattr(engine, "device_name", "") or "",
        "need_push": bool(st["need_push"]),
        "sources": [{"source": k, "label": v["label"], "side": v["side"],
                     "kind": v["kind"], "need_push": bool(v["need_push"])}
                    for k, v in MOBILE_SOURCES.items()],
    }
    if with_devices:
        out["pc_devices"] = _mobile_pc_devices()
    return out

# ---- 手机端 HTTPS（手机浏览器只在安全上下文才允许开麦）----
# IR_HTTPS=auto（默认，证书签发失败也不会影响 HTTP 使用）/ 1 强制 / 0 关闭
HTTPS_MODE = (os.environ.get("IR_HTTPS") or "auto").strip().lower()
HTTPS_PORT_ENV = (os.environ.get("IR_HTTPS_PORT") or "").strip()
CERT_DIR = os.path.join(DATA, "certs")
_HTTPS = {
    "enabled": False, "port": 0, "cert": "", "key": "", "error": "",
    "created": False, "ips": [], "dns": [], "not_after": "", "days_left": 0,
    "fingerprint": "", "checked": False,
}
_HTTPS_LOCK = threading.Lock()


def https_enabled_by_env():
    return HTTPS_MODE not in ("0", "false", "no", "off", "disable", "disabled")


def ensure_https_cert(force=False):
    """确保证书可用（缺失 / 快过期 / 本机 IP 变化时自动重签），结果写入 _HTTPS"""
    try:
        info = cert_util.ensure_cert(CERT_DIR, force=force)
    except Exception as e:
        traceback.print_exc()
        info = {"ok": False, "error": "证书模块异常：%s" % e}
    _HTTPS.update({
        "checked": True,
        "cert": info.get("cert") or os.path.join(CERT_DIR, cert_util.CERT_NAME),
        "key": info.get("key") or os.path.join(CERT_DIR, cert_util.KEY_NAME),
        "created": bool(info.get("created")),
        "ips": info.get("ips") or [],
        "dns": info.get("dns") or [],
        "not_after": info.get("not_after") or "",
        "days_left": int(info.get("days_left") or 0),
        "fingerprint": info.get("fingerprint") or "",
        "error": "" if info.get("ok") else (info.get("error") or "证书不可用"),
    })
    return info


def _pick_free_port(start, tries=30):
    """从 start 起找一个可绑定的端口（HTTPS 与 HTTP 并存时使用）"""
    for p in range(int(start), int(start) + tries):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        except Exception:
            pass
        try:
            s.bind(("0.0.0.0", p))
            return p
        except OSError:
            continue
        finally:
            s.close()
    return 0


def start_https_server(host, port=0):
    """拉起 HTTPS 监听（与既有 HTTP 端口并存），证书缺失时自动签发

    返回 (werkzeug server 或 None, 实际端口, 错误信息)
    """
    if not https_enabled_by_env():
        _HTTPS["error"] = "已通过 IR_HTTPS=0 关闭 HTTPS（手机端将无法开麦）"
        return None, 0, _HTTPS["error"]
    with _HTTPS_LOCK:
        if _HTTPS.get("enabled") and _HTTPS.get("port"):
            return None, int(_HTTPS["port"]), ""
        info = ensure_https_cert()
        if not info.get("ok"):
            _HTTPS["enabled"] = False
            return None, 0, _HTTPS.get("error") or "证书不可用"
        try:
            want = int(port or HTTPS_PORT_ENV or 0)
        except Exception:
            want = 0
        if not want:
            want = _pick_free_port(8443)
        elif not _pick_free_port(want, 1):
            want = _pick_free_port(want + 1)
        if not want:
            _HTTPS["error"] = "未能找到可用的 HTTPS 端口"
            return None, 0, _HTTPS["error"]
        try:
            from werkzeug.serving import make_server
            srv = make_server(host, want, app, threaded=True,
                              ssl_context=(_HTTPS["cert"], _HTTPS["key"]))
        except Exception as e:
            traceback.print_exc()
            _HTTPS["enabled"] = False
            _HTTPS["error"] = "HTTPS 监听启动失败：%s" % e
            return None, 0, _HTTPS["error"]
        threading.Thread(target=srv.serve_forever, name="https", daemon=True).start()
        _HTTPS.update({"enabled": True, "port": want, "error": ""})
        return srv, want, ""


def https_info():
    """给接口/页面用的 HTTPS 状态快照"""
    ip = _lan_ip()
    port = int(_HTTPS.get("port") or 0)
    page = ("https://%s:%d%s" % (ip, port, MOBILE_PAGE_PATH)) if (ip and port and _HTTPS.get("enabled")) else ""
    return {
        "enabled": bool(_HTTPS.get("enabled")),
        "port": port,
        "mode": HTTPS_MODE,
        "page_url": page,
        "cert": _HTTPS.get("cert") or "",
        "cert_created": bool(_HTTPS.get("created")),
        "cert_ips": _HTTPS.get("ips") or [],
        "cert_dns": _HTTPS.get("dns") or [],
        "cert_not_after": _HTTPS.get("not_after") or "",
        "cert_days_left": int(_HTTPS.get("days_left") or 0),
        "fingerprint": _HTTPS.get("fingerprint") or "",
        "error": _HTTPS.get("error") or "",
        "secure_context_required": True,
    }



def _req_is_secure():
    """当前这次请求自身是否处于安全上下文（HTTPS）；手机端据此决定能否开麦"""
    try:
        return bool(request.is_secure)
    except Exception:
        return False


def _req_port():
    """当前请求实际命中的端口（werkzeug 环境变量优先，回退 Host 头）"""
    try:
        p = int(request.environ.get("SERVER_PORT") or 0)
        if p:
            return p
    except Exception:
        pass
    try:
        return int(str(request.host or "").rsplit(":", 1)[-1])
    except Exception:
        return 0


def _lan_ip():
    """本机局域网 IPv4：优先私网段，跳过回环 / 自动私有地址(169.254) / 虚拟网卡干扰"""
    cands = []

    def _add(ip):
        ip = str(ip or "").strip()
        if not ip or ip.startswith("127.") or ip.startswith("169.254.") or ip in ("0.0.0.0", "::1"):
            return
        if ip not in cands:
            cands.append(ip)

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.3)
        try:
            s.connect(("223.5.5.5", 80))     # UDP 不发包，仅让系统选出站网卡
            _add(s.getsockname()[0])
        finally:
            s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            _add(info[4][0])
    except Exception:
        pass

    def _score(ip):
        if ip.startswith("192.168."):
            return 0
        if ip.startswith("10."):
            return 1
        if ip.startswith("172."):
            try:
                if 16 <= int(ip.split(".")[1]) <= 31:
                    return 2
            except Exception:
                pass
        return 5

    cands.sort(key=_score)
    return cands[0] if cands else ""


# HTTP 监听端口（模块级记录：/api/mobile/info 等接口需要给出「手机可用的 HTTP 地址」，
# 不能用当前请求命中的端口——当前请求可能是 HTTPS 端口，也不能保证 HTTP 端口是 7788）
_HTTP_PORT = {"v": int(os.environ.get("IR_PORT") or 7788)}


def set_http_port(port):
    """由启动方（server.__main__ / launcher.Service.start）登记真实 HTTP 监听端口"""
    try:
        p = int(port or 0)
    except Exception:
        p = 0
    if p > 0:
        _HTTP_PORT["v"] = p


def get_http_port():
    p = int(_HTTP_PORT.get("v") or 0)
    if p <= 0:
        try:
            p = _req_port()
        except Exception:
            p = 0
    return p


def _mobile_endpoints(secure=None):
    """返回 (局域网 base_url, 端口, 手机页面 URL)

    secure=None 时跟随当前请求的协议；secure=True 强制 HTTPS（二维码/引导链接用，
    因为手机必须在安全上下文中才允许开麦）；secure=False 强制 HTTP（此时端口取
    真实 HTTP 监听端口，不能取当前请求端口，否则 HTTPS 请求下会拼出 http://ip:https端口）
    """
    if secure is None:
        try:
            secure = bool(request.is_secure)
        except Exception:
            secure = False
    if secure:
        port = int(_HTTPS.get("port") or 0)
        if not port:
            # 没起 HTTPS 就退回 HTTP，调用方可用 https_info() 判断原因
            return _mobile_endpoints(secure=False)
    else:
        port = get_http_port()
    ip = _lan_ip()
    if not ip:
        return "", port, ""
    scheme = "https" if secure else "http"
    base = "%s://%s:%d" % (scheme, ip, port) if port else "%s://%s" % (scheme, ip)
    return base, port, base + MOBILE_PAGE_PATH


def _mobile_page_url_preferred():
    """手机端最优页面地址：HTTPS 可用则用 HTTPS（可开麦），否则退回 HTTP（仅能看、不能录音）"""
    if _HTTPS.get("enabled") and _HTTPS.get("port") and _lan_ip():
        return _mobile_endpoints(secure=True)[2]
    return _mobile_endpoints(secure=False)[2]



def _png_from_bool_matrix(matrix, scale=8, border=4):
    """把 0/1 矩阵写成 PNG 字节（8 位灰度，只用标准库 zlib + struct）

    不依赖 PIL：打包配置里 PIL 被排除，v2.5.2 的二维码裂图就是
    qrcode → PIL 这条链路在打包后断掉导致的 500。
    """
    import struct
    import zlib

    n = len(matrix)
    if n <= 0:
        raise ValueError("二维码矩阵为空")
    dim = n + border * 2
    white = b"\xff"
    blank = b"\x00" + white * (dim * scale)

    rows = []
    for _ in range(border * scale):
        rows.append(blank)
    for row in matrix:
        vals = bytearray()
        vals += white * border
        for v in row:
            vals += b"\x00" if v else white
        vals += white * border
        line = b"\x00" + b"".join(bytes([b]) * scale for b in vals)   # 行首 filter=0
        for _ in range(scale):
            rows.append(line)
    for _ in range(border * scale):
        rows.append(blank)
    raw = b"".join(rows)

    def _chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", dim * scale, dim * scale, 8, 0, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(raw, 9))
            + _chunk(b"IEND", b""))


def _qr_png_bytes(text):
    """生成二维码 PNG：qrcode 负责编码，内置写入器负责出图（不经过 PIL）"""
    import qrcode
    from qrcode.constants import ERROR_CORRECT_M
    qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M,
                       box_size=1, border=0)
    qr.add_data(text)
    qr.make(fit=True)
    return _png_from_bool_matrix(qr.get_matrix(), scale=8, border=4)


# ---------------- 请求日志（每个接口的方法/路径/状态码/耗时） ----------------
_REQ_T0_ATTR = "_iln_t0"
_SLOW_MS = 3000.0            # 超过 3 秒记 warning，便于一眼找出卡顿接口


@app.before_request
def _log_req_start():
    try:
        setattr(g, _REQ_T0_ATTR, time.time())
    except Exception:
        pass


@app.after_request
def _log_request(resp):
    """记录每次请求：方法、路径、状态码、耗时（毫秒）。
    非 2xx 或耗时 > 3 秒用 warning；/static/ 静态资源不记录，避免刷屏。"""
    try:
        path = request.path or ""
        if path.startswith("/static/"):
            return resp
        t0 = getattr(g, _REQ_T0_ATTR, None)
        cost_ms = (time.time() - t0) * 1000.0 if t0 else 0.0
        code = int(getattr(resp, "status_code", 0) or 0)
        qs = request.query_string.decode("utf-8", "replace") if request.query_string else ""
        line = "%s %s%s -> %s | %.0fms" % (
            request.method, path, ("?" + qs) if qs else "", code, cost_ms)
        if code >= 500:
            log.error(line)
        elif code >= 400 or cost_ms > _SLOW_MS:
            log.warning(line + ("（慢请求）" if cost_ms > _SLOW_MS else ""))
        else:
            log.info(line)
    except Exception:
        pass
    return resp


@app.errorhandler(HTTPException)
def _handle_http_error(e):
    """404 / 405 等标准 HTTP 错误：保持原有响应，只补一条日志（不打 traceback）"""
    try:
        log.warning("HTTP %s %s %s -> %s", request.method, request.path,
                    getattr(e, "description", ""), getattr(e, "code", ""))
    except Exception:
        pass
    try:
        return e.get_response()
    except Exception:
        return jsonify({"ok": False, "msg": getattr(e, "description", "请求失败")}), \
            int(getattr(e, "code", 500) or 500)


@app.errorhandler(Exception)
def _handle_uncaught(e):
    """未捕获异常：完整 traceback 写日志，对外仍返回原来的 JSON 错误结构"""
    try:
        log.error("接口异常 %s %s：%s: %s\n%s", request.method, request.path,
                  type(e).__name__, e, traceback.format_exc())
    except Exception:
        pass
    msg = "%s: %s" % (type(e).__name__, e)
    try:
        return jsonify({"ok": False, "msg": msg[:600]}), 500
    except Exception:
        return Response('{"ok": false, "msg": "%s"}' % msg[:300].replace('"', "'"),
                        status=500, mimetype="application/json")


# ---------------- 防火墙一键放行（手机端连不上时用） ----------------
@app.route("/api/system/firewall")
def api_firewall_status():
    """只读查询：本程序在 Windows 防火墙里的入站放行状态

    level=all 已对全部网络类型放行 / partial 只在部分网络类型放行 / none 未放行
    """
    try:
        st = firewall_util.status()
        st["ok"] = bool(st.get("ok"))
        st["admin"] = firewall_util.is_admin()
        st["supported"] = firewall_util.available()
        return jsonify(st)
    except Exception as e:
        log.error("读取防火墙状态失败：%s\n%s", e, traceback.format_exc())
        return jsonify({"ok": False, "msg": "读取防火墙状态失败：%s" % e,
                        "level": "unknown", "supported": firewall_util.available()}), 500


@app.route("/api/system/firewall/allow", methods=["POST"])
def api_firewall_allow():
    """一键放行：给本程序加一条入站允许规则，域 / 专用 / 公用网络全部适用

    非管理员运行时由系统弹 UAC 授权框，用户点「是」才生效、点「否」则原样取消。
    """
    try:
        r = firewall_util.allow()
        (log.info if r.get("ok") else log.warning)("防火墙放行：%s", r.get("msg"))
        r["status"] = firewall_util.status()
        return jsonify(r)
    except Exception as e:
        log.error("防火墙放行失败：%s\n%s", e, traceback.format_exc())
        return jsonify({"ok": False, "msg": "防火墙放行失败：%s" % e}), 500


@app.route("/api/system/firewall/remove", methods=["POST"])
def api_firewall_remove():
    """撤销放行：只删除本程序自己创建的那条规则，不动其它任何规则"""
    try:
        r = firewall_util.remove()
        (log.info if r.get("ok") else log.warning)("防火墙撤销放行：%s", r.get("msg"))
        r["status"] = firewall_util.status()
        return jsonify(r)
    except Exception as e:
        log.error("防火墙撤销失败：%s\n%s", e, traceback.format_exc())
        return jsonify({"ok": False, "msg": "防火墙撤销失败：%s" % e}), 500


@app.route("/api/system/firewall/help")
def api_firewall_help():
    """手动放行说明（一键放行失败或企业策略锁定时的兜底文案）"""
    prog = firewall_util.program_path()
    return jsonify({
        "ok": True,
        "program": prog,
        "rule_name": firewall_util.RULE_DISPLAY,
        "admin": firewall_util.is_admin(),
        "cmd": 'netsh advfirewall firewall add rule name="%s" dir=in action=allow '
               'program="%s" enable=yes profile=any' % (firewall_util.RULE_DISPLAY, prog),
        "steps": [
            "按 Win 键搜索“高级安全 Windows 防火墙”并打开",
            "左侧点“入站规则”→“新建规则”→选“程序”→下一步",
            "浏览选中：%s" % prog,
            "操作选“允许连接”，配置文件把“域 / 专用 / 公用”三项都勾上",
            "名称填“%s”，完成" % firewall_util.RULE_DISPLAY,
        ],
    })


# ---------------- 日志查询接口（设置面板「错误日志」用） ----------------
@app.route("/api/logs/info")
def api_logs_info():
    """日志目录、当前文件、总大小与文件清单"""
    try:
        st = log_util.log_stats()
        st["ok"] = True
        return jsonify(st)
    except Exception as e:
        log.error("读取日志信息失败：%s\n%s", e, traceback.format_exc())
        return jsonify({"ok": False, "msg": "读取日志信息失败：%s" % e,
                        "dir": log_util.log_dir()}), 500


@app.route("/api/logs/tail")
def api_logs_tail():
    """返回当前日志文件末尾 n 行纯文本（默认 200 行）"""
    try:
        n = int(request.args.get("n", "200") or 200)
    except Exception:
        n = 200
    try:
        text = log_util.tail_text(n)
    except Exception as e:
        text = "(读取日志失败：%s)" % e
    resp = Response(text, mimetype="text/plain")
    resp.headers["Content-Type"] = "text/plain; charset=utf-8"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/logs/open", methods=["POST"])
def api_logs_open():
    """用资源管理器打开日志目录"""
    ok, info = log_util.open_log_dir()
    try:
        log.info("打开日志目录：%s（%s）", info, "成功" if ok else "失败")
    except Exception:
        pass
    if ok:
        return jsonify({"ok": True, "dir": info})
    return jsonify({"ok": False, "msg": "打开日志目录失败：%s" % info, "dir": log_util.log_dir()}), 500


# ---------------- 开源组件声明（第三方许可合规） ----------------
# 清单口径：与 THIRD_PARTY_NOTICES.md 一一对应；版本在运行时用 importlib.metadata 探测，
# 探测不到就显示「未锁定」，绝不写死猜测值。
_OSS_COMPONENTS = [
    ("Flask", "flask", "BSD-3-Clause", "本机 HTTP 服务（仅监听本机 / 局域网手机接入）", "https://github.com/pallets/flask"),
    ("Werkzeug", "werkzeug", "BSD-3-Clause", "Flask 的 WSGI 实现与静态资源服务", "https://github.com/pallets/werkzeug"),
    ("Jinja2", "jinja2", "BSD-3-Clause", "Flask 模板渲染引擎", "https://github.com/pallets/jinja"),
    ("MarkupSafe", "markupsafe", "BSD-3-Clause", "Jinja2 的 HTML 转义依赖", "https://github.com/pallets/markupsafe"),
    ("click", "click", "BSD-3-Clause", "Flask 命令行入口依赖", "https://github.com/pallets/click"),
    ("itsdangerous", "itsdangerous", "BSD-3-Clause", "Flask 会话签名依赖", "https://github.com/pallets/itsdangerous"),
    ("requests", "requests", "Apache-2.0", "调用 AI 服务接口、查询账户余额", "https://github.com/psf/requests"),
    ("urllib3", "urllib3", "MIT", "requests 的 HTTP 连接池实现", "https://github.com/urllib3/urllib3"),
    ("certifi", "certifi", "MPL-2.0", "requests 使用的 CA 根证书包", "https://github.com/certifi/python-certifi"),
    ("charset-normalizer", "charset-normalizer", "MIT", "requests 的响应编码识别", "https://github.com/jawah/charset_normalizer"),
    ("idna", "idna", "BSD-3-Clause", "requests 的国际化域名支持", "https://github.com/kjd/idna"),
    ("numpy", "numpy", "BSD-3-Clause", "音频数据缓冲与重采样（含 0BSD / Zlib / MIT / CC0 片段）", "https://numpy.org"),
    ("scipy", "scipy", "BSD-3-Clause", "高质量音频重采样 resample_poly", "https://scipy.org"),
    ("sherpa-onnx", "sherpa-onnx", "Apache-2.0", "本地离线语音识别引擎（ASR / VAD / 标点）", "https://github.com/k2-fsa/sherpa-onnx"),
    ("PyAudioWPatch", "PyAudioWPatch", "MIT", "WASAPI 环回录音（抓取扬声器声音），其内含 PortAudio 为 MIT", "https://github.com/s0d3s/PyAudioWPatch"),
    ("cryptography", "cryptography", "Apache-2.0 OR BSD-3-Clause", "生成手机接入用的本地自签 HTTPS 证书", "https://github.com/pyca/cryptography"),
    ("qrcode", "qrcode", "BSD", "生成手机接入二维码", "https://github.com/lincolnloop/python-qrcode"),
    ("python-docx", "python-docx", "MIT", "导出 Word 格式的点评稿 / 备课稿", "https://github.com/python-openxml/python-docx"),
    ("lxml", "lxml", "BSD-3-Clause", "python-docx 的 XML 处理依赖", "https://lxml.de"),
    ("PyMuPDF (MuPDF)", "PyMuPDF", "AGPL-3.0", "导入 PDF 题目时提取文字层（注意：AGPL，分发须履行源码提供义务）", "https://github.com/pymupdf/PyMuPDF"),
    ("pywebview", "pywebview", "BSD-3-Clause", "桌面窗口外壳（内嵌 WebView2 显示界面）", "https://github.com/r0x0r/pywebview"),
    ("pythonnet", "pythonnet", "MIT", "pywebview 在 Windows 上调用 .NET / WebView2", "https://github.com/pythonnet/pythonnet"),
    ("clr-loader", "clr-loader", "MIT", "pythonnet 的 .NET 运行时加载器", "https://github.com/pythonnet/clr-loader"),
    ("PyYAML", "PyYAML", "MIT", "随包收集的传递依赖（配置读取）", "https://github.com/yaml/pyyaml"),
    ("psutil", "psutil", "BSD-3-Clause", "随包收集的传递依赖（进程 / 系统信息）", "https://github.com/giampaolo/psutil"),
    ("Pillow", "Pillow", "MIT-CMU", "qrcode 生成 PNG 时的图像后端", "https://python-pillow.org"),
]

_OSS_LICENSE_FILES = {
    "Apache-2.0": "Apache-2.0.txt",
    "BSD-3-Clause": "BSD-3-Clause.txt",
    "MIT": "MIT.txt",
    "MPL-2.0": "MPL-2.0.txt",
    "AGPL-3.0": "AGPL-3.0.txt",
    "0BSD": "0BSD.txt",
    "Zlib": "Zlib.txt",
}


def _oss_dir():
    """开源声明文件所在目录：源码运行取项目根，打包后取 _MEIPASS 根"""
    return BASE


def _oss_version(pkg):
    try:
        from importlib import metadata as _md
        return _md.version(pkg)
    except Exception:
        return ""


@app.route("/api/oss")
def api_oss():
    """设置面板「开源许可」用：返回第三方组件清单与许可证全文入口"""
    items = []
    for name, pkg, lic, use, url in _OSS_COMPONENTS:
        items.append({
            "name": name,
            "ver": _oss_version(pkg) or "未锁定",
            "license": lic,
            "use": use,
            "url": url,
        })
    d = _oss_dir()
    notices = os.path.join(d, "THIRD_PARTY_NOTICES.md")
    ldir = os.path.join(d, "LICENSES")
    return jsonify({
        "ok": True,
        "app": "面试点评器 InterviewLiveNote",
        "version": APP_VERSION,
        "author": "慕夕制作",
        "items": items,
        "notices_path": notices if os.path.exists(notices) else "",
        "licenses_dir": ldir if os.path.isdir(ldir) else "",
        "licenses": [{"spdx": k, "file": v} for k, v in sorted(_OSS_LICENSE_FILES.items())],
    })


@app.route("/api/oss/license/<spdx>")
def api_oss_license(spdx):
    """返回某个许可证的全文（只允许白名单内的 SPDX 标识，防路径穿越）"""
    fn = _OSS_LICENSE_FILES.get(spdx or "")
    if not fn:
        return jsonify({"ok": False, "msg": "未知许可证：%s" % spdx}), 404
    path = os.path.join(_oss_dir(), "LICENSES", fn)
    if not os.path.isfile(path):
        return jsonify({"ok": False, "msg": "许可证文件缺失：%s" % fn}), 404
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception as e:
        return jsonify({"ok": False, "msg": "读取许可证失败：%s" % e}), 500
    return jsonify({"ok": True, "spdx": spdx, "file": fn, "text": text})


@app.route("/api/oss/open", methods=["POST"])
def api_oss_open():
    """用资源管理器打开开源声明所在目录（用户查看 THIRD_PARTY_NOTICES.md 与 LICENSES）"""
    d = _oss_dir()
    try:
        if os.name == "nt":
            os.startfile(d)  # noqa: S606  (仅打开本地已存在的程序目录)
        else:
            return jsonify({"ok": False, "msg": "当前平台不支持自动打开目录", "dir": d}), 400
    except Exception as e:
        return jsonify({"ok": False, "msg": "打开目录失败：%s" % e, "dir": d}), 500
    return jsonify({"ok": True, "dir": d})


@app.route("/api/log/client", methods=["POST"])
def api_log_client():
    """接收前端上报的脚本错误（window.onerror / unhandledrejection）"""
    body = request.get_json(force=True, silent=True) or {}
    kind = str(body.get("kind") or "js")[:40]
    msg = str(body.get("message") or "")[:2000]
    src = str(body.get("source") or body.get("file") or "")[:500]
    line = body.get("line") or body.get("lineno") or ""
    col = body.get("col") or body.get("colno") or ""
    stack = str(body.get("stack") or "")[:4000]
    ua = str(body.get("ua") or request.headers.get("User-Agent") or "")[:300]
    page = str(body.get("url") or request.path)[:500]
    try:
        log.warning("前端脚本错误[%s] %s | %s:%s:%s | 页面=%s | UA=%s%s",
                    kind, msg or "(空消息)", src or "-", line or "-", col or "-",
                    page, ua, ("\n" + stack) if stack else "")
    except Exception:
        pass
    return jsonify({"ok": True})


@app.after_request
def _mobile_cors(resp):
    """手机端接口放行跨域（页面由本服务提供时即同源，此处仅为兜底）"""
    try:
        path = request.path or ""
    except Exception:
        path = ""
    if path.startswith("/api/mobile"):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/mobile/info")
def api_mobile_info():
    """手机端接入信息：局域网地址、二维码地址、音频协议参数、HTTPS 状态"""
    base, port, page = _mobile_endpoints(secure=False)
    hi = https_info()
    target = hi["page_url"] or page
    return jsonify({
        "ok": True,
        "lan_ip": _lan_ip(),
        "port": port,
        "base_url": base,
        "page_url": target,
        "http_page_url": page,
        "page_ready": bool(MOBILE_PAGE_READY),
        "qr_url": ("%s/api/mobile/qrcode.png" % (base or "")) if base else "",
        "qr_target": target,
        "secure_context": _req_is_secure(),
        "https_available": bool(hi["enabled"]),
        "https": hi,
        "sample_rate": MOBILE_SAMPLE_RATE,
        "chunk_ms": MOBILE_CHUNK_MS,
        "chunk_bytes": MOBILE_CHUNK_BYTES,
        "format": "s16le",
        "channels": 1,
        "endpoints": {
            "start": "/api/mobile/session/start",
            "audio": "/api/mobile/audio",
            "stop": "/api/mobile/session/stop",
            "status": "/api/mobile/status",
        },
        # 音源三选一：source / source_label / source_side / device_kind / device /
        # need_push / sources / pc_devices（本机可用设备清单）
        **_mobile_state_payload(with_devices=True),
        "remote": engine.remote_info(),
        "desktop_running": bool(engine.is_running() and getattr(engine, "device_kind", "") != "remote"),
    })


def _qr_target_for(scheme=""):
    """按 scheme 决定二维码指向的地址

    * scheme=http  强制普通地址（微信内置浏览器能直接打开，但不能开麦）
    * scheme=https 强制安全地址（可开麦，自签证书需点「继续访问」）
    * 空/auto     优先 HTTPS（可开麦），不可用时退回 HTTP
    """
    s = (scheme or "").strip().lower()
    if s == "http":
        return _mobile_endpoints(secure=False)[2]
    if s == "https":
        return _mobile_endpoints(secure=True)[2]
    return _mobile_page_url_preferred()


@app.route("/api/mobile/qrcode.png")
def api_mobile_qrcode():
    """二维码图片（PNG）

    ?scheme=http|https 指定地址类型，?url= 可直接覆盖目标；
    默认优先 HTTPS（可开麦），HTTPS 不可用时退回 HTTP。
    """
    target = (request.args.get("url") or "").strip() or _qr_target_for(request.args.get("scheme"))
    if not target:
        return jsonify({"ok": False, "msg": "未能识别本机局域网地址，请确认已连接 Wi-Fi / 有线网络"}), 500
    try:
        png = _qr_png_bytes(target)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "msg": "二维码生成失败：%s（需安装 qrcode：pip install qrcode）" % e}), 500
    resp = Response(png, mimetype="image/png")
    resp.headers["X-QR-Target"] = target
    resp.headers["X-QR-Secure"] = "1" if target.startswith("https://") else "0"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/mobile/qrcode")
def api_mobile_qrcode_json():
    """二维码 JSON（base64 data URL），便于桌面端直接内嵌展示"""
    target = (request.args.get("url") or "").strip() or _qr_target_for(request.args.get("scheme"))
    if not target:
        return jsonify({"ok": False, "msg": "未能识别本机局域网地址"}), 500
    try:
        png = _qr_png_bytes(target)
    except Exception as e:
        return jsonify({"ok": False, "msg": "二维码生成失败：%s" % e}), 500
    return jsonify({"ok": True, "url": target,
                    "secure": target.startswith("https://"),
                    "http_page_url": _mobile_endpoints(secure=False)[2],
                    "https": https_info(),
                    "data_url": "data:image/png;base64," + base64.b64encode(png).decode("ascii")})


@app.route("/api/mobile/https")
def api_mobile_https():
    """HTTPS 状态与证书信息（缺证书可现场补签）"""
    if (request.args.get("renew") or "").strip() in ("1", "true", "yes"):
        ensure_https_cert(force=True)
    hi = https_info()
    hi["page_http_url"] = _mobile_endpoints(secure=False)[2]
    return jsonify({"ok": True, "https": hi, "lan_ip": _lan_ip(), "cert_dir": CERT_DIR})


@app.route("/api/mobile/clip")
def api_mobile_clip():
    """手机端下拉用的剪辑信息：当前学生、题面、题型、是否有逐字稿、录音状态"""
    rec = _rec_target() or {}
    segs = rec.get("segments") or []
    paras = rec.get("paragraphs") or []
    return jsonify({
        "ok": True,
        "running": engine.is_running(),
        "kind": getattr(engine, "device_kind", ""),
        "source": _cur_mobile_source(),
        "student": rec.get("name") or "",
        "student_id": rec.get("id") or "",
        "question": session.get("question") or "",
        "qtype": session.get("qtype") or "",
        "segments": len(segs),
        "paragraphs": len(paras),
        "duration": int(rec.get("duration") or 0),
        "has_review": bool((rec.get("review") or "").strip()),
        "review_mode": rec.get("review_mode") or "",
        "https": https_info(),
    })



@app.route("/api/mobile/session/start", methods=["POST", "OPTIONS"])
def api_mobile_start():
    """开始手机端录音会话：source 三选一

    body: {"source": "pc_loopback" | "pc_mic" | "phone_mic",
           "device": 设备序号(可选，仅 pc_* 有效), "name": 设备名,
           "student": 学生 id(可选)}
    - pc_loopback：按 loopback 打开本机回环设备（电脑内录）
    - pc_mic     ：按 mic 打开本机麦克风（电脑麦克风）
    - phone_mic  ：引擎走 remote，不打开本机设备，音频由 /api/mobile/audio 推流
    不传 source 时默认 phone_mic（与改造前完全一致，向后兼容）。
    """
    if request.method == "OPTIONS":
        return ("", 204)
    body = request.get_json(force=True, silent=True) or {}
    src = _norm_mobile_source(body.get("source") or body.get("input") or body.get("mode"))
    st = _mobile_source_info(src)
    kind = st["kind"]
    if engine.is_running():
        cur = _cur_mobile_source() or _KIND_TO_SOURCE.get(
            str(getattr(engine, "device_kind", "") or "").strip().lower(), "")
        if cur == src:
            d = _mobile_state_payload()
            d.update({"ok": True, "msg": "会话已在运行（%s）" % st["label"],
                      "sample_rate": MOBILE_SAMPLE_RATE, "chunk_ms": MOBILE_CHUNK_MS,
                      "chunk_bytes": MOBILE_CHUNK_BYTES, "format": "s16le", "channels": 1,
                      "student": _REC_SID.get("v") or ""})
            return jsonify(d)
        cur_label = (_mobile_source_info(cur)["label"] if cur
                     else (getattr(engine, "device_kind", "") or "本机"))
        return jsonify({"ok": False, "code": "busy",
                        "msg": "正在录音（%s），请先停止后再切换音源" % cur_label,
                        "source": cur, "running": True}), 409
    # 解析要打开的音频设备（仅 pc_loopback / pc_mic 需要）
    name = (body.get("name") or "").strip()
    dev = body.get("device", body.get("device_index"))
    dev_i = None
    if kind == "remote":
        name = name or "手机/平板网页端"
        dev = ""
    else:
        if dev in (None, "") and not name:
            # 未指定设备：桌面端记忆的输入类型与本次一致时沿用记忆里的设备
            try:
                mk = str(ai.cfg.get("input_kind") or "loopback").strip().lower()
                if mk == kind:
                    name = (ai.cfg.get("input_device_name") or "").strip()
                    dev = ai.cfg.get("input_device_index", -1)
            except Exception:
                pass
        try:
            dev_i = int(dev) if dev not in (None, "", -1, "-1") else None
        except Exception:
            dev_i = None
    try:
        res = engine.start(dev_i, kind=kind, device_name=name)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "code": "device_error", "source": src,
                        "msg": "打开%s失败：%s" % (st["label"], e)}), 500
    if not res.get("ok"):
        return jsonify({"ok": False, "code": "start_failed", "source": src,
                        "msg": res.get("msg") or "启动失败"}), 409
    sid = (body.get("student") or "").strip()
    if sid:
        with _lock:
            if _find_student(sid):
                _switch_student(sid)
    _t0["v"] = time.time()
    session["status"] = "listening"
    session["device_kind"] = kind
    session["device_name"] = getattr(engine, "device_name", "") or name
    session["source"] = src
    _st_resume(_cur_student())
    _REC_SID["v"] = (_cur_student() or {}).get("id") or ""
    persist()
    broadcast({"type": "status", "state": "listening",
               "device": getattr(engine, "device_name", ""), "kind": kind,
               "source": src, "side": st["side"],
               "rec_student": _REC_SID["v"]})
    base, port, page = _mobile_endpoints()
    d = _mobile_state_payload()
    d.update({"ok": True, "msg": "已开始（%s）" % st["label"],
              "sample_rate": MOBILE_SAMPLE_RATE, "chunk_ms": MOBILE_CHUNK_MS,
              "chunk_bytes": MOBILE_CHUNK_BYTES, "format": "s16le", "channels": 1,
              "student": _REC_SID["v"], "base_url": base, "page_url": page})
    if kind != "remote":
        # 明确回显实际使用的设备；若手机端指定的设备不可用（引擎按名称/序号兜底），显式标注回退
        req_name = (name or "").strip()
        actual = str(d.get("device") or "")
        fell_back = False
        try:
            idx = None
            for k in ("loopback", "mic"):
                for it in (_mobile_pc_devices().get(k) or []):
                    if str(it.get("name") or "") == actual:
                        idx = it.get("index")
            d["device_index"] = idx
            if req_name and actual and req_name.lower() != actual.lower():
                fell_back = True
        except Exception:
            pass
        if fell_back:
            d["requested_device"] = {"index": dev_i, "name": req_name}
            d["device_fallback"] = True
            d["msg"] += "；原选设备不可用，已回退到「%s」" % actual
    return jsonify(d)


@app.route("/api/mobile/audio", methods=["POST", "OPTIONS"])
def api_mobile_audio():
    """推送音频分片：请求体为原始 PCM 字节（默认 16kHz / 单声道 / s16le）

    ?rate=16000&channels=1&fmt=s16le|u8&agc=1
    也支持 multipart/form-data 的 file / audio 字段（兜底，便于调试）。
    """
    if request.method == "OPTIONS":
        return ("", 204)
    if not engine.is_running():
        return jsonify({"ok": False, "msg": "会话未开始", "code": "no_session"}), 409
    _src = _cur_mobile_source()
    if _src != "phone_mic":
        _lbl = _mobile_source_info(_src)["label"] if _src else (getattr(engine, "device_kind", "") or "本机")
        return jsonify({"ok": False,
                        "msg": "当前音源为「%s」，不接受手机推流；请切换到手机麦克风后再推流" % _lbl,
                        "code": "source_not_phone_mic", "source": _src,
                        "device_kind": getattr(engine, "device_kind", "")}), 409
    f = request.files.get("file") or request.files.get("audio")
    if f is not None:
        data = f.read()
    else:
        data = request.get_data(cache=False) or b""
    if not data:
        return jsonify({"ok": False, "msg": "空分片"}), 400
    try:
        rate = int(request.args.get("rate") or MOBILE_SAMPLE_RATE)
    except Exception:
        rate = MOBILE_SAMPLE_RATE
    try:
        channels = int(request.args.get("channels") or 1)
    except Exception:
        channels = 1
    fmt = (request.args.get("fmt") or "s16le").strip().lower()
    sw = 1 if fmt in ("u8", "s8", "8bit") else 2
    agc = (request.args.get("agc") or "1").strip().lower() not in ("0", "false", "no")
    res = engine.push_pcm(data, src_rate=rate, channels=channels,
                          sample_width=sw, agc=agc)
    if not res.get("ok"):
        return jsonify(res), 409
    return jsonify(res)


@app.route("/api/mobile/session/stop", methods=["POST", "OPTIONS"])
def api_mobile_stop():
    """结束手机端会话：三种音源（电脑内录 / 电脑麦克风 / 手机麦克风）都可停止

    复用桌面端停止逻辑（收尾段落 / 存盘 / 精简版），并在返回体里附带被停止的音源。
    """
    if request.method == "OPTIONS":
        return ("", 204)
    src = _cur_mobile_source()
    st = _mobile_source_info(src) if src else {"label": "", "side": "", "kind": ""}
    if not engine.is_running():
        return jsonify({"ok": True, "msg": "当前没有在录音", "duration": 0,
                        "source": src, "source_label": st["label"],
                        "source_side": st["side"], "running": False})
    r = api_stop()
    resp, code = (r[0], r[1]) if isinstance(r, tuple) else (r, 200)
    try:
        data = resp.get_json(silent=True)
        if isinstance(data, dict):
            data.update({"source": src, "source_label": st["label"],
                         "source_side": st["side"], "device_kind": st["kind"],
                         "running": engine.is_running()})
            resp.set_data(json.dumps(data, ensure_ascii=False))
            resp.mimetype = "application/json"
    except Exception:
        traceback.print_exc()
    return resp, code


@app.route("/api/mobile/status")
def api_mobile_status():
    """手机端轻量状态：录音归属、句数/段数/时长、最新定稿文本"""
    rec = _rec_target() or {}
    segs = rec.get("segments") or []
    paras = rec.get("paragraphs") or []
    return jsonify({
        "ok": True,
        # 音源三选一状态：source / source_label / source_side / device_kind / device / need_push
        **_mobile_state_payload(),
        "kind": getattr(engine, "device_kind", ""),
        # 实时输入电平（供手机页面画电平条 / 自检收音是否正常）
        "level": (round(float(getattr(engine, "last_level", 0) or 0), 2)
                  if engine.is_running() else 0.0),
        "student": rec.get("name") or "",
        "student_id": rec.get("id") or "",
        "segments": len(segs),
        "paragraphs": len(paras),
        "duration": int(rec.get("duration") or 0),
        "last_text": (segs[-1].get("text") if segs else "") or "",
        "remote": engine.remote_info(),
        "error": engine.error,
    })


@app.route(MOBILE_PAGE_PATH)
def api_mobile_page():
    """手机 / 平板网页端页面（static/mobile.html，独立于桌面 index.html / app.js）"""
    path = os.path.join(app.static_folder, MOBILE_PAGE_FILE)
    if not os.path.isfile(path):
        base, port, page = _mobile_endpoints(secure=False)
        html = (
            "<!doctype html><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>面试点评器 · 手机端</title>"
            "<body style='font-family:system-ui;padding:24px;line-height:1.7'>"
            "<h2>手机端页面文件缺失</h2><p>未找到 %s（局域网地址：%s）。</p></body>"
        ) % (path, base or "未识别到局域网地址")
        return Response(html, mimetype="text/html; charset=utf-8", status=500)
    resp = send_from_directory(app.static_folder, MOBILE_PAGE_FILE)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route(SCAN_PAGE_PATH)
def api_scan_page():
    """电脑端扫码入口页（独立页，桌面 index.html / app.js 未改动）：大二维码 + 可用地址 + 证书状态"""
    path = os.path.join(app.static_folder, SCAN_PAGE_FILE)
    if not os.path.isfile(path):
        return Response(
            "<!doctype html><meta charset='utf-8'><p>未找到 %s</p>" % path,
            mimetype="text/html; charset=utf-8", status=500,
        )
    resp = send_from_directory(app.static_folder, SCAN_PAGE_FILE)
    resp.headers["Cache-Control"] = "no-store"
    return resp



@app.route("/api/session/new", methods=["POST"])
def api_new_session():
    if engine.is_running():
        return jsonify({"ok": False, "msg": "请先停止录音再新建"}), 400
    body = request.get_json(force=True, silent=True) or {}
    first = _new_student(body.get("candidate") or "学生1", 1)
    _REC_SID["v"] = ""          # 新会话，清掉上一场遗留的录音归属
    dict.update(session, {
        "id": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "question": body.get("question", ""),
        "qtype": body.get("qtype", ""),
        "ref": body.get("ref", ""),
        "candidate": body.get("candidate", ""),
        "status": "idle",
        "q_analysis": body.get("q_analysis", {}) or {},
        "students": [first],
        "cur_student": first["id"],
    })
    persist()
    broadcast({"type": "reset", "session": _session_view()})
    broadcast(_students_event())
    return jsonify({"ok": True, "session": _session_view(),
                    "students": [_st_brief(s) for s in _students()]})


# ---------------- 学生（一场多人） ----------------
@app.route("/api/students")
def api_students():
    return jsonify({"ok": True, "students": [_st_brief(s) for s in _students()],
                    "cur": dict.get(session, "cur_student") or ""})


@app.route("/api/students/add", methods=["POST"])
def api_students_add():
    """在同一场次内新增一位学生并立即切过去（录音中也可以）"""
    body = request.get_json(force=True, silent=True) or {}
    sts = _students()
    if len(sts) >= 20:
        return jsonify({"ok": False, "msg": "同一场次最多 20 位学生"}), 400
    st = _new_student(body.get("name") or "", len(sts) + 1)
    with _lock:
        sts.append(st)
        _switch_student(st["id"])
    persist()
    broadcast(_students_event())
    broadcast({"type": "switch_student", "student": dict(st), "cur": st["id"]})
    return jsonify({"ok": True, "student": _st_brief(st), "cur": st["id"]})


@app.route("/api/students/switch", methods=["POST"])
def api_students_switch():
    body = request.get_json(force=True, silent=True) or {}
    sid = body.get("id") or ""
    with _lock:
        if not _switch_student(sid):
            return jsonify({"ok": False, "msg": "未找到该学生"}), 404
        st = _find_student(sid)
    persist()
    broadcast(_students_event())
    broadcast({"type": "switch_student", "student": dict(st or {}), "cur": sid})
    return jsonify({"ok": True, "cur": sid, "student": dict(st or {})})


@app.route("/api/students/rename", methods=["POST"])
def api_students_rename():
    body = request.get_json(force=True, silent=True) or {}
    st = _find_student(body.get("id") or "")
    if not st:
        return jsonify({"ok": False, "msg": "未找到该学生"}), 404
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "msg": "名称不能为空"}), 400
    st["name"] = name[:16]
    persist()
    broadcast(_students_event())
    return jsonify({"ok": True, "student": _st_brief(st)})


@app.route("/api/students/delete", methods=["POST"])
def api_students_delete():
    body = request.get_json(force=True, silent=True) or {}
    sts = _students()
    sid = body.get("id") or ""
    st = _find_student(sid)
    if not st:
        return jsonify({"ok": False, "msg": "未找到该学生"}), 404
    if len(sts) <= 1:
        return jsonify({"ok": False, "msg": "至少要保留一位学生"}), 400
    with _lock:
        sts.remove(st)
        if dict.get(session, "cur_student") == sid:
            _switch_student(sts[0]["id"])
    persist()
    broadcast(_students_event())
    return jsonify({"ok": True, "cur": dict.get(session, "cur_student"),
                    "students": [_st_brief(s) for s in _students()]})


@app.route("/api/students/compare")
def api_students_compare():
    """多位学生的对比数据：时长 / 段数 / 精简版结构环节 / 点评摘要 / 标记数"""
    sts = _students()
    rows = []
    cur_stages = list(simp.STAGES or [])
    try:
        for st in sts:
            paras = st.get("paragraphs") or []
            # 每位学生可能答的是不同题型：结构环节按其自己的题型取值，用完再还原
            try:
                simp.set_stages(_qtype_stage_names(st.get("qtype") or session.get("qtype"))
                                or list(QT_DEFAULT_TAIL))
            except Exception:
                traceback.print_exc()
            if simp.pending_count(paras):
                try:
                    simp.apply_all(paras, seg_map={s["id"]: s for s in st.get("segments") or []})
                except Exception:
                    pass
            segs = st.get("segments") or []
            marks = [s for s in segs if s.get("tags") or s.get("ctags") or s.get("note")]
            rows.append({
                "id": st.get("id"),
                "name": st.get("name") or "",
                "duration": int(st.get("acc") or st.get("duration") or 0),
                "segments": len(segs),
                "paragraphs": len(paras),
                "chars": sum(len(s.get("text") or "") for s in segs),
                "marks": len(marks),
                "mark_detail": [{"t": pz.mmss(s.get("t_start") or 0),
                                 "tags": s.get("tags") or [],
                                 "ctags": s.get("ctags") or [],
                                 "note": s.get("note") or "",
                                 "text": (s.get("text") or "")[:60]} for s in marks],
                "stages": [{"idx": p.get("idx"),
                            "label": p.get("label") or p.get("title") or p.get("plain_label") or "",
                            "plain": p.get("plain") or ""} for p in paras],
                "review": (st.get("review") or "")[:1500],
            })
    finally:
        # 还原当前会话题型的环节，避免影响后续请求
        try:
            simp.set_stages(cur_stages or list(QT_DEFAULT_TAIL))
        except Exception:
            traceback.print_exc()
    return jsonify({"ok": True, "students": rows, "cur": dict.get(session, "cur_student")})


@app.route("/api/session/info", methods=["POST"])
def api_session_info():
    body = request.get_json(force=True, silent=True) or {}
    old_qtype = str(session.get("qtype") or "").strip()
    for k in ("question", "qtype", "ref", "candidate"):
        if k in body:
            session[k] = body[k]
    if "q_analysis" in body:
        session["q_analysis"] = body["q_analysis"] or {}
    # 题型换了：环节口径随之改变，已判好的结构要按新题型重判
    if str(session.get("qtype") or "").strip() != old_qtype:
        if session.get("paragraphs"):
            _plain_all()
        else:
            _sync_stages()
    persist()
    return jsonify({"ok": True})


# ---------------- 快速记录（听记时随手记学生问题） ----------------
NOTE_MAX_ITEMS = 600
NOTE_MAX_CHARS = 2000


def _notes_clean(items):
    """规范化前端传来的条目：去首尾空白、丢掉末尾空行、限制条数与单条长度"""
    if isinstance(items, str):
        items = items.split("\n")
    out = []
    for it in (items or [])[:NOTE_MAX_ITEMS]:
        s = str(it if it is not None else "").replace("\r", " ")
        out.append(s[:NOTE_MAX_CHARS])
    while out and not out[-1].strip():
        out.pop()
    return out


@app.route("/api/notes/save", methods=["POST"])
def api_notes_save():
    """保存快速记录；带 student_id 时精确写到该学生名下（学生切换瞬间的落盘不会串号）"""
    body = request.get_json(force=True, silent=True) or {}
    items = _notes_clean(body.get("items"))
    sid = str(body.get("student_id") or "").strip()
    target = _find_student(sid) if sid else None
    if target is None:
        target = _cur_student()
    target["notes"] = items
    persist()
    return jsonify({"ok": True, "notes": items, "student_id": target.get("id")})


@app.route("/api/question/analyze", methods=["POST"])
def api_question_analyze():
    """粘贴题目 -> 题型 / 审题要点 / 答题结构 / 关键要点 / 参考要点"""
    body = request.get_json(force=True, silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "msg": "题目内容为空"}), 400
    if len(text) > 4000:
        text = text[:4000]
    want_ai = body.get("ai", True) is not False
    qtype_name = str(body.get("qtype") or session.get("qtype") or "").strip()
    stages = _qtype_stage_names(qtype_name)
    defs = _qtype_stage_defs(qtype_name)
    cp, ft = _qtype_review_parts(qtype_name)
    analysis, ai_err = None, ""
    if want_ai and (ai.cfg.get("api_key") or "").strip():
        try:
            raw = ai.analyze_question(text, qtype_stages=_qtype_stage_map())
            analysis = _qa_merge_qtype(text, raw, qtype_name=qtype_name)
            if analysis.get("source") != "ai":
                ai_err = analysis.get("ai_error") or "AI 结果不可用，已回退本地解析"
        except Exception as e:                     # 断网 / Key 失效 / 超时都退回本地
            ai_err = str(e)[:300]
    if analysis is None:
        analysis = qa.analyze_local(text, qtype_name=qtype_name, stages=stages, stage_defs=defs,
                                    checkpoints=cp, faults=ft)
        if ai_err:
            analysis["ai_error"] = ai_err
    session["q_analysis"] = analysis
    persist()
    return jsonify({"ok": True, "analysis": analysis})


@app.route("/api/question/save", methods=["POST"])
def api_question_save():
    """保存用户编辑后的解析结果（不入库，仅挂到当前会话）"""
    body = request.get_json(force=True, silent=True) or {}
    data = qa.normalize(body.get("analysis") or {})
    if not data:
        return jsonify({"ok": False, "msg": "解析内容无效"}), 400
    session["q_analysis"] = data
    if data.get("question"):
        session["question"] = data["question"]
    if data.get("qtype"):
        session["qtype"] = data["qtype"]
    if data.get("ref"):
        session["ref"] = data["ref"]
    persist()
    return jsonify({"ok": True, "session": {"question": session["question"],
                                            "qtype": session["qtype"],
                                            "ref": session["ref"]}})


@app.route("/api/segment/delete", methods=["POST"])
def api_seg_delete():
    body = request.get_json(force=True, silent=True) or {}
    sid = body.get("id")
    with _lock:
        session["segments"] = [s for s in session["segments"] if s["id"] != sid]
        for i, s in enumerate(session["segments"], 1):
            s["idx"] = i
        _rebuild_paragraphs(keep_last_open=engine.is_running())
    persist()
    broadcast({"type": "reload", "session": _session_view()})
    return jsonify({"ok": True})


# ---------------- 题型答题环节清单（句子手动改归属的菜单数据源） ----------------
PLAIN_NONE_STAGE = "未归类"
_QT_CACHE = {"mtime": -1.0, "types": []}


def _qtype_table():
    """读取题型知识文件里的题型清单（data/ 优先，回退 default_data/）"""
    paths = [os.path.join(DATA, "question_types.json"),
             os.path.join(BASE, "default_data", "question_types.json")]
    for path in paths:
        try:
            if not os.path.exists(path):
                continue
            mt = os.path.getmtime(path)
            if _QT_CACHE["mtime"] == mt and _QT_CACHE["types"]:
                return _QT_CACHE["types"]
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            types = data.get("types") if isinstance(data, dict) else data
            types = [t for t in (types or []) if isinstance(t, dict)]
            if types:
                _QT_CACHE["mtime"] = mt
                _QT_CACHE["types"] = types
                return types
        except Exception:
            traceback.print_exc()
    return _QT_CACHE["types"] or []


def _qtype_stage_names(qtype):
    """按题型名称 / 别名 / id 找到该题型的答题环节名列表"""
    q = (qtype or "").strip()
    if not q:
        return []
    for t in _qtype_table():
        names = [t.get("name"), t.get("id")] + list(t.get("aka") or [])
        if q in [str(x).strip() for x in names if str(x or "").strip()]:
            out = []
            for s in (t.get("stages") or []):
                nm = (s.get("name") if isinstance(s, dict) else s) or ""
                nm = str(nm).strip()
                if nm and nm not in out:
                    out.append(nm)
            return out
    return []


QT_USER_PATH = os.path.join(DATA, "question_types.json")
QT_DEFAULT_TAIL = ["审题", "提观点", "重分析", "抓落实", "结尾"]


def _qtype_public(t):
    """题型对外精简结构（前端下拉与结构联动用）：名称 / 别名 / 答题环节名"""
    stages = []
    for s in (t.get("stages") or []):
        nm = (s.get("name") if isinstance(s, dict) else s) or ""
        nm = str(nm).strip()
        if nm and nm not in stages:
            stages.append(nm)
    return {
        "id": str(t.get("id") or "").strip(),
        "name": str(t.get("name") or "").strip(),
        "aka": [str(x).strip() for x in (t.get("aka") or []) if str(x or "").strip()],
        "stages": stages,
        "custom": bool(t.get("custom")),
    }


def _qtype_list_public():
    out, seen = [], set()
    for t in _qtype_table():
        item = _qtype_public(t)
        if not item["name"] or item["name"] in seen:
            continue
        seen.add(item["name"])
        out.append(item)
    return out


def _qtype_full(qtype):
    """按题型名 / id / 别名取题型完整配置（含环节定义与点评要素）"""
    q = (qtype or "").strip()
    if not q:
        return None
    for t in _qtype_table():
        names = [t.get("name"), t.get("id")] + list(t.get("aka") or [])
        if q in [str(x).strip() for x in names if str(x or "").strip()]:
            return t
    return None


def _qtype_stage_defs(qtype):
    """该题型的环节定义：[{name, what, criteria, points}]（供结构生成/精简版判定用）

    qtype 可以是题型名 / id / 别名，也可直接传题型字典。
    """
    t = qtype if isinstance(qtype, dict) else _qtype_full(qtype)
    if not t:
        return []
    out = []
    for s in (t.get("stages") or []):
        if isinstance(s, str):
            s = {"name": s}
        if not isinstance(s, dict):
            continue
        nm = str(s.get("name") or "").strip()
        if not nm or any(x["name"] == nm for x in out):
            continue
        out.append({
            "name": nm,
            "what": str(s.get("what") or "").strip(),
            "criteria": str(s.get("criteria") or "").strip(),
            "points": [str(x).strip() for x in (s.get("points") or []) if str(x or "").strip()],
        })
    return out


def _qtype_stage_map():
    """{题型名: [环节名...]}，给 AI 判定题型时参考各题型的环节"""
    out = {}
    for t in _qtype_table():
        nm = str(t.get("name") or "").strip()
        if not nm:
            continue
        stages = []
        for x in (t.get("stages") or []):
            n = (x.get("name") if isinstance(x, dict) else x) or ""
            n = str(n).strip()
            if n and n not in stages:
                stages.append(n)
        out[nm] = stages
    return out


def _qtype_review_parts(qtype):
    """该题型的点评要素：(检查点列表, 常见失分表现列表)"""
    t = _qtype_full(qtype)
    rv = (t or {}).get("review") or {}
    cp = [str(x).strip() for x in (rv.get("checkpoints") or []) if str(x or "").strip()]
    ft = [str(x).strip() for x in (rv.get("faults") or []) if str(x or "").strip()]
    return cp, ft


def _sync_stages():
    """把当前题型的环节同步到 simplifier（逐字稿结构判定用），返回环节名列表"""
    stages = _stage_options()
    try:
        simp.set_stages(stages)
    except Exception:
        traceback.print_exc()
    return stages


@app.route("/api/qtypes")
def api_qtypes():
    """题型清单（内置题型 + 用户自定义题型，各自带答题环节）"""
    return jsonify({"ok": True, "types": _qtype_list_public(),
                    "user_file": QT_USER_PATH,
                    "has_user_file": os.path.exists(QT_USER_PATH)})


@app.route("/api/qtypes/add", methods=["POST"])
def api_qtypes_add():
    """新建题型：写入用户数据目录的 question_types.json（不动随包内置文件）"""
    body = request.get_json(force=True, silent=True) or {}
    name = re.sub(r"\s+", "", str(body.get("name") or ""))
    if not name:
        return jsonify({"ok": False, "msg": "题型名称不能为空"}), 400
    if len(name) > 20:
        return jsonify({"ok": False, "msg": "题型名称不要超过 20 字"}), 400
    raw = body.get("stages") or []
    if isinstance(raw, str):
        raw = re.split(r"[\n,，、;；/]+", raw)
    stages = []
    for s in raw:
        nm = re.sub(r"\s+", "", str(s or ""))
        if nm and nm not in stages:
            stages.append(nm)
    stages = (stages or list(QT_DEFAULT_TAIL))[:12]
    for t in _qtype_list_public():
        if name in ([t["name"]] + t["aka"]):
            return jsonify({"ok": False, "msg": "题型「" + name + "」已存在"}), 400
    full = [t for t in _qtype_table()]
    nid = "u_" + re.sub(r"[^0-9a-zA-Z]", "", uuid.uuid4().hex[:8])
    full.append({"id": nid, "name": name, "aka": [], "custom": True,
                 "scene": "", "keywords": [], "precheck": {"title": "", "items": []},
                 "stages": [{"name": nm, "what": "", "criteria": "", "points": []} for nm in stages],
                 "review": {"checkpoints": [], "faults": []}})
    payload = {"schema_version": "1.0",
               "title": "题型结构与点评要素（含用户自定义）",
               "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
               "types": full}
    try:
        tmp = QT_USER_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, QT_USER_PATH)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "msg": "保存题型失败：" + str(e)}), 500
    _QT_CACHE["mtime"] = -1.0
    _QT_CACHE["types"] = []
    return jsonify({"ok": True,
                    "type": {"id": nid, "name": name, "aka": [], "custom": True,
                             "stages": _qtype_stage_names(name)},
                    "types": _qtype_list_public()})


QT_DEFAULT_PATH = os.path.join(BASE, "default_data", "question_types.json")


def _qtype_default_table():
    """随包内置的题型清单（用于「恢复内置默认」）"""
    try:
        with open(QT_DEFAULT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        types = data.get("types") if isinstance(data, dict) else data
        return [t for t in (types or []) if isinstance(t, dict)]
    except Exception:
        traceback.print_exc()
        return []


def _qtype_write(full):
    """把整份题型清单写进用户数据目录（先写临时文件再替换）"""
    payload = {"schema_version": "1.0",
               "title": "题型结构与点评要素（含用户自定义）",
               "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
               "types": full}
    tmp = QT_USER_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, QT_USER_PATH)
    _QT_CACHE["mtime"] = -1.0
    _QT_CACHE["types"] = []


def _qtype_find_in(full, qtype):
    q = (qtype or "").strip()
    if not q:
        return -1
    for i, t in enumerate(full):
        names = [t.get("name"), t.get("id")] + list(t.get("aka") or [])
        if q in [str(x).strip() for x in names if str(x or "").strip()]:
            return i
    return -1


def _qtype_norm_stages(raw, keep_keys=True):
    """前端提交的环节数组 -> 规范结构（去掉空名、重名、超过 12 个）"""
    out = []
    for d in raw or []:
        if isinstance(d, str):
            d = {"name": d}
        if not isinstance(d, dict):
            continue
        nm = re.sub(r"\s+", " ", str(d.get("name") or "")).strip()
        if not nm or any(x["name"] == nm for x in out):
            continue
        item = {
            "name": nm[:20],
            "what": str(d.get("what") or "").strip()[:400],
            "criteria": str(d.get("criteria") or "").strip()[:600],
            "points": [str(x).strip()[:300] for x in (d.get("points") or []) if str(x or "").strip()][:20],
        }
        if keep_keys and str(d.get("key") or "").strip():
            item["key"] = str(d.get("key")).strip()[:40]
        out.append(item)
    return out[:12]


def _qtype_norm_review(raw):
    raw = raw or {}
    cp = [str(x).strip()[:300] for x in (raw.get("checkpoints") or []) if str(x or "").strip()]
    ft = [str(x).strip()[:300] for x in (raw.get("faults") or []) if str(x or "").strip()]
    return {"checkpoints": cp[:30], "faults": ft[:30]}


def _qtype_detail(t):
    """题型完整配置（题型结构管理页编辑用）"""
    if not t:
        return None
    stages = _qtype_stage_defs(t)
    rv = t.get("review") or {}
    base = _qtype_find_in(_qtype_default_table(), t.get("name") or t.get("id") or "")
    return {
        "id": str(t.get("id") or "").strip(),
        "name": str(t.get("name") or "").strip(),
        "aka": [str(x).strip() for x in (t.get("aka") or []) if str(x or "").strip()],
        "scene": str(t.get("scene") or "").strip(),
        "keywords": [str(x).strip() for x in (t.get("keywords") or []) if str(x or "").strip()],
        "stages": stages,
        "review": {"checkpoints": [str(x) for x in (rv.get("checkpoints") or [])],
                   "faults": [str(x) for x in (rv.get("faults") or [])]},
        "custom": bool(t.get("custom")),
        "builtin": base >= 0,
    }


@app.route("/api/qtypes/detail")
def api_qtypes_detail():
    """单个题型的完整结构（环节定义 + 点评要素）"""
    name = (request.args.get("name") or "").strip()
    full = _qtype_table()
    i = _qtype_find_in(full, name)
    if i < 0:
        return jsonify({"ok": False, "msg": "未找到题型「" + name + "」"}), 404
    return jsonify({"ok": True, "type": _qtype_detail(full[i])})


@app.route("/api/qtypes/save", methods=["POST"])
def api_qtypes_save():
    """保存题型结构（题型名/适用场景/特征关键词/答题环节/点评要素）"""
    body = request.get_json(force=True, silent=True) or {}
    orig = (body.get("orig") or body.get("name") or "").strip()
    t = body.get("type") or {}
    name = re.sub(r"\s+", "", str(t.get("name") or orig))
    if not name:
        return jsonify({"ok": False, "msg": "题型名称不能为空"}), 400
    if len(name) > 20:
        return jsonify({"ok": False, "msg": "题型名称不要超过 20 字"}), 400
    full = [dict(x) for x in _qtype_table()]
    i = _qtype_find_in(full, orig)
    if i < 0:
        return jsonify({"ok": False, "msg": "未找到题型「" + orig + "」"}), 404
    for j, x in enumerate(full):
        if j == i:
            continue
        names = [str(x.get("name") or "")] + [str(a or "") for a in (x.get("aka") or [])]
        if name in names:
            return jsonify({"ok": False, "msg": "题型名称「" + name + "」已被占用"}), 400
    cur = full[i]
    stages = _qtype_norm_stages(t.get("stages"))
    if not stages:
        return jsonify({"ok": False, "msg": "至少要保留一个答题环节"}), 400
    item = dict(cur)
    item["name"] = name
    item["aka"] = [str(a).strip() for a in (t.get("aka") if isinstance(t.get("aka"), list)
                                            else cur.get("aka") or []) if str(a or "").strip()][:12]
    if "scene" in t:
        item["scene"] = str(t.get("scene") or "").strip()[:800]
    if "keywords" in t:
        kw = t.get("keywords")
        if isinstance(kw, str):
            kw = re.split(r"[\n,，、;；/]+", kw)
        item["keywords"] = [str(x).strip()[:40] for x in (kw or []) if str(x or "").strip()][:40]
    item["stages"] = stages
    item["review"] = _qtype_norm_review(t.get("review"))
    full[i] = item
    try:
        _qtype_write(full)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "msg": "保存失败：" + str(e)}), 500
    _sync_stages()
    return jsonify({"ok": True, "type": _qtype_detail(item), "types": _qtype_list_public()})


@app.route("/api/qtypes/copy", methods=["POST"])
def api_qtypes_copy():
    """复制题型（复制出一份自定义题型，可独立改结构）"""
    body = request.get_json(force=True, silent=True) or {}
    src = (body.get("name") or "").strip()
    full = [dict(x) for x in _qtype_table()]
    i = _qtype_find_in(full, src)
    if i < 0:
        return jsonify({"ok": False, "msg": "未找到题型「" + src + "」"}), 404
    base = str(full[i].get("name") or src)
    exist = {str(x.get("name") or "") for x in full}
    name, n = base + "-副本", 2
    while name in exist:
        name, n = "%s-副本%d" % (base, n), n + 1
    item = json.loads(json.dumps(full[i], ensure_ascii=False))
    item["id"] = "u_" + re.sub(r"[^0-9a-zA-Z]", "", uuid.uuid4().hex[:8])
    item["name"] = name
    item["aka"] = []
    item["custom"] = True
    full.append(item)
    try:
        _qtype_write(full)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "msg": "复制失败：" + str(e)}), 500
    return jsonify({"ok": True, "type": _qtype_detail(item), "types": _qtype_list_public()})


@app.route("/api/qtypes/delete", methods=["POST"])
def api_qtypes_delete():
    """删除题型：自定义题型直接移除；内置题型也移除（可用「恢复内置默认」找回）"""
    body = request.get_json(force=True, silent=True) or {}
    name = (body.get("name") or "").strip()
    full = [dict(x) for x in _qtype_table()]
    i = _qtype_find_in(full, name)
    if i < 0:
        return jsonify({"ok": False, "msg": "未找到题型「" + name + "」"}), 404
    if len(full) <= 1:
        return jsonify({"ok": False, "msg": "至少要保留一个题型"}), 400
    removed = full.pop(i)
    try:
        _qtype_write(full)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "msg": "删除失败：" + str(e)}), 500
    if (session.get("qtype") or "").strip() == str(removed.get("name") or ""):
        session["qtype"] = ""
        persist()
    return jsonify({"ok": True, "name": str(removed.get("name") or ""),
                    "types": _qtype_list_public()})


@app.route("/api/qtypes/reset", methods=["POST"])
def api_qtypes_reset():
    """恢复某个题型的内置默认结构（自定义题型不支持）"""
    body = request.get_json(force=True, silent=True) or {}
    name = (body.get("name") or "").strip()
    full = [dict(x) for x in _qtype_table()]
    i = _qtype_find_in(full, name)
    if i < 0:
        return jsonify({"ok": False, "msg": "未找到题型「" + name + "」"}), 404
    defaults = _qtype_default_table()
    j = _qtype_find_in(defaults, name)
    if j < 0:
        return jsonify({"ok": False, "msg": "「" + name + "」是自定义题型，没有内置默认可恢复"}), 400
    full[i] = json.loads(json.dumps(defaults[j], ensure_ascii=False))
    try:
        _qtype_write(full)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "msg": "恢复失败：" + str(e)}), 500
    return jsonify({"ok": True, "type": _qtype_detail(full[i]), "types": _qtype_list_public()})


@app.route("/api/qtypes/defaults")
def api_qtypes_defaults():
    """内置题型名称清单（前端区分「内置 / 自定义」与「可恢复默认」）"""
    out = [str(t.get("name") or "").strip() for t in _qtype_default_table()]
    return jsonify({"ok": True, "builtin": [x for x in out if x]})


def _stage_options():
    """当前题型的答题环节清单（手动改归属菜单的选项，不含「未归类」）"""
    out = []
    for nm in _qtype_stage_names(session.get("qtype")):
        if nm not in out:
            out.append(nm)
    if not out:
        ana = session.get("q_analysis") or {}
        for r in (ana.get("structure") or []):
            nm = (r.get("stage") or r.get("label")) if isinstance(r, dict) else r
            nm = str(nm or "").strip()
            if nm and nm not in out:
                out.append(nm)
    if not out:
        out = list(getattr(simp, "DEFAULT_STAGES", QT_DEFAULT_TAIL))
    return out


def _manual_count():
    """当前学生里被手动指定归属的句子数"""
    return sum(1 for s in (session.get("segments") or [])
               if (s.get("stage_manual") or {}).get("stage"))


@app.route("/api/plain/stages")
def api_plain_stages():
    """当前题型的答题环节清单（供「手动改归属结构」菜单）"""
    return jsonify({"ok": True, "qtype": session.get("qtype") or "",
                    "stages": _sync_stages(), "none_stage": PLAIN_NONE_STAGE,
                    "manual_count": _manual_count()})


@app.route("/api/plain/sentence/stage", methods=["POST"])
def api_plain_sentence_stage():
    """把选中的若干句手动归入某个结构环节

    stage 为空表示取消手动归属（回到该段自动语义判定结果）；
    stage 为「未归类」表示手动归到未归类（不属于任何答题环节）。
    """
    body = request.get_json(force=True, silent=True) or {}
    raw = body.get("sids") or body.get("ids") or []
    if isinstance(raw, str):
        raw = [raw]
    sids = []
    for x in raw:
        v = str(x or "").strip()
        if v and v not in sids:
            sids.append(v)
    sids = sids[:300]
    # 拆分 / 合并生成的新句不在逐字稿里，归属记在段落的句子级编辑上
    kw_raw = body.get("keys") or []
    keys = []
    if isinstance(kw_raw, dict):
        for k, v in kw_raw.items():
            keys.append((str(k or "").strip(), v))
    elif isinstance(kw_raw, (list, tuple)):
        for it in kw_raw:
            if isinstance(it, dict):
                keys.append((str(it.get("key") or "").strip(), it.get("pid")))
            elif isinstance(it, (list, tuple)) and len(it) >= 2:
                keys.append((str(it[0] or "").strip(), it[1]))
            else:
                keys.append((str(it or "").strip(), None))
    keys = [(k, p) for k, p in keys if k][:300]
    if not sids and not keys:
        return jsonify({"ok": False, "msg": "没有选中任何句子"}), 400
    stage = (body.get("stage") or "").strip()
    if stage in ("", "auto", "自动", "取消", "取消手动归属"):
        stage = ""
    elif stage != PLAIN_NONE_STAGE and stage not in _stage_options() \
            and stage not in getattr(simp, "DEFAULT_STAGES", []):
        return jsonify({"ok": False, "msg": "「" + stage + "」不在当前题型的答题环节里"}), 400
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    touched = []
    hit = set()
    with _lock:
        for s in session.get("segments") or []:
            if s.get("id") not in sids:
                continue
            if stage:
                s["stage_manual"] = {"stage": stage, "at": now}
            else:
                s.pop("stage_manual", None)
            hit.add(s["id"])
            pid = s.get("pid")
            if pid and pid not in touched:
                touched.append(pid)
    paras = []
    for pid in touched:
        p = _find_para(pid)
        if p:
            _apply_plain(p)
            paras.append(p)
    # 句子级编辑键（拆句 / 合并产生的新句）：归属写进段落编辑，优先级最高
    # 前端只传 sids 时，先在段落编辑稿里找回这些键属于哪一段
    key_hit = 0
    rest = [x for x in sids if x not in hit]
    if rest:
        with _lock:
            for s0 in rest:
                p0 = _para_of_key(s0)
                if not p0:
                    continue
                ed0 = _edits(p0, create=True)
                if stage:
                    ed0["stage"][s0] = stage
                else:
                    ed0["stage"].pop(s0, None)
                ed0["seq"] = int(ed0.get("seq") or 0) + 1
                key_hit += 1
                if p0 not in paras:
                    paras.append(p0)
    key_by_para = {}
    with _lock:
        for k, pv in keys:
            p = _find_para(pv) if pv is not None else None
            if not p:
                m = re.match(r"^#p(\d+)#", k)
                if m:
                    p = _find_para(int(m.group(1)))
            if not p:
                continue
            key_by_para.setdefault(p.get("id"), []).append(k)
        for pid, klist in key_by_para.items():
            p = _find_para(pid)
            if not p:
                continue
            ed = _edits(p, create=True)
            for k in klist:
                if stage:
                    ed["stage"][k] = stage
                else:
                    ed["stage"].pop(k, None)
                key_hit += 1
            ed["seq"] = int(ed.get("seq") or 0) + 1
            if p not in paras:
                paras.append(p)
    persist()
    for p in paras:
        try:
            broadcast(_plain_event(p))
        except Exception:
            traceback.print_exc()
    segs = [{"id": s.get("id"), "stage_manual": s.get("stage_manual") or None}
            for s in (session.get("segments") or []) if s.get("id") in hit]
    miss = [x for x in sids if x not in hit]
    if keys:
        miss += [k for k, _p in keys if not any(k in v for v in key_by_para.values())]
    return jsonify({"ok": True, "count": len(hit) + key_hit, "miss": miss,
                    "stage": stage, "stage_label": stage or PLAIN_NONE_STAGE,
                    "segs": segs, "paras": [_rich_para(p) for p in paras], "keys": key_hit,
                    "manual_count": _manual_count()})


@app.route("/api/plain/stage", methods=["POST"])
def api_plain_stage():
    """手动指定某段落的结构环节（或恢复自动语义判定）"""
    body = request.get_json(force=True, silent=True) or {}
    pid = body.get("pid") or body.get("id")
    stage = (body.get("stage") or "").strip()
    sub = (body.get("sub") or "").strip()
    para = _find_para(pid)
    if not para:
        return jsonify({"ok": False, "msg": "未找到该段落"}), 404
    valid = _sync_stages()
    with _lock:
        if stage and stage in valid:
            para["stage_manual"] = {"stage": stage, "sub": sub, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        else:
            para.pop("stage_manual", None)
    _apply_plain(para)
    persist()
    try:
        broadcast(_plain_event(para))
    except Exception:
        traceback.print_exc()
    return jsonify({"ok": True, "para": _rich_para(para),
                    "auto": bool(stage and stage not in valid)})


@app.route("/api/plain/edit", methods=["POST"])
def api_plain_edit():
    """句子级编辑：拆句 / 合并 / 改字 / 删除

    body: {op: split|merge|text|delete, pid, key, key2, pos, text, keys}
    编辑结果落到 para["plain_edits"]["items"]，并立即刷新段落 plain 与结构环节，
    复制整篇 / 导出 / 精简要点取稿全部读同一份编辑稿。
    """
    body = request.get_json(force=True, silent=True) or {}
    op = str(body.get("op") or "").strip().lower()
    if op not in ("split", "merge", "text", "delete"):
        return jsonify({"ok": False, "msg": "不支持的编辑操作"}), 400
    para = _find_para(body.get("pid"))
    if not para:
        return jsonify({"ok": False, "msg": "未找到该段落"}), 404

    with _lock:
        items = _items_of(para)
        if not items:
            return jsonify({"ok": False, "msg": "该段还没有精简句可编辑"}), 400
        keys = [i["key"] for i in items]

        def idx_of(v):
            s = str(v if v is not None else "").strip()
            if not s:
                return -1
            return keys.index(s) if s in keys else -1

        def new_key(base):
            n = 1
            while True:
                k = "%s~%d" % (base, n)
                if k not in keys:
                    return k
                n += 1

        def clean(t):
            return re.sub(r"\s+", "", str(t or "")).strip()

        if op == "text":
            i = idx_of(body.get("key"))
            if i < 0:
                return jsonify({"ok": False, "msg": "未找到要改的句子"}), 400
            t = clean(body.get("text"))
            if not t:
                return jsonify({"ok": False, "msg": "改后的句子不能为空（要删整句请用删除）"}), 400
            if len(t) > 400:
                return jsonify({"ok": False, "msg": "单句超过 400 字，请先拆分"}), 400
            items[i]["text"] = t
        elif op == "delete":
            raw = body.get("keys") or body.get("key")
            if not isinstance(raw, (list, tuple)):
                raw = [raw]
            gone = set()
            for x in raw:
                i = idx_of(x)
                if i >= 0:
                    gone.add(items[i]["key"])
            if not gone:
                return jsonify({"ok": False, "msg": "未找到要删除的句子"}), 400
            items = [i for i in items if i["key"] not in gone]
            if not items:
                return jsonify({"ok": False, "msg": "整段至少保留一句，如需清空请直接删段落"}), 400
        elif op == "split":
            i = idx_of(body.get("key"))
            if i < 0:
                return jsonify({"ok": False, "msg": "未找到要拆分的句子"}), 400
            t = items[i]["text"]
            try:
                pos = int(body.get("pos"))
            except Exception:
                pos = 0
            if pos <= 0 or pos >= len(t):
                pos = int(len(t) / 2)
            pos = max(1, min(len(t) - 1, pos))
            a, b = clean(t[:pos]), clean(t[pos:])
            if not a or not b:
                return jsonify({"ok": False, "msg": "光标位置太靠边，拆不出两句"}), 400
            base = items[i]["key"]
            items[i:i + 1] = [{"key": base, "seg": items[i].get("seg"), "text": a},
                              {"key": new_key(base), "seg": items[i].get("seg"), "text": b}]
        else:  # merge
            i = idx_of(body.get("key"))
            j = idx_of(body.get("key2"))
            if j < 0:
                j = i + 1 if 0 <= i < len(items) - 1 else -1
            if i < 0 or j < 0 or i == j:
                return jsonify({"ok": False, "msg": "请选择相邻的两句再合并"}), 400
            if j < i:
                i, j = j, i
            if j != i + 1:
                return jsonify({"ok": False, "msg": "只能合并相邻的两句"}), 400
            merged = clean(items[i]["text"] + items[j]["text"])
            items[i]["text"] = merged
            items.pop(j)

        _items_save(para, items)
    persist()
    try:
        broadcast(_plain_event(para))
    except Exception:
        traceback.print_exc()
    return jsonify({"ok": True, "op": op, "para": _rich_para(para),
                    "parts": [{"sid": p["sid"], "seg": p.get("seg"), "text": p["text"]}
                              for p in _parts_final(para)],
                    "plain": para.get("plain") or "",
                    "plain_label": para.get("plain_label") or "",
                    "edit_seq": int((_edits(para) or {}).get("seq") or 0)})


@app.route("/api/mark", methods=["POST"])
def api_mark():
    body = request.get_json(force=True, silent=True) or {}
    sid = body.get("id")
    with _lock:
        for s in session["segments"]:
            if s["id"] == sid:
                if "tags" in body:
                    s["tags"] = [str(t)[:40] for t in (body["tags"] or []) if str(t).strip()][:12]
                if "ctags" in body:
                    s["ctags"] = [str(t).strip()[:120] for t in (body["ctags"] or []) if str(t).strip()][:12]
                if "note" in body:
                    s["note"] = body["note"]
                pid = s.get("pid")
                break
        else:
            return jsonify({"ok": False, "msg": "未找到该句"}), 404
    persist()
    try:
        _s = next((x for x in (session.get("segments") or []) if x["id"] == sid), {})
        broadcast({"type": "mark", "id": sid, "tags": _s.get("tags") or [],
                   "ctags": _s.get("ctags") or [], "note": _s.get("note") or ""})
        para = _find_para(pid) if pid else None
        if para:
            broadcast(_plain_event(para))
    except Exception:
        traceback.print_exc()
    return jsonify({"ok": True})


# ---------------- 题库 ----------------
Q_HEAD_RE = re.compile(r"^\s*(?:\d{1,3}\s*[\.、)．]|[（(]\s*\d{1,3}\s*[）)]|第\s*\d{1,3}\s*题)\s*")
REF_KEY_RE = re.compile(r"^(?:【\s*(参考要点|参考答案|答题思路|参考答案要点|要点|答案|解析)\s*】|"
                        r"(参考要点|参考答案|答题思路|要点|答案|解析)\s*[：:])\s*(.*)$")
TYPE_KEY_RE = re.compile(r"^(?:【\s*(类型|题型)\s*】|(类型|题型)\s*[：:])\s*(.*)$")


# ---- 粉笔风格解析文档：【考点】/【设问类型】/【粉笔审题】/【解析示例】/【备课要点】… ----
FENBI_ANCHOR_RE = re.compile(r"^\s*[*#>\-\s]*第\s*(\d{1,3})\s*题")
FENBI_SEC_RE = re.compile(r"^\s*[*#>\-\s]*【\s*([^】]{1,24}?)\s*】\s*$")
# 标记行后面还跟着内容的情况，如「【备课要点】一、题目思路」
FENBI_SEC_INLINE_RE = re.compile(r"^\s*[*#>\-\s]*【\s*([^】]{1,24}?)\s*】\s*(\S.*)$")
# 已知的段落标记前缀（白名单，避免把正文里的【含金量…】当成新段落）
_FB_SEC_NAMES = ("考点", "设问类型", "设问", "难度星级", "难度", "题目类型", "题型", "类型",
                 "粉笔审题", "审题点", "审题", "粉笔思维", "思维", "解析示例与参考答案",
                 "解析示例", "参考答案", "解析", "粉笔拓展", "拓展", "思路拓展",
                 "备课要点", "备课", "要点", "其他标签", "评分要点", "题目", "题干", "问题", "来源")
FENBI_SUBSEC_RE = re.compile(r"^\s*([一二三四五六七八九十]{1,3})\s*[、.．]\s*(.*)$")
# 无编号的子标题（如「题目思路」单独一行）
FENBI_SUBSEC_PLAIN_RE = re.compile(r"^\s*(题目思路|答题思路|思路|题目总结|总结|知识拓展|拓展知识|补充知识|补充拓展)\s*[：:]?\s*$")
FENBI_STAGE_RE = re.compile(r"^\s*[（(]\s*([一二三四五六七八九十]{1,3})\s*[）)]\s*(.*)$")
# 行首编号条目；负向断言排除「8.7 万人次」这类小数，避免把小数点当题号分隔符
FENBI_NUM_ITEM_RE = re.compile(r"^\s*\d{1,3}\s*[.、．)）](?!\s*\d)\s*(.+)$")
FENBI_CIRCLE_RE = re.compile(r"^\s*([①②③④⑤⑥⑦⑧⑨⑩⑪⑫])\s*(.+)$")
FENBI_STAGE_NAMES = ("审题", "提观点", "重分析", "抓落实", "总结", "结尾", "表态", "分析", "对策")
# 备课要点里无编号的口语化环节行首词（如「开头：…」「有序处理，分工协作…」「后续跟进，总结提升…」）
FENBI_STAGE_WORDS = ("开头", "入题", "破题", "表态", "亮明", "审题", "提观点", "重分析", "抓落实",
                     "有序处理", "处置", "解决问题", "问题处理", "善后", "后续跟进", "总结提升",
                     "总结", "结尾", "收尾", "升华", "达成", "沟通", "共情", "事前", "事中", "事后")
FENBI_STAGE_WORD_RE = re.compile(r"^\s*(%s)\s*[：:，,【]" % "|".join(FENBI_STAGE_WORDS))
_FB_END = "。！？；：\"”’』」】.!?;…"


def _fb_strip_head(t):
    """剥离题干行首的元信息前缀：【来源…】、（三级难度）、题·…】残尾、题号、「第 N 题」"""
    t = (t or "").strip()
    pats = (r"^【[^】]{0,60}】\s*",
            r"^[（(][^）)]{0,14}难度[）)]\s*",
            r"^题\s*[·•・]\s*[^】]{0,40}】\s*",
            r"^[·•・]\s*第\s*\d{1,3}\s*题\s*[·•・]?\s*",
            r"^\d{1,3}\s*[.、．)）]\s*",
            r"^第\s*\d{1,3}\s*题\s*")
    prev = None
    while t != prev:
        prev = t
        for p in pats:
            t = re.sub(p, "", t).strip()
    return t


# PDF 版粉笔解析里标记常常是独立一行、且不带【】（如「粉笔审题」「解析示例」缩进一行）
_FB_PLAIN_MARK_RE = re.compile(
    r"^[ \t*#>\-]*(粉笔审题|粉笔思维|解析示例|粉笔拓展|备课要点)[ \t]*(?:与参考答案|及参考答案)?[ \t]*$", re.M)


def _fb_norm_marks(text):
    """把整行独立的「粉笔审题 / 解析示例 / 粉笔拓展 / 备课要点」规范成【…】，PDF 文本层靠它才能分节"""
    if not text or not _FB_PLAIN_MARK_RE.search(text):
        return text
    out = []
    for ln in text.splitlines():
        m = _FB_PLAIN_MARK_RE.match(ln)
        out.append("【" + m.group(1) + "】" if m else ln.rstrip())
    return "\n".join(out)


def _is_fenbi_text(text):
    """是否粉笔风格的结构化解析文档（按【考点】等标记分节）"""
    return bool(re.search(r"^\s*[*#>\-\s]*【\s*(?:粉笔审题|解析示例|粉笔拓展|备课要点|考点)", text or "", re.M))


def _fb_paras(lines):
    """合并被分页/换行切断的段落：上一段没有句末标点时与下一段拼接（短标题行除外）"""
    out = []
    for ln in lines or []:
        s = (ln or "").strip()
        if not s:
            continue
        prev = out[-1] if out else ""
        short_title = 0 < len(prev) <= 12 and not any(c in prev for c in _FB_END)
        if prev and not short_title and prev[-1] not in _FB_END:
            out[-1] = prev + s
        else:
            out.append(s)
    return out


def _fb_subsecs(lines):
    """把「一、题目思路 / 二、题目总结 / 三、补充知识」切成分段（也兼容没编号的「题目思路」独立行）"""
    out, order, cur = {}, [], None
    for s in lines or []:
        m = FENBI_SUBSEC_RE.match(s)
        if m and len((m.group(2) or "").strip()) <= 30:
            cur = (m.group(2) or "").strip() or m.group(1)
            if cur not in out:
                out[cur] = []
                order.append(cur)
            continue
        mp = FENBI_SUBSEC_PLAIN_RE.match(s)
        if mp:
            cur = mp.group(1)
            if cur not in out:
                out[cur] = []
                order.append(cur)
            continue
        if cur is not None:
            out[cur].append(s)
    return [(k, out[k]) for k in order]


def _fb_dedup_paras(lines, th=0.9):
    """同一题内被源文档重复排版的解析段只保留第一份（相似段落视为重复），避免参考要点出现两份"""
    out = []
    for s in lines or []:
        key = re.sub(r"\s+", "", s or "")
        if not key:
            continue
        if len(key) < 10:
            out.append(s)
            continue
        dup = False
        for prev in out:
            pk = re.sub(r"\s+", "", prev or "")
            if not pk or abs(len(pk) - len(key)) > max(12, int(0.2 * len(key))):
                continue
            if pk == key or difflib.SequenceMatcher(None, pk, key).ratio() >= th:
                dup = True
                break
        if not dup:
            out.append(s)
    return out


def _fb_sec_match(s):
    """识别【考点】这类段落标记行，返回 (段落名, 同行剩余内容)；不是标记行返回 None"""
    for pat in (FENBI_SEC_RE, FENBI_SEC_INLINE_RE):
        m = pat.match(s or "")
        if not m:
            continue
        name = re.sub(r"\s+", "", m.group(1) or "")
        if not name:
            continue
        if not any(name == nm or name.startswith(nm) or nm.startswith(name) for nm in _FB_SEC_NAMES):
            continue
        rest = (m.group(2) or "").strip() if m.re is FENBI_SEC_INLINE_RE else ""
        return name, rest
    return None



def _fb_qtype(core):
    """【考点】→ 软件内题型：「态度观点-领导人讲话类」→「态度观点」"""
    t = (core or "").strip()
    if not t:
        return ""
    hits = []
    for qt in _qtype_table():
        names = [qt.get("name"), qt.get("id")] + list(qt.get("aka") or [])
        for nm in names:
            nm = str(nm or "").strip()
            if nm and nm in t:
                hits.append((len(nm), str(qt.get("name") or nm).strip()))
    if hits:
        hits.sort(reverse=True)
        return hits[0][1]
    head = re.split(r"[-－—·/、|｜]", t)[0].strip()
    return head[:20] or t[:20]


def _qa_merge_qtype(text, raw, qtype_name=""):
    """AI 结果合并（统一入口）

    AI 返回的题型与传入题型不一致时（例如题库/文档没给题型、由 AI 判定），
    按 AI 判出的题型再合并一次，避免出现「题型=组织管理、答题环节却是通用五环节」的错配。
    """
    stages = _qtype_stage_names(qtype_name)
    cp, ft = _qtype_review_parts(qtype_name)
    data = qa.merge_ai(text, raw, qtype_name=qtype_name, stages=stages,
                       stage_defs=_qtype_stage_defs(qtype_name),
                       checkpoints=cp, faults=ft)
    if data.get("source") != "ai":
        return data
    real = _fb_qtype(str(data.get("qtype") or ""))
    if not real or real == qtype_name:
        return data
    st2 = _qtype_stage_names(real)
    if not st2:
        return data
    cp2, ft2 = _qtype_review_parts(real)
    d2 = qa.merge_ai(text, raw, qtype_name=real, stages=st2,
                     stage_defs=_qtype_stage_defs(real), checkpoints=cp2, faults=ft2)
    return d2 if d2.get("source") == "ai" else data


def _fb_qtype_candidates(core, ask="", limit=4):
    """按【考点】/【设问类型】给现有题型打分，用于识别不出题型时给出可下拉修正的候选"""
    txt = ((core or "") + " " + (ask or "")).strip()
    if not txt:
        return []
    scored = []
    for t in _qtype_table():
        name = str(t.get("name") or "").strip()
        if not name:
            continue
        score = 0
        for nm in [name, t.get("id")] + list(t.get("aka") or []):
            nm = str(nm or "").strip()
            if nm and nm in txt:
                score += 10 + len(nm)
        for kw in (t.get("keywords") or []):
            kw = str(kw or "").strip()
            if kw and kw in txt:
                score += 3 + len(kw) // 2
        if score:
            scored.append((score, name))
    scored.sort(key=lambda x: (-x[0], -len(x[1])))
    out = []
    for _, nm in scored:
        if nm not in out:
            out.append(nm)
    return out[:limit]


def _fb_stage_name(raw):
    """「（一）提观点（直接式）：【…】」→ 提观点"""
    base = re.sub(r"【[^】]*】", "", raw or "")
    base = re.split(r"[（(：:]", base)[0].strip(" ：:、")
    for nm in FENBI_STAGE_NAMES:
        if nm in base:
            return nm
    return base[:12] or "环节"


def _fb_stage_label(raw):
    """环节行里的括号说明与【中观逻辑】等备注，作为环节小标题"""
    bits = []
    for m in re.finditer(r"[（(]([^）)]{1,60})[）)]", raw or ""):
        t = m.group(1).strip()
        if t:
            bits.append(t)
    for m in re.finditer(r"【([^】]{1,80})】", raw or ""):
        t = m.group(1).strip()
        if t:
            bits.append(t)
    return "｜".join(bits)[:160]


def _fb_structure(lines):
    """【备课要点+慕夕修改】「一、题目思路」→ [{stage,label,points}]"""
    body = []
    for k, v in _fb_subsecs(lines):
        if "思路" in k:
            body = v
            break
    if not body:
        # 没有「一、题目思路」小标题时，直接对整段备课要点做环节切分，避免整题结构为空
        body = [s for s in (lines or [])
                if (s or "").strip()
                and not FENBI_SUBSEC_RE.match(s)
                and not FENBI_SUBSEC_PLAIN_RE.match(s)]
    if not body:
        return []
    stages, cur = [], None
    seen = set()
    for s in body:
        m = FENBI_STAGE_RE.match(s)
        if m:
            cur = {"stage": _fb_stage_name(m.group(2)), "label": _fb_stage_label(m.group(2)), "points": []}
            stages.append(cur)
            seen.add(cur["stage"])
            continue
        mw = FENBI_STAGE_WORD_RE.match(s) if len(s) <= 120 else None
        if mw and mw.group(1) not in seen:
            # 「开头：…」「有序处理，分工协作…」「后续跟进，总结提升…」这些无编号行本身就是环节
            rest = s[mw.end() - 1:].lstrip("：:，,【").strip()
            lab = _fb_stage_label(s)
            cur = {"stage": mw.group(1), "label": lab, "points": [rest] if rest else []}
            stages.append(cur)
            seen.add(mw.group(1))
            continue
        if cur is None:
            cur = {"stage": "思路", "label": "", "points": []}
            stages.append(cur)
        mn = FENBI_NUM_ITEM_RE.match(s)
        mc = FENBI_CIRCLE_RE.match(s)
        if mn:
            cur["points"].append(mn.group(1).strip())
        elif mc:
            cur["points"].append((mc.group(1) + mc.group(2)).strip())
        elif re.match(r"^分论点", s):
            cur["points"].append(s)
        elif cur["points"]:
            cur["points"][-1] = cur["points"][-1] + s
        elif s:
            cur["points"].append(s)
    return [x for x in stages if x["points"] or x["label"]]


def _fb_route_structure(route):
    """没有备课要点时，用「故本题的答题思路可为：首先…；其次…；最后…」兜底出结构"""
    stages = []
    for m in re.finditer(r"(首先|其次|再次|然后|接着|最后|一是|二是|三是)[，,、]?\s*([^；;。]{2,60})", route or ""):
        nm = m.group(1)
        stages.append({"stage": nm, "label": "", "points": [m.group(2).strip()]})
    return stages


def _parse_fenbi_block(lines):
    """解析单个「第 N 题」块，返回 {qno,question,qtype,qtype_reason,exam,structure,keys,ref,source}"""
    pre, secs, order, cur = [], {}, [], None
    qno = None
    for ln in lines or []:
        s = (ln or "").strip()
        if not s:
            continue
        if qno is None:
            ma = FENBI_ANCHOR_RE.match(s)
            if ma:
                try:
                    qno = int(ma.group(1))
                except Exception:
                    qno = None
        sm = _fb_sec_match(s)
        if sm:
            cur, rest = sm
            if cur not in secs:
                secs[cur] = []
                order.append(cur)
            if rest:
                # 「【备课要点】一、题目思路」这类同行内容要归入本段，否则整段结构会丢
                secs[cur].append(rest)
            continue
        if cur is None:
            pre.append(s)
        else:
            secs[cur].append(s)

    def pick(*names):
        for nm in names:
            for k in order:
                if k == nm or k.startswith(nm):
                    return secs.get(k) or []
        return []

    # 题干：题号后那段正文（PDF 按行宽断行，需要把续行拼回来）
    q, head_i = "", None
    pool = [s for s in pre
            if not FENBI_ANCHOR_RE.match(s)
            and not re.search(r"·\s*第\s*\d{1,3}\s*题\s*·", s)
            and not re.match(r"^题\s*[·•・]", s)
            and not (s.startswith("题") and "】" in s and len(s) < 40)]
    # 以「锚点行之后的第一段正文」为首选，避免把「8.7 万人次…」这类数字开头的续行当成题号行
    for i, s in enumerate(pool):
        t = _fb_strip_head(s)
        if len(t) < 8:
            continue
        if t.startswith(("【", "本题是", "审题点", "题·")):
            continue
        if re.match(r"^[一二三四五六七八九十]{1,3}\s*[、.．]", t):
            continue
        q, head_i = t, i
        break
    if not q:
        for i, s in enumerate(pool):
            mn = FENBI_NUM_ITEM_RE.match(s)
            if mn and len(mn.group(1).strip()) >= 6:
                q, head_i = mn.group(1).strip(), i
                break
    if not q:
        rest = pick("题目", "题干", "问题")
        if rest:
            q = Q_HEAD_RE.sub("", rest[0]).strip()
    if q and head_i is not None:
        # 题干可能跨多句（如「…活动主题：…。请选择其中一个主题…。」），不能遇到第一个句号就停，
        # 只在遇到下一道题锚点 / 审题段 / 元信息行时终止，避免题干缺尾句。
        q_parts = [q]
        for s in pool[head_i + 1:]:
            if FENBI_NUM_ITEM_RE.match(s) or s.startswith(("本题是", "审题点")) or _fb_sec_match(s):
                break
            if re.match(r"^[（(][^）)]{0,14}难度[）)]\s*$", s) or re.match(r"^【[^】]{0,60}】\s*$", s):
                continue
            q_parts.append(s)
            if sum(len(x) for x in q_parts) >= 1200:
                break
        # 源文档里题干整段重复排版时只保留一份
        q = "".join(_fb_dedup_paras(q_parts)).strip()

    core = "；".join(pick("考点", "题型", "题目类型"))
    if not core:
        # PDF 版把题型写成独立一行「【社会现象】」，_fb_hoist_tags 已把它挪到题首之后
        for s in (pre or []):
            m = _FB_TAG_RE.match(s or "")
            if m and _fb_qtype(m.group(1)):
                core = m.group(1)
                break
    ask = "；".join(pick("设问类型", "设问"))
    star = "；".join(pick("难度星级", "难度"))
    if not star and lines:
        m = re.search(r"[一二三四五]级难度|难度[：:]\s*\S{1,8}", lines[0])
        star = m.group(0) if m else ""
    qtype = _fb_qtype(core)

    # 【粉笔审题】→ 审题要点（总述 + 各审题点 + 答题思路尾句）
    exam, route = [], ""
    for s in _fb_paras(pick("粉笔审题", "审题")):
        if re.match(r"^故本题的?答题思路", s) or "答题思路可为" in s[:16]:
            route = s
            continue
        if re.match(r"^审题点\s*[：:]", s):
            exam.append("审题点：" + re.sub(r"^审题点\s*[：:]\s*", "", s).strip())
            continue
        if exam and exam[-1].startswith("审题点："):
            # 审题点下面的解释段落并入该审题点，保证「审题要点」整块可读
            exam[-1] = exam[-1] + "\n" + s
            continue
        exam.append(s)
    if route:
        # 尾句是「整体答题思路」，与单个审题点不同，加前缀区分，避免被当成第 N 个审题点
        exam.append("答题思路：" + re.sub(r"^故?本题的?答题思路(?:可为|如下)?\s*[：:]?\s*", "", route).strip())

    # 【解析示例】→ 参考要点正文
    ref = "\n".join(_fb_dedup_paras(_fb_paras(pick("解析示例", "参考答案", "解析示例与参考答案", "解析"))))

    # 【粉笔拓展】→ 补充思路 / 细节拓展
    keys, label = [], ""
    for s in _fb_paras(pick("粉笔拓展", "拓展", "思路拓展")):
        if re.match(r"^(补充思路|细节拓展|拓展思路|其他思路|思路拓展)\s*[：:]?\s*$", s):
            label = re.sub(r"\s*[：:]\s*$", "", s)
            continue
        m = re.match(r"^(补充思路|细节拓展|拓展思路|其他思路)\s*[：:]\s*(\S.+)$", s)
        if m:
            keys.append(m.group(1) + "：" + m.group(2))
            continue
        if label:
            keys.append(label + "：" + s)
        elif s:
            keys.append(s)
    if not ref:
        ref = "\n".join([x for x in [route] if x]).strip()

    # 【备课要点+慕夕修改】→ 结构化环节 + 总结 / 补充知识
    bp = pick("备课要点", "备课", "要点")
    structure = _fb_structure(bp)
    for k, v in _fb_subsecs(bp):
        if "思路" in k:
            continue
        pre_key = "总结" if "总结" in k else ("补充" if ("补充" in k or "知识" in k) else "")
        for s in v:
            mn = FENBI_NUM_ITEM_RE.match(s)
            mc = FENBI_CIRCLE_RE.match(s)
            txt = (mn.group(1) if mn else (mc.group(1) + mc.group(2) if mc else s)).strip()
            if not txt:
                continue
            keys.append((pre_key + "：" + txt) if pre_key and not txt.startswith(pre_key) else txt)
    if not structure and route:
        structure = _fb_route_structure(route)
    if not keys and not ref:
        ref = "\n".join(_fb_paras(pick("粉笔思维", "思维")))

    reason_bits = []
    if core:
        reason_bits.append("考点：" + re.sub(r"\s+", " ", core))
    if ask:
        reason_bits.append("设问类型：" + re.sub(r"\s+", " ", ask))
    if star:
        reason_bits.append("难度：" + re.sub(r"\s+", " ", star)[:20])
    return {
        "qno": qno,
        "question": q,
        "qtype": qtype,
        "qtype_reason": "；".join(reason_bits),
        "qtype_src": " ".join([x for x in [core, ask] if x]).strip(),
        "qtype_candidates": _fb_qtype_candidates(core, ask),
        "exam": exam,
        "structure": structure,
        "keys": keys,
        "ref": ref,
        "source": "import:fenbi",
    }


def _fb_num_anchors(lines):
    """PDF 版粉笔解析每题以「1. 题干」起头、题号在各部分内循环；
    仅当该行后 15 行内出现【粉笔审题】/【解析示例】时才认作题首，避免把参考要点里的「1.」当新题"""
    idx = []
    for i, ln in enumerate(lines):
        m = re.match(r"^\s*(\d{1,3})\s*[.．、]\s*(\S.*)$", ln or "")
        if not m or len(m.group(2).strip()) < 10:
            continue
        fwd = "\n".join(lines[i + 1:i + 16])
        if re.search(r"【(?:粉笔审题|解析示例)】", fwd):
            idx.append(i)
    return idx


_FB_TAG_RE = re.compile(r"^\s*[*#>\-]?\s*【\s*([^】]{1,20}?)\s*】\s*$")


def _fb_hoist_tags(lines):
    """PDF 版里题型标签常单独成行（如「【社会现象】」）且排在该题题首之前，
    这里把它挪到所属题目的题首之后，方便按题块取到题型；非题型的【…】（审题/解析示例等）不动。"""
    lines = list(lines or [])
    n = len(lines)
    move, drop = {}, set()
    for i, ln in enumerate(lines):
        m = _FB_TAG_RE.match(ln or "")
        if not m or not _fb_qtype(m.group(1)):
            continue
        seen = 0
        for j in range(i + 1, n):
            s = (lines[j] or "").strip()
            if not s:
                continue
            if FENBI_ANCHOR_RE.match(s) or re.match(r"^\s*\d{1,3}\s*[.．、]\s*\S", s):
                move[j] = m.group(1)
                drop.add(i)
                break
            seen += 1
            if seen >= 3 or _FB_TAG_RE.match(s):
                break
    if not drop:
        return lines
    out = []
    for i, ln in enumerate(lines):
        if i in drop:
            continue
        out.append(ln)
        if i in move:
            out.append("【%s】" % move[i])
    return out


def _fenbi_blocks(text):
    """按「第 N 题」主锚点切块；没有该锚点时依次退回「1. 题干」编号锚点、【考点】出现次数切"""
    lines = _fb_hoist_tags([ln.rstrip() for ln in (text or "").splitlines()])
    idx = [i for i, ln in enumerate(lines) if FENBI_ANCHOR_RE.match(ln)]
    if idx:
        bounds = idx + [len(lines)]
        return [lines[bounds[k]:bounds[k + 1]] for k in range(len(idx))]
    idx = _fb_num_anchors(lines)
    if idx:
        # 题首之前的内容（卷首说明/目录）不并入第一题，避免污染题干
        bounds = idx + [len(lines)]
        return [lines[bounds[k]:bounds[k + 1]] for k in range(len(idx))]
    idx = [i for i, ln in enumerate(lines) if re.match(r"^\s*[*#>\-\s]*【\s*考点\s*】", ln)]
    if idx:
        bounds = idx + [len(lines)]
        out = [lines[bounds[k]:bounds[k + 1]] for k in range(len(idx))]
        out[0] = lines[:idx[0]] + out[0]
        return out
    return [lines]


def _parse_fenbi_text(text):
    items = []
    for block in _fenbi_blocks(text):
        it = _parse_fenbi_block(block)
        if it and len(it.get("question") or "") >= 4:
            items.append(it)
    return items


# =====================================================================
# 粉笔「陪练解析」类文档（字段标签不带方括号）忠实解析
#   真实结构（已用《2026年江西事业单位·陪练解析.pdf》304 页实测）：
#     【题型标签】(可选，题号前 1 行) → 「1. 题干…」 → 粉笔审题：→ 粉笔思维：→ 解析示例：→ 粉笔拓展：
#   三个坑：
#     1) 字段标签不带【】，不能用【粉笔审题】锚点切块；
#     2) 题号在文档内按「刷题练习一/二/三…」分组从 1 重新计数，不是全局唯一；
#     3) 页脚是纯数字页码，正文会跨页断行（一句话被页码切成两截）。
#   原则：只抽文档真实写出的字段；文档没有的（答题环节等）留空并写进 missing，
#         绝不套题库模板编造，避免「模板套壳冒充文档解析」。
# =====================================================================
_PL_SEC_RE = re.compile(r"^\s{0,8}(粉笔审题|粉笔思维|解析示例与参考答案|解析示例|参考答案|"
                        r"粉笔拓展|粉笔点拨|小贴士|备课要点|设问类型|考点|难度星级)\s*[:：]?\s*$")
_PL_NUM_RE = re.compile(r"^\s{0,6}(\d{1,3})\s*[.．、]\s*(\S.*)$")
_PL_PAGE_RE = re.compile(r"^\s*\d{1,3}\s*$")
_PL_TAG_RE = re.compile(r"^\s*【\s*([^】]{1,24}?)\s*】\s*$")
_PL_GRP_RE = re.compile(r"^\s*(刷题练习[一二三四五六七八九十]+|套题练习[一二三四五六七八九十]+|"
                        r"第[一二三四五六七八九十]+套|第[一二]部分.{0,24})\s*$")
_PL_END = "。！？；：”』）」!?;:"
_PL_DECL_TAIL_RE = re.compile(r"^.{0,12}?(?:类|题目|问题|题型|题)")
_PL_QT_BUILTIN = ("社会现象", "态度观点", "人际关系", "组织管理", "应急应变", "现场模拟",
                  "自我认知", "材料题", "串词题", "演讲", "漫画", "排序")
# 文档正文里常见的另一种叫法（「本题属于人际沟通类题目」→ 人际关系）
_PL_QT_SYN = {"综合分析": "社会现象", "情景模拟": "现场模拟", "人际沟通": "人际关系",
              "人际": "人际关系", "计划组织": "组织管理", "串词": "串词题",
              "编故事": "串词题", "态度管理": "态度观点", "态度": "态度观点",
              "应急": "应急应变", "应变": "应急应变", "职位匹配": "自我认知",
              "名言警句": "社会现象"}
_PL_STRONG_RE = re.compile(r"(首先|其次|再次|然后|接着|之后|随后|最后|第一|第二|第三|第四|第五|"
                           r"一是|二是|三是|四是|一方面|另一方面)")
_PL_WEAK_RE = re.compile(r"(开篇|入题|先|再|进而|结尾|前期|中期|后期|准备阶段|实施阶段|总结阶段)")
_PL_LISTSEP_RE = re.compile(r"[“\"]([^”\"]{4,40}?)[”\"]\s*(?:的)?(?:思路|步骤|顺序|逻辑)")
_PL_ROUTE_MARK_RE = re.compile(r"(答题|作答|基本|解题|呈现)思路|作答逻辑|思路是|思路为")
_PL_NEXT_RE = re.compile(r"^(?:其次|再次|然后|接着|之后|随后|最后|第三|第四|但是|同时|并且|另一方面|再|进而)")


def _pl_qtype_names():
    """题库题型名 + 别名 + 内置兜底，长的优先匹配，避免「人际」抢在「人际关系」前面"""
    names = []
    for t in _qtype_table():
        for nm in [t.get("name"), t.get("id")] + list(t.get("aka") or []):
            nm = str(nm or "").strip()
            if nm and nm not in names:
                names.append(nm)
    for nm in _PL_QT_BUILTIN:
        if nm not in names:
            names.append(nm)
    return sorted(names, key=len, reverse=True)


def _pl_canon_names():
    out = set()
    for t in _qtype_table():
        nm = str(t.get("name") or "").strip()
        if nm:
            out.add(nm)
    return out or set(_PL_QT_BUILTIN)


def _pl_qtype_from_tag(tag):
    """题号前的【…】标签 → 题库题型名；「其他题型」这种非题型标签返回空，交给正文声明"""
    t = (tag or "").strip()
    if not t:
        return ""
    names = _pl_canon_names()
    hit = _fb_qtype(t)
    if hit and hit in names:
        return hit
    for k, v in sorted(_PL_QT_SYN.items(), key=lambda z: -len(z[0])):
        if k in t and v in names:
            return v
    for nm in _pl_qtype_names():
        if nm in t and nm in names:
            return nm
    return ""


def _pl_qtype_from_decl(text):
    """文档正文自带的题型声明：「本题是一道人际关系题」「本道题属于组织管理类题目」"""
    h = (text or "")[:420]
    if not h:
        return ""
    names = _pl_canon_names()
    hits = []
    for nm in _pl_qtype_names():
        for m in re.finditer(re.escape(nm), h):
            if _PL_DECL_TAIL_RE.match(h[m.end():m.end() + 14]):
                q = _fb_qtype(nm) or nm
                hits.append((m.start(), q if q in names else ""))
    for k, v in _PL_QT_SYN.items():
        for m in re.finditer(re.escape(k), h):
            if _PL_DECL_TAIL_RE.match(h[m.end():m.end() + 14]):
                hits.append((m.start(), v if v in names else ""))
    if not hits:
        return ""
    hits.sort()
    for _p, q in hits:
        if q:
            return q
    return ""


def _pl_stage_pairs(route):
    """从「答题思路：首先…；其次…；最后…」抽出 (环节名, 环节原文)，只认文档写出的顺序词"""
    route = (route or "").strip()
    out = []
    if not route:
        return out
    m = _PL_LISTSEP_RE.search(route)
    if m and re.search(r"[、，—\-]", m.group(1)):
        for x in re.split(r"[、，—\-]+", m.group(1)):
            x = x.strip()
            if len(x) >= 2:
                out.append((x[:16], x))
        if out:
            return out
    if route.count("—") >= 2:
        tail = re.split(r"[：:]", route, 1)[-1]
        for x in re.split(r"—+", tail):
            x = re.sub(r"^[，,、；;。]+", "", x).strip()
            if len(x) >= 2:
                out.append((x[:16], x))
        if out:
            return out
    strong = [(m.start(), m.end()) for m in _PL_STRONG_RE.finditer(route)]
    if len(strong) >= 2:
        marks = strong
    else:
        weak = [(m.start(), m.end()) for m in _PL_WEAK_RE.finditer(route)]
        marks = weak if len(weak) >= 2 else strong + weak
    marks.sort()
    for k, (p, _e) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else len(route)
        seg = route[p:end]
        body = re.sub(r"^(?:首先|其次|再次|然后|接着|之后|随后|最后|第一|第二|第三|第四|第五|"
                      r"一是|二是|三是|四是|一方面|另一方面|开篇|入题|先|再|进而|结尾|"
                      r"前期|中期|后期|准备阶段|实施阶段|总结阶段)", "", seg).lstrip("，,、：: ")
        name = re.split(r"[，,、；;。]", body)[0].strip()
        name = re.sub(r"^(要|应|需|可以|应当|做好|进行|从)", "", name)[:16]
        if len(name) >= 2 and name not in [x[0] for x in out]:
            out.append((name, body.strip("，,、；;。 ")[:160]))
    return out


def _pl_join_paras(lines, idxs, skip=()):
    """把行号区间拼成段落：句末标点断段，跨页断行（无标点结尾）直接续拼；返回 (段落列表, 跨页续拼次数)"""
    paras, cur, wraps = [], "", 0
    prev = None
    for i in idxs:
        if prev is not None and i != prev + 1:
            wraps += 1
        prev = i
        ln = lines[i] if 0 <= i < len(lines) else ""
        if i in skip or not ln.strip() or _PL_PAGE_RE.match(ln) or _PL_GRP_RE.match(ln):
            continue
        if _PL_TAG_RE.match(ln):
            continue
        t = ln.strip()
        if not cur:
            cur = t
        elif cur[-1] in _PL_END:
            paras.append(cur)
            cur = t
        else:
            cur += t
            wraps += 1
    if cur:
        paras.append(cur)
    return paras, wraps


def _pl_is_plain_fenbi(text):
    """字段标签不带方括号的粉笔陪练解析文档；带【粉笔审题】的老格式仍走原解析"""
    t = text or ""
    if len(t) < 800:
        return False
    if "粉笔审题" not in t:
        return False
    if re.search(r"【\s*粉笔审题\s*】", t) or re.search(r"【\s*解析示例\s*】", t):
        return False
    n = 0
    for ln in t.splitlines():
        if _PL_SEC_RE.match(ln):
            n += 1
            if n >= 3:
                return True
    return False


def _pl_parse_text(text):
    """按文档真实结构逐题抽取，返回 [{qno,question,qtype,exam,structure,keys,ref,...}]"""
    raw = (text or "").replace("\u2003", " ").replace("\xa0", " ").replace("\u3000", " ")
    lines = [ln.replace("\x08", " ").rstrip() for ln in raw.splitlines()]
    n = len(lines)
    secs = []
    for i, ln in enumerate(lines):
        m = _PL_SEC_RE.match(ln)
        if m:
            secs.append((i, m.group(1)))
    if not secs:
        return []
    asks = [i for i, nm in secs if nm == "粉笔审题"]
    if not asks:
        asks = [i for i, _nm in secs]
    allsec = [i for i, _nm in secs]

    def _prev_sec(i):
        p = None
        for j, nm in secs:
            if j < i:
                p = (j, nm)
            else:
                break
        return p

    def _is_num(i):
        m = _PL_NUM_RE.match(lines[i])
        return bool(m and len(m.group(2).strip()) >= 8)

    # 分组：文档题号在「刷题练习N / 第N套」内从 1 循环，先把行号映射到它所属的最细分组
    grp_at, cur_grp = [], ""
    for ln in lines:
        gm = _PL_GRP_RE.match(ln)
        if gm:
            cur_grp = re.sub(r"[\s\u2003\x08]+", "", gm.group(1))
        grp_at.append(cur_grp)
    heads = []
    for i in asks:
        ps = _prev_sec(i)
        lo = ps[0] if ps else 0
        head, mode = None, ""
        for j in range(i - 1, lo, -1):
            if _is_num(j):
                head, mode = j, "num"
                break
        if head is None:
            for j in range(i + 1, min(i + 6, n)):
                if _is_num(j):
                    head, mode = j, "after"
                    break
        if head is None:
            for j in range(i - 1, lo, -1):
                if _PL_TAG_RE.match(lines[j]):
                    head, mode = j, "tag"
                    break
        heads.append([head, mode, (grp_at[head] if head is not None else cur_grp)])

    items = []
    for k, (head, mode, grp) in enumerate(heads):
        if head is None:
            continue
        i = asks[k]
        nxt = heads[k + 1][0] if k + 1 < len(heads) and heads[k + 1][0] is not None else n
        if mode == "after":
            later = [j for j in allsec if j > head]
            qidx = list(range(head, later[0] if later else n))
        else:
            qidx = list(range(head, i))
        qparas, qwrap = _pl_join_paras(lines, qidx)
        qt_parts = []
        for p in qparas:
            p = re.sub(r"^\d{1,3}\s*[.．、]\s*", "", p).strip()
            if p:
                qt_parts.append(p)
        s_idx = [j for j, _nm in secs if min(head, i) <= j < nxt]
        fields, order = {}, []
        for pos, si in enumerate(s_idx):
            end = s_idx[pos + 1] if pos + 1 < len(s_idx) else nxt
            nm = _PL_SEC_RE.match(lines[si]).group(1)
            if nm not in fields:
                fields[nm] = []
                order.append(nm)
            fields[nm].extend(range(si + 1, end))
        skip = set(qidx)
        tag = ""
        ps = _prev_sec(i)
        lo = ps[0] if ps else 0
        for j in range(i - 1, max(0, lo) - 1, -1):
            m = _PL_TAG_RE.match(lines[j])
            if m:
                tag = m.group(1).strip()
                break
        exam, w1 = _pl_join_paras(lines, fields.get("粉笔审题", []), skip)
        think, w2 = _pl_join_paras(lines, fields.get("粉笔思维", []), skip)
        ref, w3 = _pl_join_paras(lines, fields.get("解析示例", []), skip)
        if not ref:
            ref, w3 = _pl_join_paras(lines, fields.get("解析示例与参考答案", []), skip)
        ext, w4 = _pl_join_paras(lines, fields.get("粉笔拓展", []), skip)
        qtext = "\n".join(qt_parts).strip()
        if len(qtext) < 6:
            continue
        tq = _pl_qtype_from_tag(tag)
        dq = tq or _pl_qtype_from_decl(" ".join(exam[:2])) or _pl_qtype_from_decl(" ".join(ref[:1]))
        qtype, qsrc = (tq, "tag") if tq else ((dq, "decl") if dq else ("", ""))
        route = ""
        pool = [(pos, p) for pos, p in enumerate(exam) if _PL_ROUTE_MARK_RE.search(p)]
        if pool:
            pos0 = pool[0][0]
            route = exam[pos0]
            for p in exam[pos0 + 1:pos0 + 4]:
                if _PL_NEXT_RE.match(p) and len(route) < 460:
                    route += p
                else:
                    break
        if not route:
            cand = [p for p in exam + ref[:2] if len(_PL_STRONG_RE.findall(p)) >= 2]
            if cand:
                route = cand[0]
        pairs = _pl_stage_pairs(route)
        structure = [{"stage": nm, "label": "答题思路（文档原文）", "points": [body]}
                     for nm, body in pairs]
        keys = []
        for p in ext:
            parts = re.split(r"(?:^|\s)(\d{1,2})[.．、]\s*", " " + p)
            if len(parts) >= 3:
                keys.extend([x.strip() for x in parts[2:] if len(x.strip()) >= 4])
            elif len(p) >= 4:
                keys.append(p)
        keys = [x for x in keys if x][:12]
        cands = []
        if tag:
            cands.append(tag)
        dd = _pl_qtype_from_decl(" ".join(exam[:2]))
        if dd and dd not in cands:
            cands.append(dd)
        missing = []
        if not qtype:
            missing.append("题型（文档未标注）")
        if not exam:
            missing.append("审题要点")
        if not structure:
            missing.append("答题环节（文档未写答题思路）")
        if len("\n".join(ref).strip()) < 10:
            missing.append("参考要点")
        reason = ""
        if qsrc == "tag":
            reason = "文档自带标注：" + tag
        elif qsrc == "decl":
            reason = "文档正文声明：" + (exam[0][:40] if exam else "")
        items.append({
            "qno": (int(_PL_NUM_RE.match(lines[head]).group(1))
                    if mode != "tag" and _PL_NUM_RE.match(lines[head]) else None),
            "question": qtext,
            "qtype": qtype,
            "qtype_src": qsrc,
            "qtype_reason": reason,
            "qtype_candidates": cands,
            "exam": exam,
            "think": think,
            "structure": structure,
            "keys": keys,
            "ref": "\n".join(ref).strip(),
            "ext": ext,
            "sec_names": order,
            "doc_tag": tag,
            "doc_group": grp,
            "page_wrap": qwrap + w1 + w2 + w3 + w4,
            "missing": missing,
            "parse_src": "doc",
            "doc_faithful": True,
            "source": "陪练解析文档抽取",
        })
    # 同一题首行被重复切到的（同一题在下一步又被 FLB 锚住），保留字段更全的一条
    out, seen = [], {}
    for it in items:
        key = it["question"][:60]
        if key in seen:
            old = seen[key]
            score = len(it.get("exam") or []) + len(it.get("ref") or "") + len(it.get("structure") or []) * 2
            oscore = len(old.get("exam") or []) + len(old.get("ref") or "") + len(old.get("structure") or []) * 2
            if score > oscore:
                out[out.index(old)] = it
                seen[key] = it
            continue
        seen[key] = it
        out.append(it)
    return out


def _looks_like_question(t):
    """题干特征：以问号结尾，或含「请/你/怎么/如何/谈谈/组织」等指令疑问词"""
    t = (t or "").strip()
    if not t:
        return False
    if t.endswith(("？", "?", "吗", "呢")):
        return True
    return bool(re.search(r"(请问|怎么|如何|为什么|为何|谈谈|谈谈你|看法|理解|建议|组织|处理|你|请|是否|评价|分析|怎么看|怎么办)", t))


def _split_bank_blocks(text):
    """切分普通题库文本：题号需连续递增，且答案区里的「1./2./3.」条目不能被误判成新题"""
    blocks = []
    for chunk in re.split(r"\n\s*={3,}\s*\n", "\n" + (text or "").strip() + "\n"):
        cur, last_no, has_ref = [], 0, False
        for ln in chunk.splitlines():
            s = ln.strip()
            if not s:
                continue
            if REF_KEY_RE.match(s) or TYPE_KEY_RE.match(s):
                has_ref = True
            head = False
            first = (last_no == 0)
            m = re.match(r"^(\d{1,3})\s*[\.、．)）]\s*(\S.*)$", s)
            if m:
                n, t = int(m.group(1)), m.group(2).strip()
                if first and n >= 1 and len(t) >= 4:
                    head, last_no = True, n
                elif n == last_no + 1 and len(t) >= (15 if has_ref else 12) and (not has_ref or _looks_like_question(t)):
                    head, last_no = True, n
            else:
                mq = re.match(r"^第\s*(\d{1,3})\s*题", s)
                if mq:
                    head, last_no = True, int(mq.group(1))
                else:
                    mp = re.match(r"^[（(]\s*(\d{1,3})\s*[）)]\s*(\S.*)$", s)
                    if mp and int(mp.group(1)) == last_no + 1 and len(mp.group(2).strip()) >= 12:
                        head, last_no = True, int(mp.group(1))
            if head:
                if cur and not first:
                    blocks.append("\n".join(cur))
                    cur = []
                cur.append(s)
                has_ref = False
            else:
                cur.append(s)
        if cur:
            blocks.append("\n".join(cur))
    return [b for b in blocks if b.strip()]


def _parse_bank_text(text: str):
    """把纯文本 / Word 提取出的文本解析成 [{question, qtype, exam, structure, keys, ref}]"""
    if _pl_is_plain_fenbi(text):
        # 先于 _fb_norm_marks 判定：该函数会把「粉笔审题」改写成「【粉笔审题】」，
        # 改写后就看不出这是「无方括号字段标签」的陪练解析文档了
        plain_items = _pl_parse_text(text)
        if plain_items:
            return plain_items
    text = _fb_norm_marks(text)
    if _is_fenbi_text(text):
        items = _parse_fenbi_text(text)
        if items:
            return items
    items = []
    blocks = _split_bank_blocks(text)
    for b in blocks:
        b = (b or "").strip()
        if not b:
            continue
        lines = [ln.strip() for ln in b.splitlines() if ln.strip()]
        if not lines:
            continue
        q = Q_HEAD_RE.sub("", lines[0]).strip()
        ref_lines = []
        qtype = ""
        for ln in lines[1:]:
            m = REF_KEY_RE.match(ln)
            if m:
                ref_lines.append(m.group(3))
                continue
            m = TYPE_KEY_RE.match(ln)
            if m:
                qtype = m.group(3).strip()
                continue
            ref_lines.append(ln)
        if not q or len(q) < 4:
            continue
        items.append({"question": q, "qtype": qtype, "ref": "\n".join(ref_lines).strip()})
    return items


WNS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_lines(path):
    """不依赖 python-docx，直接从 docx 的 XML 抽段落与表格文本"""
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml")
    root = ET.fromstring(xml)
    body = root.find(WNS + "body")
    if body is None:
        return []

    def ptext(p):
        return "".join(t.text or "" for t in p.iter(WNS + "t")).strip()

    lines = []
    for el in body:
        tag = el.tag.replace(WNS, "")
        if tag == "p":
            t = ptext(el)
            if t:
                lines.append(t)
        elif tag == "tbl":
            for tr in el.findall(WNS + "tr"):
                cells = []
                for tc in tr.findall(WNS + "tc"):
                    cells.append(" ".join(x for x in (ptext(p) for p in tc.findall(WNS + "p")) if x).strip())
                row = [c for c in cells if c]
                if not row:
                    continue
                if len(row) == 1:
                    lines.append(row[0])
                elif len(row[0]) >= 12:
                    # 首列是长题干：题干另起一行，其余列作为要点
                    lines.append(Q_HEAD_RE.sub("", row[0]))
                    for c in row[1:]:
                        lines.append(c)
                else:
                    lines.append(" | ".join(row))
    return lines


_PDF_PAGENO_RE = re.compile(r"^\s*(?:第\s*\d{1,4}\s*页(?:\s*[/／共]\s*\d{1,4}\s*页?)?|\d{1,4}\s*[/／]\s*\d{1,4}|\d{1,4})\s*$")


def _read_pdf_text(path, stat=None):
    """读 PDF 文本（需要 PyMuPDF）：逐页抽文本、清页眉页脚页码、无文字层时明确提示需要 OCR"""
    try:
        import pymupdf as fitz  # PyMuPDF >= 1.24
    except Exception:
        try:
            import fitz
        except Exception:
            raise RuntimeError("读取 PDF 需要 PyMuPDF，请先 pip install pymupdf，或把 PDF 另存为 .docx 再导入")
    out, pages, chars, blank = [], 0, 0, 0
    with fitz.open(path) as doc:
        for page in doc:
            pages += 1
            t = page.get_text("text") or ""
            chars += len(t)
            if len(t.strip()) < 10:
                blank += 1
            ls = [ln.rstrip() for ln in t.splitlines()]
            while ls and not ls[0].strip():
                ls.pop(0)
            while ls and not ls[-1].strip():
                ls.pop()
            # 页眉 / 页脚里的纯页码会污染切题，只在首末行且本页还有其他内容时清掉
            if len(ls) > 1 and _PDF_PAGENO_RE.match(ls[0]):
                ls.pop(0)
            if len(ls) > 1 and _PDF_PAGENO_RE.match(ls[-1]):
                ls.pop()
            out.append("\n".join(ls))
    text = "\n".join(out)
    cjk = len(re.findall(r"[\u4e00-\u9fa5]", text))
    if stat is not None:
        stat.update({"pages": pages, "chars": chars, "cjk": cjk, "blank_pages": blank})
    if cjk < 30:
        raise RuntimeError("这个 PDF 里没有可提取的文字层（%d 页，仅识别到 %d 个汉字），属于扫描件/图片版，需要先用 OCR 转成文字版 PDF 或 .docx 后再导入" % (pages, cjk))
    return text


def _read_doc_text(path, ext, stat=None):
    ext = (ext or "").lower()
    if ext == ".docx":
        lines = _docx_lines(path)
        if stat is not None:
            stat.update({"pages": 0, "chars": sum(len(x) for x in lines), "cjk": len(re.findall(r"[\u4e00-\u9fa5]", "".join(lines)))})
        return "\n".join(lines)
    if ext == ".pdf":
        return _read_pdf_text(path, stat)
    if ext in (".txt", ".md", ".csv"):
        for enc in ("utf-8", "utf-8-sig", "gbk", "utf-16"):
            try:
                with open(path, "r", encoding=enc) as f:
                    txt = f.read()
                if stat is not None:
                    stat.update({"pages": 0, "chars": len(txt), "cjk": len(re.findall(r"[\u4e00-\u9fa5]", txt))})
                return txt
            except Exception:
                continue
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    raise RuntimeError("仅支持 .docx / .pdf / .txt / .md / .csv；.doc 请先在 Word 中另存为 .docx（或导出 PDF）再导入")


def _bank_path(fn):
    return os.path.join(BANK_DIR, os.path.basename(fn or ""))


def _as_str_list(v):
    """把字符串 / 列表统一成去空的字符串列表"""
    if isinstance(v, str):
        v = re.split(r"[\n]+", v)
    out = []
    for x in (v or []):
        s = str(x or "").strip()
        if s:
            out.append(s)
    return out


def _as_stage_list(v):
    """把题库条目的答题结构统一成 [{stage,label,points}]"""
    out = []
    for s in (v or []):
        if isinstance(s, dict):
            st = str(s.get("stage") or s.get("name") or "").strip()
            lb = str(s.get("label") or "").strip()
            pts = _as_str_list(s.get("points") or s.get("what") or [])
            if st or lb or pts:
                out.append({"stage": st, "label": lb, "points": pts})
        else:
            nm = str(s or "").strip()
            if nm:
                out.append({"stage": nm, "label": "", "points": []})
    return out


def _bank_write(name, items, merge=True):
    fn = re.sub(r"[\\/:*?\"<>|]", "_", (name or "未命名题库").strip()) + ".json"
    os.makedirs(BANK_DIR, exist_ok=True)
    path = os.path.join(BANK_DIR, fn)
    if merge and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                old = json.load(f)
            olds = old.get("items", [])
            # 同题干视为同一道题：用新解析结果覆盖旧条目，避免重复堆积或旧数据不更新
            upd = {(it.get("question") or "").strip(): it for it in items}
            items = [upd.pop((o.get("question") or "").strip(), o) for o in olds]
            items += [it for it in upd.values()]
        except Exception:
            pass
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"name": name, "items": items}, f, ensure_ascii=False, indent=2)
    return fn, items


@app.route("/api/bank/add_item", methods=["POST"])
def api_bank_add_item():
    """把（已确认/编辑过的）解析结果作为一个题目条目写入题库。
    bank=已有题库名 / new_bank=新建题库名（两者互斥，new_bank 优先），bank_mode 仅作前端回显。"""
    body = request.get_json(force=True, silent=True) or {}
    bank = (body.get("bank") or "").strip()
    new_bank = (body.get("new_bank") or "").strip()
    created = False
    if new_bank:
        bank, created = new_bank, True
    if not bank:
        return jsonify({"ok": False, "msg": "请选择要保存到的题库，或填写新题库名称"}), 400
    item = qa.normalize(body.get("item") or {})
    if not item or len(item.get("question") or "") < 4:
        return jsonify({"ok": False, "msg": "题干过短，无法入库"}), 400
    item["source"] = item.get("source") or ("single:new" if created else "single:exist")
    with _lock:
        if created:
            # 新建题库：不 merge，避免与同名文件的旧内容混在一起
            fn, items = _bank_write(bank, [item], merge=False)
        else:
            fn, items = _bank_write(bank, [item], merge=True)
    return jsonify({"ok": True, "file": fn, "bank": os.path.splitext(fn)[0],
                    "count": len(items), "created": created})


@app.route("/api/bank")
def api_bank():
    out = []
    if not os.path.isdir(BANK_DIR):
        # 题库不随软件内置：首次启动没有任何题库，前端据此显示空状态引导
        return jsonify({"banks": [], "empty": True})
    for fn in sorted(os.listdir(BANK_DIR)):
        if fn.endswith(".json"):
            try:
                with open(os.path.join(BANK_DIR, fn), "r", encoding="utf-8") as f:
                    data = json.load(f)
                out.append({"file": fn, "name": data.get("name", fn), "count": len(data.get("items", []))})
            except Exception:
                continue
    return jsonify({"banks": out, "empty": not out})


@app.route("/api/bank/load")
def api_bank_load():
    path = _bank_path(request.args.get("file", ""))
    if not os.path.exists(path):
        return jsonify({"ok": False, "msg": "题库不存在"}), 404
    with open(path, "r", encoding="utf-8") as f:
        return jsonify(json.load(f))


@app.route("/api/bank/import", methods=["POST"])
def api_bank_import():
    body = request.get_json(force=True, silent=True) or {}
    name = (body.get("name") or "未命名题库").strip()
    text = body.get("text", "")
    if not text.strip():
        return jsonify({"ok": False, "msg": "内容为空"}), 400
    if text.strip().startswith("["):
        try:
            items = json.loads(text)
        except Exception as e:
            return jsonify({"ok": False, "msg": f"JSON 解析失败: {e}"}), 400
    else:
        items = _parse_bank_text(text)
    if not items:
        return jsonify({"ok": False, "msg": "未解析出题目"}), 400
    fn, items = _bank_write(name, items, merge=bool(body.get("merge", True)))
    return jsonify({"ok": True, "file": fn, "count": len(items), "items": items})


@app.route("/api/bank/import_docx", methods=["POST"])
def api_bank_import_docx():
    """导入 Word（.docx）题库：支持 multipart 上传，也支持传本地绝对路径"""
    name = ""
    text = ""
    try:
        f = request.files.get("file")
        if f is not None:
            raw_name = f.filename or "题库.docx"
            ext = os.path.splitext(raw_name)[1].lower()
            if ext not in (".docx", ".pdf", ".txt", ".md", ".csv"):
                return jsonify({"ok": False, "msg": ".doc 老格式不支持，请在 Word 里另存为 .docx（或导出 PDF）再导入"}), 400
            name = (request.form.get("name") or os.path.splitext(os.path.basename(raw_name))[0]).strip()
            tmp = os.path.join(DATA, "_upload_" + uuid.uuid4().hex[:6] + ext)
            f.save(tmp)
            try:
                text = _read_doc_text(tmp, ext)
            finally:
                try:
                    os.remove(tmp)
                except Exception:
                    pass
        else:
            body = request.get_json(force=True, silent=True) or {}
            path = (body.get("path") or "").strip().strip('"')
            if not path or not os.path.exists(path):
                return jsonify({"ok": False, "msg": "文件不存在：" + (path or "（未提供路径）")}), 400
            ext = os.path.splitext(path)[1].lower()
            name = (body.get("name") or os.path.splitext(os.path.basename(path))[0]).strip()
            text = _read_doc_text(path, ext)
        if not (text or "").strip():
            return jsonify({"ok": False, "msg": "文档里没有读到文字"}), 400
        items = _parse_bank_text(text)
        if not items:
            return jsonify({"ok": False, "msg": "未识别出题目：每题请以「第 N 题」或「1. 」编号开头；答案可用「参考要点：」/「【参考要点】」引出，粉笔风格解析文档的【考点】【粉笔审题】【解析示例】会被自动识别"}), 400
        fn, items = _bank_write(name, items, merge=True)
        return jsonify({"ok": True, "file": fn, "count": len(items),
                        "parsed": len(items), "items": items})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "msg": str(e)}), 500


_BANK_IMPORT_EXT = (".docx", ".pdf", ".txt", ".md", ".csv")
_BANK_IMPORT_MAX_FILES = 12
_BANK_ANCHOR_HINT = ("没有识别到题目：每题需要以「第 N 题」开头（兼容「1.」「一、」「【考点】」等起始标记）；"
                     "粉笔解析类文档需含【考点】【粉笔审题】【解析示例】【备课要点】等标记")


def _bank_item_stats(it):
    """返回 (环节数, 要点数, 缺失字段列表)，用于预览卡片与入库前的完整性校验"""
    st = it.get("structure") or []
    pts = sum(len(s.get("points") or []) for s in st)
    miss = []
    if len((it.get("question") or "").strip()) < 6:
        miss.append("题干")
    if not (it.get("qtype") or "").strip():
        miss.append("题型")
    if not st:
        miss.append("答题环节")
    elif pts == 0:
        miss.append("环节要点")
    if not (it.get("exam") or []):
        miss.append("审题要点")
    if len((it.get("ref") or "").strip()) < 10:
        miss.append("参考要点")
    return len(st), pts, miss


def _bank_preview_row(it, seq, src_name, src_file, key):
    q = (it.get("question") or "").strip()
    stages, pts, miss = _bank_item_stats(it)
    qno = it.get("qno")
    # 来源标注必须与事实一致：只有真走通 AI 的题才算 AI 解析；
    # AI 因余额/超时/Key 失败而回退本地规则的，明确写清回退原因，绝不显示「AI 解析」
    src_kind = (it.get("parse_src") or "").strip() or ("doc" if (it.get("structure") and it.get("exam")) else "local")
    fr = (it.get("fail_reason") or "").strip()
    src_bad = False
    lf = it.get("local_filled") or []
    if src_kind == "ai":
        src_label = "AI 解析"
    elif src_kind == "doc":
        # 文档字段齐全、本地规则没再补东西，才算纯「文档自带」
        src_label = ("文档自带 + 本地补充 %d 项" % len(lf)) if lf else "文档自带"
    elif src_kind == "local" and (it.get("parse_mode") == "local"):
        src_label = "本地解析（按你的选择，未调用 AI）"
    else:
        kind = _imp_ai_err_kind(fr)
        if kind == "balance":
            src_label, src_bad = "本地解析（AI 余额不足已回退）", True
        elif kind == "auth":
            src_label, src_bad = "本地解析（API Key 无效已回退）", True
        elif kind in ("busy", "fail"):
            src_label, src_bad = "本地解析（AI 不可用已回退）", True
        elif kind == "off":
            src_label = "本地解析（未配置 API Key）"
        else:
            src_label = "本地解析"
    return {
        "id": "%s-%d" % (key, seq),
        "src": src_name,
        "file": src_file,
        "seq": seq,
        "qno": qno if qno else seq,
        "qtype": (it.get("qtype") or "").strip(),
        "qtype_reason": (it.get("qtype_reason") or "").strip(),
        "qtype_candidates": it.get("qtype_candidates") or [],
        "question": q,
        "q_short": (q[:56] + "…") if len(q) > 56 else q,
        "stages": stages,
        "points": pts,
        "exam_n": len(it.get("exam") or []),
        "keys_n": len(it.get("keys") or []),
        "ref_len": len((it.get("ref") or "").strip()),
        "missing": miss,
        "blank": "题干" in miss,
        "parse_src": src_kind,
        "src_label": src_label,
        "src_bad": src_bad,
        "ai_filled": it.get("ai_filled") or [],
        "local_filled": it.get("local_filled") or [],
        "fail_reason": fr,
        "item": it,
    }


# ---------------- 题库导入：AI 优先解析 + 后台进度任务 ----------------
_IMP_JOBS = {}
_IMP_LOCK = threading.Lock()
_IMP_KEEP = 6
_IMP_STAGES = ("读取文件", "提取文本", "切题", "AI 识别题型与结构", "生成预览")
# 并发不能一把拉到 10：服务端按账户余额给的并发上限可能只有 5~7，
# 超出会连续返回 429（Too many requests. Your current concurrency is N, which exceeds ... limit of M）
_IMP_AI_WORKERS = int(os.environ.get("IR_IMP_AI_WORKERS") or 6)
_IMP_AI_RETRY = 2
_IMP_AI_TIMEOUT = int(os.environ.get("IR_IMP_AI_TIMEOUT") or 120)   # 单题 AI 超时（秒），实测 deepseek-flash 单题约 45 秒
_IMP_AI_BUDGET = int(os.environ.get("IR_IMP_AI_BUDGET") or 1500)    # 整批 AI 阶段总预算（秒），超预算的题目回退本地规则

# AI 不可用原因归类：用于来源标注与进度卡提示（必须与事实一致，不得含糊成「AI 解析」）
_IMP_AI_STATE_TEXT = {
    "ok": "", "off": "未配置 API Key，全部按本地规则解析",
    "balance": "AI 余额不足（402），已回退本地规则",
    "auth": "AI API Key 无效，已回退本地规则",
    "busy": "AI 限流或超时，已回退本地规则",
    "fail": "AI 接口异常，已回退本地规则",
    "part": "部分题目 AI 解析成功，其余按本地规则解析",
}


# AI 并发闸门：遇到 429 自动收窄，成功后逐步放宽，避免整批撞并发上限
_IMP_AI_CONC = {"limit": _IMP_AI_WORKERS, "active": 0}
_IMP_AI_CONC_LOCK = threading.Lock()


def _imp_ai_slot():
    """按当前自适应并发上限抢占一个槽位，返回释放函数"""
    while True:
        with _IMP_AI_CONC_LOCK:
            if _IMP_AI_CONC["active"] < _IMP_AI_CONC["limit"]:
                _IMP_AI_CONC["active"] += 1
                break
        time.sleep(0.15)

    def release():
        with _IMP_AI_CONC_LOCK:
            if _IMP_AI_CONC["active"] > 0:
                _IMP_AI_CONC["active"] -= 1
    return release


def _imp_ai_ease():
    """撞到 429/并发超限：把并发上限收窄一档（不低于 1）"""
    with _IMP_AI_CONC_LOCK:
        if _IMP_AI_CONC["limit"] > 1:
            _IMP_AI_CONC["limit"] -= 1


def _imp_ai_widen():
    """请求成功：并发上限逐步放宽回配置值"""
    with _IMP_AI_CONC_LOCK:
        if _IMP_AI_CONC["limit"] < _IMP_AI_WORKERS:
            _IMP_AI_CONC["limit"] += 1


def _imp_ai_err_kind(msg):
    """把 AI 失败信息归成 balance / auth / busy / fail / off 五类；无信息返回空串"""
    s = str(msg or "").strip()
    if not s:
        return "off" if not _imp_ai_on() else ""
    low = s.lower()
    if "402" in s or "insufficient" in low or "balance" in low or "余额" in s:
        return "balance"
    if "401" in s or "403" in s or "invalid api" in low or "unauthor" in low or "api key" in low:
        return "auth"
    if "429" in s or "timeout" in low or "超时" in s or "限流" in s:
        return "busy"
    return "fail"


def _imp_ai_balance():
    """查 AI 账户余额（DeepSeek 有 /user/balance；其他厂商无此端点时返回 None 当未知处理）

    只用于把「余额到底还有多少钱」如实带给前端，不改变解析流程。
    """
    try:
        ai.reload_if_changed()
        key = (ai.cfg.get("api_key") or "").strip()
        base = (ai.cfg.get("base_url") or "").rstrip("/")
        if not key or not base:
            return None
        r = requests.get(base + "/user/balance",
                         headers={"Authorization": "Bearer " + key}, timeout=15)
        if r.status_code != 200:
            return None
        d = r.json()
        if not isinstance(d, dict) or "is_available" not in d:
            return None
        infos = d.get("balance_infos") or []
        cur = infos[0] if infos else {}
        return {"status": r.status_code, "is_available": bool(d.get("is_available")),
                "total": str(cur.get("total_balance") or ""), "currency": cur.get("currency") or ""}
    except Exception:
        return None


_IMP_PREF_PATH = os.path.join(DATA, "bank", "import_pref.json")
_IMP_PREF_DEFAULT = "ai"


def _imp_pref_get():
    """记住上次选的解析方式：ai（AI 解析）/ local（仅本地规则），失败一律回默认 ai"""
    try:
        with open(_IMP_PREF_PATH, "r", encoding="utf-8") as f:
            mode = (json.load(f) or {}).get("mode") or ""
        return mode if mode in ("ai", "local") else _IMP_PREF_DEFAULT
    except Exception:
        return _IMP_PREF_DEFAULT


def _imp_pref_set(mode):
    if mode not in ("ai", "local"):
        return False
    try:
        os.makedirs(os.path.dirname(_IMP_PREF_PATH), exist_ok=True)
        tmp = _IMP_PREF_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"mode": mode, "at": time.time()}, f, ensure_ascii=False)
        os.replace(tmp, _IMP_PREF_PATH)
        return True
    except Exception:
        return False


def _imp_ai_on():
    """AI 通道是否可用（有 Key 才走 AI，没有就纯本地，不阻塞进度）；读之前先按文件热更新配置"""
    try:
        ai.reload_if_changed()
        return bool((ai.cfg.get("api_key") or "").strip())
    except Exception:
        return False


def _imp_need_ai(it):
    """判断这道题是否需要 AI 补：缺题型 / 缺答题环节 / 缺审题要点 / 参考要点太短"""
    if not str(it.get("qtype") or "").strip():
        return True
    if len(it.get("structure") or []) < 2:
        return True
    if not _as_str_list(it.get("exam")):
        return True
    if len((it.get("ref") or "").strip()) < 30:
        return True
    return False


def _imp_note_state(job, it):
    """某题回退本地规则时，立刻把原因推到进度卡（运行中就标红，不留到结束才说）"""
    k = _imp_ai_err_kind(it.get("fail_reason"))
    if not k:
        return
    cur = (job.get("ai_state") or "")
    if cur and cur != "ok":
        return
    _imp_set(job, ai_state=k, ai_note=_IMP_AI_STATE_TEXT.get(k, ""))


def _imp_job_new(use_ai=None, mode=None):
    jid = uuid.uuid4().hex[:10]
    mode = mode if mode in ("ai", "local") else _imp_pref_get()
    ai_on = _imp_ai_on() if use_ai is None else bool(use_ai)
    job = {"jid": jid, "status": "queued", "stage": _IMP_STAGES[0], "pct": 0,
           "done": 0, "total": 0, "files": [], "rows": [], "error": "", "msg": "",
           "ai": ai_on, "mode": mode, "ai_fail": 0, "local": 0, "key": jid[:6],
           "ai_detail": None,
           "created": time.time(), "items": [], "updated": time.time()}
    with _IMP_LOCK:
        _IMP_JOBS[jid] = job
        if len(_IMP_JOBS) > _IMP_KEEP:
            for k in sorted(_IMP_JOBS, key=lambda x: _IMP_JOBS[x]["created"])[: len(_IMP_JOBS) - _IMP_KEEP]:
                _IMP_JOBS.pop(k, None)
    return job


def _imp_set(job, **kw):
    with _IMP_LOCK:
        job.update(kw)
        job["updated"] = time.time()


def _imp_snap(job, with_rows=True):
    with _IMP_LOCK:
        out = {k: job.get(k) for k in ("jid", "status", "stage", "pct", "done", "total",
                                       "files", "error", "msg", "ai", "mode", "ai_fail", "local",
                                       "ai_ok", "doc", "patched", "ai_state", "ai_note", "src_stat",
                                       "ai_detail")}
        out["rows"] = list(job.get("rows") or []) if with_rows else []
        out["row_count"] = len(job.get("rows") or [])
    out["stages"] = list(_IMP_STAGES)
    return out


def _imp_ai_text(it):
    """给 AI 的输入：题干 + 文档里已有的审题与解析（截断到模板上限 2000 字）"""
    parts = ["【题干】" + (it.get("question") or "").strip()]
    exam = _as_str_list(it.get("exam"))
    if exam:
        parts.append("【文档审题要点】" + "；".join(exam)[:620])
    ref = (it.get("ref") or "").strip()
    if ref:
        parts.append("【文档解析示例（节选）】" + ref[:760])
    keys = _as_str_list(it.get("keys"))
    if keys:
        parts.append("【文档拓展要点】" + "；".join(keys)[:260])
    return "\n".join(parts)[:2000]


def _imp_ai_fill(it):
    """调 AI 判题型 + 生成答题结构与关键要点（题型走题库里配置的题型环节）"""
    text = _imp_ai_text(it)
    qtype_name = _fb_qtype(str(it.get("qtype") or "")) or ""
    stages = _qtype_stage_names(qtype_name)
    defs = _qtype_stage_defs(qtype_name)
    cp, ft = _qtype_review_parts(qtype_name)
    last = ""
    for attempt in range(_IMP_AI_RETRY):
        raw = None
        release = _imp_ai_slot()   # 按自适应并发上限排队，避免撞账户并发上限触发 429
        try:
            raw = ai.analyze_question(text, qtype_stages=_qtype_stage_map(), timeout=_IMP_AI_TIMEOUT)
            if not (raw or "").strip():
                raise RuntimeError("AI 返回空内容")
            data = _qa_merge_qtype(text, raw, qtype_name=qtype_name)
            if data.get("source") == "ai":
                _imp_ai_widen()
                return data
            last = data.get("ai_error") or "AI 返回内容不可用"
        except Exception as e:
            last = str(e)
            if "429" in last:
                _imp_ai_ease()   # 并发超限：立刻收窄并发
        finally:
            release()
        # 可重试：限流 / 超时 / 返回内容不是合法 JSON（多为服务端偶发夹带说明文字或输出被截断）
        low = last.lower()
        retryable = ("429" in last) or ("超时" in last) or ("timeout" in low) or ("json" in low)
        if not retryable:
            break
        time.sleep(2.0 * (attempt + 1))   # 限流/偶发错误的退避：2s、4s
    raise RuntimeError(last or "AI 调用失败")


_QTYPE_ALIAS2 = {
    "综合分析": "社会现象", "观点分析": "态度观点", "现象分析": "社会现象",
    "情景模拟": "现场模拟", "情境模拟": "现场模拟", "模拟对话": "现场模拟",
    "演讲串词": "演讲", "串词": "串词题", "计划组织": "组织管理",
    "计划组织协调": "组织管理", "组织协调": "组织管理", "活动组织": "组织管理",
    "岗位匹配": "自我认知", "求职动机": "自我认知", "人际": "人际关系",
    "应变": "应急应变", "突发事件": "应急应变",
}
# 综合分析在题库里按「社会现象 / 态度观点」分开存，用题干特征再分一次
_QTYPE_OPINION_RE = re.compile(r"观点|有人说|有人认为|名言|讲话|启示|谈谈你的理解|如何理解|看法")


def _qtype_canon(name, question=""):
    """把 AI / 本地规则给的题型名收敛到题库的题型清单（社会现象、态度观点……）"""
    s = str(name or "").strip()
    if not s:
        return ""
    names = set(str(t.get("name") or "").strip() for t in _qtype_table())
    hit = _fb_qtype(s)
    if hit and hit in names:
        return hit
    if "综合分析" in s or "综合" in s:
        real = "社会现象" if _QTYPE_OPINION_RE.search(question or "") else "态度观点"
        pick = real if real in names else ("社会现象" if "社会现象" in names else "")
        if pick:
            return pick
    for k, v in _QTYPE_ALIAS2.items():
        if k in s:
            if v == "社会现象" and _QTYPE_OPINION_RE.search(question or ""):
                return "态度观点"
            return v if v in names else v
    for k in _QTYPE_ALIAS2.values():
        if k == s:
            return s
    return s


def _imp_merge(it, data):
    """AI 结果并入文档解析结果：文档有的字段优先保留，缺的用 AI 补齐，并记录补齐了哪些"""
    filled = []
    ai_qtype = _qtype_canon(data.get("qtype") or "", it.get("question") or "")
    if not ai_qtype:
        ai_qtype = str(data.get("qtype") or "").strip()
    if not (it.get("qtype") or "").strip() and ai_qtype:
        it["qtype"] = ai_qtype
        filled.append("题型")
    ai_st = data.get("structure") or []
    ai_pts = sum(len(s.get("points") or []) for s in ai_st)
    old_st = it.get("structure") or []
    if ai_st and ai_pts > 0 and (not old_st or len(ai_st) >= len(old_st)):
        it["structure"] = ai_st
        filled.append("答题环节" if not old_st else "答题环节（AI 重排）")
    old_keys = _as_str_list(it.get("keys"))
    add_keys = [k for k in (data.get("keys") or []) if k not in old_keys]
    if add_keys:
        it["keys"] = old_keys + add_keys
        filled.append("关键要点")
    if not _as_str_list(it.get("exam")) and data.get("exam"):
        it["exam"] = data["exam"]
        filled.append("审题要点")
    if len((it.get("ref") or "").strip()) < 10 and data.get("ref"):
        it["ref"] = data["ref"]
        filled.append("参考要点")
    reason = str(data.get("qtype_reason") or "").strip()
    if reason:
        old = (it.get("qtype_reason") or "").strip()
        it["qtype_reason"] = (old + "；AI：" + reason) if old and "AI：" not in old else ("AI：" + reason)
    cur = (it.get("qtype") or "").strip()
    if cur:
        can = _qtype_canon(cur, it.get("question") or "")
        if can and can != cur:
            it["qtype"] = can
            if "题型" not in filled:
                filled.append("题型（规范为题库题型）")
    it["parse_src"] = "ai"
    it["ai_filled"] = filled
    it["fail_reason"] = ""
    return it


def _imp_local_fill(it):
    """AI 不可用/失败时的本地兜底：题型按关键词规则判、答题环节按题型模板出，保证进度不卡住"""
    q = (it.get("question") or "").strip()
    qtype_name = _fb_qtype(str(it.get("qtype") or "")) or ""
    if not qtype_name:
        # 文档没给题型时先用关键词规则判一次，再按该题型取环节模板
        try:
            qtype_name = _qtype_canon(qa.detect_qtype(q)[0], q)
        except Exception:
            qtype_name = ""
    stages = _qtype_stage_names(qtype_name)
    defs = _qtype_stage_defs(qtype_name)
    cp, ft = _qtype_review_parts(qtype_name)
    try:
        data = qa.analyze_local(q, qtype_name=qtype_name, stages=stages,
                                stage_defs=defs, checkpoints=cp, faults=ft)
    except Exception:
        data = {}
    filled = []
    if not (it.get("qtype") or "").strip() and data.get("qtype"):
        it["qtype"] = _qtype_canon(data["qtype"], q) or data["qtype"]
        filled.append("题型")
    if not (it.get("structure") or []) and data.get("structure"):
        it["structure"] = data["structure"]
        filled.append("答题环节")
    if not _as_str_list(it.get("keys")) and data.get("keys"):
        it["keys"] = data["keys"]
        filled.append("关键要点")
    cur = (it.get("qtype") or "").strip()
    if cur:
        can = _qtype_canon(cur, q)
        if can and can != cur:
            it["qtype"] = can
    prev_src = (it.get("parse_src") or "").strip()
    it["parse_mode"] = "local"
    if prev_src == "doc":
        # 文档本来就带了字段：保留 doc 标记，本地规则补了什么单独记，避免把文档内容说成本地解析
        it["parse_src"] = "doc"
        it["local_filled"] = (it.get("local_filled") or []) + ["本地规则补：" + x for x in filled]
    else:
        it["parse_src"] = "local"
    it["ai_filled"] = filled
    return it


def _imp_local_patch_missing(it):
    """审题要点/参考要点缺失时按题干+题型的本地模板补齐；补不了的留给预览标「缺…」，不静默留空"""
    added = []
    q = (it.get("question") or "").strip()
    cur = (it.get("qtype") or "").strip()
    qt = _fb_qtype(cur) or _qtype_canon(cur, q) or cur
    if not _as_str_list(it.get("exam")):
        pts = []
        try:
            cp, _ft = _qtype_review_parts(qt)
            pts = [str(x).strip() for x in (cp or []) if str(x).strip()]
        except Exception:
            pts = []
        if pts:
            it["exam"] = pts[:5]
            added.append("审题要点（本地模板）")
    if len((it.get("ref") or "").strip()) < 10:
        lines = []
        for s in (it.get("structure") or []):
            nm = str(s.get("stage") or s.get("label") or s.get("name") or "").strip()
            ps = [str(x).strip() for x in (s.get("points") or []) if str(x).strip()]
            if ps:
                lines.append((nm + "：" if nm else "") + "；".join(ps))
        if not lines:
            ks = _as_str_list(it.get("keys"))
            if ks:
                lines = ["；".join(ks)]
        if lines:
            it["ref"] = "\n".join(lines)
            added.append("参考要点（本地模板）")
    if added:
        it["local_filled"] = (it.get("local_filled") or []) + added
        it["parse_src"] = it.get("parse_src") or "local"
    return added


def _imp_rows_of(job):
    rows = []
    for seq, it in enumerate(job.get("items") or [], 1):
        q = (it.get("question") or "").strip()
        if len(q) < 6:
            continue
        clean = {k: v for k, v in it.items() if not k.startswith("_")}
        r = _bank_preview_row(clean, seq, it.get("_src_name") or "", it.get("_src_file") or "", job["key"])
        r["item_index"] = seq - 1
        rows.append(r)
    return rows


def _imp_run(job, tasks):
    """后台解析：读取文件 → 提取文本 → 切题 → AI 识别题型与结构 → 生成预览（每步更新进度）"""
    try:
        _imp_set(job, status="running", stage=_IMP_STAGES[0], pct=2)
        files, texts = [], []
        n = max(1, len(tasks))
        for i, t in enumerate(tasks):
            info = {"name": t["name"], "file": t["file"], "ok": False, "count": 0,
                    "pages": 0, "chars": 0, "error": ""}
            files.append(info)
            _imp_set(job, files=list(files), stage=_IMP_STAGES[0], pct=2 + int(4 * (i + 1) / n))
            try:
                if t["ext"] not in _BANK_IMPORT_EXT:
                    raise RuntimeError(".doc 老格式不支持，请在 Word 里另存为 .docx（或导出 PDF）再导入")
                if not os.path.exists(t["path"]):
                    raise RuntimeError("文件不存在：" + t["path"])
                _imp_set(job, stage=_IMP_STAGES[1])
                st = {}
                text = _read_doc_text(t["path"], t["ext"], st)
                info["pages"], info["chars"] = st.get("pages", 0), st.get("chars", 0)
                if not (text or "").strip():
                    raise RuntimeError("文档里没有读到文字")
                texts.append((info, text))
                info["ok"] = True
            except Exception as e:
                info["error"] = str(e)[:300]
            finally:
                if t.get("tmp"):
                    try:
                        os.remove(t["path"])
                    except Exception:
                        pass
            _imp_set(job, pct=6 + int(9 * (i + 1) / n))
        _imp_set(job, stage=_IMP_STAGES[2], pct=16)
        items = []
        for i, (info, text) in enumerate(texts):
            try:
                its = _parse_bank_text(text)
            except Exception as e:
                traceback.print_exc()
                info["error"] = "解析失败：%s" % str(e)[:200]
                its = []
            for it in its:
                it["_src_name"] = info["name"]
                it["_src_file"] = info["file"]
            info["count"] = len(its)
            items.extend(its)
            _imp_set(job, pct=16 + int(10 * (i + 1) / max(1, len(texts))))
        if not items:
            errs = [f["error"] for f in files if f.get("error")]
            _imp_set(job, status="error", stage="切题", pct=100, files=files,
                     error=(errs[0] if errs else _BANK_ANCHOR_HINT))
            return
        _imp_set(job, items=items, total=len(items), done=0,
                 stage=_IMP_STAGES[3], pct=27)
        ai_on = bool(job.get("ai"))
        total = max(1, len(items))
        done = 0
        if ai_on:
            # 文档本身已给全（题型 + 答题环节 + 审题/参考/要点）的题目不必调 AI，省时间也省 token
            need = [it for it in items if _imp_need_ai(it)]
            need_ids = set(id(x) for x in need)
            for it in items:
                if id(it) not in need_ids:
                    it["parse_src"] = "doc"
                    it["ai_filled"] = []
                    done += 1
            if done:
                _imp_set(job, done=done, pct=27 + int(58 * done / total))
            # ---- 正式解析前先按「设置页同一套配置」真打一次接口：失败就把真实原因一次性说清，
            #      不再让 100+ 道题各自撞 3 次超时后才给一句「余额不足」----
            pre = ai.probe(timeout=25)
            detail = {"url": pre.get("url", ""), "model": pre.get("model", ""),
                      "base_url": pre.get("base_url", ""), "status": pre.get("status"),
                      "body": (pre.get("body") or "")[:800], "error": pre.get("error", ""),
                      "config_path": pre.get("config_path", ""),
                      "api_key_masked": pre.get("api_key_masked", ""),
                      "at": time.strftime("%H:%M:%S")}
            bal = _imp_ai_balance()
            if bal:
                detail["balance"] = bal
            _imp_set(job, ai_detail=detail)
            if bal and not bal.get("is_available") and need:
                # 余额为负/为 0 时服务端会大量返回 402，先把真实余额摆到进度卡上，别让用户猜
                _imp_set(job, ai_note=("AI 账户余额 %s %s（is_available=false），部分题目可能因 402 回退本地规则"
                                       % (bal.get("total") or "0", bal.get("currency") or ""))[:400])
            if need and not pre.get("ok"):
                k = _imp_ai_err_kind("%s %s" % (pre.get("status") or "", pre.get("body") or "")) or "fail"
                reason = "AI 预检失败：HTTP %s %s" % (pre.get("status"), (pre.get("body") or pre.get("error") or "")[:220])
                if not pre.get("status"):
                    reason = "AI 预检失败：%s" % (pre.get("error") or "接口不可达")[:240]
                for it in need:
                    it["fail_reason"] = reason
                    it["ai_err"] = reason
                    _imp_local_fill(it)
                    done += 1
                _imp_set(job, done=done, pct=27 + int(58 * done / total), ai_state=k,
                         ai_note=(_IMP_AI_STATE_TEXT.get(k, "") + "｜" + reason)[:400])
                need = []
            ex = ThreadPoolExecutor(max_workers=_IMP_AI_WORKERS)
            futs = {ex.submit(_imp_ai_fill, it): it for it in need}
            deadline = time.time() + _IMP_AI_BUDGET
            try:
                for fu in as_completed(futs, timeout=_IMP_AI_BUDGET):
                    it = futs[fu]
                    try:
                        _imp_merge(it, fu.result())
                    except Exception as e:
                        it["fail_reason"] = ("AI 解析失败：%s" % str(e)[:300])
                        it["ai_err"] = str(e)[:800]
                        _imp_local_fill(it)
                    _imp_note_state(job, it)
                    done += 1
                    _imp_set(job, done=done, pct=27 + int(58 * done / total))
            except Exception:
                pass
            # 超预算/超时未返回的题目：本地兜底，进度不卡住
            for fu, it in futs.items():
                if fu.done():
                    continue
                if it.get("parse_src") in ("ai", "local", "doc"):
                    continue
                it["fail_reason"] = ("AI 超时（%d 秒未返回），已按本地规则解析" % _IMP_AI_TIMEOUT
                                     if time.time() > deadline else "AI 未完成，已按本地规则解析")
                _imp_local_fill(it)
                _imp_note_state(job, it)
                done += 1
                _imp_set(job, done=done, pct=27 + int(58 * done / total))
            ex.shutdown(wait=False)
        else:
            off_note = ("已按你的选择只用本地解析规则（全程未发起 AI 请求）"
                        if job.get("mode") == "local" else _IMP_AI_STATE_TEXT["off"])
            _imp_set(job, ai_state="off", ai_note=off_note)
            for it in items:
                _imp_local_fill(it)
                done += 1
                _imp_set(job, done=done, pct=27 + int(58 * done / total))
        _imp_set(job, stage=_IMP_STAGES[4], pct=88)
        # 审题要点/参考要点缺失的题：先按题干+题型本地模板补齐，补不了的留给预览标注
        patched = 0
        # 仅本地模式：文档没写的字段就留空并标缺，不拿模板套壳编造内容
        for it in items:
            if job.get("mode") == "local":
                break
            if it.get("parse_src") == "ai" and not _imp_need_ai(it):
                continue
            if _imp_local_patch_missing(it):
                patched += 1
        rows = _imp_rows_of(job)
        ai_n = sum(1 for it in items if it.get("parse_src") == "ai")
        local_n = sum(1 for it in items if it.get("parse_src") == "local")
        doc_n = sum(1 for it in items if it.get("parse_src") == "doc")
        # AI 回退原因：取本地解析题里出现最多的失败归类，保证进度卡与预览口径一致
        kinds = {}
        for it in items:
            if it.get("parse_src") != "local":
                continue
            k = _imp_ai_err_kind(it.get("fail_reason"))
            if k:
                kinds[k] = kinds.get(k, 0) + 1
        ai_state = ""
        if not ai_on:
            ai_state = "off"
        elif ai_n and local_n:
            # AI 通道本身是通的，只是个别题回退：不能再报「AI 接口异常」，否则用户会误以为整批失败
            ai_state = "part"
        elif local_n:
            ai_state = max(kinds.items(), key=lambda kv: kv[1])[0] if kinds else "fail"
        elif ai_n or doc_n:
            ai_state = "ok"
        ai_note = _IMP_AI_STATE_TEXT.get(ai_state, "")
        if job.get("mode") == "local":
            ai_state = "off"
            ai_note = "已按你的选择只用本地解析规则，全程未发起 AI 请求"
        if ai_state == "part":
            k = max(kinds.items(), key=lambda kv: kv[1])[0] if kinds else ""
            why = _IMP_AI_STATE_TEXT.get(k, "") or ""
            why = why.replace("，已回退本地规则", "").replace("已回退本地规则", "").strip("，、 ")
            ai_note = "%s（本批 %d 题走 AI 成功，%d 题回退%s）" % (
                _IMP_AI_STATE_TEXT.get("part"), ai_n, local_n, ("：" + why) if why else "")
        # 失败时必须把真实请求地址/模型/状态码带出去，不许只留一句概括
        dl = job.get("ai_detail") or {}
        if ai_state in ("balance", "auth", "busy", "fail") and dl:
            ai_note = ("%s｜真实请求 %s（模型 %s）→ HTTP %s %s" % (
                ai_note or "AI 不可用", dl.get("url") or "-", dl.get("model") or "-",
                dl.get("status"), (dl.get("body") or dl.get("error") or "")[:160]))[:600]
        bal = (job.get("ai_detail") or {}).get("balance") or {}
        if bal and bal.get("is_available") is False:
            ai_note = ("%s｜账户余额 %s %s（接口返回 is_available=false，充值后重新导入即可全部走 AI）"
                       % (ai_note or "AI 不可用", bal.get("total") or "0",
                          bal.get("currency") or ""))[:600]
        if job.get("mode") == "local":
            sup_n = sum(1 for it in items if it.get("local_filled"))
            stat = '本地解析 %d 题（文档自带 %d 题，其中 %d 题由本地规则补字段），未调用 AI' % (
                doc_n + local_n, doc_n, sup_n)
        else:
            stat = 'AI 解析 %d 题 / 本地解析 %d 题 / 文档自带 %d 题' % (ai_n, local_n, doc_n)
        extra = ("；" + ai_note) if ai_note else ""
        _imp_set(job, rows=rows, files=files, pct=100, status="done", stage="完成",
                 local=local_n, ai_ok=ai_n, doc=doc_n, patched=patched,
                 ai_state=ai_state, ai_note=ai_note,
                 src_stat={"ai": ai_n, "local": local_n, "doc": doc_n},
                 ai_fail=(local_n if ai_on else 0),
                 msg="%d 题解析完成（%s）%s" % (len(rows), stat, extra))
    except Exception as e:
        traceback.print_exc()
        _imp_set(job, status="error", error=str(e)[:400], pct=100)


def _imp_collect_tasks():
    """收集待解析文件：multipart 上传（file/files/file[]）或 JSON 里的本地绝对路径"""
    tasks = []
    uploads = request.files.getlist("file") + request.files.getlist("files") + request.files.getlist("file[]")
    for f in uploads:
        raw = os.path.basename((f.filename or "").strip())
        if not raw:
            continue
        tasks.append({"file": raw, "name": os.path.splitext(raw)[0],
                      "ext": os.path.splitext(raw)[1].lower(),
                      "upload": f, "path": "", "tmp": False})
    if not tasks:
        body = request.get_json(force=True, silent=True) or {}
        for p in (body.get("paths") or []):
            p = str(p or "").strip().strip('"')
            if not p:
                continue
            tasks.append({"file": os.path.basename(p), "name": os.path.splitext(os.path.basename(p))[0],
                          "ext": os.path.splitext(p)[1].lower(), "upload": None, "path": p, "tmp": False})
        if body.get("path") and not tasks:
            p = str(body.get("path")).strip().strip('"')
            tasks.append({"file": os.path.basename(p), "name": os.path.splitext(os.path.basename(p))[0],
                          "ext": os.path.splitext(p)[1].lower(), "upload": None, "path": p, "tmp": False})
    return tasks[:_BANK_IMPORT_MAX_FILES]


@app.route("/api/bank/import_start", methods=["POST"])
def api_bank_import_start():
    """启动后台解析任务（AI 优先）：立刻返回 jid，前端用 /api/bank/import_job 轮询进度"""
    tasks = _imp_collect_tasks()
    if not tasks:
        return jsonify({"ok": False, "msg": "没有收到文件"}), 400
    # 解析方式由用户在导入对话框里选：local 时全程不发起任何 AI 请求；不传就沿用上次选择
    body = request.get_json(force=True, silent=True) or {}
    mode = (request.form.get("mode") or body.get("mode") or "").strip().lower()
    if mode not in ("ai", "local"):
        mode = _imp_pref_get()
    _imp_pref_set(mode)
    use_ai = _imp_ai_on() if mode == "ai" else False
    for t in tasks:
        if t["upload"] is not None:
            # 后台线程没有请求上下文，上传流必须先落到临时文件
            path = os.path.join(DATA, "_upload_" + uuid.uuid4().hex[:8] + t["ext"])
            t["upload"].save(path)
            t["path"], t["tmp"] = path, True
    job = _imp_job_new(use_ai=use_ai, mode=mode)
    threading.Thread(target=_imp_run, args=(job, tasks), daemon=True).start()
    return jsonify({"ok": True, "jid": job["jid"], "ai": job["ai"], "mode": mode,
                    "ai_key": _imp_ai_on(),
                    "files": len(tasks), "stages": list(_IMP_STAGES)})


@app.route("/api/bank/import_pref", methods=["GET", "POST"])
def api_bank_import_pref():
    """读取/保存「上次用的解析方式」，前端打开导入对话框时用它做默认选中"""
    mode = _imp_pref_get()
    if request.method == "POST":
        body = request.get_json(force=True, silent=True) or {}
        want = (body.get("mode") or request.form.get("mode") or "").strip().lower()
        if want in ("ai", "local"):
            _imp_pref_set(want)
            mode = want
    return jsonify({"ok": True, "mode": mode, "ai_key": _imp_ai_on()})


@app.route("/api/bank/import_job")
def api_bank_import_job():
    jid = (request.args.get("jid") or "").strip()
    with _IMP_LOCK:
        job = _IMP_JOBS.get(jid)
    if job is None:
        return jsonify({"ok": False, "msg": "任务不存在或已过期"}), 404
    out = _imp_snap(job)
    out["ok"] = True
    out["qtypes"] = _qtype_list_public()
    return jsonify(out)


@app.route("/api/bank/import_retry", methods=["POST"])
def api_bank_import_retry():
    """AI 解析失败的题单独重试：只补这一条，成功后原地替换预览行"""
    body = request.get_json(force=True, silent=True) or {}
    jid = (body.get("jid") or "").strip()
    with _IMP_LOCK:
        job = _IMP_JOBS.get(jid)
    if job is None:
        return jsonify({"ok": False, "msg": "任务不存在或已过期"}), 404
    want = set(str(x) for x in (body.get("ids") or []))
    items = job.get("items") or []
    done, failed = [], []
    if not _imp_ai_on():
        return jsonify({"ok": False, "msg": "未配置 API Key，无法用 AI 重试；当前结果已按本地规则解析"}), 400
    for idx, it in enumerate(items):
        rid = "%s-%d" % (job["key"], idx + 1)
        if want and rid not in want:
            continue
        try:
            _imp_merge(it, _imp_ai_fill(it))
            done.append(rid)
        except Exception as e:
            it["fail_reason"] = str(e)[:200]
            failed.append({"id": rid, "msg": str(e)[:200]})
    if not done and not failed:
        return jsonify({"ok": False, "msg": "没有匹配的题目"}), 400
    rows = _imp_rows_of(job)
    local = sum(1 for it in items if it.get("parse_src") == "local")
    _imp_set(job, rows=rows, local=local, ai_fail=local)
    return jsonify({"ok": True, "fixed": len(done), "failed": failed, "rows": rows})


@app.route("/api/bank/search")
def api_bank_search():
    """跨题库检索（主界面外层搜索用）：题干 / 题型 / 参考要点 / 关键要点都可命中"""
    kw = (request.args.get("q") or "").strip()
    try:
        limit = max(1, min(60, int(request.args.get("limit") or 20)))
    except Exception:
        limit = 20
    if not kw:
        return jsonify({"ok": True, "items": [], "total": 0, "scanned": 0, "kw": ""})
    low = kw.lower()
    hits, scanned = [], 0
    if os.path.isdir(BANK_DIR):
        for fn in sorted(os.listdir(BANK_DIR)):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(BANK_DIR, fn), "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            bname = data.get("name") or os.path.splitext(fn)[0]
            for i, it in enumerate(data.get("items") or []):
                scanned += 1
                q = (it.get("question") or "").strip()
                ty = (it.get("qtype") or "").strip()
                body = "\n".join([q, ty, it.get("ref") or "",
                                  " ".join(_as_str_list(it.get("keys"))),
                                  " ".join(_as_str_list(it.get("exam")))])
                if low not in body.lower():
                    continue
                if low in q.lower():
                    where = "题干"
                elif low in ty.lower():
                    where = "题型"
                elif low in " ".join(_as_str_list(it.get("exam"))).lower():
                    where = "审题要点"
                else:
                    where = "参考要点"
                hits.append({"file": fn, "bank": bname, "index": i, "qno": it.get("qno") or (i + 1),
                             "question": q, "qtype": ty, "where": where,
                             "source": "题库：" + bname})
    return jsonify({"ok": True, "items": hits[:limit], "total": len(hits),
                    "scanned": scanned, "kw": kw})


@app.route("/api/bank/import_preview", methods=["POST"])
def api_bank_import_preview():
    """多文件导入解析（不入库）：每道题返回一张预览卡片的数据，供前端勾选 / 改题型与题干后再确认入库"""
    tasks = []
    uploads = request.files.getlist("file") + request.files.getlist("files") + request.files.getlist("file[]")
    for f in uploads:
        raw = os.path.basename((f.filename or "").strip())
        if not raw:
            continue
        ext = os.path.splitext(raw)[1].lower()
        tasks.append({"file": raw, "name": os.path.splitext(raw)[0], "ext": ext,
                      "upload": f, "path": "", "tmp": False})
    if not tasks:
        body = request.get_json(force=True, silent=True) or {}
        for p in (body.get("paths") or []):
            p = str(p or "").strip().strip('"')
            tasks.append({"file": os.path.basename(p), "name": os.path.splitext(os.path.basename(p))[0],
                          "ext": os.path.splitext(p)[1].lower(), "upload": None, "path": p, "tmp": False})
        if body.get("path"):
            p = str(body.get("path")).strip().strip('"')
            tasks.append({"file": os.path.basename(p), "name": os.path.splitext(os.path.basename(p))[0],
                          "ext": os.path.splitext(p)[1].lower(), "upload": None, "path": p, "tmp": False})
    if not tasks:
        return jsonify({"ok": False, "msg": "没有收到文件"}), 400
    files, rows = [], []
    for t in tasks[: _BANK_IMPORT_MAX_FILES]:
        info = {"name": t["name"], "file": t["file"], "ok": False, "count": 0,
                "pages": 0, "chars": 0, "error": ""}
        files.append(info)
        if t["ext"] not in _BANK_IMPORT_EXT:
            info["error"] = ".doc 老格式不支持，请在 Word 里另存为 .docx（或导出 PDF）再导入"
            continue
        path = t["path"]
        if t["upload"] is not None:
            path = os.path.join(DATA, "_upload_" + uuid.uuid4().hex[:8] + t["ext"])
            t["upload"].save(path)
            t["tmp"] = True
        try:
            if not os.path.exists(path):
                raise RuntimeError("文件不存在：" + path)
            st = {}
            text = _read_doc_text(path, t["ext"], st)
            info["pages"], info["chars"] = st.get("pages", 0), st.get("chars", 0)
        except Exception as e:
            info["error"] = str(e)
            continue
        finally:
            if t["tmp"]:
                try:
                    os.remove(path)
                except Exception:
                    pass
        if not (text or "").strip():
            info["error"] = "文档里没有读到文字"
            continue
        try:
            items = _parse_bank_text(text)
        except Exception as e:
            traceback.print_exc()
            info["error"] = "解析失败：%s" % e
            continue
        if not items:
            info["error"] = _BANK_ANCHOR_HINT
            continue
        key = uuid.uuid4().hex[:6]
        n = 0
        for seq, it in enumerate(items, 1):
            if len((it.get("question") or "").strip()) < 6:
                continue  # 空壳题不产出
            _imp_local_patch_missing(it)      # 缺审题/参考要点时按本地模板补，补不上留给预览标注
            if not it.get("parse_src"):
                it["parse_src"] = "doc"        # 同步导入不调 AI，字段来自文档本身
            rows.append(_bank_preview_row(it, seq, t["name"], t["file"], key))
            n += 1
        info["ok"] = True
        info["count"] = n
    if len(tasks) > _BANK_IMPORT_MAX_FILES:
        files.append({"name": "其余 %d 个文件" % (len(tasks) - _BANK_IMPORT_MAX_FILES), "file": "",
                      "ok": False, "count": 0, "pages": 0, "chars": 0,
                      "error": "单次最多导入 %d 个文件，请分批导入" % _BANK_IMPORT_MAX_FILES})
    return jsonify({"ok": True, "files": files, "rows": rows, "total": len(rows),
                    "qtypes": _qtype_list_public()})


@app.route("/api/bank/import_commit", methods=["POST"])
def api_bank_import_commit():
    """把预览里勾选并编辑过的题目正式写入题库"""
    body = request.get_json(force=True, silent=True) or {}
    name = (body.get("name") or body.get("bank") or "").strip() or "未命名题库"
    raw = body.get("items") or []
    items, skipped = [], 0
    for x in raw:
        if not isinstance(x, dict):
            skipped += 1
            continue
        q = str(x.get("question") or "").strip()
        if len(q) < 4:
            skipped += 1
            continue
        items.append({
            "question": q,
            "qno": x.get("qno") or (len(items) + 1),
            "qtype": str(x.get("qtype") or "").strip(),
            "qtype_reason": str(x.get("qtype_reason") or "").strip(),
            "exam": _as_str_list(x.get("exam")),
            "structure": _as_stage_list(x.get("structure")),
            "keys": _as_str_list(x.get("keys")),
            "ref": str(x.get("ref") or "").strip(),
            "source": str(x.get("source") or "import:preview"),
            "parse_src": str(x.get("parse_src") or "doc"),
            "ai_filled": _as_str_list(x.get("ai_filled")),
        })
    if not items:
        return jsonify({"ok": False, "msg": "没有可入库的题目（题干都为空）"}), 400
    with _lock:
        fn, all_items = _bank_write(name, items, merge=bool(body.get("merge", True)))
    return jsonify({"ok": True, "file": fn, "bank": os.path.splitext(fn)[0],
                    "count": len(all_items), "added": len(items), "skipped": skipped})


@app.route("/api/bank/item/delete", methods=["POST"])
def api_bank_item_delete():
    """删除题库里的单道题（不是删整个题库文件）"""
    body = request.get_json(force=True, silent=True) or {}
    path = _bank_path(body.get("file", ""))
    if not os.path.exists(path):
        return jsonify({"ok": False, "msg": "题库不存在"}), 404
    idx = body.get("index")
    try:
        idx = int(idx)
    except Exception:
        return jsonify({"ok": False, "msg": "缺少题目序号"}), 400
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("items", [])
    if idx < 0 or idx >= len(items):
        return jsonify({"ok": False, "msg": "题目序号越界"}), 400
    removed = items.pop(idx)
    data["items"] = items
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return jsonify({"ok": True, "count": len(items), "removed": (removed.get("question") or "")[:40]})


@app.route("/api/bank/item/update", methods=["POST"])
def api_bank_item_update():
    body = request.get_json(force=True, silent=True) or {}
    path = _bank_path(body.get("file", ""))
    if not os.path.exists(path):
        return jsonify({"ok": False, "msg": "题库不存在"}), 404
    try:
        idx = int(body.get("index", -1))
    except Exception:
        return jsonify({"ok": False, "msg": "缺少题目序号"}), 400
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("items", [])
    if idx < 0 or idx >= len(items):
        return jsonify({"ok": False, "msg": "题目序号越界"}), 400
    for k in ("question", "qtype", "ref", "qtype_reason"):
        if k in body:
            items[idx][k] = str(body[k] or "").strip() if k != "ref" else str(body[k] or "")
    if "qno" in body:
        # 题号随原条目保留，编辑不改变它在题库里的位置与题号
        try:
            items[idx]["qno"] = int(body.get("qno"))
        except Exception:
            pass
    if "exam" in body:
        items[idx]["exam"] = _as_str_list(body.get("exam"))
    if "keys" in body:
        items[idx]["keys"] = _as_str_list(body.get("keys"))
    if "structure" in body:
        items[idx]["structure"] = _as_stage_list(body.get("structure"))
    data["items"] = items
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return jsonify({"ok": True, "count": len(items), "item": items[idx]})


@app.route("/api/bank/delete", methods=["POST"])
def api_bank_delete():
    body = request.get_json(force=True, silent=True) or {}
    path = _bank_path(body.get("file", ""))
    if os.path.exists(path):
        os.remove(path)
        return jsonify({"ok": True})
    return jsonify({"ok": False, "msg": "不存在"}), 404


@app.route("/api/bank/create", methods=["POST"])
def api_bank_create():
    """新建一个空题库（手机 / 平板端也能建库）"""
    body = request.get_json(force=True, silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "msg": "请填写题库名称"}), 400
    fn = re.sub(r"[\\/:*?\"<>|]", "_", name) + ".json"
    if os.path.exists(os.path.join(BANK_DIR, fn)):
        return jsonify({"ok": False, "msg": "题库「%s」已存在" % name}), 409
    fn, items = _bank_write(name, [], merge=False)
    return jsonify({"ok": True, "file": fn, "name": name, "count": len(items)})


@app.route("/api/bank/rename", methods=["POST"])
def api_bank_rename():
    """重命名题库：改文件名与内部 name 字段，题目内容原样保留"""
    body = request.get_json(force=True, silent=True) or {}
    old = _bank_path(body.get("file", ""))
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "msg": "请填写新的题库名称"}), 400
    if not os.path.exists(old):
        return jsonify({"ok": False, "msg": "题库不存在"}), 404
    new_fn = re.sub(r"[\\/:*?\"<>|]", "_", name) + ".json"
    new = os.path.join(BANK_DIR, new_fn)
    if os.path.abspath(new) != os.path.abspath(old) and os.path.exists(new):
        return jsonify({"ok": False, "msg": "已有同名题库「%s」" % name}), 409
    try:
        with open(old, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return jsonify({"ok": False, "msg": "题库读取失败：%s" % e}), 400
    data["name"] = name
    with open(new, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    if os.path.abspath(new) != os.path.abspath(old):
        try:
            os.remove(old)
        except Exception:
            pass
    return jsonify({"ok": True, "file": new_fn, "name": name, "count": len(data.get("items") or [])})


# ---------------- 题库：AI 优化（逐题 / 多题，可对比、可撤销） ----------------
_AIOPT_JOBS = {}
_AIOPT_LOCK = threading.Lock()
_AIOPT_KEEP = 6
# 一题一题串行走 AI（并发固定 1）：多题并发在推理型模型上会互相抢额度、排队反而更慢，
# 串行还能让「实时输出」按题目顺序稳定刷新。需要临时并发可用环境变量覆盖。
_AIOPT_WORKERS = max(1, int(os.environ.get("IR_AIOPT_WORKERS") or 1))
_AIOPT_TIMEOUT = int(os.environ.get("IR_AIOPT_TIMEOUT") or 120)
# 连通性探测结果短期复用：同一批优化里不必每批都真实打一次 /chat/completions
_AIOPT_PROBE_OK = {"t": 0.0, "detail": None}
_AIOPT_PROBE_TTL = 120


def _aiopt_snap(item):
    """把一道题的「优化前」原样存下来，供撤销回滚（只留参与优化的字段）"""
    return {"question": item.get("question") or "", "qtype": item.get("qtype") or "",
            "qtype_reason": item.get("qtype_reason") or "", "exam": list(item.get("exam") or []),
            "structure": json.loads(json.dumps(item.get("structure") or [], ensure_ascii=False)),
            "keys": list(item.get("keys") or []), "ref": item.get("ref") or "",
            "parse_src": item.get("parse_src") or "", "ai_filled": list(item.get("ai_filled") or []),
            "_keys": list(item.keys())}


def _aiopt_apply(dst, src):
    for k in ("question", "qtype", "qtype_reason", "ref", "parse_src"):
        if src.get(k) is not None:
            dst[k] = src.get(k)
    for k in ("exam", "keys", "structure", "ai_filled"):
        if src.get(k) is not None:
            dst[k] = src.get(k)


def _aiopt_restore(dst, snap):
    """撤销：把快照原样写回；优化时新增、原本不存在的字段一并删掉，做到逐字段还原"""
    _aiopt_apply(dst, snap)
    keep = snap.get("_keys")
    if isinstance(keep, list):
        for k in ("qtype", "qtype_reason", "ref", "exam", "structure", "keys", "parse_src", "ai_filled"):
            if k not in keep and k in dst:
                dst.pop(k, None)
    return dst


def _aiopt_one(item, qtype_stages, on_delta=None):
    """单题 AI 优化：题干规范 / 题型校准 / 审题要点 / 答题环节与要点 / 参考要点提质
    on_delta：流式回调，AI 每返回一段文字就回调一次，供前端实时看到返回内容"""
    t0 = time.time()
    q_preview = log_util.short((item or {}).get("question") or "", 60) or "（题干为空）"
    before = _aiopt_snap(item)
    try:
        raw = ai.optimize_question(item, qtype_stages=qtype_stages, timeout=_AIOPT_TIMEOUT,
                                   on_delta=on_delta)
    except Exception as e:
        log.warning("AI 优化单题失败（耗时 %.1fs）：%s: %s｜题干=%s",
                    time.time() - t0, type(e).__name__, str(e)[:300], q_preview)
        return {"ok": False, "before": before, "sec": round(time.time() - t0, 1),
                "msg": ("AI 优化失败：%s" % str(e))[:400], "raw_err": str(e)[:800]}
    new = None
    merged = {}
    parse_err = ""
    try:
        txt = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
        txt = txt.strip()
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", txt)
        if m:
            txt = m.group(1).strip()
        if not txt.startswith("{"):
            s, e = txt.find("{"), txt.rfind("}")
            if s >= 0 and e > s:
                txt = txt[s:e + 1]
        merged = json.loads(txt)
    except Exception as e:
        merged = {}
        parse_err = "%s: %s" % (type(e).__name__, str(e)[:200])
    if not isinstance(merged, dict):
        merged = {}
        parse_err = parse_err or "AI 返回的 JSON 顶层不是对象"
    q = (merged.get("question") or "").strip()
    if not q:
        tail = (raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)) or ""
        why = ("AI 返回不是可解析的 JSON（%s）" % parse_err) if parse_err else "AI 返回内容里没有题干"
        log.warning("AI 优化单题返回不可用（耗时 %.1fs）：%s｜题干=%s｜返回=%s",
                    time.time() - t0, why, q_preview, log_util.short(tail, 200))
        return {"ok": False, "before": before, "sec": round(time.time() - t0, 1),
                "msg": (why + "，未改动本题｜原始返回：" + tail.strip()[:300])[:500],
                "raw": tail[:6000], "raw_err": tail[:800]}
    patch = {"question": q}
    for k in ("qtype", "qtype_reason", "ref"):
        if (merged.get(k) or "").strip():
            patch[k] = merged.get(k).strip() if k != "ref" else merged.get(k)
    if merged.get("exam"):
        patch["exam"] = _as_str_list(merged.get("exam"))
    if merged.get("keys"):
        patch["keys"] = _as_str_list(merged.get("keys"))
    if merged.get("structure"):
        patch["structure"] = _as_stage_list(merged.get("structure"))
    patch["parse_src"] = "ai_opt"
    patch["ai_filled"] = [k for k in ("exam", "structure", "keys", "ref") if patch.get(k)]
    log.info("AI 优化单题成功（耗时 %.1fs）：题干=%s｜题型=%s｜填充=%s",
             time.time() - t0, log_util.short(q, 60), patch.get("qtype") or "-",
             ",".join(patch["ai_filled"]) or "无")
    return {"ok": True, "before": before, "patch": patch, "msg": "",
            "sec": round(time.time() - t0, 1),
            "raw": ((raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)) or "")[:6000]}


def _aiopt_run(job):
    path = job["path"]
    idxs = list(job["indices"])
    _jid = job.get("jid") or "-"
    log.info("AI 优化任务开始：jid=%s 文件=%s 题数=%d 并发=%d 单题超时=%ss",
             _jid, os.path.basename(path or ""), len(idxs), max(1, _AIOPT_WORKERS), _AIOPT_TIMEOUT)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("items", [])
        if not (ai.cfg.get("api_key") or "").strip():
            ai.reload_if_changed()
        if not (ai.cfg.get("api_key") or "").strip():
            job.update(status="done", state="off", done=0,
                       msg="未配置 API Key：请先在「设置」里填写接口地址、模型名与 Key 后再优化（本题未改动）")
            log.warning("AI 优化未执行：未配置 API Key（jid=%s）", _jid)
            return
        # 120 秒内探测成功过就直接复用，省掉一次真实请求（批量优化时能少等一轮）
        _pc = _AIOPT_PROBE_OK.get("detail")
        if _pc and (time.time() - float(_AIOPT_PROBE_OK.get("t") or 0)) < _AIOPT_PROBE_TTL:
            pre = dict(_pc)
            pre["cached"] = True
            log.info("AI 优化跳过连通性探测（复用 %ds 内的成功结果）：模型=%s 地址=%s",
                     _AIOPT_PROBE_TTL, pre.get("model") or "-", log_util.mask_secret(pre.get("url") or "-"))
        else:
            pre = ai.probe(timeout=25)
            if pre.get("ok"):
                _AIOPT_PROBE_OK["t"] = time.time()
                _AIOPT_PROBE_OK["detail"] = dict(pre)
        job["detail"] = {"url": pre.get("url"), "model": pre.get("model"), "status": pre.get("status"),
                         "body": (pre.get("body") or "")[:600], "error": pre.get("error") or "",
                         "cached": bool(pre.get("cached"))}
        if not pre.get("ok"):
            job.update(status="done", state="fail", done=0,
                       msg=("AI 接口不可用：真实请求 %s（模型 %s）→ HTTP %s %s"
                            % (pre.get("url") or "-", pre.get("model") or "-", pre.get("status"),
                               (pre.get("body") or pre.get("error") or "")[:300]))[:600])
            log.warning("AI 优化未执行：接口不可用（jid=%s）HTTP %s 模型=%s 地址=%s 原因=%s",
                        _jid, pre.get("status"), pre.get("model"),
                        log_util.mask_secret(pre.get("url") or "-"),
                        log_util.short(pre.get("body") or pre.get("error") or "", 300))
            return
        stages = _qtype_stage_map()
        pending = [i for i in idxs if 0 <= i < len(items)]
        hard = _AIOPT_TIMEOUT + 30          # 单题硬超时：等不到就判失败并继续下一题
        cancelled = False
        # 逐题串行：上一题返回后再发下一题（max_workers=1），既避开接口并发限流，
        # 也让进度条点开后能一题一题实时看到优化前后内容
        ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aiopt")
        try:
            for k, i in enumerate(pending, 1):
                if job.get("cancel"):
                    cancelled = True
                    break
                job["cur"] = i
                with _AIOPT_LOCK:
                    job.setdefault("live", {})[str(i)] = {
                        "no": i + 1, "index": i, "state": "run",
                        "t0": time.time(), "sec": 0,
                        "q": ((items[i] or {}).get("question") or "")[:160],
                        "qtype": (items[i] or {}).get("qtype") or "",
                        "before": _aiopt_snap(items[i]), "after": None, "raw": "", "msg": "",
                        "raw_len": 0, "streaming": False,
                    }
                job["updated"] = time.time()
                log.info("AI 优化第 %d/%d 题开始：jid=%s 题目序号=%d",
                         k, len(pending), _jid, i + 1)
                t_one = time.time()
                # 流式回调：AI 每吐出一段文字就写进这一题的 live 快照，
                # 前端轮询 live=1 时就能看到「AI 正在写」的实时文字
                def _on_delta(full, _i=i, _t=t_one):
                    with _AIOPT_LOCK:
                        _lv = (job.get("live") or {}).get(str(_i))
                        if _lv is not None and _lv.get("state") == "run":
                            _txt = full or ""
                            _lv["raw"] = _txt[-20000:]
                            _lv["raw_len"] = len(_txt)
                            _lv["streaming"] = True
                            _lv["sec"] = round(time.time() - _t, 1)
                    job["updated"] = time.time()
                fu = ex.submit(_aiopt_one, items[i], stages, _on_delta)
                done_one = False
                # 等待间隙每 0.25 秒查一次取消标记，保证「停止」能立刻生效
                while True:
                    if job.get("cancel"):
                        cancelled = True
                        break
                    try:
                        dn, _ = wait([fu], timeout=0.25, return_when=FIRST_COMPLETED)
                    except Exception:
                        break
                    if dn:
                        done_one = True
                        break
                    job["updated"] = time.time()
                    with _AIOPT_LOCK:
                        lv = (job.get("live") or {}).get(str(i))
                        if lv is not None and lv.get("state") == "run":
                            # 让前端看到这一题已经跑了多久
                            lv["sec"] = round(time.time() - t_one, 1)
                    if (time.time() - t_one) > hard:
                        break
                if cancelled:
                    try:
                        fu.cancel()
                    except Exception:
                        pass
                    with _AIOPT_LOCK:
                        lv = (job.get("live") or {}).get(str(i))
                        if lv is not None:
                            lv.update(state="skip", msg="已停止，本题未处理")
                            lv["sec"] = round(time.time() - t_one, 1)
                    log.info("AI 优化第 %d 题因取消未处理：jid=%s", i + 1, _jid)
                    break
                if done_one:
                    try:
                        r = fu.result()
                    except Exception as e:
                        log.error("AI 优化第 %d 题线程异常：jid=%s %s: %s\n%s",
                                  i + 1, _jid, type(e).__name__, e, traceback.format_exc())
                        r = {"ok": False, "msg": ("AI 优化异常：%s" % str(e))[:400]}
                else:
                    r = {"ok": False, "msg": ("单题等待超过 %d 秒仍未返回，已跳过（可单独重试）" % hard)}
                # 把这一题的「优化前 / 优化后 / 原始返回」写进 live，供进度条点开实时查看
                with _AIOPT_LOCK:
                    lv = (job.get("live") or {}).get(str(i))
                    if lv is not None:
                        lv["state"] = "ok" if r.get("ok") else "fail"
                        lv["sec"] = r.get("sec") or round(time.time() - float(lv.get("t0") or time.time()), 1)
                        lv["msg"] = r.get("msg") or ""
                        _full_raw = r.get("raw") or r.get("raw_err") or ""
                        lv["raw"] = _full_raw[:6000]
                        lv["raw_len"] = len(_full_raw)
                        lv["streaming"] = False
                        lv["after"] = r.get("patch") or None
                        lv["done_at"] = time.time()
                if r.get("ok"):
                    job["snap"][str(i)] = r.get("before")
                    _aiopt_apply(items[i], r["patch"])
                    job["ok_n"] += 1
                    job.setdefault("rows", []).append({"index": i, "ok": True, "msg": "已优化"})
                    log.info("AI 优化第 %d 题成功：jid=%s 累计成功 %d/%d",
                             i + 1, _jid, job["ok_n"], job.get("total") or len(pending))
                else:
                    job["fail_n"] += 1
                    job.setdefault("rows", []).append({"index": i, "ok": False, "msg": r.get("msg") or "失败"})
                    if done_one:
                        log.warning("AI 优化第 %d 题失败：jid=%s 原因=%s｜题干=%s",
                                    i + 1, _jid, log_util.short(r.get("msg") or "失败", 200),
                                    log_util.short((items[i] or {}).get("question") or "", 60))
                    else:
                        log.warning("AI 优化第 %d 题超时（>%ds）：jid=%s 题干=%s",
                                    i + 1, hard, _jid,
                                    log_util.short((items[i] or {}).get("question") or "", 60))
                job["done"] += 1
                job["updated"] = time.time()
                # 每题落盘，中途关窗口或点「停止」都不会丢已优化结果
                try:
                    data["items"] = items
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass
        finally:
            try:
                ex.shutdown(wait=False)
            except Exception:
                pass
        if cancelled:
            left = max(0, int(job.get("total") or 0) - int(job.get("done") or 0))
            job.update(state="cancel", status="done",
                       msg=("已停止：成功 %d 题，未处理 %d 题" % (job["ok_n"], left)))
            log.info("AI 优化任务已取消：jid=%s 成功 %d 题，未处理 %d 题，耗时 %.1fs",
                     _jid, job["ok_n"], left, time.time() - (job.get("t0") or time.time()))
        else:
            job["state"] = "ok" if job["ok_n"] else "fail"
            job["msg"] = ("已优化 %d 题%s" % (job["ok_n"], ("，失败 %d 题（可单独重试）" % job["fail_n"]) if job["fail_n"] else ""))
            job["status"] = "done"
            log.info("AI 优化任务结束：jid=%s 成功 %d / 失败 %d / 共 %d，耗时 %.1fs",
                     _jid, job["ok_n"], job["fail_n"], job.get("total") or 0,
                     time.time() - (job.get("t0") or time.time()))
    except Exception as e:
        job.update(status="done", state="fail", msg=("AI 优化异常：%s" % str(e))[:400])
        log.error("AI 优化任务异常：jid=%s %s: %s\n%s", _jid, type(e).__name__, e,
                  traceback.format_exc())
    finally:
        job["updated"] = time.time()


@app.route("/api/bank/ai_optimize", methods=["POST"])
def api_bank_ai_optimize():
    """启动后台 AI 优化任务：indices 为空表示优化整个题库"""
    body = request.get_json(force=True, silent=True) or {}
    path = _bank_path(body.get("file", ""))
    if not os.path.exists(path):
        return jsonify({"ok": False, "msg": "题库不存在"}), 404
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return jsonify({"ok": False, "msg": "题库读取失败：%s" % e}), 400
    items = data.get("items", [])
    raw_idx = body.get("indices")
    if raw_idx:
        idxs = []
        for x in raw_idx:
            try:
                x = int(x)
            except Exception:
                continue
            if 0 <= x < len(items) and x not in idxs:
                idxs.append(x)
    else:
        idxs = list(range(len(items)))
    if not idxs:
        return jsonify({"ok": False, "msg": "没有可优化的题目"}), 400
    jid = uuid.uuid4().hex[:12]
    job = {"jid": jid, "file": body.get("file"), "path": path, "indices": idxs,
           "status": "running", "state": "", "done": 0, "ok_n": 0, "fail_n": 0,
           "total": len(idxs), "msg": "", "rows": [], "snap": {}, "detail": None,
           "cancel": False, "cancel_at": 0, "t0": time.time(),
           "created": time.time(), "updated": time.time()}
    with _AIOPT_LOCK:
        _AIOPT_JOBS[jid] = job
        old = sorted(_AIOPT_JOBS.values(), key=lambda j: j["created"])
        while len(old) > _AIOPT_KEEP:
            dead = old.pop(0)
            _AIOPT_JOBS.pop(dead["jid"], None)
    threading.Thread(target=_aiopt_run, args=(job,), daemon=True, name="aiopt-%s" % jid).start()
    return jsonify({"ok": True, "jid": jid, "total": len(idxs)})


def _aiopt_live_slim(x):
    """把一道题的快照压成前端展示需要的字段，避免轮询 payload 过大"""
    if not isinstance(x, dict):
        return None
    stages = []
    for s in (x.get("structure") or []):
        if not isinstance(s, dict):
            continue
        stages.append({"stage": s.get("stage") or "", "label": s.get("label") or "",
                       "points": [str(p) for p in (s.get("points") or [])][:8]})
    return {"question": x.get("question") or "", "qtype": x.get("qtype") or "",
            "qtype_reason": x.get("qtype_reason") or "",
            "exam": [str(p) for p in (x.get("exam") or [])][:10],
            "structure": stages,
            "keys": [str(p) for p in (x.get("keys") or [])][:10],
            "ref": x.get("ref") or ""}


def _aiopt_live_view(job, raw_max=6000):
    """实时输出视图：按题目顺序给出「排队 / 进行中 / 完成 / 失败 / 未处理」与前后内容"""
    idxs = list(job.get("indices") or [])
    live = job.get("live") or {}
    raw_max = max(0, int(raw_max or 0))
    rows = []
    for pos, i in enumerate(idxs):
        lv = live.get(str(i))
        if not lv:
            rows.append({"no": pos + 1, "index": i, "state": "wait", "sec": 0, "q": "", "qtype": "",
                         "msg": "", "raw": "", "raw_len": 0, "raw_cut": False, "streaming": False,
                         "before": None, "after": None})
            continue
        _raw = lv.get("raw") or ""
        # 流式时保留「尾部」：AI 正在写的是最后那一段，截头不截尾才能看到实时内容
        _shown = _raw[-raw_max:] if raw_max else ""
        rows.append({"no": lv.get("no") or (pos + 1), "index": i, "state": lv.get("state") or "wait",
                     "sec": lv.get("sec") or 0, "q": lv.get("q") or "", "qtype": lv.get("qtype") or "",
                     "msg": (lv.get("msg") or "")[:600],
                     "raw": _shown, "raw_len": lv.get("raw_len") or len(_raw),
                     "raw_cut": len(_shown) < len(_raw),
                     "streaming": bool(lv.get("streaming")),
                     "before": _aiopt_live_slim(lv.get("before")),
                     "after": _aiopt_live_slim(lv.get("after"))})
    return rows


@app.route("/api/bank/ai_optimize_job")
def api_bank_ai_optimize_job():
    jid = request.args.get("jid", "")
    want_live = str(request.args.get("live") or "").lower() in ("1", "true", "yes", "on")
    with _AIOPT_LOCK:
        job = _AIOPT_JOBS.get(jid)
        if not job:
            return jsonify({"ok": False, "msg": "任务不存在或已过期"}), 404
        out = {k: job.get(k) for k in ("jid", "status", "state", "done", "ok_n", "fail_n",
                                       "total", "msg", "rows", "detail")}
        out["cancel"] = bool(job.get("cancel"))
        out["workers"] = _AIOPT_WORKERS
        cur = job.get("cur")
        out["cur"] = cur
        out["cur_no"] = (list(job.get("indices") or []).index(cur) + 1) if cur in (job.get("indices") or []) else 0
        if want_live:
            try:
                _rawmax = int(request.args.get("rawmax") or 6000)
            except Exception:
                _rawmax = 6000
            out["live"] = _aiopt_live_view(job, max(0, min(20000, _rawmax)))
        _t0 = job.get("t0") or job.get("created") or time.time()
        _end = job.get("updated") if job.get("status") != "running" else time.time()
        out["elapsed"] = round(max(0.0, float(_end or time.time()) - float(_t0)), 1)
    out["ok"] = True
    return jsonify(out)


@app.route("/api/bank/ai_optimize_cancel", methods=["POST"])
def api_bank_ai_optimize_cancel():
    """立即停止正在跑的 AI 优化任务：只置取消标记并马上返回，后台线程在等待间隙自行退出"""
    body = request.get_json(force=True, silent=True) or {}
    jid = body.get("jid", "") or request.args.get("jid", "")
    with _AIOPT_LOCK:
        job = _AIOPT_JOBS.get(jid)
        if not job:
            return jsonify({"ok": False, "msg": "任务不存在或已过期"}), 404
        job["cancel"] = True
        job["cancel_at"] = time.time()
        job["updated"] = time.time()
        snap = {"jid": jid, "status": job.get("status"), "state": job.get("state"),
                "done": job.get("done") or 0, "total": job.get("total") or 0,
                "ok_n": job.get("ok_n") or 0, "fail_n": job.get("fail_n") or 0}
    return jsonify(dict(snap, ok=True, msg="已发送停止指令，正在收尾…"))


@app.route("/api/bank/ai_optimize_undo", methods=["POST"])
def api_bank_ai_optimize_undo():
    """一键撤销：按任务里保存的「优化前」快照回滚（可只回滚指定题号）"""
    body = request.get_json(force=True, silent=True) or {}
    jid = body.get("jid", "")
    with _AIOPT_LOCK:
        job = _AIOPT_JOBS.get(jid)
    if not job:
        return jsonify({"ok": False, "msg": "任务不存在或已过期，无法撤销"}), 404
    path = job["path"]
    if not os.path.exists(path):
        return jsonify({"ok": False, "msg": "题库文件已被移动或删除，无法撤销"}), 404
    want = body.get("indices")
    keys = [str(int(x)) for x in want] if want else list(job.get("snap") or {})
    if not keys:
        return jsonify({"ok": True, "restored": 0, "msg": "该任务没有可撤销的改动（已回滚过或未成功优化）"})
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return jsonify({"ok": False, "msg": "题库读取失败：%s" % e}), 400
    items = data.get("items", [])
    n = 0
    for k in keys:
        try:
            i = int(k)
        except Exception:
            continue
        snap = (job.get("snap") or {}).get(str(i))
        if not snap or not (0 <= i < len(items)):
            continue
        _aiopt_restore(items[i], snap)
        n += 1
    data["items"] = items
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    # 已回滚的题清掉快照，避免二次撤销把内容又「还原」回优化后（防止误操作）
    with _AIOPT_LOCK:
        snap_map = job.get("snap") or {}
        for k in keys:
            snap_map.pop(k, None)
        job["snap"] = snap_map
        job["updated"] = time.time()
    return jsonify({"ok": True, "restored": n})


@app.route("/api/bank/ai_optimize_last")
def api_bank_ai_optimize_last():
    """最近一次该题库的优化记录（刷新页面后仍能看前后对比 / 撤销）"""
    fn = request.args.get("file", "")
    try:
        path = _bank_path(fn)
    except Exception:
        return jsonify({"ok": False, "msg": "题库名不合法"}), 400
    with _AIOPT_LOCK:
        cands = [j for j in _AIOPT_JOBS.values() if j.get("path") == path and (j.get("snap") or {})]
        job = max(cands, key=lambda j: j.get("created") or 0) if cands else None
        if not job:
            return jsonify({"ok": True, "jid": "", "snap": {}})
        out = {"jid": job.get("jid"), "status": job.get("status"), "ok_n": job.get("ok_n") or 0,
               "fail_n": job.get("fail_n") or 0, "total": job.get("total") or 0,
               "rows": job.get("rows") or [],
               "snap": {k: v for k, v in (job.get("snap") or {}).items()}}
    out["ok"] = True
    return jsonify(out)


# ---------------- 应用信息 ----------------
@app.route("/api/appinfo")
def api_appinfo():
    return jsonify({
        "name": APP_NAME,
        "author": APP_AUTHOR,
        "version": APP_VERSION,
        "data_dir": DATA,
        "records_dir": REC_DIR,
        "frozen": IS_FROZEN,
        "model_dir": MODEL_DIR,
        "asr_model": os.path.basename(_model_file(MODEL_DIR)),
        "punct_model": PUNCT_MODEL,
        "punct_ready": bool(getattr(engine.punct, "ok", False)),
        "partial_ui": True,
        "project_url": PROJECT_URL,
        "github_repo": GITHUB_REPO,
    })


# ---------------- 版本更新检查 ----------------
def _ver_key(v):
    """把 2.6.0 / v2.6.0 之类的版本串转成可比较的元组（非数字部分忽略）"""
    nums = re.findall(r"\d+", str(v or ""))
    return tuple(int(x) for x in nums[:4]) if nums else ()


@app.route("/api/update/check")
def api_update_check():
    """查询 GitHub 最新 Release，与本地 APP_VERSION 比较。

    约定：网络异常、限流、仓库无 Release 等情况一律返回 ok=false + msg，
    前端只做提示，不影响软件其它功能。
    """
    cur = APP_VERSION
    headers = {
        "User-Agent": "%s/%s" % (GITHUB_REPO, cur),
        "Accept": "application/vnd.github+json",
    }
    try:
        r = requests.get(GITHUB_API + "/releases/latest", headers=headers, timeout=UPDATE_TIMEOUT)
    except requests.exceptions.Timeout:
        return jsonify({"ok": False, "current": cur, "msg": "连接 GitHub 超时（%d 秒），请稍后重试" % UPDATE_TIMEOUT})
    except Exception as e:
        return jsonify({"ok": False, "current": cur, "msg": "网络不可用：%s" % e})
    try:
        if r.status_code == 404:
            return jsonify({"ok": False, "current": cur, "msg": "该仓库暂无 Release 记录"})
        if r.status_code in (403, 429):
            return jsonify({"ok": False, "current": cur, "msg": "GitHub 接口访问受限（限流），请稍后再试"})
        if r.status_code != 200:
            return jsonify({"ok": False, "current": cur, "msg": "GitHub 返回状态码 %s" % r.status_code})
        d = r.json() or {}
    except Exception as e:
        return jsonify({"ok": False, "current": cur, "msg": "解析 GitHub 返回失败：%s" % e})

    latest = str(d.get("tag_name") or d.get("name") or "").strip().lstrip("vV")
    if not latest:
        return jsonify({"ok": False, "current": cur, "msg": "未能从 GitHub 返回中读取版本号"})
    notes = str(d.get("body") or "").strip()
    if len(notes) > 600:
        notes = notes[:600] + "…"
    return jsonify({
        "ok": True,
        "current": cur,
        "latest": latest,
        "has_update": bool(_ver_key(latest) > _ver_key(cur)),
        "url": str(d.get("html_url") or (PROJECT_URL + "/releases/latest")),
        "published_at": str(d.get("published_at") or ""),
        "name": str(d.get("name") or ""),
        "notes": notes,
    })


@app.route("/api/open/url", methods=["POST"])
def api_open_url():
    """用系统默认浏览器打开外链（仅 http/https + 域名白名单）"""
    body = request.get_json(force=True, silent=True) or {}
    url = str(body.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "msg": "缺少 url 参数"}), 400
    try:
        from urllib.parse import urlparse
        u = urlparse(url)
    except Exception:
        return jsonify({"ok": False, "msg": "链接格式不合法"}), 400
    if u.scheme not in ("http", "https") or not u.hostname:
        return jsonify({"ok": False, "msg": "仅支持 http/https 链接"}), 400
    if u.hostname.lower() not in OPEN_URL_HOSTS:
        return jsonify({"ok": False, "msg": "该域名不在允许打开的白名单内"}), 400
    try:
        if os.name == "nt":
            os.startfile(url)
        else:
            import webbrowser
            webbrowser.open(url)
        return jsonify({"ok": True, "url": url})
    except Exception as e:
        return jsonify({"ok": False, "msg": "打开浏览器失败：%s" % e}), 500


# ---------------- AI 点评 ----------------
@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "GET":
        d = ai.public_config()
        d.update(ai.source())
        d["data_dir"] = DATA
        return jsonify(d)
    body = request.get_json(force=True, silent=True) or {}
    ai.save(body)
    ai.load()   # 保存后立刻按新配置生效，不需要重启
    d = ai.public_config()
    d.update(ai.source())
    d["data_dir"] = DATA
    return jsonify({"ok": True, "config": d})


@app.route("/api/config/test", methods=["POST"])
def api_config_test():
    """真实打一次接口：返回地址、模型、HTTP 状态码与原始返回正文，不掩盖失败原因"""
    r = ai.probe(timeout=25)
    r["ok_msg"] = ("接口可用：%s" % (r.get("content") or "")) if r.get("ok") else ""
    if not r.get("ok"):
        return jsonify(r), 200
    return jsonify(r)


@app.route("/api/ai/source")
def api_ai_source():
    """设置页/排查用：当前真正生效的配置来源与最近一次失败原文"""
    d = ai.source()
    d["data_dir"] = DATA
    d["last_error"] = ai.last_error
    d["last_req"] = ai.last_req
    return jsonify(d)


def _transcript_lines():
    lines = []
    for s in session["segments"]:
        mm = int(s["t_start"]) // 60
        ss = int(s["t_start"]) % 60
        tag = ""
        if s.get("tags"):
            tag = " 〔标记:" + "/".join(s["tags"]) + "〕"
        note = f" （讲师备注:{s['note']}）" if s.get("note") else ""
        lines.append(f"[{mm:02d}:{ss:02d}] {s['text']}{tag}{note}")
    return lines


@app.route("/api/review", methods=["POST"])
def api_review():
    if not session["segments"]:
        return jsonify({"ok": False, "msg": "还没有逐字稿内容"}), 400

    paras = session.get("paragraphs") or []
    if not paras:
        _rebuild_paragraphs(keep_last_open=False)
        paras = session.get("paragraphs") or []

    stage_names = _sync_stages()
    cp, ft = _qtype_review_parts(session.get("qtype"))
    local_md, metrics = local_analyzer.analyze(
        session["question"], session["segments"], session["duration"], paras,
        stages=stage_names, checkpoints=cp, faults=ft)
    mode, md, err = "local", local_md, ""

    if ai.public_config()["has_key"]:
        try:
            ai_md = ai.review(session["question"], session["qtype"], session["ref"],
                              _transcript_lines(), session["duration"],
                              pz.render_for_ai(paras),
                              stage_names=stage_names, checkpoints=cp, faults=ft)
            if ai_md and ai_md.strip():
                md = ai_md
                mode = "ai"
            else:
                err = "AI 返回内容为空，已回退本地诊断"
                md = local_md
        except Exception as e:
            err = str(e)
            md = local_md

    session["review"] = md
    session["review_mode"] = mode
    persist()
    return jsonify({"ok": True, "review": md, "mode": mode, "error": err, "metrics": metrics})


@app.route("/api/quick", methods=["POST"])
def api_quick():
    body = request.get_json(force=True, silent=True) or {}
    sid = body.get("id")
    seg = next((s for s in session["segments"] if s["id"] == sid), None)
    if not seg:
        return jsonify({"ok": False, "msg": "未找到该句"}), 404
    try:
        note = ai.quick_sentence(session["question"], seg["text"])
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500
    seg["ai_note"] = note.strip()
    persist()
    return jsonify({"ok": True, "ai_note": seg["ai_note"]})


@app.route("/api/paragraph/quick", methods=["POST"])
def api_paragraph_quick():
    """按段落点评：只评这一段"""
    body = request.get_json(force=True, silent=True) or {}
    para = _find_para(body.get("id"))
    if not para:
        return jsonify({"ok": False, "msg": "未找到该段落"}), 404
    if not (para.get("text") or "").strip():
        return jsonify({"ok": False, "msg": "该段落还没有内容"}), 400
    try:
        cp, ft = _qtype_review_parts(session.get("qtype"))
        txt = ai.paragraph_review(
            session["question"], session["qtype"], para.get("idx", 1), para["text"],
            para.get("t_start", 0), para.get("t_end", 0), _prev_labels(para),
            stage_names=_stage_options(), checkpoints=cp, faults=ft)
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500
    label, body_txt = parse_paragraph_reply(txt)
    if not (body_txt or txt).strip():
        return jsonify({"ok": False, "msg": "AI 返回内容为空，请重试"}), 502
    if label:
        para["label"] = label
        para["title"] = label
    para["ai_note"] = body_txt or txt.strip()
    para["ai_raw"] = txt.strip()
    _apply_plain(para)                     # 标签更新后同步刷新结构环节
    persist()
    broadcast({"type": "paragraph", "para": para})
    broadcast(_plain_event(para))
    return jsonify({"ok": True, "para": para})


# ---------------- 精简版 ----------------
def _para_of_key(key):
    """按句子级编辑键反查所属段落（拆句 / 合并生成的新句不在逐字稿里）"""
    k = str(key or "").strip()
    if not k:
        return None
    for p in (session.get("paragraphs") or []):
        try:
            if any(x.get("sid") == k for x in _parts_final(p)):
                return p
        except Exception:
            continue
    return None


def _sent_stage_of(para, sid, seg_id=None, seg_map=None):
    """某一句当前实际归属的环节：句子级手动归属 > 逐字稿句上的手动归属 > 段落判定"""
    try:
        ed = _edits(para) or {}
        st = ed.get("stage") if isinstance(ed.get("stage"), dict) else {}
        hit = (st or {}).get(sid) or ""
        if hit:
            return hit
    except Exception:
        traceback.print_exc()
    sm = seg_map if seg_map is not None else _seg_map()
    seg = sm.get(seg_id or sid) or {}
    return ((seg.get("stage_manual") or {}).get("stage") or "")


def _plain_text_staged(paras=None, with_stage=True, seg_map=None):
    """按「每句实际归属的环节」拼整篇精简版

    改字 / 拆句 / 合并后文本取自编辑稿；句子被手动改归属后，
    复制与取稿都把它放到新环节下，不会仍留在原段落里。
    """
    paras = sorted([p for p in (paras if paras is not None else (session.get("paragraphs") or []))
                    if isinstance(p, dict)], key=lambda p: p.get("idx", 0))
    sm = seg_map if seg_map is not None else _seg_map()
    lines = []
    for p in paras:
        try:
            parts = _parts_final(p)
        except Exception:
            traceback.print_exc()
            parts = []
        if not parts:
            t = (p.get("plain") or "").strip()
            if not t:
                continue
            parts = [{"sid": p.get("id") or "", "seg": None, "text": t, "edited": False}]
        if not with_stage:
            lines.append("".join(x.get("text") or "" for x in parts))
            continue
        base_stage = (p.get("plain_stage") or "").strip()
        base_label = (p.get("plain_label") or base_stage or "").strip()
        runs = []          # [[label, text], ...]
        for x in parts:
            stg = _sent_stage_of(p, x.get("sid"), x.get("seg"), sm)
            if stg and stg != base_stage:
                try:
                    label = simp.stage_label(stg, "") or stg
                except Exception:
                    label = stg
            else:
                label = base_label
            label = label or "未标注"
            if runs and runs[-1][0] == label:
                runs[-1][1] += x.get("text") or ""
            else:
                runs.append([label, x.get("text") or ""])
        for lab, t in runs:
            if (t or "").strip():
                lines.append("【%s】%s" % (lab, t))
    return "\n".join(lines)


@app.route("/api/plain/text")
def api_plain_text():
    """整篇精简版纯文字（供一键复制 / 导出）

    每次都按当前编辑稿实时重建，绝不返回初次解析时缓存下来的原文。
    """
    with_stage = str(request.args.get("stage", "1")) not in ("0", "false", "no")
    paras = session.get("paragraphs") or []
    if simp.pending_count(paras):
        _plain_all(paras)
    text = _plain_text_staged(paras, with_stage=with_stage)
    return jsonify({"ok": True, "text": text, "chars": len(text.replace("\n", "")),
                    "stamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "edit_seq": sum(int((_edits(p) or {}).get("seq") or 0) for p in paras),
                    "count": len([p for p in paras if (p.get("plain") or "").strip()])})


@app.route("/api/plain/correct", methods=["POST"])
def api_plain_correct():
    """段落智能纠错：body.pid 单段 / 不传 pid 则全篇；result 是否等 AI 返回"""
    body = request.get_json(force=True, silent=True) or {}
    pid = body.get("pid")
    wait = body.get("wait") is True
    paras = session.get("paragraphs") or []
    if pid:
        para = _find_para(pid)
        if not para:
            return jsonify({"ok": False, "msg": "未找到该段落"}), 404
        targets = [para["id"]]
    else:
        targets = [p["id"] for p in paras if (p.get("plain_parts") or [])]
    if not targets:
        return jsonify({"ok": False, "msg": "还没有可纠错的内容"}), 400
    if wait:
        res = [_ai_correct_para(t) for t in targets]
        ok = [r for r in res if r.get("ok")]
        return jsonify({"ok": bool(ok), "count": len(ok),
                        "msg": "" if ok else (res[0].get("msg") if res else "纠错失败")})
    for t in targets:
        threading.Thread(target=_ai_correct_para, args=(t,), daemon=True).start()
    return jsonify({"ok": True, "count": len(targets), "async": True})


# ---------------- 精简要点（学生作答 -> 答题要点关键词） ----------------
def _points_source(force=True, paras=None, seg_map=None):
    """「精简版」最新稿：每次取稿都按人工编辑稿重建，不返回初次解析缓存

    paras / seg_map 传入时按该组段落取稿（历史回看用），不传则取当前会话。
    """
    hist = paras is not None
    paras = paras if paras is not None else (session.get("paragraphs") or [])
    sm = seg_map if seg_map is not None else _seg_map()
    if force or simp.pending_count(paras) or any(
            isinstance((_edits(p) or {}).get("items"), list) for p in paras):
        try:
            _plain_all(paras, seg_map=sm)   # 含拆句 / 合并 / 改字 / 改归属后的最新结果
        except Exception:
            traceback.print_exc()
    for p in paras:                    # 兜底：确保每段 plain 与编辑稿一致
        if isinstance((_edits(p) or {}).get("items"), list):
            try:
                p["plain"] = _para_plain(p)
                _restage(p)
            except Exception:
                traceback.print_exc()
    txt = _plain_text_staged(paras, with_stage=True, seg_map=sm)
    if not txt.strip():
        segs = sm.values() if hist else (session.get("segments") or [])
        txt = "".join(str(s.get("text") or "") for s in segs).strip()
    return txt


def _points_blocks(paras=None, seg_map=None):
    """按「每句实际归属的环节」分组的取稿快照（供浮层核对是否取到最新稿）"""
    hist = paras is not None
    paras = paras if paras is not None else (session.get("paragraphs") or [])
    sm = seg_map if seg_map is not None else _seg_map()
    order, bucket = [], {}
    for p in sorted([x for x in paras if (x.get("plain") or "").strip()],
                    key=lambda p: p.get("idx", 0)):
        base = p.get("plain_stage") or PLAIN_NONE_STAGE
        for x in _parts_final(p):
            stg = _sent_stage_of(p, x["sid"], x.get("seg"), sm) or base
            if stg not in bucket:
                bucket[stg] = {"lines": [], "pids": []}
                order.append(stg)
            if x.get("text"):
                bucket[stg]["lines"].append(x["text"])
            if p.get("id") not in bucket[stg]["pids"]:
                bucket[stg]["pids"].append(p.get("id"))
    out = []
    for stage in order:
        lines = bucket[stage]["lines"]
        out.append({"stage": stage, "lines": lines,
                    "chars": len("".join(lines)), "paras": len(bucket[stage]["pids"])})
    return out


def _points_ctx():
    """题型 / 环节 / 参考要点 / 题干，取自当前会话"""
    qtype = session.get("qtype") or ""
    stages = _stage_options()
    ref = session.get("ref") or ""
    if not ref:
        ana = session.get("q_analysis") or {}
        ref = str(ana.get("ref") or "").strip()
    return qtype, stages, ref, session.get("question") or ""


def _points_ai_refine(text, qtype, stages, ref, question, local):
    """有 Key 时用 AI 优化关键词；任何异常都静默回退本地结果"""
    try:
        if not (ai.cfg.get("api_key") or "").strip():
            return None
        reply = ai.points(text, qtype=qtype, stages=stages, ref=ref, question=question)
        reply = str(reply or "").strip()
        if not reply or "【" not in reply:
            return None
        got = poi.analyze(reply, qtype=qtype, stages=stages, ref=ref, question=question)
        if len(got.get("stages") or []) < 1:
            got["stages"] = stages or got.get("stages") or []
        if sum(len(g.get("items") or []) for g in got.get("groups") or []) < 1:
            return None
        got["note"] = "已用 AI 优化关键词（本地规则校验）"
        got["ai"] = True
        return got
    except Exception as e:                      # noqa: BLE001
        print("[points] ai refine failed: %s" % e)
        return None


@app.route("/api/points/source", methods=["GET", "POST"])
def api_points_source():
    """浮层「取当前精简稿」：实时重建当前精简版（含拆句/合并/改字/改归属）"""
    txt = _points_source()
    qtype, stages, ref, question = _points_ctx()
    blocks = _points_blocks()
    body_chars = len(re.sub(r"【[^】]*】|\s", "", txt))
    return jsonify({"ok": True, "text": txt, "chars": len(txt), "body_chars": body_chars,
                    "stamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "edit_seq": sum(int((_edits(p) or {}).get("seq") or 0)
                                    for p in (session.get("paragraphs") or [])),
                    "paras": len([p for p in (session.get("paragraphs") or [])
                                  if (p.get("plain") or "").strip()]),
                    "blocks": blocks,
                    "qtype": qtype, "stages": stages, "ref": ref, "question": question,
                    "has_key": bool((ai.cfg.get("api_key") or "").strip())})


# ---------------- 精简要点：答到 / 偏题 / 遗漏 的 AI 判定 ----------------
_LINK_VALUES = ("完整", "部分", "缺失")
_JUDGE_SRC_NOTE = {"ai": "", "local": "本地判定", "local_fallback": "本地判定"}


def _strip_json_noise(txt):
    """剥掉代码块围栏与 JSON 前后的解释文字"""
    t = re.sub(r"```(?:json|JSON)?", "", str(txt or "")).replace("```", "")
    a, b = t.find("{"), t.rfind("}")
    if a >= 0 and b > a:
        t = t[a:b + 1]
    return t.strip()


def _pick_json(txt):
    """JSON 容错解析：先直解析，失败再剥离多余文本重试一次"""
    raw = str(txt or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        pass
    fixed = _strip_json_noise(raw)
    if not fixed:
        return None
    try:
        return json.loads(fixed)               # 剥离后重试一次
    except Exception:
        return None


def _points_students(out):
    """按 S1…S n 顺序收集考生要点（跳过「遗漏」参考组），返回[(sid, group, item)]"""
    rows = []
    for g in out.get("groups") or []:
        if g.get("miss"):
            continue
        for it in g.get("items") or []:
            t = str(it.get("text") or "").strip()
            if not t:
                continue
            rows.append(("S%d" % (len(rows) + 1), g, it))
    return rows


def _points_ai_judge(out, qtype, stages, question):
    """把考生要点与参考要点一起交给 AI 逐条判定；返回 (payload, 原因码)"""
    if not (ai.cfg.get("api_key") or "").strip():
        return None, "no_key"
    refs = [str(x).strip() for x in (out.get("ref_points") or []) if str(x).strip()]
    rows = _points_students(out)
    if not refs or not rows:
        return None, "no_data"
    timeout = float(ai.cfg.get("points_judge_timeout", 25) or 25)
    try:
        reply = ai.judge_points(refs, [r[2]["text"] for r in rows], qtype=qtype,
                                stages=stages, question=question, timeout=timeout)
    except Exception as e:                     # noqa: BLE001
        msg = "%s" % e
        print("[points] ai judge failed: %s" % msg)
        low = msg.lower()
        return None, ("timeout" if ("timeout" in low or "timed out" in low) else "error")
    data = _pick_json(reply)
    if not isinstance(data, dict) or not isinstance(data.get("items"), list) or not data["items"]:
        return None, "parse"
    return {"data": data, "rows": rows, "refs": refs}, ""


def _apply_ai_judge(out, box):
    """把 AI 判定写回要点状态，并按参考要点重算覆盖率 / 漏答 / 偏题清单"""
    data, rows, refs = box["data"], box["rows"], box["refs"]
    sid_of = {r[0]: r[2] for r in rows}
    items = [x for x in data.get("items") or [] if isinstance(x, dict)]
    off_map = {}
    for x in data.get("off") or []:
        if isinstance(x, dict):
            for sid in re.findall(r"S\d+", str(x.get("sid") or "").upper()):
                off_map[sid] = str(x.get("reason") or "").strip()

    judge_rows, missed, link_stat, hit_sids = [], [], {"完整": 0, "部分": 0, "缺失": 0}, set()
    for idx, rk in enumerate(refs):
        rid = "R%d" % (idx + 1)
        got = None
        for x in items:
            if str(x.get("id") or "").strip().upper() == rid:
                got = x
                break
        if got is None and idx < len(items):     # AI 漏写或错写 id 时按顺序兜底
            got = items[idx]
        hit = bool(got.get("hit")) if isinstance(got, dict) else False
        raw_sid = str((got or {}).get("sid") or "").strip().upper()
        # 允许多个编号（如「S3、S4 共同覆盖」），只保留真实存在的考生要点编号
        sid_list = [x for x in re.findall(r"S\d+", raw_sid) if x in sid_of]
        sid = sid_list[0] if sid_list else ""
        if hit and not sid_list:                # 答到却没指向任何真实考生要点时视为未答到
            hit = False
        link = str((got or {}).get("link") or "").strip()
        if link not in _LINK_VALUES:
            link = "完整" if hit else "缺失"
        if not hit:
            link = "缺失"
        elif link == "缺失":
            link = "部分"
        link_stat[link] += 1
        if hit:
            for _sd in sid_list:
                hit_sids.add(_sd)
        else:
            missed.append(rk)
        judge_rows.append({"id": rid, "text": rk, "hit": hit,
                           "sid": "/".join(sid_list) if sid_list else "",
                           "reason": str((got or {}).get("reason") or "").strip(),
                           "link": link})

    # 学生要点状态以 AI 结论为准：命中→答到，跑题→偏题，其余不给徽标
    off_texts = []
    for sid, _g, it in rows:
        if sid in hit_sids:
            it["status"], it["by"] = poi.STATUS_HIT, "ai"
        elif sid in off_map:
            it["status"], it["by"] = poi.STATUS_OFF, "ai"
            it["why"] = off_map[sid]
            off_texts.append(it["text"])
        else:
            it["status"], it["by"] = "", ""

    total = len(refs)
    # 覆盖率口径：参考要点被答到的条数 / 参考要点总数（分子分母同一口径）
    hit_refs = len([r for r in judge_rows if r.get("hit")])
    cov = int(round(hit_refs * 100.0 / total)) if total else None
    groups = [g for g in (out.get("groups") or []) if not g.get("miss")]
    groups.append({"stage": "参考未答到（遗漏）", "role": "", "miss": True,
                   "items": [{"text": r["text"], "status": poi.STATUS_MISS, "by": "ai"}
                             for r in judge_rows if not r["hit"]]})
    out["groups"] = groups
    note = "AI 判定｜覆盖率 %d%%（%d/%d）｜漏答 %d 条｜偏题 %d 条｜链路 完整 %d/部分 %d/缺失 %d" % (
        cov or 0, len(hit_sids), total, len(missed), len(off_texts),
        link_stat["完整"], link_stat["部分"], link_stat["缺失"])
    summary = str(data.get("summary") or "").strip()
    if summary:
        note += "｜%s" % summary
    out["align"] = {"ok": True, "ref_total": total, "ref_hit": hit_refs,
                    "coverage": cov, "missed": missed, "off": off_texts,
                    "note": note, "source": "ai", "rows": judge_rows,
                    "link_stat": link_stat, "summary": summary}
    st = out.setdefault("stats", {})
    st.update({"hit": len(hit_sids), "off": len(off_texts), "miss": len(missed),
               "ref_total": total, "ref_hit": len(hit_sids), "coverage": cov})
    out["note"] = note
    return out


_FALLBACK_MSG = {
    "timeout": "AI 判定超时",
    "error": "AI 判定报错",
    "parse": "AI 返回不是合法 JSON",
    "no_data": "缺少参考要点或考生要点",
}


def _mark_judge_local(out, reason):
    """回退本地判定：只在 align 上标注来源与原因，不改动任何本地结果"""
    align = dict(out.get("align") or {})
    align["source"] = "local"
    base = align.get("note") or ""
    if reason == "no_key":
        tip = "本地判定（未配置 API Key）"
    elif reason == "off":
        tip = "本地判定（本次未启用 AI 判定）"
    else:
        tip = "本地判定（%s，已静默回退本地关键词匹配）" % _FALLBACK_MSG.get(reason, "AI 不可用")
    align["note"] = ("%s｜%s" % (tip, base)) if base else tip
    align["fallback"] = reason
    out["align"] = align
    out["note"] = align["note"]
    return out


@app.route("/api/points/analyze", methods=["POST"])
def api_points_analyze():
    """分析学生答题要点：本地规则优先，可选 AI 优化（失败静默回退）"""
    body = request.get_json(force=True, silent=True) or {}
    text = str(body.get("text") or "").strip()
    if not text:
        text = _points_source()
    qtype, stages, ref, question = _points_ctx()
    qtype = str(body.get("qtype") or qtype or "")
    if isinstance(body.get("stages"), list):
        stages = [str(x).strip() for x in body["stages"] if str(x).strip()] or stages
    if "ref" in body:
        ref = str(body.get("ref") or "")
    try:
        max_len = int(body.get("max_len") or 20)
    except Exception:
        max_len = 20
    max_len = max(8, min(40, max_len))

    local = poi.analyze(text, qtype=qtype, stages=stages, ref=ref, question=question, max_len=max_len)
    out = local
    if body.get("ai") is True:
        got = _points_ai_refine(text, qtype, stages, ref, question, local)
        if got:
            out = got
    out = dict(out)
    # 「答到 / 偏题 / 遗漏」改由 AI 逐条判定；任何异常静默回退本地关键词匹配
    out["judge_source"] = "local"
    if body.get("ai") is True:
        box, why = _points_ai_judge(out, qtype, stages, question)
        if box:
            out = _apply_ai_judge(out, box)
            out["judge_source"] = "ai"
        else:
            out = _mark_judge_local(out, why)
            print("[points] judge fallback -> %s" % why)
    else:
        out = _mark_judge_local(out, "off")
    # 对齐标注可开关：带标注 / 不带标注两套复制文本都备好，前端按开关直接取
    want_status = body.get("with_status", True)
    if isinstance(want_status, str):
        want_status = want_status.strip().lower() not in ("0", "false", "no", "off", "")
    want_status = bool(want_status)
    out["plain"] = poi.format_text(out, "plain", with_status=want_status)
    out["bullet"] = poi.format_text(out, "bullet", with_status=want_status)
    out["plain_bare"] = poi.format_text(out, "plain", with_status=False)
    out["bullet_bare"] = poi.format_text(out, "bullet", with_status=False)
    out["with_status"] = want_status
    out["source"] = "ai" if out.get("ai") else "local"
    out["qtype"] = qtype
    out["stages"] = stages
    align = out.get("align") or {}
    out["coverage"] = align.get("coverage")
    out["ref_total"] = align.get("ref_total") or 0
    out["ref_hit"] = align.get("ref_hit") or 0
    out["missed"] = align.get("missed") or []
    out["off"] = align.get("off") or []
    out["align_note"] = align.get("note") or ""
    out["judge_rows"] = align.get("rows") or []
    out["judge_link"] = align.get("link_stat") or {}
    out["judge_fallback"] = align.get("fallback") or ""
    out["judge_source_note"] = {"ai": "AI 判定"}.get(out["judge_source"], "本地判定")
    return jsonify(out)


# ---------------- 段落点评校准：自检与手动触发 ----------------
@app.route("/api/paragraph/review_state", methods=["POST"])
def api_para_review_state():
    """某一段会不会被重复点评 / 需不需要合并评价（不改数据，只回判据）"""
    body = request.get_json(force=True, silent=True) or {}
    pid = str(body.get("pid") or "")
    out = {"ok": True, "stats": rv_stats()}
    para = _find_para(pid) if pid else None
    if para is None:
        out["ok"] = False
        out["msg"] = "未找到该段落"
        return jsonify(out), 404
    mode, prev, sim = para_review_mode(para)
    out.update({
        "pid": pid,
        "idx": para.get("idx", 0),
        "mode": mode,
        "sim": round(sim, 3),
        "prev_pid": (prev or {}).get("id", ""),
        "prev_idx": (prev or {}).get("idx", 0),
        "stage": _para_stage_of(para),
        "prev_stage": _para_stage_of(prev or {}),
        "note": _RV_NOTE_DUP if mode == "dup" else (_RV_NOTE_MERGE if mode == "merge" else ""),
        "ai_note": para.get("ai_note") or "",
        "review_mode": para.get("review_mode") or "",
        "merge_with": para.get("merge_with") or "",
        "sig_same": bool(para.get("ai_sig")) and para.get("ai_sig") == _rv_sig(para),
    })
    return jsonify(out)


@app.route("/api/paragraph/review_run", methods=["POST"])
def api_para_review_run():
    """手动跑一次该段点评校准（dup/merge 不发 AI，正常段才请求 AI）"""
    body = request.get_json(force=True, silent=True) or {}
    pid = str(body.get("pid") or "")
    if not pid:
        return jsonify({"ok": False, "msg": "缺少 pid"}), 400
    if body.get("enqueue") is True:
        queued = enqueue_para_review(pid)
        return jsonify({"ok": True, "queued": queued, "stats": rv_stats()})
    _run_para_review(pid)
    para = _find_para(pid) or {}
    return jsonify({"ok": True, "stats": rv_stats(),
                    "ai_note": para.get("ai_note") or "",
                    "review_mode": para.get("review_mode") or ""})


# ---------------- 存档 ----------------
def _build_markdown_for(st, multi=False, only_current=False):
    """单个学生的听记 Markdown"""
    s = session
    segs = st.get("segments") or []
    paras = st.get("paragraphs") or []
    seg_map = {seg["id"]: seg for seg in segs}
    lines = []
    if multi:
        lines.append(f"## 学生：{st.get('name') or '未命名'}")
        lines.append("")
        lines.append(f"- 作答时长：{int(st.get('acc') or st.get('duration') or 0)} 秒")
        lines.append(f"- 句子数：{len(segs)} ／ 段落数：{len(paras)}")
        lines.append("")
    if not paras:
        for seg in segs:
            tag = "  `" + "/".join(seg["tags"]) + "`" if seg.get("tags") else ""
            lines.append(f"{pz.mmss(seg['t_start'])} {seg['text']}{tag}")
        if st.get("review"):
            lines.append("")
            lines.append("## 点评（时间 / 优点 / 问题）")
            lines.append(st["review"])
        _notes = [str(x).strip() for x in (st.get("notes") or []) if str(x).strip()]
        if _notes:
            lines.append("")
            lines.append("## 快速记录")
            lines.extend(f"{i}. {t.replace(chr(10), ' / ')}" for i, t in enumerate(_notes, 1))
        return lines
    lines.append("### 逐字稿（按段落计时）")
    for p in paras:
        sec = int(round((p.get("t_end") or 0) - (p.get("t_start") or 0)))
        lines.append("")
        lines.append(f"#### 第{p.get('idx')}段 · {p.get('label') or p.get('title') or '未命名'} ｜ "
                     f"{pz.span_text(p.get('t_start'), p.get('t_end'))} ｜ {sec} 秒 ｜ {p.get('chars', 0)} 字")
        if p.get("ai_note"):
            lines.append(f"> 段落点评：{p['ai_note'].replace(chr(10), ' / ')}")
        for sid in p.get("seg_ids") or []:
            seg = seg_map.get(sid)
            if not seg:
                continue
            lines.append("")
            mark = ""
            if seg.get("tags"):
                mark = "**〔" + "/".join(seg["tags"]) + "〕" + seg["text"] + "**"
            lines.append(f"- {pz.mmss(seg['t_start'])} {mark or seg['text']}")
            if seg.get("note"):
                lines.append(f"  - 备注：{seg['note']}")
            if seg.get("ai_note"):
                lines.append(f"  - AI：{seg['ai_note'].replace(chr(10), ' / ')}")
    if simp.pending_count(paras):
        # 存档可能属于别的题型：按其自身题型取环节，再还原当前会话的环节
        keep = list(simp.STAGES or [])
        try:
            simp.set_stages(_qtype_stage_names(st.get("qtype")) or list(QT_DEFAULT_TAIL))
            simp.apply_all(paras, seg_map=seg_map)
            for p in paras:
                _correct_local(p)
        except Exception:
            pass
        finally:
            try:
                simp.set_stages(keep or list(QT_DEFAULT_TAIL))
            except Exception:
                pass
    plain_lines = _plain_text_staged(paras, seg_map=seg_map)
    if plain_lines:
        lines.append("")
        lines.append("### 精简版（去冗余 · 智能纠错 · 按结构环节，无时间戳）")
        lines.append(plain_lines)
    problems = [seg for seg in segs if seg.get("tags") or seg.get("ctags") or seg.get("note")]
    if problems:
        lines.append("")
        lines.append("### 标记汇总")
        for seg in problems:
            tagstr = "/".join(list(seg.get("tags") or []) + ["问题：" + str(c) for c in (seg.get("ctags") or [])])
            lines.append(f"- {pz.mmss(seg['t_start'])} {tagstr} · {seg['text'][:60]}"
                         f"{'…' if len(seg['text']) > 60 else ''}"
                         + (f"（{seg['note']}）" if seg.get("note") else ""))
    if st.get("review"):
        lines.append("")
        lines.append("### 点评（时间 / 优点 / 问题）")
        lines.append(st["review"])
    notes = [str(x).strip() for x in (st.get("notes") or []) if str(x).strip()]
    if notes:
        lines.append("")
        lines.append("### 快速记录")
        lines.extend(f"{i}. {t.replace(chr(10), ' / ')}" for i, t in enumerate(notes, 1))
    return lines


def _build_markdown():
    s = session
    sts = _students()
    multi = len(sts) > 1
    cur = _cur_student()
    lines = []
    lines.append(f"# 面试作答听记 · {s['created_at']}")
    lines.append("")
    lines.append(f"- 场次编号：{s.get('id')}")
    if multi:
        lines.append(f"- 本场学生：{len(sts)} 位（{'、'.join(st.get('name') or '' for st in sts)}）")
    else:
        lines.append(f"- 考生：{cur.get('name') or s.get('candidate') or '未填写'}")
    lines.append(f"- 题目类型：{s.get('qtype') or '未标注'}")
    lines.append(f"- 本场总时长：{sum(int(st.get('acc') or st.get('duration') or 0) for st in sts)} 秒")
    lines.append("")
    lines.append("## 题目")
    lines.append(s.get("question") or "（未填写）")
    if s.get("ref"):
        lines.append("")
        lines.append("## 参考要点")
        lines.append(s["ref"])
    lines.append("")
    if multi:
        lines.append("## 多位学生作答")
        for st in sts:
            lines.append("")
            lines.extend(_build_markdown_for(st, multi=True))
    else:
        lines.append("## 逐字稿（按段落计时）")
        lines.extend(_build_markdown_for(cur or sts[0]))
    return "\n".join(lines)


def _save_record():
    base = f"{session['id']}"
    md_path = os.path.join(REC_DIR, base + ".md")
    js_path = os.path.join(REC_DIR, base + ".json")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(_build_markdown())
    with open(js_path, "w", encoding="utf-8") as f:
        json.dump(session, f, ensure_ascii=False, indent=2)
    return md_path


@app.route("/api/save", methods=["POST"])
def api_save():
    if not any((st.get("segments") or []) for st in _students()):
        return jsonify({"ok": False, "msg": "内容为空"}), 400
    try:
        return jsonify({"ok": True, "md": _save_record()})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


# ---------------- 历史记录：筛选 / 检索 / 参考分 / 导出 ----------------
REC_EXPORT_DIR = os.path.join(DATA, "exports")


def _rec_students(d):
    """存档里的学生列表；兼容 v1.x 单学生存档（数据都在顶层）"""
    sts = [st for st in (d.get("students") or []) if isinstance(st, dict)]
    if sts:
        return sts
    if d.get("segments"):
        return [{
            "id": "std0",
            "name": d.get("candidate") or "学生1",
            "segments": d.get("segments") or [],
            "paragraphs": d.get("paragraphs") or [],
            "duration": int(d.get("duration") or 0),
            "acc": float(d.get("duration") or 0),
            "review": d.get("review") or "",
            "review_mode": d.get("review_mode") or "",
            "qtype": d.get("qtype") or "",
        }]
    return []


def _rec_primary(d):
    sts = _rec_students(d)
    if not sts:
        return {}
    cur = d.get("cur_student")
    for st in sts:
        if st.get("id") == cur:
            return st
    return sts[0]


def _rec_chars(st):
    n = 0
    for sg in st.get("segments") or []:
        n += len(re.sub(r"\s", "", str(sg.get("text") or "")))
    return n


def _rec_dur(d, st):
    v = st.get("duration") or st.get("acc") or 0
    if not v:
        v = d.get("duration") or d.get("acc") or 0
    try:
        return int(round(float(v or 0)))
    except Exception:
        return 0


def _rec_body_text(st):
    return "\n".join(str(sg.get("text") or "") for sg in st.get("segments") or [])


def _rec_para_rows(st):
    """段号 / 起止秒 / 环节 / 精简文本：时间分配表、要点、逐字稿共用同一口径"""
    rows = []
    for i, p in enumerate(st.get("paragraphs") or [], 1):
        if not isinstance(p, dict):
            continue
        try:
            idx = int(p.get("idx") or i)
        except Exception:
            idx = i
        try:
            t0 = float(p.get("t_start") or 0)
            t1 = float(p.get("t_end") or 0)
        except Exception:
            t0, t1 = 0.0, 0.0
        if t1 < t0:
            t1 = t0
        txt = str(p.get("plain") or p.get("text") or "").strip()
        stage = str(p.get("plain_stage") or p.get("plain_label") or "").strip() or "未分环节"
        try:
            chars = int(p.get("chars") or len(re.sub(r"\s", "", txt)))
        except Exception:
            chars = len(re.sub(r"\s", "", txt))
        rows.append({"no": idx, "t0": t0, "t1": t1, "stage": stage,
                     "text": txt, "raw": str(p.get("text") or "").strip(),
                     "chars": chars, "p": p})
    return rows


def _rec_stage_names(d, st):
    qtype = st.get("qtype") or d.get("qtype") or ""
    try:
        names = _qtype_stage_names(qtype) or list(QT_DEFAULT_TAIL)
    except Exception:
        names = list(QT_DEFAULT_TAIL)
    out = []
    for x in names:
        x = str(x or "").strip()
        if x and x not in out:
            out.append(x)
    return out or list(QT_DEFAULT_TAIL)


def _rec_issues(rows, st):
    """问题条目：段落点评 + 句级问题标签，全部带段号与秒级坐标"""
    segs = {sg.get("id"): sg for sg in (st.get("segments") or []) if isinstance(sg, dict)}
    out, seen = [], set()
    for r in rows:
        p = r["p"]
        cands = []
        note = str(p.get("ai_note") or "").strip()
        if note:
            cands.append(note)
        for sid in p.get("seg_ids") or []:
            sg = segs.get(sid) or {}
            for key in ("ctags", "tags"):
                for t in sg.get(key) or []:
                    t = str(t or "").strip()
                    if t:
                        cands.append(t)
            an = str(sg.get("ai_note") or "").strip()
            if an:
                cands.append(an)
        for c in cands:
            k = (r["no"], c)
            if k in seen:
                continue
            seen.add(k)
            out.append({"no": r["no"], "t0": r["t0"], "t1": r["t1"], "text": c})
    return out


def _rec_time_table(rows):
    """按环节聚合：首次开始 → 末次结束、累计开口时长、字数、占比（占比按累计时长）"""
    order, agg = [], {}
    for r in rows:
        s = r["stage"]
        if s not in agg:
            agg[s] = {"stage": s, "t0": r["t0"], "t1": r["t1"], "sec": 0.0,
                      "chars": 0, "n": 0}
            order.append(s)
        a = agg[s]
        a["t0"] = min(a["t0"], r["t0"])
        a["t1"] = max(a["t1"], r["t1"])
        a["sec"] += max(0.0, r["t1"] - r["t0"])
        a["chars"] += r["chars"]
        a["n"] += 1
    total = sum(agg[s]["sec"] for s in order) or 0.0
    out = []
    for s in order:
        a = agg[s]
        a["pct"] = (a["sec"] / total * 100.0) if total else 0.0
        out.append(a)
    return out


def _rec_score(dur, chars, stages, covered, issues):
    """参考分（本地测算，不依赖 AI）：结构覆盖 40 + 语速节奏 35 + 问题扣分 25"""
    if dur <= 0 or chars <= 0:
        return None, {}
    cpm = chars / (dur / 60.0)
    s1 = 40.0 * min(1.0, len(covered) / float(max(1, len(stages))))
    if 180 <= cpm <= 300:
        s2 = 35.0
    elif cpm < 180:
        s2 = 35.0 * max(0.3, (cpm - 60.0) / 120.0)
    else:
        s2 = 35.0 * max(0.3, 1.0 - (cpm - 300.0) / 200.0)
    s3 = max(0.0, 25.0 - 2.5 * len(issues))
    total = int(round(s1 + s2 + s3))
    total = max(0, min(100, total))
    return total, {"struct": round(s1, 1), "pace": round(s2, 1), "issue": round(s3, 1),
                   "cpm": int(round(cpm)), "issues": len(issues),
                   "stages": "%d/%d" % (len(covered), len(stages))}


def _rec_row(d, fn):
    """把一份存档压成一行列表数据（含检索用全文，返回前端前会剔除）"""
    st = _rec_primary(d)
    sts = _rec_students(d)
    qtype = str(d.get("qtype") or st.get("qtype") or "").strip()
    created = str(d.get("created_at") or st.get("created_at") or "")
    question = str(d.get("question") or st.get("question") or "").strip()
    dur = _rec_dur(d, st)
    chars = _rec_chars(st)
    rows = _rec_para_rows(st)
    issues = _rec_issues(rows, st)
    stages = _rec_stage_names(d, st)
    covered = []
    for r in rows:
        if r["stage"] in stages and r["stage"] not in covered:
            covered.append(r["stage"])
    score, sinfo = _rec_score(dur, chars, stages, covered, issues)
    names = [str(x.get("name") or "").strip() for x in sts if str(x.get("name") or "").strip()]
    body = _rec_body_text(st)
    plain = "\n".join(r["text"] for r in rows)
    return {
        "file": fn,
        "id": str(d.get("id") or fn.replace(".json", "")),
        "question": question,
        "q_short": question[:40],
        "created_at": created,
        "date": created[:10],
        "qtype": qtype or "未标注",
        "students": names,
        "student": names[0] if names else "未命名",
        "stu_n": len(names),
        "segments": len(st.get("segments") or []),
        "paragraphs": len(rows),
        "duration": dur,
        "chars": chars,
        "score": score,
        "score_info": sinfo,
        "_kw_question": question,
        "_kw_body": body,
        "_kw_plain": plain,
    }


def _rec_snippet(row, kw):
    """命中位置：题干优先，其次逐字稿；返回一句可展示的上下文"""
    if not kw:
        return "", ""
    low = kw.lower()
    q = row["_kw_question"]
    if low in q.lower():
        i = q.lower().index(low)
        a = max(0, i - 12)
        b = min(len(q), i + len(kw) + 18)
        return "题干", ("…" if a else "") + q[a:b] + ("…" if b < len(q) else "")
    for label, text in (("逐字稿", row["_kw_body"]), ("精简稿", row["_kw_plain"])):
        if low in text.lower():
            i = text.lower().index(low)
            a = max(0, i - 12)
            b = min(len(text), i + len(kw) + 18)
            frag = text[a:b].replace("\n", " ")
            return label, ("…" if a else "") + frag + ("…" if b < len(text) else "")
    return "", ""


def _rec_match(row, qtype, student, d1, d2, kw):
    if qtype and row["qtype"] != qtype:
        return False
    if student and student not in row["students"]:
        return False
    if d1 and row["date"] and row["date"] < d1:
        return False
    if d2 and row["date"] and row["date"] > d2:
        return False
    if kw:
        low = kw.lower()
        if not (low in row["_kw_question"].lower() or low in row["_kw_body"].lower()
                or low in row["_kw_plain"].lower()):
            return False
    return True


@app.route("/favicon.ico")
def _favicon_ico():
    # 浏览器默认会请求站点图标，返回 204 避免控制台 404 噪声
    return ("", 204)


@app.route("/api/records")
def api_records():
    """历史记录：题型 / 学生 / 日期范围 / 关键词（题干或逐字稿）组合筛选"""
    qtype = (request.args.get("qtype") or "").strip()
    student = (request.args.get("student") or "").strip()
    d1 = (request.args.get("d1") or "").strip()
    d2 = (request.args.get("d2") or "").strip()
    kw = (request.args.get("kw") or "").strip()
    rows, broken = [], 0
    for fn in sorted(os.listdir(REC_DIR), reverse=True):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(REC_DIR, fn), "r", encoding="utf-8") as f:
                d = json.load(f)
            if not isinstance(d, dict):
                continue
            rows.append(_rec_row(d, fn))
        except Exception:
            broken += 1
            continue
    # 分面选项按全量计算，避免选了条件后其它选项消失
    qmap, smap, dates = {}, {}, []
    for r in rows:
        qmap[r["qtype"]] = qmap.get(r["qtype"], 0) + 1
        for nm in r["students"]:
            smap[nm] = smap.get(nm, 0) + 1
        if r["date"]:
            dates.append(r["date"])
    facets = {
        "qtypes": [{"name": k, "count": v} for k, v in sorted(qmap.items(), key=lambda x: (-x[1], x[0]))],
        "students": [{"name": k, "count": v} for k, v in sorted(smap.items(), key=lambda x: (-x[1], x[0]))],
        "date_min": min(dates) if dates else "",
        "date_max": max(dates) if dates else "",
    }
    out = []
    for r in rows:
        if not _rec_match(r, qtype, student, d1, d2, kw):
            continue
        hit, snip = _rec_snippet(r, kw)
        r = dict(r)
        r.pop("_kw_question", None)
        r.pop("_kw_body", None)
        r.pop("_kw_plain", None)
        r["hit"] = hit
        r["snippet"] = snip
        out.append(r)
    active = [x for x in (qtype, student, d1, d2, kw) if x]
    return jsonify({"ok": True, "records": out, "matched": len(out), "total": len(rows),
                    "facets": facets, "active": len(active), "broken": broken})


# ---------------- 整场复盘稿导出 Word ----------------

def _docx_supports():
    try:
        import docx  # noqa: F401
        return True
    except Exception:
        return False


def _docx_init(doc):
    from docx.shared import Pt, RGBColor
    from docx.oxml.ns import qn
    normal = doc.styles["Normal"]
    normal.font.name = "微软雅黑"
    normal.font.size = Pt(10.5)
    try:
        normal.element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "微软雅黑")
    except Exception:
        pass
    for i, size in ((1, 15), (2, 12.5), (3, 11)):
        try:
            h = doc.styles["Heading %d" % i]
            h.font.name = "微软雅黑"
            h.font.size = Pt(size)
            h.font.color.rgb = RGBColor(0x1F, 0x2A, 0x37)
            h.element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "微软雅黑")
        except Exception:
            pass


def _docx_table(doc, head, rows):
    t = doc.add_table(rows=1, cols=len(head))
    try:
        t.style = "Table Grid"
    except Exception:
        pass
    for i, h in enumerate(head):
        cell = t.rows[0].cells[i]
        cell.text = ""
        run = cell.paragraphs[0].add_run(str(h))
        run.bold = True
    for row in rows:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = str(v)
    return t


def _fmt_sec(sec):
    sec = max(0, int(round(float(sec or 0))))
    return "%02d:%02d" % (sec // 60, sec % 60)


def _export_docx(d, student_id=None):
    """生成整场复盘稿 .docx，返回 (路径, 段落数, 概要)"""
    from docx import Document
    sts = _rec_students(d)
    if not sts:
        raise ValueError("该存档没有可导出的作答数据")
    st = sts[0]
    picked = False
    if student_id:
        # 兼容按学生 id 或姓名指定
        for x in sts:
            if student_id in (str(x.get("id") or ""), str(x.get("name") or "")):
                st = x
                picked = True
                break
    if not picked and not student_id:
        st = _rec_primary(d) or sts[0]
    qtype = str(d.get("qtype") or st.get("qtype") or "").strip() or "未标注"
    question = str(d.get("question") or st.get("question") or "").strip() or "（未填写）"
    created = str(d.get("created_at") or "")
    dur = _rec_dur(d, st)
    chars = _rec_chars(st)
    rows = _rec_para_rows(st)
    issues = _rec_issues(rows, st)
    stages = _rec_stage_names(d, st)
    covered = [s for s in stages if any(r["stage"] == s for r in rows)]
    tables = _rec_time_table(rows)
    score, sinfo = _rec_score(dur, chars, stages, covered, issues)
    names = [str(x.get("name") or "").strip() for x in sts if str(x.get("name") or "").strip()]
    stu_name = str(st.get("name") or (names[0] if names else "未命名")).strip()
    cpm = int(round(chars / (dur / 60.0))) if dur > 0 else 0

    doc = Document()
    _docx_init(doc)
    doc.add_heading("面试复盘稿", 0)
    doc.add_heading("一、基本信息", 1)
    _docx_table(doc, ["项目", "内容"], [
        ["学生", stu_name + (("（本场共 %d 位学生，此处为%s）" % (len(names), "所选学生" if picked else "当前学生"))
                              if len(names) > 1 else "")],
        ["题型", qtype],
        ["日期", created or "（未记录）"],
        ["作答时长", "%s（%d 秒）" % (_fmt_sec(dur), dur)],
        ["有效字数", "%d 字" % chars],
        ["语速", "%d 字/分" % cpm if cpm else "—"],
        ["参考分", ("%d 分" % score) if score is not None else "—"],
        ["题干", question],
    ])

    doc.add_heading("二、时间分配", 1)
    if tables:
        trs = []
        for a in tables:
            trs.append([a["stage"], "%s–%s" % (_fmt_sec(a["t0"]), _fmt_sec(a["t1"])),
                        "%.1f 秒" % a["sec"], "%d 字" % a["chars"],
                        "%.1f%%" % a["pct"]])
        trs.append(["合计", "—", "%.1f 秒" % (dur or sum(a["sec"] for a in tables)),
                    "%d 字" % chars, "100.0%"])
        _docx_table(doc, ["环节", "起止", "时长", "字数", "占比"], trs)
        long_a = max(tables, key=lambda a: a["sec"])
        short_a = min(tables, key=lambda a: a["sec"])
        doc.add_paragraph("最长环节：%s（%.1f 秒，占 %.1f%%）；最短环节：%s（%.1f 秒，占 %.1f%%）。"
                          % (long_a["stage"], long_a["sec"], long_a["pct"],
                             short_a["stage"], short_a["sec"], short_a["pct"]))
    else:
        doc.add_paragraph("该场没有分段数据，无法生成时间分配表。")

    doc.add_heading("三、点评", 1)
    doc.add_heading("（一）时间", 2)
    tlines = ["总作答时长 %s（%d 秒），精简后有效字数 %d 字，平均语速 %d 字/分。"
              % (_fmt_sec(dur), dur, chars, cpm)]
    if cpm:
        if 180 <= cpm <= 300:
            tlines.append("语速落在正常区间（180–300 字/分），表达节奏可控。")
        elif cpm < 180:
            tlines.append("语速偏慢（低于 180 字/分），说明停顿或思考偏多，可压缩停顿、先说框架。")
        else:
            tlines.append("语速偏快（高于 300 字/分），容易吞字、口头禅增多，需要刻意放慢。")
    if tables:
        tlines.append("环节时间分布：" + "；".join("%s %.1f 秒（%.1f%%）" % (a["stage"], a["sec"], a["pct"])
                                              for a in tables) + "。")
    for t in tlines:
        doc.add_paragraph(t, style="List Bullet")

    doc.add_heading("（二）优点", 2)
    goods = []
    if covered:
        goods.append("结构覆盖 %d/%d 个环节：%s。" % (len(covered), len(stages), "、".join(covered)))
    if cpm and 180 <= cpm <= 300:
        goods.append("语速 %d 字/分处在正常区间，整体表达连贯。" % cpm)
    if rows:
        avg = int(round(sum(r["chars"] for r in rows) / float(len(rows))))
        goods.append("共 %d 段、%d 句，平均每段 %d 字，段落切分清晰可回看。" % (len(rows), len(st.get("segments") or []), avg))
    if issues and len(issues) <= 3:
        goods.append("句级问题标注仅 %d 条，表达瑕疵较少。" % len(issues))
    if not issues:
        goods.append("未检出明显的空洞对策、无主体等句级问题。")
    if not goods:
        goods.append("可先补足分段与时长数据，再判断优点。")
    for g in goods:
        doc.add_paragraph(g, style="List Bullet")

    doc.add_heading("（三）问题", 2)
    missing = [s for s in stages if s not in covered]
    if missing:
        doc.add_paragraph("环节遗漏：%s 未作答（或未识别到对应段落）。" % "、".join(missing), style="List Bullet")
    if issues:
        for it in issues:
            doc.add_paragraph("第 %d 段（%s–%s）：%s"
                              % (it["no"], _fmt_sec(it["t0"]), _fmt_sec(it["t1"]), it["text"]),
                              style="List Bullet")
    elif not missing:
        doc.add_paragraph("未发现句级问题标注。", style="List Bullet")

    doc.add_heading("四、精简要点（按环节分组）", 1)
    if rows:
        for s in stages:
            pts = [r["text"] for r in rows if r["stage"] == s and r["text"]]
            if not pts:
                continue
            doc.add_heading(s, 2)
            for t in pts:
                doc.add_paragraph(t, style="List Bullet")
        other = [r["text"] for r in rows if r["stage"] not in stages and r["text"]]
        if other:
            doc.add_heading("未分环节", 2)
            for t in other:
                doc.add_paragraph(t, style="List Bullet")
    else:
        doc.add_paragraph("该场没有可用的精简要点。")

    doc.add_heading("五、精简逐字稿全文", 1)
    if rows:
        for r in rows:
            p = doc.add_paragraph()
            head = p.add_run("第 %d 段 %s–%s（%s）" % (r["no"], _fmt_sec(r["t0"]), _fmt_sec(r["t1"]), r["stage"]))
            head.bold = True
            p.add_run("：" + (r["text"] or r["raw"]))
    else:
        body = _rec_body_text(st)
        doc.add_paragraph(body or "该场没有逐字稿内容。")
    if len(sts) > 1:
        doc.add_paragraph("说明：本场共 %d 位学生（%s），本文件导出学生「%s」的复盘内容。"
                          % (len(sts), "、".join(names), stu_name))

    os.makedirs(REC_EXPORT_DIR, exist_ok=True)
    safe = re.sub(r"[\\/:*?\"<>|\s]+", "", "%s_%s" % (stu_name, qtype))
    name = "%s_复盘稿_%s_%s.docx" % (str(d.get("id") or "record")[:24], safe[:24],
                                    datetime.now().strftime("%Y%m%d_%H%M%S"))
    path = os.path.join(REC_EXPORT_DIR, name)
    doc.save(path)
    return path, len(doc.paragraphs), {
        "student": stu_name, "qtype": qtype, "duration": dur, "chars": chars,
        "score": score, "sections": 5, "time_rows": len(tables),
        "issues": len(issues), "points": len(rows), "tables": 2,
    }


@app.route("/api/export/docx", methods=["POST"])
def api_export_docx():
    """一键导出整场复盘稿：body 传 file（历史存档）或不传（当前会话）"""
    if not _docx_supports():
        return jsonify({"ok": False, "msg": "缺少 python-docx，无法导出 Word"}), 500
    body = request.get_json(silent=True) or {}
    fn = os.path.basename(str(body.get("file") or "").strip())
    sid = str(body.get("student") or "").strip()
    try:
        if fn:
            p = os.path.join(REC_DIR, fn)
            if not fn.endswith(".json") or not os.path.exists(p):
                return jsonify({"ok": False, "msg": "存档不存在"}), 404
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
        else:
            d = json.loads(json.dumps(session, ensure_ascii=False))
        path, n, info = _export_docx(d, sid or None)
        return jsonify({"ok": True, "path": path, "name": os.path.basename(path),
                        "paragraphs": n, "info": info})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "msg": str(e)}), 500


@app.route("/api/export/download")
def api_export_download():
    name = os.path.basename(request.args.get("name", ""))
    if not name or not os.path.exists(os.path.join(REC_EXPORT_DIR, name)):
        return jsonify({"ok": False, "msg": "文件不存在"}), 404
    return send_from_directory(REC_EXPORT_DIR, name, as_attachment=True, download_name=name)


def _read_record(fn):
    """按文件名读历史存档（只读，绝不回写磁盘）"""
    fn = os.path.basename(fn or "")
    p = os.path.join(REC_DIR, fn)
    if not os.path.exists(p):
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _prepare_record(data):
    """载入历史存档后的只读补齐：v1.x 单学生封装、点评提升、各学生段落 / 精简版补齐

    全部只改内存对象，不动磁盘存档。
    """
    if isinstance(data, dict):
        sts = data.get("students")
        if not isinstance(sts, list) or not sts:
            # v1.x 单学生存档：包成一位学生，前端照常回看
            one = _new_student(data.get("candidate") or "学生1", 1)
            one["acc"] = float(data.get("duration") or 0)
            one["duration"] = int(data.get("duration") or 0)
            one["segments"] = data.get("segments") or []
            one["paragraphs"] = data.get("paragraphs") or []
            one["review"] = data.get("review") or ""
            one["review_mode"] = data.get("review_mode") or ""
            data["students"] = [one]
            data["cur_student"] = one["id"]
        # 多学生存档的点评存在各自学生对象里，回看时补到顶层供点评区渲染
        if not data.get("review"):
            cur = None
            for st in data["students"]:
                if st.get("id") == data.get("cur_student"):
                    cur = st
                    break
            cur = cur or data["students"][0]
            data["review"] = cur.get("review") or ""
            data["review_mode"] = cur.get("review_mode") or data.get("review_mode") or ""
        for st in data["students"]:
            st.setdefault("notes", [])
            # 按该存档自己的题型取环节，避免用当前会话的题型去套历史作答
            keep = list(simp.STAGES or [])
            try:
                simp.set_stages(_qtype_stage_names(st.get("qtype")) or list(QT_DEFAULT_TAIL))
                if not st.get("paragraphs") and st.get("segments"):
                    st["paragraphs"] = pz.build_from_segments(st["segments"], _para_cfg())
                # 历史记录若无精简版字段，回看时自动补齐（只读，不改动磁盘存档）
                if st.get("paragraphs") and simp.pending_count(st["paragraphs"]):
                    simp.apply_all(st["paragraphs"],
                                   seg_map={s["id"]: s for s in st.get("segments") or []})
            except Exception:
                traceback.print_exc()
            finally:
                try:
                    simp.set_stages(keep or list(QT_DEFAULT_TAIL))
                except Exception:
                    pass
        # 回看默认展示「当前学生」的逐字稿 / 精简版：段落实际保存在 students[].paragraphs，
        # 存档顶层往往没有 paragraphs，前端只读顶层就会渲染成空白，这里提升上来。
        cur = None
        for st in data["students"]:
            if st.get("id") == data.get("cur_student"):
                cur = st
                break
        cur = cur or data["students"][0]
        if not data.get("cur_student"):
            data["cur_student"] = cur.get("id")
        data["paragraphs"] = cur.get("paragraphs") or []
        data["segments"] = cur.get("segments") or []
    return data


@app.route("/api/records/load")
def api_records_load():
    fn = request.args.get("file", "")
    data = _read_record(fn)
    if data is None:
        return jsonify({"ok": False, "msg": "不存在"}), 404
    return jsonify({"ok": True, "data": _prepare_record(data)})


@app.route("/api/records/points")
def api_records_points():
    """历史回看「取当前精简稿」：按存档里指定学生的段落取稿，不读实时会话

    回看时实时会话可能是空的（或属于别的学生），只走这里才能取到该场学生的精简稿。
    """
    fn = request.args.get("file", "")
    sid = request.args.get("student", "")
    data = _read_record(fn)
    if data is None:
        return jsonify({"ok": False, "msg": "历史记录不存在"}), 404
    data = _prepare_record(data)
    sts = data.get("students") or []
    st = None
    for x in sts:
        if x.get("id") == sid:
            st = x
            break
    st = st or (sts[0] if sts else {})
    paras = [p for p in (st.get("paragraphs") or []) if isinstance(p, dict)]
    sm = {s["id"]: s for s in (st.get("segments") or []) if isinstance(s, dict) and s.get("id")}
    qtype = st.get("qtype") or data.get("qtype") or ""
    ref = st.get("ref") or data.get("ref") or ""
    question = data.get("question") or st.get("question") or ""
    # 环节按该存档自己的题型取，取完恢复，避免污染当前会话的题型
    keep = list(simp.STAGES or [])
    try:
        simp.set_stages(_qtype_stage_names(qtype) or list(QT_DEFAULT_TAIL))
        stages = _stage_options()
    finally:
        try:
            simp.set_stages(keep or list(QT_DEFAULT_TAIL))
        except Exception:
            pass
    txt = _points_source(force=True, paras=paras, seg_map=sm)
    blocks = _points_blocks(paras=paras, seg_map=sm)
    body_chars = len(re.sub(r"【[^】]*】|\s", "", txt))
    return jsonify({"ok": True, "hist": True, "file": os.path.basename(fn or ""),
                    "student": st.get("id") or "", "student_name": st.get("name") or "",
                    "text": txt, "chars": len(txt), "body_chars": body_chars,
                    "stamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "edit_seq": sum(int((_edits(p) or {}).get("seq") or 0) for p in paras),
                    "paras": len([p for p in paras if (p.get("plain") or "").strip()]),
                    "para_total": len(paras), "blocks": blocks,
                    "qtype": qtype, "stages": stages, "ref": ref, "question": question,
                    "has_key": bool((ai.cfg.get("api_key") or "").strip())})


@app.route("/api/open_folder", methods=["POST"])
def api_open_folder():
    try:
        os.startfile(REC_DIR)
        return jsonify({"ok": True, "path": REC_DIR})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


def bootstrap():
    """启动前的准备工作：载入上次会话 + 预加载识别模型（供 launcher 调用）"""
    load_session()
    try:
        engine._load()
        return True, "识别模型已加载"
    except Exception as e:
        return False, "模型预加载失败: %s" % e


if __name__ == "__main__":
    ok, msg = bootstrap()
    print(("[OK] " if ok else "[WARN] ") + msg)
    _port = int(os.environ.get("IR_PORT", "7788"))
    set_http_port(_port)          # 登记真实 HTTP 端口，供 /api/mobile/info 拼手机可用地址
    # v2.6：监听 0.0.0.0，让同一局域网内的手机/平板可直接访问；本机仍用 127.0.0.1 打开
    _host = os.environ.get("IR_HOST", "0.0.0.0")
    print("服务已启动: http://127.0.0.1:%d" % _port)
    _lan = _lan_ip()
    if _lan:
        print("手机/平板访问(HTTP，不能开麦): http://%s:%d%s" % (_lan, _port, MOBILE_PAGE_PATH))
    else:
        print("[WARN] 未识别到局域网地址，手机端可能无法访问")
    # v2.7：并存一条 HTTPS 监听，手机端只有 HTTPS 才能拿到麦克风
    _hsrv, _hport, _herr = start_https_server(_host, 0)
    if _hsrv or _hport:
        print("手机/平板访问(HTTPS，可开麦): https://%s:%d%s" % (_lan or "127.0.0.1", _hport, MOBILE_PAGE_PATH))
        if _HTTPS.get("created"):
            print("[INFO] 已自动签发自签证书: %s" % _HTTPS.get("cert"))
        print("[INFO] 手机首次打开会提示证书不受信任，选择“继续访问/高级→继续”即可")
    else:
        print("[WARN] HTTPS 未启动（手机端将无法录音）: %s" % (_herr or "未知原因"))
    app.run(host=_host, port=_port, threaded=True, debug=False, use_reloader=False)
