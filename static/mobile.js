/* ==========================================================================
   面试听记 · 移动端（手机 / 平板）
   - 主题与电脑版同源（style.css 的新拟态浅色变量）
   - 侧边栏导航：手机抽屉式 / 平板常驻
   - 音源三选一：电脑内录 / 电脑麦克风 / 手机麦克风
   ========================================================================== */
(function () {
  'use strict';

  /* ------------------------------------------------------------ 常量 */
  var SR = 16000;
  var CHUNK_MS = 100;
  var CHUNK_SAMPLES = 1600;
  var CHUNK_BYTES = 3200;
  var MAX_QUEUE = 40;

  var LS_MODE = 'ir.mobile.mode';
  var LS_SRC = 'ir.mobile.source';
  var LS_DEV = 'ir.mobile.dev';

  var NAV_TITLE = {
    live: '听记', script: '逐字稿', marks: '标记', review: '点评',
    plain: '精简', students: '学生', bank: '题库', history: '历史', settings: '设置'
  };

  var SRC_LABEL = {
    pc_loopback: '电脑内录', pc_mic: '电脑麦克风', phone_mic: '手机麦克风'
  };

  var TAG_DEFS = [
    { name: '亮点', cls: 'm1' },
    { name: '硬伤', cls: 'm2' },
    { name: '对策缺失', cls: 'm3' },
    { name: '口头禅', cls: 'm4' },
    { name: '备注', cls: 'm5', note: true }
  ];
  var TAG_CLS = { '亮点': 'm1', '硬伤': 'm2', '对策缺失': 'm3', '口头禅': 'm4', '备注': 'm5' };

  var $ = function (id) { return document.getElementById(id); };
  var $$ = function (sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  };

  var state = {
    view: 'live',
    mode: 'phone',
    modeChoice: 'phone',
    info: null,
    secure: false,
    running: false,
    stopped: false,
    source: 'phone_mic',
    needPush: true,
    dev: {},
    srvLevel: 0,
    startedAt: 0,
    duration: 0,
    chunks: 0,
    bytes: 0,
    dropped: 0,
    failed: 0,
    partial: '',
    paras: {},
    segs: {},
    order: [],
    selected: null,
    sheetTags: {},
    clip: null,
    reviewBusy: false,
    reviewText: '',
    reviewMode: '',
    bank: null,
    records: null,
    students: null,
    config: null,
    plain: '',
    markFilter: null,
    question: '',
    qtype: '',
    stuName: '',
    lastSeg: '',
    pvBusy: {},
    pvErr: {},
    progT0: 0,
    progLabel: '',
    plainData: null,
    plainStage: null,
    stuEdit: '',
    stuCmp: null,
    bankOpen: null,
    bankSearch: null,
    bankCur: null,
    curQ: null,
    hist: null,
    histFile: '',
    histStu: '',
    appinfo: null
  };

  /* 录音链路运行时 */
  var rec = {
    ctx: null, stream: null, src: null, proc: null, mute: null,
    inRate: 0, phase: 0, acc: null, accLen: 0,
    queue: [], pumping: false, closed: false, abort: false,
    peak: 0, lastUi: 0, lastCtx: 0
  };

  /* ------------------------------------------------------------ 工具 */
  function nowMs() {
    return (window.performance && performance.now) ? performance.now() : Date.now();
  }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function pad2(n) { return (n < 10 ? '0' : '') + n; }

  function fmtClock(sec) {
    var s = Math.max(0, Math.floor(sec || 0));
    var h = Math.floor(s / 3600);
    var m = Math.floor((s % 3600) / 60);
    var x = s % 60;
    return h > 0 ? (h + ':' + pad2(m) + ':' + pad2(x)) : (pad2(m) + ':' + pad2(x));
  }

  function fmtKB(bytes) {
    var b = bytes || 0;
    if (b < 1024) return b + ' B';
    if (b < 1024 * 1024) return (b / 1024).toFixed(1) + ' KB';
    return (b / 1048576).toFixed(2) + ' MB';
  }

  function fmtDur(sec) {
    var s = Math.round(sec || 0);
    if (s < 60) return s + ' 秒';
    var m = Math.floor(s / 60);
    var r = s % 60;
    return m + ' 分' + (r ? ' ' + r + ' 秒' : '');
  }

  var toastTimer = null;
  function toast(msg, ms) {
    var el = $('toast');
    el.textContent = msg;
    el.classList.add('show');
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.classList.remove('show'); }, ms || 2400);
  }

  function api(url) {
    return fetch(url, { cache: 'no-store' }).then(function (r) {
      return r.text().then(function (t) {
        var d = null;
        try { d = JSON.parse(t); } catch (e) {}
        return { ok: r.ok, status: r.status, data: d, raw: t };
      });
    });
  }

  function postJSON(url, body) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    }).then(function (r) {
      return r.text().then(function (t) {
        var d = null;
        try { d = JSON.parse(t); } catch (e) {}
        return { ok: r.ok, status: r.status, data: d, raw: t };
      });
    });
  }

  function copyText(text) {
    var t = String(text || '');
    if (!t) { toast('没有可复制的内容'); return; }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(t).then(function () { toast('已复制'); },
        function () { fallbackCopy(t); });
      return;
    }
    fallbackCopy(t);
  }

  function fallbackCopy(t) {
    try {
      var ta = document.createElement('textarea');
      ta.value = t;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      toast('已复制');
    } catch (e) {
      toast('复制失败，请手动选择文本');
    }
  }

  function inline(t) {
    return esc(t)
      .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
      .replace(/`(.+?)`/g, '<code>$1</code>');
  }

  function mdLite(text) {
    var lines = String(text || '').split(/\r?\n/);
    var out = [];
    var inUl = false;
    lines.forEach(function (line) {
      var L = line.trim();
      var h = L.match(/^(#{1,6})\s+(.*)$/);
      if (h) {
        if (inUl) { out.push('</ul>'); inUl = false; }
        out.push('<h3>' + inline(h[2]) + '</h3>');
        return;
      }
      var li = L.match(/^[-*•]\s+(.*)$/);
      if (li) {
        if (!inUl) { out.push('<ul>'); inUl = true; }
        out.push('<li>' + inline(li[1]) + '</li>');
        return;
      }
      if (inUl) { out.push('</ul>'); inUl = false; }
      if (!L) { out.push('<p></p>'); return; }
      out.push('<p>' + inline(L) + '</p>');
    });
    if (inUl) out.push('</ul>');
    return out.join('');
  }

  /* ------------------------------------------------------------ 形态（手机 / 平板） */
  function initMode() {
    var saved = '';
    try { saved = localStorage.getItem(LS_MODE) || ''; } catch (e) {}
    applyMode(saved || 'auto', false);
  }

  function applyMode(choice, persist) {
    var eff = choice;
    if (choice === 'auto') eff = (window.innerWidth >= 900) ? 'tablet' : 'phone';
    if (eff !== 'tablet') eff = 'phone';

    state.modeChoice = choice;
    state.mode = eff;
    document.body.setAttribute('data-mode', eff);

    $$('.segbtn').forEach(function (b) {
      b.classList.toggle('on', b.getAttribute('data-mode') === choice);
    });
    $('btnModeTxt').textContent = (eff === 'phone') ? '平板' : '手机';
    $('modeHint').textContent = (choice === 'auto') ? '当前：跟随屏幕（' + (eff === 'tablet' ? '平板' : '手机') + '）'
      : '当前：' + (eff === 'tablet' ? '平板' : '手机');
    $('sideTip').textContent = (eff === 'tablet')
      ? '平板模式：与电脑版同排布，听记页三栏——左侧题库、中间逐字稿、右侧点评与会话。'
      : '手机模式：侧栏为抽屉，内容单列。点左上角菜单展开。';
    if (eff === 'tablet') closeSide();
    if (persist) { try { localStorage.setItem(LS_MODE, choice); } catch (e) {} }
    syncShellMode();
  }

  function onResize() {
    if (state.modeChoice === 'auto') applyMode('auto', false);
    else syncShellMode();
  }

  /* ------------------------------------------------------------ 侧栏与路由 */
  function openSide() {
    if (state.mode === 'tablet') return;
    $('side').classList.add('open');
    $('mask').classList.add('on');
  }

  function closeSide() {
    $('side').classList.remove('open');
    $('mask').classList.remove('on');
  }

  function toggleSide() {
    if ($('side').classList.contains('open')) closeSide(); else openSide();
  }

  function setView(key) {
    if (!NAV_TITLE[key]) key = 'live';
    state.view = key;
    $$('.nav-item').forEach(function (b) {
      b.classList.toggle('on', b.getAttribute('data-nav') === key);
    });
    $$('.view').forEach(function (v) {
      v.classList.toggle('on', v.id === 'view-' + key);
    });
    $('hdrTitle').textContent = NAV_TITLE[key];
    $('ops').hidden = (key !== 'live');
    $('btnHelp').style.display = (key === 'live') ? '' : 'none';

    if (state.mode === 'phone') closeSide();

    if (key === 'script') { renderAll(); setTimeout(scriptToBottom, 30); }
    if (key === 'marks') renderMarks();
    if (key === 'review') { renderParaReviewList(); updateReviewCard(); refreshReviewStat(); }
    if (key === 'plain') { ptsSyncAi(); paintPoints(); loadPlain(); }
    if (key === 'students') loadStudents();
    if (key === 'bank') loadBank();
    if (key === 'history') loadRecords();
    if (key === 'settings') loadSettings();
    if (key === 'live') { updateStats(); renderClip(); renderCtx(); }
    else closeCtxStuPick();
    syncShellMode();
  }

  /* 平板形态的三栏外壳：左侧题库（复用 #view-bank）、中间听记、右侧点评与会话
     手机竖屏保持原有「一页一视图 + 抽屉侧栏」形态，不加 tab-3col */
  function syncShellMode() {
    /* 760px 是 CSS 侧三栏的生效断点，窄屏即便选了平板也不硬塞三栏，避免挤压 */
    var three = (state.mode === 'tablet') && (state.view === 'live') && (window.innerWidth >= 760);
    document.body.classList.toggle('tab-3col', three);
    if (!three) return;
    if (!state.students) loadStudents();
    if (!state.bank) loadBank();
    else loadBankCur();
  }

  /* ------------------------------------------------------------ 音源 */
  function sourceMap() {
    var m = {};
    var arr = (state.info && state.info.sources) || [];
    arr.forEach(function (s) { if (s && s.source) m[s.source] = s; });
    if (!m.pc_loopback) m.pc_loopback = { source: 'pc_loopback', need_push: false, label: '电脑内录' };
    if (!m.pc_mic) m.pc_mic = { source: 'pc_mic', need_push: false, label: '电脑麦克风' };
    if (!m.phone_mic) m.phone_mic = { source: 'phone_mic', need_push: true, label: '手机麦克风' };
    return m;
  }

  function isNeedPush(src) {
    var m = sourceMap();
    var it = m[src || state.source];
    return it ? !!it.need_push : (src || state.source) === 'phone_mic';
  }

  function deviceList(src) {
    var pc = (state.info && state.info.pc_devices) || {};
    if (src === 'pc_loopback') return pc.loopback || [];
    if (src === 'pc_mic') return pc.mic || [];
    return [];
  }

  function curDevice() {
    var list = deviceList(state.source);
    if (!list.length) return null;
    var want = state.dev[state.source];
    if (want == null || want === '') return list[0];
    for (var i = 0; i < list.length; i++) {
      if (String(list[i].index) === String(want)) return list[i];
    }
    return list[0];
  }

  function initSourceFromStore() {
    var s = '';
    try { s = localStorage.getItem(LS_SRC) || ''; } catch (e) {}
    if (s === 'pc_loopback' || s === 'pc_mic' || s === 'phone_mic') state.source = s;
    try {
      var d = JSON.parse(localStorage.getItem(LS_DEV) || '{}');
      if (d && typeof d === 'object') state.dev = d;
    } catch (e) {}
    state.needPush = isNeedPush(state.source);
  }

  function renderSources() {
    $$('#sourceGrid .src-btn').forEach(function (b) {
      var src = b.getAttribute('data-src');
      b.classList.toggle('on', src === state.source);
      b.setAttribute('aria-checked', src === state.source ? 'true' : 'false');
      b.setAttribute('aria-disabled', state.running ? 'true' : 'false');
      var m = sourceMap()[src] || {};
      var sub = b.querySelector('.sb-s');
      if (sub) {
        if (src === 'pc_loopback') sub.textContent = '系统声音';
        else if (src === 'pc_mic') sub.textContent = '电脑收音';
        else sub.textContent = '本机收音';
        if (state.running && src === state.source) sub.textContent = '录音中';
      }
      b.disabled = false;
    });
    $('srcBadge').textContent = SRC_LABEL[state.source] || '';
    $('sourceHint').textContent = state.running
      ? '录音中不可切换'
      : (state.source === 'phone_mic' ? '手机直接推流' : '电脑端直接采集，手机不推流');
    renderDeviceSel();
    buildStats();
  }

  function renderDeviceSel() {
    var row = $('devRow');
    if (state.source === 'phone_mic') { row.hidden = true; return; }
    row.hidden = false;
    var sel = $('deviceSel');
    var list = deviceList(state.source);
    if (!list.length) {
      sel.innerHTML = '<option value="">（未取到设备，点右侧刷新）</option>';
      return;
    }
    var want = state.dev[state.source];
    var html = list.map(function (d) {
      var label = '#' + d.index + ' ' + (d.name || '设备') +
        (d.rate ? ' · ' + d.rate + 'Hz/' + (d.channels || 1) + 'ch' : '');
      return '<option value="' + esc(d.index) + '">' + esc(label) + '</option>';
    }).join('');
    sel.innerHTML = html;
    var hit = null;
    if (want != null && want !== '') {
      for (var i = 0; i < list.length; i++) {
        if (String(list[i].index) === String(want)) { hit = list[i]; break; }
      }
    }
    if (!hit) hit = list[0];
    sel.value = String(hit.index);
    state.dev[state.source] = hit.index;
    saveDev();
  }

  function saveDev() {
    try { localStorage.setItem(LS_DEV, JSON.stringify(state.dev)); } catch (e) {}
  }

  function pickSource(src, fromRestore) {
    if (state.running) { toast('录音中不能切换声音来源，请先停止'); return; }
    if (!SRC_LABEL[src]) return;
    state.source = src;
    state.needPush = isNeedPush(src);
    try { localStorage.setItem(LS_SRC, src); } catch (e) {}
    renderSources();
    buildStats();
    if (!fromRestore) {
      var tip = isNeedPush(src) ? '已选手机麦克风：点开始时申请麦克风权限'
        : '已选' + SRC_LABEL[src] + '：由电脑端采集，手机无需授权';
      toast(tip, 2600);
    }
  }

  /* ------------------------------------------------------------ 会话统计条 */
  function buildStats() {
    var el = $('stats');
    if (!el) return;
    if (state.needPush) {
      el.innerHTML = '<span>已推 <b id="sChunk">0</b> 块</span>'
        + '<span>流量 <b id="sFlow">0.0 KB</b></span>'
        + '<span>丢 <b id="sDrop">0</b></span>'
        + '<span>失败 <b id="sFail">0</b></span>';
    } else {
      el.innerHTML = '<span>电脑端直接采集，无需推流</span>'
        + '<span>识别 <b id="sCnt">' + countSegs() + '</b> 句</span>';
    }
    updateStats();
  }

  function updateStats() {
    var a = $('sChunk'), b = $('sFlow'), c = $('sDrop'), d = $('sFail'), e = $('sCnt');
    if (a) a.textContent = state.chunks;
    if (b) b.textContent = fmtKB(state.bytes);
    if (c) c.textContent = state.dropped;
    if (d) d.textContent = state.failed;
    if (e) e.textContent = countSegs();
  }

  function countSegs() { return Object.keys(state.segs).length; }

  /* ------------------------------------------------------------ 录音链路（仅手机麦克风） */
  function startCapture() {
    if (!state.secure) return Promise.reject(new Error('insecure'));
    var Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return Promise.reject(new Error('浏览器不支持 AudioContext'));
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      return Promise.reject(new Error('当前页面无法开麦（需要 HTTPS 或 localhost）'));
    }
    return navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true
      },
      video: false
    }).then(function (stream) {
      rec.stream = stream;
      var ctx;
      try { ctx = new Ctx({ sampleRate: SR }); } catch (err) { ctx = new Ctx(); }
      rec.ctx = ctx;
      rec.inRate = ctx.sampleRate || 48000;
      rec.phase = 0;
      rec.acc = new Int16Array(CHUNK_SAMPLES);
      rec.accLen = 0;
      rec.queue = [];
      rec.closed = false;
      rec.abort = false;
      rec.pumping = false;
      rec.peak = 0;
      rec.src = ctx.createMediaStreamSource(stream);
      rec.proc = ctx.createScriptProcessor ? ctx.createScriptProcessor(2048, 1, 1) : null;
      if (!rec.proc) throw new Error('浏览器不支持 ScriptProcessor，请更换浏览器');
      rec.mute = ctx.createGain();
      rec.mute.gain.value = 0;
      rec.proc.onaudioprocess = function (ev) {
        if (rec.closed) return;
        pushSamples(ev.inputBuffer.getChannelData(0));
      };
      rec.src.connect(rec.proc);
      rec.proc.connect(rec.mute);
      rec.mute.connect(ctx.destination);
      if (ctx.state === 'suspended' && ctx.resume) ctx.resume();
      return true;
    });
  }

  function pushSamples(input) {
    var pk = 0;
    var n = input.length;
    var i, a;
    for (i = 0; i < n; i += 2) {
      a = input[i] < 0 ? -input[i] : input[i];
      if (a > pk) pk = a;
    }
    if (pk > rec.peak) rec.peak = pk;

    var ratio = rec.inRate / SR;
    if (ratio > 0.999 && ratio < 1.001) {
      for (i = 0; i < n; i++) feed(input[i]);
    } else {
      var p = rec.phase;
      while (p + 1 < n) {
        var i0 = Math.floor(p);
        var frac = p - i0;
        feed(input[i0] * (1 - frac) + input[i0 + 1] * frac);
        p += ratio;
      }
      rec.phase = p - n;
      if (rec.phase < 0) rec.phase = 0;
    }
  }

  function feed(v) {
    var x = v;
    if (x > 1) x = 1; else if (x < -1) x = -1;
    rec.acc[rec.accLen++] = (x < 0 ? x * 0x8000 : x * 0x7fff) | 0;
    if (rec.accLen >= CHUNK_SAMPLES) emitChunk();
  }

  function emitChunk() {
    var buf = new ArrayBuffer(CHUNK_BYTES);
    var view = new Int16Array(buf);
    var copy = rec.accLen;
    if (copy > CHUNK_SAMPLES) copy = CHUNK_SAMPLES;
    for (var i = 0; i < copy; i++) view[i] = rec.acc[i];
    for (var k = copy; k < CHUNK_SAMPLES; k++) view[k] = 0;
    rec.accLen = 0;
    rec.queue.push(buf);
    if (rec.queue.length > MAX_QUEUE) {
      var drop = rec.queue.length - MAX_QUEUE;
      rec.queue.splice(0, drop);
      state.dropped += drop;
    }
    pump();
  }

  function pump() {
    if (rec.pumping || !rec.queue.length) return;
    rec.pumping = true;
    var one = function () {
      if (rec.abort) { rec.queue.length = 0; rec.abort = false; rec.pumping = false; return; }
      if (!rec.queue.length) { rec.pumping = false; return; }
      var buf = rec.queue.shift();
      fetch('/api/mobile/audio?rate=' + SR + '&channels=1&fmt=s16le&agc=1', {
        method: 'POST',
        headers: { 'Content-Type': 'application/octet-stream' },
        body: buf
      }).then(function (r) {
        if (r.ok) {
          state.chunks++;
          state.bytes += buf.byteLength;
          return null;
        }
        return r.text().then(function (t) {
          var j = null;
          try { j = JSON.parse(t); } catch (e) {}
          if (r.status === 409) {
            rec.abort = true;
            setTimeout(function () { localStop('服务端会话已结束，录音已停止'); }, 0);
          } else {
            state.failed++;
          }
          return j;
        });
      }).catch(function () { state.failed++; }).then(function () { one(); });
    };
    one();
  }

  function stopCapture(flush) {
    rec.closed = true;
    try {
      if (rec.proc) rec.proc.onaudioprocess = null;
      if (flush && rec.acc && rec.accLen > 0) emitChunk();
    } catch (e) {}
    try { if (rec.src) rec.src.disconnect(); } catch (e) {}
    try { if (rec.proc) rec.proc.disconnect(); } catch (e) {}
    try { if (rec.mute) rec.mute.disconnect(); } catch (e) {}
    try { if (rec.stream) rec.stream.getTracks().forEach(function (t) { t.stop(); }); } catch (e) {}
    try { if (rec.ctx && rec.ctx.close) rec.ctx.close(); } catch (e) {}
    rec.ctx = null; rec.stream = null; rec.src = null; rec.proc = null; rec.mute = null;
  }

  /* ------------------------------------------------------------ 启停 */
  function setRunning(on) {
    state.running = !!on;
    var dot = $('dot');
    var stat = $('statText');
    if (on) {
      dot.className = 'dot live';
      stat.textContent = '录音中 · ' + (SRC_LABEL[state.source] || '');
    } else {
      dot.className = 'dot';
      stat.textContent = state.stopped ? '已停止' : '未开始';
    }
    $('btnStart').disabled = !!on;
    $('btnStop').disabled = !on;
    $$('#sourceGrid .src-btn').forEach(function (b) { b.disabled = false; });
    $('sourceHint').textContent = on ? '录音中不可切换'
      : (state.source === 'phone_mic' ? '手机直接推流' : '电脑端直接采集，手机不推流');
    $('srcBadge').textContent = SRC_LABEL[state.source] || '';
    if (!on) {
      $('barFill').style.width = '0%';
      $('barFill').className = '';
    }
    $$('#sourceGrid .src-btn').forEach(function (b) {
      var sub = b.querySelector('.sb-s');
      if (sub) {
        var src = b.getAttribute('data-src');
        if (on && src === state.source) sub.textContent = '录音中';
        else if (src === 'pc_loopback') sub.textContent = '系统声音';
        else if (src === 'pc_mic') sub.textContent = '电脑收音';
        else sub.textContent = '本机收音';
      }
    });
  }

  function syncSession(d) {
    if (!d) return;
    if (d.source) state.source = d.source;
    if (d.need_push != null) state.needPush = !!d.need_push;
    else state.needPush = isNeedPush(state.source);
    if (d.device_kind || d.device) {
      state.srvDevice = d.device || '';
    }
    renderSources();
  }

  function ensureFreshSession() {
    return api('/api/mobile/clip').then(function (r) {
      var c = (r && r.data) || {};
      var cnt = (typeof c.segments === 'number') ? c.segments : ((c.segments || []).length);
      var hasOld = !c.running && cnt > 0;
      if (!hasOld) return false;
      return postJSON('/api/session/new', {}).then(function (r2) {
        if (r2.ok && r2.data && r2.data.ok) {
          resetAll();
          state.clip = null;
          renderClip();
          return true;
        }
        return false;
      });
    }).catch(function () { return false; });
  }

  function doStart() {
    if (state.running) return;
    var needPush = isNeedPush(state.source);
    if (needPush && !state.secure) {
      $('secAlert').hidden = false;
      try { $('secAlert').scrollIntoView({ behavior: 'smooth', block: 'nearest' }); } catch (e) {}
      toast('手机麦克风需要 HTTPS 页面，请按提示改用安全地址打开', 4200);
      return;
    }
    var body = { source: state.source };
    if (needPush) {
      body.name = '手机/平板网页端';
    } else {
      var d = curDevice();
      if (!d) { toast('没有取到电脑设备清单，请点「刷新」后重试'); return; }
      body.device = d.index;
      body.name = d.name;
    }
    $('btnStart').disabled = true;
    $('statText').textContent = '正在启动…';
    $('dot').className = 'dot wait';

    ensureFreshSession().then(function (fresh) {
      state.freshSession = !!fresh;
      return postJSON('/api/mobile/session/start', body);
    }).then(function (r) {
      var d = r.data || {};
      if (!r.ok || d.ok === false) {
        if (r.status === 409 && String(d.code || '') === 'busy') {
          throw new Error('电脑端正在录音，请先在电脑上停止后再试');
        }
        throw new Error(d.msg || d.message || ('启动失败（HTTP ' + r.status + '）'));
      }
      syncSession(d);
      return needPush ? startCapture() : null;
    }).then(function () {
      state.running = true;
      state.startedAt = nowMs();
      state.duration = 0;
      state.stopped = false;
      state.chunks = 0; state.bytes = 0; state.dropped = 0; state.failed = 0;
      setRunning(true);
      $('empty').hidden = !!$('script').innerHTML;
      toast(state.freshSession ? '已开新一场，开始听记' : '已开始听记', 2400);
    }).catch(function (err) {
      stopCapture(false);
      state.running = false;
      setRunning(false);
      var m = (err && err.message) || '启动失败';
      if (/NotAllowed|Permission|denied/i.test(m)) m = '麦克风权限被拒绝，请在浏览器设置里允许本页使用麦克风';
      else if (/NotFound|Devices/i.test(m)) m = '没有找到麦克风设备';
      else if (/insecure/i.test(m)) m = '手机麦克风需要 HTTPS 页面';
      toast(m, 3800);
      if (needPush) postJSON('/api/mobile/session/stop', {});
    });
  }

  function localStop(msg) {
    if (!state.running) return;
    state.running = false;
    stopCapture(true);
    state.stopped = true;
    setRunning(false);
    setPartial('');
    updateStats();
    if (msg) toast(msg, 2600);
  }

  function doStop() {
    if (!state.running) return;
    $('btnStop').disabled = true;
    $('statText').textContent = '正在收尾…';
    state.running = false;
    stopCapture(true);

    var sendStop = function () {
      postJSON('/api/mobile/session/stop', {}).then(function (r) {
        state.stopped = true;
        setRunning(false);
        var d = r.data || {};
        if (d.duration) state.duration = d.duration;
        if (d.ok === false) {
          toast('停止失败：' + (d.msg || '未知错误'), 3200);
        } else if (d.saved) {
          toast('已停止并自动存档：' + d.saved, 4200);
          setPanel('已自动存档', '本地',
            '<p>本次录音已结束，按当前学生自动存档。</p><p class="dim">存档文件：' + esc(d.saved) + '</p>');
        } else {
          toast('已停止（本次没有识别到内容，未生成存档）', 3400);
        }
        loadClip();
        loadInfo();
      }).catch(function (e) {
        setRunning(false);
        state.stopped = true;
        toast('停止请求失败：' + (e && e.message), 3200);
      });
    };

    var waitEmpty = function (tries) {
      if ((rec.queue.length || rec.pumping) && tries > 0) {
        setTimeout(function () { waitEmpty(tries - 1); }, 100);
        return;
      }
      setTimeout(sendStop, 150);
    };
    waitEmpty(15);
  }

  /* ------------------------------------------------------------ 计时 / 电平 */
  function tick() {
    if (state.running && state.startedAt) {
      state.duration = (nowMs() - state.startedAt) / 1000;
    }
    $('clock').textContent = fmtClock(state.duration);

    var now = nowMs();
    if (now - rec.lastUi > 90) {
      rec.lastUi = now;
      var pk = state.needPush ? rec.peak : state.srvLevel;
      if (state.needPush) rec.peak = pk * 0.45;
      var pct = Math.min(100, Math.round(Math.sqrt(Math.max(0, pk || 0)) * 130));
      var fill = $('barFill');
      fill.style.width = pct + '%';
      fill.className = pct > 92 ? 'clip' : (pct > 68 ? 'hot' : '');
      updateStats();
      refreshBusyCards();
      tickProg();
      if (now - rec.lastCtx > 900) { rec.lastCtx = now; renderCtx(); }
    }
  }

  function pollStatus() {
    api('/api/mobile/status').then(function (r) {
      var d = r.data || {};
      if (!d.ok) return;
      if (d.level != null) state.srvLevel = Number(d.level) || 0;
      if (d.source) state.source = d.source;
      if (d.need_push != null) state.needPush = !!d.need_push;
      if (!state.running && d.running) {
        // 电脑端已开始（用户在电脑上操作），同步 UI
        state.running = true;
        state.startedAt = nowMs() - (Number(d.duration) || 0) * 1000;
        setRunning(true);
        renderSources();
      }
      if (state.running && !d.running) {
        localStop('后端会话已结束');
      }
      if (d.duration && state.running) {
        var local = (nowMs() - state.startedAt) / 1000;
        if (Math.abs(Number(d.duration) - local) > 2) {
          state.startedAt = nowMs() - Number(d.duration) * 1000;
        }
      }
    }).catch(function () {});
  }

  /* ------------------------------------------------------------ 逐字稿渲染 */
  function paraKeyOrder() {
    state.order.sort(function (a, b) {
      var pa = state.paras[a], pb = state.paras[b];
      return ((pa && pa.idx) || 0) - ((pb && pb.idx) || 0);
    });
    return state.order;
  }

  function ensurePara(pid, idx) {
    var p = state.paras[pid];
    if (!p) {
      p = state.paras[pid] = {
        pid: pid, idx: idx || (state.order.length + 1),
        segs: [], title: '', label: '', conf: ''
      };
      state.order.push(pid);
      paraKeyOrder();
    } else if (idx) {
      p.idx = idx;
    }
    return p;
  }

  function upsertSeg(seg, pid) {
    if (!seg || !seg.id) return null;
    var id = seg.id;
    var p = ensurePara(pid || seg.pid, null);
    var s = state.segs[id];
    if (!s) {
      s = state.segs[id] = { id: id, pid: p.pid, text: '', t_start: 0, t_end: 0, tags: [], ctags: [], note: '' };
      if (p.segs.indexOf(id) < 0) p.segs.push(id);
    }
    if (seg.text) s.text = seg.text;
    if (seg.t_start != null) s.t_start = seg.t_start;
    if (seg.t_end != null) s.t_end = seg.t_end;
    if (seg.tags) s.tags = seg.tags.slice();
    if (seg.ctags) s.ctags = seg.ctags.slice();
    if (seg.note != null) s.note = seg.note || '';
    if (s.pid !== p.pid) {
      var old = state.paras[s.pid];
      if (old) {
        var k = old.segs.indexOf(id);
        if (k >= 0) old.segs.splice(k, 1);
      }
      s.pid = p.pid;
      if (p.segs.indexOf(id) < 0) p.segs.push(id);
    }
    return s;
  }

  function segTagsHtml(s) {
    var out = [];
    (s.tags || []).forEach(function (t) {
      out.push('<span class="tg ' + (TAG_CLS[t] || 'm5') + '">' + esc(t) + '</span>');
    });
    (s.ctags || []).forEach(function (t) {
      out.push('<span class="tg m5">' + esc(t) + '</span>');
    });
    if (s.note) out.push('<span class="tg note">备注：' + esc(s.note) + '</span>');
    return out.length ? '<span class="sg-tags">' + out.join('') + '</span>' : '';
  }

  /* 段落时间范围 / 字数 / 句数（优先用后端下发的 t_start / t_end / chars） */
  function paraRange(p) {
    var first = null, last = null, chars = 0, n = 0;
    for (var i = 0; i < p.segs.length; i++) {
      var s = state.segs[p.segs[i]];
      if (!s) continue;
      n++;
      chars += (s.text || '').length;
      var a = (s.t_start != null) ? Number(s.t_start) : null;
      if (a != null && (first == null || a < first)) first = a;
      var b = (s.t_end != null) ? Number(s.t_end) : a;
      if (b != null && (last == null || b > last)) last = b;
    }
    if (p.t_start != null && (first == null || Number(p.t_start) < first)) first = Number(p.t_start);
    if (p.t_end != null && (last == null || Number(p.t_end) > last)) last = Number(p.t_end);
    return { first: first, last: last, chars: p.chars || chars, n: n };
  }

  function paraMetaText(p) {
    var r = paraRange(p);
    var bits = [];
    if (r.first != null) bits.push(fmtClock(r.first) + '–' + fmtClock(r.last != null ? r.last : r.first));
    bits.push(r.n + ' 句');
    bits.push(r.chars + ' 字');
    if (!p.closed) bits.push('进行中');
    return bits.join(' · ');
  }

  function paraReviewState(p) {
    if (!p) return 'none';
    if (state.pvBusy[p.pid]) return 'busy';
    if (p.ai_note) {
      if (p.review_mode === 'dup' || p.review_mode === 'merge') return 'skip';
      return 'done';
    }
    if (state.pvErr[p.pid]) return 'err';
    return 'none';
  }

  function paraNoteHtml(p, st) {
    if (st === 'busy') {
      var b = state.pvBusy[p.pid] || {};
      var sec = b.t0 ? Math.round((nowMs() - b.t0) / 1000) : 0;
      return '<div class="para-note wait">正在生成第 ' + esc(p.idx || '') + ' 段点评… ' + sec + ' 秒</div>';
    }
    if (st === 'err') {
      return '<div class="para-note err">点评失败：' + esc(state.pvErr[p.pid] || '未知错误') + '（可点「已点评」重试）</div>';
    }
    if (!p.ai_note) return '';
    if (st === 'skip') {
      return '<div class="para-note wait"><div class="pn-hd">结构提示</div><div class="pn-b">'
        + esc(p.ai_note) + '</div></div>';
    }
    var modeTxt = p.review_mode === 'ai' ? '段落点评 · AI'
      : (p.review_mode === 'local' ? '段落点评 · 本地规则' : '段落点评');
    return '<div class="para-note"><div class="pn-hd">' + esc(modeTxt) + '</div>'
      + '<div class="pn-b">' + mdLite(p.ai_note) + '</div></div>';
  }

  function paraActHtml(p, st) {
    if (st === 'busy') return '<span class="para-act busy">点评中…</span>';
    var label = st === 'done' ? '已点评' : (st === 'skip' ? '已跳过' : (st === 'err' ? '重新点评' : '点评本段'));
    return '<button type="button" class="para-act' + (st === 'done' || st === 'skip' ? ' done' : '')
      + '" data-act="pr" data-pid="' + esc(p.pid) + '">' + esc(label) + '</button>';
  }

  function paraHtml(p) {
    var conf = (p.conf || '').toLowerCase();
    var st = paraReviewState(p);
    var hd = ['<div class="para-hd"><span class="para-idx">第 ' + esc(p.idx || '') + ' 段</span>'];
    if (p.label) hd.push('<span class="para-tag' + (conf ? ' conf-' + esc(conf) : '') + '">' + esc(p.label) + '</span>');
    hd.push('<span class="para-meta">' + esc(paraMetaText(p)) + '</span>');
    hd.push(paraActHtml(p, st));
    hd.push('</div>');

    var body = [];
    for (var i = 0; i < p.segs.length; i++) {
      var s = state.segs[p.segs[i]];
      if (!s) continue;
      var t = (s.t_start != null && s.t_start > 0) ? fmtClock(s.t_start) : '';
      body.push('<button type="button" class="sent' + (state.selected === s.id ? ' sel' : '')
        + '" data-id="' + esc(s.id) + '">'
        + (t ? '<span class="t">' + t + '</span>' : '')
        + esc(s.text || '') + segTagsHtml(s) + '</button>');
    }
    return '<div class="para" data-pid="' + esc(p.pid) + '">' + hd.join('') + body.join('')
      + paraNoteHtml(p, st) + '</div>';
  }

  function insertPara(box, fresh, p) {
    var kids = box.querySelectorAll('.para');
    for (var i = 0; i < kids.length; i++) {
      var kp = state.paras[kids[i].getAttribute('data-pid')];
      if (kp && (kp.idx || 0) > (p.idx || 0)) {
        kids[i].before(fresh);
        return;
      }
    }
    box.appendChild(fresh);
  }

  function renderPara(pid) {
    var p = state.paras[pid];
    ['script', 'scriptFull'].forEach(function (boxId) {
      var box = $(boxId);
      if (!box) return;
      var node = box.querySelector('.para[data-pid="' + pid + '"]');
      if (!p || !p.segs.length) {
        if (node) node.remove();
        return;
      }
      var tmp = document.createElement('div');
      tmp.innerHTML = paraHtml(p);
      var fresh = tmp.firstChild;
      if (node) node.replaceWith(fresh);
      else insertPara(box, fresh, p);
    });
    syncEmpty();
    updateScriptStat();
  }

  function renderAll() {
    var pids = paraKeyOrder();
    var html = pids.map(function (pid) {
      var p = state.paras[pid];
      return (p && p.segs.length) ? paraHtml(p) : '';
    }).join('');
    $('script').innerHTML = html;
    $('scriptFull').innerHTML = html;
    syncEmpty();
    updateScriptStat();
    if (state.view === 'live') {
      var el = $('view-live');
      el.scrollTop = el.scrollHeight;
    }
  }

  function syncEmpty() {
    var has = !!$('script').innerHTML;
    $('empty').hidden = has || !!state.partial;
    $('scriptFullEmpty').hidden = !!$('scriptFull').innerHTML;
  }

  function updateScriptStat() {
    var s = countSegs();
    var p = state.order.length;
    var chars = 0;
    Object.keys(state.segs).forEach(function (k) { chars += (state.segs[k].text || '').length; });
    $('scriptStat').textContent = s + ' 句 · ' + p + ' 段 · ' + chars + ' 字';
    $('scriptHint').textContent = state.running ? '录音中 · 点句子可标记' : '点句子可加标记';
    var bl = $('badgeLive'), bs = $('badgeScript'), bm = $('badgeMarks');
    if (s > 0) { bl.hidden = false; bl.textContent = s; bs.hidden = false; bs.textContent = s; }
    else { bl.hidden = true; bs.hidden = true; }
    var marks = markList().length;
    if (marks > 0) { bm.hidden = false; bm.textContent = marks; } else { bm.hidden = true; }
    renderCtx();
  }

  /* ------------------------------------------------------------ 听记页上下文（学生 / 题目 / 当前段 / 最新一句） */
  function lastParaObj() {
    var order = paraKeyOrder();
    for (var i = order.length - 1; i >= 0; i--) {
      var p = state.paras[order[i]];
      if (p && p.segs.length) return p;
    }
    return null;
  }

  function curQuestion() {
    return state.question || (state.clip && state.clip.question) || '';
  }

  function renderCtx() {
    var qs = $('ctxStudent'), qq = $('ctxQuestion'), qp = $('ctxPara'), ql = $('ctxLast'), qh = $('ctxHint');
    if (!qs) return;
    var stu = state.stuName || (state.clip && state.clip.student) || '';
    var q = curQuestion();
    var qt = state.qtype || (state.clip && state.clip.qtype) || '';
    qs.textContent = stu || '未选择（点「切换学生」选一位）';
    qs.classList.toggle('dim', !stu);
    qq.textContent = q ? ((qt ? '[' + qt + '] ' : '') + q) : '未设置题目（可在电脑端选题）';
    qq.classList.toggle('dim', !q);
    var p = lastParaObj();
    if (p) {
      var r = paraRange(p);
      qp.textContent = '第 ' + (p.idx || '') + ' 段' + (p.label ? ' · ' + p.label : '')
        + ' · ' + r.n + ' 句 ' + r.chars + ' 字' + (p.closed ? ' · 已收段' : ' · 进行中');
      qp.classList.remove('dim');
    } else if (state.partial) {
      qp.textContent = '第 1 段 · 正在说…';
      qp.classList.remove('dim');
    } else {
      qp.textContent = '还没有段落';
      qp.classList.add('dim');
    }
    var last = state.lastSeg || state.partial || '';
    if (last) {
      ql.innerHTML = '<span class="k">最新</span>' + esc(last);
      ql.classList.add('live');
    } else {
      ql.innerHTML = '<span class="k">最新</span>还没有逐字稿，开始后会在这里实时显示。';
      ql.classList.remove('live');
    }
    var n = countSegs();
    qh.textContent = (state.running ? '录音中' : (n ? '已停止' : '未开始'))
      + ' · ' + n + ' 句 / ' + state.order.length + ' 段';
  }

  /* 逐字稿视图自动滚动 */
  function scriptNearBottom() {
    var el = $('view-script');
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight < 140;
  }

  function scriptToBottom() {
    var el = $('view-script');
    if (el) el.scrollTop = el.scrollHeight;
  }

  /* 从标记 / 点评跳回原文位置并高亮 */
  function gotoSeg(id) {
    var s = state.segs[id];
    if (!s) { toast('这条内容已不在当前会话中'); return; }
    if (!s.pid) { toast('找不到该句所属段落'); return; }
    setView('script');
    state.selected = id;
    renderPara(s.pid);
    var box = $('scriptFull');
    var el = box ? box.querySelector('.sent[data-id="' + id + '"]') : null;
    if (!el) { toast('原文暂未渲染，请稍后重试'); return; }
    try { el.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
    catch (e) { try { el.scrollIntoView(); } catch (e2) {} }
    el.classList.remove('flash');
    void el.offsetWidth;
    el.classList.add('flash');
    var pid = s.pid;
    setTimeout(function () {
      if (state.selected === id) state.selected = null;
      var sec = state.paras[pid];
      if (sec) renderPara(pid);
    }, 2000);
  }

  /* 从标记 / 点评跳回某一段原文 */
  function gotoPara(pid) {
    var p = state.paras[pid];
    if (!p) { toast('这段内容已不在当前会话中'); return; }
    if (!p.segs.length) { toast('这一段还没有内容'); return; }
    setView('script');
    state.selected = p.segs[0];
    renderPara(pid);
    var box = $('scriptFull') || $('script');
    var el = box ? box.querySelector('.para[data-pid="' + pid + '"]') : null;
    if (!el) { toast('原文暂未渲染，请稍后重试'); return; }
    try { el.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
    catch (e) { try { el.scrollIntoView(); } catch (e2) {} }
    el.classList.remove('flash');
    void el.offsetWidth;
    el.classList.add('flash');
    setTimeout(function () {
      if (state.selected === p.segs[0]) state.selected = null;
      var sec = state.paras[pid];
      if (sec) renderPara(pid);
    }, 2000);
  }

  function nearBottom() {
    var el = $('view-live');
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight < 160;
  }

  function setPartial(text) {
    state.partial = text || '';
    var box = $('partial');
    if (!state.partial) { box.hidden = true; $('partialText').textContent = ''; syncEmpty(); return; }
    box.hidden = false;
    $('partialText').textContent = state.partial;
    $('empty').hidden = true;
    if (nearBottom()) {
      var el = $('view-live');
      el.scrollTop = el.scrollHeight;
    }
  }

  function resetAll() {
    state.paras = {}; state.segs = {}; state.order = []; state.selected = null;
    state.chunks = 0; state.bytes = 0; state.dropped = 0; state.failed = 0;
    state.duration = 0; state.stopped = false; state.partial = '';
    renderAll();
    setPartial('');
    $('clock').textContent = '00:00';
    $('barFill').style.width = '0%';
    buildStats();
  }

  /* ------------------------------------------------------------ SSE */
  function onSegment(ev) {
    var seg = ev.seg || {};
    var para = ev.para || {};
    var p = ensurePara(seg.pid || para.id, para.idx);
    if (para.title) p.title = para.title;
    if (para.plain_label) p.label = para.plain_label;
    if (para.plain_conf) p.conf = para.plain_conf;
    if (para.closed) p.closed = true;
    upsertSeg(seg, p.pid);
    state.lastSeg = seg.text || state.lastSeg;
    renderPara(p.pid);
    setPartial('');
    renderCtx();
    if (state.view === 'live' && nearBottom()) {
      var el = $('view-live');
      el.scrollTop = el.scrollHeight;
    }
    if (state.view === 'script' && scriptNearBottom()) scriptToBottom();
  }

  function onParagraph(ev) {
    var para = ev.para || {};
    if (!para.id) return;
    var p = ensurePara(para.id, para.idx);
    if (para.title) p.title = para.title;
    if (para.plain_label) p.label = para.plain_label;
    if (para.plain_conf) p.conf = para.plain_conf;
    if (para.closed) p.closed = true;
    if (para.segments) para.segments.forEach(function (s) { upsertSeg(s, para.id); });
    if (para.ai_note) p.ai_note = para.ai_note;
    if (para.review_mode) p.review_mode = para.review_mode;
    renderPara(p.pid);
    renderCtx();
    if (state.view === 'marks') renderMarks();
    if (state.view === 'review') renderParaReviewList();
    if (state.view === 'script' && scriptNearBottom()) scriptToBottom();
  }

  function onPlain(ev) {
    var pid = ev.pid;
    if (!pid) return;
    var p = ensurePara(pid, ev.idx);
    if (ev.plain_label != null) p.label = ev.plain_label || p.label;
    if (ev.plain_conf != null) p.conf = ev.plain_conf || p.conf;
    (ev.plain_parts || []).forEach(function (part) {
      var id = part.seg || part.sid;
      if (!id) return;
      var s = upsertSeg({
        id: id, text: part.text, tags: part.tags, ctags: part.ctags,
        note: part.note, t_start: part.t_start
      }, pid);
      if (s && part.text) s.text = part.text;
    });
    renderPara(pid);
    ptsMarkStale();
  }

  function onAiNote(ev) {
    var id = ev.id || ev.seg;
    var s = state.segs[id];
    if (!s || !ev.ai_note) return;
    s.ai_note = ev.ai_note;
    if (s.pid) renderPara(s.pid);
  }

  function onMark(ev) {
    var id = ev.id || ev.seg;
    var s = state.segs[id];
    if (!s) return;
    if (ev.tags) s.tags = ev.tags.slice();
    if (ev.ctags) s.ctags = ev.ctags.slice();
    if (ev.note != null) s.note = ev.note || '';
    if (s.pid) renderPara(s.pid);
    if (state.view === 'marks') renderMarks();
  }

  function onStatus(ev) {
    var st = ev.state || '';
    if (st === 'stopped' || st === 'closed') {
      if (state.running) localStop('');
      state.stopped = true;
      setRunning(false);
      if (ev.duration) state.duration = ev.duration;
      loadClip();
    }
  }

  function openSSE() {
    if (!window.EventSource) return;
    var es = new EventSource('/api/stream');
    es.onmessage = function (e) {
      var ev;
      try { ev = JSON.parse(e.data); } catch (err) { return; }
      if (!ev || !ev.type) return;
      switch (ev.type) {
        case 'partial': setPartial(ev.text); break;
        case 'segment': onSegment(ev); break;
        case 'paragraph': onParagraph(ev); break;
        case 'plain': onPlain(ev); break;
        case 'mark': onMark(ev); break;
        case 'status': onStatus(ev); break;
        case 'ai_note': onAiNote(ev); break;
        case 'reset': resetAll(); break;
        case 'para_error': if (ev.msg) toast('段落分析失败：' + ev.msg, 2600); break;
      }
    };
    es.onerror = function () {};
  }

  /* ------------------------------------------------------------ 标记抽屉 */
  function openSheet(segId) {
    var s = state.segs[segId];
    if (!s) return;
    state.selected = segId;
    state.sheetTags = {};
    (s.tags || []).forEach(function (t) { state.sheetTags[t] = true; });
    $('sheetQuote').textContent = s.text || '';
    $('noteInput').value = s.note || '';
    renderTagRow();
    $('sheet').hidden = false;
    requestAnimationFrame(function () { $('sheet').classList.add('on'); });
    if (s.pid) renderPara(s.pid);
  }

  function renderTagRow() {
    var hasNote = !!($('noteInput').value || '').trim();
    $('tagRow').innerHTML = TAG_DEFS.map(function (t) {
      var on = t.note ? hasNote : !!state.sheetTags[t.name];
      return '<button type="button" class="' + t.cls + (on ? ' on' : '') + '"'
        + (t.note ? ' data-note="1"' : '')
        + ' data-tag="' + esc(t.name) + '">' + esc(t.name) + '</button>';
    }).join('');
  }

  function closeSheet() {
    var el = $('sheet');
    el.classList.remove('on');
    var sel = state.selected;
    setTimeout(function () {
      el.hidden = true;
      state.selected = null;
      if (sel && state.segs[sel] && state.segs[sel].pid) renderPara(state.segs[sel].pid);
      if (state.view === 'marks') renderMarks();
    }, 200);
  }

  function saveTags() {
    var id = state.selected;
    if (!id) { closeSheet(); return; }
    var tags = Object.keys(state.sheetTags).filter(function (k) { return state.sheetTags[k]; });
    var note = ($('noteInput').value || '').trim();
    $('btnTagSave').disabled = true;
    postJSON('/api/mark', { id: id, tags: tags, ctags: [], note: note }).then(function (r) {
      $('btnTagSave').disabled = false;
      if (!r.ok || (r.data && r.data.ok === false)) {
        toast('标记保存失败：' + ((r.data && r.data.msg) || ('HTTP ' + r.status)), 3000);
        return;
      }
      var s = state.segs[id];
      if (s) {
        s.tags = tags;
        s.note = note;
        if (s.pid) renderPara(s.pid);
      }
      closeSheet();
      toast(tags.length ? '已标记：' + tags.join('、') : (note ? '已保存备注' : '已清除标记'));
    }).catch(function (e) {
      $('btnTagSave').disabled = false;
      toast('标记保存失败：' + (e && e.message), 3000);
    });
  }

  /* ------------------------------------------------------------ 标记页 */
  function markList() {
    var out = [];
    Object.keys(state.segs).forEach(function (k) {
      var s = state.segs[k];
      var n = (s.tags || []).length + (s.ctags || []).length + (s.note ? 1 : 0);
      if (n > 0) out.push(s);
    });
    out.sort(function (a, b) {
      var pa = state.paras[a.pid], pb = state.paras[b.pid];
      var ia = pa ? (pa.idx || 0) : 0, ib = pb ? (pb.idx || 0) : 0;
      if (ia !== ib) return ia - ib;
      return (a.t_start || 0) - (b.t_start || 0);
    });
    return out;
  }

  function markHasNote(s) { return !!(s.note || (s.ctags || []).length); }

  function markHit(s, f) {
    if (!f) return true;
    if (f === '备注') return markHasNote(s);
    return (s.tags || []).indexOf(f) >= 0;
  }

  function markCounts(all) {
    var counts = {};
    TAG_DEFS.forEach(function (t) { counts[t.name] = 0; });
    all.forEach(function (s) {
      (s.tags || []).forEach(function (t) { counts[t] = (counts[t] || 0) + 1; });
      if (markHasNote(s)) counts['备注'] = (counts['备注'] || 0) + 1;
    });
    return counts;
  }

  function renderMarks() {
    var all = markList();
    var counts = markCounts(all);
    var filter = state.markFilter;
    var rows = all.filter(function (s) { return markHit(s, filter); });

    var fhtml = [{ k: '', t: '全部 ' + all.length }, { k: '备注', t: '备注 ' + (counts['备注'] || 0) }];
    fhtml = [fhtml[0]].concat(TAG_DEFS.map(function (t) {
      return { k: t.name, t: t.name + ' ' + (counts[t.name] || 0) };
    })).map(function (o) {
      return '<button type="button" class="' + (TAG_CLS[o.k] || '') + ((filter || '') === o.k ? ' on' : '')
        + '" data-mf="' + esc(o.k) + '">' + esc(o.t) + '</button>';
    }).join('');
    $('markFilter').innerHTML = fhtml;

    $('marksStat').textContent = rows.length + ' / ' + all.length + ' 条';

    if (!rows.length) {
      $('marksList').innerHTML = '<div class="card"><div class="empty">'
        + (all.length ? '当前筛选下没有标记。换个类型看看。'
          : '还没有标记。在「听记」页点任意句子，选亮点 / 硬伤 / 对策缺失 / 口头禅，或写备注。')
        + '</div></div>';
      updateScriptStat();
      return;
    }
    $('marksList').innerHTML = rows.map(function (s) {
      var p = state.paras[s.pid];
      var head = p ? ('第 ' + (p.idx || '') + ' 段' + (p.label ? ' · ' + p.label : '')) : '标记';
      var tg = segTagsHtml(s).replace('class="sg-tags"', 'class="item-tags"');
      return '<div class="mrow">'
        + '<button type="button" class="item" data-mid="' + esc(s.id) + '">'
        + '<span class="item-ico">标</span>'
        + '<span class="item-main"><span class="item-t">' + esc(s.text || '') + '</span>'
        + '<span class="item-tags-wrap">' + tg + '</span>'
        + '<span class="item-s" style="color:var(--dim)">' + esc(head) + ' · ' + fmtClock(s.t_start || 0) + '</span>'
        + '</span></button>'
        + '<span class="mrow-ops">'
        + '<button type="button" class="mini" data-edit="' + esc(s.id) + '">改</button>'
        + '<button type="button" class="mini del" data-del="' + esc(s.id) + '">删</button>'
        + '</span></div>';
    }).join('');
    updateScriptStat();
  }

  function deleteMark(id) {
    var s = state.segs[id];
    if (!s) return;
    postJSON('/api/mark', { id: id, tags: [], ctags: [], note: '' }).then(function (r) {
      var d = r.data || {};
      if (!r.ok || d.ok === false) {
        toast('删除失败：' + (d.msg || ('HTTP ' + r.status)), 3200);
        return;
      }
      s.tags = []; s.ctags = []; s.note = '';
      if (s.pid) renderPara(s.pid);
      renderMarks();
      toast('已删除这条标记', 2200);
    }).catch(function (e) {
      toast('删除失败：' + (e && e.message), 3200);
    });
  }

  /* ------------------------------------------------------------ 点评 */
  var REV_SECS = [
    { key: 'time', name: '一、时间', re: /^#{0,6}\s*(?:一|1)[、.．)）]?\s*时间/ },
    { key: 'good', name: '二、优点', re: /^#{0,6}\s*(?:二|2)[、.．)）]?\s*优点/ },
    { key: 'bad', name: '三、问题', re: /^#{0,6}\s*(?:三|3)[、.．)）]?\s*(?:问题|不足|缺点|风险)/ }
  ];

  function splitReview(text) {
    var lines = String(text || '').split(/\r?\n/);
    var blocks = [];
    var cur = { name: '', lines: [] };
    lines.forEach(function (line) {
      var t = line.trim();
      var hit = null;
      for (var i = 0; i < REV_SECS.length; i++) {
        if (REV_SECS[i].re.test(t)) { hit = REV_SECS[i]; break; }
      }
      if (hit) {
        blocks.push(cur);
        cur = { name: hit.name, lines: [] };
        return;
      }
      cur.lines.push(line);
    });
    blocks.push(cur);
    return blocks.filter(function (b) { return b.name || b.lines.join('').trim(); });
  }

  function reviewHtml(text) {
    var blocks = splitReview(text);
    var named = blocks.filter(function (b) { return !!b.name; });
    if (named.length < 2) {
      return { html: '<div class="rev-sec"><div class="rev-sec-b">' + mdLite(text) + '</div></div>', nav: [] };
    }
    var nav = [];
    var html = blocks.map(function (b, i) {
      if (b.name) nav.push({ i: i, name: b.name });
      return '<div class="rev-sec" data-rsecidx="' + i + '">'
        + (b.name ? '<div class="rev-sec-hd">' + esc(b.name) + '</div>' : '')
        + '<div class="rev-sec-b">' + mdLite(b.lines.join('\n')) + '</div></div>';
    }).join('');
    return { html: html, nav: nav };
  }

  function setPanel(title, mode, html) {
    $('panelTitle').textContent = title || '整场点评';
    $('panelMode').textContent = mode || '';
    $('panelBody').innerHTML = html || '';
    $('panelEmpty').hidden = !!html;
    $('reviewTitle').textContent = title || '整场点评';
    $('reviewMode').textContent = mode || '';
    $('reviewBody').innerHTML = html || '';
    $('reviewEmpty').hidden = !!html;
  }

  function setReview(text, mode, errMsg) {
    var r = reviewHtml(text || '');
    var head = errMsg ? '<div class="para-note err">' + esc(errMsg) + '</div>' : '';
    var body = head + r.html;
    $('reviewTitle').textContent = '整场点评';
    $('reviewMode').textContent = mode || '';
    $('reviewBody').innerHTML = body;
    $('reviewEmpty').hidden = !!(text || errMsg);
    $('reviewNav').hidden = !r.nav.length;
    $('reviewNav').innerHTML = r.nav.map(function (x) {
      return '<button type="button" data-rsec="' + x.i + '">' + esc(x.name.replace(/^[一二三]、/, '')) + '</button>';
    }).join('');
    $$('#reviewNav button').forEach(function (b, i) { b.classList.toggle('on', i === 0); });
    setPanel('整场点评', mode, body || '<p class="dim">点评为空</p>');
  }

  function reviewProg(on, label) {
    var box = $('reviewProg');
    if (!box) return;
    if (!on) { box.hidden = true; return; }
    state.progT0 = nowMs();
    state.progLabel = label || '正在生成整场点评';
    box.hidden = false;
    tickProg();
  }

  function tickProg() {
    var box = $('reviewProg');
    if (!box || box.hidden) return;
    var sec = Math.round((nowMs() - (state.progT0 || nowMs())) / 1000);
    $('reviewProgTxt').textContent = (state.progLabel || '正在生成') +
      ' · 已用 ' + sec + ' 秒' + (sec > 8 ? '（AI 较慢时会自动回退本地规则）' : '');
  }

  function doReview() {
    if (state.reviewBusy) { toast('点评正在生成，请稍候'); return; }
    if (!countSegs()) {
      toast('还没有识别到内容，先说几句话再生成点评', 3200);
      return;
    }
    state.reviewBusy = true;
    $('btnReview').disabled = true;
    $('btnReview2').disabled = true;
    if (state.view !== 'review') setView('review');
    $('reviewStat').textContent = '生成中…';
    setReview('', '生成中…', '');
    reviewProg(true, '正在按整场对话生成点评');
    postJSON('/api/review', {}).then(function (r) {
      state.reviewBusy = false;
      reviewProg(false);
      $('btnReview').disabled = false;
      $('btnReview2').disabled = false;
      var d = r.data || {};
      if (!r.ok || d.ok === false) {
        var msg = d.msg || d.error || ('HTTP ' + r.status);
        $('reviewStat').textContent = '失败';
        setReview('', '失败', '生成失败：' + msg + '。请确认已有逐字稿后重试。');
        toast('点评生成失败：' + msg, 3600);
        return;
      }
      var mode = d.mode === 'ai' ? 'AI 点评' : '本地规则点评';
      state.reviewText = d.review || '';
      var warn = '';
      if (d.error) {
        var em = String(d.error);
        if (em.length > 140) em = em.slice(0, 140) + '…';
        warn = 'AI 未成功，已回退本地规则点评：' + em;
      }
      setReview(state.reviewText, mode, warn);
      $('reviewStat').textContent = mode + ' · ' + state.reviewText.length + ' 字';
      toast('点评已生成（' + mode + '）', 2600);
    }).catch(function (e) {
      state.reviewBusy = false;
      reviewProg(false);
      $('btnReview').disabled = false;
      $('btnReview2').disabled = false;
      $('reviewStat').textContent = '失败';
      setReview('', '失败', '生成失败（网络或服务异常）：' + String(e && e.message || e));
      toast('点评生成失败，请检查网络后重试', 3200);
    });
  }

  /* ------------------------------------------------------------ 段落点评 */
  function paraIds() {
    return paraKeyOrder().filter(function (pid) {
      var p = state.paras[pid];
      return p && p.segs.length;
    });
  }

  function pvFail(pid, msg) {
    delete state.pvBusy[pid];
    state.pvErr[pid] = String(msg || '未知错误');
    renderPara(pid);
    renderParaReviewList();
    var p = state.paras[pid];
    toast('第 ' + ((p && p.idx) || '') + ' 段点评失败：' + state.pvErr[pid], 3600);
  }

  function pvDone(pid, note, mode, msg) {
    delete state.pvBusy[pid];
    state.pvErr[pid] = '';
    var p = state.paras[pid];
    if (p) {
      if (note) p.ai_note = note;
      if (mode) p.review_mode = mode;
      renderPara(pid);
    }
    renderParaReviewList();
    if (msg) toast(msg, 2600);
  }

  function waitParaNote(pid, n) {
    var b = state.pvBusy[pid];
    if (!b) return;
    if (nowMs() - b.t0 > 150000) {
      pvFail(pid, '超时（超过 150 秒仍无结果），请稍后重试');
      return;
    }
    setTimeout(function () {
      if (!state.pvBusy[pid]) return;
      postJSON('/api/paragraph/review_state', { pid: pid }).then(function (r) {
        var d = r.data || {};
        if (!r.ok || d.ok === false) { pvFail(pid, (d && d.msg) || ('HTTP ' + r.status)); return; }
        if (d.mode === 'dup' || d.mode === 'merge') {
          pvDone(pid, d.ai_note, d.mode, d.note || '本段与上一段同环节，已跳过 AI 点评');
          return;
        }
        if (d.ai_note && d.sig_same) {
          pvDone(pid, d.ai_note, d.review_mode || 'ai',
            '第 ' + (d.idx || '') + ' 段点评已生成');
          return;
        }
        waitParaNote(pid, n + 1);
      }).catch(function () { waitParaNote(pid, n + 1); });
    }, n === 0 ? 700 : 1300);
  }

  function enqueuePara(pid) {
    var p = state.paras[pid];
    if (!p || state.pvBusy[pid]) return;
    state.pvErr[pid] = '';
    state.pvBusy[pid] = { t0: nowMs() };
    renderPara(pid);
    renderParaReviewList();
    postJSON('/api/paragraph/review_run', { pid: pid, enqueue: true }).then(function (r) {
      var d = r.data || {};
      if (!r.ok || d.ok === false) { pvFail(pid, (d && d.msg) || ('HTTP ' + r.status)); return; }
      waitParaNote(pid, 0);
    }).catch(function (e) { pvFail(pid, e && e.message); });
  }

  function paraReview(pid, force) {
    var p = state.paras[pid];
    if (!p) return;
    if (!p.segs.length) { toast('这一段还没有内容'); return; }
    if (state.pvBusy[pid]) { toast('第 ' + (p.idx || '') + ' 段正在点评，请稍候'); return; }
    postJSON('/api/paragraph/review_state', { pid: pid }).then(function (r) {
      var d = r.data || {};
      if (!r.ok || d.ok === false) {
        toast('读取段落状态失败：' + ((d && d.msg) || ('HTTP ' + r.status)), 3200);
        return;
      }
      if (d.mode === 'dup' || d.mode === 'merge') {
        p.ai_note = d.ai_note || p.ai_note || '';
        p.review_mode = d.review_mode || d.mode;
        renderPara(pid);
        renderParaReviewList();
        toast(d.note || '本段与上一段同环节，不重复点评', 3600);
        return;
      }
      if (d.ai_note && d.sig_same && !force) {
        p.ai_note = d.ai_note;
        p.review_mode = d.review_mode || p.review_mode;
        state.pvErr[pid] = '';
        renderPara(pid);
        renderParaReviewList();
        toast('第 ' + (d.idx || p.idx) + ' 段已有点评（文本未变），已直接展示', 3000);
        return;
      }
      enqueuePara(pid);
    }).catch(function (e) {
      toast('读取段落状态失败：' + (e && e.message), 3200);
    });
  }

  function reviewMissing() {
    var ids = paraIds().filter(function (pid) {
      var p = state.paras[pid];
      return !p.ai_note && !state.pvBusy[pid];
    });
    if (!ids.length) { toast('没有待点评的段落（都已有结果）'); return; }
    var cap = Math.min(ids.length, 12);
    for (var i = 0; i < cap; i++) {
      (function (pid, k) { setTimeout(function () { paraReview(pid, false); }, k * 320); })(ids[i], i);
    }
    toast('已提交 ' + cap + ' 段点评' + (ids.length > cap ? ('，其余 ' + (ids.length - cap) + ' 段请稍后再点') : ''), 3600);
  }

  function renderParaReviewList() {
    var box = $('paraRevList');
    if (!box) return;
    var ids = paraIds();
    var done = 0, busy = 0;
    ids.forEach(function (pid) {
      var st = paraReviewState(state.paras[pid]);
      if (st === 'done' || st === 'skip') done++;
      if (st === 'busy') busy++;
    });
    $('paraRevStat').textContent = ids.length + ' 段 · 已点评 ' + done + (busy ? (' · 进行中 ' + busy) : '');
    if (!ids.length) {
      box.innerHTML = '<div class="card"><div class="empty">还没有段落。先在「听记」页开始一场，收段后这里就能逐段点评。</div></div>';
      return;
    }
    box.innerHTML = ids.map(function (pid) {
      var p = state.paras[pid];
      var st = paraReviewState(p);
      var raw = p.text || p.segs.map(function (sid) {
        var s2 = state.segs[sid];
        return s2 ? (s2.text || '') : '';
      }).join('');
      var text = String(raw).replace(/\s+/g, ' ').trim();
      var quote = text.length > 70 ? text.slice(0, 70) + '…' : text;
      return '<div class="card" data-pcard="' + esc(pid) + '">'
        + '<div class="para-hd"><span class="para-idx">第 ' + esc(p.idx || '') + ' 段</span>'
        + (p.label ? '<span class="para-tag">' + esc(p.label) + '</span>' : '')
        + '<span class="para-meta">' + esc(paraMetaText(p)) + '</span>'
        + paraActHtml(p, st) + '</div>'
        + '<div class="para-quote">' + esc(quote || '（无文本）') + '</div>'
        + paraNoteHtml(p, st) + '</div>';
    }).join('');
  }

  function refreshBusyCards() {
    var busy = Object.keys(state.pvBusy);
    if (!busy.length) return;
    busy.forEach(function (pid) {
      var p = state.paras[pid];
      if (!p) { delete state.pvBusy[pid]; return; }
      var card = document.querySelector('#paraRevList [data-pcard="' + pid + '"] .para-note.wait');
      if (card) {
        var b = state.pvBusy[pid];
        var sec = b && b.t0 ? Math.round((nowMs() - b.t0) / 1000) : 0;
        card.textContent = '正在生成第 ' + (p.idx || '') + ' 段点评… ' + sec + ' 秒';
      }
      var inline = document.querySelector('#script .para[data-pid="' + pid + '"] .para-act.busy');
      if (inline) inline.textContent = '点评中… ' + (b && b.t0 ? Math.round((nowMs() - b.t0) / 1000) : 0) + 's';
    });
  }


  /* ------------------------------------------------------------ 精简稿 */
  var PS_LS = 'ir.mobile.plainstage';

  function txtChars(t) { return String(t == null ? '' : t).replace(/\s+/g, '').length; }

  function plainStageOn() {
    if (state.plainStage == null) {
      var v = '';
      try { v = localStorage.getItem(PS_LS) || ''; } catch (e) {}
      state.plainStage = (v !== '0');
    }
    return !!state.plainStage;
  }

  function setPlainStage(on) {
    state.plainStage = !!on;
    try { localStorage.setItem(PS_LS, on ? '1' : '0'); } catch (e) {}
    paintPlain();
    toast(on ? '已按五环节结构标注显示' : '已隐藏结构标签，显示连续全文', 2000);
  }

  /* 后端带结构标签时输出「【环节名】正文」，这里解析成环节分组 */
  function parsePlainTagged(txt) {
    var groups = [], cur = null;
    String(txt || '').split(/\r?\n/).forEach(function (line) {
      var m = /^\s*【([^】]{1,20})】\s*(.*)$/.exec(line);
      if (m) {
        cur = { label: m[1].trim(), text: String(m[2] || '').trim() };
        groups.push(cur);
        return;
      }
      if (!line.trim()) return;
      if (!cur) { cur = { label: '全文', text: '' }; groups.push(cur); }
      cur.text += (cur.text ? '\n' : '') + line.trim();
    });
    groups = groups.filter(function (g) { return g.text; });
    return {
      groups: groups,
      raw: groups.map(function (g) { return g.text; }).join('\n')
    };
  }

  function loadPlain() {
    $('plainBody').innerHTML = '<div class="dim">正在读取…</div>';
    api('/api/plain/text?stage=1').then(function (r) {
      var d = r.data || {};
      if (!d.ok) {
        $('plainBody').innerHTML = '<div class="empty">读取失败：' + esc(d.msg || ('HTTP ' + r.status)) + '</div>';
        return;
      }
      state.plainData = { text: String(d.text || ''), chars: d.chars || 0, count: d.count || 0 };
      paintPlain();
    }).catch(function (e) {
      $('plainBody').innerHTML = '<div class="empty">读取失败：' + esc(e && e.message) + '</div>';
    });
  }

  function paintPlain() {
    var box = $('plainBody');
    if (!box) return;
    var on = plainStageOn();
    var btn = $('btnPlainStage');
    if (btn) {
      btn.textContent = on ? '隐藏结构标签' : '显示结构标签';
      btn.classList.toggle('accent', on);
    }
    var d = state.plainData;
    if (!d) return;
    var p = parsePlainTagged(d.text);
    var groups = p.groups;
    state.plain = p.raw;
    var chars = d.chars || txtChars(p.raw);
    $('plainStat').textContent = chars + ' 字 · 第 ' + (d.count || 0) + ' 版'
      + (groups.length ? (' · ' + groups.length + ' 环节') : '');
    if (!groups.length) {
      box.innerHTML = '<div class="empty">还没有精简稿。录音过程中段落收段后会自动生成。</div>';
      return;
    }
    if (!on) {
      box.innerHTML = '<div class="mono pg-raw">' + esc(p.raw) + '</div>';
      return;
    }
    box.innerHTML = groups.map(function (g) {
      return '<div class="pg">'
        + '<div class="pg-hd"><span class="pg-t">' + esc(g.label) + '</span>'
        + '<span class="pg-n">' + txtChars(g.text) + ' 字</span>'
        + '<button type="button" class="mini" data-pgcopy="' + esc(g.label) + '">复制本节</button></div>'
        + '<div class="pg-tx">' + esc(g.text) + '</div></div>';
    }).join('');
  }

  function plainCopyAll() {
    var p = parsePlainTagged((state.plainData || {}).text || '');
    var txt = plainStageOn()
      ? p.groups.map(function (g) { return '【' + g.label + '】' + g.text; }).join('\n')
      : p.raw;
    if (!txt) { toast('还没有精简稿可复制'); return; }
    copyText(txt);
  }

  function plainCopyGroup(label) {
    var p = parsePlainTagged((state.plainData || {}).text || '');
    var hit = null;
    p.groups.forEach(function (g) { if (g.label === label) hit = g; });
    if (!hit) return;
    copyText(hit.text);
  }

  /* ------------------------------------------ 精简要点（按答题结构分组） */
  var PTS_LS = 'ir.mobile.ptsai';
  var PTS_ROLE_TXT = { open: '入题', opinion: '观点', analyze: '分析', action: '对策', close: '收尾' };
  var PTS_BY_TXT = { tag: '标签', cue: '语义', pos: '位置', ref: '参考', ai: 'AI' };

  function ptsState() {
    if (!state.pts) {
      state.pts = {
        src: null, info: null, res: null, busy: false, ai: null,
        err: '', stale: false, via: '', lastCopy: ''
      };
    }
    return state.pts;
  }

  function ptsAiOn() {
    var s = ptsState();
    if (s.ai == null) {
      var v = '';
      try { v = localStorage.getItem(PTS_LS) || ''; } catch (e) {}
      s.ai = (v === '1');
    }
    return !!s.ai;
  }

  /* 判定来源：AI 逐条判定 / 本地关键词匹配（含回退说明） */
  function ptsSrcText(res) {
    if (!res) return '';
    var by = res.judge_source || ((res.align || {}).source) || 'local';
    var t = res.judge_source_note || (by === 'ai' ? 'AI 判定' : '本地判定');
    if (res.judge_fallback) t += '（AI 未生效，已回退）';
    return t;
  }

  function ptsByText(by) { return PTS_BY_TXT[by] || ''; }

  /* 取当前精简稿与题型上下文：优先 POST /api/points/source，旧版后端回退 GET */
  function ptsSource() {
    var s = ptsState();
    var viaGet = function () {
      s.via = 'get';
      return api('/api/plain/text?stage=1').then(function (r) {
        var d = r.data || {};
        if (!r.ok || !d.ok) throw new Error(d.msg || ('HTTP ' + r.status));
        return {
          ok: true, text: String(d.text || ''), chars: d.chars || 0, count: d.count || 0,
          qtype: state.qtype || '', stages: [], ref: null, question: '', has_key: null, fallback: true
        };
      });
    };
    var viaPost = function () {
      return postJSON('/api/points/source', {}).then(function (r) {
        var d = r.data || {};
        if (r.ok && d.ok) { s.via = 'post'; return d; }
        if (r.status === 404 || r.status === 405) return viaGet();
        throw new Error(d.msg || d.error || ('HTTP ' + r.status));
      });
    };
    return s.via === 'get' ? viaGet() : viaPost();
  }

  function ptsRunLabel() {
    var s = ptsState();
    var btn = $('btnPtsRun');
    if (!btn || s.busy) return;
    btn.textContent = s.res ? '重新生成' : '生成要点';
  }

  function ptsSyncAi() {
    var cb = $('ptsAi');
    if (cb) cb.checked = ptsAiOn();
  }

  function ptsSetAi(on) {
    var s = ptsState();
    if (on && s.src && s.src.has_key === false) {
      s.ai = false;
      try { localStorage.setItem(PTS_LS, '0'); } catch (e) {}
      ptsSyncAi();
      toast('当前未配置模型 Key，只能用本地判定', 3000);
      return;
    }
    s.ai = !!on;
    try { localStorage.setItem(PTS_LS, on ? '1' : '0'); } catch (e) {}
    toast(on ? '已开启 AI 判定，点「重新生成」生效' : '已切回本地判定（关键词匹配）', 2400);
  }

  /* 精简稿变动后把已有要点标记为过期（不清空，只提示重生成） */
  function ptsMarkStale() {
    var s = state.pts;
    if (!s || !s.res || s.stale) return;
    s.stale = true;
    if (state.view === 'plain') paintPoints();
  }

  function ptsLoad() {
    var s = ptsState();
    if (s.busy) return;
    s.busy = true;
    s.err = '';
    s.stale = false;
    var btn = $('btnPtsRun');
    if (btn) { btn.disabled = true; btn.textContent = '生成中…'; }
    var stat = $('ptsStat');
    if (stat) stat.textContent = '正在取当前精简稿…';
    var box = $('ptsBody');
    if (box) box.innerHTML = '<div class="dim">正在取当前精简稿…</div>';
    if ($('ptsLegend')) $('ptsLegend').hidden = true;
    if ($('ptsNote')) $('ptsNote').textContent = '';

    ptsSource().then(function (src) {
      s.src = src;
      var text = String(src.text || '').trim();
      s.info = {
        text: text,
        qtype: src.qtype || state.qtype || '',
        stages: src.stages || [],
        ref: src.ref,
        stamp: src.stamp || '',
        chars: src.chars || 0,
        count: src.count || 0,
        has_key: src.has_key
      };
      ptsSyncAi();
      if (!text) {
        throw { soft: true, msg: '还没有精简稿，先录音收到段落，或换个有内容的学生' };
      }
      if (box) box.innerHTML = '<div class="dim">正在按答题结构抽取要点…</div>';
      if (stat) {
        stat.textContent = '正在分析 ' + txtChars(text) + ' 字'
          + (s.info.stages.length ? ('／' + s.info.stages.length + ' 环节') : '');
      }
      var body = {
        text: text,
        qtype: s.info.qtype,
        stages: s.info.stages,
        max_len: 20,
        ai: ptsAiOn(),
        with_status: true
      };
      if (typeof s.info.ref === 'string' && s.info.ref) body.ref = s.info.ref;
      return postJSON('/api/points/analyze', body).then(function (r) {
        var d = r.data || {};
        if (!r.ok || d.ok === false) throw new Error(d.msg || d.error || ('HTTP ' + r.status));
        return d;
      });
    }).then(function (res) {
      s.res = res;
      s.busy = false;
      if (btn) btn.disabled = false;
      ptsRunLabel();
      paintPoints();
      var n = (res.stats || {}).points || 0;
      toast(n ? ('已生成 ' + n + ' 条要点 · ' + ptsSrcText(res))
        : '没抽出要点，先补全作答内容再重新生成', 3000);
    }).catch(function (e) {
      s.busy = false;
      s.res = null;
      s.err = (e && (e.msg || e.message)) || '取精简稿失败';
      if (btn) btn.disabled = false;
      ptsRunLabel();
      paintPoints();
      toast(s.err, 3000);
    });
  }

  function ptsStatText(res) {
    var stt = (res && res.stats) || {};
    var n = stt.points || 0;
    if (!n) return '没有要点 · 判定来源：' + ptsSrcText(res);
    return '共 ' + n + ' 条要点 · 答到 ' + (stt.hit || 0) + '｜偏题 ' + (stt.off || 0)
      + '｜遗漏 ' + (stt.miss || 0) + ' · ' + ptsSrcText(res);
  }

  function paintPoints() {
    var s = ptsState();
    var box = $('ptsBody');
    var stat = $('ptsStat');
    var note = $('ptsNote');
    var btn = $('btnPtsRun');
    var res = s.res;
    if (btn && !s.busy) btn.textContent = res ? '重新生成' : '生成要点';
    if (!box) return;
    if (!res) {
      if ($('ptsLegend')) $('ptsLegend').hidden = true;
      if (stat) stat.textContent = s.err ? '生成失败' : '还没有生成要点';
      if (note) note.textContent = '';
      box.innerHTML = s.err
        ? ('<div class="empty">生成失败：' + esc(s.err) + '<br>可点「重新生成」再试；'
          + '若持续失败，先到上方精简稿点「刷新」确认有内容。</div>')
        : '<div class="empty">点「生成要点」按本题答题结构抽取关键词要点。</div>';
      return;
    }
    var groups = res.groups || [];
    var stt = res.stats || {};
    if (stat) {
      stat.textContent = ptsStatText(res) + (s.stale ? ' · 精简稿已更新，建议重新生成' : '');
      stat.title = '命中 ' + (stt.hit || 0) + '｜偏题 ' + (stt.off || 0) + '｜遗漏 ' + (stt.miss || 0)
        + '｜去重 ' + (stt.dup_removed || 0) + '｜分组 ' + (stt.groups || 0)
        + '｜判定来源 ' + ptsSrcText(res);
    }
    var lg = $('ptsLegend');
    if (lg) lg.hidden = !groups.length;
    if (note) {
      var al = res.align || {};
      var bits = [];
      if (res.note) bits.push(res.note);
      if (al && al.ok && al.coverage != null) {
        bits.push('对齐覆盖率 ' + al.coverage + '%（' + (al.ref_hit || 0) + '/' + (al.ref_total || 0) + '）');
      }
      note.textContent = bits.join('｜');
      note.title = bits.join('｜');
    }
    if (!groups.length) {
      box.innerHTML = '<div class="empty">' + esc(res.note || '没抽出要点，先取精简稿或补全作答内容再分析') + '</div>';
      return;
    }
    box.innerHTML = '<div class="pts-grid">' + groups.map(function (g) {
      var items = g.items || [];
      var isMiss = g.miss === true || String(g.stage || '').indexOf('遗漏') >= 0;
      var hit = 0, off = 0, miss = 0;
      items.forEach(function (it) {
        if (it.status === '答到') hit++;
        else if (it.status === '偏题') off++;
        else if (it.status === '遗漏') miss++;
      });
      var cnt = [];
      if (hit) cnt.push('答到 ' + hit);
      if (off) cnt.push('偏题 ' + off);
      if (miss) cnt.push('遗漏 ' + miss);
      var head = '<div class="pts-g-hd">'
        + '<span class="pts-g-t' + (isMiss ? ' miss' : '') + '">' + esc(g.stage || '要点') + '</span>'
        + (PTS_ROLE_TXT[g.role] ? '<span class="pts-g-role">' + esc(PTS_ROLE_TXT[g.role]) + '</span>' : '')
        + '<span class="pts-g-n">' + items.length + ' 条</span></div>'
        + (cnt.length ? '<div class="pts-g-cnt">' + cnt.join('｜') + '</div>' : '');
      var lis = items.map(function (it) {
        var st = it.status || '';
        var cls = st === '答到' ? 'hit' : (st === '偏题' ? 'off' : (st === '遗漏' ? 'miss' : 'none'));
        var by = ptsByText(it.by);
        return '<li class="pts-li" title="归组依据：' + esc(by || '未标注') + '">'
          + '<i class="pts-dot ' + cls + '"></i>'
          + '<span class="pts-t">' + esc(it.text || '') + '</span>'
          + (by ? '<em class="pts-by">' + esc(by) + '</em>' : '')
          + (st ? '<span class="pts-b ' + cls + '">' + esc(st) + '</span>' : '')
          + '</li>';
      }).join('');
      return '<div class="pts-g' + (isMiss ? ' missg' : '') + '">' + head
        + '<ul class="pts-list">' + lis + '</ul></div>';
    }).join('') + '</div>';
  }

  function ptsCopyAll() {
    var s = ptsState();
    var res = s.res;
    if (!res) { toast('还没有可复制的要点，先点「生成要点」', 2800); return; }
    var txt = String(res.bullet || res.plain || '');
    if (!txt.trim()) {
      txt = (res.groups || []).map(function (g) {
        return '【' + (g.stage || '要点') + '】\n' + (g.items || []).map(function (it) {
          return '- ' + (it.text || '') + (it.status ? '（' + it.status + '）' : '');
        }).join('\n');
      }).join('\n\n');
    }
    if (!txt.trim()) { toast('没有可复制的要点', 2400); return; }
    s.lastCopy = txt;
    var n = txt.split('\n').filter(function (x) { return x.trim(); }).length;
    copyText(txt);
    setTimeout(function () { toast('已复制全部要点（' + n + ' 行）', 2400); }, 40);
  }

  /* ------------------------------------------------------------ 学生 */
  function loadStudents() {
    $('studentsList').innerHTML = '<div class="dim">正在读取学生列表…</div>';
    api('/api/students').then(function (r) {
      var d = r.data || {};
      if (d.ok === false) {
        $('studentsList').innerHTML = '<div class="empty">读取失败：' + esc(d.msg || ('HTTP ' + r.status)) + '</div>';
        return;
      }
      state.students = d;
      renderStudents();
    }).catch(function (e) {
      $('studentsList').innerHTML = '<div class="empty">读取失败：' + esc(e && e.message) + '</div>';
    });
  }

  function stuList() {
    var d = state.students || {};
    return d.students || d.list || [];
  }

  function stuLimitN() {
    return (state.students || {}).limit || 20;
  }

  function stuCur() {
    var d = state.students || {};
    var cur = String(d.cur || d.current || d.active || '');
    var hit = null;
    stuList().forEach(function (s) { if (!hit && String(s.id) === cur) hit = s; });
    return hit;
  }

  function stuBits(s) {
    var bits = [];
    if (s.qtype) bits.push(s.qtype);
    if (s.segments != null) bits.push(s.segments + ' 句');
    if (s.paragraphs != null) bits.push(s.paragraphs + ' 段');
    if (s.duration) bits.push(fmtDur(s.duration));
    if (s.has_review) bits.push('已点评');
    return bits.join(' · ') || '暂无数据';
  }

  /* 听记页内的学生切换面板：与「学生」页共用同一份 state.students */
  function renderCtxStuPick() {
    var box = $('ctxStuPick');
    if (!box) return;
    var list = stuList();
    var cur = stuCur();
    var curId = cur ? String(cur.id) : '';
    if (!list.length) {
      box.innerHTML = '<div class="stu-pick-empty">还没有学生，先到「<b>学生</b>」页添加一位。</div>'
        + '<div class="btn-row" style="margin-top:8px">'
        + '<button type="button" class="mini" data-csgo="1">去学生页添加</button></div>';
      return;
    }
    box.innerHTML = '<div class="stu-chips">' + list.map(function (s) {
      var sid = String(s.id == null ? '' : s.id);
      var on = !!curId && sid === curId;
      return '<button type="button" class="stu-chip' + (on ? ' on' : '') + '" data-csid="' + esc(sid) + '"'
        + (on ? ' disabled aria-current="true"' : '') + '>'
        + '<span class="stu-chip-n">' + esc(s.name || '未命名') + '</span>'
        + (on ? '<i class="stu-chip-tag">当前</i>' : '')
        + '</button>';
    }).join('') + '</div>'
      + '<div class="stu-pick-hint">' + (cur
        ? '当前归属：' + esc(cur.name || '未命名') + ' · ' + esc(stuBits(cur))
        : '还没有选学生')
      + '。切换后新录的句子归新学生，已录内容仍留在原学生名下。</div>';
  }

  function ctxStuPickOpen() {
    var box = $('ctxStuPick');
    return !!(box && !box.hidden);
  }

  function setCtxStuPick(open) {
    var box = $('ctxStuPick'), btn = $('btnCtxStu');
    if (!box) return;
    box.hidden = !open;
    if (btn) btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (!open) return;
    if (!state.students) {
      box.innerHTML = '<div class="dim">正在读取学生…</div>';
      loadStudents();
      return;
    }
    renderCtxStuPick();
  }

  function toggleCtxStuPick() {
    setCtxStuPick(!ctxStuPickOpen());
  }

  function closeCtxStuPick() {
    setCtxStuPick(false);
  }

  function renderStudents() {
    var list = stuList();
    var limit = stuLimitN();
    var cur = stuCur();
    var curId = cur ? String(cur.id) : '';
    $('studentsStat').textContent = list.length + ' / ' + limit + ' 人';
    $('stuLimit').textContent = '还可添加 ' + Math.max(0, limit - list.length) + ' 人';
    $('stuOwner').innerHTML = cur
      ? ('<div class="own-box"><span class="own-dot live"></span>'
        + '<span class="own-t">' + esc(cur.name || '未命名') + '</span>'
        + '<span class="own-s">' + esc(stuBits(cur)) + '</span>'
        + '<span class="own-tag">当前录音归属</span></div>')
      : '<div class="dim">还没有学生，先在上方添加一位。</div>';
    $('stuOwnerHint').textContent = cur
      ? ('录音与逐字稿都写入这位学生名下，切换后新录的句子归新学生。')
      : '还没有学生，先在上方添加一位。';

    if (!list.length) {
      $('studentsList').innerHTML = '<div class="empty">还没有学生，先在上方输入姓名添加一位。</div>';
      renderCtxStuPick();
      return;
    }

    $('studentsList').innerHTML = '<div class="list-grid">' + list.map(function (s) {
      var sid = String(s.id == null ? '' : s.id);
      var on = sid === curId;
      var editing = state.stuEdit && state.stuEdit === sid;
      var head = editing
        ? '<input class="inp stu-in" data-sin="' + esc(sid) + '" value="' + esc(s.name || '')
          + '" maxlength="16" placeholder="学生姓名">'
        : '<span class="item-t">' + esc(s.name || '未命名') + '</span>';
      var ops = editing
        ? '<button type="button" class="mini accent" data-sact="rsave" data-sid="' + esc(sid) + '">保存</button>'
          + '<button type="button" class="mini" data-sact="rcancel">取消</button>'
        : '<button type="button" class="mini" data-sact="rename" data-sid="' + esc(sid) + '">改名</button>'
          + '<button type="button" class="mini del" data-sact="del" data-sid="' + esc(sid) + '"'
          + (list.length <= 1 ? ' disabled' : '') + '>删除</button>';
      return '<div class="item' + (on ? ' on' : '') + '" data-sid="' + esc(sid) + '">'
        + '<span class="item-ico">' + (on ? '录' : '生') + '</span>'
        + '<span class="item-main">' + head
        + '<span class="item-s">' + esc(stuBits(s)) + '</span>'
        + (on ? '<span class="own-tag">当前录音归属</span>' : '')
        + '</span>'
        + '<span class="item-op">' + ops + '</span></div>';
    }).join('') + '</div>';
    renderCtxStuPick();
  }

  function switchStudent(id) {
    if (!id) return;
    var cur = stuCur();
    if (cur && String(cur.id) === String(id)) { toast('已经是当前学生'); return; }
    postJSON('/api/students/switch', { id: id }).then(function (r) {
      var d = r.data || {};
      if (!r.ok || d.ok === false) {
        toast('切换失败：' + (d.msg || ('HTTP ' + r.status)), 3000);
        return;
      }
      state.stuEdit = '';
      // 切换接口已回传新归属学生名，先就地更新上下文，保证听记页立刻反映新学生
      var nm = (d.student && d.student.name) || d.cur || '';
      if (nm) state.stuName = nm;
      toast('已切换到「' + (nm || '该学生') + '」');
      closeCtxStuPick();
      renderCtx();
      updateStats();
      loadStudents();
      var pClip = Promise.resolve(loadClip());
      var pState = Promise.resolve(loadState());
      // 两份数据都回来后对齐一次（题目 / 段落 / 统计 / 学生名），避免旧数据覆盖刚切换的归属
      Promise.all([pClip, pState]).then(function () {
        if (nm && !state.stuName) state.stuName = nm;
        renderCtx();
        updateStats();
      }).catch(function () {});
      loadPlain();
    }).catch(function (e) { toast('切换失败：' + (e && e.message), 3000); });
  }

  function addStudent() {
    var el = $('stuName');
    var name = (el.value || '').trim();
    if (!name) { el.focus(); toast('请先填学生姓名'); return; }
    if (stuList().length >= stuLimitN()) { toast('已达上限 ' + stuLimitN() + ' 人，先删掉不用的再添加', 3000); return; }
    postJSON('/api/students/add', { name: name }).then(function (r) {
      var d = r.data || {};
      if (!r.ok || d.ok === false) {
        toast('添加失败：' + (d.msg || ('HTTP ' + r.status)), 3000);
        return;
      }
      el.value = '';
      toast('已添加「' + ((d.student && d.student.name) || name) + '」并切换为当前');
      loadStudents();
      loadClip();
      loadState();
      loadPlain();
    }).catch(function (e) { toast('添加失败：' + (e && e.message), 3000); });
  }

  function stuRename(id) {
    var list = stuList();
    var hit = null;
    list.forEach(function (s) { if (String(s.id) === String(id)) hit = s; });
    if (!hit) return;
    state.stuEdit = String(id);
    renderStudents();
    var inp = document.querySelector('#studentsList .stu-in');
    if (inp) { inp.focus(); inp.select(); }
  }

  function saveStuRename(id) {
    var inp = document.querySelector('#studentsList .stu-in[data-sin="' + id + '"]');
    var name = inp ? (inp.value || '').trim() : '';
    if (!name) { toast('姓名不能为空'); return; }
    postJSON('/api/students/rename', { id: id, name: name }).then(function (r) {
      var d = r.data || {};
      if (!r.ok || d.ok === false) {
        toast('改名失败：' + (d.msg || ('HTTP ' + r.status)), 3000);
        return;
      }
      state.stuEdit = '';
      toast('已改名为「' + name + '」');
      loadStudents();
      loadClip();
    }).catch(function (e) { toast('改名失败：' + (e && e.message), 3000); });
  }

  function delStudent(id) {
    var list = stuList();
    var hit = null;
    list.forEach(function (s) { if (String(s.id) === String(id)) hit = s; });
    if (!hit) return;
    if (list.length <= 1) { toast('至少保留一位学生，不能删'); return; }
    var isCur = stuCur() && String(stuCur().id) === String(id);
    var msg = '删除学生「' + (hit.name || '未命名') + '」？\n'
      + '该学生名下 ' + (hit.segments || 0) + ' 句逐字稿、'
      + (hit.has_review ? '已生成的点评' : '未生成的点评') + ' 会一起移除，删除后不可恢复。\n'
      + (isCur ? '这是当前录音归属的学生，删除后自动切换到剩下的第一位。' : '');
    if (!window.confirm(msg)) return;
    postJSON('/api/students/delete', { id: id }).then(function (r) {
      var d = r.data || {};
      if (!r.ok || d.ok === false) {
        toast('删除失败：' + (d.msg || ('HTTP ' + r.status)), 3000);
        return;
      }
      state.stuEdit = '';
      toast('已删除');
      loadStudents();
      loadClip();
      loadState();
      loadPlain();
      if ((state.stuCmp || {}).students) loadStuCompare();
    }).catch(function (e) { toast('删除失败：' + (e && e.message), 3000); });
  }

  /* 多人对比：一次取回全部学生的规模、环节与点评，横向列表 */
  function loadStuCompare() {
    var box = $('stuCompare');
    box.hidden = false;
    box.innerHTML = '<div class="dim">正在汇总各学生数据…</div>';
    api('/api/students/compare').then(function (r) {
      var d = r.data || {};
      if (d.ok === false) {
        box.innerHTML = '<div class="empty">读取失败：' + esc(d.msg || ('HTTP ' + r.status)) + '</div>';
        return;
      }
      state.stuCmp = d;
      renderStuCompare();
    }).catch(function (e) {
      box.innerHTML = '<div class="empty">读取失败：' + esc(e && e.message) + '</div>';
    });
  }

  function renderStuCompare() {
    var box = $('stuCompare');
    var d = state.stuCmp || {};
    var rows = d.students || [];
    if (!rows.length) {
      box.innerHTML = '<div class="empty">还没有可以对比的学生数据。</div>';
      return;
    }
    var curId = String(d.cur || '');
    var head = '<div class="cmp-hd"><span>学生</span><span>时长</span><span>句/段</span>'
      + '<span>字数</span><span>标记</span><span>点评</span></div>';
    var body = rows.map(function (x) {
      var on = String(x.id) === curId;
      var rv = (x.review || '').trim();
      return '<div class="cmp-row' + (on ? ' on' : '') + '">'
        + '<span class="cmp-nm">' + esc(x.name || '未命名') + (on ? '<i class="cmp-dot"></i>' : '') + '</span>'
        + '<span>' + esc(x.duration ? fmtDur(x.duration) : '—') + '</span>'
        + '<span>' + esc((x.segments || 0) + '/' + (x.paragraphs || 0)) + '</span>'
        + '<span>' + esc(String(x.chars || 0)) + '</span>'
        + '<span>' + esc(String(x.marks || 0)) + '</span>'
        + '<span>' + (rv ? (txtChars(rv) + ' 字') : '—') + '</span></div>'
        + '<div class="cmp-b">'
        + ((x.stages || []).length
          ? '<div class="cmp-bars">' + x.stages.map(function (sg) {
            return '<div class="cmp-bar"><span class="cmp-k">' + esc(sg.label || ('环节' + sg.idx)) + '</span>'
              + '<span class="cmp-v">' + txtChars(sg.plain || '') + ' 字</span></div>';
          }).join('') + '</div>'
          : '<div class="dim">该学生还没有精简稿环节。</div>')
        + (rv ? ('<div class="cmp-meta">点评：' + esc(rv.slice(0, 90)) + (rv.length > 90 ? '…' : '') + '</div>') : '')
        + '</div>';
    }).join('');
    box.innerHTML = head + body
      + '<div class="dim" style="margin-top:8px">带圆点的行是当前录音归属的学生；切到该学生后听记与逐字稿都归他。</div>';
  }

  /* ------------------------------------------------------------ 题库 */
  /* 与电脑端一致的六个可比字段（题干 / 题型 / 审题要点 / 答题环节 / 关键要点 / 参考要点） */
  var BQ_FIELDS = [
    { k: 'question', name: '题干' },
    { k: 'qtype', name: '题型' },
    { k: 'exam', name: '审题要点' },
    { k: 'structure', name: '答题环节' },
    { k: 'keys', name: '关键要点' },
    { k: 'ref', name: '参考要点' }
  ];
  var MOPT_TXT = { wait: '排队中', run: '处理中', ok: '已完成', fail: '失败', skip: '未处理' };

  /* 勾选集合 / 优化任务运行时（惰性初始化，避免动 state 字面量） */
  function bqSelSet() {
    if (!state.bankSel) state.bankSel = {};
    return state.bankSel;
  }
  function bqOpt() {
    if (!state.bankOpt) {
      state.bankOpt = {
        jid: '', timer: null, liveTimer: null, open: false, running: false, stopping: false,
        total: 0, done: 0, okN: 0, failN: 0, live: [], before: {}, fail: [], snapFile: ''
      };
    }
    return state.bankOpt;
  }
  function bqOn() { return Object.keys(bqSelSet()).filter(function (k) { return bqSelSet()[k]; }).map(Number); }
  function bqFileOf(b) { return bankIndexOf(b); }
  function bqNameOf(b) {
    var fn = bankIndexOf(b) || '';
    return b.name || fn.replace(/\.json$/i, '');
  }
  function bqSnapOf(it) {
    return {
      question: it.question || '', qtype: it.qtype || '',
      exam: (it.exam || []).slice(), keys: (it.keys || []).slice(),
      structure: JSON.parse(JSON.stringify(it.structure || [])), ref: it.ref || ''
    };
  }
  /* 某字段 → 可读行数组（答题环节按「环节（小标题）：要点；要点」拼一行） */
  function bqLines(o, k) {
    if (!o) return [];
    if (k === 'exam' || k === 'keys') {
      return (o[k] || []).map(function (x) { return String(x); });
    }
    if (k === 'structure') {
      return (o.structure || []).map(function (s) {
        var head = [s.stage || '', s.label ? ('（' + s.label + '）') : ''].join('');
        var pts = (s.points || []).map(function (p) { return String(p); }).filter(Boolean);
        return head + (pts.length ? ('：' + pts.join('；')) : '');
      });
    }
    var v = o[k];
    if (v == null || v === '') return [];
    return String(v).split(/\n+/).filter(function (x) { return x.trim(); });
  }
  /* 精简前后对比：标出新增 / 删除的字里行（按行做差异，足够看清改了什么） */
  function bqDiffRows(before, after) {
    var A = before || [], B = after || [];
    var out = [];
    var n = Math.max(A.length, B.length);
    for (var i = 0; i < n; i++) {
      var a = A[i] == null ? '' : A[i], b = B[i] == null ? '' : B[i];
      out.push({ a: a, b: b, same: a === b });
    }
    return out;
  }
  function bqCellHtml(o, k, cls) {
    var lines = bqLines(o, k);
    var body = lines.length
      ? lines.map(function (x) { return esc(x); }).join('<br>')
      : '<span class="dim">（空）</span>';
    return '<div class="mdf-cell' + (cls || '') + '"><div class="mdf-k">' + esc(k) + '</div>' + body + '</div>';
  }
  /* 左右（手机为上下）并排对比：只列有差异的字段，全同也能看 */
  function bqDiffHtml(before, after) {
    var html = '<div class="mdf-g">';
    BQ_FIELDS.forEach(function (f) {
      var aL = bqLines(before, f.k), bL = bqLines(after, f.k);
      var same = aL.join('\n') === bL.join('\n');
      var rows = bqDiffRows(aL, bL);
      var oldHtml = rows.map(function (r) { return r.a ? esc(r.a) : '<span class="dim">（无）</span>'; }).join('<br>');
      var newHtml = rows.map(function (r) {
        return r.b ? esc(r.b) : '<span class="dim">（无）</span>';
      }).join('<br>');
      if (!rows.length) { oldHtml = '<span class="dim">（空）</span>'; newHtml = '<span class="dim">（空）</span>'; }
      html += '<div class="mdf-cell' + (same ? ' same' : '') + '"><div class="mdf-k">' + esc(f.name)
        + ' · 优化前</div>' + oldHtml + '</div>'
        + '<div class="mdf-cell' + (same ? ' same' : '') + '"><div class="mdf-k">' + esc(f.name)
        + ' · 优化后</div>' + newHtml + '</div>';
    });
    return html + '</div>';
  }

  /* ---------------- 移动端通用面板（详情 / 编辑 / 新建 / 管理 / 实时输出 / 对比） ---------------- */
  function mSheetOpen(title, bodyHtml, ops) {
    $('mSheetTitle').textContent = title || '题库';
    $('mSheetBody').innerHTML = bodyHtml || '';
    var opBox = $('mSheetOps');
    opBox.innerHTML = (ops || []).map(function (o, i) {
      return '<button type="button" class="btn' + (o.primary ? ' primary' : '') + '" data-mop="' + i + '">'
        + esc(o.text) + '</button>';
    }).join('');
    opBox._ops = ops || [];
    $('mSheet').hidden = false;
    requestAnimationFrame(function () { $('mSheet').classList.add('on'); });
  }
  function mSheetClose() {
    var el = $('mSheet');
    if (!el || el.hidden) return;
    el.classList.remove('on');
    try { bqCloseLive(); } catch (e) {}   // 面板关掉就别再拉实时内容，省流量
    setTimeout(function () { el.hidden = true; $('mSheetBody').innerHTML = ''; $('mSheetOps').innerHTML = ''; }, 180);
  }
  function mSheetIsOpen() { return $('mSheet') && !$('mSheet').hidden; }

  function bqItemAt(gi) {
    var b = bankOpenObj();
    var items = (b && b.items) || [];
    var it = items[gi];
    return it ? { item: it, bank: b, gi: gi } : null;
  }

  function loadBank() {
    $('bankList').innerHTML = '<div class="dim">正在读取题库…</div>';
    loadBankCur();
    api('/api/bank').then(function (r) {
      var d = r.data || {};
      if (d.ok === false) {
        $('bankList').innerHTML = '<div class="empty">读取失败：' + esc(d.msg || ('HTTP ' + r.status)) + '</div>';
        return;
      }
      state.bank = d;
      state.bankOpen = null;
      state.bankSearch = null;
      renderBank();
    }).catch(function (e) {
      $('bankList').innerHTML = '<div class="empty">读取失败：' + esc(e && e.message) + '</div>';
    });
  }

  function bankListArr() {
    var d = state.bank || {};
    return d.banks || d.list || [];
  }

  function bankItemCount(b) {
    if (b.count != null) return b.count;
    if (b.total != null) return b.total;
    return (b.items || []).length;
  }

  function bankIndexOf(b) {
    return b.file || b.name || '';
  }

  function loadBankCur() {
    var box = $('bankCur');
    if (!box) return;
    var c = state.clip;
    if (!c || !c.ok) {
      box.textContent = '读取中…';
      api('/api/mobile/clip').then(function (r) {
        var d = r.data || {};
        if (d.ok) { state.clip = d; }
        loadBankCur();
      }).catch(function () {});
      return;
    }
    var q = String(c.question || '').trim();
    var qt = String(c.qtype || '').trim();
    var label = q ? q.slice(0, 40) + (q.length > 40 ? '…' : '') : '（未设题目）';
    box.textContent = label + (qt ? (' · ' + qt) : '');
    $('bankCurHint').textContent = q ? '当前题目' : '当前题目为空';
  }

  function renderBank() {
    var list = bankListArr();
    var total = 0;
    list.forEach(function (b) { total += bankItemCount(b); });
    $('bankStat').textContent = list.length + ' 个题库 · ' + total + ' 题';
    $('bankHit').textContent = '';
    $('btnBankBack').hidden = (state.bankOpen == null && state.bankSearch == null);
    bqOptSync();

    if (state.bankSearch) { renderBankSearch(); return; }
    if (state.bankOpen != null) {
      var ob = bankOpenObj();
      if (!ob) { state.bankOpen = null; }
      else if (!(ob.items || []).length && bankIndexOf(ob)) { loadBankItems(ob); return; }
      renderBankItems();
      return;
    }

    if (!list.length) {
      $('bankList').innerHTML = '<div class="empty">还没有题库。点上方「题库管理」可以新建题库，或在电脑端导入题库。</div>';
      return;
    }
    $('bankList').innerHTML = '<div class="list-grid">' + list.map(function (b) {
      var idx = bankIndexOf(b);
      return '<div class="item" data-bopen="' + esc(idx) + '">'
        + '<span class="item-ico">库</span>'
        + '<span class="item-main"><span class="item-t">' + esc(b.name || idx || '未命名题库') + '</span>'
        + '<span class="item-s">' + bankItemCount(b) + ' 题'
        + (b.qtype ? (' · ' + b.qtype) : '') + '</span></span>'
        + '<span class="item-op"><button type="button" class="mini" data-brename="' + esc(idx) + '">重命名</button>'
        + '<button type="button" class="mini danger" data-bdrop="' + esc(idx) + '">删除</button></span>'
        + '<span class="item-x">查看</span></div>';
    }).join('') + '</div>';
  }

  function bankOpenObj() {
    var list = bankListArr();
    var key = String(state.bankOpen);
    var hit = null;
    list.forEach(function (b) {
      if (!hit && (String(bankIndexOf(b)) === key || String(b.name) === key)) hit = b;
    });
    return hit;
  }

  /* 题库列表接口只给题库名与题量，进入某题库时按 file 拉一次题目明细 */
  function loadBankItems(b) {
    var key = bankIndexOf(b);
    if (state.bankLoading === key) return;
    state.bankLoading = key;
    $('bankList').innerHTML = '<div class="dim">正在读取「' + esc(b.name || key) + '」的题目…</div>';
    api('/api/bank/load?file=' + encodeURIComponent(key)).then(function (r) {
      var d = r.data || {};
      if (state.bankLoading === key) state.bankLoading = '';
      if (!r.ok || d.ok === false) {
        $('bankList').innerHTML = '<div class="empty">读取题库内容失败：'
          + esc(d.msg || ('HTTP ' + r.status)) + '</div>';
        return;
      }
      b.items = d.items || [];
      if (d.name) b.name = b.name || d.name;
      b.count = b.items.length;
      // 顺带恢复「上一次 AI 优化」的优化前快照，用于「对比」与「已优化」标签
      bqLoadLast(key).then(function () {
        bqSelReset(b.items.length);
        renderBank();
      });
    }).catch(function (e) {
      if (state.bankLoading === key) state.bankLoading = '';
      $('bankList').innerHTML = '<div class="empty">读取题库内容失败：' + esc(e && e.message) + '</div>';
    });
  }

  /* 优化前快照：来自 /api/bank/ai_optimize_last（与电脑端同一份记录） */
  function bqLoadLast(file) {
    var o = bqOpt();
    if (o.running) return Promise.resolve(o);
    return api('/api/bank/ai_optimize_last?file=' + encodeURIComponent(file)).then(function (r) {
      var d = r.data || {};
      var snap = d.snap || {};
      var keys = Object.keys(snap);
      o.snapFile = file;
      o.jid = d.jid || '';
      o.before = {};
      keys.forEach(function (k) { o.before[Number(k)] = snap[k]; });
      o.okN = d.ok_n || keys.length;
      o.failN = d.fail_n || 0;
      o.fail = (d.rows || []).filter(function (x) { return !x.ok; }).map(function (x) { return x.index; });
      return o;
    }).catch(function () { return o; });
  }

  /* 换了题库 / 重新加载后清掉越界的勾选 */
  function bqSelReset(n) {
    var sel = bqSelSet();
    Object.keys(sel).forEach(function (k) {
      var i = Number(k);
      if (!(sel[k]) || i < 0 || i >= n) delete sel[k];
    });
    bqOptSync();
  }

  function bankItemsShow() {
    var b = bankOpenObj();
    var items = (b && b.items) || [];
    var kw = ($('bankQ').value || '').trim().toLowerCase();
    var only = !!state.bankOptOnly;
    var o = bqOpt();
    return items.filter(function (it, gi) {
      if (kw) {
        var hay = (String(it.question || '') + ' ' + String(it.qtype || '')).toLowerCase();
        if (hay.indexOf(kw) < 0) return false;
      }
      if (only && o.before[gi]) return false;
      return true;
    });
  }

  /* 单题的优化状态标签（已优化 / 失败可重试 / 未优化） */
  function bqOptTag(gi) {
    var o = bqOpt();
    if (o.fail && o.fail.indexOf(gi) >= 0) return '<span class="bopt-tag bad">优化失败</span>';
    if (o.before[gi]) return '<span class="bopt-tag">已优化</span>';
    return '';
  }

  function renderBankItems() {
    var b = bankOpenObj();
    if (!b) { state.bankOpen = null; renderBank(); return; }
    var kw = ($('bankQ').value || '').trim().toLowerCase();
    var show = bankItemsShow();
    var items = b.items || [];
    $('bankHit').textContent = (kw ? ('本库命中 ' + show.length + ' 题') : '')
      + (state.bankOptOnly ? '（只看未优化）' : '');
    $('btnBankOptOnly').hidden = !(b.items && b.items.length);
    $('btnBankOptOnly').textContent = state.bankOptOnly ? '显示全部题目' : '只看未优化';
    if (!show.length) {
      $('bankList').innerHTML = '<div class="empty">'
        + (state.bankOptOnly ? '这个题库的题目都已优化过了。' : (kw ? '本库没有匹配的题目。' : '这个题库是空的，点「新建题目」添加第一条。'))
        + '</div>';
      bqOptSync();
      return;
    }
    var curQ = String(((state.clip || {}).question) || '').trim();
    $('bankList').innerHTML = show.slice(0, 300).map(function (it, i) {
      var gi = items.indexOf(it);
      var q = String(it.question || it.qno || '（无题干）');
      var on = curQ && q.trim() === curQ;
      var sel = !!bqSelSet()[gi];
      return '<div class="item bq' + (on ? ' on' : '') + '" data-bgi="' + gi + '">'
        + '<span class="item-chk"><input type="checkbox" data-bsel="' + gi + '"'
        + (sel ? ' checked' : '') + ' aria-label="选择第 ' + (gi + 1) + ' 题"></span>'
        + '<span class="item-ico">' + esc(it.qno != null ? String(it.qno) : String(gi + 1)) + '</span>'
        + '<span class="item-main"><span class="item-q">' + esc(q) + '</span>'
        + '<span class="item-s">' + esc(it.qtype || '未标题型') + (on ? ' · 已是当前题目' : '') + '</span></span>'
        + bqOptTag(gi)
        + '<div class="bitem-ops">'
        + '<button type="button" class="mini' + (on ? ' accent' : '') + '" data-bset="' + gi + '">'
        + (on ? '当前' : '设为当前') + '</button>'
        + '<button type="button" class="mini" data-bdet="' + gi + '">详情</button>'
        + '<button type="button" class="mini" data-bedit="' + gi + '">编辑</button>'
        + '<button type="button" class="mini" data-bopt="' + gi + '">AI 优化</button>'
        + (bqOpt().before[gi] ? '<button type="button" class="mini" data-bcmp="' + gi + '">对比</button>' : '')
        + '<button type="button" class="mini danger" data-bdel="' + gi + '">删除</button>'
        + '</div></div>';
    }).join('');
    bqOptSync();
  }

  function searchBank() {
    var kw = ($('bankQ').value || '').trim();
    if (!kw) { state.bankSearch = null; state.bankOpen = null; renderBank(); return; }
    $('bankList').innerHTML = '<div class="dim">正在搜索「' + esc(kw) + '」…</div>';
    api('/api/bank/search?limit=30&q=' + encodeURIComponent(kw)).then(function (r) {
      var d = r.data || {};
      if (!r.ok || d.ok === false) {
        $('bankList').innerHTML = '<div class="empty">搜索失败：' + esc(d.msg || ('HTTP ' + r.status)) + '</div>';
        return;
      }
      state.bankSearch = { kw: kw, d: d };
      state.bankOpen = null;
      renderBankSearch();
    }).catch(function (e) {
      $('bankList').innerHTML = '<div class="empty">搜索失败：' + esc(e && e.message) + '</div>';
    });
  }

  function renderBankSearch() {
    var s = state.bankSearch || {};
    var d = s.d || {};
    var items = d.items || [];
    $('btnBankBack').hidden = false;
    $('bankHit').textContent = '搜索「' + (s.kw || '') + '」命中 ' + (d.total != null ? d.total : items.length)
      + ' 题' + (d.scanned ? (' · 扫描 ' + d.scanned) : '');
    if (!items.length) {
      $('bankList').innerHTML = '<div class="empty">没有搜到题干或题型匹配的题目。换个关键词试试。</div>';
      return;
    }
    var curQ = String(((state.clip || {}).question) || '').trim();
    $('bankList').innerHTML = items.map(function (it, i) {
      var q = String(it.question || '（无题干）');
      var on = curQ && q.trim() === curQ;
      var where = it.where ? ('命中' + it.where) : '';
      var bits = [it.qtype || '未标题型', it.bank || it.file || ''];
      if (where) bits.push(where);
      if (on) bits.push('已是当前题目');
      return '<div class="item bq' + (on ? ' on' : '') + '">'
        + '<span class="item-ico">' + esc(it.qno != null ? String(it.qno) : String(i + 1)) + '</span>'
        + '<span class="item-main"><span class="item-q">' + esc(q) + '</span>'
        + '<span class="item-s">' + esc(bits.filter(Boolean).join(' · ')) + '</span></span>'
        + '<div class="bitem-ops">'
        + '<button type="button" class="mini' + (on ? ' accent' : '') + '" data-bs="' + i + '">'
        + (on ? '当前' : '设为当前') + '</button>'
        + '<button type="button" class="mini" data-bsgo="' + i + '">打开所在题库</button>'
        + '</div></div>';
    }).join('');
  }

  function bankSetCur(payload) {
    if (!payload || !payload.question) { toast('这条题目没有题干，无法设为当前'); return; }
    var cur = state.clip || {};
    if (String(cur.question || '').trim() === String(payload.question).trim()) {
      toast('已经是当前题目');
      return;
    }
    var body = { question: payload.question };
    if (payload.qtype) body.qtype = payload.qtype;
    postJSON('/api/session/info', body).then(function (r) {
      var d = r.data || {};
      if (!r.ok || d.ok === false) {
        toast('设置失败：' + (d.msg || ('HTTP ' + r.status)), 3000);
        return;
      }
      state.clip = null;
      toast('已设为当前题目' + (payload.qtype ? ('，题型：' + payload.qtype) : ''));
      loadBankCur();
      loadClip();
      renderBank();
    }).catch(function (e) { toast('设置失败：' + (e && e.message), 3000); });
  }

  /* ---------------- 题目详情 / 编辑 / 新建 / 删除（手机 / 平板全功能） ---------------- */
  function bqSplitLines(txt) {
    return String(txt || '').split(/\n+/).map(function (x) { return x.trim(); }).filter(Boolean);
  }
  /* 把「环节（小标题）：要点1；要点2」文本还原成 structure 结构 */
  function bqParseStages(txt, old) {
    var out = [];
    String(txt || '').split(/\n+/).forEach(function (line) {
      line = line.trim();
      if (!line) return;
      var stage = '', label = '', rest = '';
      var m = line.match(/^([^:：（(]*)[（(]([^）)]*)[）)]\s*[:：]?\s*(.*)$/);
      if (m) {
        stage = m[1]; label = m[2]; rest = m[3] || '';
      } else {
        var m2 = line.match(/^([^:：]+)[:：]\s*(.*)$/);
        if (m2) { stage = m2[1]; rest = m2[2] || ''; }
        else { stage = line; }
      }
      stage = stage.replace(/^[·•\s]+/, '').replace(/^\d+[.、)）]\s*/, '').trim();
      if (!stage) return;
      var pts = String(rest).split(/[；;]/).map(function (x) { return x.trim(); }).filter(Boolean);
      out.push({ stage: stage, label: label.trim(), points: pts });
    });
    if (!out.length && old && old.length) return old;
    return out;
  }

  function bqDetailHtml(it) {
    var html = '';
    html += '<div class="mfield"><label>题干</label><div class="mkv">'
      + (it.question ? esc(it.question) : '<span class="diml">（空）</span>') + '</div></div>';
    html += '<div class="mfield"><label>题型</label><div class="mkv">'
      + (it.qtype ? esc(it.qtype) : '<span class="diml">（未标注）</span>') + '</div></div>';
    if (it.qtype_reason) {
      html += '<div class="mfield"><label>题型判断依据</label><div class="mkv diml">'
        + esc(it.qtype_reason) + '</div></div>';
    }
    var exam = it.exam || [];
    html += '<div class="mfield"><label>审题要点 · ' + exam.length + ' 条</label><div class="mkv">'
      + (exam.length ? exam.map(function (x, i) { return (i + 1) + '. ' + esc(x); }).join('<br>')
        : '<span class="diml">（空）</span>') + '</div></div>';
    var st = it.structure || [];
    html += '<div class="mfield"><label>答题环节 · ' + st.length + ' 个</label>';
    if (st.length) {
      html += st.map(function (s) {
        var head = (s.stage || '') + (s.label ? ('（' + s.label + '）') : '');
        var pts = (s.points || []).map(function (p) { return esc(p); });
        return '<div class="mstg"><div class="mstg-h">' + esc(head) + '</div>'
          + '<div class="mstg-p">' + (pts.length ? pts.join('<br>') : '（空）') + '</div></div>';
      }).join('');
    } else {
      html += '<div class="mkv diml">（空）</div>';
    }
    html += '</div>';
    var keys = it.keys || [];
    html += '<div class="mfield"><label>关键要点 · ' + keys.length + ' 条</label><div class="mkv">'
      + (keys.length ? keys.map(function (x, i) { return (i + 1) + '. ' + esc(x); }).join('<br>')
        : '<span class="diml">（空）</span>') + '</div></div>';
    html += '<div class="mfield"><label>参考要点</label><div class="mkv">'
      + (it.ref ? esc(it.ref) : '<span class="diml">（空）</span>') + '</div></div>';
    return html;
  }

  function bqOpenDetail(gi) {
    var o = bqItemAt(gi);
    if (!o) return;
    var it = o.item;
    var no = (it.qno != null) ? it.qno : (gi + 1);
    mSheetOpen('第 ' + no + ' 题 · 详情', bqDetailHtml(it), [
      { text: '设为当前题目', primary: true, act: 'cur', gi: gi },
      { text: '编辑', act: 'edit', gi: gi },
      { text: 'AI 优化', act: 'opt', gi: gi }
    ]);
  }

  function bqOpenEdit(gi) {
    var o = bqItemAt(gi);
    if (!o) return;
    var it = o.item;
    var html = '';
    html += '<div class="mfield"><label>题干</label><textarea class="inp" id="mBqQ">'
      + esc(it.question || '') + '</textarea></div>';
    html += '<div class="mfield"><label>题型</label><input class="inp w100" id="mBqType" value="'
      + esc(it.qtype || '') + '"></div>';
    html += '<div class="mfield"><label>审题要点（每行一条）</label><textarea class="inp" id="mBqExam">'
      + esc((it.exam || []).join('\n')) + '</textarea></div>';
    html += '<div class="mfield"><label>答题环节（每行「环节（小标题）：要点1；要点2」）</label>'
      + '<textarea class="inp" id="mBqStg" style="min-height:120px">'
      + esc(bqLines(it, 'structure').join('\n')) + '</textarea></div>';
    html += '<div class="mfield"><label>关键要点（每行一条）</label><textarea class="inp" id="mBqKeys">'
      + esc((it.keys || []).join('\n')) + '</textarea></div>';
    html += '<div class="mfield"><label>参考要点</label><textarea class="inp" id="mBqRef">'
      + esc(it.ref || '') + '</textarea></div>';
    mSheetOpen('编辑第 ' + (gi + 1) + ' 题', html, [
      { text: '保存', primary: true, act: 'save-edit', gi: gi },
      { text: '取消', act: 'close' }
    ]);
  }

  function bqSaveEdit(gi) {
    var o = bqItemAt(gi);
    if (!o) return;
    var b = o.bank;
    var qEl = $('mBqQ');
    if (!qEl) return;
    var question = String(qEl.value || '').trim();
    if (question.length < 4) { toast('题干太短，至少 4 个字'); return; }
    var body = {
      file: bqFileOf(b), index: gi, question: question,
      qtype: ($('mBqType') || {}).value || '',
      exam: bqSplitLines(($('mBqExam') || {}).value),
      keys: bqSplitLines(($('mBqKeys') || {}).value),
      ref: ($('mBqRef') || {}).value || '',
      structure: bqParseStages(($('mBqStg') || {}).value, o.item.structure)
    };
    postJSON('/api/bank/item/update', body).then(function (r) {
      var d = r.data || {};
      if (!r.ok || d.ok === false) { toast('保存失败：' + (d.msg || ('HTTP ' + r.status)), 3200); return; }
      var it = (d.item || body);
      o.item.question = it.question != null ? it.question : body.question;
      o.item.qtype = it.qtype != null ? it.qtype : body.qtype;
      o.item.exam = it.exam || body.exam;
      o.item.keys = it.keys || body.keys;
      o.item.ref = it.ref != null ? it.ref : body.ref;
      o.item.structure = it.structure || body.structure;
      mSheetClose();
      toast('已保存');
      renderBankItems();
    }).catch(function (e) { toast('保存失败：' + (e && e.message), 3200); });
  }

  function bqOpenNew() {
    var b = bankOpenObj();
    if (!b) { toast('请先打开一个题库'); return; }
    var html = '';
    html += '<div class="mfield"><label>题干</label><textarea class="inp" id="mBqQ" placeholder="粘贴或输入面试题干"></textarea></div>';
    html += '<div class="mfield"><label>题型</label><input class="inp w100" id="mBqType" placeholder="如 综合分析 / 组织管理"></div>';
    html += '<div class="mfield"><label>审题要点（每行一条）</label><textarea class="inp" id="mBqExam"></textarea></div>';
    html += '<div class="mfield"><label>答题环节（每行「环节（小标题）：要点1；要点2」）</label><textarea class="inp" id="mBqStg" style="min-height:120px"></textarea></div>';
    html += '<div class="mfield"><label>关键要点（每行一条）</label><textarea class="inp" id="mBqKeys"></textarea></div>';
    html += '<div class="mfield"><label>参考要点</label><textarea class="inp" id="mBqRef"></textarea></div>';
    mSheetOpen('新建题目 · ' + bqNameOf(b), html, [
      { text: '保存入库', primary: true, act: 'save-new' },
      { text: '取消', act: 'close' }
    ]);
  }

  function bqSaveNew() {
    var b = bankOpenObj();
    if (!b) return;
    var qEl = $('mBqQ');
    if (!qEl) return;
    var question = String(qEl.value || '').trim();
    if (question.length < 4) { toast('题干太短，至少 4 个字'); return; }
    var item = {
      question: question,
      qtype: ($('mBqType') || {}).value || '',
      exam: bqSplitLines(($('mBqExam') || {}).value),
      structure: bqParseStages(($('mBqStg') || {}).value, []),
      keys: bqSplitLines(($('mBqKeys') || {}).value),
      ref: ($('mBqRef') || {}).value || ''
    };
    postJSON('/api/bank/add_item', { bank: bqNameOf(b), item: item }).then(function (r) {
      var d = r.data || {};
      if (!r.ok || d.ok === false) { toast('保存失败：' + (d.msg || ('HTTP ' + r.status)), 3200); return; }
      mSheetClose();
      toast('已入库');
      bqReloadItems();
    }).catch(function (e) { toast('保存失败：' + (e && e.message), 3200); });
  }

  function bqDeleteItem(gi) {
    var o = bqItemAt(gi);
    if (!o) return;
    var q = String(o.item.question || '').slice(0, 30);
    if (!window.confirm('删除这道题？\n\n' + q + '\n\n删除后不可恢复。')) return;
    postJSON('/api/bank/item/delete', { file: bqFileOf(o.bank), index: gi }).then(function (r) {
      var d = r.data || {};
      if (!r.ok || d.ok === false) { toast('删除失败：' + (d.msg || ('HTTP ' + r.status)), 3200); return; }
      mSheetClose();
      toast('已删除');
      bqReloadItems();
    }).catch(function (e) { toast('删除失败：' + (e && e.message), 3200); });
  }

  /* 重新拉题目明细（增删改后保持停留在同一题库） */
  function bqReloadItems() {
    var b = bankOpenObj();
    if (!b) { loadBank(); return; }
    b.items = null;
    state.bankLoading = '';
    loadBankItems(b);
  }

  /* ---------------- 题库管理：新建 / 重命名 / 删除题库 ---------------- */
  function bqOpenMgr() {
    var list = bankListArr();
    var html = '';
    html += '<div class="mfield"><label>新建题库</label>'
      + '<div style="display:flex;gap:8px;align-items:center">'
      + '<input class="inp w100" id="mNewBank" placeholder="题库名称，如 2026 国考结构化">'
      + '<button type="button" class="mini accent" data-mact="newbank">新建</button></div></div>';
    html += '<div class="mfield"><label>已有题库 · ' + list.length + ' 个</label>';
    if (list.length) {
      html += list.map(function (b, i) {
        var f = bankIndexOf(b);
        return '<div class="mstg"><div class="mstg-h">' + esc(b.name || f) + '</div>'
          + '<div class="mstg-p">' + bankItemCount(b) + ' 题</div>'
          + '<div class="mrow" style="margin:8px 0 0">'
          + '<button type="button" class="mini" data-mact="open" data-mi="' + i + '">打开</button>'
          + '<button type="button" class="mini" data-mact="rename" data-mi="' + i + '">重命名</button>'
          + '<button type="button" class="mini danger" data-mact="drop" data-mi="' + i + '">删除题库</button>'
          + '</div></div>';
      }).join('');
    } else {
      html += '<div class="mkv diml">还没有题库，先在上面新建一个。</div>';
    }
    html += '</div>';
    mSheetOpen('题库管理', html, [{ text: '关闭', act: 'close' }]);
  }

  function bqNewBank() {
    var el = $('mNewBank');
    var name = el ? String(el.value || '').trim() : '';
    if (!name) { toast('请填写题库名称'); return; }
    postJSON('/api/bank/create', { name: name }).then(function (r) {
      var d = r.data || {};
      if (!r.ok || d.ok === false) { toast('新建失败：' + (d.msg || ('HTTP ' + r.status)), 3200); return; }
      mSheetClose();
      toast('已新建题库「' + name + '」');
      loadBank();
    }).catch(function (e) { toast('新建失败：' + (e && e.message), 3200); });
  }

  function bqRenameForm(idx) {
    var b = bankListArr()[idx];
    if (!b) return;
    var f = bankIndexOf(b);
    var old = bqNameOf(b);
    var html = '<div class="mfield"><label>题库名称</label><input class="inp w100" id="mRenBank" value="'
      + esc(old) + '"></div><div class="mfield"><div class="mkv diml">改的是题库名称，里面 ' + bankItemCount(b)
      + ' 道题的内容不受影响。</div></div>';
    mSheetOpen('重命名题库', html, [
      { text: '保存', primary: true, act: 'dobank-rename', file: f },
      { text: '取消', act: 'close' }
    ]);
  }

  function bqRenameBank(file) {
    var el = $('mRenBank');
    var name = el ? String(el.value || '').trim() : '';
    if (!name) { toast('请填写新的题库名称'); return; }
    postJSON('/api/bank/rename', { file: file, name: name }).then(function (r) {
      var d = r.data || {};
      if (!r.ok || d.ok === false) { toast('重命名失败：' + (d.msg || ('HTTP ' + r.status)), 3200); return; }
      mSheetClose();
      toast('已重命名为「' + name + '」');
      if (String(state.bankOpen || '') === String(file)) state.bankOpen = d.file || state.bankOpen;
      state.bank = null;
      loadBank();
    }).catch(function (e) { toast('重命名失败：' + (e && e.message), 3200); });
  }

  function bqDropBank(idx) {
    var b = bankListArr()[idx];
    if (!b) return;
    var f = bankIndexOf(b);
    if (!window.confirm('删除整个题库「' + bqNameOf(b) + '」？\n\n里面有 ' + bankItemCount(b)
      + ' 道题，删除后不可恢复。')) return;
    postJSON('/api/bank/delete', { file: f }).then(function (r) {
      var d = r.data || {};
      if (!r.ok || d.ok === false) { toast('删除失败：' + (d.msg || ('HTTP ' + r.status)), 3200); return; }
      mSheetClose();
      toast('已删除题库');
      if (String(state.bankOpen || '') === String(f)) state.bankOpen = null;
      state.bank = null;
      loadBank();
    }).catch(function (e) { toast('删除失败：' + (e && e.message), 3200); });
  }

  /* ---------------- AI 优化：一题一题串行 + 实时输出 ---------------- */
  function bqDoOptimize(idxs) {
    var b = bankOpenObj();
    if (!b) { toast('请先打开一个题库'); return; }
    if (!(b.items || []).length) { toast('题库是空的'); return; }
    if (!idxs || !idxs.length) { toast('请先勾选题目'); return; }
    var o = bqOpt();
    if (o.running) { toast('正在优化中，等这一轮结束再继续'); return; }
    o.running = true; o.stopping = false; o.jid = ''; o.live = [];
    o.total = idxs.length; o.done = 0; o.okN = 0; o.failN = 0; o.msg = ''; o.curNo = 0;
    bqOptSync();
    if (o.open) bqLiveRender();
    postJSON('/api/bank/ai_optimize', { file: bqFileOf(b), indices: idxs }).then(function (r) {
      var d = r.data || {};
      if (!r.ok || d.ok === false) {
        o.running = false; bqOptSync();
        toast('提交失败：' + (d.msg || ('HTTP ' + r.status)), 3400);
        return;
      }
      o.jid = d.jid || '';
      o.total = d.total || idxs.length;
      bqOptSync();
      bqOptPoll();
    }).catch(function (e) {
      o.running = false; bqOptSync();
      toast('提交失败：' + (e && e.message), 3400);
    });
  }

  function bqOptPoll() {
    var o = bqOpt();
    if (!o.jid) return;
    clearTimeout(o.timer);
    o.timer = setTimeout(function () {
      // 弹层没打开时只取进度（省流量），打开了才连带逐题前后内容与 AI 实时返回
      var url = '/api/bank/ai_optimize_job?jid=' + encodeURIComponent(o.jid)
        + (o.open ? '&live=1&rawmax=3000' : '');
      api(url).then(function (r) {
        var d = r.data || {};
        if (!r.ok || d.ok === false) {
          o.running = false; o.stopping = false; bqOptSync();
          toast('优化任务丢失：' + (d.msg || ('HTTP ' + r.status)), 3200);
          return;
        }
        o.done = d.done || 0;
        o.total = d.total || o.total;
        o.okN = d.ok_n || 0;
        o.failN = d.fail_n || 0;
        if (d.live) o.live = d.live;
        o.elapsed = d.elapsed || 0;
        o.curNo = d.cur_no || 0;
        o.msg = d.msg || '';
        o.cancel = !!d.cancel;
        o.fail = (d.rows || []).filter(function (x) { return !x.ok; }).map(function (x) { return x.index; });
        o.live.forEach(function (x) { if (x && x.before) o.before[x.index] = x.before; });
        bqOptSync();
        if (o.open) bqLiveRender();
        if (d.status === 'running') { o.running = true; bqOptPoll(); return; }
        o.running = false; o.stopping = false;
        bqOptSync();
        if (o.open) bqLiveRender();
        toast('AI 优化结束：成功 ' + o.okN + ' 题，失败 ' + o.failN + ' 题', 3000);
        bqReloadItems();
      }).catch(function () {
        if (o.open) { var st = $('bqLiveBox'); if (st) st.innerHTML = '<div class="ml-wait">读取实时输出失败，稍后自动重试…</div>'; }
        clearTimeout(o.timer);
        o.timer = setTimeout(bqOptPoll, 1600);
      });
    }, o.open ? 500 : 900);
  }

  function bqOptStop() {
    var o = bqOpt();
    if (!o.jid || !o.running) return;
    o.stopping = true;
    bqOptSync();
    postJSON('/api/bank/ai_optimize_cancel', { jid: o.jid }).then(function () {
      toast('已发送停止指令，跑完当前这一题就停');
    }).catch(function () { toast('停止失败，请重试'); });
  }

  function bqOptSync() {
    var o = bqOpt();
    var b = bankOpenObj();
    var card = $('bankOptCard');
    if (card) card.hidden = !b;
    var el = $('bankOptN');
    if (el) el.textContent = '已选 ' + bqOn().length + ' 题';
    var pct = o.total ? Math.round(100 * o.done / o.total) : 0;
    var wrap = $('bankOptBarWrap');
    if (wrap) {
      // 一题都没处理完就整条隐藏；处理过题才留着：点它可回看逐题输出
      wrap.hidden = !(o.running || (o.total > 0 && o.done > 0));
      wrap.classList.toggle('running', !!o.running);
    }
    var bar = $('bankOptBar');
    if (bar) bar.style.width = pct + '%';
    var pp = $('bankOptPct');
    if (pp) pp.textContent = pct + '%';
    var st = $('bankOptSt');
    if (st) {
      if (o.running) {
        st.textContent = 'AI 优化中… 已完成 ' + o.done + '/' + o.total + '（成功 ' + o.okN + '，失败 '
          + o.failN + '）· 点进度条看实时输出';
        st.classList.remove('dim');
      } else if (o.jid) {
        st.textContent = '上次：成功 ' + o.okN + ' 题，失败 ' + o.failN + ' 题 · 点进度条可回看逐题输出';
        st.classList.add('dim');
      } else {
        st.textContent = '一题一题串行走 AI；点进度条可实时查看每题优化前后内容。';
        st.classList.add('dim');
      }
    }
    var sel = $('btnBankOptSel');
    if (sel) sel.disabled = o.running || bqOn().length === 0;
    var all = $('btnBankOptAll');
    if (all) all.disabled = o.running || !(b && (b.items || []).length);
    var stop = $('btnBankOptStop');
    if (stop) {
      stop.hidden = !o.running;
      stop.disabled = !!o.stopping;
      stop.textContent = o.stopping ? '正在停止…' : '停止';
    }
  }

  /* 逐题实时输出（点进度条打开） */
  function bqLiveRow(x, autoOpen) {
    var o = bqOpt();
    var st = x.state || 'wait';
    var sec = x.sec ? (x.sec >= 10 ? Math.round(x.sec) + 's' : x.sec.toFixed(1) + 's') : '';
    var q = x.q || ((x.before && x.before.question) || '') || '（题干为空）';
    var open = !!autoOpen || !!(o.liveOpen && o.liveOpen[x.index]);
    var inner = '';
    if (st === 'wait') inner += '<div class="ml-wait">排队中，轮到这一题时这里会实时出现 AI 返回的文字。</div>';
    if (st === 'skip') inner += '<div class="ml-wait">已停止，这一题没有处理。</div>';
    // AI 实时返回的文字：运行中直接铺开、随返回增长；结束后保留全文可展开
    if (x.raw) {
      var _n = x.raw_len || x.raw.length;
      var _tail = x.raw_cut ? ('，仅显示最后 ' + x.raw.length + ' 字') : '';
      if (st === 'run') {
        inner += '<div class="ml-stream"><div class="ml-stream-hd">AI 正在返回… <b>' + _n
          + '</b> 字<span class="ml-cur"></span>' + _tail + '</div>'
          + '<pre class="ml-stream-pre">' + esc(x.raw) + '</pre></div>';
      } else {
        inner += '<details class="ml-raw"><summary>AI 原始返回（' + _n + ' 字' + _tail + '）</summary>'
          + '<pre>' + esc(x.raw) + '</pre></details>';
      }
    } else if (st === 'run') {
      inner += '<div class="ml-wait">已发出请求，正在等 AI 返回第一个字……（串行：一题一题走 AI）</div>';
    }
    if (x.after) inner += bqDiffHtml(x.before, x.after);
    if (x.msg) inner += '<div class="ml-msg">' + esc(x.msg) + '</div>';
    return '<div class="ml-item st-' + st + '" data-mi="' + x.index + '">'
      + '<div class="ml-hd" role="button" tabindex="0">'
      + '<span class="ml-no">第 ' + (x.no || (x.index + 1)) + ' 题</span>'
      + '<span class="ml-tag">' + (MOPT_TXT[st] || st) + (sec ? (' · ' + sec) : '') + '</span>'
      + '<span class="ml-q">' + esc(String(q).slice(0, 90)) + '</span>'
      + '</div>'
      + '<div class="ml-bd"' + (open ? '' : ' hidden') + '>' + inner + '</div></div>';
  }

  function bqLiveRender() {
    var o = bqOpt();
    var box = $('bqLiveBox');
    if (!box) return;
    var rows = o.live || [];
    var tot = o.total || rows.length;
    var done = o.done || 0;
    var pct = tot ? Math.round(100 * done / tot) : 0;
    var head = '<div class="mbank-pw" style="cursor:default">'
      + '<span class="mbank-bar"><i style="width:' + pct + '%"></i></span>'
      + '<span class="mbank-pct">' + pct + '%</span></div>'
      + '<div class="dim" style="margin:10px 0 4px">共 ' + tot + ' 题 · 已处理 ' + done
      + ' · 成功 ' + (o.okN || 0) + ' · 失败 ' + (o.failN || 0)
      + (o.elapsed ? (' · 用时 ' + Math.round(o.elapsed) + 's') : '') + '</div>'
      + '<div class="dim" style="margin-bottom:10px">'
      + esc(o.running ? ('正在处理第 ' + (o.curNo || 0) + ' 题 / 共 ' + tot + ' 题（串行：一题一题走 AI）')
        : (o.msg || '已结束'))
      + '</div>';
    if (!rows.length) {
      box.innerHTML = head + '<div class="ml-wait">这次任务还没有逐题记录。'
        + '点「优化所选」或「优化全库」开跑后，这里会一题一题实时显示前后内容。</div>';
      return;
    }
    var auto = {}, last = -1;
    rows.forEach(function (x) {
      if (x.state === 'run') auto[x.index] = 1;
      if (x.state === 'ok' || x.state === 'fail') last = x.index;
    });
    if (last >= 0) auto[last] = 1;
    box.innerHTML = head + '<div class="ml-list">'
      + rows.map(function (x) { return bqLiveRow(x, auto[x.index]); }).join('') + '</div>';
    // 运行中把正在返回的文字滚到最新一行，保持「AI 正在写」的实时观感
    var pres = box.querySelectorAll('.st-run .ml-stream-pre');
    for (var i = 0; i < pres.length; i++) pres[i].scrollTop = pres[i].scrollHeight;
  }

  function bqLiveOpen() {
    var o = bqOpt();
    if (!o.liveOpen) o.liveOpen = {};
    o.open = true;
    mSheetOpen('逐题实时输出', '<div id="bqLiveBox"><div class="ml-wait">正在读取…</div></div>', [
      { text: '刷新', act: 'live-refresh' },
      { text: '关闭', act: 'close' }
    ]);
    bqLiveRender();
    if (o.jid && !o.running) {
      // 结束后/打开弹层时回看：拉一次最新一帧
      api('/api/bank/ai_optimize_job?jid=' + encodeURIComponent(o.jid) + '&live=1&rawmax=3000').then(function (r) {
        var d = r.data || {};
        if (!d.ok) return;
        if (d.live) o.live = d.live;
        o.done = d.done || o.done; o.total = d.total || o.total;
        o.okN = d.ok_n || 0; o.failN = d.fail_n || 0; o.msg = d.msg || o.msg;
        o.elapsed = d.elapsed || 0;
        o.curNo = d.cur_no || 0;
        o.live.forEach(function (x) { if (x && x.before) o.before[x.index] = x.before; });
        bqLiveRender();
      }).catch(function () {});
    }
  }

  function bqCloseLive() {
    var o = bqOpt();
    o.open = false;
  }

  /* 单题前后对比（来自上一次优化的快照 vs 当前内容） */
  function bqOpenDiff(gi) {
    var o = bqItemAt(gi);
    if (!o) return;
    var st = bqOpt();
    var before = st.before[gi];
    if (!before) {
      (st.live || []).forEach(function (x) { if (x.index === gi && x.before) before = x.before; });
    }
    if (!before) { toast('没有找到这一题的优化前记录'); return; }
    var after = bqSnapOf(o.item);
    var it = o.item;
    var no = (it.qno != null) ? it.qno : (gi + 1);
    mSheetOpen('第 ' + no + ' 题 · 优化前后对比',
      bqDiffHtml(before, after)
      + '<div class="dim" style="margin-top:10px">上（左）为优化前，下（右）为优化后；灰色表示该字段没变化。</div>',
      [{ text: '关闭', act: 'close' }]);
  }

  /* 面板按钮统一分发 */
  function mSheetOp(i) {
    var box = $('mSheetOps');
    var ops = (box && box._ops) || [];
    var o = ops[i];
    if (!o) return;
    if (o.act === 'close') { mSheetClose(); return; }
    if (o.act === 'cur') { bankSetCurFrom(o.gi); return; }
    if (o.act === 'edit') { bqOpenEdit(o.gi); return; }
    if (o.act === 'opt') { bqDoOptimize([o.gi]); return; }
    if (o.act === 'save-edit') { bqSaveEdit(o.gi); return; }
    if (o.act === 'save-new') { bqSaveNew(); return; }
    if (o.act === 'dobank-rename') { bqRenameBank(o.file); return; }
    if (o.act === 'live-refresh') { bqOptPoll(); bqLiveRender(); return; }
  }

  function bankSetCurFrom(gi) {
    var o = bqItemAt(gi);
    if (!o) return;
    bankSetCur({ question: String(o.item.question || ''), qtype: o.item.qtype || '' });
  }

  /* ------------------------------------------------------------ 历史 */
  function loadRecords() {
    var sel = $('histStuSel');
    var stu = (sel && sel.value) || '';
    var url = '/api/records' + (stu ? ('?student=' + encodeURIComponent(stu)) : '');
    $('historyList').innerHTML = '<div class="dim">正在读取…</div>';
    api(url).then(function (r) {
      var d = r.data || {};
      if (d.ok === false) {
        $('historyList').innerHTML = '<div class="empty">读取失败：' + esc(d.msg || ('HTTP ' + r.status)) + '</div>';
        return;
      }
      state.records = d;
      renderHistStuSel(d.facets);
      renderRecords();
    }).catch(function (e) {
      $('historyList').innerHTML = '<div class="empty">读取失败：' + esc(e && e.message) + '</div>';
    });
  }

  function renderHistStuSel(facets) {
    var sel = $('histStuSel');
    if (!sel) return;
    var cur = sel.value || '';
    var list = (facets && facets.students) || [];
    var has = false;
    var html = '<option value="">全部学生</option>' + list.map(function (x) {
      if (x.name === cur) has = true;
      return '<option value="' + esc(x.name) + '">' + esc(x.name) + '（' + x.count + '）</option>';
    }).join('');
    if (sel.innerHTML !== html) sel.innerHTML = html;
    sel.value = has ? cur : '';
  }

  function renderRecords() {
    var d = state.records || {};
    var rows = d.records || [];
    var total = (d.total != null) ? d.total : rows.length;
    var matched = (d.matched != null) ? d.matched : rows.length;
    $('historyStat').textContent = matched + ' / ' + total + ' 场'
      + (d.facets && d.facets.date_max ? (' · 最近 ' + d.facets.date_max) : '');
    if (!rows.length) {
      $('historyList').innerHTML = '<div class="empty">没有符合条件的存档。换个学生筛选或先录一场。</div>';
      return;
    }
    $('historyList').innerHTML = rows.map(function (x) {
      var title = String(x.q_short || x.question || '（未设题目）');
      var bits = [];
      if (x.created_at) bits.push(x.created_at);
      else if (x.date) bits.push(x.date);
      if (x.qtype) bits.push(x.qtype);
      var who = x.student || x.stu_n || '';
      if (who) bits.push(who);
      if (x.duration) bits.push(fmtDur(x.duration));
      if (x.segments != null) bits.push(x.segments + ' 句');
      var snip = String(x.snippet || '');
      return '<div class="item">'
        + '<span class="item-ico">史</span>'
        + '<span class="item-main"><span class="item-t">' + esc(title) + '</span>'
        + '<span class="item-s">' + esc(bits.join(' · ')) + '</span>'
        + (snip ? ('<span class="item-s">' + esc(snip) + '</span>') : '')
        + '</span>'
        + '<span class="row-ops">'
        + '<button type="button" class="mini accent" data-rec="' + esc(x.file || '') + '">回看</button>'
        + '<button type="button" class="mini" data-recload="' + esc(x.file || '') + '">载入题面</button>'
        + '</span></div>';
    }).join('');
  }

  function histTxt(text) {
    var t = String(text == null ? '' : text);
    return t.replace(/\s+/g, '').length;
  }

  function openRecord(file, title) {
    if (!file) return;
    state.histFile = file;
    $('historyDetailCard').hidden = false;
    $('historyDetailTitle').textContent = '存档详情' + (title ? ('：' + String(title).slice(0, 16)) : '');
    $('historyDetail').innerHTML = '<div class="dim">正在读取存档…</div>';
    api('/api/records/load?file=' + encodeURIComponent(file)).then(function (r) {
      var d = r.data || {};
      if (!r.ok || d.ok === false) {
        $('historyDetail').innerHTML = '<div class="empty">读取失败：' + esc(d.msg || ('HTTP ' + r.status)) + '</div>';
        return;
      }
      var rec = d.data || d.record || {};
      state.hist = rec;
      var sts = rec.students || [];
      var cur = String(rec.cur_student || '');
      var pick = null;
      sts.forEach(function (x) { if (!pick && String(x.id == null ? '' : x.id) === cur) pick = x; });
      pick = pick || sts[0] || null;
      state.histStu = pick ? String(pick.id == null ? '' : pick.id) : '';
      renderHistDetail();
      var card = $('historyDetailCard');
      if (card && card.scrollIntoView) card.scrollIntoView({ block: 'start' });
    }).catch(function (e) {
      $('historyDetail').innerHTML = '<div class="empty">读取失败：' + esc(e && e.message) + '</div>';
    });
  }

  function histStudent() {
    var rec = state.hist || {};
    var sts = rec.students || [];
    var sid = String(state.histStu || '');
    var hit = null;
    sts.forEach(function (x) { if (!hit && String(x.id == null ? '' : x.id) === sid) hit = x; });
    return hit || sts[0] || {};
  }

  function histPlainGroups(paras) {
    var groups = [], map = {};
    (paras || []).forEach(function (p) {
      var tx = String((p && p.plain) || '').trim();
      if (!tx) return;
      var label = String((p && (p.plain_label || p.label || p.title)) || '未标注').trim() || '未标注';
      if (!map[label]) { map[label] = { label: label, text: '', chars: 0 }; groups.push(map[label]); }
      var g = map[label];
      g.text += (g.text ? '\n' : '') + tx;
    });
    groups.forEach(function (g) { g.chars = histTxt(g.text); });
    return groups;
  }

  function renderHistDetail() {
    var rec = state.hist || {};
    var sts = rec.students || [];
    var st = histStudent();
    var segs = st.segments || rec.segments || [];
    var paras = st.paragraphs || rec.paragraphs || [];
    var review = st.review || rec.review || '';
    var html = '';

    if (sts.length > 1) {
      html += '<div class="btn-row" style="margin-bottom:8px">' + sts.map(function (x) {
        var on = String(x.id == null ? '' : x.id) === String(st.id == null ? '' : st.id);
        return '<button type="button" class="mini' + (on ? ' accent' : '') + '" data-hstu="'
          + esc(x.id == null ? '' : x.id) + '">' + esc(x.name || '学生') + '</button>';
      }).join('') + '</div>';
    }

    var meta = [];
    if (st.name || rec.candidate) meta.push(['学生', st.name || rec.candidate || '']);
    meta.push(['题型', st.qtype || rec.qtype || '—']);
    meta.push(['时间', rec.created_at || rec.saved_at || rec.date || '—']);
    if (rec.duration || st.duration) meta.push(['时长', fmtDur(rec.duration || st.duration)]);
    meta.push(['规模', segs.length + ' 句 · ' + paras.length + ' 段']);
    if (rec.question) meta.push(['题面', rec.question]);
    html += kvRows(meta);

    html += '<div class="sec-t">逐字稿</div>';
    if (!segs.length) {
      html += '<div class="empty">该场没有逐字稿。</div>';
    } else {
      var segMap = {};
      segs.forEach(function (s) { if (s && s.id) segMap[String(s.id)] = s; });
      var used = {};
      var body = '';
      paras.forEach(function (p, pi) {
        var ids = p.seg_ids || [];
        var lines = '';
        ids.forEach(function (sid) {
          var s = segMap[String(sid)];
          if (!s) return;
          used[String(sid)] = 1;
          var tags = (s.tags || []).concat(s.ctags || []);
          lines += '<div class="hseg"><span class="hseg-t">' + esc(s.text || '') + '</span>'
            + (tags.length ? ('<span class="hseg-tag">' + esc(tags.join('/')) + '</span>') : '')
            + (s.note ? ('<span class="hseg-note">' + esc(s.note) + '</span>') : '')
            + '</div>';
        });
        if (!lines) return;
        body += '<div class="hpara"><div class="hpara-hd">' + esc(p.title || p.plain_label || ('第 ' + (pi + 1) + ' 段')) + '</div>'
          + lines + '</div>';
      });
      var rest = '';
      segs.forEach(function (s) {
        if (used[String(s.id)]) return;
        var tags = (s.tags || []).concat(s.ctags || []);
        rest += '<div class="hseg"><span class="hseg-t">' + esc(s.text || '') + '</span>'
          + (tags.length ? ('<span class="hseg-tag">' + esc(tags.join('/')) + '</span>') : '') + '</div>';
      });
      if (rest) body += '<div class="hpara"><div class="hpara-hd">未归段</div>' + rest + '</div>';
      html += body || '<div class="empty">该场没有逐字稿。</div>';
    }

    var groups = histPlainGroups(paras);
    html += '<div class="sec-t">精简稿</div>';
    if (!groups.length) {
      html += '<div class="empty">该场没有精简稿。</div>';
    } else {
      html += groups.map(function (g) {
        return '<div class="pg"><div class="pg-hd"><span class="pg-t">' + esc(g.label) + '</span>'
          + '<span class="pg-n">' + g.chars + ' 字</span></div>'
          + '<div class="pg-tx">' + esc(g.text) + '</div></div>';
      }).join('');
    }

    html += '<div class="sec-t">点评</div>';
    html += review
      ? ('<div class="md">' + mdLite(review) + '</div>')
      : '<div class="empty">该场还没有点评。</div>';

    html += '<div class="btn-row" style="margin-top:10px">'
      + '<button type="button" class="mini accent" data-hact="loadcur">载入题面到当前</button>'
      + '<button type="button" class="mini" data-hact="copyscript">复制逐字稿</button>'
      + '<button type="button" class="mini" data-hact="copyreview">复制点评</button>'
      + '</div>';
    $('historyDetail').innerHTML = html;
  }

  function histPlainText() {
    var st = histStudent();
    var paras = st.paragraphs || [];
    var gs = histPlainGroups(paras);
    return gs.map(function (g) { return '【' + g.label + '】' + g.text; }).join('\n');
  }

  function histSegText() {
    var rec = state.hist || {};
    var st = histStudent();
    var segs = st.segments || rec.segments || [];
    return segs.map(function (s) { return String(s.text || ''); }).join('\n');
  }

  /* 历史「载入当前」：现有后端没有把存档逐字稿写回会话的接口，这里只把该场题面/题型载入备战下一场 */
  /* 列表里的「载入题面」：先读该场存档，再把题面/题型设为当前题目 */
  function histLoadFile(file) {
    if (!file) return;
    if (state.histFile === file && state.hist) { histLoadCur(); return; }
    api('/api/records/load?file=' + encodeURIComponent(file)).then(function (r) {
      var d = r.data || {};
      var rec = d.data || d.record || {};
      if (!r.ok || !rec || (!rec.question && !rec.qtype)) {
        toast((d.msg || '该场没有题面信息可载入'), 3000);
        return;
      }
      state.histFile = file;
      state.hist = rec;
      var sts = rec.students || [];
      var cur = String(rec.cur_student || '');
      var pick = null;
      sts.forEach(function (x) { if (!pick && String(x.id == null ? '' : x.id) === cur) pick = x; });
      pick = pick || sts[0] || null;
      state.histStu = pick ? String(pick.id == null ? '' : pick.id) : '';
      histLoadCur();
    }).catch(function (e) { toast('读取失败：' + (e && e.message), 3000); });
  }

  function histLoadCur() {
    var rec = state.hist || {};
    var st = histStudent();
    var body = {
      question: rec.question || st.question || '',
      qtype: st.qtype || rec.qtype || '',
      ref: st.ref || rec.ref || ''
    };
    if (!body.question && !body.qtype) { toast('该场没有题面信息可载入'); return; }
    if (!window.confirm('把该场题面与题型设为当前题目？\n当前会话已有的逐字稿不受影响，存档只做回看。')) return;
    postJSON('/api/session/info', body).then(function (r) {
      var d = r.data || {};
      if (!r.ok || d.ok === false) { toast('载入失败：' + (d.msg || ('HTTP ' + r.status)), 3000); return; }
      state.curQ = { question: body.question, qtype: body.qtype, ref: body.ref };
      state.bankCur = null;
      toast('已把该场题面载入为当前题目');
      loadClip();
    }).catch(function (e) { toast('载入失败：' + (e && e.message), 3000); });
  }

  /* ------------------------------------------------------------ 设置 */
  function syncModeBtns() {
    var m = state.mode === 'tablet' ? 'tablet' : 'phone';
    var seg = $('modeSeg2');
    if (seg) {
      $$('#modeSeg2 .segbtn').forEach(function (b) {
        b.classList.toggle('on', b.getAttribute('data-mode') === m);
      });
    }
    var hint = $('modeHint');
    if (hint) hint.textContent = (state.modeChoice === 'auto') ? '跟随屏幕自动' : ('当前：' + (m === 'tablet' ? '平板' : '手机'));
  }

  function loadSettings() {
    syncModeBtns();
    api('/api/appinfo').then(function (r) {
      state.appinfo = r.data || {};
      renderSettingsNet();
    }).catch(function () {});
    api('/api/mobile/info').then(function (r) {
      var d = r.data || {};
      if (d.ok === false) {
        $('settingsNet').innerHTML = '<div class="empty">读取失败：' + esc(d.msg || ('HTTP ' + r.status)) + '</div>';
        return;
      }
      state.info = d;
      state.secure = !!d.secure_context;
      renderSettingsNet();
      renderSettingsSrc();
    }).catch(function (e) {
      $('settingsNet').innerHTML = '<div class="empty">读取失败：' + esc(e && e.message) + '</div>';
    });
    api('/api/config').then(function (r) {
      state.config = r.data || {};
      renderSettingsCfg();
    }).catch(function (e) {
      $('settingsCfg').innerHTML = '<div class="empty">读取失败：' + esc(e && e.message) + '</div>';
    });
    renderSettingsSrc();
    var st = $('settingsStat');
    if (st) st.textContent = state.appinfo && state.appinfo.version ? ('v' + state.appinfo.version) : '';
  }

  function kvRows(rows) {
    return '<div class="kv-list">' + (rows || []).map(function (r) {
      return '<div class="kv-r"><span class="k">' + esc(r[0]) + '</span>'
        + '<span class="v">' + esc(r[1] == null ? '' : r[1]) + '</span></div>';
    }).join('') + '</div>';
  }

  function certText() {
    var i = state.info || {};
    var h = i.https || {};
    var enabled = !!h.enabled || !!i.https_available;
    if (!enabled) return '未启用（手机麦克风需要 HTTPS）';
    var t = '已启用';
    if (h.cert_not_after) {
      t += ' · 证书有效至 ' + String(h.cert_not_after).slice(0, 10);
      if (h.cert_days_left != null) t += '（剩 ' + h.cert_days_left + ' 天）';
    } else if (h.cert) {
      t += ' · ' + h.cert;
    }
    if (h.error) t += ' · 异常：' + h.error;
    return t;
  }

  function renderSettingsNet() {
    var box = $('settingsNet');
    if (!box) return;
    var i = state.info || {};
    var h = i.https || {};
    var ap = state.appinfo || {};
    var ver = ap.version ? (ap.version + (ap.frozen ? '（打包版）' : '（开发版）')) : '—';
    var httpsUrl = h.page_url || (i.lan_ip && h.port
      ? ('https://' + i.lan_ip + ':' + h.port + '/mobile') : '');
    $('secState').textContent = state.secure ? '麦克风可用' : '麦克风受限';
    box.innerHTML = kvRows([
      ['当前版本', ver],
      ['局域网地址', i.base_url || (i.lan_ip ? ('http://' + i.lan_ip + ':' + i.port) : '—')],
      ['手机页面', i.page_url || i.http_page_url || '—'],
      ['HTTPS 地址', httpsUrl || '—'],
      ['证书状态', certText()],
      ['安全环境', state.secure ? '是（浏览器允许开麦）' : '否（浏览器会拦住麦克风）'],
      ['推流参数', (i.sample_rate || SR) + ' Hz · ' + (i.channels || 1) + ' ch · '
        + (i.format || 's16le') + ' · ' + (i.chunk_bytes || CHUNK_BYTES) + ' B/块'],
      ['电脑端', i.desktop_running ? '运行中' : '未运行']
    ]) + '<div class="dim" style="margin-top:8px">手机浏览器只在 HTTPS 或本机 localhost 下允许开麦克风；'
      + '用电脑内录 / 电脑麦克风时手机端无需授权。</div>';
  }

  function renderSettingsCfg() {
    var box = $('settingsCfg');
    if (!box) return;
    var c = state.config || {};
    var hasKey = !!c.has_key;
    var hasModel = !!(c.model || '').trim();
    var hasBase = !!(c.base_url || '').trim();
    var ok = hasKey && hasModel && hasBase;
    var hint = $('cfgState');
    if (hint) hint.textContent = ok ? '可用' : '不可用';
    var tips = [];
    if (!hasKey) tips.push('未配置 API 密钥：AI 点评与智能纠错不可用，仍可用本地规则点评。');
    if (!hasModel) tips.push('未填模型名。');
    if (!hasBase) tips.push('未填接口地址。');
    if (ok) tips.push('配置齐全，生成点评时会走该模型。');
    box.innerHTML = kvRows([
      ['模型', c.model || '—'],
      ['接口地址', c.base_url || '—'],
      ['密钥', c.api_key_masked || (hasKey ? '已配置' : '未配置')],
      ['实时纠错', c.enable_realtime ? '开' : '关'],
      ['精简纠错', c.enable_correction ? '开' : '关'],
      ['配置来源', c.source || c.label || c.file || '—'],
      ['数据目录', c.data_dir || '—']
    ]) + '<div class="' + (ok ? 'dim' : 'warn2') + '" style="margin-top:8px">' + esc(tips.join(' ')) + '</div>';
  }

  function renderSettingsSrc() {
    var box = $('settingsSrc');
    if (!box) return;
    var rows = ['pc_loopback', 'pc_mic', 'phone_mic'].map(function (s) {
      var on = state.source === s;
      return '<div class="kv-r"><span class="k">' + esc(SRC_LABEL[s] || s) + '</span>'
        + '<span class="v">' + (on ? '当前' : (s === 'phone_mic' ? '需 HTTPS' : '电脑端采集')) + '</span></div>';
    }).join('');
    var d = state.dev[state.source];
    var dn = '';
    if (state.source !== 'phone_mic') {
      var list = deviceList(state.source);
      list.forEach(function (x) { if (String(x.index) === String(d)) dn = '#' + x.index + ' ' + (x.name || ''); });
    }
    box.innerHTML = '<div class="kv-list">' + rows
      + (dn ? ('<div class="kv-r"><span class="k">当前设备</span><span class="v">' + esc(dn) + '</span></div>') : '')
      + '</div><div class="dim" style="margin-top:8px">本机记住上次选择：'
      + esc(SRC_LABEL[state.source] || '') + '</div>';
  }


  /* ------------------------------------------------------------ 上下文 / 面板 */
  function renderSub() {
    var bits = [];
    var i = state.info || {};
    if (i.lan_ip) bits.push('电脑 ' + i.lan_ip);
    var c = state.clip;
    if (c && c.ok) {
      if (c.student) bits.push('学生 ' + c.student);
      if (c.qtype) bits.push(c.qtype);
      if (c.segments != null) bits.push((c.segments || 0) + ' 句 / ' + (c.paragraphs || 0) + ' 段');
      if (c.has_review) bits.push('已有点评');
    } else {
      bits.push(i.https && i.https.enabled ? 'HTTPS 已开' : 'HTTPS 未开');
    }
    $('subLine').textContent = bits.join(' · ');
  }

  function renderClip() {
    var c = state.clip;
    if (!c || !c.ok) {
      $('clipBox').innerHTML = '<div class="dim">暂无会话信息。</div>';
      $('clipHint').textContent = '';
      return;
    }
    $('clipHint').textContent = c.running ? '录音中' : '已结束';
    $('clipBox').innerHTML = kvRows([
      ['学生', c.student || ''],
      ['题型', c.qtype || ''],
      ['题面', c.question || ''],
      ['逐字稿', (c.segments != null ? c.segments : '') + ' 句 / ' + (c.paragraphs != null ? c.paragraphs : '') + ' 段'],
      ['点评', c.has_review ? ('已有（' + (c.review_mode || '') + '）') : '未生成']
    ]);
  }

  function loadInfo() {
    return api('/api/mobile/info').then(function (r) {
      if (r.data && r.data.ok) {
        state.info = r.data;
        renderSub();
        renderSources();
        if (state.view === 'settings') renderSettingsNet();
      }
    }).catch(function () {});
  }

  function loadClip() {
    return api('/api/mobile/clip').then(function (r) {
      var d = r.data || {};
      if (!d.ok) return;
      state.clip = d;
      renderSub();
      renderClip();
      if (d.running && !state.running && d.source) {
        state.source = d.source;
        state.needPush = isNeedPush(d.source);
        state.running = true;
        state.startedAt = nowMs() - (Number(d.duration) || 0) * 1000;
        setRunning(true);
        renderSources();
      }
      if (d.has_review && !state.reviewText) {
        // 已有存档带点评时，先给个提示，点生成可重建
        $('reviewStat').textContent = '已有历史点评';
      }
    }).catch(function () {});
  }

  function loadState() {
    return api('/api/state').then(function (r) {
      var d = (r.data && r.data.session) || {};
      var segs = d.segments || [];
      var paras = d.paragraphs || [];
      state.reviewText = d.review || state.reviewText || '';
      state.reviewMode = d.review_mode || '';
      state.stuName = d.student_name || d.cur_student || '';
      state.question = d.question || '';
      state.qtype = d.qtype || '';
      if (!segs.length) { renderAll(); renderCtx(); renderParaReviewList(); return; }
      segs.forEach(function (s) { upsertSeg(s, s.pid); });
      paras.forEach(function (p) {
        var o = ensurePara(p.id, p.idx);
        o.title = p.title || '';
        o.label = p.plain_label || p.label || '';
        o.conf = p.plain_conf || p.conf || '';
        o.closed = !!p.closed;
        o.text = p.text || o.text || '';
        o.ai_note = p.ai_note || o.ai_note || '';
        o.review_mode = p.review_mode || o.review_mode || '';
        if (p.t_start != null) o.t_start = p.t_start;
        if (p.t_end != null) o.t_end = p.t_end;
        if (p.chars != null) o.chars = p.chars;
        (p.seg_ids || []).forEach(function (sid) {
          var s = state.segs[sid];
          if (s && o.segs.indexOf(sid) < 0) { o.segs.push(sid); s.pid = o.pid; }
        });
      });
      var order = paraKeyOrder();
      if (order.length) {
        var lp = state.paras[order[order.length - 1]];
        if (lp && lp.segs.length) state.lastSeg = (state.segs[lp.segs[lp.segs.length - 1]] || {}).text || state.lastSeg;
      }
      renderAll();
      renderCtx();
      renderParaReviewList();
      if (d.duration) state.duration = d.duration;
      $('clock').textContent = fmtClock(state.duration);
    }).catch(function () {});
  }

  function updateReviewCard() {
    if (state.reviewText) {
      setReview(state.reviewText, state.reviewMode === 'ai' ? 'AI 点评' : (state.reviewMode ? '本地规则点评' : '历史点评'), '');
    } else {
      $('reviewBody').innerHTML = '';
      $('reviewEmpty').hidden = false;
      $('reviewNav').hidden = true;
      $('reviewNav').innerHTML = '';
      $('reviewTitle').textContent = '整场点评';
      $('reviewMode').textContent = '';
      $('panelTitle').textContent = '整场点评';
      $('panelMode').textContent = '';
      $('panelBody').innerHTML = '';
      $('panelEmpty').hidden = false;
    }
  }

  function refreshReviewStat() {
    var ids = paraIds();
    var done = 0;
    ids.forEach(function (pid) {
      var st = paraReviewState(state.paras[pid]);
      if (st === 'done' || st === 'skip') done++;
    });
    var bits = [];
    if (state.reviewText) bits.push('整场 ' + (state.reviewMode === 'ai' ? 'AI' : '本地') + ' ' + state.reviewText.length + ' 字');
    if (ids.length) bits.push('段落 ' + done + '/' + ids.length);
    $('reviewStat').textContent = bits.join(' · ') || '未生成';
  }

  function refreshSecure() {
    var i = state.info || {};
    var https = i.https || {};
    var secure = (window.isSecureContext === true)
      || location.protocol === 'https:'
      || location.hostname === 'localhost'
      || location.hostname === '127.0.0.1';
    state.secure = !!secure && !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);

    var pill = $('pillSecure');
    if (state.secure) {
      pill.textContent = '麦克风可用';
      pill.className = 'pill';
    } else {
      pill.textContent = '麦克风不可用';
      pill.className = 'pill bad';
    }
    if (!state.secure) {
      renderSecureAlert();
      // 首次检测到「开不了麦」时自动展开指引卡，避免用户只看到顶部红标签却找不到升级入口
      if (!state.secAlertShown) {
        state.secAlertShown = true;
        $('secAlert').hidden = false;
      }
    } else {
      $('secAlert').hidden = true;
    }
    if (state.view === 'settings') renderSettingsNet();
    if (!state.secure) {
      $('btnStart').dataset.blocked = '1';
    } else {
      delete $('btnStart').dataset.blocked;
    }
  }

  function renderSecureAlert() {
    var i = state.info || {};
    var https = i.https || {};
    var httpsUrl = https.url || '';
    if (!httpsUrl && https.enabled && i.lan_ip) {
      httpsUrl = 'https://' + i.lan_ip + ':' + (https.port || 8443) + '/mobile';
    }
    // HTTPS 未启动时不给假地址，避免点了打不开
    if (!httpsUrl) httpsUrl = '';
    $('secTitle').textContent = '当前页面拿不到手机麦克风';
    $('secBody').innerHTML = '浏览器只允许在 <b>HTTPS</b> 或 localhost 页面使用麦克风。当前地址：<br><span class="mono" style="display:inline-block;padding:4px 8px;margin-top:4px">'
      + esc(location.href) + '</span>'
      + (httpsUrl ? '<br>请改用：<br><span class="mono" style="display:inline-block;padding:4px 8px;margin-top:4px">' + esc(httpsUrl) + '</span>' : '');
    $('secSteps').innerHTML = ''
      + '<li>手机与电脑连同一个网络</li>'
      + '<li>手机浏览器打开 HTTPS 地址（首次会提示证书风险，选择继续访问）</li>'
      + '<li>重新允许麦克风权限后回到本页</li>'
      + '<li>若只想用电脑的声音，也可以把声音来源切成「电脑内录」或「电脑麦克风」，此时手机无需开麦</li>';
    $('btnCopyHttps').dataset.url = httpsUrl || '';
  }

  /* ------------------------------------------------------------ 事件绑定 */
  function bindUI() {
    $('btnMenu').addEventListener('click', toggleSide);
    $('mask').addEventListener('click', closeSide);
    $('btnHelp').addEventListener('click', function () {
      var a = $('secAlert');
      a.hidden = !a.hidden;
      if (!a.hidden) renderSecureAlert();
    });
    $('btnMode').addEventListener('click', function () {
      var next = (state.mode === 'phone') ? 'tablet' : 'phone';
      applyMode(next, true);
      toast('已切到' + (next === 'phone' ? '手机' : '平板') + '形态，已记住选择', 2200);
    });

    $$('.nav-item').forEach(function (b) {
      b.addEventListener('click', function () { setView(b.getAttribute('data-nav')); });
    });

    $$('.segbtn').forEach(function (b) {
      b.addEventListener('click', function () {
        var m = b.getAttribute('data-mode');
        applyMode(m, true);
        toast('已切到' + (m === 'phone' ? '手机' : '平板') + '形态', 2000);
        renderSettingsSrc();
      });
    });
    $('btnModeAuto').addEventListener('click', function () {
      applyMode('auto', true);
      toast('已改为跟随屏幕：宽 ≥ 900px 用平板形态');
      renderSettingsSrc();
    });

    $$('#sourceGrid .src-btn').forEach(function (b) {
      b.addEventListener('click', function () { pickSource(b.getAttribute('data-src')); });
    });
    $('deviceSel').addEventListener('change', function () {
      state.dev[state.source] = $('deviceSel').value;
      saveDev();
      renderSettingsSrc();
    });
    $('btnReloadDev').addEventListener('click', function () {
      loadInfo().then(function () {
        renderDeviceSel();
        toast('设备清单已刷新');
      });
    });

    $('btnStart').addEventListener('click', doStart);
    $('btnStop').addEventListener('click', doStop);
    $('btnReview').addEventListener('click', doReview);
    $('btnReview2').addEventListener('click', doReview);

    $('btnCopyHttps').addEventListener('click', function () {
      copyText($('btnCopyHttps').dataset.url || '');
    });
    $('btnOpenHttps').addEventListener('click', function () {
      var u = $('btnCopyHttps').dataset.url || '';
      if (!u) { toast('当前没有可用的 HTTPS 地址，请检查电脑端服务', 3200); return; }
      if (!confirm('即将打开安全地址：\n' + u + '\n\n首次打开会提示证书不受信任：安卓选「高级 → 继续前往」，iPhone 选「显示详细信息 → 访问此网站」。继续？')) return;
      location.href = u;
    });
    $('btnCopyHttps2').addEventListener('click', function () {
      var i = state.info || {};
      var https = i.https || {};
      var u = https.url || (i.lan_ip && https.port ? ('https://' + i.lan_ip + ':' + https.port + '/mobile') : '');
      copyText(u);
    });
    $('btnCopyPage').addEventListener('click', function () {
      var i = state.info || {};
      copyText(i.page_url || (i.lan_ip ? ('http://' + i.lan_ip + ':' + (i.port || 7799) + '/mobile') : ''));
    });
    $('btnRetry').addEventListener('click', function () {
      Promise.all([loadInfo(), loadClip()]).then(function () {
        refreshSecure();
        toast(state.secure ? '麦克风可用' : '仍不可用，请按指引操作', 2600);
      });
    });

    // 句子点击 → 标记抽屉；段头「点评本段」→ 单段点评
    ['script', 'scriptFull'].forEach(function (boxId) {
      $(boxId).addEventListener('click', function (e) {
        var act = e.target.closest ? e.target.closest('.para-act[data-act="pr"]') : null;
        if (act) {
          e.preventDefault();
          e.stopPropagation();
          var apid = act.getAttribute('data-pid');
          var ap = apid ? state.paras[apid] : null;
          if (apid) paraReview(apid, !!(ap && ap.ai_note));
          return;
        }
        var btn = e.target.closest ? e.target.closest('.sent') : null;
        if (!btn) return;
        var id = btn.getAttribute('data-id');
        if (id) openSheet(id);
      });
    });

    // 段落点评列表
    $('paraRevList').addEventListener('click', function (e) {
      var act = e.target.closest ? e.target.closest('.para-act[data-act="pr"]') : null;
      if (act) {
        var pid = act.getAttribute('data-pid');
        var p = state.paras[pid];
        if (pid) paraReview(pid, !!(p && p.ai_note));
        return;
      }
      var card = e.target.closest ? e.target.closest('[data-pcard]') : null;
      if (card) gotoPara(card.getAttribute('data-pcard'));
    });
    $('btnParaRevMiss').addEventListener('click', reviewMissing);
    $('btnParaRevRefresh').addEventListener('click', function () {
      var n = Object.keys(state.pvBusy).length;
      renderParaReviewList();
      refreshReviewStat();
      toast('段落状态已刷新' + (n ? ('，' + n + ' 段仍在生成中') : ''));
    });

    // 整场点评三段导航
    $('reviewNav').addEventListener('click', function (e) {
      var b = e.target.closest ? e.target.closest('button[data-rsec]') : null;
      if (!b) return;
      var idx = b.getAttribute('data-rsec');
      $$('#reviewNav button').forEach(function (x) { x.classList.toggle('on', x === b); });
      var sec = document.querySelector('#reviewBody .rev-sec[data-rsecidx="' + idx + '"]');
      if (!sec) return;
      try { sec.scrollIntoView({ block: 'start', behavior: 'smooth' }); }
      catch (e2) { try { sec.scrollIntoView(); } catch (e3) {} }
      sec.classList.remove('flash');
      void sec.offsetWidth;
      sec.classList.add('flash');
    });

    // 标记页
    $('markFilter').addEventListener('click', function (e) {
      var b = e.target.closest ? e.target.closest('button[data-mf]') : null;
      if (!b) return;
      var k = b.getAttribute('data-mf');
      state.markFilter = k || null;
      renderMarks();
    });
    $('marksList').addEventListener('click', function (e) {
      var del = e.target.closest ? e.target.closest('[data-del]') : null;
      if (del) {
        e.preventDefault();
        e.stopPropagation();
        var did = del.getAttribute('data-del');
        var ds = state.segs[did] || {};
        var body = String(ds.text || '').slice(0, 40) + (String(ds.text || '').length > 40 ? '…' : '');
        var okDel = window.confirm('删除这条标记？\n将清空这句的亮点 / 硬伤 / 对策缺失 / 口头禅与备注：\n' + body);
        if (okDel) deleteMark(did);
        return;
      }
      var ed = e.target.closest ? e.target.closest('[data-edit]') : null;
      if (ed) {
        e.preventDefault();
        e.stopPropagation();
        openSheet(ed.getAttribute('data-edit'));
        return;
      }
      var b = e.target.closest ? e.target.closest('[data-mid]') : null;
      if (!b) return;
      gotoSeg(b.getAttribute('data-mid'));
    });

    // 抽屉
    $('tagRow').addEventListener('click', function (e) {
      var b = e.target.closest ? e.target.closest('button[data-tag]') : null;
      if (!b) return;
      if (b.getAttribute('data-note')) { $('noteInput').focus(); return; }
      var name = b.getAttribute('data-tag');
      state.sheetTags[name] = !state.sheetTags[name];
      b.classList.toggle('on', !!state.sheetTags[name]);
    });
    $('noteInput').addEventListener('input', function () {
      var nb = document.querySelector('#tagRow button[data-note]');
      if (nb) nb.classList.toggle('on', !!($('noteInput').value || '').trim());
    });
    $('btnSheetClose').addEventListener('click', closeSheet);
    $('btnTagCancel').addEventListener('click', closeSheet);
    $('sheetMask').addEventListener('click', closeSheet);
    $('btnTagSave').addEventListener('click', saveTags);

    // 逐字稿页
    $('btnScriptReload').addEventListener('click', function () {
      loadState().then(function () { toast('已刷新'); });
    });
    $('btnScriptCopy').addEventListener('click', function () {
      var txt = Object.keys(state.segs).map(function (k) { return state.segs[k]; })
        .sort(function (a, b) {
          var pa = state.paras[a.pid], pb = state.paras[b.pid];
          return (pa ? pa.idx : 0) - (pb ? pb.idx : 0);
        }).map(function (s) { return s.text || ''; }).join('');
      copyText(txt);
    });

    // 点评页
    $('btnReviewCopy').addEventListener('click', function () {
      copyText(state.reviewText || ($('reviewBody').innerText || ''));
    });

    // 精简页
    $('btnPlainReload').addEventListener('click', loadPlain);
    $('btnPlainStage').addEventListener('click', function () { setPlainStage(!plainStageOn()); });
    $('btnPlainCopy').addEventListener('click', plainCopyAll);
    $('plainBody').addEventListener('click', function (e) {
      var b = e.target.closest ? e.target.closest('[data-pgcopy]') : null;
      if (!b) return;
      plainCopyGroup(b.getAttribute('data-pgcopy'));
    });

    // 精简要点
    $('btnPtsRun').addEventListener('click', ptsLoad);
    $('btnPtsCopyAll').addEventListener('click', ptsCopyAll);
    $('ptsAi').addEventListener('change', function () { ptsSetAi(!!$('ptsAi').checked); });
    ptsSyncAi();

    // 听记页：右上「切换学生」→ 展开学生胶囊列表，点名字直接切换本场归属
    $('btnCtxStu').addEventListener('click', function (e) {
      e.preventDefault();
      toggleCtxStuPick();
    });
    $('ctxStuPick').addEventListener('click', function (e) {
      var go = e.target.closest ? e.target.closest('[data-csgo]') : null;
      if (go) { closeCtxStuPick(); setView('students'); return; }
      var chip = e.target.closest ? e.target.closest('[data-csid]') : null;
      if (!chip) return;
      var sid = chip.getAttribute('data-csid');
      var cur = stuCur();
      if (cur && String(cur.id) === String(sid)) { closeCtxStuPick(); toast('已经是当前学生'); return; }
      switchStudent(sid);
    });

    // 学生页：点学生卡片切换，卡片内按钮负责改名/删除
    $('studentsList').addEventListener('click', function (e) {
      var act = e.target.closest ? e.target.closest('[data-sact]') : null;
      if (act) {
        e.preventDefault();
        e.stopPropagation();
        var what = act.getAttribute('data-sact');
        var sid = act.getAttribute('data-sid');
        if (what === 'rename') { stuRename(sid); return; }
        if (what === 'del') { delStudent(sid); return; }
        if (what === 'rsave') { saveStuRename(sid); return; }
        if (what === 'rcancel') { state.stuEdit = ''; renderStudents(); return; }
        return;
      }
      if (e.target && e.target.getAttribute && e.target.getAttribute('data-sin') != null) return;
      var it = e.target.closest ? e.target.closest('[data-sid]') : null;
      if (!it) return;
      switchStudent(it.getAttribute('data-sid'));
    });
    $('studentsList').addEventListener('keydown', function (e) {
      var t = e.target;
      if (!t || !t.getAttribute) return;
      var sid = t.getAttribute('data-sin');
      if (sid == null) return;
      if (e.key === 'Enter') { e.preventDefault(); saveStuRename(sid); }
      if (e.key === 'Escape') { state.stuEdit = ''; renderStudents(); }
    });
    $('btnStuAdd').addEventListener('click', addStudent);
    $('stuName').addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); addStudent(); }
    });
    $('btnStuCompare').addEventListener('click', loadStuCompare);

    // 题库页
    $('btnBankSearch').addEventListener('click', searchBank);
    $('bankQ').addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); searchBank(); }
    });
    $('btnBankBack').addEventListener('click', function () {
      state.bankOpen = null;
      state.bankSearch = null;
      state.bankOptOnly = false;
      $('bankQ').value = '';
      $('bankHit').textContent = '';
      renderBank();
    });
    if ($('btnBankMgr')) $('btnBankMgr').addEventListener('click', bqOpenMgr);
    if ($('btnBankNewQ')) $('btnBankNewQ').addEventListener('click', bqOpenNew);
    if ($('btnBankReload')) $('btnBankReload').addEventListener('click', function () {
      state.bank = null;
      state.bankLoading = '';
      if (state.bankOpen != null) bqReloadItems(); else loadBank();
      toast('已刷新题库');
    });
    if ($('btnBankOptOnly')) $('btnBankOptOnly').addEventListener('click', function () {
      state.bankOptOnly = !state.bankOptOnly;
      renderBankItems();
    });
    if ($('btnBankSelAll')) $('btnBankSelAll').addEventListener('click', function () {
      var b = bankOpenObj();
      if (!b) return;
      var items = b.items || [];
      var sel = bqSelSet();
      bankItemsShow().forEach(function (it) {
        var gi = items.indexOf(it);
        if (gi >= 0) sel[gi] = 1;
      });
      renderBankItems();
    });
    if ($('btnBankSelNone')) $('btnBankSelNone').addEventListener('click', function () {
      state.bankSel = {};
      renderBankItems();
    });
    if ($('btnBankOptSel')) $('btnBankOptSel').addEventListener('click', function () {
      bqDoOptimize(bqOn());
    });
    if ($('btnBankOptAll')) $('btnBankOptAll').addEventListener('click', function () {
      var b = bankOpenObj();
      if (!b) { toast('请先打开一个题库'); return; }
      var n = (b.items || []).length;
      if (!n) { toast('题库是空的'); return; }
      if (!window.confirm('优化整个题库的 ' + n + ' 道题？\n\nAI 会一题一题串行处理，题多时耗时较长，中途可点「停止」。')) return;
      var all = [];
      for (var i = 0; i < n; i++) all.push(i);
      bqDoOptimize(all);
    });
    if ($('btnBankOptStop')) $('btnBankOptStop').addEventListener('click', bqOptStop);
    if ($('bankOptBarWrap')) {
      $('bankOptBarWrap').addEventListener('click', bqLiveOpen);
      $('bankOptBarWrap').addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); bqLiveOpen(); }
      });
    }

    $('bankList').addEventListener('click', function (e) {
      var t = e.target.closest ? e.target.closest.bind(e.target) : null;
      var g = function (sel) { return t ? t(sel) : null; };
      // 题库列表：重命名 / 删除优先于「打开」
      var rn = g('[data-brename]');
      if (rn) {
        e.preventDefault(); e.stopPropagation();
        var list = bankListArr();
        var file = rn.getAttribute('data-brename');
        for (var i = 0; i < list.length; i++) {
          if (String(bankIndexOf(list[i])) === String(file)) { bqRenameForm(i); break; }
        }
        return;
      }
      var dp = g('[data-bdrop]');
      if (dp) {
        e.preventDefault(); e.stopPropagation();
        var l2 = bankListArr();
        var f2 = dp.getAttribute('data-bdrop');
        for (var j = 0; j < l2.length; j++) {
          if (String(bankIndexOf(l2[j])) === String(f2)) { bqDropBank(j); break; }
        }
        return;
      }
      var bo = g('[data-bopen]');
      if (bo) {
        state.bankOpen = bo.getAttribute('data-bopen');
        state.bankSearch = null;
        state.bankOptOnly = false;
        bqSelSet();
        renderBank();
        return;
      }
      // 题库内单题操作
      var gi = null;
      var grab = function (attr) {
        var el = g('[' + attr + ']');
        if (!el) return null;
        return parseInt(el.getAttribute(attr), 10);
      };
      gi = grab('data-bset');
      if (gi != null) { bankSetCurFrom(gi); return; }
      gi = grab('data-bdet');
      if (gi != null) { bqOpenDetail(gi); return; }
      gi = grab('data-bedit');
      if (gi != null) { bqOpenEdit(gi); return; }
      gi = grab('data-bopt');
      if (gi != null) { bqDoOptimize([gi]); return; }
      gi = grab('data-bcmp');
      if (gi != null) { bqOpenDiff(gi); return; }
      gi = grab('data-bdel');
      if (gi != null) { bqDeleteItem(gi); return; }
      // 搜索页：设为当前 / 打开所在题库
      var bs = grab('data-bs');
      if (bs != null) {
        var hit = (((state.bankSearch || {}).d || {}).items || [])[bs];
        if (hit) bankSetCur({ question: String(hit.question || ''), qtype: hit.qtype || '' });
        return;
      }
      var go = grab('data-bsgo');
      if (go != null) {
        var h2 = (((state.bankSearch || {}).d || {}).items || [])[go];
        if (!h2) return;
        state.bankOpen = h2.file || h2.bank || state.bankOpen;
        state.bankSearch = null;
        $('bankQ').value = '';
        bqSelSet();
        renderBank();
        return;
      }
    });

    // 勾选框单独处理（用 change 才能拿到切换后的状态）
    $('bankList').addEventListener('change', function (e) {
      var cb = e.target;
      if (!cb || cb.tagName !== 'INPUT' || !cb.hasAttribute('data-bsel')) return;
      var gi = parseInt(cb.getAttribute('data-bsel'), 10);
      if (isNaN(gi)) return;
      var sel = bqSelSet();
      if (cb.checked) sel[gi] = 1; else delete sel[gi];
      bqOptSync();
    });

    // 通用面板：关闭 / 遮罩 / 面板内按钮 / 面板内动作 / 实时输出条目展开
    if ($('btnMSheetClose')) $('btnMSheetClose').addEventListener('click', function () {
      bqCloseLive();
      mSheetClose();
    });
    if ($('mSheetMask')) $('mSheetMask').addEventListener('click', function () {
      bqCloseLive();
      mSheetClose();
    });
    if ($('mSheetOps')) $('mSheetOps').addEventListener('click', function (e) {
      var btn = e.target.closest ? e.target.closest('[data-mop]') : null;
      if (!btn) return;
      mSheetOp(parseInt(btn.getAttribute('data-mop'), 10));
    });
    if ($('mSheetBody')) $('mSheetBody').addEventListener('click', function (e) {
      var cl = e.target.closest ? e.target.closest.bind(e.target) : null;
      var act = cl ? cl('[data-mact]') : null;
      if (act) {
        var a = act.getAttribute('data-mact');
        var idx = parseInt(act.getAttribute('data-mi'), 10);
        if (a === 'newbank') { bqNewBank(); return; }
        if (a === 'open') {
          var bb = bankListArr()[idx];
          if (bb) {
            state.bankOpen = bankIndexOf(bb);
            state.bankSearch = null;
            state.bankOptOnly = false;
            bqSelSet();
            mSheetClose();
            renderBank();
          }
          return;
        }
        if (a === 'rename') { bqRenameForm(idx); return; }
        if (a === 'drop') { bqDropBank(idx); return; }
        return;
      }
      var hd = cl ? cl('.ml-hd') : null;
      if (hd) {
        var row = hd.parentNode;
        var body = row.querySelector('.ml-bd');
        var mi = parseInt(row.getAttribute('data-mi'), 10);
        if (body) {
          body.hidden = !body.hidden;
          var o = bqOpt();
          if (!o.liveOpen) o.liveOpen = {};
          if (body.hidden) delete o.liveOpen[mi]; else o.liveOpen[mi] = 1;
        }
      }
    });


    // 历史页
    $('btnHistoryReload').addEventListener('click', loadRecords);
    $('histStuSel').addEventListener('change', loadRecords);
    $('historyList').addEventListener('click', function (e) {
      var rl = e.target.closest ? e.target.closest('[data-recload]') : null;
      if (rl) {
        e.preventDefault();
        e.stopPropagation();
        histLoadFile(rl.getAttribute('data-recload'));
        return;
      }
      var b = e.target.closest ? e.target.closest('[data-rec]') : null;
      if (!b) return;
      var host2 = b.closest ? b.closest('.item') : null;
      var t2 = host2 ? host2.querySelector('.item-t') : null;
      openRecord(b.getAttribute('data-rec'), t2 ? t2.textContent : '');
    });
    $('historyDetail').addEventListener('click', function (e) {
      var hs = e.target.closest ? e.target.closest('[data-hstu]') : null;
      if (hs) {
        state.histStu = hs.getAttribute('data-hstu');
        renderHistDetail();
        return;
      }
      var ha = e.target.closest ? e.target.closest('[data-hact]') : null;
      if (!ha) return;
      var what = ha.getAttribute('data-hact');
      if (what === 'loadcur') { histLoadCur(); return; }
      if (what === 'copyscript') { copyText(histSegText() || '（该场没有逐字稿）'); return; }
      if (what === 'copyreview') { copyText((histStudent().review || (state.hist || {}).review || '') || '（该场没有点评）'); return; }
    });
    $('btnHistoryClose').addEventListener('click', function () {
      $('historyDetailCard').hidden = true;
    });

    // 设置页
    $('settingsSrc').addEventListener('click', function (e) {
      var b = e.target.closest ? e.target.closest('[data-picksrc]') : null;
      if (!b) return;
      pickSource(b.getAttribute('data-picksrc'));
      renderSettingsSrc();
    });

    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape') return;
      closeSide();
      if (!$('sheet').hidden) closeSheet();
    });

    window.addEventListener('resize', onResize);
    window.addEventListener('orientationchange', function () { setTimeout(onResize, 220); });
    window.addEventListener('beforeunload', function () {
      if (state.running) {
        try { navigator.sendBeacon('/api/mobile/session/stop', ''); } catch (e) {}
      }
    });
  }

  /* ------------------------------------------------------------ 启动 */
  function startTimers() {
    setInterval(tick, 120);
    setInterval(pollStatus, 4000);
  }

  document.addEventListener('DOMContentLoaded', function () {
    initMode();
    initSourceFromStore();
    bindUI();
    setView('live');
    buildStats();
    loadInfo().then(function () {
      refreshSecure();
      renderSources();
      return loadState();
    }).then(function () {
      return loadClip();
    }).then(function () {
      openSSE();
      startTimers();
      setRunning(state.running);
      renderSettingsSrc();
      renderSettingsNet();
      updateScriptStat();
    }).catch(function () {
      openSSE();
      startTimers();
    });

    /* ---------------- 开源许可（设置页入口） ---------------- */
    var MOSS = null, mossBound = false;
    function mossRender() {
      var j = MOSS;
      if (!j) return;
      var items = j.items || [];
      var h = '<div class="dim" style="margin-bottom:8px">' + esc(j.app) + ' v' + esc(j.version) +
        '（' + esc(j.author) + '）共使用 ' + items.length +
        ' 个第三方开源组件，均遵循各自许可证分发；清单与随包 THIRD_PARTY_NOTICES.md 一致。</div>';
      h += '<div class="moss-list">';
      items.forEach(function (it) {
        var agpl = /AGPL/i.test(it.license || '');
        h += '<div class="moss-row' + (agpl ? ' agpl' : '') + '">' +
          '<div class="moss-n"><b>' + esc(it.name) + '</b><span class="moss-v">' + esc(it.ver) + '</span></div>' +
          '<div class="moss-l">' + esc(it.license) + '</div>' +
          '<div class="moss-u">' + esc(it.use) + '</div>' +
          '</div>';
      });
      h += '</div><div class="moss-licbar">';
      (j.licenses || []).forEach(function (l) {
        h += '<button type="button" class="mini" data-oss-lic="' + esc(l.spdx) + '">' + esc(l.spdx) + '</button>';
      });
      h += '</div><pre class="moss-pre" id="mossPre">点上方许可证查看全文</pre>';
      $('mSheetBody').innerHTML = h;
    }
    function mossBind() {
      if (mossBound) return;
      mossBound = true;
      $('mSheetBody').addEventListener('click', function (e) {
        var t = e.target.closest ? e.target.closest('[data-oss-lic]') : null;
        if (!t) return;
        var spdx = t.getAttribute('data-oss-lic');
        var pre = $('mossPre');
        if (!pre) return;
        pre.textContent = '正在读取 ' + spdx + ' 全文…';
        fetch('/api/oss/license/' + encodeURIComponent(spdx)).then(function (r) { return r.json(); })
          .then(function (j) {
            var p = $('mossPre');
            if (!p) return;
            p.textContent = (j && j.ok) ? j.text : ('读取失败：' + ((j && j.msg) || ''));
          }).catch(function (err) {
            var p = $('mossPre');
            if (p) p.textContent = '读取失败：' + (err && err.message ? err.message : err);
          });
      });
    }
    function mossOpen() {
      mSheetOpen('开源许可', '<div class="dim">正在读取…</div>', [{ text: '关闭' }]);
      mossBind();
      fetch('/api/oss').then(function (r) { return r.json(); }).then(function (j) {
        if (!j || !j.ok) throw new Error((j && j.msg) || '读取失败');
        MOSS = j;
        if (mSheetIsOpen() && $('mSheetTitle').textContent === '开源许可') mossRender();
      }).catch(function (e) {
        var b = $('mSheetBody');
        if (b && mSheetIsOpen()) b.innerHTML = '<div class="dim">读取失败：' + esc(e && e.message ? e.message : String(e)) + '</div>';
      });
    }
    if ($('btnMoss')) $('btnMoss').addEventListener('click', mossOpen);

    // ---- 项目地址 & 检查更新 ----
    var MPROJ = 'https://github.com/lyckeven/InterviewLiveNote';
    function mOpenUrl(url) {
      fetch('/api/open/url', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: url })
      }).then(function (r) { return r.json(); }).then(function (j) {
        if (j && j.ok) return;
        window.open(url, '_blank', 'noopener');
      }).catch(function () {
        try { window.open(url, '_blank', 'noopener'); } catch (e) { toast('请手动访问 ' + url); }
      });
    }
    if ($('mlinkProject')) {
      $('mlinkProject').addEventListener('click', function (e) {
        e.preventDefault();
        mOpenUrl(MPROJ);
      });
    }
    var mUpdOut = $('settingsUpd');
    function mUpdShow(html, cls) {
      if (!mUpdOut) return;
      mUpdOut.hidden = false;
      mUpdOut.className = 'dim upd-out' + (cls ? ' ' + cls : '');
      mUpdOut.innerHTML = html;
    }
    function mUpdLink(url, text) {
      var a = document.createElement('a');
      a.href = '#';
      a.className = 'lnk';
      a.textContent = text;
      a.addEventListener('click', function (e) { e.preventDefault(); mOpenUrl(url); });
      return a;
    }
    if ($('btnMCheck')) {
      var mChecking = false;
      $('btnMCheck').addEventListener('click', function () {
        if (mChecking) return;
        mChecking = true;
        var btn = $('btnMCheck');
        var old = btn.textContent;
        btn.disabled = true;
        btn.textContent = '检查中…';
        mUpdShow('正在向 GitHub 查询最新版本…', '');
        fetch('/api/update/check', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (d) {
          if (!d || d.ok !== true) {
            mUpdShow('检查失败：' + esc((d && d.msg) || '网络不可用或接口异常'), 'bad');
            return;
          }
          if (d.has_update) {
            mUpdShow('发现新版本 <b>v' + esc(d.latest) + '</b>（当前 v' + esc(d.current) + '）', 'newer');
            var row = document.createElement('div');
            row.appendChild(mUpdLink(d.url || (MPROJ + '/releases/latest'), '前往 Releases 下载'));
            mUpdOut.appendChild(row);
          } else {
            mUpdShow('已是最新版本（当前 v' + esc(d.current) + '，GitHub 最新 v' + esc(d.latest) + '）', '');
          }
          var foot = document.createElement('div');
          foot.appendChild(mUpdLink(MPROJ, '打开项目主页'));
          mUpdOut.appendChild(foot);
        }).catch(function (e) {
          mUpdShow('检查失败：' + esc(e && e.message ? e.message : String(e)), 'bad');
        }).then(function () {
          btn.disabled = false;
          btn.textContent = old;
          mChecking = false;
        });
      });
    }
  });
})();
