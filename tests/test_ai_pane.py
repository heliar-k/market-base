"""伴读抽屉的桌面分栏纯逻辑（frontend/public/js/ai-pane.js）—— node 直接跑。

分栏的坑全在「宽度该被谁夹住」：
- 上限由**正文地板**反推（不是拍一个 50vw），否则 1440 屏会被拖成「1024 窗口那档」；
- 下限由抽屉头的按钮实测宽度定（宽 300 时 6 个按钮就溢出）；
- 断点 = 地板 + 最小抽屉 + 导航 + 内距，改任一常量断点自动跟着走 —— 所以这里把它钉死。
  注意断点只活在 JS（ai-pane.js 的 paneMode + ai-chat.js 的 matchMedia），app.css 里没有
  1324 的媒体查询；CSS 那份 768 是「抽屉整屏」档，改它要连 NARROW_MAX 一起改。
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.node_runner import run_node

ROOT = Path(__file__).resolve().parent.parent
PANE = ROOT / "frontend" / "public" / "js" / "ai-pane.js"

BODY = """
const m = await import(%s);
const widths = [1280, 1324, 1440, 1512, 1680, 1920, 2560];
console.log(JSON.stringify({
  min: m.PANE_MIN,
  max: m.PANE_MAX,
  def: m.PANE_DEFAULT,
  breakpoint: m.PANE_BREAKPOINT,
  mq: m.PANE_MQ,
  maxAt: Object.fromEntries(widths.map((w) => [w, m.paneMaxWidth(w)])),
  clamp: {
    below: m.clampPaneWidth(100, 1440),
    above: m.clampPaneWidth(9999, 1440),
    stored: m.clampPaneWidth(560, 1680),
    bad: m.clampPaneWidth('abc', 1440),
    zero: m.clampPaneWidth(0, 1440),
    negative: m.clampPaneWidth(-220, 1440),
    round: m.clampPaneWidth(388.6, 1920),
    tightScreen: m.clampPaneWidth(500, 1280),
  },
  mode: {
    phone: m.paneMode(390, true),
    tablet: m.paneMode(900, true),
    justUnder: m.paneMode(m.PANE_BREAKPOINT - 1, true),
    exactly: m.paneMode(m.PANE_BREAKPOINT, true),
    wide: m.paneMode(1920, true),
    disabled: m.paneMode(1920, false),
    phoneDisabled: m.paneMode(390, false),
  },
  pushes: [m.panePushes(1920, true), m.panePushes(1920, false), m.panePushes(900, true)],
}));
"""


def _run() -> dict:
    return run_node(BODY % json.dumps(PANE.as_uri()), timeout=30)


def test_breakpoint_is_derived_from_floor() -> None:
    """断点 = 导航 + 正文地板 + 内距 + 最小抽屉 = 1324（ai-chat.js 的 matchMedia 用它）。

    改任一项都要同步改这里 —— 这条断言就是防漂移的那颗钉子。
    """
    out = _run()
    assert out["breakpoint"] == 1324
    assert out["mq"] == "(min-width: 1324px)"
    assert out["min"] == 320
    assert out["max"] == 640
    assert out["def"] == 380


def test_max_width_leaves_content_floor() -> None:
    """上限随视口收缩：1440 → 436（正文留 700），1680 起封顶 640。"""
    out = _run()
    assert out["maxAt"] == {
        "1280": 276,
        "1324": 320,
        "1440": 436,
        "1512": 508,
        "1680": 640,
        "1920": 640,
        "2560": 640,
    }


def test_clamp_handles_bad_and_extreme_input() -> None:
    out = _run()
    c = out["clamp"]
    assert c["below"] == 320  # 拖过头 → 最小宽
    assert c["above"] == 436  # 拖过头 → 正文地板顶住
    assert c["stored"] == 560  # 记住的宽度在视口容得下时原样用
    assert c["bad"] == 380  # 脏值（localStorage 被手改）回落默认
    assert c["zero"] == 320  # 0 不是「未设置」的哨兵，按越界夹到最小宽
    assert c["negative"] == 320  # 拖过下限算出来是负数，必须粘在 320（曾错误回落到 380）
    assert c["round"] == 389  # 亚像素不写进 CSS 变量
    # 1280 屏上「上限」只有 216 < 320：这时分栏本就不该启用，但夹取不能返回更小的值
    assert c["tightScreen"] == 320


def test_mode_picks_full_overlay_or_pane() -> None:
    out = _run()
    m = out["mode"]
    assert m == {
        "phone": "full",  # ≤768 整屏模态（现有行为不变）
        "tablet": "overlay",  # 不宽不窄 → 浮层
        "justUnder": "overlay",  # 断点差 1px 就回落，不留半截让位
        "exactly": "pane",
        "wide": "pane",
        "disabled": "overlay",  # 设置里关掉
        "phoneDisabled": "full",  # 关掉也不影响整屏档
    }
    assert out["pushes"] == [True, False, False]
