"""CME BTC 期权墙 fetcher 解析测试（样本内嵌，不联网）。

样本为 BTC 量级（strike 45,000-105,000）；历史样本（/tmp/cme_opt2.txt）实际是
CME 页面回落到默认产品（KC 小麦）的表——strike 四位数、自始未发现。
sanity guard 测试锁定该回归：小麦量级 strike / OI 全 0 → 拒绝落盘。
"""

import pytest

from src.fetchers import cme_options_fetcher as mod
from src.fetchers.cme_options_fetcher import parse_options


@pytest.fixture(autouse=True)
def _spot(monkeypatch):
    """锁死 BTC 现价锚 ~$83,000（不依赖真实 asset_prices.csv）。"""
    monkeypatch.setattr(mod, "_btc_spot", lambda: 83000.0)


# BTC 量级页面样例行（结构同真实页面，strike 为 BTC 正常区间）
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
| 62000 Call | 3 | 0 | 0 | 3 | 0 | 0 | 0 | 10 | 0 |
| 64750 Call | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 11 | 0 |
| 67000 Call | 2 | 0 | 0 | 2 | 0 | 0 | 0 | 23 | -2 |
| 70000 Call | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 29 | 0 |
| 72000 Call | 8 | 0 | 0 | 8 | 0 | 0 | 0 | 20 | 0 |
| 84750 Call | 16 | 0 | 0 | 16 | 0 | 0 | 0 | 16 | +16 |
| 105000 Call | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 158 | 0 |
| 45000 Put | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 17 | 0 |
| 48500 Put | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 24 | 0 |
| 55000 Put | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 24 | 0 |
| 59000 Put | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 23 | 0 |
| 67000 Put | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 20 | 0 |
| 70000 Put | 4 | 0 | 0 | 4 | 0 | 0 | 0 | 31 | +2 |
"""

# 小麦量级样本（CME 默认产品表回落的真实形态）：strike 四位数 → 必须被拒
WHEAT = (
    SAMPLE.replace("105000", "10500")
    .replace("84750", "8475")
    .replace("62000", "6200")
    .replace("64750", "6475")
    .replace("67000", "6700")
    .replace("70000", "7000")
    .replace("72000", "7200")
    .replace("45000", "4500")
    .replace("48500", "4850")
    .replace("55000", "5500")
    .replace("59000", "5900")
)

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
    assert snap["call_wall"] == 105000
    assert snap["call_wall_oi"] == 158
    assert snap["put_wall"] == 70000
    assert snap["put_wall_oi"] == 31


def test_max_pain_hand_verified(monkeypatch):
    # 标准公式：pain(K)=Σ call_oi×max(0,K−K′) + put_oi×max(0,K′−K)
    # pain(100) = call(110)×0? 不：call 在行权价 110 于 K=100 上方 → 损失 0；
    # 唯一起作用的是 put(110)×max(0,110−100)=5×10=50 → pain(100)=50
    # pain(110) = call(100)×max(0,110−100)=90×10=900 → 选 100（pain 50）
    monkeypatch.setattr(mod, "_btc_spot", lambda: 100.0)  # 锚定样本量级
    snap = parse_options(TINY)
    assert snap["max_pain"]["strike"] == 100
    assert snap["max_pain"]["pain"] == 50


def test_groupby_dup_strike(monkeypatch):
    # 同 strike 同侧两行（100 Call 50+40）groupby 求和 → call_wall = 100 OI=90
    monkeypatch.setattr(mod, "_btc_spot", lambda: 100.0)
    snap = parse_options(TINY)
    assert snap["call_wall"] == 100
    assert snap["call_wall_oi"] == 90


def test_top5_desc():
    snap = parse_options(SAMPLE)
    assert [t["strike"] for t in snap["top_calls"][:3]] == [105000, 70000, 67000]
    assert [t["oi"] for t in snap["top_calls"]] == sorted(
        [t["oi"] for t in snap["top_calls"]], reverse=True
    )
    assert [t["strike"] for t in snap["top_puts"][:3]] == [70000, 48500, 55000]
    assert len(snap["top_calls"]) == 5
    assert len(snap["top_puts"]) == 5


def test_as_of():
    assert parse_options(SAMPLE)["as_of"] == "2026-08-22"


def test_empty_returns_dash():
    assert parse_options("") == {}
    assert parse_options("no table\njust some text") == {}


def test_wheat_table_rejected():
    """CME 页面回落默认产品（小麦量级 strike）→ sanity guard 拒绝。"""
    assert parse_options(WHEAT) == {}


def test_zero_oi_rejected():
    """已到期月份（Total OI 全 0）→ 拒绝。"""
    zero = SAMPLE.replace("837", "0").replace("458", "0").replace("379", "0")
    assert parse_options(zero) == {}


def test_no_spot_anchor_rejected(monkeypatch):
    """现价锚不可得 → 拒绝（本源已失信，宁可空缺不可误导）。"""
    monkeypatch.setattr(mod, "_btc_spot", lambda: None)
    assert parse_options(SAMPLE) == {}


def test_crypto_derivatives_wires_cme_options(monkeypatch, tmp_path):
    """crypto_derivatives 组装含 cme_options（无文件时 available=False）。"""
    import json

    from src import assets_analysis as aa

    monkeypatch.setattr(aa, "ROOT", tmp_path)
    # 无 cme_options 文件 → available False
    d = aa.crypto_derivatives()
    assert d is None or d.get("cme_options", {}).get("available", False) is False
    # 写一个最小快照后 → 透传（墙位须在 BTC 合理区间，否则被读取侧 guard 拦下）
    out = tmp_path / "data" / "crypto_derivatives"
    out.mkdir(parents=True, exist_ok=True)
    (out / "20260823.json").write_text(
        json.dumps({"ts": "x", "perp": {}, "options_BTC": {}, "taker": {}}),
        encoding="utf-8",
    )
    co = tmp_path / "data" / "cme_options"
    co.mkdir(parents=True)
    (co / "20260823.json").write_text(
        json.dumps(
            {"call_wall": 105000, "put_wall": 70000, "total_oi": 837, "pcr_oi": 0.83}
        ),
        encoding="utf-8",
    )
    d = aa.crypto_derivatives()
    assert d["cme_options"]["call_wall"] == 105000
    # 小麦量级墙 → 读取侧 guard 拦截（不误导页面）
    (co / "20260824.json").write_text(
        json.dumps({"call_wall": 10500, "put_wall": 7000, "total_oi": 837}),
        encoding="utf-8",
    )
    d = aa.crypto_derivatives()
    assert d["cme_options"]["available"] is False


def test_total_row_na_tolerated():
    """Total 行含 N/A（Preliminary 阶段）不崩、跳过非数字。"""
    snap = parse_options(
        "Last Updated 22 Aug 2026 12:21:19 AM CT.\n"
        "Call Total 83 0 0 83 0 0 0 N/A N/A\n"
        "Put Total 92 0 0 92 0 0 0 379 56\n"
        "| 105000 Call | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 158 | 0 |\n"
    )
    # Call Total 是 N/A → 该字段缺省（墙 OI 仍可用）；Put Total 正常解析
    assert "call_total_oi" not in snap
    assert snap["put_total_oi"] == 379
    assert snap["call_wall"] == 105000  # 墙 OI 不受 Total 行影响


def test_incomplete_returns_empty():
    """totals 与墙都缺 → 返回 {}（不覆盖好快照）。"""
    assert parse_options("No table here at all") == {}


def test_browser_extract_contract():
    """浏览器路径合成文本 → parse_options 闭环（_row_oi 取倒数第二列）。

    模拟 _extract_markdown 的输入（CME DOM 行：首列「strike Call」，
    末列 Change 可能为负/0/粘连），验证合成行能被 parser 正确解析。
    """
    from src.fetchers.cme_options_fetcher import _row_oi

    # _row_oi：OI 是倒数第二列；末列 Change 为 0 时不得误取
    assert _row_oi(["105000 Call", "0", "0", "158", "0"]) == "158"
    assert _row_oi(["105000 Call", "3", "158", "-16"]) == "158"
    assert _row_oi(["Call Total", "0", "458", "0-660"]) == "458"

    # 合成文本（与 _extract_markdown 同构）走 parser 闭环
    synth = "\n".join(
        [
            "Last Updated 25 Sep 2026 12:00:00 AM CT.",
            "| 100000 Call | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 158 | 0 |",
            "| 85000 Put | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 90 | 0 |",
            "Call Total 0 0 0 0 0 0 0 458 0",
            "Put Total 0 0 0 0 0 0 0 379 0",
        ]
    )
    snap = parse_options(synth)
    assert snap["call_wall"] == 100000 and snap["call_wall_oi"] == 158
    assert snap["put_wall"] == 85000
    assert snap["total_oi"] == 837
    assert snap["as_of"] == "2026-09-25"
