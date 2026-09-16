"""图例与曲线一致性（reSyncLegend）——SPA 与所有专题页折线图共用，改坏了这里红。

色标单源在 static/js/echarts-theme.js（SPA 与专题页都加载的唯一 ECharts 共享脚本），
R.lineOption 收尾调用它。两个文件拼进同一个临时脚本用 node 跑（浏览器 API 打桩），
断言图例色标按 series 线型/取色推导。
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
THEME = ROOT / "static" / "js" / "echarts-theme.js"
COMMON = ROOT / "static" / "js" / "rates-common.js"

# 拼在两个源文件之后执行：RE_LEGEND / reSyncLegend / R 同一模块作用域，可直接引用
BODY = """
globalThis.window = { addEventListener() {} };
globalThis.document = { body: { classList: { contains: () => false } } };

const shape = (o, name) => {
  const it = (o.legend.data || []).find((d) => d.name === name);
  if (!it || !it.icon) return 'none';
  return {
    [RE_LEGEND.solid]: 'solid', [RE_LEGEND.dashed]: 'dashed',
    [RE_LEGEND.dotted]: 'dotted', [RE_LEGEND.dashdot]: 'dashdot',
    [RE_LEGEND.band]: 'band', [RE_LEGEND.dotline]: 'dotline',
  }[it.icon];
};
const out = {};

// 线型 + 色块取色（不补 series.color 时图例落到主题调色板，与曲线对不上）
let o = R.lineOption({ legend: { data: ['A', 'B', 'C', 'D'] }, series: [
  { name: 'A', type: 'line', lineStyle: { color: '#111' } },
  { name: 'B', type: 'line', lineStyle: { color: '#222', type: 'dashed' } },
  { name: 'C', type: 'line', lineStyle: { color: '#333', type: [6, 3, 2, 3] } },
  { name: 'D', type: 'line', lineStyle: { color: '#444' }, symbol: 'circle' },
] }, {});
out.lineTypes = ['A', 'B', 'C', 'D'].map((n) => shape(o, n));
out.colors = o.series.map((s) => s.color);

// 阴影带：不透明 areaStyle = 色块；areaStyle.opacity === 0（堆叠技巧）仍算实线
o = R.lineOption({ legend: { data: ['line', 'band'] }, series: [
  { name: 'line', type: 'line', lineStyle: { color: '#111' },
    areaStyle: { opacity: 0 } },
  { name: 'band', type: 'line', color: '#08f80', lineStyle: { opacity: 0 },
    areaStyle: { color: { type: 'linear' } } },
] }, {});
out.areas = ['line', 'band'].map((n) => shape(o, n));
out.bandColorKept = o.series[1].color;

// 非 line（柱/散点）与隐藏图例不动；无 legend.data 时按 series 自动补
o = R.lineOption({ legend: { data: ['bar'] }, series: [
  { name: 'bar', type: 'bar', itemStyle: { color: '#111' } },
] }, {});
out.barKept = shape(o, 'bar');
o = R.lineOption(
  { legend: { show: false }, series: [{ name: 'x', type: 'line' }] }, {}
);
out.hiddenUntouched = o.legend.data === undefined;
o = R.lineOption({ legend: { top: 0 }, series: [
  { name: 'y', type: 'line', lineStyle: { color: '#111', type: 'dotted' } },
] }, {});
out.autoData = [o.legend.data[0].name, shape(o, 'y')];

// 不走 lineOption 的图（SPA / labor / liquidity / fed）包一层即可
o = reSyncLegend({ legend: { top: 0 }, series: [
  { name: 'p', type: 'line', itemStyle: { color: '#555' } },
  { name: 'q', type: 'line', lineStyle: { color: '#666', type: 'dashed' } },
] });
out.wrapped = [['p', 'q'].map((n) => shape(o, n)), o.series.map((s) => s.color)];

console.log(JSON.stringify(out));
"""


def _run() -> dict:
    if shutil.which("node") is None:
        pytest.skip("无 node，跳过图例一致性检查")
    src = "\n".join(
        [
            THEME.read_text(encoding="utf-8"),
            COMMON.read_text(encoding="utf-8"),
            BODY,
        ]
    )
    with tempfile.NamedTemporaryFile(
        "w", suffix=".js", delete=False, encoding="utf-8"
    ) as f:
        f.write(src)
        tmp = f.name
    try:
        r = subprocess.run(
            ["node", tmp], capture_output=True, text=True, encoding="utf-8"
        )
    finally:
        Path(tmp).unlink(missing_ok=True)
    assert r.returncode == 0, r.stderr[:400]
    return json.loads(r.stdout)


def test_legend_matches_series() -> None:
    out = _run()
    assert out["lineTypes"] == ["solid", "dashed", "dashdot", "dotline"]
    assert out["colors"] == ["#111", "#222", "#333", "#444"]
    assert out["areas"] == ["solid", "band"]
    assert out["bandColorKept"] == "#08f80"
    assert out["barKept"] == "none"
    assert out["hiddenUntouched"] is True
    assert out["autoData"] == ["y", "dotted"]
    assert out["wrapped"] == [["solid", "dashed"], ["#555", "#666"]]
