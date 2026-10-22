// POST /api/notes — 把伴读对话存成 Markdown。
//
// 静态 Pages 没有 data/ 写权限：绑了 NOTES（KV）就落到 notes/YYYY-MM-DD/，
// 没绑返回 503 并提示回本地 `uv run python -m src.server`（本地 FastAPI 写 data/notes/）。
import {
  HttpError,
  NOTES_BODY_MAX,
  RATE_LIMITS,
  NOTES_MAX,
  clientIp,
  json,
  rateLimit,
  readJson,
  safeNoteName,
} from '../_lib/common.js';

export async function onRequestPost({ request, env }) {
  try {
    if (!rateLimit('notes:' + clientIp(request), ...RATE_LIMITS.notes)) {
      return json(
        { error: `保存过于频繁（每小时 ${RATE_LIMITS.notes[0]} 次），请稍后再试。` },
        429,
        { 'retry-after': '600' }
      );
    }

    const body = await readJson(request, NOTES_BODY_MAX);
    const markdown = typeof (body && body.markdown) === 'string' ? body.markdown : '';
    if (!markdown.trim()) return json({ error: 'markdown 为空' }, 400);
    if (new TextEncoder().encode(markdown).length > NOTES_MAX) {
      return json({ error: '笔记超过 1 MB' }, 413);
    }

    if (!env || !env.NOTES) {
      return json(
        {
          error:
            '静态部署没有 data/ 写权限（未绑定 NOTES KV）。' +
            '请本地运行 uv run python -m src.server 后保存，或点「导出 .md」。',
        },
        503
      );
    }

    const title = String((body && body.thread && body.thread.title) || '');
    const name = safeNoteName((body && body.filename) || title, '伴读对话');
    const day = new Date().toISOString().slice(0, 10);
    const key = `notes/${day}/${Date.now()}-${name}`;
    await env.NOTES.put(key, markdown, {
      metadata: { title: title.slice(0, 120), savedAt: Date.now() },
    });
    return json({ ok: true, key, storage: 'kv' });
  } catch (e) {
    if (e instanceof HttpError) return json({ error: e.message }, e.status);
    return json({ error: '保存失败：' + (e && e.message ? e.message : e) }, 500);
  }
}
