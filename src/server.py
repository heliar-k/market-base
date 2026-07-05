# pip install fastapi uvicorn
"""FastAPI backend for K-line analysis web prototype."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.analyze import analyze
from src.cache import load_or_compute
from src.config import FRED_SERIES, FRED_SERIES_FLAT, IBKR_SYMBOLS, ROOT, TERM_SERIES
from src.macro import cross_category_partners, derive_macro

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
def get_kline(symbol: str, as_of: str | None = Query(None)):
    csv = _csv_path(symbol)
    if not csv.exists():
        raise HTTPException(404, f"No data for {symbol}")
    df = load_or_compute(symbol, csv)
    if as_of:
        df = df.loc[: pd.Timestamp(as_of)]
    df = df.reset_index()  # date index → column
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    records = df.to_dict(orient="records")
    return _sanitize(records)


@app.get("/api/diag/{symbol}")
def get_diag(symbol: str, as_of: str | None = Query(None)):
    csv = _csv_path(symbol)
    if not csv.exists():
        raise HTTPException(404, f"No data for {symbol}")
    df = load_or_compute(symbol, csv)
    result = analyze(df, symbol, as_of=as_of)
    return _sanitize(result)


# ── macro endpoints ──────────────────────────────────────────────────────────

# 指标 → 分类反查
_METRIC_TO_CAT: dict[str, str] = {}
for _cat, _series in FRED_SERIES.items():
    for _metric in _series:
        _METRIC_TO_CAT[_metric] = _cat

# 派生指标 → 所需原始列的源分类
_DERIVED_CATS = {
    "SPREAD_2S10S": ["rates"],
    "NET_LIQUIDITY": ["liquidity"],
    "BEI_5Y": ["rates", "tips"],
    "BEI_10Y": ["rates", "tips"],
    "SOFR_IORB_SPREAD_BP": ["rates"],
}

# 中文标签（与前端 MACRO_LABELS 保持同步）
_MACRO_LABELS = {
    "VIX": "波动率指数（恐慌指数）", "HY_OAS": "高收益债信用利差", "IG_OAS": "投资级债信用利差",
    "CPI": "消费者物价指数", "PCE": "个人消费支出价格指数", "CORE_CPI": "核心消费者物价指数",
    "T5YIE": "5年期通胀预期", "T10YIE": "10年期通胀预期", "T5YIFR": "5年期远期通胀率",
    "MICH": "密歇根通胀预期", "EXPINF_1Y": "1年期通胀预期", "EXPINF_2Y": "2年期通胀预期",
    "EXPINF_5Y": "5年期通胀预期", "EXPINF_10Y": "10年期通胀预期",
    "UNRATE": "失业率", "PAYEMS": "非农就业人数", "ICSA": "初请失业金人数",
    "GDP": "国内生产总值(GDP)", "INDPRO": "工业生产指数",
    "FEDFUNDS": "联邦基金利率", "SOFR": "担保隔夜融资利率", "IORB": "准备金余额利率",
    "DGS1MO": "1月期国债收益率", "DGS3MO": "3月期国债收益率", "DGS6MO": "6月期国债收益率",
    "DGS1": "1年期国债收益率", "DGS2": "2年期国债收益率", "DGS3": "3年期国债收益率",
    "DGS5": "5年期国债收益率", "DGS7": "7年期国债收益率", "DGS10": "10年期国债收益率",
    "DGS20": "20年期国债收益率", "DGS30": "30年期国债收益率",
    "SPREAD_2S10S": "2s10s利差", "SOFR_IORB_SPREAD_BP": "SOFR-IORB利差(bp)",
    "DFII5": "5年期TIPS收益率", "DFII7": "7年期TIPS收益率", "DFII10": "10年期TIPS收益率",
    "DFII20": "20年期TIPS收益率", "DFII30": "30年期TIPS收益率",
    "BEI_5Y": "5年期盈亏平衡通胀率", "BEI_10Y": "10年期盈亏平衡通胀率",
    "NFCI": "金融状况指数", "RRPONTSYD": "隔夜逆回购规模", "WTREGEN": "财政部一般账户余额",
    "WRESBAL": "准备金余额", "WALCL": "美联储总资产", "NET_LIQUIDITY": "净流动性",
    "UMCSENT": "密歇根消费者信心指数", "STLFSI4": "金融压力指数",
    "DXY": "美元指数",
}


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


@app.get("/api/macro/correlate")
def get_macro_correlate(
    indicators: str = Query(..., description="Comma-separated indicator names"),
):
    names = [i.strip() for i in indicators.split(",") if i.strip()]
    if not names:
        raise HTTPException(400, "No indicators specified")

    # Collect all categories needed (raw + derived input deps)
    cats_needed: set[str] = set()
    for name in names:
        if name in _DERIVED_CATS:
            cats_needed.update(_DERIVED_CATS[name])
        elif name in _METRIC_TO_CAT:
            cats_needed.add(_METRIC_TO_CAT[name])
        else:
            raise HTTPException(404, f"Unknown indicator: {name}")

    # Load & merge CSVs
    dfs: list[pd.DataFrame] = []
    for cat in cats_needed:
        csv_path = ROOT / "data" / "fred" / cat / f"{cat}.csv"
        if not csv_path.exists():
            raise HTTPException(404, f"No data for category {cat}")
        df = pd.read_csv(csv_path, index_col="date", parse_dates=True)
        # Also load cross-category partners (e.g. tips for BEI)
        for partner in cross_category_partners(cat):
            p_csv = ROOT / "data" / "fred" / partner / f"{partner}.csv"
            if p_csv.exists():
                p_df = pd.read_csv(p_csv, index_col="date", parse_dates=True)
                df = df.join(p_df, how="outer")
        dfs.append(df)

    # Outer-join all category DataFrames
    merged = dfs[0]
    for df in dfs[1:]:
        merged = merged.join(df, how="outer")
    merged.sort_index(inplace=True)

    # Compute derived indicators
    merged = derive_macro(merged)

    # Validate all requested indicators exist after derivation
    for name in names:
        if name not in merged.columns:
            raise HTTPException(404, f"Indicator {name} not available in data")

    # Build response
    merged = merged.reset_index()
    merged["date"] = merged["date"].dt.strftime("%Y-%m-%d")

    result: dict[str, Any] = {}
    for name in names:
        cat = _DERIVED_CATS.get(name, [_METRIC_TO_CAT.get(name, "derived")])[0]
        if name in _DERIVED_CATS:
            cat = "derived"
        result[name] = {
            "category": cat,
            "label": _MACRO_LABELS.get(name, name),
            "data": _sanitize(
                merged[["date", name]].rename(columns={name: "value"}).to_dict(orient="records")
            ),
        }

    date_range = {"from": merged["date"].iloc[0], "to": merged["date"].iloc[-1]}
    return _sanitize({"indicators": result, "date_range": date_range})


@app.get("/api/macro/{category}")
def get_macro(category: str):
    csv_path = ROOT / "data" / "fred" / category / f"{category}.csv"
    if not csv_path.exists():
        raise HTTPException(404, f"No macro data for {category}")
    df = pd.read_csv(csv_path, index_col="date", parse_dates=True)
    # join partner categories for cross-category derived series
    for partner in cross_category_partners(category):
        p_csv = ROOT / "data" / "fred" / partner / f"{partner}.csv"
        if p_csv.exists():
            p_df = pd.read_csv(p_csv, index_col="date", parse_dates=True)
            df = df.join(p_df, how="left")
    df = derive_macro(df)
    df = df.reset_index()
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    return _sanitize(df.to_dict(orient="records"))


@app.get("/api/macro/{category}/term")
def get_macro_term(category: str):
    if category not in TERM_SERIES:
        raise HTTPException(404, f"No term structure for {category}")
    csv_path = ROOT / "data" / "fred" / category / f"{category}.csv"
    if not csv_path.exists():
        raise HTTPException(404, f"No macro data for {category}")
    df = pd.read_csv(csv_path, index_col="date", parse_dates=True)
    terms = TERM_SERIES[category]
    available = [t for t in terms if t in df.columns]
    if not available:
        raise HTTPException(404, "No term columns found")
    last = df.iloc[-1]

    # friendly labels: DGS1MO→1mo, DGS1→1y, DFII5→5y, etc.
    def _label(s: str) -> str:
        suffix = s.replace("DGS", "").replace("DFII", "")
        return suffix.lower().replace("mo", "mo").replace("MO", "mo")

    labels = [_label(t) for t in available]
    values = [float(last[t]) if pd.notna(last[t]) else None for t in available]
    return _sanitize(
        {
            "date": df.index[-1].strftime("%Y-%m-%d"),
            "labels": labels,
            "values": values,
        }
    )


# ── static files (must be last) ─────────────────────────────────────────────

_static = ROOT / "static"
_static.mkdir(exist_ok=True)
app.mount("/", StaticFiles(directory=str(_static), html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
