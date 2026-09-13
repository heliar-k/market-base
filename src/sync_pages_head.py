"""专题页 <head> 单源同步 / 校验（前端风格统一 Phase 3A）。

背景：static/ 下的 HTML 由 FastAPI StaticFiles 原样服务，head 不能挪到只有构建期
才存在的模板文件里。所以 head 的单一来源 = 本脚本里的 HEAD_TEMPLATE，
各页 HTML 里的 head 是它的渲染产物，可重放覆盖（body 一律不动）。

用法：
    uv run python -m src.sync_pages_head             # 同步：把模板写回所有专题页
    uv run python -m src.sync_pages_head --check     # 只校验（有漂移退出码 1，不写盘）

防漂移规则（= 模板允许的槽位）：
- 固定样板：charset / icon / viewport / title / app.css / special.css / site-nav.js
- 每页槽位：title 页名、额外 css link、页内 <style> 块、额外 script（vendor 或 /js/）
- 出现规则外的行 → 报错，让人回来补槽位，而不是各自在页面里塞私货
- <title> 必须以「 — 观澜台」结尾（品牌名单一来源 BRAND）
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
# SPA 主入口 head 结构不同（不引 special.css、额外 vendor 脚本），不在收编范围
SPA_ENTRY = STATIC / "index.html"

BRAND = "观澜台"

# 固定样板；{title}=页名，{extra} 依次为：额外 css link → 页内 style → 额外 script
HEAD_TEMPLATE = """<head>
<meta charset="UTF-8">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} — {brand}</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap">
<link rel="stylesheet" href="/css/app.css">
<link rel="stylesheet" href="/css/special.css">
<script src="/js/site-nav.js"></script>
{extra}</head>"""

FIXED = (
    '<meta charset="UTF-8">',
    '<link rel="icon" href="/favicon.svg" type="image/svg+xml">',
    '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap">',
    '<link rel="stylesheet" href="/css/app.css">',
    '<link rel="stylesheet" href="/css/special.css">',
    '<script src="/js/site-nav.js"></script>',
)

_TITLE_RE = re.compile(r"^<title>(.+?) — (.+)</title>$")
_CSS_RE = re.compile(r'^<link rel="stylesheet" href="(/[^"]+)">$')
_SCRIPT_RE = re.compile(r'^<script src="(/[^"]+)"></script>$')
_HEAD_RE = re.compile(r"(?s)<head>.*?</head>")


def special_pages() -> list[Path]:
    """站内所有专题页 HTML（排除 SPA 主入口）。"""
    return sorted(p for p in STATIC.rglob("*.html") if p != SPA_ENTRY)


def normalize_head(head: str, path: Path) -> str:
    """把一段 head 重排为模板渲染结果；出现模板未覆盖的行则报错。"""
    lines = head[len("<head>") : -len("</head>")].split("\n")
    title: str | None = None
    css: list[str] = []
    styles: list[str] = []
    scripts: list[str] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        i += 1
        if not stripped:
            continue
        if stripped in FIXED:
            continue
        if stripped.startswith("<style"):
            block = [lines[i - 1]]
            while "</style>" not in block[-1]:
                block.append(lines[i])
                i += 1
            styles.append("\n".join(block))
            continue
        if m := _TITLE_RE.match(stripped):
            if m.group(2) != BRAND:
                raise ValueError(f"{path}: 标题品牌名不是「{BRAND}」：{stripped}")
            title = m.group(1)
            continue
        if _CSS_RE.match(stripped):
            css.append(stripped)
            continue
        if m := _SCRIPT_RE.match(stripped):
            scripts.append(stripped)
            continue
        raise ValueError(
            f"{path}: head 里有模板未覆盖的行，请补 HEAD_TEMPLATE 槽位：{stripped}"
        )
    if title is None:
        raise ValueError(f"{path}: head 里没有 <title>")
    extra = "".join(f"{line.rstrip()}\n" for line in css + styles + scripts)
    return HEAD_TEMPLATE.format(title=title, brand=BRAND, extra=extra)


def render_file(path: Path) -> str:
    """返回 head 收编后的整页文本（body 原样保留）。"""
    text = path.read_text(encoding="utf-8")
    m = _HEAD_RE.search(text)
    if not m:
        raise ValueError(f"{path}: 找不到 <head> 段")
    return text[: m.start()] + normalize_head(m.group(0), path) + text[m.end() :]


def sync(check: bool = False) -> int:
    """同步（或校验）所有专题页 head，返回漂移页数。"""
    drift = 0
    for path in special_pages():
        old = path.read_text(encoding="utf-8")
        new = render_file(path)
        if new == old:
            continue
        drift += 1
        rel = path.relative_to(ROOT)
        if check:
            print(f"HEAD 漂移：{rel}（修复：uv run python -m src.sync_pages_head）")
        else:
            print(f"同步 head：{rel}")
            path.write_text(new, encoding="utf-8")
    return drift


def main() -> None:
    check = "--check" in sys.argv
    drift = sync(check=check)
    verb = "校验" if check else "同步"
    print(f"{verb}完成：{len(special_pages())} 个专题页，{drift} 个 head 漂移")
    if drift and check:
        sys.exit(1)


if __name__ == "__main__":
    main()
