"""语义色「文本档」对比度防漂移（不起浏览器，纯文本 + WCAG 计算）。

背景：--color-up/down/neutral/hawk/dove 是 TradingView 蜡烛色，同时充当 K 线填充、
ECharts series 调色板与热力图发散端点（图形，不看对比度），但也被当正文色用。
亮色下实测原语作文本最低只有 2.13:1（dove 在 18% 同色徽章底上），远低于 AA 4.5:1。
故拆出 --color-*-text 档，只给 `color:` 用；图形仍用原语，蜡烛观感不变。

本测试锁三件事：
1. 每个 -text 档在三种真实底色上（卡片 / 页面底 / 同色透明徽章底）都 ≥ 4.5:1；
2. CSS 里不再残留「拿图形原语当文字色」的写法（否则拆分形同没做）；
3. .astro island 拼的内联 style 与共享 JS 拼的 style 也不残留同样写法
   （CSS 目录扫不到这两处，见 islands_and_shared_js 那个测试）。

徽章底是 color-mix(语义色 N%, transparent) 叠在卡片上——同色相浅底比白底更难，
所以按它反压，而不是按白卡反压。

第 3 项曾长期 xfail（前端 island 内联样式 49 处违规）——2026-07 已全部改完并去掉标记。
清单：`uv run python -m pytest tests/test_semantic_text_contrast.py -q`。
已知覆盖边界：ECharts `label.color` / `axisLabel.color` 这类 JS 对象键里的**文字**色
静态区分不了（与线色同形），那部分靠 `R.colors()` 的 greenText/redText/orangeText
三个文字档单源人工守。

后端下发字面 hex 的漏报面已封三条（2026-10 审计第 4 步）：分析模块不得下发 `*_color`
字段、前端不得把 `*_color` 插进 `color:` 槽、CSS 不得拿名字以 `-color` 结尾的自定义属性
写字。brand 也已入名单（第 5 步）：它的文字档 `--accent-ink` 从 #1665d8 再压深到
#155ecb，在 16% brand 徽章底上 4.38 → 4.87，于是 `--color-brand` 可以像其余六组一样
被禁在 `color:` 里。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS_DIR = ROOT / "frontend/public/css"
FRONTEND = ROOT / "frontend"
# island / 共享 JS 的扫描范围（CSS 由上面那个测试负责，不重复扫）
SCAN_TARGETS = ("src/pages/**/*.astro", "public/js/*.js")
RULE = re.compile(r"(?P<sel>[^{}]+)\{(?P<body>[^{}]*)\}")
DECL = re.compile(r"(--[\w-]+)\s*:\s*([^;{}]+)")
VARREF = re.compile(r"var\((--[\w-]+)\)")

# 徽章底占比：亮色 16%（hawk/dove 18%），暗色 22%（hawk/dove 25%）
# ——与 tokens.css 的 color-mix 一致
TEXT_TOKENS = {
    "up": ("--color-up-text", "--color-up", 0.16),
    "down": ("--color-down-text", "--color-down", 0.16),
    "neutral": ("--color-neutral-text", "--color-neutral", 0.16),
    "warn": ("--color-warn-text", "--color-warn", 0.16),
    "brand": ("--accent-ink", "--color-brand", 0.16),
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
    r"""`color: var(--color-up)` 这类写法必须已迁到 -text 档。

    border-/background-/accent-color 不算。`[,)]` 是为了连带兜底的写法一起抓：
    `color: var(--color-warn, var(--accent))` 在只认 `\)` 时是漏的。
    """
    pat = re.compile(
        r"(?<![-\w])color:\s*var\(--color-(?:up|down|neutral|warn|hawk|dove|brand)[,)]"
    )
    bad = [
        f"{p.name}:{i}:{ln.strip()}"
        for p in sorted(CSS_DIR.glob("*.css"))
        for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if pat.search(ln)
    ]
    assert not bad, "文字色请走 --color-*-text（图形档对比度不足）：\n" + "\n".join(bad)


# ── island 内联样式 / 共享 JS ──────────────────────────────────────────────
#
# 图形档 token（tokens.css 里注明「只做填充/色带/线色」的那一档）。--color-*-text
# 与别名 --up/--down/--flat/--warning 属文字档，不在其列。--accent 是 brand 的图形档
# 别名（tokens.css:135），亮色下在页面底只 4.26:1，写字得换 --accent-ink。
GRAPHIC_TOKENS = (
    "--color-warn-light",
    "--color-warn-deep",
    "--color-neutral",
    "--color-brand",
    "--color-hawk",
    "--color-dove",
    "--color-up",
    "--color-down",
    "--color-warn",
    "--accent",
)
# `color:` 属性位。(?<![-\w]) 挡掉 background-color / border-color / accent-color /
# stop-color / text-decoration-color 等前缀，也不会把 token 名里的 `-color-` 当属性名。
COLOR_PROP = re.compile(r"(?<![-\w])color\s*:\s*")
STYLE_ATTR = re.compile("""style\\s*=\\s*(["'`])""")
# 赋值语句：名字 = 右半边（到下一个 `;`，可跨行 → 覆盖多行对象字面量与箭头函数）
ASSIGN = re.compile(
    r"(?:const|let|var|,)\s*([A-Za-z_$][\w$]*)\s*=(?![=>])([^;]{0,400})"
)


def _graphic_pat(tokens: tuple[str, ...] | list[str]) -> re.Pattern[str]:
    r"""图形档 token → 匹配正则（长名在前，免得短名抢在 --color-warn-light 前）。

    存完整 token 名（含 --accent 这种不带 --color- 前缀的别名）；(?![\w-]) 同时挡住
    --accent 误抢 --accent-ink / --accent-light。
    """
    alt = "|".join(re.escape(t) for t in sorted(set(tokens), key=len, reverse=True))
    return re.compile(rf"(?:{alt})(?![\w-])")


GRAPHIC = _graphic_pat(GRAPHIC_TOKENS)


def _is_word(name: str, text: str) -> bool:
    """name 以独立标识符出现（`rg.color` 不算 color，`P.up` 算 P）。"""
    return re.search(rf"(?<![\w$.]){re.escape(name)}(?![\w$-])", text) is not None


def _decl_value(line: str, start: int, quote: str) -> str:
    """`color:` 之后本条声明的文本，扫到引号外的 `;`、`}` 或属性收尾引号为止。

    引号内的 `;` 不算结束（模板三元里 `'a' : 'b'` 常见）；`}` 用来截断 `${...}`，
    免得把后面 `background:var(--color-up)` 这类合法填充读进本条声明里。
    """
    out: list[str] = []
    open_q = ""
    for ch in line[start:]:
        if open_q:
            if ch == open_q:
                open_q = ""
        elif ch == quote or ch in ";}":
            break
        elif ch in "\"'`":
            open_q = ch
        out.append(ch)
    return "".join(out)


def _color_holders(text: str, pat: re.Pattern[str]) -> set[str]:
    """值可能取到图形档 token 的变量名。

    粗粒度按名字聚合：赋值右半边出现图形档 token、或引用了另一个已认定的取色变量，
    就算取色变量（覆盖 `const BUCKET_COLOR = bucketColors()` 这种二跳别名）。
    误纳无害——只有落在 `color:` 属性位才会被报出来。
    """
    stmts = [(m.group(1), m.group(2)) for m in ASSIGN.finditer(text)]
    names: set[str] = set()
    for _ in range(3):
        grown = False
        for name, rhs in stmts:
            if name in names:
                continue
            if pat.search(rhs) or any(_is_word(n, rhs) for n in names):
                names.add(name)
                grown = True
        if not grown:
            break
    return names


def _graphic_text_hits(text: str, origin: str, pat: re.Pattern[str]) -> list[str]:
    """一份源文本里「图形档 token 被用作文字色」的位置（origin:行号）。

    只认两种文字色上下文：HTML/模板里的 `style="…color:…"`（含模板三元），
    以及 .astro `<style>` 块里的声明。JS 对象字面量里的 `color:` 键
    （ECharts series / TradingView 线色 / 热力图色带）是图形用法，一律不报。
    """
    holders = _color_holders(text, pat)
    hits: list[str] = []
    in_style = False
    for i, line in enumerate(text.splitlines(), 1):
        in_style = in_style or "<style" in line
        for m in COLOR_PROP.finditer(line):
            head = line[: m.start()].rstrip()
            quotes = STYLE_ATTR.findall(line[: m.start()])
            first = bool(quotes) and head.endswith(quotes[-1])
            cont = head.endswith(";") and (bool(quotes) or in_style)
            block = in_style and head.endswith("{")
            if not (first or cont or block):
                continue
            val = _decl_value(line, m.end(), quotes[-1] if quotes else '"')
            tok = pat.search(val)
            if tok:
                # color-mix(图形档 N%, transparent) 是填充用法，合法
                if "color-mix(" in val[: tok.start()]:
                    continue
                hits.append(f"{origin}:{i} 文字色用了 {tok.group(0)}")
            else:
                via = sorted(n for n in holders if _is_word(n, val))
                if via:
                    hits.append(f"{origin}:{i} 文字色经 {via[0]} 取到图形档")
        if "</style>" in line:
            in_style = False
    return hits


def _under_aa_graphic_tokens() -> list[str]:
    """亮色主题下做文字色跌破 AA 的图形档 token（暗色原语本就过 AA，不用管）。

    卡片与页面底任一不达即算不达；解析不到字面色时保守计入。
    """
    light = _themes()[":root"]
    out: list[str] = []
    for tok in GRAPHIC_TOKENS:
        val = light.get(tok, "")
        if not val.startswith("#"):
            out.append(tok)
        elif any(_cr(val, light[k]) < AA_NORMAL for k in ("--surface", "--bg")):
            out.append(tok)
    return out


def test_detector_ignores_graphic_fill_usages() -> None:
    """检测器自检：图形档做填充/线色/JS 调色板键一律不报（假阳性防线）。"""
    legal = "\n".join(
        [
            '<div style="background:var(--color-up)"></div>',
            '<div style="background-color: var(--color-down);color:var(--text)"></div>',
            '<div style="border-color:var(--color-warn);color:var(--text-dim)"></div>',
            '<div style="border:1px solid var(--color-hawk)"></div>',
            '<div style="accent-color: var(--color-dove);color:var(--text)"></div>',
            '<span style="color:color-mix(in srgb, var(--color-up) 22%, transparent)"'
            "></span>",
            '<svg><path fill="var(--color-brand)" stroke="var(--color-warn-light)"'
            "/></svg>",
            '<svg><text style="stop-color: var(--color-warn-deep)"/></svg>',
            '<div style="box-shadow:0 1px 4px var(--color-neutral);color:var(--text)"'
            "></div>",
            "const s = chart.addLineSeries("
            "  { color: cssVar('--color-up'), lineWidth: 1 });",
            "  itemStyle: { color: reCssVar('--color-down'), borderRadius: 4 },",
            "  inRange: { color: [reCssVar('--color-brand'),"
            " reCssVar('--color-down')] },",
            "  rsiUpper.applyOptions({ color: cssVar('--color-down') });",
            '<span style="color:var(--color-up-text)">${v}</span>',
            '<span style="color:var(--down)">${v}</span>',
            '<span style="color:var(--warning)">${v}</span>',
            # 同行后面的合法填充不得泄进本条声明（`;` / `}` / 属性收尾引号截断）
            '<span style="color:var(--text-dim)">${x}</span>'
            '<span style="background:var(--color-down)">▲</span>',
            "const color = up ? 'var(--color-up)' : 'var(--color-down)';",
            '<polyline stroke="${color}" fill="none"/></svg>',
        ]
    )
    assert _graphic_text_hits(legal, "x.astro", GRAPHIC) == []
    illegal = "\n".join(
        [
            '<span style="color:var(--color-warn-light)">温和</span>',
            '<span style="color: var(--color-up)">+1.2%</span>',
            """`<span style="color:${v >= 0 ? 'var(--color-up)'"""
            """ : 'var(--color-down)'}">${v}</span>`""",
            "const stateColor = bad"
            " ? reCssVar('--color-down') : reCssVar('--color-up');",
            '<span style="padding:2px;color:${stateColor}">● 承压</span>',
        ]
    )
    got = _graphic_text_hits(illegal, "y.astro", GRAPHIC)
    assert [h.split(":")[1].split()[0] for h in got] == ["1", "2", "3", "5"], got


def test_islands_and_shared_js_do_not_use_graphic_tokens_as_text_color() -> None:
    """`.astro` island 与共享 JS 拼出来的 style 也得走 --color-*-text。

    检测器只认两种文字色上下文：style="…color:…"（含模板三元）与 .astro <style> 块声明；
    JS 对象字面量里的 color: 键（ECharts/TradingView 线色）一律不认，否则全是假阳性。
    `--accent` 是 brand 的图形档别名（tokens.css:135），亮色下在页面底只有 4.26:1，
    所以写字要换同色相的文字档 `--accent-ink`；它已在 GRAPHIC_TOKENS 里，是否入扫
    由实测对比度决定（_under_aa_graphic_tokens）。
    """
    allowed = _under_aa_graphic_tokens()
    assert allowed, "tokens.css 里没解析出任何图形档 token，先修 _themes()"
    pat = _graphic_pat(allowed)
    bad = [
        hit
        for glob in SCAN_TARGETS
        for path in sorted(FRONTEND.glob(glob))
        for hit in _graphic_text_hits(
            path.read_text(encoding="utf-8"),
            str(path.relative_to(ROOT)),
            pat,
        )
    ]
    assert not bad, "文字色请走 --color-*-text（图形档对比度不足）：\n" + "\n".join(bad)


# ── 后端下发字面 hex 的封口（审计第 4 步）──────────────────────────────────
#
# 上面那些规则只认 token 名，所以 `style="color:${d.zone_color}"`（值来自后端 JSON 的
# 字面 hex）对它们完全隐形 —— 这正是 credit 页那几处 AA 违规能活过一轮审计的原因。
# 口径统一成「后端下发语义 key、前端查文字档映射」（volatility/vix.astro 早就这么写），
# 再用下面三条规则封掉回潮。

# 分析模块下发的 `*_color` 字段（zone_color 那一类）
BACKEND_COLOR_FIELD = re.compile(r'"([a-z_]*_color)"\s*:')
# 前端把 `*_color` 字段插进 `color:` 文字槽（border-color / background-color 不算）
FRONTEND_COLOR_INTERP = re.compile(r"(?<![-\w])color\s*:\s*\$\{[^}]*_color\b")
# CSS 文字槽引用名字以 `-color` 结尾的自定义属性（那是 JS 拼出来的图形档）
CSS_TEXT_VIA_NAMED_COLOR = re.compile(r"(?<![-\w])color:[^;{}]*var\(--[\w-]+-color[,)]")
# DOM 内联 style 里不得出现 reCssVar 的解析结果（字面 hex 一进 DOM 就固化）
RECSSVAR = re.compile(r"reCssVar\s*\(")
STYLE_LINE = re.compile(r"""(?<![-\w])style\s*=\s*["'`]""")


def test_islands_do_not_bake_resolved_colors_into_inline_styles() -> None:
    """DOM 内联 `style="…"` 里不得出现 `reCssVar()` 的解析结果。

    两档制解决的是「用哪个档」，这条解决的是「什么时候解析」：reCssVar 把 CSS 变量
    解析成字面 hex 再拼进 style 字符串（`background:${hex}1a`），字符串一进 DOM 就
    固化，用户切亮/暗主题不刷新页面就还是旧主题色。正确写法是把引用交给浏览器在样式
    计算期解析：`var(--token)`，透明底/边框用
    `color-mix(in srgb, var(--token) N%, transparent)`。

    只管 DOM 内联 style：ECharts / TradingView 的 canvas 取色**必须**继续用实际色值
    （canvas 不认 var()/color-mix），那些位置在 JS 对象键里（itemStyle:/axisLabel:），
    不带 `style=` 所以不会被误报。

    ponytail: 按行判定 —— `style="` 与 `${hex}` 拆在两行的模板会漏，目测未出现；
    `.cssText = '…'` 也不认（全站仅 1 处且不带 reCssVar）。要封死得改成跨行模板扫描。
    """
    bad: list[str] = []
    for glob in SCAN_TARGETS:
        for path in sorted(FRONTEND.glob(glob)):
            text = path.read_text(encoding="utf-8")
            holders = _color_holders(text, RECSSVAR)  # 取过色的变量名（含二跳别名）
            for i, line in enumerate(text.splitlines(), 1):
                if not STYLE_LINE.search(line):
                    continue
                got = RECSSVAR.search(line) or next(
                    (h for h in sorted(holders) if _is_word(h, line)), None
                )
                if got:
                    rel = path.relative_to(ROOT)
                    bad.append(f"{rel}:{i} 内联 style 用了 {got} → 改 var()/color-mix")
    assert not bad, "内联色请交给样式计算期解析（切主题才跟得上）：\n" + "\n".join(bad)


def test_analysis_modules_do_not_emit_color_fields() -> None:
    """`src/*_analysis.py` 不得下发 `*_color` 字段（下发语义 key，前端查映射）。

    区间色表（`zones: [{label, color}]`，前端只拿去画色条/圆点）不在此列 ——
    它键名是 `color` 不是 `*_color`，且消费位是 background。
    """
    bad = [
        f"{p.name}:{i}:{ln.strip()}"
        for p in sorted((ROOT / "src").glob("*_analysis.py"))
        for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if BACKEND_COLOR_FIELD.search(ln)
    ]
    assert not bad, (
        "后端请下发语义 key（如 zone 名），色由前端映射到文字档：\n" + "\n".join(bad)
    )


def test_frontend_does_not_interpolate_backend_color_fields_as_text() -> None:
    """前端 `color:` 槽不得插值 `*_color` 字段（后端字面 hex 绕过两档制）。

    反向验过：把 `style="color:${d.zone_color}"` 塞回 credit 页会红。
    """
    bad = [
        f"{path.relative_to(ROOT)}:{i}:{ln.strip()[:100]}"
        for glob in SCAN_TARGETS
        for path in sorted(FRONTEND.glob(glob))
        for i, ln in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if FRONTEND_COLOR_INTERP.search(ln)
    ]
    assert not bad, (
        "文字色不得取后端 *_color 字面 hex，改按语义 key 查文字档：\n" + "\n".join(bad)
    )


def test_css_does_not_read_graphic_named_custom_prop_for_text() -> None:
    """CSS 里 `color:` 不得引用名字以 `-color` 结尾的自定义属性。

    这类属性是 JS 拼出来的图形档（`rc.style.setProperty('--rc-color', …)`），名字就说明
    它是填充档；写字得另给一个 `-ink` 档属性。
    """
    bad = [
        f"{p.name}:{i}:{ln.strip()[:120]}"
        for p in sorted(CSS_DIR.glob("*.css"))
        for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if CSS_TEXT_VIA_NAMED_COLOR.search(ln)
    ]
    assert not bad, "文字槽请引用 -ink / -text 档属性：\n" + "\n".join(bad)
