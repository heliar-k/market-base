# pip install fastapi uvicorn
"""FastAPI backend for K-line analysis web prototype."""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.analyze import analyze
from src.cache import load_or_compute
from src.config import (
    FOMC_MEETINGS,
    FRED_SERIES,
    IBKR_SYMBOLS,
    ROOT,
    TERM_INFO,
    TERM_SERIES,
)
from src.credit_analysis import (
    generate_credit_cds,
    generate_credit_overview,
    generate_credit_stress,
)
from src.fed_analysis import generate_fed_analysis
from src.macro import (
    DERIVED_INPUTS,
    categories_for,
    load_macro_categories,
    load_macro_category,
    read_macro_category,
    rrp_in_millions,
)
from src.rates_analysis import generate_analysis
from src.volatility_analysis import generate_volatility_analysis

app = FastAPI(title="K-line Analysis Web")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── helpers ──────────────────────────────────────────────────────────────────

_SYMBOL_TYPE = {s["name"]: s["type"] for s in IBKR_SYMBOLS}


def _csv_path(symbol: str) -> Path:
    sub = "indices" if _SYMBOL_TYPE.get(symbol) == "index" else "stocks"
    return ROOT / "data" / sub / f"{symbol}.csv"


def _sanitize(obj: Any) -> Any:
    """Recursively convert numpy types to Python native; NaN → None."""
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
    return _sanitize(records)


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
    return _sanitize({"symbol": symbol, "data": out.to_dict(orient="records")})


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
    return _sanitize(result)


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


@app.get("/api/macro/categories")
def get_macro_categories():
    out = []
    for name, series_map in FRED_SERIES.items():
        out.append({"name": name, "series": list(series_map.keys())})
    return out


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

    # 从 rates CSV 中查找 current meeting 对应的目标利率
    target_lower = None
    target_upper = None
    if current_meeting:
        try:
            df = read_macro_category("rates")
        except FileNotFoundError:
            df = None
        if df is not None:
            meeting_date = pd.Timestamp(
                year=current_meeting.year,
                month=current_meeting.month,
                day=current_meeting.end_day,
            )
            # 找 meeting 结束后最近的非空目标利率
            if meeting_date in df.index:
                row = df.loc[meeting_date]
                if pd.notna(row.get("DFEDTARL")):
                    target_lower = float(row["DFEDTARL"])
                if pd.notna(row.get("DFEDTARU")):
                    target_upper = float(row["DFEDTARU"])
            if target_lower is None or target_upper is None:
                # fallback: 向后查找最近的非空值
                after = df.loc[meeting_date:].dropna(subset=["DFEDTARL", "DFEDTARU"])
                if not after.empty:
                    r = after.iloc[0]
                    target_lower = target_lower or float(r["DFEDTARL"])
                    target_upper = target_upper or float(r["DFEDTARU"])

    return _sanitize(
        {
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
        }
    )


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
        cat = (
            "derived"
            if name in DERIVED_INPUTS
            else (categories_for(name) or ["derived"])[0]
        )
        result[name] = {
            "category": cat,
            "label": _MACRO_LABELS.get(name, name),
            "data": _sanitize(
                merged[["date", name]]
                .rename(columns={name: "value"})
                .to_dict(orient="records")
            ),
        }

    date_range = {"from": merged["date"].iloc[0], "to": merged["date"].iloc[-1]}
    return _sanitize({"indicators": result, "date_range": date_range})


@app.get("/api/macro/{category}")
def get_macro(category: str):
    try:
        df = load_macro_category(category)
    except FileNotFoundError:
        raise HTTPException(404, f"No macro data for {category}")
    df = rrp_in_millions(df)
    df = df.reset_index()
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    return _sanitize(df.to_dict(orient="records"))


@app.get("/api/macro/{category}/term")
def get_macro_term(category: str):
    if category not in TERM_SERIES:
        raise HTTPException(404, f"No term structure for {category}")
    try:
        df = read_macro_category(category)
    except FileNotFoundError:
        raise HTTPException(404, f"No macro data for {category}")
    terms = TERM_SERIES[category]
    available = [t for t in terms if t in df.columns]
    if not available:
        raise HTTPException(404, "No term columns found")
    last = df.iloc[-1]

    # 期限标签统一走 config.TERM_INFO（与 TUI 共用）
    labels = [TERM_INFO[t].short for t in available]
    values = [float(last[t]) if pd.notna(last[t]) else None for t in available]
    return _sanitize(
        {
            "date": df.index[-1].strftime("%Y-%m-%d"),
            "labels": labels,
            "values": values,
        }
    )


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

    cutoff = None
    if range != "all":
        months = {
            "1m": 1,
            "3m": 3,
            "6m": 6,
            "1y": 12,
            "2y": 24,
            "3y": 36,
            "5y": 60,
            "10y": 120,
            "30y": 360,
        }.get(range, 0)
        if months:
            cutoff = pd.Timestamp.now() - pd.DateOffset(months=months)

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
            series[key] = _sanitize(
                filtered[["date", key]]
                .rename(columns={key: "value"})
                .to_dict(orient="records")
            )

    # Pre-compute stacked cumulative for area chart (WRESBAL → +WTREGEN → +RRPONTSYD)
    # 周频列（WRESBAL/WTREGEN）在非发布日必须 ffill 而非 fillna(0)：
    # fillna(0) 会让累计在非周三日期跌到 ~0，产生万亿级假跌落（审计 P1-⑪）。
    # 仅序列开头无前值的 NaN 补 0（此前观测为 0，语义正确）。
    stack_keys = ["WRESBAL", "WTREGEN", "RRPONTSYD"]
    cum = pd.DataFrame(index=filtered.index)
    running = pd.Series(0.0, index=filtered.index)
    for sk in stack_keys:
        col = (
            filtered[sk]
            if sk in filtered.columns
            else pd.Series(0.0, index=filtered.index)
        )
        running = running.add(col.ffill().fillna(0))
        cum[sk] = running
    stacked = {}
    for sk in stack_keys:
        stacked[sk] = _sanitize(
            filtered[["date"]].assign(value=cum[sk]).to_dict(orient="records")
        )

    return _sanitize({"summary": summary, "series": series, "stacked": stacked})


@app.get("/api/liquidity/compare-spx")
def get_liquidity_compare_spx(range: str = Query("5y")):
    try:
        df = load_macro_category("liquidity")
    except FileNotFoundError:
        raise HTTPException(404, "No liquidity data")

    cutoff = None
    if range != "all":
        months = {
            "1m": 1,
            "3m": 3,
            "6m": 6,
            "1y": 12,
            "2y": 24,
            "3y": 36,
            "5y": 60,
            "10y": 120,
            "30y": 360,
        }.get(range, 60)
        if months:
            cutoff = pd.Timestamp.now() - pd.DateOffset(months=months)

    filtered = df.loc[cutoff:] if cutoff is not None else df
    filtered = filtered.reset_index()
    filtered["date"] = filtered["date"].dt.strftime("%Y-%m-%d")

    result: dict[str, Any] = {}
    if "NET_LIQUIDITY" in filtered.columns:
        result["NET_LIQUIDITY"] = _sanitize(
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
        result["SPX"] = _sanitize(
            spx[["date", close_col]]
            .rename(columns={close_col: "value"})
            .to_dict(orient="records")
        )

    return result


# ── rate expectations ────────────────────────────────────────────────────────


@app.get("/api/rate-expectations")
def get_rate_expectations() -> dict:
    csv_path = ROOT / "data" / "rate_expectations" / "fomc_probabilities.csv"
    if not csv_path.exists():
        raise HTTPException(
            404, "No rate expectations data yet. Run ./bin/fetch_rate_expectations"
        )
    df = pd.read_csv(csv_path, index_col="date", parse_dates=True)

    # range cols → normalized rows
    range_cols = [c for c in df.columns if c.startswith("range_")]

    latest_date = df.index.max()
    latest = df.loc[latest_date]
    # 同日多次写入会产生重复行（upsert 幂等性缺陷），按会议去重
    latest = latest.drop_duplicates(subset=["meeting_date"])

    meetings = []
    for _, row in latest.iterrows():
        meeting_date = pd.to_datetime(row["meeting_date"]).strftime("%Y-%m-%d")
        probs = []
        for c in range_cols:
            lo, hi = c.replace("range_", "").split("-")
            p = row.get(c, 0.0)
            if pd.notna(p) and float(p) > 0:
                probs.append(
                    {"lo": float(lo), "hi": float(hi), "prob": round(float(p), 4)}
                )

        meetings.append(
            {
                "meeting_date": meeting_date,
                "contract": row["contract"],
                "implied_rate": float(row["implied_rate"]),
                "post_meeting_rate": float(row["post_meeting_rate"]),
                "prob_cut": float(row["prob_cut"]),
                "prob_hold": float(row["prob_hold"]),
                "prob_hike": float(row["prob_hike"]),
                "expectation": row["expectation"],
                "probs": probs,
            }
        )

    return _sanitize(
        {
            "as_of": latest_date.strftime("%Y-%m-%d"),
            "meetings": meetings,
        }
    )


# ── fed hub（复刻 timsun.net/fed）────────────────────────────────────────────


@app.get("/api/fed/overview")
def get_fed_overview() -> dict:
    """美联储鹰鸽面板：指示器 + 声明/演讲列表 + 官员立场 + 时间线。"""
    out = generate_fed_analysis()
    if "error" in out:
        raise HTTPException(404, out["error"])
    return _sanitize(out)


# ── rates hub（复刻 timsun.net/rates）───────────────────────────────────────


def _macro_df(category: str) -> pd.DataFrame:
    try:
        return read_macro_category(category)
    except FileNotFoundError:
        raise HTTPException(
            404, f"{category}.csv 缺失，先运行 ./bin/fetch_fred"
        ) from None


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
    return _sanitize(out)


# ── credit hub（复刻 timsun.net/credit）──────────────────────────────────


@app.get("/api/credit/overview")
def get_credit_overview() -> dict:
    """信用周期雷达总览：OAS 分层 / all-in 融资成本 / SLOOS / 贷款质量 / 金融条件。"""
    out = generate_credit_overview()
    if "error" in out:
        raise HTTPException(404, out["error"])
    return _sanitize(out)


@app.get("/api/credit/cds")
def get_credit_cds() -> dict:
    """CDS 专题：主权 CDS 代理（10Y）+ 银行系统风险代理（KBWB vs SPX）。"""
    out = generate_credit_cds()
    if "error" in out:
        raise HTTPException(404, out["error"])
    return _sanitize(out)


@app.get("/api/credit/stress")
def get_credit_stress() -> dict:
    """信用压力仪表盘：5 分量合成指数 + 跨资产对照 + 历史曲线。"""
    out = generate_credit_stress()
    if "error" in out:
        raise HTTPException(404, out["error"])
    return _sanitize(out)


@app.get("/api/rates/analysis")
def get_rates_analysis() -> dict:
    """利率研判（规则引擎，LLM 预留）。"""
    return _sanitize(generate_analysis())


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

    return _sanitize(
        {
            "as_of": df.index.max().strftime("%Y-%m-%d"),
            "latest": latest,
            # 利率走廊（近 90 天）：EFFR/SOFR/TGCR/BGCR/ONRRP 五利率 + 目标区间
            "corridor": {
                "dates": [d.strftime("%Y-%m-%d") for d in df.tail(90).index],
                "effr": _series(df, effr_col, 90),
                "sofr": _series(df, "SOFR", 90),
                "tgcr": _series(df, "TGCR", 90),
                "bgcr": _series(df, "BGCR", 90),
                "onrrp": _series(df, "ONRRP", 90),
                "sofr_p1": _series(df, "SOFR1", 90),
                "sofr_p25": _series(df, "SOFR25", 90),
                "sofr_p75": _series(df, "SOFR75", 90),
                "sofr_p99": _series(df, "SOFR99", 90),
                "target_lo": _series(df, "DFEDTARL", 90),
                "target_hi": _series(df, "DFEDTARU", 90),
            },
            "sofr_vol": _series(df, "SOFRVOL", 90),
            "effr_history": _series(df, effr_col, 5 * 250),
            "recent": [
                {"date": d.strftime("%Y-%m-%d"), "effr": float(v)}
                for d, v in df[effr_col].dropna().tail(30).items()
            ],
        }
    )


@app.get("/api/rates/yield-curve")
def get_rates_yield_curve() -> dict:
    """收益率曲线：四线对比 + 变化 + 利差时序 + 解读（规则引擎）。"""
    df = _macro_df("rates")
    out = generate_analysis()
    # 近 6 个月三大利差时序（bp）
    spread_hist = {}
    for name, a, b in [
        ("2s10s", "DGS10", "DGS2"),
        ("3m10s", "DGS10", "DGS3MO"),
        ("5s30s", "DGS30", "DGS5"),
    ]:
        diff = (df[a] - df[b]).dropna().tail(6 * 22)
        spread_hist[name] = [
            {**p, "value": round(p["value"] * 100, 1)} for p in _to_points(diff)
        ]
    out["yield_curve"]["spreads_history"] = spread_hist
    return _sanitize(out)


@app.get("/api/rates/real-rates")
def get_rates_real_rates(days: int = Query(365, le=2000)) -> dict:
    """实际利率：10Y 名义/TIPS/盈亏平衡 + 2Y + 2s10s（近 1 年日频）。"""
    df = _macro_df("rates")
    tips = _macro_df("tips")

    d10 = df["DGS10"].dropna()
    d10_real = tips["DFII10"].dropna()
    aligned = pd.concat([d10, d10_real], axis=1).dropna().tail(days)
    breakeven = aligned["DGS10"] - aligned["DFII10"]
    spread = (df["DGS10"] - df["DGS2"]).dropna().tail(days)

    return _sanitize(
        {
            "as_of": df.index.max().strftime("%Y-%m-%d"),
            "nominal_10y": _series(df, "DGS10", days),
            "real_10y": _series(tips, "DFII10", days),
            "breakeven_10y": _to_points(breakeven),
            "y2": _series(df, "DGS2", days),
            "spread_2s10": _to_points(spread),
        }
    )


def _trend_bucket(term: str) -> str | None:
    """拍卖标的期限 → 趋势分组（含重开拍卖，审计 P1-⑦）。

    Treasury 重开标的的 security_term 是剩余期限：10Y 重开 1/2 个月后叫
    '9-Year 11-Month' / '9-Year 10-Month'，30Y 重开叫 '29-Year *'。
    归入原发行期限组，避免趋势图只画原始发行、365 天窗口内每线只剩几个点。
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
        }

    recent = auc.loc[auc.index >= today - pd.Timedelta(days=90)].sort_index()
    coupon = auc[auc["security_type"] != "Bill"]
    covers = (
        pd.to_numeric(coupon["bid_to_cover_ratio"], errors="coerce").dropna().tail(10)
    )
    avg_cover = round(float(covers.mean()), 2) if not covers.empty else None

    trend = {}
    for term in ["2-Year", "5-Year", "10-Year", "30-Year"]:
        sub = auc[auc["security_term"].map(_trend_bucket) == term]
        sub = sub.loc[sub.index >= today - pd.Timedelta(days=365)]
        trend[term.replace("-Year", "Y")] = [
            {
                "date": d.strftime("%Y-%m-%d"),
                "value": float(pd.to_numeric(r["bid_to_cover_ratio"])),
            }
            for d, r in sub.iterrows()
            if pd.notna(r.get("bid_to_cover_ratio"))
        ]

    upcoming = []
    if upcoming_path.exists():
        up = pd.read_csv(upcoming_path, index_col="auction_date", parse_dates=True)
        # 按日期窗口过滤（未来 21 天），源文件含历史记录不能按行数截断
        window_end = today + pd.Timedelta(days=21)
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

    return _sanitize(
        {
            "as_of": auc.index.max().strftime("%Y-%m-%d"),
            "avg_cover_10_coupon": avg_cover,
            "upcoming_count": len(upcoming),
            "recent": [
                {**{"auction_date": d.strftime("%Y-%m-%d")}, **_row(r)}
                for d, r in recent.iterrows()
            ],
            "upcoming": upcoming,
            "trend": trend,
        }
    )


# ── static files (must be last) ─────────────────────────────────────────────

_static = ROOT / "static"
_static.mkdir(exist_ok=True)
app.mount("/", StaticFiles(directory=str(_static), html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
