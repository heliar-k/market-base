"""Coinglass 全市场衍生品聚合 fetcher 单元测试。

样本取自 Cloudflare Browser Run 实际渲染出的 markdown（2026-11-22 抓取）：
导航栏一行链接 + OI 分布管道表（表头 / 分隔行 / All 合计行 / 交易所行）。
"""

import pytest
import requests

from src.fetchers.coinglass_fetcher import (
    _pct,
    _qty,
    parse_oi_table,
    parse_snapshot,
    parse_top,
    render_markdown,
)

# 真实导航栏行（四种链接在同一行）
_NAV = (
    "[24h Volume $135,203,236,834 -59.39%]"
    "(https://www.coinglass.com/pro/futures/FuturesVolume) "
    "[Open Interest $102,447,937,628 +1.12%]"
    "(https://www.coinglass.com/pro/futures/Cryptofutures) "
    "[24h Liquidation $394,896,766 -84.67%](https://www.coinglass.com/liquidations) "
    "[24h Long/Short 49.46%/50.54%](https://www.coinglass.com/LongShortRatio)\n"
)

# 真实表头 + 分隔行
_HEADER = (
    "| Ranking | Exchanges | OI(BTC) | OI | Rate | OI Change (1h) | OI Change (4h) | "
    "OI Change (24h) | OI/24h\\_Vol | Trade |\n"
    "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
)


def _row(
    rank: int | str,
    name: str,
    oi_btc: str,
    oi_usd: str,
    share: str,
    chg: tuple[str, str, str],
    nested_link: bool = False,
    extra_col: bool = False,
) -> str:
    """一行 OI 分布表。nested_link=True 用真实页面的 `[![X](img) X](url)` 形式。"""
    img = f"![{name}](https://cdn/u.png)"
    if nested_link:
        cell = f"[{img} {name}](https://www.coinglass.com/exchanges/{name})"
    else:
        cell = f"{img} {name}"
    cells_row = [str(rank), cell, f"{oi_btc} BTC", f"${oi_usd}", f"{share}%", *chg, "0.8124", ""]
    if extra_col:  # 页面多出一列 → 行宽与表头不一致
        cells_row.append("x")
    return "| " + " | ".join(cells_row) + " |\n"


_ALL = _row("", "All", "656.28K", "38.78B", "100", ("+0.04%", "+0.72%", "+1.48%"))
_SAMPLE = (
    _NAV
    + "\n"
    + _HEADER
    + _ALL
    + _row(1, "CME", "92.56K", "5.47B", "14.09", ("-0.07%", "-0.47%", "+1.42%"))
    + _row(
        2, "Binance", "135.68K", "8.01B", "20.66", ("+0.18%", "+0.78%", "-1.37%"), nested_link=True
    )
    + _row(3, "OKX", "44.69K", "2.64B", "6.8", ("-0.10%", "-0.07%", "-2.71%"), nested_link=True)
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
    assert out["all_open_interest_usd"] == 102_447_937_628
    assert out["liq24h_usd"] == 394_896_766
    assert out["liq24h_chg_pct"] == -84.67
    assert out["ls_ratio"] == {"long_pct": 49.46, "short_pct": 50.54}


def test_parse_oi_table():
    out = parse_oi_table(_SAMPLE)
    # All 合计行 → BTC OI（BTC 等价 + USD）
    assert out["btc_oi_btc"] == 656_280.0
    assert out["btc_oi_usd"] == 38.78e9
    # 裸图片与嵌套链接两种单元格都能取到名字，页面顺序保持
    assert [e["name"] for e in out["exchanges"]] == ["CME", "Binance", "OKX"]
    cme = out["exchanges"][0]
    assert cme["oi_btc"] == 92_560.0
    assert cme["oi_usd"] == 5.47e9
    assert cme["share_pct"] == 14.09
    assert cme["chg_1d_pct"] == 1.42
    assert out["exchanges"][1]["chg_1d_pct"] == -1.37


def test_missing_table_warns_but_does_not_raise(caplog):
    """没有表（拦截页/改版）→ 不出表字段，只告警，严格性交给 parse_snapshot。"""
    import logging

    caplog.set_level(logging.WARNING, logger="src.fetchers.coinglass_fetcher")
    assert parse_top("no nav here") == {}
    assert parse_oi_table("## Exchange BTC Open Interest (USD)\nempty") == {}
    assert "未找到 OI 分布表" in caplog.text


def test_header_missing_column_returns_empty(caplog):
    """表头缺 OI Change (24h) 列 → 不出表，告警（按名称定位列，不按下标猜）。"""
    import logging

    caplog.set_level(logging.WARNING, logger="src.fetchers.coinglass_fetcher")
    header = (
        "| Ranking | Exchanges | OI(BTC) | OI | Rate | OI Change (1h) |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
    )
    cme = _row(1, "CME", "92.56K", "5.47B", "14.09", ("-0.07%", "-0.47%", "+1.42%"))
    out = parse_oi_table(header + cme)
    assert out == {}
    assert "缺列" in caplog.text


def test_eight_good_rows_no_warning(caplog):
    """8 行好数据 → 零告警（负断言，防正常页面误报）。"""
    import logging

    caplog.set_level(logging.WARNING, logger="src.fetchers.coinglass_fetcher")
    rows = "".join(
        _row(i, f"Ex{i}", "10.0K", "1.0B", "2.50", ("+0.10%", "+0.20%", "+0.30%"))
        for i in range(1, 9)
    )
    out = parse_oi_table(_HEADER + rows)
    assert [e["name"] for e in out["exchanges"]] == [f"Ex{i}" for i in range(1, 9)]
    assert "Coinglass" not in caplog.text


def test_na_in_24h_column_counted_as_drifted(caplog):
    """24h 格不是百分比（N/A）→ 弃行并告警，不错标成 0。"""
    import logging

    caplog.set_level(logging.WARNING, logger="src.fetchers.coinglass_fetcher")
    rows = _row(1, "OKX", "44.69K", "2.64B", "6.8", ("-0.10%", "-0.07%", "N/A")) + _row(
        2, "CME", "92.56K", "5.47B", "14.09", ("-0.07%", "-0.47%", "+1.42%")
    )
    out = parse_oi_table(_HEADER + rows)
    assert [e["name"] for e in out["exchanges"]] == ["CME"]
    assert "1 行变化列缺失/异常" in caplog.text


def test_extra_column_row_counted_as_drifted(caplog):
    """行宽与表头不一致（页面加了一列）→ 弃行并告警，不按错位下标取值。"""
    import logging

    caplog.set_level(logging.WARNING, logger="src.fetchers.coinglass_fetcher")
    good = _row(1, "CME", "92.56K", "5.47B", "14.09", ("-0.07%", "-0.47%", "+1.42%"))
    wide = _row(
        2, "Binance", "135.68K", "8.01B", "20.66", ("+0.18%", "+0.78%", "-1.37%"), extra_col=True
    )
    out = parse_oi_table(_HEADER + good + wide)
    assert [e["name"] for e in out["exchanges"]] == ["CME"]
    assert "1 行变化列缺失/异常" in caplog.text


def _full_sample(n: int = 8) -> str:
    """导航 + All 行 + n 家交易所行（parse_snapshot 要求 8 家齐才算渲染完整）。"""
    rows = "".join(
        _row(i, f"Ex{i}", "10.0K", "1.00B", "2.50", ("+0.10%", "+0.20%", "+0.30%"))
        for i in range(1, n + 1)
    )
    return _NAV + "\n" + _HEADER + _ALL + rows


def test_parse_snapshot_complete():
    """导航、All 行、8 家交易所都在 → 快照字段齐。"""
    snap = parse_snapshot(_full_sample())
    assert snap["all_open_interest_usd"] == 102_447_937_628
    assert snap["btc_oi_usd"] == 38_780_000_000.0
    assert len(snap["exchanges"]) == 8


def test_parse_snapshot_degenerate_raises():
    """仅拿到占位页（cookie 提示）时直接报错，不写 75 字节空快照。"""
    with pytest.raises(RuntimeError, match="渲染不全"):
        parse_snapshot("We use essential cookies to make our site work.")


def test_parse_snapshot_few_exchanges_raises():
    """交易所行不足 8 家 → 报错（页面改版或半渲染）。"""
    with pytest.raises(RuntimeError, match="仅 3 家"):
        parse_snapshot(_SAMPLE)


def test_render_markdown_needs_credentials(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_BROWSER_TOKEN", raising=False)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct")
    with pytest.raises(RuntimeError, match="缺 CLOUDFLARE_BROWSER_TOKEN"):
        render_markdown()


def test_render_markdown_unwraps_result(monkeypatch):
    seen = {}

    class _Resp:
        def json(self):
            return {"success": True, "result": "# markdown"}

        def raise_for_status(self):
            pass

    def fake_post(url, **kw):
        seen["url"] = url
        seen.update(kw)
        return _Resp()

    monkeypatch.setenv("CLOUDFLARE_BROWSER_TOKEN", "tok")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct-1")
    monkeypatch.setattr(requests, "post", fake_post)
    assert render_markdown("https://example.com") == "# markdown"
    assert seen["url"].endswith("/accounts/acct-1/browser-run/markdown")
    assert seen["json"]["url"] == "https://example.com"
    assert seen["headers"]["Authorization"] == "Bearer tok"


def test_render_markdown_raises_on_api_error(monkeypatch):
    class _Resp:
        def json(self):
            return {"success": False, "errors": [{"message": "quota exceeded"}]}

        def raise_for_status(self):
            pass

    monkeypatch.setenv("CLOUDFLARE_BROWSER_TOKEN", "tok")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct-1")
    monkeypatch.setattr(requests, "post", lambda url, **kw: _Resp())
    with pytest.raises(RuntimeError, match="渲染失败"):
        render_markdown()
