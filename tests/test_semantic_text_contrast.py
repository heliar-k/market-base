"""语义色「文本档」对比度防漂移（不起浏览器，纯文本 + WCAG 计算）。

背景：--color-up/down/neutral/hawk/dove 是 TradingView 蜡烛色，同时充当 K 线填充、
ECharts series 调色板与热力图发散端点（图形，不看对比度），但也被当正文色用。
亮色下实测原语作文本最低只有 2.13:1（dove 在 18% 同色徽章底上），远低于 AA 4.5:1。
故拆出 --color-*-text 档，只给 `color:` 用；图形仍用原语，蜡烛观感不变。

本测试锁两件事：
1. 每个 -text 档在三种真实底色上（卡片 / 页面底 / 同色透明徽章底）都 ≥ 4.5:1；
2. CSS 里不再残留「拿图形原语当文字色」的写法（否则拆分形同没做）。

徽章底是 color-mix(语义色 N%, transparent) 叠在卡片上——同色相浅底比白底更难，
所以按它反压，而不是按白卡反压。
"""

from __future__ import annotations

import re
from pathlib import Path

CSS_DIR = Path(__file__).resolve().parent.parent / "frontend/public/css"
RULE = re.compile(r"(?P<sel>[^{}]+)\{(?P<body>[^{}]*)\}")
DECL = re.compile(r"(--[\w-]+)\s*:\s*([^;{}]+)")
VARREF = re.compile(r"var\((--[\w-]+)\)")

# 徽章底占比：亮色 16%（hawk/dove 18%），暗色 22%（hawk/dove 25%）
# ——与 tokens.css 的 color-mix 一致
TEXT_TOKENS = {
    "up": ("--color-up-text", "--color-up", 0.16),
    "down": ("--color-down-text", "--color-down", 0.16),
    "neutral": ("--color-neutral-text", "--color-neutral", 0.16),
    "hawk": ("--color-hawk-text", "--color-hawk", 0.18),
    "dove": ("--color-dove-text", "--color-dove", 0.18),
}
AA_NORMAL = 4.5


def _rules(text: str):
    """去注释后逐条 (selector, body)。不去注释会把 `/* ... */\n:root` 整块当选择器。"""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    for m in RULE.finditer(text):
        yield " ".join(m["sel"].split()), m["body"]


def _themes() -> dict[str, dict[str, str]]:
    """读 tokens.css + app.css，按 :root（亮）/ body.dark（暗）收集字面色值，
    并就地展开 var() 引用。
    """
    decls: dict[str, dict[str, str]] = {":root": {}, "body.dark": {}}
    for path in sorted(CSS_DIR.glob("*.css")):
        for sel, body in _rules(path.read_text(encoding="utf-8")):
            theme = (
                ":root"
                if sel == ":root"
                else "body.dark"
                if sel == "body.dark"
                else None
            )
            if theme is None:
                continue
            for d in DECL.finditer(body):
                decls[theme].setdefault(d.group(1), d.group(2).strip())

    def resolve(name: str, theme: str, seen: frozenset[str] = frozenset()) -> str:
        val = decls[theme].get(name) or decls[":root"].get(name, "")
        for ref in VARREF.findall(val):
            if ref in seen:
                continue
            val = val.replace(f"var({ref})", resolve(ref, theme, seen | {name}))
        return val.strip()

    return {t: {k: resolve(k, t) for k in decls[t] | decls[":root"]} for t in decls}


def _rgb(h: str) -> tuple[float, ...]:
    h = h.strip().lstrip("#")
    if len(h) == 3:  # #fff 缩写（app.css 的 --surface 就是）
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))


def _lum(h: str) -> float:
    c = [(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4) for v in _rgb(h)]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _cr(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _tint(fg: str, bg: str, p: float) -> str:
    """color-mix(fg p%, transparent) 叠在 bg 上的实际观感色。

    _rgb 已归一到 0~1，混完要乘回 255。
    """
    f, b = _rgb(fg), _rgb(bg)
    return "#%02x%02x%02x" % tuple(
        round((f[i] * p + b[i] * (1 - p)) * 255) for i in range(3)
    )


def test_text_tokens_clear_aa_on_real_backgrounds() -> None:
    themes = _themes()
    bad: list[str] = []
    for theme, surface in ((":root", "--surface"), ("body.dark", "--surface")):
        card = themes[theme][surface]
        page = themes[theme]["--bg"]
        for name, (tok, prim, pct) in TEXT_TOKENS.items():
            text, graphic = themes[theme][tok], themes[theme][prim]
            if not text.startswith("#") or not graphic.startswith("#"):
                bad.append(f"{theme} {name}: 未解析到字面色（{text!r}）")
                continue
            for label, bg in (
                ("卡片", card),
                ("页面底", page),
                ("徽章底", _tint(graphic, card, pct)),
            ):
                if (got := _cr(text, bg)) < AA_NORMAL:
                    bad.append(
                        f"{theme} {name}-text {text} on {label} {bg} = {got:.2f}"
                    )
    assert not bad, "-text 档跌破 AA 4.5:1：\n" + "\n".join(bad)


def test_css_does_not_use_graphic_primitives_as_text_color() -> None:
    """`color: var(--color-up)` 这类写法必须已迁到 -text 档。

    border-/background-/accent-color 不算。
    """
    pat = re.compile(r"(?<![-\w])color:\s*var\(--color-(?:up|down|neutral|hawk|dove)\)")
    bad = [
        f"{p.name}:{i}:{ln.strip()}"
        for p in sorted(CSS_DIR.glob("*.css"))
        for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if pat.search(ln)
    ]
    assert not bad, "文字色请走 --color-*-text（图形档对比度不足）：\n" + "\n".join(bad)
