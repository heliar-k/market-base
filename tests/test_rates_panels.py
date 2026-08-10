"""rates 面板新数据源单元测试：借款估算解析 + Bill 占比合并 + 期限溢价 1 月变化。"""

import numpy as np
import pandas as pd

import src.server as server


def _write_refunding(tmp_path, body: str, kind: str = "financing_estimates") -> None:
    (tmp_path / "data" / "treasury").mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "id": "t1",
                "date": "2026-08-03",
                "quarter": "2026-Q3",
                "kind": kind,
                "body": body,
            }
        ]
    ).to_csv(tmp_path / "data" / "treasury" / "refunding.csv", index=False)


def test_borrowing_estimate_parses_two_quarters_and_change(monkeypatch, tmp_path):
    _write_refunding(
        tmp_path,
        "Treasury expects to borrow $739 billion in privately-held "
        "net marketable debt. During the October-December 2026 quarter, "
        "Treasury expects to borrow $628 billion. The borrowing estimate "
        "is $68 billion higher than announced in May 2026. Actual borrowing "
        "was $18 billion lower than announced in May.",
    )
    monkeypatch.setattr(server, "ROOT", tmp_path)
    est = server._borrowing_estimate()
    assert est == {
        "date": "2026-08-03",
        "quarter": "2026-Q3",
        "current_b": 739,
        "next_b": 628,
        "chg_b": 68,
        "chg_dir": "higher",
    }


def test_borrowing_estimate_ignores_prior_quarter_actual(monkeypatch, tmp_path):
    """变化量锚定 "estimate is ..."：上季实际值（actual borrowing was）不参与。"""
    _write_refunding(
        tmp_path,
        "Actual borrowing was $18 billion lower than announced in May. "
        "The borrowing estimate is $68 billion higher than announced in May. ",
    )
    monkeypatch.setattr(server, "ROOT", tmp_path)
    est = server._borrowing_estimate()
    assert est["chg_b"] == 68
    assert est["chg_dir"] == "higher"


def test_borrowing_estimate_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "ROOT", tmp_path)
    assert server._borrowing_estimate() is None


def test_bill_share_merges_monthly_and_daily_dedup(monkeypatch, tmp_path):
    td = tmp_path / "data" / "treasury"
    td.mkdir(parents=True)
    pd.DataFrame(
        {
            "record_date": ["2026-06-30", "2026-07-31"],
            "BILL_SHARE": [20.0, 22.0],
        }
    ).to_csv(td / "mspd.csv", index=False)
    pd.DataFrame(
        {"date": ["2026-07-31", "2026-08-01"], "BILL_SHARE": [22.4, 22.6]}
    ).to_csv(td / "bill_share_daily.csv", index=False)
    monkeypatch.setattr(server, "ROOT", tmp_path)

    pts, latest = server._bill_share_series()
    assert latest == 22.6
    # 7-31 重叠日两源值不同（22.0 vs 22.4），keep='last' 必须取日频 22.4
    assert [p["date"] for p in pts] == ["2026-06-30", "2026-07-31", "2026-08-01"]
    assert pts[1]["value"] == 22.4


def test_term_premium_chg_1m(monkeypatch, tmp_path):
    """ACMTP10 1 月变化：最近值 − 30 天前最近值，bp。"""
    dates = pd.date_range("2026-01-01", periods=40, freq="D")
    df = pd.DataFrame(
        {
            "ACMTP10": [0.5] * 10 + [0.6] * 20 + [0.7] * 10,
            "DGS10": np.nan,
            "DGS2": np.nan,
        },
        index=dates,
    )

    def fake_macro(cat: str) -> pd.DataFrame:
        if cat == "rates":
            return df
        return pd.DataFrame({"DFII10": np.nan}, index=dates)  # tips 空表

    monkeypatch.setattr(server, "_macro_df", fake_macro)
    r = server.get_rates_real_rates(days=365)
    # 30 天前窗口内最新值 0.5（前 10 点）→ 0.7 - 0.5 = 20bp
    assert r["term_premium_chg_1m"] == 20.0
    assert len(r["term_premium"]) == 40
    assert r["nominal_10y"] == []  # rates 表无 DGS10，序列为空
