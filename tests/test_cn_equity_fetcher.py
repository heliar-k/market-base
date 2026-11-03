import math
from pathlib import Path

import pandas as pd
import pytest

from src.fetchers.cn_equity_fetcher import (
    AI_SUPPLY_CHAIN,
    CHINEXT_START_DATE,
    CHINEXT_TICKER,
    CNI_DAILY_URL,
    DAILY_COLUMNS,
    QUARTERLY_COLUMNS,
    build_valuation_row,
)

_DATA = Path(__file__).resolve().parents[1] / "data" / "china"


def _read_real_csv(name: str, index_column: str = "date") -> pd.DataFrame:
    path = _DATA / f"{name}.csv"
    if not path.exists():
        pytest.skip(f"真实抓取结果不存在，先运行 ./bin/fetch_cn_equity: {path}")
    return pd.read_csv(path, index_col=index_column, parse_dates=[index_column])


def _daily_for_valuation(price: float = 100.0) -> pd.DataFrame:
    return pd.DataFrame(
        {"close": [price]},
        index=pd.DatetimeIndex(["2026-11-03"], name="date"),
    )


def _ttm_for_valuation(eps: float, row_name: str = "Diluted EPS") -> pd.DataFrame:
    return pd.DataFrame(
        {pd.Timestamp("2026-09-30"): [eps]},
        index=[row_name],
    )


def test_fixed_universe_and_official_index_url() -> None:
    assert CHINEXT_TICKER == "399006.SZ"
    assert AI_SUPPLY_CHAIN == {"300308.SZ": "中际旭创", "300502.SZ": "新易盛"}
    assert CNI_DAILY_URL.startswith("https://hq.cnindex.com.cn/")


def test_pe_ttm_uses_dated_close_and_diluted_eps() -> None:
    row = build_valuation_row("300308.SZ", _daily_for_valuation(), _ttm_for_valuation(10.0))

    assert row["pe_ttm"] == pytest.approx(10.0)
    assert row["price_date"] == "2026-11-03"
    assert row["eps_period_end"] == "2026-09-30"
    assert "history Close" in row["price_source"]
    assert "ttm_income_stmt Diluted EPS" in row["eps_source"]
    assert "target" not in row["price_source"].lower()


@pytest.mark.parametrize("eps", [0.0, -1.0, float("nan")])
def test_pe_ttm_empty_for_unusable_eps(eps: float) -> None:
    row = build_valuation_row("300502.SZ", _daily_for_valuation(), _ttm_for_valuation(eps))

    assert math.isnan(row["pe_ttm"])
    assert row["eps_period_end"] == "2026-09-30"


def test_pe_ttm_empty_without_diluted_eps() -> None:
    row = build_valuation_row(
        "300502.SZ",
        _daily_for_valuation(),
        _ttm_for_valuation(10.0, row_name="Basic EPS"),
    )

    assert math.isnan(row["pe_ttm"])
    assert math.isnan(row["diluted_eps_ttm_cny"])


def test_real_chinext_full_history() -> None:
    df = _read_real_csv(f"{CHINEXT_TICKER}_daily")

    assert list(df.columns) == ["open", "high", "low", "close", "volume", "amount"]
    assert len(df) >= 3986
    assert df.index[0] == pd.Timestamp(CHINEXT_START_DATE)
    assert df.index[-1] >= pd.Timestamp("2026-11-02")
    assert df.index.is_unique and df.index.is_monotonic_increasing
    assert (df[["open", "high", "low", "close"]] > 0).all().all()
    assert (df["high"] >= df["low"]).all()
    assert (df["volume"] >= 0).all() and (df["amount"] > 0).all()


@pytest.mark.parametrize(
    ("ticker", "first_date", "minimum_rows"),
    [
        ("300308.SZ", "2012-04-10", 3537),
        ("300502.SZ", "2016-03-03", 2590),
    ],
)
def test_real_company_full_history(ticker: str, first_date: str, minimum_rows: int) -> None:
    df = _read_real_csv(f"{ticker}_daily")

    assert list(df.columns) == DAILY_COLUMNS
    assert len(df) >= minimum_rows
    assert df.index[0] == pd.Timestamp(first_date)
    assert df.index[-1] >= pd.Timestamp("2026-11-03")
    assert df.index.is_unique and df.index.is_monotonic_increasing
    assert (df["close"] > 0).all()


@pytest.mark.parametrize("ticker", list(AI_SUPPLY_CHAIN))
def test_real_quarterly_fundamentals(ticker: str) -> None:
    df = _read_real_csv(f"{ticker}_quarterly")

    assert list(df.columns) == QUARTERLY_COLUMNS
    assert len(df) >= 6
    assert df.index[-1] >= pd.Timestamp("2026-09-30")
    latest = df.iloc[-1]
    assert latest["revenue_cny"] > 0
    assert latest["net_income_cny"] > 0
    assert latest["inventory_cny"] > 0


def test_real_valuation_sources_dates_and_calculation() -> None:
    path = _DATA / "ai_supply_chain_valuation.csv"
    if not path.exists():
        pytest.skip(f"真实估值结果不存在，先运行 python -m src.fetchers.cn_equity_fetcher: {path}")
    df = pd.read_csv(path, index_col="ticker")

    assert set(df.index) == set(AI_SUPPLY_CHAIN)
    for ticker, row in df.iterrows():
        assert row["price_cny"] > 0
        assert pd.Timestamp(row["price_date"]) >= pd.Timestamp("2026-11-03")
        assert pd.Timestamp(row["eps_period_end"]) >= pd.Timestamp("2026-09-30")
        assert row["diluted_eps_ttm_cny"] > 0
        assert row["pe_ttm"] == pytest.approx(row["price_cny"] / row["diluted_eps_ttm_cny"])
        assert "history Close" in row["price_source"]
        assert "ttm_income_stmt Diluted EPS" in row["eps_source"]
        assert "target" not in " ".join(str(value) for value in row).lower()
