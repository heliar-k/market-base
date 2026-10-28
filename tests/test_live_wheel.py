"""Tests for src.live_wheel（盘前/盘中实时 Wheel）与 wheel_scanner 的链回溯修复。"""

from __future__ import annotations

import pandas as pd

from src.live_wheel import _chain_health, _window_expiries
from src.wheel_scanner import _find_chain_file, _pick_contract


def _raw_chain(**over) -> pd.DataFrame:
    """一份健康的期权链（bid>0 + OI>0 占多数），可按需覆盖。"""
    base = {
        "strike": [195.0, 200.0],
        "right": ["P", "C"],
        "expiration": ["20261120", "20261120"],
        "bid": [4.00, 3.00],
        "ask": [4.20, 3.20],
        "openInterest": [15000, 12000],
        "volume": [500, 400],
        "impliedVolatility": [0.42, 0.40],
    }
    base.update(over)
    return pd.DataFrame(base)


def test_find_chain_file_falls_back_to_previous_day(tmp_path, monkeypatch) -> None:
    """当日链缺失时回溯到最近的前一份真实链，并返回陈旧天数。

    这是「静默退化成 BSM 估算」的根因：直接拼当日文件名找不到就落兜底分支，
    四条选点闸门全失效。回溯后必须如实给出 stale_days。

    文件日期统一使用美东日历日，未来日期文件不参与选点。
    """
    import src.wheel_scanner as ws

    monkeypatch.setattr(ws, "ROOT", tmp_path)
    d = tmp_path / "data" / "options_structure"
    d.mkdir(parents=True)
    (d / "raw_NVDA_20261023.csv").write_text("strike\n1\n")
    (d / "raw_NVDA_20261024.csv").write_text("strike\n1\n")

    from datetime import date

    # 当日 10-26 链不存在 → 取最近的 10-24（间隔 2 天）
    path, stale = _find_chain_file("NVDA", date(2026, 10, 26))
    assert path is not None and path.name == "raw_NVDA_20261024.csv"
    assert stale == 2

    # 当日链存在 → stale=0
    (d / "raw_NVDA_20261026.csv").write_text("strike\n1\n")
    path, stale = _find_chain_file("NVDA", date(2026, 10, 26))
    assert path is not None and path.name == "raw_NVDA_20261026.csv"
    assert stale == 0

    # 未来日期文件不能参与选点；存在旧链时取最近的旧链
    (d / "raw_AAPL_20261026.csv").write_text("strike\n1\n")
    (d / "raw_AAPL_20261028.csv").write_text("strike\n1\n")
    path, stale = _find_chain_file("AAPL", date(2026, 10, 27))
    assert path is not None and path.name == "raw_AAPL_20261026.csv"
    assert stale == 1

    (d / "raw_GOOG_20261029.csv").write_text("strike\n1\n")
    path, stale = _find_chain_file("GOOG", date(2026, 10, 26))
    assert path is None and stale == 0


def test_pick_contract_marks_stale_chain(tmp_path, monkeypatch) -> None:
    """用旧链选出的合约须带 chain_date / stale_days，不得静默充作当日报价。"""
    import src.wheel_scanner as ws

    monkeypatch.setattr(ws, "ROOT", tmp_path)
    d = tmp_path / "data" / "options_structure"
    d.mkdir(parents=True)
    _raw_chain(quote_ts=["2026-10-24T20:00:00Z"] * 2).to_csv(
        d / "raw_NVDA_20261024.csv", index=False
    )

    # 快照日 10-26，链是 10-24 的
    pick = _pick_contract("NVDA", "20261026", {}, 211.0, 42.0, "P", -0.22)
    assert pick is not None
    assert pick.executable is True
    assert pick.chain_date == "2026-10-24"
    assert pick.stale_days == 2
    assert pick.quote_ts == "2026-10-24T20:00:00Z"


def test_pick_contract_fresh_chain_has_zero_stale(tmp_path, monkeypatch) -> None:
    import src.wheel_scanner as ws

    monkeypatch.setattr(ws, "ROOT", tmp_path)
    d = tmp_path / "data" / "options_structure"
    d.mkdir(parents=True)
    _raw_chain().to_csv(d / "raw_NVDA_20261026.csv", index=False)

    pick = _pick_contract("NVDA", "20261026", {}, 211.0, 42.0, "P", -0.22)
    assert pick is not None
    assert pick.chain_date == "2026-10-26"
    assert pick.stale_days == 0


def test_pick_contract_uses_live_chain_override(monkeypatch) -> None:
    import src.wheel_scanner as ws

    def _no_disk(*args):
        raise AssertionError("现场链模式不应读取磁盘 raw 文件")

    monkeypatch.setattr(ws, "_find_chain_file", _no_disk)
    chain = _raw_chain(quote_ts=["2026-10-27T20:05:00Z"] * 2)
    pick = _pick_contract(
        "NVDA",
        "20261027",
        {},
        211.0,
        42.0,
        "P",
        -0.22,
        chain_info=(chain, "2026-10-27"),
    )
    assert pick is not None and pick.executable is True
    assert pick.chain_date == "2026-10-27"
    assert pick.stale_days == 0
    assert pick.quote_ts == "2026-10-27T20:05:00Z"


def test_chain_health_rejects_premarket_garbage() -> None:
    """盘前 yfinance 链：bid 全 0、OI 全 0 —— 必须被判为不可用。

    实测盘前 05:57 ET：bid>0 占 3%、openInterest 全 0、impliedVolatility 报
    0.00%~3%（ATM 真实 44.8%）。用它算出的 iv_curve=1.56%、net_gex=0、
    gamma_flip=None：读数看着像个数，比没有数据更危险。
    """
    bad = _raw_chain(bid=[0.0, 0.0], openInterest=[0, 0])
    ok, why = _chain_health(bad)
    assert ok is False and "盘口未就绪" in why

    good = _raw_chain()
    ok, _ = _chain_health(good)
    assert ok is True

    # 空链
    ok, why = _chain_health(pd.DataFrame())
    assert ok is False and "链为空" in why


def test_chain_health_requires_majority_not_all() -> None:
    """阈值是「多数健康」而非「全部健康」：尾部深 OTM 僵尸档 bid=0 很常见。"""
    half = _raw_chain(
        strike=[1.0, 2.0, 195.0, 200.0],
        right=["P", "C", "P", "C"],
        expiration=["20261120"] * 4,
        bid=[0.0, 0.0, 4.0, 3.0],
        ask=[0.01, 0.01, 4.2, 3.2],
        openInterest=[0, 0, 15000, 12000],
        volume=[0, 0, 500, 400],
        impliedVolatility=[0.5, 0.5, 0.42, 0.40],
    )
    ok, _ = _chain_health(half)
    assert ok is True


def test_window_expiries_bounds() -> None:
    """到期日窗口：5 ~ dte_max+14 天，且过滤无效格式。"""
    from datetime import timedelta

    from src.live_wheel import _now_et

    today = _now_et().date()
    near = (today + timedelta(days=3)).strftime("%Y-%m-%d")
    inside = (today + timedelta(days=30)).strftime("%Y-%m-%d")
    far = (today + timedelta(days=400)).strftime("%Y-%m-%d")
    out = _window_expiries("X", (near, inside, far, "bad-date"))
    assert inside.replace("-", "") in out
    assert near.replace("-", "") not in out
    assert far.replace("-", "") not in out
    assert len(out) == 1


def test_build_opt_map_preserves_previous_chain_quote_and_static_events(monkeypatch) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    import yfinance as yf

    import src.live_wheel as lw

    monkeypatch.setattr(yf, "Ticker", lambda sym: type("T", (), {"options": ()})())
    monkeypatch.setattr(lw, "_window_expiries", lambda sym, options: ["20261120"])
    monkeypatch.setattr(
        lw,
        "_now_et",
        lambda: datetime(2026, 10, 27, 20, 30, tzinfo=ZoneInfo("America/New_York")),
    )
    bad = _raw_chain(bid=[0.0, 0.0], openInterest=[0, 0])
    monkeypatch.setattr(lw, "fetch_yf_chain", lambda sym, exps: bad)
    previous = _raw_chain(quote_ts=["2026-10-26T20:00:00Z"] * 2)
    monkeypatch.setattr(lw, "_prev_trading_chain", lambda sym, static: (previous, "2026-10-26"))
    monkeypatch.setattr(lw, "_extract_events", lambda tk: {})
    static = {
        "NVDA": {
            "earnings_date": "2026-11-18",
            "ex_dividend_date": "2026-12-01",
        }
    }

    opt_map, _, chain_map = lw._build_opt_map(
        ["NVDA"], static, "yfinance", spot_overrides={"NVDA": 211.0}
    )
    chain, chain_date = chain_map["NVDA"]
    assert chain_date == "2026-10-26"
    assert chain["quote_ts"].iloc[0] == "2026-10-26T20:00:00Z"
    assert opt_map["NVDA"]["spot"] == 211.0
    assert opt_map["NVDA"]["earnings_date"] == "2026-11-18"
    assert opt_map["NVDA"]["ex_dividend_date"] == "2026-12-01"


def test_chain_from_ibkr_needs_oi_merged(monkeypatch) -> None:
    """IBKR 只回 Greeks/bid/ask，没有 openInterest —— 必须合 yfinance OI 再用。

    回归：IBKR 的 fetch_options_greeks 返回列里没有 openInterest（那是另一次申请），
    而 compute_structure 按 OI 加权算 GEX/墙位，直接喂会 KeyError: 'openInterest'。
    compute_gex 主路径也是 greeks 与 oi 分开取再 merge，这里对齐同一做法。
    """
    import src.live_wheel as lw

    greeks = pd.DataFrame(
        [
            {
                "expiration": "20261120",
                "strike": 200.0,
                "right": "P",
                "bid": 4.0,
                "ask": 4.2,
                "last": 4.1,
                "volume": 500,
                "delta": -0.22,
                "gamma": -0.004,
                "theta": -0.05,
                "vega": 0.1,
                "iv": 0.42,
            }
        ]
    )
    assert "openInterest" not in greeks.columns

    monkeypatch.setitem(
        __import__("sys").modules,
        "src.compute_gex",
        type(
            "m",
            (),
            {
                "connect_ib": staticmethod(lambda ports=(4001, 4002): ("fake_ib", 4001)),
                "fetch_options_greeks": staticmethod(lambda *a, **k: greeks.copy()),
                "filter_options": staticmethod(lambda chain, spot, n, pct: (["20261120"], [200.0])),
            },
        )(),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "src.fetchers.ibkr_fetcher",
        type(
            "m",
            (),
            {
                "IBKRConnectionError": type("E", (Exception,), {}),
                "get_option_chain_params": staticmethod(
                    lambda ib, sym: [
                        type("C", (), {"expirations": ["20261120"], "strikes": [200.0]})()
                    ]
                ),
            },
        )(),
    )
    monkeypatch.setattr(lw, "fetch_yf_chain", lambda sym, exps: _yf_chain_with_oi())

    out = lw._chain_from_ibkr("NVDA", ["20261120"], 211.0)
    assert out is not None
    assert "openInterest" in out.columns
    assert float(out["openInterest"].iloc[0]) == 15000.0
    # 合并后必须能直接喂给 compute_structure（原崩溃点）
    from src.options_structure import compute_structure

    detail = compute_structure(out, 211.0)
    assert detail.get("spot") is not None


def _yf_chain_with_oi() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "expiration": "20261120",
                "strike": 200.0,
                "right": "P",
                "bid": 4.0,
                "ask": 4.2,
                "last": 4.1,
                "openInterest": 15000,
                "volume": 480,
                "impliedVolatility": 0.42,
            }
        ]
    )


def test_oi_from_ticker_distinguishes_missing_from_zero() -> None:
    """genericTick 101 的 OI：未取到返回 None，真为 0 返回 0 —— 两者不能混。"""
    from src.compute_gex import _oi_from_ticker

    class T:
        callOpenInterest = float("nan")
        putOpenInterest = 15000.0

    assert _oi_from_ticker(T(), "P") == 15000
    assert _oi_from_ticker(T(), "C") is None  # nan = 没取到，不是 0

    class Z:
        callOpenInterest = 0.0
        putOpenInterest = 0.0

    assert _oi_from_ticker(Z(), "C") == 0  # 真为 0
    assert _oi_from_ticker(Z(), "P") == 0


def test_chain_from_ibkr_prefers_ibkr_oi_over_yfinance(monkeypatch) -> None:
    """IBKR 自带 OI 时不得再去合 yfinance —— 盘前外部源的 OI 全为 0。"""
    import sys

    import src.live_wheel as lw

    greeks = pd.DataFrame(
        [
            {
                "expiration": "20261120",
                "strike": 200.0,
                "right": "P",
                "bid": 4.0,
                "ask": 4.2,
                "last": 4.1,
                "volume": 500,
                "delta": -0.22,
                "gamma": -0.004,
                "theta": -0.05,
                "vega": 0.1,
                "iv": 0.42,
                "openInterest": 15000,
            }
        ]
    )
    called = {"yf": False}

    def _spy_yf(sym, exps):
        called["yf"] = True
        return _yf_chain_with_oi()

    monkeypatch.setitem(
        sys.modules,
        "src.compute_gex",
        type(
            "m",
            (),
            {
                "connect_ib": staticmethod(lambda ports=(4001, 4002): ("fake", 4001)),
                "fetch_options_greeks": staticmethod(lambda *a, **k: greeks.copy()),
                "filter_options": staticmethod(lambda chain, spot, n, pct: (["20261120"], [200.0])),
            },
        )(),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.fetchers.ibkr_fetcher",
        type(
            "m",
            (),
            {
                "IBKRConnectionError": type("E", (Exception,), {}),
                "get_option_chain_params": staticmethod(
                    lambda ib, sym: [
                        type("C", (), {"expirations": ["20261120"], "strikes": [200.0]})()
                    ]
                ),
            },
        )(),
    )
    monkeypatch.setattr(lw, "fetch_yf_chain", _spy_yf)

    out = lw._chain_from_ibkr("NVDA", ["20261120"], 211.0)
    assert out is not None
    assert called["yf"] is False  # 完全没碰 yfinance
    assert int(out["openInterest"].iloc[0]) == 15000


def test_rebuild_rows_restores_nested_pick_objects() -> None:
    """asdict 后重建 ScanRow 必须把嵌套的 OptionPick 还原成对象。

    回归：`ScanRow(**asdict_dict)` 会把 put_pick 留成 dict，而 print_report 的
    _fmt_pick 按属性取值（p.expiration），一有 SELL PUT/CALL 就报
    `'dict' object has no attribute 'expiration'`。全池长期都是「暂不操作」
    （put_pick=None），这个分支直到第一次真出现推荐合约才暴露。
    """
    from dataclasses import asdict, fields

    from src.live_wheel import _rebuild_rows
    from src.wheel_scanner import OptionPick, ScanRow, _fmt_pick

    pick = OptionPick(
        right="P",
        expiration="2026-11-20",
        dte=24,
        strike=575.0,
        delta=-0.21,
        iv_pct=40.0,
        oi=1200,
        dist_pct=-7.2,
        annual_roc_pct=14.0,
        chain_date="2026-10-24",
        stale_days=3,
    )
    row = ScanRow(
        **{
            **{f.name: None for f in fields(ScanRow)},
            "symbol": "META",
            "tier": "T1",
            "signal": "SELL PUT",
            "reason": "超卖",
            "actionable": True,
            "put_pick": pick,
        }
    )

    raw = asdict(row)
    assert isinstance(raw["put_pick"], dict)  # asdict 确实把它变成了 dict

    (rebuilt,) = _rebuild_rows([raw])
    assert isinstance(rebuilt.put_pick, OptionPick)
    # 这一步就是 _fmt_pick 内部会炸的地方
    assert "$575P" in _fmt_pick(rebuilt.put_pick)
