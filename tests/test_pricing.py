"""C4: src/pricing.py 共享期权定价模块测试。

模式沿用 C2/C3 验证过的 monkeypatch 硬测：
- aggregate_wall 纯函数，小 DataFrame 直测
- fetch_yf_chain 内部惰性 import yfinance，patch 模块属性 Ticker + ensure_yf_proxy
"""

import pandas as pd
import pytest

from src.pricing import aggregate_wall, fetch_yf_chain


class _FakeChain:
    def __init__(self, calls: pd.DataFrame, puts: pd.DataFrame):
        self.calls = calls
        self.puts = puts


class _FakeTicker:
    def __init__(self, chains: dict[str, _FakeChain]):
        self._chains = chains

    def option_chain(self, yf_date: str) -> _FakeChain:
        return self._chains[yf_date]


def _patch_yf(monkeypatch: pytest.MonkeyPatch, chains: dict[str, _FakeChain]) -> None:
    """把 yfinance.Ticker 换成假对象，ensure_yf_proxy 换成 no-op。"""
    import yfinance

    monkeypatch.setattr(
        "src.fetchers.yfinance_fetcher.ensure_yf_proxy", lambda *a, **k: None
    )
    monkeypatch.setattr(yfinance, "Ticker", lambda symbol: _FakeTicker(chains))


_ROW = {
    "strike": 100.0,
    "bid": 1.5,
    "ask": 1.7,
    "lastPrice": 1.6,
    "openInterest": 10,
    "impliedVolatility": 0.25,
    "volume": 5,
}


def _chains(rows: dict[str, list[dict]]) -> dict[str, _FakeChain]:
    out = {}
    for yf_date, rs in rows.items():
        calls = [r for r in rs if r["right"] == "C"]
        puts = [r for r in rs if r["right"] == "P"]
        out[yf_date] = _FakeChain(calls=pd.DataFrame(calls), puts=pd.DataFrame(puts))
    return out


# ============================================================================
# aggregate_wall
# ============================================================================
def test_aggregate_wall_math_and_columns():
    contracts = pd.DataFrame(
        {
            "strike": [100, 100, 105, 105, 110],
            "right": ["C", "P", "C", "P", "C"],
            "gex": [1e6, -0.5e6, 2e6, -1e6, 3e6],
            "openInterest": [100, 200, 300, 400, 500],
            "iv": [0.20, 0.25, 0.30, 0.35, 0.40],
        }
    )
    wall = aggregate_wall(contracts).set_index("strike")

    assert list(wall.columns) == [
        "call_gex",
        "put_gex",
        "total_gex",
        "total_oi",
        "avg_iv",
        "abs_gex",
    ]
    # 100: C=1e6, P=-0.5e6
    assert wall.loc[100, "call_gex"] == 1e6
    assert wall.loc[100, "put_gex"] == -0.5e6
    assert wall.loc[100, "total_gex"] == 0.5e6
    assert wall.loc[100, "abs_gex"] == 0.5e6
    assert wall.loc[100, "total_oi"] == 300
    assert wall.loc[100, "avg_iv"] == pytest.approx(0.225)
    # 105: C=2e6, P=-1e6
    assert wall.loc[105, "total_gex"] == 1e6
    assert wall.loc[105, "total_oi"] == 700
    # 110: 仅 call
    assert wall.loc[110, "call_gex"] == 3e6
    assert wall.loc[110, "put_gex"] == 0
    assert wall.loc[110, "avg_iv"] == 0.4


def test_aggregate_wall_empty_keeps_columns():
    empty = pd.DataFrame(columns=["strike", "right", "gex", "openInterest", "iv"])
    wall = aggregate_wall(empty)
    assert wall.empty
    assert list(wall.columns) == [
        "strike",
        "call_gex",
        "put_gex",
        "total_gex",
        "total_oi",
        "avg_iv",
        "abs_gex",
    ]


# ============================================================================
# fetch_yf_chain
# ============================================================================
def test_fetch_yf_chain_normalizes_rows(monkeypatch):
    _patch_yf(
        monkeypatch,
        _chains(
            {
                "2025-01-17": [
                    {**_ROW, "right": "C"},
                    {
                        **_ROW,
                        "right": "P",
                        "strike": 95.0,
                        "bid": float("nan"),
                        "openInterest": float("nan"),
                        "volume": float("nan"),
                    },
                ]
            }
        ),
    )
    df = fetch_yf_chain("TEST", ["20250117"])

    assert list(df.columns) == [
        "expiration",
        "strike",
        "right",
        "bid",
        "ask",
        "last",
        "openInterest",
        "impliedVolatility",
        "volume",
    ]
    assert len(df) == 2
    row = df.iloc[1]
    assert row["expiration"] == "20250117"
    assert row["right"] == "P"
    assert row["openInterest"] == 0  # NaN → 0
    assert pd.isna(row["bid"])  # 报价原样保留，兜底是消费方策略
    assert pd.isna(row["volume"])  # 仅 OI 做唯一规整
    assert df.iloc[0]["last"] == 1.6  # lastPrice → last 列映射


def test_fetch_yf_chain_accepts_dashed_input(monkeypatch):
    _patch_yf(monkeypatch, _chains({"2025-01-17": [{**_ROW, "right": "C"}]}))
    df = fetch_yf_chain("TEST", ["2025-01-17"])
    assert df["expiration"].iloc[0] == "20250117"


def test_fetch_yf_chain_skips_failing_expiry(monkeypatch):
    import yfinance

    chains = _chains(
        {
            "2025-01-17": [{**_ROW, "right": "C"}],
            "2025-02-21": [{**_ROW, "right": "P", "strike": 90.0}],
        }
    )

    class _Broken(_FakeTicker):
        def option_chain(self, yf_date: str) -> _FakeChain:
            if yf_date == "2025-02-21":
                raise RuntimeError("chain 不可用")
            return super().option_chain(yf_date)

    monkeypatch.setattr(yfinance, "Ticker", lambda symbol: _Broken(chains))
    monkeypatch.setattr(
        "src.fetchers.yfinance_fetcher.ensure_yf_proxy", lambda *a, **k: None
    )
    df = fetch_yf_chain("TEST", ["20250117", "20250221"])
    assert len(df) == 1
    assert df["expiration"].iloc[0] == "20250117"


def test_fetch_yf_chain_all_expiries_fail_returns_empty(monkeypatch):
    import yfinance

    class _Broken(_FakeTicker):
        def option_chain(self, yf_date: str) -> _FakeChain:
            raise RuntimeError("chain 不可用")

    monkeypatch.setattr(yfinance, "Ticker", lambda symbol: _Broken({}))
    monkeypatch.setattr(
        "src.fetchers.yfinance_fetcher.ensure_yf_proxy", lambda *a, **k: None
    )
    df = fetch_yf_chain("TEST", ["20250117"])
    assert df.empty
    assert list(df.columns) == [
        "expiration",
        "strike",
        "right",
        "bid",
        "ask",
        "last",
        "openInterest",
        "impliedVolatility",
        "volume",
    ]
