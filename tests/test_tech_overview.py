"""技术分析结论总览端点与静态导出注册测试。"""

from __future__ import annotations

from pathlib import Path

from src import export_pages, server
from src.analyze import analyze
from src.indicators import compute_all_indicators


def test_analyze_outputs_conclusion_and_three_dim_scores(sample_ohlcv_df) -> None:
    """analyze() 必须输出结论三元组、三维拆解分数、均线斜率/偏离与 52 周分位。"""
    df = compute_all_indicators(sample_ohlcv_df.copy())
    res = analyze(df, "TEST")
    assert res["regime"] in {"bullish", "mild_bullish", "neutral", "mild_bearish", "bearish"}
    assert res["regime_label"]
    assert res["setup_tag"]
    assert isinstance(res["summary_lines"], list) and len(res["summary_lines"]) == 3
    assert res["total_score"] == res["trend_score"] + res["momentum_score"] + res["loc_score"]
    assert "ma_slopes" in res and "ma_dists" in res
    assert "BB_width" in res and isinstance(res["BB_squeeze"], bool)
    assert "pos_52w" in res and "high_52w" in res and "low_52w" in res


def test_get_tech_overview_skips_missing_and_returns_items(
    monkeypatch, tmp_path: Path, sample_ohlcv_df
) -> None:
    """get_tech_overview() 遍历品种列表：有 CSV 的返回带 type/regime 的诊断，缺失的跳过。"""
    stocks_dir = tmp_path / "data" / "stocks"
    indices_dir = tmp_path / "data" / "indices"
    stocks_dir.mkdir(parents=True)
    indices_dir.mkdir(parents=True)

    sample_ohlcv_df.to_csv(stocks_dir / "AAPL.csv")
    sample_ohlcv_df.to_csv(indices_dir / "SPX.csv")

    fake_symbols = [
        {"name": "SPX", "type": "index"},
        {"name": "AAPL", "type": "stock"},
        {"name": "MISSING", "type": "stock"},
    ]
    monkeypatch.setattr(server, "IBKR_SYMBOLS", fake_symbols)
    monkeypatch.setattr(server, "ROOT", tmp_path)

    out = server.get_tech_overview()
    assert len(out) == 2
    by_sym = {d["symbol"]: d for d in out}
    assert by_sym["SPX"]["type"] == "index"
    assert by_sym["AAPL"]["type"] == "stock"
    assert "regime" in by_sym["AAPL"]
    assert "trend_score" in by_sym["AAPL"]


def test_export_pages_registers_tech_overview() -> None:
    """静态导出模块必须注册 api/tech/overview，防线上静默 404。"""
    src_text = Path(export_pages.__file__).read_text(encoding="utf-8")
    assert "get_tech_overview" in src_text
    assert '_safe("api/tech/overview", get_tech_overview)' in src_text


def test_analyze_short_history_fallback_ma_labels() -> None:
    """上市不足 200 天的次新标的回退 MA120/MA60 时，评分标签如实标注实际均线名而非假称 MA200。"""
    import numpy as np
    import pandas as pd

    dates = pd.date_range("2024-01-01", periods=130, freq="B")
    close = np.linspace(100.0, 150.0, 130)
    short_raw = pd.DataFrame(
        {
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1_000_000,
        },
        index=dates,
    )
    short_df = compute_all_indicators(short_raw)
    res = analyze(short_df, "NEWIPO")
    labels = [s["label"] for s in res["trend_scores"]]
    assert any("MA120" in lb for lb in labels)
    assert not any("MA200" in lb for lb in labels)
