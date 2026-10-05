"""analysis_utils.trading_only —— 日频快照周末占位行的剥离口径。

save_daily_csv 按「拉取日」追加行，周末把上一交易日收盘原样续写。这类行不是
观测，却占了索引位：窗口长度（tail(30)）与回溯步数（chg_pct(s, 14)）全被拉长。
守在这里是因为 credit / assets 两条分析线都踩同一个源。
"""

import pandas as pd

from src.analysis_utils import SEVEN_DAY, clean_snapshot, trading_only


def _snap(pairs: list[tuple[str, float]]) -> pd.Series:
    return pd.Series([v for _, v in pairs], index=pd.to_datetime([d for d, _ in pairs]))


def test_mid_week_placeholders_are_dropped():
    """周末夹在中间：值与周五相同，直接删行（不需要回灌）。"""
    s = _snap(
        [
            ("2026-09-21", 10.0),  # 周一
            ("2026-09-25", 11.0),  # 周五
            ("2026-09-26", 11.0),  # 周六 占位
            ("2026-09-27", 11.0),  # 周日 占位
            ("2026-09-28", 12.0),  # 周一
        ]
    )
    out = trading_only(s)
    assert [str(d.date()) for d in out.index] == [
        "2026-09-21",
        "2026-09-25",
        "2026-09-28",
    ]
    assert list(out) == [10.0, 11.0, 12.0]


def test_trailing_placeholder_carries_revision_back():
    """快照以周末结尾，且周末行带来收盘修订 → 修订并入最后交易日，不丢数据。"""
    s = _snap(
        [
            ("2026-10-02", 10.0),  # 周五（当日快照写下的盘中价）
            ("2026-10-03", 10.4),  # 周六（Yahoo 已给出的收盘修订）
            ("2026-10-04", 10.4),  # 周日 占位
        ]
    )
    out = trading_only(s)
    assert [str(d.date()) for d in out.index] == ["2026-10-02"]
    assert out.iloc[-1] == 10.4


def test_seven_day_keeps_weekend():
    """7×24 标的（BTC/ETH）周末是真实行情，一行都不能删。"""
    s = _snap([("2026-10-02", 1.0), ("2026-10-03", 2.0), ("2026-10-04", 3.0)])
    assert len(trading_only(s, seven_day=True)) == 3


def test_all_weekend_input_returns_empty_not_crash():
    s = _snap([("2026-10-03", 1.0), ("2026-10-04", 2.0)])
    assert trading_only(s).empty


def test_clean_snapshot_is_per_column():
    """宽表逐列剥离：股票列没周末行，7×24 列保留自己那行。

    拼回宽表时索引取并集（周六行对 KBWB 为 NaN），所以消费方必须按列取值
    后 dropna——现有调用方（_latest_card / trading_only / chg_pct）全是这个形状。
    """
    idx = pd.to_datetime(["2026-09-25", "2026-09-26", "2026-09-28"])  # 五、六、一
    df = pd.DataFrame(
        {
            "KBWB": [10.0, 10.0, 11.0],
            "BTC": [100.0, 101.0, 102.0],
        },
        index=idx,
    )
    out = clean_snapshot(df)
    assert [str(d.date()) for d in out["KBWB"].dropna().index] == [
        "2026-09-25",
        "2026-09-28",
    ]
    assert len(out["BTC"].dropna()) == 3
    assert "BTC" in SEVEN_DAY
