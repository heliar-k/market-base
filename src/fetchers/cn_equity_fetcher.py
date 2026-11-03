# A 股指数与 AI 光模块供应链公开数据：国证指数官网 + Yahoo Finance。

from __future__ import annotations

import argparse
import logging
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from ..config import CHINEXT_CODE as CONFIG_CHINEXT_CODE
from ..config import CN_AI_SUPPLY_CHAIN, ROOT
from ._io import upsert_timeseries
from .yfinance_fetcher import ensure_yf_proxy

logger = logging.getLogger(__name__)

CHINEXT_TICKER = f"{CONFIG_CHINEXT_CODE}.SZ"
CHINEXT_CODE = CONFIG_CHINEXT_CODE
CHINEXT_START_DATE = "2010-06-01"
AI_SUPPLY_CHAIN = CN_AI_SUPPLY_CHAIN

CNI_DAILY_URL = "https://hq.cnindex.com.cn/market/market/getIndexDailyDataWithDataFormat"
CNI_DETAIL_URL = "https://www.cnindex.com.cn/module/index-detail.html?act_menu=1&indexCode=399006"
YAHOO_QUOTE_URL = "https://finance.yahoo.com/quote/{ticker}"

CNI_FIELDS = [
    "timestamp",
    "current",
    "high",
    "open",
    "low",
    "close",
    "chg",
    "percent",
    "amount",
    "volume",
    "avg",
]
DAILY_COLUMNS = [
    "open",
    "high",
    "low",
    "close",
    "adj_close",
    "volume",
    "dividends",
    "stock_splits",
]
QUARTERLY_COLUMNS = [
    "revenue_cny",
    "net_income_cny",
    "inventory_cny",
    "diluted_eps_cny",
    "diluted_average_shares",
    "ordinary_shares_number",
]


def _check_unique_dates(df: pd.DataFrame, source: str) -> None:
    if df.index.has_duplicates:
        raise ValueError(f"{source} 返回重复日期")
    if df.index.hasnans:
        raise ValueError(f"{source} 返回无效日期")


def parse_cni_daily(payload: dict) -> pd.DataFrame:
    """解析国证指数日线；成交量转为股，成交额转为元。"""
    if payload.get("code") != 200:
        raise ValueError(f"国证指数接口错误: {payload.get('code')} {payload.get('message')}")
    data = payload.get("data") or {}
    fields = data.get("item")
    rows = data.get("data")
    if fields != CNI_FIELDS:
        raise ValueError(f"国证指数字段变化: {fields}")
    if not rows:
        raise ValueError("国证指数未返回日线")
    if len(rows) != payload.get("total"):
        raise ValueError(f"国证指数历史不完整: {len(rows)} / {payload.get('total')}")

    raw = pd.DataFrame(rows, columns=fields)
    dates = pd.to_datetime(raw["timestamp"], format="%Y-%m-%d", errors="raise")
    result = pd.DataFrame(index=pd.DatetimeIndex(dates, name="date"))
    for source, target in (("open", "open"), ("high", "high"), ("low", "low"), ("close", "close")):
        result[target] = pd.to_numeric(raw[source], errors="raise").to_numpy()
    # 官网口径：成交量万手、成交额亿元；1 万手 = 1,000,000 股。
    volume = pd.to_numeric(raw["volume"], errors="raise") * 1_000_000
    amount = pd.to_numeric(raw["amount"], errors="raise") * 100_000_000
    result["volume"] = volume.round().astype("int64").to_numpy()
    result["amount"] = amount.round().astype("int64").to_numpy()
    result = result.sort_index()
    _check_unique_dates(result, "国证指数")
    return result


def fetch_chinext_daily(end_date: str | None = None) -> pd.DataFrame:
    """抓取创业板指自 2010-06-01 发布以来的全部日线。"""
    if end_date is None:
        end_date = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    response = requests.get(
        CNI_DAILY_URL,
        params={
            "indexCode": CHINEXT_CODE,
            "startDate": CHINEXT_START_DATE,
            "endDate": end_date,
            "frequency": "day",
        },
        headers={"Referer": CNI_DETAIL_URL},
        timeout=60,
    )
    response.raise_for_status()
    result = parse_cni_daily(response.json())
    if result.index[0].date().isoformat() != CHINEXT_START_DATE:
        raise ValueError(f"创业板指首日异常: {result.index[0].date()}")
    logger.info(
        "创业板指: %d 行, %s → %s",
        len(result),
        result.index[0].date(),
        result.index[-1].date(),
    )
    return result


def _yahoo_ticker(ticker: str) -> object:
    if ticker not in AI_SUPPLY_CHAIN:
        raise ValueError(f"未登记的 A 股 ticker: {ticker}")
    ensure_yf_proxy()
    import yfinance as yf

    return yf.Ticker(ticker)


def _normalise_yahoo_daily(history: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if history.empty:
        raise ValueError(f"Yahoo Finance 未返回 {ticker} 日线")
    result = history.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Adj Close": "adj_close",
            "Volume": "volume",
            "Dividends": "dividends",
            "Stock Splits": "stock_splits",
        }
    ).copy()
    result.index = pd.to_datetime(result.index).tz_localize(None).normalize()
    result.index.name = "date"
    missing = [column for column in DAILY_COLUMNS if column not in result.columns]
    if missing:
        raise ValueError(f"{ticker} 日线缺少字段: {missing}")
    result = result[DAILY_COLUMNS].sort_index()
    _check_unique_dates(result, ticker)
    return result


def fetch_yahoo_daily(ticker: str) -> pd.DataFrame:
    """抓取固定公司自上市以来的未复权 OHLCV，并保留复权收盘价与公司行动。"""
    history = _yahoo_ticker(ticker).history(
        start="1990-01-01",
        interval="1d",
        auto_adjust=False,
        timeout=30,
    )
    result = _normalise_yahoo_daily(history, ticker)
    logger.info(
        "%s 日线: %d 行, %s → %s",
        ticker,
        len(result),
        result.index[0].date(),
        result.index[-1].date(),
    )
    return result


def _normalise_periods(*statements: pd.DataFrame) -> pd.DatetimeIndex:
    periods = pd.DatetimeIndex([])
    for statement in statements:
        if statement.empty:
            continue
        current = pd.to_datetime(statement.columns).tz_localize(None).normalize()
        periods = periods.union(current)
    return periods.sort_values()


def _statement_row(
    statement: pd.DataFrame,
    row_name: str,
    periods: pd.DatetimeIndex,
) -> pd.Series:
    if statement.empty or row_name not in statement.index:
        return pd.Series(float("nan"), index=periods, dtype="float64")
    values = statement.loc[row_name]
    if isinstance(values, pd.DataFrame):
        raise ValueError(f"财报科目重复: {row_name}")
    values.index = pd.to_datetime(values.index).tz_localize(None).normalize()
    values = pd.to_numeric(values, errors="coerce")
    return values.reindex(periods)


def _normalise_quarterly(
    income: pd.DataFrame,
    balance: pd.DataFrame,
    ticker: str,
) -> pd.DataFrame:
    if income.empty and balance.empty:
        raise ValueError(f"Yahoo Finance 未返回 {ticker} 季度财报")
    periods = _normalise_periods(income, balance)
    if periods.empty:
        raise ValueError(f"{ticker} 季度财报没有报告期")
    result = pd.DataFrame(index=periods.rename("date"))
    # Yahoo quarterly_income_stmt 是单季度值；存货是报告期末时点值。
    result["revenue_cny"] = _statement_row(income, "Total Revenue", periods)
    result["net_income_cny"] = _statement_row(income, "Net Income Common Stockholders", periods)
    result["inventory_cny"] = _statement_row(balance, "Inventory", periods)
    result["diluted_eps_cny"] = _statement_row(income, "Diluted EPS", periods)
    result["diluted_average_shares"] = _statement_row(income, "Diluted Average Shares", periods)
    result["ordinary_shares_number"] = _statement_row(balance, "Ordinary Shares Number", periods)
    return result[QUARTERLY_COLUMNS]


def fetch_quarterly_fundamentals(ticker: str) -> pd.DataFrame:
    """抓取季度营收、归母净利润、存货及 EPS/股本相关字段。"""
    yf_ticker = _yahoo_ticker(ticker)
    result = _normalise_quarterly(
        yf_ticker.quarterly_income_stmt,
        yf_ticker.quarterly_balance_sheet,
        ticker,
    )
    logger.info("%s 季度财报: %d 个报告期", ticker, len(result))
    return result


def _finite_positive(value: object) -> bool:
    if value is None or pd.isna(value):
        return False
    return math.isfinite(float(value)) and float(value) > 0


def build_valuation_row(
    ticker: str,
    daily: pd.DataFrame,
    ttm_income: pd.DataFrame,
) -> pd.Series:
    """用最新未复权收盘价 / TTM diluted EPS；EPS 缺失或非正数时 PE 留空。"""
    price = daily["close"].dropna().iloc[-1]
    price_date = daily["close"].dropna().index[-1].date().isoformat()
    eps = float("nan")
    eps_period_end = ""
    if not ttm_income.empty:
        eps_period_end = pd.Timestamp(ttm_income.columns[0]).date().isoformat()
        eps_values = _statement_row(
            ttm_income,
            "Diluted EPS",
            pd.DatetimeIndex([pd.Timestamp(ttm_income.columns[0])]),
        )
        eps = float(eps_values.iloc[0])
    pe_ttm = price / eps if _finite_positive(price) and _finite_positive(eps) else float("nan")
    quote_url = YAHOO_QUOTE_URL.format(ticker=ticker)
    return pd.Series(
        {
            "company": AI_SUPPLY_CHAIN[ticker],
            "currency": "CNY",
            "price_cny": price,
            "price_date": price_date,
            "price_source": f"Yahoo Finance Ticker.history Close (auto_adjust=False), {quote_url}",
            "diluted_eps_ttm_cny": eps,
            "eps_period_end": eps_period_end,
            "eps_source": (
                f"Yahoo Finance Ticker.ttm_income_stmt Diluted EPS, {quote_url}/financials"
            ),
            "pe_ttm": pe_ttm,
            "pe_ttm_method": "price_cny / diluted_eps_ttm_cny",
        },
        name=ticker,
    )


def fetch_valuation(ticker: str, daily: pd.DataFrame | None = None) -> pd.Series:
    """抓取带价格日期和 EPS 报告期末日期的 PE-TTM 快照。"""
    if daily is None:
        daily = fetch_yahoo_daily(ticker)
    ttm_income = _yahoo_ticker(ticker).ttm_income_stmt
    return build_valuation_row(ticker, daily, ttm_income)


def fetch_all() -> dict[str, pd.DataFrame]:
    """抓取固定指数与两家公司，返回可直接写 CSV 的 DataFrame 集合。"""
    frames = {f"{CHINEXT_TICKER}_daily": fetch_chinext_daily()}
    valuations = []
    for ticker in AI_SUPPLY_CHAIN:
        daily = fetch_yahoo_daily(ticker)
        frames[f"{ticker}_daily"] = daily
        frames[f"{ticker}_quarterly"] = fetch_quarterly_fundamentals(ticker)
        valuations.append(fetch_valuation(ticker, daily))
    frames["ai_supply_chain_valuation"] = pd.DataFrame(
        [row.to_dict() for row in valuations],
        index=pd.Index([row.name for row in valuations], name="ticker"),
    )
    return frames


def write_outputs(frames: dict[str, pd.DataFrame], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, frame in frames.items():
        if frame.empty:
            raise ValueError(f"{name} 为空，拒绝覆盖已有数据")
        path = output_dir / f"{name}.csv"
        if name.endswith(("_daily", "_quarterly")):
            upsert_timeseries(path, frame, column_order=list(frame.columns))
        else:
            frame.to_csv(path)
        logger.info("写入 %s", path)


def main() -> None:
    parser = argparse.ArgumentParser(description="A 股创业板指与 AI 光模块供应链公开数据")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "data" / "china",
        help="CSV 输出目录，默认 data/china",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    frames = fetch_all()
    write_outputs(frames, args.output_dir)
    for name, frame in frames.items():
        print(f"{name}: {len(frame)} 行 × {len(frame.columns)} 列 → {args.output_dir / name}.csv")
    print(frames["ai_supply_chain_valuation"].to_string())


if __name__ == "__main__":
    main()
