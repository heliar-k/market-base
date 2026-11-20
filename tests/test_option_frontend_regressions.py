import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CRYPTO_OPTIONS = ROOT / "frontend/src/pages/assets/crypto-options.astro"


GEX_CHART_PROBE = r"""
__BUILD_GEX__

// 1) 新快照：keepFlip 保留两侧 <0.5M 的相邻行权价，axisIdx 精准插值，hasProfile 为 true
const r1 = buildGexChartData({
  gamma_flip: 102.5,
  wall: [
    { strike: 90, gex_m: -5.0, call_gex_m: 0.0, put_gex_m: -5.0, profile_gex_m: -20.0 },
    { strike: 100, gex_m: -0.1, call_gex_m: 0.2, put_gex_m: -0.3, profile_gex_m: -2.5 },
    { strike: 105, gex_m: 0.1, call_gex_m: 0.3, put_gex_m: -0.2, profile_gex_m: 2.5 },
    { strike: 115, gex_m: 6.0, call_gex_m: 6.0, put_gex_m: 0.0, profile_gex_m: 18.0 },
  ],
});
if (r1.ss.join(',') !== '90,100,105,115') throw new Error('keepFlip 失败: ' + r1.ss.join(','));
if (!r1.hasProfile) throw new Error('hasProfile 应为 true');
if (Math.abs(r1.axisIdx(102.5) - 1.5) > 1e-9) throw new Error('axisIdx 错误: ' + r1.axisIdx(102.5));

// 2) 旧快照 cum_gex_m 回退：hasProfile 为 false 且 profile 取 cum_gex_m
const r2 = buildGexChartData({
  gamma_flip: 100,
  wall: [
    { strike: 95, gex_m: -4.0, call_gex_m: 0, put_gex_m: -4.0, cum_gex_m: -4.0 },
    { strike: 100, gex_m: 6.0, call_gex_m: 6.0, put_gex_m: 0, cum_gex_m: 2.0 },
  ],
});
if (r2.hasProfile) throw new Error('旧快照 hasProfile 应为 false');
if (r2.profile.join(',') !== '-4,2') throw new Error('旧快照回退值错误: ' + r2.profile.join(','));

// 3) gex_dist 回退（无 call_gex_m / put_gex_m）：派生柱子不得为空
const r3 = buildGexChartData({
  gamma_flip: null,
  gex_dist: [
    { strike: 95, gex_m: -3.0 },
    { strike: 105, gex_m: 4.0 },
  ],
});
if (r3.callBars.join(',') !== '0,4' || r3.putBars.join(',') !== '-3,0') {
  throw new Error('gex_dist 派生柱子错误: ' + r3.callBars + ' / ' + r3.putBars);
}
"""


def test_gex_chart_uses_profile_series_and_keeps_flip():
    text = Path("frontend/src/pages/assets/equities/options.astro").read_text(encoding="utf-8")
    assert "smooth: false" in text
    assert "hasProfile ? '净 GEX 曲线' : '累计 GEX'" in text
    if shutil.which("node") is None:
        pytest.skip("无 node，跳过 buildGexChartData 行为验证")
    probe = GEX_CHART_PROBE.replace("__BUILD_GEX__", _fn_src(text, "buildGexChartData"))
    r = subprocess.run(
        ["node", "-e", probe], capture_output=True, text=True, encoding="utf-8", cwd=ROOT
    )
    assert r.returncode == 0, r.stderr[:600]


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
