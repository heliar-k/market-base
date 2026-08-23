"""Coinglass 全市场衍生品聚合 fetcher 单元测试。

样本来自真实抓取的页面摘录（2026-08-23 Jina 渲染格式）：
两行式（CME）/ 一行式（Binance/OKX）交易所行 + All 合计行 + 导航栏。
"""

from src.fetchers.coinglass_fetcher import _pct, _qty, parse_oi_table, parse_top

# 真实格式摘录（含菜单噪声，验证正则锚定）；长行字符串拼接，内容与真实页面一致
_SAMPLE = (
    "\n"
    "[24h Volume $220,069,668,717 -44.02%]("
    "https://www.coinglass.com/pro/futures/FuturesVolume)"
    "[Open Interest $137,149,294,671 -2.38%]("
    "https://www.coinglass.com/pro/futures/Cryptofutures)"
    "[24h Liquidation $994,810,128 -32.24%]("
    "https://www.coinglass.com/liquidations)"
    "[24h Long/Short 49.28%/50.72%]("
    "https://www.coinglass.com/LongShortRatio)\n\n"
    "| Ranking | Exchanges | OI(BTC) | OI | Rate | OI Change (1h) | "
    "OI Change (4h) | OI Change (24h) | OI/24h_Vol | Trade |\n"
    "| --- | --- | --- | --- | --- | --- |\n\n"
    "![Image 3: All](https://cdn.coinglasscdn.com/static/blank.png)\n\n"
    "All 717.29K BTC$55.45B 100%+0.19%-0.37%-1.61%0.9345\n"
    "1![Image 4: CME](https://cdn.coinglasscdn.com/static/exchanges/"
    "cme-icon-144x144.png)\n\n"
    "CME 125.95K BTC$9.74B 17.56%+0.31%+0.05%+2.01%0.8277\n"
    "2[![Image 5: Binance](https://cdn.coinglasscdn.com/static/exchanges/270.png) "
    "Binance](https://www.coinglass.com/exchanges/Binance)141.88K BTC$10.96B "
    "19.77%+0.22%-0.19%-0.08%0.845[Binance]("
    "https://www.coinglass.com/goto/en_ex_binance_4)\n"
    "3[![Image 6: OKX](https://cdn.coinglasscdn.com/static/exchanges/okx2.png) "
    "OKX](https://www.coinglass.com/exchanges/OKX)37.26K BTC$2.88B "
    "5.19%+0.03%-0.28%-1.04%0.5356[OKX]("
    "https://www.okx.com/join/12782349)\n"
    "\n"
)


def test_units_kmb():
    assert _qty("125.95K") == 125950.0
    assert _qty("9.74B") == 9.74e9
    assert _qty("576.11M") == 576.11e6
    assert _qty("992.38") == 992.38  # 无单位（小交易所）
    assert _pct("17.56%") == 17.56
    assert _pct("+2.01%") == 2.01
    assert _pct("-66.36%") == -66.36


def test_parse_top():
    out = parse_top(_SAMPLE)
    assert out["all_open_interest_usd"] == 137_149_294_671
    assert out["liq24h_usd"] == 994_810_128
    assert out["liq24h_chg_pct"] == -32.24
    assert out["ls_ratio"] == {"long_pct": 49.28, "short_pct": 50.72}


def test_parse_oi_table():
    out = parse_oi_table(_SAMPLE)
    # All 合计行 → BTC OI（BTC 等价 + USD）
    assert out["btc_oi_btc"] == 717290.0
    assert out["btc_oi_usd"] == 55.45e9
    # 两种行格式均解析，页面顺序保持
    assert [e["name"] for e in out["exchanges"]] == ["CME", "Binance", "OKX"]
    cme = out["exchanges"][0]
    assert cme["oi_btc"] == 125950.0
    assert cme["oi_usd"] == 9.74e9
    assert cme["share_pct"] == 17.56
    assert cme["chg_1d_pct"] == 2.01
    assert out["exchanges"][1]["chg_1d_pct"] == -0.08


def test_missing_fields_tolerated():
    # 无导航栏 → 只跳过 top 字段，不抛异常
    assert parse_top("no nav here") == {}
    # 无表格 → 只跳过表字段
    assert parse_oi_table("## Exchange BTC Open Interest (USD)\nempty") == {}
    # 无 All 行但有交易所行 → 交易所字段仍解析
    row = "1![Image 9: Bitget](https://u) Bitget 26.91K BTC$2.08B "
    row += "3.75%+0.27%+0.09%+1.19%0.7445"
    out = parse_oi_table(row)["exchanges"]
    assert out == [
        {
            "name": "Bitget",
            "oi_btc": 26910.0,
            "oi_usd": 2.08e9,
            "share_pct": 3.75,
            "chg_1d_pct": 1.19,
        }
    ]
