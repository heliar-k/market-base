# pip install fastapi uvicorn
"""FastAPI backend for K-line analysis web prototype."""

from __future__ import annotations

import math
import re
import time
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import numpy as np
import pandas as pd
import requests
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.analyze import analyze, score_history
from src.cache import load_or_compute
from src.chat_proxy import (
    BODY_MAX as CHAT_BODY_MAX,
)
from src.chat_proxy import (
    MISSING_KEY,
    ChatValidationError,
    build_payload,
    chat_models,
    llm_base,
    llm_key,
    validate_messages,
    validate_tool_choice,
    validate_tools,
)
from src.config import (
    FOMC_MEETINGS,
    IBKR_SYMBOLS,
    ROOT,
    TERM_INFO,
)
from src.credit_analysis import (
    generate_credit_cds,
    generate_credit_overview,
    generate_credit_stress,
    generate_euro_sovereign,
)
from src.fed_analysis import (
    generate_fed_analysis,
    market_odds,
    polymarket_fomc_history,
    polymarket_fomc_odds,
    zq_buckets_from_probs,
)
from src.inflation_analysis import generate_inflation_overview
from src.labor_analysis import generate_labor_overview
from src.macro import (
    DERIVED_INPUTS,
    categories_for,
    load_macro_categories,
    load_macro_category,
    read_macro_category,
    rrp_in_millions,
)
from src.notes_store import (
    BODY_MAX as NOTES_BODY_MAX,
)
from src.notes_store import (
    MAX_BYTES as NOTES_MAX_BYTES,
)
from src.notes_store import (
    save_note as save_note_file,
)
from src.rates_analysis import generate_analysis
from src.treasury_analysis import generate_treasury_overview
from src.volatility_analysis import generate_volatility_analysis

# ── helpers ──────────────────────────────────────────────────────────────────

_SYMBOL_TYPE = {s["name"]: s["type"] for s in IBKR_SYMBOLS}


def _csv_path(symbol: str) -> Path:
    sub = "indices" if _SYMBOL_TYPE.get(symbol) == "index" else "stocks"
    return ROOT / "data" / sub / f"{symbol}.csv"


def _sanitize(obj: Any) -> Any:
    """Recursively convert numpy types to Python native; NaN/NA/NaT → None。"""
    if obj is pd.NA or obj is pd.NaT:
        return None
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return None if math.isnan(f) else f
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return _sanitize(obj.tolist())
    if isinstance(obj, pd.Timestamp):
        return obj.strftime("%Y-%m-%d")
    if isinstance(obj, (np.str_,)):
        return str(obj)
    return obj


class _SanitizedJSONResponse(JSONResponse):
    """默认响应类：渲染边界统一 _sanitize（numpy 类型 → 原生，NaN → None）。"""

    def render(self, content: Any) -> bytes:
        return super().render(_sanitize(content))


_RANGE_MONTHS = {
    "1m": 1,
    "3m": 3,
    "6m": 6,
    "1y": 12,
    "2y": 24,
    "3y": 36,
    "5y": 60,
    "10y": 120,
    "30y": 360,
}


def _range_cutoff(range_: str, default: int = 0) -> pd.Timestamp | None:
    """range 参数 → 截止时点；"all" 或未知值（default=0 时）返回 None。"""
    months = None if range_ == "all" else _RANGE_MONTHS.get(range_, default)
    return pd.Timestamp.now() - pd.DateOffset(months=months) if months else None


app = FastAPI(title="K-line Analysis Web", default_response_class=_SanitizedJSONResponse)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── kline endpoints ──────────────────────────────────────────────────────────


@app.get("/api/symbols")
def get_symbols():
    return IBKR_SYMBOLS


@app.get("/api/kline/{symbol}")
def get_kline(
    symbol: str,
    as_of: str | None = Query(None),
    interval: str = Query("1d"),
    days: int = Query(0),
):
    csv = _csv_path(symbol)
    if not csv.exists():
        raise HTTPException(404, f"No data for {symbol}")
    df = load_or_compute(symbol, csv)
    if as_of:
        df = df.loc[: pd.Timestamp(as_of)]
    if interval != "1d":
        df = _resample_ohlcv(df, interval)
    if days > 0:
        df = df.tail(days)
    df = df.reset_index()  # date index → column
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    records = df.to_dict(orient="records")
    return records


# ── stock endpoints ──────────────────────────────────────────────────────────

_STOCKS_DIR = ROOT / "data" / "stocks"


@app.get("/api/stocks")
def get_stocks():
    stocks = sorted(p.stem for p in _STOCKS_DIR.glob("*.csv"))
    return {"stocks": stocks}


@app.get("/api/kline/{symbol}/indicators")
def get_kline_indicators(symbol: str, days: int = Query(365)):
    csv = _csv_path(symbol)
    if not csv.exists():
        raise HTTPException(404, f"No data for {symbol}")
    df = load_or_compute(symbol, csv)
    if days > 0:
        df = df.tail(days)
    df = df.reset_index()
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    # Map internal column names → spec names
    _COL_MAP = {
        "MA5": "MA5",
        "MA20": "MA20",
        "MA60": "MA60",
        "MA120": "MA120",
        "BB_upper": "BB_Upper",
        "BB_mid": "BB_Mid",
        "BB_lower": "BB_Lower",
        "MACD": "MACD",
        "MACD_signal": "MACD_Signal",
        "MACD_hist": "MACD_Hist",
        "RSI": "RSI",
        "volume": "volume",
    }
    out = df[["date", "close"]].copy()
    for src, dst in _COL_MAP.items():
        out[dst] = df[src] if src in df.columns else pd.NA
    return {"symbol": symbol, "data": out.to_dict(orient="records")}


def _resample_ohlcv(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    rule = {"1wk": "W-FRI", "1mo": "ME"}.get(interval)
    if not rule:
        return df
    resampled = (
        df.resample(rule)
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        .dropna()
    )
    return resampled


@app.get("/api/diag/{symbol}")
def get_diag(symbol: str, as_of: str | None = Query(None)):
    csv = _csv_path(symbol)
    if not csv.exists():
        raise HTTPException(404, f"No data for {symbol}")
    df = load_or_compute(symbol, csv)
    result = analyze(df, symbol, as_of=as_of)
    return result


@app.get("/api/tech/overview")
def get_tech_overview():
    """全标的（指数 + 个股）技术面诊断总览：供技术分析页结论总览视图一次拉齐。"""
    items = []
    for s in IBKR_SYMBOLS:
        sym = s["name"]
        csv = _csv_path(sym)
        if not csv.exists():
            continue
        try:
            df = load_or_compute(sym, csv)
            if df.empty:
                continue
            d = analyze(df, sym)
            d["type"] = s["type"]
            d["score_history"] = score_history(df, sym)
            items.append(d)
        except Exception:
            continue
    return items


# ── macro endpoints ──────────────────────────────────────────────────────────

# 派生指标 → 所需原始列的源分类
# （macro.categories_for 从 DERIVED_INPUTS + FRED_SERIES 派生）

# 中文标签（与前端 MACRO_LABELS 保持同步）。
# 期限品种（DGS*/DFII*）的长名/短标签统一在 config.TERM_INFO，此处不再重复维护。
_MACRO_LABELS = {
    "VIX": "波动率指数（恐慌指数）",
    "HY_OAS": "高收益债信用利差",
    "IG_OAS": "投资级债信用利差",
    "CPI": "消费者物价指数",
    "PCE": "个人消费支出价格指数",
    "CORE_CPI": "核心消费者物价指数",
    "T5YIE": "5年期通胀预期",
    "T10YIE": "10年期通胀预期",
    "T5YIFR": "5年期远期通胀率",
    "MICH": "密歇根通胀预期",
    "EXPINF_1Y": "1年期通胀预期",
    "EXPINF_2Y": "2年期通胀预期",
    "EXPINF_5Y": "5年期通胀预期",
    "EXPINF_10Y": "10年期通胀预期",
    "UNRATE": "失业率",
    "PAYEMS": "非农就业人数",
    "ICSA": "初请失业金人数",
    "GDP": "国内生产总值(GDP)",
    "INDPRO": "工业生产指数",
    "FEDFUNDS": "联邦基金利率",
    "DFF": "有效联邦基金利率",
    "DFEDTARL": "FOMC 目标利率下限",
    "DFEDTARU": "FOMC 目标利率上限",
    "SOFR": "担保隔夜融资利率",
    "SOFR1": "SOFR 1st 分位数",
    "SOFR25": "SOFR 25th 分位数",
    "SOFR75": "SOFR 75th 分位数",
    "SOFR99": "SOFR 99th 分位数",
    "SOFRVOL": "SOFR 日成交量",
    "OBFR": "隔夜银行融资利率",
    "IORB": "准备金余额利率",
    "SPREAD_2S10S": "2s10s利差",
    "SPREAD_3M10S": "3m10s利差",
    "SPREAD_5S30S": "5s30s利差",
    "SOFR_IORB_SPREAD_BP": "SOFR-IORB利差(bp)",
    "BEI_5Y": "5年期盈亏平衡通胀率",
    "BEI_7Y": "7年期盈亏平衡通胀率",
    "BEI_10Y": "10年期盈亏平衡通胀率",
    "BEI_20Y": "20年期盈亏平衡通胀率",
    "BEI_30Y": "30年期盈亏平衡通胀率",
    "NFCI": "金融状况指数",
    "RRPONTSYD": "隔夜逆回购规模",
    "WTREGEN": "财政部一般账户余额",
    "WRESBAL": "准备金余额",
    "WALCL": "美联储总资产",
    "NET_LIQUIDITY": "净流动性",
    "UMCSENT": "密歇根消费者信心指数",
    "STLFSI4": "金融压力指数",
    "DXY": "美元指数",
}
# 期限品种的长名合并进同一查找表（单一数据源在 config.TERM_INFO）
_MACRO_LABELS.update({k: v.name for k, v in TERM_INFO.items()})


@app.get("/api/macro/presets")
def get_macro_presets():
    return {
        "presets": [
            {
                "id": "inflation_vs_rates",
                "name": "通胀 vs 利率",
                "description": "CPI 与联邦基金利率的历史关系",
                "indicators": ["CPI", "FEDFUNDS"],
                "left_axis": ["CPI"],
                "right_axis": ["FEDFUNDS"],
            },
            {
                "id": "yield_curve",
                "name": "收益率曲线",
                "description": "2s10s利差与衰退阴影",
                "indicators": ["SPREAD_2S10S", "FEDFUNDS"],
                "left_axis": ["SPREAD_2S10S"],
                "right_axis": ["FEDFUNDS"],
            },
            {
                "id": "liquidity_vix",
                "name": "流动性 vs 波动率",
                "description": "净流动性与VIX的反向关系",
                "indicators": ["NET_LIQUIDITY", "VIX"],
                "left_axis": ["NET_LIQUIDITY"],
                "right_axis": ["VIX"],
            },
            {
                "id": "labor_inflation",
                "name": "就业 vs 通胀",
                "description": "失业率与CPI的菲利普斯曲线关系",
                "indicators": ["UNRATE", "CPI"],
                "left_axis": ["UNRATE"],
                "right_axis": ["CPI"],
            },
            {
                "id": "dollar_rates",
                "name": "美元 vs 利差",
                "description": "美元指数与2s10s利差",
                "indicators": ["DXY", "SPREAD_2S10S"],
                "left_axis": ["DXY"],
                "right_axis": ["SPREAD_2S10S"],
            },
            # 宏观×资产对：资产序列不在 FRED CSV，由前端从 /api/assets/prices
            # 拼接（asset 字段标注列名）
            {
                "id": "cpi_spx",
                "name": "通胀 vs 美股",
                "description": "CPI 与标普500的联动（通胀驱动 vs 盈利驱动）",
                "indicators": ["CPI", "SPX"],
                "left_axis": ["CPI"],
                "right_axis": ["SPX"],
                "asset": {"SPX": "SPX"},
            },
            {
                "id": "credit_hy",
                "name": "信用利差 vs 高收益债",
                "description": "HY OAS 与 HYG 价格（利差走阔 = 价格下跌，负相关为常态）",  # noqa: E501
                "indicators": ["HY_OAS", "HYG"],
                "left_axis": ["HY_OAS"],
                "right_axis": ["HYG"],
                "asset": {"HYG": "HYG"},
            },
            {
                "id": "rates_tlt",
                "name": "10Y 利率 vs 长债",
                "description": "10 年期收益率与 TLT 价格（久期负相关）",
                "indicators": ["DGS10", "TLT"],
                "left_axis": ["DGS10"],
                "right_axis": ["TLT"],
                "asset": {"TLT": "TLT"},
            },
        ]
    }


# ── FOMC calendar ────────────────────────────────────────────────────────────


@app.get("/api/fomc/calendar")
def get_fomc_calendar() -> dict:
    """返回当前利率区间、下次 FOMC 会议日期、及目标利率上下界。"""
    today = date.today()
    current_meeting = None
    next_meeting = None
    for m in sorted(FOMC_MEETINGS):
        meeting_end = date(m.year, m.month, m.end_day)
        if meeting_end < today:
            current_meeting = m
        elif next_meeting is None:
            next_meeting = m

    # 当前目标区间 = CSV 最后一个非空 DFEDTARL/U（与 /api/rates/fed-funds 同口径）。
    # 不能锚定会议结束日：FRED 在决议次日才切换新区间，结束日当天取到的永远是旧区间。
    target_lower = None
    target_upper = None
    try:
        df = read_macro_category("rates")
    except FileNotFoundError:
        df = None
    if df is not None:
        tarl = df["DFEDTARL"].dropna() if "DFEDTARL" in df else pd.Series(dtype=float)
        taru = df["DFEDTARU"].dropna() if "DFEDTARU" in df else pd.Series(dtype=float)
        if not tarl.empty and not taru.empty:
            target_lower = float(tarl.iloc[-1])
            target_upper = float(taru.iloc[-1])

    # 未来会议列表（end_day >= 今天，含进行中场）：静态站 JSON 会被固化，
    # 前端按打开页面时间从此列表重选下一场；next 保留兼容旧页面
    upcoming_meetings = [
        {
            "year": m.year,
            "month": m.month,
            "start_day": m.start_day,
            "end_day": m.end_day,
        }
        for m in sorted(FOMC_MEETINGS)
        if date(m.year, m.month, m.end_day) >= today
    ][:4]

    return {
        "current": (
            {
                "year": current_meeting.year,
                "month": current_meeting.month,
                "start_day": current_meeting.start_day,
                "end_day": current_meeting.end_day,
            }
            if current_meeting
            else None
        ),
        "next": (
            {
                "year": next_meeting.year,
                "month": next_meeting.month,
                "start_day": next_meeting.start_day,
                "end_day": next_meeting.end_day,
            }
            if next_meeting
            else None
        ),
        "target_lower": target_lower,
        "target_upper": target_upper,
        "meetings": upcoming_meetings,
    }


@app.get("/api/macro/correlate")
def get_macro_correlate(
    indicators: str = Query(..., description="Comma-separated indicator names"),
):
    names = [i.strip() for i in indicators.split(",") if i.strip()]
    if not names:
        raise HTTPException(400, "No indicators specified")

    # 收集所需分类（原始 + 派生输入列归属）
    cats_needed: set[str] = set()
    for name in names:
        cats = categories_for(name)
        if cats is None:
            raise HTTPException(404, f"Unknown indicator: {name}")
        cats_needed.update(cats)

    # 加载所需分类并集（伙伴分类自动并入，outer join 合并后一次派生）
    try:
        merged = load_macro_categories(cats_needed)
    except FileNotFoundError:
        raise HTTPException(404, "No data for requested categories")

    # 校验请求的指标都已在派生后存在
    for name in names:
        if name not in merged.columns:
            raise HTTPException(404, f"Indicator {name} not available in data")

    # 显示层单位统一（RRP 十亿→百万，与旧响应一致；不影响派生列）
    merged = rrp_in_millions(merged)

    # Build response
    merged = merged.reset_index()
    merged["date"] = merged["date"].dt.strftime("%Y-%m-%d")

    result: dict[str, Any] = {}
    for name in names:
        cat = "derived" if name in DERIVED_INPUTS else (categories_for(name) or ["derived"])[0]
        result[name] = {
            "category": cat,
            "label": _MACRO_LABELS.get(name, name),
            "data": (
                merged[["date", name]].rename(columns={name: "value"}).to_dict(orient="records")
            ),
        }

    date_range = {"from": merged["date"].iloc[0], "to": merged["date"].iloc[-1]}
    return {"indicators": result, "date_range": date_range}


@app.get("/api/macro/{category}")
def get_macro(category: str):
    try:
        df = load_macro_category(category)
    except FileNotFoundError:
        raise HTTPException(404, f"No macro data for {category}")
    df = rrp_in_millions(df)
    df = df.reset_index()
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    return df.to_dict(orient="records")


# ── liquidity endpoints ──────────────────────────────────────────────────────

_LIQ_KEYS = ["WALCL", "RRPONTSYD", "WRESBAL", "WTREGEN", "NET_LIQUIDITY"]


def _liq_pct(current: float, past: float) -> float | None:
    if past is None or past == 0 or current is None:
        return None
    return round((current - past) / abs(past), 4)


def _liq_latest(s: pd.Series) -> float | None:
    v = s.dropna()
    return float(v.iloc[-1]) if len(v) > 0 else None


def _liq_summary(df: pd.DataFrame, key: str) -> dict:
    s = df[key] if key in df.columns else pd.Series(dtype=float)
    latest = _liq_latest(s)
    if latest is None:
        return {"latest_value": None, "change_1m": None, "change_1y": None}
    now = s.dropna().index[-1]
    m1 = _liq_latest(s.loc[: now - pd.DateOffset(months=1)])
    y1 = _liq_latest(s.loc[: now - pd.DateOffset(years=1)])
    return {
        "latest_value": latest,
        "change_1m": _liq_pct(latest, m1),
        "change_1y": _liq_pct(latest, y1),
    }


@app.get("/api/liquidity/overview")
def get_liquidity_overview(range: str = Query("all")):
    try:
        df = load_macro_category("liquidity")
    except FileNotFoundError:
        raise HTTPException(404, "No liquidity data")
    # 显示层单位统一：RRP 十亿→百万，与 WRESBAL/WTREGEN 同图（stacked）同单位
    df = rrp_in_millions(df)

    cutoff = _range_cutoff(range)

    summary = {}
    labels = {
        "WALCL": "美联储总资产",
        "RRPONTSYD": "隔夜逆回购",
        "WRESBAL": "准备金余额",
        "WTREGEN": "TGA余额",
        "NET_LIQUIDITY": "净流动性",
    }
    for key in _LIQ_KEYS:
        summary[key] = {**_liq_summary(df, key), "label": labels.get(key, key)}

    filtered = df.loc[cutoff:] if cutoff is not None else df
    filtered = filtered.reset_index()
    filtered["date"] = filtered["date"].dt.strftime("%Y-%m-%d")

    series = {}
    for key in _LIQ_KEYS + ["NFCI"]:
        if key in filtered.columns:
            series[key] = (
                filtered[["date", key]].rename(columns={key: "value"}).to_dict(orient="records")
            )

    # Pre-compute stacked cumulative for area chart (WRESBAL → +WTREGEN → +RRPONTSYD)
    # 周频列（WRESBAL/WTREGEN）在非发布日必须 ffill 而非 fillna(0)：
    # fillna(0) 会让累计在非周三日期跌到 ~0，产生万亿级假跌落（审计 P1-⑪）。
    # 仅序列开头无前值的 NaN 补 0（此前观测为 0，语义正确）。
    stack_keys = ["WRESBAL", "WTREGEN", "RRPONTSYD"]
    cum = pd.DataFrame(index=filtered.index)
    running = pd.Series(0.0, index=filtered.index)
    for sk in stack_keys:
        col = filtered[sk] if sk in filtered.columns else pd.Series(0.0, index=filtered.index)
        running = running.add(col.ffill().fillna(0))
        cum[sk] = running
    stacked = {}
    for sk in stack_keys:
        stacked[sk] = filtered[["date"]].assign(value=cum[sk]).to_dict(orient="records")

    return {"summary": summary, "series": series, "stacked": stacked}


@app.get("/api/liquidity/compare-spx")
def get_liquidity_compare_spx(range: str = Query("5y")):
    try:
        df = load_macro_category("liquidity")
    except FileNotFoundError:
        raise HTTPException(404, "No liquidity data")

    cutoff = _range_cutoff(range, default=60)

    filtered = df.loc[cutoff:] if cutoff is not None else df
    filtered = filtered.reset_index()
    filtered["date"] = filtered["date"].dt.strftime("%Y-%m-%d")

    result: dict[str, Any] = {}
    if "NET_LIQUIDITY" in filtered.columns:
        result["NET_LIQUIDITY"] = (
            filtered[["date", "NET_LIQUIDITY"]]
            .rename(columns={"NET_LIQUIDITY": "value"})
            .to_dict(orient="records")
        )

    # Try SPX from yfinance data
    spx_path = ROOT / "data" / "indices" / "SPX.csv"
    if spx_path.exists():
        spx = pd.read_csv(spx_path, index_col="date", parse_dates=True)
        if cutoff is not None:
            spx = spx.loc[cutoff:]
        spx = spx.reset_index()
        spx["date"] = spx["date"].dt.strftime("%Y-%m-%d")
        close_col = "close" if "close" in spx.columns else spx.columns[1]
        result["SPX"] = (
            spx[["date", close_col]].rename(columns={close_col: "value"}).to_dict(orient="records")
        )

    return result


# ── rate expectations ────────────────────────────────────────────────────────


# ── liquidity hub（复刻 timsun.net/liquidity 主页面 + 7 子页）────────────


@app.get("/api/liquidity/snapshot")
def get_liquidity_snapshot() -> dict:
    """主页面：卡片 + 评估叙事（净流动性 / 展望 / 准备金三段）。"""
    from src.liquidity_analysis import liquidity_snapshot

    return liquidity_snapshot()


@app.get("/api/liquidity/transmission-chain")
def get_liquidity_transmission_chain() -> dict:
    """子页：规则型美元流动性压力指数 LPI（三层加权 + 确认条件 + 证据层）。"""
    from src.liquidity_analysis import lpi

    return lpi()


@app.get("/api/liquidity/fed-balance-sheet")
def get_liquidity_fed_balance_sheet() -> dict:
    """子页：美联储资产负债表（卡片 + 近 20 日对照表）。"""
    from src.liquidity_analysis import page_fed_balance_sheet

    return page_fed_balance_sheet()


@app.get("/api/liquidity/operations")
def get_liquidity_operations() -> dict:
    """子页：公开市场操作（RMP / 最近 40 条 / SOMA / SRF 90 天）。"""
    from src.liquidity_analysis import page_operations

    return page_operations()


@app.get("/api/liquidity/rrp-tga")
def get_liquidity_rrp_tga() -> dict:
    """子页：RRP & TGA（压力预警 / 未来 14 天热力 / 日度现金流）。"""
    from src.liquidity_analysis import page_rrp_tga

    return page_rrp_tga()


@app.get("/api/liquidity/reserves")
def get_liquidity_reserves() -> dict:
    """子页：银行准备金（余额 + 中介意愿双确认）。"""
    from src.liquidity_analysis import page_reserves

    return page_reserves()


@app.get("/api/liquidity/global-dollar")
def get_liquidity_global_dollar() -> dict:
    """子页：全球美元（DXY / 央行互换 / 掉期点一览）。"""
    from src.liquidity_analysis import page_global_dollar

    return page_global_dollar()


@app.get("/api/liquidity/subsurface")
def get_liquidity_subsurface() -> dict:
    """子页：次表层资金流（SOFR 分位/成交量 z/SRF/央行互换）。"""
    from src.liquidity_analysis import page_subsurface

    return page_subsurface()


@app.get("/api/rate-expectations")
def get_rate_expectations() -> dict:
    csv_path = ROOT / "data" / "rate_expectations" / "fomc_probabilities.csv"
    if not csv_path.exists():
        raise HTTPException(404, "No rate expectations data yet. Run ./bin/fetch_rate_expectations")
    df = pd.read_csv(csv_path, index_col="date", parse_dates=True)

    # range cols → normalized rows
    range_cols = [c for c in df.columns if c.startswith("range_")]

    latest_date = df.index.max()
    # 用 [latest_date]（双层）而不是 latest_date：当日快照只有 1 行时 df.loc[date]
    # 返回 Series，Series 没有 drop_duplicates(subset=…) → TypeError → 整个端点 500。
    # 这不是假设：FOMC_MEETINGS 只到 2026-12-09，2026-10-29 起未来只剩一场会议，
    # 快照恒为单行；ZQ 时效闸门把最近一场拒掉时也会立刻命中。
    latest = df.loc[[latest_date]]
    # 同日多次写入会产生重复行（upsert 幂等性缺陷），按会议去重
    latest = latest.drop_duplicates(subset=["meeting_date"])
    # 会议按日期升序：最近的一场排在最上
    latest = latest.sort_values("meeting_date")
    # 过滤已结束会议：基准 = 页面构建/请求时的今天（非数据快照日——数据停更时
    # 快照日会早于会议结束，导致会后行继续显示）。会议当天凌晨采样时尚未开会，保留。
    latest = latest[latest["meeting_date"] >= date.today().strftime("%Y-%m-%d")]

    meetings = []
    for _, row in latest.iterrows():
        meeting_date = pd.to_datetime(row["meeting_date"]).strftime("%Y-%m-%d")
        probs = []
        for c in range_cols:
            lo, hi = c.replace("range_", "").split("-")
            p = row.get(c, 0.0)
            if pd.notna(p) and float(p) > 0:
                probs.append({"lo": float(lo), "hi": float(hi), "prob": round(float(p), 4)})

        # 旧快照无 contract_used / zq_as_of 列 → 回退 contract / None
        contract_used = row.get("contract_used")
        zq_as_of = row.get("zq_as_of")
        meetings.append(
            {
                "meeting_date": meeting_date,
                "contract": row["contract"],
                "contract_used": (
                    str(contract_used) if pd.notna(contract_used) else row["contract"]
                ),
                "implied_rate": float(row["implied_rate"]),
                "post_meeting_rate": float(row["post_meeting_rate"]),
                "zq_as_of": str(zq_as_of) if pd.notna(zq_as_of) else None,
                "prob_cut": float(row["prob_cut"]),
                "prob_hold": float(row["prob_hold"]),
                "prob_hike": float(row["prob_hike"]),
                "expectation": row["expectation"],
                "probs": probs,
            }
        )

    # Polymarket 对照：fed 分类 "Fed Decision in {Month}?" 事件按 (年, 月) 匹配会议。
    # market_odds 无数据返回 None → 对照列整体为空（前端显「—」），不影响 ZQ 渲染。
    pm = market_odds()
    pm_odds = polymarket_fomc_odds(
        pm["events"] if pm else [], [m["meeting_date"] for m in meetings]
    )
    # ZQ 五档：range 概率相对当前目标区间分档（区间取 /api/fomc/calendar 同源）
    cal = get_fomc_calendar()
    for m in meetings:
        m["polymarket"] = pm_odds.get(m["meeting_date"])
        m["zq_buckets"] = zq_buckets_from_probs(
            m["probs"], cal.get("target_lower"), cal.get("target_upper")
        )

    return {
        "as_of": latest_date.strftime("%Y-%m-%d"),
        # 期货数据实际日期（最早值）：任一合约陈旧即在此暴露，与快照日 as_of 区分
        "zq_as_of": min((m["zq_as_of"] for m in meetings if m["zq_as_of"]), default=None),
        "polymarket_as_of": pm["as_of"] if pm else None,
        "meetings": meetings,
    }


@app.get("/api/polymarket/history")
def get_polymarket_history(
    meeting: str | None = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    """Polymarket 决策事件日频概率历史（收敛路径图数据源）。

    无参返回全量 {as_of, meetings: {YYYY-MM: [{date, cut, hold, hike}]}}（静态导出用）；
    meeting=YYYY-MM 只返回该场序列（无则空列表）。
    """
    out = polymarket_fomc_history()
    if meeting:
        return out["meetings"].get(str(meeting)[:7], [])
    return out


# ── 首屏「今日一句话」结论层（issue #21：9 个专题引擎各出一行，前端只渲染不算）─────
# 文案一律由 Python 规则引擎组装（AGENTS 第 8 节「分析层 vs 展示层」）；这里只做取首句、
# 去重复日期前缀与截断。export_pages 复用本函数产静态 JSON（同 daily-brief）。


def _sentence(text: object, limit: int = 120) -> str | None:
    """引擎叙事首行首句 → 首屏「今日一句话」（issue #21）。

    文案一律由 Python 规则引擎组装（AGENTS 第 8 节），前端只渲染不计算；
    引擎用 \n 分「结论 / 依据 / 触发」（全站研判契约），这里先取首行再取首句；
    首句超 limit 字则截到 limit 并补省略号。

    limit 从 72 抬到 120：首屏改成一专题一张卡后正文会换行，不再靠字数保行高；
    留着硬上限只会把「…，能源上涨对股…」截在从句中间。
    """
    s = " ".join(str(text or "").split("\n")[0].split())
    # 引擎叙事常以「截至YYYY-MM-DD，」开头，行尾已有观测日 → 去掉重复前缀
    s = re.sub(r"^截至\d{4}-\d{2}-\d{2}[，,]?\s*", "", s)
    if not s:
        return None
    first = next((p.strip() for p in s.split("。") if p.strip()), "")
    if not first:
        return None
    # 首句尾部若带括号日期（「核心 PCE 3.01%（2026-08-01）」）剔掉：
    # 行里已有观测日，重复只会挤掉真正有信息量的后半句（注意要在取首句后做）
    first = re.sub(r"\s*（\d{4}-\d{2}-\d{2}）$", "", first)
    return first if len(first) <= limit else first[:limit].rstrip() + "…"


def _iso(d: object) -> str | None:
    """紧凑日期（fed 文档表用 YYYYMMDD）→ ISO，其余原样。"""
    s = str(d or "")
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s or None


def _fed_home(d: dict) -> tuple[str, str | None]:
    """美联储引擎无现成叙事句，由指示器拼一句（评分口径单源 stance_label）。"""
    ind = d["indicator"]
    dates = [s["date"] for s in d.get("statements", [])] + [
        s["date"] for s in d.get("speeches", [])
    ]
    text = f"联储口径「{ind['label']}」——近 {ind['sample']} 条声明与演讲评分 {ind['score']:+.1f}"
    return text, _iso(max(dates, default=None))


@app.get("/api/home-lines")
def get_home_lines() -> dict:
    """首屏结论层：9 个专题引擎各出一句话，前端按 SITE_NAV 键定位专题页（issue #21）。

    单源失败只丢该行（打印 skip），不阻断整体导出；全空时前端给空状态。
    """
    from src.assets_analysis import (
        overview as assets_overview,  # 顶层未 import，按 server.py 惯例惰性取
    )
    from src.liquidity_analysis import liquidity_snapshot
    from src.polymarket_analysis import geo_overview

    # (SITE_NAV 组键, 标签, 引擎入口, 从引擎输出取 (一句话原文, 观测日))
    specs = [
        (
            "rates",
            "利率",
            generate_analysis,
            lambda d: (
                d["overview"]["sections"][0]["body"],
                d["overview"].get("as_of"),
            ),
        ),
        (
            "credit",
            "信用",
            generate_credit_overview,
            lambda d: (d["signals"]["what_changed"], d.get("as_of")),
        ),
        (
            "inflation",
            "通胀",
            generate_inflation_overview,
            lambda d: (d["signals"][0]["text"], d.get("as_of")),
        ),
        (
            "labor",
            "就业",
            generate_labor_overview,
            lambda d: (d["signals"][0]["text"], d.get("as_of")),
        ),
        (
            "treasury",
            "美债",
            generate_treasury_overview,
            lambda d: (d["signals"][0]["text"], d.get("as_of")),
        ),
        (
            "vol",
            "波动率",
            generate_volatility_analysis,
            lambda d: (d["signals"][0]["text"], d.get("as_of")),
        ),
        (
            "liquidity",
            "流动性",
            liquidity_snapshot,
            lambda d: (d["evaluation"]["net_liquidity"]["text"], d.get("data_date")),
        ),
        (
            "assets",
            "大类资产",
            assets_overview,
            lambda d: (
                d["analysis"]["risk_appetite"]["text"].replace("。\n", " —— "),
                d.get("as_of"),
            ),
        ),
        (
            "fed",
            "美联储",
            generate_fed_analysis,
            _fed_home,
        ),
        (
            "geo",
            "地缘与政治风险",
            geo_overview,
            lambda d: (
                d["signals"][1] if len(d.get("signals") or []) > 1 else d["signals"][0],
                d.get("as_of"),
            ),
        ),
    ]

    lines: list[dict] = []
    for key, label, fn, extract in specs:
        try:
            text, as_of = extract(fn())
        except Exception as e:  # 单专题数据缺失不影响其余行
            print(f"  home-lines skip {key}: {type(e).__name__}: {e}")
            continue
        line = _sentence(text)
        if line:
            lines.append({"key": key, "label": label, "text": line, "as_of": as_of})
    return {"generator": "rules", "lines": lines}


# ── consensus（多源共识层，ADR-0004）────────────────────────────────


@app.get("/api/consensus")
def get_consensus() -> dict:
    """两级共识状态卡（市场风险轴 + 宏观条件轴）+ 定稿标注。

    finalized = 当日 history 行 source=final（daily-full 收盘定稿后为真；
    盘中/首次计算为假，前端标「盘中临时值」）。历史缺失不阻断计算。
    """
    from src.consensus import ROOT, compute

    out = compute()
    finalized = False
    finalized_state: tuple[str, str] | None = None
    hist_path = ROOT / "data/consensus/history.csv"
    if out.get("ref_date") and hist_path.exists():
        try:
            hist = pd.read_csv(hist_path)
            rows = hist[hist["date"] == out["ref_date"]]
            if not rows.empty and str(rows.iloc[0]["source"]) == "final":
                # 缺列/空值不能算「已定稿」：str(NaN) 会变成字面量 'nan' 当状态词下发，
                # 缺列则 KeyError 被上面 except 吞掉后 finalized 仍为 True —— 徽章照样
                # 给现算值背书，正是这一段要治的病。
                ms, xs = rows.iloc[0].get("market_state"), rows.iloc[0].get("macro_state")
                if pd.notna(ms) and str(ms).strip() and pd.notna(xs) and str(xs).strip():
                    finalized = True
                    finalized_state = (str(ms), str(xs))
        except Exception as e:  # 历史缺失/损坏不阻断状态卡，但不静默（§8 反静默精神）
            print(f"consensus finalized 标注失败: {e}")

    # finalized 徽章不能给「这一次算出来的数字」背书。
    # compute() 是导出时点现算的，而 history 里的 final 行是收盘时落盘的 —— 两者读的是
    # 不同时刻的底层表。intraday-refresh 尾班（21:52）晚于 daily-full 定稿（21:15），
    # 且 crypto-hourly 每小时都会 dispatch 部署，于是「已定稿」徽章每天都在给一个
    # 从未被记录的现算状态背书（实测只改 VIX 一行就能让 state 从 risk-on 翻 risk-off，
    # 徽章仍是已定稿）。
    #
    # 做法：只摘徽章、不改状态。覆盖 state 会漏掉 cls / n_on / n_off / members 等
    # 派生字段（它们各自从 members 现算），结果是「绿色的卡写着分歧、旁边 5 个 ✓ 0 个 ✗」
    # 的自相矛盾。history 只存了 state 字符串，无法据此重建成员明细，所以回退成
    # 「盘中临时值」是唯一自洽的口径：数据确实在定稿后被刷新了，状态未重新定稿。
    state_source = "computed"
    if finalized and finalized_state is not None:
        cur = (str(out["market"].get("state")), str(out["macro"].get("state")))
        if cur != finalized_state:
            print(
                f"consensus: 现算状态 {cur} 与当日 final 留痕 {finalized_state} 不一致"
                f"（定稿后底层数据又被刷新）→ 徽章退回盘中临时值，不背书现算结果"
            )
            finalized = False
            state_source = "diverged_from_final"

    out["state_source"] = state_source
    out["finalized"] = finalized
    return out


# ── daily brief（复刻 timsun.net 首页「今日宏观决策台」）────────────────────


@app.get("/api/daily-brief")
def get_daily_brief() -> dict:
    """今日研判：跨资产变化表 + 三种可能解释 + 结构报警 + FOMC 倒计时。"""
    from src.daily_brief import generate_daily_brief

    out = generate_daily_brief()
    try:
        out["fomc"] = get_fomc_calendar()
    except Exception:  # 日历缺失不阻断研判
        pass
    return out


# ── fed hub（复刻 timsun.net/fed）────────────────────────────────────────────


@app.get("/api/geo")
def get_geo_overview() -> dict:
    """地缘与政治风险专题：Polymarket geo+policy 事件主题聚类 + 概率时序。"""
    from src.polymarket_analysis import geo_overview

    out = geo_overview()
    if out is None:
        raise HTTPException(404, "无地缘事件快照，先运行 ./bin/fetch_polymarket")
    return out


@app.get("/api/fed/overview")
def get_fed_overview() -> dict:
    """美联储鹰鸽面板：指示器 + 声明/演讲列表 + 官员立场 + 时间线 + 市场预期。"""
    out = generate_fed_analysis()
    if "error" in out:
        raise HTTPException(404, out["error"])
    # Polymarket 缺失不阻断页面（None 时前端显示空状态）
    out["market_odds"] = market_odds()
    return out


# ── rates hub（复刻 timsun.net/rates）───────────────────────────────────────


def _macro_df(category: str) -> pd.DataFrame:
    try:
        return read_macro_category(category)
    except FileNotFoundError:
        raise HTTPException(404, f"{category}.csv 缺失，先运行 ./bin/fetch_fred") from None


def _to_points(s: pd.Series, days: int | None = None) -> list[dict]:
    """Series → [{date, value}]；days 限最近 N 条。"""
    s = s.dropna()
    if days is not None:
        s = s.tail(days)
    return [{"date": d.strftime("%Y-%m-%d"), "value": float(v)} for d, v in s.items()]


def _series(df: pd.DataFrame, col: str, days: int) -> list[dict]:
    """列序列最近 days 天 → [{date, value}]。"""
    if col not in df.columns:
        return []
    return _to_points(df[col], days)


def _offering_b(v) -> float | None:
    """发行额（USD）→ 十亿美元；空值返回 None。"""
    if not v:
        return None
    return round(float(v) / 1e9, 1)


@app.get("/api/volatility/analysis")
def get_volatility_analysis() -> dict:
    """波动率研判：VIX 卡片 + 信号三段文 + 期限结构 + 图表序列
    （规则引擎，LLM 预留）。"""
    out = generate_volatility_analysis()
    if "error" in out:
        raise HTTPException(404, out["error"])
    return out


@app.get("/api/volatility/dashboard")
def get_volatility_dashboard() -> dict:
    """波动率全景仪表盘：30 指数 + 风险矩阵 + Trade Map + 统计 + 7 段叙事
    （timsun.net/volatility/dashboard 复刻，规则引擎，LLM 预留）。"""
    from src.volatility_dashboard import generate_dashboard

    out = generate_dashboard()
    if "error" in out:
        raise HTTPException(404, out["error"])
    return out


# ── credit hub（复刻 timsun.net/credit）──────────────────────────────────


@app.get("/api/credit/overview")
def get_credit_overview() -> dict:
    """信用周期雷达总览：OAS 分层 / all-in 融资成本 / SLOOS / 贷款质量 / 金融条件。"""
    out = generate_credit_overview()
    if "error" in out:
        raise HTTPException(404, out["error"])
    return out


@app.get("/api/credit/cds")
def get_credit_cds() -> dict:
    """CDS 专题：主权 CDS 代理（10Y）+ 银行系统风险代理（KBWB vs SPX）。"""
    out = generate_credit_cds()
    if "error" in out:
        raise HTTPException(404, out["error"])
    return out


@app.get("/api/credit/stress")
def get_credit_stress() -> dict:
    """信用压力仪表盘：5 分量合成指数 + 跨资产对照 + 历史曲线。"""
    out = generate_credit_stress()
    if "error" in out:
        raise HTTPException(404, out["error"])
    return out


@app.get("/api/credit/euro")
def get_credit_euro() -> dict:
    """欧元区 10Y 主权利差（法/意/西 vs 德国 Bund），CNBC 日频 3 年。"""
    out = generate_euro_sovereign()
    if "error" in out:
        raise HTTPException(404, out["error"])
    return out


# ── inflation hub ───────────────────────────────────────────────────────────


@app.get("/api/inflation/overview")
def get_inflation_overview() -> dict:
    """通胀专题总览：YoY 卡片 + 三段研判 + 历史曲线 + 分项/预期/Shapiro 供需。"""
    out = generate_inflation_overview()
    if "error" in out:
        raise HTTPException(404, out["error"])
    return out


@app.get("/api/treasury/overview")
def get_treasury_overview() -> dict:
    """美债需求专题总览：TIC 海外持仓/净买入 + 官方占比 + Bill 占比 + 再融资。"""
    out = generate_treasury_overview()
    if "error" in out:
        raise HTTPException(404, out["error"])
    return out


@app.get("/api/labor/overview")
def get_labor_overview() -> dict:
    """就业专题总览：失业率/非农/初请/JOLTS 卡片 + Sahm + V/U + 三段研判。"""
    out = generate_labor_overview()
    if "error" in out:
        raise HTTPException(404, out["error"])
    return out


@app.get("/api/rates/analysis")
def get_rates_analysis() -> dict:
    """利率研判（规则引擎，LLM 预留）。"""
    return generate_analysis()


@app.get("/api/rates/fed-funds")
def get_rates_fed_funds() -> dict:
    """联邦基金利率：EFFR/SOFR/目标区间 + 走廊 + 成交量 + 历史。"""
    df = _macro_df("rates")
    # EFFR 用 DFF（日频，1999-03 起）；FEDFUNDS 是月频均值，
    # 当日频绘制会错位（审计 P1-②）
    effr_col = "DFF" if "DFF" in df.columns else "FEDFUNDS"
    latest = {}
    for col, key in [(effr_col, "effr"), ("SOFR", "sofr"), ("SOFRVOL", "sofr_vol")]:
        s = df[col].dropna() if col in df.columns else pd.Series(dtype=float)
        if not s.empty:
            latest[key] = {
                "value": float(s.iloc[-1]),
                "as_of": s.index[-1].strftime("%Y-%m-%d"),
            }
    tarl = df["DFEDTARL"].dropna() if "DFEDTARL" in df else pd.Series(dtype=float)
    taru = df["DFEDTARU"].dropna() if "DFEDTARU" in df else pd.Series(dtype=float)
    if not tarl.empty and not taru.empty:
        latest["target"] = [float(tarl.iloc[-1]), float(taru.iloc[-1])]
    # SOFR 分位价差只服务两张卡片，服务端算好 2 个数（原下发 4×90 点序列给前端算）
    for lo, hi, key in [
        ("SOFR25", "SOFR75", "sofr_p2575_bp"),
        ("SOFR1", "SOFR99", "sofr_p199_bp"),
    ]:
        lo_s = df[lo].dropna() if lo in df.columns else pd.Series(dtype=float)
        hi_s = df[hi].dropna() if hi in df.columns else pd.Series(dtype=float)
        if not lo_s.empty and not hi_s.empty:
            latest[key] = round(float(hi_s.iloc[-1] - lo_s.iloc[-1]) * 100, 1)

    # 时效取已实现的市场利率（EFFR/SOFR/ONRRP）最新日期，不被 IORB/DFEDTAR 预填的未来行带偏
    mkt_dates = [
        df[c].dropna().index[-1]
        for c in (effr_col, "SOFR", "ONRRP")
        if c in df.columns and not df[c].dropna().empty
    ]
    as_of_dt = max(mkt_dates) if mkt_dates else df.index.max()
    return {
        "as_of": as_of_dt.strftime("%Y-%m-%d"),
        "latest": latest,
        # 利率走廊（近 90 天）：EFFR/SOFR/TGCR/BGCR/ONRRP 五利率 + 目标区间
        "corridor": {
            "effr": _series(df, effr_col, 90),
            "sofr": _series(df, "SOFR", 90),
            "tgcr": _series(df, "TGCR", 90),
            "bgcr": _series(df, "BGCR", 90),
            "onrrp": _series(df, "ONRRP", 90),
            "target_lo": _series(df, "DFEDTARL", 90),
            "target_hi": _series(df, "DFEDTARU", 90),
        },
        "sofr_vol": _series(df, "SOFRVOL", 90),
        "effr_history": _series(df, effr_col, 5 * 250),
        "recent": [
            {"date": d.strftime("%Y-%m-%d"), "effr": float(v)}
            for d, v in df[effr_col].dropna().tail(30)[::-1].items()  # 最新在前
        ],
    }


@app.get("/api/rates/yield-curve")
def get_rates_yield_curve() -> dict:
    """收益率曲线：四线对比 + 变化 + 利差时序 + 解读（规则引擎）。"""
    df = _macro_df("rates")
    out = generate_analysis()
    out.pop("overview", None)  # 该页只渲染 yield_curve（overview 四段文不下发）
    # 近 6 个月三大利差时序（bp）
    spread_hist = {}
    for name, a, b in [
        ("2s10s", "DGS10", "DGS2"),
        ("3m10s", "DGS10", "DGS3MO"),
        ("5s30s", "DGS30", "DGS5"),
    ]:
        diff = (df[a] - df[b]).dropna().tail(6 * 22)
        spread_hist[name] = [{**p, "value": round(p["value"] * 100, 1)} for p in _to_points(diff)]
    out["yield_curve"]["spreads_history"] = spread_hist
    return out


@app.get("/api/rates/real-rates")
def get_rates_real_rates(days: int = Query(365, le=2000)) -> dict:
    """实际利率：10Y 名义/TIPS/盈亏平衡 + ACM 期限溢价 + 2Y + 2s10s。"""
    df = _macro_df("rates")
    tips = _macro_df("tips")

    d10 = df["DGS10"].dropna()
    d10_real = tips["DFII10"].dropna()
    aligned = pd.concat([d10, d10_real], axis=1).dropna().tail(days)
    breakeven = aligned["DGS10"] - aligned["DFII10"]
    spread = (df["DGS10"] - df["DGS2"]).dropna().tail(days)

    tp = df["ACMTP10"].dropna().tail(days)
    tp_chg_1m = None
    if len(tp) >= 2:
        month_ago = tp.index[-1] - pd.Timedelta(days=30)
        tp_prev = tp[tp.index <= month_ago]
        if not tp_prev.empty:
            tp_chg_1m = round(float(tp.iloc[-1] - tp_prev.iloc[-1]) * 100, 1)

    return {
        "as_of": df.index.max().strftime("%Y-%m-%d"),
        "nominal_10y": _series(df, "DGS10", days),
        "real_10y": _series(tips, "DFII10", days),
        "breakeven_10y": _to_points(breakeven),
        "term_premium": _to_points(tp),
        "term_premium_chg_1m": tp_chg_1m,
        "y2": _series(df, "DGS2", days),
        "spread_2s10": _to_points(spread),
    }


def _trend_bucket(term: str) -> str | None:
    """拍卖标的期限 → 趋势分组（含重开拍卖，审计 P1-⑦）。

    Treasury 重开标的的 security_term 是剩余期限：10Y 重开 1/2 个月后叫
    '9-Year 11-Month' / '9-Year 10-Month'，30Y 重开叫 '29-Year *'。
    归入原发行期限组，避免趋势图只画原始发行、365 天窗口内每线只剩几个点。

    注意：TIPS 共用同一套 security_term，归组前必须先用
    _is_indexed() 排除 —— 否则 TIPS 的实际利率（比名义低约一个盈亏平衡通胀率）
    会混进名义券趋势（曾出现 10Y 中标 2.438% 与同期名义 4.71% 并存）。
    """
    if not isinstance(term, str):
        return None
    if term == "2-Year" or term.startswith("1-Year"):
        return "2-Year"
    if term == "5-Year" or term.startswith("4-Year"):
        return "5-Year"
    if term == "10-Year" or term.startswith("9-Year"):
        return "10-Year"
    if term == "30-Year" or term.startswith("29-Year"):
        return "30-Year"
    return None


def _is_indexed(r) -> bool:
    """该行是否 TIPS（通胀指数券）。旧 CSV 无此列时按 False 处理（不误删名义券）。"""
    return str(r.get("inflation_index_security", "")).strip().lower() == "yes"


def _is_frn(r) -> bool:
    """该行是否 FRN（浮动票息，无固定中标利率）。同上，缺列按 False。"""
    return str(r.get("floating_rate", "")).strip().lower() == "yes"


def _last_traded_date(auc: pd.DataFrame, today: pd.Timestamp) -> str:
    """已成交场次的最新拍卖日（不越过今天）。

    auction_results.csv 含「已公告未拍卖」的未来场次（结果字段全 NaN），
    直接取 index.max() 会把 as_of 写成未来日期。
    """
    done = auc.loc[auc["bid_to_cover_ratio"].notna() & (auc.index <= today)].index
    return done.max().strftime("%Y-%m-%d") if len(done) else ""


def _auction_points(sub: pd.DataFrame, col: str) -> list[dict]:
    """拍卖子集某指标列 → [{date, value, reopening, size}]。"""
    pts = []
    for d, r in sub.iterrows():
        if pd.notna(r.get(col)):
            pts.append(
                {
                    "date": d.strftime("%Y-%m-%d"),
                    "value": float(pd.to_numeric(r[col])),
                    "reopening": str(r.get("reopening", "")).strip().lower() == "yes",
                    "size": _offering_b(r.get("offering_amt"))
                    if pd.notna(r.get("offering_amt"))
                    else None,
                }
            )
    return pts


def _borrowing_estimate() -> dict | None:
    """最新 QRA 再融资借款估算：当季/下季净借款（十亿美元）+ 较上次变化。

    解析 financing_estimates 正文固定句式（"expects to borrow $X billion"）；
    变化量锚定头条句式 "[Tt]he borrowing estimate is $X billion higher/lower
    than announced"，排除从句（"the current quarter borrowing estimate is"）
    与上季实际值（"actual borrowing was ..."）；锚定不到则返回 None（缺字段
    显示 "—"），绝不静默取错。
    # ponytail: 与 QRA 正文措辞强耦合，句式微调会静默缺字段；升级路径 =
    # 改结构化字段（如 QRA 附带的机器可读 JSON）或人工校验。
    """
    path = ROOT / "data" / "treasury" / "refunding.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path)
    est = df[df["kind"] == "financing_estimates"].sort_values("date")
    if est.empty:
        return None
    row = est.iloc[-1]
    body = str(row["body"]).replace("\xa0", " ")
    vals = [
        int(float(v.replace(",", ""))) for v in re.findall(r"borrow\s+\$([\d,]+) billion", body)
    ]
    chg = re.findall(
        r"[Tt]he borrowing estimate is \$([\d,]+) billion "
        r"(higher|lower) than announced",
        body,
    )
    return {
        "date": row["date"],
        "quarter": row["quarter"],
        "current_b": vals[0] if vals else None,
        "next_b": vals[1] if len(vals) > 1 else None,
        "chg_b": int(float(chg[0][0])) if chg else None,
        "chg_dir": chg[0][1] if chg else None,
    }


def _bill_share_series() -> tuple[list[dict], float | None]:
    """Bill 占未偿可交易债务比例：MSPD 月频历史 + 日频近期（派生），去重叠。"""
    mspd_path = ROOT / "data" / "treasury" / "mspd.csv"
    daily_path = ROOT / "data" / "treasury" / "bill_share_daily.csv"
    parts: list[pd.Series] = []
    if mspd_path.exists():
        m = pd.read_csv(mspd_path, index_col="record_date", parse_dates=True)
        parts.append(m["BILL_SHARE"].dropna())
    if daily_path.exists():
        d = pd.read_csv(daily_path, index_col="date", parse_dates=True)
        parts.append(d["BILL_SHARE"].dropna())
    if not parts:
        return [], None
    s = pd.concat(parts)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    # 按日期窗口切，不按行数：序列主体是 MSPD 月频（308 行），`tail(6*366)` 吃不掉
    # 任何行 —— 页面标「近 6 年」而图从 2001 年画起。
    if not s.empty:
        s = s.loc[s.index >= s.index.max() - pd.Timedelta(days=6 * 366)]
    latest = round(float(s.iloc[-1]), 2) if not s.empty else None
    return _to_points(s), latest


@app.get("/api/rates/auctions")
def get_rates_auctions() -> dict:
    """国债拍卖：需求概览 + 近 90 天结果 + 未来 21 天日历 + 2/5/10/30Y 趋势。"""
    results_path = ROOT / "data" / "treasury" / "auction_results.csv"
    upcoming_path = ROOT / "data" / "treasury" / "upcoming_auctions.csv"
    if not results_path.exists():
        raise HTTPException(404, "拍卖数据缺失，先运行 ./bin/fetch_treasury")

    auc = pd.read_csv(results_path, index_col="auction_date", parse_dates=True)
    today = pd.Timestamp(date.today())

    def _row(r) -> dict:
        cover = r.get("bid_to_cover_ratio")
        return {
            "security_type": r.get("security_type"),
            "security_term": r.get("security_term"),
            "offering_b": _offering_b(r.get("offering_amt")),
            "bid_to_cover": round(float(cover), 2) if pd.notna(cover) else None,
            "indirect_pct": r.get("indirect_pct"),
            "high_rate": r.get("high_rate"),
            "tail_bp": r.get("tail_bp"),
            "reopening": r.get("reopening"),
            # 两者都是「不是丢数据，是口径不同」：TIPS 为实际利率，FRN 无固定中标利率
            "is_tips": _is_indexed(r),
            "is_frn": _is_frn(r),
        }

    # 近 90 天结果：最新在前；源文件含已公告未拍卖的场次（结果字段 NaN），
    # 只保留已有 bid_to_cover 的成交行，避免与未来日历重复
    recent = auc.loc[
        (auc.index >= today - pd.Timedelta(days=90)) & auc["bid_to_cover_ratio"].notna()
    ].sort_index(ascending=False)
    # 名义付息券（排除 Bill 与 TIPS）—— 需求概览均值与四条趋势线同一口径
    coupon = auc[(auc["security_type"] != "Bill") & ~auc.apply(_is_indexed, axis=1)]
    covers = pd.to_numeric(coupon["bid_to_cover_ratio"], errors="coerce").dropna().tail(10)
    avg_cover = round(float(covers.mean()), 2) if not covers.empty else None

    trend, tail_trend, indirect_trend = {}, {}, {}
    for term in ["2-Year", "5-Year", "10-Year", "30-Year"]:
        sub = coupon[coupon["security_term"].map(_trend_bucket) == term]
        key = term.replace("-Year", "Y")
        sub365 = sub.loc[sub.index >= today - pd.Timedelta(days=365)]
        trend[key] = _auction_points(sub365, "bid_to_cover_ratio")
        tail_trend[key] = _auction_points(sub365, "tail_bp")
        indirect_trend[key] = _auction_points(sub365, "indirect_pct")

    upcoming = []
    if upcoming_path.exists():
        up = pd.read_csv(upcoming_path, index_col="auction_date", parse_dates=True)
        # 按日期窗口过滤，源文件含历史记录不能按行数截断。
        # 窗口给 45 天（展示口径 21 天）：静态站构建期固化，前端按打开时间重切 21 天
        window_end = today + pd.Timedelta(days=45)
        up = up.loc[(up.index >= today) & (up.index <= window_end)].sort_index()
        for d, r in up.iterrows():
            upcoming.append(
                {
                    "auction_date": d.strftime("%Y-%m-%d"),
                    "security_type": r.get("security_type"),
                    "security_term": r.get("security_term"),
                    "offering_b": _offering_b(r.get("offering_amt")),
                    "issue_date": r.get("issue_date"),
                    "reopening": r.get("reopening"),
                }
            )

    bill_share, bill_share_latest = _bill_share_series()

    return {
        # 只算已成交场次的最新日：源文件含已公告未拍卖的未来场次（bid_to_cover 为空），
        # 直接取 index.max() 会返回未来日期（实测 10-04 读到 10-08）
        "as_of": _last_traded_date(auc, today),
        "avg_cover_10_coupon": avg_cover,
        "upcoming_count": len(upcoming),
        "recent": [
            {**{"auction_date": d.strftime("%Y-%m-%d")}, **_row(r)} for d, r in recent.iterrows()
        ],
        "upcoming": upcoming,
        "trend": trend,
        "tail_trend": tail_trend,
        "indirect_trend": indirect_trend,
        "bill_share": bill_share,
        "bill_share_latest": bill_share_latest,
        "borrowing": _borrowing_estimate(),
    }


# ── assets hub（复刻 timsun.net/assets）──────────────────────────────


@app.get("/api/assets/overview")
def get_assets_overview() -> dict:
    """大类资产主页：6 大分组价格表 + 30 日相关性热力 + 报警 + 4 段交叉分析。"""
    from src.assets_analysis import overview

    return overview()


@app.get("/api/assets/equities")
def get_assets_equities() -> dict:
    """美股：指数卡片 + 市场广度（SPX/RUT 20D + ABV 占比）+ 动能集中度分析。"""
    from src.assets_analysis import equities

    return equities()


@app.get("/api/assets/equities-options")
def get_assets_equities_options() -> dict:
    """期权市场结构：22 标的看板 + SPY GEX/DEX/Vanna/Charm 详图（快照 JSON）。"""
    from src.assets_analysis import options_board

    return options_board()


@app.get("/api/assets/positioning")
def get_assets_positioning() -> dict:
    """持仓追踪：CFTC COT 拥挤度 + 分组合约。"""
    from src.assets_analysis import positioning

    return positioning()


@app.get("/api/assets/bonds")
def get_assets_bonds() -> dict:
    """债券：4 只 ETF 卡片 + 近 20 日收盘表。"""
    from src.assets_analysis import bonds

    return bonds()


@app.get("/api/assets/commodities")
def get_assets_commodities() -> dict:
    """商品：卡片 + 近 20 日表 + 归一化走势。

    贵金属部分迁 /api/assets/metals，ENSO 专题迁 /api/assets/agriculture。
    """
    from src.assets_analysis import commodities

    return commodities()


@app.get("/api/assets/agriculture")
def get_assets_agriculture() -> dict:
    """农产品和气候：软商品与谷物期货 + 农业 ETF + ENSO 气候周期专题 + 近 20 日表。"""
    from src.assets_analysis import agriculture

    return agriculture()


@app.get("/api/assets/metals")
def get_assets_metals() -> dict:
    """贵金属：价格上下文 + ETF 持仓与 WGC 全球资金流。"""
    from src.assets_analysis import metals

    return metals()


@app.get("/api/assets/crypto")
def get_assets_crypto() -> dict:
    """加密货币：BTC/ETH 卡片 + 净流动性溢出（β / 背离监控）+ 20 日表。"""
    from src.assets_analysis import crypto

    return crypto()


@app.get("/api/assets/crypto-derivatives")
def get_assets_crypto_derivatives() -> dict:
    """加密衍生品：OKX 永续 + Deribit 期权墙 + CME 基差 + BTC 前瞻雷达。"""
    from src.assets_analysis import crypto_derivatives

    return crypto_derivatives()


@app.get("/api/assets/etfs")
def get_assets_etfs() -> dict:
    """ETF：精选池温度筛选 + 全量清单分类统计。"""
    from src.assets_analysis import etfs

    return etfs()


@app.get("/api/assets/fx")
def get_assets_fx() -> dict:
    """外汇：美元广度评分 + 各分组 20D USD 压力仪表盘。"""
    from src.assets_analysis import fx

    return fx()


@app.get("/api/assets/prices")
def get_assets_prices() -> dict:
    """资产价格全量宽表（关联面板 drill-down 用：任意两标的滚动相关 + 归一化）。"""
    from src.config import (
        ROOT,  # 函数内 import：与 macro.py 同模式，测试 monkeypatch 生效
    )

    path = ROOT / "data" / "yfinance" / "asset_prices.csv"
    if not path.exists():
        raise HTTPException(404, "asset_prices.csv 不存在（先运行 ./bin/fetch_yfinance）")
    df = pd.read_csv(path, index_col="date", parse_dates=True)
    df = df[[c for c in df.columns if not c.endswith("_volume")]]
    df = df.round(4).reset_index()
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    # replace 而非 where：float 列上 where(cond, None) 会把 None 当 NaN → 静默 no-op
    return {"prices": df.replace({np.nan: None}).to_dict(orient="records")}


@app.get("/api/cross-asset")
def get_cross_asset() -> dict:
    """跨资产相关性面板：30 日相关矩阵 + 结构报警对日序列（关联分析视图）。"""
    from src.config import (
        ROOT,  # 函数内 import：与 macro.py 同模式，测试 monkeypatch 生效
    )
    from src.cross_asset import ALERTS, ASSETS, GROUP_LABELS, WINDOW

    matrix_path = ROOT / "data" / "cross_asset" / "correlation.csv"
    if not matrix_path.exists():
        raise HTTPException(
            404, "correlation.csv 不存在（先运行 uv run python -m src.cross_asset）"
        )
    raw = pd.read_csv(matrix_path, index_col="asset")
    as_of = str(raw.loc["date"].iloc[0])
    assets = [a for a in raw.columns if a in ASSETS]
    sub = raw.drop(index="date")[assets].loc[assets]  # 双轴对齐到已知标的

    alerts_path = ROOT / "data" / "cross_asset" / "alerts.csv"
    alerts = []
    if alerts_path.exists():
        adf = pd.read_csv(alerts_path, index_col="date")
        for _i, _j, key, label in ALERTS:
            if key not in adf.columns:
                continue
            s = adf[key].dropna()
            if s.empty:
                continue
            latest = float(s.iloc[-1])
            prev = float(s.iloc[-2]) if len(s) >= 2 else latest
            alerts.append(
                {
                    "key": key,
                    "label": label,
                    "latest": latest,
                    "prev": prev,
                    "series": [{"date": d, "value": round(float(v), 4)} for d, v in s.items()],
                }
            )

    return {
        "as_of": as_of,
        "window": WINDOW,
        "assets": [
            {"name": a, "group": ASSETS[a]["group"], "label": ASSETS[a]["label"]} for a in assets
        ],
        "groups": GROUP_LABELS,
        "matrix": [[None if pd.isna(v) else round(float(v), 4) for v in row] for row in sub.values],
        "alerts": alerts,
    }


# ── 伴读抽屉后端通道（/api/chat · /api/models · /api/notes）─────────────────
# 线上是 Cloudflare Pages 静态站，同一组路由另有一份 Pages Functions 实现
# （仓库根 functions/api/*.js，由 `wrangler pages deploy` 自动拾取）。两边契约一致：
#   POST /api/chat   {model?, messages:[{role,content}], stream?, reasoning_effort?}
#                    → 透传上游 SSE（text/event-stream），Key 只留在服务端
#   GET  /api/models → {data:[{id,display_name}], proxy:true}，供抽屉「探测模型」
#   POST /api/notes  {filename?, markdown, thread?} → 写 data/notes/（本地）
# 没配 LLM_API_KEY 时 /api/chat 返回 503，前端提示「填自己的 Key 直连」。
# 细节与 Cloudflare 环境变量见 docs/ai-chat-backend.md。

_CHAT_HITS: dict[str, list[float]] = {}
# 限额与 functions/_lib/common.js 的 RATE_LIMITS、docs/ai-chat-backend.md 的表同步
_LIMITS = {
    "chat": (10, 60),
    "models": (30, 60),
    "notes": (20, 3600),
}
_API_BODY_LIMITS = {"/api/chat": CHAT_BODY_MAX, "/api/notes": NOTES_BODY_MAX}
_LOCAL_WRITE_PATHS = {"/api/chat", "/api/notes"}


def _json_error(status: int, message: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message}, headers=headers)


def _rate_ok(key: str, limit: int, window_seconds: float) -> bool:
    """进程内滑动窗口限流（单机够用；线上同名逻辑在 Pages Function 里）。"""
    now = time.monotonic()
    hits = [t for t in _CHAT_HITS.get(key, []) if now - t < window_seconds]
    if len(hits) >= limit:
        _CHAT_HITS[key] = hits
        return False
    hits.append(now)
    _CHAT_HITS[key] = hits
    if len(_CHAT_HITS) > 2000:  # 别让异常 IP 把内存撑大
        for k in list(_CHAT_HITS)[:1000]:
            _CHAT_HITS.pop(k, None)
    return True


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _is_local_origin(origin: str) -> bool:
    """只允许本机页面调用会写文件/花 Key 的接口（CORS 的 * 不覆盖这层）。"""
    if not origin:
        return True
    try:
        host = urlparse(origin).hostname
    except ValueError:
        return False
    return host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


@app.middleware("http")
async def _guard_api(request: Request, call_next):
    """拦截跨站调用 + 超限 body：新端点能写 data/notes、花 LLM_API_KEY。"""
    path = request.url.path
    if path not in _LOCAL_WRITE_PATHS:
        return await call_next(request)
    origin = request.headers.get("origin", "")
    if origin and not _is_local_origin(origin):
        return _json_error(403, "只允许本机页面调用该接口")
    if request.method == "POST":
        if "chunked" in request.headers.get("transfer-encoding", "").lower():
            return _json_error(411, "请带 Content-Length 发送请求体")
        raw = request.headers.get("content-length")
        if raw:
            try:
                size = int(raw)
            except ValueError:
                return _json_error(400, "非法 Content-Length")
            limit = _API_BODY_LIMITS[path]
            if size > limit:
                return _json_error(413, f"请求体超过 {limit // 1024} KB")
    return await call_next(request)


class ChatMessageIn(BaseModel):
    """一条对话消息。

    `content` 可空：assistant 只带工具调用时上游就发 null，校验层归一成 ""。
    `tool_calls` 用宽松类型承接（`list[Any]`）——结构校验在 chat_proxy 里做，
    与 Pages Functions 逐条对齐，且保证原样透传（子模型会裁掉未知字段）。
    """

    role: str
    content: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[Any] | None = None


class ChatBodyIn(BaseModel):
    model: str | None = None
    messages: list[ChatMessageIn]
    reasoning_effort: str | None = None
    tools: list[Any] | None = None
    # 具名函数对象也是「非法值」的一种形状，这里不收紧类型，交给校验层统一 422
    tool_choice: Any = None


class NoteBodyIn(BaseModel):
    filename: str | None = None
    markdown: str
    thread: dict[str, Any] | None = None


@app.get("/api/models")
def get_chat_models(request: Request) -> Any:
    """本站代理可用的模型白名单（抽屉设置里的「探测模型」走这里）。"""
    if not llm_key():
        return _json_error(503, MISSING_KEY)
    if not _rate_ok("models:" + _client_ip(request), *_LIMITS["models"]):
        return _json_error(
            429,
            f"请求过于频繁（模型列表每分钟 {_LIMITS['models'][0]} 次），请稍后再试。",
            {"Retry-After": "60"},
        )
    return {"data": [{"id": m, "display_name": m} for m in chat_models()], "proxy": True}


@app.post("/api/chat")
def chat_proxy(body: ChatBodyIn, request: Request) -> Any:
    """OpenAI 兼容代理：浏览器 → 本站 → 上游，SSE 原样透传，Key 不下发。"""
    key = llm_key()
    if not key:
        return _json_error(503, MISSING_KEY)
    if not _rate_ok("chat:" + _client_ip(request), *_LIMITS["chat"]):
        return _json_error(
            429,
            f"请求过于频繁（本站代理每分钟 {_LIMITS['chat'][0]} 次），请稍后再试。",
            {"Retry-After": "60"},
        )

    try:
        # 顺序与 functions/api/chat.js 一致：先 messages，再 tools / tool_choice
        messages = validate_messages([m.model_dump(exclude_none=True) for m in body.messages])
        tools = validate_tools(body.tools)
        tool_choice = validate_tool_choice(body.tool_choice)
    except ChatValidationError as exc:
        return _json_error(exc.status, str(exc))
    payload = build_payload(body.model, messages, body.reasoning_effort, tools, tool_choice)

    try:
        upstream = requests.post(
            llm_base() + "/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            json=payload,
            stream=True,
            timeout=(10, 300),
        )
    except requests.RequestException as exc:
        return _json_error(502, f"上游连接失败：{exc}")

    if upstream.status_code != 200:
        detail = upstream.text[:2000]
        upstream.close()
        return _json_error(upstream.status_code, f"上游 {upstream.status_code}：{detail}")

    ctype = upstream.headers.get("content-type") or "text/event-stream; charset=utf-8"

    def _iter_upstream() -> Iterator[bytes]:
        try:
            for chunk in upstream.iter_content(chunk_size=None):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return StreamingResponse(
        _iter_upstream(),
        headers={
            "Content-Type": ctype,
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-LLM-Model": str(payload["model"]),
        },
    )


@app.post("/api/notes")
def save_note(body: NoteBodyIn, request: Request) -> Any:
    """把一条对话落盘到 data/notes/（本地服务专有；静态站由其 Pages Function 返回 503）。"""
    if not _rate_ok("notes:" + _client_ip(request), *_LIMITS["notes"]):
        return _json_error(
            429,
            f"保存过于频繁（每小时 {_LIMITS['notes'][0]} 次），请稍后再试。",
            {"Retry-After": "600"},
        )
    markdown = body.markdown or ""
    if not markdown.strip():
        return _json_error(400, "markdown 为空")
    raw = markdown.encode("utf-8")
    if len(raw) > NOTES_MAX_BYTES:
        return _json_error(413, "笔记超过 1 MB")

    title = ""
    if isinstance(body.thread, dict):
        title = str(body.thread.get("title") or "")
    try:
        path = save_note_file(markdown, filename=body.filename, title=title)
    except OSError as exc:
        return _json_error(500, f"保存失败：{exc}")
    try:
        rel = str(path.relative_to(ROOT))
    except ValueError:
        rel = str(path)
    return {"ok": True, "path": rel, "bytes": len(raw)}


# ── static files (must be last) ─────────────────────────────────────────────

_static = ROOT / "frontend" / "public"
_static.mkdir(exist_ok=True)
_dist = ROOT / "frontend" / "dist"

# 专题页（/rates/ /credit/ …）是 astro 产物，只存在于 frontend/dist，public 里没有。
# 不挂它们的话，仪表盘「今日一句话」与左侧专题树的链接在本地全部 404
# （线上部署的是 dist，所以只有本地会重现）。
# 逐个挂页面目录，而不是把静态根整体指向 dist：后者会让改了 public/css|js
# 必须重新 npm run build 才可见，丢掉开发热更（dist 里那几份只是 public 的拷贝）。
_shared = {"api", "css", "js", "vendor"}
_page_dirs = [p for p in sorted(_dist.iterdir()) if p.is_dir()] if _dist.is_dir() else []
for _p in _page_dirs:
    if _p.name in _shared:
        continue
    app.mount(
        f"/{_p.name}",
        StaticFiles(directory=str(_p), html=True),
        name=f"dist-{_p.name}",
    )
if not _page_dirs:
    print(
        "  提示：frontend/dist 不存在 → 本地专题页（/rates/ 等）会 404；"
        "跑 cd frontend && npm run build"
    )


# ADR-0005：站点根重定向到今日头版（与线上 _redirects 同口径）。SPA 已挪 public/app/，
# 根路径不再有 index.html —— StaticFiles(html=True) 会 404，这里显式接住。
@app.get("/", include_in_schema=False)
def root_redirect() -> RedirectResponse:
    return RedirectResponse("/daily/", status_code=307)


app.mount("/", StaticFiles(directory=str(_static), html=True), name="static")


@app.middleware("http")
async def _no_cache_html(request: Request, call_next):
    """HTML 一律 no-cache：Starlette 静态文件不带 Cache-Control，浏览器会按
    Last-Modified 龄期启发式缓存 HTML 数天，改了专题页/SPA 却看不到更新。"""
    resp = await call_next(request)
    if request.url.path.endswith((".html", "/")):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
