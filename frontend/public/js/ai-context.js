// ai-context.js — 伴读抽屉「页面事实集」单源（声明 → 预算裁剪 → 紧凑文本）。
//
// 为什么不抓 DOM 摘要：DOM 里只有「看起来像结论的串」，而真正要回答的问题常常在
// canvas 上（相关性矩阵、热力图、K 线）——那里一个文字节点都没有。更糟的是隐藏视图
// 的文字照样能被 innerText 抓到：/app/ 关联视图下曾把技术视图的 1119 字读数
// （RSI 44.1、MACD死叉）当成页面上下文送进模型，模型拿它编相关性。
//
// 契约（页面只声明数据，不写文案；编码与裁剪只在本文件）：
//   AICtx.declare(id, () => block | block[])
//   block = {
//     title:    '跨资产相关性',            // 块标题
//     src:      'cross_asset / 日收益',    // 数据源与口径（一句话）
//     asOf:     '2026-10-18',              // 观测日
//     facts:    [['窗口', '30 日滚动', '']], // [名称, 值, 注]；注可省
//     matrix:   { labels, rows },          // 关系矩阵：rows 为下三角各行数值（见下）
//     note:     '±0.3 以上视为有意义',      // 段末注
//     priority: 10,                        // 越小越先占预算，默认 50
//   }
//
// 两个工具函数放在这里而不是各页自写（都是「页面数据 → 上下文」这一步的知识）：
//   AICtx.isVisible(el)          可见性判定（DOM 摘要与 provider 共用，隐藏视图一律不送）
//   AICtx.matrix(labels, square) 方阵 → 下三角行；越界/错位的转换只此一处
//
// matrix.rows 的约定（为什么是下三角）：两两相关有 231 对，全写成「A×B 值」要 3.7k 字，
// 下三角只留数字 1.4k 字就能放下整张 22×22 表。渲染时每行以标的名开头、后接它与
// 「标的顺序」中前 k 个标的的系数（第 0 行只有对角线，用 — 占位），于是任意一对都能查：
// 第 i 行第 j 个数 = labels[i] 与 labels[j] 的系数。
//
// 加载方式：经典脚本（非 module），在 rates-common.js 之后、页面 island 脚本之前。
// 专题页 inline 脚本与 SPA 的 ESM 模块都走全局 window.AICtx，不各挂一份。
(function () {
  'use strict';

  if (window.AICtx) return;

  const providers = new Map(); // id → provider（Map 保声明顺序，同 id 重复声明即覆盖）

  // 数值格式化：相关矩阵要定宽（+0.91 / -0.12 / NA），别的块用原样字符串
  const coef = (v) => (v === null || v === undefined || Number.isNaN(v) ? 'NA' : (v >= 0 ? '+' : '') + Number(v).toFixed(2));

  function normBlock(b) {
    if (!b || typeof b !== 'object') return null;
    const facts = (Array.isArray(b.facts) ? b.facts : [])
      .filter((f) => Array.isArray(f) && f[0] != null && f[1] != null && String(f[1]).length)
      .map(([label, value, note]) => [String(label), String(value), note ? String(note) : '']);
    const m = b.matrix;
    const matrix =
      m && Array.isArray(m.labels) && m.labels.length && Array.isArray(m.rows) && m.rows.length
        ? { labels: m.labels.map(String), rows: m.rows }
        : null;
    if (!facts.length && !matrix) return null;
    return {
      title: b.title ? String(b.title) : '',
      src: b.src ? String(b.src) : '',
      asOf: b.asOf ? String(b.asOf) : '',
      facts,
      matrix,
      note: b.note ? String(b.note) : '',
      priority: Number.isFinite(b.priority) ? b.priority : 50,
    };
  }

  function blockList() {
    const out = [];
    for (const [, provider] of providers) {
      let r;
      try {
        r = provider();
      } catch (e) {
        continue; // 单个页面 provider 抛错不影响别的块（与 R.getOpt 同一容错哲学）
      }
      for (const b of Array.isArray(r) ? r : [r]) {
        const nb = normBlock(b);
        if (nb) out.push(nb);
      }
    }
    return out.sort((a, b) => a.priority - b.priority);
  }

  // 可见性：SPA 双视图 / 关联页的资产↔宏观子模式都靠 display:none 切，节点照样在 DOM 里。
  // offsetWidth/Height 对 inline 元素也可用，getClientRects 兜底（display:contents 之类）。
  function isVisible(el) {
    if (!el) return false;
    return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  }

  function renderHead(b) {
    const meta = [b.src, b.asOf ? 'as_of ' + b.asOf : ''].filter(Boolean).join(' · ');
    return '【' + (b.title || '页面数据') + '】' + (meta ? '（' + meta + '）' : '');
  }

  function renderFacts(facts) {
    return facts.map(([l, v, n]) => '- ' + l + '：' + v + (n ? '（' + n + '）' : '')).join('\n');
  }

  // 下三角：第 i 行 i+1 个数（含对角线）。对角线恒 1，用 — 省掉 132 字。
  function renderMatrix(m) {
    const lines = ['标的顺序：' + m.labels.join(' ')];
    lines.push('相关系数（下三角，2 位小数）：第 i 行第 j 个数 = 顺序里第 i 个与第 j 个标的的系数，对角线以 — 占位');
    m.rows.forEach((row, i) => {
      const vals = (Array.isArray(row) ? row : []).slice(0, i + 1).map((v, j) => (j === i ? '—' : coef(v)));
      lines.push(m.labels[i] + ' ' + vals.join(' '));
    });
    return lines.join('\n');
  }

  // 一个块按「优先保 facts、矩阵可整块丢」的顺序降级：
  // 矩阵被截一半会让模型查到错的对（错位比缺失更危险），所以宁可整块不给。
  function renderBlock(b, budget) {
    const head = renderHead(b);
    const facts = b.facts.length ? renderFacts(b.facts) : '';
    const note = b.note ? '注：' + b.note : '';
    const dropped = b.matrix ? '（关系矩阵超出上下文预算，已省略）' : '';
    const full = [head, facts, b.matrix ? renderMatrix(b.matrix) : '', note].filter(Boolean).join('\n');
    if (full.length <= budget) return full;

    // 退一档：丢矩阵（整块丢，绝不截半张——截半会让模型查到错位的对）
    const noMatrix = [head, facts, note, dropped].filter(Boolean).join('\n');
    if (noMatrix.length <= budget) return noMatrix;

    // 退二档：facts 逐条截断，矩阵同样只报省略
    const keep = [];
    let len = head.length + 1;
    for (const f of b.facts) {
      const line = renderFacts([f]);
      if (len + line.length + 1 > budget - 40) break;
      keep.push(line);
      len += line.length + 1;
    }
    const cut = b.facts.length - keep.length;
    return [
      head,
      keep.join('\n'),
      cut > 0 ? '（另有 ' + cut + ' 条读数超出预算，已省略）' : '',
      dropped,
      note,
    ]
      .filter(Boolean)
      .join('\n');
  }

  window.AICtx = {
    declare(id, provider) {
      if (!id || typeof provider !== 'function') return;
      providers.set(String(id), provider);
    },

    /** 已声明的块（调试与守卫测试用） */
    blocks: blockList,

    /**
     * 渲染成一段可直接塞进 system message 的文本；无内容返回 ''。
     * budget 是「页面事实」这一档的总预算，按 priority 顺序先到先得。
     */
    render({ budget = 2600 } = {}) {
      const out = [];
      let left = budget;
      let skipped = 0;
      for (const b of blockList()) {
        // 单块上限 = 当前剩余预算（不另设更低的天花板：那会让「刚好占满一档」的块丢掉矩阵）
        if (left <= 120) {
          skipped++;
          continue;
        }
        const txt = renderBlock(b, left);
        if (!txt) continue;
        out.push(txt);
        left -= txt.length + 2;
      }
      if (skipped) out.push('（另有 ' + skipped + ' 个数据块超出上下文预算，已省略）');
      return out.join('\n\n');
    },

    isVisible,

    /** 方阵 → 下三角行（第 i 行取 square[i][0..i]）；转换只此一处，别在页面里手写 slice */
    matrix(labels, square) {
      if (!Array.isArray(labels) || !labels.length || !Array.isArray(square)) return null;
      return {
        labels: labels.map(String),
        rows: square.map((row, i) => (Array.isArray(row) ? row.slice(0, i + 1) : [])),
      };
    },
  };
})();
