"""伴读抽屉/浮钮的三条布局约定：操作条贴输入框上沿、浮钮全宽度可拖拽贴边、
浮钮全宽度都是窄屏那档 40px 圆胶囊（右下角）。

宽屏分栏后抽屉有一整屏高（1600×1000 实测头部按钮到输入框 845px），操作放头部 =
用户先横跨半屏再回来打字；浮钮原先只有 ≤768 能拖/才是圆 FAB，宽屏是右上角 30px
直角钮、改不了位置。三条都在源码结构层钉住，别再退回「按钮全塞头部 /
浮钮只在窄屏可动 / 宽屏另画一个右上角钮」。
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
    # 默认落点也不再分档：任何宽度都是「视口高 - 底距 - 钮高」
    assert "if (!isNarrow()) return 8;" not in text, "宽屏不能再回 top:8 那档"
    assert "if (isNarrow()) fabPlace(" not in text, "fabLayout 不能只在窄屏贴边"


def test_float_button_is_narrow_fab_at_every_width() -> None:
    """浮钮只有一档：右下角 40px 圆胶囊。不许再按视口给桌面画个右上角直角钮。"""
    css = CSS.read_text("utf-8")
    block = css[css.index(".ai-float {") :]
    block = block[: block.index("}")]
    for prop in ("width: 40px", "height: 40px", "border-radius: 999px", "bottom: calc(14px"):
        assert prop in block, f"浮钮基础样式缺 {prop}（应沿用窄屏 FAB 档）"
    assert "top: auto" in block and "right: 8px" in block, "浮钮默认位在右下角"
    # 不许再有「窄屏才换形态」的媒体查询：其余命中 .ai-float 的选择器只能是
    # app-shell 底距 / 分栏隐藏这类单属性覆盖，不能重定义尺寸或圆角。
    assert "@media (max-width: 768px) { .ai-float" not in css
    assert "body .ai-float {" not in css
    sels = {
        " ".join(s.split())
        for s in re.findall(
            r"([^{}]*\.ai-float[^{}]*)\{", re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        )
    }
    assert sels <= {
        ".ai-float",
        ".ai-float:hover",
        ".ai-float:active",
        ".ai-float.fab-dragging",
        "body.app-shell .ai-float",
        "html.ai-pane .ai-float",
    }, f"浮钮多出形态选择器：{sorted(sels)}"
