# -*- coding: utf-8 -*-
"""
面试点评器 v2.5.3 - 桌面主程序（内嵌 WebView，不依赖外部浏览器）
* 内嵌 WebView2 窗口承载界面：无地址栏、自定义标题栏与窗口按钮
* 启动即出窗口，关闭窗口即停止本地服务并退出进程
* 本地服务端口自适应，单实例运行
作者：慕夕制作
"""
import os
import sys
import json
import time
import ctypes
import ctypes.wintypes
import socket
import threading
import traceback

APP_NAME = "面试点评器"
APP_AUTHOR = "慕夕制作"
APP_VERSION = "2.8.0"
WINDOW_TITLE = "%s · %s" % (APP_NAME, APP_AUTHOR)

WIN_W, WIN_H = 1320, 860
WIN_MIN = (980, 660)

IS_FROZEN = bool(getattr(sys, "frozen", False))
BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable))) if IS_FROZEN \
    else os.path.dirname(os.path.abspath(__file__))

STATE_ROOT = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "InterviewReviewer")
LOG_DIR = os.path.join(STATE_ROOT, "logs")
DATA_HOME = os.path.join(STATE_ROOT, "data")
STORAGE_DIR = os.path.join(STATE_ROOT, "webview")
for _d in (LOG_DIR, STORAGE_DIR):
    try:
        os.makedirs(_d, exist_ok=True)
    except Exception:
        pass
LOG_PATH = os.path.join(LOG_DIR, "app.log")
RUN_FILE = os.path.join(STATE_ROOT, "runtime.json")


def _redirect_streams():
    """窗口模式下没有控制台，把 stdout/stderr 落到日志，便于排查"""
    try:
        f = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    except Exception:
        return None
    f.write("\n==== %s v%s 启动 %s ====\n" % (APP_NAME, APP_VERSION, time.strftime("%Y-%m-%d %H:%M:%S")))
    sys.stdout = f
    sys.stderr = f
    return f


_LOGF = _redirect_streams()
if BASE not in sys.path:
    sys.path.insert(0, BASE)


def log(msg):
    try:
        print(msg)
        if sys.stdout:
            sys.stdout.flush()
    except Exception:
        pass


# ---------------- 端口与单实例 ----------------
def _port_free(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    except Exception:
        pass
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def pick_port(start=7788, tries=40):
    for p in range(start, start + tries):
        if _port_free(p):
            return p
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _probe(port, timeout=1.0):
    """端口上是否为本应用的实例：必须能解析 /api/appinfo 且 name 匹配，
    避免端口被其它程序占用时误判为「已在运行」而直接退出。"""
    import json as _json
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/appinfo" % port, timeout=timeout) as r:
            if r.status != 200:
                return False
            try:
                data = _json.loads(r.read(8192).decode("utf-8", "ignore"))
            except Exception:
                return False
            return isinstance(data, dict) and str(data.get("name") or "").strip() == APP_NAME
    except Exception:
        return False


def find_running_instance():
    """若上次实例仍在运行，返回其端口"""
    try:
        with open(RUN_FILE, "r", encoding="utf-8") as f:
            info = json.load(f)
        port = int(info.get("port") or 0)
        if port and _probe(port, 0.8):
            return port
    except Exception:
        pass
    return 0


def write_run_file(port):
    try:
        os.makedirs(STATE_ROOT, exist_ok=True)
        with open(RUN_FILE, "w", encoding="utf-8") as f:
            json.dump({"port": port, "pid": os.getpid(), "app": APP_NAME, "version": APP_VERSION},
                      f, ensure_ascii=False)
    except Exception:
        pass


def clear_run_file():
    try:
        with open(RUN_FILE, "r", encoding="utf-8") as f:
            info = json.load(f)
        if int(info.get("pid") or 0) == os.getpid():
            os.remove(RUN_FILE)
    except Exception:
        pass


# ---------------- Win32 窗口辅助 ----------------
_u32 = ctypes.windll.user32 if sys.platform == "win32" else None

WM_NCLBUTTONDOWN = 0x00A1
HT = {
    "left": 10, "right": 11, "top": 12, "topleft": 13,
    "topright": 14, "bottom": 15, "bottomleft": 16, "bottomright": 17,
}
SW_MINIMIZE, SW_MAXIMIZE, SW_RESTORE, SW_SHOW, SW_FOREGROUND = 6, 3, 9, 5, 1

# 拖动 / 缩放后台跟随线程的公共状态
VK_LBUTTON = 0x01
SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
_MOVE_ON = [False]


def _lbutton_down():
    """左键是否仍按下（拖动 / 缩放循环的退出条件）"""
    try:
        return bool(_u32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
    except Exception:
        return False


def _cursor_pos():
    p = ctypes.wintypes.POINT()
    _u32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def find_hwnd():
    """取主窗口句柄：按本进程枚举可见顶层窗口，取面积最大者（避免命中同名辅助窗口）"""
    if not _u32:
        return 0
    try:
        pid = ctypes.windll.kernel32.GetCurrentProcessId()
        best = [0, 0, False]
        proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def _cb(hwnd, _lp):
            try:
                p = ctypes.c_ulong(0)
                _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
                if p.value != pid:
                    return True
                if not _u32.IsWindowVisible(hwnd):
                    return True
                r = ctypes.wintypes.RECT()
                _u32.GetWindowRect(hwnd, ctypes.byref(r))
                w = r.right - r.left
                h = r.bottom - r.top
                if w <= 0 or h <= 0:
                    return True
                named = False
                buf = ctypes.create_unicode_buffer(256)
                _u32.GetWindowTextW(hwnd, buf, 256)
                if buf.value == WINDOW_TITLE:
                    named = True
                score = w * h + (10 ** 9 if named else 0)
                if score > best[1]:
                    best[0], best[1], best[2] = hwnd, score, named
            except Exception:
                pass
            return True

        _u32.EnumWindows(proto(_cb), 0)
        if best[0]:
            return best[0]
    except Exception:
        log(traceback.format_exc())
    try:
        return _u32.FindWindowW(None, WINDOW_TITLE) or 0
    except Exception:
        return 0


def focus_self():
    h = find_hwnd()
    if not h:
        return False
    try:
        if _u32.IsIconic(h):
            _u32.ShowWindow(h, SW_RESTORE)
        _u32.SetForegroundWindow(h)
        return True
    except Exception:
        return False


def round_corners(hwnd=None):
    """Windows 11 圆角，无边框窗口也有柔和外轮廓"""
    h = hwnd or find_hwnd()
    if not h:
        return
    try:
        v = ctypes.c_int(2)          # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(h, 33, ctypes.byref(v), ctypes.sizeof(v))
        v2 = ctypes.c_int(1)         # 深色/浅色边框跟随标题栏（此处保持系统默认）
        ctypes.windll.dwmapi.DwmSetWindowAttribute(h, 20, ctypes.byref(v2), ctypes.sizeof(v2))
    except Exception:
        pass


def start_native_move():
    """窗口拖动（无边框窗口专用）

    v2.0.0 的做法是 ReleaseCapture + SendMessage(WM_NCLBUTTONDOWN, HTCAPTION) 走系统
    标题栏拖动，但 WebView2 子窗口持有鼠标捕获，且 ReleaseCapture 只对「调用线程」的
    捕获生效，pywebview 的 JS 桥又是工作线程，于是系统模态移动循环拿不到鼠标消息，
    窗口纹丝不动。v2.1.0 改为自己跟随系统光标：按下期间持续读 GetCursorPos 并
    SetWindowPos，按 OS 屏幕坐标增量平移窗口，不依赖浏览器特性，也不依赖鼠标捕获。
    """
    if not _u32 or _MOVE_ON[0]:
        return False
    if not _lbutton_down():
        return False
    h = find_hwnd()
    if not h:
        return False
    _MOVE_ON[0] = True

    def _run():
        try:
            _u32.SetForegroundWindow(h)
            # 最大化状态下拖动标题栏：先还原，再让光标落在窗口的相对位置（与系统行为一致）
            if _u32.IsZoomed(h):
                mx, my = _cursor_pos()
                r0 = ctypes.wintypes.RECT()
                _u32.GetWindowRect(h, ctypes.byref(r0))
                sw = max(1, r0.right - r0.left)
                ratio = (mx - r0.left) / float(sw)
                _u32.ShowWindow(h, SW_RESTORE)
                time.sleep(0.06)
                r1 = ctypes.wintypes.RECT()
                _u32.GetWindowRect(h, ctypes.byref(r1))
                w1 = max(320, r1.right - r1.left)
                nx = int(mx - w1 * ratio)
                ny = int(my - 18)
                _u32.SetWindowPos(h, 0, nx, ny, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
                time.sleep(0.06)
            mx0, my0 = _cursor_pos()
            r = ctypes.wintypes.RECT()
            _u32.GetWindowRect(h, ctypes.byref(r))
            wx0, wy0 = r.left, r.top
            t0 = time.time()
            while _lbutton_down() and (time.time() - t0) < 120:
                mx, my = _cursor_pos()
                nx, ny = wx0 + (mx - mx0), wy0 + (my - my0)
                _u32.SetWindowPos(h, 0, nx, ny, 0, 0,
                                  SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
                time.sleep(0.008)
        except Exception:
            log(traceback.format_exc())
        finally:
            _MOVE_ON[0] = False

    threading.Thread(target=_run, name="win-move", daemon=True).start()
    return True


def start_native_resize(edge):
    """边缘 / 角拖拽缩放（同样用 SetWindowPos 跟随光标，带最小尺寸约束）"""
    code = HT.get(str(edge or "").lower())
    if not _u32 or not code or _MOVE_ON[0]:
        return False
    if not _lbutton_down():
        return False
    h = find_hwnd()
    if not h or _u32.IsZoomed(h):
        return False
    key = str(edge or "").lower()
    _MOVE_ON[0] = True

    def _run():
        try:
            min_w, min_h = WIN_MIN
            mx0, my0 = _cursor_pos()
            r = ctypes.wintypes.RECT()
            _u32.GetWindowRect(h, ctypes.byref(r))
            L, T, R, B = r.left, r.top, r.right, r.bottom
            t0 = time.time()
            while _lbutton_down() and (time.time() - t0) < 120:
                mx, my = _cursor_pos()
                dx, dy = mx - mx0, my - my0
                nl, nt, nr, nb = L, T, R, B
                if "left" in key:
                    nl = min(L + dx, R - min_w)
                if "right" in key:
                    nr = max(R + dx, L + min_w)
                if "top" in key:
                    nt = min(T + dy, B - min_h)
                if "bottom" in key:
                    nb = max(B + dy, T + min_h)
                _u32.SetWindowPos(h, 0, nl, nt, max(min_w, nr - nl), max(min_h, nb - nt),
                                  SWP_NOZORDER | SWP_NOACTIVATE)
                time.sleep(0.008)
        except Exception:
            log(traceback.format_exc())
        finally:
            _MOVE_ON[0] = False

    threading.Thread(target=_run, name="win-resize", daemon=True).start()
    return True


# ---------------- 服务 ----------------
class Service(object):
    def __init__(self, port):
        self.port = port
        self.srv = None
        self.https_srv = None
        self.https_port = 0
        self.thread = None
        self.error = None

    def start(self):
        from werkzeug.serving import make_server
        # 关键：先钉死数据目录再导入 server，保证「设置」页读写的就是 DATA_HOME 这一份配置，
        # 不会出现开发态读源码 data、安装态读 LOCALAPPDATA 导致设置里填的接口/Key 不生效
        os.environ.setdefault("IR_DATA_DIR", DATA_HOME)
        import server as srv_mod          # 触发数据目录初始化与 Flask app 构建
        self.mod = srv_mod
        if os.path.abspath(srv_mod.DATA) != os.path.abspath(DATA_HOME):
            log("[WARN] 数据目录不一致 server.DATA=%s launcher.DATA_HOME=%s" % (srv_mod.DATA, DATA_HOME))
        # v2.6：绑定 0.0.0.0，使同一局域网内的手机/平板可通过 http://本机IP:端口 访问
        # （桌面内嵌 WebView 仍走 127.0.0.1，行为不变）
        try:
            srv_mod.set_http_port(self.port)      # 手机端地址拼接要用真实 HTTP 端口
        except Exception:
            pass
        self.srv = make_server("0.0.0.0", self.port, srv_mod.app, threaded=True)
        self.thread = threading.Thread(target=self.srv.serve_forever, name="flask", daemon=True)
        self.thread.start()
        log("[OK] 服务已启动 http://127.0.0.1:%d  数据目录=%s" % (self.port, srv_mod.DATA))
        # v2.7：并存一条 HTTPS 监听。手机浏览器只在安全上下文（HTTPS / 本机回环）才允许开麦，
        # 手机用 http://局域网IP 打开时 navigator.mediaDevices 根本不存在，必须走 HTTPS。
        self.https_srv = None
        try:
            default_hp = self.port + 1
            try:
                _hp = int(os.environ.get("IR_HTTPS_PORT") or default_hp)
            except Exception:
                _hp = default_hp
            _hsrv, _hport, _herr = srv_mod.start_https_server("0.0.0.0", _hp)
            self.https_srv = _hsrv
            self.https_port = _hport
            if _hsrv or _hport:
                _lan = ""
                try:
                    _lan = srv_mod._lan_ip()
                except Exception:
                    pass
                log("[OK] HTTPS 已启动 https://%s:%d%s （手机端录音入口，端口 %d）"
                    % (_lan or "127.0.0.1", _hport,
                       getattr(srv_mod, "MOBILE_PAGE_PATH", "/mobile"), _hport))
                if srv_mod._HTTPS.get("created"):
                    log("     已自动签发本机自签证书: %s" % srv_mod._HTTPS.get("cert"))
                log("     手机首次打开会提示证书不受信任，点“高级→继续访问”即可")
            else:
                log("[WARN] HTTPS 未启动，手机端将无法录音：%s" % (_herr or "未知原因"))
        except Exception:
            log("[WARN] HTTPS 启动异常：\n" + traceback.format_exc())
        try:
            _lan = srv_mod._lan_ip()
            if _lan:
                log("     手机/平板访问 http://%s:%d%s （HTTP 只能看，不能录音）"
                    % (_lan, self.port, getattr(srv_mod, "MOBILE_PAGE_PATH", "/mobile")))
        except Exception:
            pass
        log("     AI 配置=%s  接口=%s  模型=%s" % (srv_mod.CFG_PATH, srv_mod.ai.cfg.get("base_url"),
                                               srv_mod.ai.cfg.get("model")))
        log("     模型目录=%s" % srv_mod.MODEL_DIR)

    def stop(self):
        try:
            if getattr(self, "https_srv", None):
                try:
                    self.https_srv.shutdown()
                except Exception:
                    pass
                self.https_srv = None
            if self.srv:
                self.srv.shutdown()
                log("[OK] 服务已停止")
        except Exception:
            log(traceback.format_exc())


# ---------------- 前端可调的窗口接口 ----------------
class WindowApi(object):
    def __init__(self):
        # 注意：属性名必须以 _ 开头。pywebview 注入 JS 桥时会递归遍历所有公开属性，
        # 公开属性若持有 Window / Event 这类对象会触发无限递归，导致 window.pywebview 注入失败。
        self._win = None
        self._closed = threading.Event()

    # 基本信息：前端据此判断是否运行在桌面壳内
    def app_info(self):
        return {
            "name": APP_NAME, "author": APP_AUTHOR, "version": APP_VERSION,
            "desktop": True, "state_dir": STATE_ROOT, "log": LOG_PATH,
        }

    def win_min(self):
        h = find_hwnd()
        if h:
            _u32.ShowWindow(h, SW_MINIMIZE)
        return True

    def win_max(self):
        h = find_hwnd()
        if not h:
            return "normal"
        try:
            if _u32.IsZoomed(h):
                _u32.ShowWindow(h, SW_RESTORE)
                return "normal"
            _u32.ShowWindow(h, SW_MAXIMIZE)
            return "max"
        except Exception:
            return "normal"

    def win_state(self):
        """返回窗口当前状态：max / min / normal（供标题栏同步图标与拖拽热区）"""
        h = find_hwnd()
        if not h:
            return "normal"
        try:
            if _u32.IsZoomed(h):
                return "max"
            if _u32.IsIconic(h):
                return "min"
        except Exception:
            pass
        return "normal"

    def win_theme(self, theme=""):
        """深浅色同步：让系统窗口边框 / 标题栏随主题（无边框窗口也受益于圆角与描边）"""
        h = find_hwnd()
        if not h:
            return False
        try:
            dark = 1 if str(theme) == "dark" else 0
            if str(theme) == "auto":
                try:
                    import winreg
                    k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                       r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
                    dark = 0 if winreg.QueryValueEx(k, "AppsUseLightTheme")[0] else 1
                except Exception:
                    dark = 0
            v = ctypes.c_int(dark)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(h, 20, ctypes.byref(v), ctypes.sizeof(v))
            return True
        except Exception:
            return False

    def win_close(self):
        log("[WINDOW] 关闭窗口")
        try:
            if self._win:
                self._win.destroy()
        except Exception:
            log(traceback.format_exc())
        return True

    def win_drag(self):
        ok = start_native_move()
        log("[WINDOW] 拖拽标题栏 start=%s" % ok)
        return ok

    def win_resize(self, edge=""):
        ok = start_native_resize(edge)
        log("[WINDOW] 拖拽缩放 %s start=%s" % (edge, ok))
        return ok

    def open_data(self):
        try:
            os.startfile(DATA_HOME)
            return True
        except Exception:
            try:
                os.startfile(STATE_ROOT)
                return True
            except Exception:
                return False

    def open_log(self):
        try:
            os.startfile(LOG_PATH)
            return True
        except Exception:
            return False

    def quit_app(self):
        return self.win_close()


# ---------------- 主流程 ----------------
def run_desktop(port, service, preload):
    """内嵌 WebView 窗口：启动即出窗口，关窗即退出服务"""
    import webview

    # 验收/排查用：仅当显式设置 IR_REMOTE_DEBUG_PORT=<port> 时才开放本机远程调试卷口，
    # 正常启动完全不受影响（默认关闭，端口只绑定 127.0.0.1）。
    _dbg_port = (os.environ.get("IR_REMOTE_DEBUG_PORT") or "").strip()
    if _dbg_port.isdigit():
        try:
            webview.settings["REMOTE_DEBUGGING_PORT"] = int(_dbg_port)
            log("[DEBUG] 已开启本机远程调试卷口 %s" % _dbg_port)
        except Exception:
            log("[DEBUG] 开启远程调试卷口失败:\n%s" % traceback.format_exc())

    api = WindowApi()
    url = "http://127.0.0.1:%d/" % port
    try:
        win = webview.create_window(
            WINDOW_TITLE, url,
            width=WIN_W, height=WIN_H, min_size=WIN_MIN,
            frameless=True, easy_drag=False, resizable=True,
            background_color="#e6eaf2", text_select=True,
            js_api=api,
        )
    except Exception:
        log("[ERROR] 创建窗口失败:\n%s" % traceback.format_exc())
        raise
    api._win = win

    def on_close():
        log("[WINDOW] 窗口关闭，准备退出")
        api._closed.set()

    try:
        win.events.closing += on_close
    except Exception:
        pass

    def after_start(*_a):
        for _ in range(40):
            time.sleep(0.15)
            if find_hwnd():
                round_corners()
                log("[WINDOW] 已应用圆角/边框样式 hwnd=%s" % find_hwnd())
                return
        log("[WARN] 未找到窗口句柄，跳过圆角设置")

    log("[WINDOW] 创建内嵌窗口 %s  size=%dx%d" % (WINDOW_TITLE, WIN_W, WIN_H))
    webview.start(after_start, gui="edgechromium", debug=False,
                  private_mode=False, storage_path=STORAGE_DIR)
    log("[WINDOW] 窗口主循环结束")


def main():
    log("[BOOT] frozen=%s BASE=%s" % (IS_FROZEN, BASE))

    running = find_running_instance()
    if running:
        log("[INFO] 检测到已在运行，端口 %d，置前窗口" % running)
        if not focus_self():
            try:
                import webbrowser
                webbrowser.open("http://127.0.0.1:%d/" % running, new=1)
            except Exception:
                pass
        return 0

    port = pick_port(7788)
    log("[INFO] 选用端口 %d" % port)
    service = Service(port)
    try:
        service.start()
        write_run_file(port)
    except Exception:
        log("[ERROR] 服务启动失败:\n%s" % traceback.format_exc())
        _fatal("本地服务启动失败，详情见日志：\n%s" % LOG_PATH)
        return 1

    # 后台预热识别模型（不阻塞窗口显示）
    preload = {"done": False, "ok": False, "msg": ""}

    def warm():
        try:
            ok, msg = service.mod.bootstrap()
            preload.update({"done": True, "ok": ok, "msg": msg})
            log("[PRELOAD] %s %s" % (ok, msg))
        except Exception:
            preload.update({"done": True, "ok": False, "msg": traceback.format_exc()})
            log("[ERROR] 预热异常:\n%s" % traceback.format_exc())

    threading.Thread(target=warm, name="warmup", daemon=True).start()

    try:
        run_desktop(port, service, preload)
    except Exception:
        log("[ERROR] 桌面窗口异常:\n%s" % traceback.format_exc())
        _fatal("界面窗口启动失败，可能是缺少 WebView2 运行时。\n\n"
               "请安装 Microsoft Edge WebView2 Runtime 后重试。\n日志：%s" % LOG_PATH)
        service.stop()
        clear_run_file()
        return 1

    # 关窗即退出：停服务、清运行标记
    service.stop()
    clear_run_file()
    log("[EXIT] 程序正常退出")
    try:
        sys.stdout.flush()
    except Exception:
        pass
    # 确保后台线程（识别/录音）不拖住进程
    os._exit(0)


def _fatal(msg):
    log("[FATAL] %s" % msg)
    try:
        import tkinter as tk
        from tkinter import messagebox
        r = tk.Tk()
        r.withdraw()
        messagebox.showerror("%s 启动失败" % APP_NAME, msg)
        r.destroy()
    except Exception:
        pass


def removed_browser_mode():
    """保留浏览器模式入口（调试用）：python launcher.py --browser"""
    import webbrowser
    running = find_running_instance()
    if running:
        webbrowser.open("http://127.0.0.1:%d/" % running, new=1)
        return 0
    port = pick_port(7788)
    service = Service(port)
    service.start()
    write_run_file(port)
    threading.Thread(target=service.mod.bootstrap, name="warmup", daemon=True).start()
    webbrowser.open("http://127.0.0.1:%d/" % port, new=1)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        service.stop()
        clear_run_file()
    return 0


if __name__ == "__main__":
    if "--browser" in sys.argv:
        sys.exit(removed_browser_mode())
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        log("[FATAL]\n%s" % traceback.format_exc())
        _fatal("程序异常退出，详情见日志：\n%s\n\n%s" % (LOG_PATH, traceback.format_exc()[-800:]))
        sys.exit(1)
