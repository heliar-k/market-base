// macro-view.js — SPA 左侧全局专题导航（唯一数据源：site-nav.js）
//
// 2026-10-02（#24 A 方案）去 iframe 化：原先「宏观视图」用 iframe 懒加载专题页，
// 代价是双滚动条、主题要跨文档广播（localStorage + storage 事件）、键盘 Tab 进 iframe
// 回不来、移动端表现差。现在专题链接是真实 <a href>，点击由浏览器整页跳转；
// SPA 只保留三个自有视图（仪表盘 / 技术 / 关联），默认落地页 = 仪表盘。
// 旧的专题深链（#daily、#macro/fed 之类）在 routeHash 里整页重定向，不静默失效。

// ── 全站专题导航（唯一数据源：site-nav.js ESM import，与专题页顶栏 Tab 同源）──
// home = 今日研判（专题页）；groups = 专题分组（可展开/收起，items 为二级页）
import { SITE_NAV as SITE } from './site-nav.js';
const NAV = SITE.groups;

// 核心入口：专题页（home）+ SPA 自有视图
const DEFAULT_PAGE = SITE.home.page;
const CORE = [{ label: SITE.home.label, page: DEFAULT_PAGE }].concat(SITE.spaViews);

// key ↔ page 双向映射（只用于旧深链重定向）
const PAGES = new Map(); // key → page
const KEYS = new Map();  // page → key
NAV.forEach(g => {
  PAGES.set(g.key, g.page);
  KEYS.set(g.page, g.key);
  (g.items || []).forEach(it => { PAGES.set(it.key, it.page); KEYS.set(it.page, it.key); });
});
PAGES.set(SITE.home.key, DEFAULT_PAGE);
KEYS.set(DEFAULT_PAGE, SITE.home.key);

// SPA 自有视图键（#dashboard / #tech / #correlation）
const VIEW_KEYS = new Set(SITE.spaViews.map(v => v.view));

// hash 键名容错：分组前缀猜错时按最后一段回退（导航 key 末段无重名）
// 例：#macro/fed → fed；#equities/crypto → assets/crypto
const pageOf = key =>
  PAGES.get(key) ??
  PAGES.get([...PAGES.keys()].find(k => k.split('/').pop() === key?.split('/').pop()));

// 转义单源 R.esc（rates-common.js 已在 index.html:23 先于本模块加载，dashboard.js 同样用法）
const esc = R.esc;

// ── state ──
let navEl = null;
let activeView = null; // 'dashboard' | 'tech' | 'correlation'

function highlight() {
  navEl.querySelectorAll('a.macro-nav-item[data-view]').forEach(a => {
    const on = a.dataset.view === activeView;
    a.classList.toggle('active', on);
    // 读屏器标注当前视图（与 .active 同步）
    if (on) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
}

function goView(view) {
  window.dispatchEvent(new CustomEvent('switch-tab', { detail: view }));
}

function toggleGroup(grp) {
  grp.classList.toggle('open');
}

// 旧专题深链 → 整页跳转（iframe 时代留下的 #daily / #macro/fed 等）；
// 视图深链（#tech 等）返回键名，由 app.js 决定初始视图。
function viewFromHash() {
  const key = location.hash.slice(1);
  if (key && VIEW_KEYS.has(key)) return key;
  if (key && !VIEW_KEYS.has(key) && pageOf(key)) location.replace(pageOf(key));
  return null;
}

function routeHash() {
  const key = location.hash.slice(1);
  if (!key) return;
  if (VIEW_KEYS.has(key)) { goView(key); return; }
  const page = pageOf(key);
  if (page) location.replace(page); // 专题页已不是 SPA 内嵌，只能整页跳
}

// ── 全局导航渲染（index.html 的 #macro-nav）──
export function initGlobalNav() {
  const el = document.getElementById('macro-nav');
  if (!el || navEl) return;
  navEl = el;
  // 品牌名即回站根入口（与 TopicLayout 渲染结果一致：类名不变，只把这层换成 <a>）。
  // 下面三个内联声明是 TopicLayout <style> 里那条规则的镜像（SPA 不加载 .astro 的
  // scoped 样式，app.css 又不可改；:hover 内联表达不了，故 SPA 侧品牌名无 hover 变色）——
  // 样式真源在 TopicLayout.astro，改观感两处同改。
  navEl.innerHTML = `
    <div class="macro-nav-brand">
      <div>
        <a class="macro-nav-brand-name" href="/" style="display:block;color:var(--text);text-decoration:none">观澜台</a>
        <div class="macro-nav-brand-sub">美国宏观研究平台</div>
      </div>
      <button class="theme-toggle" id="theme-toggle" title="切换暗色/亮色模式">🌙</button>
    </div>
    <div class="macro-nav-caption">核心入口</div>
    ${CORE.map(c => c.view
      ? `<a class="macro-nav-item core" data-view="${c.view}" href="#${c.view}">${esc(c.label)}</a>`
      : `<a class="macro-nav-item core" href="${c.page}">${esc(c.label)}</a>`).join('')}
    <div class="macro-nav-sep"></div>
    <div class="macro-nav-caption">全部专题</div>
    ${NAV.map(g => `
      <div class="macro-nav-group" data-key="${g.key}">
        <div class="macro-nav-group-head">
          <a class="macro-nav-item" href="${g.page}">${esc(g.label)}</a>
          ${g.items ? '<button class="macro-nav-toggle" title="展开 / 收起">›</button>' : ''}
        </div>
        ${g.items ? `<div class="macro-nav-sub">${g.items.map(it =>
          `<a class="macro-nav-item child${it.sub ? ' sub' : ''}" href="${it.page}">${esc(it.label)}</a>`).join('')}</div>` : ''}
      </div>`).join('')}
    <div class="macro-nav-foot">
      <div class="macro-nav-foot-row"><span>美东时间</span><span id="macro-nav-clock"></span></div>
      <div class="macro-nav-foot-note">数据每个交易日自动更新 · 不构成投资建议</div>
    </div>
  `;

  navEl.addEventListener('click', e => {
    const a = e.target.closest('a.macro-nav-item');
    if (a) {
      // 专题页是真实链接：交给浏览器整页跳转（#24），只有 SPA 自有视图拦下来切 tab
      if (a.dataset.view) {
        e.preventDefault();
        goView(a.dataset.view);
        closeDrawer();
      }
      return;
    }
    const t = e.target.closest('.macro-nav-toggle');
    if (t) toggleGroup(t.closest('.macro-nav-group'));
  });

  // 外部切视图（仪表盘小卡片、go-stock 等）由 app.js 直接调 markView，不再监听事件

  window.addEventListener('hashchange', routeHash);
  const initialView = viewFromHash(); // 带着旧专题深链进来就直接跳走

  // 美东时间（每分钟更新）
  function tick() {
    const el2 = document.getElementById('macro-nav-clock');
    if (!el2) return;
    el2.textContent = new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(new Date());
  }
  tick();
  setInterval(tick, 60_000);

  // 窄屏抽屉
  const toggle = document.getElementById('macro-nav-toggle');
  const backdrop = document.getElementById('macro-nav-backdrop');
  toggle?.addEventListener('click', () =>
    document.body.classList.toggle('side-open'));
  backdrop?.addEventListener('click', closeDrawer);
  // Esc 关闭抽屉（与 drill 弹层的 Esc 约定一致）
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && document.body.classList.contains('side-open')) closeDrawer();
  });

  return initialView; // app.js 据此定初始视图（无 hash 时 null → 仪表盘）
}

// 当前视图高亮单入口：app.js 的 switchTab 调它（含 go-stock 等绕过侧栏点击的路径）
export function markView(view) {
  activeView = view;
  if (navEl) highlight();
}

function closeDrawer() {
  document.body.classList.remove('side-open');
}
