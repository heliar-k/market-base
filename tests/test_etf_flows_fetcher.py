"""Farside ETF 资金流 fetcher 单元测试。"""

import pandas as pd
import pytest

from src.assets_analysis import crypto_consensus, crypto_radar
from src.fetchers.etf_flows_fetcher import COLUMNS, parse_farside

# 与真实页面一致的表结构：表头行 + 每日一行 + 页尾 Total 累计行；列序即 COLUMNS
_HEAD = "<tr><th>Date</th>" + "".join(f"<th>{c}</th>" for c in COLUMNS) + "</tr>"
_TOTAL = "<tr><td>Total</td>" + "".join("<td>63,370</td>" for _ in COLUMNS) + "</tr>"


def _row(date: str, **vals: str) -> str:
    """一行；未给的列填 '-'（真实页面里缺失值就是 '-'）。"""
    cells = ["<td>-</td>" if c not in vals else f"<td>{vals[c]}</td>" for c in COLUMNS]
    return f"<tr><td>{date}</td>{''.join(cells)}</tr>"


_SAMPLE = (
    "<html><body><table>\n"
    + _HEAD
    + "\n"
    + _row("11 Jan 2024", IBIT="111.7", FBTC="227.0", GBTC="(95.1)", Total="655.3")
    + "\n"
    + _row("12 Jan 2024", IBIT="386.0", FBTC="195.3", GBTC="(484.1)", Total="203.0")
    + "\n"
    + _row("15 Jan 2024", FBTC="0.0", Total="-1.0")
    + "\n"
    + _TOTAL
    + "\n</table></body></html>"
)


def test_parse_farside_basic():
    df = parse_farside(_SAMPLE)
    assert list(df.index) == ["2024-01-11", "2024-01-12", "2024-01-15"]
    assert df.loc["2024-01-11", "IBIT"] == 111.7
    assert df.loc["2024-01-11", "Total"] == 655.3
    assert pd.isna(df.loc["2024-01-15", "IBIT"])  # '-' → NaN
    assert df.loc["2024-01-15", "Total"] == -1.0  # 负值


def test_parse_farside_paren_negative():
    html = "<table>" + _HEAD + _row("11 Jan 2024", GBTC="(95.1)", Total="(1.0)") + "</table>"
    df = parse_farside(html)
    assert df.loc["2024-01-11", "GBTC"] == -95.1
    assert df.loc["2024-01-11", "Total"] == -1.0


def test_parse_farside_skips_total_row():
    """页尾 Total 累计行首列不是日期（63,370 是累计持仓，不是当日净流入）→ 不进结果。"""
    df = parse_farside(_SAMPLE)
    assert len(df) == 3


def test_parse_farside_header_mismatch_raises():
    """表头与内置列序不一致 → 报错（按位置取值会把邻居列写进 Total）。"""
    html = (
        "<table><tr><td>Date</td><td>IBIT</td><td>NEWFUND</td><td>Total</td></tr>"
        "<tr><td>11 Jan 2024</td><td>1.0</td><td>2.0</td><td>3.0</td></tr></table>"
    )
    with pytest.raises(RuntimeError, match="表头与预期不一致"):
        parse_farside(html)


def test_parse_farside_duplicate_dates_keep_last():
    html = "<table>" + _HEAD + _row("11 Jan 2024", IBIT="1.0") + _row("11 Jan 2024", IBIT="2.0")
    df = parse_farside(html + "</table>")
    assert len(df) == 1
    assert df.loc["2024-01-11", "IBIT"] == 2.0


def test_fetch_flows_raises_when_page_has_no_table(monkeypatch):
    """拿到的是拦截页（无表）→ 抛错，不静默写空。"""
    import src.fetchers.etf_flows_fetcher as mod

    class _Resp:
        text = "<html>blocked</html>"

        def raise_for_status(self):
            pass

    class _Session:
        def __init__(self, **kw):
            pass

        def get(self, url):
            return _Resp()

    monkeypatch.setattr(mod.cffi_requests, "Session", _Session)
    with pytest.raises(RuntimeError, match="解析为空"):
        mod.fetch_flows()


def test_fetch_flows_propagates_http_error(monkeypatch):
    """403 等非 2xx → HTTPError 直接上抛，不进解析。"""
    import requests

    import src.fetchers.etf_flows_fetcher as mod

    class _Resp:
        text = "<html>blocked</html>"

        def raise_for_status(self):
            raise requests.HTTPError("403")

    class _Session:
        def __init__(self, **kw):
            pass

        def get(self, url):
            return _Resp()

    monkeypatch.setattr(mod.cffi_requests, "Session", _Session)
    with pytest.raises(requests.HTTPError):
        mod.fetch_flows()


def test_parse_farside_no_table_returns_empty():
    assert parse_farside("<html>blocked</html>").empty


def test_etf_signal_scored_when_fresh(monkeypatch, tmp_path):
    """radar：ETF 数据新鲜且 5d 流入 → dir=+1 纳入评分（权重 15）；stale 时不纳入。"""
    df = pd.DataFrame(
        [{c: 100.0 for c in COLUMNS}] * 6,
        index=pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=6),
    )
    df.index.name = "date"
    from src import assets_analysis

    out = tmp_path / "data" / "etf_flows"
    out.mkdir(parents=True)
    df.to_csv(out / "etf_flows.csv")

    monkeypatch.setattr(assets_analysis, "ROOT", tmp_path)
    snap = {
        "perp": {"BTC": {"funding_annual": 10.0, "funding_rate": 0.0001, "oi_usd": 1e9}},
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
    assert etf_sig["dir"] == 1 and etf_sig["weight"] == 25

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


def test_consensus_inst_split_no_crash(monkeypatch, tmp_path):
    """机构票型分裂（CME 偏多 + Spread 偏空）→ 不 KeyError，verdict 为内部分歧。

    consensus 现在直接复用雷达的 CME 信号（不再自读 cot.csv），注入 radar 即可。"""
    from src import assets_analysis
    from src.assets_analysis import crypto_consensus

    monkeypatch.setattr(assets_analysis, "ROOT", tmp_path)

    snap = {
        "etf": {"available": False},
        "basis": {"spread": -3.0, "ema60": 1.0, "sofr": 4.0},
        "perp": {"BTC": {"funding_annual": 0.1, "funding_rate": 0.0001}},
        "options_BTC": {"pcr": 0.8},
        "taker": {"BTC": [{"buy": 1.0, "sell": 1.0}] * 5},
    }
    radar = {
        "signals": [
            {
                "name": "CME 机构头寸",
                "weight": 15,
                "dir": 1,
                "value": 5.0,
                "desc": "CME OI 7d 变化 +5.0%（Coinglass，±1% 中性带）",
            }
        ]
    }
    cons = crypto_consensus(snap, radar)
    assert cons["verdict"]  # 不崩
    assert "分歧" in cons["verdict"] or "多空" in cons["verdict"]


def test_consensus_retail_missing_not_same_direction(monkeypatch, tmp_path):
    """散户三条通道全缺 → 不得靠「空集未反向」冒充同向，verdict 标单侧可读。

    旧口径把缺失当 0 票中性，机构一条偏多票就能得出「同向偏多 — 趋势延续」；
    卡片同时要把缺失通道标 has_data=False，前端画「缺数据」而不是「中性」。"""
    from src import assets_analysis
    from src.assets_analysis import crypto_consensus

    monkeypatch.setattr(assets_analysis, "ROOT", tmp_path)

    snap = {
        "etf": {
            "available": True,
            "stale": False,
            "sum5d_busd": 0.9,
            "latest": "2026-10-02",
        },
        "basis": {"spread": 8.0, "ema60": 12.0, "sofr": 4.0},
        "perp": {},  # 无 funding
        "options_BTC": {},  # 无 pcr
        "coinglass": {},  # 无多空比
    }
    radar = {"signals": [{"name": "CME 机构头寸", "weight": 15, "dir": 0, "value": None}]}
    cons = crypto_consensus(snap, radar)

    assert "散户侧数据缺失" in cons["verdict"]
    assert all(not c["has_data"] for c in cons["retail"]["channels"])
    assert [c["has_data"] for c in cons["inst"]["channels"]] == [False, True, True]
    assert cons["inst"]["note"] == "ETF / 基差"
