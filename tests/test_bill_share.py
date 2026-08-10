"""bill_share 派生日频序列单元测试：锚定 + 净发行累计逻辑。"""

import pandas as pd
import pytest

from src.bill_share import compute_daily_bill_share


def _mspd() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "BILLS": [2800.0],
            "MARKETABLE_TOTAL": [10000.0],
        },
        index=pd.to_datetime(["2026-06-30"]),
    )


def _auctions() -> pd.DataFrame:
    """锚后 7 月：Bill 发行 200、到期 50；Note 发行 300。均为百万美元换算前（美元）。"""
    return pd.DataFrame(
        {
            "security_type": ["Bill", "Bill", "Note"],
            "issue_date": ["2026-07-01", "2026-07-15", "2026-07-10"],
            "maturity_date": ["2026-08-15", "2026-07-20", "2026-10-10"],
            "offering_amt": [200e6, 50e6, 300e6],  # 美元
        }
    )


def test_compute_basic_accumulation():
    df = compute_daily_bill_share(_mspd(), _auctions())

    assert df.index[0] == pd.Timestamp("2026-06-30")
    # 2026-06-30 锚点：等于 MSPD
    assert df.loc["2026-06-30", "BILLS"] == pytest.approx(2800.0)
    # 07-01 发行 200 → 3000；07-15 发行 50 → 3050；07-20 到期 50 → 3000
    assert df.loc["2026-07-01", "BILLS"] == pytest.approx(3000.0)
    assert df.loc["2026-07-15", "BILLS"] == pytest.approx(3050.0)
    assert df.loc["2026-07-20", "BILLS"] == pytest.approx(3000.0)
    # MARKETABLE：锚 10000 + 累计净发行（bill 200 + note 300 = 500）
    assert df.loc["2026-07-20", "MARKETABLE"] == pytest.approx(10500.0)
    assert df.loc["2026-07-20", "BILL_SHARE"] == pytest.approx(
        3000 / 10500 * 100, abs=0.01
    )  # noqa: E501


def test_compute_anchor_rows_missing_dates_raises():
    mspd = _mspd().rename(columns={"MARKETABLE_TOTAL": "MARKETABLE"})
    with pytest.raises(KeyError):
        compute_daily_bill_share(mspd, _auctions())
