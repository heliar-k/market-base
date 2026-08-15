"""GitHub Pages 静态站点生成：预渲染 API 为 JSON + 复制前端 + 注入部署路径前缀。

用法：uv run python -m src.export_pages
（输出到 site/，供 upload-pages-artifact 部署）

原理：
- 直接调用 src.server 的路由函数（与 HTTP 同一代码路径），结果 _sanitize 后写 JSON
- 前端 fetch('/api/...') 在 Pages 子路径下会断 → 构建期把站内绝对路径
  /api /css /js /fed /volatility /rates /credit 统一加 /market-base 前缀
- K 线导出近 3 年全量 + _d2/_d5 尾部小文件（构建期把 ?days=N 请求转成
  小文件，避免侧栏预取拉全量）
- diag 的 as_of 变体（光标回看）无法预渲染所有日期 → 前端已降级为固定取最新
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import date, timedelta
from typing import Callable

import pandas as pd

from src.config import FRED_SERIES, ROOT
from src.macro import categories_for, load_macro_categories
from src.server import (  # noqa: PLC2701 复用路由函数
    _RANGE_MONTHS,
    HTTPException,
    _sanitize,
    get_credit_cds,
    get_credit_overview,
    get_credit_stress,
    get_diag,
    get_fed_overview,
    get_fomc_calendar,
    get_inflation_overview,
    get_kline,
    get_labor_overview,
    get_liquidity_overview,
    get_macro,
    get_macro_correlate,
    get_macro_presets,
    get_rate_expectations,
    get_rates_analysis,
    get_rates_auctions,
    get_rates_fed_funds,
    get_rates_real_rates,
    get_rates_yield_curve,
    get_symbols,
    get_treasury_overview,
    get_volatility_analysis,
)

SITE = ROOT / "site"
STATIC = ROOT / "static"
BASE = "/market-base"  # Pages 部署子路径；repo 改名需同步
KLINE_YEARS = 3
CORRELATE_YEARS = 5  # 全指标合并文件体积大，截 5 年（10Y/30Y/All 按钮显示止于此处）

# 站内绝对路径前缀（html/js 中出现，均需加 BASE；https:// 不受影响）
_PATH_PREFIXES = (
    "api",
    "css",
    "js",
    "vendor",
    "favicon",
    "fed",
    "inflation",
    "labor",
    "treasury",
    "liquidity",
    "volatility",
    "rates",
    "credit",
)

_PREFIX_RE = re.compile(r'(["\'\x60])/(' + "|".join(_PATH_PREFIXES) + r")/")
# kline ?days=N → 尾部小文件（模板字符串与字面量两种写法）
_KLINE_DAYS_TMPL_RE = re.compile(r"/api/kline/(\$\{[^}]+\})\?days=(\d+)")
_KLINE_DAYS_LIT_RE = re.compile(r"/api/kline/([A-Za-z.]+)\?days=(\d+)")
# correlate 动态查询 → 静态全量文件（本地 dev 用 FastAPI，静态版本地过滤）
_CORRELATE_RE = re.compile(r"/api/macro/correlate\?indicators=[^'\"`]*")
# 流动性 overview 动态 URL（range query）→ 静态文件名（静态托管忽略 query）
# 注意：不匹配引号，替换后前缀规则再处理；模板字符串（dateRange 变量）单独一条
_LIQ_URL_RE = re.compile(r"/api/liquidity/overview\?range=([a-z0-9]+)")
_LIQ_URL_TMPL_RE = re.compile(
    r"/api/liquidity/overview\?range=\$\{encodeURIComponent\(dateRange\)\}"
)
_HOME_RE = re.compile(r'href="/"')


def _dump(rel: str, obj: object) -> None:
    """写 JSON（NaN → null），建父目录，打印体积。"""
    path = SITE / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(_sanitize(obj), ensure_ascii=False, separators=(",", ":"))
    path.write_text(payload, encoding="utf-8")
    size = len(payload.encode()) / 1024
    print(f"  {size:9.0f} KB  {rel}")


def _safe(rel: str, fn: Callable[..., object], *args: object, **kwargs: object) -> None:
    """调用路由函数；404（数据缺失）跳过并警告。"""
    try:
        _dump(rel, fn(*args, **kwargs))
    except HTTPException as e:
        print(f"  SKIP {rel}: {e.detail}")
    except Exception as e:  # 其他异常不阻断整体导出
        print(f"  SKIP {rel}: {type(e).__name__}: {e}")


def export_api() -> None:
    symbols = get_symbols()
    _dump("api/symbols", symbols)

    # K 线：近 3 年（as_of 截断）；days=2/5 变体自动兼容（前端只取尾部）
    # CDL_* 62 列前端未使用，剔除控体积（约 -45%）
    as_of = (date.today() - timedelta(days=365 * KLINE_YEARS)).isoformat()
    for s in symbols:
        records = get_kline(s["name"], as_of=as_of, interval="1d", days=0)
        records = [
            {k: v for k, v in r.items() if not k.startswith("CDL_")} for r in records
        ]
        _dump(f"api/kline/{s['name']}", records)
        # 尾部小文件：?days=N 变体（静态托管忽略 query，前端预取价格只用尾部几行）
        for n in (2, 5):
            tail = get_kline(s["name"], as_of=as_of, interval="1d", days=n)
            tail = [
                {k: v for k, v in r.items() if not k.startswith("CDL_")} for r in tail
            ]
            _dump(f"api/kline/{s['name']}_d{n}", tail)
        _safe(f"api/diag/{s['name']}", get_diag, s["name"], as_of=None)

    # 宏观：presets + 全分类 + correlate 全指标合并（截 5 年控体积）
    _dump("api/macro/presets", get_macro_presets())
    for cat in FRED_SERIES:
        _safe(f"api/macro/{cat}", get_macro, cat)
    merged = load_macro_categories(list(FRED_SERIES))
    all_indicators = [c for c in merged.columns if categories_for(c) is not None]
    correlate = get_macro_correlate(indicators=",".join(all_indicators))
    cutoff = (pd.Timestamp.now() - pd.DateOffset(years=CORRELATE_YEARS)).strftime(
        "%Y-%m-%d"
    )
    for info in correlate["indicators"].values():
        info["data"] = [d for d in info["data"] if d["date"] >= cutoff]
    correlate["date_range"]["from"] = cutoff
    _dump("api/macro/correlate.json", correlate)

    # 流动性：全部 range 变体（前端 toolbar 可点）
    for range_ in ["all", *_RANGE_MONTHS]:
        _safe(f"api/liquidity/overview_{range_}.json", get_liquidity_overview, range_)

    # 专题页（FOMC / rates / volatility / fed / credit）
    _safe("api/fomc/calendar", get_fomc_calendar)
    _safe("api/rate-expectations", get_rate_expectations)
    for name, fn in [
        ("analysis", get_rates_analysis),
        ("fed-funds", get_rates_fed_funds),
        ("yield-curve", get_rates_yield_curve),
        ("real-rates", lambda: get_rates_real_rates(days=365)),
        ("auctions", get_rates_auctions),
    ]:
        _safe(f"api/rates/{name}", fn)
    _safe("api/volatility/analysis", get_volatility_analysis)
    from src.volatility_dashboard import generate_dashboard

    _safe("api/volatility/dashboard", generate_dashboard)
    _safe("api/fed/overview", get_fed_overview)
    _safe("api/credit/overview", get_credit_overview)
    _safe("api/credit/cds", get_credit_cds)
    _safe("api/credit/stress", get_credit_stress)
    _safe("api/inflation/overview", get_inflation_overview)
    _safe("api/treasury/overview", get_treasury_overview)
    _safe("api/labor/overview", get_labor_overview)


def export_frontend() -> None:
    """复制 static/ → site/，并注入 Pages 子路径前缀。"""
    if SITE.exists():
        shutil.rmtree(SITE)
    shutil.copytree(STATIC, SITE)
    for p in SITE.rglob("*"):
        if p.is_file() and p.suffix in (".html", ".js"):
            text = p.read_text(encoding="utf-8")
            # 动态 API URL → 静态文件名（本地 dev 用 FastAPI 路由，静态版用文件名）；
            # 必须先转文件名再加前缀，否则 /market-base 前缀会被二次匹配
            text = _CORRELATE_RE.sub(f"{BASE}/api/macro/correlate.json", text)
            text = _LIQ_URL_TMPL_RE.sub(
                rf"{BASE}/api/liquidity/overview_${{dateRange}}.json", text
            )
            text = _LIQ_URL_RE.sub(rf"{BASE}/api/liquidity/overview_\1.json", text)
            text = _KLINE_DAYS_TMPL_RE.sub(rf"{BASE}/api/kline/\1_d\2", text)
            text = _KLINE_DAYS_LIT_RE.sub(rf"{BASE}/api/kline/\1_d\2", text)
            text = _PREFIX_RE.sub(rf"\1{BASE}/\2/", text)
            # favicon.svg 在站点根目录（无目录斜杠，前缀规则匹配不到），单独替换
            text = text.replace('href="/favicon.svg"', f'href="{BASE}/favicon.svg"')
            text = _HOME_RE.sub(f'href="{BASE}/"', text)
            p.write_text(text, encoding="utf-8")


def main() -> None:
    print("复制前端并注入路径前缀 → site/ ...")
    export_frontend()
    print("导出 API JSON → site/api/ ...")
    export_api()
    total = sum(f.stat().st_size for f in SITE.rglob("*") if f.is_file()) / 1024 / 1024
    print(f"完成：site/ 共 {total:.1f} MB（含未压缩 JSON）")


if __name__ == "__main__":
    main()
