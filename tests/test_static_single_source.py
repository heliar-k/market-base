"""前端约定防漂移测试（ADR-0003 迁移收尾后存续部分）。

机器退役、约定存活（工单 #18）：sync_pages_head 模板机器随 32 页全迁 .astro
删除，head 样板由 TopicLayout.astro 构建期渲染。本文件只保校验「约定」的用例：
- public/ 只剩 SPA 壳、专题页以 .astro 存续 → test_public_is_spa_shell_only
- SITE_NAV 里的每个 page 都要有对应页面 → test_site_nav_pages_exist
- 每个 '/….html' 链接都要有 _redirects 重写行 → test_html_links_have_redirects
- 导航消费方不得自带页路径清单 → test_nav_consumers_read_site_nav
- 「数据截至」文案只能由 R.asOf 组装 → test_as_of_*
- 页头时效标签段数上限（只放 Section 级数据源）→ test_as_of_segments_capped
- echarts-theme.js 先于 rates-common.js 加载 → test_echarts_theme_before_rates_common
- 脚本语法（is:inline 不打包）→ test_all_js_files_parse / test_inline_scripts_parse
- 静态小节一律 Section 组件 → test_static_sections_use_section_component
- 页脚「本页」行只走 slot + R.foot，来源术语只用「数据源」
  → test_pages_own_one_foot_slot / test_foot_text_only_via_r_foot
  / test_page_level_source_term_is_data_source
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
STATIC = FRONTEND / "public"
SPA_ENTRY = STATIC / "index.html"
# 32 个专题页 .astro 源（build 产同路径 HTML）；head/顶栏样板唯一来源 = TopicLayout
ASTRO_PAGES = FRONTEND / "src" / "pages"
TOPIC_LAYOUT = FRONTEND / "src" / "layouts" / "TopicLayout.astro"


def test_public_is_spa_shell_only() -> None:
    """收缩完成态（工单 #18）：public/ 仅 SPA 壳与共享资源，专题页 HTML 零残留。"""
    html = {p for p in STATIC.rglob("*.html")}
    assert html == {SPA_ENTRY}, (
        f"public/ 混入专题页 HTML 残留：{sorted(html - {SPA_ENTRY})}"
    )
    astro = list(ASTRO_PAGES.rglob("*.astro"))
    assert len(astro) >= 30, f"专题页 .astro 源异常偏少：{len(astro)}"


@pytest.mark.parametrize(
    "src", [TOPIC_LAYOUT, SPA_ENTRY], ids=["TopicLayout.astro", "index.html"]
)
def test_echarts_theme_before_rates_common(src: Path) -> None:
    """echarts-theme.js 先于 rates-common.js（同步加载、顺序敏感）。
    .astro 页的 head 由 TopicLayout 统一渲染 → 校验 Layout 源一次覆盖
    32 页；SPA 壳另算。"""
    text = src.read_text(encoding="utf-8")
    theme = text.find('src="/js/echarts-theme.js"')
    common = text.find('src="/js/rates-common.js"')
    assert theme != -1, f"{src.relative_to(ROOT)} 缺 echarts-theme.js"
    if common != -1:
        assert theme < common, f"{src.relative_to(ROOT)} 加载顺序颠倒"


def _site_nav_js() -> str:
    return (STATIC / "js" / "site-nav.js").read_text(encoding="utf-8")


def _nav_pages() -> list[str]:
    return re.findall(r"page: '(/[^']+)'", _site_nav_js())


def test_site_nav_pages_exist() -> None:
    """SITE_NAV 里的每个 page 都要有对应页面（public HTML 或已迁移的 .astro 源）。"""
    assert _nav_pages(), "site-nav.js 里没解析到任何 page"
    for page in _nav_pages():
        rel = page.strip("/")
        target = STATIC / rel if Path(rel).suffix else STATIC / rel / "index.html"
        astro = ASTRO_PAGES / (
            rel[:-5] + ".astro" if rel.endswith(".html") else rel + "/index.astro"
        )
        assert target.exists() or astro.exists(), f"SITE_NAV 指向不存在的页面：{page}"


def _html_links_in_frontend() -> set[str]:
    """前端源码里所有字面量形式的 '/….html' 链接（SITE_NAV 条目与页内跳转）。"""
    found: set[str] = set()
    sources = [
        *sorted(ASTRO_PAGES.rglob("*.astro")),
        *sorted((STATIC / "js").glob("*.js")),
        SPA_ENTRY,
    ]
    for src in sources:
        found |= set(
            re.findall(
                r"""['"](/[A-Za-z0-9/_-]*\.html)['"]""", src.read_text(encoding="utf-8")
            )
        )
    # public/ 下真实存在的同名文件不需要重写（如 SPA 壳 /index.html）
    return {p for p in found if not (STATIC / p.lstrip("/")).is_file()}


def _redirect_sources() -> set[str]:
    """_redirects 里的源路径（跳过注释与空行）。"""
    lines = (STATIC / "_redirects").read_text(encoding="utf-8").splitlines()
    return {
        parts[0]
        for ln in lines
        if (parts := ln.split()) and not ln.lstrip().startswith("#")
    }


def test_html_links_have_redirects() -> None:
    """每个 '/….html' 链接都要有 _redirects 重写行（#26）。

    产物是 directory 路由（Astro `format: 'directory'`），而 SITE_NAV 与页内跳转都带
    `.html` 后缀（共享 JS 硬闸不可改），两者靠 `frontend/public/_redirects` 里手写的
    `.html → 目录页 200` 对上。新子页漏一行即静默 404，而
    `test_site_nav_pages_exist` 只看页面文件存在、看不见重写表。

    反向验法：手工删掉 `_redirects` 任意一行 → 本测试红并指名失去重写的链接。
    不查孤儿（重写行 ⊃ 链接集之外的）：重写也可能服务外部入站链接（冻结备份站、
    书签），孤儿不是错误。"""
    links, redirects = _html_links_in_frontend(), _redirect_sources()
    assert links, "前端源码里没解析到任何 '.html' 链接（改写法了？）"
    missing = sorted(links - redirects)
    assert not missing, (
        f"以下 '.html' 链接缺 _redirects 重写行，线上会静默 404：{missing}"
    )


def test_nav_consumers_read_site_nav() -> None:
    """SPA 宏观视图不得自带页路径清单（SITE_NAV 唯一数据源）。
    专题页顶栏 Tab 已由 TopicLayout 构建期渲染，nav.js 随工单 #18 退役。"""
    pages = _nav_pages()
    assert pages
    text = (STATIC / "js" / "macro-view.js").read_text(encoding="utf-8")
    assert "import { SITE_NAV" in text, "macro-view.js 未 import SITE_NAV"
    hardcoded = [p for p in pages if f"'{p}'" in text]
    assert not hardcoded, f"macro-view.js 里仍有硬编码专题路径：{hardcoded}"


def test_dashboard_links_use_site_nav() -> None:
    """dashboard.js 是导航消费方：专题路径不得重复硬编码，全部从 SITE_NAV 派生。

    （仪表盘不再自带跨资产表，指标键→导航键的 LINKS 映射随之消失；
    现在只余两处页路径：今日一句话行的 NAV_PAGES、与去 /daily/ 的入口条。）"""
    text = (STATIC / "js" / "dashboard.js").read_text(encoding="utf-8")
    assert "import { SITE_NAV" in text, "dashboard.js 未 import SITE_NAV"
    assert "for (const g of SITE_NAV.groups)" in text, "NAV_PAGES 未从 groups 派生"
    assert "SITE_NAV.home.page" in text, "去今日研判页的入口条未用 SITE_NAV.home.page"
    hardcoded = [p for p in _nav_pages() if f"'{p}'" in text]
    assert not hardcoded, f"dashboard.js 里仍有硬编码专题路径：{hardcoded}"


def test_as_of_text_only_via_r_asof() -> None:
    """「数据截至」文案只能由 R.asOf 组装（rates-common.js 是唯一出处）。

    管辖 SPA 壳、全部 .astro 页（island 脚本同样不得手写）与 **共享 JS**
    —— 旧版只扫 index.html + .astro，dashboard.js / cross-correlation.js 里
    手写 `textContent = '数据截至 …'` 全漏（2026-10 补）。逐行匹，不把隔行的
    `textContent =` 与注释里的「数据截至」误拼成一条。
    """
    offenders = []
    sources = [
        SPA_ENTRY,
        *sorted(ASTRO_PAGES.rglob("*.astro")),
        *sorted((STATIC / "js").glob("*.js")),
    ]
    for page in sources:
        for ln in page.read_text(encoding="utf-8").splitlines():
            if re.search(r"textContent\s*=\s*[^;]*数据截至", ln):
                offenders.append(f"{page.relative_to(ROOT)}:{ln.strip()[:60]}")
                break
    assert not offenders, f"手写时效标签文案，请改 R.asOf(...)：{offenders}"


def test_as_of_formatter_present() -> None:
    """R.asOf / R.asMonth 存在且每个专题页确有调用（防止 formatter 被删空）。"""
    js = (STATIC / "js" / "rates-common.js").read_text(encoding="utf-8")
    assert "asOf(src)" in js and "asMonth" in js
    # 404 页套 TopicLayout（共用主题/顶栏）但无数据源，页头时效槽恒空 → 不计入
    astro_pages = sorted(
        p for p in ASTRO_PAGES.rglob("*.astro") if p.name != "404.astro"
    )
    used = sum(1 for p in astro_pages if "R.asOf(" in p.read_text(encoding="utf-8"))
    assert used == len(astro_pages), (
        f"仅 {used}/{len(astro_pages)} 个专题页用 R.asOf 组装时效标签"
    )


# 页头 `re-as-of` 是「整页第一数据源」的坐标，不是条目级时效的清单。
# 2026-10 crypto 页堆到 6 段（其中「资金费率/PCR」与「衍生品快照」同源同文件、
# 「Polymarket/BTCD/ETF 流量」只是交叉验证层四张卡的日期），一行压成三行且
# 自相矛盾（PM 卡面写「缺失」页头却报有数据）。条目级日期写在各卡 sub 里。
# 只数写字面量数组的调用（`Object.entries(g)` / `segs` 这类动态入参不在射程）。
def _asof_args(text: str) -> list[str]:
    """抽出每处 R.asOf(...) 的入参源码（括号配对，含换行）。"""
    out: list[str] = []
    for m in re.finditer(r"R\.asOf\(", text):
        i, depth, start = m.end(), 1, m.end()
        while i < len(text) and depth:
            depth += (text[i] in "([{") - (text[i] in ")]}")
            i += 1
        out.append(text[start : i - 1])
    return out


def _asof_segments(arg: str) -> int | None:
    """字面量数组入参的顶层段数；非数组入参（单值 / 变量）返回 None 不计。"""
    if not arg.lstrip().startswith("["):
        return None
    depth, n = 0, 1
    for ch in arg:
        depth += (ch in "([{") - (ch in ")]}")
        if ch == "," and depth == 1:
            n += 1
    return n


AS_OF_MAX_SEGS = 4  # 页头最多 4 段（≈一行可读）：主数据源 + 各 Section 第一源


def test_as_of_segments_capped() -> None:
    """页头时效标签不得堆条目级日期（R.asOf 字面量入参 ≤ AS_OF_MAX_SEGS 段）。"""
    bad = []
    for page in sorted(
        [SPA_ENTRY, *ASTRO_PAGES.rglob("*.astro"), *(STATIC / "js").glob("*.js")]
    ):
        for arg in _asof_args(page.read_text(encoding="utf-8")):
            n = _asof_segments(arg)
            if n and n > AS_OF_MAX_SEGS:
                bad.append(f"{page.relative_to(ROOT)}（{n} 段）")
    assert not bad, (
        "页头只放 Section 级数据源，条目级日期写进对应卡片 sub"
        f"（≤{AS_OF_MAX_SEGS} 段）：{bad}"
    )


# ── 多端点页的容错纪律（审计 R3）──


def test_multi_endpoint_pages_treat_secondary_as_optional() -> None:
    """拉 ≥2 个 /api/ 端点的页面，至少有一个要走 `R.getOpt`。

    静态导出端 `src/export_pages.py` 的 `_safe()` 会跳过当日缺数据的端点 → 对应 JSON
    可以合法 404。全用 `R.get` 时一个次要端点缺失就让整个 `await` reject，页级
    `load().catch` 把 `.re-error` 写进**所有**段 —— 本来正常的段跟着一起报废。
    规则只查「有没有把任一端点当可选」，不查具体结构，以免和页面写法绑死。
    """
    offenders = []
    for page in sorted(ASTRO_PAGES.rglob("*.astro")):
        text = page.read_text(encoding="utf-8")
        endpoints = set(re.findall(r"""['"`]/api/[A-Za-z0-9_\-./]+""", text))
        if len(endpoints) >= 2 and "R.getOpt(" not in text:
            offenders.append(f"{page.relative_to(ROOT)}（{len(endpoints)} 端点）")
    assert not offenders, (
        "次要端点应走 R.getOpt（失败返回 null）+ 段级空态，不得全部用 R.get："
        f"{offenders}"
    )


def test_r_fail_targets_exist_on_page() -> None:
    """`R.fail([ids], e)` 里的 id 必须能在同一页找到 `id="…"`。

    `rates-common.js` 的 `R.fail` 对每个 id 先 `getElementById` 再 `if (el)` 写错误态
    —— id 拼错或段被改名后，错误态**静默不显示**（不报错、不留痕迹），正好死在
    「数据缺失要给原因」的约定上。人跟 grep 看不出来，进测试。
    """
    offenders = []
    for page in sorted(ASTRO_PAGES.rglob("*.astro")):
        text = page.read_text(encoding="utf-8")
        have = set(re.findall(r'id="([a-zA-Z0-9_-]+)"', text))
        used: set[str] = set()
        for arg in re.findall(r"R\.fail\(\[([^\]]*)\]", text):
            used |= set(re.findall(r"'([a-zA-Z0-9_-]+)'", arg))
        if miss := sorted(used - have):
            offenders.append(f"{page.relative_to(ROOT)} → {miss}")
    assert not offenders, (
        "R.fail 目标 id 在页面上不存在（错误态会静默失效）：" + "; ".join(offenders)
    )


def test_escaping_only_via_r_esc() -> None:
    """HTML 转义只能走 `R.esc`（rates-common.js 是唯一出处），页面只允许别名。

    背景：macro-view.js 自写过一份 esc，少了 `"` 转义（属性位注入面）还没做 null
    守卫；geo / dashboard.js / commodities 都是 `const esc = R.esc` 别名，合法。
    只拦「自己重实现一遍正则」的写法。
    """
    offenders = []
    srcs = [
        SPA_ENTRY,
        TOPIC_LAYOUT,
        *sorted(ASTRO_PAGES.rglob("*.astro")),
        *sorted((STATIC / "js").glob("*.js")),
    ]
    for page in srcs:
        if page.name == "rates-common.js":
            continue
        text = page.read_text(encoding="utf-8")
        for m in re.finditer(r"(?:const|let|function)\s+esc\b", text):
            line = text[m.start() :].split("\n", 1)[0]
            if "R.esc" not in line:
                offenders.append(f"{page.relative_to(ROOT)}: {line.strip()[:60]}")
    assert not offenders, (
        "自写转义实现，请改 R.esc（或 const esc = R.esc 别名）：" + "; ".join(offenders)
    )


# ── 脚本语法（is:inline 不走打包管线，改完必须能直接被浏览器/Node 解析）──


def _check_js(code: str, name: str) -> None:
    with tempfile.NamedTemporaryFile(
        "w", suffix=".js", delete=False, encoding="utf-8"
    ) as f:
        f.write(code)
        tmp = f.name
    try:
        r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
    finally:
        Path(tmp).unlink(missing_ok=True)
    if r.returncode == 0:
        return
    if shutil.which("node") is None:
        pytest.skip("无 node，跳过语法检查")
    pytest.fail(f"{name} 语法错误：{r.stderr.strip()[:300]}")


def test_all_js_files_parse() -> None:
    for js in sorted((STATIC / "js").glob("*.js")):
        _check_js(js.read_text(encoding="utf-8"), f"js/{js.name}")


def test_inline_scripts_parse() -> None:
    """内联 <script> 逐个过 node --check：SPA 壳 + TopicLayout 运行时
    + 每页 is:inline island。"""
    inline = re.compile(r"(?s)<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>")
    for page in [SPA_ENTRY, TOPIC_LAYOUT, *sorted(ASTRO_PAGES.rglob("*.astro"))]:
        code = page.read_text(encoding="utf-8")
        for n, m in enumerate(inline.finditer(code), 1):
            if m.group(1).strip():
                _check_js(m.group(1), f"{page.relative_to(ROOT)} 内联#{n}")


# ── #28：逐页图表内联高度（硬禁）──────────────────────────────────────────
# 内联 style 优先级高于任何类，CSS 层覆盖不了 → 只能逐页删。18 处存量已在 #28 一轮清空，
# 本测试从此硬禁：新页面要非基线高度，走档位家族
# .chart.chart-{sm,lg,md,hero}（260/280/340/520）或 .re-chart + .re-chart-sm。
# 反向验法：给任一 .astro 加一行 style="height:320px" → 本测试红并指名。
_INLINE_HEIGHT = re.compile(r'style="[^"]*\bheight:(\d+)px')

# 卡行单源（2026-10）：.re-cards 是 flex，卡宽由 CSS 一处定（≤275px = 4 列档）。
# 页面写内联 grid-template-columns、给 .re-corridor-value 手写 font-size，
# 就是在造平行卡规格 —— /assets/crypto/ 一度同页 6 种卡宽（最高 562px）。
# 宽卡 .re-cards-wide，长文本 .re-card-wrap，密卡 .re-card-dense，都在 special.css。
_CARD_ROW_OVERRIDE = re.compile(
    r'class="re-cards[^"]*" style="[^"]*grid-template-columns'
)
_CARD_VALUE_FONT = re.compile(r'class="re-corridor-value"[^>]*style="[^"]*font-size')


def test_card_rows_use_single_source_grid() -> None:
    """卡行不写内联列宽，卡值字号不逐页覆盖（走 -wide / -wrap / -dense）。"""
    hits = {
        str(page.relative_to(ASTRO_PAGES)): [
            m.group(0)[:60]
            for rx in (_CARD_ROW_OVERRIDE, _CARD_VALUE_FONT)
            for m in rx.finditer(page.read_text("utf-8"))
        ]
        for page in sorted(ASTRO_PAGES.rglob("*.astro"))
    }
    bad = {k: v for k, v in hits.items() if v}
    assert not bad, f"以下页绕过了卡行单源规格：{bad}"


def test_no_hardcoded_chart_heights() -> None:
    """专题页不得写死图表高度（#28）：≥100px 的 height 内联一律红。"""
    hits = {
        str(page.relative_to(ASTRO_PAGES)): [
            m.group(0)
            for m in _INLINE_HEIGHT.finditer(page.read_text("utf-8"))
            if int(m.group(1)) >= 100
        ]
        for page in sorted(ASTRO_PAGES.rglob("*.astro"))
    }
    bad = {k: v for k, v in hits.items() if v}
    assert not bad, f"以下页写死了图表高度，改走档位类（.chart.chart-sm~hero）：{bad}"


# 窄屏横向溢出守卫（#27 同族）：auto-fit 轨道的 min 不会低于自身，
# 轨道宽 ≥260px 在 375（内容宽 245）下必把文档顶宽 → 必须写
# minmax(min(100%, Npx), 1fr)（/assets/index/ 先例）。
# 240px 及以下不查：375 仍装得下（存量 .vol-basic-grid / .re-col3 即此档）。
_WIDE_TRACK = re.compile(r"minmax\((\d{3})px,\s*1fr\)")


def test_wide_grid_tracks_have_narrow_fallback() -> None:
    """≥260px 的 auto-fit 轨道必须带 min(100%, …) 窄屏兜底。"""
    srcs = [
        *ASTRO_PAGES.rglob("*.astro"),
        *(STATIC / "js").rglob("*.js"),
        *(STATIC / "css").rglob("*.css"),
        SPA_ENTRY,
    ]
    bad = []
    for src in srcs:
        text = src.read_text("utf-8")
        for m in _WIDE_TRACK.finditer(text):
            if (
                int(m.group(1)) >= 260
                and "min(100%" not in text[max(0, m.start() - 12) : m.start()]
            ):
                bad.append(f"{src.name}:{m.group(0)}")
    assert not bad, f"宽轨道缺窄屏兜底（写 minmax(min(100%, Npx), 1fr)）：{bad}"


# R.table 第 5 参 html=true 时，没写 formatter 的列走默认格式化器 = R.esc 转义（防名称类
# 自由文本注入 HTML）。所以「markup 拼进行值 + 传空 formatter 表」会让 <span> 以字面文本
# 显示出来（2026-10 波动率页 Top8 / 全量 30 指数表踩过）。着色 markup 一律放 formatter。
_TABLE_NO_FMT = re.compile(r"R\.table\([^;]*?\{\},\s*[^;]*?,\s*true\s*\)", re.S)


def test_html_tables_never_pass_empty_formatters() -> None:
    """R.table(..., html=true) 不得传空 formatter 表（markup 会被转义成字面文本）。"""
    srcs = [*ASTRO_PAGES.rglob("*.astro"), *(STATIC / "js").rglob("*.js")]
    bad = [
        str(p.relative_to(ROOT))
        for p in srcs
        if _TABLE_NO_FMT.search(p.read_text("utf-8"))
    ]
    assert not bad, (
        f"以下页给 html=true 的 R.table 传了空 formatter，着色列要写进 formatter：{bad}"
    )


# 专题页小节标题单源 = components/Section.astro（.re-sec-title 带 accent 竖条）。
# 裸 <div class="re-section"><h2> 在静态层 = 卡中卡双层框 + 标题形式与全站不一致
# （2026-10 波动率两页踩过，当时以「内容依赖卡片容器」为由躲过 Section 组件化批 1）。
# 只查 HTML 结构层（首个 <script is:inline> 之前）：JS 生成的卡片集（七段叙事 /
# 地缘事件 / 压力测试等）用 .re-section 是正当用法，不在射程内。
_STATIC_RE_SECTION = re.compile(r'class="[^"]*\bre-section\b[^"]*"')


def test_static_sections_use_section_component() -> None:
    """专题页静态层不得写 class="re-section"（静态小节一律包 Section 组件）。"""
    bad: dict[str, list[str]] = {}
    for page in sorted(ASTRO_PAGES.rglob("*.astro")):
        html = re.split(r"<script is:inline", page.read_text("utf-8"), maxsplit=1)[0]
        hits = [
            f"{i + 1}:{m.group(0)}"
            for i, line in enumerate(html.split("\n"))
            if (m := _STATIC_RE_SECTION.search(line))
        ]
        if hits:
            bad[str(page.relative_to(ASTRO_PAGES))] = hits
    assert not bad, (
        f"静态小节要改用 <Section title=... sub=...>，"
        f".re-section 只留给 JS 卡片集：{bad}"
    )


# 页脚两行（本页口径 / 免责）单源 = TopicLayout：各页只往 <slot name="foot">
# 交一个空容器（.re-foot-page #re-foot），文案由 rates-common.js 的 R.foot 组装。
# 2026-09 之前各页自写 .re-gen（右对齐小字），措辞裂成 6 种、4 页干脆没有，
# 且与页脚那句全站来源撞成两套说法 —— 本测试锁住收口结果（全站枚举已删，只留本页行）。
_FOOT_DIV = re.compile(r'<div class="re-foot-page" slot="foot" id="re-foot"></div>')


def test_pages_own_one_foot_slot() -> None:
    """每个专题页（除 404）恰好一个页脚「本页」容器，且由 R.foot 填。"""
    bad: list[str] = []
    for page in sorted(ASTRO_PAGES.rglob("*.astro")):
        if page.name == "404.astro":
            continue
        text = page.read_text("utf-8")
        if len(_FOOT_DIV.findall(text)) != 1:
            bad.append(f"{page.relative_to(ASTRO_PAGES)} 页脚容器 ≠1 个")
        elif "R.foot(" not in text:
            bad.append(f"{page.relative_to(ASTRO_PAGES)} 容器未被 R.foot 填充")
    assert not bad, f"页脚「本页」行未走 slot + R.foot：{bad}"


def test_foot_text_only_via_r_foot() -> None:
    """「研判生成：」「数据源：」的页脚文案只走 R.foot（措辞模板单源）。"""
    offenders = []
    for src in [
        *sorted(ASTRO_PAGES.rglob("*.astro")),
        *sorted((STATIC / "js").glob("*.js")),
        SPA_ENTRY,
    ]:
        for ln in src.read_text("utf-8").splitlines():
            if re.search(r"getElementById\('re-foot'\)\s*\.textContent", ln):
                offenders.append(f"{src.relative_to(ROOT)}:{ln.strip()[:60]}")
                break
    assert not offenders, (
        f"页脚文案绕过 R.foot 手写，请改 R.foot(gen, {{src, note}})：{offenders}"
    )


def test_page_level_source_term_is_data_source() -> None:
    """「数据来源」这个说法已废弃；页脚来源行统一用「数据源」。"""
    offenders = []
    for src in [
        *sorted(ASTRO_PAGES.rglob("*.astro")),
        *sorted((STATIC / "js").glob("*.js")),
        SPA_ENTRY,
    ]:
        for ln in src.read_text("utf-8").splitlines():
            if "数据来源" in ln:
                offenders.append(f"{src.relative_to(ROOT)}:{ln.strip()[:60]}")
    assert not offenders, f"用了废弃的「数据来源」，本页级请写「数据源」：{offenders}"


# ── 键值行 / 段底注脚单源（credit 页 UI 收口一轮定下的形状）────────────────
# 「一项一个数」的段写 2-4 列键值表，表被拉到卡片全宽时数值列甩到最右、中间一大片
# 空白；改 .re-cards 又会在窄屏炸（卡片 ≤768 强制单列，一张 ~117px）。两者都不对，
# 答案是 .re-kv-grid 键值行网格，只能由 R.kvRows 产出。
_KV_CLASS = re.compile(r'class="re-kv-grid')
# 口径/读法说明一律沉到卡片底部（.re-sec-note），不作卡片首段正文：首段 <p> 与下面的
# 数据只差 8px 间距、1px 字号，读起来像列表的第一项（credit 五段先犯的即此）。
_NOTE_CLASS = re.compile(r'class="re-sec-note')
# 研判横排小卡行只能由 R.judgeRow 产出（sig-row 只有它一个生产者）
_SIGROW_INLINE = re.compile(r'class="sig-row"')


def _kv_note_sig_pages() -> list[tuple[Path, str]]:
    return [
        (page, page.read_text("utf-8"))
        for page in sorted(ASTRO_PAGES.rglob("*.astro"))
        if page.name != "404.astro"
    ]


def test_kv_grid_and_sec_note_only_via_r_helpers() -> None:
    """.re-kv-grid / .re-sec-note 只在 rates-common.js 里出现，页面不手抄形状。"""
    offenders = []
    for src, text in [
        *_kv_note_sig_pages(),
        *[(p, p.read_text("utf-8")) for p in sorted((STATIC / "js").glob("*.js"))],
    ]:
        for rx, helper in ((_KV_CLASS, "R.kvRows"), (_NOTE_CLASS, "R.secNote")):
            if rx.search(text) and src != FRONTEND / "public" / "css" / "app.css":
                offenders.append(f"{src.relative_to(ROOT)} 手写了 {helper} 的产物类名")
    assert not offenders, f"键值行/段底注脚未走单源 helper：{offenders}"


def test_judge_row_only_via_r_judge_row() -> None:
    """研判横排小卡行（.sig-row）只由 R.judgeRow 产出，页面不再拼 re-section + sig-row。

    例外两个（本轮未迁，形状与 judgeRow 不同构，迁移时删掉例外）：
    - volatility：7 段叙事的 parts 段卡数由数据决定，同一张卡里还要混 <h2> 与图；
    - geo：分主题明细把整组结论塞进一个 <ul>，不是「一段一格」。
    """
    exempt = ("volatility/index.astro", "geo/index.astro")
    offenders = [
        str(page.relative_to(ASTRO_PAGES))
        for page, text in _kv_note_sig_pages()
        if str(page.relative_to(ASTRO_PAGES)) not in exempt
        and _SIGROW_INLINE.search(text)
    ]
    assert not offenders, f"以下页手拼 .sig-row，请改 R.judgeRow(items)：{offenders}"


def test_credit_no_orphan_framework_note() -> None:
    """credit 研判不再挂「框架：What changed / …」note —— 三格标题即该框架，同页说两遍。

    只查渲染语句（模板串里的 `框架：`），注释里提这件事是允许的。
    """
    text = (ASTRO_PAGES / "credit" / "index.astro").read_text("utf-8")
    rendered = "\n".join(
        ln for ln in text.splitlines() if not ln.lstrip().startswith("//")
    )
    assert "框架：" not in rendered, (
        "研判段又出现独立的框架 note；生成方式由页脚 R.foot 的 R.genText 说明"
    )
