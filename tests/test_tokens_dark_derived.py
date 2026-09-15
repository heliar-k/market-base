"""tokens.css 派生 token 防漂移（不起浏览器，纯文本检查）。

CSS 自定义属性在声明所在元素上做 var() 替换：写在 `:root`（html）里、
值引用主题原语的派生 token（如 `--tooltip-bg: color-mix(... var(--surface-alt) ...)`）
看不到 `body.dark` 的覆写，暗色模式下仍是亮色值——
全站 ECharts tooltip 夜间白底即此 bug。
所以派生 token 必须挂在覆盖 body 的选择器上
（现状：tokens.css 末尾的 `:root, body.dark` 段）。
"""

from __future__ import annotations

import re
from pathlib import Path

CSS_DIR = Path(__file__).resolve().parent.parent / "static/css"
RULE = re.compile(r"(?P<sel>[^{}]+)\{(?P<body>[^{}]*)\}")
DERIVED = re.compile(r"--[\w-]+\s*:\s*[^;]*var\(--", re.M)


def test_no_derived_token_in_root_only_rule() -> None:
    """css 文件 `:root` 规则里不许有 var() 派生声明（暗色取不到 body.dark 覆写）。"""
    bad = [
        f"{path.name} {s}: {m.group(0).strip()}"
        for path in sorted(CSS_DIR.glob("*.css"))
        for s, b in (
            (m["sel"].strip(), m["body"])
            for m in RULE.finditer(path.read_text(encoding="utf-8"))
        )
        if s == ":root"
        for m in DERIVED.finditer(b)
    ]
    assert not bad, (
        "派生 token 请移到 tokens.css 末尾的 `:root, body.dark` 段：\n" + "\n".join(bad)
    )
