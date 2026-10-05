"""窄屏栅格压缩 R._clampGrid（rates-common.js）——全站图表唯一出口的纯函数守卫。

背景：主题 grid 带 containLabel: true，页面又传桌面尺度的数字 left，轴标签的位
被留两遍；_clampGrid 在画布 <480px 时把 left 压到 8。三个边界（阈值、
containLabel:false 跳过、right 不动）任一被改坏，这里红。
node 直接跑源文件，不依赖浏览器（_clampGrid 无 DOM 依赖）。
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
COMMON = ROOT / "frontend" / "public" / "js" / "rates-common.js"

# _clampGrid 是 R 对象里的方法，源文件顶层引用了 window/document；用 Function
# 把这段抠出来单独执行，避免为一个纯函数给 node 打桩整套浏览器 API。
BODY = """
const src = require('fs').readFileSync(process.argv[2], 'utf8');
const head = '_clampGrid(opt, w) {';
const start = src.indexOf(head);
const end = src.indexOf('\\n  },', start);
const fn = new Function('opt', 'w', src.slice(start + head.length, end));

const g = (o) => (Array.isArray(o.grid) ? o.grid : [o.grid])[0];
const out = {};

// 窄屏：数字 left 压到 8
out.narrowLeft = g(fn({ grid: { left: 48, right: 16 } }, 390)).left;
// 已 <=8 不动（不回弹）
out.alreadySmall = g(fn({ grid: { left: 4 } }, 390)).left;
// 宽屏：原样
out.wide = g(fn({ grid: { left: 48 } }, 1440)).left;
// 阈值边界：480 不压，479 压
out.at480 = g(fn({ grid: { left: 48 } }, 480)).left;
out.at479 = g(fn({ grid: { left: 48 } }, 479)).left;
// containLabel:false 跳过（left 是轴标签本身的位子）
out.noContain = g(fn({ grid: { left: 56, containLabel: false } }, 390)).left;
// right 永不动（柱末标注 / 双轴右轴名的位子，containLabel 不保护）
out.rightKept = g(fn({ grid: { left: 110, right: 55 } }, 390)).right;
// 百分比 left 不动（非数字，交给 ECharts 自己算）
out.percent = g(fn({ grid: { left: '3%' } }, 390)).left;
// 数组 grid 全压
const arr = fn({ grid: [{ left: 48 }, { left: 60 }] }, 390).grid;
out.arrAll = [arr[0].left, arr[1].left];
// 无 grid / 空宽度：原样返回不炸
out.noGrid = fn({ series: [] }, 390);
out.zeroW = g(fn({ grid: { left: 48 } }, 0)).left;

console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result():
    if not shutil.which("node"):
        pytest.skip("node 不可用")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(BODY)
        script = f.name
    proc = subprocess.run(
        ["node", script, str(COMMON)], capture_output=True, text=True, timeout=30
    )
    Path(script).unlink(missing_ok=True)
    assert proc.returncode == 0, f"node 执行失败：{proc.stderr}"
    return json_loads(proc.stdout)


def json_loads(s):
    import json

    return json.loads(s)


def test_narrow_clamps_left(result):
    assert result["narrowLeft"] == 8
    assert result["at479"] == 8


def test_wide_untouched(result):
    assert result["wide"] == 48
    assert result["at480"] == 48  # 阈值边界：>=480 不压


def test_already_small_not_resized(result):
    assert result["alreadySmall"] == 4  # min(4,8)=4，不回弹到 8


def test_contain_label_false_skipped(result):
    # yc_change 那类：left 就是轴标签位子，压了会把标签挤出画布
    assert result["noContain"] == 56


def test_right_never_clamped(result):
    # vol-cross-chart 柱末数值标注靠 right 留位，压了会被裁出画布
    assert result["rightKept"] == 55


def test_percent_left_untouched(result):
    assert result["percent"] == "3%"


def test_array_grid_all_clamped(result):
    assert result["arrAll"] == [8, 8]


def test_no_grid_or_zero_width_safe(result):
    assert result["noGrid"] == {"series": []}
    assert result["zeroW"] == 48  # 隐藏容器（宽 0）不误判成窄屏
