# -*- coding: utf-8 -*-
"""
ASR 引擎 v1.1：系统内录(WASAPI Loopback) + Silero VAD + SenseVoice 识别 + CT-Transformer 标点

v1.1 相比 v1.0 的关键改动
1. 增量上屏：说话过程中每 PARTIAL_INTERVAL 秒对「正在说的这段音频」做一次识别，
   通过 on_partial 回调把中间结果推给前端，不必等整段结束。
2. 快速定稿：VAD 最小静音判定 0.4s -> 0.32s；取消原先「凑满 1.5s / 30 字才发射」的
   pending 合并（这是出字慢的主因），每段识别完立即 emit。
3. 标点恢复：接入 sherpa-onnx CT-Transformer 中文标点模型（带上下文），
   模型缺失时退回轻量规则补标点。
4. 准确性：VAD 起点前补 PREROLL_SEC 的音频，避免吞首字；模型优先 int8，可换 fp32。

v1.2 新增（手机 / 平板网页端录音，只加输入源，不改既有链路）
5. remote 输入源：设备采集线程换成「网络推流」——手机浏览器把 16kHz 单声道 PCM
   定长小分片 POST 上来，push_pcm() 原样喂进 _audio_q，因此 VAD、增量上屏、
   定稿、段落切分、精简版、SSE 推送全部沿用原有实现，loopback / mic 行为不变。
"""
import os
import re
import sys
import time
import queue
import threading
import traceback

import numpy as np
import pyaudiowpatch as pyaudio
import sherpa_onnx


# ---------------- PortAudio 全局互斥（打包成 exe 后的稳定性关键） ----------------
# PyAudio/PortAudio 在同一进程内重复 PyAudio()/terminate() 且并发时会触发
# 0xC0000005 访问冲突（页面自身与前端轮询同时调用 /api/devices 即可复现）。
# 因此：枚举设备统一复用同一个常驻 PyAudio 实例，并把所有 Pa_Initialize /
# Pa_Terminate / 枚举动作串行化。
_PA_LOCK = threading.RLock()
_PA_SHARED = None


def _pa_shared():
    """常驻 PyAudio 实例（枚举设备用，进程退出前不再 terminate）"""
    global _PA_SHARED
    with _PA_LOCK:
        if _PA_SHARED is None:
            _PA_SHARED = pyaudio.PyAudio()
        return _PA_SHARED


def _pa_release():
    """进程退出时统一释放（正常流程不调用）"""
    global _PA_SHARED
    with _PA_LOCK:
        if _PA_SHARED is not None:
            try:
                _PA_SHARED.terminate()
            except Exception:
                pass
            _PA_SHARED = None


TARGET_SR = 16000
CHUNK_SEC = 0.1           # 每次读取 100ms
MAX_SEG_SEC = 8.0         # 单句最长秒数，超过则强制切分
PREROLL_SEC = 0.25        # 语音起点前补的音频，防止吞首字
PREROLL_GUARD_SEC = 0.35  # 与上一段间隔小于该值时不补前置音频，避免重复字

MIC_AGC_TARGET = 0.05   # 麦克风增益目标电平（RMS）
MIC_AGC_MAX = 80.0      # 最大放大倍数（仅语音块放大，避免放大底噪）
MIC_VAD_THRESHOLD = 0.35  # 麦克风 VAD 触发阈值（低于系统内录，适配远场/小音量）
LOOPBACK_VAD_THRESHOLD = 0.5

# ---- v1.2：手机 / 平板网页端（remote）输入源 ----
REMOTE_SAMPLE_RATE = 16000     # 手机端统一上传 16kHz 单声道 PCM
REMOTE_CHUNK_MS = 100          # 建议的分片时长（毫秒），一块 = 1600 采样 = 3200 字节
REMOTE_MAX_QUEUE = 300         # 网络分片积压上限（块，约 30 秒），超出丢新块保住低延迟
REMOTE_VAD_THRESHOLD = 0.35    # 手机麦克风属远场，阈值与 mic 一致


class _MicAgc:
    """麦克风自动增益：跟随噪声底，仅在语音块把弱信号拉高到可识别电平"""

    def __init__(self):
        self.floor = 1e-4
        self.last_gain = 1.0

    def __call__(self, x):
        try:
            if not x.size:
                return x
            rms = float(np.sqrt(np.mean(np.square(x))))
        except Exception:
            return x
        if rms <= 0:
            return x
        self.floor = 0.98 * self.floor + 0.02 * min(rms, max(self.floor, 1e-6) * 4.0)
        self.floor = max(self.floor, 1e-6)
        g = 1.0
        if rms > max(self.floor * 3.0, 1.2e-3):
            g = min(MIC_AGC_MAX, max(1.0, MIC_AGC_TARGET / rms))
        self.last_gain = g
        if g <= 1.01:
            return x
        return np.clip(x * np.float32(g), -1.0, 1.0).astype(np.float32)


MIN_SILENCE = 0.32        # VAD 最小静音判定（越小定稿越快）
MIN_SPEECH = 0.2          # VAD 最小语音判定
PARTIAL_INTERVAL = 0.3    # 中间结果刷新间隔
PARTIAL_MIN_SEC = 0.35    # 语音累计超过该时长才出中间结果
PARTIAL_MAX_SEC = 6.0     # 中间结果最多取尾部多少秒

PUNCT_END = "。！？…"
LINKERS = ["但是", "所以", "因此", "而且", "同时", "一方面", "另一方面",
           "如果", "因为", "其实", "另外", "总之", "比如", "不过", "然而", "然后"]
_NON_PUNCT_RE = re.compile(r"[，。！？；：、,.!?;:\s\"'“”‘’（）()【】\[\]…—-]+")


def add_punct(text: str) -> str:
    """轻量标点兜底：无标点模型时使用（不改字，只补标点）"""
    t = (text or "").strip()
    if not t:
        return t
    pat = re.compile(r"(?<![，。！？；：、\s])(" +
                     "|".join(sorted(LINKERS, key=len, reverse=True)) + ")")
    t = pat.sub(r"，\1", t)
    t = re.sub(r"^[，、；：]+", "", t)
    t = re.sub(r"[，]{2,}", "，", t)
    if not t:
        return t
    if t[-1] not in PUNCT_END:
        t += "。"
    return t


def tidy_punct(text: str) -> str:
    """清理重复/错位标点：连续标点只保留一个，强标点优先"""
    t = (text or "").strip()
    if not t:
        return t
    t = re.sub(r"^[，、；：。！？…]+", "", t)

    def repl(m):
        run = m.group(0)
        strong = [c for c in run if c in "。！？…"]
        return strong[-1] if strong else "，"

    t = re.sub(r"[，、；：。！？…]{2,}", repl, t)
    return t


class PunctRestorer:
    """CT-Transformer 标点恢复：不增删字，只在字间插入标点"""

    def __init__(self, model_path: str = "", num_threads: int = 2):
        self.model_path = model_path or ""
        self.ok = False
        self._p = None
        if self.model_path and os.path.exists(self.model_path):
            try:
                cfg = sherpa_onnx.OfflinePunctuationConfig()
                cfg.model.ct_transformer = self.model_path
                cfg.model.num_threads = int(num_threads)
                self._p = sherpa_onnx.OfflinePunctuation(cfg)
                self.ok = True
            except Exception:
                traceback.print_exc()
                self.ok = False

    def _raw(self, text):
        try:
            return (self._p.add_punctuation(text) or "").strip()
        except Exception:
            return ""

    def add(self, text: str, context: str = "") -> str:
        t = (text or "").strip()
        if not t:
            return t
        if not self.ok:
            return add_punct(t)
        nt = _NON_PUNCT_RE.sub("", t)
        ctx = _NON_PUNCT_RE.sub("", context or "")[-60:]
        out = self._raw(ctx + t) if ctx else self._raw(t)
        tail = out
        if ctx and out:
            cnt = 0
            cut = 0
            for i, ch in enumerate(out):
                if cnt >= len(ctx):
                    cut = i
                    break
                if not _NON_PUNCT_RE.match(ch):
                    cnt += 1
                cut = i + 1
            tail = out[cut:]
        if _NON_PUNCT_RE.sub("", tail) != nt:
            only = self._raw(t)
            tail = only if _NON_PUNCT_RE.sub("", only) == nt else add_punct(t)
        return tail.strip()


def _resample_to_16k(samples: np.ndarray, src_rate: int) -> np.ndarray:
    if src_rate == TARGET_SR:
        return samples
    try:
        from scipy.signal import resample_poly
        from math import gcd
        g = gcd(int(src_rate), TARGET_SR)
        up = TARGET_SR // g
        down = int(src_rate) // g
        return resample_poly(samples, up, down).astype(np.float32)
    except Exception:
        n = int(len(samples) * TARGET_SR / src_rate)
        if n <= 1:
            return np.zeros(0, dtype=np.float32)
        idx = np.linspace(0, len(samples) - 1, n)
        return np.interp(idx, np.arange(len(samples)), samples).astype(np.float32)


class AsrEngine:
    def __init__(self, model_dir: str, vad_model: str, num_threads: int = 4,
                 punct_model: str = ""):
        self._agc = _MicAgc()
        self.model_dir = model_dir
        self.vad_model = vad_model
        self.num_threads = num_threads
        self.punct_model = punct_model
        self.recognizer = None
        self.punct = None
        self._stop = threading.Event()
        self._threads = []
        self._audio_q = queue.Queue()
        self._final_q = queue.Queue()
        self._dec_lock = threading.Lock()
        self._partial_slot = None
        self._partial_lock = threading.Lock()
        self._partial_evt = threading.Event()
        self.on_final = None      # callback(text, start_sec, end_sec)
        self.on_status = None     # callback(dict)
        self.on_partial = None    # callback(text, start_sec, end_sec, key)
        self.started_at = None
        self.device_name = ""
        self.device_kind = "loopback"
        self.error = ""
        self.last_text = ""       # 最近定稿文本，作为标点上下文
        self.stats = {"finals": 0, "partials": 0, "decode_ms": [], "partial_ms": []}
        # v1.2：remote（手机网页端）推流统计，供接口回显与自检
        self._remote_lock = threading.Lock()
        self.remote_stats = {"chunks": 0, "bytes": 0, "samples": 0,
                             "dropped": 0, "last_ts": 0.0}

    # ---------- 初始化 ----------
    def _load(self):
        model = os.path.join(self.model_dir, "model.int8.onnx")
        if not os.path.exists(model):
            alt = os.path.join(self.model_dir, "model.onnx")
            if os.path.exists(alt):
                model = alt
        tokens = os.path.join(self.model_dir, "tokens.txt")
        for p in (model, tokens, self.vad_model):
            if not os.path.exists(p):
                raise FileNotFoundError(f"缺少模型文件: {p}")
        self.recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=model,
            tokens=tokens,
            num_threads=self.num_threads,
            use_itn=True,
            debug=False,
            language="zh",
        )
        pm = self.punct_model or os.environ.get("IR_PUNCT_MODEL", "")
        if not pm:
            cand = os.path.join(os.path.dirname(os.path.abspath(self.model_dir)), "punct", "model.onnx")
            if os.path.exists(cand):
                pm = cand
        self.punct = PunctRestorer(pm, num_threads=2)
        self.punct_model = pm

    def list_loopback_devices(self):
        out = []
        with _PA_LOCK:
            try:
                p = _pa_shared()
                for lb in p.get_loopback_device_info_generator():
                    out.append({
                        "index": lb["index"],
                        "name": lb["name"],
                        "rate": int(lb["defaultSampleRate"]),
                        "channels": int(lb["maxInputChannels"]),
                        "kind": "loopback",
                    })
            except Exception:
                pass
        return out

    def list_input_devices(self):
        """麦克风（录音输入）设备列表：只取 WASAPI 输入设备，排除回环与重复别名"""
        out = []
        with _PA_LOCK:
            try:
                p = _pa_shared()
                try:
                    wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)["index"]
                except Exception:
                    wasapi = None
                seen = set()
                for i in range(p.get_device_count()):
                    try:
                        d = p.get_device_info_by_index(i)
                    except Exception:
                        continue
                    ch = int(d.get("maxInputChannels") or 0)
                    if ch <= 0:
                        continue
                    name = str(d.get("name") or "")
                    low = name.lower()
                    if "[loopback]" in low or "立体声混音" in name or "stereo mix" in low:
                        continue
                    if wasapi is not None and d.get("hostApi") != wasapi:
                        continue
                    key = low.strip()
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append({
                        "index": i,
                        "name": name,
                        "rate": int(d.get("defaultSampleRate") or 48000),
                        "channels": ch,
                        "kind": "mic",
                    })
            except Exception:
                pass
        return out

    def list_devices(self, kind="loopback"):
        return self.list_input_devices() if kind == "mic" else self.list_loopback_devices()

    def _default_device(self, kind="loopback"):
        """系统默认输入设备 / 系统默认输出对应的回环设备"""
        with _PA_LOCK:
            try:
                p = _pa_shared()
                if kind == "mic":
                    info = p.get_default_input_device_info()
                else:
                    info = p.get_default_wasapi_loopback()
                return {
                    "index": info["index"],
                    "name": info["name"],
                    "rate": int(info.get("rate") or info.get("defaultSampleRate") or 48000),
                    "channels": int(info.get("channels") or info.get("maxInputChannels") or 1) or 1,
                    "kind": kind,
                }
            except Exception:
                return None

    def _resolve_device(self, kind, device_index=None, device_name=""):
        """按「名称优先、序号兜底、默认兜底」解析设备，保证重启后设备记忆仍可用"""
        cands = self.list_devices(kind)
        want = str(device_name or "").strip().lower()
        if want:
            for d in cands:
                if str(d["name"]).strip().lower() == want:
                    return d
            for d in cands:
                if want in str(d["name"]).strip().lower() or str(d["name"]).strip().lower() in want:
                    return d
        if device_index not in (None, "", 0, "0"):
            try:
                idx = int(device_index)
            except Exception:
                idx = None
            if idx is not None:
                for d in cands:
                    if int(d["index"]) == idx:
                        return d
        if cands:
            # 未显式指定设备 → 必须落到「系统默认输出回环 / 系统默认输入」，
            # 不能直接取列表首个（否则耳机等非默认设备的回环会读不到音频、线程空转）
            dflt = self._default_device(kind)
            if dflt:
                for d in cands:
                    if int(d["index"]) == int(dflt["index"]):
                        return d
                if kind == "mic":
                    for d in cands:
                        if str(d["name"]).strip().lower() == str(dflt["name"]).strip().lower():
                            return d
                return dflt
            return cands[0]
        # 没有候选：名称匹配失败时退回系统默认输入 / 默认回环
        with _PA_LOCK:
            try:
                p = _pa_shared()
                if kind == "mic":
                    info = p.get_default_input_device_info()
                else:
                    info = p.get_default_wasapi_loopback()
                return {
                    "index": info["index"],
                    "name": info["name"],
                    "rate": int(info.get("rate") or info.get("defaultSampleRate") or 48000),
                    "channels": int(info.get("channels") or info.get("maxInputChannels") or 1) or 1,
                    "kind": kind,
                }
            except Exception as e:
                raise RuntimeError("未找到可用的%s设备：%s" % (
                    "麦克风" if kind == "mic" else "系统内录回环", e))

    # ---------- 启动 / 停止 ----------
    def start(self, device_index=None, kind="loopback", device_name=""):
        """启动识别

        kind: loopback（系统内录） / mic（本机麦克风） / remote（手机网页端推流）
        remote 不打开任何本机音频设备，音频由 push_pcm() 从网络写入 _audio_q。
        """
        if self._threads:
            return {"ok": False, "msg": "已在运行中"}

        self._stop.clear()
        self.error = ""
        kraw = str(kind or "").strip().lower()
        if kraw in ("remote", "net", "network", "mobile", "web", "webremote"):
            kind = "remote"
        elif kraw in ("mic", "microphone", "input"):
            kind = "mic"
        else:
            kind = "loopback"
        if self.recognizer is None:
            self._load()

        self._agc = _MicAgc()
        t_rec = None
        if kind == "remote":
            # 手机 / 平板网页端：仅跳过了本机设备采集线程，后续 VAD、增量上屏、
            # 定稿、段落切分、精简版、SSE 推送全部复用原有实现
            self.device_kind = "remote"
            self.device_name = (device_name or "").strip() or "手机/平板网页端"
            self._reset_remote()
        else:
            info = self._resolve_device(kind, device_index, device_name)
            if kind == "mic" and int(info.get("channels") or 0) <= 0:
                raise RuntimeError("所选设备不是麦克风输入设备")
            self.device_kind = kind
            self.device_name = info["name"]
            t_rec = threading.Thread(target=self._record_loop, args=(info,),
                                     daemon=True, name="asr-record")

        self.started_at = time.time()
        self._drain_audio()

        t_work = threading.Thread(target=self._work_loop, daemon=True, name="asr-work")
        t_fin = threading.Thread(target=self._final_loop, daemon=True, name="asr-final")
        t_par = threading.Thread(target=self._partial_loop, daemon=True, name="asr-partial")
        ths = [t_work, t_fin, t_par]
        if t_rec is not None:
            ths.insert(0, t_rec)
        self._threads = ths
        for t in ths:
            t.start()
        extra = {"partial": True, "punct": bool(self.punct and self.punct.ok), "kind": kind}
        if kind == "remote":
            extra.update({"input": "remote", "sample_rate": TARGET_SR,
                          "chunk_ms": REMOTE_CHUNK_MS})
        self._emit_status("listening", extra=extra)
        return {"ok": True, "device": self.device_name, "kind": kind,
                "sample_rate": TARGET_SR, "chunk_ms": REMOTE_CHUNK_MS}

    def _drain_audio(self):
        """启动前清空音频队列，避免上一场残留音频被当作本场开头"""
        try:
            while True:
                self._audio_q.get_nowait()
        except queue.Empty:
            pass

    def _reset_remote(self):
        with self._remote_lock:
            self.remote_stats = {"chunks": 0, "bytes": 0, "samples": 0,
                                 "dropped": 0, "last_ts": 0.0}

    def remote_info(self):
        """remote 推流侧统计（供手机端接口回显 / 自检）"""
        with self._remote_lock:
            st = dict(self.remote_stats)
        st.update({
            "kind": getattr(self, "device_kind", ""),
            "running": self.is_running(),
            "device": self.device_name,
            "sample_rate": TARGET_SR,
            "chunk_ms": REMOTE_CHUNK_MS,
            "queue": self._audio_q.qsize(),
        })
        return st

    def push_pcm(self, data, src_rate=REMOTE_SAMPLE_RATE, channels=1,
                 sample_width=2, agc=True):
        """手机 / 平板网页端推流入口：把网络收到的 PCM 分片写进识别链路

        data         原始音频字节（默认 16kHz / 单声道 / 16bit 小端）
        src_rate     分片采样率，非 16000 时自动重采样
        channels     声道数，>1 时混成单声道
        sample_width 采样字节数，1=8bit 无符号，2=16bit 小端
        agc          是否做远场增益（手机麦克风音量普遍偏低）
        返回 dict：ok / samples / bytes / queued / dropped
        """
        if getattr(self, "device_kind", "") != "remote" or not self.is_running():
            return {"ok": False, "msg": "远程会话未开始", "samples": 0}
        raw = bytes(data or b"")
        if not raw:
            return {"ok": True, "samples": 0, "bytes": 0}
        sw = int(sample_width or 2)
        if sw == 2:
            n = len(raw) // 2
            if n <= 0:
                return {"ok": True, "samples": 0, "bytes": len(raw)}
            a = np.frombuffer(raw[:n * 2], dtype=np.int16).astype(np.float32) / 32768.0
        elif sw == 1:
            a = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        else:
            return {"ok": False, "msg": "仅支持 8bit / 16bit PCM", "samples": 0}
        ch = max(1, int(channels or 1))
        if ch > 1:
            usable = len(a) // ch * ch
            a = a[:usable].reshape(-1, ch).mean(axis=1)
        try:
            rate = int(src_rate or TARGET_SR)
        except Exception:
            rate = TARGET_SR
        if rate != TARGET_SR:
            a = _resample_to_16k(a, rate)
        if agc:
            a = self._agc(a)
        if not len(a):
            return {"ok": True, "samples": 0, "bytes": len(raw)}
        try:
            self.last_level = float(np.sqrt(np.mean(np.square(a))) * 32768.0)
        except Exception:
            pass
        with self._remote_lock:
            st = self.remote_stats
            if self._audio_q.qsize() > REMOTE_MAX_QUEUE:
                # 积压过多说明网络/端侧跟不上，丢新块保实时，宁可少字也不要越拖越晚
                st["dropped"] += 1
                st["last_ts"] = time.time()
                return {"ok": True, "samples": 0, "bytes": len(raw),
                        "dropped": st["dropped"], "overload": True}
            st["chunks"] += 1
            st["bytes"] += len(raw)
            st["samples"] += int(len(a))
            st["last_ts"] = time.time()
        self._audio_q.put(a)
        return {"ok": True, "samples": int(len(a)), "bytes": len(raw),
                "queued": self._audio_q.qsize()}

    def stop(self):
        self._stop.set()
        self._partial_evt.set()
        for t in self._threads:
            t.join(timeout=3.0)
        self._threads = []
        self._emit_status("stopped")
        return {"ok": True}

    def is_running(self):
        return bool(self._threads and any(t.is_alive() for t in self._threads))

    def _emit_status(self, state, msg="", extra=None):
        if self.on_status:
            try:
                ev = {"state": state, "msg": msg, "device": self.device_name}
                if extra:
                    ev.update(extra)
                self.on_status(ev)
            except Exception:
                pass

    # ---------- 采集线程 ----------
    def _record_loop(self, info):
        rate = int(info.get("rate") or info.get("defaultSampleRate") or 48000)
        ch = int(info.get("channels") or info.get("maxInputChannels") or 1) or 1
        frames = int(rate * CHUNK_SEC)
        stream = None
        try:
            with _PA_LOCK:
                p = pyaudio.PyAudio()
                stream = p.open(
                    format=pyaudio.paInt16,
                    channels=ch,
                    rate=rate,
                    frames_per_buffer=frames,
                    input=True,
                    input_device_index=int(info["index"]),
                )
            while not self._stop.is_set():
                try:
                    data = stream.read(frames, exception_on_overflow=False)
                except Exception as e:
                    self.error = f"音频读取失败: {e}"
                    break
                if not data:
                    continue
                try:
                    self.last_level = float(np.abs(np.frombuffer(data, dtype=np.int16)).mean())
                except Exception:
                    pass
                a = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
                if ch > 1:
                    a = a.reshape(-1, ch).mean(axis=1)
                a16 = _resample_to_16k(a, rate)
                if getattr(self, "device_kind", "loopback") == "mic":
                    a16 = self._agc(a16)
                self._audio_q.put(a16)
        except Exception as e:
            kind_cn = "麦克风" if getattr(self, "device_kind", "loopback") == "mic" else "系统内录"
            self.error = f"打开{kind_cn}失败: {e}"
            traceback.print_exc()
        finally:
            with _PA_LOCK:
                try:
                    if stream is not None:
                        stream.stop_stream()
                        stream.close()
                except Exception:
                    pass
                try:
                    p.terminate()
                except Exception:
                    pass
            self._emit_status("closed", self.error)

    # ---------- 解码 ----------
    def _decode(self, samples, with_lock=True):
        if with_lock:
            self._dec_lock.acquire()
        try:
            s = self.recognizer.create_stream()
            s.accept_waveform(TARGET_SR, samples)
            self.recognizer.decode_stream(s)
            return (s.result.text or "").strip()
        finally:
            if with_lock:
                self._dec_lock.release()

    def _final_loop(self):
        """定稿队列：保证顺序，不被中间结果阻塞"""
        while True:
            try:
                job = self._final_q.get(timeout=0.2)
            except queue.Empty:
                if self._stop.is_set():
                    break
                continue
            if job is None:
                break
            samples = job["samples"]
            t0 = time.time()
            try:
                text = self._decode(samples)
            except Exception as e:
                self.error = f"识别失败: {e}"
                continue
            self.stats["decode_ms"].append(int((time.time() - t0) * 1000))
            if text:
                self._emit_final(text, job["t_start"], job["t_end"], job["key"])

    def _partial_loop(self):
        """中间结果：有新的就解，解码忙时直接跳过，绝不拖慢定稿"""
        while True:
            self._partial_evt.wait(timeout=0.2)
            if self._stop.is_set():
                break
            if not self._partial_evt.is_set():
                continue
            self._partial_evt.clear()
            with self._partial_lock:
                job = self._partial_slot
                self._partial_slot = None
            if not job:
                continue
            if not self._dec_lock.acquire(timeout=0.02):
                continue
            try:
                t0 = time.time()
                s = self.recognizer.create_stream()
                s.accept_waveform(TARGET_SR, job["samples"])
                self.recognizer.decode_stream(s)
                text = (s.result.text or "").strip()
                self.stats["partial_ms"].append(int((time.time() - t0) * 1000))
            except Exception:
                text = ""
            finally:
                self._dec_lock.release()
            if not text or self._stop.is_set():
                continue
            try:
                shown = tidy_punct(self.punct.add(text, self.last_text) if self.punct else add_punct(text))
            except Exception:
                shown = text
            shown = shown.rstrip("。") if shown.endswith("。") else shown
            if shown.strip():
                self.stats["partials"] += 1
                if self.on_partial:
                    try:
                        self.on_partial(shown, job["t_start"], job["t_end"], job["key"])
                    except Exception:
                        self.error = "partial 回调异常: " + traceback.format_exc(limit=2)

    def _emit_final(self, text, t_start, t_end, key=0):
        try:
            shown = tidy_punct(self.punct.add(text, self.last_text) if self.punct else add_punct(text))
        except Exception:
            shown = text
        if not shown.strip():
            return
        self.last_text = (self.last_text + "。" + shown)[-200:] if self.last_text else shown
        self.stats["finals"] += 1
        if self.on_final:
            try:
                self.on_final(shown, round(t_start, 2), round(t_end, 2))
            except Exception:
                self.error = "回调异常: " + traceback.format_exc(limit=2)

    # ---------- 主工作线程：喂 VAD + 触发中间结果 ----------
    def _work_loop(self):
        cfg = sherpa_onnx.VadModelConfig()
        cfg.silero_vad.model = self.vad_model
        # 手机网页端（remote）与本机麦克风同属远场弱信号，共用较低阈值；系统内录保持高阈值
        _dk = getattr(self, "device_kind", "loopback")
        cfg.silero_vad.threshold = (REMOTE_VAD_THRESHOLD if _dk == "remote"
                                   else (MIC_VAD_THRESHOLD if _dk == "mic"
                                         else LOOPBACK_VAD_THRESHOLD))
        cfg.silero_vad.min_silence_duration = MIN_SILENCE
        cfg.silero_vad.min_speech_duration = MIN_SPEECH
        cfg.silero_vad.max_speech_duration = MAX_SEG_SEC
        cfg.sample_rate = TARGET_SR
        window = cfg.silero_vad.window_size

        try:
            vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=120)
        except Exception as e:
            self.error = f"VAD 初始化失败: {e}"
            return

        buf = np.zeros(0, dtype=np.float32)
        offset = 0
        hist = np.zeros(0, dtype=np.float32)   # 供前置补音的最近音频
        hist_start = 0                          # hist[0] 的绝对采样点
        last_end_abs = -10 ** 9                 # 上一段结束的绝对采样点
        last_partial_ts = 0.0

        def push_final(samples, t_start, t_end, key):
            self._final_q.put({"samples": samples, "t_start": t_start,
                               "t_end": t_end, "key": key})
            with self._partial_lock:
                self._partial_slot = None

        def handle_segment(seg):
            nonlocal last_end_abs, last_partial_ts
            seg_start = float(seg.start)
            arr = np.array(seg.samples, dtype=np.float32)
            seg_end = seg_start + len(arr)
            pre = np.zeros(0, dtype=np.float32)
            if seg_start - last_end_abs > PREROLL_GUARD_SEC * TARGET_SR:
                s0 = seg_start - PREROLL_SEC * TARGET_SR
                i0 = max(0, int(s0 - hist_start))
                i1 = max(0, int(seg_start - hist_start))
                if i1 > i0:
                    pre = hist[i0:i1]
            full = np.concatenate([pre, arr]) if len(pre) else arr
            t_start = (seg_start - len(pre)) / TARGET_SR
            t_end = seg_end / TARGET_SR
            last_end_abs = seg_end
            last_partial_ts = 0.0
            if len(full) < TARGET_SR * MIN_SPEECH:
                return
            push_final(full, t_start, t_end, int(round(t_start * 1000)))

        while not self._stop.is_set():
            try:
                chunk = self._audio_q.get(timeout=0.25)
                parts = [chunk]
                while True:
                    try:
                        parts.append(self._audio_q.get_nowait())
                    except queue.Empty:
                        break
                chunk = np.concatenate(parts)
            except queue.Empty:
                chunk = None

            if chunk is not None and len(chunk):
                buf = np.concatenate([buf, chunk])
                while offset + window < len(buf):
                    win = buf[offset:offset + window]
                    vad.accept_waveform(win)
                    hist = np.concatenate([hist, win])
                    offset += window
                    if len(hist) > TARGET_SR * 2:
                        cut = len(hist) - TARGET_SR
                        hist = hist[cut:]
                        hist_start += cut

                    while not vad.empty():
                        seg = vad.front
                        handle_segment(seg)
                        vad.pop()

                    # 中间结果调度
                    try:
                        if vad.is_speech_detected():
                            cs = vad.current_segment
                            cs_samples = np.array(cs.samples, dtype=np.float32)
                            dur = len(cs_samples) / TARGET_SR
                            now = time.time()
                            if dur >= PARTIAL_MIN_SEC and (now - last_partial_ts) >= PARTIAL_INTERVAL:
                                last_partial_ts = now
                                tail_n = min(len(cs_samples), int(PARTIAL_MAX_SEC * TARGET_SR))
                                tail = cs_samples[-tail_n:]
                                t_start = cs.start / TARGET_SR + (len(cs_samples) - tail_n) / TARGET_SR
                                job = {
                                    "samples": tail,
                                    "t_start": round(t_start, 2),
                                    "t_end": round((cs.start + len(cs_samples)) / TARGET_SR, 2),
                                    "key": int(round(cs.start / TARGET_SR * 1000)),
                                }
                                with self._partial_lock:
                                    self._partial_slot = job
                                self._partial_evt.set()
                    except Exception:
                        pass

                if offset > TARGET_SR * 30:
                    buf = buf[offset:]
                    offset = 0

            # 队列空闲时尝试把 VAD 内部残留语音收尾（长时间无新音频）
        # 收尾
        try:
            vad.flush()
            while not vad.empty():
                seg = vad.front
                handle_segment(seg)
                vad.pop()
        except Exception:
            pass
        try:
            if vad.is_speech_detected():
                cs = vad.current_segment
                arr = np.array(cs.samples, dtype=np.float32)
                if len(arr) >= TARGET_SR * PARTIAL_MIN_SEC:
                    handle_segment(cs)
        except Exception:
            pass
        self._final_q.put(None)
        self._emit_status("closed")
