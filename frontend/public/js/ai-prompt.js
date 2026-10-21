// ai-prompt.js — 伴读抽屉提示词唯一数据源（用户消息模板 + 系统提示词）。
//
// 为什么是 ESM 单文件（与 site-nav.js 同一套模式）：
//   - 改提示词只动这一个文件，ai-chat.js 里不再有任何提示词正文；
//   - 消费方是运行时 import（`import { … } from './ai-prompt.js'`）而不是构建期注入：
//     专题页由 Astro 构建，但 SPA 壳 frontend/public/app/index.html 是 public/ 原样
//     拷贝的静态文件（不走打包），构建期注入会需要第二套机制；ESM 两条路径同一份。
//   - VERSION 进 request 元信息，对话导出与排障时能一眼看出用的哪版提示词。
//
// 与后端研判提示词分工（src/llm.py 的 _SYSTEM_PROMPT，build-time 批量改写各专题叙事，
// 输出受数字白名单 + 长度校验的定长卡片文案）：
//   本文件的提示词只服务「自由问答」——用户随时提问、无结构约束、流式直出。
//   两者不共用：那份要 JSON 骨架，这份要自然语言；混用会把卡片版式约束带进对话。

export const AI_PROMPT_VERSION = '2026-10-21';

// 系统提示词（静态段）。页面标题 / 路径 / 时效 / 结论摘要由 ai-chat.js 的
// buildMessages() 拼在后面，不写在提示词里——提示词只放「角色 + 纪律」这类长期稳定的指令。
export const AI_SYSTEM_PROMPT = [
  '你是观澜台（US Macro 研究平台）的伴读分析助手。',
  '只依据当前页面披露/估算的数据与用户划词片段回答；页面没给出的数字不要编造，缺数据就直接说「页面未给出」。',
  '回答简洁，中文为主，国际通用术语（VIX / GEX / Contango 等）保留英文；引用数字要带单位与口径。',
].join('\n');

// 引用块模板：划词片段进请求时的包装格式。改这里即可换「引用怎么递给模型」。
export function formatQuote(q, i) {
  const lines = [
    '【引用' + (i + 1) + '】页面：' + (q.pageTitle || '') + (q.asOf ? '（' + q.asOf + '）' : ''),
  ];
  if (q.section) lines.push('小节：' + q.section);
  if (q.tableHeaders && q.tableHeaders.length) lines.push('表头：' + q.tableHeaders.join(' | '));
  if (q.rowText) lines.push('本行：' + q.rowText);
  lines.push('原文：「' + q.text + '」');
  return lines.join('\n');
}
