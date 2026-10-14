"""repair_recent_rows 缺口回填回归测试（tmp_path 隔离，fetch_ohlcv 打桩不碰网络）。

背景（2026-10-13 事故）：快照以拉取日为 key，盘前班次把前日收盘挂进当日行，
ETF EOD 迟发布又留下缺失列——旧行永不回访就固化。回填按 bar 日期取真值。
"""

import pandas as pd

from src.fetchers import yfinance_fetcher as yf_mod
from src.fetchers.yfinance_fetcher import repair_recent_rows


def _csv(root, rows):
    p = root / "data/yfinance/asset_prices.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(p, index=False)


def _bars(dates_closes: list[tuple[str, float]], volume: float | None = 1_000.0):
    df = pd.DataFrame(
        {
            "close": [c for _, c in dates_closes],
            "volume": [volume] * len(dates_closes),
        },
        index=pd.DatetimeIndex([d for d, _ in dates_closes]),
    )
    df.index.name = "date"
    return df


def _read(root):
    return pd.read_csv(root / "data/yfinance/asset_prices.csv")


def test_copied_value_repaired_from_bar(tmp_path, monkeypatch):
    _csv(
        tmp_path,
        [
            {"date": "2026-10-12", "SPX": 7807.24},
            {"date": "2026-10-13", "SPX": 7807.24},  # 复制昨日（占位）
        ],
    )
    monkeypatch.setattr(
        yf_mod,
        "fetch_ohlcv",
        lambda t, period="5d": _bars([("2026-10-12", 7807.24), ("2026-10-13", 7805.45)]),
    )
    assert repair_recent_rows(tmp_path / "data/yfinance/asset_prices.csv") == 1
    assert _read(tmp_path).query("date == '2026-10-13'")["SPX"].iloc[0] == 7805.45


def test_missing_value_repaired(tmp_path, monkeypatch):
    _csv(
        tmp_path,
        [
            {"date": "2026-10-12", "SMH": 590.99, "SMH_volume": 100.0},
            {"date": "2026-10-13", "SMH": None, "SMH_volume": None},  # 缺失
        ],
    )
    monkeypatch.setattr(
        yf_mod,
        "fetch_ohlcv",
        lambda t, period="5d": _bars([("2026-10-13", 592.1)], volume=2_345.0),
    )
    assert repair_recent_rows(tmp_path / "data/yfinance/asset_prices.csv") == 2
    row = _read(tmp_path).query("date == '2026-10-13'").iloc[0]
    assert row["SMH"] == 592.1 and row["SMH_volume"] == 2345.0


def test_weekend_row_skipped(tmp_path, monkeypatch):
    """周末行整行复制属正常（无交易），不回填、零改动。"""
    _csv(
        tmp_path,
        [
            {"date": "2026-10-09", "SPX": 7811.54},
            {"date": "2026-10-10", "SPX": 7811.54},  # 周六
            {"date": "2026-10-12", "SPX": 7807.24},
        ],
    )
    monkeypatch.setattr(
        yf_mod,
        "fetch_ohlcv",
        lambda t, period="5d": _bars([("2026-10-09", 7811.54), ("2026-10-10", 7810.0)]),
    )

    def _boom(t, period="5d"):
        raise AssertionError("周末行不应触发拉取")

    monkeypatch.setattr(yf_mod, "fetch_ohlcv", _boom)
    assert repair_recent_rows(tmp_path / "data/yfinance/asset_prices.csv") == 0


def test_no_bar_for_date_keeps_snapshot(tmp_path, monkeypatch):
    """该日休市无 bar（如 ETF EOD 未发布）→ 保留快照值，不标变化。"""
    _csv(
        tmp_path,
        [
            {"date": "2026-10-12", "SMH": 590.99},
            {"date": "2026-10-13", "SMH": 590.99},  # 复制，但源也没有 10-13 bar
        ],
    )
    monkeypatch.setattr(
        yf_mod,
        "fetch_ohlcv",
        lambda t, period="5d": _bars([("2026-10-12", 590.99)]),  # 无 10-13 bar
    )
    assert repair_recent_rows(tmp_path / "data/yfinance/asset_prices.csv") == 0
    assert _read(tmp_path).query("date == '2026-10-13'")["SMH"].iloc[0] == 590.99


def test_no_gaps_means_no_network(tmp_path, monkeypatch):
    _csv(
        tmp_path,
        [
            {"date": "2026-10-12", "SPX": 7807.24},
            {"date": "2026-10-13", "SPX": 7805.45},
        ],
    )

    def _boom(t, period="5d"):
        raise AssertionError("无缺口不应发起拉取")

    monkeypatch.setattr(yf_mod, "fetch_ohlcv", _boom)
    assert repair_recent_rows(tmp_path / "data/yfinance/asset_prices.csv") == 0


def test_multi_ticker_gaps_do_not_cross_contaminate(tmp_path, monkeypatch):
    """多 ticker 同时有缺口：各自按自己的 bar 回填，绝不把别家的值写进自家列。"""
    _csv(
        tmp_path,
        [
            {"date": "2026-10-12", "SPX": 7807.24, "SMH": 590.99},
            {"date": "2026-10-13", "SPX": 7807.24, "SMH": 590.99},  # 两列都是占位
        ],
    )
    bars = {
        "^GSPC": _bars([("2026-10-12", 7807.24), ("2026-10-13", 7805.45)], volume=9.0),
        "SMH": _bars([("2026-10-12", 590.99), ("2026-10-13", 592.1)], volume=1_234.0),
    }
    monkeypatch.setattr(yf_mod, "fetch_ohlcv", lambda t, period="5d": bars[t])
    assert repair_recent_rows(tmp_path / "data/yfinance/asset_prices.csv") == 2
    row = _read(tmp_path).query("date == '2026-10-13'").iloc[0]
    assert row["SPX"] == 7805.45  # 来自 ^GSPC 的 close，不是 SMH 的 volume
    assert row["SMH"] == 592.1  # 来自 SMH 的 close，不是 ^GSPC 的 volume
