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

export const AI_PROMPT_VERSION = '2026-10-22';

// 系统提示词（静态段）。页面标题 / 路径 / 时效 / 结论摘要由 ai-chat.js 的
// buildMessages() 拼在后面，不写在提示词里——提示词只放「角色 + 纪律」这类长期稳定的指令。
export const AI_SYSTEM_PROMPT = [
  '你是观澜台（US Macro 研究平台）的伴读分析助手。',
  '取数按三层优先级：① 当前页面事实集 / DOM 摘要里有的，直接用（已能回答就不调工具）；',
  '② 页面没有的站内读数调 query_site_data 查，别凭记忆答；最多查两次，没拿到就基于已有信息作答并说明哪项没查到；结果过大按提示用 json_path 下钻。',
  '③ 工具返回 error（本期未导出 / 404 / 超时）时，明确说「本期未导出」或「页面未给出」，页面没给出的数字不要编造。',
  '回答简洁，中文为主，国际通用术语（VIX / GEX / Contango 等）保留英文；引用数字要带单位与口径。',
].join('\n');

// 【页面数据】块存在时的追加纪律（块本身的格式由 ai-context.js 编码，
// 这句话是给模型的用法说明，属提示词，所以留在这里而不是拼在 ai-chat.js 里）
export const AI_FACTS_HINT =
  '用户问到的标的/指标，先在【页面数据】里查（那里是本页的结构化读数与关系矩阵）；本页没有的站内读数用 query_site_data 查，工具也没有才说「页面未给出」。';

// 页面坐标段的标签文案（标题/路径/时效/结论），改口径措辞只动这里
export const AI_CONTEXT_LABELS = {
  page: '【当前页面】',
  title: '标题：',
  path: '路径：',
  asOf: '时效：',
  summary: '页面上已给出的结论与读数：',
  facts: '【页面数据】',
};

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
