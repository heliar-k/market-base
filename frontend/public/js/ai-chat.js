// ai-chat.js — 伴读抽屉（划词引用 + 页面上下文问答）
//
// ESM 模块（与 site-nav.js 同一套模式），在 TopicLayout.astro 与 SPA 壳里都紧跟
// rates-common.js、用 `type="module"` 加载：
//   - 提示词与引用块模板单源在 ./ai-prompt.js —— 调提示词只改那个文件；
//   - 本文件用 rates-common.js 的全局 R.esc，不自带一份实现（守卫见
//     tests/test_static_single_source.py）。
// 挂三样东西到 body 末尾（全是 position: fixed，不进 SPA 的 flex 流）：
//   .ai-float   右上角浮钮（原主题切换位，主题已改恒跟随系统）
//   .ai-sel-pop 划词气泡「问这段」
//   .ai-drawer  右侧抽屉（对话 + 引用胶囊 + 设置 + 历史）
// 数据全在本机 localStorage：四个键见下方 K_*，接口直连 OpenAI 兼容端点；
// Base URL 保持默认且没填 API Key 时走本站 /api/chat（本地 FastAPI / 线上 Pages Function）。

import { AI_PROMPT_VERSION, AI_SYSTEM_PROMPT, formatQuote } from './ai-prompt.js';
import {
  DEFAULT_CFG as DEF_CFG,
  chatPayload,
  effortLevels,
  isProxyMode,
  modelCacheMatches,
  normalizeBase,
  readCfgDraft,
  reconcileCfg,
} from './ai-config.js';
import { mdToHtml } from './ai-md.js';

(function () {
  'use strict';

  if (window.__aiChatInited) return;
  if (window.self !== window.top) return; // 内嵌 iframe 不重复挂一套
  if (typeof R === 'undefined' || typeof R.esc !== 'function') return; // 缺 rates-common
  window.__aiChatInited = true;

  const esc = R.esc;

  // ── 存储 ────────────────────────────────────────────────────────────────
  const K_CFG = 'guanlantai-ai-config';
  const K_THREADS = 'guanlantai-ai-threads';
  const K_ACTIVE = 'guanlantai-ai-active-thread';
  const K_MODELS = 'guanlantai-ai-model-cache';
  // localStorage 有 ~5MB 硬上限：对话不封顶迟早写不进（写失败整个历史静默丢）。
  const MAX_THREADS = 50;
  const MODEL_CACHE_TTL = 7 * 24 * 3600 * 1000;

  // 提示词正文单源在 ./ai-prompt.js，这里只保留运行时覆写位（控制台试新版，刷新恢复）
  let systemPromptOverride = '';
  const sysPrompt = () => systemPromptOverride || AI_SYSTEM_PROMPT;
  // 内存兜底：配额满或隐私模式禁写 localStorage 时，当前会话仍在内存里完整可用
  const mem = {};

  function loadJSON(key, fallback) {
    if (Object.prototype.hasOwnProperty.call(mem, key)) return mem[key];
    try {
      const v = JSON.parse(localStorage.getItem(key) || 'null');
      return v === null || v === undefined ? fallback : v;
    } catch (e) {
      return fallback;
    }
  }
  function saveJSON(key, val) {
    mem[key] = val;
    try {
      localStorage.setItem(key, JSON.stringify(val));
    } catch (e) {
      /* 配额满 / 隐私模式禁写：由 mem 兜底保持「本次会话可用，刷新即丢」，不打断对话 */
    }
  }
  const getCfg = () => Object.assign({}, DEF_CFG, loadJSON(K_CFG, {}) || {});
  const getThreads = () => {
    const t = loadJSON(K_THREADS, []);
    return Array.isArray(t) ? t.slice() : [];
  };
  function saveThreads(list) {
    list.sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
    saveJSON(K_THREADS, list.slice(0, MAX_THREADS));
  }
  // 模型表（含 reasoning_levels）缓存 7 天：刷新后仍知道该模型有哪些推理档位，
  // 否则设置面板只能显示「网关没报档位」，已存的 effort 会和实际发送值打架。
  function loadModelCache() {
    const c = loadJSON(K_MODELS, null);
    if (!c || !c.ts) return [];
    if (Date.now() - c.ts > MODEL_CACHE_TTL) return [];
    const cfg = getCfg();
    if (!modelCacheMatches(c, cfg.baseUrl, isProxyMode(cfg, DEF_CFG.baseUrl))) return [];
    return c.list;
  }
  function saveModelCache(list, base, proxy) {
    saveJSON(K_MODELS, {
      base: normalizeBase(base || getCfg().baseUrl),
      proxy: !!proxy,
      ts: Date.now(),
      list: Array.isArray(list) ? list : [],
    });
  }
  // 页面加载时就用缓存对账一次 effort：cache 过期/为空时清掉，别让配置里留着一个不发出去的值
  function normalizeSavedCfg() {
    const cfg = getCfg();
    const next = reconcileCfg(cfg, modelCache);
    if (next.effort !== cfg.effort) saveJSON(K_CFG, next);
  }
  const activeId = () => {
    if (Object.prototype.hasOwnProperty.call(mem, K_ACTIVE)) return mem[K_ACTIVE] || '';
    try {
      return localStorage.getItem(K_ACTIVE) || '';
    } catch (e) {
      return '';
    }
  };
  const setActiveId = (id) => {
    mem[K_ACTIVE] = id || '';
    try {
      localStorage.setItem(K_ACTIVE, id || '');
    } catch (e) {
      /* 同上 */
    }
  };

  // ── 页面上下文 ──────────────────────────────────────────────────────────
  // 划词片段与提问都要带「这数是哪页、几时的」：没有坐标的引用模型只能瞎猜口径。
  const SUMMARY_SELS = ['.sig-row', '.re-cards', '.re-kv-grid', '#tech-ov-current', '.re-sec-title'];

  function pageSummary() {
    const out = [];
    let len = 0;
    for (const sel of SUMMARY_SELS) {
      for (const el of document.querySelectorAll(sel)) {
        if (el.closest('.ai-drawer')) continue;
        const txt = (el.textContent || '').replace(/\s+/g, ' ').trim();
        if (!txt) continue;
        out.push(txt);
        len += txt.length;
        if (len >= 1500) return out.join(' | ').slice(0, 1500);
      }
    }
    return out.join(' | ').slice(0, 1500);
  }

  function getPageContext() {
    const pagePath = location.pathname + location.hash;
    const h1 = document.querySelector('.re-header h1');
    const pageTitle = (h1 && h1.textContent.trim()) || document.title || pagePath;
    let asOf = '';
    const asOfEl = document.getElementById('re-as-of');
    if (asOfEl) asOf = (asOfEl.textContent || '').trim();
    // SPA 技术视图没有页头时效槽：用「标的 · 最新价 · 口径」顶替坐标
    const sel = document.getElementById('tech-sym-select');
    if (sel) {
      const bits = [];
      if (sel.value) bits.push(sel.value);
      const price = document.getElementById('info-price');
      const p = price ? price.textContent.trim() : '';
      if (p && p !== '--') bits.push('最新价 ' + p);
      const rng = document.getElementById('status-range');
      if (rng && rng.textContent.trim()) bits.push(rng.textContent.trim());
      if (bits.length) asOf = bits.join(' · ');
    }
    return { pagePath, pageTitle, asOf, summary: pageSummary() };
  }

  // ── 划词：最近的小节标题 + 表格表头/本行 ─────────────────────────────────
  function sectionTitle(node) {
    // 就近的卡若无标题（JS 生成的表卡就是），逐层往上找那个真有标题的小节
    let el = node && (node.nodeType === 1 ? node : node.parentElement);
    for (let i = 0; i < 5 && el; i++) {
      el = el.closest('.re-sec, .re-section, .re-panel, .chart-card, section');
      if (!el) return '';
      const h = el.querySelector('.re-sec-title, h2, h3');
      if (h) return (h.textContent || '').replace(/\s+/g, ' ').trim();
      el = el.parentElement;
    }
    return '';
  }

  function tableContext(node) {
    const el = node && (node.nodeType === 1 ? node : node.parentElement);
    const tr = el && el.closest('tr');
    const table = tr && tr.closest('table');
    if (!table) return null;
    const head = table.querySelector('thead tr');
    return {
      tableHeaders: head ? Array.from(head.cells).map((c) => (c.textContent || '').trim()) : [],
      rowText: Array.from(tr.cells)
        .map((c) => (c.textContent || '').replace(/\s+/g, ' ').trim())
        .join(' | '),
    };
  }

  // ── 状态 ────────────────────────────────────────────────────────────────
  let pendingQuotes = [];
  let busy = false;
  let controller = null;
  let lastRange = null;
  let lastText = '';
  let histOnlyPage = false;
  let modelCache = loadModelCache(); // 「探测模型」拿到的网关模型表（含 reasoning_levels）
  let probeNote = '';

  let drawer, floatBtn, selPop, msgsEl, quotesEl, inputEl, sendBtn, cfgEl, histEl, histN, flashEl;
  let flashTimer = 0;

  const trunc = (s, n) => (String(s || '').length > n ? String(s).slice(0, n) + '…' : String(s || ''));
  const pad2 = (n) => (n < 10 ? '0' + n : '' + n);
  function fmtDay(ts) {
    const d = new Date(ts || Date.now());
    return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate());
  }
  const fmtFull = (ts) => new Date(ts || Date.now()).toLocaleString('zh-CN', { hour12: false });
  const scrollBottom = () => {
    if (msgsEl) msgsEl.scrollTop = msgsEl.scrollHeight;
  };

  // 保存/代理失败这类一次性结果走抽屉顶部细条，不塞进对话流里污染历史
  function flash(msg, isErr) {
    if (!flashEl) return;
    flashEl.textContent = msg;
    flashEl.classList.toggle('err', !!isErr);
    flashEl.removeAttribute('hidden');
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => flashEl.setAttribute('hidden', ''), isErr ? 9000 : 4000);
  }

  // ── 对话线程 ────────────────────────────────────────────────────────────
  function newThread() {
    const ctx = getPageContext();
    const t = {
      id: 't' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
      title: '新对话',
      createdAt: Date.now(),
      updatedAt: Date.now(),
      pagePath: ctx.pagePath,
      pageTitle: ctx.pageTitle,
      messages: [],
    };
    const list = getThreads();
    list.push(t);
    saveThreads(list);
    setActiveId(t.id);
    updateHistCount();
    return t;
  }
  const getActiveThread = () => {
    const id = activeId();
    return id ? getThreads().find((t) => t.id === id) || null : null;
  };
  function persist(thread, activate = true) {
    const list = getThreads();
    const i = list.findIndex((t) => t.id === thread.id);
    if (i >= 0) list[i] = thread;
    else if (activate) list.push(thread);
    else return; // 流式期间被用户删除的会话不再复活写回
    saveThreads(list);
    if (activate) setActiveId(thread.id);
    updateHistCount();
  }

  // ── 渲染：消息 / 引用胶囊 ────────────────────────────────────────────────
  // mdToHtml 单源在 ./ai-md.js（能直接过 node 测，见 tests/test_ai_md.py）

  function quoteHtml(q) {
    const head = esc(q.section || q.pageTitle || '引用') + (q.asOf ? ' · ' + esc(q.asOf) : '');
    let body = '';
    if (q.tableHeaders && q.tableHeaders.length) body += '<br>' + esc(q.tableHeaders.join(' | '));
    if (q.rowText) body += '<br>' + esc(q.rowText);
    if (q.text) body += '<br>「' + esc(trunc(q.text, 300)) + '」';
    return '<div class="ai-quote"><b>' + head + '</b>' + body + '</div>';
  }

  function emptyHtml() {
    return (
      '<div class="ai-empty">' +
      '<p>把本页的读数变成能追问的对话：</p>' +
      '<p>1）页面任意位置划词，点浮出的「💬 问这段」——片段连同小节名、表头与本行一起进入引用胶囊；</p>' +
      '<p>2）直接提问也会自动带上本页结论与时效坐标，不必手动贴数据；</p>' +
      '<p>3）本站配了代理（<code>/api/chat</code>）时直接提问即可；要换成自己的模型，点右上「⚙ 设置」填 OpenAI 兼容接口，点「探测模型」可把网关的模型与推理强度一起列出来。</p>' +
      '</div>'
    );
  }

  function renderMessages() {
    const t = getActiveThread();
    if (!msgsEl) return;
    if (!t || !t.messages.length) {
      msgsEl.innerHTML = emptyHtml();
      return;
    }
    msgsEl.innerHTML = t.messages
      .map((m, i) => {
        const isUser = m.role === 'user';
        const meta = [
          isUser ? '' : m.model ? '模型 ' + m.model : '',
          m.pageTitle,
          m.asOf,
        ]
          .filter(Boolean)
          .join(' · ');
        const quotes = (m.quotes || []).map(quoteHtml).join('');
        const body = isUser
          ? '<p>' + esc(m.content || '').replace(/\n/g, '<br>') + '</p>'
          : mdToHtml(m.content || '');
        // 提问可回填重发；答案只有最后一条给「重试」（中途的答案后面还有问答，重发没有意义）
        const acts = isUser
          ? '<button class="ai-btn" type="button" data-mact="edit" data-mi="' + i + '">编辑</button>'
          : i === t.messages.length - 1
            ? '<button class="ai-btn" type="button" data-mact="retry">重试</button>'
            : '';
        return (
          '<div class="ai-msg ' + (isUser ? 'ai-msg-user' : 'ai-msg-assistant') + '">' +
          (meta ? '<div class="ai-msg-meta">' + esc(meta) + '</div>' : '') +
          body +
          (quotes ? '<div class="ai-msg-quotes">' + quotes + '</div>' : '') +
          (acts ? '<div class="ai-acts ai-msg-acts">' + acts + '</div>' : '') +
          '</div>'
        );
      })
      .join('');
    scrollBottom();
  }

  function renderChips() {
    if (!quotesEl) return;
    quotesEl.innerHTML = pendingQuotes
      .map(
        (q) =>
          '<span class="ai-chip">' +
          '<span class="ai-chip-label">' + esc(trunc(q.section || q.pageTitle || '引用', 18)) + '</span>' +
          '<span class="ai-chip-text">' + esc(trunc(q.text, 42)) + '</span>' +
          '<button class="ai-chip-rm" type="button" data-rm="' + esc(q.id) + '" title="移除该引用">×</button>' +
          '</span>'
      )
      .join('');
  }

  // ── 抽屉开合 ────────────────────────────────────────────────────────────
  function open() {
    if (!drawer) return;
    drawer.classList.add('open');
    drawer.inert = false;
    drawer.setAttribute('aria-hidden', 'false');
    if (inputEl) inputEl.focus();
  }
  function close() {
    if (!drawer) return;
    drawer.classList.remove('open');
    drawer.inert = true; // 关着时 Tab 不能进去（aria-hidden 拦不住焦点）
    drawer.setAttribute('aria-hidden', 'true');
    hidePop();
  }
  const toggle = () => (drawer && drawer.classList.contains('open') ? close() : open());

  // ── 引用胶囊 ────────────────────────────────────────────────────────────
  function addQuote(q) {
    if (!q || !q.text) return;
    pendingQuotes.push(
      Object.assign({ id: 'q' + Date.now().toString(36) + Math.random().toString(36).slice(2, 5) }, q)
    );
    renderChips();
  }

  function hidePop() {
    if (selPop) selPop.classList.remove('show');
  }

  function onSelect() {
    if (!selPop) return;
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed) return hidePop();
    const text = String(sel.toString()).trim();
    if (text.length < 2 || text.length > 2000) return hidePop();
    let range;
    try {
      range = sel.getRangeAt(0);
    } catch (e) {
      return hidePop();
    }
    const node = range.commonAncestorContainer;
    const el = node.nodeType === 1 ? node : node.parentElement;
    if (!el) return hidePop();
    if (el.closest('.ai-drawer, .ai-sel-pop, input, textarea, [contenteditable="true"]')) return hidePop();
    lastRange = range;
    lastText = text;
    selPop.classList.add('show'); // 先显示才能量自身尺寸（靠上/靠下要翻边）
    const rect = range.getBoundingClientRect();
    const w = selPop.offsetWidth;
    const h = selPop.offsetHeight;
    const left = Math.min(Math.max(8, rect.left + rect.width / 2 - w / 2), window.innerWidth - w - 8);
    const top = rect.top - h - 6 < 8 ? rect.bottom + 6 : rect.top - h - 6;
    selPop.style.left = left + 'px';
    selPop.style.top = top + 'px';
  }

  function addQuoteFromSelection() {
    if (!lastRange || !lastText) return hidePop();
    const ctx = getPageContext();
    const node = lastRange.commonAncestorContainer;
    const q = {
      text: lastText,
      section: sectionTitle(node),
      pagePath: ctx.pagePath,
      pageTitle: ctx.pageTitle,
      asOf: ctx.asOf,
    };
    const tbl = tableContext(node);
    if (tbl) {
      q.tableHeaders = tbl.tableHeaders;
      q.rowText = tbl.rowText;
    }
    addQuote(q);
    hidePop();
    open();
  }

  // ── 请求 ────────────────────────────────────────────────────────────────
  // 引用块包装模板单源在 ./ai-prompt.js 的 formatQuote（改「引用怎么递给模型」只动那里）
  function quotePrefix(quotes) {
    if (!quotes || !quotes.length) return '';
    return quotes.map(formatQuote).join('\n\n') + '\n\n';
  }

  // thread 末尾那条 user 消息就是本轮要回答的（send / retry 都先把它压好）
  function buildMessages(thread) {
    const last = thread.messages[thread.messages.length - 1] || { content: '', quotes: [] };
    const ctx = getPageContext();
    // 提示词静态段单源在 SYSTEM_PROMPT；这里只拼接页面的动态坐标与读数
    const sys = [
      sysPrompt(),
      '',
      '【当前页面】',
      '标题：' + ctx.pageTitle,
      '路径：' + ctx.pagePath,
      ctx.asOf ? '时效：' + ctx.asOf : '',
      ctx.summary ? '结论与读数：' + ctx.summary : '',
    ]
      .filter(Boolean)
      .join('\n');
    const messages = [{ role: 'system', content: sys }];
    // 只带本轮之前的最近 11 条历史（slice(0, -1) 去掉末尾本轮，避免重复发送）
    thread.messages.slice(0, -1).slice(-11).forEach((m) =>
      messages.push({ role: m.role, content: quotePrefix(m.quotes) + (m.content || '') })
    );
    messages.push({ role: 'user', content: quotePrefix(last.quotes) + (last.content || '') });
    return messages;
  }

  // 把后端 {error} / FastAPI {detail} / 静态站非 JSON 响应统一成一句人话。
  // 404/405 只在「真的没有这个接口」时出现，别把探测 /models 的失败也说成 /api/chat。
  async function apiError(res, label) {
    let detail = '';
    try {
      const j = await res.json();
      if (j && typeof j.error === 'string') detail = j.error;
      else if (j && typeof j.detail === 'string') detail = j.detail;
      else if (j && j.detail) detail = JSON.stringify(j.detail);
    } catch (e) {
      /* 非 JSON（Pages 405 HTML / 代理错误页）：走下面的状态码兜底 */
    }
    if (detail) return detail;
    if (res.status === 404 || res.status === 405) {
      return (
        label +
        ' 接口不可用（' +
        res.status +
        '）。点右上「⚙ 设置」填 Base URL 与 API Key 直连 OpenAI 兼容接口。'
      );
    }
    return label + ' 返回 ' + res.status + (res.statusText ? ' ' + res.statusText : '');
  }

  function request(cfg, messages, signal) {
    const headers = { 'content-type': 'application/json' };
    if (cfg.apiKey) headers.authorization = 'Bearer ' + cfg.apiKey;
    const base = normalizeBase(cfg.baseUrl);
    // 没填 Key 且 Base URL 保持默认 → 本站代理；其余一律直连
    const proxy = isProxyMode(cfg, DEF_CFG.baseUrl);
    const url = proxy ? '/api/chat' : base + '/chat/completions';
    // 档位/模型规则单源在 ai-config.js 的 chatPayload（代理不传 effort）
    const body = chatPayload(cfg, messages, modelCache, proxy);
    return fetch(url, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
      signal,
    });
  }

  function sseDelta(line) {
    const t = line.trim();
    if (t.slice(0, 5) !== 'data:') return null;
    const payload = t.slice(5).trim();
    if (payload === '[DONE]') return '[DONE]';
    try {
      const j = JSON.parse(payload);
      const c = j.choices && j.choices[0];
      const d = c && (c.delta || c.message);
      return (d && d.content) || '';
    } catch (e) {
      return '';
    }
  }

  async function consume(res, onChunk) {
    const ct = res.headers.get('content-type') || '';
    if (ct.indexOf('event-stream') >= 0 && res.body && res.body.getReader) {
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      for (;;) {
        const r = await reader.read();
        if (r.done) break;
        buf += dec.decode(r.value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop() || '';
        let done = false;
        for (const line of lines) {
          const d = sseDelta(line);
          if (d === '[DONE]') {
            done = true;
            break;
          }
          if (d) onChunk(d);
        }
        if (done) {
          try {
            reader.cancel();
          } catch (e) {
            /* 已关闭 */
          }
          return;
        }
      }
      const tail = sseDelta(buf);
      if (tail && tail !== '[DONE]') onChunk(tail);
      return;
    }
    // 有代理会忽略 stream:true 直接回整包：同时兜住 SSE 文本与普通 JSON
    const raw = await res.text();
    if (raw.trim().slice(0, 5) === 'data:') {
      raw.split('\n').forEach((line) => {
        const d = sseDelta(line);
        if (d && d !== '[DONE]') onChunk(d);
      });
      return;
    }
    let j;
    try {
      j = JSON.parse(raw);
    } catch (e) {
      throw new Error('返回内容无法解析：' + raw.slice(0, 120));
    }
    const c = j.choices && j.choices[0];
    const d = c && (c.message || c.delta);
    if (!d || !d.content) {
      throw new Error('返回为空' + (j.error && j.error.message ? '：' + j.error.message : ''));
    }
    onChunk(d.content);
  }

  function setBusy(on) {
    busy = on;
    if (sendBtn) sendBtn.textContent = on ? '停止' : '发送';
  }

  function appendStream() {
    const el = document.createElement('div');
    el.className = 'ai-msg ai-msg-assistant';
    el.innerHTML = '<div class="ai-msg-meta">生成中…</div>';
    msgsEl.appendChild(el);
    scrollBottom();
    return el;
  }

  // 一轮问答：thread 末尾已是本轮要回答的 user 消息（send / retry 都先把末尾摆好）
  async function runTurn(thread) {
    const last = thread.messages[thread.messages.length - 1];
    if (!last || last.role !== 'user') return;
    const cfg = getCfg();
    controller = new AbortController();
    setBusy(true);
    const node = appendStream();
    let live = '';
    let answeredModel = '';
    try {
      const res = await request(cfg, buildMessages(thread), controller.signal);
      if (!res.ok) throw new Error(await apiError(res, '本站 /api/chat'));
      answeredModel = res.headers.get('x-llm-model') || cfg.model || '';
      await consume(res, (chunk) => {
        live += chunk;
        node.innerHTML = mdToHtml(live);
        scrollBottom();
      });
      if (!live.trim()) live = '（接口没有返回内容）';
    } catch (e) {
      const aborted = e && e.name === 'AbortError';
      const note = aborted ? '（已停止）' : '请求失败：' + (e && e.message ? e.message : e);
      live = (live ? live + '\n\n' : '') + note;
    } finally {
      controller = null;
      setBusy(false);
    }
    thread.messages.push({
      role: 'assistant',
      content: live,
      model: answeredModel,
      pagePath: last.pagePath,
      pageTitle: last.pageTitle,
      asOf: last.asOf,
      ts: Date.now(),
    });
    thread.updatedAt = Date.now();
    const stillActive = activeId() === thread.id;
    persist(thread, stillActive);
    if (stillActive) renderMessages();
  }

  async function send() {
    if (busy || !inputEl) return;
    const text = (inputEl.value || '').trim();
    if (!text) return;
    const thread = getActiveThread() || newThread();
    const ctx = getPageContext();
    const quotes = pendingQuotes.slice();
    if (!thread.messages.length) thread.title = trunc(text, 30);
    thread.messages.push({
      role: 'user',
      content: text,
      quotes,
      pagePath: ctx.pagePath,
      pageTitle: ctx.pageTitle,
      asOf: ctx.asOf,
      ts: Date.now(),
    });
    thread.updatedAt = Date.now();
    thread.pagePath = ctx.pagePath;
    thread.pageTitle = ctx.pageTitle;
    persist(thread);
    pendingQuotes = [];
    renderChips();
    inputEl.value = '';
    renderMessages();
    renderHist();
    await runTurn(thread);
  }

  // 重试：砍掉末尾答案，按当前设置重发同一条提问
  async function retry() {
    if (busy) return;
    const thread = getActiveThread();
    if (!thread || !thread.messages.length) return;
    if (thread.messages[thread.messages.length - 1].role === 'assistant') thread.messages.pop();
    if (!thread.messages.length || thread.messages[thread.messages.length - 1].role !== 'user') return;
    thread.updatedAt = Date.now();
    persist(thread);
    renderMessages();
    await runTurn(thread);
  }

  // 改已发出的提问：该条及其之后全部作废，文本与引用回填到输入框/胶囊，改完自己再发
  function editAt(i) {
    if (busy) return;
    const thread = getActiveThread();
    const m = thread && thread.messages[i];
    if (!m || m.role !== 'user') return;
    thread.messages.length = i;
    thread.updatedAt = Date.now();
    pendingQuotes = (m.quotes || []).slice();
    persist(thread);
    renderChips();
    renderMessages();
    renderHist();
    if (inputEl) {
      inputEl.value = m.content || '';
      inputEl.focus();
    }
  }

  // ── 历史 / 设置 / 导出 ──────────────────────────────────────────────────
  function updateHistCount() {
    if (histN) histN.textContent = String(getThreads().length);
  }

  function renderHist() {
    if (!histEl) return;
    const all = getThreads();
    const cur = activeId();
    const path = getPageContext().pagePath;
    const list = histOnlyPage ? all.filter((t) => t.pagePath === path) : all;
    const btn = (on, act, label) =>
      '<button class="ai-btn' + (on ? ' on' : '') + '" type="button" data-' + act + '>' + label + '</button>';
    histEl.innerHTML =
      '<div class="ai-acts" style="margin-bottom:6px">' +
      btn(!histOnlyPage, 'filter="all"', '全部') +
      btn(histOnlyPage, 'filter="page"', '本页') +
      '</div>' +
      (list.length
        ? list
            .map(
              (t) =>
                '<div class="ai-hist-item' + (t.id === cur ? ' on' : '') + '" data-tid="' + esc(t.id) + '">' +
                '<span class="ai-hist-title">' + esc(t.title || '新对话') + '</span>' +
                '<span class="ai-hist-meta">' + esc(fmtDay(t.updatedAt)) + ' · ' + ((t.messages || []).length) + '</span>' +
                '<button class="ai-hist-rm" type="button" data-rmtid="' + esc(t.id) + '" title="删除该对话">×</button>' +
                '</div>'
            )
            .join('')
        : '<div class="ai-empty">还没有对话。划词或直接提问就开始一条。</div>');
  }

  // 探测结果里这个模型支持的推理强度档位 —— 单源在 ai-config.js 的 effortLevels
  // （纯函数，node 直接可测；这里不再留第二份实现）

  function renderCfg(draft, persist = true) {
    if (!cfgEl) return;
    const source = draft || getCfg();
    const c = reconcileCfg(source, modelCache);
    // 打开设置时把「已保存但当前模型不认」的 effort 清掉并落盘：
    // 否则面板显示「默认」、请求却还带着它（见 ai-config.js 的 reconcileCfg）
    if (persist && c.effort !== source.effort) saveJSON(K_CFG, c);
    const proxy = isProxyMode(c, DEF_CFG.baseUrl);
    if (proxy && c.effort) c.effort = ''; // 代理模式不传档位，面板别显示一个其实不发的值
    const ids = modelCache.map((m) => m.id);
    // 手填过、但网关没报的旧值也留着，不静默换模型
    if (c.model && ids.indexOf(c.model) < 0) ids.unshift(c.model);
    const opt = (v, label, cur) =>
      '<option value="' + esc(v) + '"' + (v === cur ? ' selected' : '') + '>' + esc(label || v) + '</option>';
    const modelOpts = ids.length
      ? ids
          .map((id) => {
            const m = modelCache.find((x) => x.id === id) || {};
            return opt(id, m.display_name ? m.display_name + ' · ' + id : id, c.model);
          })
          .join('')
      : opt(c.model, c.model, c.model);
    const levels = effortLevels(modelCache, c.model);
    const effortHint = proxy
      ? '（本站代理固定）'
      : levels.length
        ? ''
        : '（网关没报这个模型的档位）';
    cfgEl.innerHTML =
      '<div class="ai-row"><label>Base URL</label>' +
      '<input type="text" spellcheck="false" data-k="baseUrl" value="' + esc(c.baseUrl) + '"></div>' +
      '<div class="ai-row"><label>API Key</label>' +
      '<input type="password" spellcheck="false" autocomplete="off" placeholder="sk-…" data-k="apiKey" value="' + esc(c.apiKey) + '"></div>' +
      '<div class="ai-row"><label>Model</label>' +
      '<div class="ai-cfg-pick"><select data-k="model">' + modelOpts + '</select>' +
      '<button class="ai-btn" type="button" data-probe="1">探测模型</button></div></div>' +
      '<div class="ai-row"><label>推理强度' + effortHint + '</label>' +
      '<select data-k="effort"' + (proxy || !levels.length ? ' disabled' : '') + '>' +
      opt('', '默认（不传 reasoning_effort）', c.effort) +
      levels.map((lv) => opt(lv, lv, c.effort)).join('') +
      '</select></div>' +
      (probeNote ? '<p class="ai-hint">' + esc(probeNote) + '</p>' : '') +
      (proxy
        ? '<p class="ai-hint"><b>本站代理</b>：未填 Key 时请求走 <code>/api/chat</code>，模型以服务端白名单为准（下方 Model 可能被覆盖）；推理强度只对直连生效。</p>'
        : '') +
      '<p class="ai-hint">Base URL 填到 <code>/v1</code> 为止（代码自己拼 <code>/chat/completions</code>）；留空 Key 且 Base URL 保持默认时走本站 <code>/api/chat</code> 代理（需服务端配了 LLM_API_KEY），填了 Key 就直连。Key 只存本机 localStorage。</p>' +
      '<div class="ai-acts"><button class="ai-btn" type="button" data-cfgsave="1">保存</button>' +
      '<button class="ai-btn" type="button" data-cfgclose="1">收起</button></div>';
  }

  // 从网关（或本站代理）把模型表拉回来（失败给一句人话，不静默）
  async function probeModels() {
    // 先读草稿：探测会重渲染面板，不能从「已保存配置」重建，否则没保存的 Base/Key 被清空
    const draft = readCfgDraft(cfgEl, getCfg());
    const base = normalizeBase(draft.baseUrl || DEF_CFG.baseUrl);
    const key = String(draft.apiKey || '').trim();
    const proxy = isProxyMode(draft, DEF_CFG.baseUrl);
    probeNote = '探测中…';
    renderCfg(draft, false);
    try {
      const res = await fetch(proxy ? '/api/models' : base + '/models', {
        headers: key ? { authorization: 'Bearer ' + key } : {},
      });
      if (!res.ok) throw new Error(await apiError(res, '模型列表'));
      const j = await res.json();
      const list = (Array.isArray(j.data) ? j.data : []).filter((m) => m && m.id);
      if (!list.length) throw new Error('网关没有返回模型列表');
      modelCache = list;
      saveModelCache(list, base, proxy);
      probeNote = proxy
        ? '本站代理：' + list.length + ' 个模型（由服务端决定）'
        : '探测到 ' + list.length + ' 个模型 · ' + base;
    } catch (e) {
      probeNote = '探测失败：' + (e && e.message ? e.message : e);
    }
    renderCfg(draft, false);
  }

  function toMarkdown(t) {
    if (!t) return '';
    const out = ['# ' + (t.title || '伴读对话'), ''];
    out.push('- 创建：' + fmtFull(t.createdAt), '- 更新：' + fmtFull(t.updatedAt));
    if (t.pagePath) out.push('- 起页：' + t.pagePath);
    out.push('');
    (t.messages || []).forEach((m) => {
      out.push('## ' + (m.role === 'user' ? '我' : '伴读'));
      const meta = [m.model ? '模型 ' + m.model : '', m.pageTitle, m.asOf]
        .filter(Boolean)
        .join(' · ');
      if (meta) out.push('> ' + meta);
      (m.quotes || []).forEach((q) => {
        out.push('', '> 引用 · ' + (q.section || q.pageTitle || '') + (q.asOf ? ' · ' + q.asOf : ''));
        if (q.tableHeaders && q.tableHeaders.length) out.push('> 表头：' + q.tableHeaders.join(' | '));
        if (q.rowText) out.push('> 本行：' + q.rowText);
        out.push('>');
        String(q.text || '').split('\n').forEach((l) => out.push('> ' + l));
      });
      out.push('', m.content || '', '');
    });
    return out.join('\n');
  }

  const fileName = (t) =>
    fmtDay(t.updatedAt || Date.now()) +
    '_' +
    String(t.title || '伴读对话').replace(/[\\/:*?"<>|\s]+/g, '-').slice(0, 40) +
    '.md';

  function download(name, text) {
    const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown;charset=utf-8' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    // 立刻 revoke 会让部分浏览器取不到 blob：下一轮事件循环再收
    setTimeout(() => {
      URL.revokeObjectURL(url);
      a.remove();
    }, 0);
  }

  // 把当前对话落盘：本地 FastAPI 写 data/notes/，线上 Pages Function 落 NOTES KV；
  // 静态站没绑 KV 时返回 503，apiError 会把「请本地运行 / 导出 .md」原样带出来。
  async function saveToServer(t) {
    const thread = t || getActiveThread();
    if (!thread) throw new Error('没有可保存的对话');
    const res = await fetch('/api/notes', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ filename: fileName(thread), markdown: toMarkdown(thread), thread }),
    });
    if (!res.ok) throw new Error(await apiError(res, '保存接口'));
    return res.json();
  }

  async function saveActiveThread() {
    const thread = getActiveThread();
    if (!thread) return flash('还没有对话可保存', true);
    const btn = drawer && drawer.querySelector('[data-act="save"]');
    if (btn) {
      btn.disabled = true;
      btn.textContent = '保存中…';
    }
    try {
      const r = await saveToServer(thread);
      flash('已保存：' + (r.path || r.key || 'OK'));
    } catch (e) {
      flash('保存失败：' + (e && e.message ? e.message : e), true);
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = '存笔记';
      }
    }
  }

  // ── DOM ─────────────────────────────────────────────────────────────────
  const SHELL =
    '<button class="ai-float" type="button" title="伴读问答（⌘/Ctrl + K）" aria-label="伴读问答">💬</button>' +
    '<button class="ai-sel-pop" type="button">💬 问这段</button>' +
    '<div class="ai-drawer" role="dialog" aria-label="伴读问答" aria-hidden="true">' +
    '<div class="ai-head">' +
    '<span class="ai-title">伴读问答</span>' +
    '<button class="ai-btn" type="button" data-act="new">＋ 新对话</button>' +
    '<button class="ai-btn" type="button" data-act="hist">历史 <span data-hist-n>0</span></button>' +
    '<button class="ai-btn" type="button" data-act="export" title="导出为 Markdown（.md）">导出</button>' +
    '<button class="ai-btn" type="button" data-act="save" title="保存到 data/notes/（本地）或 NOTES KV（线上）">存笔记</button>' +
    '<button class="ai-btn" type="button" data-act="cfg">⚙ 设置</button>' +
    '<button class="ai-btn" type="button" data-act="close" title="关闭（Esc）">✕</button>' +
    '</div>' +
    '<div class="ai-flash" data-flash role="status" hidden></div>' +
    '<div class="ai-cfg" data-cfg hidden></div>' +
    '<div class="ai-hist" data-hist hidden></div>' +
    '<div class="ai-msgs" data-msgs></div>' +
    '<div class="ai-quotes" data-quotes></div>' +
    '<div class="ai-foot">' +
    '<textarea class="ai-input" rows="2" placeholder="就本页数据提问…"></textarea>' +
    '<div class="ai-foot-row"><span class="ai-hint">Enter 发送 · Shift+Enter 换行</span>' +
    '<button class="ai-send" type="button" data-send>发送</button></div>' +
    '</div>' +
    '</div>';

  function panel(el, openIt) {
    if (!el) return;
    if (openIt) el.removeAttribute('hidden');
    else el.setAttribute('hidden', '');
  }

  function init() {
    document.body.insertAdjacentHTML('beforeend', SHELL);
    floatBtn = document.querySelector('.ai-float');
    selPop = document.querySelector('.ai-sel-pop');
    drawer = document.querySelector('.ai-drawer');
    msgsEl = drawer.querySelector('[data-msgs]');
    quotesEl = drawer.querySelector('[data-quotes]');
    inputEl = drawer.querySelector('.ai-input');
    sendBtn = drawer.querySelector('[data-send]');
    cfgEl = drawer.querySelector('[data-cfg]');
    histEl = drawer.querySelector('[data-hist]');
    histN = drawer.querySelector('[data-hist-n]');
    flashEl = drawer.querySelector('[data-flash]');

    floatBtn.addEventListener('click', toggle);

    // mousedown + preventDefault：否则点击瞬间选区被浏览器清掉，「问这段」拿不到 range
    selPop.addEventListener('mousedown', (e) => {
      e.preventDefault();
      addQuoteFromSelection();
    });

    document.addEventListener('mouseup', (e) => {
      if (e.target && e.target.closest && e.target.closest('.ai-drawer, .ai-sel-pop')) return;
      setTimeout(onSelect, 0); // 等浏览器把选区落定
    });
    document.addEventListener('keyup', (e) => {
      if (e.key === 'Escape') return;
      setTimeout(onSelect, 0); // 键盘选区（Shift+箭头 / 三连击）同样弹气泡，无选区时立即早退
    });
    document.addEventListener('mousedown', (e) => {
      if (!e.target.closest || !e.target.closest('.ai-sel-pop')) hidePop();
    });

    drawer.addEventListener('click', (e) => {
      const t = e.target;
      const act = t.closest('[data-act]');
      if (act) {
        const a = act.dataset.act;
        if (a === 'close') return close();
        if (a === 'new') {
          if (controller) controller.abort();
          newThread();
          renderMessages();
          renderHist();
          if (inputEl) inputEl.focus();
          return;
        }
        if (a === 'hist') {
          renderHist();
          panel(histEl, histEl.hasAttribute('hidden'));
          panel(cfgEl, false);
          return;
        }
        if (a === 'cfg') {
          renderCfg();
          panel(cfgEl, cfgEl.hasAttribute('hidden'));
          panel(histEl, false);
          return;
        }
        if (a === 'export') {
          const thread = getActiveThread();
          if (!thread) return;
          download(fileName(thread), toMarkdown(thread));
          return;
        }
        if (a === 'save') {
          saveActiveThread();
          return;
        }
      }
      const mact = t.closest('[data-mact]');
      if (mact) {
        if (mact.dataset.mact === 'retry') retry();
        else editAt(Number(mact.dataset.mi));
        return;
      }
      if (t.closest('[data-probe]')) return probeModels();
      const rm = t.closest('[data-rm]');
      if (rm) {
        pendingQuotes = pendingQuotes.filter((q) => q.id !== rm.dataset.rm);
        renderChips();
        return;
      }
      const cfgsave = t.closest('[data-cfgsave]');
      if (cfgsave) {
        const prev = getCfg();
        const prevBase = normalizeBase(prev.baseUrl);
        const wasProxy = isProxyMode(prev, DEF_CFG.baseUrl);
        const cfg = getCfg();
        cfgEl.querySelectorAll('[data-k]').forEach((el) => {
          cfg[el.dataset.k] = String(el.value || '').trim();
        });
        if (!cfg.baseUrl) cfg.baseUrl = DEF_CFG.baseUrl;
        if (!cfg.model) cfg.model = DEF_CFG.model;
        let next = reconcileCfg(cfg, modelCache); // 该模型没这个档位就别留着
        saveJSON(K_CFG, next);
        const nowProxy = isProxyMode(next, DEF_CFG.baseUrl);
        if (prevBase !== normalizeBase(next.baseUrl) || wasProxy !== nowProxy) {
          // 换网关或切代理/直连：旧模型表不再适用，换成新模式的缓存（没有就空），
          // 再清一次失效 effort——否则代理白名单会串到 BYOK 的 Model 下拉里
          modelCache = loadModelCache();
          const fixed = reconcileCfg(next, modelCache);
          if (fixed.effort !== next.effort) {
            next = fixed;
            saveJSON(K_CFG, next);
          }
        }
        panel(cfgEl, false);
        return;
      }
      if (t.closest('[data-cfgclose]')) return panel(cfgEl, false);
      const filter = t.closest('[data-filter]');
      if (filter) {
        histOnlyPage = filter.dataset.filter === 'page';
        renderHist();
        return;
      }
      const rmtid = t.closest('[data-rmtid]');
      if (rmtid) {
        const id = rmtid.dataset.rmtid;
        if (activeId() === id && controller) controller.abort();
        saveThreads(getThreads().filter((x) => x.id !== id));
        if (activeId() === id) setActiveId('');
        updateHistCount();
        renderHist();
        renderMessages();
        return;
      }
      const item = t.closest('[data-tid]');
      if (item) {
        if (activeId() !== item.dataset.tid && controller) controller.abort();
        setActiveId(item.dataset.tid);
        renderHist();
        renderMessages();
      }
    });

    // 下拉选择立即落盘（保存按钮只管文本输入）；换模型时推理强度档位跟着刷新
    cfgEl.addEventListener('change', (e) => {
      const sel = e.target.closest && e.target.closest('select[data-k]');
      if (!sel) return;
      if (sel.dataset.k === 'model') {
        const next = getCfg();
        next.model = sel.value;
        const lv = effortLevels(modelCache, sel.value);
        if (next.effort && lv.indexOf(next.effort) < 0) next.effort = ''; // 新模型没这档
        saveJSON(K_CFG, next);
        // 重渲染必须带草稿：否则没保存的 Base URL / Key 会被已保存配置顶掉
        const draft = readCfgDraft(cfgEl, next);
        draft.model = sel.value;
        draft.effort = next.effort;
        probeNote = '';
        renderCfg(draft, false);
        return;
      }
      const next = getCfg();
      next[sel.dataset.k] = sel.value;
      saveJSON(K_CFG, next);
    });

    sendBtn.addEventListener('click', () => {
      if (busy) {
        if (controller) controller.abort();
        return;
      }
      send();
    });
    inputEl.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        send();
      }
    });

    document.addEventListener('keydown', (e) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        toggle();
        return;
      }
      if (e.key === 'Escape' && drawer.classList.contains('open')) close();
    });

    normalizeSavedCfg();
    updateHistCount();
    renderChips();
    renderMessages();
    drawer.inert = true; // 初始关着
  }

  // 测试与后续扩展的挂点（saveToServer 是 data/ 持久化的预留入口）
  window.__aiChat = {
    open,
    close,
    toggle,
    addQuote,
    getPageContext,
    toMarkdown,
    saveToServer,
    // 提示词可读可覆写：控制台里 __aiChat.systemPrompt = '…' 即可试新版本，刷新恢复
    get systemPrompt() {
      return sysPrompt();
    },
    set systemPrompt(v) {
      if (typeof v === 'string' && v.trim()) systemPromptOverride = v;
    },
    promptVersion: AI_PROMPT_VERSION,
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
