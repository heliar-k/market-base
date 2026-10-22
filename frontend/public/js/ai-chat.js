// ai-chat.js — 伴读抽屉（划词引用 + 页面上下文问答）
//
// ESM 模块（与 site-nav.js 同一套模式），在 TopicLayout.astro 与 SPA 壳里都紧跟
// rates-common.js、用 `type="module"` 加载：
//   - 提示词与引用块模板单源在 ./ai-prompt.js —— 调提示词只改那个文件；
//   - 本文件用 rates-common.js 的全局 R.esc，不自带一份实现（守卫见
//     tests/test_static_single_source.py）。
// 挂三样东西到 body 末尾（全是 position: fixed，不进 SPA 的 flex 流）：
//   .ai-float   浮钮：桌面在右上角（原主题切换位）；窄屏挪到右下角 44px FAB
//               —— 页头右上角被「数据截至」时效块占着，26px 小钮贴上去会打架
//   .ai-sel-pop 划词气泡「问这段」（触屏补了 touchend / selectionchange）
//   .ai-drawer  右侧抽屉：>768 是 380px 侧栏（非模态），≤768 整屏模态
//               （锁背景滚动 / 焦点陷阱 / 接管返回键；头部低频项收进「⋯」）
// 数据全在本机 localStorage：四个键见下方 K_*，接口直连 OpenAI 兼容端点；
// Base URL 保持默认且没填 API Key 时走本站 /api/chat（本地 FastAPI / 线上 Pages Function）。

import { AI_CONTEXT_LABELS, AI_FACTS_HINT, AI_PROMPT_VERSION, AI_SYSTEM_PROMPT, formatQuote } from './ai-prompt.js';
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
  //
  // 结构化数据走 ./ai-context.js 的 AICtx（页面自己声明事实集，矩阵/表格由页面编码）；
  // 这里只兜底「DOM 长什么样」这一段，给模型一张小节地图与页面上已经写死的结论。
  // 两个坑（都实测过）：① 必须跳过不可见元素——SPA 双视图时技术视图是 display:none
  // 却仍在 DOM 里，曾吃掉 1500 字预算的 1119 字，模型拿 BTC 的 RSI 去答相关性；
  // ② 预算别给太大，事实集已经占了主预算，这里只补结论层。
  const SUMMARY_SELS = ['.sig-row', '.re-cards', '.re-kv-grid', '#tech-ov-current', '#corr-insights', '.re-sec-title'];
  const SUMMARY_BUDGET = 1500; // 没声明事实集的页（多数页）保持原量，别因为别处加了上下文就偷偷截短
  const SUMMARY_BUDGET_WITH_FACTS = 1200; // 有结构化事实集的页让出一档给它

  // 可见性判定单源在 ai-context.js（与 pageSummary 共用一份）；脚本没到位时退回「全算可见」
  const isVisible = (el) =>
    window.AICtx && typeof AICtx.isVisible === 'function' ? AICtx.isVisible(el) : true;

  function pageSummary(budget = SUMMARY_BUDGET) {
    const out = [];
    let len = 0;
    for (const sel of SUMMARY_SELS) {
      for (const el of document.querySelectorAll(sel)) {
        if (el.closest('.ai-drawer')) continue;
        if (!isVisible(el)) continue; // 别的视图 / 折叠面板里的读数一律不送
        const txt = (el.textContent || '').replace(/\s+/g, ' ').trim();
        if (!txt) continue;
        out.push(txt);
        len += txt.length;
        if (len >= budget) return out.join(' | ').slice(0, budget);
      }
    }
    return out.join(' | ').slice(0, budget);
  }

  // 页面事实集（单源在 ./ai-context.js；未加载或被 CSP 拦住时安静降级成空串）
  function pageFacts() {
    try {
      if (window.AICtx && typeof window.AICtx.render === 'function') return window.AICtx.render(); // 预算单源在 ai-context.js
    } catch (e) {
      /* 页面 provider 出错不该拖垮对话 */
    }
    return '';
  }

  function getPageContext(summaryBudget = SUMMARY_BUDGET) {
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
    return { pagePath, pageTitle, asOf, summary: pageSummary(summaryBudget) };
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
  let menuEl, moreBtn;
  let flashTimer = 0;
  // 窄屏 = 全屏模态（锁滚动 / 焦点陷阱 / 接管返回键）；桌面侧栏保持非模态
  let stickBottom = true; // 消息流是否贴底（往上翻读时流式输出不再抢滚动）
  let locked = false;
  let lockY = 0;
  let drawerPushed = false; // 打开时压了一个 history state，返回键/关闭时弹掉
  const isNarrow = () => {
    try {
      return window.matchMedia('(max-width: 768px)').matches;
    } catch (e) {
      return false;
    }
  };
  const isCoarse = () => {
    try {
      return window.matchMedia('(pointer: coarse)').matches;
    } catch (e) {
      return false;
    }
  };

  const trunc = (s, n) => (String(s || '').length > n ? String(s).slice(0, n) + '…' : String(s || ''));
  const pad2 = (n) => (n < 10 ? '0' + n : '' + n);
  function fmtDay(ts) {
    const d = new Date(ts || Date.now());
    return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate());
  }
  const fmtFull = (ts) => new Date(ts || Date.now()).toLocaleString('zh-CN', { hour12: false });
  // 贴底才滚：用户往上翻读历史/答案时，流式 chunk 不再把人拽回底部
  const atBottom = () =>
    !msgsEl || msgsEl.scrollHeight - msgsEl.scrollTop - msgsEl.clientHeight < 48;
  const scrollBottom = (force) => {
    if (!msgsEl || (!force && !stickBottom)) return;
    msgsEl.scrollTop = msgsEl.scrollHeight;
    stickBottom = true;
  };

  // 保存/代理失败这类一次性结果走抽屉顶部细条，不塞进对话流里污染历史。
  // sticky：进行中状态（如「保存中…」）不自动收，等结果来顶掉
  function flash(msg, isErr, sticky) {
    if (!flashEl) return;
    flashEl.textContent = msg;
    flashEl.classList.toggle('err', !!isErr);
    flashEl.removeAttribute('hidden');
    clearTimeout(flashTimer);
    if (!sticky) flashTimer = setTimeout(() => flashEl.setAttribute('hidden', ''), isErr ? 9000 : 4000);
  }

  // 复制答案：手机上手动拖选 Markdown 很痛苦，给一键复制（非安全上下文回退 execCommand）
  async function copyText(text) {
    const s = String(text || '');
    if (!s) return;
    try {
      await navigator.clipboard.writeText(s);
      flash('已复制到剪贴板');
      return;
    } catch (e) {
      /* 没权限 / http 环境：走下面的回退 */
    }
    const ta = document.createElement('textarea');
    ta.value = s;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.top = '-1000px';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try {
      ok = document.execCommand('copy');
    } catch (e) {
      ok = false;
    }
    ta.remove();
    flash(ok ? '已复制到剪贴板' : '复制失败，请手动选择', !ok);
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
    // 窄屏也读得完的两段；「划词」在触屏上是长按选词，措辞不写死在鼠标上
    return (
      '<div class="ai-empty">' +
      '<p>直接问，或选中本页任意片段（会带上小节名与表头），提问会自动附上本页结论与时效坐标。</p>' +
      '<p>想换模型：点右上「设置」填 OpenAI 兼容接口；窄屏在「⋯」里。</p>' +
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
        // 提问可回填重发；答案给「复制」（手机上手动选 Markdown 太费劲），
        // 只有最后一条给「重试」（中途的答案后面还有问答，重发没有意义）
        let acts = '';
        if (isUser) {
          acts = '<button class="ai-btn" type="button" data-mact="edit" data-mi="' + i + '">编辑</button>';
        } else {
          acts = '<button class="ai-btn" type="button" data-mact="copy" data-mi="' + i + '">复制</button>';
          if (i === t.messages.length - 1) {
            acts += '<button class="ai-btn" type="button" data-mact="retry">重试</button>';
          }
        }
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
  function showMenu(on) {
    if (!menuEl) return;
    if (on) menuEl.removeAttribute('hidden');
    else menuEl.setAttribute('hidden', '');
    if (moreBtn) moreBtn.setAttribute('aria-expanded', on ? 'true' : 'false');
  }
  function hideMenu() {
    showMenu(false);
  }
  const menuOpen = () => !!(menuEl && !menuEl.hasAttribute('hidden'));

  // 键盘弹出后 visual viewport 比 layout viewport 矮：把抽屉钉在可见区，
  // 否则 fixed 底栏（输入框 + 发送）会落在键盘后面（iOS Safari 不认 interactive-widget）
  function syncDrawerToViewport() {
    if (!drawer || !drawer.classList.contains('open')) return;
    const vv = window.visualViewport;
    if (!vv) return;
    if (window.innerHeight - vv.height > 80) {
      drawer.style.top = Math.max(0, vv.offsetTop) + 'px';
      drawer.style.height = vv.height + 'px';
    } else {
      drawer.style.top = '';
      drawer.style.height = '';
    }
    // 可见高度不足时（键盘弹出 / 横屏手机）关掉设置面板的吸底按钮：
    // 48px 的吸底行会把只剩 ~80px 的面板再啃掉一半，输入框反而看不见
    const short = drawer.getBoundingClientRect().height < 480;
    drawer.classList.toggle('ai-kb', short);
  }

  // 整屏模态时锁背景滚动；iOS 的 body overflow:hidden 拦不住，用 position:fixed + 负 top 定住
  function lockScroll() {
    if (locked) return;
    locked = true;
    lockY = window.scrollY || document.documentElement.scrollTop || 0;
    document.documentElement.classList.add('ai-lock');
    document.body.style.position = 'fixed';
    document.body.style.top = -lockY + 'px';
    document.body.style.left = '0';
    document.body.style.right = '0';
    document.body.style.width = '100%';
  }
  function unlockScroll() {
    if (!locked) return;
    locked = false;
    document.body.style.position = '';
    document.body.style.top = '';
    document.body.style.left = '';
    document.body.style.right = '';
    document.body.style.width = '';
    document.documentElement.classList.remove('ai-lock');
    window.scrollTo(0, lockY);
  }

  // 接管 Android 返回键：压一条同 URL 的 history，返回时只关抽屉不离开页面
  function pushDrawerState() {
    if (drawerPushed) return;
    try {
      history.pushState({ aiDrawer: 1 }, '');
      drawerPushed = true;
    } catch (e) {
      drawerPushed = false;
    }
  }

  // 模态与否只由宽度决定，但宽度会在抽屉开着时变（转屏 / 拉窗）。
  // 不跟着改的话：≤768 开→拉宽，body 一直 fixed、页面锁死；>768 开→缩窄，
  // 全屏抽屉却没有锁滚/焦点陷阱。mql 的 change 在 init 里挂。
  function syncModalMode() {
    if (!drawer || !drawer.classList.contains('open')) return;
    if (isNarrow()) {
      if (drawer.getAttribute('aria-modal') === 'true') return;
      drawer.setAttribute('aria-modal', 'true');
      lockScroll();
      pushDrawerState();
    } else {
      drawer.removeAttribute('aria-modal');
      unlockScroll();
    }
  }

  function open() {
    if (!drawer) return;
    drawer.classList.add('open');
    drawer.inert = false;
    drawer.setAttribute('aria-hidden', 'false');
    if (isNarrow()) {
      drawer.setAttribute('aria-modal', 'true');
      lockScroll();
      pushDrawerState();
    }
    // 触屏不自动聚焦输入框（一开就弹键盘会把说明/历史顶掉一半）；
    // 焦点落到抽屉本身（tabindex=-1，不会弹键盘），读屏才知道对话框开了
    if (!isCoarse() && inputEl) inputEl.focus();
    else if (isNarrow()) drawer.focus({ preventScroll: true });
    requestAnimationFrame(syncDrawerToViewport);
  }
  function close(fromPop) {
    if (!drawer) return;
    // inert 会把焦点踢到 body，先记住，关完再还给浮钮
    const hadFocus = drawer.contains(document.activeElement);
    hideMenu();
    drawer.classList.remove('open');
    drawer.inert = true; // 关着时 Tab 不能进去（aria-hidden 拦不住焦点）
    drawer.setAttribute('aria-hidden', 'true');
    drawer.removeAttribute('aria-modal');
    drawer.style.top = '';
    drawer.style.height = '';
    drawer.classList.remove('ai-kb');
    hidePop();
    unlockScroll();
    if (drawerPushed) {
      drawerPushed = false;
      if (!fromPop) {
        try {
          history.back();
        } catch (e) {
          /* 无 history 的环境（如 file://）直接关 */
        }
      }
    }
    if (hadFocus && floatBtn) floatBtn.focus({ preventScroll: true });
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
    const facts = pageFacts(); // 页面声明的事实集（矩阵/读数），DOM 摘要之外的另一档
    const ctx = getPageContext(facts ? SUMMARY_BUDGET_WITH_FACTS : SUMMARY_BUDGET);
    // 提示词静态段单源在 ai-prompt.js（AI_SYSTEM_PROMPT / AI_FACTS_HINT / AI_CONTEXT_LABELS）；
    // 这里只按固定顺序把页面的动态坐标与读数填进去，不写任何提示词正文
    const L = AI_CONTEXT_LABELS;
    const sys = [
      sysPrompt(),
      '',
      L.page,
      L.title + ctx.pageTitle,
      L.path + ctx.pagePath,
      ctx.asOf ? L.asOf + ctx.asOf : '',
      ctx.summary ? L.summary + ctx.summary : '',
      facts ? '\n' + L.facts + '\n' + facts : '',
      facts ? AI_FACTS_HINT : '',
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
    stickBottom = true; // 自己发的新提问一定跳到底部
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
    stickBottom = true;
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
    // 头部按钮与 ⋯ 菜单里各有一个「存笔记」，两个都切状态（点菜单项后菜单即收，
    // 真正给人看的是顶部细条里的「保存中…」）
    const btns = drawer ? Array.from(drawer.querySelectorAll('[data-act="save"]')) : [];
    btns.forEach((btn) => {
      btn.disabled = true;
      btn.textContent = '保存中…';
    });
    flash('保存到笔记中…', false, true);
    try {
      const r = await saveToServer(thread);
      flash('已保存：' + (r.path || r.key || 'OK'));
    } catch (e) {
      flash('保存失败：' + (e && e.message ? e.message : e), true);
    } finally {
      btns.forEach((btn) => {
        btn.disabled = false;
        btn.textContent = '存笔记';
      });
    }
  }

  // ── DOM ─────────────────────────────────────────────────────────────────
  const SHELL =
    '<button class="ai-float" type="button" title="伴读问答（⌘/Ctrl + K）" aria-label="伴读问答">💬</button>' +
    '<button class="ai-sel-pop" type="button">💬 问这段</button>' +
    // tabindex=-1：打开时焦点能落在对话框上（读屏播报），又不进 Tab 序列、不弹键盘
    '<div class="ai-drawer" role="dialog" tabindex="-1" aria-labelledby="ai-title" aria-hidden="true">' +
    '<div class="ai-head">' +
    '<span class="ai-title" id="ai-title">伴读问答</span>' +
    '<button class="ai-btn" type="button" data-act="new">＋ 新对话</button>' +
    '<button class="ai-btn" type="button" data-act="hist">历史 <span data-hist-n>0</span></button>' +
    '<button class="ai-btn" type="button" data-act="export" title="导出为 Markdown（.md）">导出</button>' +
    '<button class="ai-btn" type="button" data-act="save" title="保存到 data/notes/（本地）或 NOTES KV（线上）">存笔记</button>' +
    '<button class="ai-btn" type="button" data-act="cfg">⚙ 设置</button>' +
    // 窄屏：上面三项收进这里（CSS 在 ≤768 隐藏旧钮、显示 ⋯），✕ 永远压在最右。
    // 弹层是普通按钮组（disclosure），不用 aria-haspopup（那是 menu 语义），只留 aria-expanded
    '<button class="ai-btn ai-more" type="button" data-act="more" aria-expanded="false" aria-label="更多操作">⋯</button>' +
    '<button class="ai-btn" type="button" data-act="close" title="关闭（Esc）" aria-label="关闭">✕</button>' +
    '<div class="ai-menu" data-menu hidden>' +
    '<button class="ai-btn" type="button" data-act="cfg">⚙ 设置</button>' +
    '<button class="ai-btn" type="button" data-act="export">导出 Markdown</button>' +
    '<button class="ai-btn" type="button" data-act="save">存笔记</button>' +
    '</div>' +
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
    menuEl = drawer.querySelector('[data-menu]');
    moreBtn = drawer.querySelector('[data-act="more"]');

    floatBtn.addEventListener('click', toggle);

    // 贴底判定：只有本来就贴着底（或内容还没撑满）才跟着新 chunk 滚
    msgsEl.addEventListener('scroll', () => { stickBottom = atBottom(); }, { passive: true });

    // 键盘弹出/收起时把抽屉钉在可见区（iOS Safari）
    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', syncDrawerToViewport);
      window.visualViewport.addEventListener('scroll', syncDrawerToViewport);
    }

    // Android 返回键：只关抽屉，不离开页面（open() 时压了一条同 URL 的 state）
    window.addEventListener('popstate', () => {
      if (drawerPushed && drawer.classList.contains('open')) {
        drawerPushed = false;
        close(true);
      }
    });

    window.addEventListener('resize', syncDrawerToViewport);

    // 跨过 768 断点时补一次模态状态（老 Safari 只有 addListener）
    const narrowMq = window.matchMedia ? window.matchMedia('(max-width: 768px)') : null;
    if (narrowMq) {
      if (narrowMq.addEventListener) narrowMq.addEventListener('change', syncModalMode);
      else if (narrowMq.addListener) narrowMq.addListener(syncModalMode);
    }

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
      if (!e.target.closest || !e.target.closest('.ai-menu, [data-act="more"]')) hideMenu();
    });

    // 触屏选词：长按选词、拖选择手柄都不触发 mouseup，补 touchend + selectionchange
    document.addEventListener('touchend', () => setTimeout(onSelect, 120), { passive: true });
    let selTimer = 0;
    document.addEventListener('selectionchange', () => {
      clearTimeout(selTimer);
      selTimer = setTimeout(onSelect, 260);
    });

    // 选区气泡是 fixed 定位，页面一滚就错位：任何滚动都收起（捕获阶段含内部滚动容器）
    window.addEventListener('scroll', hidePop, { passive: true, capture: true });

    drawer.addEventListener('click', (e) => {
      const t = e.target;
      if (!t.closest('.ai-menu') && !t.closest('[data-act="more"]')) hideMenu();
      const act = t.closest('[data-act]');
      if (act) {
        const a = act.dataset.act;
        if (act.closest('.ai-menu')) hideMenu(); // 菜单项点完即收
        if (a === 'more') return showMenu(!menuOpen());
        if (a === 'close') return close();
        if (a === 'new') {
          if (controller) controller.abort();
          newThread();
          renderMessages();
          renderHist();
          if (inputEl) inputEl.focus(); // 主动点「新对话」就是准备输入，弹键盘符合预期
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
        const kind = mact.dataset.mact;
        if (kind === 'retry') retry();
        else if (kind === 'copy') {
          const thread = getActiveThread();
          const msg = thread && thread.messages[Number(mact.dataset.mi)];
          if (msg) copyText(msg.content || '');
        } else editAt(Number(mact.dataset.mi));
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
        stickBottom = true; // 换会话看的是最新几条，别沿用上一个会话的滚动位置
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
      // 窄屏整屏模态：Tab 在抽屉内循环，别跑到底下的页面和 Astro 开发工具条
      if (e.key === 'Tab' && drawer.getAttribute('aria-modal') === 'true') {
        const items = Array.from(
          drawer.querySelectorAll(
            'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
          )
        ).filter((el) => !el.disabled && el.getClientRects().length);
        if (!items.length) return;
        const first = items[0];
        const last = items[items.length - 1];
        // 焦点在抽屉自身（刚打开时）等于「还没进第一个控件」，两个方向都从头/尾接
        if (document.activeElement === drawer || !drawer.contains(document.activeElement)) {
          e.preventDefault();
          (e.shiftKey ? last : first).focus();
        } else if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
        return;
      }
      if (e.key === 'Escape' && drawer.classList.contains('open')) {
        if (menuOpen()) return hideMenu();
        close();
      }
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
