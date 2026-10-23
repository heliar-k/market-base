"""伴读抽屉的两条布局约定：操作条贴输入框上沿、浮钮全宽度可拖拽贴边；
以及「所有宽度都是整屏模态」——不按视口分栏、不留 380px 侧栏档。

抽屉在 1600×1000 实测头部按钮到输入框 845px，操作放头部 = 用户先横跨大半屏再
回来打字；浮钮原先只有 ≤768 能拖，宽屏固定右上角、改不了位置。整屏档曾经只给
≤768，宽屏另走「桌面分栏」（正文让位 + 左缘拖拽柄），现在统一成整屏。
三条都在源码结构层钉住，别再退回「按钮全塞头部 / 浮钮只在窄屏可动 / 宽屏分栏」。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHAT = ROOT / "frontend" / "public" / "js" / "ai-chat.js"
CSS = ROOT / "frontend" / "public" / "css" / "app.css"


def _shell() -> str:
    text = CHAT.read_text("utf-8")
    return text[text.index("const SHELL =") : text.index("function panel(")]


def test_actions_sit_on_composer_top() -> None:
    shell = _shell()
    foot = shell[shell.index('<div class="ai-foot">') :]
    assert 'class="ai-tools"' in foot, "操作条必须在 .ai-foot 里"
    assert foot.index('class="ai-tools"') < foot.index('class="ai-input"'), (
        "操作条要在输入框上沿（新对话/历史/存笔记贴着手打字的地方）"
    )
    head = shell[shell.index('<div class="ai-head">') : shell.index('<div class="ai-cfg"')]
    acts = re.findall(r'data-act="([^"]+)"', head)
    assert acts == ["close"], f"抽屉头只留关闭（其余操作在输入框上沿），实际：{acts}"


def test_more_menu_opens_upward() -> None:
    css = CSS.read_text("utf-8")
    block = css[css.index(".ai-menu {") :]
    block = block[: block.index("}")]
    assert "bottom: calc(100% + 4px)" in block, "⋯ 菜单在抽屉底部，只能往上弹（top 会顶出屏幕）"


def test_float_drag_not_gated_by_narrow() -> None:
    text = CHAT.read_text("utf-8")
    assert "if (!floatBtn || !isNarrow()) return;" not in text, "fabPlace 不能再按窄屏早退"
    assert "if (!isNarrow() || (e.button" not in text, "浮钮拖拽不能只开给窄屏"
    # 没拖过的默认位仍分两档：窄屏按 CSS 的 bottom 反推，桌面回 CSS 的 top:8
    assert "if (!isNarrow()) return 8;" in text


def test_drawer_is_full_screen_at_every_width() -> None:
    """抽屉只有一档：100vw 整屏。宽度不许再按视口/偏好分叉（380px 侧栏、--ai-w 让位）。"""
    css = CSS.read_text("utf-8")
    block = css[css.index(".ai-drawer {") :]
    block = block[: block.index("}")]
    assert "width: 100vw" in block, "抽屉必须是整屏宽（与手机同一形态）"
    assert "380px" not in block, "不能再有 380px 侧栏档"
    for dead in ("html.ai-pane", ".ai-grip", "--ai-w", "ai-resizing", ".ai-check"):
        assert dead not in css, f"分栏遗留样式还在：{dead}"
    assert "@media (max-width: 768px) { .ai-drawer" not in css, "整屏不需要再按 768 兜一次"


def test_drawer_modal_on_every_open() -> None:
    """打开即模态：不分宽窄，一律 aria-modal + 锁滚动 + 接管返回键。"""
    text = CHAT.read_text("utf-8")
    open_fn = text[text.index("function open()") : text.index("function close(")]
    assert "if (isNarrow())" not in open_fn, "模态不能再看宽度分档"
    assert "drawer.setAttribute('aria-modal', 'true')" in open_fn
    assert "lockScroll();" in open_fn
    assert "pushDrawerState();" in open_fn
    # 分栏模块已删：谁再 import 回来就是回退
    assert "ai-pane.js" not in text
    assert not (ROOT / "frontend" / "public" / "js" / "ai-pane.js").exists()
    assert 'data-pane="1"' not in text, "设置里不该再有「桌面分栏」开关"
    assert 'class="ai-grip"' not in _shell()
