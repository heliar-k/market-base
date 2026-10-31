import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CRYPTO_OPTIONS = ROOT / "frontend/src/pages/assets/crypto-options.astro"


def test_gex_chart_uses_full_cumulative_series_and_keeps_flip():
    text = Path("frontend/src/pages/assets/equities/options.astro").read_text()
    assert "x.cum_gex_m" in text
    assert "x.strike === v.gamma_flip" in text
    assert "const cum = g.map(x => cumByStrike.get(x.strike))" in text


def test_oi_panel_uses_near_spot_band():
    text = Path("frontend/src/pages/assets/equities/options.astro").read_text()
    assert "近现价 OI 集中度 Top 20" in text
    assert "ATM ±10%" in text


def test_options_page_header_uses_et_snapshot_date():
    text = Path("frontend/src/pages/assets/equities/options.astro").read_text()
    assert "D.snapshot_date || (D.date && D.date.slice(0, 10))" in text


def _fn_src(text: str, name: str) -> str:
    """从 .astro 抠出 `const <name> = (...) => {...};` 整段（花括号配平）。

    模板串里的 ${} 也成对，直接数花括号即可。
    """
    start = text.index(f"const {name} = ")
    i = text.index("{", start)
    depth = 0
    while True:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 2]
        i += 1


LADDER_PROBE = r"""
__COMMON__
const fmt = R.num;
const usd0 = v => (v == null ? '—' : '$' + fmt(v, 0));
__LADDER__

// 价位高的必须在上面：↑ 才是「现价之上」。取 BTC 实盘形态（put 全在现价下方、call 全在上方）
const o = {
  top_calls: [{ strike: 82000, oi: 706 }, { strike: 83000, oi: 907 }, { strike: 88000, oi: 584 },
    { strike: 90000, oi: 2815 }, { strike: 95000, oi: 2332 }],
  top_puts: [{ strike: 70000, oi: 575 }, { strike: 72000, oi: 513 }, { strike: 74000, oi: 767 },
    { strike: 75000, oi: 590 }, { strike: 76000, oi: 636 }],
  near_call_wall: 90000, near_put_wall: 74000, call_wall: 90000, put_wall: 60000,
};
const spot = 77953;
const html = ladderRows(o, spot);
const ks = h => [...h.matchAll(/class="sw-k">\$([\d,]+)</g)]
  .map(m => Number(m[1].replace(/,/g, '')));
const strikes = ks(html);
if (strikes.join(',') !== '95000,90000,88000,83000,82000,76000,75000,74000,72000,70000') {
  throw new Error('阶梯顺序不是价位降序：' + strikes.join(','));
}
const [above, below] = html.split('class="sw-spot"');
if (below === undefined) throw new Error('现价分隔线没插进阶梯里');
for (const s of ks(above)) {
  if (!(s > spot)) throw new Error(`分隔线之上的 $${s} 不在现价 $${spot} 之上`);
}
for (const s of ks(below)) {
  if (!(s <= spot)) throw new Error(`分隔线之下的 $${s} 不在现价 $${spot} 之下`);
}
// 墙位胶囊落在自己那一侧
if (!/class="sw-tag call">墙/.test(above)) throw new Error('Call 墙没画在分隔线之上');
if (!/class="sw-tag put">墙/.test(below)) throw new Error('Put 墙没画在分隔线之下');
"""


def test_strike_wall_ladder_descends_with_spot_line_between() -> None:
    """Strike Wall 阶梯必须价位高的在上：升序渲染时 Put 墙（下方支撑）会跑到现价线上方、
    Call 墙跑到下方，分隔线那两个箭头方向与屏幕相反。这里跑真函数验顺序与现价线的插入位置。"""
    if shutil.which("node") is None:
        pytest.skip("无 node，跳过阶梯顺序检查")
    text = CRYPTO_OPTIONS.read_text(encoding="utf-8")
    probe = LADDER_PROBE.replace(
        "__COMMON__", (ROOT / "frontend/public/js/rates-common.js").read_text(encoding="utf-8")
    ).replace("__LADDER__", _fn_src(text, "ladderRows"))
    r = subprocess.run(
        ["node", "-e", probe], capture_output=True, text=True, encoding="utf-8", cwd=ROOT
    )
    assert r.returncode == 0, r.stderr[:600]


def test_crypto_walls_keep_both_sides_and_use_near_max_pain():
    text = Path("frontend/src/pages/assets/crypto-options.astro").read_text()
    assert "byStrike.set" not in text
    assert "o.near_max_pain" in text
    assert "optB.near_max_pain" in text
    assert "optE.near_max_pain" in text
