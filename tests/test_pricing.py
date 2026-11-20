"""C4: src/pricing.py 共享期权定价模块测试。

模式沿用 C2/C3 验证过的 monkeypatch 硬测：
- aggregate_wall 纯函数，小 DataFrame 直测
- fetch_yf_chain 内部惰性 import yfinance，patch 模块属性 Ticker + ensure_yf_proxy
"""

import pandas as pd
import pytest

from src.pricing import (
    aggregate_wall,
    bs_greeks,
    fetch_yf_chain,
    net_gex_profile,
    zero_gamma_flip,
)


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

    monkeypatch.setattr("src.fetchers.yfinance_fetcher.ensure_yf_proxy", lambda *a, **k: None)
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
    monkeypatch.setattr("src.fetchers.yfinance_fetcher.ensure_yf_proxy", lambda *a, **k: None)
    df = fetch_yf_chain("TEST", ["20250117", "20250221"])
    assert len(df) == 1
    assert df["expiration"].iloc[0] == "20250117"


def test_fetch_yf_chain_all_expiries_fail_returns_empty(monkeypatch):
    import yfinance

    class _Broken(_FakeTicker):
        def option_chain(self, yf_date: str) -> _FakeChain:
            raise RuntimeError("chain 不可用")

    monkeypatch.setattr(yfinance, "Ticker", lambda symbol: _Broken({}))
    monkeypatch.setattr("src.fetchers.yfinance_fetcher.ensure_yf_proxy", lambda *a, **k: None)
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


# ============================================================================
# net_gex_profile & zero_gamma_flip
# ============================================================================
def test_net_gex_profile_matches_scalar_bs_greeks():
    strikes = [95.0, 100.0, 105.0]
    rights = ["P", "P", "C"]
    years = [0.1, 0.2, 0.15]
    ivs = [0.25, 0.22, 0.20]
    ois = [1000, 500, 1200]
    spot = 100.0

    expected = 0.0
    for k, right, t, iv, oi in zip(strikes, rights, years, ivs, ois, strict=True):
        g, _ = bs_greeks(spot, k, t, iv)
        expected += (g if right == "C" else -g) * oi * 100.0 * spot

    got = net_gex_profile(strikes, rights, years, ivs, ois, [spot])[0]
    assert got == pytest.approx(expected, rel=1e-12)


def test_zero_gamma_flip_finds_root_above_and_below_spot():
    strikes = [95.0, 105.0]
    rights = ["P", "C"]
    years = [30 / 365, 30 / 365]
    ivs = [0.2, 0.2]
    ois = [1000, 1000]

    flip_from_below = zero_gamma_flip(strikes, rights, years, ivs, ois, spot=97.0)
    flip_from_above = zero_gamma_flip(strikes, rights, years, ivs, ois, spot=103.0)
    assert flip_from_below is not None and 97.0 < flip_from_below < 105.0
    assert flip_from_above is not None and 95.0 < flip_from_above < 103.0
    assert flip_from_below == pytest.approx(flip_from_above, abs=0.02)
    assert abs(net_gex_profile(strikes, rights, years, ivs, ois, [flip_from_above])[0]) < 1e3


def test_zero_gamma_flip_returns_none_without_crossing_or_outside_band():
    assert (
        zero_gamma_flip([95.0, 105.0], ["C", "C"], [0.1, 0.1], [0.2, 0.2], [500, 500], 100.0)
        is None
    )
    assert (
        zero_gamma_flip([40.0, 50.0], ["P", "C"], [0.1, 0.1], [0.2, 0.2], [500, 500], 100.0) is None
    )


def test_zero_gamma_flip_handles_wide_band_and_narrow_zero_dte_spike():
    # band >= 1.0 时 lo_bound 截断到 1e-6，spot 不再是网格中点，val_spot 仍需精确取 spot
    flip = zero_gamma_flip(
        [95.0, 105.0], ["P", "C"], [30 / 365, 30 / 365], [0.2, 0.2], [1000, 1000], 103.0, band=1.5
    )
    assert flip is not None and 95.0 < flip < 103.0

    # 极短到期（4 小时）下 0.2% 间距的窄 Call 尖峰被夹在两侧 Put 之间，网格合入行权价后不漏检
    t_4h = 4.0 / (365 * 24)
    narrow_flip = zero_gamma_flip(
        [100.0, 100.2, 100.4],
        ["P", "C", "P"],
        [t_4h, t_4h, t_4h],
        [0.05, 0.05, 0.05],
        [2000, 2000, 2000],
        spot=100.0,
        steps=21,
    )
    assert narrow_flip is not None and 100.0 < narrow_flip < 100.2


def test_zero_gamma_flip_no_underflow_phantom_root_on_zero_dte():
    # 0DTE 纯 Put 或全负链在高斯深尾部下溢时绝不产生伪 Flip
    t_2h = 2.0 / (365 * 24)
    t_4h = 4.0 / (365 * 24)
    assert zero_gamma_flip([100.0], ["P"], [t_4h], [0.20], [5000], spot=100.0) is None
    assert (
        zero_gamma_flip(
            [490.0, 500.0, 500.0, 510.0, 510.0],
            ["P", "P", "C", "P", "C"],
            [t_2h] * 5,
            [0.12] * 5,
            [5000, 8000, 3000, 4000, 1000],
            spot=500.0,
            band=0.12,
        )
        is None
    )
    # 0DTE 空白带（85P / 125C，spot=100 处普通 exp 下溢为 0）：对数空间求得根 sqrt(85*125)≈103.08
    gap_flip = zero_gamma_flip(
        [85.0, 125.0], ["P", "C"], [t_4h, t_4h], [0.15, 0.15], [1000, 1000], spot=100.0
    )
    assert gap_flip is not None
    assert gap_flip == pytest.approx(103.08, abs=0.05)
