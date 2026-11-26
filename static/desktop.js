/* =====================================================================
   面试点评器 v2.1.0 · 慕夕制作
   desktop.js —— 内嵌 WebView 桌面外壳适配层
   · 只有运行在桌面壳（pywebview）内才显示自定义标题栏与边角拖拽区
   · 浏览器直接访问时自动降级为普通网页（不显示窗口按钮）
   ===================================================================== */
(function () {
  'use strict';

  var state = { desktop: false, version: '', author: '', maximized: false };

  function api() {
    return (window.pywebview && window.pywebview.api) ? window.pywebview.api : null;
  }

  function byId(id) { return document.getElementById(id); }

  /* ---------------- 最大化状态同步 ---------------- */
  function applyMax(maxed) {
    if (state.maximized === maxed && document.body.classList.contains('win-max') === maxed) return;
    state.maximized = maxed;
    document.body.classList.toggle('win-max', maxed);
    var b = byId('btnWinMax');
    if (b) {
      b.title = maxed ? '还原' : '最大化';
      b.classList.toggle('is-max', maxed);
    }
  }

  function geomMax() {
    try {
      var sw = (window.screen && (screen.availWidth || screen.width)) || 0;
      applyMax(sw > 0 && (window.outerWidth || window.innerWidth) >= sw - 2);
    } catch (e) {}
  }

  function syncMaxState() {
    var a = api();
    if (a && a.win_state) {
      try {
        var r = a.win_state();
        if (r && r.then) {
          r.then(function (st) { applyMax(st === 'max'); }).catch(geomMax);
        } else if (typeof r === 'string') {
          applyMax(r === 'max');
        } else {
          geomMax();
        }
        return;
      } catch (e) {}
    }
    geomMax();
  }

  /* ---------------- 拖动与缩放（交给系统原生处理） ---------------- */
  function clearSel() {
    try {
      var s = window.getSelection && window.getSelection();
      if (s && s.removeAllRanges) s.removeAllRanges();
    } catch (e) {}
  }

  function endWinOp() {
    document.body.classList.remove('win-dragging');
    document.body.classList.remove('win-resizing');
    try { document.body.removeAttribute('data-winop'); } catch (e) {}
    window.removeEventListener('mousemove', touchOp);
    window.removeEventListener('mouseup', endWinOp);
    window.removeEventListener('blur', endWinOp);
    clearSel();
  }

  /* 给 app.js 的状态类巡检一个「活着」的心跳：鼠标还在动就续期，
     真正卡死（收不到 mouseup 且不再有 mousemove）时由巡检在数秒后自行收尾 */
  function touchOp() {
    try { document.body.setAttribute('data-winop', String(Date.now())); } catch (e) {}
  }

  function markOp(cls) {
    document.body.classList.add(cls);
    touchOp();
    /* 拖动 / 缩放期间全程禁止选中文字，松开左键或移出窗口时统一收尾 */
    window.addEventListener('mousemove', touchOp);
    window.addEventListener('mouseup', endWinOp);
    window.addEventListener('blur', endWinOp);
    setTimeout(endWinOp, 120000);   // 兜底，避免状态类残留
  }

  function startDrag(ev) {
    if (ev.button !== 0) return;
    if (!state.desktop) return;
    var t = ev.target;
    if (t && t.closest && t.closest('.atb-btn')) return;
    ev.preventDefault();
    ev.stopPropagation();
    clearSel();
    markOp('win-dragging');
    try { api().win_drag(); } catch (e) {}
  }

  function startResize(ev) {
    if (ev.button !== 0) return;
    if (!state.desktop || state.maximized) return;
    var edge = this.getAttribute('data-edge');
    if (!edge) return;
    ev.preventDefault();
    ev.stopPropagation();
    clearSel();
    markOp('win-resizing');
    try { api().win_resize(edge); } catch (e) {}
  }

  /* ---------------- 窗口按钮 ---------------- */
  function bindButtons() {
    var bMin = byId('btnWinMin'), bMax = byId('btnWinMax'), bClose = byId('btnWinClose');
    if (bMin) bMin.addEventListener('click', function () { try { api().win_min(); } catch (e) {} });
    if (bMax) bMax.addEventListener('click', function () {
      try {
        var r = api().win_max();
        if (r && r.then) {
          r.then(function (st) {
            if (typeof st === 'string') applyMax(st === 'max');
            setTimeout(syncMaxState, 80);
          }).catch(function () { setTimeout(syncMaxState, 80); });
        } else {
          setTimeout(syncMaxState, 80);
        }
      } catch (e) {}
    });
    if (bClose) bClose.addEventListener('click', function () {
      try {
        var r = api().win_close();
        if (r && r.then) r.catch(function () {});
      } catch (e) {}
    });
  }

  function bindDragResize() {
    var bar = byId('appTitleBar');
    if (bar) {
      bar.addEventListener('mousedown', startDrag);
      bar.addEventListener('dblclick', function (ev) {
        if (ev.target && ev.target.closest && ev.target.closest('.atb-btn')) return;
        if (!state.desktop) return;
        try {
          var r = api().win_max();
          if (r && r.then) r.then(function (st) {
            if (typeof st === 'string') applyMax(st === 'max');
            setTimeout(syncMaxState, 80);
          }).catch(function () {});
        } catch (e) {}
      });
    }
    var handles = document.querySelectorAll('#rzLayer .rz');
    for (var i = 0; i < handles.length; i++) {
      handles[i].addEventListener('mousedown', startResize);
    }
    window.addEventListener('resize', syncMaxState);
    // 系统级最大化/还原不触发 JS resize（Win32 ShowWindow），用轻量轮询兜底
    setInterval(function () { if (state.desktop) syncMaxState(); }, 700);
  }

  /* ---------------- 桌面模式激活 ---------------- */
  function activate() {
    if (state.desktop) return;
    state.desktop = true;
    document.body.classList.add('desktop-app');
    document.documentElement.classList.add('desktop-app');
    window.__ILN_DESKTOP = state;
    var sub = document.querySelector('.atb-sub');
    try {
      var p = api().app_info();
      if (p && p.then) {
        p.then(function (info) {
          if (!info) return;
          state.version = info.version || '';
          state.author = info.author || '';
          if (sub && state.version) sub.textContent = 'v' + state.version + ' · ' + state.author;
          // 顶栏版本号与关于信息同步
          var ver = document.querySelector('.brand .ver');
          if (ver && state.version) ver.textContent = 'v' + state.version + ' · ' + state.author;
          document.title = (info.name || '面试点评器') + ' · ' + (state.author || '慕夕制作');
        }).catch(function () {});
      }
    } catch (e) {}
    syncMaxState();
  }

  function boot() {
    bindButtons();
    bindDragResize();
    if (window.pywebview && window.pywebview.api) {
      activate();
    } else {
      // pywebview 注入较晚，等待事件；超时则按浏览器模式处理
      window.addEventListener('pywebviewready', activate);
      var tries = 0;
      var timer = setInterval(function () {
        tries++;
        if (window.pywebview && window.pywebview.api) {
          clearInterval(timer);
          activate();
        } else if (tries > 40) {
          clearInterval(timer);
          document.body.classList.add('web-mode');
        }
      }, 100);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
