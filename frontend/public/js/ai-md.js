// ai-md.js — 伴读抽屉的 Markdown 渲染（单源）
//
// 只覆盖模型实际会写的语法：围栏代码块、两档标题、有序/无序列表、引用、表格、分隔线，
// 行内 code / 粗体 / 斜体 / 链接。先转义再套标签，任何原始 HTML 都进不来。
// 单独成文件的理由：能被 node 直接跑（tests/test_ai_md.py），且改渲染只动一处。
// 转义复用 rates-common.js 的全局 R.esc，不自带一份实现。

const esc = (s) => (typeof R !== 'undefined' && R.esc ? R.esc(String(s)) : String(s));

const RE_FENCE = /^```\s*[\w+#-]*\s*$/;
const RE_HEAD = /^(#{1,6})\s+(.*)$/;
const RE_HR = /^(?:-{3,}|\*{3,}|_{3,})$/;
const RE_QUOTE = /^>\s?(.*)$/;
const RE_UL = /^[-*+]\s+(.*)$/;
const RE_OL = /^\d+[.)]\s+(.*)$/;

const cells = (line) =>
  line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((c) => c.trim());

// 表头分隔行（|---|:--:|）：只含 | - : 空白，且 | 与 - 都在
const isSep = (s) => /^[|\s:-]+$/.test(s) && s.includes('|') && s.includes('-');

function inline(s) {
  return esc(s)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^*\n]+)\*/g, '<em>$1</em>')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}

export function mdToHtml(src) {
  const lines = String(src ?? '')
    .replace(/\r\n?/g, '\n')
    .split('\n');
  const out = [];
  let list = '';
  const closeList = () => {
    if (!list) return;
    out.push('</' + list + '>');
    list = '';
  };
  const openList = (kind) => {
    if (list === kind) return;
    closeList();
    list = kind;
    out.push('<' + kind + '>');
  };

  for (let i = 0; i < lines.length; i++) {
    const ln = lines[i].trim();
    if (!ln) {
      closeList();
      continue;
    }

    // 围栏代码块：内容整段转义，不再解释里面的任何标记
    if (RE_FENCE.test(ln)) {
      closeList();
      const buf = [];
      let j = i + 1;
      for (; j < lines.length && !RE_FENCE.test(lines[j].trim()); j++) buf.push(lines[j]);
      out.push('<pre class="ai-pre"><code>' + esc(buf.join('\n')) + '</code></pre>');
      i = j;
      continue;
    }

    const head = ln.match(RE_HEAD);
    if (head) {
      closeList();
      // 抽屉窄，只有两档：一二级一档，更深的并到第二档
      out.push('<p class="ai-h' + (head[1].length <= 2 ? 1 : 2) + '">' + inline(head[2]) + '</p>');
      continue;
    }

    if (RE_HR.test(ln)) {
      closeList();
      out.push('<hr>');
      continue;
    }

    // 表格：本行含 | 且下一行是分隔行
    if (ln.includes('|') && i + 1 < lines.length && isSep(lines[i + 1].trim())) {
      closeList();
      const th = cells(ln);
      const rows = [];
      let j = i + 2;
      for (; j < lines.length && lines[j].trim().includes('|'); j++) rows.push(cells(lines[j]));
      out.push(
        '<table class="ai-table"><thead><tr>' +
          th.map((c) => '<th>' + inline(c) + '</th>').join('') +
          '</tr></thead>' +
          (rows.length
            ? '<tbody>' +
              rows.map((r) => '<tr>' + r.map((c) => '<td>' + inline(c) + '</td>').join('') + '</tr>').join('') +
              '</tbody>'
            : '') +
          '</table>'
      );
      i = j - 1;
      continue;
    }

    const quote = ln.match(RE_QUOTE);
    if (quote) {
      closeList();
      const buf = [quote[1]];
      let j = i + 1;
      for (; j < lines.length; j++) {
        const m = lines[j].trim().match(RE_QUOTE);
        if (!m) break;
        buf.push(m[1]);
      }
      out.push('<blockquote>' + buf.map(inline).join('<br>') + '</blockquote>');
      i = j - 1;
      continue;
    }

    const ul = ln.match(RE_UL);
    if (ul) {
      openList('ul');
      out.push('<li>' + inline(ul[1]) + '</li>');
      continue;
    }

    const ol = ln.match(RE_OL);
    if (ol) {
      openList('ol');
      out.push('<li>' + inline(ol[1]) + '</li>');
      continue;
    }

    closeList();
    out.push('<p>' + inline(ln) + '</p>');
  }

  closeList();
  return out.join('');
}
