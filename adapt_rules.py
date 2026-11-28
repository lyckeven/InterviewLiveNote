# -*- coding: utf-8 -*-
"""本地功能适配更新（适配规则热更新）

目的
    让「本地题库解析 / 本地点评 / 文字处理」里那些靠规则吃饭的部分（题型识别关键词、
    答题框架要点、点评方式与常见问题、段落环节识别、口误纠错词表、笔记取题规则）
    可以只更新一份「适配规则包」就生效，不用重新打包、重装整个软件。

规则包结构（zip 或单节 json）
    manifest.json        必需，版本与说明
    question_detect.json 题目识别规则（题型关键词、判别顺序、易混区分、题号样式）
    frameworks.json      各题型答题框架（环节 → 要点）
    comment_rules.json   点评方式与学员常见问题（按题型）
    segment_rules.json   段落环节识别正则
    text_fix.json        文字处理：口误/错别字替换对、口头禅词表
    bank_parse.json      笔记取题规则（题号样式、答案段标记、分节标题、噪声行）
    bank_items.json      从笔记里抽出的真题（需在界面点"导入题库"才真正入库）

存储位置
    内置：BASE/adapt            （随包分发，只读）
    生效：DATA/adapt/current    （用户可写，导入后覆盖内置）
    备份：DATA/adapt/backup     （每次导入前的上一版）
    历史：DATA/adapt/packs      （导入过的原始包，保留最近 5 个）

热更新
    - 每次读取都校验 current/manifest.json 的 mtime，变了就清缓存重载；
    - 因此讲师直接把新规则文件拷进 DATA/adapt/current 也能立刻生效；
    - 界面上的「导入适配包」走 import_pack()，带完整校验。

安全约束
    只接受白名单分节文件、限制单文件与总解压体积、拒绝绝对路径与 ../ 越界条目、
    正则一律预编译校验，校验不通过整包拒绝（不会留下半套规则）。
"""

import io
import json
import os
import re
import hashlib
import shutil
import sys
import time
import zipfile

# ---------------- 路径 ----------------
SECTION_FILES = {
    "question_detect": "question_detect.json",
    "frameworks": "frameworks.json",
    "comment_rules": "comment_rules.json",
    "segment_rules": "segment_rules.json",
    "text_fix": "text_fix.json",
    "bank_parse": "bank_parse.json",
    "bank_items": "bank_items.json",
}
SECTION_LABEL = {
    "question_detect": "题目识别规则",
    "frameworks": "题型答题框架",
    "comment_rules": "点评方式与常见问题",
    "segment_rules": "段落环节识别",
    "text_fix": "文字处理词表",
    "bank_parse": "笔记取题规则",
    "bank_items": "真题条目",
}
MANIFEST = "manifest.json"

MAX_FILE_BYTES = 4 * 1024 * 1024          # 单个分节 4MB
MAX_TOTAL_BYTES = 32 * 1024 * 1024        # 解压总量 32MB
MAX_ENTRIES = 2000                        # zip 条目数上限
KEEP_PACKS = 5


def _resource_base():
    """资源根：打包后是 _MEIPASS，源码态是脚本所在目录"""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.abspath(__file__))


def _user_data_dir():
    """与 server._user_data_dir 保持完全一致的口径"""
    env = os.environ.get("IR_DATA_DIR")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    if getattr(sys, "frozen", False):
        root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(root, "InterviewReviewer", "data")
    return os.path.join(_resource_base(), "data")


BASE = _resource_base()
DATA = _user_data_dir()
BUILTIN_DIR = os.path.join(BASE, "adapt")
ADAPT_HOME = os.path.join(DATA, "adapt")
CURRENT_DIR = os.path.join(ADAPT_HOME, "current")
BACKUP_DIR = os.path.join(ADAPT_HOME, "backup")
PACK_DIR = os.path.join(ADAPT_HOME, "packs")


class AdaptError(Exception):
    """规则包不合法（面向用户的中文原因）"""


# ---------------- 内置默认规则 ----------------
# 规则包缺失某节时的兜底，保证任何情况下软件都能跑
DEFAULT_QUESTION_DETECT = {
    "version": "builtin",
    "question_markers": [r"^\s*\d{1,3}\s*[\.、．)）]\s*", r"^\s*[（(]\s*\d{1,3}\s*[）)]\s*", r"^\s*第\s*\d{1,3}\s*题\s*"],
    "types": [],
    "score_rules": [],
    "judge_order": [],
}

DEFAULT_BANK_PARSE = {
    "version": "builtin",
    "question_start": [
        r"^\s*\d{1,3}\s*[\.、．]\s*",
        r"^\s*[（(]\s*\d{1,3}\s*[）)]\s*",
        r"^\s*第\s*\d{1,3}\s*题",
        r"^\s*【\s*题目\s*】",
    ],
    "answer_start": [
        r"^\s*【?\s*(?:参考答案|参考要点|答题示范|示范作答|答题思路|解析|作答参考)\s*】?\s*[:：]?",
        r"^\s*(?:考生作答|考生开始答题)\s*[:：]?",
    ],
    "section_heads": [r"^\s*[一二三四五六七八九十]+[、\.]\s*\S{2,20}$", r"^\s*【[^】]{2,20}】\s*$"],
    "qtype_hints": {
        "社会现象": ["社会现象", "现象类"],
        "态度观点": ["态度观点", "观点类", "观点分析"],
        "组织管理": ["组织管理", "计划组织"],
        "应急应变": ["应急应变", "情景应变"],
        "人际关系": ["人际关系", "人际交往"],
        "情景模拟": ["情景模拟", "现场模拟"],
        "自我认知": ["自我认知", "岗位匹配"],
        "漫画": ["漫画"],
        "演讲": ["演讲"],
        "串词": ["串词"],
        "反驳": ["反驳"],
        "材料题": ["材料题"],
        "排序": ["排序"],
    },
    "noise": [
        r"^\s*第\s*\d+\s*页\s*$",
        r"^\s*\d{1,3}\s*/\s*\d{1,3}\s*$",
        r"^\s*[—\-=_·]{3,}\s*$",
        r"^\s*\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日\s*$",
    ],
    "ask_words": ["怎么看", "谈谈", "怎么办", "如何", "为什么", "请你", "假如", "如果", "你作为", "你怎么", "请现场模拟", "有什么启示"],
    "min_len": 12,
    "max_len": 1500,
}

DEFAULT_SEGMENT_RULES = {
    "version": "builtin",
    "rules": [
        {"role": "open", "sub": "", "pattern": r"(各位考官|考官好|考生开始答题|下面我开始|我开始回答)"},
        {"role": "opinion", "sub": "表态", "pattern": r"(我认为|我的看法|我觉得|总体来看|对此|对于这道题|我个人认为|在我看来|我的观点)"},
        {"role": "analyze", "sub": "原因", "pattern": r"(原因|之所以|根源|究其原因|问题出在)"},
        {"role": "analyze", "sub": "影响", "pattern": r"(危害|影响|意义|好处|不良后果|负面|积极作用)"},
        {"role": "action", "sub": "对策", "pattern": r"(对策|措施|建议|抓好|落实|加强|完善|一方面要|首先要|第一要|需要)"},
        {"role": "close", "sub": "收束", "pattern": r"(总之|综上|以上就是|回答完毕|因此我认为|总之我认为)"},
    ],
}

DEFAULT_TEXT_FIX = {
    "version": "builtin",
    "pairs": [],
    "fillers": ["嗯", "呃", "啊", "那个", "这个", "就是说", "然后", "所以说", "对吧"],
}

DEFAULT_COMMENT_RULES = {"version": "builtin", "method": [], "problems": []}

DEFAULT_FRAMEWORKS = {"version": "builtin", "types": []}

_DEFAULTS = {
    "question_detect": DEFAULT_QUESTION_DETECT,
    "bank_parse": DEFAULT_BANK_PARSE,
    "segment_rules": DEFAULT_SEGMENT_RULES,
    "text_fix": DEFAULT_TEXT_FIX,
    "comment_rules": DEFAULT_COMMENT_RULES,
    "frameworks": DEFAULT_FRAMEWORKS,
    "bank_items": {"version": "builtin", "items": []},
}


# ---------------- 内部工具 ----------------
def _ensure_dir(path):
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass


def _read_json(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def _write_json(path, obj):
    _ensure_dir(os.path.dirname(path))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _safe_name(name):
    """zip 内部条目名归一化：拒绝绝对路径与 ../ 越界"""
    raw = str(name or "").replace("\\", "/")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise AdaptError("适配包里有绝对路径条目：%s" % name)
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise AdaptError("适配包里有越界路径条目：%s" % name)
    return "/".join(parts)


def _check_patterns(obj, where, out):
    """递归校验所有以 _re / _pattern / pattern 结尾或名为正则的字段，预编译失败则整包拒绝"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = str(k)
            if key.endswith(("_re", "_regex", "pattern", "regex")) and isinstance(v, str):
                try:
                    re.compile(v)
                except re.error as e:
                    raise AdaptError("%s 字段「%s」正则不合法：%s" % (where, key, e))
                out.append(v)
            else:
                _check_patterns(v, where, out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _check_patterns(v, "%s[%d]" % (where, i), out)


def _entry_count(obj):
    n = 0
    if isinstance(obj, dict):
        for v in obj.values():
            n += 1 + _entry_count(v)
    elif isinstance(obj, list):
        for v in obj:
            n += 1 + _entry_count(v)
    return n


# ---------------- 加载与缓存 ----------------
_CACHE = {}
_CACHE_SIG = [None]          # (current manifest mtime, builtin manifest mtime)


def _manifest_path():
    return os.path.join(CURRENT_DIR, MANIFEST)


def _sig():
    sig = []
    for p in (os.path.join(CURRENT_DIR, MANIFEST), os.path.join(BUILTIN_DIR, MANIFEST)):
        try:
            st = os.stat(p)
            sig.append((st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append(None)
    return tuple(sig)


def reload():
    """清缓存（文件被外部替换后调用，或导入后调用）"""
    _CACHE.clear()
    _CACHE_SIG[0] = None


def _maybe_invalidate():
    s = _sig()
    if _CACHE_SIG[0] != s:
        _CACHE.clear()
        _CACHE_SIG[0] = s


def _section_path(name):
    """优先取用户生效目录，没有则回退内置"""
    fn = SECTION_FILES.get(name)
    if not fn:
        return None
    p = os.path.join(CURRENT_DIR, fn)
    if os.path.isfile(p):
        return p
    p = os.path.join(BUILTIN_DIR, fn)
    if os.path.isfile(p):
        return p
    return None


def section(name):
    """读一节规则，缺失时回退内置默认值；带 mtime 失效缓存"""
    _maybe_invalidate()
    if name in _CACHE:
        return _CACHE[name]
    obj = None
    p = _section_path(name)
    if p:
        try:
            obj = _read_json(p)
        except Exception:
            obj = None
    if not isinstance(obj, dict):
        obj = dict(_DEFAULTS.get(name) or {})
        obj["_source"] = "内置默认"
    _CACHE[name] = obj
    return obj


def manifest():
    """当前生效的规则包清单"""
    p = _manifest_path()
    if os.path.isfile(p):
        try:
            m = _read_json(p)
            if isinstance(m, dict):
                return m
        except Exception:
            pass
    p = os.path.join(BUILTIN_DIR, MANIFEST)
    if os.path.isfile(p):
        try:
            m = _read_json(p)
            if isinstance(m, dict):
                m = dict(m)
                m.setdefault("source", "builtin")
                return m
        except Exception:
            pass
    return {"id": "builtin-empty", "name": "内置适配规则", "version": "0", "source": "builtin"}


def _dir_info(path):
    n, size, newest = 0, 0, 0
    for fn in SECTION_FILES.values():
        fp = os.path.join(path, fn)
        if os.path.isfile(fp):
            n += 1
            try:
                st = os.stat(fp)
                size += st.st_size
                newest = max(newest, st.st_mtime)
            except OSError:
                pass
    return n, size, newest


def status():
    """给界面用的状态摘要"""
    m = manifest()
    n_cur, size_cur, newest = _dir_info(CURRENT_DIR)
    n_builtin, size_builtin, _ = _dir_info(BUILTIN_DIR)
    secs = []
    _src = str(m.get("source") or "builtin")
    _from = "随包内置" if _src in ("builtin", "", "export") else "用户导入"
    for key, fn in SECTION_FILES.items():
        p = _section_path(key)
        obj = section(key)
        cnt = 0
        if key == "question_detect":
            cnt = len(obj.get("types") or [])
        elif key == "frameworks":
            cnt = len(obj.get("types") or [])
        elif key == "comment_rules":
            cnt = len(obj.get("method") or []) + len(obj.get("problems") or [])
        elif key == "segment_rules":
            cnt = len(obj.get("rules") or [])
        elif key == "text_fix":
            cnt = len(obj.get("pairs") or []) + len(obj.get("fillers") or [])
        elif key == "bank_parse":
            cnt = len(obj.get("question_start") or [])
        elif key == "bank_items":
            cnt = len(obj.get("items") or [])
        secs.append({
            "key": key,
            "file": fn,
            "label": SECTION_LABEL.get(key, key),
            "count": cnt,
            "from": _from if os.path.isfile(os.path.join(CURRENT_DIR, fn)) else "内置随包",
            "path": p or "",
        })
    return {
        "ok": True,
        "manifest": m,
        "current_dir": CURRENT_DIR,
        "builtin_dir": BUILTIN_DIR,
        "home": ADAPT_HOME,
        "has_user_pack": n_cur > 0,
        "user_files": n_cur,
        "user_bytes": size_cur,
        "user_updated": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(newest)) if newest else "",
        "builtin_files": n_builtin,
        "builtin_bytes": size_builtin,
        "sections": secs,
        "packs": _list_packs(),
        "revision": "%s-%s" % (m.get("version") or "0", newest or 0),
    }


def _list_packs():
    out = []
    if not os.path.isdir(PACK_DIR):
        return out
    try:
        names = sorted(os.listdir(PACK_DIR), reverse=True)
    except OSError:
        return out
    for n in names[:20]:
        fp = os.path.join(PACK_DIR, n)
        if os.path.isfile(fp):
            try:
                out.append({"name": n, "bytes": os.path.getsize(fp)})
            except OSError:
                pass
    return out


# ---------------- 导入 / 导出 / 还原 ----------------
def _collect_from_zip(data):
    """解压到内存，返回 (manifest, {section: obj})；任何不合规都抛 AdaptError"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception as e:
        raise AdaptError("不是有效的 zip 包：%s" % e)
    infos = zf.infolist()
    if len(infos) > MAX_ENTRIES:
        raise AdaptError("适配包条目过多（%d），最多 %d 条" % (len(infos), MAX_ENTRIES))
    total = sum(max(0, i.file_size) for i in infos)
    if total > MAX_TOTAL_BYTES:
        raise AdaptError("适配包解压后 %.1fMB，超过 %.0fMB 上限" % (total / 1048576.0, MAX_TOTAL_BYTES / 1048576.0))
    files = {}
    for info in infos:
        if info.is_dir():
            continue
        name = _safe_name(info.filename)
        if not name:
            continue
        base = name.split("/")[-1]
        if base not in list(SECTION_FILES.values()) + [MANIFEST]:
            # 包里允许带说明文件，但只读白名单
            continue
        if info.file_size > MAX_FILE_BYTES:
            raise AdaptError("分节文件 %s 超过 4MB" % base)
        try:
            raw = zf.read(info)
        except Exception as e:
            raise AdaptError("读取 %s 失败：%s" % (base, e))
        files[base] = raw
    if MANIFEST not in files:
        raise AdaptError("适配包缺少 manifest.json（至少要写 name / version）")
    try:
        man = json.loads(files[MANIFEST].decode("utf-8-sig"))
    except Exception as e:
        raise AdaptError("manifest.json 不是合法 JSON：%s" % e)
    if not isinstance(man, dict):
        raise AdaptError("manifest.json 顶层必须是对象")
    if not str(man.get("version") or "").strip():
        raise AdaptError("manifest.json 缺少 version 字段")
    secs = {}
    for key, fn in SECTION_FILES.items():
        if fn not in files:
            continue
        try:
            obj = json.loads(files[fn].decode("utf-8-sig"))
        except Exception as e:
            raise AdaptError("%s 不是合法 JSON：%s" % (fn, e))
        if not isinstance(obj, dict):
            raise AdaptError("%s 顶层必须是对象" % fn)
        _check_patterns(obj, fn, [])
        if _entry_count(obj) > 50000:
            raise AdaptError("%s 条目过多，疑似格式有误" % fn)
        secs[key] = obj
    if not secs:
        raise AdaptError("适配包里没有任何规则分节（%s）" % "、".join(SECTION_FILES.values()))
    return man, secs


def _collect_from_section(name, obj):
    """单个分节 json 导入（讲师手改某一节用）"""
    if name not in SECTION_FILES:
        raise AdaptError("不支持的分节：%s" % name)
    if not isinstance(obj, dict):
        raise AdaptError("分节内容顶层必须是对象")
    _check_patterns(obj, SECTION_FILES[name], [])
    return {name: obj}


def _backup_current(tag=""):
    if not os.path.isdir(CURRENT_DIR):
        return ""
    dst = os.path.join(BACKUP_DIR, "backup_%s%s" % (time.strftime("%Y%m%d_%H%M%S"), tag))
    try:
        shutil.copytree(CURRENT_DIR, dst)
        return dst
    except Exception:
        return ""


def _prune_backups(keep=8):
    if not os.path.isdir(BACKUP_DIR):
        return
    try:
        names = sorted(os.listdir(BACKUP_DIR), reverse=True)
    except OSError:
        return
    for n in names[keep:]:
        try:
            shutil.rmtree(os.path.join(BACKUP_DIR, n))
        except Exception:
            pass


def import_pack(data, filename="adapt_pack.zip", origin="user"):
    """导入整包（zip 字节）或单节 json。返回 status()"""
    if not isinstance(data, (bytes, bytearray)):
        raise AdaptError("导入内容为空")
    data = bytes(data)
    if len(data) > MAX_TOTAL_BYTES:
        raise AdaptError("文件超过 32MB 上限")
    if zipfile.is_zipfile(io.BytesIO(data)):
        man, secs = _collect_from_zip(data)
    else:
        # 单节 json：靠文件名判断是哪一节
        base = os.path.basename(str(filename or "")).lower()
        key = next((k for k, v in SECTION_FILES.items() if v == base), "")
        if not key:
            raise AdaptError("无法识别文件：既不是 zip 包，也不叫 %s 之一" % " / ".join(SECTION_FILES.values()))
        try:
            obj = json.loads(data.decode("utf-8-sig"))
        except Exception as e:
            raise AdaptError("%s 不是合法 JSON：%s" % (filename, e))
        secs = _collect_from_section(key, obj)
        man = {
            "id": "single-%s" % key,
            "name": "单节更新：%s" % SECTION_LABEL.get(key, key),
            "version": str(obj.get("version") or time.strftime("%Y%m%d.%H%M")),
        }
    _ensure_dir(CURRENT_DIR)
    _backup_current()
    for key, obj in secs.items():
        _write_json(os.path.join(CURRENT_DIR, SECTION_FILES[key]), obj)
    merged = dict(manifest())
    merged.update(man)
    merged["source"] = origin
    merged["imported_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    merged["imported_from"] = str(filename or "")
    merged["sections"] = sorted(SECTION_FILES[k] for k in secs)
    _write_json(_manifest_path(), merged)
    # 留档原始包（单节 json 也存一份）
    _ensure_dir(PACK_DIR)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    try:
        ext = ".zip" if zipfile.is_zipfile(io.BytesIO(data)) else ".json"
        keep = os.path.join(PACK_DIR, "%s_%s%s" % (stamp, re.sub(r"[^\w\-.]", "_", str(filename or "pack"))[:40], ext))
        with open(keep, "wb") as f:
            f.write(data)
        names = sorted(os.listdir(PACK_DIR), reverse=True)
        for n in names[KEEP_PACKS:]:
            try:
                os.remove(os.path.join(PACK_DIR, n))
            except OSError:
                pass
    except Exception:
        pass
    _prune_backups()
    reload()
    return status()


def reset():
    """清空用户规则，回到随包内置"""
    if os.path.isdir(CURRENT_DIR):
        _backup_current("_reset")
        try:
            shutil.rmtree(CURRENT_DIR)
        except Exception as e:
            raise AdaptError("删除用户规则失败：%s" % e)
    reload()
    return status()


def export_pack():
    """把当前生效规则打成一个 zip（含 manifest），便于在另一台电脑导入"""
    buf = io.BytesIO()
    man = dict(manifest())
    man["version"] = str(man.get("version") or time.strftime("%Y%m%d"))
    man["exported_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    man["exported_from"] = sys.platform
    man["source"] = "export"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST, json.dumps(man, ensure_ascii=False, indent=1))
        for key, fn in SECTION_FILES.items():
            p = _section_path(key)
            if p and os.path.isfile(p):
                try:
                    zf.writestr(fn, open(p, "rb").read())
                except OSError:
                    pass
    return buf.getvalue()


# ---------------- 首启动 / 升级同步 ----------------
def _dir_hash(dirpath):
    """内置包内容摘要：文件内容变了（版本号没变）也能识别出需要同步"""
    h = hashlib.md5()
    for key in sorted(SECTION_FILES):
        fn = SECTION_FILES[key]
        h.update(fn.encode("utf-8"))
        p = os.path.join(dirpath, fn)
        try:
            with open(p, "rb") as f:
                h.update(f.read())
        except OSError:
            h.update(b"")
    return h.hexdigest()


def _copy_builtin(target, version, pack_hash, only_missing=False):
    """把内置包复制到 target；only_missing=True 时只补 target 里缺的分节"""
    copied = False
    for fn in SECTION_FILES.values():
        src = os.path.join(BUILTIN_DIR, fn)
        if not os.path.isfile(src):
            continue
        dst = os.path.join(target, fn)
        if only_missing and os.path.isfile(dst):
            continue
        shutil.copy2(src, dst)
        copied = True
    return copied


def boot():
    """启动时调用：用户目录不存在则用内置初始化；内置包变了（版本或内容）则同步

    用户导入过自己的规则包时不被内置覆盖，只补齐内置新增的分节。
    """
    try:
        _ensure_dir(CURRENT_DIR)
        um = _manifest_path()
        bm = os.path.join(BUILTIN_DIR, MANIFEST)
        b_ver = ""
        if os.path.isfile(bm):
            try:
                b_ver = str(_read_json(bm).get("version") or "")
            except Exception:
                b_ver = ""
        b_hash = _dir_hash(BUILTIN_DIR) if os.path.isdir(BUILTIN_DIR) else ""

        if not os.path.isfile(um):
            if os.path.isdir(BUILTIN_DIR):
                _copy_builtin(CURRENT_DIR, b_ver, b_hash)
                man = _read_json(bm) if os.path.isfile(bm) else {"version": b_ver}
                man["source"] = "builtin"
                man["builtin_version"] = b_ver
                man["builtin_hash"] = b_hash
                man["synced_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                _write_json(um, man)
            reload()
            return True

        cur = manifest()
        if cur.get("source") == "builtin":
            changed = (b_ver and str(cur.get("version") or "") != b_ver) or \
                      (b_hash and str(cur.get("builtin_hash") or "") != b_hash)
            if changed:
                _copy_builtin(CURRENT_DIR, b_ver, b_hash)
                man = _read_json(bm) if os.path.isfile(bm) else {"version": b_ver}
                man["source"] = "builtin"
                man["builtin_version"] = b_ver
                man["builtin_hash"] = b_hash
                man["synced_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                _write_json(um, man)
                reload()
                return True
        elif os.path.isdir(BUILTIN_DIR) and _copy_builtin(CURRENT_DIR, b_ver, b_hash, only_missing=True):
            # 用户规则优先，仅补齐内置新增的分节
            cur["builtin_hash"] = b_hash
            cur["builtin_version"] = b_ver
            _write_json(um, cur)
            reload()
            return True
    except Exception:
        pass
    reload()
    return False


# ---------------- 便捷查询：给业务模块用 ----------------
def _types_of(sect_name):
    return section(sect_name).get("types") or []


def _norm_qtype(qtype):
    return str(qtype or "").strip()


# 适配包里的题型名 → 题目解析内部判定口径（question_analyzer 的 7 类）
# 只做归类映射，不改判定逻辑：社会现象/态度观点/漫画/材料题最终都是「综合分析」，
# 由软件再按题干特征细分成社会现象 / 态度观点，试卷口径与笔记口径一致。
_DETECT_ALIAS = {
    "社会现象": "综合分析",
    "态度观点": "综合分析",
    "漫画": "综合分析",
    "材料题": "综合分析",
    "反驳": "综合分析",
    "现场模拟": "情景模拟",
    "情景模拟": "情景模拟",
    "串词题": "演讲串词",
    "串词": "演讲串词",
    "演讲": "演讲串词",
    "排序": "组织管理",
    "排序统筹": "组织管理",
    "组织管理": "组织管理",
    "应急应变": "应急应变",
    "人际关系": "人际关系",
    "自我认知": "自我认知",
}

# 关键词散分不足以改判：只有硬信号（四步判别命中）或密集命中才参与决策
_BONUS_MIN = 3.0


def detect_bonus(question):
    """题目识别规则给的加权分：{判定口径题型: 分值增加}

    分值 < _BONUS_MIN 的题型会被丢弃，避免单个泛词（如"领导""沟通"）改变原判定。
    """
    q = question or ""
    if not q:
        return {}
    bonus = {}

    def _add(name, score):
        key = _DETECT_ALIAS.get(_norm_qtype(name), _norm_qtype(name))
        if key:
            bonus[key] = bonus.get(key, 0.0) + score

    # 1) 四步判别：命中即视为硬信号
    for r in judge_order():
        try:
            if r["re"].search(q):
                _add(r["qtype"], 5.0)
        except Exception:
            continue
    # 2) 题型关键词与题干模式
    for t in _types_of("question_detect"):
        name = _norm_qtype(t.get("题型") or t.get("qtype"))
        if not name:
            continue
        weight = float(t.get("weight") or 1)
        score = 0.0
        for kw in (t.get("keywords") or []):
            kw = str(kw or "").strip()
            if kw and kw in q:
                score += weight
        for pat in (t.get("patterns") or []):
            try:
                if re.search(str(pat), q):
                    score += weight * 1.5
            except re.error:
                continue
        if score:
            _add(name, score)
    # 3) 显式打分规则
    for r in (section("question_detect").get("score_rules") or []):
        try:
            pat = str(r.get("pattern") or r.get("pattern_re") or "")
            name = _norm_qtype(r.get("题型") or r.get("qtype"))
            add = float(r.get("score") or r.get("weight") or 3)
            if pat and name and re.search(pat, q):
                _add(name, add)
        except (re.error, TypeError, ValueError):
            continue
    return {k: v for k, v in bonus.items() if v >= _BONUS_MIN}


def judge_order():
    """题型判别顺序（笔记里的四步判别），返回 [{step, when, qtype, re}]"""
    out = []
    for r in (section("question_detect").get("judge_order") or []):
        pat = str(r.get("pattern") or "")
        if not pat:
            continue
        try:
            out.append({"step": r.get("步骤") or r.get("step") or 0,
                        "when": r.get("判断") or r.get("when") or "",
                        "qtype": _norm_qtype(r.get("题型") or r.get("qtype")),
                        "re": re.compile(pat)})
        except re.error:
            continue
    out.sort(key=lambda x: x["step"] if isinstance(x["step"], (int, float)) else 0)
    return out


def infer_qtype(question):
    """按「题目识别规则」的关键词兜底判题型（分节未命中时用）"""
    q = str(question or "")
    if not q:
        return ""
    best, best_hit = "", 0
    for t in _types_of("question_detect"):
        name = _norm_qtype(t.get("题型") or t.get("qtype"))
        if not name:
            continue
        hit = 0
        for kw in (t.get("keywords") or []):
            kw = str(kw).strip()
            if kw and kw in q:
                hit += 1
        for kw in (t.get("题干特征") or []):
            kw = str(kw).strip()
            if kw and kw in q:
                hit += 1
        if hit > best_hit:
            best, best_hit = name, hit
    return best if best_hit >= 1 else ""


def easy_confuse(qtype):
    """该题型的易混区分说明（笔记口径），没有则空串"""
    name = _norm_qtype(qtype)
    for t in _types_of("question_detect"):
        if _norm_qtype(t.get("题型") or t.get("qtype")) == name:
            return str(t.get("易混区分") or t.get("distinguish") or "").strip()
    return ""


_FRAME_KEYS = ("框架", "总框架", "作答思路", "答题框架")
_VARIANT_KEYS = ("变体", "类型变体")
_SKIP_KEYS = ("题型", "qtype", "别名", "source", "id", "version", "审题") + _FRAME_KEYS + _VARIANT_KEYS


def _as_point_list(v):
    """把各种写法的要点收成字符串列表"""
    out = []
    if isinstance(v, str):
        if v.strip():
            out.append(v.strip())
    elif isinstance(v, list):
        for x in v:
            out.append(str(x).strip() if not isinstance(x, dict) else str(x.get("point") or x.get("内容") or "").strip())
    elif isinstance(v, dict):
        for k, x in v.items():
            if isinstance(x, list):
                out.extend("%s：%s" % (k, str(i).strip()) for i in x if str(i).strip())
            elif str(x or "").strip():
                out.append("%s：%s" % (k, str(x).strip()))
    return [x for x in out if x]


def framework(qtype):
    """某题型的适配框架：{stages:[{name, points:[...]}], variants:{...}, notes:str}

    兼容多种写法：主框架取 框架/总框架/作答思路；审题（dict 或 list）作为首个环节；
    变体取 变体/类型变体（含 结构/要点 或纯文本）；其余说明性字段统一进 notes。
    """
    name = _norm_qtype(qtype)
    if not name:
        return None
    for t in _types_of("frameworks"):
        tname = _norm_qtype(t.get("题型") or t.get("qtype"))
        alias = _norm_qtype(t.get("别名"))
        if tname != name and alias != name:
            continue
        stages, variants, notes = [], {}, []

        # 审题要点前置为第一环节，便于和「审题」环节对齐
        shen_pts = _as_point_list(t.get("审题"))
        if shen_pts:
            stages.append({"name": "审题", "points": shen_pts})

        fr = None
        for k in _FRAME_KEYS:
            v = t.get(k)
            if isinstance(v, (list, dict)) and v:
                fr = v
                break
        if isinstance(fr, list):
            for s in fr:
                if not isinstance(s, dict):
                    if str(s or "").strip():
                        notes.append(str(s).strip())
                    continue
                label = str(s.get("label") or s.get("name") or s.get("stage_name") or "").strip()
                pts = _as_point_list(s.get("points") or s.get("要点") or s.get("内容"))
                if label and (pts or label):
                    stages.append({"name": label, "points": pts})
        elif isinstance(fr, dict):
            for k, v in fr.items():
                if isinstance(v, dict):
                    variants[str(k)] = {
                        "stages": [str(x).strip() for x in (v.get("结构") or v.get("stages") or []) if str(x).strip()],
                        "points": _as_point_list(v.get("要点") or v.get("points")),
                        "note": str(v.get("参考") or v.get("note") or "").strip(),
                    }
                else:
                    variants[str(k)] = {"stages": [], "points": [], "note": str(v or "").strip()}

        for k in _VARIANT_KEYS:
            v = t.get(k)
            if isinstance(v, dict):
                for vk, vv in v.items():
                    if vk in variants:
                        continue
                    if isinstance(vv, dict):
                        variants[str(vk)] = {
                            "stages": [str(x).strip() for x in (vv.get("结构") or vv.get("stages") or []) if str(x).strip()],
                            "points": _as_point_list(vv.get("要点") or vv.get("points")),
                            "note": str(vv.get("参考") or vv.get("note") or "").strip(),
                        }
                    else:
                        variants[str(vk)] = {"stages": [], "points": [], "note": str(vv or "").strip()}

        for k, v in t.items():
            if k in _SKIP_KEYS:
                continue
            if isinstance(v, str) and v.strip():
                notes.append("%s：%s" % (k, v.strip()) if k not in ("共性与总纲", "总纲", "总思路", "参考") else v.strip())
            elif isinstance(v, list):
                pts = _as_point_list(v)
                if pts:
                    notes.append("%s：%s" % (k, "；".join(pts)) if k not in ("注意事项", "三类现象基础思路", "答题原则", "核心原则") else "；".join(pts))
        return {"qtype": name, "stages": stages, "variants": variants,
                "notes": "\n".join(x for x in notes if x), "source": t.get("source") or ""}
    return None


# 适配框架环节名 → 软件固定五环节（名称对不上时按语义归位，带原环节名前缀）
_STAGE_ALIAS = {
    "审题": "审题",
    "简单入题": "提观点", "简单切入": "提观点", "破题": "提观点", "报题目": "提观点",
    "开头": "提观点", "前期准备": "提观点", "准备": "提观点", "筹备": "提观点",
    "提观点": "提观点", "表态": "提观点", "定调": "提观点", "入题": "提观点",
    "重分析": "重分析", "分析": "重分析", "中期开展": "重分析", "开展": "重分析",
    "解决问题": "重分析", "安排": "重分析", "调研": "重分析", "宣传": "重分析",
    "抓落实": "抓落实", "对策": "抓落实", "后期工作": "抓落实", "积极提升": "抓落实",
    "总结提升": "抓落实", "后续": "抓落实", "提升": "抓落实",
    "结尾": "结尾", "做结尾": "结尾", "收束": "结尾", "总结": "结尾",
}


def _stage_target(label, want):
    """环节名归位：先精确/互相包含，再查语义别名"""
    label = str(label or "").strip()
    if not label or not want:
        return ""
    for w in want:
        if label == w or label in w or w in label:
            return w
    for k, v in _STAGE_ALIAS.items():
        if (k == label or (len(k) >= 2 and k in label)) and v in want:
            return v
    return ""


def stage_point_addons(qtype, stage_names=None):
    """把适配框架里的要点按环节对齐，返回 {环节名: [要点]}（只补要点，不动题型原有环节）

    环节名对不上的题型（如组织管理「前期准备/中期开展/后期工作」）按语义归位，
    要点前加原环节名前缀，避免老师误读。
    """
    fw = framework(qtype)
    if not fw:
        return {}
    want = [str(s).strip() for s in (stage_names or []) if str(s or "").strip()]
    out = {}
    for st in fw.get("stages") or []:
        label = st["name"]
        points = [p for p in (st.get("points") or []) if str(p).strip()]
        if not points:
            continue
        target = _stage_target(label, want) if want else label
        if want and target and target in want:
            if target != label:
                points = ["%s：%s" % (label, p) for p in points]
        else:
            target = label if label else (want[-1] if want else "")
        if not target:
            continue
        out.setdefault(target, []).extend(points)
    for name, pts in out.items():
        seen, uniq = set(), []
        for p in pts:
            if p not in seen:
                seen.add(p)
                uniq.append(p)
        out[name] = uniq
    return out


def framework_notes(qtype, include_stages=True):
    """框架提示：先给环节结构摘要，再给说明与变体"""
    fw = framework(qtype)
    if not fw:
        return []
    out = []
    if include_stages and fw.get("stages"):
        seg = []
        for st in fw["stages"]:
            pts = st.get("points") or []
            if pts:
                seg.append("%s（%s）" % (st["name"], "；".join(pts[:3])))
            else:
                seg.append(st["name"])
        out.append("答题框架：" + " → ".join(seg))
    if fw.get("notes"):
        out.extend([ln for ln in fw["notes"].split("\n") if ln.strip()])
    for k, v in (fw.get("variants") or {}).items():
        bits = []
        if v.get("stages"):
            bits.append("→".join(v["stages"]))
        if v.get("points"):
            bits.append("；".join(v["points"]))
        if v.get("note"):
            bits.append(v["note"])
        if bits:
            out.append("%s：%s" % (k, "｜".join(bits)))
    return out


def tips_for(qtype, limit=5):
    """本地点评用：该题型的常见问题与建议（按命中优先返回，不命中返回通用前缀若干条）"""
    name = _norm_qtype(qtype)
    rows = section("comment_rules").get("problems") or []
    same, other = [], []
    for r in rows:
        if not isinstance(r, dict):
            continue
        t = _norm_qtype(r.get("题型"))
        item = {
            "qtype": t,
            "stage": str(r.get("环节") or "").strip(),
            "problem": str(r.get("问题") or "").strip(),
            "show": str(r.get("表现") or "").strip(),
            "example": str(r.get("举例") or "").strip(),
            "reason": str(r.get("原因") or "").strip(),
            "advice": [str(x).strip() for x in (r.get("建议") or []) if str(x).strip()],
            "source": str(r.get("source") or "").strip(),
        }
        if name and (t == name or t in name or name in t):
            same.append(item)
        else:
            other.append(item)
    out = same or other
    return out[:max(0, int(limit or 0))]


def methods(limit=0):
    rows = []
    for r in (section("comment_rules").get("method") or []):
        if isinstance(r, dict):
            rows.append({
                "item": str(r.get("项") or "").strip(),
                "content": str(r.get("内容") or "").strip(),
                "source": str(r.get("source") or "").strip(),
            })
    return rows[:int(limit)] if limit else rows


def segment_rules():
    """段落环节识别规则：(role, sub, compiled) 列表；适配规则前置，命中优先"""
    out = []
    for r in (section("segment_rules").get("rules") or []):
        if not isinstance(r, dict):
            continue
        role = str(r.get("role") or "").strip()
        pat = str(r.get("pattern") or "")
        if not role or not pat:
            continue
        try:
            out.append((role, str(r.get("sub") or "").strip(), re.compile(pat), "adapt"))
        except re.error:
            continue
    for role, sub, pat in _BUILTIN_SEG:
        out.append((role, sub, _BUILTIN_SEG_RE[pat], "builtin"))
    return out


_BUILTIN_SEG = [(r["role"], r["sub"], r["pattern"]) for r in DEFAULT_SEGMENT_RULES["rules"]]
_BUILTIN_SEG_RE = {r["pattern"]: re.compile(r["pattern"]) for r in DEFAULT_SEGMENT_RULES["rules"]}


def filler_words():
    """口头禅词表：默认 + 适配追加，去重保序"""
    out = []
    seen = set()
    for w in (section("text_fix").get("fillers") or []):
        w = str(w or "").strip()
        if w and w not in seen:
            seen.add(w)
            out.append(w)
    return out


def fix_pairs():
    """纠错替换对：[(错, 对)]，适配的排在前面（优先命中）"""
    out, seen = [], set()
    for r in (section("text_fix").get("pairs") or []):
        if isinstance(r, dict):
            w = str(r.get("错") or r.get("from") or "").strip()
            t = str(r.get("对") or r.get("to") or "").strip()
        elif isinstance(r, (list, tuple)) and len(r) >= 2:
            w, t = str(r[0]).strip(), str(r[1]).strip()
        else:
            continue
        if w and t and w != t and w not in seen:
            seen.add(w)
            out.append((w, t))
    return out


def question_markers():
    """题干题号样式正则（编译列表）"""
    out = []
    for pat in (section("question_detect").get("question_markers") or []) + \
               (section("bank_parse").get("question_start") or []):
        try:
            out.append(re.compile(str(pat)))
        except re.error:
            continue
    return out


def revision():
    """规则版本号：业务模块可用它判断缓存是否需要失效"""
    m = manifest()
    return "%s|%s|%s" % (m.get("id") or "", m.get("version") or "", m.get("imported_at") or m.get("synced_at") or "")


# ---------------- 从笔记里提取题目 ----------------
def _iter_lines(text):
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\u3000", " ")
    return [ln.rstrip() for ln in t.split("\n")]


# 题本里单独一行的出处/来源，如「【2026年1月8日内蒙古阿拉善盟事业单位面试题】」
_EXAM_LINE_RE = re.compile(
    r"^(?:\d{1,3}\s*[\.、\)）]\s*)?[【\[（(]\s*((?:19|20)\d{2}\s*年[^【】\[\]]{4,90})[】\]）)]\s*$")


_ANSWER_SMELL = r"各位考官|^我(?:会|作为|们|想|心里)|我心里|我会以|下面我|首先我"


def _answer_like(q):
    """题干不可能长这样：第一人称作答口吻 + 没有问句"""
    if "？" in q or "?" in q:
        return False
    return bool(re.search(_ANSWER_SMELL, q))


def _strong_question(s, starts):
    """答案段落里再起新题，必须是很硬的问句信号，避免把逐字稿里的假设句当题干"""
    if "？" in s or "?" in s:
        return True
    if any(p.search(s) for p in starts):
        return True
    if s.startswith(("请", "假如", "如果", "谈谈", "说说", "作为")):
        return True
    for w in ("你怎么看", "你怎么理解", "你怎么处理", "你怎么办", "你怎么做", "你会怎么做",
              "你作为", "谈谈你的看法", "请谈谈", "请现场模拟", "给你的启示", "谈谈启示"):
        if w in s:
            return True
    return False


def _looks_like_question(q, ask_words):
    """题干特征：带问号，或含设问词，避免把笔记里的板书短句当成题目"""
    if not q:
        return False
    if "？" in q or "?" in q:
        return True
    if any(w and w in q for w in (ask_words or [])):
        return True
    return q.startswith(("请", "谈", "说说", "谈谈", "如何", "假如", "如果", "作为"))


def extract_questions(text, default_qtype="", source="", strict=True):
    """从课堂笔记 / 题本 / 逐字稿里按规则抽出题目（题干 + 出处 + 参考答案）

    返回 [{question, exam, qtype, qtype_reason, ref, source, section, line}]
    只做抽取，不入库；是否入库由界面确认。
    """
    cfg = section("bank_parse")
    starts = []
    for pat in (cfg.get("question_start") or []):
        try:
            starts.append(re.compile(str(pat)))
        except re.error:
            continue
    answers = []
    for pat in (cfg.get("answer_start") or []):
        try:
            answers.append(re.compile(str(pat)))
        except re.error:
            continue
    noise = []
    for pat in (cfg.get("noise") or []):
        try:
            noise.append(re.compile(str(pat)))
        except re.error:
            continue
    heads = []
    for pat in (cfg.get("section_heads") or []):
        try:
            heads.append(re.compile(str(pat)))
        except re.error:
            continue
    ask_words = [str(w) for w in (cfg.get("ask_words") or []) if str(w).strip()]
    hints = cfg.get("qtype_hints") or {}
    min_len = int(cfg.get("min_len") or 12)
    max_len = int(cfg.get("max_len") or 1500)

    lines = _iter_lines(text)
    items = []
    cur = None
    cur_section = ""
    pending_exam = ""     # 题本里的「【2026年x月x日xx面试题】」这类出处行，挂到下一题
    mode = "q"            # q=题干 c=答案
    for idx, ln in enumerate(lines):
        s = ln.strip()
        if not s:
            continue
        if any(p.search(s) for p in noise) and len(s) < 30:
            continue
        m_exam = _EXAM_LINE_RE.match(s)
        if m_exam:
            pending_exam = (m_exam.group(1) or "").strip()
            if cur:
                items.append(cur)   # 出处行是分题边界：上一题到此收尾
                cur = None
            mode = "q"              # 出处行之后必然是新题的题干
            continue
        is_head = any(p.search(s) for p in heads) and len(s) <= 40 and not any(p.search(s) for p in starts)
        is_start = any(p.search(s) for p in starts)
        looks_q = (len(s) >= min_len and any(w in s for w in ask_words)
                   and (s.endswith(("？", "?", "。")) or "？" in s or "?" in s))
        is_answer = any(p.search(s) for p in answers)

        if is_head and not is_start:
            cur_section = s.strip("【】[]# 　")
            continue

        if is_answer and cur:
            mode = "c"
            cur["_ref"].append(answers_next(s, answers))
            continue

        strong_q = _strong_question(s, starts) and len(s) >= min_len
        new_q = bool(is_start)
        if not new_q:
            if not cur:
                new_q = looks_q
            elif mode == "c":
                new_q = strong_q
        if new_q:
            if cur:
                items.append(cur)
            body = s
            for p in starts:
                m = p.search(body)
                if m and m.start() == 0:
                    body = body[m.end():]
                    break
            cur = {"_q_text": body.strip(), "_ref": [], "_section": cur_section,
                   "_exam": pending_exam, "line": idx + 1}
            pending_exam = ""
            mode = "q"
            continue

        if not cur:
            # 题干前面没编号，但本身是问句，也收
            if looks_q:
                cur = {"_q_text": s, "_ref": [], "_section": cur_section,
                       "_exam": pending_exam, "line": idx + 1}
                pending_exam = ""
                mode = "q"
            continue

        if mode == "q":
            if is_answer:
                mode = "c"
                cur["_ref"].append(answers_next(s, answers))
            elif s.startswith(("【", "[", "〔")) and len(s) <= 40 and "？" not in s and "?" not in s:
                # 「【答题示范稿】完整版本」这类标记行：题干到此为止，后面都是解析
                mode = "c"
                mk = re.match(r"^[【\[〔]([^】\]〕]{1,24})[】\]〕]", s)
                cur["_ref"].append((mk.group(1) if mk else s).strip())
            else:
                # 题干续行：遇到明显是解析/说明的段落就切到答案
                cur["_q_text"] = (cur["_q_text"] + " " + s).strip()
        else:
            cur["_ref"].append(s)
    if cur:
        items.append(cur)

    out = []
    for it in items:
        q = re.sub(r"\s{2,}", " ", str(it["_q_text"] or "")).strip()
        if len(q) < min_len:
            continue
        if strict and not _looks_like_question(q, ask_words):
            continue
        if _answer_like(q):
            continue
        # 题号 / 出处标记行（如「【模考第1题】【2026年6月13日陕西…】」）不属于题干
        q = re.sub(r"^(?:\s*【[^】]{0,48}】)+", "", q).strip()
        if len(q) < min_len or _answer_like(q):
            continue
        # 题干后面误粘了作答内容：在作答标志处截断
        m_ans = re.search(r"各位考官|如果我有幸|作为一名(?:年轻|青年|新|基层)?干部.{0,6}我会|\s我会", q)
        if m_ans and m_ans.start() >= 20 and "？" not in q[:m_ans.start()] and "?" not in q[:m_ans.start()]:
            q = q[:m_ans.start()].strip()
        if len(q) < min_len:
            continue
        q = q[:max_len]
        exam = ""
        m = re.search(r"[（(]\s*((?:19|20)\d{2}[^（）()]{4,40}?(?:面试|考试|真题)[^（）()]{0,20})[）)]\s*$", q)
        if m:
            exam = m.group(1).strip()
            q = q[:m.start()].strip()
        if not exam:
            exam = str(it.get("_exam") or "").strip()
        qtype = ""
        for name, kws in hints.items():
            if any(str(k) in (it.get("_section") or "") for k in (kws or [])):
                qtype = name
                break
        reason = ""
        if qtype:
            reason = "按笔记分节「%s」归类" % it.get("_section")
        else:
            qtype = default_qtype
            if qtype:
                reason = "规则未命中题型提示，按题干关键词判定"
            else:
                qtype = infer_qtype(q)
                reason = "按题目识别规则的关键词判定" if qtype else "未判定题型"
        ref = "\n".join([x for x in it["_ref"] if x.strip()])
        # 答题结构按适配框架补齐，入题库后直接可用
        structure, keys = [], []
        fw = framework(qtype) if qtype else None
        for st in ((fw or {}).get("stages") or []):
            nm = str(st.get("name") or "").strip()
            if not nm:
                continue
            pts = [str(x).strip() for x in (st.get("points") or []) if str(x).strip()]
            structure.append({"stage": nm, "label": "", "points": pts})
            keys.extend(pts)
        out.append({
            "question": q,
            "exam": [exam] if exam else [],
            "qtype": qtype,
            "qtype_reason": reason,
            "structure": structure,
            "keys": keys[:40],
            "ref": ref[:6000],
            "section": it.get("_section") or "",
            "source": source,
            "line": it.get("line"),
        })
    return out


def answers_next(line, answers):
    """答案段标记行只保留标记之后的内容"""
    for p in answers:
        m = p.search(line)
        if m and m.start() == 0:
            return line[m.end():].strip()
    return line


# ---------------- 自检 ----------------
if __name__ == "__main__":
    import pprint
    print("BASE =", BASE)
    print("DATA =", DATA)
    print("内置适配目录存在:", os.path.isdir(BUILTIN_DIR))
    boot()
    st = status()
    pprint.pprint({k: v for k, v in st.items() if k != "sections"})
    for s in st["sections"]:
        print("  -", s["label"], s["count"], s["from"])
    print("题型数量（识别规则）:", len(section("question_detect").get("types") or []))
    print("框架数量:", len(section("frameworks").get("types") or []))
    print("点评条数:", len(section("comment_rules").get("problems") or []),
          "方法:", len(section("comment_rules").get("method") or []))
    print("纠错对:", len(fix_pairs()), "口头禅:", len(filler_words()))
