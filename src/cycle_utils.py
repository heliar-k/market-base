from __future__ import annotations

import pandas as pd

from src.analysis_utils import trading_only


def quarterly_yoy(series: pd.Series) -> pd.Series:
    """按上年同季日期计算同比，允许 10 天报告期偏移，缺季保留 NaN。"""
    series = series.dropna().sort_index()
    prior_dates = series.index - pd.DateOffset(years=1)
    positions = series.index.get_indexer(
        prior_dates, method="nearest", tolerance=pd.Timedelta(days=10)
    )
    prior_values = [
        series.iloc[position] if position >= 0 else float("nan") for position in positions
    ]
    prior = pd.Series(prior_values, index=series.index)
    return (series / prior - 1) * 100


def _broken_quarter_boundary(index: pd.DatetimeIndex, position: int) -> bool:
    if position + 1 >= len(index):
        return False
    days = (index[position + 1] - index[position]).days
    return not 75 <= days <= 100


def negative_streak(s: pd.Series) -> int:
    count = 0
    for position in range(len(s) - 1, -1, -1):
        value = s.iloc[position]
        if pd.isna(value) or value >= 0:
            break
        if isinstance(s.index, pd.DatetimeIndex) and _broken_quarter_boundary(s.index, position):
            break
        count += 1
    return count


def true_streak(s: pd.Series) -> int:
    count = 0
    for position in range(len(s) - 1, -1, -1):
        value = s.iloc[position]
        if pd.isna(value) or not bool(value):
            break
        if isinstance(s.index, pd.DatetimeIndex) and _broken_quarter_boundary(s.index, position):
            break
        count += 1
    return count


def drawdown(series: pd.Series, history_start: str) -> dict[str, object]:
    """样本内日线与已完成月线回撤；history_start 是完整历史的准入起点。"""
    series = trading_only(series).dropna()
    if series.empty:
        return {}
    peak = float(series.max())
    current = float(series.iloc[-1])
    monthly = series.groupby(series.index.to_period("M")).tail(1)
    last = series.index[-1]
    complete = (last + pd.offsets.BDay(1)).month != last.month
    completed = monthly if complete else monthly.iloc[:-1]
    monthly_drawdown = (
        (1 - float(completed.iloc[-1]) / float(completed.max())) * 100
        if not completed.empty
        else None
    )
    rounded = lambda value: None if value is None else round(float(value), 2)  # noqa: E731
    return {
        "as_of": last.strftime("%Y-%m-%d"),
        "current": rounded(current),
        "peak": rounded(peak),
        "peak_date": series.idxmax().strftime("%Y-%m-%d"),
        "drawdown_pct": rounded((1 - current / peak) * 100),
        "monthly_as_of": completed.index[-1].strftime("%Y-%m-%d") if not completed.empty else None,
        "monthly_drawdown_pct": rounded(monthly_drawdown),
        "history_start": series.index[0].strftime("%Y-%m-%d"),
        "scope": "历史" if series.index[0] <= pd.Timestamp(history_start) else "样本期",
    }
