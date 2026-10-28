"""期权结构快照聚合逻辑测试（Gamma Flip 最近穿越选择）。

回归背景：旧逻辑只找现价下方第一个累计 GEX 负转正穿越点，①翻转点在现价
上方时漏检（REGIME 卡回退 net_gex 符号后与分布图矛盾）②深 ITM 噪声穿越
被误当翻转点。新逻辑取离现价最近的穿越点（上下皆可），保证
sign(flip_dist) == sign(现价处累计 GEX)。
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from src.options_structure import compute_structure

EXP = (datetime.now() + timedelta(days=30)).strftime("%Y%m%d")


def _chain(legs: list[tuple[float, str, int]]) -> pd.DataFrame:
    """legs: (strike, right, oi) → 单到期日合成期权链（IV 0.2）。"""
    return pd.DataFrame(
        [
            {
                "strike": strike,
                "right": right,
                "expiration": EXP,
                "openInterest": oi,
                "volume": 0,
                "impliedVolatility": 0.2,
            }
            for strike, right, oi in legs
        ]
    )


def test_flip_nearest_crossing_above_spot():
    """现价处累计为负、穿越点在现价上方 → flip 取上方穿越（旧逻辑漏检返回 None）。"""
    out = compute_structure(
        _chain(
            [
                (90.0, "P", 50000),
                (95.0, "P", 50000),
                (100.0, "P", 20000),
                (105.0, "C", 90000),
                (110.0, "C", 10000),
            ]
        ),
        100.0,
    )
    assert out["gamma_flip"] == 105.0
    assert out["flip_dist"] < 0  # 现价在 Flip 下方 → 局部负 Gamma


def test_flip_nearest_of_multiple():
    """多个穿越点取离现价最近的（旧逻辑取第一个，被深 ITM 噪声带偏）。"""
    out = compute_structure(
        _chain(
            [
                (80.0, "P", 80000),  # 深负
                (84.0, "C", 60000),  # 第一次上穿（噪声位，旧逻辑取这里）
                (92.0, "P", 50000),  # 下穿
                (96.0, "C", 50000),  # 上穿 —— 离现价最近
            ]
        ),
        100.0,
    )
    assert out["gamma_flip"] == 96.0
    assert out["flip_dist"] > 0  # 现价在 Flip 上方 → 局部正 Gamma


def test_flip_none_when_no_crossing():
    """全链累计恒为正（无穿越）→ flip/flip_dist 均为 None。"""
    out = compute_structure(
        _chain(
            [
                (95.0, "C", 20000),
                (100.0, "C", 50000),
                (105.0, "C", 20000),
            ]
        ),
        100.0,
    )
    assert out["gamma_flip"] is None
    assert out["flip_dist"] is None


def test_flip_ignores_deep_itm_junk_outside_band():
    """回归：高价新标的链含 $5 级深 ITM 存量（如 MU 20260911 flip=105 @ spot=977）。
    带外（现价 ±50%~200% 之外）的噪声穿越不得成为 flip。"""
    out = compute_structure(
        _chain(
            [
                (10.0, "P", 200000),  # 带外深 ITM：巨量 put 拉负
                (20.0, "C", 200000),  # 带外噪声穿越（旧逻辑会返回 20）
                (60.0, "C", 30000),  # 带内累计回正，此后无负→正穿越
                (100.0, "C", 30000),
            ]
        ),
        100.0,
    )
    assert out["gamma_flip"] is None


def test_xnys_calendar_excludes_weekend_and_market_holiday():
    import src.options_structure as os

    assert os._is_session(date(2026, 10, 23)) is True
    assert os._is_session(date(2026, 10, 24)) is False
    assert os._is_session(date(2026, 9, 7)) is False
    assert os._next_session(date(2026, 10, 23)) == date(2026, 10, 26)


def test_expired_zero_dte_is_excluded_after_close(monkeypatch):
    import src.options_structure as os

    monkeypatch.setattr(
        os,
        "_now_et",
        lambda: datetime(2026, 10, 27, 20, 29, tzinfo=ZoneInfo("America/New_York")),
    )
    rows = [
        {
            "strike": 100.0,
            "right": "C",
            "expiration": exp,
            "openInterest": 10,
            "impliedVolatility": 0.2,
            "volume": 0,
        }
        for exp in ("20261027", "20261028", "20261029")
    ]
    out = compute_structure(pd.DataFrame(rows), 100.0)
    assert [p["dte"] for p in out["iv_curve"]] == [1, 2]
    assert out["buckets"]["0DTE"]["contracts"] == 0


def test_atm_iv_averages_call_and_put_at_same_strike():
    rows = [
        {
            "strike": 100.0,
            "right": right,
            "expiration": EXP,
            "openInterest": 100,
            "impliedVolatility": iv,
            "volume": 0,
        }
        for right, iv in (("C", 0.2), ("P", 0.4))
    ]
    out = compute_structure(pd.DataFrame(rows), 100.0)
    assert out["iv_front"]["iv"] == 30.0


def test_skew_uses_closest_25_delta_call_and_put():
    rows = [
        {
            "strike": 94.0,
            "right": "P",
            "expiration": EXP,
            "openInterest": 100,
            "impliedVolatility": 0.4,
            "volume": 0,
        },
        {
            "strike": 93.0,
            "right": "C",
            "expiration": EXP,
            "openInterest": 100,
            "impliedVolatility": 0.6,
            "volume": 0,
        },
        {
            "strike": 105.0,
            "right": "C",
            "expiration": EXP,
            "openInterest": 100,
            "impliedVolatility": 0.25,
            "volume": 0,
        },
        {
            "strike": 105.0,
            "right": "C",
            "expiration": EXP,
            "openInterest": 100,
            "impliedVolatility": 0.27,
            "volume": 0,
        },
    ]
    out = compute_structure(pd.DataFrame(rows), 100.0)
    assert out["skew_put_iv"] == 40.0
    assert out["skew_call_iv"] == 25.0
    assert out["skew"] == 15.0


def test_fetch_chain_rejects_premarket_garbage_without_caching(monkeypatch, tmp_path):
    import yfinance as yf

    import src.options_structure as os

    monkeypatch.setattr(os, "DATA", tmp_path)
    expiration = "20261120"
    raw = tmp_path / f"raw_NVDA_{os._now_et():%Y%m%d}.csv"
    bad = pd.DataFrame(
        {
            "strike": [200.0],
            "bid": [0.0],
            "ask": [0.01],
            "openInterest": [0],
            "volume": [0],
            "impliedVolatility": [0.001],
        }
    )
    cached = bad.copy()
    cached["right"] = "P"
    cached["expiration"] = expiration
    cached.to_csv(raw, index=False)
    chain = type("Chain", (), {"calls": bad, "puts": bad.copy()})()
    ticker = type("Ticker", (), {"option_chain": lambda self, exp: chain})()
    monkeypatch.setattr(yf, "Ticker", lambda symbol: ticker)

    out = os.fetch_chain("NVDA", [expiration])
    assert out.empty
    assert not raw.exists()


def test_main_merges_partial_full_run_with_existing_snapshot(monkeypatch, tmp_path):
    import json
    import sys

    import src.options_structure as os

    monkeypatch.setattr(os, "DATA", tmp_path)
    monkeypatch.setattr(
        os,
        "_now_et",
        lambda: datetime(2026, 10, 27, 20, 29, tzinfo=ZoneInfo("America/New_York")),
    )
    path = tmp_path / "20261027.json"
    path.write_text(
        json.dumps({"date": "old", "symbols": {"MSFT": {"symbol": "MSFT", "oi_final": False}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        os,
        "build_snapshot",
        lambda symbols, use_cache: {
            "date": "new",
            "snapshot_date": "2026-10-27",
            "symbols": {"NVDA": {"symbol": "NVDA", "oi_final": True}},
            "errors": {},
        },
    )
    monkeypatch.setattr(sys, "argv", ["options_structure", "--symbols", "NVDA"])

    os.main()
    out = json.loads(path.read_text(encoding="utf-8"))
    assert set(out["symbols"]) == {"MSFT", "NVDA"}
    assert out["date"] == "new"
    assert out["snapshot_date"] == "2026-10-27"
    assert out["oi_final"] is False


def test_wall_exports_full_cumulative_gex():
    out = compute_structure(
        _chain(
            [
                (80.0, "P", 80000),
                (84.0, "C", 60000),
                (92.0, "P", 50000),
                (96.0, "C", 50000),
            ]
        ),
        100.0,
    )
    prev = 0.0
    crossings = []
    for row in out["wall"]:
        cum = row["cum_gex_m"]
        if prev < 0 <= cum:
            crossings.append(row["strike"])
        prev = cum
    assert 96.0 in crossings


def test_oi_top_uses_total_open_interest():
    rows = [
        {
            "strike": float(strike),
            "right": "C",
            "expiration": EXP,
            "openInterest": 2000 - i,
            "volume": 0,
            "impliedVolatility": 0.2,
        }
        for i, strike in enumerate(range(90, 111))
    ]
    rows.append(
        {
            "strike": 500.0,
            "right": "P",
            "expiration": EXP,
            "openInterest": 999999,
            "volume": 0,
            "impliedVolatility": 0.2,
        }
    )
    out = compute_structure(pd.DataFrame(rows), 100.0)
    assert len(out["oi_top"]) == 20
    assert out["oi_top"][0]["strike"] == 90.0
    assert 110.0 not in {row["strike"] for row in out["oi_top"]}
    assert 500.0 not in {row["strike"] for row in out["oi_top"]}


def test_raw_retention_keeps_latest_three_actual_dates_and_pending(monkeypatch, tmp_path):
    import sys

    import src.options_structure as os

    monkeypatch.setattr(os, "DATA", tmp_path)
    monkeypatch.setattr(
        os,
        "_now_et",
        lambda: datetime(2026, 10, 26, 8, 0, tzinfo=ZoneInfo("America/New_York")),
    )
    monkeypatch.setattr(
        os,
        "build_snapshot",
        lambda symbols, use_cache: {
            "date": "new",
            "snapshot_date": "2026-10-26",
            "symbols": {"NVDA": {"symbol": "NVDA"}},
        },
    )
    for stamp in ("20261021", "20261022", "20261023", "20261026", "20261027"):
        (tmp_path / f"raw_NVDA_{stamp}.csv").write_text("strike\n100\n")
    (tmp_path / "20261021.json").write_text('{"oi_final":false}')
    monkeypatch.setattr(sys, "argv", ["options_structure", "--symbols", "NVDA"])

    os.main()

    assert (tmp_path / "raw_NVDA_20261022.csv").exists()
    assert (tmp_path / "raw_NVDA_20261023.csv").exists()
    assert (tmp_path / "raw_NVDA_20261026.csv").exists()
    assert (tmp_path / "raw_NVDA_20261021.csv").exists()
    assert not (tmp_path / "raw_NVDA_20261027.csv").exists()


def test_build_snapshot_uses_previous_session_after_midnight(monkeypatch, tmp_path):
    import json

    import yfinance as yf

    import src.options_structure as os

    monkeypatch.setattr(os, "DATA", tmp_path)
    (tmp_path / "20261027.json").write_text(
        json.dumps({"symbols": {"NVDA": {"spot": 95.0}}}), encoding="utf-8"
    )
    (tmp_path / "raw_NVDA_20261027.csv").write_text("expiration\n20261120\n")
    monkeypatch.setattr(
        os,
        "_now_et",
        lambda: datetime(2026, 10, 28, 1, 0, tzinfo=ZoneInfo("America/New_York")),
    )

    class Ticker:
        fast_info = type("FastInfo", (), {"last_price": 100.0})()
        calendar = {}

        @property
        def options(self):
            raise AssertionError("历史快照不应读取当前 ticker.options")

    ticker = Ticker()
    monkeypatch.setattr(yf, "Ticker", lambda symbol: ticker)
    monkeypatch.setattr(os, "_fetch_barchart_iv_ranks", lambda symbols: {})
    captured = {}
    chain = pd.DataFrame(
        [
            {
                "expiration": "20261120",
                "strike": 100.0,
                "right": "C",
                "openInterest": 100,
                "volume": 10,
                "impliedVolatility": 0.2,
                "quote_ts": "2026-10-28T01:00:00Z",
            }
        ]
    )

    def _fetch(symbol, expirations, **kwargs):
        captured.update(kwargs)
        return chain

    monkeypatch.setattr(os, "fetch_chain", _fetch)
    out = os.build_snapshot(["NVDA"], use_cache=True)
    assert out["snapshot_date"] == "2026-10-27"
    assert out["symbols"]["NVDA"]["spot"] == 95.0
    assert out["symbols"]["NVDA"]["iv_front"]["dte"] == 24
    assert captured["chain_date"] == date(2026, 10, 27)


def test_main_writes_snapshot_for_target_session_not_current_date(monkeypatch, tmp_path):
    import sys

    import src.options_structure as os

    monkeypatch.setattr(os, "DATA", tmp_path)
    monkeypatch.setattr(
        os,
        "_now_et",
        lambda: datetime(2026, 10, 28, 1, 0, tzinfo=ZoneInfo("America/New_York")),
    )
    monkeypatch.setattr(
        os,
        "build_snapshot",
        lambda symbols, use_cache: {
            "date": "new",
            "snapshot_date": "2026-10-27",
            "oi_final": False,
            "symbols": {"NVDA": {"symbol": "NVDA"}},
            "errors": {},
        },
    )
    monkeypatch.setattr(sys, "argv", ["options_structure", "--symbols", "NVDA"])
    os.main()
    assert (tmp_path / "20261027.json").exists()
    assert not (tmp_path / "20261028.json").exists()


def test_finalize_oi_waits_for_next_session(monkeypatch, tmp_path):
    import json

    import src.options_structure as os

    monkeypatch.setattr(os, "DATA", tmp_path)
    monkeypatch.setattr(
        os,
        "_now_et",
        lambda: datetime(2026, 10, 28, 11, 0, tzinfo=ZoneInfo("America/New_York")),
    )
    monkeypatch.setattr(os, "_is_session", lambda day: False)
    (tmp_path / "20261027.json").write_text(
        json.dumps(
            {
                "snapshot_date": "2026-10-27",
                "oi_final": False,
                "symbols": {"NVDA": {"spot": 100.0}},
            }
        ),
        encoding="utf-8",
    )
    assert os.finalize_oi_snapshot() is None


def test_finalize_oi_rejects_expiration_with_zero_oi(monkeypatch, tmp_path):
    import json

    import src.options_structure as os

    monkeypatch.setattr(os, "DATA", tmp_path)
    monkeypatch.setattr(
        os,
        "_now_et",
        lambda: datetime(2026, 10, 28, 11, 0, tzinfo=ZoneInfo("America/New_York")),
    )
    snapshot = {
        "snapshot_date": "2026-10-27",
        "oi_final": False,
        "symbols": {"NVDA": {"spot": 100.0}},
    }
    path = tmp_path / "20261027.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    raw = pd.DataFrame(
        [
            {
                "expiration": expiration,
                "strike": 100.0,
                "right": "C",
                "bid": 3.0,
                "ask": 3.2,
                "openInterest": 10,
                "volume": 5,
                "impliedVolatility": 0.2,
            }
            for expiration in ("20261120", "20261127")
        ]
    )
    raw.to_csv(tmp_path / "raw_NVDA_20261027.csv", index=False)
    current = raw.assign(openInterest=[100, 0])
    monkeypatch.setattr(os, "fetch_yf_chain", lambda symbol, expirations: current)
    with pytest.raises(RuntimeError, match="20261127"):
        os.finalize_oi_snapshot("20261027")
    assert json.loads(path.read_text(encoding="utf-8")) == snapshot


def test_finalize_oi_rejects_later_session_oi(monkeypatch, tmp_path):
    import json

    import src.options_structure as os

    monkeypatch.setattr(os, "DATA", tmp_path)
    monkeypatch.setattr(
        os,
        "_now_et",
        lambda: datetime(2026, 10, 28, 11, 0, tzinfo=ZoneInfo("America/New_York")),
    )
    path = tmp_path / "20261026.json"
    original = {
        "snapshot_date": "2026-10-26",
        "oi_final": False,
        "symbols": {"NVDA": {"spot": 100.0}},
    }
    path.write_text(json.dumps(original), encoding="utf-8")

    def _no_fetch(symbol, expirations):
        raise AssertionError("错过下一交易日后不得读取后续 OI")

    monkeypatch.setattr(os, "fetch_yf_chain", _no_fetch)
    with pytest.raises(RuntimeError, match="错过"):
        os.finalize_oi_snapshot("20261026")
    assert json.loads(path.read_text(encoding="utf-8")) == original


def test_finalize_oi_waits_for_publication_time(monkeypatch, tmp_path):
    import json

    import src.options_structure as os

    monkeypatch.setattr(os, "DATA", tmp_path)
    monkeypatch.setattr(
        os,
        "_now_et",
        lambda: datetime(2026, 10, 28, 9, 0, tzinfo=ZoneInfo("America/New_York")),
    )
    (tmp_path / "20261027.json").write_text(
        json.dumps(
            {
                "snapshot_date": "2026-10-27",
                "oi_final": False,
                "symbols": {"NVDA": {"spot": 100.0}},
            }
        ),
        encoding="utf-8",
    )
    assert os.finalize_oi_snapshot() is None


def test_finalize_oi_ignores_final_snapshot_with_cleaned_raw(monkeypatch, tmp_path):
    import json

    import src.options_structure as os

    monkeypatch.setattr(os, "DATA", tmp_path)
    monkeypatch.setattr(
        os,
        "_now_et",
        lambda: datetime(2026, 10, 28, 11, 0, tzinfo=ZoneInfo("America/New_York")),
    )
    (tmp_path / "20261027.json").write_text(
        json.dumps(
            {
                "snapshot_date": "2026-10-27",
                "oi_final": True,
                "symbols": {"NVDA": {"spot": 100.0}},
            }
        ),
        encoding="utf-8",
    )
    assert os.finalize_oi_snapshot("20261027") is None


def test_barchart_health_does_not_require_yahoo_iv_column():
    import src.options_structure as os

    chain = pd.DataFrame(
        {
            "expiration": ["20261120", "20261120"],
            "openInterest": [100, 200],
            "iv": [0.2, 0.3],
        }
    )
    assert os.chain_health(chain, require_iv=False)[0] is True


def test_finalize_oi_selects_oldest_pending_snapshot(monkeypatch, tmp_path):
    import json

    import src.options_structure as os

    monkeypatch.setattr(os, "DATA", tmp_path)
    monkeypatch.setattr(
        os,
        "_now_et",
        lambda: datetime(2026, 10, 28, 11, 0, tzinfo=ZoneInfo("America/New_York")),
    )
    for day in ("20261026", "20261027"):
        (tmp_path / f"{day}.json").write_text(
            json.dumps(
                {
                    "snapshot_date": f"{day[:4]}-{day[4:6]}-{day[6:]}",
                    "oi_final": False,
                    "symbols": {"NVDA": {"spot": 100.0}},
                }
            ),
            encoding="utf-8",
        )
    with pytest.raises(RuntimeError, match="20261026"):
        os.finalize_oi_snapshot()


def test_write_snapshot_replace_failure_preserves_previous_file(monkeypatch, tmp_path):
    from pathlib import Path

    import src.options_structure as os

    path = tmp_path / "20261027.json"
    path.write_text('{"old":true}', encoding="utf-8")

    def _fail_replace(self, target):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", _fail_replace)
    with pytest.raises(OSError, match="disk full"):
        os._write_snapshot(path, {"new": True})
    assert path.read_text(encoding="utf-8") == '{"old":true}'


def test_main_skips_weekend_without_calling_build(monkeypatch, tmp_path):
    import sys

    import src.options_structure as os

    monkeypatch.setattr(os, "DATA", tmp_path)
    monkeypatch.setattr(
        os,
        "_now_et",
        lambda: datetime(2026, 10, 31, 17, 0, tzinfo=ZoneInfo("America/New_York")),
    )
    monkeypatch.setattr(sys, "argv", ["options_structure"])

    def _forbidden(symbols, use_cache):
        raise AssertionError("周末不应生成收盘快照")

    monkeypatch.setattr(os, "build_snapshot", _forbidden)
    os.main()


def test_zero_dte_uses_actual_remaining_time(monkeypatch):
    import src.options_structure as os

    rows = [
        {
            "strike": 100.0,
            "right": "C",
            "expiration": "20261027",
            "openInterest": 1000,
            "impliedVolatility": 0.2,
            "volume": 0,
        }
    ]
    monkeypatch.setattr(
        os,
        "_now_et",
        lambda: datetime(2026, 10, 27, 10, 0, tzinfo=ZoneInfo("America/New_York")),
    )
    morning = compute_structure(pd.DataFrame(rows), 100.0)
    monkeypatch.setattr(
        os,
        "_now_et",
        lambda: datetime(2026, 10, 27, 15, 50, tzinfo=ZoneInfo("America/New_York")),
    )
    near_close = compute_structure(pd.DataFrame(rows), 100.0)
    assert near_close["net_gex"] > morning["net_gex"]


def test_structure_health_does_not_require_bid_coverage():
    import src.options_structure as os

    chain = pd.DataFrame(
        {
            "expiration": ["20261120", "20261120"],
            "bid": [0.0, 0.0],
            "openInterest": [100, 200],
            "impliedVolatility": [0.2, 0.3],
        }
    )
    assert os.chain_health(chain)[0] is True
    assert os.chain_health(chain.assign(openInterest=[0, 0]))[0] is False
    assert os.chain_health(chain.assign(impliedVolatility=[0.001, 0.001]))[0] is False


def test_chain_health_requires_every_requested_expiration():
    import src.options_structure as os

    chain = pd.DataFrame(
        {
            "expiration": ["20261120", "20261120"],
            "openInterest": [100, 200],
            "impliedVolatility": [0.2, 0.3],
        }
    )
    ok, why = os.chain_health(chain, expected_expirations=["20261120", "20261127"])
    assert ok is False
    assert "20261127" in why


def test_chain_health_rejects_expiration_without_valid_iv():
    import src.options_structure as os

    chain = pd.DataFrame(
        {
            "expiration": ["20261120", "20261127"],
            "openInterest": [100, 200],
            "impliedVolatility": [0.2, 0.001],
        }
    )
    ok, why = os.chain_health(chain)
    assert ok is False
    assert "20261127" in why


def test_fetch_chain_rejects_mismatched_final_oi_date(monkeypatch, tmp_path):
    import src.options_structure as os

    monkeypatch.setattr(os, "DATA", tmp_path)
    monkeypatch.setattr(
        os,
        "_now_et",
        lambda: datetime(2026, 10, 28, 11, 0, tzinfo=ZoneInfo("America/New_York")),
    )
    raw = tmp_path / "raw_NVDA_20261027.csv"
    pd.DataFrame(
        [
            {
                "expiration": "20261120",
                "strike": 100.0,
                "right": "C",
                "openInterest": 100,
                "impliedVolatility": 0.2,
                "oi_final": True,
                "oi_date": "2026-10-26",
            }
        ]
    ).to_csv(raw, index=False)
    with pytest.raises(RuntimeError, match="OI 日期"):
        os.fetch_chain("NVDA", ["20261120"], chain_date=date(2026, 10, 27))


def test_fetch_chain_rejects_partial_expirations_without_caching(monkeypatch, tmp_path):
    import yfinance as yf

    import src.options_structure as os

    monkeypatch.setattr(os, "DATA", tmp_path)
    good = pd.DataFrame(
        {
            "strike": [100.0],
            "bid": [2.0],
            "ask": [2.2],
            "openInterest": [100],
            "volume": [10],
            "impliedVolatility": [0.2],
        }
    )

    class Ticker:
        def option_chain(self, expiration):
            if expiration == "2026-11-27":
                raise RuntimeError("missing expiry")
            return type("Chain", (), {"calls": good, "puts": good.copy()})()

    monkeypatch.setattr(yf, "Ticker", lambda symbol: Ticker())
    out = os.fetch_chain("NVDA", ["20261120", "20261127"], use_cache=False)
    assert out.empty
    assert not (tmp_path / f"raw_NVDA_{os._now_et():%Y%m%d}.csv").exists()


def test_full_run_failure_preserves_previous_snapshot(monkeypatch, tmp_path):
    import json
    import sys

    import src.options_structure as os

    monkeypatch.setattr(os, "DATA", tmp_path)
    monkeypatch.setattr(
        os,
        "_now_et",
        lambda: datetime(2026, 10, 27, 20, 29, tzinfo=ZoneInfo("America/New_York")),
    )
    path = tmp_path / "20261027.json"
    original = {"date": "old", "symbols": {"MSFT": {"symbol": "MSFT"}}}
    path.write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr(
        os,
        "build_snapshot",
        lambda symbols, use_cache: {
            "date": "new",
            "snapshot_date": "2026-10-27",
            "symbols": {"NVDA": {"symbol": "NVDA"}},
            "errors": {"AAPL": "boom"},
        },
    )
    monkeypatch.setattr(sys, "argv", ["options_structure"])
    with pytest.raises(SystemExit):
        os.main()
    assert json.loads(path.read_text(encoding="utf-8")) == original


def test_finalize_oi_preserves_eod_quotes_and_excludes_expired(monkeypatch, tmp_path):
    import json

    import src.options_structure as os

    monkeypatch.setattr(os, "DATA", tmp_path)
    monkeypatch.setattr(
        os,
        "_now_et",
        lambda: datetime(2026, 10, 28, 11, 0, tzinfo=ZoneInfo("America/New_York")),
    )
    quote_ts = "2026-10-27T21:19:00Z"
    snapshot = {
        "date": quote_ts,
        "snapshot_date": "2026-10-27",
        "oi_final": False,
        "symbols": {"NVDA": {"symbol": "NVDA", "spot": 100.0, "quote_ts": quote_ts}},
    }
    path = tmp_path / "20261027.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    raw = pd.DataFrame(
        [
            {
                "expiration": "20261027",
                "strike": 100.0,
                "right": "C",
                "bid": 3.0,
                "ask": 3.2,
                "openInterest": 10,
                "volume": 5,
                "impliedVolatility": 0.2,
                "quote_ts": quote_ts,
            },
            {
                "expiration": "20261120",
                "strike": 100.0,
                "right": "C",
                "bid": 3.0,
                "ask": 3.2,
                "openInterest": 10,
                "volume": 5,
                "impliedVolatility": 0.2,
                "quote_ts": quote_ts,
            },
        ]
    )
    raw_path = tmp_path / "raw_NVDA_20261027.csv"
    raw.to_csv(raw_path, index=False)
    current = pd.DataFrame(
        [
            {
                "expiration": "20261120",
                "strike": 100.0,
                "right": "C",
                "bid": 99.0,
                "ask": 100.0,
                "openInterest": 999,
                "volume": 999,
                "impliedVolatility": 0.9,
            }
        ]
    )
    monkeypatch.setattr(os, "fetch_yf_chain", lambda symbol, expirations: current)

    result = os.finalize_oi_snapshot()

    assert result["oi_final"] is True
    assert result["oi_date"] == "2026-10-27"
    assert result["date"] == quote_ts
    detail = result["symbols"]["NVDA"]
    assert detail["spot"] == 100.0
    assert detail["oi_coverage"] == 50.0
    assert detail["oi_excluded_expired"] == 1
    updated = pd.read_csv(raw_path, dtype={"expiration": str})
    assert updated["expiration"].tolist() == ["20261120"]
    row = updated.iloc[0]
    assert row["openInterest"] == 999
    assert row["bid"] == 3.0
    assert row["impliedVolatility"] == 0.2
    assert row["volume"] == 5
    assert row["quote_ts"] == quote_ts
    assert row["oi_date"] == "2026-10-27"

    # 模拟 raw 已写入而 JSON 尚未更新的中断：重试不得重复取数或丢失排除口径。
    crashed = json.loads(path.read_text(encoding="utf-8"))
    crashed["oi_final"] = False
    path.write_text(json.dumps(crashed), encoding="utf-8")

    def _no_fetch(symbol, expirations):
        raise AssertionError("已回补 raw 在重试时不应重复取数")

    monkeypatch.setattr(os, "fetch_yf_chain", _no_fetch)
    retried = os.finalize_oi_snapshot()
    assert retried["symbols"]["NVDA"]["oi_coverage"] == 50.0
    assert retried["symbols"]["NVDA"]["oi_excluded_expired"] == 1
    assert retried["symbols"]["NVDA"]["oi_quote_ts"] == result["symbols"]["NVDA"]["oi_quote_ts"]


def test_finalize_oi_after_close_keeps_contract_valid_at_snapshot(monkeypatch, tmp_path):
    import json

    import src.options_structure as os

    monkeypatch.setattr(os, "DATA", tmp_path)
    monkeypatch.setattr(
        os,
        "_now_et",
        lambda: datetime(2026, 10, 28, 17, 0, tzinfo=ZoneInfo("America/New_York")),
    )
    snapshot = {
        "date": "old",
        "snapshot_date": "2026-10-27",
        "oi_final": False,
        "symbols": {"NVDA": {"symbol": "NVDA", "spot": 100.0}},
    }
    path = tmp_path / "20261027.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    raw = pd.DataFrame(
        [
            {
                "expiration": "20261028",
                "strike": 100.0,
                "right": "C",
                "bid": 3.0,
                "ask": 3.2,
                "openInterest": 10,
                "volume": 5,
                "impliedVolatility": 0.2,
            }
        ]
    )
    raw_path = tmp_path / "raw_NVDA_20261027.csv"
    raw.to_csv(raw_path, index=False)
    current = raw.assign(openInterest=999)
    monkeypatch.setattr(os, "fetch_yf_chain", lambda symbol, expirations: current)

    result = os.finalize_oi_snapshot()

    assert result["symbols"]["NVDA"]["iv_front"]["dte"] == 1
    updated = pd.read_csv(raw_path, dtype={"expiration": str})
    assert updated["expiration"].tolist() == ["20261028"]
    assert updated["openInterest"].tolist() == [999]


@pytest.mark.parametrize("missing_contract", [True, False])
def test_finalize_oi_rejects_incomplete_contract_coverage(monkeypatch, tmp_path, missing_contract):
    import json

    import src.options_structure as os

    monkeypatch.setattr(os, "DATA", tmp_path)
    monkeypatch.setattr(
        os,
        "_now_et",
        lambda: datetime(2026, 10, 28, 11, 0, tzinfo=ZoneInfo("America/New_York")),
    )
    snapshot = {
        "date": "old",
        "snapshot_date": "2026-10-27",
        "oi_final": False,
        "symbols": {"NVDA": {"symbol": "NVDA", "spot": 100.0}},
    }
    path = tmp_path / "20261027.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    raw = pd.DataFrame(
        [
            {
                "expiration": "20261120",
                "strike": 100.0,
                "right": "C",
                "bid": 3.0,
                "ask": 3.2,
                "openInterest": 10,
                "volume": 5,
                "impliedVolatility": 0.2,
            }
        ]
    )
    raw_path = tmp_path / "raw_NVDA_20261027.csv"
    raw.to_csv(raw_path, index=False)
    current = pd.DataFrame(columns=raw.columns) if missing_contract else raw.assign(openInterest=0)
    monkeypatch.setattr(os, "fetch_yf_chain", lambda symbol, expirations: current)
    with pytest.raises(RuntimeError, match="OI 回补不完整"):
        os.finalize_oi_snapshot()
    assert json.loads(path.read_text(encoding="utf-8")) == snapshot
    assert pd.read_csv(raw_path, dtype={"expiration": str}).equals(raw)
