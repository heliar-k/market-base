"""首屏「今日一句话」结论层（issue #21）：导出字段 + 前端消费两端各锁一刀。

形状：`/api/home-lines` → {generator, lines:[{key,label,text,as_of}]}，key 必须是
SITE_NAV 的组键/子页键（前端靠它解出专题页路径，不硬编码路由）。
"""

from __future__ import annotations

import re
from pathlib import Path

from src.server import get_home_lines

ROOT = Path(__file__).resolve().parent.parent

# 9 个专题引擎；少一行就是某个引擎的叙事字段改名/断供了（红的时候看 skip 日志）
EXPECTED_KEYS = {
    "rates",
    "credit",
    "inflation",
    "labor",
    "treasury",
    "vol",
    "liquidity",
    "assets",
    "fed",
}


def test_home_lines_covers_nine_engines() -> None:
    out = get_home_lines()
    lines = out["lines"]
    assert out["generator"] == "rules"
    assert {x["key"] for x in lines} == EXPECTED_KEYS, (
        "有专题引擎没出一句话（看导出日志里的 home-lines skip）："
        f"{sorted(EXPECTED_KEYS - {x['key'] for x in lines})}"
    )
    nav = (ROOT / "frontend/public/js/site-nav.js").read_text(encoding="utf-8")
    nav_keys = set(re.findall(r"key: '([^']+)'", nav))
    for line in lines:
        assert line["key"] and line["label"] and line["text"], line
        assert line["key"] in nav_keys, f"key 解不出专题页：{line['key']}"
        # 首句不带句末句号（行尾还有观测日，读起来才像一行结论）
        assert not line["text"].endswith("。"), line
        assert line["as_of"] is None or re.fullmatch(r"\d{4}-\d{2}-\d{2}", line["as_of"]), line


def test_daily_consumes_home_lines() -> None:
    """今日头版（/daily/，ADR-0005 后 home-lines 唯一消费方）必须拉 /api/home-lines。

    原 test_dashboard_consumes_home_lines 随 SPA 仪表盘视图删除而改写：
    dashboard.js 的 NAV_PAGES/SITE_NAV 派生守卫同批废弃，专题路径改在
    test_static_single_source 的字面量链接守卫下覆盖。
    """
    js = (ROOT / "frontend/src/pages/daily/index.astro").read_text(encoding="utf-8")
    assert "/api/home-lines" in js, "今日头版未接入 /api/home-lines"
    assert "LINE_LINKS" in js and "R.setAsOf(" in js, "缺 LINE_LINKS 映射或 R.setAsOf 时效组装"
