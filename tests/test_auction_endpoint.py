"""拍卖端点口径回归：名义券趋势必须剔除 TIPS，FRN 要能标注，as_of 不能是未来日。

背景（2026-10 审计）：Treasury 的 TIPS 与名义券共用 security_term（TIPS 的 10Y
也叫 "10-Year"），只按期限归组会把 TIPS 的实际利率混进名义券趋势 —— 线上曾同时
出现「10-Year 中标 2.44%」与同期名义 10Y 4.71%，读者无从分辨。
"""

import pandas as pd
import pytest

import src.server as server

TODAY = pd.Timestamp("2026-10-04")


@pytest.fixture
def auc_dir(monkeypatch, tmp_path):
    """最小 auction_results.csv：名义 10Y / TIPS 10Y / FRN / 未拍卖的未来场次。"""
    d = tmp_path / "data" / "treasury"
    d.mkdir(parents=True)
    rows = [
        # 名义 10Y：需求强
        dict(
            auction_date="2026-09-22",
            security_type="Note",
            security_term="10-Year",
            offering_amt=42e9,
            bid_to_cover_ratio=2.40,
            high_yield=4.468,
            avg_med_yield=4.413,
            indirect_pct=51.5,
            tail_bp=5.5,
            reopening="No",
            cusip="A1",
            inflation_index_security="No",
            floating_rate="No",
            high_rate=4.468,
        ),
        # TIPS 10Y：实际利率低一个盈亏平衡通胀，绝不能进名义券趋势
        dict(
            auction_date="2026-09-17",
            security_type="Note",
            security_term="9-Year 10-Month",
            offering_amt=19e9,
            bid_to_cover_ratio=2.24,
            high_yield=2.653,
            avg_med_yield=2.577,
            indirect_pct=51.1,
            tail_bp=7.6,
            reopening="Yes",
            cusip="A2",
            inflation_index_security="Yes",
            floating_rate="No",
            high_rate=2.653,
        ),
        # FRN：无固定中标利率
        dict(
            auction_date="2026-09-23",
            security_type="Note",
            security_term="1-Year 10-Month",
            offering_amt=28e9,
            bid_to_cover_ratio=2.63,
            high_yield=None,
            avg_med_yield=None,
            indirect_pct=59.1,
            tail_bp=None,
            reopening="Yes",
            cusip="A3",
            inflation_index_security="No",
            floating_rate="Yes",
            high_rate=None,
        ),
        # 已公告未拍卖（结果字段全空）：不得把 as_of 推到未来
        dict(
            auction_date="2026-10-08",
            security_type="Bond",
            security_term="29-Year 10-Month",
            offering_amt=22e9,
            bid_to_cover_ratio=None,
            high_yield=None,
            avg_med_yield=None,
            indirect_pct=None,
            tail_bp=None,
            reopening="Yes",
            cusip="A4",
            inflation_index_security="No",
            floating_rate="No",
            high_rate=None,
        ),
    ]
    pd.DataFrame(rows).to_csv(d / "auction_results.csv", index=False)
    monkeypatch.setattr(server, "ROOT", tmp_path)

    class _Today:
        @staticmethod
        def today():
            return TODAY.date()

    monkeypatch.setattr(server, "date", _Today)  # 端点内部调 date.today()
    return tmp_path


def test_tips_excluded_from_nominal_trend(auc_dir):
    out = server.get_rates_auctions()
    dates = [p["date"] for p in out["trend"]["10Y"]]
    assert "2026-09-22" in dates
    assert "2026-09-17" not in dates, "TIPS 混进了名义 10Y 趋势"
    # 三条派生序列同一口径
    for key in ("tail_trend", "indirect_trend"):
        assert "2026-09-17" not in [p["date"] for p in out[key]["10Y"]]


def test_avg_cover_uses_nominal_only(auc_dir):
    out = server.get_rates_auctions()
    # 名义付息券 = 非 Bill 且非 TIPS：10Y 2.40 + 5Y 2.62 + FRN 2.50（FRN 有投标倍数，
    # 它是名义付息券，该计入）；TIPS 的 2.24 必须排除
    nominal = round((2.40 + 2.62 + 2.50) / 3, 2)
    assert out["avg_cover_10_coupon"] == pytest.approx(nominal)
    assert out["avg_cover_10_coupon"] != 2.44  # 含 TIPS 时的旧值


def test_recent_rows_flag_tips_and_frn(auc_dir):
    out = server.get_rates_auctions()
    by_term = {r["security_term"]: r for r in out["recent"]}
    assert by_term["9-Year 10-Month"]["is_tips"] is True
    assert by_term["1-Year 10-Month"]["is_frn"] is True
    assert by_term["10-Year"]["is_tips"] is False


def test_as_of_never_in_the_future(auc_dir):
    out = server.get_rates_auctions()
    assert out["as_of"] == "2026-09-23"  # 源文件最大日期是 10-08（未拍卖）


def test_missing_tips_column_treats_rows_as_nominal(auc_dir):
    """旧 CSV 没有 inflation_index_security 列 → 不得误删名义券。"""
    path = auc_dir / "data" / "treasury" / "auction_results.csv"
    df = pd.read_csv(path).drop(columns=["inflation_index_security", "floating_rate"])
    df.to_csv(path, index=False)
    out = server.get_rates_auctions()
    assert [p["date"] for p in out["trend"]["10Y"]] == ["2026-09-22", "2026-09-17"]
    assert all(r["is_tips"] is False for r in out["recent"])
