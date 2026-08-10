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


def _auctions_with_preanchor_maturity() -> pd.DataFrame:
    """加一单锚前发行（2026-06-15）、锚后到期（2026-07-05）的 Bill——
    发行已含在锚余额里，只应扣到期（回归：整帧按 issue_date 过滤会丢掉它）。"""
    pre = pd.DataFrame(
        [
            {
                "security_type": "Bill",
                "issue_date": "2026-06-15",
                "maturity_date": "2026-07-05",
                "offering_amt": 100e6,
            }
        ]
    )
    return pd.concat([_auctions(), pre], ignore_index=True)


def test_compute_basic_accumulation():
    df = compute_daily_bill_share(_mspd(), _auctions(), end=pd.Timestamp("2026-07-20"))

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


def test_preanchor_issue_maturity_is_deducted():
    """锚前发行、锚后到期的 Bill：发行在锚余额里，到期必须扣减。"""
    df = compute_daily_bill_share(
        _mspd(), _auctions_with_preanchor_maturity(), end=pd.Timestamp("2026-07-20")
    )
    # 2026-07-05 扣 100 → 2900；07-15 发 50 → 2950；07-20 到期 50 → 2900
    assert df.loc["2026-07-05", "BILLS"] == pytest.approx(2900.0)
    assert df.loc["2026-07-20", "BILLS"] == pytest.approx(2900.0)
    # MARKETABLE 同样扣 100：10000 + (200+50+300) − (50+100) = 10400
    assert df.loc["2026-07-20", "MARKETABLE"] == pytest.approx(10400.0)


def test_missing_marketable_column_raises():
    """锚行缺 MARKETABLE_TOTAL 列时报 KeyError（保持原有覆盖）。"""
    mspd = _mspd().rename(columns={"MARKETABLE_TOTAL": "MARKETABLE"})
    with pytest.raises(KeyError):
        compute_daily_bill_share(mspd, _auctions(), end=pd.Timestamp("2026-07-20"))
