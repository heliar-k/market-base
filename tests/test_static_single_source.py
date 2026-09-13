"""前端单源化防漂移测试（Phase 3：head 样板 / 导航数据 / 时效标签文案）。

这些测试不改文件，只在有人绕过单源机制时报警：
- 手改专题页 <head> 样板 → test_page_head_matches_template
- 导航页路径漏进 Pages 白名单 → test_site_nav_paths_in_export_prefixes
- 页面里重新手写「数据截至」文案 → test_as_of_text_only_via_r_asof
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from src.export_pages import _PATH_PREFIXES
from src.sync_pages_head import SPA_ENTRY, normalize_head, special_pages

STATIC = Path(__file__).resolve().parent.parent / "static"

PAGES = special_pages()


def _head(text: str) -> str:
    return re.search(r"(?s)<head>.*?</head>", text).group(0)


def test_special_pages_found() -> None:
    """收编范围 = 除 SPA 主入口外的全部 HTML（新增页面自动纳入校验）。"""
    html = {p for p in STATIC.rglob("*.html")}
    assert html - {SPA_ENTRY} == set(PAGES)
    assert len(PAGES) >= 30


@pytest.mark.parametrize("page", PAGES, ids=lambda p: str(p.relative_to(STATIC)))
def test_page_head_matches_template(page: Path) -> None:
    """head 必须等于 src/sync_pages_head.py 模板的渲染结果（改样板请改模板）。"""
    text = page.read_text(encoding="utf-8")
    expected = normalize_head(_head(text), page)
    assert _head(text) == expected, (
        f"{page.relative_to(STATIC)} head 漂移 → uv run python -m src.sync_pages_head"
    )


@pytest.mark.parametrize(
    "page", PAGES + [SPA_ENTRY], ids=lambda p: str(p.relative_to(STATIC))
)
def test_echarts_theme_before_rates_common(page: Path) -> None:
    """echarts-theme.js 先于 rates-common.js（同步加载、顺序敏感，Phase 2）。"""
    text = page.read_text(encoding="utf-8")
    theme = text.find('src="/js/echarts-theme.js"')
    common = text.find('src="/js/rates-common.js"')
    assert theme != -1, f"{page.relative_to(STATIC)} 缺 echarts-theme.js"
    if common != -1:
        assert theme < common, f"{page.relative_to(STATIC)} 加载顺序颠倒"


def _site_nav_js() -> str:
    return (STATIC / "js" / "site-nav.js").read_text(encoding="utf-8")


def _nav_pages() -> list[str]:
    return re.findall(r"page: '(/[^']+)'", _site_nav_js())


def test_site_nav_pages_exist() -> None:
    """SITE_NAV 里的每个 page 都要有对应 HTML（防止导航与页面脱节）。"""
    assert _nav_pages(), "site-nav.js 里没解析到任何 page"
    for page in _nav_pages():
        target = STATIC / page.strip("/")
        target = target if target.suffix else target / "index.html"
        assert target.exists(), f"SITE_NAV 指向不存在的页面：{page}"


def test_site_nav_paths_in_export_prefixes() -> None:
    """SITE_NAV 用到的顶层路径必须都在 export_pages._PATH_PREFIXES 白名单里，
    否则 Pages 子路径部署时该链接不会被注入 /market-base 前缀。"""
    missing = {p.split("/")[1] for p in _nav_pages()} - set(_PATH_PREFIXES)
    assert not missing, f"新顶层目录漏进 export_pages._PATH_PREFIXES：{sorted(missing)}"


def test_nav_consumers_read_site_nav() -> None:
    """nav.js / macro-view.js 不得再自带页路径清单（两处视图共用 SITE_NAV）。"""
    pages = _nav_pages()
    assert pages
    for js in ("nav.js", "macro-view.js"):
        text = (STATIC / "js" / js).read_text(encoding="utf-8")
        assert "SITE_NAV" in text, f"{js} 未读 SITE_NAV"
        hardcoded = [p for p in pages if f"'{p}'" in text]
        assert not hardcoded, f"{js} 里仍有硬编码专题路径：{hardcoded}"


def test_dashboard_links_use_site_nav() -> None:
    """dashboard.js 跨资产表跳转（导航消费方第 4 处）：路径不得重复硬编码，
    指标键→导航键映射里的每个键必须真实存在于 SITE_NAV。"""
    text = (STATIC / "js" / "dashboard.js").read_text(encoding="utf-8")
    assert "SITE_NAV" in text, "dashboard.js LINKS 未从 SITE_NAV 派生"
    hardcoded = [p for p in _nav_pages() if f"'{p}'" in text]
    assert not hardcoded, f"dashboard.js 里仍有硬编码专题路径：{hardcoded}"
    m = re.search(r"const NAV_KEY = \{(.*?)\n  \};", text, re.S)
    assert m, "dashboard.js 里没解析到 NAV_KEY 映射"
    used = set(re.findall(r": '([a-z][a-z0-9/_-]*)'", m.group(1)))
    assert used, "NAV_KEY 映射为空"
    missing = used - set(re.findall(r"key: '([^']+)'", _site_nav_js()))
    assert not missing, f"dashboard.js 引用了不存在的 SITE_NAV 键：{sorted(missing)}"


def test_as_of_text_only_via_r_asof() -> None:
    """「数据截至」文案只能由 R.asOf 组装（rates-common.js 是唯一出处）。"""
    offenders = []
    for page in STATIC.rglob("*.html"):
        text = page.read_text(encoding="utf-8")
        if re.search(r"textContent\s*=\s*[^;]*数据截至", text):
            offenders.append(str(page.relative_to(STATIC)))
    assert not offenders, f"手写时效标签文案，请改 R.asOf(...)：{offenders}"


def test_as_of_formatter_present() -> None:
    """R.asOf / R.asMonth 存在且专题页确有调用（防止 formatter 被删空）。"""
    js = (STATIC / "js" / "rates-common.js").read_text(encoding="utf-8")
    assert "asOf(src)" in js and "asMonth" in js
    used = sum(1 for p in special_pages() if "R.asOf(" in p.read_text(encoding="utf-8"))
    assert used == len(PAGES), f"仅 {used}/{len(PAGES)} 个专题页用 R.asOf 组装时效标签"


# ── 脚本语法（无构建工具，改完必须能直接被浏览器/Node 解析）──


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
    """页面内联 <script> 逐个过 node --check（无构建工具，写坏了没有任何提示）。"""
    inline = re.compile(r"(?s)<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>")
    for page in sorted(STATIC.rglob("*.html")):
        code = page.read_text(encoding="utf-8")
        for n, m in enumerate(inline.finditer(code), 1):
            if m.group(1).strip():
                _check_js(m.group(1), f"{page.relative_to(STATIC)} 内联#{n}")
