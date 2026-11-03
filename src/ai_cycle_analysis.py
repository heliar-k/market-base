from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.analysis_utils import chg_prev, read_csv_or_empty
from src.config import ROOT
from src.cycle_utils import drawdown, negative_streak, quarterly_yoy, true_streak

CLOUD_COMPANIES = {
    "MSFT": "Microsoft",
    "GOOG": "Alphabet",
    "AMZN": "Amazon",
    "META": "Meta",
}
HARDWARE_COMPANIES = {
    "NVDA": "NVIDIA",
    "AMD": "AMD",
    "AVGO": "Broadcom",
    "MU": "Micron",
    "TSM": "台积电",
}
CASHFLOW_FIELDS = ["Capital Expenditure", "Operating Cash Flow", "Free Cash Flow"]


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None or pd.isna(value) else round(float(value), digits)


def _pct_change(s: pd.Series, periods: int) -> float | None:
    pair = chg_prev(s.dropna(), periods)
    return None if pair is None else _round((pair[0] / pair[1] - 1) * 100)


def reconcile_single_quarter(quarterly: pd.DataFrame, annual: pd.DataFrame) -> dict[str, object]:
    """用已完整披露的财年验证季度现金流是否可按单季相加。"""
    years: list[str] = []
    for dt, annual_row in annual.iterrows():
        start = dt - pd.DateOffset(years=1)
        rows = quarterly.loc[(quarterly.index > start) & (quarterly.index <= dt)]
        if len(rows) != 4:
            continue
        checks: list[bool] = []
        for field in CASHFLOW_FIELDS:
            if field not in quarterly or field not in annual:
                continue
            annual_value = annual_row.get(field)
            values = rows[field]
            if pd.isna(annual_value) or values.isna().any():
                continue
            tolerance = max(1.0, abs(float(annual_value)) * 0.005)
            checks.append(abs(float(values.sum()) - float(annual_value)) <= tolerance)
        if checks and all(checks):
            years.append(dt.strftime("%Y-%m-%d"))
    return {"single_quarter_verified": bool(years), "reconciled_years": years}


def _financial_csv(root: Path, symbol: str, name: str) -> pd.DataFrame:
    return read_csv_or_empty(root / "data" / "financials" / symbol / f"{name}.csv")


def cloud_financials(root: Path = ROOT) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for symbol, company in CLOUD_COMPANIES.items():
        quarterly = _financial_csv(root, symbol, "cashflow_quarterly")
        annual = _financial_csv(root, symbol, "cashflow_annual")
        if quarterly.empty or "Capital Expenditure" not in quarterly:
            continue
        reconciliation = reconcile_single_quarter(quarterly, annual)
        capex = -quarterly["Capital Expenditure"].dropna()
        if capex.empty:
            continue
        fcf = quarterly.get("Free Cash Flow", pd.Series(index=quarterly.index, dtype=float)).copy()
        if {"Operating Cash Flow", "Capital Expenditure"} <= set(quarterly.columns):
            missing = fcf.isna()
            fcf.loc[missing] = (
                quarterly.loc[missing, "Operating Cash Flow"]
                + quarterly.loc[missing, "Capital Expenditure"]
            )
        fcf = fcf.dropna()
        latest_date = capex.index[-1]
        latest = quarterly.loc[latest_date]
        cfo = latest.get("Operating Cash Flow")
        fcf_latest = fcf.get(latest_date)
        rows.append(
            {
                "symbol": symbol,
                "company": company,
                "as_of": latest_date.strftime("%Y-%m-%d"),
                "capex_b": _round(capex.iloc[-1] / 1e9, 3),
                "capex_qoq_pct": _pct_change(capex, 1),
                "capex_yoy_pct": _round(quarterly_yoy(capex).iloc[-1]),
                "capex_down_quarters": true_streak(capex.diff() < 0),
                "cfo_b": _round(cfo / 1e9, 3) if pd.notna(cfo) else None,
                "fcf_b": _round(fcf_latest / 1e9, 3) if pd.notna(fcf_latest) else None,
                "fcf_negative_quarters": (
                    negative_streak(fcf)
                    if reconciliation["single_quarter_verified"] and not fcf.empty
                    else None
                ),
                **reconciliation,
            }
        )
    if rows:
        latest = max(pd.Timestamp(r["as_of"]) for r in rows)
        for row in rows:
            row["stale"] = (latest - pd.Timestamp(row["as_of"])).days > 120
    triggered = [r["symbol"] for r in rows if (r["fcf_negative_quarters"] or 0) >= 4]
    return {
        "rows": rows,
        "max_fcf_negative_quarters": max(
            (r["fcf_negative_quarters"] or 0 for r in rows), default=0
        ),
        "fcf_triggered": triggered,
        "stale_symbols": [r["symbol"] for r in rows if r["stale"]],
    }


def capex_guidance(root: Path = ROOT) -> dict[str, object]:
    path = root / "data" / "ai_cycle" / "capex_guidance.csv"
    if not path.exists():
        return {"rows": [], "present": False, "triggered": []}
    frame = read_csv_or_empty(path, index_col=None)
    if frame.empty:
        return {"rows": [], "present": False, "triggered": []}
    rows: list[dict[str, object]] = []
    for symbol, group in frame.groupby("symbol"):
        latest = group.sort_values("announced_on").iloc[-1]
        annual = _financial_csv(root, symbol, "cashflow_annual")
        annual_capex = (
            -annual["Capital Expenditure"].dropna() if not annual.empty else pd.Series(dtype=float)
        )
        point = latest.get("point_estimate_usd")
        minimum, maximum = latest.get("minimum_usd"), latest.get("maximum_usd")
        if pd.isna(point) and pd.notna(minimum) and pd.notna(maximum):
            point = (float(minimum) + float(maximum)) / 2
        actual = float(annual_capex.iloc[-1]) if not annual_capex.empty else None
        growth = None
        absolute_cut = None
        guidance_period = str(latest["guidance_period"])
        if point is not None and pd.notna(point) and actual and guidance_period.startswith("FY"):
            guidance_year = int(guidance_period[2:])
            if guidance_year == annual_capex.index[-1].year + 1:
                growth = (float(point) / actual - 1) * 100
                absolute_cut = float(point) < actual
        rows.append(
            {
                "symbol": symbol,
                "company": CLOUD_COMPANIES.get(symbol, symbol),
                "announced_on": latest["announced_on"],
                "guidance_period": guidance_period,
                "minimum_b": _round(minimum / 1e9, 3) if pd.notna(minimum) else None,
                "maximum_b": _round(maximum / 1e9, 3) if pd.notna(maximum) else None,
                "point_b": _round(point / 1e9, 3)
                if point is not None and pd.notna(point)
                else None,
                "actual_capex_b": _round(actual / 1e9, 3) if actual is not None else None,
                "guidance_growth_pct": _round(growth),
                "absolute_cut": absolute_cut,
                "source_url": latest["source_url"],
                "source_excerpt": latest["source_excerpt"],
            }
        )
    triggered = [
        r["symbol"]
        for r in rows
        if (r["guidance_growth_pct"] is not None and r["guidance_growth_pct"] < 20)
        or r["absolute_cut"] is True
    ]
    return {
        "rows": rows,
        "present": any(r["guidance_growth_pct"] is not None for r in rows),
        "triggered": triggered,
    }


def cloud_disclosures(root: Path = ROOT) -> dict[str, object]:
    path = root / "data" / "ai_cycle" / "cloud_disclosures.csv"
    if not path.exists():
        return {"rows": [], "triggered": []}
    frame = read_csv_or_empty(path, index_col=None)
    if frame.empty:
        return {"rows": [], "triggered": []}
    frame["period_end"] = pd.to_datetime(frame["period_end"])
    frame["filing_date"] = pd.to_datetime(frame["filing_date"])
    quarters = frame[frame["period_type"] == "quarter"].copy()
    rows: list[dict[str, object]] = []
    for symbol, group in quarters.groupby("symbol"):
        group = group.sort_values(["period_end", "filing_date"]).drop_duplicates(
            "period_end", keep="last"
        )
        cashflow = _financial_csv(root, symbol, "cashflow_quarterly")
        capex = (
            -cashflow["Capital Expenditure"].dropna()
            if not cashflow.empty
            else pd.Series(dtype=float)
        )
        capex_yoy = quarterly_yoy(capex)
        comparisons: dict[pd.Timestamp, float] = {}
        for _, disclosure in group.iterrows():
            revenue_growth = disclosure["revenue_growth_pct"]
            capex_growth = capex_yoy.get(disclosure["period_end"])
            comparisons[disclosure["period_end"]] = (
                float(revenue_growth) - float(capex_growth)
                if pd.notna(revenue_growth) and pd.notna(capex_growth)
                else float("nan")
            )
        comparison_series = pd.Series(comparisons, dtype=float).sort_index()
        streak = true_streak(comparison_series < 0) if comparison_series.notna().any() else None
        latest = group.iloc[-1]
        period_end = latest["period_end"]
        revenue = latest["revenue_usd"]
        capex_growth_latest = capex_yoy.get(period_end)
        capex_latest = capex.get(period_end)
        revenue_growth_latest = latest["revenue_growth_pct"]
        gap = (
            float(revenue_growth_latest) - float(capex_growth_latest)
            if pd.notna(revenue_growth_latest) and pd.notna(capex_growth_latest)
            else None
        )
        rows.append(
            {
                "symbol": symbol,
                "company": CLOUD_COMPANIES.get(symbol, symbol),
                "segment": latest["cloud_unit"] or "未单独披露",
                "availability": latest["availability"],
                "as_of": period_end.strftime("%Y-%m-%d"),
                "filing_date": latest["filing_date"].strftime("%Y-%m-%d"),
                "revenue_b": _round(revenue / 1e9, 3) if pd.notna(revenue) else None,
                "revenue_yoy_pct": _round(revenue_growth_latest),
                "capex_yoy_pct": _round(capex_growth_latest),
                "capex_b": _round(capex_latest / 1e9, 3) if pd.notna(capex_latest) else None,
                "capex_revenue_ratio_pct": (
                    _round(float(capex_latest) / float(revenue) * 100)
                    if pd.notna(capex_latest) and pd.notna(revenue) and float(revenue) != 0
                    else None
                ),
                "growth_gap_pp": _round(gap),
                "revenue_below_capex_quarters": streak,
                "growth_source": latest["growth_source"],
                "missing_reason": latest["missing_reason"],
                "filing_url": latest["filing_url"],
            }
        )
    return {
        "rows": rows,
        "triggered": [r["symbol"] for r in rows if (r["revenue_below_capex_quarters"] or 0) >= 2],
    }


def hardware_fundamentals(root: Path = ROOT) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for symbol, company in HARDWARE_COMPANIES.items():
        balance = _financial_csv(root, symbol, "balance_quarterly")
        income = _financial_csv(root, symbol, "income_quarterly")
        if (
            balance.empty
            or income.empty
            or "Inventory" not in balance
            or "Total Revenue" not in income
        ):
            continue
        frame = balance[["Inventory"]].join(income[["Total Revenue"]], how="inner").dropna()
        if frame.empty:
            continue
        frame["inventory_yoy"] = quarterly_yoy(frame["Inventory"])
        frame["revenue_yoy"] = quarterly_yoy(frame["Total Revenue"])
        frame["gap"] = frame["inventory_yoy"] - frame["revenue_yoy"]
        growth = frame.dropna(subset=["inventory_yoy", "revenue_yoy"])
        latest = frame.iloc[-1]
        growth_streak = true_streak(frame["gap"] > 0) if not growth.empty else None
        rows.append(
            {
                "symbol": symbol,
                "company": company,
                "as_of": frame.index[-1].strftime("%Y-%m-%d"),
                "inventory_b": _round(latest["Inventory"] / 1e9, 3),
                "revenue_b": _round(latest["Total Revenue"] / 1e9, 3),
                "inventory_yoy_pct": _round(latest["inventory_yoy"]),
                "revenue_yoy_pct": _round(latest["revenue_yoy"]),
                "gap_pp": _round(latest["gap"]),
                "inventory_above_revenue_quarters": growth_streak,
            }
        )
    return {
        "rows": rows,
        "triggered": [
            r["symbol"] for r in rows if (r["inventory_above_revenue_quarters"] or 0) >= 2
        ],
    }


def ndx_prices(root: Path = ROOT) -> pd.DataFrame:
    history = read_csv_or_empty(root / "data" / "indices" / "NDX.csv")
    if not history.empty and "close" in history:
        return history[["close"]].rename(columns={"close": "NDX"})
    return read_csv_or_empty(root / "data" / "yfinance" / "asset_prices.csv")


def ndx_drawdown(prices: pd.DataFrame) -> dict[str, object]:
    if prices.empty or "NDX" not in prices:
        return {}
    result = drawdown(prices["NDX"], history_start="1990-01-01")
    monthly_drawdown = result.get("monthly_drawdown_pct")
    return {
        **result,
        "monthly_confirmed": monthly_drawdown >= 40 if monthly_drawdown is not None else None,
        "threshold_pct": 40,
    }


def _signal(
    key: str,
    name: str,
    present: bool,
    triggered: bool | None,
    value: float | int | None,
    threshold: str,
    as_of: str | None,
    detail: str,
) -> dict[str, object]:
    return {
        "key": key,
        "name": name,
        "present": present,
        "triggered": triggered if present else None,
        "value": value,
        "threshold": threshold,
        "as_of": as_of,
        "detail": detail,
    }


def _layer(key: str, name: str, signals: list[dict[str, object]], min_present: int) -> dict:
    present = [s for s in signals if s["present"]]
    triggered = [s for s in present if s["triggered"]]
    enough = len(present) >= min_present
    confirmed = enough and len(triggered) / len(present) >= 2 / 3 if present else False
    return {
        "key": key,
        "name": name,
        "status": "triggered" if confirmed else ("clear" if enough else "data_insufficient"),
        "present": len(present),
        "triggered": len(triggered),
        "total": len(signals),
        "signals": signals,
    }


def generate_ai_cycle_overview(root: Path = ROOT) -> dict[str, object]:
    cloud = cloud_financials(root)
    disclosure = cloud_disclosures(root)
    guidance = capex_guidance(root)
    hardware = hardware_fundamentals(root)
    prices = ndx_prices(root)
    ndx = ndx_drawdown(prices)
    cloud_rows = cloud["rows"]
    hardware_rows = hardware["rows"]
    capex_triggered = [r["symbol"] for r in cloud_rows if r["capex_down_quarters"] >= 2]
    fcf_present = any(r["single_quarter_verified"] for r in cloud_rows)
    inventory_present = any(
        r["inventory_above_revenue_quarters"] is not None for r in hardware_rows
    )
    leading = _layer(
        "leading",
        "领先层",
        [
            _signal(
                "capex_slowdown",
                "实际资本开支连续两季下降",
                bool(cloud_rows),
                bool(capex_triggered),
                len(capex_triggered),
                "任一公司连续 2 季",
                max((r["as_of"] for r in cloud_rows), default=None),
                "、".join(capex_triggered) or "暂无公司触发",
            ),
            _signal(
                "inventory_revenue",
                "存货增速连续两季高于营收",
                inventory_present,
                bool(hardware["triggered"]),
                len(hardware["triggered"]),
                "任一公司连续 2 季",
                max((r["as_of"] for r in hardware_rows), default=None),
                "、".join(hardware["triggered"]) or "暂无公司触发",
            ),
            _signal(
                "capex_guidance",
                "次年资本开支指引减速",
                guidance["present"],
                bool(guidance["triggered"]),
                len(guidance["triggered"]),
                "增速 <20% 或绝对值下调",
                max((r["announced_on"] for r in guidance["rows"]), default=None),
                "、".join(guidance["triggered"]) or "暂无公司触发",
            ),
        ],
        min_present=2,
    )
    synchronous = _layer(
        "synchronous",
        "同步层",
        [
            _signal(
                "fcf_negative",
                "自由现金流连续为负",
                fcf_present,
                bool(cloud["fcf_triggered"]),
                cloud["max_fcf_negative_quarters"],
                "任一公司连续 4 季",
                max((r["as_of"] for r in cloud_rows), default=None),
                "、".join(cloud["fcf_triggered"]) or "暂无公司触发",
            ),
            _signal(
                "cloud_growth_gap",
                "云收入增速连续低于 Capex 增速",
                any(r["revenue_below_capex_quarters"] is not None for r in disclosure["rows"]),
                bool(disclosure["triggered"]),
                len(disclosure["triggered"]),
                "任一公司连续 2 季",
                max((r["as_of"] for r in disclosure["rows"]), default=None),
                "、".join(disclosure["triggered"]) or "暂无公司触发",
            ),
        ],
        min_present=2,
    )
    confirmation = _layer(
        "confirmation",
        "确认层",
        [
            _signal(
                "ndx_drawdown",
                "NDX 月线回撤",
                bool(ndx),
                ndx.get("monthly_confirmed"),
                ndx.get("monthly_drawdown_pct"),
                "月线 ≥40%",
                ndx.get("monthly_as_of"),
                f"{ndx.get('scope', '样本期')}峰值回撤" if ndx else "缺少 NDX 行情",
            )
        ],
        min_present=1,
    )
    layers = [leading, synchronous, confirmation]
    by_key = {layer["key"]: layer for layer in layers}
    if by_key["confirmation"]["status"] == "triggered":
        stage = {"number": 4, "label": "阶段四 · 市场确认"}
    elif by_key["synchronous"]["status"] == "triggered":
        stage = {"number": 3, "label": "阶段三 · 同步转弱"}
    elif by_key["leading"]["status"] == "triggered":
        stage = {"number": 2, "label": "阶段二 · 领先转弱"}
    elif all(layer["status"] == "clear" for layer in layers):
        stage = {"number": 1, "label": "阶段一 · 扩张延续"}
    else:
        stage = {"number": 0, "label": "证据不足"}
    return {
        "generator": "rules",
        "stage": stage,
        "layers": layers,
        "cloud": cloud,
        "cloud_disclosure": disclosure,
        "capex_guidance": guidance,
        "hardware": hardware,
        "ndx": ndx,
        "financial_as_of": max((r["as_of"] for r in cloud_rows), default=None),
        "as_of": max(
            (value for value in [ndx.get("as_of"), *(r["as_of"] for r in cloud_rows)] if value),
            default=None,
        ),
    }


if __name__ == "__main__":
    import json

    print(json.dumps(generate_ai_cycle_overview(), ensure_ascii=False, indent=2, default=str))
