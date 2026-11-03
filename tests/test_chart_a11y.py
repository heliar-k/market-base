"""图表与研判段的可及性单源（echarts-theme.js 的 aria / R._ariaText / R.sigBlocks 的 h3）。

浏览器 API 打桩后用 node 跑（同 test_secnote_fold.py）。锁三件事：
  · 主题开启 aria.enabled，全站图表零改动拿到 role="img" 与容器描述；
  · R._ariaText 必须产出 description —— ECharts 自动生成的文案会把前 10 项原始数据
    （时间戳 + 数值）整段念出来，description 是整段替换；
  · 研判格标题是 h3（屏幕阅读器按标题跳转能停到每格），且标题取自最近的小节
    （静态 Section 是 .re-sec，JS 生成的卡是 .re-section，内层 .re-panel 上没有 h2）。
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
THEME = ROOT / "frontend" / "public" / "js" / "echarts-theme.js"
COMMON = ROOT / "frontend" / "public" / "js" / "rates-common.js"

BODY = """
globalThis.window = { addEventListener() {} };
const mk = (tag) => ({
  tag, className: '', _text: '', children: [],
  set textContent(v) { this._text = v; this.children = []; },
  get textContent() { return this._text; },
  append(...c) { this.children.push(...c); },
  appendChild(c) { this.children.push(c); return c; },
});
globalThis.document = { body: { classList: { contains: () => false } }, createElement: mk };
// buildTheme 从 CSS 变量回读取色（reCssVar），node 里没有，打空桩即可：
// 本测试只关心主题对象上的 aria 开关，不关心色值。
globalThis.getComputedStyle = () => ({ getPropertyValue: () => '' });

// 主题：aria.enabled 必须开着
const theme = buildTheme();

// 描述：标题取最近小节；series 名称与类型入文案
const secH2 = { textContent: 'VIX 波动率指数' };
const section = { querySelector: (s) => (s === 'h2' ? secH2 : null) };
const dom = { closest: (sel) => (sel.includes('.re-sec') ? section : null) };
const lineOnly = R._ariaText({ series: [{ name: 'VIX', type: 'line' }] }, dom);
const mixed = R._ariaText({
  series: [
    { name: '分布', type: 'line' },
    { name: '本窗口', type: 'scatter' },
    { name: '分布', type: 'line' },
  ],
}, dom);
// 页面显式写了 description 时不覆盖
const custom = R._ariaText({ aria: { description: '自定义' }, series: [] }, dom);

// 研判格标题标签类型
const row = R.judgeRow([['水平', '第一句'], ['展望', '第二句']]);

console.log(JSON.stringify({
  ariaEnabled: theme.aria && theme.aria.enabled,
  lineOnly, mixed, custom,
  h3Count: (row.match(/<h3 class="sig-title">/g) || []).length,
  divTitleLeft: row.includes('<div class="sig-title">'),
}));
"""


def _run() -> dict:
    if shutil.which("node") is None:
        pytest.skip("无 node，跳过图表可及性检查")
    src = "\n".join([THEME.read_text(encoding="utf-8"), COMMON.read_text(encoding="utf-8"), BODY])
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
        tmp = f.name
    try:
        r = subprocess.run(["node", tmp], capture_output=True, text=True, encoding="utf-8")
    finally:
        Path(tmp).unlink(missing_ok=True)
    assert r.returncode == 0, r.stderr[:400]
    return json.loads(r.stdout)


def test_theme_enables_aria() -> None:
    assert _run()["ariaEnabled"] is True


def test_description_names_section_and_series() -> None:
    out = _run()
    assert out["lineOnly"].startswith("VIX 波动率指数（折线图）")
    assert "VIX" in out["lineOnly"]
    # 类型去重、系列名去重，不把同一系列念两遍
    assert out["mixed"].count("折线图") == 1
    assert "散点图" in out["mixed"]
    assert out["mixed"].count("分布") == 1


def test_page_supplied_description_wins() -> None:
    assert _run()["custom"] == "自定义"


def test_judge_row_titles_are_headings() -> None:
    out = _run()
    assert out["h3Count"] == 2
    assert out["divTitleLeft"] is False


def test_layout_exposes_main_landmark() -> None:
    """正文容器是 <main>，屏幕阅读器的地标列表才有「主内容」一项。"""
    src = (ROOT / "frontend" / "src" / "layouts" / "TopicLayout.astro").read_text(encoding="utf-8")
    assert '<main class="re-root" id="re-main">' in src
    assert '<div class="re-root" id="re-main">' not in src
