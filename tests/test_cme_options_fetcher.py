"""CME BTC 期权墙 fetcher 解析测试（样本内嵌，不联网）。"""

from src.fetchers.cme_options_fetcher import parse_options

# 真实页面样例行（截取自 /tmp/cme_opt2.txt）
SAMPLE = """##### Preliminary Data![Image 1](https://www.cmegroup.com/aemedge/icons/info-filled.svg)

Last Updated 22 Aug 2026 12:21:19 AM CT.

Trade Date

Expiration

Globex Open Outcry PNT/ClearPort Total Volume Block Trades EOO Exercises At Close Change
Total 175 0 0 175 0 0 0 837 100
Call Total 83 0 0 83 0 0 0 458 44
Put Total 92 0 0 92 0 0 0 379 56

| Strike | Volume | Exercises | Open Interest |
| --- | --- | --- | --- |
| Venue Detail | Trade Type Detail | At Close | Change |
| 6200 Call | 3 | 0 | 0 | 3 | 0 | 0 | 0 | 10 | 0 |
| 6475 Call | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 11 | 0 |
| 6700 Call | 2 | 0 | 0 | 2 | 0 | 0 | 0 | 23 | -2 |
| 7000 Call | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 29 | 0 |
| 7200 Call | 8 | 0 | 0 | 8 | 0 | 0 | 0 | 20 | 0 |
| 8475 Call | 16 | 0 | 0 | 16 | 0 | 0 | 0 | 16 | +16 |
| 10500 Call | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 158 | 0 |
| 4500 Put | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 17 | 0 |
| 4850 Put | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 24 | 0 |
| 5500 Put | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 24 | 0 |
| 5900 Put | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 23 | 0 |
| 6700 Put | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 20 | 0 |
| 7000 Put | 4 | 0 | 0 | 4 | 0 | 0 | 0 | 31 | +2 |
"""

# 小样本（手工可验的 Max Pain）：仅行权价行，无 Total 行
TINY = """.strike | Volume | Exercises | Open Interest |
| 100 Call | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 50 | 0 |
| 100 Call | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 40 | 0 |
| 100 Put | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 20 | 0 |
| 110 Call | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 10 | 0 |
| 110 Put | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 5 | 0 |
"""


def test_totals_and_pcr():
    snap = parse_options(SAMPLE)
    assert snap["call_total_oi"] == 458
    assert snap["put_total_oi"] == 379
    assert snap["total_oi"] == 837
    assert snap["pcr_oi"] == round(379 / 458, 2)


def test_walls_max_oi():
    snap = parse_options(SAMPLE)
    assert snap["call_wall"] == 10500
    assert snap["call_wall_oi"] == 158
    assert snap["put_wall"] == 7000
    assert snap["put_wall_oi"] == 31


def test_max_pain_hand_verified():
    # 标准公式：pain(K)=Σ call_oi×max(0,K−K′) + put_oi×max(0,K′−K)
    # pain(100) = call(110)×0? 不：call 在行权价 110 于 K=100 上方 → 损失 0；
    # 唯一起作用的是 put(110)×max(0,110−100)=5×10=50 → pain(100)=50
    # pain(110) = call(100)×max(0,110−100)=90×10=900 → 选 100（pain 50）
    snap = parse_options(TINY)
    assert snap["max_pain"]["strike"] == 100
    assert snap["max_pain"]["pain"] == 50


def test_groupby_dup_strike():
    # 同 strike 同侧两行（100 Call 50+40）groupby 求和 → call_wall = 100 OI=90
    snap = parse_options(TINY)
    assert snap["call_wall"] == 100
    assert snap["call_wall_oi"] == 90


def test_top5_desc():
    snap = parse_options(SAMPLE)
    assert [t["strike"] for t in snap["top_calls"][:3]] == [10500, 7000, 6700]
    assert [t["oi"] for t in snap["top_calls"]] == sorted(
        [t["oi"] for t in snap["top_calls"]], reverse=True
    )
    assert [t["strike"] for t in snap["top_puts"][:3]] == [7000, 4850, 5500]
    assert len(snap["top_calls"]) == 5
    assert len(snap["top_puts"]) == 5


def test_as_of():
    assert parse_options(SAMPLE)["as_of"] == "2026-08-22"


def test_empty_returns_dash():
    assert parse_options("") == {}
    assert parse_options("no table\njust some text") == {}


def test_crypto_derivatives_wires_cme_options(monkeypatch, tmp_path):
    """crypto_derivatives 组装含 cme_options（无文件时 available=False）。"""
    import json

    from src import assets_analysis as aa

    monkeypatch.setattr(aa, "ROOT", tmp_path)
    # 无 cme_options 文件 → available False
    d = aa.crypto_derivatives()
    assert d is None or d.get("cme_options", {}).get("available", False) is False
    # 写一个最小快照后 → 透传
    out = tmp_path / "data" / "crypto_derivatives"
    out.mkdir(parents=True, exist_ok=True)
    (out / "20260823.json").write_text(
        json.dumps({"ts": "x", "perp": {}, "options_BTC": {}, "taker": {}}),
        encoding="utf-8",
    )
    co = tmp_path / "data" / "cme_options"
    co.mkdir(parents=True)
    (co / "20260823.json").write_text(
        json.dumps({"available": True, "call_wall": 10500, "pcr_oi": 0.83}),
        encoding="utf-8",
    )
    d = aa.crypto_derivatives()
    assert d["cme_options"]["call_wall"] == 10500


def test_total_row_na_tolerated():
    """Total 行含 N/A（Preliminary 阶段）不崩、跳过非数字。"""
    snap = parse_options(
        "Last Updated 22 Aug 2026 12:21:19 AM CT.\n"
        "Call Total 83 0 0 83 0 0 0 N/A N/A\n"
        "Put Total 92 0 0 92 0 0 0 379 56\n"
        "| 10500 Call | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 158 | 0 |\n"
    )
    # Call Total 是 N/A → 该字段缺省（墙 OI 仍可用）；Put Total 正常解析
    assert "call_total_oi" not in snap
    assert snap["put_total_oi"] == 379
    assert snap["call_wall"] == 10500  # 墙 OI 不受 Total 行影响


def test_incomplete_returns_empty():
    """totals 与墙都缺 → 返回 {}（不覆盖好快照）。"""
    assert parse_options("No table here at all") == {}
