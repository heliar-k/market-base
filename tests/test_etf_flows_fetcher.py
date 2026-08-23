"""Farside ETF 资金流 fetcher 单元测试。"""

import pandas as pd

from src.assets_analysis import crypto_consensus, crypto_radar
from src.fetchers.etf_flows_fetcher import COLUMNS, parse_farside

# 与真实页面一致的列序（Date + 13 值列）；构造最小 3 列样本时下标需对齐 COLUMNS
_SAMPLE = """Bitcoin ETF Flow – All Data (US$m)
Date
IBIT
FBTC
BITB
ARKB
BTCO
EZBC
BRRR
HODL
BTCW
MSBT
GBTC
BTC
Total

11 Jan 2024
111.7
227.0
237.9
65.3
17.4
50.1
29.4
10.6
1.0
-
(95.1)
-
655.3

12 Jan 2024
386.0
195.3
17.4
39.8
28.4
0.0
20.2
0.0
0.0
-
(484.1)
-
203.0

15 Jan 2024
-
0.0
0.0
0.0
0.0
0.0
0.0
0.0
0.0
-
0.0
-
-1.0
"""


def _row(tokens: list[str]) -> list[str]:
    """单行 token 列表 → 按 COLUMNS 位置对齐（Total 在末位）。"""
    out = [None] * len(COLUMNS)
    assert len(tokens) == 4  # date 行 + 3 个值：IBIT/FBTC/Total
    out[COLUMNS.index("IBIT")] = tokens[1]
    out[COLUMNS.index("FBTC")] = tokens[2]
    out[COLUMNS.index("Total")] = tokens[3]
    return out


def test_parse_farside_basic():
    df = parse_farside(_SAMPLE)
    assert list(df.index) == ["2024-01-11", "2024-01-12", "2024-01-15"]
    assert df.loc["2024-01-11", "IBIT"] == 111.7
    assert df.loc["2024-01-11", "Total"] == 655.3
    assert pd.isna(df.loc["2024-01-15", "IBIT"])  # '-' → NaN
    assert df.loc["2024-01-15", "Total"] == -1.0  # 负值


def test_parse_farside_paren_negative():
    df = parse_farside("Date	\nGBTC\n\n11 Jan 2024	\n(95.1)	\n")
    assert df.loc["2024-01-11", "GBTC"] == -95.1


def test_parse_farside_last_row_has_footer():
    """最后一行混入页脚 token：只取前 13 值，不崩。"""
    footer = _SAMPLE + "\nSource: Farside Investors\ninfo@farside.co.uk"
    df = parse_farside(footer)
    assert len(df) == 3
    assert df.loc["2024-01-15", "Total"] == -1.0


def test_etf_signal_scored_when_fresh(monkeypatch, tmp_path):
    """radar：ETF 数据新鲜且 5d 流入 → dir=+1 纳入评分（权重 15）；stale 时不纳入。"""
    df = pd.DataFrame(
        [{c: 100.0 for c in COLUMNS}] * 6,
        index=pd.bdate_range(
            "2026-08-14", periods=6
        ),  # 至 2026-08-21（周五，贴近 Today）
    )
    df.index.name = "date"
    from src import assets_analysis

    out = tmp_path / "data" / "etf_flows"
    out.mkdir(parents=True)
    df.to_csv(out / "etf_flows.csv")

    monkeypatch.setattr(assets_analysis, "ROOT", tmp_path)
    snap = {
        "perp": {
            "BTC": {"funding_annual": 10.0, "funding_rate": 0.0001, "oi_usd": 1e9}
        },
        "options_BTC": {
            "pcr": 0.6,
            "call_wall": 80000,
            "put_wall": 70000,
            "spot_anchor": 77000,
        },
        "cme": {"basis_pct": 1.0, "fut_price": 78000, "spot": 77200},
        "taker": {"BTC": [{"buy": 1.0, "sell": 1.0}] * 5},
    }
    snap["etf"] = assets_analysis._etf_flows()
    assert snap["etf"]["available"] and not snap["etf"]["stale"]
    radar = crypto_radar(snap)
    etf_sig = [s for s in radar["signals"] if s["name"] == "ETF 资金流"][0]
    assert etf_sig["dir"] == 1 and etf_sig["weight"] == 15

    # stale 时不纳入（dir=0）
    snap["etf"]["stale"] = True
    radar = crypto_radar(snap)
    etf_sig = [s for s in radar["signals"] if s["name"] == "ETF 资金流"][0]
    assert etf_sig["dir"] == 0

    # stale 时 consensus 机构侧不投 ETF 票（与 radar dir=0 一致）
    cons = crypto_consensus(snap, radar)
    assert "ETF" not in cons["inst"]["note"]

    # fresh 时机构侧含 ETF 票
    snap["etf"]["stale"] = False
    cons = crypto_consensus(snap, radar)
    assert "ETF" in cons["inst"]["note"]


def test_consensus_inst_split_no_crash():
    """机构票型分裂（CME 偏多 + Spread 偏空）→ 不 KeyError，verdict 为内部分歧。"""
    from src.assets_analysis import crypto_consensus

    snap = {
        "etf": {"available": False},
        "basis": {"spread": -3.0, "ema60": 1.0, "sofr": 4.0},
        "perp": {"BTC": {"funding_annual": 0.1, "funding_rate": 0.0001}},
        "options_BTC": {"pcr": 0.8},
        "taker": {"BTC": [{"buy": 1.0, "sell": 1.0}] * 5},
    }
    cons = crypto_consensus(snap, {"signals": []})
    assert cons["verdict"]  # 不崩
    assert "分歧" in cons["verdict"] or "多空" in cons["verdict"]
