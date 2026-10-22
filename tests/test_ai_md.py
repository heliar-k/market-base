"""伴读抽屉的 Markdown 渲染（frontend/public/js/ai-md.js）—— 单源，改坏了这里红。

mdToHtml 是手写的块级/行内解析，属于"非平凡逻辑"，用真实模块跑 node 验产出：
先整体转义，任何原始 HTML 都进不来；围栏代码块里的标记不再解释。
"""

import json
from pathlib import Path

from tests.node_runner import run_node

ROOT = Path(__file__).resolve().parent.parent
MD = ROOT / "frontend" / "public" / "js" / "ai-md.js"

# 两个入口都是浏览器侧：R.esc 由 rates-common.js 提供，这里给同口径的桩
BODY = """
globalThis.R = {
  esc: (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
};
const { mdToHtml } = await import(%s);
console.log(JSON.stringify({
  head: mdToHtml('# 标题\\n\\n正文'),
  h3: mdToHtml('### 三级'),
  fence: mdToHtml('```js\\nconst a = 1 < 2;\\n```'),
  table: mdToHtml('| a | b |\\n|---|---|\\n| 1 | 2 |'),
  ol: mdToHtml('1. 一\\n2. 二'),
  ul: mdToHtml('- 甲\\n- 乙'),
  inline: mdToHtml('**粗** 与 `code`'),
  quote: mdToHtml('> 引用第一行\\n> 第二行'),
  hr: mdToHtml('---'),
  esc: mdToHtml('<script>alert(1)</script>'),
}));
"""


def _render() -> dict:
    return run_node(BODY % json.dumps(MD.as_uri()))


def test_markdown_blocks() -> None:
    out = _render()
    assert out["head"] == '<p class="ai-h1">标题</p><p>正文</p>'
    assert out["h3"] == '<p class="ai-h2">三级</p>', "三级以上标题并到第二档"
    assert out["ol"] == "<ol><li>一</li><li>二</li></ol>"
    assert out["ul"] == "<ul><li>甲</li><li>乙</li></ul>"
    assert out["quote"] == "<blockquote>引用第一行<br>第二行</blockquote>"
    assert out["hr"] == "<hr>"


def test_markdown_fence_and_table() -> None:
    out = _render()
    # 代码块内容只转义、不再解释：尖括号变实体，围栏本身不出现
    assert "```" not in out["fence"]
    assert "const a = 1 &lt; 2;" in out["fence"]
    assert out["fence"].startswith('<pre class="ai-pre"><code>')
    assert "<th>a</th>" in out["table"] and "<td>2</td>" in out["table"]


def test_markdown_inline_and_escaping() -> None:
    out = _render()
    assert "<strong>粗</strong>" in out["inline"] and "<code>code</code>" in out["inline"]
    # 原始 HTML 永远进不来（先转义再套标签）
    assert "<script>" not in out["esc"] and "&lt;script&gt;" in out["esc"]
