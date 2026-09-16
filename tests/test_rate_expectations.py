"""rate_expectations_fetcher 数据源选择 + 月末会议定约测试。

被测对象为纯函数 / 可注入路径的小函数，不读真实 data/ 盘：
- _pick_fresher / _read_zq_close：IBKR vs Barchart 两源取数据日期更新者
- _use_next_month_contract：FedWatch 月末会议改用下月合约的判定
"""

from __future__ import annotations

import pandas as pd

from src.config import FomcMeeting
from src.fetchers import rate_expectations_fetcher as rex
from src.fetchers.rate_expectations_fetcher import (
    _pick_fresher,
    _read_zq_close,
    _use_next_month_contract,
)


class TestPickFresher:
    def test_barchart_newer_wins(self) -> None:
        """IBKR 本地停更（7/31）→ 取 Barchart 新数据（9/15）。"""
        ibkr = (96.18, "2026-07-31")
        barchart = (96.125, "2026-09-15")
        assert _pick_fresher(ibkr, barchart) == barchart

    def test_ibkr_newer_wins(self) -> None:
        assert _pick_fresher((96.2, "2026-09-16"), (96.1, "2026-09-15")) == (
            96.2,
            "2026-09-16",
        )

    def test_same_date_prefers_ibkr(self) -> None:
        """同日 IBKR 质量更高，取 IBKR（即使价格不同）。"""
        assert _pick_fresher((96.18, "2026-09-15"), (96.125, "2026-09-15")) == (
            96.18,
            "2026-09-15",
        )

    def test_none_falls_back_to_other(self) -> None:
        assert _pick_fresher(None, (96.1, "2026-09-15")) == (96.1, "2026-09-15")
        assert _pick_fresher((96.2, "2026-07-31"), None) == (96.2, "2026-07-31")
        assert _pick_fresher(None, None) is None


def _write_zq_csv(path, rows: list[tuple[str, float]], ibkr_format: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if ibkr_format:
        df = pd.DataFrame(
            [{"date": d, "open": c, "high": c, "low": c, "close": c} for d, c in rows]
        )
    else:
        df = pd.DataFrame([{"date": d, "close": c} for d, c in rows])
    df.to_csv(path, index=False)


class TestReadZqClose:
    def test_freshness_across_sources(self, tmp_path) -> None:
        """IBKR 文件存在但停在 7/31，Barchart 有 9/15 → 必须返回 Barchart。"""
        _write_zq_csv(
            tmp_path / "data" / "commodities" / "ZQ" / "ZQ_202610.csv",
            [("2026-07-30", 96.19), ("2026-07-31", 96.18)],
            ibkr_format=True,
        )
        _write_zq_csv(
            tmp_path / "data" / "barchart" / "commodities" / "ZQ" / "ZQ_202610.csv",
            [("2026-09-14", 96.14), ("2026-09-15", 96.125)],
            ibkr_format=False,
        )
        assert _read_zq_close(2026, 10, root=tmp_path) == (96.125, "2026-09-15")

    def test_ibkr_newer_than_barchart(self, tmp_path) -> None:
        _write_zq_csv(
            tmp_path / "data" / "commodities" / "ZQ" / "ZQ_202609.csv",
            [("2026-09-16", 96.28)],
            ibkr_format=True,
        )
        _write_zq_csv(
            tmp_path / "data" / "barchart" / "commodities" / "ZQ" / "ZQ_202609.csv",
            [("2026-09-15", 96.26)],
            ibkr_format=False,
        )
        assert _read_zq_close(2026, 9, root=tmp_path) == (96.28, "2026-09-16")

    def test_both_missing_returns_none(self, tmp_path) -> None:
        assert _read_zq_close(2099, 1, root=tmp_path) is None


class TestUseNextMonthContract:
    def test_month_end_uses_next_month(self, monkeypatch) -> None:
        """会后采样 3 天 + 下月无会议 + 下月可读 → 用下月合约。"""
        monkeypatch.setattr(
            rex,
            "FOMC_MEETINGS",
            [FomcMeeting(2026, 10, 28, 29), FomcMeeting(2026, 12, 9, 10)],
        )
        assert _use_next_month_contract(3, 2026, 10, next_readable=True)

    def test_next_month_has_meeting_falls_back(self, monkeypatch) -> None:
        """下月有 FOMC 会议（合约被污染）→ 回退本月方法。"""
        monkeypatch.setattr(
            rex,
            "FOMC_MEETINGS",
            [FomcMeeting(2026, 9, 16, 17), FomcMeeting(2026, 10, 28, 29)],
        )
        assert not _use_next_month_contract(3, 2026, 9, next_readable=True)

    def test_next_month_unreadable_falls_back(self, monkeypatch) -> None:
        """下月合约文件缺失 → 回退本月方法。"""
        monkeypatch.setattr(rex, "FOMC_MEETINGS", [FomcMeeting(2026, 10, 28, 29)])
        assert not _use_next_month_contract(3, 2026, 10, next_readable=False)

    def test_not_month_end(self, monkeypatch) -> None:
        """会后采样 ≥10 天 / 会议在最后一天 → 不换约（days_after==0 直接 implied）。"""
        monkeypatch.setattr(rex, "FOMC_MEETINGS", [])
        assert not _use_next_month_contract(14, 2026, 9, next_readable=True)
        assert not _use_next_month_contract(10, 2026, 12, next_readable=True)
        assert not _use_next_month_contract(0, 2026, 7, next_readable=True)

    def test_december_rolls_to_next_year(self, monkeypatch) -> None:
        """12 月会议 → 下月是次年 1 月（跨年判定）。"""
        monkeypatch.setattr(
            rex,
            "FOMC_MEETINGS",
            [FomcMeeting(2026, 12, 29, 30), FomcMeeting(2027, 3, 16, 17)],
        )
        assert _use_next_month_contract(1, 2026, 12, next_readable=True)

    def test_real_config_october_2026(self) -> None:
        """真实日历：2026-10-28 会后仅 3 天、11 月无会议 → 换约；9/12 月不换。"""
        assert _use_next_month_contract(3, 2026, 10, next_readable=True)
        assert not _use_next_month_contract(14, 2026, 9, next_readable=True)
        assert not _use_next_month_contract(22, 2026, 12, next_readable=True)
