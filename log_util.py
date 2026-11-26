# -*- coding: utf-8 -*-
"""
面试点评器 - 统一日志模块
===================================================================
设计目标：出问题时不需要用户复现+截图，直接把日志文件发过来就能定位。

* 目录：%LOCALAPPDATA%\\InterviewReviewer\\logs（可用环境变量 IR_LOG_DIR 覆盖，
  便于开发自测或把日志放到 U 盘里）
* 文件：app-YYYYMMDD.log，按天新建；单文件 2MB，滚动保留 5 份（app-xxx.log.1 ~ .5）
* 格式：时间 [级别] 模块/线程: 消息（异常自动带完整 traceback）
* 控制台：同时输出到 stdout/stderr（打包成 exe 无控制台时自动跳过）
* 接口：get_logger / install_excepthooks / log_dir / log_stats / tail_text /
        mask_secret / open_log_dir

线程安全：handler 全局只初始化一次，多线程/多进程各自持文件句柄（Windows 下
同一文件多进程追加写入是可接受的，且本项目常态只有一个后端进程）。
"""
import os
import sys
import time
import logging
import threading
import traceback
from logging.handlers import RotatingFileHandler

APP_FOLDER = "InterviewReviewer"
MAX_BYTES = 2 * 1024 * 1024          # 单文件 2MB
BACKUP_COUNT = 5                     # 保留 5 份历史
FILE_PREFIX = "app-"
LOG_FMT = "%(asctime)s [%(levelname)s] %(name)s/%(threadName)s: %(message)s"
DATE_FMT = "%Y-%m-%d %H:%M:%S"

_lock = threading.RLock()
_file_handler = None                # type: _DailyRotatingFileHandler | None
_console_handler = None
_inited = False


# ------------------------------------------------------------------
# 路径
# ------------------------------------------------------------------
def log_dir():
    """日志目录（不存在则创建）；IR_LOG_DIR 优先，其余情况落在用户本地数据目录"""
    env = (os.environ.get("IR_LOG_DIR") or "").strip()
    if env:
        path = os.path.abspath(os.path.expanduser(env))
    else:
        root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        path = os.path.join(root, APP_FOLDER, "logs")
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass
    return path


def _day_str():
    return time.strftime("%Y%m%d")


def current_log_file():
    """当前应写入的日志文件（按天命名）"""
    return os.path.join(log_dir(), "%s%s.log" % (FILE_PREFIX, _day_str()))


# ------------------------------------------------------------------
# 按天滚动的 RotatingFileHandler
# ------------------------------------------------------------------
class _DailyRotatingFileHandler(RotatingFileHandler):
    """在 RotatingFileHandler（按大小滚动）基础上叠加“按天换文件”：

    跨天时把底层流切到 app-YYYYMMDD.log 新建的那一份，避免程序连续运行
    多日时把日志全挤在同一天的文件里，也避免跨天文件名与实际日期不符。
    """

    def __init__(self, directory, max_bytes=MAX_BYTES, backup_count=BACKUP_COUNT):
        self._dir = directory
        self._day = _day_str()
        RotatingFileHandler.__init__(
            self,
            os.path.join(directory, "%s%s.log" % (FILE_PREFIX, self._day)),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
            delay=False,
        )

    def _roll_day_if_needed(self):
        day = _day_str()
        if day == self._day:
            return
        self.acquire()
        try:
            day = _day_str()
            if day == self._day:
                return
            if self.stream is not None:
                try:
                    self.stream.flush()
                    self.stream.close()
                except Exception:
                    pass
                self.stream = None
            self._day = day
            self.baseFilename = os.path.abspath(
                os.path.join(self._dir, "%s%s.log" % (FILE_PREFIX, day)))
            # 新一天从 .log 开始写，旧文件保留原样（历史文件仍受 5 份上限约束）
            self.stream = self._open()
        finally:
            self.release()

    def emit(self, record):
        try:
            self._roll_day_if_needed()
        except Exception:
            pass
        try:
            RotatingFileHandler.emit(self, record)
        except Exception:
            # 日志本身绝不能把业务打挂：磁盘满、权限变更时静默降级
            self.handleError(record)


def _console_stream():
    """取一个可用的控制台流；无控制台（pythonw / 双击 exe）时返回 None"""
    for cand in (sys.stderr, sys.stdout):
        try:
            if cand is not None and hasattr(cand, "write"):
                cand.write("")
                return cand
        except Exception:
            continue
    return None


def _level_from_env(default=logging.INFO):
    raw = (os.environ.get("IR_LOG_LEVEL") or "").strip().upper()
    if not raw:
        return default
    return getattr(logging, raw, default)


def _init_handlers():
    """初始化全局 handler（只做一次）；任何一步失败都不影响程序启动"""
    global _file_handler, _console_handler, _inited
    with _lock:
        if _inited:
            return
        _inited = True
        try:
            _file_handler = _DailyRotatingFileHandler(log_dir())
            _file_handler.setLevel(logging.DEBUG)
            _file_handler.setFormatter(logging.Formatter(LOG_FMT, DATE_FMT))
        except Exception:
            _file_handler = None
            try:
                sys.stderr.write("[WARN] 日志文件初始化失败: %s\n" % traceback.format_exc(limit=2))
            except Exception:
                pass
        stream = _console_stream()
        if stream is not None:
            try:
                _console_handler = logging.StreamHandler(stream)
                _console_handler.setLevel(_level_from_env())
                _console_handler.setFormatter(logging.Formatter(LOG_FMT, DATE_FMT))
            except Exception:
                _console_handler = None


def get_logger(name="app"):
    """获取带文件+控制台双输出的 logger（同名多次调用返回同一实例）"""
    _init_handlers()
    logger = logging.getLogger("iln.%s" % (name or "app"))
    if getattr(logger, "_iln_ready", False):
        return logger
    with _lock:
        if getattr(logger, "_iln_ready", False):
            return logger
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        if _file_handler is not None:
            logger.addHandler(_file_handler)
        if _console_handler is not None:
            logger.addHandler(_console_handler)
        if not logger.handlers:
            logger.addHandler(logging.NullHandler())
        logger._iln_ready = True
    return logger


# ------------------------------------------------------------------
# 未捕获异常兜底
# ------------------------------------------------------------------
def _format_exc(exc_type, exc_value, exc_tb):
    try:
        return "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    except Exception:
        return "%s: %s" % (exc_type, exc_value)


def install_excepthooks(logger=None):
    """接管 sys.excepthook / threading.excepthook，把主线程与子线程里没人接的
    异常写进日志（否则打包成 exe 后这些异常会静默丢掉，用户只看到“闪退”）。

    会在日志里标记 Fatal，并保留原 hook 的默认输出行为（打印到控制台）。
    """
    log = logger or get_logger("crash")
    prev_sys_hook = sys.excepthook

    def _sys_hook(exc_type, exc_value, exc_tb):
        try:
            if not issubclass(exc_type, KeyboardInterrupt):
                log.error("未捕获异常（主线程，程序可能退出）\n%s",
                          _format_exc(exc_type, exc_value, exc_tb))
        except Exception:
            pass
        try:
            prev_sys_hook(exc_type, exc_value, exc_tb)
        except Exception:
            pass

    try:
        sys.excepthook = _sys_hook
    except Exception:
        pass

    if hasattr(threading, "excepthook"):
        prev_thr_hook = threading.excepthook

        def _thr_hook(args):
            try:
                name = getattr(getattr(args, "thread", None), "name", "?")
                if not issubclass(getattr(args, "exc_type", Exception) or Exception,
                                  KeyboardInterrupt):
                    log.error("未捕获异常（子线程 %s）\n%s", name,
                              _format_exc(args.exc_type, args.exc_value, args.exc_traceback))
            except Exception:
                pass
            try:
                prev_thr_hook(args)
            except Exception:
                pass

        try:
            threading.excepthook = _thr_hook
        except Exception:
            pass
    return True


# ------------------------------------------------------------------
# 查询 / 读取 / 打开
# ------------------------------------------------------------------
def _size_of(path):
    try:
        return os.path.getsize(path)
    except Exception:
        return 0


def log_stats():
    """日志目录概况：目录、当前文件、当前文件大小、目录总大小、文件清单"""
    directory = log_dir()
    cur = current_log_file()
    files = []
    total = 0
    try:
        names = os.listdir(directory)
    except Exception:
        names = []
    for fn in sorted(names):
        if not (fn.startswith(FILE_PREFIX) and ".log" in fn):
            continue
        full = os.path.join(directory, fn)
        if not os.path.isfile(full):
            continue
        sz = _size_of(full)
        total += sz
        try:
            mt = time.strftime(DATE_FMT, time.localtime(os.path.getmtime(full)))
        except Exception:
            mt = ""
        files.append({"name": fn, "path": full, "size": sz, "size_text": _fmt_size(sz),
                      "mtime": mt})
    cur_exists = os.path.isfile(cur)
    return {
        "dir": directory,
        "current": cur if cur_exists else "",
        "current_name": os.path.basename(cur),
        "current_size": _size_of(cur) if cur_exists else 0,
        "total_size": total,
        "total_size_text": _fmt_size(total),
        "files": files,
        "max_bytes": MAX_BYTES,
        "backup_count": BACKUP_COUNT,
    }


def _fmt_size(n):
    try:
        n = float(n or 0)
    except Exception:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0
    return "%.1f GB" % n


def tail_text(lines=200, max_bytes=512 * 1024):
    """读取当前日志文件末尾若干行（纯文本）；文件不存在或读失败时返回说明文本"""
    try:
        lines = int(lines)
    except Exception:
        lines = 200
    lines = max(1, min(lines, 5000))
    path = current_log_file()
    if not os.path.isfile(path):
        return "(日志文件尚未生成：%s)" % path
    try:
        size = _size_of(path)
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(-max_bytes, os.SEEK_END)
            raw = f.read()
        text = raw.decode("utf-8", errors="replace")
        if size > max_bytes:
            # 从中间截断时首行可能不完整，丢掉
            idx = text.find("\n")
            if idx > 0:
                text = text[idx + 1:]
        arr = text.splitlines()
        if len(arr) > lines:
            arr = arr[-lines:]
        return "\n".join(arr)
    except Exception as e:
        return "(读取日志失败：%s: %s)" % (type(e).__name__, e)


def open_log_dir():
    """用资源管理器打开日志目录，返回 (ok, 说明)"""
    directory = log_dir()
    try:
        os.makedirs(directory, exist_ok=True)
        if os.name == "nt":
            os.startfile(directory)          # noqa: S606  (Windows 专用)
        elif sys.platform == "darwin":
            os.system('open "%s"' % directory)
        else:
            os.system('xdg-open "%s"' % directory)
        return True, directory
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)


# ------------------------------------------------------------------
# 脱敏
# ------------------------------------------------------------------
def mask_secret(text, keep_tail=False):
    """把文本里的 api_key / Bearer token 统一脱敏成 sk-***，
    避免日志把用户的密钥写进文件再被发给别人。"""
    if text is None:
        return ""
    s = str(text)
    try:
        import re as _re
        s = _re.sub(r"(sk-[A-Za-z0-9_\-]{4,})", "sk-***", s)
        s = _re.sub(r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?bearer\s+)[^\"'\s,}]+",
                    r"\1sk-***", s)
        s = _re.sub(r"(?i)(api[_-]?key[\"']?\s*[:=]\s*[\"']?)[A-Za-z0-9_\-]{6,}",
                    r"\1sk-***", s)
        s = _re.sub(r"(?i)([?&](?:key|api_?key|token)=)[A-Za-z0-9_\-]{6,}", r"\1sk-***", s)
    except Exception:
        pass
    return s


def short(text, n=200):
    """截断长文本用于日志（保留首部，超长标注总长度）"""
    if text is None:
        return ""
    s = str(text)
    s = mask_secret(s)
    s = s.replace("\r", " ").replace("\n", " ⏎ ")
    if len(s) <= n:
        return s
    return "%s…（共 %d 字）" % (s[:n], len(s))
