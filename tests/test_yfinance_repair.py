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


def test_full_reconcile_fixes_stale_and_wrong_values(tmp_path):
    """全列对账：不仅缺口，任何与 bar 收盘不符的值都改写（含盘中价/错误值）。"""
    _csv(
        tmp_path,
        [
            {"date": "2026-10-10", "SPX": 7811.54},
            {"date": "2026-10-13", "SPX": 7807.24},  # 复制旧值
            {"date": "2026-10-14", "SPX": 9999.99},  # 明显错误值
        ],
    )
    bars = {
        "SPX": _bars(
            [("2026-10-10", 7811.54), ("2026-10-13", 7805.45), ("2026-10-14", 7790.0)],
            volume=1.0,
        )
    }
    fixed = yf_mod.repair_recent_rows(tmp_path / "data/yfinance/asset_prices.csv", bars=bars)
    assert fixed == 2
    df = _read(tmp_path)
    assert df.query("date == '2026-10-13'")["SPX"].iloc[0] == 7805.45
    assert df.query("date == '2026-10-14'")["SPX"].iloc[0] == 7790.0


def test_reconcile_preserves_no_bar_day(tmp_path):
    """该日源无 bar（周末/休市/EOD 未发布）→ 保留快照值，不改写。"""
    _csv(
        tmp_path,
        [
            {"date": "2026-10-09", "SPX": 7811.54},
            {"date": "2026-10-10", "SPX": 7812.0},  # 周六，源无 bar
            {"date": "2026-10-12", "SPX": 7807.24},
        ],
    )
    bars = {"SPX": _bars([("2026-10-09", 7811.54), ("2026-10-12", 7807.24)])}
    fixed = yf_mod.repair_recent_rows(tmp_path / "data/yfinance/asset_prices.csv", bars=bars)
    assert fixed == 0
    assert _read(tmp_path).query("date == '2026-10-10'")["SPX"].iloc[0] == 7812.0


# ── 1m 临时收盘兑底（EOD 迟发布）──────────────────────────────


class _FakeMinute:
    """yf.Ticker 桩：返回指定日期的 1m 会话（含盘后根，应被窗口过滤）。"""

    def __init__(self, day: str, closes: dict[str, float], after_hours: float = 99.0):
        idx = pd.DatetimeIndex(
            [f"{day} 09:30", f"{day} 15:59", f"{day} 16:05"], tz="America/New_York"
        )
        self._df = pd.DataFrame(
            {"Close": [closes["open"], closes["close"], after_hours], "Volume": [10.0, 20.0, 5.0]},
            index=idx,
        )

    def history(self, **kw):
        return self._df


def test_late_close_fallback_injects_session_close(monkeypatch):
    """日线缺 10-13 bar → 注入 ET 15:59 末根；盘后根（16:05）不进。"""
    bars = {"TLT": _bars([("2026-10-09", 77.98), ("2026-10-12", 78.05)])}
    calls = []

    fake = _FakeMinute("2026-10-13", {"open": 78.20, "close": 78.35})

    class _FakeTicker:
        def __init__(self, ticker):
            calls.append(ticker)

        def history(self, **kw):
            return fake.history(**kw)

    import yfinance

    monkeypatch.setattr(yf_mod, "ensure_yf_proxy", lambda: None)
    monkeypatch.setattr(yfinance, "Ticker", _FakeTicker)
    # ET 17:00（收盘后对账窗内）；now 取 UTC，对应 ET = UTC-4
    now = pd.Timestamp("2026-10-13 21:00", tz="UTC").to_pydatetime()
    injected = yf_mod._late_close_fallback(bars, now=now)
    assert injected == 1 and calls == ["TLT"]
    day = bars["TLT"].loc["2026-10-13"]
    assert day["Close"] == 78.35  # 15:59 会话末根，非盘后 99.0
    assert day["Volume"] == 30.0  # 会话窗口内根求和（10+20），盘后 5.0 不算


def test_late_close_fallback_skips_existing_bar(monkeypatch):
    """日线已有该日 bar（EOD 已发布）→ 不注入（日线优先）。"""
    bars = {"TLT": _bars([("2026-10-12", 78.05), ("2026-10-13", 78.37)])}
    monkeypatch.setattr(yf_mod, "ensure_yf_proxy", lambda: None)

    def _boom(t):
        raise AssertionError("已有日线 bar 不应发起 1m 拉取")

    import yfinance

    monkeypatch.setattr(yfinance, "Ticker", _boom)
    now = pd.Timestamp("2026-10-13 21:00", tz="UTC").to_pydatetime()
    assert yf_mod._late_close_fallback(bars, now=now) == 0


def test_late_close_fallback_outside_window(monkeypatch):
    """ET 收盘前（对账窗外）不注入：盘中快照本就是实时价。"""
    bars = {"TLT": _bars([("2026-10-12", 78.05)])}
    monkeypatch.setattr(yf_mod, "ensure_yf_proxy", lambda: None)
    now = pd.Timestamp("2026-10-13 19:00", tz="UTC").to_pydatetime()  # ET 15:00 盘中
    assert yf_mod._late_close_fallback(bars, now=now) == 0
