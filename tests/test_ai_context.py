"""伴读抽屉「页面事实集」约定测试（js/ai-context.js）。

守的是会静默坏掉、坏了没人发现的那几样：

- DOM 摘要只收可见元素（SPA 双视图时隐藏视图的文字照样在 DOM 里，曾把技术视图
  1119 字读数当成关联视图的上下文，模型拿 RSI 去编相关性）；
- system message 真的带上了【页面数据】块（页面声明了却没人拼 = 白声明）；
- 矩阵渲染与预算降级的行为本身（矩阵被截一半会让模型查到错位的对）。

加载顺序（ai-context.js 早于 ai-chat.js）在 test_static_single_source.py 里与
其它 head 顺序断言同处，不在这里重复一份路径常量。

依赖 node 的用例在无 node 环境自动 skip（与 test_static_single_source 一致）。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
JS = FRONTEND / "public" / "js"
AI_CONTEXT = JS / "ai-context.js"
AI_CHAT = JS / "ai-chat.js"


def test_page_summary_skips_hidden_nodes() -> None:
    """DOM 摘要只收可见元素：隐藏视图的读数不得进上下文。"""
    text = AI_CHAT.read_text(encoding="utf-8")
    assert "const isVisible" in text, "pageSummary 缺可见性判定"
    assert "if (!isVisible(el)) continue" in text, "pageSummary 未跳过不可见元素"


def test_chat_sends_structured_facts() -> None:
    """system message 必须带【页面数据】块，否则结构化事实集白声明。"""
    text = AI_CHAT.read_text(encoding="utf-8")
    assert "AICtx.render(" in text, "ai-chat 未读取 AICtx"
    assert "AI_CONTEXT_LABELS" in text, "页面数据块的标题文案未走 ai-prompt.js 单源"


ASTRO_PAGES = FRONTEND / "src" / "pages"
DECL_RE = re.compile(r"AICtx\.declare\('([^']+)'")


def _declaring_files() -> list[tuple[Path, str, str]]:
    """(文件, 相对路径, declare id)，只收声明过的页。"""
    out = []
    for f in sorted(ASTRO_PAGES.rglob("*.astro")):
        text = f.read_text(encoding="utf-8")
        for m in DECL_RE.finditer(text):
            out.append((f, str(f.relative_to(ASTRO_PAGES)), m.group(1)))
    return out


def test_every_topic_page_declares_context() -> None:
    """除 404 外每个专题页都要声明页面事实集。

    漏一页不会报错、不会白屏——只是那一页的问答悄悄退回「只能看 DOM 摘要」，
    也就是这次要修的那个毛病。所以做成全量守卫，而不是只盯迁移清单。
    """
    missing = [
        str(f.relative_to(ASTRO_PAGES))
        for f in sorted(ASTRO_PAGES.rglob("*.astro"))
        if str(f.relative_to(ASTRO_PAGES)) != "404.astro"
        and "AICtx.declare" not in f.read_text(encoding="utf-8")
    ]
    assert not missing, f"这些页没声明页面事实集：{missing}"


def test_context_ids_unique_and_follow_path() -> None:
    """id 取页面路径（去 .astro、去尾部 /index、/ 换 -），且全站唯一。

    provider 的 id 是覆盖键：两页撞名 = 后声明的悄悄顶掉先声明的。
    """
    seen: dict[str, str] = {}
    for _f, rel, ident in _declaring_files():
        expected = rel[: -len(".astro")]
        if expected.endswith("/index"):
            expected = expected[: -len("/index")]
        expected = expected.replace("/", "-")
        assert ident == expected, f"{rel} 的 declare id 应为 {expected!r}，实际 {ident!r}"
        assert ident not in seen, f"id 重复：{seen.get(ident)} 与 {rel} 都是 {ident!r}"
        seen[ident] = rel


def test_declarations_are_guarded_and_after_data() -> None:
    """declare 必须包在 window.AICtx 守卫里（脚本没到位时不抛错），
    且不能出现在文件第一次 await 之前（那时 provider 闭包里的 payload 还是 undefined）。"""
    for f, rel, _ident in _declaring_files():
        text = f.read_text(encoding="utf-8")
        guard = text.find("if (window.AICtx")
        decl = text.find("AICtx.declare")
        assert guard != -1 and guard < decl, f"{rel} 的 declare 没有 window.AICtx 守卫"
        first_await = text.find("await ")
        assert first_await == -1 or first_await < decl, (
            f"{rel} 的 declare 出现在首次 await 之前，取不到数据"
        )


def test_correlation_uses_matrix_helper() -> None:
    """相关性页只交方阵，下三角转换必须走 AICtx.matrix（页面不手写 slice，错位只可能错一处）。"""
    text = (JS / "cross-correlation.js").read_text(encoding="utf-8")
    assert "AICtx.matrix(" in text, "相关性页未使用 AICtx.matrix，转换逻辑散回页面"
    assert "row.slice(0, i + 1)" not in text, "页面里又出现手写的下三角转换"


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("无 node，跳过渲染行为测试")
    return node


def _run_context(tmp_path: Path, driver: str) -> str:
    script = tmp_path / "driver.js"
    script.write_text(
        "const fs = require('fs'), vm = require('vm');\n"
        f"const code = fs.readFileSync({str(AI_CONTEXT)!r}, 'utf8');\n"
        "const win = {};\n"
        "vm.runInNewContext(code, { window: win, console });\n"
        "const A = win.AICtx;\n" + driver,
        encoding="utf-8",
    )
    r = subprocess.run([_node(), str(script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_blocks_normalises_before_render(tmp_path: Path) -> None:
    """空值 / 空数组在归一化阶段就被丢掉，不靠 render 兜（否则会渲染出「- ：」）。"""
    out = _run_context(
        tmp_path,
        """A.declare('t', () => ({ title: 'T', facts: [['有值', 0, ''], ['空值', '', '']],
  matrix: { labels: [], rows: [] } }));
console.log(JSON.stringify(A.blocks()));""",
    )
    blocks = json.loads(out)
    assert len(blocks) == 1, "应只产出一个块"
    assert [f[0] for f in blocks[0]["facts"]] == ["有值"], "空值条目未被归一化丢弃"
    assert blocks[0]["matrix"] is None, "空矩阵未被归一化丢弃"


def test_visibility_predicate(tmp_path: Path) -> None:
    """隐藏视图判定：宽高与 client rect 全零即不可见（DOM 摘要与 provider 共用这一份）。"""
    out = _run_context(
        tmp_path,
        """const hidden = { offsetWidth: 0, offsetHeight: 0, getClientRects: () => [] };
const shown = { offsetWidth: 10, offsetHeight: 0, getClientRects: () => [] };
const inline = { offsetWidth: 0, offsetHeight: 0, getClientRects: () => [{ x: 0 }] };
const out = [A.isVisible(null), A.isVisible(hidden), A.isVisible(shown), A.isVisible(inline)];
console.log(JSON.stringify(out));""",
    )
    assert json.loads(out) == [False, False, True, True]


def test_square_matrix_helper_is_the_only_conversion(tmp_path: Path) -> None:
    """方阵 → 下三角只走 AICtx.matrix：第 i 行 i+1 个数，且渲染出的第 i 行第 j 个数
    必须等于 square[i][j]（错位的话任意一对都会读到别人的系数）。"""
    out = _run_context(
        tmp_path,
        """const square = [[1, 0.2, 0.3], [0.2, 1, 0.4], [0.3, 0.4, 1]];
A.declare('t', () => ({ title: 'T', matrix: A.matrix(['X', 'Y', 'Z'], square) }));
const rows = A.blocks()[0].matrix.rows;
console.log(JSON.stringify(rows));
console.log(A.render({ budget: 2000 }));""",
    )
    rows_line, rendered = out.split("\n", 1)
    assert json.loads(rows_line) == [[1], [0.2, 1], [0.3, 0.4, 1]]
    # 渲染层：第 i 行第 j 个数 = square[i][j]，对角线以 — 占位
    assert "Y +0.20 —" in rendered
    assert "Z +0.30 +0.40 —" in rendered


def test_matrix_render_shape(tmp_path: Path) -> None:
    """下三角：第 i 行 i+1 个数，对角线以 — 占位，行首是标的名。"""
    out = _run_context(
        tmp_path,
        """A.declare('t', () => ({ title: '矩阵', facts: [['窗口', '30 日', '']],
  matrix: { labels: ['X', 'Y', 'Z'], rows: [[1], [0.5, 1], [-0.25, 0.1, 1]] } }));
console.log(A.render({ budget: 2000 }));""",
    )
    assert "标的顺序：X Y Z" in out
    assert "X —" in out
    assert "Y +0.50 —" in out
    assert "Z -0.25 +0.10 —" in out


def test_matrix_dropped_whole_when_over_budget(tmp_path: Path) -> None:
    """预算不够时整块丢矩阵，不做半截截断（半张表会让模型查到错位的对）。"""
    out = _run_context(
        tmp_path,
        """const labels = Array.from({ length: 15 }, (_, i) => 'A' + i);
const rows = labels.map((_, i) => labels.slice(0, i + 1).map(() => 0.5));
A.declare('t', () => ({ title: '大矩阵', facts: [['窗口', '30 日', '']],
  matrix: { labels, rows } }));
console.log(A.render({ budget: 400 }));""",
    )
    assert "大矩阵" in out  # 块还在
    assert "+0.50" not in out  # 但矩阵整块不在了
    assert "已省略" in out


def test_empty_context_is_empty_string(tmp_path: Path) -> None:
    """没声明任何块的页（如纯表格页）返回空串，不产生空段落。"""
    out = _run_context(tmp_path, "console.log(JSON.stringify(A.render()));")
    assert out.strip() == '""'
