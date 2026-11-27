/* 面试实时听记与点评助手 - 前端逻辑（按段落计时 / 段落级点评） */
const TAGS = [
  { k: '亮点', c: 't1' },
  { k: '硬伤', c: 't2' },
  { k: '追问', c: 't3' },
  { k: '口头禅', c: 't4' },
];
const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

/* ---------------- 前端错误上报（写进后端同一份日志，排查闪错 / 白屏用） ----------------
   window.onerror 抓同步脚本异常，unhandledrejection 抓 Promise 未捕获拒绝；
   同一个错误 5 秒内只上报一次，避免异常循环把日志刷爆。上报失败绝不二次抛出。 */
const LOG_SENT = new Map();
function reportClientError(kind, payload) {
  try {
    const p = payload || {};
    const key = kind + '|' + (p.message || '') + '|' + (p.source || '') + '|' + (p.line || '');
    const now = Date.now();
    if (now - (LOG_SENT.get(key) || 0) < 5000) return;
    LOG_SENT.set(key, now);
    if (LOG_SENT.size > 50) LOG_SENT.clear();
    p.kind = kind;
    p.url = location.pathname + location.search;
    p.ua = navigator.userAgent;
    const body = JSON.stringify(p);
    if (navigator.sendBeacon) {
      navigator.sendBeacon('/api/log/client', new Blob([body], { type: 'application/json' }));
    } else {
      fetch('/api/log/client', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body, keepalive: true,
      }).catch(() => {});
    }
  } catch (e) { /* 上报本身失败就静默放弃，不能在错误处理里再制造错误 */ }
}
window.onerror = function (message, source, line, col, err) {
  reportClientError('error', {
    message: String(message || ''),
    source: String(source || ''),
    line: line || 0,
    col: col || 0,
    stack: err && err.stack ? String(err.stack) : '',
  });
  return false;   // 返回 false：不吞错误，控制台仍按默认方式输出
};
window.addEventListener('unhandledrejection', function (ev) {
  const r = ev && ev.reason;
  const msg = (r && (r.message || r.msg)) ? (r.message || r.msg) : String(r);
  reportClientError('unhandledrejection', {
    message: String(msg || '未捕获的 Promise 拒绝'),
    source: '',
    line: 0,
    col: 0,
    stack: r && r.stack ? String(r.stack) : '',
  });
});

let S = {
  session: null, running: false, sel: null, timer: null, t0: null,
  config: null, partial: null,
  students: [], cur: '', kind: 'loopback',
  recSid: '',        // 本次录音归属的学生（后端锁定值，切换视图不改它）
  viewOnly: false,   // 录音中查看别的学生：只读展示，不写回
};

/* ---------------- 工具 ---------------- */
function toast(msg, isErr) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast show' + (isErr ? ' err' : '');
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.className = 'toast'), 2600);
}
async function api(url, body) {
  const opt = body === undefined ? {} : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  };
  const r = await fetch(url, opt);
  const j = await r.json().catch(() => ({ ok: false, msg: '请求失败 ' + r.status }));
  if (!r.ok || j.ok === false) throw new Error(j.msg || ('请求失败 ' + r.status));
  return j;
}
function fmt(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const m = String(Math.floor(sec / 60)).padStart(2, '0');
  const s = String(sec % 60).padStart(2, '0');
  return m + ':' + s;
}
function span(a, b) { return fmt(a) + '-' + fmt(b); }
function esc(s) {
  return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/* ---------------- 极简 markdown ---------------- */
function md2html(src) {
  if (!src) return '';
  const lines = src.replace(/\r/g, '').split('\n');
  let out = [], i = 0, listType = null;

  const closeList = () => { if (listType) { out.push('</' + listType + '>'); listType = null; } };
  const inline = (t) => esc(t)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');

  while (i < lines.length) {
    let ln = lines[i];

    if (/^\s*\|.*\|\s*$/.test(ln) && i + 1 < lines.length && /^\s*\|[\s:\-\|]+\|\s*$/.test(lines[i + 1])) {
      closeList();
      const head = ln.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
      i += 2;
      let rows = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
        rows.push(lines[i].trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim()));
        i++;
      }
      out.push('<table><thead><tr>' + head.map((h) => '<th>' + inline(h) + '</th>').join('') + '</tr></thead><tbody>');
      rows.forEach((r) => {
        out.push('<tr>' + r.map((c) => '<td>' + inline(c) + '</td>').join('') + '</tr>');
      });
      out.push('</tbody></table>');
      continue;
    }

    let m = ln.match(/^(#{1,6})\s+(.*)$/);
    if (m) {
      closeList();
      const lv = Math.min(3, m[1].length);
      out.push('<h' + lv + '>' + inline(m[2]) + '</h' + lv + '>');
      i++; continue;
    }

    if (/^>\s?/.test(ln)) {
      closeList();
      out.push('<blockquote>' + inline(ln.replace(/^>\s?/, '')) + '</blockquote>');
      i++; continue;
    }

    m = ln.match(/^\s*(\d+)[\.、)]\s+(.*)$/);
    if (m) {
      if (listType !== 'ol') { closeList(); out.push('<ol>'); listType = 'ol'; }
      out.push('<li>' + inline(m[2]) + '</li>');
      i++; continue;
    }
    m = ln.match(/^\s*[-*]\s+(.*)$/);
    if (m) {
      if (listType !== 'ul') { closeList(); out.push('<ul>'); listType = 'ul'; }
      out.push('<li>' + inline(m[1]) + '</li>');
      i++; continue;
    }

    if (!ln.trim()) { closeList(); i++; continue; }

    closeList();
    out.push('<p>' + inline(ln) + '</p>');
    i++;
  }
  closeList();
  return out.join('');
}

/* ---------------- 渲染：段落 + 句子 ---------------- */
function segHTML(s) {
  const tags = (s.tags || []).map((t) => {
    const d = TAGS.find((x) => x.k === t);
    return '<span class="tag ' + (d ? d.c : 't1') + '">' + esc(t) + '</span>';
  }).join('');
  // 自定义问题标（现有四类标记没涵盖的问题，自己填）
  const ctags = (s.ctags || []).map((t, i) =>
    '<span class="ctag" title="自定义问题：' + esc(t) + '">' + esc(t) +
      '<b class="ctagx" data-act="ctag-del" data-i="' + i + '" title="删除这条问题">×</b></span>'
  ).join('');
  const btns = TAGS.map((t) =>
    '<button data-act="tag" data-tag="' + t.k + '" class="' + ((s.tags || []).includes(t.k) ? 'act' : '') + '">' + t.k + '</button>'
  ).join('') +
    '<button data-act="ctag" class="' + ((s.ctags || []).length ? 'act' : '') + '" title="填写现有四类标记没涵盖的问题">＋问题</button>' +
    '<button data-act="note">备注</button>' +
    '<button data-act="quick">AI速评</button>' +
    '<button data-act="menu" title="右键也可直接打开：指定段落结构 / 本段点评">结构/点评</button>' +
    '<button data-act="del">删除</button>';

  // 被标记的句子在段落内加粗变色（取第一个标记决定颜色）
  let mcls = '';
  (s.tags || []).forEach((t) => {
    const c = MARK_CLS[t];
    if (c && !mcls) mcls = c;
  });
  const pq = (s.ctags || []).length ? ' hasq' : '';
  return '' +
    '<div class="seg' + (S.sel === s.id ? ' sel' : '') + (mcls ? ' ' + mcls : '') + pq +
      '" data-id="' + s.id + '">' +
      '<div class="meta">' + fmt(s.t_start) + '</div>' +
      '<div class="body">' +
        '<div class="text">' + esc(s.text) + '</div>' +
        (tags ? '<div class="tags">' + tags + '</div>' : '') +
        (ctags ? '<div class="ctags">' + ctags + '</div>' : '') +
        (s.note ? '<div class="note">备注：' + esc(s.note) + '</div>' : '') +
        (s.ai_note ? '<div class="ainote">AI：' + esc(s.ai_note) + '</div>' : '') +
        '<div class="tools">' + btns + '</div>' +
      '</div>' +
    '</div>';
}

function paraHTML(p, segs) {
  const label = p.label || p.title || '';
  const sec = Math.max(0, Math.round((p.t_end || 0) - (p.t_start || 0)));
  const chars = p.chars || 0;
  const speed = sec > 0 ? Math.round(chars / sec * 60) : 0;
  const ai = p.ai_note
    ? '<div class="pai">' + esc(p.ai_note).replace(/\n/g, '<br/>') + '</div>'
    : '';
  const st = stageMeta(p);
  const stageChip = st.stage
    ? '<span class="pstageh ' + (st.k ? 'k-' + st.k : '') + (st.man ? ' manual' : '') +
        '" title="' + esc('本段结构：' + st.label + '（' + (st.confTxt || '') +
          (st.why ? '｜依据：' + st.why : '') + '）右键可改') + '">' +
        esc(st.label) + (st.man ? '·手动' : '') + '</span>'
    : '';
  return '' +
    '<section class="para' + (p.closed ? ' closed' : ' live') + '" data-pid="' + p.id + '">' +
      '<header class="ph">' +
        '<span class="pno">第' + p.idx + '段</span>' +
        '<span class="plabel">' + esc(label || '未命名') + '</span>' +
        stageChip +
        '<span class="ptime">' + span(p.t_start, p.t_end) + '</span>' +
        '<span class="pmeta">' + sec + '秒 · ' + chars + '字 · ' + speed + '字/分</span>' +
        '<button class="mini" data-act="pmenu" title="指定本段结构 / 本段 AI 点评（右键段落也可）">结构/点评</button>' +
        '<button class="mini" data-act="pquick">本段点评</button>' +
      '</header>' +
      ai +
      '<div class="psegs">' + segs.map(segHTML).join('') + '</div>' +
    '</section>';
}

/* ---------------- 渲染：实时识别中（中间结果） ---------------- */
function liveHTML() {
  if (!S.partial || !S.partial.text) return '';
  return '<div id="liveline" class="liveline">' +
      '<span class="livedot"></span>' +
      '<span class="livetext">' + esc(S.partial.text) + '</span>' +
      '<span class="livehint">识别中</span>' +
    '</div>';
}

function renderLive() {
  const box = $('#transcript');
  const hasSegs = !!((S.session && S.session.segments) || []).length;
  if (!hasSegs) {
    // 还没定稿的句子也要能立刻看到
    renderTranscript(false);
    return;
  }
  let el = document.getElementById('liveline');
  if (!S.partial || !S.partial.text) {
    if (el) el.remove();
    return;
  }
  if (!el) {
    box.insertAdjacentHTML('beforeend', liveHTML());
    el = document.getElementById('liveline');
  } else {
    const t = el.querySelector('.livetext');
    if (t) t.textContent = S.partial.text;
  }
  box.scrollTop = box.scrollHeight;
}

function clearLive() {
  S.partial = null;
  const el = document.getElementById('liveline');
  if (el) el.remove();
}

/* ---------------- 渲染：精简版（去冗余 · 无时间戳 · 标结构环节） ---------------- */
const STAGE_KEY = { '审题': 'shen', '提观点': 'op', '重分析': 'an', '抓落实': 'ac', '结尾': 'en' };
/* 环节配色：已知环节用固定色，题型自定义环节按其在当前题型中的顺序轮转配色 */
const STAGE_KEY_CYCLE = ['shen', 'op', 'an', 'ac', 'en'];
function stageK(stage) {
  const s = String(stage || '').trim();
  if (!s) return '';
  if (STAGE_KEY[s]) return STAGE_KEY[s];
  const i = STAGE_OPTS.indexOf(s);
  if (i < 0) return '';
  return STAGE_KEY_CYCLE[i % STAGE_KEY_CYCLE.length];
}

function plainParas() {
  const paras = (S.session && S.session.paragraphs) || [];
  return paras
    .filter((p) => (p.plain || '').trim() || (p.plain_parts || []).length)
    .sort((a, b) => (a.idx || 0) - (b.idx || 0));
}

/* 标记类型 -> 段落内加粗变色类 */
const MARK_CLS = { '亮点': 'm1', '硬伤': 'm2', '追问': 'm3', '口头禅': 'm4' };

/* 把段落拆成「句子级片段」：原始分句 + 纠错后的文本 + 该句的标记 */
function paraParts(p) {
  const raw = p.plain_parts || [];
  const corr = p.plain_correct || {};
  const bySid = corr.by_sid || {};
  const fixMap = {};
  (corr.changes || []).forEach((c) => {
    if (c && c.sid) (fixMap[c.sid] = fixMap[c.sid] || []).push(c);
  });
  if (!raw.length) {
    return [{ sid: '', text: (p.plain || '').trim(), tags: [], note: '', fixes: [] }];
  }
  return raw.map((pt) => {
    const sg = findSeg(pt.sid) || {};
    const tags = (pt.tags && pt.tags.length) ? pt.tags : (sg.tags || []);
    const ctags = (pt.ctags && pt.ctags.length) ? pt.ctags : (sg.ctags || []);
    const edited = !!pt.edited;
    return {
      sid: pt.sid || '',
      /* 人工拆句 / 合并 / 改字后的句子以编辑稿为准，不再被纠错结果覆盖 */
      text: (edited ? (pt.text || '') : (bySid[pt.sid] || pt.text || '')),
      edited: edited,
      tags: tags,
      ctags: ctags,
      note: pt.note || sg.note || '',
      fixes: fixMap[pt.sid] || [],
      stage_manual: pt.stage_manual || sg.stage_manual || null,
    };
  });
}

function sentTitle(pt) {
  const a = [];
  if (pt.tags && pt.tags.length) a.push('标记：' + pt.tags.join(' / '));
  if (pt.ctags && pt.ctags.length) a.push('自定义问题：' + pt.ctags.join(' / '));
  if (pt.note) a.push('备注：' + pt.note);
  if (pt.fixes && pt.fixes.length) {
    a.push('纠错：' + pt.fixes.map((c) => (c.from || '') + '→' + (c.to || '')).join('；'));
  }
  return a.join('｜');
}

/* 单句 HTML：被标记的句子加粗变色；纠错过的句子带「改」角标 */
function sentHTML(pt, pid) {
  const cls = ['psent'];
  (pt.tags || []).forEach((t) => {
    const c = MARK_CLS[t];
    if (c && cls.indexOf(c) < 0) cls.push(c);
  });
  if (pt.sid && P_SEL.indexOf(pt.sid) >= 0) cls.push('picked');
  const ttl = sentTitle(pt);
  const fix = (pt.fixes && pt.fixes.length)
    ? '<sup class="fix" title="' + esc('纠错：' + pt.fixes.map((c) => (c.from || '') + '→' + (c.to || '')).join('；')) + '">改</sup>'
    : '';
  const q = (pt.ctags && pt.ctags.length)
    ? '<sup class="qsup" title="' + esc('自定义问题：' + pt.ctags.join(' / ')) + '">问</sup>'
    : '';
  const manStage = (pt.stage_manual && pt.stage_manual.stage) ? pt.stage_manual.stage : '';
  const man = manStage
    ? '<sup class="mansup" title="' + esc('这句由你手动归入「' + manStage + '」，右键可改或取消') + '">' +
        '→' + esc(manStage) + '</sup>'
    : '';
  return '<span class="' + cls.join(' ') + (q ? ' hasq' : '') + (manStage ? ' hasman' : '') +
      (pt.edited ? ' pedited' : '') + '"' +
      (pt.sid ? ' data-sid="' + esc(pt.sid) + '"' : '') +
      (pid ? ' data-pid="' + esc(pid) + '"' : '') +
      (ttl ? ' title="' + esc(ttl) + '"' : '') + '>' +
      esc(pt.text) + q + fix + man + '</span>';
}

function plainFixBadge(p) {
  const st = (p.plain_correct || {}).state || 'raw';
  if (st === 'ai') return '<span class="pfix ai" title="已由 AI 按上下文还原同音字 / 错别字 / 口误">智能纠错</span>';
  if (st === 'local') return '<span class="pfix" title="已按本地词表纠正同音字与错别字，点右上「智能纠错」可用 AI 再校一次">本地纠错</span>';
  return '';
}

/* 段落结构元信息：环节 / 依据 / 置信度 / 是否手动指定 */
function stageMeta(p) {
  const stage = p.plain_stage || '';
  const label = p.plain_label || stage || '未标注';
  const k = stageK(stage);
  const why = (p.plain_reason || '').replace(/^依据[:：]\s*/, '').trim();
  const man = (p.stage_manual && p.stage_manual.stage) ? p.stage_manual.stage : '';
  const conf = man ? 'manual' : (p.plain_conf || '');
  const confTxt = conf === 'manual' ? '手动指定'
    : conf === 'high' ? '依据充分'
    : conf === 'mid' ? '依据较明确'
    : conf === 'low' ? '偏推断' : '';
  return { stage, label, k, why, man, conf, confTxt };
}

function renderPlain(scrollToEnd) {
  const box = $('#plainBox');
  const list = $('#plainList');
  if (!box || !list) return;
  const items = plainParas();
  const empty = $('#plainEmpty');
  if (empty) empty.style.display = items.length ? 'none' : '';
  if (!items.length) { list.innerHTML = ''; paintSel(); return; }
  const near = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
  list.innerHTML = (P_VIEW === 'stage') ? stageViewHTML(items) : paraViewHTML(items);
  if (scrollToEnd || near) box.scrollTop = box.scrollHeight;
  paintSel();
  updateViewBtn();
}

/* 按段落视图：一段一行，句子归属跟随段落（手动改过的句子带 → 角标） */
function paraViewHTML(items) {
  return items.map((p) => {
    const st = stageMeta(p);
    const body = paraParts(p).map((pt) => sentHTML(pt, p.id)).join('');
    const whyTxt = st.man
      ? ('该段结构由你手动指定为「' + st.stage + '」，右键可改回自动语义判定')
      : (st.why ? '依据：' + st.why : '尚未给出判定依据');
    const whyHtml = st.man
      ? '<span class="pwhy c-manual" title="' + esc(whyTxt) + '">手动指定</span>'
      : (st.why
        ? '<span class="pwhy c-' + (st.conf || 'mid') + '" title="' + esc(whyTxt) + '">' +
            esc(st.confTxt) + '·' + esc(st.why.length > 40 ? st.why.slice(0, 40) + '…' : st.why) + '</span>'
        : '<span class="pwhy c-none" title="' + esc(whyTxt) + '">' +
            (st.stage ? '暂无依据' : '尚未判定') + '</span>');
    const manHtml = st.man
      ? '<span class="pman" title="该段结构由你手动指定，右键可改回自动语义判定">手动</span>'
      : '';
    return '<div class="pitem' + (p.closed ? '' : ' live') + '" data-pid="' + p.id + '">' +
        '<span class="pstag ' + (st.k ? 'k-' + st.k : '') + (st.man ? ' manual' : '') +
          '" title="' + esc('本段结构：' + st.label + (st.why ? '（依据：' + st.why + '）' : '')) + '">' +
          esc(st.label) + '</span>' +
        '<div class="pwrap">' +
          '<div class="pstext">' + body + plainFixBadge(p) + '</div>' +
          '<div class="pmrow">' + whyHtml + manHtml +
            '<button class="mini pbtn" data-act="pmenu" title="指定本段结构 / 对本段单独 AI 点评">改结构 / 本段点评</button>' +
          '</div>' +
        '</div>' +
      '</div>';
  }).join('');
}

/* ---------------- 精简版：句子级手动改归属结构 ---------------- */
const NONE_STAGE = '未归类';
let P_SEL = [];          // 当前选中的句子 sid（按文档顺序）
let P_ANCHOR = '';       // Shift 连选的锚点
let P_DRAG = null;       // 拖拽选句过程中
let P_VIEW = 'para';     // para=按段落  stage=按结构
let STAGE_OPTS = [];     // 当前题型的答题环节（后端给）
let SENT_OPEN_AT = 0;    // 菜单打开时刻，挡掉右键后紧跟的 click 误关

/* 某句的实际归属：手动优先，否则跟随该段自动判定 */
function sentStageOf(row) {
  const man = (row.stage_manual && row.stage_manual.stage) ? row.stage_manual.stage : '';
  if (man) return { stage: man, man: true };
  return { stage: row.pstage || NONE_STAGE, man: false };
}

function paraOfSid(sid) {
  return ((S.session && S.session.paragraphs) || []).find(
    (p) => (p.plain_parts || []).some((x) => x.sid === sid)) || null;
}

/* 把所有段落的所有句子摊平成按时间顺序的行 */
function plainRows(items) {
  const rows = [];
  (items || plainParas()).forEach((p) => {
    const st = stageMeta(p);
    paraParts(p).forEach((pt) => {
      rows.push({
        sid: pt.sid, pt: pt, pid: p.id, pidx: p.idx || 0,
        pstage: st.stage, stage_manual: pt.stage_manual,
      });
    });
  });
  return rows;
}

/* 按结构视图：句子按归属环节分组，手动改过的句子直接显示在新分组里 */
function stageViewHTML(items) {
  const rows = plainRows(items);
  const order = STAGE_OPTS.slice();
  const extra = [];   // 不在题型环节表里的（审题等前置关口）排在前面
  rows.forEach((r) => {
    const g = sentStageOf(r).stage;
    if (g && g !== NONE_STAGE && order.indexOf(g) < 0 && extra.indexOf(g) < 0) extra.push(g);
  });
  const groups = {};
  rows.forEach((r) => {
    const s = sentStageOf(r);
    (groups[s.stage] = groups[s.stage] || []).push({ r: r, man: s.man });
  });
  const keys = extra.concat(order, [NONE_STAGE]).filter((k) => (groups[k] || []).length);
  if (!keys.length) return '<div class="muted">暂无内容</div>';
  return keys.map((k) => {
    const list = groups[k];
    const manN = list.filter((x) => x.man).length;
    const kk = stageK(k);
    const key = kk ? ' k-' + kk : '';
    const body = list.map((x) => sentHTML(x.r.pt, x.r.pid) +
      '<sub class="porig" title="' + esc('原属第 ' + x.r.pidx + ' 段') + '">第' + x.r.pidx + '段</sub>'
    ).join('');
    return '<div class="pgroup" data-gstage="' + esc(k) + '">' +
        '<div class="pgh">' +
          '<span class="pstag' + key + '">' + esc(k) + '</span>' +
          '<span class="pgc">' + list.length + ' 句</span>' +
          (manN ? '<span class="pman" title="' + esc('其中 ' + manN + ' 句由你手动指定归属') + '">手动 ' + manN + '</span>' : '') +
        '</div>' +
        '<div class="pgtext">' + body + '</div>' +
      '</div>';
  }).join('');
}

function sentSids() {
  return Array.from(document.querySelectorAll('#plainList .psent[data-sid]'))
    .map((x) => x.dataset.sid).filter(Boolean);
}

function rangeSids(a, b) {
  const all = sentSids();
  let i = all.indexOf(a);
  let j = all.indexOf(b);
  if (i < 0 || j < 0) return b ? [b] : [];
  if (i > j) { const t = i; i = j; j = t; }
  return all.slice(i, j + 1);
}

function paintSel() {
  document.querySelectorAll('#plainList .psent.picked').forEach((x) => x.classList.remove('picked'));
  P_SEL.forEach((sid) => {
    document.querySelectorAll('#plainList .psent[data-sid="' + sid + '"]').forEach((el) => el.classList.add('picked'));
  });
  const bar = $('#plainSelBar');
  if (bar) bar.hidden = false;
  const c = $('#plainSelCount');
  if (c) {
    c.textContent = P_SEL.length ? ('已选 ' + P_SEL.length + ' 句') : '未选句 · 单击 / 拖选 / Shift 连选';
    c.classList.toggle('empty', !P_SEL.length);
  }
  ['#btnSentStage', '#btnSentAuto', '#btnSentClear', '#btnSentSplit', '#btnSentMerge', '#btnSentDel'].forEach((q) => {
    const btn = $(q);
    if (btn) btn.disabled = !P_SEL.length;
  });
}

function clearSel() {
  P_SEL = [];
  P_ANCHOR = '';
  paintSel();
}

function updateViewBtn() {
  const b = $('#btnPlainView');
  if (!b) return;
  b.textContent = (P_VIEW === 'stage') ? '按段落看' : '按结构看';
  b.title = (P_VIEW === 'stage')
    ? '当前按结构分组显示，点击改回「一段一行」'
    : '当前按段落显示，点击按答题结构分组（手动改过归属的句子会在新结构下）';
}

function toggleView() {
  P_VIEW = (P_VIEW === 'stage') ? 'para' : 'stage';
  if (P_VIEW === 'stage') ensureStageOpts().then(() => renderPlain(false));
  else renderPlain(false);
}

async function ensureStageOpts(force) {
  if (STAGE_OPTS.length && !force) return STAGE_OPTS;
  try {
    const r = await api('/api/plain/stages');
    STAGE_OPTS = r.stages || [];
  } catch (e) {
    if (!STAGE_OPTS.length) STAGE_OPTS = ['审题', '提观点', '重分析', '抓落实', '结尾'];
  }
  return STAGE_OPTS;
}

function patchSegManual(list) {
  (list || []).forEach((x) => {
    const seg = findSeg(x.id);
    if (!seg) return;
    if (x.stage_manual) seg.stage_manual = x.stage_manual;
    else delete seg.stage_manual;
  });
}

/* 打开「结构选择」菜单 */
async function openSentMenu(x, y) {
  const m = $('#sentMenu');
  if (!m) return;
  if (!P_SEL.length) { toast('先在精简版里选中要改归属的句子', true); return; }
  await ensureStageOpts(true);
  const cur = {};
  P_SEL.forEach((sid) => {
    const seg = findSeg(sid) || {};
    const p = paraOfSid(sid);
    const man = (seg.stage_manual && seg.stage_manual.stage) ? seg.stage_manual.stage : '';
    const eff = man || ((p && p.plain_stage) || '') || NONE_STAGE;
    cur[eff] = (cur[eff] || 0) + 1;
  });
  const hasMan = P_SEL.some((sid) => ((findSeg(sid) || {}).stage_manual || {}).stage);
  const opts = STAGE_OPTS.slice();
  Object.keys(cur).forEach((k) => {
    if (k !== NONE_STAGE && opts.indexOf(k) < 0) opts.push(k);
  });
  const item = (val, label, extra) =>
    '<button class="ctx-i" data-stage="' + esc(val) + '">' +
      '<i class="sd"></i><span>' + esc(label) + '</span>' + (extra || '') + '</button>';
  const here = (k) => {
    const n = cur[k] || 0;
    if (!n) return '';
    return n === P_SEL.length ? '<em class="cur">当前</em>' : '<em class="cur">' + n + ' 句在此</em>';
  };
  const noneN = cur[NONE_STAGE] || 0;
  m.innerHTML =
    '<div class="ctx-t">把选中的 ' + P_SEL.length + ' 句归入哪个结构</div>' +
    opts.map((s) => item(s, s, here(s))).join('') +
    item(NONE_STAGE, NONE_STAGE, noneN === P_SEL.length ? '<em class="cur">当前</em>' : (noneN ? '<em class="cur">' + noneN + ' 句在此</em>' : '')) +
    '<div class="ctx-sep"></div>' +
    '<button class="ctx-i" data-act="auto"' + (hasMan ? '' : ' disabled') + '>' +
      '<i class="sd sd-auto"></i><span>取消手动归属 · 回自动判定</span></button>' +
    (manualSids().length
      ? '<button class="ctx-i" data-act="auto-all"><i class="sd sd-auto"></i><span>取消全部手动归属（' + manualSids().length + ' 句）</span></button>'
      : '') +
    '<button class="ctx-i" data-act="clear"><i class="sd sd-info"></i><span>清空选择</span></button>';
  m.style.display = 'block';
  SENT_OPEN_AT = Date.now();
  const w = m.offsetWidth;
  const h = m.offsetHeight;
  m.style.left = Math.max(6, Math.min(x, window.innerWidth - w - 8)) + 'px';
  m.style.top = Math.max(6, Math.min(y, window.innerHeight - h - 8)) + 'px';
}

function closeSentMenu() {
  const m = $('#sentMenu');
  if (m) { m.style.display = 'none'; m.innerHTML = ''; }
}

function manualSids() {
  return ((S.session && S.session.segments) || [])
    .filter((s) => (s.stage_manual || {}).stage).map((s) => s.id);
}

async function applySentStage(stage, sids) {
  const list = (sids && sids.length) ? sids.slice() : P_SEL.slice();
  if (!list.length) return;
  try {
    const r = await api('/api/plain/sentence/stage', { sids: list, stage: stage || '' });
    patchSegManual(r.segs);
    (r.paras || []).forEach((p) => upsertPara(p));
    const label = r.stage_label || stage || NONE_STAGE;
    if (stage) P_VIEW = 'stage';      // 改完立刻按结构看，句子就在新结构下面
    renderPlain(false);
    renderTranscript(false);
    const miss = (r.miss || []).length;
    toast('已把 ' + (r.count || list.length) + ' 句归入「' + label + '」' +
      (miss ? '，' + miss + ' 句已失效' : ''));
  } catch (e) {
    toast('改归属失败：' + e.message, true);
  }
}

async function cancelSentManual(all) {
  const sids = all ? manualSids() : P_SEL.slice();
  if (!sids.length) { toast(all ? '当前没有手动改过归属的句子' : '先选中要恢复自动判定的句子', true); return; }
  try {
    const r = await api('/api/plain/sentence/stage', { sids: sids, stage: '' });
    patchSegManual(r.segs);
    (r.paras || []).forEach((p) => upsertPara(p));
    renderPlain(false);
    renderTranscript(false);
    toast('已取消 ' + (r.count || sids.length) + ' 句手动归属，回到该段自动判定结果');
  } catch (e) {
    toast('取消失败：' + e.message, true);
  }
}

/* ==================================================================
   精简版：句子级手工编辑（拆句 / 合并 / 改字 / 删除）
   —— 所有改动落到后端 para.plain_edits.items，复制整篇、导出、
      精简要点取稿都读同一份编辑稿，不会再拿到改动前的原文。
   ================================================================== */

/* 当前正在就地改字的那一句 */
let P_EDITKEY = '';

function sentRowOf(key) {
  return plainRows().find((r) => r.sid === key) || null;
}

/* 文档顺序下的句子键（段落顺序 + 段内顺序） */
function sentKeys() {
  return Array.from(document.querySelectorAll('#plainList .psent[data-sid]'))
    .map((x) => x.dataset.sid).filter(Boolean);
}

/* 光标在本句纯文本里的位置（跳过 角标 sup / 小标 sub） */
function caretPos(el) {
  const sel = window.getSelection();
  if (!sel || !sel.rangeCount) return -1;
  const rg = sel.getRangeAt(0);
  if (!el.contains(rg.startContainer)) return -1;
  let n = 0;
  let done = false;
  const walk = (node) => {
    Array.from(node.childNodes).forEach((c) => {
      if (done) return;
      if (c.nodeType === 1 && /^(SUP|SUB|BUTTON)$/.test(c.nodeName)) return;
      if (c.nodeType === 3) {
        if (c === rg.startContainer) { n += rg.startOffset; done = true; return; }
        n += c.nodeValue.length;
      } else if (c.nodeType === 1) {
        walk(c);
      }
    });
  };
  walk(el);
  return done ? n : -1;
}

/* 进入就地改字 */
function beginSentEdit(el) {
  if (!el || el._editing) return;
  const key = el.dataset.sid;
  if (!key || !el.dataset.pid) { toast('这句没有可编辑的编号，请先把该段精简稿刷新出来', true); return; }
  const row = sentRowOf(key);
  const clean = ((row && row.pt && row.pt.text) || '').replace(/\s+/g, '');
  el._editing = true;
  el._orig = clean;
  P_EDITKEY = key;
  el.classList.add('editing');
  /* 角标（改 / 问 / →环节）不属于正文，编辑时先摘掉，避免被当成句子内容保存 */
  el.textContent = clean;
  el.setAttribute('contenteditable', 'true');
  el.setAttribute('spellcheck', 'false');
  el.addEventListener('keydown', onSentKey);
  el.addEventListener('blur', onSentBlur);
  el.focus();
  try {
    const rg = document.createRange();
    rg.selectNodeContents(el);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(rg);
  } catch (e) { /* 忽略 */ }
  toast('直接改字：Enter 保存 · Esc 取消 · Ctrl+Enter 在光标处拆成两句');
}

function endSentEdit(el) {
  if (!el || !el._editing) return;
  el._editing = false;
  P_EDITKEY = '';
  el.classList.remove('editing');
  el.removeAttribute('contenteditable');
  el.removeEventListener('keydown', onSentKey);
  el.removeEventListener('blur', onSentBlur);
}

function onSentKey(e) {
  if (!this._editing) return;
  e.stopPropagation();
  if (e.key === 'Escape') {
    e.preventDefault();
    endSentEdit(this);
    renderPlain(false);
    return;
  }
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    const pos = caretPos(this);
    const pid = this.dataset.pid;
    const key = this.dataset.sid;
    endSentEdit(this);
    if (pos <= 0) { toast('请把光标放到要拆开的位置再按 Ctrl+Enter', true); renderPlain(false); return; }
    sentEdit('split', { pid: pid, key: key, pos: pos }, '已拆成两句');
    return;
  }
  if (e.key === 'Enter') {
    e.preventDefault();
    commitSentEdit(this);
  }
}

function onSentBlur() {
  if (this._editing) commitSentEdit(this);
}

function commitSentEdit(el) {
  const pid = el.dataset.pid;
  const key = el.dataset.sid;
  const t = (el.textContent || '').replace(/\s+/g, '').trim();
  const orig = (el._orig || '').replace(/\s+/g, '').trim();
  endSentEdit(el);
  if (!t) { toast('句子不能为空，已还原（要删整句请选中后点「删除」）', true); renderPlain(false); return; }
  if (t === orig) { renderPlain(false); return; }
  sentEdit('text', { pid: pid, key: key, text: t }, '已改字');
}

/* 统一调用后端编辑接口 */
async function sentEdit(op, body, okMsg) {
  try {
    const r = await api('/api/plain/edit', Object.assign({ op: op }, body || {}));
    if (r && r.para) upsertPara(r.para);
    renderPlain(false);
    if (okMsg) {
      const n = ((r && r.parts) || []).length;
      toast(okMsg + (n ? '（本段现有 ' + n + ' 句）' : ''));
    }
    return r;
  } catch (e) {
    toast('编辑失败：' + e.message, true);
    renderPlain(false);
    return null;
  }
}

/* 工具条：光标处拆句（未选中时取第一句选中项） */
function splitSentUI() {
  if (!P_SEL.length) { toast('先单击选中要拆的那一句，并把光标点到拆分位置', true); return; }
  const key = P_SEL[0];
  const row = sentRowOf(key);
  if (!row) { toast('选中的句子已失效，请重新选', true); return; }
  const el = document.querySelector('#plainList .psent[data-sid="' + key + '"]');
  const pos = el ? caretPos(el) : -1;
  if (pos <= 0) {
    toast('要拆句：双击这句进入改字，把光标放到要拆开的位置，再按 Ctrl+Enter', true);
    if (el) beginSentEdit(el);
    return;
  }
  sentEdit('split', { pid: row.pid, key: key, pos: pos }, '已拆成两句');
}

/* 工具条：合并选中的相邻两句 */
async function mergeSentUI() {
  if (P_SEL.length !== 2) { toast('先选中相邻的两句（单击一句 + Shift 点下一句）再合并', true); return; }
  const all = sentKeys();
  let i = all.indexOf(P_SEL[0]);
  let j = all.indexOf(P_SEL[1]);
  if (i < 0 || j < 0) { toast('选中的句子已失效，请重新选', true); return; }
  if (i > j) { const t = i; i = j; j = t; }
  if (j !== i + 1) { toast('只能合并相邻的两句', true); return; }
  const a = sentRowOf(all[i]);
  const b = sentRowOf(all[j]);
  if (!a || !b) { toast('选中的句子已失效，请重新选', true); return; }
  if (a.pid !== b.pid) { toast('跨段的两句不能合并，请先改归属或拆句', true); return; }
  const r = await sentEdit('merge', { pid: a.pid, key: all[i], key2: all[j] }, '已合并为一句');
  if (r) { P_SEL = [all[i]]; paintSel(); }
}

/* 工具条：删除选中的句子（可跨段，按段批量） */
async function deleteSentUI() {
  if (!P_SEL.length) { toast('先选中要删除的句子', true); return; }
  const byPid = {};
  P_SEL.forEach((k) => {
    const row = sentRowOf(k);
    if (row) (byPid[row.pid] = byPid[row.pid] || []).push(k);
  });
  const pids = Object.keys(byPid);
  if (!pids.length) { toast('选中的句子已失效，请重新选', true); return; }
  let done = 0;
  let fail = '';
  for (const pid of pids) {
    const r = await sentEdit('delete', { pid: pid, keys: byPid[pid] }, '');
    if (r && r.ok) done += (byPid[pid] || []).length;
    else fail = '有段落只剩一句，至少保留一句';
  }
  P_SEL = [];
  paintSel();
  renderPlain(false);
  if (done) toast('已删除 ' + done + ' 句' + (fail ? '；' + fail : ''));
  else toast(fail || '删除失败', true);
}

/* 双击句子 → 就地改字 */
if ($('#plainList')) {
  $('#plainList').addEventListener('dblclick', (e) => {
    const s = e.target.closest('.psent');
    if (!s || !s.dataset.sid) return;
    if (e.target.closest('button')) return;
    e.preventDefault();
    e.stopPropagation();
    beginSentEdit(s);
  });
}

/* 定位并高亮某个句子（精简版段落内） */
function hitSent(sid, scroll) {
  if (!sid) return false;
  const el = document.querySelector('#plainList .psent[data-sid="' + sid + '"]');
  if (!el) return false;
  document.querySelectorAll('#plainList .psent.hit').forEach((x) => x.classList.remove('hit'));
  el.classList.add('hit');
  if (scroll) el.scrollIntoView({ block: 'center', behavior: 'smooth' });
  clearTimeout(el._h);
  el._h = setTimeout(() => el.classList.remove('hit'), 2400);
  return true;
}

/* 点精简版里的句子 → 反向定位到逐字稿对应句 */
function plainToSeg(el) {
  const sid = el.dataset.sid;
  if (!sid) return;
  S.sel = sid;
  renderTranscript(false);
  updateFloat();
  const s = document.querySelector('.seg[data-id="' + sid + '"]');
  if (s) s.scrollIntoView({ block: 'center', behavior: 'smooth' });
}

/* 智能纠错（AI 逐句还原，失败自动回退本地结果） */
async function correctPlain(btn) {
  const label = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = '纠错中…'; }
  try {
    const r = await api('/api/plain/correct', { wait: true });
    toast('已纠错 ' + (r.count || 0) + ' 段，标记句已同步刷新');
    const st = await api('/api/state');
    S.session.paragraphs = st.session.paragraphs || [];
    renderPlain(false);
  } catch (e) {
    toast('纠错失败：' + e.message, true);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = label; }
  }
}

async function copyPlain() {
  const btn = $('#btnCopyPlain');
  const old = btn.textContent;
  const chk = $('#chkCopyStage');
  const withStage = !chk || chk.checked;
  btn.disabled = true;
  btn.textContent = '复制中…';
  try {
    let text = '';
    try {
      const r = await api('/api/plain/text?stage=' + (withStage ? '1' : '0'));
      text = (r.text || '').trim();
    } catch (e) { text = ''; }
    if (!text) {
      // 后端不可用时按本地数据兜底拼装
      const ok = plainParas().filter((p) => (p.plain || '').trim());
      text = withStage
        ? ok.map((p) => '【' + (p.plain_label || p.plain_stage || '未标注') + '】' + (p.plain || '').trim()).join('\n')
        : ok.map((p) => (p.plain || '').trim()).join('\n');
    }
    if (!text) { toast('还没有精简版内容可复制', true); return; }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.left = '-9999px';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    }
    toast('已复制整篇（' + (withStage ? '带环节前置标题' : '纯文本，无环节标题') + ' · '
      + text.split('\n').filter((x) => x.trim()).length + ' 段）');
  } catch (e) {
    toast('复制失败：' + e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = old;
  }
}

function togglePlain() {
  const pane = $('#plainPane');
  if (!pane) return;
  const on = pane.classList.toggle('collapsed');
  $('#btnTogglePlain').textContent = on ? '展开' : '收起';
  if (!on) renderPlain(true);
}

function renderTranscript(scrollToEnd) {
  const box = $('#transcript');
  const segs = (S.session && S.session.segments) || [];
  const paras = (S.session && S.session.paragraphs) || [];
  /* 重新渲染会重建 innerHTML 并把 scrollTop 归零，必须先记住用户当前位置。
     只有「明确要求滚到底」或「用户本来就贴着底部」才自动跟随新内容，
     否则原样还原，保证手动上滑后不会被新文本拽回底部 / 弹回顶部。 */
  const prevTop = box.scrollTop;
  const stick = scrollToEnd || (box.scrollHeight - box.scrollTop - box.clientHeight < 80);
  const keep = () => {
    if (stick) box.scrollTop = box.scrollHeight;
    else box.scrollTop = Math.min(prevTop, Math.max(0, box.scrollHeight - box.clientHeight));
  };
  if (!segs.length) {
    const live = liveHTML();
    if (live) {
      box.innerHTML = '<section class="para live"><header class="ph"><span class="pno">第1段</span>' +
        '<span class="plabel">识别中</span></header><div class="psegs">' + live + '</div></section>';
      keep();
      return;
    }
    box.innerHTML = '<div class="empty">点「开始听记」后，系统会抓取电脑正在播放的声音并实时转成文字，并按段落计时。<br />播放课程音频 / 视频会议 / 录音均可，无需麦克风。</div>';
    return;
  }
  const segMap = {};
  segs.forEach((s) => (segMap[s.id] = s));
  const used = new Set();
  let html = paras.map((p) => {
    const list = (p.seg_ids || []).map((id) => segMap[id]).filter(Boolean);
    list.forEach((s) => used.add(s.id));
    return paraHTML(p, list);
  }).join('');
  const rest = segs.filter((s) => !used.has(s.id));
  if (rest.length) {
    html += '<section class="para"><header class="ph"><span class="pno">未分段</span>' +
      '<span class="pmeta">' + rest.length + ' 句</span></header>' +
      '<div class="psegs">' + rest.map(segHTML).join('') + '</div></section>';
  }
  html += liveHTML();
  box.innerHTML = html;
  keep();
}

function renderMarks() {
  const segs = (S.session && S.session.segments) || [];
  const marked = segs.filter((s) => (s.tags && s.tags.length) || (s.ctags && s.ctags.length) || s.note);
  $('#markCount').textContent = marked.length;
  const box = $('#marks');
  if (!marked.length) { box.innerHTML = '<div class="muted">还没有标记</div>'; return; }
  box.innerHTML = marked.map((s) => {
    const t = (s.tags || [])[0] || '';
    const d = TAGS.find((x) => x.k === t);
    const ct = (s.ctags || []);
    const tagTxt = (s.tags || []).join('/');
    const head = [tagTxt, ct.length ? (tagTxt ? '问题' : '自定义问题') : ''].filter(Boolean).join('·') || '备注';
    return '<div class="mark-item ' + (d ? d.c : (ct.length ? 'q' : '')) + '" data-id="' + s.id + '">' +
      '<span class="mt">' + fmt(s.t_start) + ' ' + head + '</span>' +
      esc(s.text.slice(0, 60)) + (s.text.length > 60 ? '…' : '') +
      ct.map((x) => '<br/><span class="mt q">问题</span>' + esc(x)).join('') +
      (s.note ? '<br/><span class="mt">备注</span>' + esc(s.note) : '') +
      '</div>';
  }).join('');
}

/* ================== 学生（同一场次内多人分别答题） ================== */
function stById(sid) { return (S.students || []).find((s) => s.id === sid) || null; }

function renderStudents() {
  const box = $('#studentChips');
  if (!box) return;
  const sts = S.students || [];
  if (!sts.length) { box.innerHTML = ''; return; }
  box.innerHTML = sts.map((s) => {
    const on = s.id === S.cur;
    return '<span class="chip' + (on ? ' active' : '') + '" data-sid="' + esc(s.id) + '" title="点击切换到该学生；双击改名">' +
        '<span class="cname">' + esc(s.name || '未命名') + '</span>' +
        '<span class="cdur">' + fmt(s.duration || 0) + '</span>' +
        '<span class="ccount">' + (s.segments || 0) + '句</span>' +
        (s.marks ? '<span class="ccount mk">' + s.marks + '标记</span>' : '') +
        (s.has_review ? '<span class="cok" title="已有点评">✓</span>' : '') +
        (sts.length > 1 ? '<span class="cedit" data-del="' + esc(s.id) + '" title="移除该学生（不影响其他人）">×</span>' : '') +
      '</span>';
  }).join('');
  const tip = $('#studentTip');
  if (tip) {
    const cur = stById(S.cur);
    const rec = stById(S.recSid);
    if (S.recSid && S.viewOnly) {
      // 录音归属在「录音学生」身上，此刻只是只读查看别人
      tip.textContent = '「' + ((rec && rec.name) || '录音学生') + '」正在录音 · 当前为「'
        + ((cur && cur.name) || '该学生') + '」只读查看（新内容不会记到这里）';
      tip.classList.add('ro');
    } else if (S.recSid && rec) {
      tip.textContent = '正在录音并记录到「' + (rec.name || '未命名') + '」名下 · 切到别的学生仅只读查看';
      tip.classList.remove('ro');
    } else {
      tip.classList.remove('ro');
      tip.textContent = cur
        ? ('当前记录到「' + (cur.name || '未命名') + '」名下 · 逐字稿 / 精简版 / 点评 / 标记各自独立')
        : '切到哪位学生，逐字稿 / 精简版 / 点评 / 标记就记到哪位名下';
    }
  }
}

function applySession(sess, force) {
  if (!sess) return;
  S.session = sess;
  // 拿到实时会话即退出「历史回看」态，否则学生切换会被回看分支拦住
  if (!sess.hist_view) { S.histFile = ''; S.histData = null; }
  S.session.paragraphs = sess.paragraphs || [];
  S.students = sess.students || S.students;
  S.cur = sess.cur_student || S.cur;
  if (typeof sess.rec_student === 'string') S.recSid = sess.rec_student;
  if (sess.view_only) S.cur = sess.view_student || S.cur;
  // 录音归属不在当前查看的学生身上 → 只读，不写回任何内容
  S.viewOnly = !!(S.recSid && S.cur && S.cur !== S.recSid);
  // 计时只认录音开始时刻的绝对时间，切视图 / 复制都不影响
  if (S.recSid && sess.rec_t0) S.t0 = sess.rec_t0 * 1000;
  /* 用户主动切换（换学生 / 新增 / 移除 / 载入历史）用 force=true 强刷；
     后台推送触发的回填不覆盖正在编辑、尚未提交的输入 */
  setVal($('#question'), sess.question || '', force);
  syncQtypeTo(sess.qtype || '', true);
  setVal($('#ref'), sess.ref || '', force);
  setVal($('#candidate'), sess.candidate || '', force);
  if (!sess.view_only) $('#timer').textContent = fmt(sess.duration || 0);
  qaShowCard(sess.q_analysis);
  if (sess.review) renderReview(sess.review, sess.review_mode || '');
  else renderReviewReset();
  renderStudents();
  renderTranscript(true); renderMarks(); renderPlain(true); updateFloat();
  if (typeof qnOnSession === 'function') qnOnSession(sess);   // 快速记录跟着学生/会话走
}

async function switchStudent(sid) {
  if (!sid || sid === S.cur) return;
  // 历史回看态：只切换查看哪一位学生的存档，绝不写回实时会话
  if (S.histFile && S.histData) {
    histShowStudent(sid, false);
    const st = histStuOf(sid) || {};
    toast('已切到「' + (st.name || '学生') + '」的历史作答（只读查看）');
    return;
  }
  // 录音中：只切换「查看谁」，绝不改动录音归属，也不发任何改变会话状态的请求
  if (S.recSid) {
    try {
      let r = null;
      if (sid === S.recSid) {
        r = await api('/api/state');
        if ((r.session.cur_student || '') !== sid) {
          r = await api('/api/state?view=' + encodeURIComponent(sid));
        }
      } else {
        // 只读快照：GET，不写 cur_student、不动计时、不清缓冲
        r = await api('/api/state?view=' + encodeURIComponent(sid));
      }
      S.sel = null;
      clearLive();
      applySession(r.session, true);
      S.cur = sid;
      renderStudents();
      const st = stById(sid);
      const rec = stById(S.recSid);
      if (sid === S.recSid) {
        toast('已切回「' + ((st && st.name) || '学生') + '」，继续记录中');
      } else {
        toast('「' + ((rec && rec.name) || '录音学生') + '」正在录音，当前为「'
          + ((st && st.name) || '该学生') + '」只读查看');
      }
    } catch (e) { toast(e.message, true); }
    return;
  }
  try {
    await api('/api/students/switch', { id: sid });
    const r = await api('/api/state');
    S.sel = null;
    clearLive();
    applySession(r.session, true);
    const st = stById(sid);
    toast('已切到「' + ((st && st.name) || '学生') + '」的独立记录');
  } catch (e) { toast(e.message, true); }
}

async function addStudent() {
  try {
    const r = await api('/api/students/add', { name: '' });
    const st = await api('/api/state');
    S.sel = null;
    clearLive();
    applySession(st.session, true);
    toast('已新增「' + r.student.name + '」，现在开始记到她/他名下');
    setTimeout(() => {
      const el = document.querySelector('.chip[data-sid="' + r.cur + '"]');
      if (el) el.scrollIntoView({ inline: 'end', block: 'nearest' });
    }, 60);
  } catch (e) { toast(e.message, true); }
}

async function renameStudent(sid) {
  const st = stById(sid);
  const name = prompt('学生姓名（最多 16 字）', (st && st.name) || '');
  if (name === null) return;
  const nm = name.trim();
  if (!nm) { toast('名称不能为空', true); return; }
  try {
    await api('/api/students/rename', { id: sid, name: nm });
    const r = await api('/api/state');
    S.students = r.session.students || S.students;
    renderStudents();
    toast('已改名为「' + nm + '」');
  } catch (e) { toast(e.message, true); }
}

async function delStudent(sid) {
  const st = stById(sid);
  if (!st) return;
  if (!confirm('从本场移除「' + (st.name || '未命名') + '」？\n\n她/他的逐字稿、精简版、点评、标记会一并移除，其他学生不受影响。')) return;
  try {
    const r = await api('/api/students/delete', { id: sid });
    const s2 = await api('/api/state');
    S.sel = null;
    clearLive();
    applySession(s2.session, true);
    toast('已移除该学生');
  } catch (e) { toast(e.message, true); }
}

async function showCompare() {
  const body = $('#compareBody');
  body.innerHTML = '<div class="muted">加载中…</div>';
  openModal('#modalCompare');
  try {
    const r = await api('/api/students/compare');
    const rows = r.students || [];
    if (!rows.length) { body.innerHTML = '<div class="muted">本场还没有学生记录</div>'; return; }
    const maxChars = Math.max.apply(null, rows.map((x) => x.chars || 0).concat([1]));
    body.innerHTML = '<div class="cmp-wrap"><div class="cmp-cards">' + rows.map((x) => {
      const cur = x.id === r.cur;
      const w = Math.max(2, Math.round((x.chars || 0) / maxChars * 100));
      const markTags = (x.mark_detail || []).map((m) =>
        '<div class="cmp-mk"><span class="mt">' + esc(m.t) + '</span>' +
        (m.tags && m.tags.length ? '<b>' + esc(m.tags.join('/')) + '</b> ' : '') +
        esc((m.text || '').slice(0, 44)) + (m.note ? '（' + esc(m.note) + '）' : '') + '</div>').join('');
      const stages = (x.stages || []).map((s) =>
        '<div class="cmp-stage"><span class="cs-k">第' + s.idx + '段 · ' + esc(s.label || '未标注') + '</span>' +
        '<span class="cs-v">' + esc((s.plain || '').slice(0, 90)) + ((s.plain || '').length > 90 ? '…' : '') + '</span></div>').join('');
      return '<div class="cmp-card' + (cur ? ' cur' : '') + '" data-sid="' + esc(x.id) + '">' +
          '<div class="cmp-name">' + esc(x.name || '未命名') + (cur ? '<span class="cn-cur">当前</span>' : '') + '</div>' +
          '<div class="cmp-nums">' +
            '<span>' + fmt(x.duration || 0) + '</span><span>' + (x.paragraphs || 0) + ' 段</span>' +
            '<span>' + (x.segments || 0) + ' 句</span><span>' + (x.chars || 0) + ' 字</span>' +
            '<span>' + (x.marks || 0) + ' 标记</span>' +
          '</div>' +
          '<div class="cmp-bar"><i style="width:' + w + '%"></i></div>' +
          (markTags ? '<div class="cmp-sec">标记</div>' + markTags : '') +
          (stages ? '<div class="cmp-sec">精简版结构</div><div class="cmp-stages">' + stages + '</div>' : '') +
          (x.review ? '<div class="cmp-sec">点评摘要</div><div class="cmp-rv">' + md2html(x.review.slice(0, 600)) + '</div>' : '') +
        '</div>';
    }).join('') + '</div></div>';
    body.querySelectorAll('.cmp-card').forEach((el) => {
      el.addEventListener('click', () => {
        closeModal($('#modalCompare'));
        switchStudent(el.dataset.sid);
      });
    });
    $('#compareTip').textContent = '共 ' + rows.length + ' 位学生 · 点任一卡片可切换到该学生继续记录';
  } catch (e) {
    body.innerHTML = '<div class="muted">加载失败：' + esc(e.message) + '</div>';
  }
}

function updateFloat() {
  const fm = $('#floatMark');
  const segs = (S.session && S.session.segments) || [];
  const target = segs.find((s) => s.id === S.sel) || segs[segs.length - 1];
  if (!target) { fm.classList.remove('show'); return; }
  fm.classList.add('show');
  $('#floatText').textContent = fmt(target.t_start) + '  ' + target.text.slice(0, 46);
}

/* ---------------- 输入电平自检（麦克风/内录是否真的在收音） ---------------- */
const LVL = { timer: 0, peak: 0 };
function lvlShow(level, running) {
  const bar = $('#levelBar');
  if (!bar) return;
  const i = bar.querySelector('i');
  const v = Number(level) || 0;
  // int16 均值口径：静音 <20，正常说话 300~8000，用对数映射
  let pct = v <= 0 ? 0 : Math.min(100, (Math.log10(v + 1) / Math.log10(6000)) * 100);
  if (pct < 2 && v > 0) pct = 2;
  i.style.width = pct.toFixed(1) + '%';
  bar.classList.toggle('hot', v >= 1200);
  bar.classList.toggle('mute', !running || v < 8);
  LVL.peak = Math.max(LVL.peak, v);
}
async function lvlTick() {
  try {
    const r = await api('/api/level');
    lvlShow(r.level, r.running);
    if (r.running) S.running = true;
  } catch (e) { /* 忽略 */ }
}
function lvlStart() {
  if (LVL.timer) return;
  LVL.peak = 0;
  LVL.timer = setInterval(lvlTick, 700);
}
function lvlStop() {
  clearInterval(LVL.timer);
  LVL.timer = 0;
  lvlShow(0, false);
}

function setRunning(on, keepT0) {
  S.running = on;
  $('#btnStart').disabled = on;
  $('#btnStop').disabled = !on;
  $('#statusDot').className = 'dot' + (on ? ' on' : '');
  $('#stateText').textContent = on ? ('听记中 · ' + ((S.session && S.session.device) || '系统内录')) : '待机';
  if (on) {
    if (!keepT0) S.t0 = Date.now();
    lvlStart();
    clearInterval(S.timer);
    S.timer = setInterval(() => {
      const sec = Math.floor((Date.now() - S.t0) / 1000);
      $('#timer').textContent = fmt(sec);
    }, 500);
  } else {
    clearInterval(S.timer);
    lvlStop();
  }
}

function findSeg(id) {
  return ((S.session && S.session.segments) || []).find((s) => s.id === id);
}
function findPara(pid) {
  return ((S.session && S.session.paragraphs) || []).find((p) => p.id === pid);
}
function upsertPara(p) {
  if (!S.session) return;
  S.session.paragraphs = S.session.paragraphs || [];
  const i = S.session.paragraphs.findIndex((x) => x.id === p.id);
  if (i >= 0) S.session.paragraphs[i] = p; else S.session.paragraphs.push(p);
  S.session.paragraphs.sort((a, b) => (a.idx || 0) - (b.idx || 0));
}

/* ---------------- 事件 ---------------- */
async function startRec() {
  try {
    const sel = $('#deviceSel');
    const dev = sel ? sel.value : '';
    const body = { kind: S.kind || 'loopback', device: dev === '' ? 0 : Number(dev), name: '' };
    const r = await api('/api/start', body);
    setRunning(true);
    const kt = S.kind === 'mic' ? '麦克风' : '系统内录';
    toast('已开始听记（' + kt + '）：' + (r.device || '系统默认设备'));
  } catch (e) { toast(e.message, true); }
}
async function stopRec() {
  try {
    const r = await api('/api/stop', {});
    setRunning(false);
    $('#timer').textContent = fmt(r.duration || 0);
    const n = (S.session.segments || []).length;
    const pn = (S.session.paragraphs || []).length;
    toast('已停止，共 ' + n + ' 句 / ' + pn + ' 段' + (r.saved ? '，已自动存档' : ''));
    // 刷新学生条（各自时长 / 句数）
    setTimeout(async () => {
      try {
        const st = await api('/api/state');
        S.students = st.session.students || S.students;
        renderStudents();
      } catch (e) { /* 忽略 */ }
    }, 400);
    // 答题/录制结束 → 自动触发最终点评
    if (n > 0) {
      if (window.__autoReviewTimer) clearTimeout(window.__autoReviewTimer);
      window.__autoReviewTimer = setTimeout(() => doReview(true), 600);
    } else {
      $('#reviewMeta').textContent = '本次没有识别到内容，无法生成点评';
    }
  } catch (e) { toast(e.message, true); }
}

async function markSeg(seg, patch) {
  Object.assign(seg, patch);
  renderTranscript(false); renderMarks(); renderPlain(false); updateFloat();
  try { await api('/api/mark', { id: seg.id, tags: seg.tags, ctags: seg.ctags, note: seg.note }); }
  catch (e) { toast(e.message, true); }
}

/* 自定义问题：保存 / 删除（现有四类标记没涵盖的问题都能记） */
async function saveCtags(seg, list) {
  const uniq = [];
  (list || []).forEach((x) => {
    const v = String(x || '').trim().replace(/[。；;]+$/, '');
    if (v && uniq.indexOf(v) < 0) uniq.push(v);
  });
  await markSeg(seg, { ctags: uniq });
}

/* 备注 / 自定义问题：多行输入框，自动增高与手动拖拽缩放共存（手动拖过的高度优先） */
function noteTaAuto(ta) {
  if (ta.dataset.manualH) { ta.style.height = ta.dataset.manualH + 'px'; return; }
  ta.style.height = 'auto';
  ta.style.height = Math.max(30, Math.min(ta.scrollHeight, 140)) + 'px';
}

function noteTaMake(extraCls, ph, val) {
  const ta = document.createElement('textarea');
  ta.className = 'noteinput ' + (extraCls || '');
  ta.rows = 1;
  ta.spellcheck = false;
  if (ph) ta.placeholder = ph;
  if (val) ta.value = val;
  noteTaAuto(ta);
  ta.addEventListener('input', () => noteTaAuto(ta));
  ta.addEventListener('pointerdown', () => { ta._h0 = ta.offsetHeight; });
  ta.addEventListener('pointerup', () => {
    const h = ta.offsetHeight;
    if (Math.abs(h - (ta._h0 || 0)) >= 3) { ta.dataset.manualH = String(h); ta.style.height = h + 'px'; }
  });
  return ta;
}

function openCtagInput(segEl, seg) {
  const body = segEl.querySelector('.body');
  if (!body || segEl.querySelector('.ctaginput')) return;
  const inp = noteTaMake('ctaginput', '填写四类标记没涵盖的问题，如「未结合题干」「对策不可落地」；多条用；分隔，Enter 保存，Shift+Enter 换行，Esc 取消');
  body.appendChild(inp);
  inp.focus();
  inp.addEventListener('keydown', async (ev) => {
    if (ev.key === 'Enter' && !ev.shiftKey) {
      ev.preventDefault();
      const add = inp.value.split(/[;；\n]+/).map((x) => x.trim()).filter(Boolean);
      if (!add.length) { inp.remove(); return; }
      inp.remove();
      await saveCtags(seg, (seg.ctags || []).concat(add));
      toast('已记下问题：' + add.join('、'));
    }
    if (ev.key === 'Escape') { inp.remove(); }
  });
  inp.addEventListener('blur', () => { setTimeout(() => { if (!inp.value.trim()) inp.remove(); }, 120); });
}

async function quickPara(pid, btn) {
  if (btn) { btn.disabled = true; btn.textContent = '点评中…'; }
  try {
    const r = await api('/api/paragraph/quick', { id: pid });
    upsertPara(r.para);
    renderTranscript(false);
    renderPlain(false);
    toast('第 ' + r.para.idx + ' 段点评已生成');
  } catch (e) {
    toast(e.message, true);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '本段点评'; }
  }
}

/* ---------------- 段落操作菜单：手动指定结构 / 本段 AI 点评 ---------------- */
/* 环节提示语：按环节名的功能语义给一句提示，题型自定义环节（如「前期准备」「开头」）也能命中 */
const STAGE_TIP_RULES = [
  [/准备|方案|分工|物资|调研|筹划/, '前期准备：定方案、明分工、备物资'],
  [/开展|推进|实施|执行|处置|解决|落实|对策|措施|做法|整改|善后/, '推进落实：讲清做法、责任主体与效果'],
  [/总结|收尾|结尾|收束|升华|提升|展望|回扣/, '总结收束：回顾成效、提炼提升'],
  [/分析|原因|影响|论证|展开|补充|细节/, '分析论证：析原因、讲影响、举证'],
  [/观点|表态|态度|亮明|立意|主题/, '亮明观点：一句话给出结论'],
  [/审题|入题|切入|开场|开头|引入|描述|呈现|框架/, '开场入题：点明话题、破题'],
];
function stageTip(name) {
  const s = String(name || '');
  if (!s) return '';
  for (const [re, tip] of STAGE_TIP_RULES) if (re.test(s)) return tip;
  return '归入「' + s + '」这一环节';
}
let CTX_PID = null;

function closeParaMenu() {
  const m = $('#ctxMenu');
  if (m) { m.style.display = 'none'; m.innerHTML = ''; }
  CTX_PID = null;
}

async function openParaMenu(pid, x, y) {
  const m = $('#ctxMenu');
  const p = findPara(pid);
  if (!m || !p) { toast('未找到该段落', true); return; }
  CTX_PID = pid;
  const st = stageMeta(p);
  // 可选环节 = 当前题型的答题环节（题型未配环节时后端回退通用环节）
  await ensureStageOpts(true);
  let opts = STAGE_OPTS.slice();
  if (st.stage && opts.indexOf(st.stage) < 0) opts.push(st.stage);   // 历史数据里的旧环节也要能显示
  const btns = opts.map((k) =>
    '<button class="ctx-i" data-stage="' + esc(k) + '" title="' + esc(stageTip(k)) + '">' +
      '<i class="sd k-' + stageK(k) + '"></i><span>' + esc(k) + '</span>' +
      (st.stage === k ? '<em class="cur">' + (st.man ? '当前·手动' : '当前') + '</em>' : '') +
    '</button>').join('');
  m.innerHTML =
    '<div class="ctx-t">第 ' + p.idx + ' 段 · 指定结构归属' +
      (st.why ? '<span class="ctx-why" title="' + esc('自动判定依据：' + st.why) + '">' + esc(st.confTxt) + '</span>' : '') +
    '</div>' +
    btns +
    '<button class="ctx-i" data-stage=""' + (st.man ? '' : ' disabled') + '>' +
      '<i class="sd sd-auto"></i><span>恢复自动语义判定</span>' +
      (st.man ? '' : '<em class="cur">自动</em>') +
    '</button>' +
    '<div class="ctx-sep"></div>' +
    '<button class="ctx-i" data-act="review"><i class="sd sd-ai"></i><span>本段 AI 点评</span></button>' +
    '<button class="ctx-i" data-act="why"><i class="sd sd-info"></i><span>查看判定依据</span></button>';
  m.style.display = 'block';
  const w = m.offsetWidth || 210, h = m.offsetHeight || 240;
  const vw = window.innerWidth, vh = window.innerHeight;
  m.style.left = Math.max(8, Math.min(x, vw - w - 8)) + 'px';
  m.style.top = Math.max(8, Math.min(y, vh - h - 8)) + 'px';
}

async function setParaStage(pid, stage) {
  try {
    const r = await api('/api/plain/stage', { pid: pid, stage: stage || '' });
    const p = r.para || {};
    upsertPara(p);
    renderTranscript(false); renderPlain(false);
    const st = stageMeta(p);
    if (stage) toast('第 ' + p.idx + ' 段已指定为「' + st.label + '」');
    else toast('第 ' + p.idx + ' 段已恢复自动判定' + (st.why ? '（' + st.why + '）' : ''));
  } catch (e) { toast(e.message, true); }
}

function showParaWhy(pid) {
  const p = findPara(pid);
  if (!p) return;
  const st = stageMeta(p);
  toast('第 ' + p.idx + ' 段：' + st.label + '（' +
    (st.man ? '手动指定' : (st.confTxt || '自动判定')) +
    (st.why ? '｜依据：' + st.why : '｜暂无依据') + '）');
}

document.addEventListener('click', (e) => {
  const m = $('#ctxMenu');
  if (!m || m.style.display === 'none') return;
  // 点「结构/点评」按钮本身：交给打开逻辑处理，不要立刻关掉刚弹出的菜单
  if (e.target.closest && e.target.closest('button[data-act="pmenu"]')) return;
  if (!m.contains(e.target)) { closeParaMenu(); return; }
  const b = e.target.closest('.ctx-i');
  if (!b || b.disabled) return;
  const pid = CTX_PID;
  closeParaMenu();
  if (!pid) return;
  const ds = b.dataset.stage;
  const act = b.dataset.act;
  if (act === 'review') {
    if (!S.config || !S.config.has_key) { toast('请先在「设置」里配置 API Key', true); return; }
    quickPara(pid, null);
  } else if (act === 'why') {
    showParaWhy(pid);
  } else if (ds !== undefined) {
    setParaStage(pid, ds);
  }
});
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeParaMenu(); });
window.addEventListener('resize', closeParaMenu);
document.addEventListener('scroll', () => closeParaMenu(), true);

/* 右键任意段落（逐字稿 / 精简版）→ 弹出结构指定菜单 */
['#transcript', '#plainList'].forEach((sel) => {
  const el = $(sel);
  if (!el) return;
  el.addEventListener('contextmenu', (e) => {
    const p = e.target.closest('[data-pid]');
    if (!p) return;
    e.preventDefault();
    openParaMenu(p.dataset.pid, e.clientX, e.clientY);
  });
});

$('#transcript').addEventListener('click', async (e) => {
  const pEl = e.target.closest('.para');
  const pBtn = e.target.closest('button[data-act="pquick"]');
  if (pBtn && pEl) {
    if (!S.config || !S.config.has_key) { toast('请先在「设置」里配置 API Key', true); return; }
    await quickPara(pEl.dataset.pid, pBtn);
    return;
  }
  const pMenuBtn = e.target.closest('button[data-act="pmenu"]');
  if (pMenuBtn && pEl) {
    const r = pMenuBtn.getBoundingClientRect();
    openParaMenu(pEl.dataset.pid, r.left, r.bottom + 4, 'transcript');
    return;
  }

  const segEl = e.target.closest('.seg');
  if (!segEl) return;
  const seg = findSeg(segEl.dataset.id);
  if (!seg) return;
  const btn = e.target.closest('button[data-act]');
  const delBtn = e.target.closest('.ctagx');

  // 自定义问题标上的 ×：删除这一条
  if (delBtn) {
    e.stopPropagation();
    const i = Number(delBtn.dataset.i);
    const list = (seg.ctags || []).slice();
    if (i >= 0 && i < list.length) {
      const rm = list.splice(i, 1)[0];
      await saveCtags(seg, list);
      toast('已删除问题：' + rm);
    }
    return;
  }

  /* 点击落在输入类元素（备注 / 自定义问题 textarea、input、select 等）上时，
     绝不能重渲染段落 —— 否则正在编辑的输入框会被销毁、内容丢失、拖拽缩放被打断 */
  if (e.target.closest('input, textarea, select, [contenteditable="true"]')) return;

  if (!btn) {
    S.sel = (S.sel === seg.id ? null : seg.id);
    renderTranscript(false); updateFloat();
    return;
  }

  const act = btn.dataset.act;
  if (act === 'tag') {
    const t = btn.dataset.tag;
    const tags = new Set(seg.tags || []);
    tags.has(t) ? tags.delete(t) : tags.add(t);
    await markSeg(seg, { tags: Array.from(tags) });
  } else if (act === 'ctag') {
    openCtagInput(segEl, seg);
  } else if (act === 'menu') {
    const r = btn.getBoundingClientRect();
    openParaMenu(segEl.closest('.para').dataset.pid, r.left, r.bottom + 4, 'transcript');
  } else if (act === 'note') {
    const body = segEl.querySelector('.body');
    if (segEl.querySelector('.noteinput')) return;
    const inp = noteTaMake('', '输入备注后按 Enter 保存，Shift+Enter 换行，Esc 取消', seg.note || '');
    body.appendChild(inp);
    inp.focus();
    inp.addEventListener('keydown', async (ev) => {
      if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); inp.remove(); await markSeg(seg, { note: inp.value.trim() }); }
      if (ev.key === 'Escape') { inp.remove(); }
    });
  } else if (act === 'quick') {
    btn.textContent = '分析中…';
    try {
      const r = await api('/api/quick', { id: seg.id });
      seg.ai_note = r.ai_note;
      renderTranscript(false);
      toast('已生成速评');
    } catch (err) { toast(err.message, true); btn.textContent = 'AI速评'; }
  } else if (act === 'del') {
    try {
      await api('/api/segment/delete', { id: seg.id });
      const r = await api('/api/state');
      S.session = r.session;
      renderTranscript(false); renderMarks(); updateFloat();
    } catch (err) { toast(err.message, true); }
  }
});

$('#marks').addEventListener('click', (e) => {
  const it = e.target.closest('.mark-item');
  if (!it) return;
  S.sel = it.dataset.id;
  renderTranscript(false);
  const el = document.querySelector('.seg.sel');
  if (el) {
    el.scrollIntoView({ block: 'center', behavior: 'smooth' });
    // 逐字稿对应句高亮闪烁
    el.classList.remove('hit');
    void el.offsetWidth;
    el.classList.add('hit');
    clearTimeout(el._hit);
    el._hit = setTimeout(() => el.classList.remove('hit'), 1600);
  }
  // 同步定位到精简版里对应的那句话（加粗变色 + 高亮闪烁）
  hitSent(it.dataset.id, true);
});

/* 快捷键 */
document.addEventListener('keydown', (e) => {
  const tag = (e.target.tagName || '').toLowerCase();
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
  const segs = (S.session && S.session.segments) || [];
  if (!segs.length) return;
  const seg = segs.find((s) => s.id === S.sel) || segs[segs.length - 1];

  if (['1', '2', '3', '4'].includes(e.key)) {
    e.preventDefault();
    const t = TAGS[Number(e.key) - 1].k;
    const tags = new Set(seg.tags || []);
    tags.has(t) ? tags.delete(t) : tags.add(t);
    markSeg(seg, { tags: Array.from(tags) });
    toast('已标记「' + t + '」');
  } else if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    S.sel = seg.id;
    const el = document.querySelector('.seg[data-id="' + seg.id + '"]');
    if (el) { el.scrollIntoView({ block: 'center', behavior: 'smooth' }); el.querySelector('button[data-act="note"]').click(); }
  } else if (e.key === ' ') {
    e.preventDefault();
    S.running ? stopRec() : startRec();
  }
});

/* ---------------- 设备 / 题库 / 设置 ---------------- */
let DEV_LIST = [];
let DEV_SAVED = { kind: 'loopback', name: '', index: -1 };

function devOf(kind) {
  return (DEV_LIST || []).filter((d) => (d.kind || 'loopback') === kind);
}

function devOptionsHTML(kind) {
  const def = kind === 'mic' ? '系统默认麦克风（自动）' : '系统默认输出（自动）';
  return '<option value="">' + def + '</option>' +
    devOf(kind).map((d) => '<option value="' + d.index + '" data-name="' + esc(d.name) + '">' +
      esc(d.name) + '</option>').join('');
}

/* 按记忆选中设备：优先同名（换机/换序号也认得出），其次同序号 */
function pickSaved(kind) {
  const cands = devOf(kind);
  const nm = (DEV_SAVED.name || '').trim().toLowerCase();
  if (nm) {
    const hit = cands.find((d) => String(d.name).trim().toLowerCase() === nm);
    if (hit) return String(hit.index);
  }
  if ((DEV_SAVED.kind || 'loopback') === kind) {
    const hit2 = cands.find((d) => Number(d.index) === Number(DEV_SAVED.index));
    if (hit2) return String(hit2.index);
  }
  return '';
}

/* 切换输入源种类：系统内录 / 麦克风（两端下拉框同步） */
function setKind(kind, useSaved) {
  const k = kind === 'mic' ? 'mic' : 'loopback';
  S.kind = k;
  $$('#srcSeg .segbtn').forEach((b) => {
    b.classList.toggle('active', b.dataset.kind === k);
  });
  const sel = $('#deviceSel');
  if (sel) {
    sel.innerHTML = devOptionsHTML(k);
    sel.title = k === 'mic' ? '麦克风（录现场说话）' : '系统内录（录电脑正在播放的声音）';
    sel.value = useSaved === false ? '' : pickSaved(k);
  }
  const cs = $('#cfgKind');
  const cd = $('#cfgDevice');
  if (cs) cs.value = k;
  if (cd) {
    cd.innerHTML = devOptionsHTML(k);
    cd.value = pickSaved(k);
  }
  const hint = $('#srcHint');
  if (hint) {
    hint.textContent = k === 'mic'
      ? '麦克风：适合现场面试 / 面对面答题，直接录说话声'
      : '系统内录：适合线上问答 / 网课，录电脑正在播放的声音';
  }
}

async function loadDevices(keepKind) {
  try {
    const r = await api('/api/devices');
    DEV_LIST = r.devices || [];
    DEV_SAVED = r.saved || DEV_SAVED;
    const kind = keepKind ? (S.kind || DEV_SAVED.kind) : (DEV_SAVED.kind || 'loopback');
    setKind(kind, true);
  } catch (e) { /* 忽略 */ }
}

let CUR_BANK = null;

async function loadBanks() {
  const r = await api('/api/bank');
  const list = $('#bankList');
  const empty = $('#bankEmpty');
  if (!r.banks.length) {
    list.innerHTML = '';
    if (empty) {
      empty.style.display = '';
      empty.innerHTML =
        '<div class="be-ico">▤</div>' +
        '<div class="be-t">题库还是空的</div>' +
        '<div class="be-d">题库不随软件内置，需自行导入或新建，首次启动题库为空。可以导入现成的题目文本 / Word，或直接新建一道题——粘贴题干后会自动识别题型与答题结构，确认后写入题库。</div>' +
        '<div class="be-btns">' +
          '<button class="mini primary-m" id="beNewQ">＋ 新建题目</button>' +
          '<button class="mini" id="beImportText">导入文本</button>' +
          '<button class="mini" id="beImportWord">导入 Word</button>' +
        '</div>';
      const bn = $('#beNewQ');
      if (bn) bn.addEventListener('click', () => {
        const t = $('#question').value.trim();
        if (t.length >= 4) qaAnalyze(t, null);
        else {
          qaFillForm(QAN || { question: '', qtype: $('#qtype').value.trim() });
          openModal('#modalQAnalyze');
          qaFillDatalist();
          $('#qaQuestion').focus();
          toast('把题目原文粘贴到弹层里，再点「重新分析」');
        }
      });
      const bi = $('#beImportText');
      if (bi) bi.addEventListener('click', () => { const b = $('#btnImport'); if (b) b.click(); });
      const bw = $('#beImportWord');
      if (bw) bw.addEventListener('click', () => { const f = $('#bankFile'); if (f) f.click(); });
    }
    $('#bankItems').innerHTML = '';
    return;
  }
  if (empty) { empty.style.display = 'none'; empty.innerHTML = ''; }
  list.innerHTML = r.banks.map((b) =>
    '<div class="bank-row" data-file="' + esc(b.file) + '">' +
      '<span class="bn">' + esc(b.name) + '</span><span class="bc">' + b.count + ' 题</span>' +
      '<button class="bdel" data-file="' + esc(b.file) + '" title="删除整个题库">×</button>' +
    '</div>'
  ).join('');
  list.querySelectorAll('.bank-row').forEach((row) => {
    row.addEventListener('click', (e) => {
      if (e.target.closest('.bdel')) return;
      showBankItems(row.dataset.file);
    });
  });
  list.querySelectorAll('.bdel').forEach((btn) => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const file = btn.dataset.file;
      if (!confirm('删除题库文件「' + file + '」？该操作不可恢复。')) return;
      try {
        await api('/api/bank/delete', { file });
        toast('已删除题库');
        if (CUR_BANK === file) closeBankItems();
        await loadBanks();
      } catch (err) { toast(err.message, true); }
    });
  });
}

let BANK_ITEMS = [];

async function showBankItems(file) {
  CUR_BANK = file;
  const data = await api('/api/bank/load?file=' + encodeURIComponent(file));
  BANK_ITEMS = data.items || [];
  $('#bankBackBar').style.display = 'flex';
  $('#bankCurName').textContent = (data.name || file) + ' · 共 ' + BANK_ITEMS.length + ' 题';
  $('#bankList').style.display = 'none';
  setVal($('#bankFilter'), '', true);
  $('#bankFilter').style.display = '';
  renderBankItems('');
  await aioRestore(file);
}

/* 返回题库列表：恢复列表视图，清空题目面板 */
function closeBankItems() {
  CUR_BANK = null;
  BANK_ITEMS = [];
  $('#bankItems').innerHTML = '';
  $('#bankBackBar').style.display = 'none';
  setVal($('#bankFilter'), '', true);
  $('#bankFilter').style.display = 'none';
  $('#bankList').style.display = 'flex';
  // 退出题库时清空 AI 优化状态，避免带到下一个题库
  AIO.sel.clear(); AIO.before = {}; AIO.fail.clear();
  AIO.jid = ''; AIO.okN = 0; AIO.failN = 0; AIO.done = 0; AIO.total = 0;
  clearTimeout(AIO.timer);
}

// ---- 题库 AI 优化状态（勾选 / 进度 / 前后对比 / 撤销） ----
const AIO = { sel: new Set(), before: {}, fail: new Set(), jid: '', timer: null,
              total: 0, done: 0, okN: 0, failN: 0, running: false, stopping: false,
              liveTimer: null, liveOpen: null };

/* 打开题库时把最近的优化记录恢复出来（前后对比 / 撤销按钮不因刷新丢失） */
async function aioRestore(file) {
  try {
    const d = await api('/api/bank/ai_optimize_last?file=' + encodeURIComponent(file));
    const snap = (d && d.snap) || {};
    const keys = Object.keys(snap);
    if (!d || !d.jid || !keys.length) return;
    AIO.before = {};
    keys.forEach((k) => { AIO.before[Number(k)] = snap[k]; });
    AIO.jid = d.jid;
    AIO.okN = d.ok_n || keys.length;
    AIO.failN = d.fail_n || 0;
    AIO.total = d.total || keys.length;
    AIO.done = d.done || keys.length;
    // 失败清单取并集：不覆盖本次会话里刚记录的失败题（否则「重试」按钮会消失）
    const rf = (d.rows || []).filter((r) => !r.ok).map((r) => r.index);
    AIO.fail = new Set([...(AIO.fail || []), ...rf]);
    renderBankItems(($('#bankFilter') && $('#bankFilter').value) || '');
  } catch (e) { /* 记录恢复失败不影响查看题库 */ }
}

function aioSync() {
  const n = AIO.sel.size;
  const cnt = $('#aiOptCount');
  if (cnt) cnt.innerHTML = '已选 <b>' + n + '</b> 题';
  const all = $('#aiOptAll');
  if (all) {
    const total = BANK_ITEMS.length;
    all.checked = total > 0 && n === total;
    all.indeterminate = n > 0 && n < total;
  }
  const sel = $('#btnAiOptSel');
  if (sel) sel.disabled = AIO.running || n === 0;
  const allq = $('#btnAiOptAllQ');
  if (allq) allq.disabled = AIO.running || BANK_ITEMS.length === 0;
  const undo = $('#btnAiOptUndo');
  if (undo) undo.disabled = AIO.running || !AIO.jid || !(AIO.okN > 0);
  const wrap = $('#aiOptBarWrap');
  // 一题都没处理时整条隐藏（既没在跑、也没有任何已处理记录）；
  // 处理过题才留着：点它就能回看这一轮逐题的优化前后内容与 AI 原始返回
  if (wrap) wrap.hidden = !(AIO.running || (AIO.total > 0 && AIO.done > 0));
  const stop = $('#btnAiOptCancel');
  if (stop) {
    stop.hidden = !AIO.running;          // 只在优化运行时出现
    stop.disabled = !!AIO.stopping;
    stop.textContent = AIO.stopping ? '正在停止…' : '停止';
  }
  const bar = $('#aiOptBar');
  if (bar) bar.style.width = (AIO.total ? Math.round(100 * AIO.done / AIO.total) : 0) + '%';
  const pct = $('#aiOptPct');
  if (pct) pct.textContent = (AIO.total ? Math.round(100 * AIO.done / AIO.total) : 0) + '%';
  if (wrap) wrap.classList.toggle('running', !!AIO.running);
}

function aioSay(txt, bad) {
  const el = $('#aiOptSt');
  if (!el) return;
  el.textContent = txt || '';
  el.classList.toggle('bad', !!bad);
  if (bad) toast(txt, true);
}

function aioSnapOf(it) {
  return {
    question: it.question || '', qtype: it.qtype || '',
    exam: (it.exam || []).slice(), keys: (it.keys || []).slice(),
    structure: JSON.parse(JSON.stringify(it.structure || [])), ref: it.ref || '',
  };
}

/* ---------- AI 优化对比（大窗口左右并排） ---------- */
const AIO_FIELDS = [
  { k: 'question', name: '题干' },
  { k: 'qtype', name: '题型' },
  { k: 'exam', name: '审题要点' },
  { k: 'structure', name: '答题环节' },
  { k: 'keys', name: '关键要点' },
  { k: 'ref', name: '参考要点' },
];

/* 取某字段的可对比行数组（答题环节按「· 环节（小标题）：要点1；要点2」合并成一行） */
function aioFieldLines(x, k) {
  if (!x) return ['（空）'];
  if (k === 'structure') {
    const rows = (x.structure || []).map((s) => '· ' + (s.stage || '') +
      (s.label ? '（' + s.label + '）' : '') +
      ((s.points || []).length ? '：' + (s.points || []).join('；') : ''));
    return rows.length ? rows : ['（空）'];
  }
  if (k === 'exam' || k === 'keys') {
    const rows = (x[k] || []).map((s) => String(s == null ? '' : s).trim()).filter(Boolean);
    return rows.length ? rows : ['（空）'];
  }
  const t = String(x[k] == null ? '' : x[k]);
  if (!t.trim()) return ['（空）'];
  const rows = t.split(/\r?\n/);
  while (rows.length && !rows[rows.length - 1].trim()) rows.pop();
  return rows.length ? rows : ['（空）'];
}

/* 行级 LCS 差异：返回 eq / del / ins 操作序列 */
function aioDiffLines(a, b) {
  const n = a.length, m = b.length;
  const dp = [];
  for (let i = 0; i <= n; i++) dp.push(new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = (a[i] === b[j]) ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const ops = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { ops.push({ t: 'eq', ai: i, bi: j }); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { ops.push({ t: 'del', ai: i }); i++; }
    else { ops.push({ t: 'ins', bi: j }); j++; }
  }
  while (i < n) { ops.push({ t: 'del', ai: i }); i++; }
  while (j < m) { ops.push({ t: 'ins', bi: j }); j++; }
  return ops;
}

/* 把「删除块」与「新增块」按顺序配对：返回 { 新行号: 旧行号 }，用于字段内中段差异高亮 */
function aioPairsOf(ops) {
  const map = {};
  let pend = [];
  ops.forEach((op) => {
    if (op.t === 'del') { pend.push(op.ai); return; }
    if (op.t === 'eq') { pend = []; return; }
    if (pend.length) map[op.bi] = pend.shift();
  });
  return map;
}

/* 单行文本高亮：公共前后缀之外的中段差异用 <em class="chg"> 包裹 */
function aioMark(oldLine, newLine) {
  const o = String(oldLine == null ? '' : oldLine);
  const n = String(newLine == null ? '' : newLine);
  if (!n) return '';
  if (!o || o === n) return esc(n);
  const max = Math.min(o.length, n.length);
  let p = 0;
  while (p < max && o.charAt(p) === n.charAt(p)) p++;
  let s = 0;
  while (s < max - p && o.charAt(o.length - 1 - s) === n.charAt(n.length - 1 - s)) s++;
  const head = n.slice(0, p);
  const mid = n.slice(p, n.length - s);
  const tail = n.slice(n.length - s);
  if (!mid) return esc(n);
  return esc(head) + '<em class="chg">' + esc(mid) + '</em>' + esc(tail);
}

/* 生成某字段左右两栏 HTML：[左（优化前）, 右（优化后）] */
function aioFieldPair(oldIt, newIt, k) {
  const oldLines = aioFieldLines(oldIt, k);
  const newLines = aioFieldLines(newIt, k);
  const oldEmpty = oldLines.length === 1 && oldLines[0] === '（空）';
  const newEmpty = newLines.length === 1 && newLines[0] === '（空）';
  const lineNone = (t) => '<div class="df-l df-none">' + esc(t) + '</div>';
  // 某一侧整体为空：占位符不参与比对，另一侧全部按新增/删除处理
  if (oldEmpty || newEmpty) {
    return [
      oldEmpty ? lineNone('（空）') : oldLines.map((s) => '<div class="df-l chg-del">' + esc(s) + '</div>').join(''),
      newEmpty ? lineNone('（无）') : newLines.map((s) => '<div class="df-l"><em class="chg">' + esc(s) + '</em></div>').join('')
    ];
  }
  const ops = aioDiffLines(oldLines, newLines);
  const pairs = aioPairsOf(ops);
  let left = '', right = '';
  ops.forEach((op) => {
    if (op.t === 'eq') {
      left += '<div class="df-l">' + esc(oldLines[op.ai]) + '</div>';
      right += '<div class="df-l">' + esc(newLines[op.bi]) + '</div>';
    } else if (op.t === 'del') {
      left += '<div class="df-l chg-del">' + esc(oldLines[op.ai]) + '</div>';
    } else {
      const ai = pairs[op.bi];
      right += '<div class="df-l">' + (ai === undefined
        ? '<em class="chg">' + esc(newLines[op.bi]) + '</em>'
        : aioMark(oldLines[ai], newLines[op.bi])) + '</div>';
    }
  });
  if (!left) left = '<div class="df-l df-none">（无）</div>';
  if (!right) right = '<div class="df-l df-none">（无）</div>';
  return [left, right];
}

/* 打开「AI 优化对比」弹层 */
function renderAiDiff(i) {
  const b = AIO.before[i];
  const a = BANK_ITEMS[i];
  if (!b || !a) { toast('这一题还没有 AI 优化记录，无法对比'); return; }
  const no = a.qno || (i + 1);
  $('#aiDiffTitle').textContent = 'AI 优化对比 · 第 ' + no + ' 题';
  let html = '<div class="df-h">优化前</div><div class="df-h df-h-new">优化后</div>';
  AIO_FIELDS.forEach((f) => {
    const pair = aioFieldPair(b, a, f.k);
    const same = aioFieldLines(b, f.k).join('\n') === aioFieldLines(a, f.k).join('\n');
    const cls = same ? ' df-same' : '';
    html += '<div class="df-cell df-old' + cls + '"><div class="df-k">' + f.name + '</div>' + pair[0] + '</div>' +
      '<div class="df-cell df-new' + cls + '"><div class="df-k">' + f.name + '</div>' + pair[1] + '</div>';
  });
  $('#dfGrid').innerHTML = html;
  openModal('#modalAiDiff');
}

/* ---------- AI 优化「实时输出」（点进度条打开，逐题看前后与 AI 原始返回） ---------- */
const AIO_LIVE_TXT = { run: '处理中', ok: '已完成', fail: '失败', skip: '未处理', wait: '排队中' };

function aioLiveIsOpen() {
  const el = $('#modalAiLive');
  return !!(el && el.classList.contains('open'));
}

function aioLiveSec(v) {
  const s = Number(v || 0);
  return s > 0 ? (s >= 10 ? Math.round(s) + 's' : s.toFixed(1) + 's') : '';
}

/* 单题内容：AI 实时返回的文字 + 优化前 / 优化后并排比对 */
function aioLiveBody(row) {
  const st = row.state;
  let head = '';
  if (st === 'wait') {
    head = '<div class="aol-wait">排队中，轮到这一题时这里会实时出现 AI 返回的文字。</div>';
  } else if (st === 'skip') {
    head = '<div class="aol-wait">已停止，这一题没有处理。</div>';
  }
  // AI 实时返回：运行中直接铺开、随返回增长；结束后保留全文可展开
  let live = '';
  if (row.raw) {
    const n = row.raw_len || row.raw.length;
    const tail = row.raw_cut ? '仅显示最后 ' + row.raw.length + ' 字' : '';
    const hd = (st === 'run')
      ? ('AI 正在返回… <b>' + n + '</b> 字<span class="aol-cur"></span>' + (tail ? '（' + tail + '）' : ''))
      : ('AI 原始返回（' + n + ' 字' + (tail ? '，' + tail : '') + '）');
    if (st === 'run') {
      live = '<div class="aol-stream"><div class="aol-stream-hd">' + hd + '</div>' +
             '<pre class="aol-stream-pre">' + esc(row.raw) + '</pre></div>';
    } else {
      live = '<details class="aol-raw"><summary>' + hd + '</summary>' +
             '<pre>' + esc(row.raw) + '</pre></details>';
    }
  } else if (st === 'run') {
    head = '<div class="aol-wait">已发出请求，正在等 AI 返回第一个字…（串行处理，一题一题来）</div>';
  }
  if (!row.after) {
    return (head || '') + live + (row.msg ? '<div class="aol-msg">' + esc(row.msg) + '</div>' : '');
  }
  let html = '<div class="df-grid">';
  AIO_FIELDS.forEach((f) => {
    const pair = aioFieldPair(row.before || {}, row.after || {}, f.k);
    const same = aioFieldLines(row.before || {}, f.k).join('\n') === aioFieldLines(row.after || {}, f.k).join('\n');
    const cls = same ? ' df-same' : '';
    html += '<div class="df-cell df-old' + cls + '"><div class="df-k">' + f.name + '</div>' + pair[0] + '</div>' +
            '<div class="df-cell df-new' + cls + '"><div class="df-k">' + f.name + '</div>' + pair[1] + '</div>';
  });
  html += '</div>';
  if (row.msg) html += '<div class="aol-msg">' + esc(row.msg) + '</div>';
  return (head || '') + html + live;
}

/* 渲染整个实时列表：进行中与最新完成的自动展开，其余折叠；保留用户手动展开过的题 */
function aioLiveRender(r) {
  const rows = (r.live || []).slice();
  const tot = r.total || rows.length;
  const done = r.done || 0;
  const pct = tot ? Math.round(100 * done / tot) : 0;
  const bw = $('#aiLiveBar'); if (bw) bw.style.width = pct + '%';
  const bp = $('#aiLivePct'); if (bp) bp.textContent = pct + '%';
  // 一题都还没处理完时，进度条整条隐藏（避免开局就是一条空条）
  const barWrap = $('#aiLiveBarWrap');
  if (barWrap) barWrap.hidden = !(tot > 0 && done > 0);
  const sum = $('#aiLiveSum');
  if (sum) {
    sum.textContent = '共 ' + tot + ' 题 · 已处理 ' + done + ' · 成功 ' + (r.ok_n || 0) + ' · 失败 ' + (r.fail_n || 0) +
      (r.elapsed ? (' · 用时 ' + aioLiveSec(r.elapsed)) : '');
  }
  const line = $('#aiLiveSt');
  if (line) {
    const stTxt = r.status === 'running'
      ? ('正在处理第 ' + (r.cur_no || 0) + ' 题 / 共 ' + tot + ' 题（串行：一题一题走 AI）')
      : (r.msg || '已结束');
    line.textContent = stTxt + (r.detail && r.detail.cached ? '｜连通性探测已复用缓存' : '');
  }
  const box = $('#aiLiveList');
  if (!box) return;
  if (!rows.length) {
    box.innerHTML = '<div class="aol-empty">这次任务没有逐题记录（可能是任务已过期）。重新跑一次「AI 优化」后可在这里实时查看。</div>';
    return;
  }
  // 自动展开：进行中的题 + 最近完成的一题
  const autoOn = new Set();
  let lastDone = -1;
  rows.forEach((x) => {
    if (x.state === 'run') autoOn.add(x.index);
    if (x.state === 'ok' || x.state === 'fail') lastDone = x.index;
  });
  if (lastDone >= 0) autoOn.add(lastDone);
  if (!AIO.liveOpen) AIO.liveOpen = new Set();
  box.innerHTML = rows.map((x) => {
    const open = AIO.liveOpen.has(x.index) || autoOn.has(x.index);
    const st = x.state || 'wait';
    const sec = aioLiveSec(x.sec);
    const q = x.q || ((x.before && x.before.question) || '') || '（题干为空）';
    return '<div class="aol-item st-' + st + '" data-li="' + x.index + '">' +
      '<div class="aol-hd" role="button" tabindex="0">' +
        '<span class="aol-no">第 ' + (x.no || (x.index + 1)) + ' 题</span>' +
        '<span class="aol-tag">' + (AIO_LIVE_TXT[st] || st) + (sec ? (' · ' + sec) : '') + '</span>' +
        '<span class="aol-q">' + esc(String(q).slice(0, 90)) + '</span>' +
        '<span class="aol-x">' + (open ? '收起' : '展开') + '</span>' +
      '</div>' +
      '<div class="aol-bd"' + (open ? '' : ' hidden') + '>' + aioLiveBody(x) + '</div>' +
    '</div>';
  }).join('');
  // 运行中把正在返回的文字自动滚到最新一行，保持「AI 正在写」的实时观感
  box.querySelectorAll('.aol-item.st-run .aol-stream-pre').forEach((pre) => {
    pre.scrollTop = pre.scrollHeight;
  });
}

async function aioLiveTick(once) {
  clearTimeout(AIO.liveTimer);
  AIO.liveTimer = null;
  if (!AIO.jid) {
    const box = $('#aiLiveList');
    if (box) box.innerHTML = '<div class="aol-empty">还没有优化任务。勾选题目后点「AI 优化所选」或「优化全库」，再点进度条就能看实时输出。</div>';
    return;
  }
  try {
    const r = await api('/api/bank/ai_optimize_job?jid=' + encodeURIComponent(AIO.jid) + '&live=1&rawmax=4000');
    aioLiveRender(r);
    if (r.status === 'running' && !once && aioLiveIsOpen()) {
      // 0.4s 一帧：既能看到 AI 逐段吐字，又不至于把本地服务刷爆
      AIO.liveTimer = setTimeout(() => aioLiveTick(false), 400);
    }
  } catch (e) {
    const line = $('#aiLiveSt');
    if (line) line.textContent = '取实时输出失败：' + e.message;
    if (!once && aioLiveIsOpen()) AIO.liveTimer = setTimeout(() => aioLiveTick(false), 1500);
  }
}

function aioLiveOpen() {
  AIO.liveOpen = new Set();
  openModal('#modalAiLive');
  aioLiveTick(true);
}

function aioLiveStop() {
  clearTimeout(AIO.liveTimer);
  AIO.liveTimer = null;
}

/* 点进度条 / 回车 / 空格都能打开；运行中每 0.8s 自动刷新，结束后停在最后一帧 */
(function () {
  const el = $('#aiOptBarWrap');
  if (el) {
    el.addEventListener('click', aioLiveOpen);
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); aioLiveOpen(); }
    });
  }
})();
(function () {
  const btn = $('#btnAiLiveRefresh');
  if (btn) btn.addEventListener('click', () => aioLiveTick(true));
  const modal = $('#modalAiLive');
  if (modal) {
    // 弹层被关掉（× / Esc / 点遮罩）就停止轮询，别在后台空转
    const mo = new MutationObserver(() => { if (!aioLiveIsOpen()) aioLiveStop(); });
    mo.observe(modal, { attributes: true, attributeFilter: ['class'] });
    // 折叠 / 展开单题
    modal.addEventListener('click', (e) => {
      const hd = e.target.closest && e.target.closest('.aol-hd');
      if (!hd) return;
      const item = hd.parentNode;
      const idx = Number(item.getAttribute('data-li'));
      const bd = item.querySelector('.aol-bd');
      const x = item.querySelector('.aol-x');
      if (!bd) return;
      if (bd.hidden) { bd.hidden = false; AIO.liveOpen.add(idx); if (x) x.textContent = '收起'; }
      else { bd.hidden = true; AIO.liveOpen.delete(idx); if (x) x.textContent = '展开'; }
    });
  }
})();

function aioPoll() {
  if (!AIO.jid) return;
  clearTimeout(AIO.timer);
  AIO.timer = setTimeout(async () => {
    try {
      const r = await api('/api/bank/ai_optimize_job?jid=' + encodeURIComponent(AIO.jid));
      AIO.done = r.done || 0; AIO.total = r.total || AIO.total;
      AIO.okN = r.ok_n || 0; AIO.failN = r.fail_n || 0;
      (r.rows || []).forEach((row) => {
        if (row.ok) AIO.fail.delete(row.index); else AIO.fail.add(row.index);
      });
      if (r.status === 'running') {
        if (AIO.stopping || r.cancel) {
          AIO.stopping = true;
          aioSay('正在停止… 已完成 ' + AIO.done + '/' + AIO.total + '（成功 ' + AIO.okN + '，失败 ' + AIO.failN + '）', false);
        } else {
          aioSay('AI 优化中… 已完成 ' + AIO.done + '/' + AIO.total + '（成功 ' + AIO.okN + '，失败 ' + AIO.failN + '）', false);
        }
        aioSync();
        aioPoll();
        return;
      }
      // 结束
      AIO.running = false;
      AIO.stopping = false;
      aioLiveStop();
      if (aioLiveIsOpen()) aioLiveTick(true);   // 收尾刷新一帧，停在最终结果上
      const d = r.detail || {};
      if (r.state === 'cancel' || r.cancel) {
        aioSay(r.msg || ('已停止：成功 ' + AIO.okN + ' 题，未处理 ' + Math.max(0, AIO.total - AIO.done) + ' 题'), AIO.failN > 0);
      } else if (r.pending && r.pending.length) {
        AIO.sayPending = r.pending;
        aioSay('AI 优化中断：剩余 ' + r.pending.length + ' 题未处理（可重新点「AI 优化」继续）', true);
      } else if (r.state === 'off') {
        aioSay(r.msg || '未配置 API Key，请先到「设置」填写接口地址、模型名与 Key', true);
      } else if (r.state === 'fail' && !AIO.okN) {
        aioSay((r.msg || 'AI 优化失败') + '｜真实请求 ' + (d.url || '-') + '（模型 ' + (d.model || '-') +
               '）→ HTTP ' + (d.status != null ? d.status : '无响应') +
               '｜原始返回：' + String(d.body || d.error || '（无正文）').slice(0, 300), true);
      } else {
        const failTxt = AIO.failN ? ('，失败 ' + AIO.failN + ' 题（点该行「重试」）') : '';
        aioSay('AI 优化完成：成功 ' + AIO.okN + ' 题' + failTxt +
               '｜请求 ' + (d.url || '-') + '（' + (d.model || '-') + '）HTTP ' + (d.status != null ? d.status : '-') +
               '｜点进度条可回看逐题输出',
               AIO.failN > 0);
      }
      const keep = CUR_BANK;
      if (keep) await showBankItems(keep);
      AIO.sel.clear();
      // 优化过的行保持折叠，可点标签或双击行展开前后对比
      if (r.state === 'cancel' || r.cancel) {
        if (AIO.okN > 0) toast('已停止：' + AIO.okN + ' 题已更新，未处理的题目可稍后继续优化');
      } else if (AIO.okN > 0) toast('优化完成：' + AIO.okN + ' 题已更新，点「已优化 · 查看对比」可看前后对比');
      aioSync();
    } catch (e) {
      AIO.running = false;
      aioSay('取进度失败：' + e.message, true);
      aioSync();
    }
  }, 700);
}

async function aioRun(indices) {
  if (!CUR_BANK) return;
  if (AIO.running) { toast('已有 AI 优化任务在跑，请等它结束', true); return; }
  const idxs = (indices || []).filter((i) => i >= 0 && i < BANK_ITEMS.length);
  if (!idxs.length) { toast('先勾选要优化的题目', true); return; }
  AIO.before = {}; AIO.fail.clear();
  idxs.forEach((i) => { AIO.before[i] = aioSnapOf(BANK_ITEMS[i] || {}); });
  AIO.running = true; AIO.stopping = false; AIO.done = 0; AIO.okN = 0; AIO.failN = 0; AIO.total = idxs.length;
  AIO.liveOpen = new Set();
  aioSay('已提交 ' + idxs.length + ' 题，正在按「设置」里的接口优化…', false);
  aioSync();
  try {
    const r = await api('/api/bank/ai_optimize', { file: CUR_BANK, indices: idxs });
    AIO.jid = r.jid || '';
    aioPoll();
    if (aioLiveIsOpen()) aioLiveTick(false);   // 弹层已开着就直接接上实时输出
  } catch (e) {
    AIO.running = false;
    aioSay('提交失败：' + e.message, true);
    aioSync();
  }
}

$('#aiOptAll').addEventListener('change', (e) => {
  AIO.sel.clear();
  if (e.target.checked) BANK_ITEMS.forEach((it, i) => AIO.sel.add(i));
  renderBankItems($('#bankFilter').value || '');
});
$('#btnAiOptSel').addEventListener('click', () => aioRun(Array.from(AIO.sel)));
$('#btnAiOptAllQ').addEventListener('click', () => {
  if (!BANK_ITEMS.length) { toast('题库里还没有题目', true); return; }
  if (!confirm('对整个题库共 ' + BANK_ITEMS.length + ' 题逐题调用 AI 优化？\n\n耗时取决于题目数量与接口速度，可随时看进度。')) return;
  aioRun(BANK_ITEMS.map((it, i) => i));
});
/* 停止：立即通知后端置取消标记，前端先切到「正在停止…」，等轮询到 state=cancel 收尾 */
$('#btnAiOptCancel').addEventListener('click', async () => {
  if (!AIO.running) return;
  if (AIO.stopping) return;
  const jid = AIO.jid;
  if (!jid) { toast('任务还没提交成功，稍等再停止', true); return; }
  AIO.stopping = true;
  aioSay('正在停止…', false);
  aioSync();
  try {
    await api('/api/bank/ai_optimize_cancel', { jid: jid });
    toast('已发送停止指令');
  } catch (e) {
    AIO.stopping = false;
    aioSay('停止失败：' + e.message, true);
    aioSync();
    return;
  }
  aioPoll();
});

$('#btnAiOptUndo').addEventListener('click', async () => {
  if (!AIO.jid) { toast('暂无可撤销的优化记录', true); return; }
  if (!confirm('把本批已优化的题目全部还原成优化前的样子？（可按「AI 优化」重新优化）')) return;
  try {
    const r = await api('/api/bank/ai_optimize_undo', { jid: AIO.jid });
    const cnt = r.restored || 0;
    if (cnt > 0) {
      toast('已撤销 ' + cnt + ' 题，题目内容已还原为优化前的样子');
      aioSay('已撤销 ' + cnt + ' 题（题干/题型/要点/环节/参考要点均已还原）', false);
    } else {
      aioSay(r.msg || '没有可撤销的改动', true);
    }
    // 撤销后本地状态一并清空，避免「已优化」标记与撤销按钮停留在旧状态
    AIO.before = {}; AIO.fail.clear();
    AIO.okN = 0; AIO.failN = 0; AIO.jid = '';
    const keep = CUR_BANK;
    if (keep) await showBankItems(keep);
    aioSync();
  } catch (e) { toast(e.message, true); aioSay('撤销失败：' + e.message, true); }
});

function renderBankItems(kw) {
  const box = $('#bankItems');
  const bar = $('#bankOptBar');
  if (bar) bar.style.display = CUR_BANK ? 'flex' : 'none';
  const q = (kw || '').trim().toLowerCase();
  const rows = BANK_ITEMS.map((it, i) => ({ it, i }))
    .filter(({ it }) => !q || ((it.question || '') + ' ' + (it.qtype || '') + ' ' + (it.ref || '')).toLowerCase().includes(q));
  if (!rows.length) {
    box.innerHTML = '<div class="muted">' + (BANK_ITEMS.length ? '没有匹配的题目' : '该题库为空') + '</div>';
    aioSync();
    return;
  }
  box.innerHTML = rows.map(({ it, i }) =>
    '<div class="bank-item" data-i="' + i + '">' +
      '<label class="ip-chk" title="勾选后点上方「AI 优化所选」批量优化"><input type="checkbox" class="ichk" data-i="' + i + '"' +
        (AIO.sel.has(i) ? ' checked' : '') + ' /></label>' +
      '<span class="bi-t">' + (i + 1) + '. ' + esc((it.question || '').slice(0, 60)) +
      ((it.question || '').length > 60 ? '…' : '') +
      (it.qtype ? '<span class="bi-type" title="题型：' + esc(it.qtype) + '">' + esc(it.qtype) + '</span>' : '<span class="bi-type empty">未设题型</span>') +
      '</span>' +
      '<span class="bi-acts">' +
        '<button class="mini bopt' + (AIO.fail.has(i) ? ' bad' : '') + '" data-i="' + i + '" title="' +
          (AIO.fail.has(i) ? '上次优化失败，点这里单独重试' : '只优化这一题：题干规范 / 题型校准 / 审题要点 / 答题环节与要点 / 参考要点提质') + '">' +
          (AIO.fail.has(i) ? '重试' : 'ai') + '</button>' +
        '<button class="mini bedit" data-i="' + i + '" title="编辑该题的题干 / 题型 / 参考要点">✎</button>' +
        '<button class="bdel" data-i="' + i + '" title="删除该题">×</button>' +
      '</span>' +
      (AIO.before[i] ? '<span class="bi-diff" title="点击查看这道题优化前后的内容对比（大窗口左右并排）">已优化 · 查看对比</span>' : '') +
    '</div>'
  ).join('');
  box.querySelectorAll('.bank-item').forEach((el) => {
    el.addEventListener('click', (e) => {
      // 双击打开对比时的第二次 click 不再「填入题目」，避免副作用重排导致误触到上方按钮
      if (e.detail > 1) return;
      if (e.target.closest('.bdel') || e.target.closest('.bedit') || e.target.closest('.bopt') ||
          e.target.closest('.ip-chk') || e.target.closest('.bi-diff')) return;
      const it = BANK_ITEMS[Number(el.dataset.i)] || {};
      setVal($('#question'), it.question || '', true);
      setVal($('#ref'), it.ref || '', true);
      syncQtypeTo(it.qtype || '', true);
      qaFillFromItem(it);
      saveInfo();
      toast(it.qtype ? ('已填入题目 · 题型：' + it.qtype) : '已填入题目（该题还没设题型）');
    });
  });
  box.querySelectorAll('.ichk').forEach((el) => {
    el.addEventListener('change', (e) => {
      const i = Number(el.dataset.i);
      if (e.target.checked) AIO.sel.add(i); else AIO.sel.delete(i);
      aioSync();
    });
  });
  box.querySelectorAll('.bopt').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const i = Number(btn.dataset.i);
      AIO.fail.delete(i);
      aioRun([i]);
    });
  });
  // 优化对比改为大窗口弹层：点「已优化 · 查看对比」标签或双击该行均打开 #modalAiDiff
  box.querySelectorAll('.bank-item').forEach((el) => {
    const i = Number(el.dataset.i);
    if (!AIO.before[i]) return;
    const tag = el.querySelector('.bi-diff');
    if (tag) tag.addEventListener('click', (e) => { e.stopPropagation(); renderAiDiff(i); });
    el.addEventListener('dblclick', (e) => {
      e.preventDefault();
      if (e.target.closest('.bopt') || e.target.closest('.bedit') ||
          e.target.closest('.bdel') || e.target.closest('.ip-chk') || e.target.closest('.bi-diff')) return;
      renderAiDiff(i);
    });
  });
  aioSync();
  box.querySelectorAll('.bedit').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      /* 编辑入口复用「题目解析」弹层（题干/题型/审题要点/结构/关键要点/参考要点全字段），
         保存走 /api/bank/item/update 覆盖原题；简陋的 #modalItemEdit 不再调用 */
      qaOpenForBankItem(Number(btn.dataset.i));
    });
  });
  box.querySelectorAll('.bdel').forEach((btn) => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const i = Number(btn.dataset.i);
      const cur = BANK_ITEMS[i] || {};
      if (!confirm('删除这道题？\n\n' + (cur.question || '').slice(0, 60))) return;
      try {
        await api('/api/bank/item/delete', { file: CUR_BANK, index: i });
        toast('已删除该题');
        const keep = CUR_BANK;
        await showBankItems(keep);
        await loadBanks();
      } catch (err) { toast(err.message, true); }
    });
  });
}

$('#btnBankBack').addEventListener('click', closeBankItems);
$('#btnBankCloseItems').addEventListener('click', closeBankItems);
$('#bankFilter').addEventListener('input', (e) => { if (CUR_BANK) renderBankItems(e.target.value); });

let _infoTimer = null;
function saveInfo() {
  clearTimeout(_infoTimer);
  _infoTimer = setTimeout(() => {
    const snap = {
      '#question': $('#question').value,
      '#qtype': $('#qtype').value,
      '#ref': $('#ref').value,
      '#candidate': $('#candidate').value,
    };
    api('/api/session/info', {
      question: snap['#question'],
      qtype: snap['#qtype'],
      ref: snap['#ref'],
      candidate: snap['#candidate'],
    }).then(() => {
      /* 只有「值没再被改动」才算真正提交成功，此时才解除脏标记；
         保存失败则保留脏标记，宁可挡住后台回写也不让输入被冲掉 */
      Object.keys(snap).forEach((k) => {
        const el = $(k);
        if (el && el.value === snap[k]) clearDirty(el);
      });
    }).catch(() => {});
  }, 400);
}
['#question', '#qtype', '#ref', '#candidate'].forEach((s) => $(s).addEventListener('input', saveInfo));

/* ---------------- 题型清单（内置题型 + 用户自定义题型） ---------------- */
let QTYPE_LIST = [];
let QTYPE_READY = false;
const QT_NEW = '__new__';
const QTYPE_SELS = ['#qtype', '#qaQtype', '#itQtype'];

async function loadQtypes(force) {
  if (QTYPE_READY && !force) return QTYPE_LIST;
  try {
    const r = await api('/api/qtypes');
    QTYPE_LIST = r.types || [];
  } catch (e) { QTYPE_LIST = QTYPE_LIST || []; }
  QTYPE_READY = true;
  return QTYPE_LIST;
}

function qtFind(name) {
  const n = (name || '').trim();
  if (!n) return null;
  return QTYPE_LIST.find((t) => t.name === n) ||
    QTYPE_LIST.find((t) => t.id === n) ||
    QTYPE_LIST.find((t) => (t.aka || []).indexOf(n) >= 0) || null;
}

function qtStages(name) {
  const t = qtFind(name);
  return (t && t.stages && t.stages.length) ? t.stages.slice() : [];
}

function qtOptionsHTML(cur) {
  const names = QTYPE_LIST.map((t) => t.name).filter(Boolean);
  const val = (cur || '').trim();
  let html = '<option value="">（未选择题型）</option>';
  html += names.map((n) => '<option value="' + esc(n) + '">' + esc(n) + '</option>').join('');
  if (val && names.indexOf(val) < 0) {
    html += '<option value="' + esc(val) + '">' + esc(val) + '（不在题型列表）</option>';
  }
  html += '<option value="' + QT_NEW + '">＋ 新建题型…</option>';
  return html;
}

function fillQtypeSelect(sel, cur) {
  if (!sel) return;
  const val = (cur || '').trim();
  sel.innerHTML = qtOptionsHTML(val);
  sel.value = val;
  if (sel.value !== val) sel.value = '';
  sel.dataset.last = sel.value;
}

function refreshQtypeSelects(cur) {
  QTYPE_SELS.forEach((s) => {
    const el = $(s);
    if (!el) return;
    const keep = (cur === undefined || cur === null) ? (el.value === QT_NEW ? '' : el.value) : cur;
    fillQtypeSelect(el, keep);
  });
}

/* 切换题型：下拉取值 + 立刻按该题型的环节刷新结构显示 */
function syncQtypeTo(name, quiet) {
  const val = (name || '').trim();
  const sel = $('#qtype');
  if (sel) { fillQtypeSelect(sel, val); }
  const st = qtStages(val);
  STAGE_OPTS = st.length ? st : [];
  if (!quiet) saveInfo();
  if (typeof P_VIEW !== 'undefined' && P_VIEW === 'stage') renderPlain(false);
  setTimeout(async () => {
    await ensureStageOpts(true);
    if (!quiet) {
      // 题型换了，后端会按新题型重判结构：把最新段落取回来再画
      try {
        const st = await api('/api/state');
        if (st && st.session && S.session) {
          S.session.paragraphs = st.session.paragraphs || [];
          S.session.plain_pending = st.session.plain_pending;
        }
      } catch (e) { /* 忽略 */ }
    }
    if (typeof P_VIEW !== 'undefined' && P_VIEW === 'stage') renderPlain(false);
  }, 620);
  return st;
}

function applyQtype(name) {
  const val = (name || '').trim();
  const st = syncQtypeTo(val, false);
  toast(val ? ('题型：' + val + (st.length ? ' · 结构 ' + st.join(' / ') : '')) : '已清空题型');
}

/* 新建题型弹层：创建成功后立即在触发处选中 */
let QT_PENDING = null;

function openNewQtype(cb) {
  QT_PENDING = cb || null;
  setVal($('#qtNewName'), '', true);
  const ta = $('#qtNewStages');
  if (ta && !ta.value.trim()) ta.value = '审题\n提观点\n重分析\n抓落实\n结尾';
  openModal('#modalQtype');
  setTimeout(() => { const n = $('#qtNewName'); if (n) n.focus(); }, 60);
}

async function submitNewQtype() {
  const name = ($('#qtNewName').value || '').trim();
  if (!name) { toast('先填题型名称', true); return; }
  const stages = ($('#qtNewStages').value || '').split(/[\n,，、;；\/]+/).map((s) => s.trim()).filter(Boolean);
  if (!stages.length) { toast('至少写一个答题环节', true); return; }
  const btn = $('#btnQtNewOk');
  const label = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = '创建中…'; }
  try {
    const r = await api('/api/qtypes/add', { name, stages });
    await loadQtypes(true);
    refreshQtypeSelects();
    closeModal($('#modalQtype'));
    toast('已新建题型「' + r.type.name + '」· 结构 ' + (r.type.stages || []).join(' / '));
    const cb = QT_PENDING;
    QT_PENDING = null;
    if (cb) cb(r.type.name);
  } catch (e) {
    toast(e.message, true);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = label; }
  }
}

function bindQtypeSelect(selId, onPick) {
  const el = $(selId);
  if (!el) return;
  el.addEventListener('change', () => {
    const v = el.value;
    if (v === QT_NEW) {
      const back = el.dataset.last || '';
      el.value = back;
      openNewQtype((name) => onPick(name));
      return;
    }
    el.dataset.last = v;
    onPick(v);
  });
}

bindQtypeSelect('#qtype', (v) => applyQtype(v));
bindQtypeSelect('#qaQtype', (v) => {
  const st = qtStages(v);
  if (st.length) $('#qaStruct').innerHTML = qaStructHTML(st.map((nm) => ({ stage: nm, label: '', points: [] })));
  applyQtype(v);
});
bindQtypeSelect('#itQtype', (v) => { toast(v ? ('题型：' + v) : '已清空题型'); });

$('#btnNewQtype').addEventListener('click', () => openNewQtype((name) => applyQtype(name)));
$('#btnQaNewQtype').addEventListener('click', () => openNewQtype((name) => {
  const st = qtStages(name);
  if (st.length) $('#qaStruct').innerHTML = qaStructHTML(st.map((nm) => ({ stage: nm, label: '', points: [] })));
  applyQtype(name);
}));
$('#btnItNewQtype').addEventListener('click', () => openNewQtype((name) => {
  fillQtypeSelect($('#itQtype'), name);
}));
$('#btnQtNewOk').addEventListener('click', submitNewQtype);
$('#qtNewName').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); submitNewQtype(); } });

/* ---------------- 编辑题库单题（题干 / 题型 / 参考要点） ---------------- */
let IT_IDX = -1;

function openItemEdit(i) {
  const it = BANK_ITEMS[i];
  if (!it) return;
  IT_IDX = i;
  $('#itEditBank').textContent = '题库：' + String(CUR_BANK || '').replace(/\.json$/i, '') + ' · 第 ' + (i + 1) + ' 题';
  setVal($('#itQuestion'), it.question || '', true);
  setVal($('#itRef'), it.ref || '', true);
  fillQtypeSelect($('#itQtype'), it.qtype || '');
  openModal('#modalItemEdit');
  setTimeout(() => { const q = $('#itQuestion'); if (q) q.focus(); }, 60);
}

async function saveItemEdit() {
  if (IT_IDX < 0 || !CUR_BANK) return;
  const q = ($('#itQuestion').value || '').trim();
  if (q.length < 4) { toast('题干太短，至少 4 个字', true); return; }
  const old = BANK_ITEMS[IT_IDX] || {};
  const qtype = ($('#itQtype').value === QT_NEW) ? '' : ($('#itQtype').value || '').trim();
  const body = { file: CUR_BANK, index: IT_IDX, question: q, qtype, ref: $('#itRef').value || '' };
  if (qtype !== (old.qtype || '')) {
    const st = qtStages(qtype);
    if (st.length) body.structure = st.map((nm) => ({ stage: nm, label: '', points: [] }));
    else body.structure = [];
  }
  const btn = $('#btnItSave');
  const label = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = '保存中…'; }
  try {
    const r = await api('/api/bank/item/update', body);
    const keep = CUR_BANK;
    await showBankItems(keep);
    await loadBanks();
    closeModal($('#modalItemEdit'));
    toast('已保存该题' + (qtype ? ' · 题型：' + qtype : ''));
    const curQ = ($('#question').value || '').trim();
    if (curQ && curQ === (old.question || '').trim()) {
      setVal($('#question'), q, true);
      setVal($('#ref'), body.ref, true);
      syncQtypeTo(qtype, true);
      qaFillFromItem(r.item || body);
      saveInfo();
    }
    IT_IDX = -1;
  } catch (e) {
    toast(e.message, true);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = label; }
  }
}

$('#btnItSave').addEventListener('click', saveItemEdit);

/* ==================================================================
   输入稳定性守护（文本框「概率性打不了字」的根治层）
   ------------------------------------------------------------------
   根因一：拖分隔条 layStart 会给 body 加 .resizing，其样式是
           body.resizing *{pointer-events:none}；若鼠标在窗口外松开，
           pointerup 收不到，类残留 → 整站输入框点不动、打不了字。
           拖窗口的 .win-dragging / .win-resizing（user-select:none）同理。
   根因二：后台 SSE 推送（switch_student / reload）与 /api/state 回填直接写
           input/textarea.value，把用户正在输入的内容冲掉或跳回旧值。
   根因三：输入法合成中按 Esc 被弹窗逻辑吃掉，上屏被吞。
   对策：状态类自愈 + 回填保护 + 合成期屏蔽 Esc。
   ================================================================== */
var IME_ON = false;                       /* 输入法合成中 */

function clearStuckDrag(force) {
  var b = document.body;
  if (!b) return;
  var hasLay = b.classList.contains('resizing');
  var hasWin = b.classList.contains('win-dragging') || b.classList.contains('win-resizing');
  if (!hasLay && !hasWin) return;
  var layActive = false;
  try { layActive = !!(typeof LAY_DRAG !== 'undefined' && LAY_DRAG); } catch (e) { layActive = false; }
  /* 拖拽真在进行中就别打断；force 表示已有新一次按下，上一次必然结束 */
  if (hasLay && layActive && !force) {
    var t0 = Number((typeof LAY_DRAG !== 'undefined' && LAY_DRAG && LAY_DRAG.t0) || 0);
    if (!t0 || Date.now() - t0 < 60000) return;   /* 真在拖就别打断；超过 60s 视为卡死 */
    try { layEnd(); } catch (e) {}
  }
  if (hasWin && !force) {
    var t = Number(b.getAttribute('data-winop') || 0);
    if (t && Date.now() - t < 2500) return;
  }
  b.classList.remove('resizing', 'win-dragging', 'win-resizing');
  b.removeAttribute('data-laydrag');
  b.removeAttribute('data-winop');
  var d = document.querySelectorAll('.drag');
  for (var i = 0; i < d.length; i++) d[i].classList.remove('drag');
}
/* ① 任何一次按下之前，先把上一次可能残留的全局状态清干净 */
document.addEventListener('pointerdown', function () { clearStuckDrag(true); }, true);
document.addEventListener('mousedown', function () { clearStuckDrag(true); }, true);
/* ② 定时巡检（拖拽结束事件丢失时的兜底） */
setInterval(function () { clearStuckDrag(false); }, 400);
/* ③ 窗口失焦 / 页面隐藏：拖拽必然已结束，直接收尾 */
window.addEventListener('blur', function () {
  try { if (typeof layEnd === 'function') layEnd(); } catch (e) {}
  try { clearStuckDrag(true); } catch (e) {}
});
document.addEventListener('visibilitychange', function () {
  if (document.visibilityState === 'hidden') { try { clearStuckDrag(true); } catch (e) {} }
});

/* ---- 回填保护：后台推送 / 状态同步不得覆盖用户正在输入的内容 ---- */
function elDirty(el) { return !!(el && el.dataset && el.dataset.dirty === '1'); }
function clearDirty(el) { if (el && el.dataset) delete el.dataset.dirty; }
function setVal(el, v, force) {
  if (!el) return;
  var nv = (v === null || v === undefined) ? '' : String(v);
  if (!force) {
    if (elDirty(el)) return;                                        /* 有未提交改动：不回写 */
    if (document.activeElement === el && el.value !== nv) return;   /* 正在编辑：不打断 */
  }
  if (el.value !== nv) el.value = nv;
  clearDirty(el);
}
/* 输入即打脏标记；输入法合成期置位，用于屏蔽 Esc 等热键 */
document.addEventListener('input', function (e) {
  var t = e.target;
  if (!t || (t.tagName !== 'INPUT' && t.tagName !== 'TEXTAREA')) return;
  if (t.type === 'checkbox' || t.type === 'radio') return;
  t.dataset.dirty = '1';
}, true);
document.addEventListener('compositionstart', function (e) {
  var t = e.target;
  if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA')) IME_ON = true;
}, true);
document.addEventListener('compositionend', function (e) {
  var t = e.target;
  if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA')) {
    IME_ON = false;
    /* 合成结束补一次脏标记，避免部分输入法不触发 input */
    t.dataset.dirty = '1';
  }
}, true);

/* ==================================================================
   弹层：每层只有一个关闭入口（标题栏右侧 ×），位置与样式统一
   · 点 × 只关按钮所在那一层
   · 点遮罩空白只关最上层
   · Esc 只关最上层；输入法合成中不响应
   ================================================================== */
let MODAL_STACK = [];
function openModal(id) {
  const el = (typeof id === 'string') ? $(id) : id;
  if (!el) return;
  /* 打开题目解析弹层时先复位成普通「一键写入题库」，避免上次的「覆盖本题」标记残留；
     题库行 ✎ 的流程是 openModal 之后紧接着 qaSetMode(覆盖)，因此不受影响 */
  if (el.id === 'modalQAnalyze' && typeof qaIsOverwrite === 'function' && qaIsOverwrite()) {
    try { qaSetMode(null); } catch (e) {}
  }
  el.classList.add('open');
  if (MODAL_STACK.indexOf(el) < 0) MODAL_STACK.push(el);
  /* 弹层显示后首个输入框自动获得焦点，省一次点击（无输入框则跳过） */
  setTimeout(function () {
    try {
      if (!el.classList.contains('open')) return;
      var f = el.querySelector('input:not([type=hidden]):not([disabled]), textarea:not([disabled])');
      if (f && document.activeElement !== f && !el.contains(document.activeElement)) f.focus();
    } catch (e) {}
  }, 30);
}
function closeModal(el) {
  const t = (typeof el === 'string') ? $(el) : el;
  if (!t) return;
  /* 题目解析弹层被关掉时，若还处在「覆盖本题」模式，必须复位回普通「一键写入题库」，
     否则下次从题目区打开会残留：题库选择列不显示、主按钮还是「保存并覆盖本题」 */
  if (t.id === 'modalQAnalyze' && typeof qaIsOverwrite === 'function' && qaIsOverwrite()) {
    try { qaSetMode(null); } catch (e) {}
  }
  if (t.contains(document.activeElement) && document.activeElement.blur) {
    try { document.activeElement.blur(); } catch (e) {}
  }
  t.classList.remove('open');
  MODAL_STACK = MODAL_STACK.filter((x) => x !== t);
}
var MODAL_DOWN = null;
$$('.modal').forEach((m) => {
  /* 记录按下起点：只有「在遮罩空白处按下且几乎没有移动」才算点击遮罩，
     避免从弹窗内往外拖（如拖 textarea 缩放柄、拖选文字）松手落在遮罩上被误判为点遮罩而关掉弹窗 */
  m.addEventListener('pointerdown', (e) => {
    MODAL_DOWN = { t: Date.now(), x: e.clientX, y: e.clientY, onMask: e.target === m };
  }, true);
  m.addEventListener('click', (e) => {
    const hit = (e.target.closest ? e.target.closest('[data-close]') : null);
    if (hit) {                                   /* 只关按钮所属的那一层 */
      const owner = hit.closest ? hit.closest('.modal') : null;
      closeModal(owner || m);
      return;
    }
    if (e.target === m && MODAL_STACK[MODAL_STACK.length - 1] === m) {
      const d = MODAL_DOWN;
      MODAL_DOWN = null;
      if (!d || !d.onMask) return;                       /* 不是从遮罩上按下的：不算点遮罩 */
      if (Math.abs(e.clientX - d.x) > 6 || Math.abs(e.clientY - d.y) > 6) return;  /* 拖拽后松手：忽略 */
      if (d.t && Date.now() - d.t > 1200) return;        /* 长按拖动：忽略 */
      closeModal(m);
    }
  });
});
/* Esc：先关最上层弹窗，若没有弹窗则退出点评全屏 */
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if (IME_ON) return;                            /* 输入法合成中不吃 Esc */
  const top = MODAL_STACK[MODAL_STACK.length - 1];
  if (top) { e.preventDefault(); e.stopPropagation(); closeModal(top); return; }
  /* 快速记录浮层：无弹窗时 Esc 收起；焦点在它内部时先失焦，再按一次才收起（不打断正在敲的内容） */
  try {
    if (typeof QN !== 'undefined' && QN.open) {
      const p = qnPanel();
      if (p) {
        if (p.contains(document.activeElement)) {
          e.preventDefault();
          e.stopPropagation();
          try { document.activeElement.blur(); } catch (err) {}
          return;
        }
        e.preventDefault();
        e.stopPropagation();
        qnClose();
        return;
      }
    }
  } catch (err) {}
  const rv = $('#review');
  if (rv && rv.classList.contains('full')) {
    setReviewFull(false);
  }
}, true);

let PICK_DATA = [];
function renderBankPicker(kw) {
  const body = $('#bankPickBody');
  const q = (kw || '').trim().toLowerCase();
  if (!PICK_DATA.length) {
    body.innerHTML = '<div class="muted">题库为空，先在左侧「导入」</div>';
    $('#bankPickCount').textContent = '';
    return;
  }
  let total = 0;
  const html = PICK_DATA.map((b) => {
    const items = q
      ? b.items.filter((it) => ((it.question || '') + ' ' + (it.qtype || '') + ' ' + (it.ref || '')).toLowerCase().includes(q))
      : b.items;
    if (!items.length) return '';
    total += items.length;
    return '<h3 class="pick-h">' + esc(b.name) + '（' + items.length + ' 题）</h3>' +
      items.map((it, i) =>
        '<div class="bank-item" data-b="' + esc(b.file) + '" data-i="' + i + '">' +
          '<span class="bi-t">' + esc((it.question || '').slice(0, 100)) + ((it.question || '').length > 100 ? '…' : '') +
          (it.qtype ? '<span class="bi-type">' + esc(it.qtype) + '</span>' : '') + '</span>' +
        '</div>').join('');
  }).join('');
  body.innerHTML = html || '<div class="muted">没有匹配的题目，换个关键词试试</div>';
  $('#bankPickCount').textContent = total ? ('匹配 ' + total + ' 题') : '';
  body.querySelectorAll('.bank-item').forEach((el) => {
    el.addEventListener('click', () => {
      const bank = PICK_DATA.find((b) => b.file === el.dataset.b) || { items: [] };
      const it = bank.items[Number(el.dataset.i)] || {};
      setVal($('#question'), it.question || '', true);
      syncQtypeTo(it.qtype || '', true);
      setVal($('#ref'), it.ref || '', true);
      qaFillFromItem(it);
      saveInfo();
      closeModal($('#modalBank'));
      toast(it.qtype ? ('已填入题目 · 题型：' + it.qtype) : '已填入题目（该题还没设题型）');
    });
  });
}

$('#btnPickBank').addEventListener('click', async () => {
  const body = $('#bankPickBody');
  body.innerHTML = '<div class="muted">加载中…</div>';
  setVal($('#bankPickFilter'), '', true);
  $('#bankPickCount').textContent = '';
  openModal('#modalBank');
  try {
    const r = await api('/api/bank');
    PICK_DATA = [];
    for (const b of r.banks) {
      const data = await api('/api/bank/load?file=' + encodeURIComponent(b.file));
      PICK_DATA.push({ file: b.file, name: b.name, items: data.items || [] });
    }
    renderBankPicker('');
  } catch (e) {
    body.innerHTML = '<div class="muted">加载失败：' + esc(e.message) + '</div>';
  }
});
$('#bankPickFilter').addEventListener('input', (e) => renderBankPicker(e.target.value));

/* 导入（文本 / JSON） */
$('#btnImport').addEventListener('click', () => openModal('#modalImport'));
$('#btnDoImport').addEventListener('click', async () => {
  try {
    const r = await api('/api/bank/import', { name: $('#bankName').value || '未命名题库', text: $('#bankText').value });
    toast('已导入，共 ' + r.count + ' 题');
    closeModal($('#modalImport'));
    setVal($('#bankText'), '', true);
    await loadBanks();
  } catch (e) { toast(e.message, true); }
});

/* 导入 Word / PDF（可一次多选）：先解析进「预览列表」，勾选 / 改题型与题干后再确认入库 */
let IMP_ROWS = [];   // 预览行：{id,seq,qno,qtype,question,item,checked,expanded,missing,stages,points,exam_n,keys_n,ref_len,q_short,src,qtype_candidates}
let IMP_FILES = [];  // 文件级状态：{name,file,ok,count,pages,chars,error}

function ipKind(f) {
  const e = (f || '').toLowerCase();
  if (e.endsWith('.pdf')) return 'PDF';
  if (e.endsWith('.docx')) return 'Word';
  return '文本';
}

function ipTypeOptionsHTML(cur) {
  const val = (cur || '').trim();
  let html = '<option value=""' + (val ? '' : ' selected') + '>（未识题型 · 点此选择）</option>';
  const names = QTYPE_LIST.map((t) => t.name).filter(Boolean);
  html += names.map((n) => '<option value="' + esc(n) + '"' + (n === val ? ' selected' : '') + '>' + esc(n) + '</option>').join('');
  if (val && names.indexOf(val) < 0) {
    html += '<option value="' + esc(val) + '" selected>' + esc(val) + '（不在题型列表）</option>';
  }
  return html;
}

function renderImportFiles() {
  const box = $('#ipFiles');
  if (!box) return;
  box.innerHTML = IMP_FILES.map((f) => {
    const kind = ipKind(f.file || f.name);
    if (f.ok) {
      const extra = f.pages ? (' · ' + f.pages + ' 页') : '';
      return '<div class="ip-file ok"><b>' + esc(f.name) + '</b>'
        + '<span class="tag">' + kind + '</span>'
        + '<span class="muted">解析出 ' + f.count + ' 道题' + extra + ' · ' + (f.chars || 0) + ' 字</span></div>';
    }
    return '<div class="ip-file bad"><b>' + esc(f.name) + '</b>'
      + '<span class="tag">' + kind + '</span>'
      + '<span class="ip-err">' + esc(f.error || '解析失败') + '</span></div>';
  }).join('');
}

function updateIpStat() {
  const sel = IMP_ROWS.filter((x) => x.checked).length;
  const bad = IMP_ROWS.filter((x) => x.missing && x.missing.length).length;
  const c = ipSrcCount();
  if ($('#ipStat')) {
    $('#ipStat').textContent = '已选 ' + sel + ' / 共 ' + IMP_ROWS.length + ' 题'
      + (bad ? (' · ' + bad + ' 题有缺失字段') : '')
      + ' · 来源：AI ' + c.ai + ' / 本地 ' + c.local + ' / 文档自带 ' + c.doc;
  }
  if ($('#ipTip')) {
    const modeTxt = (IMP_JOB.mode === 'local')
      ? '本次仅本地解析（未调用 AI） · '
      : (IMP_JOB.ai ? '本次 AI 解析 · ' : '本次本地解析（未配置 API Key） · ');
    $('#ipTip').textContent = IMP_FILES.some((f) => !f.ok)
      ? '有文件解析失败：扫描件 PDF 需 OCR，见上方红字'
      : modeTxt + '逐题核对题干与题型，取消勾选不需要的题后确认入库';
  }
}

/* ---------- 导入预览：环节 / 要点 / 参考要点编辑 ---------- */
let IMP_DRAG = null;   // {id, from}

function ipStages(row) {
  if (!row) return [];
  if (!row.item) row.item = {};
  if (!Array.isArray(row.item.structure)) row.item.structure = [];
  row.item.structure.forEach((s) => {
    if (typeof s === 'string') s = { stage: s, label: '', points: [] };
    if (!Array.isArray(s.points)) s.points = [];
    if (typeof s.stage !== 'string') s.stage = '';
    if (typeof s.label !== 'string') s.label = '';
  });
  // 统一成对象（防后端返回字符串数组）
  row.item.structure = row.item.structure.map((s) => (typeof s === 'string'
    ? { stage: s, label: '', points: [] } : s));
  return row.item.structure;
}

/* 与后端 _bank_item_stats 同一套缺失判定，编辑后即时刷新 */
function ipMissing(row) {
  const st = ipStages(row);
  const pts = st.reduce((n, s) => n + (s.points || []).length, 0);
  const miss = [];
  if ((row.question || '').trim().length < 6) miss.push('题干');
  if (!(row.qtype || '').trim()) miss.push('题型');
  if (!st.length) miss.push('答题环节');
  else if (pts === 0) miss.push('环节要点');
  if (!((row.item && row.item.exam) || []).length) miss.push('审题要点');
  if (((row.item && row.item.ref) || '').trim().length < 10) miss.push('参考要点');
  return miss;
}

/* 卡片上的一行统计：直接从当前编辑结果算，保证改名/增删后即时同步 */
function ipMeta(row) {
  const st = ipStages(row);
  const pts = st.reduce((n, s) => n + (s.points || []).length, 0);
  row.stages = st.length;
  row.points = pts;
  row.ref_len = (((row.item && row.item.ref) || '').trim()).length;
  row.exam_n = ((row.item && row.item.exam) || []).length;
  row.missing = ipMissing(row);
  return row.stages + ' 环节 · ' + row.points + ' 要点 · 审题 ' + row.exam_n
    + ' · 参考 ' + row.ref_len + ' 字';
}

/* 编辑后只改卡片上的统计与缺失提示，避免整表重绘丢焦点 */
function ipRefreshMeta(row) {
  const card = document.querySelector('.ip-card[data-id="' + row.id + '"]');
  if (!card) return;
  const st = ipStages(row);
  const pts = st.reduce((n, s) => n + (s.points || []).length, 0);
  row.stages = st.length;
  row.points = pts;
  row.ref_len = (((row.item && row.item.ref) || '').trim()).length;
  row.exam_n = ((row.item && row.item.exam) || []).length;
  row.missing = ipMissing(row);
  const meta = card.querySelector('.ip-meta');
  if (meta) {
    meta.textContent = row.stages + ' 环节 · ' + row.points + ' 要点 · 审题 ' + row.exam_n
      + ' · 参考 ' + row.ref_len + ' 字';
  }
  const foot = card.querySelector('.ip-foot');
  if (foot) {
    const hint = row.missing.length
      ? '<span class="ip-miss">缺失：' + esc(row.missing.join('、')) + '</span>'
      : '<span class="ip-hint">字段完整</span>';
    const reason = card.querySelector('.ip-reason');
    foot.innerHTML = hint + (reason ? reason.outerHTML : '');
  }
  updateIpStat();
}

function ipEditorHTML(row) {
  const st = ipStages(row);
  const stages = st.map((s, si) => {
    const pts = (s.points || []).map((pt, pi) => '<div class="ip-pt">'
      + '<input class="inp ip-ptx" data-id="' + esc(row.id) + '" data-si="' + si + '" data-pi="' + pi
      + '" value="' + esc(pt) + '" />'
      + '<button class="mini ip-ptdel" type="button" data-id="' + esc(row.id) + '" data-si="' + si
      + '" data-pi="' + pi + '">删</button></div>').join('');
    return '<div class="ip-stage" draggable="true" data-id="' + esc(row.id) + '" data-si="' + si + '">'
      + '<div class="ip-sh">'
      + '<span class="ip-grip" title="按住拖动可调整环节顺序">⋮⋮</span>'
      + '<input class="inp ip-sname" data-id="' + esc(row.id) + '" data-si="' + si
      + '" value="' + esc(s.stage || '') + '" placeholder="环节名称" />'
      + '<button class="mini ip-sup" type="button" data-id="' + esc(row.id) + '" data-si="' + si + '">上移</button>'
      + '<button class="mini ip-sdown" type="button" data-id="' + esc(row.id) + '" data-si="' + si + '">下移</button>'
      + '<button class="mini ip-sdel" type="button" data-id="' + esc(row.id) + '" data-si="' + si + '">删环节</button>'
      + '</div>'
      + '<div class="ip-pts">' + (pts || '<div class="muted ip-noPt">该环节还没有要点</div>') + '</div>'
      + '<button class="mini ip-ptadd" type="button" data-id="' + esc(row.id) + '" data-si="' + si + '">+ 加要点</button>'
      + '</div>';
  }).join('');
  const ref = ((row.item && row.item.ref) || '');
  return '<div class="ip-editor">'
    + '<div class="ip-eh">答题环节与要点 <span class="muted">（环节可改名 / 增删 / 拖动排序；要点可增删改）</span></div>'
    + '<div class="ip-stages">' + (stages || '<div class="muted">还没有识别到答题环节，点下面按钮手动加</div>') + '</div>'
    + '<button class="mini ip-sadd" type="button" data-id="' + esc(row.id) + '">+ 加环节</button>'
    + '<div class="ip-eh">参考要点</div>'
    + '<textarea class="inp ta ip-ref" data-id="' + esc(row.id) + '" rows="4" '
    + 'placeholder="参考答案要点，一行一条或用分号隔开">' + esc(ref) + '</textarea>'
    + '<div class="ip-ereset"><button class="mini ip-reset" type="button" data-id="' + esc(row.id)
    + '">恢复原解析</button><span class="muted">丢弃本题所有手动编辑，回到刚导入时的解析结果</span></div>'
    + '</div>';
}

/* 来源标注：只用服务端算好的事实口径（src_label/src_bad），禁止凭 parse_src 猜「AI 解析」 */
function ipSrcLabel(x) {
  if (x.src_label) return x.src_label;
  const k = x.parse_src || '';
  if (k === 'ai') return 'AI 解析';
  if (k === 'doc') return '文档自带';
  return '本地解析';
}

function ipSrcTag(x) {
  const k = x.parse_src || 'local';
  const cls = 'ij-tag ' + (k === 'ai' ? 'ai' : (k === 'doc' ? 'doc' : 'local')) + (x.src_bad ? ' bad' : '');
  const tip = x.fail_reason
    || (k === 'ai' ? ('AI 补齐：' + (((x.ai_filled || []).join('、')) || '题型/结构校验'))
      : ((x.local_filled || []).length ? ('本地模板补齐：' + x.local_filled.join('、'))
        : (k === 'doc' ? '字段来自文档本身，未调用 AI' : '未配置 API Key 或 AI 未生效，已按本地规则解析')));
  return '<span class="' + cls + '" title="' + esc(tip) + '">' + esc(ipSrcLabel(x)) + '</span>';
}

function ipSrcCount() {
  const c = { ai: 0, local: 0, doc: 0 };
  IMP_ROWS.forEach((x) => { const k = x.parse_src || 'local'; c[k in c ? k : 'local'] += 1; });
  return c;
}

function renderImportList() {
  const box = $('#ipList');
  if (!box) return;
  if (!IMP_ROWS.length) {
    box.innerHTML = '<div class="muted" style="padding:16px">没有可入库的题目。若文件是扫描件 PDF（无文字层），请先做 OCR 或改用带文字版的 PDF / Word。</div>';
    return;
  }
  box.innerHTML = IMP_ROWS.map((x) => {
    const checked = x.checked ? ' checked' : '';
    const miss = (x.missing && x.missing.length)
      ? '<span class="ip-miss">缺失：' + esc(x.missing.join('、')) + '</span>'
      : '<span class="ip-hint">字段完整</span>';
    const tag = ipSrcTag(x);
    const retry = (x.parse_src === 'local' && x.fail_reason)
      ? '<button class="mini ip-retry" type="button" data-id="' + esc(x.id) + '" title="用 AI 重试解析这道题">AI 重试</button>'
      : '';
    return '<div class="ip-card' + (x.checked ? '' : ' off') + '" data-id="' + esc(x.id) + '">'
      + '<div class="ip-row">'
      + '<input type="checkbox" class="ip-chk" data-id="' + esc(x.id) + '"' + checked + ' />'
      + '<span class="ip-no">第 ' + esc(String(x.qno)) + ' 题</span>'
      + '<select class="inp ip-type" data-id="' + esc(x.id) + '">' + ipTypeOptionsHTML(x.qtype) + '</select>'
      + tag
      + '<span class="ip-meta">' + ipMeta(x) + '</span>'
      + '<button class="mini ip-edit" type="button" data-id="' + esc(x.id) + '">' + (x.expanded ? '收起编辑' : '编辑') + '</button>'
      + '</div>'
      + '<div class="ip-qtxt">' + esc(x.q_short || x.question.slice(0, 56))
      + '<span class="ip-src">' + esc(x.src || '') + '</span></div>'
      + (x.expanded
        ? ('<textarea class="inp ta ip-ta" data-id="' + esc(x.id) + '" rows="4">' + esc(x.question) + '</textarea>'
           + ipEditorHTML(x))
        : '')
      + '<div class="ip-foot">' + miss + retry + (x.qtype_reason ? '<span class="ip-reason">' + esc(x.qtype_reason) + '</span>' : '') + '</div>'
      + '</div>';
  }).join('');
  updateIpStat();
}

function renderImportPreview() {
  renderImportFiles();
  renderImportList();
}

/* 导入解析方式：ai=调用 AI 接口，local=只用本地规则（不发起任何 AI 请求）
   选过一次就记住，下次打开导入弹层默认选中它（本地 localStorage + 后端 import_pref 双写） */
const IMP_MODE = {
  cur: 'ai', key: 'irl_imp_mode',
  norm(v) { return (v === 'local' || v === 'ai') ? v : 'ai'; },
  async load() {
    let v = '';
    try { v = localStorage.getItem(this.key) || ''; } catch (e) { v = ''; }
    if (!v) {
      try {
        const r = await fetch('/api/bank/import_pref');
        const j = await r.json().catch(() => ({}));
        if (j && j.mode) v = j.mode;
      } catch (e) { v = ''; }
    }
    this.cur = this.norm(v);
    return this.cur;
  },
  save(v) {
    this.cur = this.norm(v);
    try { localStorage.setItem(this.key, this.cur); } catch (e) {}
    fetch('/api/bank/import_pref', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: this.cur }) }).catch(() => {});
  },
  paint() {
    document.querySelectorAll('#modalImpMode input[name="impMode"]').forEach((el) => {
      el.checked = (el.value === this.cur);
      const box = el.closest('.impmode-item');
      if (box) box.classList.toggle('on', el.checked);
    });
    const tip = document.getElementById('impModeTip');
    if (tip) tip.textContent = this.cur === 'local'
      ? '当前：仅本地解析 —— 本次导入不会发起任何 AI 请求。'
      : '当前：AI 解析 —— 未配置 API Key 或接口不可用时会如实标出并回退本地规则。';
  },
};
window.IMP_MODE = IMP_MODE;
IMP_MODE.load().then(() => IMP_MODE.paint());

$('#btnImportDocx').addEventListener('click', () => {
  IMP_MODE.load().then(() => { IMP_MODE.paint(); openModal('#modalImpMode'); });
});
document.querySelectorAll('#modalImpMode input[name="impMode"]').forEach((el) => {
  el.addEventListener('change', () => { IMP_MODE.save(el.value); IMP_MODE.paint(); });
});
{
  const okBtn = document.getElementById('btnImpModeOk');
  if (okBtn) okBtn.addEventListener('click', () => {
    const picked = document.querySelector('#modalImpMode input[name="impMode"]:checked');
    IMP_MODE.save(picked ? picked.value : IMP_MODE.cur);
    closeModal('#modalImpMode');
    const f = $('#bankFile');
    if (f) f.click();
  });
}
$('#bankFile').addEventListener('change', async (e) => {
  const files = Array.from(e.target.files || []);
  e.target.value = '';
  if (!files.length) return;
  const mode = IMP_MODE.cur;
  const fd = new FormData();
  files.forEach((f) => fd.append('file', f));
  fd.append('mode', mode);
  try {
    const r = await fetch('/api/bank/import_start', { method: 'POST', body: fd });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || j.ok === false) throw new Error(j.msg || ('启动解析失败 ' + r.status));
    if (typeof loadQtypes === 'function') loadQtypes();
    IMP_JOB.start(j.jid, j.ai, j.files || files.length, j.mode || mode);
  } catch (err) { toast(err.message, true); }
});

$('#ipList').addEventListener('change', (e) => {
  const t = e.target;
  const id = t.dataset ? t.dataset.id : '';
  const row = IMP_ROWS.find((x) => x.id === id);
  if (!row) return;
  if (t.classList.contains('ip-chk')) {
    row.checked = !!t.checked;
    const card = t.closest('.ip-card');
    if (card) card.classList.toggle('off', !row.checked);
    updateIpStat();
  } else if (t.classList.contains('ip-type')) {
    row.qtype = t.value;
    updateIpStat();
  }
});
$('#ipList').addEventListener('input', (e) => {
  const t = e.target;
  if (!t.classList.contains('ip-ta')) return;
  const row = IMP_ROWS.find((x) => x.id === t.dataset.id);
  if (row) { row.question = t.value; row.q_short = t.value.slice(0, 56); }
});
/* 环节 / 要点 / 参考要点的编辑动作 -------------------------------------------------- */
function ipRowOf(el) {
  return IMP_ROWS.find((x) => x.id === (el && el.dataset ? el.dataset.id : ''));
}
$('#ipList').addEventListener('click', (e) => {
  const t = e.target.closest ? e.target.closest('button') : null;
  if (!t) return;
  const cls = t.className || '';
  const row = ipRowOf(t);
  if (!row) return;
  const si = parseInt(t.dataset.si, 10);
  const pi = parseInt(t.dataset.pi, 10);
  const st = ipStages(row);
  if (cls.indexOf('ip-edit') >= 0) {
    row.expanded = !row.expanded;
    renderImportList();
    return;
  }
  if (cls.indexOf('ip-sadd') >= 0) {
    st.push({ stage: '新环节 ' + (st.length + 1), label: '', points: [] });
  } else if (cls.indexOf('ip-sdel') >= 0) {
    if (!(si >= 0 && si < st.length)) return;
    st.splice(si, 1);
  } else if (cls.indexOf('ip-sup') >= 0) {
    if (!(si > 0 && si < st.length)) return;
    const tmp = st[si - 1]; st[si - 1] = st[si]; st[si] = tmp;
  } else if (cls.indexOf('ip-sdown') >= 0) {
    if (!(si >= 0 && si < st.length - 1)) return;
    const tmp = st[si + 1]; st[si + 1] = st[si]; st[si] = tmp;
  } else if (cls.indexOf('ip-ptadd') >= 0) {
    if (!(si >= 0 && si < st.length)) return;
    if (!Array.isArray(st[si].points)) st[si].points = [];
    st[si].points.push('');
  } else if (cls.indexOf('ip-ptdel') >= 0) {
    if (!(si >= 0 && si < st.length)) return;
    const pts = st[si].points || [];
    if (!(pi >= 0 && pi < pts.length)) return;
    pts.splice(pi, 1);
  } else if (cls.indexOf('ip-reset') >= 0) {
    if (!row.orig) return;
    row.item = JSON.parse(JSON.stringify(row.orig));
    row.question = row.item.question || row.question;
    row.q_short = row.question.slice(0, 56);
    row.qtype = row.item.qtype || '';
    toast('已恢复本题的原始解析结果');
    renderImportList();
    return;
  } else {
    return;
  }
  renderImportList();
  ipRefreshMeta(row);
});

/* 文字编辑：环节名 / 要点 / 参考要点，边输入边写入 IMP_ROWS，切换卡片不丢 */
$('#ipList').addEventListener('input', (e) => {
  const t = e.target;
  const row = ipRowOf(t);
  if (!row) return;
  const st = ipStages(row);
  const si = parseInt(t.dataset.si, 10);
  const pi = parseInt(t.dataset.pi, 10);
  if (t.classList.contains('ip-sname')) {
    if (si >= 0 && si < st.length) st[si].stage = t.value;
  } else if (t.classList.contains('ip-ptx')) {
    if (si >= 0 && si < st.length && pi >= 0 && pi < (st[si].points || []).length) {
      st[si].points[pi] = t.value;
    }
  } else if (t.classList.contains('ip-ref')) {
    if (!row.item) row.item = {};
    row.item.ref = t.value;
  } else {
    return;
  }
  ipRefreshMeta(row);
});

/* 拖动排序：拖动手柄所在的环节行可上下移动 */
$('#ipList').addEventListener('dragstart', (e) => {
  const el = e.target.closest ? e.target.closest('.ip-stage') : null;
  if (!el) return;
  IMP_DRAG = { id: el.dataset.id, from: parseInt(el.dataset.si, 10) };
  el.classList.add('drag');
  if (e.dataTransfer) {
    e.dataTransfer.effectAllowed = 'move';
    try { e.dataTransfer.setData('text/plain', el.dataset.si); } catch (err) {}
  }
});
$('#ipList').addEventListener('dragover', (e) => {
  if (!IMP_DRAG) return;
  const el = e.target.closest ? e.target.closest('.ip-stage') : null;
  if (!el || el.dataset.id !== IMP_DRAG.id) return;
  e.preventDefault();
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
  el.classList.add('over');
});
$('#ipList').addEventListener('dragleave', (e) => {
  const el = e.target.closest ? e.target.closest('.ip-stage') : null;
  if (el) el.classList.remove('over');
});
$('#ipList').addEventListener('drop', (e) => {
  const el = e.target.closest ? e.target.closest('.ip-stage') : null;
  if (!el || !IMP_DRAG || el.dataset.id !== IMP_DRAG.id) { IMP_DRAG = null; return; }
  e.preventDefault();
  const row = IMP_ROWS.find((x) => x.id === IMP_DRAG.id);
  const to = parseInt(el.dataset.si, 10);
  const from = IMP_DRAG.from;
  IMP_DRAG = null;
  if (!row || !(to >= 0) || !(from >= 0) || to === from) { renderImportList(); return; }
  const st = ipStages(row);
  if (from >= st.length) { renderImportList(); return; }
  const moved = st.splice(from, 1)[0];
  st.splice(to, 0, moved);
  renderImportList();
  ipRefreshMeta(row);
  toast('环节顺序已调整：第 ' + (from + 1) + ' → 第 ' + (to + 1));
});
$('#ipList').addEventListener('dragend', () => {
  IMP_DRAG = null;
  const box = $('#ipList');
  if (box) box.querySelectorAll('.ip-stage').forEach((el) => el.classList.remove('drag', 'over'));
});
$('#ipAll').addEventListener('click', () => { IMP_ROWS.forEach((x) => { x.checked = true; }); renderImportList(); });
$('#ipNone').addEventListener('click', () => { IMP_ROWS.forEach((x) => { x.checked = false; }); renderImportList(); });

$('#btnImportCommit').addEventListener('click', async () => {
  const picked = IMP_ROWS.filter((x) => x.checked && (x.question || '').trim().length >= 4);
  if (!picked.length) { toast('没有勾选题目', true); return; }
  const name = ($('#ipBankName').value || '').trim() || '导入题库';
  const items = picked.map((x) => {
    const it = Object.assign({}, x.item);
    it.question = (x.question || '').trim();
    it.qtype = (x.qtype || '').trim();
    // 预览页里编辑过的环节 / 要点 / 参考要点随「确认入库」一起写入
    const st = ipStages(x).map((s) => ({
      stage: String(s.stage || '').trim(),
      label: String(s.label || '').trim(),
      points: (s.points || []).map((p) => String(p == null ? '' : p).trim()).filter(Boolean),
    })).filter((s) => s.stage || s.points.length);
    it.structure = st;
    it.ref = String((x.item && x.item.ref) || '');
    if (x.item && x.item.exam) it.exam = x.item.exam;
    if (x.item && x.item.keys) it.keys = x.item.keys;
    return it;
  });
  const btn = $('#btnImportCommit');
  btn.disabled = true;
  toast('正在入库 ' + items.length + ' 题 …');
  try {
    const r = await api('/api/bank/import_commit', { name: name, items: items, merge: true });
    toast('已入库 ' + (r.added || items.length) + ' 题，题库「' + (r.bank || name) + '」现有 ' + r.count + ' 题');
    closeModal($('#modalImportPreview'));
    IMP_ROWS = []; IMP_FILES = [];
    await loadBanks();
    await showBankItems(r.file);
  } catch (err) { toast(err.message, true); }
  btn.disabled = false;
});

/* 设置 */
$('#btnSettings').addEventListener('click', async () => {
  /* 先把上一次残留的「未提交改动」标记清掉，再异步取配置；
     取回后用非强制回填：用户在等待期间已经敲进去的内容不会被冲掉 */
  if (!$('#modalSettings') || !$('#modalSettings').classList.contains('open')) {
    ['#cfgBase', '#cfgModel', '#cfgKey', '#cfgPause', '#cfgMaxSec', '#cfgMaxChars']
      .forEach((sel) => { const el = $(sel); if (el) clearDirty(el); });
  }
  const c = await api('/api/config');
  setVal($('#cfgBase'), c.base_url || '', false);
  setVal($('#cfgModel'), c.model || '', false);
  setVal($('#cfgKey'), '', false);
  $('#cfgRealtime').checked = !!c.enable_realtime;
  setVal($('#cfgPause'), c.para_pause_gap != null ? c.para_pause_gap : 1.5, false);
  setVal($('#cfgMaxSec'), c.para_max_sec != null ? c.para_max_sec : 55, false);
  setVal($('#cfgMaxChars'), c.para_max_chars != null ? c.para_max_chars : 240, false);
  if ($('#cfgCorrect')) $('#cfgCorrect').checked = c.enable_correction !== false;
  if ($('#cfgTheme')) $('#cfgTheme').value = currentTheme();
  // 输入源与设备（记忆值优先）
  DEV_SAVED = {
    kind: c.input_kind || DEV_SAVED.kind,
    name: c.input_device_name || '',
    index: (c.input_device_index != null ? c.input_device_index : -1),
  };
  setKind(S.kind || DEV_SAVED.kind || 'loopback', true);
  $('#cfgTip').textContent = c.has_key ? ('当前 Key：' + c.api_key_masked) : '尚未配置 Key，AI 点评不可用';
  loadLogInfo();
  loadFirewallInfo();
  openModal('#modalSettings');
});
/* 防火墙放行：查状态 + 一键放行 / 撤销（手机连不上时用） */
function fwRender(j) {
  const badge = $('#fwBadge');
  const txt = $('#fwStatusText');
  const prog = $('#fwProgram');
  if (!badge || !txt) return;
  const level = (j && j.level) || 'unknown';
  badge.className = 'fw-badge' + (level === 'all' ? ' ok' : level === 'partial' ? ' warn' : level === 'none' ? ' bad' : '');
  badge.textContent = level === 'all' ? '已放行' : level === 'partial' ? '部分放行' : level === 'none' ? '未放行' : '未知';
  txt.textContent = (j && (j.text || j.msg)) || '状态未知';
  if (prog) {
    const p = (j && j.program) || '';
    prog.textContent = p ? ('放行对象：' + p) : '';
    prog.hidden = !p;
  }
}
async function loadFirewallInfo() {
  const badge = $('#fwBadge');
  const txt = $('#fwStatusText');
  if (badge) { badge.className = 'fw-badge'; badge.textContent = '读取中…'; }
  if (txt) txt.textContent = '正在读取 Windows 防火墙状态…';
  try {
    const r = await fetch('/api/system/firewall');
    const j = await r.json();
    if (!j || j.ok === false) throw new Error((j && j.msg) || ('请求失败 ' + r.status));
    fwRender(j);
  } catch (e) {
    if (badge) { badge.className = 'fw-badge warn'; badge.textContent = '未知'; }
    if (txt) txt.textContent = '防火墙状态读取失败：' + (e.message || e);
  }
}
async function fwAction(kind) {
  const btn = $(kind === 'allow' ? '#btnFwAllow' : '#btnFwRemove');
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = '处理中…';
  try {
    const r = await fetch('/api/system/firewall/' + kind, { method: 'POST' });
    const j = await r.json();
    if (!j.ok) throw new Error(j.msg || '操作失败');
    fwRender(j.status || {});
    toast(j.msg || (kind === 'allow' ? '已放行' : '已撤销'));
  } catch (e) {
    toast((kind === 'allow' ? '放行失败：' : '撤销失败：') + (e.message || e), true);
    loadFirewallInfo();
  }
  btn.disabled = false;
  btn.textContent = old;
}
$('#btnFwAllow')?.addEventListener('click', () => {
  if (!confirm('给当前程序添加一条入站放行规则（域 / 专用 / 公用网络全部适用）？\n\n'
    + '若弹出「用户账户控制」授权框，需点「是」才会生效；点「否」则保持原样。\n'
    + '只添加本程序这一条规则，不修改防火墙总开关。')) return;
  fwAction('allow');
});
$('#btnFwRemove')?.addEventListener('click', () => {
  if (!confirm('撤销本程序创建的入站放行规则？\n\n只删除这条规则，系统里其它防火墙规则不受影响。')) return;
  fwAction('remove');
});

/* 错误日志：读取日志目录信息 + 一键打开目录（排查问题用） */
async function loadLogInfo() {
  const box = $('#logDirPath');
  const tip = $('#logSizeTip');
  if (!box) return;
  try {
    const r = await fetch('/api/logs/info');
    const j = await r.json();
    if (!j || j.ok === false) throw new Error((j && j.msg) || ('请求失败 ' + r.status));
    box.textContent = j.dir || '--';
    box.title = [j.dir, j.current || j.current_name].filter(Boolean).join('\n');
    if (tip) {
      const cnt = (j.files || []).length;
      tip.textContent = '当前文件：' + (j.current_name || '尚未生成')
        + '　当前大小：' + (j.current ? (j.current_size_text || '') : '0 B')
        + '　累计占用：' + (j.total_size_text || '0 B') + '（共 ' + cnt + ' 个文件）';
    }
  } catch (e) {
    box.textContent = '--';
    if (tip) tip.textContent = '日志信息读取失败：' + (e.message || e);
  }
}
$('#btnOpenLogs')?.addEventListener('click', async () => {
  const btn = $('#btnOpenLogs');
  btn.disabled = true;
  try {
    const r = await fetch('/api/logs/open', { method: 'POST' });
    const j = await r.json();
    if (!j.ok) throw new Error(j.msg || '打开失败');
    toast('已打开日志目录');
  } catch (e) {
    toast('打开日志目录失败：' + (e.message || e), true);
  }
  btn.disabled = false;
});

$('#cfgKind').addEventListener('change', () => {
  setKind($('#cfgKind').value, true);
});
$('#btnSaveCfg').addEventListener('click', async () => {
  const cd = $('#cfgDevice');
  const devOpt = cd && cd.value !== '' ? cd.options[cd.selectedIndex] : null;
  const devName = devOpt ? (devOpt.dataset.name || devOpt.textContent) : '';
  const body = {
    base_url: $('#cfgBase').value.trim(),
    model: $('#cfgModel').value.trim(),
    enable_realtime: $('#cfgRealtime').checked,
    enable_correction: $('#cfgCorrect') ? $('#cfgCorrect').checked : true,
    para_pause_gap: Number($('#cfgPause').value) || 1.5,
    para_max_sec: Number($('#cfgMaxSec').value) || 55,
    para_max_chars: Number($('#cfgMaxChars').value) || 240,
    input_kind: $('#cfgKind') ? $('#cfgKind').value : S.kind,
    input_device_name: devName,
    input_device_index: (cd && cd.value !== '') ? Number(cd.value) : -1,
  };
  const k = $('#cfgKey').value.trim();
  if (k) body.api_key = k;
  try {
    const r = await api('/api/config', body);
    S.config = r.config;
    DEV_SAVED = { kind: body.input_kind, name: devName, index: body.input_device_index };
    setKind(body.input_kind, true);
    toast('设置已保存' + (S.running ? '，下次开始听记时生效' : ''));
    closeModal($('#modalSettings'));
  } catch (e) { toast(e.message, true); }
});
$('#btnTestCfg').addEventListener('click', async () => {
  const out = $('#cfgTestOut');
  const show = (txt, bad) => {
    if (!out) { toast(txt, bad); return; }
    out.hidden = false;
    out.classList.toggle('bad', !!bad);
    out.textContent = txt;
  };
  try {
    const r = await api('/api/config/test', {});
    const lines = [
      '结果：' + (r.ok ? '接口可用（' + (r.content || '（空）') + '）' : '接口失败（' + (r.status ? 'HTTP ' + r.status : '无响应') + '）'),
      '请求地址：' + (r.url || '-'),
      '模型：' + (r.model || '-'),
      '配置来源：' + (r.config_path || '-') + (r.api_key_masked ? '（Key ' + r.api_key_masked + '）' : ''),
      'HTTP 状态码：' + (r.status != null ? r.status : '-'),
    ];
    if (!r.ok) lines.push('原始返回：' + ((r.body || r.error || '（无正文）').slice(0, 600)));
    show(lines.join('\n'), !r.ok);
    toast(r.ok ? '连通正常' : '连通失败：见下方原始返回', !r.ok);
  } catch (e) { show('请求异常：' + e.message, true); toast(e.message, true); }
});

/* 点评 / 存档 */
function renderReview(md, mode) {
  const rv = $('#review');
  rv.innerHTML = md2html(md || '') || '<div class="muted">点评内容为空</div>';
  const chars = (md || '').replace(/\s/g, '').length;
  const tag = mode === 'ai' ? 'AI 点评' : (mode === 'local' ? '本地量化诊断' : '点评');
  $('#reviewMeta').textContent = tag + ' · 全文 ' + chars + ' 字 · 可滚动或点「全屏」查看完整内容';
  rv.scrollTop = 0;
}

function renderReviewReset() {
  setReviewFull(false);
  $('#reviewMeta').textContent = '';
  $('#review').innerHTML = '<div class="muted">停止听记后会自动生成最终点评；也可点「重新点评」手动再评一次。内容按「先时间 · 再优点 · 最后问题」三段给出，可滚动或全屏查看全文。</div>';
}

let REVIEWING = false;
async function doReview(auto) {
  if (REVIEWING) { toast('点评正在生成中，请稍候…'); return; }
  REVIEWING = true;
  const btn = $('#btnReview');
  const hasKey = S.config && S.config.has_key;
  btn.disabled = true;
  btn.textContent = auto ? '自动点评中…' : '生成中…';
  setReviewFull(false);
  $('#reviewMeta').textContent = auto ? '答题已结束，正在自动生成最终点评…' : '正在生成点评…';
  $('#review').innerHTML = '<div class="muted">' +
    (hasKey ? 'AI 正在按段落比对，通常 20-45 秒…' : '正在做本地量化诊断…') + '</div>';
  try {
    const r = await api('/api/review', {});
    S.session.review = r.review;
    renderReview(r.review, r.mode);
    if (r.mode === 'local') {
      toast(auto ? '已自动生成本地量化诊断（未配置 AI）' : '已生成本地量化诊断（未配置 AI，内容层面建议需接 AI）');
    } else if (r.error) {
      toast('AI 调用失败，已回退本地诊断：' + r.error, true);
    } else {
      toast(auto ? '最终点评已自动生成（时间 / 优点 / 问题）' : '点评已重新生成（时间 / 优点 / 问题）');
    }
  } catch (e) {
    $('#review').innerHTML = '<div class="muted">生成失败：' + esc(e.message) + '</div>';
    $('#reviewMeta').textContent = '';
  } finally {
    btn.disabled = false;
    btn.textContent = '重新点评';
    REVIEWING = false;
  }
}

$('#btnReview').addEventListener('click', () => doReview(false));

/* 点评全屏态：唯一入口仍是标题栏「全屏 / 退出全屏」；但面板全屏后会盖住标题栏，
   所以在全屏时同步浮出右上角同款 ×（#btnReviewExit）。Esc、×、标题栏按钮三条路
   都走 setReviewFull，显隐状态只有一处真相，不会出现两个关闭入口同时可见。 */
function setReviewFull(on) {
  const rv = $('#review');
  if (!rv) return;
  const want = !!on;
  rv.classList.toggle('full', want);
  const b = $('#btnReviewFull');
  if (b) b.textContent = want ? '退出全屏' : '全屏';
  const x = $('#btnReviewExit');
  if (x) x.hidden = !want;
  if (want) rv.scrollTop = 0;
}
$('#btnReviewFull').addEventListener('click', () => {
  const rv = $('#review');
  setReviewFull(!rv.classList.contains('full'));
});
$('#btnReviewExit').addEventListener('click', () => setReviewFull(false));

$('#btnSave').addEventListener('click', async () => {
  try {
    const r = await api('/api/save', {});
    toast('已存档：' + r.md);
  } catch (e) { toast(e.message, true); }
});

$('#btnNew').addEventListener('click', async () => {
  if (S.running) { toast('请先停止听记', true); return; }
  if (!confirm('新建一场会清空当前逐字稿（未存档将丢失），继续？')) return;
  try {
    const r = await api('/api/session/new', {
      question: '', qtype: '', ref: '', candidate: $('#candidate').value,
    });
    S.session = r.session; S.sel = null;
    renderReviewReset();
    $('#timer').textContent = '00:00';
    renderTranscript(true); renderMarks(); updateFloat();
    toast('已新建一场');
  } catch (e) { toast(e.message, true); }
});

/* ---------------- 历史记录：筛选 / 检索 / 导出 ---------------- */
const HIST_F = { qtype: '', student: '', d1: '', d2: '', kw: '' };
let HIST_ALL = [];
let HIST_TIMER = null;

function histQuery() {
  const q = [];
  if (HIST_F.qtype) q.push('qtype=' + encodeURIComponent(HIST_F.qtype));
  if (HIST_F.student) q.push('student=' + encodeURIComponent(HIST_F.student));
  if (HIST_F.d1) q.push('d1=' + encodeURIComponent(HIST_F.d1));
  if (HIST_F.d2) q.push('d2=' + encodeURIComponent(HIST_F.d2));
  if (HIST_F.kw) q.push('kw=' + encodeURIComponent(HIST_F.kw));
  return q.length ? '?' + q.join('&') : '';
}

function histFilterDesc() {
  const bits = [];
  if (HIST_F.qtype) bits.push('题型＝' + HIST_F.qtype);
  if (HIST_F.student) bits.push('学生＝' + HIST_F.student);
  if (HIST_F.d1 && HIST_F.d2) bits.push('日期＝' + HIST_F.d1 + ' 至 ' + HIST_F.d2);
  else if (HIST_F.d1) bits.push('日期≥' + HIST_F.d1);
  else if (HIST_F.d2) bits.push('日期≤' + HIST_F.d2);
  if (HIST_F.kw) bits.push('关键词＝“' + HIST_F.kw + '”');
  return bits;
}

function histFillSelect(sel, items, cur, allText) {
  if (!sel) return;
  const opts = ['<option value="">' + esc(allText) + '</option>'].concat(
    items.map((it) => '<option value="' + esc(it.name) + '">' + esc(it.name) + '（' + it.count + '）</option>')
  );
  sel.innerHTML = opts.join('');
  if (cur && !items.some((it) => it.name === cur)) {
    sel.insertAdjacentHTML('beforeend', '<option value="' + esc(cur) + '">' + esc(cur) + '</option>');
  }
  sel.value = cur || '';
}

async function histLoad() {
  const list = $('#histList');
  if (list) list.innerHTML = '<div class="muted">加载中…</div>';
  try {
    const r = await api('/api/records' + histQuery());
    HIST_ALL = r.records || [];
    histFillSelect($('#histQtype'), (r.facets || {}).qtypes || [], HIST_F.qtype, '全部题型');
    histFillSelect($('#histStudent'), (r.facets || {}).students || [], HIST_F.student, '全部学生');
    const dmin = (r.facets || {}).date_min || '';
    const dmax = (r.facets || {}).date_max || '';
    if ($('#histD1')) { $('#histD1').min = dmin; $('#histD1').max = dmax; }
    if ($('#histD2')) { $('#histD2').min = dmin; $('#histD2').max = dmax; }
    if ($('#histD1')) $('#histD1').value = HIST_F.d1;
    if ($('#histD2')) $('#histD2').value = HIST_F.d2;
    if ($('#histKw')) $('#histKw').value = HIST_F.kw;
    histRender(r);
  } catch (e) {
    if (list) list.innerHTML = '<div class="hist-empty">加载失败：' + esc(e.message) + '</div>';
  }
}

function histRender(r) {
  const list = $('#histList');
  const stat = $('#histStat');
  const acts = histFilterDesc();
  const recs = r.records || [];
  if (stat) {
    stat.innerHTML = '共 ' + (r.total || 0) + ' 条存档'
      + (acts.length ? '，当前筛选命中 <b>' + recs.length + '</b> 条（' + esc(acts.join(' · ')) + '）'
                     : '，未加筛选');
    stat.title = '列表依次显示：题型 / 学生 / 日期 / 时长 / 字数 / 参考分（参考分＝结构覆盖 40 + 语速节奏 35 + 问题扣分 25，本地测算，仅供参考）';
  }
  if (!list) return;
  if (!recs.length) {
    const desc = acts.length ? esc(acts.join(' · ')) : '当前没有任何存档';
    list.innerHTML = '<div class="hist-empty">'
      + '<div class="he-t">没有符合条件的记录</div>'
      + '<div class="he-s">筛选条件：' + desc + '</div>'
      + '<div class="he-s">可以放宽日期范围、换学生或题型，或直接清除筛选查看全部。</div>'
      + (acts.length ? '<button class="mini" id="histEmptyClear">清除筛选</button>' : '')
      + '</div>';
    const b = $('#histEmptyClear');
    if (b) b.addEventListener('click', histClear);
    return;
  }
  list.innerHTML = recs.map((x) => {
    const score = (x.score === null || x.score === undefined) ? '参考分 —' : ('参考分 ' + x.score);
    const stu = x.stu_n > 1 ? (x.student + ' 等 ' + x.stu_n + ' 人') : x.student;
    return '<div class="rec-row" data-file="' + esc(x.file) + '" title="点击载入查看">'
      + '<span class="rq">' + esc(x.q_short || '（未填写题目）') + '</span>'
      + '<span class="hd-tag">' + esc(x.qtype) + '</span>'
      + '<span class="hd-stu">' + esc(stu) + '</span>'
      + '<span class="rd">' + esc(x.date || '—') + '</span>'
      + '<span class="rd">' + fmt(x.duration) + '</span>'
      + '<span class="rd">' + x.chars + ' 字</span>'
      + '<span class="hd-score" title="结构 ' + ((x.score_info || {}).struct || 0) + ' / 语速 '
      + ((x.score_info || {}).pace || 0) + ' / 问题 ' + ((x.score_info || {}).issue || 0)
      + '，语速 ' + ((x.score_info || {}).cpm || 0) + ' 字/分，覆盖环节 '
      + esc((x.score_info || {}).stages || '') + '">' + score + '</span>'
      + '<button class="mini hd-exp" data-exp="' + esc(x.file) + '" title="导出这份存档的整场复盘稿（Word）">导出</button>'
      + (x.snippet ? '<span class="rec-sub">命中' + esc(x.hit) + '：' + esc(x.snippet) + '</span>' : '')
      + '</div>';
  }).join('');
  list.querySelectorAll('.rec-row').forEach((row) => {
    row.addEventListener('click', () => histOpen(row.dataset.file));
  });
  list.querySelectorAll('.hd-exp').forEach((btn) => {
    btn.addEventListener('click', (ev) => {
      ev.stopPropagation();
      exportDocx(btn.dataset.exp);
    });
  });
}

function histClear() {
  HIST_F.qtype = ''; HIST_F.student = ''; HIST_F.d1 = ''; HIST_F.d2 = ''; HIST_F.kw = '';
  histLoad();
}

/* 历史回看用到的学生档案（存档里每位学生各自持有 segments / paragraphs） */
function histStuOf(sid) {
  const d = S.histData;
  return ((d && d.students) || []).find((s) => s.id === sid) || null;
}

/* 回看态切换学生：段落 / 逐字稿 / 精简版 / 点评全部按该生自己的存档重绘 */
function histShowStudent(sid, initial) {
  const d = S.histData;
  if (!d) return null;
  const list = d.students || [];
  const st = histStuOf(sid) || list[0] || null;
  if (!st) return null;
  S.cur = st.id;
  S.sel = null;
  S.session = S.session || {};
  S.session.cur_student = st.id;
  S.session.paragraphs = st.paragraphs || [];      // 紧凑版 / 复制 / 取稿都读这里
  S.session.segments = st.segments || [];
  S.session.duration = st.duration || 0;
  S.session.review = st.review || (initial ? (d.review || '') : '');
  S.session.review_mode = st.review_mode || d.review_mode || '';
  setVal($('#candidate'), st.name || d.candidate || '', true);
  $('#timer').textContent = fmt(st.duration || 0);
  renderStudents();
  renderTranscript(true); renderMarks(); renderPlain(true); updateFloat();
  if (S.session.review) {
    renderReview(S.session.review, S.session.review_mode || '');
  } else {
    $('#review').innerHTML = '<div class="muted">该记录没有点评</div>';
    $('#reviewMeta').textContent = '';
  }
  if (typeof qnOnSession === 'function') {
    qnOnSession({ student_id: st.id || '', notes: st.notes || [] });
  }
  // 精简要点浮层开着时跟着换人重新取稿，避免停留在上一位学生的稿子
  if (typeof PT !== 'undefined' && PT.open) {
    const ta = ptEl('ptText');
    if (ta) ta.value = '';
    ptFetch(true);
  }
  return st;
}

async function histOpen(file) {
  try {
    const d = await api('/api/records/load?file=' + encodeURIComponent(file));
    const data = d.data;
    S.histFile = file;
    S.histData = data;
    S.sel = null;
    // 回看时学生 chips 需要摘要字段（segments 存的是数组，直接显示会变成 [object Object]）
    S.students = (data.students || []).map((s) => ({
      id: s.id,
      name: s.name || '未命名',
      duration: s.duration || 0,
      segments: (s.segments || []).length,
      has_review: !!s.review,
    }));
    S.cur = data.cur_student || (S.students[0] && S.students[0].id) || '';
    S.session = data;
    setVal($('#question'), data.question || '', true);
    syncQtypeTo(data.qtype || '', true);
    setVal($('#ref'), data.ref || '', true);
    histShowStudent(S.cur, true);
    closeModal($('#modalHistory'));
    const st = histStuOf(S.cur) || {};
    const np = (st.paragraphs || []).length;
    toast('已载入历史记录（只读查看 · ' + np + ' 段精简稿）');
  } catch (e) { toast(e.message, true); }
}

function histDownload(name) {
  const a = document.createElement('a');
  a.href = '/api/export/download?name=' + encodeURIComponent(name);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

async function exportDocx(file) {
  try {
    const r = await api('/api/export/docx', file ? { file: file } : {});
    histDownload(r.name);
    toast('已导出 Word：' + r.name + '（' + r.paragraphs + ' 段）');
  } catch (e) { toast(e.message, true); }
}

$('#btnHistory').addEventListener('click', () => {
  histLoad();
  openModal('#modalHistory');
});

$('#histFilter').addEventListener('change', (ev) => {
  const t = ev.target;
  if (t.id === 'histQtype') HIST_F.qtype = t.value;
  else if (t.id === 'histStudent') HIST_F.student = t.value;
  else if (t.id === 'histD1') HIST_F.d1 = t.value;
  else if (t.id === 'histD2') HIST_F.d2 = t.value;
  else return;
  histLoad();
});

$('#histKw').addEventListener('input', () => {
  HIST_F.kw = $('#histKw').value.trim();
  clearTimeout(HIST_TIMER);
  HIST_TIMER = setTimeout(histLoad, 320);
});

$('#histKw').addEventListener('keydown', (ev) => {
  if (ev.key === 'Enter') {
    ev.preventDefault();
    HIST_F.kw = $('#histKw').value.trim();
    clearTimeout(HIST_TIMER);
    histLoad();
  }
});

$('#histClear').addEventListener('click', histClear);

$('#btnExportDocx').addEventListener('click', () => {
  if (!S.session) { toast('当前没有可导出的内容', true); return; }
  exportDocx(S.histFile || '');
});


/* ---------------- SSE ---------------- */
function connectSSE() {
  const es = new EventSource('/api/stream');
  es.onmessage = (ev) => {
    let d;
    try { d = JSON.parse(ev.data); } catch { return; }
    if (d.type === 'partial') {
      S.partial = { text: d.text, t_start: d.t_start, t_end: d.t_end, key: d.key };
      renderLive();
    } else if (d.type === 'segment') {
      if (!S.session) return;
      // 归属过滤：新句子只上屏给「这次录音的学生」，只读视图一律不写
      if (S.viewOnly) return;
      if (d.student && S.cur && d.student !== S.cur) return;
      clearLive();
      S.session.segments.push(d.seg);
      S.session.duration = d.duration;
      if (d.para) upsertPara(d.para);
      renderTranscript(true);
      renderPlain(true);
      updateFloat();
    } else if (d.type === 'paragraph') {
      if (!S.session || S.viewOnly) return;
      upsertPara(d.para);
      renderTranscript(false);
      renderPlain(false);
    } else if (d.type === 'plain') {
      // 段落说完 → 精简版即时上屏
      if (!S.session || S.viewOnly) return;
      if (d.para && d.plain_parts) d.para.plain_parts = d.plain_parts;
      // 句子手动归属：别的窗口改过时同步到本地 seg，保证换视图后归属一致
      ((d.para && d.para.plain_parts) || []).forEach((pt) => {
        const seg = findSeg(pt.sid);
        if (!seg) return;
        if (pt.stage_manual) seg.stage_manual = pt.stage_manual;
        else delete seg.stage_manual;
      });
      if (d.para) upsertPara(d.para);
      renderPlain(true);
      /* 精简稿在本窗口取稿之后又变了：提示可能已不是最新，建议重新取稿 */
      if (PT.stamp && PT.seq !== null && typeof d.edit_seq === 'number' && d.edit_seq !== PT.seq) {
        PT.stale = true;
        ptStampTxt();
      }
    } else if (d.type === 'mark') {
      if (S.viewOnly) return;
      const seg = findSeg(d.id);
      if (seg) {
        seg.tags = d.tags || [];
        seg.note = d.note || '';
        renderTranscript(false); renderMarks(); renderPlain(false); updateFloat();
      }
    } else if (d.type === 'para_error') {
      toast('段落点评失败：' + d.msg, true);
      renderTranscript(false);
    } else if (d.type === 'ai_note') {
      if (S.viewOnly) return;
      const seg = findSeg(d.id);
      if (seg) { seg.ai_note = d.ai_note; renderTranscript(false); }
    } else if (d.type === 'status') {
      if (d.state === 'listening') {
        const hadRec = !!S.recSid;
        if (typeof d.rec_student === 'string' && d.rec_student) S.recSid = d.rec_student;
        setRunning(true, hadRec);
        S.viewOnly = !!(S.recSid && S.cur && S.cur !== S.recSid);
        renderStudents();
      }
      if (d.state === 'closed' || d.state === 'stopped') {
        clearLive();
        setRunning(false);
        S.recSid = '';
        S.viewOnly = false;
        renderStudents();
        (async () => {
          try {
            const st = await api('/api/state');
            applySession(st.session, true);
          } catch (e) { /* 忽略 */ }
        })();
        if (d.msg) toast('录音中断：' + d.msg, true);
      }
    } else if (d.type === 'reset') {
      if (S.viewOnly) return;
      clearLive();
      S.session = d.session; S.sel = null;
      S.students = d.session.students || S.students;
      S.cur = d.session.cur_student || S.cur;
      renderStudents();
      renderTranscript(true); renderMarks(); renderPlain(true); updateFloat();
      if (typeof qnOnSession === 'function') qnOnSession(d.session);
    } else if (d.type === 'students') {
      S.students = d.students || S.students;
      S.cur = d.cur || S.cur;
      renderStudents();
    } else if (d.type === 'switch_student') {
      // 学生切换：整块记录换成该学生自己的
      S.sel = null;
      S.cur = d.cur || S.cur;
      if (d.students) S.students = d.students;
      clearLive();
      renderStudents();
      (async () => {
        try {
          const st = await api('/api/state');
          S.students = st.session.students || S.students;
          applySession(st.session, true);
        } catch (e) { /* 忽略 */ }
      })();
    } else if (d.type === 'reload') {
      S.session = d.session;
      renderTranscript(false); renderMarks(); renderPlain(true); updateFloat();
    }
  };
  es.onerror = () => { /* 浏览器自动重连 */ };
}

/* ---------------- 初始化 ---------------- */
(async function init() {
  try {
    const st = await api('/api/state');
    S.session = st.session;
    S.config = st.config;
    S.session.paragraphs = st.session.paragraphs || [];
    S.students = st.session.students || [];
    S.cur = st.session.cur_student || st.session.student_id || '';
    S.recSid = st.session.rec_student || '';
    S.viewOnly = !!(S.recSid && S.cur && S.cur !== S.recSid);
    S.kind = (st.config && st.config.input_kind) || st.device_kind || 'loopback';
    renderStudents();
    await loadQtypes();
    refreshQtypeSelects();
    setVal($('#question'), st.session.question || '', true);
    syncQtypeTo(st.session.qtype || '', true);
    setVal($('#ref'), st.session.ref || '', true);
    setVal($('#candidate'), st.session.candidate || '', true);
    $('#timer').textContent = fmt(st.session.duration);
    qaShowCard(st.session.q_analysis);
    if (st.session.review) renderReview(st.session.review, st.session.review_mode);
    renderTranscript(true); renderMarks(); renderPlain(true); updateFloat();
    if (typeof layApply === 'function') layApply();
    setRunning(!!st.running);
  if (st.running) lvlTick();
    if (st.running) {
      S.t0 = (st.session.rec_t0 ? st.session.rec_t0 * 1000
        : Date.now() - (st.session.duration || 0) * 1000);
    }
  } catch (e) {
    toast('无法连接本地服务：' + e.message, true);
  }
  loadDevices();
  loadBanks();
  connectSSE();
})();

/* 录音中切换输入源 / 设备 → 立即停采并按新设备重启，断点不打断当前会话 */
async function rewireSource(msg) {
  if (!S.running) return;
  try {
    await api('/api/stop', {});
    setRunning(false);
    await new Promise((r) => setTimeout(r, 350));
    await startRec();
    if (msg) toast(msg);
  } catch (e) {
    toast('切换输入源失败：' + e.message, true);
  }
}

$('#btnStart').addEventListener('click', startRec);
$('#btnStop').addEventListener('click', stopRec);
$('#btnCopyPlain').addEventListener('click', copyPlain);
$('#btnTogglePlain').addEventListener('click', togglePlain);

// ---- 输入源切换（系统内录 / 麦克风） ----
$$('#srcSeg .segbtn').forEach((b) => {
  b.addEventListener('click', async () => {
    const want = b.dataset.kind;
    const changed = (S.kind || 'loopback') !== want;
    setKind(want, true);
    const sel = $('#deviceSel');
    const op = (sel && sel.value !== '') ? sel.options[sel.selectedIndex] : null;
    try {
      await api('/api/config', {
        input_kind: S.kind,
        input_device_name: op ? (op.dataset.name || op.textContent) : '',
        input_device_index: (sel && sel.value !== '') ? Number(sel.value) : -1,
      });
    } catch (e) { /* 记忆失败不影响使用 */ }
    if (S.running && changed) {
      await rewireSource(S.kind === 'mic' ? '已切到麦克风输入，听记已按新设备重启' : '已切到系统内录，听记已按新设备重启');
    } else {
      toast(S.kind === 'mic' ? '已切到麦克风输入' : '已切到系统内录');
    }
  });
});
$('#deviceSel').addEventListener('change', async () => {
  const sel = $('#deviceSel');
  const op = sel.value !== '' ? sel.options[sel.selectedIndex] : null;
  try {
    await api('/api/config', {
      input_kind: S.kind,
      input_device_name: op ? (op.dataset.name || op.textContent) : '',
      input_device_index: sel.value !== '' ? Number(sel.value) : -1,
    });
    if (S.running) {
      await rewireSource('已切到该设备，听记已重启');
    } else {
      toast('已记住该设备，下次自动沿用');
    }
  } catch (e) { /* 忽略 */ }
});

// ---- 学生（一场多人） ----
$('#btnAddStudent').addEventListener('click', addStudent);
$('#btnCompare').addEventListener('click', showCompare);
$('#studentChips').addEventListener('click', (e) => {
  const del = e.target.closest('[data-del]');
  if (del) { e.stopPropagation(); delStudent(del.dataset.del); return; }
  const chip = e.target.closest('.chip');
  if (chip) switchStudent(chip.dataset.sid);
});
$('#studentChips').addEventListener('dblclick', (e) => {
  const chip = e.target.closest('.chip');
  if (chip && !e.target.closest('[data-del]')) renameStudent(chip.dataset.sid);
});

// ---- 精简版：点句子定位到逐字稿 / 智能纠错 / 段落结构菜单 ----
$('#plainList').addEventListener('click', (e) => {
  const pBtn = e.target.closest('button[data-act="pmenu"]');
  if (pBtn) {
    const pItem = pBtn.closest('[data-pid]');
    if (pItem) {
      const r = pBtn.getBoundingClientRect();
      openParaMenu(pItem.dataset.pid, r.left, r.bottom + 4);
    }
    return;
  }
});

// ---- 精简版：鼠标选句（单击 / Ctrl 多选 / Shift 连选 / 按住拖选） ----
$('#plainList').addEventListener('mousedown', (e) => {
  const s = e.target.closest('.psent');
  if (!s || e.button !== 0 || !s.dataset.sid) return;
  // 正在就地改字时把鼠标完全交给 contenteditable（否则选不了插入点）
  if (s._editing || P_EDITKEY) return;
  if (e.target.closest('button')) return;
  e.preventDefault();                       // 用句子级选中替代浏览器文本选择
  P_DRAG = {
    sid: s.dataset.sid,
    moved: false,
    ctrl: !!(e.ctrlKey || e.metaKey),
    shift: !!e.shiftKey,
  };
});

document.addEventListener('mousemove', (e) => {
  if (!P_DRAG) return;
  const el = document.elementFromPoint(e.clientX, e.clientY);
  const s = el && el.closest ? el.closest('#plainList .psent') : null;
  if (!s || !s.dataset.sid) return;
  const sid = s.dataset.sid;
  if (sid !== P_DRAG.sid) P_DRAG.moved = true;
  if (!P_DRAG.moved) return;
  const base = P_DRAG.ctrl ? P_SEL.slice() : [];
  const rg = rangeSids(P_DRAG.sid, sid);
  P_SEL = Array.from(new Set(base.concat(rg)));
  paintSel();
});

document.addEventListener('mouseup', (e) => {
  if (!P_DRAG) return;
  const d = P_DRAG;
  P_DRAG = null;
  if (d.moved) { P_ANCHOR = d.sid; return; }
  const sid = d.sid;
  // 修饰键以抬起这一刻为准（避免 mousedown 阶段丢失 Shift/Ctrl）
  const shift = d.shift || e.shiftKey;
  const ctrl = d.ctrl || e.ctrlKey || e.metaKey;
  if (shift && P_ANCHOR && P_ANCHOR !== sid) {
    P_SEL = rangeSids(P_ANCHOR, sid);
  } else if (ctrl) {
    const i = P_SEL.indexOf(sid);
    if (i >= 0) P_SEL.splice(i, 1); else P_SEL.push(sid);
  } else {
    P_SEL = (P_SEL.length === 1 && P_SEL[0] === sid) ? [] : [sid];
  }
  P_ANCHOR = sid;
  paintSel();
  const el = document.querySelector('#plainList .psent[data-sid="' + sid + '"]');
  if (el) plainToSeg(el);                   // 保留「点句子定位到逐字稿」老行为
});

// ---- 精简版：右键句子 → 结构选择菜单（捕获阶段，挡住段落右键菜单） ----
$('#plainList').addEventListener('contextmenu', (e) => {
  const s = e.target.closest('.psent');
  if (!s || !s.dataset.sid) return;
  e.preventDefault();
  e.stopImmediatePropagation();
  const sid = s.dataset.sid;
  if (P_SEL.indexOf(sid) < 0) { P_SEL = [sid]; P_ANCHOR = sid; paintSel(); }
  openSentMenu(e.clientX, e.clientY);
}, true);

// 选中工具条 + 视图切换
if ($('#btnSentStage')) {
  $('#btnSentStage').addEventListener('click', (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    openSentMenu(r.left, r.bottom + 6);
  });
}
if ($('#btnSentAuto')) {
  $('#btnSentAuto').addEventListener('click', () => cancelSentManual(false));
}
if ($('#btnSentClear')) {
  $('#btnSentClear').addEventListener('click', () => clearSel());
}
if ($('#btnSentSplit')) {
  $('#btnSentSplit').addEventListener('click', () => splitSentUI());
}
if ($('#btnSentMerge')) {
  $('#btnSentMerge').addEventListener('click', () => mergeSentUI());
}
if ($('#btnSentDel')) {
  $('#btnSentDel').addEventListener('click', () => deleteSentUI());
}
if ($('#btnPlainView')) {
  $('#btnPlainView').addEventListener('click', () => toggleView());
}

// 结构菜单点击
document.addEventListener('click', (e) => {
  const m = $('#sentMenu');
  if (!m || m.style.display === 'none' || !m.style.display) return;
  if (!m.contains(e.target)) {
    // 右键后会紧跟一个 click，且点选句子本身也不该关掉菜单
    if (Date.now() - SENT_OPEN_AT < 400) return;
    if (e.target.closest && e.target.closest('#plainList')) return;
    closeSentMenu();
    return;
  }
  const b = e.target.closest('.ctx-i');
  if (!b || b.disabled) return;
  const act = b.dataset.act;
  const stage = b.dataset.stage;
  closeSentMenu();
  if (act === 'clear') { clearSel(); return; }
  if (act === 'auto') { cancelSentManual(false); return; }
  if (act === 'auto-all') { cancelSentManual(true); return; }
  if (stage !== undefined) applySentStage(stage);
});
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if (P_EDITKEY) return;                    // 改字中先让 Esc 退出编辑
  closeSentMenu();
  if (P_SEL.length) clearSel();
});
document.addEventListener('keydown', (e) => {
  if (P_EDITKEY || !P_SEL.length) return;
  const tg = e.target;
  if (tg && (tg.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(tg.tagName || ''))) return;
  if (e.key === 'Delete') {
    e.preventDefault();
    deleteSentUI();
  }
});
window.addEventListener('resize', closeSentMenu);
$('#plainBox') && $('#plainBox').addEventListener('scroll', () => {
  if (!P_DRAG) closeSentMenu();
});
if ($('#btnCorrectAll')) {
  $('#btnCorrectAll').addEventListener('click', (e) => correctPlain(e.currentTarget));
}

// ---- 关于信息（面试点评器 / 慕夕制作） ----
(async () => {
  try {
    const r = await fetch('/api/appinfo');
    const info = await r.json();
    const box = $('#aboutData');
    if (box) box.textContent = '数据目录：' + (info.data_dir || '--');
  } catch (e) { /* 忽略 */ }
})();

// ---- 项目地址 & 检查 GitHub 更新（设置 → 关于） ----
const PROJECT_PAGE = 'https://github.com/lyckeven/InterviewLiveNote';

/* 打开外链：优先走后端（系统默认浏览器），后端拒绝时退回 window.open */
async function openExternal(url) {
  try {
    const r = await fetch('/api/open/url', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });
    const j = await r.json().catch(() => ({}));
    if (j && j.ok) return true;
    throw new Error((j && j.msg) || '打开失败');
  } catch (e) {
    try { window.open(url, '_blank', 'noopener'); return true; } catch (_) {
      toast('无法打开浏览器，请手动访问 ' + url);
      return false;
    }
  }
}

(function bindAboutUpdate() {
  const link = $('#linkProject');
  if (link) {
    link.addEventListener('click', (e) => {
      e.preventDefault();
      openExternal(PROJECT_PAGE);
    });
  }

  const btn = $('#btnCheckUpdate');
  const out = $('#updOut');
  if (!btn || !out) return;
  let busy = false;

  function show(html, cls) {
    out.hidden = false;
    out.className = 'tip upd-out' + (cls ? ' ' + cls : '');
    out.innerHTML = html;
  }

  function lnkGo(url, text) {
    const a = document.createElement('a');
    a.href = '#';
    a.className = 'lnk';
    a.textContent = text;
    a.addEventListener('click', (e) => { e.preventDefault(); openExternal(url); });
    return a;
  }

  btn.addEventListener('click', async () => {
    if (busy) return;
    busy = true;
    const old = btn.textContent;
    btn.disabled = true;
    btn.textContent = '检查中…';
    show('正在向 GitHub 查询最新版本…', '');
    try {
      const r = await fetch('/api/update/check', { cache: 'no-store' });
      const d = await r.json().catch(() => ({}));
      if (!d || d.ok !== true) {
        show('检查失败：' + esc((d && d.msg) || '网络不可用或接口异常'), 'bad');
      } else if (d.has_update) {
        const line = document.createElement('div');
        line.innerHTML = '发现新版本 <b>v' + esc(d.latest) + '</b>（当前 v' + esc(d.current) + '）';
        out.hidden = false;
        out.className = 'tip upd-out newer';
        out.innerHTML = '';
        out.appendChild(line);
        const go = document.createElement('div');
        go.appendChild(document.createTextNode('下载地址：'));
        go.appendChild(lnkGo(d.url || (PROJECT_PAGE + '/releases/latest'), '前往 GitHub Releases 下载'));
        out.appendChild(go);
        if (d.published_at) {
          const t = document.createElement('div');
          t.textContent = '发布时间：' + String(d.published_at).slice(0, 10);
          out.appendChild(t);
        }
      } else {
        show('已是最新版本（当前 v' + esc(d.current) + '，GitHub 最新 v' + esc(d.latest) + '）', '');
      }
      if (d && d.ok === true) {
        const foot = document.createElement('div');
        foot.appendChild(lnkGo(PROJECT_PAGE, '打开项目主页'));
        out.appendChild(foot);
      }
    } catch (e) {
      show('检查失败：' + esc(e && e.message ? e.message : String(e)), 'bad');
    } finally {
      btn.disabled = false;
      btn.textContent = old;
      busy = false;
    }
  });
})();


/* ==================================================================
   面板尺寸自由拖拽 + 本地记忆（任意尺寸不重叠、不溢出）
   ================================================================== */
const LAY_KEY = 'iln.layout.v3';
/* qAuto：题目块高度是否随内容自适应（用户没手动拖过上下分隔条时为 1）。
   之前固定 qH=340，装不下"考生/题干/题型/参考要点"全部字段，
   参考要点被裁在滚动区外，点击落到下方题库块，表现为输入框点不进去。 */
const LAY_DEF = { leftW: 300, rightW: 400, qH: 460, marksH: 140, plainH: 260, qAuto: 1 };
const LAY_MIN = { leftW: 240, rightW: 280, qH: 160, marksH: 72, plainH: 110,
                  centerW: 300, bankH: 130, reviewH: 110, transcriptH: 200 };
let LAY = Object.assign({}, LAY_DEF);
try {
  const saved = JSON.parse(localStorage.getItem(LAY_KEY) || '{}');
  Object.keys(LAY_DEF).forEach((k) => {
    if (typeof saved[k] === 'number' && isFinite(saved[k])) LAY[k] = saved[k];
  });
} catch (e) { /* 本地配置损坏则用默认值 */ }

function layClamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
function laySave() { try { localStorage.setItem(LAY_KEY, JSON.stringify(LAY)); } catch (e) {} }

function layApply() {
  const layout = $('#layout');
  if (!layout) return;
  const totalW = layout.clientWidth;
  const totalH = layout.clientHeight;
  if (!totalW || totalW < 120 || !totalH || totalH < 60) return;   // 完全隐藏（display:none）时不重算，避免把尺寸压坏
  const avail = totalW - 14;                                        // 减掉两条竖直分隔条
  /* 注意：下面只用局部变量做显示夹紧，绝不回写 LAY，
     这样窗口临时变小或最小化时不会把用户设定的尺寸永久压小 */
  let rw = layClamp(LAY.rightW, LAY_MIN.rightW, Math.max(LAY_MIN.rightW, avail - LAY_MIN.centerW - LAY_MIN.leftW));
  let lw = layClamp(LAY.leftW, LAY_MIN.leftW, Math.max(LAY_MIN.leftW, avail - LAY_MIN.centerW - rw));
  /* 视口过窄（缩窗 / 最小化 / 低分辨率）时按比例压缩两侧面板，
     保证三栏始终能同时放进视口，不产生横向滚动条与出界 */
  const centerNeed = Math.min(LAY_MIN.centerW, Math.max(40, Math.floor(avail * 0.34)));
  if (lw + rw + centerNeed > avail) {
    const room = Math.max(20, avail - centerNeed);
    const k = (lw + rw) > 0 ? room / (lw + rw) : 1;
    if (k < 1) { lw = Math.floor(lw * k); rw = Math.floor(rw * k); }
  }
  const L = $('#paneLeft'), R = $('#paneRight');
  L.style.flex = '0 0 ' + lw + 'px';
  L.style.width = lw + 'px';
  R.style.flex = '0 0 ' + rw + 'px';
  R.style.width = rw + 'px';

  /* 左栏上下：本题信息 / 题库 */
  const lh = L.clientHeight;
  if (lh > 80) {
    const bq = $('#blockQ');
    const maxQ = Math.max(30, lh - Math.min(LAY_MIN.bankH, 60) - 7);
    let qh;
    if (bq && LAY.qAuto) {
      /* 自适应：按题目块的真实内容高度分配，保证参考要点等末位字段不被裁到滚动区外。
         先临时置为 0 0 auto 量出"内容自然高度"（scrollHeight 会被当前 clientHeight 兜底，
         量不出缩小的需求），再恢复，避免残留样式。 */
      const prevFlex = bq.style.flex;
      bq.style.flex = '0 0 auto';
      const need = bq.offsetHeight + 2;
      bq.style.flex = prevFlex;
      qh = layClamp(need, Math.min(LAY_MIN.qH, Math.max(30, lh - 40)), maxQ);
    } else {
      qh = layClamp(Math.min(LAY.qH, lh - 40), Math.min(LAY_MIN.qH, Math.max(30, lh - 40)), maxQ);
    }
    if (bq) bq.style.flex = '0 0 ' + qh + 'px';
  }
  /* 右栏上下：标记 / 点评 */
  const rh = R.clientHeight;
  if (rh > 80) {
    const mh = layClamp(Math.min(LAY.marksH, rh - 40), Math.min(LAY_MIN.marksH, Math.max(30, rh - 40)),
                        Math.max(30, rh - Math.min(LAY_MIN.reviewH, 60) - 7));
    $('#blockMarks').style.flex = '0 0 ' + mh + 'px';
  }
  /* 中栏上下：逐字稿 / 精简版 */
  const ch = $('#paneCenter').clientHeight;
  const plain = $('#plainPane');
  if (plain) {
    if (plain.classList.contains('collapsed')) {
      plain.style.flex = '';
    } else if (ch > 40) {
      const headH = (document.querySelector('#paneCenter > .pane-head') || {}).offsetHeight || 0;
      const body = Math.max(20, ch - headH);
      const tMin = Math.min(LAY_MIN.transcriptH, Math.max(20, Math.floor(body * 0.3)));
      const pMin = Math.min(LAY_MIN.plainH, Math.max(20, Math.floor(body * 0.25)));
      const ph = layClamp(LAY.plainH, pMin, Math.max(pMin, ch - tMin - 7 - headH));
      plain.style.flex = '0 0 ' + ph + 'px';
    }
  }
}

let LAY_DRAG = null;
function layStart(e, kind, el) {
  if (e.button !== undefined && e.button !== 0) return;
  e.preventDefault();
  LAY_DRAG = { kind, x: e.clientX, y: e.clientY, base: Object.assign({}, LAY), moved: false, el, t0: Date.now(), pid: e.pointerId };
  el.classList.add('drag');
  document.body.classList.add('resizing');
  document.body.setAttribute('data-laydrag', '1');
  /* 抓住指针：鼠标拖到窗口外再松手时，pointerup 仍会回到这个元素，
     不会因为收不到 pointerup 让 body.resizing 永久残留（输入框点不动的元凶） */
  try { if (el.setPointerCapture && e.pointerId != null) el.setPointerCapture(e.pointerId); } catch (err) {}
}
function layMove(e) {
  if (!LAY_DRAG) return;
  const dx = e.clientX - LAY_DRAG.x;
  const dy = e.clientY - LAY_DRAG.y;
  if (Math.abs(dx) + Math.abs(dy) > 2) LAY_DRAG.moved = true;
  const b = LAY_DRAG.base;
  if (LAY_DRAG.kind === 'left') LAY.leftW = b.leftW + dx;
  else if (LAY_DRAG.kind === 'right') LAY.rightW = b.rightW - dx;
  else if (LAY_DRAG.kind === 'left-v') { LAY.qH = b.qH + dy; LAY.qAuto = 0; }
  else if (LAY_DRAG.kind === 'right-v') LAY.marksH = b.marksH + dy;
  else if (LAY_DRAG.kind === 'center') LAY.plainH = b.plainH - dy;
  layApply();
}
function layEnd() {
  /* 无论 LAY_DRAG 是否还在，先把全局拖拽状态清干净：
     否则 body.resizing 残留会让全站输入框彻底点不动 */
  document.body.classList.remove('resizing');
  document.body.removeAttribute('data-laydrag');
  const dragEls = document.querySelectorAll('.drag');
  for (let i = 0; i < dragEls.length; i++) dragEls[i].classList.remove('drag');
  if (!LAY_DRAG) return;
  try {
    if (LAY_DRAG.moved) {
      /* 用真实渲染尺寸回写：拖过头被夹紧的值自动纠正为实际值 */
      const L = $('#paneLeft'), R = $('#paneRight');
      const q = $('#blockQ'), mk = $('#blockMarks'), pl = $('#plainPane');
      if (L) LAY.leftW = L.offsetWidth;
      if (R) LAY.rightW = R.offsetWidth;
      if (q) LAY.qH = q.offsetHeight;
      if (mk) LAY.marksH = mk.offsetHeight;
      if (pl && !pl.classList.contains('collapsed')) LAY.plainH = pl.offsetHeight;
      layApply();
      laySave();
    }
  } finally {
    try {
      if (LAY_DRAG.el && LAY_DRAG.el.releasePointerCapture && LAY_DRAG.pid != null) {
        LAY_DRAG.el.releasePointerCapture(LAY_DRAG.pid);
      }
    } catch (err) {}
    LAY_DRAG = null;
  }
}
document.addEventListener('pointermove', layMove);
document.addEventListener('pointerup', layEnd);
document.addEventListener('pointercancel', layEnd);

$$('.vsplit,.hsplit').forEach((el) => {
  el.addEventListener('pointerdown', (e) => layStart(e, el.dataset.split, el));
  el.addEventListener('dblclick', () => {
    const k = el.dataset.split;
    if (k === 'left') LAY.leftW = LAY_DEF.leftW;
    else if (k === 'right') LAY.rightW = LAY_DEF.rightW;
    else if (k === 'left-v') { LAY.qH = LAY_DEF.qH; LAY.qAuto = LAY_DEF.qAuto; }
    else if (k === 'right-v') LAY.marksH = LAY_DEF.marksH;
    else if (k === 'center') LAY.plainH = LAY_DEF.plainH;
    layApply(); laySave();
    toast('已恢复该分隔条默认尺寸');
  });
});
window.addEventListener('resize', () => layApply());
document.addEventListener('visibilitychange', () => { if (!document.hidden) layApply(); });

/* 输入框可点性兜底：任何输入框获得焦点时，若它被所在滚动容器裁在可视区外
   （容器太高时题目块/弹窗正文/题库列表都会发生），立刻滚到最近位置。
   这样即使面板被人为拖得很矮，Tab 或脚本聚焦后也不会停在"看不见的框"上。 */
document.addEventListener('focusin', (e) => {
  const t = e.target;
  if (!t || !(t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT')) return;
  if (t.type === 'file' || t.type === 'hidden') return;
  const box = t.closest('.qblock,.modal-body,.plainbox,.bank-list,.qta-right,.qta-list,.qa-body');
  if (!box || box.scrollHeight <= box.clientHeight + 1) return;
  const br = box.getBoundingClientRect();
  const tr = t.getBoundingClientRect();
  if (tr.top < br.top || tr.bottom > br.bottom) {
    try { t.scrollIntoView({ block: 'nearest' }); } catch (err) {}
  }
});
/* 精简版收起 / 展开后重算中栏上下分配 */
$('#btnTogglePlain').addEventListener('click', () => { layApply(); setTimeout(layApply, 60); });

/* ==================================================================
   题目粘贴 -> 自动解析（题型 / 审题 / 结构 / 要点） -> 一键入库
   ================================================================== */
/* 通用回退环节：仅在题型未设置答题环节时使用（题型环节来自 /api/qtypes 与题型结构管理） */
const QSTAGES = ['审题', '提观点', '重分析', '抓落实', '结尾'];
let QAN = null;            // 当前解析结果
let QA_REF_DIRTY = false;  // 用户是否手动改过参考要点

/* ---------------- 题库行「编辑」=> 复用题目解析弹层（覆盖本题） ----------------
   题库行点 ✎ 以前打开的是简陋的 #modalItemEdit（只有题干 / 题型 / 参考要点）。
   现在改为复用 #modalQAnalyze —— 它本身就是完整的题目编辑器（题干 / 题型 /
   审题要点 / 答题结构各环节 / 关键要点 / 参考要点），把题库条目的全部字段预填
   进去，保存时调 /api/bank/item/update 覆盖原题。
   进入覆盖模式：隐藏题库选择列、主按钮文案改「保存并覆盖本题」；
   关闭或保存后必须复位回普通「一键写入题库」模式，保证题目区原来的
   「查看/编辑 → 一键写入题库」流程完全不受影响。 */
let QA_OVR = null;   // null = 普通写入题库；{file,index,qno,question,qtype_reason} = 覆盖本题

function qaIsOverwrite() { return !!(QA_OVR && QA_OVR.file); }

function qaSetMode(ovr) {
  QA_OVR = ovr || null;
  const on = qaIsOverwrite();
  /* 题库选择列：覆盖模式下这题已归属当前题库，不需要也不允许再选 */
  const col = $('#qaBankCol');
  if (col) col.classList.toggle('hidden', on);
  const sel = $('#qaBankSel');
  if (sel) sel.disabled = on;
  const inp = $('#qaBankNew');
  if (inp) { inp.disabled = on; if (on) inp.style.display = 'none'; }
  const btn = $('#btnQaSave');
  if (btn) btn.textContent = on ? '保存并覆盖本题' : '一键写入题库';
  const hint = $('#qaHint');
  if (hint) {
    hint.textContent = on
      ? '当前为「覆盖本题」：保存后会用上面的内容覆盖题库中的这一题（题号与位置不变），并刷新题库列表。'
      : '确认无误后点「一键写入题库」，题干与参考要点会一起存进题库，并同步为当前题目，可直接开始答题与点评。';
  }
}

/* 从题库列表某一行打开编辑（覆盖本题模式） */
function qaOpenForBankItem(i) {
  const it = BANK_ITEMS[i];
  if (!it || !CUR_BANK) return;
  openModal('#modalQAnalyze');
  qaSetMode({
    file: CUR_BANK, index: i, qno: it.qno,
    question: it.question || '', qtype_reason: it.qtype_reason || '',
  });
  qaFillForm({
    question: it.question || '',
    qtype: it.qtype || '',
    qtype_reason: it.qtype_reason || '',
    exam: it.exam || [],
    structure: it.structure || [],
    keys: it.keys || [],
    ref: it.ref || '',
    source: 'edited',
  });
  qaFillDatalist();
  setTimeout(function () {
    const q = $('#qaQuestion');
    if (q) {
      q.focus();
      try { q.setSelectionRange(0, 0); q.scrollTop = 0; } catch (e) { /* 忽略 */ }
    }
  }, 60);
  toast('正在编辑第 ' + (i + 1) + ' 题，保存将覆盖题库中的这一题');
}

function qEsc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function qDefaultBank() {
  if (CUR_BANK) return String(CUR_BANK).replace(/\.json$/i, '');
  return '粘贴题目';
}
/* 入库目标题库：下拉选已有题库，或选「＋ 新建题库」后填名称（真实写进 BANK_DIR） */
function qaFillDatalist() {
  const sel = $('#qaBankSel');
  if (!sel) return;
  api('/api/bank').then((r) => {
    const banks = r.banks || [];
    const keep = sel.value;
    /* value 用文件名（唯一），label 用题库显示名，避免同名题库保存时目标错位 */
    sel.innerHTML = banks.map((b) =>
      '<option value="' + qEsc(b.file) + '">' + qEsc(b.name) + '（' + b.count + ' 题）</option>').join('')
      + '<option value="__new__">＋ 新建题库…</option>';
    const want = keep && keep !== '__new__' ? keep
      : (CUR_BANK ? String(CUR_BANK) : (banks[0] ? banks[0].file : '__new__'));
    if (banks.some((b) => b.file === want)) sel.value = want;
    else if (!banks.length) sel.value = '__new__';
    qaBankMode();
  }).catch(() => {});
}
/* 选「新建题库」时才显示名称输入框；「覆盖本题」模式下这题已归属当前题库，永远不显示 */
function qaBankMode() {
  const sel = $('#qaBankSel');
  const inp = $('#qaBankNew');
  if (!sel || !inp) return;
  const isNew = sel.value === '__new__';
  inp.style.display = (isNew && !qaIsOverwrite()) ? '' : 'none';
  if (isNew && !inp.value.trim()) inp.value = '';
}
function qaBankTarget() {
  const sel = $('#qaBankSel');
  const inp = $('#qaBankNew');
  const v = sel ? sel.value : '';
  if (v === '__new__') {
    const name = ((inp && inp.value) || '').trim();
    return name ? { new_bank: name } : { err: '请填写新题库名称' };
  }
  if (!v) return { err: '请选择要保存到的题库' };
  return { bank: String(v).replace(/\.json$/i, '') };
}

/* 左侧卡片 */
function qaShowCard(a) {
  QAN = (a && a.qtype) ? a : null;
  const card = $('#qAnalyCard');
  if (!card) return;
  if (!QAN) { card.style.display = 'none'; return; }
  card.style.display = 'flex';
  $('#qaType').textContent = QAN.qtype || '未判定';
  const srcMap = { ai: 'AI 解析', local: '本地规则解析', edited: '已手动确认' };
  let src = srcMap[QAN.source] || '本地规则解析';
  if (QAN.ai_error) src += '（AI 未生效已回退）';
  $('#qaSrc').textContent = src;
  const stages = QAN.structure || [];
  $('#qaStages').innerHTML = stages.map((s) => {
    const kk0 = stageK(s.stage);
    const k = kk0 ? ' k-' + kk0 : '';
    const tip = (s.points || []).join(' / ');
    return '<span class="qa-chip' + k + '" title="' + qEsc(tip) + '">' +
      qEsc((s.stage || '') + (s.label ? '·' + s.label : '')) + '</span>';
  }).join('') || '<span class="qa-chip">无环节</span>';
  if (QAN.qtype) syncQtypeTo(QAN.qtype, true);
  if (QAN.ref && !$('#ref').value.trim()) $('#ref').value = QAN.ref;
  /* 解析卡片的显隐会改变题目块内容高度，同步重算左栏上下分配，
     否则卡片一出现就把参考要点挤出可视区（表现为点不进去）。 */
  if (typeof layApply === 'function') { layApply(); setTimeout(layApply, 60); }
}

/* 从题库条目恢复解析结果（点选题库题目时联动：卡片、题型、结构一并还原） */
function qaFillFromItem(it) {
  it = it || {};
  const hasAny = !!it.qtype || (it.exam && it.exam.length) || (it.structure && it.structure.length);
  if (!hasAny) { qaShowCard(null); return; }
  const a = {
    question: it.question || '', qtype: it.qtype || '',
    qtype_reason: it.qtype_reason || '',
    exam: it.exam || [], structure: it.structure || [], keys: it.keys || [],
    ref: it.ref || '', source: 'edited'
  };
  QAN = a;
  qaShowCard(a);
  try {
    fetch('/api/session/info', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ q_analysis: a })
    }).catch(() => {});
  } catch (e) { /* 忽略 */ }
}

/* 当前题型的环节池（题型未设环节时回退通用五环节） */
function qaStagePool() {
  try {
    const t = qtStages($('#qaQtype') ? $('#qaQtype').value : ($('#qtype') ? $('#qtype').value : ''));
    if (t.length) return t;
  } catch (e) { /* 忽略 */ }
  return QSTAGES.slice();
}

/* 弹层里的结构行：可选项以「当前题型」的答题环节为准，题型未设环节时回退通用五环节 */
function qaStageOptions(cur) {
  let list = QSTAGES.slice();
  try {
    const t = qtStages($('#qaQtype') ? $('#qaQtype').value : ($('#qtype') ? $('#qtype').value : ''));
    if (t.length) list = t.slice();
  } catch (e) { /* 题型清单未就绪时用通用环节 */ }
  if (cur && list.indexOf(cur) < 0) list.push(cur);
  return list.map((s) => '<option value="' + qEsc(s) + '"' + (s === cur ? ' selected' : '') + '>' + qEsc(s) + '</option>').join('');
}
function qaStructHTML(stages) {
  const arr = stages || [];
  if (!arr.length) return '<div class="muted">暂无环节，点右上「+ 增加环节」或「重新分析」</div>';
  return arr.map((s, i) => (
    '<div class="qa-stage" data-i="' + i + '">' +
      '<div class="qa-srow">' +
        '<span class="qa-idx">' + (i + 1) + '</span>' +
        '<select class="inp qa-sstage" data-f="stage">' + qaStageOptions(s.stage) + '</select>' +
        '<input class="inp qa-slabel" data-f="label" value="' + qEsc(s.label || '') + '" placeholder="环节小标题，如 亮明观点" />' +
        '<button class="mini qa-sdel" data-del="' + i + '" title="删除该环节">删除</button>' +
      '</div>' +
      '<textarea class="inp ta qa-spoints" data-f="points" rows="2" placeholder="本环节要点，每行一条">' + qEsc((s.points || []).join('\n')) + '</textarea>' +
    '</div>'
  )).join('');
}
function qaFillForm(a) {
  const d = a || {};
  setVal($('#qaQuestion'), d.question || $('#question').value || '', true);
  setVal($('#qaQtype'), '', true);
  fillQtypeSelect($('#qaQtype'), d.qtype || '');
  $('#qaReason').textContent = d.qtype_reason || '';
  setVal($('#qaExam'), (d.exam || []).join('\n'), true);
  $('#qaStruct').innerHTML = qaStructHTML(d.structure || []);
  setVal($('#qaKeys'), (d.keys || []).join('\n'), true);
  setVal($('#qaRef'), d.ref || '', true);
  QA_REF_DIRTY = false;
  if ($('#qaBankSel') && !$('#qaBankSel').options.length) qaFillDatalist();
}
function qaLines(sel) {
  return $(sel).value.split('\n').map((s) => s.trim()).filter(Boolean);
}
function qaCollect() {
  const struct = $$('#qaStruct .qa-stage').map((row) => {
    const st = row.querySelector('[data-f="stage"]');
    const lb = row.querySelector('[data-f="label"]');
    const pt = row.querySelector('[data-f="points"]');
    return {
      stage: st ? st.value : '',
      label: lb ? lb.value.trim() : '',
      points: pt ? pt.value.split('\n').map((s) => s.trim()).filter(Boolean) : [],
    };
  }).filter((s) => s.stage || s.label || s.points.length);
  return {
    question: $('#qaQuestion').value.trim(),
    qtype: $('#qaQtype').value.trim(),
    /* 覆盖本题时弹层里显示的是该题自己的题型理由（QAN 可能是别的题的解析结果，
       绝不能拿来覆盖），所以覆盖模式优先取弹层当前展示值 */
    qtype_reason: qaIsOverwrite()
      ? ((($('#qaReason') && $('#qaReason').textContent) || '').trim() || (QA_OVR && QA_OVR.qtype_reason) || '')
      : ((QAN && QAN.qtype_reason) || ''),
    exam: qaLines('#qaExam'),
    structure: struct,
    keys: qaLines('#qaKeys'),
    ref: QA_REF_DIRTY ? $('#qaRef').value.trim() : '',
    source: 'edited',
  };
}

/* 调后端解析 */
async function qaAnalyze(text, btn) {
  const t = (text || '').trim();
  if (t.length < 4) { toast('先粘贴完整题目原文', true); return; }
  const label = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = '分析中…'; }
  /* 立即给出「分析中」反馈：AI 解析通常数秒到数十秒，不能让界面长时间无响应 */
  const card0 = $('#qAnalyCard');
  if (card0 && !QAN) {
    card0.style.display = 'flex';
    $('#qaType').textContent = '分析中…';
    $('#qaSrc').textContent = '请稍候';
    $('#qaStages').innerHTML = '<span class="qa-chip">正在解析题型与答题结构</span>';
  } else if (card0) {
    $('#qaSrc').textContent = '重新解析中…';
  }
  try {
    const r = await api('/api/question/analyze', { text: t });
    qaShowCard(r.analysis);
    if ($('#modalQAnalyze').classList.contains('open')) qaFillForm(r.analysis);
    const n = (r.analysis.structure || []).length;
    toast('已解析：' + r.analysis.qtype + ' · ' + n + ' 个环节' + (r.analysis.ai_error ? '（AI 未生效已回退本地）' : ''));
  } catch (e) {
    toast('解析失败：' + e.message, true);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = label; }
  }
}

/* 写入题库（useModal=true 取弹层里编辑后的内容） */
async function qaSave(useModal, btn) {
  /* 「覆盖本题」模式：只走覆盖分支，不再选择目标题库 */
  if (qaIsOverwrite()) { return qaSaveOverwrite(btn); }
  const payload = useModal ? qaCollect() : Object.assign({}, QAN || {});
  if (!payload || !payload.question || payload.question.length < 4) { toast('题干太短，先粘贴完整题目', true); return; }
  if (!(payload.structure || []).length) { toast('答题结构为空，先「重新分析」或手动加环节', true); return; }
  const target = qaBankTarget();
  if (target.err) { toast(target.err, true); if (target.err.indexOf('名称') >= 0 && $('#qaBankNew')) $('#qaBankNew').focus(); return; }
  const label = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = '写入中…'; }
  try {
    let ref = payload.ref || '';
    if (!ref) {
      const r1 = await api('/api/question/save', { analysis: Object.assign({}, payload, { ref: '' }) });
      ref = (r1.session && r1.session.ref) || '';
      if ($('#qaRef')) $('#qaRef').value = ref;
      QA_REF_DIRTY = false;
    }
    const r2 = await api('/api/bank/add_item', Object.assign({}, target, { item: Object.assign({}, payload, { ref }) }));
    setVal($('#question'), payload.question, true);
    if (payload.qtype) syncQtypeTo(payload.qtype, true);
    setVal($('#ref'), ref, true);
    const merged = Object.assign({}, payload, { ref, source: 'edited' });
    delete merged.answer;
    qaShowCard(merged);
    saveInfo();
    toast((r2.created ? '已新建题库「' : '已写入题库「') + r2.bank + '」，该库共 ' + r2.count + ' 题');
    if ($('#modalQAnalyze').classList.contains('open')) closeModal($('#modalQAnalyze'));
    loadBanks();
  } catch (e) {
    toast('入库失败：' + e.message, true);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = label; }
  }
}

/* 覆盖本题：把弹层内容整题写回题库（题干 / 题型 / 题型理由 / 审题要点 /
   答题结构各环节 / 关键要点 / 参考要点），题号与位置不变 */
async function qaSaveOverwrite(btn) {
  const ovr = QA_OVR ? Object.assign({}, QA_OVR) : null;
  if (!ovr || !ovr.file) return;
  const payload = qaCollect();
  if (!payload.question || payload.question.length < 4) { toast('题干太短，先粘贴完整题目', true); return; }
  const old = BANK_ITEMS[ovr.index] || {};
  /* 结构为空时按当前题型的环节池补齐；仍为空就沿用原题结构，
     绝不把原题的答题结构覆盖成空 */
  let structure = payload.structure || [];
  if (!structure.length) {
    structure = qtStages(payload.qtype || '').map(function (nm) { return { stage: nm, label: '', points: [] }; });
  }
  if (!structure.length) structure = (old.structure || []).slice();
  /* 参考要点：qaCollect 在「没手动改过」时返回空串，直接提交会把原题参考要点清空。
     这里回落到弹层当前展示的内容（qaFillForm 已填入原题 ref），
     用户主动清空时弹层也是空的，仍然按清空处理。 */
  let ref = payload.ref || '';
  if (!ref && $('#qaRef')) ref = ($('#qaRef').value || '').trim();
  const label = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = '保存中…'; }
  try {
    const body = {
      file: ovr.file, index: ovr.index,
      question: payload.question,
      qtype: payload.qtype || '',
      qtype_reason: payload.qtype_reason || '',
      exam: payload.exam || [],
      structure: structure,
      keys: payload.keys || [],
      ref: ref,
    };
    if (!structure.length) delete body.structure;   /* 结构确实为空：不动原题结构 */
    await api('/api/bank/item/update', body);
    /* 先关弹层（closeModal 内复位覆盖模式），再刷新题库列表 */
    closeModal($('#modalQAnalyze'));
    if (CUR_BANK === ovr.file) await showBankItems(ovr.file);
    await loadBanks();
    toast('已覆盖第 ' + (ovr.index + 1) + ' 题' + (body.qtype ? ' · 题型：' + body.qtype : ''));
    /* 题目区当前正是这道题时同步过去（与旧版单题编辑保存行为一致） */
    const curQ = ($('#question').value || '').trim();
    if (curQ && curQ === String(old.question || '').trim()) {
      setVal($('#question'), body.question, true);
      setVal($('#ref'), body.ref, true);
      syncQtypeTo(body.qtype, true);
      qaFillFromItem(Object.assign({}, body));
      saveInfo();
    }
  } catch (e) {
    toast('覆盖保存失败：' + e.message, true);
  } finally {
    if (btn) {
      btn.disabled = false;
      /* 已复位成普通模式时文案必须回到「一键写入题库」，不能留在覆盖文案上 */
      btn.textContent = qaIsOverwrite() ? (label || '保存并覆盖本题') : '一键写入题库';
    }
  }
}

/* 按当前编辑内容重建参考要点 */
async function qaRebuildRef(btn) {
  const payload = qaCollect();
  if (!payload.question) { toast('题干为空', true); return; }
  const label = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = '重建中…'; }
  try {
    const r = await api('/api/question/save', { analysis: Object.assign({}, payload, { ref: '' }) });
    setVal($('#qaRef'), (r.session && r.session.ref) || '', true);
    QA_REF_DIRTY = false;
    toast('参考要点已按当前编辑内容重建');
  } catch (e) {
    toast('重建失败：' + e.message, true);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = label; }
  }
}

/* 交互绑定 */
$('#btnAnalyzeQ').addEventListener('click', (e) => {
  const t = $('#question').value.trim();
  if (t.length < 4) {
    qaFillForm(QAN || { question: '', qtype: $('#qtype').value.trim() });
    openModal('#modalQAnalyze');
    qaFillDatalist();
    $('#qaQuestion').focus();
    toast('把题目原文粘贴到弹层里，再点「重新分析」');
    return;
  }
  qaAnalyze(t, e.currentTarget);
});
/* 题框里直接粘贴 => 自动分析 */
$('#question').addEventListener('input', () => {
  if (!$('#question').value.trim()) { qaShowCard(null); }
});
$('#question').addEventListener('paste', () => {
  setTimeout(() => { if ($('#question').value.trim().length >= 6) qaAnalyze($('#question').value, null); }, 160);
});
$('#btnQaOpen').addEventListener('click', () => {
  qaFillForm(QAN || { question: $('#question').value.trim(), qtype: $('#qtype').value.trim() });
  openModal('#modalQAnalyze');
  qaFillDatalist();
});
$('#btnQaBank').addEventListener('click', (e) => qaSave(false, e.currentTarget));
$('#btnQaSave').addEventListener('click', (e) => qaSave(true, e.currentTarget));
$('#btnQaRebuild').addEventListener('click', (e) => qaRebuildRef(e.currentTarget));
$('#btnQaReanalyze').addEventListener('click', (e) => qaAnalyze($('#qaQuestion').value, e.currentTarget));
$('#btnQaAddStage').addEventListener('click', () => {
  const stages = $$('#qaStruct .qa-stage').map((row) => ({
    stage: row.querySelector('[data-f="stage"]').value,
    label: row.querySelector('[data-f="label"]').value.trim(),
    points: row.querySelector('[data-f="points"]').value.split('\n').map((s) => s.trim()).filter(Boolean),
  }));
  const pool = qaStagePool();
  stages.push({ stage: pool[stages.length % pool.length] || '提观点', label: '', points: [] });
  $('#qaStruct').innerHTML = qaStructHTML(stages);
});
$('#qaStruct').addEventListener('click', (e) => {
  const del = e.target.closest('[data-del]');
  if (!del) return;
  const stages = [];
  $$('#qaStruct .qa-stage').forEach((row) => {
    if (row === del.closest('.qa-stage')) return;
    stages.push({
      stage: row.querySelector('[data-f="stage"]').value,
      label: row.querySelector('[data-f="label"]').value.trim(),
      points: row.querySelector('[data-f="points"]').value.split('\n').map((s) => s.trim()).filter(Boolean),
    });
  });
  $('#qaStruct').innerHTML = qaStructHTML(stages);
});
$('#qaRef').addEventListener('input', () => { QA_REF_DIRTY = true; });

/* ==================================================================
   设置 · 题型结构管理
   题型名 / 适用场景 / 特征关键词 / 答题环节（可增删改、上下调序）
   / 点评要素（检查点 + 常见失分表现），保存后立即生效
   ================================================================== */
let QTA_LIST = [];      // 题型清单 [{id,name,aka,stages,custom}]
let QTA_CUR = '';       // 当前编辑的题型原始名（保存时作为 orig）
let QTA_DETAIL = null;  // 当前题型的完整详情

function qtaMsg(t, bad) {
  const el = $('#qtaMsg');
  if (!el) return;
  el.textContent = t || '';
  el.style.color = bad ? 'var(--bad)' : 'var(--accent2)';
}

function qtaSetTab(tab) {
  const q = (tab === 'qtype');
  $$('.set-tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === (q ? 'qtype' : 'basic')));
  const basic = $('#setPaneBasic');
  const pane = $('#setPaneQtype');
  if (basic) basic.classList.toggle('hidden', q);
  if (pane) pane.classList.toggle('hidden', !q);
  const box = $('#modalSettings .modal-box');
  if (box) box.classList.toggle('xwide', q);
  if (q) qtaInit();
}

let QTA_READY = false;
async function qtaInit(force) {
  if (QTA_READY && !force) return;
  try {
    await qtaLoadList();
    QTA_READY = true;
    const first = QTA_CUR || (QTA_LIST[0] && QTA_LIST[0].name) || '';
    if (first) await qtaOpen(first);
  } catch (e) { qtaMsg(e.message || '题型加载失败', true); }
}

async function qtaLoadList() {
  const r = await api('/api/qtypes');
  QTA_LIST = r.types || [];
  qtaRenderList();
  const c = $('#qtaCount');
  if (c) c.textContent = QTA_LIST.length;
}

function qtaRenderList() {
  const box = $('#qtaList');
  if (!box) return;
  const kw = ($('#qtaFilter') ? $('#qtaFilter').value : '').trim();
  const list = QTA_LIST.filter((t) => {
    if (!kw) return true;
    const hay = [t.name].concat(t.aka || []).join(' ');
    return hay.indexOf(kw) >= 0;
  });
  if (!list.length) {
    box.innerHTML = '<div class="muted" style="padding:12px 8px">没有匹配的题型</div>';
    return;
  }
  box.innerHTML = list.map((t) => {
    const on = (t.name === QTA_CUR) ? ' on' : '';
    const flag = t.custom
      ? '<span class="qta-flag custom">自定义</span>'
      : '<span class="qta-flag builtin">内置</span>';
    const tip = (t.stages || []).join(' / ') || '未设置答题环节';
    return '<button class="qta-item' + on + '" data-qtype="' + qEsc(t.name) + '" title="' + qEsc(tip) + '">' +
      '<span class="qta-nm">' + qEsc(t.name) + '</span>' + flag + '</button>';
  }).join('');
}

function qtaStageRow(st, i) {
  const nm = st.name || '';
  return '<div class="qta-st" data-i="' + i + '">' +
      '<div class="qta-sh">' +
        '<span class="qta-no">' + (i + 1) + '</span>' +
        '<input class="inp qta-sname" data-f="name" maxlength="20" value="' + qEsc(nm) + '" placeholder="环节名称，如 前期准备" />' +
        '<button class="mini qta-up" data-act="up" title="上移">↑</button>' +
        '<button class="mini qta-down" data-act="down" title="下移">↓</button>' +
        '<button class="mini qta-del" data-act="del" title="删除该环节">删</button>' +
      '</div>' +
      '<div class="qta-sdet">' +
        '<input class="inp qta-swhat" data-f="what" value="' + qEsc(st.what || '') + '" placeholder="该环节讲什么（一句话说明）" />' +
        '<input class="inp qta-scri" data-f="criteria" value="' + qEsc(st.criteria || '') + '" placeholder="判断依据（逐字稿落到该环节的信号）" />' +
        '<textarea class="inp ta qta-spoints" data-f="points" rows="2" placeholder="常见要点，每行一条">' + qEsc((st.points || []).join('\n')) + '</textarea>' +
      '</div>' +
    '</div>';
}

function qtaRenderStages(stages) {
  const box = $('#qtaStages');
  if (!box) return;
  const arr = stages || [];
  box.innerHTML = arr.length ? arr.map(qtaStageRow).join('')
    : '<div class="muted" style="padding:10px">还没有答题环节，点右上「＋加环节」添加</div>';
}

function qtaStagesFromDOM() {
  const rows = $$('#qtaStages .qta-st');
  return rows.map((row) => {
    const g = (f) => { const el = row.querySelector('[data-f="' + f + '"]'); return el ? el.value : ''; };
    return {
      name: g('name').trim(),
      what: g('what').trim(),
      criteria: g('criteria').trim(),
      points: g('points').split('\n').map((x) => x.trim()).filter(Boolean),
    };
  });
}

async function qtaOpen(name, focusNew) {
  const nm = (name || '').trim();
  if (!nm) return;
  try {
    const r = await api('/api/qtypes/detail?name=' + encodeURIComponent(nm));
    const d = r.type || {};
    QTA_CUR = d.name || nm;
    QTA_DETAIL = d;
    $('#qtaEmpty').classList.add('hidden');
    $('#qtaForm').classList.remove('hidden');
    setVal($('#qtaName'), d.name || '', true);
    setVal($('#qtaAka'), (d.aka || []).join(','), true);
    setVal($('#qtaScene'), d.scene || '', true);
    setVal($('#qtaKeywords'), (d.keywords || []).join(','), true);
    qtaRenderStages(d.stages || []);
    setVal($('#qtaCheckpoints'), ((d.review || {}).checkpoints || []).join('\n'), true);
    setVal($('#qtaFaults'), ((d.review || {}).faults || []).join('\n'), true);
    $('#btnQtaReset').disabled = !d.builtin;
    $('#btnQtaReset').title = d.builtin ? '把该题型恢复为随包内置的默认结构'
      : '自定义题型没有内置默认可恢复';
    qtaMsg('');
    qtaRenderList();
    if (focusNew) setTimeout(() => { const n = $('#qtaName'); if (n) { n.focus(); n.select(); } }, 60);
  } catch (e) { qtaMsg(e.message || '读取题型失败', true); }
}

function qtaCollect() {
  return {
    orig: QTA_CUR,
    type: {
      name: ($('#qtaName').value || '').trim(),
      aka: ($('#qtaAka').value || '').split(/[,，、;；\/]+/).map((x) => x.trim()).filter(Boolean),
      scene: ($('#qtaScene').value || '').trim(),
      keywords: ($('#qtaKeywords').value || '').split(/[,，、;；\/]+/).map((x) => x.trim()).filter(Boolean),
      stages: qtaStagesFromDOM(),
      review: {
        checkpoints: ($('#qtaCheckpoints').value || '').split('\n').map((x) => x.trim()).filter(Boolean),
        faults: ($('#qtaFaults').value || '').split('\n').map((x) => x.trim()).filter(Boolean),
      },
    },
  };
}

/* 保存后同步：下拉列表 + 当前会话题型环节 + 精简版重绘 */
async function qtaAfterChange(name) {
  await loadQtypes(true);
  refreshQtypeSelects();
  const cur = ($('#qtype') ? $('#qtype').value : '').trim();
  if (name && (!cur || cur === name || cur === QTA_CUR)) {
    syncQtypeTo(name, true);
  } else {
    await ensureStageOpts(true);
  }
  if (typeof P_VIEW !== 'undefined' && P_VIEW === 'stage') renderPlain(false);
}

async function qtaNew() {
  let name = '新题型', n = 2;
  const exist = new Set(QTA_LIST.map((t) => t.name));
  while (exist.has(name)) { name = '新题型' + n; n++; }
  try {
    const r = await api('/api/qtypes/add', { name });
    await qtaLoadList();
    await qtaOpen(r.type.name, true);
    await qtaAfterChange('');
    qtaMsg('已新建题型「' + r.type.name + '」，改好名称与结构后点「保存并生效」');
  } catch (e) { qtaMsg(e.message || '新建题型失败', true); }
}

async function qtaSave() {
  const body = qtaCollect();
  if (!body.type.name) { qtaMsg('题型名称不能为空', true); return; }
  if (!body.type.stages.filter((x) => (x.name || '').trim()).length) { qtaMsg('至少要有一个填了名称的答题环节', true); return; }
  try {
    const r = await api('/api/qtypes/save', body);
    QTA_CUR = r.type.name;
    QTA_DETAIL = r.type;
    await qtaLoadList();
    await qtaOpen(QTA_CUR);
    await qtaAfterChange(QTA_CUR);
    qtaMsg('已保存并生效：' + QTA_CUR + ' · ' + (r.type.stages || []).map((s) => s.name).join(' / '));
    toast('题型「' + QTA_CUR + '」已保存并生效');
  } catch (e) { qtaMsg(e.message || '保存失败', true); }
}

async function qtaCopy() {
  if (!QTA_CUR) return;
  try {
    const r = await api('/api/qtypes/copy', { name: QTA_CUR });
    await qtaLoadList();
    await qtaOpen(r.type.name, true);
    await qtaAfterChange('');
    qtaMsg('已复制为「' + r.type.name + '」，可直接改结构后保存');
  } catch (e) { qtaMsg(e.message || '复制失败', true); }
}

async function qtaDelete() {
  if (!QTA_CUR) return;
  if (!window.confirm('确定删除题型「' + QTA_CUR + '」？\n删除后它会从题型下拉里消失；内置题型可点「恢复内置默认」找回。')) return;
  try {
    const r = await api('/api/qtypes/delete', { name: QTA_CUR });
    QTA_CUR = '';
    QTA_DETAIL = null;
    await qtaLoadList();
    const next = (QTA_LIST[0] && QTA_LIST[0].name) || '';
    if (next) await qtaOpen(next);
    else {
      $('#qtaForm').classList.add('hidden');
      $('#qtaEmpty').classList.remove('hidden');
    }
    await qtaAfterChange('');
    qtaMsg('已删除题型「' + (r.name || '') + '」');
  } catch (e) { qtaMsg(e.message || '删除失败', true); }
}

async function qtaReset() {
  if (!QTA_CUR) return;
  if (!window.confirm('把「' + QTA_CUR + '」恢复成内置默认结构？当前改动会被覆盖。')) return;
  try {
    const r = await api('/api/qtypes/reset', { name: QTA_CUR });
    await qtaLoadList();
    await qtaOpen(QTA_CUR);
    await qtaAfterChange(QTA_CUR);
    qtaMsg('已恢复内置默认结构');
  } catch (e) { qtaMsg(e.message || '恢复失败', true); }
}

/* 首屏应用已保存的尺寸（等布局稳定后再校准一次） */
layApply();
setTimeout(layApply, 80);
setTimeout(layApply, 400);

/* ================= 外观主题（新拟物 浅色 / 深色） ================= */
const THEME_KEY = 'iln_theme';
function currentTheme(){
  try{ return localStorage.getItem(THEME_KEY) || 'auto'; }catch(e){ return 'auto'; }
}
function applyTheme(t){
  const v = (t === 'light' || t === 'dark') ? t : 'auto';
  document.documentElement.setAttribute('data-theme', v);
  try{ localStorage.setItem(THEME_KEY, v); }catch(e){}
  if (window.__ILN_DESKTOP && window.pywebview && window.pywebview.api && window.pywebview.api.win_theme){
    try{ window.pywebview.api.win_theme(v); }catch(e){}
  }
}
window.__ilnApplyTheme = applyTheme;
applyTheme(currentTheme());
if (document.getElementById('cfgTheme')){
  document.getElementById('cfgTheme').addEventListener('change', (e) => {
    applyTheme(e.target.value);
  });
}

/* ================= 题型结构管理：事件绑定 ================= */
(function bindQtypeAdmin() {
  const tabs = $$('.set-tab');
  tabs.forEach((b) => b.addEventListener('click', () => qtaSetTab(b.dataset.tab)));

  const list = $('#qtaList');
  if (list) {
    list.addEventListener('click', (e) => {
      const it = e.target.closest('[data-qtype]');
      if (it) qtaOpen(it.dataset.qtype);
    });
  }
  const f = $('#qtaFilter');
  if (f) f.addEventListener('input', () => qtaRenderList());

  const addStage = $('#btnQtaAddStage');
  if (addStage) {
    addStage.addEventListener('click', () => {
      const arr = qtaStagesFromDOM();
      if (arr.length >= 12) { qtaMsg('答题环节最多 12 个', true); return; }
      arr.push({ name: '', what: '', criteria: '', points: [] });
      qtaRenderStages(arr);
      const rows = $$('#qtaStages .qta-st');
      const last = rows[rows.length - 1];
      if (last) { const n = last.querySelector('[data-f="name"]'); if (n) n.focus(); }
    });
  }

  const stagesBox = $('#qtaStages');
  if (stagesBox) {
    stagesBox.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-act]');
      if (!btn) return;
      const row = btn.closest('.qta-st');
      if (!row) return;
      const i = Number(row.dataset.i);
      const arr = qtaStagesFromDOM();
      const act = btn.dataset.act;
      if (act === 'del') {
        if (arr.length <= 1) { qtaMsg('至少要保留一个答题环节', true); return; }
        arr.splice(i, 1);
      } else if (act === 'up') {
        if (i <= 0) return;
        const t = arr[i - 1]; arr[i - 1] = arr[i]; arr[i] = t;
      } else if (act === 'down') {
        if (i >= arr.length - 1) return;
        const t = arr[i + 1]; arr[i + 1] = arr[i]; arr[i] = t;
      }
      qtaRenderStages(arr);
    });
  }

  const bind = (sel, fn) => { const el = $(sel); if (el) el.addEventListener('click', fn); };
  bind('#btnQtaNew', () => qtaNew());
  bind('#btnQtaSave', () => qtaSave());
  bind('#btnQtaCopy', () => qtaCopy());
  bind('#btnQtaDelete', () => qtaDelete());
  bind('#btnQtaReset', () => qtaReset());
})();

/* =====================================================================
   快速记录（浮层面板）：听记时随手记学生的问题
   - 回车即换下一条并自动编号；删行 / 插行后编号自动重排
   - 每条正文可自由编辑；内容按「当前学生」自动存到后端，重启 / 换会话 / 换学生都不丢
   - 面板可拖动、可缩放、可最小化（不挡逐字稿）；支持一键复制全部 / 一键清空
   ===================================================================== */
const QN_POS_KEY = 'iln.quicknote.pos.v1';
const QN = { open: false, sid: '', dirty: false, timer: 0, drag: null };
window.__qn = QN;   // 便于自检 / 排障时查看面板状态

function qnPanel() { return document.getElementById('qnotePanel'); }
function qnRowList() { return Array.from(document.querySelectorAll('#qnList .qn-row')); }
function qnTexts() {
  return qnRowList().map((r) => { const t = r.querySelector('.qn-ta'); return t ? t.value : ''; });
}

/* 去掉末尾空行，保证至少一行 */
function qnNormal(list) {
  const out = (list || []).map((s) => String(s === null || s === undefined ? '' : s));
  while (out.length && !out[out.length - 1].trim()) out.pop();
  return out.length ? out : [''];
}

/* 每条快速记录：自动增高 与 手动拖拽 共存
   - 没手动拖过：随内容自动增高（上限 220px，超出改为内部滚动）
   - 手动拖过（dataset.manualH）：一律听用户的，内容超出自带滚动条
   - 手动高度按「学生 + 行号」记在 localStorage，刷新后原样恢复 */
var QN_H_KEY = 'iln.qnHeights.v1';
function qnHMap() {
  try { return JSON.parse(localStorage.getItem(QN_H_KEY) || '{}') || {}; } catch (e) { return {}; }
}
function qnHStore(obj) {
  try { localStorage.setItem(QN_H_KEY, JSON.stringify(obj)); } catch (e) {}
}
function qnHSid() {
  return (QN && QN.sid) || (S.session && S.session.cur_student) || 'default';
}
function qnHBucket() {
  const m = qnHMap();
  return m[qnHSid()] || {};
}
function qnHGet(i) {
  const b = qnHBucket();
  return b[i] ? Math.round(b[i]) : 0;
}
function qnHSet(i, h) {
  const m = qnHMap();
  const k = qnHSid();
  m[k] = m[k] || {};
  if (h) m[k][i] = Math.round(h); else delete m[k][i];
  qnHStore(m);
}
/* 删除/重排行号后，把内存里的手动高度按新行号重新落一遍 */
function qnHCapture() {
  const b = {};
  qnRowList().forEach((row, i) => {
    const ta = row.querySelector('.qn-ta');
    const h = ta && parseFloat(ta.dataset.manualH || '0');
    if (h > 0) b[i] = Math.round(h);
  });
  const m = qnHMap();
  m[qnHSid()] = b;
  qnHStore(m);
}
function qnFitAuto(ta) {
  ta.style.height = 'auto';
  const h = Math.max(26, Math.min(ta.scrollHeight, 220));
  ta.style.height = h + 'px';
  return h;
}
function qnAutosize(ta) {
  if (!ta) return;
  const mh = parseFloat(ta.dataset.manualH || '0');
  if (mh > 0) { ta.style.height = mh + 'px'; return; }   // 手动拖过：不动它
  qnFitAuto(ta);
}

function qnRenumber() {
  qnRowList().forEach((row, i) => {
    const no = row.querySelector('.qn-no');
    if (no) no.textContent = (i + 1) + '.';
  });
  qnSyncCount();
}

function qnSyncCount() {
  const n = qnTexts().filter((t) => t.trim()).length;
  const c = document.getElementById('qnCount');
  if (c) c.textContent = n + ' 条';
}

function qnNewRowEl() {
  const row = document.createElement('div');
  row.className = 'qn-row';
  const no = document.createElement('span');
  no.className = 'qn-no';
  const ta = document.createElement('textarea');
  ta.className = 'qn-ta';
  ta.rows = 1;
  ta.spellcheck = false;
  const del = document.createElement('button');
  del.type = 'button';
  del.className = 'qn-del';
  del.textContent = '\u00d7';
  del.title = '删除这条（后面的编号自动重排）';
  row.appendChild(no); row.appendChild(ta); row.appendChild(del);
  return row;
}

function qnRender(list) {
  const box = document.getElementById('qnList');
  if (!box) return;
  box.innerHTML = '';
  const arr = qnNormal(list);
  arr.forEach((t, i) => {
    const row = qnNewRowEl();
    const ta = row.querySelector('.qn-ta');
    ta.value = t || '';
    if (i === 0) ta.placeholder = '记学生回答里暴露的问题，回车换下一条\u2026';
    box.appendChild(row);
  });
  qnRenumber();
  Array.from(box.querySelectorAll('.qn-ta')).forEach((ta, i) => {
    const h = qnHGet(i);                 // 刷新后恢复手动拖出来的高度
    if (h) ta.dataset.manualH = h;
    qnAutosize(ta);
  });
  box.scrollTop = box.scrollHeight;
}

function qnAddRow(afterRow, focus) {
  const box = document.getElementById('qnList');
  if (!box) return null;
  const row = qnNewRowEl();
  if (afterRow && afterRow.parentNode === box) box.insertBefore(row, afterRow.nextSibling);
  else box.appendChild(row);
  qnRenumber();
  const ta = row.querySelector('.qn-ta');
  qnAutosize(ta);
  if (focus !== false && ta) ta.focus();
  if (row.scrollIntoView) { try { row.scrollIntoView({ block: 'nearest' }); } catch (e) {} }
  qnSaveSoon();
  return row;
}

function qnDelRow(row) {
  const rows = qnRowList();
  if (!rows.length) return;
  const i = rows.indexOf(row);
  if (rows.length <= 1) {   // 只剩一条：清空正文但保留这一行
    const ta = row.querySelector('.qn-ta');
    if (ta) { ta.value = ''; qnAutosize(ta); ta.focus(); }
    qnRenumber(); qnSaveSoon();
    return;
  }
  const prev = rows[i - 1] || rows[i + 1];
  row.remove();
  qnRenumber();
  qnHCapture();   // 行号已顺移，按新行号重存手动高度，避免串到别的行
  if (prev) {
    const ta = prev.querySelector('.qn-ta');
    if (ta) {
      ta.focus();
      const n = ta.value.length;
      try { ta.setSelectionRange(n, n); } catch (e) {}
    }
  }
  qnSaveSoon();
}

/* ---------------- 存取（按学生落到后端 session） ---------------- */
async function qnSave() {
  if (!QN.open) return;
  const items = qnTexts();
  const sid = QN.sid || '';
  try {
    const r = await api('/api/notes/save', { items: items, student_id: sid });
    QN.dirty = false;
    if (S.session) S.session.notes = r.notes || items;
    const st = stById(r.student_id);
    if (st) st.notes = r.notes || items;
  } catch (e) { /* 静默失败：内容仍在界面上，下次输入会再存一次 */ }
}

function qnSaveSoon() {
  QN.dirty = true;
  clearTimeout(QN.timer);
  QN.timer = setTimeout(() => { QN.timer = 0; qnSave(); }, 400);
}

/* 会话 / 学生变化时把面板内容换成对应的记录，避免串号 */
function qnOnSession(sess) {
  if (!sess) return;
  const sid = sess.student_id || sess.cur_student || '';
  if (!QN.open) { if (sid) QN.sid = sid; return; }
  const changed = !!(sid && QN.sid && sid !== QN.sid);
  if (changed) {
    clearTimeout(QN.timer); QN.timer = 0;
    if (QN.dirty) qnSave();     // 用切换前记录的 student_id 精确落盘
    QN.sid = sid;
    QN.dirty = false;
    qnRender(sess.notes || []);
    return;
  }
  if (!QN.sid) QN.sid = sid;
  if (QN.dirty) return;         // 用户正在敲字，别打断
  const cur = qnNormal(qnTexts());
  const incoming = qnNormal(sess.notes || []);
  if (JSON.stringify(cur) !== JSON.stringify(incoming)) qnRender(sess.notes || []);
}

/* ---------------- 位置 / 尺寸 ---------------- */
function qnLoadPos() {
  try { return JSON.parse(localStorage.getItem(QN_POS_KEY) || '{}') || {}; } catch (e) { return {}; }
}
function qnSavePos(o) {
  try { localStorage.setItem(QN_POS_KEY, JSON.stringify(o)); } catch (e) {}
}
function qnClampXY(x, y) {
  const p = qnPanel();
  const w = p ? p.offsetWidth : 320;
  const h = p ? p.offsetHeight : 340;
  const maxX = Math.max(0, window.innerWidth - w - 4);
  const maxY = Math.max(0, window.innerHeight - h - 4);
  return { x: Math.min(Math.max(0, x), maxX), y: Math.min(Math.max(0, y), maxY) };
}

function qnPlace() {
  const p = qnPanel();
  if (!p) return;
  const st = qnLoadPos();
  if (typeof st.w === 'number') p.style.width = Math.round(st.w) + 'px';
  if (p.classList.contains('min')) p.style.height = 'auto';
  else if (typeof st.h === 'number') p.style.height = Math.round(st.h) + 'px';
  if (typeof st.x === 'number' && typeof st.y === 'number') {
    const q = qnClampXY(st.x, st.y);
    p.style.left = q.x + 'px'; p.style.top = q.y + 'px';
    p.style.right = 'auto'; p.style.bottom = 'auto';
  } else {
    // 没存过位置就用 CSS 默认定位（右下角，避开顶部工具栏与逐字稿）；隐藏态 rect 全为 0，不能用它兜底
    p.style.left = ''; p.style.top = ''; p.style.right = ''; p.style.bottom = '';
  }
}

function qnResetPos() {
  const p = qnPanel();
  if (!p) return;
  try { localStorage.removeItem(QN_POS_KEY); } catch (e) {}
  p.classList.remove('min');
  p.style.width = ''; p.style.height = '';
  p.style.left = ''; p.style.top = ''; p.style.right = '';
  const minBtn = document.getElementById('qnMin');
  if (minBtn) { minBtn.textContent = '\u2014'; minBtn.title = '最小化 / 展开（缩小后不挡逐字稿）'; }
  qnPlace();
  qnSavePos({});
}

/* ---------------- 打开 / 收起 / 最小化 ---------------- */
function qnOpen() {
  const p = qnPanel();
  if (!p) return;
  p.hidden = false;
  QN.open = true;
  const btn = document.getElementById('btnQuickNote');
  if (btn) btn.classList.add('open');
  QN.sid = (S.session && (S.session.student_id || S.session.cur_student)) || S.cur || '';
  QN.dirty = false;
  qnRender((S.session && S.session.notes) || []);
  qnPlace();
  // 再拉一次最新状态，保证别处（换学生 / 换会话）改过也对得上
  api('/api/state').then((r) => {
    const sess = (r && r.session) || {};
    if (QN.open && !QN.dirty) qnOnSession(sess);
  }).catch(() => {});
  setTimeout(() => {
    const p2 = qnPanel();
    if (!p2 || p2.classList.contains('min')) return;
    const row = document.querySelector('#qnList .qn-row');
    const ta = row ? row.querySelector('.qn-ta') : null;
    if (ta) { ta.focus(); const n = ta.value.length; try { ta.setSelectionRange(n, n); } catch (e) {} }
  }, 40);
}

function qnClose() {
  clearTimeout(QN.timer); QN.timer = 0;
  if (QN.dirty) qnSave();
  const p = qnPanel();
  if (p) p.hidden = true;
  QN.open = false;
  const btn = document.getElementById('btnQuickNote');
  if (btn) btn.classList.remove('open');
}

function qnToggle() {
  const p = qnPanel();
  if (!p) return;
  if (p.hidden) qnOpen(); else qnClose();
}

function qnToggleMin() {
  const p = qnPanel();
  if (!p) return;
  const st = qnLoadPos();
  const min = !p.classList.contains('min');
  if (min) {
    st.h = p.offsetHeight || st.h;
    p.classList.add('min');
    p.style.height = 'auto';
  } else {
    p.classList.remove('min');
    p.style.height = (typeof st.h === 'number' ? st.h : 340) + 'px';
    if (!p.style.width && typeof st.w === 'number') p.style.width = st.w + 'px';
    const r = p.getBoundingClientRect();
    const q = qnClampXY(r.left, r.top);
    p.style.left = q.x + 'px'; p.style.top = q.y + 'px'; p.style.right = 'auto';
  }
  st.min = min;
  st.w = p.offsetWidth || st.w;
  qnSavePos(st);
  const b = document.getElementById('qnMin');
  if (b) {
    b.textContent = min ? '\uff0b' : '\u2014';
    b.title = min ? '展开面板' : '最小化（缩小后不挡逐字稿）';
  }
}

/* ---------------- 复制全部 / 清空 ---------------- */
function qnCopyText() {
  const items = qnTexts().map((t) => t.trim()).filter(Boolean);
  return items.map((t, i) => (i + 1) + '. ' + t).join('\n');
}

async function qnWriteClipboard(text) {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (e) { /* 走兜底 */ }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    ta.remove();
    return ok;
  } catch (e) { return false; }
}

async function qnCopyAll() {
  const text = qnCopyText();
  if (!text) { toast('还没有可复制的内容', true); return; }
  const ok = await qnWriteClipboard(text);
  window.__qnLastCopy = text;   // 便于自检
  const n = text.split('\n').length;
  toast(ok ? ('已复制 ' + n + ' 条记录到剪贴板') : '复制失败，请手动选择文本复制', !ok);
}

async function qnClearAll() {
  const n = qnTexts().filter((t) => t.trim()).length;
  if (n && !confirm('清空快速记录里的 ' + n + ' 条内容？\n\n清空后无法恢复。')) return;
  clearTimeout(QN.timer); QN.timer = 0;
  qnRender(['']);
  QN.dirty = true;
  await qnSave();
  toast('快速记录已清空');
  const ta = document.querySelector('#qnList .qn-ta');
  if (ta) ta.focus();
}

/* ---------------- 交互绑定 ---------------- */
function qnBind() {
  const p = qnPanel();
  if (!p) return;
  const btn = document.getElementById('btnQuickNote');
  if (btn) btn.addEventListener('click', qnToggle);

  const list = document.getElementById('qnList');

  // 回车＝下一条（光标后的文字带到新行）；Shift+回车＝同一条内换行
  list.addEventListener('keydown', (e) => {
    const ta = e.target.closest ? e.target.closest('.qn-ta') : null;
    if (!ta) return;
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      const row = ta.closest('.qn-row');
      const v = ta.value;
      const a = (ta.selectionStart === null || ta.selectionStart === undefined) ? v.length : ta.selectionStart;
      const b = (ta.selectionEnd === null || ta.selectionEnd === undefined) ? v.length : ta.selectionEnd;
      ta.value = v.slice(0, a);
      const tail = v.slice(b);
      qnAutosize(ta);
      const nr = qnAddRow(row, true);
      const nta = nr ? nr.querySelector('.qn-ta') : null;
      if (nta) {
        nta.value = tail;
        qnAutosize(nta);
        try { nta.setSelectionRange(0, 0); } catch (err) {}
      }
      qnSaveSoon();
      return;
    }
    if (e.key === 'Backspace' && ta.value === '') {
      const row = ta.closest('.qn-row');
      if (qnRowList().indexOf(row) > 0) { e.preventDefault(); qnDelRow(row); }
    }
  });

  list.addEventListener('input', (e) => {
    const ta = e.target.closest ? e.target.closest('.qn-ta') : null;
    if (!ta) return;
    qnAutosize(ta);
    qnSyncCount();
    qnSaveSoon();
  });

  // 手动拖拽缩放：和自动增高共存；松手后记住这条的高度（刷新后恢复）
  let qnRz = null;
  list.addEventListener('pointerdown', (e) => {
    const ta = e.target.closest ? e.target.closest('.qn-ta') : null;
    if (!ta) return;
    qnRz = { ta: ta, h: ta.offsetHeight };
  }, true);
  const qnRzEnd = () => {
    const st = qnRz;
    qnRz = null;
    if (!st || !st.ta || !st.ta.isConnected) return;
    const h = st.ta.offsetHeight;
    if (Math.abs(h - (st.h || 0)) < 3) return;     // 高度没变化，不算手动拖过
    st.ta.dataset.manualH = h;
    const rows = qnRowList();
    const idx = rows.findIndex((r) => r.contains(st.ta));
    if (idx >= 0) qnHSet(idx, h);
  };
  document.addEventListener('pointerup', qnRzEnd, true);
  document.addEventListener('pointercancel', qnRzEnd, true);

  // 双击把这条恢复成「随内容自动增高」
  list.addEventListener('dblclick', (e) => {
    const ta = e.target.closest ? e.target.closest('.qn-ta') : null;
    if (!ta) return;
    if (!ta.dataset.manualH) return;
    delete ta.dataset.manualH;
    qnFitAuto(ta);
    const rows = qnRowList();
    const idx = rows.findIndex((r) => r.contains(ta));
    if (idx >= 0) qnHSet(idx, 0);
    toast('这条已恢复自动高度');
  });

  // 多行粘贴：一行一条，自动拆行并编号
  list.addEventListener('paste', (e) => {
    const ta = e.target.closest ? e.target.closest('.qn-ta') : null;
    if (!ta) return;
    const cd = e.clipboardData || window.clipboardData;
    const txt = cd ? (cd.getData('text') || '') : '';
    if (!txt || !/[\r\n]/.test(txt)) return;
    e.preventDefault();
    const lines = txt.replace(/\r/g, '').split('\n');
    const first = lines.shift() || '';
    const a = ta.selectionStart === null ? ta.value.length : ta.selectionStart;
    const b = ta.selectionEnd === null ? ta.value.length : ta.selectionEnd;
    ta.setRangeText(first, a, b, 'end');
    qnAutosize(ta);
    let anchor = ta.closest('.qn-row');
    lines.forEach((ln) => {
      const r = qnAddRow(anchor, false);
      if (!r) return;
      const t2 = r.querySelector('.qn-ta');
      t2.value = ln;
      qnAutosize(t2);
      anchor = r;
    });
    qnRenumber();
    const lastTa = anchor ? anchor.querySelector('.qn-ta') : null;
    if (lastTa) {
      lastTa.focus();
      const n = lastTa.value.length;
      try { lastTa.setSelectionRange(n, n); } catch (err) {}
    }
    qnSaveSoon();
  });

  list.addEventListener('click', (e) => {
    const del = e.target.closest ? e.target.closest('.qn-del') : null;
    if (!del) return;
    const row = del.closest('.qn-row');
    if (row) qnDelRow(row);
  });

  const add = document.getElementById('qnAdd');
  if (add) add.addEventListener('click', () => {
    const rows = qnRowList();
    qnAddRow(rows[rows.length - 1] || null, true);
  });
  const copy = document.getElementById('qnCopy');
  if (copy) copy.addEventListener('click', qnCopyAll);
  const clr = document.getElementById('qnClear');
  if (clr) clr.addEventListener('click', qnClearAll);
  const min = document.getElementById('qnMin');
  if (min) min.addEventListener('click', qnToggleMin);
  const close = document.getElementById('qnClose');
  if (close) close.addEventListener('click', qnClose);

  // ---- 拖动 ----
  const head = document.getElementById('qnHead');
  if (head) {
    head.addEventListener('pointerdown', (e) => {
      if (e.target.closest && e.target.closest('.qn-ib')) return;
      if (typeof e.button === 'number' && e.button !== 0) return;
      const r = p.getBoundingClientRect();
      p.style.left = r.left + 'px';
      p.style.top = r.top + 'px';
      p.style.right = 'auto';
      QN.drag = { mode: 'move', dx: e.clientX - r.left, dy: e.clientY - r.top, w: r.width, h: r.height };
      p.classList.add('dragging');
      try { head.setPointerCapture(e.pointerId); } catch (err) {}
      e.preventDefault();
    });
    head.addEventListener('dblclick', (e) => {
      if (e.target.closest && e.target.closest('.qn-ib')) return;
      qnResetPos();
    });
    head.addEventListener('pointermove', (e) => {
      if (!QN.drag || QN.drag.mode !== 'move') return;
      const q = qnClampXY(e.clientX - QN.drag.dx, e.clientY - QN.drag.dy);
      p.style.left = q.x + 'px';
      p.style.top = q.y + 'px';
    });
    const endMove = () => {
      if (!QN.drag || QN.drag.mode !== 'move') return;
      const r2 = p.getBoundingClientRect();
      const st = qnLoadPos();
      st.x = Math.round(r2.left); st.y = Math.round(r2.top);
      qnSavePos(st);
      QN.drag = null;
      p.classList.remove('dragging');
    };
    head.addEventListener('pointerup', endMove);
    head.addEventListener('pointercancel', endMove);
  }

  // ---- 缩放（右下角） ----
  const grip = document.getElementById('qnResize');
  if (grip) {
    grip.addEventListener('pointerdown', (e) => {
      if (typeof e.button === 'number' && e.button !== 0) return;
      const r = p.getBoundingClientRect();
      p.style.left = r.left + 'px';
      p.style.top = r.top + 'px';
      p.style.right = 'auto';
      if (p.classList.contains('min')) { p.classList.remove('min'); p.style.height = r.height + 'px'; }
      QN.drag = { mode: 'size', x0: e.clientX, y0: e.clientY, w: r.width, h: r.height };
      p.classList.add('dragging');
      try { grip.setPointerCapture(e.pointerId); } catch (err) {}
      e.preventDefault();
    });
    grip.addEventListener('pointermove', (e) => {
      if (!QN.drag || QN.drag.mode !== 'size') return;
      const minW = 240, minH = 140;
      const r0 = p.getBoundingClientRect();
      // 宽度不能超出右边界，高度不能超出下边界（避免缩放后跑出窗口）
      const maxW = Math.max(minW, Math.min(window.innerWidth - 8, window.innerWidth - r0.left - 4));
      const maxH = Math.max(minH, Math.min(window.innerHeight - 8, window.innerHeight - r0.top - 4));
      const w = Math.min(Math.max(minW, QN.drag.w + (e.clientX - QN.drag.x0)), maxW);
      const h = Math.min(Math.max(minH, QN.drag.h + (e.clientY - QN.drag.y0)), maxH);
      p.style.width = Math.round(w) + 'px';
      p.style.height = Math.round(h) + 'px';
    });
    const endSize = () => {
      if (!QN.drag || QN.drag.mode !== 'size') return;
      const r2 = p.getBoundingClientRect();
      const st = qnLoadPos();
      st.w = Math.round(r2.width); st.h = Math.round(r2.height);
      st.x = Math.round(r2.left); st.y = Math.round(r2.top);
      st.min = p.classList.contains('min');
      qnSavePos(st);
      QN.drag = null;
      p.classList.remove('dragging');
    };
    grip.addEventListener('pointerup', endSize);
    grip.addEventListener('pointercancel', endSize);
  }

  // 窗口尺寸变化：把面板收回可视区
  window.addEventListener('resize', () => {
    const pp = qnPanel();
    if (!pp || pp.hidden) return;
    const r = pp.getBoundingClientRect();
    const q = qnClampXY(r.left, r.top);
    pp.style.left = q.x + 'px';
    pp.style.top = q.y + 'px';
    pp.style.right = 'auto';
  });

  // 切走页面 / 关窗口前先把没落盘的内容存掉
  window.addEventListener('beforeunload', () => { if (QN.dirty) qnSave(); });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden' && QN.open && QN.dirty) qnSave();
  });

  // 首屏：按上次的位置 / 尺寸摆好（默认隐藏）
  qnPlace();
}

qnBind();

/* ======================================================================
   精简要点浮层：把学生作答压成按答题结构分组的关键词要点，可一键复制
   ====================================================================== */
const PT = { open: false, res: null, busy: false, key: 'ir_points_pos_v1',
  alignKey: 'ir_points_align_v1',
  stamp: '', seq: null, stale: false,
  showAlign: (function () {
    try { return localStorage.getItem('ir_points_align_v1') !== '0'; } catch (e) { return true; }
  })() };

function ptPanel() { return document.getElementById('pointsPanel'); }
function ptEl(id) { return document.getElementById(id); }

function ptLoadPos() {
  try { return JSON.parse(localStorage.getItem(PT.key) || '{}') || {}; } catch (e) { return {}; }
}
function ptSavePos(o) { try { localStorage.setItem(PT.key, JSON.stringify(o)); } catch (e) {} }

function ptMinY() {
  const tb = document.querySelector('.topbar');
  if (!tb) return 8;
  const r = tb.getBoundingClientRect();
  return Math.round((r.bottom || 56) + 4);
}
const PT_MIN_W = 300, PT_MIN_H = 260;
/* 浮层理想尺寸（用户拖拽/记忆值）：视口收缩时仅当次压缩，窗口变大后可还原 */
function ptIdealSize(p) {
  const st = ptLoadPos();
  const iw = p && p.dataset && p.dataset.iw ? parseFloat(p.dataset.iw) : NaN;
  const ih = p && p.dataset && p.dataset.ih ? parseFloat(p.dataset.ih) : NaN;
  const w = isFinite(iw) ? iw : (typeof st.w === 'number' ? st.w : (p ? p.offsetWidth : 420) || 420);
  const h = isFinite(ih) ? ih : (typeof st.h === 'number' ? st.h : (p ? p.offsetHeight : 520) || 520);
  return { w: w, h: h };
}
/* 位置 + 尺寸一起收进可视区：窄窗口 / 高 DPI 缩放下不越界，顶部避让顶栏 */
function ptClampXY(x, y) {
  const p = ptPanel();
  const minY = ptMinY();
  const vw = window.innerWidth, vh = window.innerHeight;
  const isMin = !!(p && p.classList.contains('min'));
  const ideal = ptIdealSize(p);
  const wCap = Math.max(PT_MIN_W, vw - 8);
  const hCap = Math.max(120, vh - minY - 8);
  const w = Math.round(Math.min(Math.max(PT_MIN_W, ideal.w), wCap));
  const h = isMin ? Math.round(ideal.h)
    : Math.round(Math.min(Math.max(PT_MIN_H, ideal.h), Math.max(PT_MIN_H, hCap)));
  const maxX = Math.max(0, vw - w - 4);
  const maxY = Math.max(minY, vh - h - 4);
  return { x: Math.round(Math.min(Math.max(0, x), maxX)),
           y: Math.round(Math.min(Math.max(minY, y), maxY)), w: w, h: h };
}

/* 按当前视口重新摆正（位置 + 宽度 + 高度），不改写用户记忆的理想尺寸 */
function ptFit() {
  const p = ptPanel();
  if (!p || p.hidden) return;
  const r = p.getBoundingClientRect();
  const q = ptClampXY(r.left, r.top);
  p.style.width = q.w + 'px';
  p.style.left = q.x + 'px'; p.style.top = q.y + 'px';
  p.style.right = 'auto'; p.style.bottom = 'auto';
  if (p.classList.contains('min')) {
    p.style.height = 'auto';
  } else {
    p.style.height = q.h + 'px';
    p.style.maxHeight = 'calc(100vh - ' + (ptMinY() + 8) + 'px)';
  }
}

function ptPlace() {
  const p = ptPanel();
  if (!p) return;
  const st = ptLoadPos();
  if (typeof st.w === 'number') p.style.width = Math.round(st.w) + 'px';
  if (p.classList.contains('min')) p.style.height = 'auto';
  else if (typeof st.h === 'number') p.style.height = Math.round(st.h) + 'px';
  if (typeof st.x === 'number' && typeof st.y === 'number') {
    const q = ptClampXY(st.x, st.y);
    p.style.left = q.x + 'px'; p.style.top = q.y + 'px';
    p.style.right = 'auto'; p.style.bottom = 'auto';
  } else if (st.right === true) {
    const w = p.offsetWidth || 420;
    const q = ptClampXY(window.innerWidth - w - 22, 74);
    p.style.left = q.x + 'px'; p.style.top = q.y + 'px';
    p.style.right = 'auto'; p.style.bottom = 'auto';
  } else {
    p.style.left = ''; p.style.top = ''; p.style.right = ''; p.style.bottom = '';
  }
  // 记住理想尺寸，再按当前视口收一次，避免小窗/高 DPI 下面板越出可视区
  p.dataset.iw = String(Math.round((typeof st.w === 'number' ? st.w : p.offsetWidth) || 420));
  p.dataset.ih = String(Math.round((typeof st.h === 'number' ? st.h : p.offsetHeight) || 520));
  ptFit();
}

/* ---------- 取精简稿 ---------- */
/* 字数统计口径：去掉【环节】标注与空白后的正文字数 */
function ptCountText(t) {
  return String(t || '').replace(/【[^】]*】|\s/g, '').length;
}

function ptStampTxt() {
  const el = ptEl('ptSrcStamp');
  if (!el) return;
  if (!PT.stamp) { el.textContent = ''; el.classList.remove('stale'); return; }
  el.textContent = (PT.stale ? '精简稿已更新 · 上次取稿 ' : '取稿于 ') + PT.stamp +
    (PT.stale ? '，可再点「取当前精简稿」' : '');
  el.classList.toggle('stale', !!PT.stale);
}

function ptSetStat() {
  const ta = ptEl('ptText');
  const n = ptCountText(ta ? ta.value : '');
  const st = ptEl('ptSrcStat');
  if (st) st.textContent = n + ' 字';
  ptStampTxt();
}

/* 取不到稿时给明确出口：不用空白面板让人猜 */
function ptGuide(kind, extra) {
  const emp = ptEl('ptEmpty');
  const note = ptEl('ptNote');
  const box = ptEl('ptGroups');
  if (box) box.innerHTML = '';
  PT.res = null;
  const isHist = kind === 'hist-empty';
  const main = isHist ? '该场记录里这位学生没有可用的逐字稿'
    : '当前无实时会话，可从历史记录选择一场';
  if (note) note.textContent = main;
  if (!emp) return;
  emp.hidden = false;
  emp.innerHTML = '<div class="pt-guide-t">' + esc(main) + '</div>' +
    (extra ? '<div class="pt-guide-s">' + esc(extra) + '</div>' : '') +
    '<div class="pt-guide-b">' +
      '<button class="mini primary-m" id="ptGoHist">从历史记录选择一场</button>' +
      '<span class="muted">也可以直接把学生作答粘贴到上方输入框</span>' +
    '</div>';
  const b = ptEl('ptGoHist');
  if (b) {
    b.addEventListener('click', () => {
      if (typeof histLoad === 'function') histLoad();
      if (typeof openModal === 'function') openModal('#modalHistory');
      ptClose();
    });
  }
}

async function ptFetch(silent) {
  const btn = ptEl('ptFetch');
  if (btn) btn.disabled = true;
  try {
    // 历史回看态：稿子在存档里（按学生分开存），实时会话可能是空的
    const hist = !!(S.histFile && S.histData);
    const r = hist
      ? await api('/api/records/points?file=' + encodeURIComponent(S.histFile) +
          '&student=' + encodeURIComponent(S.cur || ''))
      : await api('/api/points/source');
    const ta = ptEl('ptText');
    const txt = (r && r.text) || '';
    if (ta) ta.value = txt;
    PT.stamp = (r && r.stamp) || '';
    PT.seq = (r && typeof r.edit_seq === 'number') ? r.edit_seq : null;
    PT.stale = false;
    ptSetStat();
    const meta = ptEl('ptMeta');
    if (meta) {
      const stg = (r && r.stages) || [];
      meta.textContent = (r && r.qtype ? r.qtype : '未识别题型') +
        (stg.length ? ' · ' + stg.length + ' 环节' : '') +
        (hist ? ' · 历史' : '');
    }
    if (!txt.trim()) {
      // 空稿不再静默：直接告诉用户去哪儿拿内容
      const np = parseInt((r && r.para_total) || 0, 10) || 0;
      ptGuide(hist ? 'hist-empty' : 'live-empty',
        hist ? ('「' + ((r && r.student_name) || '该学生') + '」' +
          (np ? ('共 ' + np + ' 段，但都没有可用精简文本') : '在存档里没有逐字稿')) : '');
      if (!silent) toast(hist ? '这位学生在存档里没有精简稿，可换个学生或粘贴作答' :
        '当前无实时会话，可从历史记录选择一场', true);
    } else if (!silent) {
      toast('已取当前精简稿 ' + ptCountText(txt) + ' 字（' + (r.stamp || '') + '）');
    }
    return txt;
  } catch (e) {
    if (!silent) toast('取精简稿失败：' + e.message, true);
    return '';
  } finally {
    if (btn) btn.disabled = false;
  }
}

/* ---------- 渲染结果 ---------- */
const PT_ROLE_TXT = { open: '入题', opinion: '观点', analyze: '分析', action: '对策', close: '收尾' };

function ptBadgeCls(s) {
  if (s === '答到') return 'hit';
  if (s === '偏题') return 'off';
  if (s === '遗漏') return 'miss';
  return '';
}

function ptRender(res) {
  PT.res = res;
  const box = ptEl('ptGroups');
  const emp = ptEl('ptEmpty');
  if (!box) return;
  const showAlign = !!PT.showAlign;
  const _pn = ptPanel();
  if (_pn) _pn.classList.toggle('show-align', showAlign);
  // 对齐关掉时：不显示「参考未答到（遗漏）」组，也不挂答到/偏题徽标，只留纯要点清单
  const groups = ((res && res.groups) || []).filter(
    (g) => showAlign || !(g.miss === true || String(g.stage || '').indexOf('遗漏') >= 0));
  box.innerHTML = '';
  if (!groups.length) {
    if (emp) {
      emp.hidden = false;
      emp.textContent = (res && res.note) || '没抽出要点，先取精简稿或粘贴更完整的作答内容再分析';
    }
  } else if (emp) {
    emp.hidden = true;
  }
  const total = groups.reduce((a, g) => a + ((g.items || []).length), 0);
  groups.forEach((g) => {
    const d = document.createElement('div');
    d.className = 'pt-g' + (String(g.stage || '').indexOf('遗漏') >= 0 ? ' missg' : '');
    const items = g.items || [];
    const head = document.createElement('div');
    head.className = 'pt-g-head';
    const nm = document.createElement('span');
    nm.className = 'pt-g-name';
    nm.textContent = g.stage || '要点';
    head.appendChild(nm);
    const cnt = document.createElement('span');
    cnt.className = 'pt-g-n';
    cnt.textContent = items.length + ' 条';
    head.appendChild(cnt);
    if (g.role && PT_ROLE_TXT[g.role]) {
      const rl = document.createElement('span');
      rl.className = 'pt-g-role';
      rl.textContent = PT_ROLE_TXT[g.role];
      head.appendChild(rl);
    }
    d.appendChild(head);
    const ul = document.createElement('ul');
    ul.className = 'pt-list';
    items.forEach((it) => {
      const li = document.createElement('li');
      const dot = document.createElement('i');
      dot.className = 'pt-li-dot';
      li.appendChild(dot);
      const tx = document.createElement('span');
      tx.className = 'pt-txt';
      tx.textContent = it.text || '';
      li.appendChild(tx);
      if (it.status && showAlign) {
        const b = document.createElement('span');
        b.className = 'pt-badge ' + ptBadgeCls(it.status);
        b.textContent = it.status;
        li.appendChild(b);
      }
      ul.appendChild(li);
    });
    d.appendChild(ul);
    box.appendChild(d);
  });

  const meta = ptEl('ptMeta');
  const stt = (res && res.stats) || {};
  const srcTxt = ptJudgeSrc(res);
  if (meta) {
    meta.textContent = (total ? (total + ' 条要点') : '无要点') +
      (showAlign ? ' · ' + srcTxt : '');
    meta.title = '命中 ' + (stt.hit || 0) + '｜偏题 ' + (stt.off || 0) + '｜遗漏 ' + (stt.miss || 0) +
      '｜去重 ' + (stt.dup_removed || 0) + '｜判定来源：' + srcTxt;
  }
  const note = ptEl('ptNote');
  if (note) {
    note.textContent = (res && res.note) || '';
    note.title = note.textContent;
  }
  const tip = ptEl('ptTip');
  if (tip) {
    tip.textContent = showAlign
      ? ('命中 ' + (stt.hit || 0) + '｜偏题 ' + (stt.off || 0) + '｜遗漏 ' + (stt.miss || 0))
      : '对齐标注已关闭';
  }
  ptAlignNote(res);
  const out = ptEl('ptOut');
  if (out) out.scrollTop = 0;
}

/* 判定来源：AI（模型逐条比对）还是本地（关键词匹配兜底） */
function ptJudgeSrc(res) {
  const src = (res && (res.judge_source || ((res.align || {}).source))) || 'local';
  return src === 'ai' ? 'AI 判定' : '本地判定';
}

/* 逐条判定明细：哪条参考要点答到 / 没答到，对应哪条学生要点、判定理由、链路完整性 */
function ptJudgeRows(res) {
  const rows = (res && res.judge_rows) || [];
  if (!rows.length) return '';
  const one = rows.map((r) => {
    const t = '[' + (r.id || '') + '] ' + (r.text || '') + '｜'
      + (r.hit ? '答到' : '未答到')
      + (r.sid ? '（对应 ' + r.sid + '）' : '')
      + '｜链路' + (r.link || '—')
      + (r.reason ? '｜' + r.reason : '');
    return '<span class="pal-jrow ' + (r.hit ? 'on' : 'no') + '">' + esc(t) + '</span>';
  }).join('');
  return '<span class="pal-row pal-judge">逐条判定：' + one + '</span>';
}

/* 参考要点对齐信息：单独一行小标注，跟着「对齐标注」开关一起显示 / 隐藏 */
function ptAlignNote(res) {
  const el = ptEl('ptAlignNote');
  if (!el) return;
  const al = (res && res.align) || null;
  const stt = (res && res.stats) || {};
  if (!PT.showAlign || !al) {
    el.innerHTML = '';
    el.title = '';
    el.hidden = true;
    return;
  }
  const srcBadge = '<span class="pal-src ' + (ptJudgeSrc(res) === 'AI 判定' ? 'ai' : 'local') + '">'
    + ptJudgeSrc(res) + '</span>';
  if (!al.ok) {
    // 题型或参考要点为空：只提示，不报错、不给百分比
    el.hidden = false;
    el.innerHTML = srcBadge + '<span class="pal-none">'
      + esc(al.note || '本题未设参考要点，仅做结构分组') + '</span>';
    el.title = '题型或参考要点为空时不做覆盖率对齐';
    return;
  }
  const missed = al.missed || [];
  const off = al.off || [];
  el.hidden = false;
  el.innerHTML = srcBadge
    + '<span class="pal-cov">对齐覆盖率 ' + (al.coverage == null ? '—' : al.coverage + '%')
    + '（' + (al.ref_hit || 0) + '/' + (al.ref_total || 0) + '）</span>'
    + '<span class="pal-row">漏答清单（' + missed.length + '）：'
    + (missed.length ? esc(missed.join('、')) : '无') + '</span>'
    + '<span class="pal-row">偏题清单（' + off.length + '）：'
    + (off.length ? esc(off.join('、')) : '无') + '</span>'
    + ptJudgeRows(res);
  el.title = (al.note || '') + '（答到 ' + (stt.hit || 0) + ' 条，仅供参考要点对齐）';
}
window.ptAlignNote = ptAlignNote;

/* ---------- 分析 ---------- */
async function ptAnalyze() {
  if (PT.busy) return;
  const ta = ptEl('ptText');
  const text = ta ? ta.value.trim() : '';
  if (!text) { toast('先「取当前精简稿」或粘贴学生作答内容', true); return; }
  const btn = ptEl('ptRun');
  const old = btn ? btn.textContent : '';
  PT.busy = true;
  if (btn) { btn.disabled = true; btn.textContent = '分析中…'; }
  try {
    const useAi = !!(ptEl('ptAi') && ptEl('ptAi').checked);
    const r = await api('/api/points/analyze', { text: text, ai: useAi, max_len: 20,
      with_status: !!PT.showAlign });
    ptRender(r);
    const stt = (r && r.stats) || {};
    toast('已分析：' + (stt.points || 0) + ' 条要点，' +
      (r && r.source === 'ai' ? 'AI 优化' : '本地规则') + '｜' + ptJudgeSrc(r) +
      (PT.stale ? '（注意：精简稿已更新，建议重新取稿）' : ''));
  } catch (e) {
    toast('分析失败：' + e.message, true);
  } finally {
    PT.busy = false;
    if (btn) { btn.disabled = false; btn.textContent = old || '分析要点'; }
  }
}

/* ---------- 复制 ---------- */
async function ptCopy(mode) {
  const res = PT.res;
  if (!res) { toast('还没有可复制的要点，先点「分析要点」', true); return; }
  let text = '';
  if (PT.showAlign) {
    text = (mode === 'plain' ? res.plain : res.bullet) || '';
  } else {
    text = (mode === 'plain' ? (res.plain_bare || res.plain) : (res.bullet_bare || res.bullet)) || '';
  }
  if (!text.trim()) { toast('还没有可复制的要点', true); return; }
  const ok = await qnWriteClipboard(text);
  window.__ptLastCopy = text;             // 便于自检
  window.__ptLastMode = mode;
  const n = text.split('\n').filter((x) => x.trim()).length;
  toast(ok ? ('已复制（' + (mode === 'plain' ? '纯文本' : '分行要点') + '，' + n + ' 行）')
           : '复制失败，请手动选中文本复制', !ok);
}

/* ---------- 打开 / 关闭 ---------- */
async function ptOpen() {
  const p = ptPanel();
  if (!p) return;
  p.hidden = false;
  PT.open = true;
  const b = ptEl('btnPoints');
  if (b) b.classList.add('open');
  ptPlace();
  if (typeof rzScan === 'function') rzScan();   /* 浮层文本框同样挂自绘拖拽手柄 */
  const ta = ptEl('ptText');
  if (ta && !ta.value.trim()) await ptFetch(true);
  else ptSetStat();
  setTimeout(() => { if (ta && !ta.value.trim()) ta.focus(); }, 40);
}

function ptClose() {
  const p = ptPanel();
  if (p) p.hidden = true;
  PT.open = false;
  const b = ptEl('btnPoints');
  if (b) b.classList.remove('open');
}

function ptToggle() {
  const p = ptPanel();
  if (!p) return;
  if (p.hidden) ptOpen(); else ptClose();
}

function ptToggleMin() {
  const p = ptPanel();
  if (!p) return;
  const st = ptLoadPos();
  const min = !p.classList.contains('min');
  if (min) {
    st.h = p.dataset.ih ? parseFloat(p.dataset.ih) || st.h : (p.offsetHeight || st.h);
    p.style.maxHeight = '';
    p.classList.add('min');
    p.style.height = 'auto';
  } else {
    p.classList.remove('min');
    const hh = p.dataset.ih ? parseFloat(p.dataset.ih) : (typeof st.h === 'number' ? st.h : 520);
    p.style.height = (isFinite(hh) && hh > 0 ? hh : 520) + 'px';
    ptFit();                                   // 展开后连尺寸一起收回可视区
  }
  st.min = min;
  st.w = p.dataset.iw ? parseFloat(p.dataset.iw) : (p.offsetWidth || st.w);
  ptSavePos(st);
  const mb = ptEl('ptMin');
  if (mb) { mb.textContent = min ? '\uff0b' : '\u2014'; mb.title = min ? '展开浮层' : '最小化'; }
}

function ptResetPos() {
  const p = ptPanel();
  if (!p) return;
  try { localStorage.removeItem(PT.key); } catch (e) {}
  p.classList.remove('min');
  p.style.width = ''; p.style.height = '';
  p.style.left = ''; p.style.top = ''; p.style.right = ''; p.style.bottom = '';
  const mb = ptEl('ptMin');
  if (mb) mb.textContent = '\u2014';
  ptPlace();
}

/* ---------- 交互绑定 ---------- */
function ptBind() {
  const p = ptPanel();
  if (!p) return;
  const btn = ptEl('btnPoints');
  if (btn) btn.addEventListener('click', ptToggle);
  const close = ptEl('ptClose');
  if (close) close.addEventListener('click', ptClose);
  const min = ptEl('ptMin');
  if (min) min.addEventListener('click', ptToggleMin);
  const fetchBtn = ptEl('ptFetch');
  if (fetchBtn) fetchBtn.addEventListener('click', () => ptFetch(false));
  const clr = ptEl('ptClearSrc');
  if (clr) clr.addEventListener('click', () => {
    const ta = ptEl('ptText');
    if (ta) ta.value = '';
    ptSetStat();
    if (ta) ta.focus();
  });
  const ta = ptEl('ptText');
  if (ta) {
    ta.addEventListener('input', ptSetStat);
    // 输入法合成中不拦 Esc，避免吃掉上屏
    ta.addEventListener('keydown', (e) => {
      if (e.isComposing) return;
      if (e.key === 'Escape') { ptClose(); }
    });
  }
  const run = ptEl('ptRun');
  if (run) run.addEventListener('click', ptAnalyze);
  const cb = ptEl('ptCopyBullet');
  if (cb) cb.addEventListener('click', () => ptCopy('bullet'));
  const cp = ptEl('ptCopyPlain');
  if (cp) cp.addEventListener('click', () => ptCopy('plain'));

  // ---- 对齐标注开关（默认开；关掉后连复制文本里也不带答到/偏题/遗漏） ----
  const alCk = ptEl('ptAlign');
  if (alCk) {
    alCk.checked = !!PT.showAlign;
    alCk.addEventListener('change', () => {
      PT.showAlign = !!alCk.checked;
      try { localStorage.setItem(PT.alignKey, PT.showAlign ? '1' : '0'); } catch (e) {}
      if (PT.res) ptRender(PT.res);
      toast(PT.showAlign ? '已显示参考要点对齐标注' : '已隐藏对齐标注（只留纯要点）');
    });
  }

  // ---- 拖动 ----
  const head = ptEl('ptHead');
  let drag = null;
  if (head) {
    head.addEventListener('pointerdown', (e) => {
      if (e.target.closest && e.target.closest('.qn-ib')) return;
      if (typeof e.button === 'number' && e.button !== 0) return;
      const r = p.getBoundingClientRect();
      p.style.left = r.left + 'px';
      p.style.top = r.top + 'px';
      p.style.right = 'auto';
      p.style.bottom = 'auto';
      drag = { mode: 'move', dx: e.clientX - r.left, dy: e.clientY - r.top };
      p.classList.add('dragging');
      try { head.setPointerCapture(e.pointerId); } catch (err) {}
      e.preventDefault();
    });
    head.addEventListener('dblclick', (e) => {
      if (e.target.closest && e.target.closest('.qn-ib')) return;
      ptResetPos();
    });
    head.addEventListener('pointermove', (e) => {
      if (!drag || drag.mode !== 'move') return;
      const q = ptClampXY(e.clientX - drag.dx, e.clientY - drag.dy);
      p.style.left = q.x + 'px';
      p.style.top = q.y + 'px';
    });
    const endMove = () => {
      if (!drag || drag.mode !== 'move') return;
      const r2 = p.getBoundingClientRect();
      const st = ptLoadPos();
      st.x = Math.round(r2.left); st.y = Math.round(r2.top);
      ptSavePos(st);
      drag = null;
      p.classList.remove('dragging');
    };
    head.addEventListener('pointerup', endMove);
    head.addEventListener('pointercancel', endMove);
  }

  // ---- 缩放（右下角） ----
  const grip = ptEl('ptResize');
  let rz = null;
  if (grip) {
    grip.addEventListener('pointerdown', (e) => {
      if (typeof e.button === 'number' && e.button !== 0) return;
      const r = p.getBoundingClientRect();
      p.style.left = r.left + 'px';
      p.style.top = r.top + 'px';
      p.style.right = 'auto';
      p.style.bottom = 'auto';
      rz = { x0: e.clientX, y0: e.clientY, w: r.width, h: r.height };
      p.classList.add('dragging');
      try { grip.setPointerCapture(e.pointerId); } catch (err) {}
      e.preventDefault();
    });
    grip.addEventListener('pointermove', (e) => {
      if (!rz) return;
      const minW = 300, minH = 260;
      const r0 = p.getBoundingClientRect();
      const maxW = Math.max(minW, Math.min(window.innerWidth - 8, window.innerWidth - r0.left - 4));
      const maxH = Math.max(minH, Math.min(window.innerHeight - 8, window.innerHeight - r0.top - 4));
      const w = Math.min(Math.max(minW, rz.w + (e.clientX - rz.x0)), maxW);
      const h = Math.min(Math.max(minH, rz.h + (e.clientY - rz.y0)), maxH);
      p.style.width = Math.round(w) + 'px';
      p.style.height = Math.round(h) + 'px';
    });
    const endSize = () => {
      if (!rz) return;
      const r2 = p.getBoundingClientRect();
      const st = ptLoadPos();
      p.dataset.iw = String(Math.round(r2.width));
      p.dataset.ih = String(Math.round(r2.height));
      st.w = Math.round(r2.width); st.h = Math.round(r2.height);
      st.x = Math.round(r2.left); st.y = Math.round(r2.top);
      ptSavePos(st);
      rz = null;
      p.classList.remove('dragging');
    };
    grip.addEventListener('pointerup', endSize);
    grip.addEventListener('pointercancel', endSize);
  }

  // 窗口变化后把浮层收回可视区：位置 + 宽度 + 高度一起收，窄窗口 / 高 DPI 下不越界
  const ptRefit = () => { try { ptFit(); } catch (e) {} };
  window.addEventListener('resize', ptRefit);
  window.addEventListener('orientationchange', ptRefit);
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', ptRefit);
    window.visualViewport.addEventListener('scroll', ptRefit);
  }

  ptPlace();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', ptBind);
} else {
  ptBind();
}

/* =====================================================================
   低版本系统文本框拖拽（自绘手柄，20x20 热区，最小 60px，原生 resize 兜底）
   ===================================================================== */
const RZ_MIN_H = 60;
function rzApply(ta) {
  if (!ta || ta.dataset.rzp === '1') return;
  const par = ta.parentNode;
  if (!par) return;
  ta.dataset.rzp = '1';
  const cs = window.getComputedStyle(ta);
  const wrap = document.createElement('div');
  wrap.className = 'rzp';
  wrap.style.position = 'relative';
  wrap.style.display = 'block';
  if (cs.flex && cs.flex !== '0 1 auto' && cs.flex !== 'none') wrap.style.flex = cs.flex;
  par.insertBefore(wrap, ta);
  wrap.appendChild(ta);
  ta.style.boxSizing = 'border-box';
  if (cs.width && parseFloat(cs.width) > 0) ta.style.width = '100%';
  ta.style.resize = 'vertical';            /* 原生手柄保留兜底，不生效也无妨 */
  ta.style.minHeight = RZ_MIN_H + 'px';
  const h = document.createElement('div');
  h.className = 'rz-h';
  h.setAttribute('role', 'separator');
  h.setAttribute('aria-label', '拖动调整文本框高度');
  h.title = '拖动调整高度（最小 ' + RZ_MIN_H + 'px）';
  h.style.right = '3px';
  h.style.bottom = '3px';
  wrap.appendChild(h);
  const key = 'rz_h_' + (ta.id || String(ta.className).replace(/[^a-z0-9]+/gi, '_'));
  try {
    const sv = parseInt(localStorage.getItem(key) || '', 10);
    if (sv >= RZ_MIN_H) ta.style.height = sv + 'px';
  } catch (_) { }
  h.addEventListener('pointerdown', function (e) {
    if (e.button !== undefined && e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    h.classList.add('on');
    const y0 = e.clientY;
    const h0 = Math.round(ta.getBoundingClientRect().height);
    if (h.setPointerCapture) { try { h.setPointerCapture(e.pointerId); } catch (_) { } }
    const move = function (ev) {
      const nh = Math.max(RZ_MIN_H, Math.round(h0 + (ev.clientY - y0)));
      ta.style.height = nh + 'px';
      ta.style.overflowY = 'auto';
    };
    const up = function () {
      h.classList.remove('on');
      h.removeEventListener('pointermove', move);
      h.removeEventListener('pointerup', up);
      h.removeEventListener('pointercancel', up);
      try { localStorage.setItem(key, ta.style.height); } catch (_) { }
    };
    h.addEventListener('pointermove', move);
    h.addEventListener('pointerup', up);
    h.addEventListener('pointercancel', up);
  });
}
function rzScan() {
  document.querySelectorAll('textarea.ta, textarea.ip-ta, textarea.pt-ta').forEach(rzApply);
}

/* =====================================================================
   题库导入：后台任务 + 进度条 + 最小化角标 + 失败重试
   ===================================================================== */
const IMP_JOB = {
  jid: '', timer: 0, min: false, ai: false, mode: 'ai', files: 1, last: null,
  el(id) { return document.getElementById(id); },
  stop() { if (this.timer) { clearInterval(this.timer); this.timer = 0; } },
  start(jid, ai, files, mode) {
    this.stop();
    this.jid = jid; this.ai = !!ai; this.mode = (mode === 'local' ? 'local' : 'ai');
    this.files = (typeof files === 'number' ? files : 1); this.min = false;
    const card = this.el('impJob'), badge = this.el('impBadge');
    if (card) card.hidden = false;
    if (badge) { badge.hidden = true; badge.classList.remove('done'); }
    if (this.el('ijAi')) {
      this.el('ijAi').textContent = (this.mode === 'local') ? '仅本地解析 · 不调用 AI'
        : (this.ai ? 'AI 识别中…' : '无 API Key · 本地规则');
      this.el('ijAi').classList.remove('warn');
    }
    if (this.el('ijErr')) this.el('ijErr').hidden = true;
    if (this.el('ijFiles')) this.el('ijFiles').textContent = this.files + ' 个文件';
    this.paint({ stage: '读取文件', pct: 0, done: 0, total: 0, status: 'running' });
    const self = this;
    this.timer = setInterval(() => self.poll(), 700);
    this.poll();
  },
  paint(s) {
    this.last = s || {};
    const pct = Math.max(0, Math.min(100, Number(s.pct) || 0));
    if (this.el('ijStage')) this.el('ijStage').textContent = (s.stage || '处理中') + (s.status === 'done' ? ' · 完成' : '…');
    if (this.el('ijBar')) this.el('ijBar').style.width = pct + '%';
    if (this.el('ijPct')) this.el('ijPct').textContent = Math.round(pct) + '%';
    if (this.el('ijCur')) {
      const st = s.src_stat || {};
      const srcTxt = (s.status === 'done' && st.local != null)
        ? (' · AI ' + (st.ai || 0) + ' / 本地 ' + (st.local || 0) + ' / 文档自带 ' + (st.doc || 0)) : '';
      this.el('ijCur').textContent = '第 ' + (s.done || 0) + '/' + (s.total || 0) + ' 题' + srcTxt;
    }
    // AI 通道实际状态：余额不足/Key 无效/超时一律显式告知已回退，不冒充「AI 识别」
    const aiEl = this.el('ijAi');
    if (s && s.mode === 'local') this.mode = 'local';
    if (aiEl) {
      const st = s.ai_state || '';
      // 仅本地解析是用户自己选的，不是故障，不能标红成「AI 不可用」
      const isLocal = (s.mode === 'local');
      // part = AI 通道正常、仅个别题回退，不能整块标红成「AI 不可用」
      const bad = !isLocal && st && st !== 'ok' && st !== 'part';
      aiEl.textContent = bad
        ? (s.ai_note || 'AI 不可用 · 已回退本地规则')
        : (st === 'part'
          ? (s.ai_note || '部分题目回退本地规则')
          : (st === 'ok' ? 'AI 解析成功'
            : (this.mode === 'local' ? '仅本地解析 · 不调用 AI'
              : (this.ai ? 'AI 识别中…' : '无 API Key · 本地规则'))));
      aiEl.classList.toggle('warn', !!bad);
      // 悬停看完整真实原因（请求地址/模型/HTTP 状态码/响应正文/余额），行内只放摘要
      if (s.ai_note) aiEl.title = s.ai_note;
    }
    const badge = this.el('impBadge');
    if (badge && !badge.hidden && this.el('ijBadgeTxt')) this.el('ijBadgeTxt').textContent = '解析中 ' + Math.round(pct) + '%';
  },
  async poll() {
    if (!this.jid) return;
    try {
      const r = await fetch('/api/bank/import_job?jid=' + encodeURIComponent(this.jid));
      const j = await r.json().catch(() => ({}));
      if (!r.ok || j.ok === false) throw new Error(j.msg || ('进度读取失败 ' + r.status));
      this.paint(j);
      if (j.status === 'done') { this.stop(); this.finish(j); }
      else if (j.status === 'error') { this.stop(); this.fail(j.error || j.msg || '解析失败'); }
    } catch (e) {
      this.stop();
      this.fail(e.message);
    }
  },
  fail(msg) {
    const er = this.el('ijErr');
    if (er) { er.hidden = false; er.textContent = '解析失败：' + msg; }
    if (this.el('ijStage')) this.el('ijStage').textContent = '解析中断';
    const badge = this.el('impBadge');
    if (badge && !badge.hidden && this.el('ijBadgeTxt')) {
      badge.classList.add('done');
      this.el('ijBadgeTxt').textContent = '解析失败，点开查看';
    }
  },
  finish(j) {
    const rows = (j.rows || []).map((x) => Object.assign({}, x, {
      checked: true, expanded: false, orig: JSON.parse(JSON.stringify(x.item || {})),
    }));
    IMP_ROWS = rows;
    const seen = {};
    rows.forEach((x) => { const k = x.file || x.src || ''; if (k) seen[k] = (seen[k] || 0) + 1; });
    IMP_FILES = Object.keys(seen).map((k) => ({ file: k, name: k.replace(/\.[^.]+$/, ''), ok: true, count: seen[k] }));
    if (IMP_FILES.length) $('#ipBankName').value = IMP_FILES[0].name;
    renderImportPreview();
    const card = this.el('impJob');
    if (card) card.hidden = true;
    const badge = this.el('impBadge');
    const n = rows.length;
    const c = ipSrcCount();
    const loc = c.local;
    // 完成文案统一用三类真实来源统计，不再出现「AI 解析」冒充全部的情况
    const srcTxt = '解析完成：' + n + ' 题（AI ' + c.ai + ' / 本地 ' + c.local + ' / 文档自带 ' + c.doc + '）'
      + (this.mode === 'local' ? '，仅本地解析未调用 AI' : '');
    const note = (j && j.ai_note) ? ('；' + j.ai_note) : '';
    if (this.min && badge) {
      badge.hidden = false;
      badge.classList.add('done');
      this.el('ijBadgeTxt').textContent = n + ' 题解析完成（AI ' + c.ai + ' / 本地 ' + c.local + ' / 文档自带 ' + c.doc + '）';
      toast(srcTxt + note + '，点右下角标查看预览');
    } else {
      openModal('#modalImportPreview');
      if (!n) toast('没解析到题目，请看预览里的文件提示', true);
      else toast(srcTxt + note);
    }
  },
  minimize() {
    const card = this.el('impJob'), badge = this.el('impBadge');
    if (card) card.hidden = true;
    this.min = true;
    if (badge) {
      badge.hidden = false;
      badge.classList.remove('done');
      const s = this.last || {};
      if (this.el('ijBadgeTxt')) this.el('ijBadgeTxt').textContent = '解析中 ' + Math.round(Number(s.pct) || 0) + '%';
    }
  },
  restore() {
    const card = this.el('impJob'), badge = this.el('impBadge');
    this.min = false;
    if (badge) badge.hidden = true;
    if (card) {
      card.hidden = false;
      if (this.last) this.paint(this.last);
    }
    if (!this.timer && this.jid && (!this.last || this.last.status !== 'done')) {
      const self = this;
      this.timer = setInterval(() => self.poll(), 700);
    }
    if (this.last && this.last.status === 'done' && (IMP_ROWS || []).length) {
      openModal('#modalImportPreview');
    }
  },
};

/* 失败题 AI 重试：原地替换预览行，不新增副本 */
async function impRetry(id, btn) {
  if (!IMP_JOB.jid) { toast('该任务已过期，请重新导入文件', true); return; }
  const label = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = '重试中…'; }
  try {
    const r = await api('/api/bank/import_retry', { jid: IMP_JOB.jid, ids: [id] });
    const rows = r.rows || [];
    if (rows.length) {
      IMP_ROWS = rows.map((x) => {
        const old = IMP_ROWS.find((y) => y.id === x.id) || {};
        return Object.assign({}, x, {
          checked: old.checked !== false, expanded: !!old.expanded,
          orig: old.orig || JSON.parse(JSON.stringify(x.item || {})),
        });
      });
      renderImportList();
    }
    if (r.failed && r.failed.length) toast('这道题 AI 重试仍失败：' + r.failed[0].msg, true);
    else toast('已用 AI 重新解析该题（补齐：' + (((rows[0] || {}).ai_filled || []).join('、') || '无') + '）');
  } catch (e) {
    toast('重试失败：' + e.message, true);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = label; }
  }
}

/* =====================================================================
   主界面题库搜索（前置到外层，输入即时浮层，可载入会话 / 进题编辑）
   ===================================================================== */
const QS = { seq: 0, rows: [], idx: -1, t: 0, open: false };
function qEsc(s) { return esc(String(s == null ? '' : s)); }
function qsHide() {
  const pop = $('#bankSearchPop');
  if (pop) { pop.hidden = true; pop.innerHTML = ''; }
  QS.rows = []; QS.idx = -1; QS.open = false;
}
/* 浮层定位：贴输入框下沿留 6px，先左对齐再按视口收边；下方空间不足时翻到上方，绝不压住输入框 */
function qsPlace() {
  const pop = $('#bankSearchPop'), inp = $('#bankSearchInp');
  if (!pop || !inp || pop.hidden) return;
  const vw = window.innerWidth, vh = window.innerHeight;
  const M = 8, GAP = 6;
  const r = inp.getBoundingClientRect();
  const w = Math.min(430, Math.max(240, vw - M * 2));
  let left = r.left;
  if (left + w > vw - M) left = vw - M - w;
  left = Math.max(M, Math.min(left, Math.max(M, vw - M - w)));
  const below = vh - (r.bottom + GAP) - M;
  const above = r.top - GAP - M;
  let top = r.bottom + GAP;
  let maxH = Math.min(340, below);
  if (maxH < 120 && above > below) {
    maxH = Math.min(340, above);
    top = Math.max(M, r.top - GAP - maxH);
  }
  maxH = Math.max(96, maxH);
  pop.style.position = 'fixed';
  pop.style.width = Math.round(w) + 'px';
  pop.style.left = Math.round(left) + 'px';
  pop.style.right = 'auto';
  pop.style.top = Math.round(top) + 'px';
  pop.style.maxHeight = Math.round(maxH) + 'px';
  // 二次校正：按真实渲染高度回压，确保不越出视口下沿
  requestAnimationFrame(() => {
    if (pop.hidden) return;
    const pr = pop.getBoundingClientRect();
    const over = pr.bottom - (window.innerHeight - M);
    if (over > 0) {
      const t2 = Math.max(M, pr.top - over);
      pop.style.top = Math.round(t2) + 'px';
      const h2 = window.innerHeight - M - t2;
      if (h2 > 96) pop.style.maxHeight = Math.round(Math.min(pr.height, h2)) + 'px';
    }
  });
}

function qsPaint() {
  const pop = $('#bankSearchPop');
  if (!pop) return;
  if (!QS.rows.length) {
    pop.innerHTML = '<div class="qs-tip">没找到匹配的题目（可试更短的关键词）</div>';
  } else {
    pop.innerHTML = QS.rows.map((x, i) => {
      const q = (x.question || '').replace(/\s+/g, ' ');
      return '<div class="qs-row' + (i === QS.idx ? ' on' : '') + '" data-i="' + i + '">'
        + '<span class="qs-t">' + qEsc(x.qtype || '未分题型') + '</span>'
        + '<div class="qs-i"><div class="qs-q">' + qEsc(q.length > 92 ? q.slice(0, 92) + '…' : q) + '</div>'
        + '<div class="qs-m">' + qEsc(x.source || '') + ' · 第 ' + qEsc(String(x.qno)) + ' 题 · 命中' + qEsc(x.where || '题干') + '</div></div>'
        + '<span class="qs-e" data-i="' + i + '" title="进入该题编辑">编辑</span></div>';
    }).join('');
  }
  pop.hidden = false;
  QS.open = true;
  qsPlace();
}
async function qsSearch(kw) {
  const q = (kw || '').trim();
  if (!q) { qsHide(); return; }
  const seq = ++QS.seq;
  try {
    const r = await api('/api/bank/search?limit=20&q=' + encodeURIComponent(q));
    if (seq !== QS.seq) return;
    QS.rows = r.items || [];
    QS.idx = QS.rows.length ? 0 : -1;
    qsPaint();
  } catch (e) { /* 静默：搜索失败不打扰输入 */ }
}
async function qsLoadIntoSession(i) {
  const hit = QS.rows[i];
  if (!hit) return;
  try {
    const d = await api('/api/bank/load?file=' + encodeURIComponent(hit.file));
    const it = (d.items || [])[hit.index];
    if (!it) { toast('这道题已不存在，可能题库被改动过', true); return; }
    setVal($('#question'), it.question || '', true);
    setVal($('#ref'), it.ref || '', true);
    if (it.qtype) syncQtypeTo(it.qtype, true);
    qaFillFromItem(it);
    saveInfo();
    qsHide();
    if ($('#bankSearchInp')) $('#bankSearchInp').blur();
    toast('已载入当前会话 · ' + (hit.bank || '') + ' 第 ' + (hit.qno || '') + ' 题');
  } catch (e) { toast('载入失败：' + e.message, true); }
}
async function qsEditItem(i) {
  const hit = QS.rows[i];
  if (!hit) return;
  qsHide();
  try {
    await showBankItems(hit.file);
    openModal('#modalBank');
    /* 编辑入口复用题目解析弹层（覆盖本题），不再用 #modalItemEdit */
    qaOpenForBankItem(hit.index);
  } catch (e) { toast('打开失败：' + e.message, true); }
}

/* =====================================================================
   初始化绑定
   ===================================================================== */
function rzQsInit() {
  rzScan();
  let t = 0;
  const mo = new MutationObserver(() => { clearTimeout(t); t = setTimeout(rzScan, 120); });
  mo.observe(document.body, { childList: true, subtree: true });
  window.addEventListener('resize', () => setTimeout(rzScan, 80));

  const inp = $('#bankSearchInp'), pop = $('#bankSearchPop');
  if (inp) {
    inp.addEventListener('input', () => {
      clearTimeout(QS.t);
      const kw = inp.value;
      QS.t = setTimeout(() => qsSearch(kw), 180);
    });
    inp.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        if (!QS.rows.length) return;
        e.preventDefault();
        QS.idx = (QS.idx + (e.key === 'ArrowDown' ? 1 : -1) + QS.rows.length) % QS.rows.length;
        const rows = pop ? pop.querySelectorAll('.qs-row') : [];
        Array.prototype.forEach.call(rows, (el, i) => el.classList.toggle('on', i === QS.idx));
        const cur = rows[QS.idx];
        if (cur && cur.scrollIntoView) cur.scrollIntoView({ block: 'nearest' });
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (QS.rows.length) qsLoadIntoSession(QS.idx < 0 ? 0 : QS.idx);
      } else if (e.key === 'Escape') { qsHide(); inp.blur(); }
    });
    inp.addEventListener('focus', () => { if (inp.value.trim()) qsSearch(inp.value); });
  }
  if (pop) {
    pop.addEventListener('click', (e) => {
      const ed = e.target.closest ? e.target.closest('.qs-e') : null;
      if (ed) { e.stopPropagation(); qsEditItem(Number(ed.dataset.i)); return; }
      const row = e.target.closest ? e.target.closest('.qs-row') : null;
      if (row) qsLoadIntoSession(Number(row.dataset.i));
    });
  }
  document.addEventListener('pointerdown', (e) => {
    if (!QS.open) return;
    const w = $('#qsWrap');
    if (w && !w.contains(e.target) && pop && !pop.contains(e.target)) qsHide();
  });
  // 视口变化 / 页面滚动时重算浮层位置（fixed 定位，避免越界或压住输入框）
  const qsRefit = () => { if (QS.open) { try { qsPlace(); } catch (e) {} } };
  window.addEventListener('resize', qsRefit);
  window.addEventListener('orientationchange', qsRefit);
  window.addEventListener('scroll', qsRefit, true);
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', qsRefit);
    window.visualViewport.addEventListener('scroll', qsRefit);
  }

  /* 复制逐字稿：环节标题开关状态记忆 */
  const chk = $('#chkCopyStage');
  if (chk) {
    let v = '1';
    try { v = localStorage.getItem('copy_stage_head') || '1'; } catch (_) { }
    chk.checked = v === '1';
    chk.addEventListener('change', () => {
      try { localStorage.setItem('copy_stage_head', chk.checked ? '1' : '0'); } catch (_) { }
    });
  }

  /* 进度卡最小化 / 还原 + 失败题重试 */
  const minBtn = $('#ijMin'), badge = $('#impBadge');
  if (minBtn) minBtn.addEventListener('click', () => IMP_JOB.minimize());
  if (badge) badge.addEventListener('click', () => IMP_JOB.restore());
  const ipList = $('#ipList');
  if (ipList) {
    ipList.addEventListener('click', (e) => {
      const b = e.target.closest ? e.target.closest('.ip-retry') : null;
      if (!b) return;
      e.stopPropagation(); e.preventDefault();
      impRetry(b.dataset.id, b);
    });
  }

  /* 新增单题的题库下拉：选「新建」才显示名称框 */
  const qsel = $('#qaBankSel');
  if (qsel) {
    qsel.addEventListener('change', qaBankMode);
    if (!qsel.options.length) qaFillDatalist();
  }
  if (typeof loadBanks === 'function') { try { loadBanks(); } catch (_) { } }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', rzQsInit);
} else {
  rzQsInit();
}

/* ===== v2.5.3：启动后静默自检防火墙放行状态，未完全放行时当天只提示一次 ===== */
(function fwStartupCheck() {
  const today = new Date().toISOString().slice(0, 10);
  const key = 'ir_fw_tip_' + today;
  try { if (localStorage.getItem(key)) return; } catch (_) { return; }
  const go = () => {
    setTimeout(async () => {
      try {
        const r = await fetch('/api/system/firewall');
        const j = await r.json();
        if (!j || !j.ok) return;
        if (j.level === 'all' || j.level === 'unknown') return;
        toast('手机连不上？设置里有「一键放行防火墙」（' +
          (j.level === 'partial' ? '当前只部分网络放行' : '当前未放行') + '）');
        try { localStorage.setItem(key, '1'); } catch (_) { }
      } catch (_) { }
    }, 6000);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', go);
  else go();
})();

/* ---------------- 开源许可面板（第三方组件声明 / 合规） ---------------- */
(function ossPanel() {
  const modal = $('#modalOss');
  if (!modal) return;
  let DATA = null;
  const LIC_CACHE = {};
  let curSpdx = '';

  function render() {
    if (!DATA) return;
    const kw = ($('#ossFilter').value || '').trim().toLowerCase();
    const lic = $('#ossLicSel').value || '';
    const rows = DATA.items.filter((it) => {
      if (lic && it.license !== lic) return false;
      if (!kw) return true;
      return (it.name + ' ' + it.license + ' ' + it.use).toLowerCase().indexOf(kw) >= 0;
    });
    $('#ossBody').innerHTML = rows.length ? rows.map((it) => {
      const agpl = /AGPL/i.test(it.license);
      return '<tr' + (agpl ? ' class="oss-agpl"' : '') + '>' +
        '<td><b>' + esc(it.name) + '</b></td>' +
        '<td class="oss-ver">' + esc(it.ver) + '</td>' +
        '<td><span class="oss-lic-tag' + (agpl ? ' warn' : '') + '">' + esc(it.license) + '</span></td>' +
        '<td>' + esc(it.use) + '</td>' +
        '<td><a href="' + esc(it.url) + '" target="_blank" rel="noopener">↗</a></td>' +
        '</tr>';
    }).join('') : '<tr><td colspan="5" class="tip">没有匹配的组件</td></tr>';
  }

  async function load() {
    try {
      DATA = await api('/api/oss');
    } catch (e) {
      $('#ossHead').textContent = '读取组件清单失败：' + e.message;
      $('#ossBody').innerHTML = '<tr><td colspan="5" class="tip">读取失败：' + esc(e.message) + '</td></tr>';
      return;
    }
    const n = DATA.items.length;
    $('#ossHead').textContent = DATA.app + ' v' + DATA.version + '（' + DATA.author + '）共使用 ' +
      n + ' 个第三方开源组件，均遵循各自许可证分发；本清单与随包 THIRD_PARTY_NOTICES.md 保持一致。';
    const licSet = [];
    DATA.items.forEach((it) => { if (licSet.indexOf(it.license) < 0) licSet.push(it.license); });
    $('#ossLicSel').innerHTML = '<option value="">全部许可证（' + n + '）</option>' +
      licSet.map((l) => '<option value="' + esc(l) + '">' + esc(l) + '（' +
        DATA.items.filter((x) => x.license === l).length + '）</option>').join('');
    $('#ossLicBar').innerHTML = (DATA.licenses || []).map((l) =>
      '<button class="btn ghost oss-lic-btn" data-spdx="' + esc(l.spdx) + '" type="button">' +
      esc(l.spdx) + '</button>').join('') ||
      '<span class="tip">未找到 LICENSES 目录，许可证全文缺失</span>';
    render();
  }

  async function showLic(spdx) {
    const pre = $('#ossLicText');
    curSpdx = spdx;
    $$('#ossLicBar .oss-lic-btn').forEach((b) => {
      if (b.dataset.spdx === spdx) b.classList.add('on'); else b.classList.remove('on');
    });
    if (LIC_CACHE[spdx]) { pre.textContent = LIC_CACHE[spdx]; pre.scrollTop = 0; return; }
    pre.textContent = '正在读取 ' + spdx + ' 全文…';
    try {
      const j = await api('/api/oss/license/' + encodeURIComponent(spdx));
      LIC_CACHE[spdx] = j.text;
      if (curSpdx === spdx) { pre.textContent = j.text; pre.scrollTop = 0; }
    } catch (e) {
      if (curSpdx === spdx) pre.textContent = '读取失败：' + e.message;
    }
  }

  function open() {
    openModal('#modalOss');
    if (!DATA) load();
  }

  const btn = $('#btnOpenOss');
  if (btn) btn.addEventListener('click', open);
  $('#ossFilter').addEventListener('input', render);
  $('#ossLicSel').addEventListener('change', render);
  $('#ossLicBar').addEventListener('click', (e) => {
    const t = e.target.closest ? e.target.closest('.oss-lic-btn') : null;
    if (t) showLic(t.dataset.spdx);
  });
  const bd = $('#btnOssDir');
  if (bd) bd.addEventListener('click', async () => {
    try {
      const j = await api('/api/oss/open', {});
      toast('已打开声明目录：' + j.dir);
    } catch (e) {
      toast('打开失败：' + e.message);
    }
  });
})();
