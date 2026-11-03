import pandas as pd

from src.ai_cycle_analysis import ndx_prices
from src.config import ROOT


def test_ndx_full_history_is_complete_and_preferred() -> None:
    path = ROOT / "data" / "indices" / "NDX.csv"
    frame = pd.read_csv(path, index_col="date", parse_dates=True)
    assert len(frame) >= 10_000
    assert frame.index[0] <= pd.Timestamp("1985-10-01")
    assert frame.index[-1] >= pd.Timestamp("2026-11-03")
    assert frame.index.is_unique
    assert frame["close"].gt(0).all()
    preferred = ndx_prices()
    assert preferred.index[0] == frame.index[0]
    assert preferred["NDX"].iloc[-1] == frame["close"].iloc[-1]
