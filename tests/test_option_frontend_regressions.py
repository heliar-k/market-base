from pathlib import Path


def test_gex_chart_uses_full_cumulative_series_and_keeps_flip():
    text = Path("frontend/src/pages/assets/equities/options.astro").read_text()
    assert "x.cum_gex_m" in text
    assert "x.strike === v.gamma_flip" in text
    assert "const cum = g.map(x => cumByStrike.get(x.strike))" in text


def test_oi_panel_uses_near_spot_band():
    text = Path("frontend/src/pages/assets/equities/options.astro").read_text()
    assert "近现价 OI 集中度 Top 20" in text
    assert "ATM ±10%" in text


def test_options_page_header_uses_et_snapshot_date():
    text = Path("frontend/src/pages/assets/equities/options.astro").read_text()
    assert "D.snapshot_date || (D.date && D.date.slice(0, 10))" in text


def test_crypto_walls_keep_both_sides_and_use_near_max_pain():
    text = Path("frontend/src/pages/assets/crypto-options.astro").read_text()
    assert "byStrike.set" not in text
    assert "o.near_max_pain" in text
    assert "optB.near_max_pain" in text
    assert "optE.near_max_pain" in text
