from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.analysis_utils import read_csv_or_empty
from src.config import CN_AI_SUPPLY_CHAIN, ROOT
from src.cycle_utils import drawdown, quarterly_yoy, true_streak
from src.inflation_analysis import real_demand_block


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None or pd.isna(value) else round(float(value), digits)


def household_block(root: Path = ROOT) -> dict[str, object]:
    frame = read_csv_or_empty(root / "data" / "china" / "nbs_household.csv")
    required = {"nbs_income_real_yoy_ytd_pct", "nbs_consumption_real_yoy_ytd_pct"}
    if frame.empty or not required <= set(frame.columns):
        return {}
    growth = frame[list(required)].dropna().copy()
    growth["gap"] = (
        growth["nbs_income_real_yoy_ytd_pct"] - growth["nbs_consumption_real_yoy_ytd_pct"]
    )
    latest = frame.iloc[-1]
    streak = true_streak(growth["gap"] > 2)
    history = frame.tail(16).copy()
    history["gap"] = (
        history["nbs_income_real_yoy_ytd_pct"] - history["nbs_consumption_real_yoy_ytd_pct"]
    )
    r = lambda v: _round(v)  # noqa: E731
    return {
        "as_of": frame.index[-1].strftime("%Y-%m-%d"),
        "income_ytd_cny": r(latest.get("nbs_income_per_capita_ytd_cny")),
        "consumption_ytd_cny": r(latest.get("nbs_consumption_per_capita_ytd_cny")),
        "income_real_yoy_pct": r(latest["nbs_income_real_yoy_ytd_pct"]),
        "consumption_real_yoy_pct": r(latest["nbs_consumption_real_yoy_ytd_pct"]),
        "gap_pp": r(growth["gap"].iloc[-1]),
        "gap_above_2pp_quarters": streak,
        "triggered": streak >= 2,
        "history": {
            "dates": [d.strftime("%Y-%m-%d") for d in history.index],
            "income": [r(v) for v in history["nbs_income_real_yoy_ytd_pct"]],
            "consumption": [r(v) for v in history["nbs_consumption_real_yoy_ytd_pct"]],
            "gap": [r(v) for v in history["gap"]],
        },
    }


def index_drawdown(series: pd.Series) -> dict[str, object]:
    return drawdown(series, history_start="2010-06-01")


def _yoy(series: pd.Series) -> float | None:
    series = series.dropna().sort_index()
    if series.empty:
        return None
    return _round(quarterly_yoy(series).iloc[-1])


def a_share_block(root: Path = ROOT) -> dict[str, object]:
    index = read_csv_or_empty(root / "data" / "china" / "399006.SZ_daily.csv")
    index_out = index_drawdown(index["close"]) if not index.empty and "close" in index else {}
    valuation = read_csv_or_empty(
        root / "data" / "china" / "ai_supply_chain_valuation.csv", index_col="ticker"
    )
    rows: list[dict[str, object]] = []
    for ticker, company in CN_AI_SUPPLY_CHAIN.items():
        daily = read_csv_or_empty(root / "data" / "china" / f"{ticker}_daily.csv")
        quarterly = read_csv_or_empty(root / "data" / "china" / f"{ticker}_quarterly.csv")
        row: dict[str, object] = {"ticker": ticker, "company": company}
        if not daily.empty and "close" in daily:
            drawdown = index_drawdown(daily["close"])
            row.update(
                {
                    "price_cny": drawdown.get("current"),
                    "price_date": drawdown.get("as_of"),
                    "drawdown_pct": drawdown.get("drawdown_pct"),
                    "peak_date": drawdown.get("peak_date"),
                    "history_start": drawdown.get("history_start"),
                }
            )
        if ticker in valuation.index:
            current = valuation.loc[ticker]
            row.update(
                {
                    "pe_ttm": _round(current.get("pe_ttm")),
                    "eps_ttm_cny": _round(current.get("diluted_eps_ttm_cny")),
                    "eps_period_end": current.get("eps_period_end") or None,
                }
            )
        if not quarterly.empty:
            latest = quarterly.iloc[-1]
            row.update(
                {
                    "financial_as_of": quarterly.index[-1].strftime("%Y-%m-%d"),
                    "revenue_yoy_pct": _yoy(quarterly["revenue_cny"]),
                    "net_income_yoy_pct": _yoy(quarterly["net_income_cny"]),
                    "inventory_yoy_pct": _yoy(quarterly["inventory_cny"]),
                    "revenue_b": _round(latest.get("revenue_cny") / 1e9, 3),
                    "inventory_b": _round(latest.get("inventory_cny") / 1e9, 3),
                }
            )
        rows.append(row)
    return {"index": index_out, "rows": rows}


def _us_comparison(root: Path) -> dict[str, object]:
    consumption = read_csv_or_empty(root / "data" / "fred" / "consumption" / "consumption.csv")
    growth = read_csv_or_empty(root / "data" / "fred" / "growth" / "growth.csv")
    inflation = read_csv_or_empty(root / "data" / "fred" / "inflation" / "inflation.csv")
    producer = read_csv_or_empty(root / "data" / "fred" / "producer_prices" / "producer_prices.csv")
    demand = real_demand_block(consumption, growth)
    cards = demand.get("cards", {})
    result: dict[str, object] = {
        "real_dpi_yoy_pct": cards.get("real_dpi_yoy"),
        "real_pce_yoy_pct": cards.get("real_pce_yoy"),
        "demand_as_of": demand.get("as_of"),
    }
    for key, frame, column in [
        ("cpi_yoy_pct", inflation, "CPI"),
        ("ppi_yoy_pct", producer, "PPI_FD"),
    ]:
        if not frame.empty and column in frame:
            series = frame[column].dropna()
            if len(series) >= 13:
                result[key] = _round((float(series.iloc[-1]) / float(series.iloc[-13]) - 1) * 100)
                result[f"{key.split('_')[0]}_as_of"] = series.index[-1].strftime("%Y-%m-%d")
    return result


def generate_china_overview(root: Path = ROOT) -> dict[str, object]:
    household = household_block(root)
    monthly_path = root / "data" / "china" / "nbs_monthly.csv"
    monthly = read_csv_or_empty(monthly_path) if monthly_path.exists() else pd.DataFrame()
    monthly_out: dict[str, object] = {}
    if not monthly.empty:
        latest = monthly.iloc[-1]
        monthly_out = {
            "as_of": monthly.index[-1].strftime("%Y-%m-%d"),
            **{column: _round(latest[column]) for column in monthly.columns},
        }
    a_shares = a_share_block(root)
    us = _us_comparison(root)
    signals = []
    if household:
        signals.append(
            {
                "title": "居民收入与消费",
                "text": (
                    f"实际收入累计同比 {household['income_real_yoy_pct']}%，实际消费累计同比 "
                    f"{household['consumption_real_yoy_pct']}%，差值 {household['gap_pp']}pp；"
                    f"差值连续 {household['gap_above_2pp_quarters']} 个季度超过 2pp。"
                ),
            }
        )
    if a_shares["index"]:
        signals.append(
            {
                "title": "A 股价格确认",
                "text": (
                    f"创业板指{a_shares['index']['scope']}回撤 "
                    f"{a_shares['index']['drawdown_pct']}%，"
                    f"已完成月线回撤 {a_shares['index']['monthly_drawdown_pct']}%。"
                ),
            }
        )
    dates = [household.get("as_of"), monthly_out.get("as_of"), a_shares["index"].get("as_of")]
    return {
        "generator": "rules",
        "as_of": max((value for value in dates if value), default=None),
        "household": household,
        "monthly": monthly_out,
        "a_shares": a_shares,
        "us": us,
        "signals": signals,
    }


if __name__ == "__main__":
    import json

    print(json.dumps(generate_china_overview(), ensure_ascii=False, indent=2, default=str))
