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
from src.config import FRED_SERIES, IBKR_SYMBOLS, ROOT, TERM_SERIES
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


@app.get("/api/macro/categories")
def get_macro_categories():
    out = []
    for name, series_map in FRED_SERIES.items():
        out.append({"name": name, "series": list(series_map.keys())})
    return out


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
