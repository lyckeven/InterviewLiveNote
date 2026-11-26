# -*- mode: python ; coding: utf-8 -*-
"""面试点评器 - PyInstaller 打包配置（onedir，绿色版/安装包共用）
作者：慕夕制作
"""
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

# 工程根目录：由 PyInstaller 注入的 SPECPATH（即本 spec 所在目录）向上推导，换机器无需改路径
PROJ = os.path.dirname(os.path.abspath(SPECPATH))

# 离线识别模型所在目录。可用环境变量 IR_MODEL_ROOT 覆盖；默认指向开发机已装好模型的 TMSpeech 目录。
MODEL_ROOT = os.environ.get("IR_MODEL_ROOT") or r"E:\Software\TMSpeech\external-recognizer"
MODEL_SRC = os.path.join(MODEL_ROOT, "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09")
VAD_SRC = os.path.join(MODEL_ROOT, "silero_vad.onnx")
PUNCT_SRC = os.path.join(MODEL_ROOT, "punct_int8", "model.onnx")

datas = [
    (os.path.join(PROJ, "static"), "static"),
    (os.path.join(PKG, "default_data"), "default_data"),
    (os.path.join(PKG, "app.ico"), "."),
    # 开源合规：第三方组件声明清单 + 许可证全文（Apache-2.0/BSD-3-Clause/MIT/MPL-2.0/AGPL-3.0/0BSD/Zlib）
    (os.path.join(PROJ, "THIRD_PARTY_NOTICES.md"), "."),
    (os.path.join(PROJ, "LICENSES"), "LICENSES"),
    (os.path.join(MODEL_SRC, "model.int8.onnx"), os.path.join("models", "sense-voice")),
    (os.path.join(MODEL_SRC, "tokens.txt"), os.path.join("models", "sense-voice")),
    (VAD_SRC, "models"),
    (PUNCT_SRC, os.path.join("models", "punct")),
]

sherpa_datas, sherpa_binaries, sherpa_hidden = collect_all("sherpa_onnx")
datas += sherpa_datas
binaries = sherpa_binaries

# ---- 内嵌 WebView2 桌面壳依赖（pywebview + pythonnet + WebView2 托管 DLL）----
wv_datas, wv_binaries, wv_hidden = collect_all("webview")
datas += wv_datas
binaries += wv_binaries

pn_datas, pn_binaries, pn_hidden = collect_all("pythonnet")
datas += pn_datas
binaries += pn_binaries

cl_datas, cl_binaries, cl_hidden = collect_all("clr_loader")
datas += cl_datas
binaries += cl_binaries

# ---- 手机扫码链路依赖（v2.5.2 缺失导致：二维码 500 + HTTPS 起不来）----
# cryptography：手机端 HTTPS 自签证书（缺失 -> 手机只能走 HTTP，麦克风被浏览器禁用）
cr_datas, cr_binaries, cr_hidden = collect_all("cryptography")
datas += cr_datas
binaries += cr_binaries

# qrcode：二维码编码（纯 Python）；出图走 server 内置 PNG 写入器，不需要 PIL
qr_hidden = collect_submodules("qrcode")

# sherpa 的 DLL 需与 _sherpa_onnx.pyd 同目录，显式再放一份到包内 lib 目录
SHERPA_LIB = os.path.join(MODEL_ROOT, "WPy64-31700", "python", "Lib", "site-packages", "sherpa_onnx", "lib")
datas += [
    (os.path.join(SHERPA_LIB, fn), os.path.join("sherpa_onnx", "lib"))
    for fn in ("onnxruntime.dll", "sherpa-onnx-c-api.dll", "sherpa-onnx-cxx-api.dll")
    if os.path.isfile(os.path.join(SHERPA_LIB, fn))
]

hiddenimports = list(sherpa_hidden) + list(wv_hidden) + list(pn_hidden) + list(cl_hidden) + list(cr_hidden) + qr_hidden + [
    "webview",
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
    "clr",
    "clr_loader",
    "pythonnet",
    "System",
    "System.Windows.Forms",
    "System.Drawing",
    "sherpa_onnx",
    "_portaudiowpatch",
    "pyaudiowpatch",
    "scipy.signal",
    "scipy._lib.array_api_compat.numpy",
    "flask",
    "werkzeug.serving",
    "jinja2",
    "lxml.etree",
    "docx",
    "requests",
    "numpy",
    "fitz",
    "pymupdf",
]

excludes = [
    "matplotlib", "pandas", "playwright", "sqlite_bro", "sv_ttk", "sounddevice",
    "PIL", "IPython", "pytest", "notebook", "scipy.io", "scipy.sparse.csgraph",
    "tkinter.test", "unittest", "pydoc_data",
]

a = Analysis(
    [os.path.join(PROJ, "launcher.py")],
    pathex=[PROJ],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="面试点评器",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(PKG, "app.ico"),
    version=os.path.join(PKG, "version_info.txt"),
    uac_admin=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="面试点评器",
)
