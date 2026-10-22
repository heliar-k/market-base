"""伴读抽屉的配置纯逻辑（frontend/public/js/ai-config.js）—— node 直接跑。

覆盖两个回归点：
- 探测/换模型重渲染时，未保存的 Base URL / Key 必须能通过草稿读回来（readCfgDraft）；
- 刷新后模型表为空时，已保存但不被当前模型支持的 effort 必须被清掉（reconcileCfg），
  否则 UI 显示「默认」、请求却还带着它。
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.node_runner import run_node

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "frontend" / "public" / "js" / "ai-config.js"

BODY = """
const m = await import(%s);
const cache = [
  { id: 'm1', reasoning_levels: ['low', 'high'] },
  { id: 'm2', supported_reasoning_levels: [{ effort: 'medium' }, {}] },
  { id: 'm3' },
];
const fakeRoot = (values) => ({
  querySelector: (sel) => {
    const key = (sel.match(/data-k=\"([^\"]+)\"/) || [])[1];
    return Object.prototype.hasOwnProperty.call(values, key) ? { value: values[key] } : null;
  },
});
console.log(JSON.stringify({
  proxyDefault: m.isProxyMode({ ...m.DEFAULT_CFG }),
  proxyKey: m.isProxyMode({ ...m.DEFAULT_CFG, apiKey: 'sk' }),
  proxyOtherBase: m.isProxyMode({ ...m.DEFAULT_CFG, baseUrl: 'http://localhost:8000/v1' }),
  proxySlash: m.isProxyMode({ ...m.DEFAULT_CFG, baseUrl: m.DEFAULT_CFG.baseUrl + '/' }),
  levels1: m.effortLevels(cache, 'm1'),
  levels2: m.effortLevels(cache, 'm2'),
  levels3: m.effortLevels(cache, 'm3'),
  levelsMissing: m.effortLevels(cache, 'nope'),
  cacheMatchProxy: m.modelCacheMatches(
    { base: m.DEFAULT_CFG.baseUrl, proxy: true, list: [] },
    m.DEFAULT_CFG.baseUrl + '/',
    true
  ),
  cacheMatchDirect: m.modelCacheMatches(
    { base: m.DEFAULT_CFG.baseUrl, proxy: false, list: [] },
    m.DEFAULT_CFG.baseUrl,
    false
  ),
  cacheMismatchMode: m.modelCacheMatches(
    { base: m.DEFAULT_CFG.baseUrl, proxy: true, list: [] },
    m.DEFAULT_CFG.baseUrl,
    false
  ),
  cacheMismatchBase: m.modelCacheMatches(
    { base: 'https://a/v1', proxy: false, list: [] },
    'https://b/v1',
    false
  ),
  cacheNoList: m.modelCacheMatches(
    { base: m.DEFAULT_CFG.baseUrl, proxy: false },
    m.DEFAULT_CFG.baseUrl,
    false
  ),
  payloadProxy: m.chatPayload(
    { model: 'm1', effort: 'high' },
    [{ role: 'user', content: 'hi' }],
    cache,
    true
  ),
  payloadDirect: m.chatPayload(
    { model: 'm1', effort: 'high' },
    [{ role: 'user', content: 'hi' }],
    cache,
    false
  ),
  payloadUnknown: m.chatPayload(
    { model: 'm2', effort: 'high' },
    [{ role: 'user', content: 'hi' }],
    cache,
    false
  ),
  keep: m.reconcileCfg({ model: 'm1', effort: 'high' }, cache),
  clear: m.reconcileCfg({ model: 'm1', effort: 'medium' }, cache),
  clearNoModel: m.reconcileCfg({ model: 'nope', effort: 'high' }, cache),
  draft: m.readCfgDraft(
    fakeRoot({ baseUrl: 'http://x/v1', apiKey: 'sk-a', model: 'm1', effort: 'high' }),
    { baseUrl: 'def', apiKey: '', model: 'g', effort: '' }
  ),
  draftMissing: m.readCfgDraft(
    fakeRoot({ apiKey: 'sk-b' }),
    { baseUrl: 'def', apiKey: '', model: 'g', effort: 'low' }
  ),
  draftEmptyRoot: m.readCfgDraft(null, { baseUrl: 'def', apiKey: 'k', model: 'g', effort: '' }),
}));
"""


def _run() -> dict:
    return run_node(BODY % json.dumps(CONFIG.as_uri()), timeout=30)


def test_proxy_mode() -> None:
    out = _run()
    assert out["proxyDefault"] is True
    assert out["proxyKey"] is False
    assert out["proxyOtherBase"] is False
    assert out["proxySlash"] is True


def test_effort_levels_both_shapes() -> None:
    out = _run()
    assert out["levels1"] == ["low", "high"]
    assert out["levels2"] == ["medium"]
    assert out["levels3"] == []
    assert out["levelsMissing"] == []


def test_model_cache_is_mode_scoped() -> None:
    out = _run()
    assert out["cacheMatchProxy"] is True
    assert out["cacheMatchDirect"] is True
    assert out["cacheMismatchMode"] is False
    assert out["cacheMismatchBase"] is False
    assert out["cacheNoList"] is False


def test_chat_payload_proxy_and_effort() -> None:
    out = _run()
    user = [{"role": "user", "content": "hi"}]
    assert out["payloadProxy"] == {"model": "m1", "stream": True, "messages": user}
    assert out["payloadDirect"] == {
        "model": "m1",
        "stream": True,
        "messages": user,
        "reasoning_effort": "high",
    }
    # m2 只有 medium 档，high 不该被带出去
    assert out["payloadUnknown"] == {"model": "m2", "stream": True, "messages": user}


def test_reconcile_clears_unsupported_effort() -> None:
    out = _run()
    assert out["keep"] == {"model": "m1", "effort": "high"}
    assert out["clear"] == {"model": "m1", "effort": ""}
    assert out["clearNoModel"] == {"model": "nope", "effort": ""}


def test_read_draft_preserves_unsaved_values() -> None:
    out = _run()
    assert out["draft"] == {
        "baseUrl": "http://x/v1",
        "apiKey": "sk-a",
        "model": "m1",
        "effort": "high",
    }
    # 缺字段时用 fallback 补齐，不能把已保存值读丢
    assert out["draftMissing"] == {
        "baseUrl": "def",
        "apiKey": "sk-b",
        "model": "g",
        "effort": "low",
    }
    assert out["draftEmptyRoot"] == {
        "baseUrl": "def",
        "apiKey": "k",
        "model": "g",
        "effort": "",
    }
