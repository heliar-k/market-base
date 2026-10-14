"""data_freshness 时效断言回归测试。

覆盖三类判定（ok / STALE / MISSING）与两个真实解析坑（fed 的 `%Y%m%d` 被当 epoch、
_release_dates 的混时区串），都用注入的 checks 跑，不碰真实 data/。
另盖占位行检测（us_indices_placeholder_msg）：交叉 ETF 打桩，不碰网络。
"""

from datetime import date, datetime, timezone

import pandas as pd

import src.data_freshness as fr
from src.data_freshness import audit


def _csv(root, rel: str, dates: list[str], col: str = "date") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({col: dates}).to_csv(p, index=False)


def test_stale_and_ok_by_budget(tmp_path):
    _csv(tmp_path, "a.csv", ["2026-08-01", "2026-09-01"])
    checks = [
        {"path": "a.csv", "kind": "csv", "budget": 30, "note": "fresh"},
        {"path": "a.csv", "kind": "csv", "budget": 3, "note": "too tight"},
    ]
    rows = audit(tmp_path, date(2026, 9, 5), checks)
    assert [r[0] for r in rows] == ["ok", "STALE"]
    assert rows[1][2] == 4  # 落后天数（09-01 → 09-05）


def test_missing_file_flags_missing(tmp_path):
    rows = audit(
        tmp_path,
        date(2026, 9, 5),
        [{"path": "nope.csv", "kind": "csv", "budget": 6, "note": "x"}],
    )
    assert rows[0][0] == "MISSING"
    assert rows[0][2] is None


def test_compact_yyyymmdd_not_parsed_as_epoch(tmp_path):
    """`20260903` 若被当 int → epoch 纳秒 → 1970（旧实现虚报 2 万天）。"""
    _csv(tmp_path, "s.csv", ["20260901", "20260903"])
    rows = audit(
        tmp_path,
        date(2026, 9, 5),
        [{"path": "s.csv", "kind": "csv", "budget": 21, "note": "x"}],
    )
    assert rows[0][0] == "ok"
    assert rows[0][2] == 2


def test_mixed_tz_offsets_parse(tmp_path):
    """_release_dates 风格：带 -05 后缀的混时区串，取 ISO 前缀解析。"""
    _csv(
        tmp_path,
        "r.csv",
        ["2026-08-17 15:15:50-05", "2026-09-04 09:40:28-05"],
        col="last_updated",
    )
    chk = {
        "path": "r.csv",
        "kind": "csv",
        "column": "last_updated",
        "budget": 6,
        "note": "x",
    }
    assert audit(tmp_path, date(2026, 9, 5), [chk])[0][0] == "ok"


def test_ahead_calendar_needs_lead(tmp_path):
    """拍卖日历末行必须领先今天 ≥ budget；过期的日历=源停更，判 STALE。"""
    chk = {"path": "u.csv", "kind": "ahead", "budget": 1, "note": "x"}
    _csv(tmp_path, "u.csv", ["2026-09-14", "2026-09-17"])
    assert audit(tmp_path, date(2026, 9, 12), [chk])[0][0] == "ok"
    _csv(tmp_path, "u.csv", ["2026-09-10", "2026-09-12"])
    assert audit(tmp_path, date(2026, 9, 12), [chk])[0][0] == "STALE"


def test_json_dir_uses_filename_date(tmp_path):
    (tmp_path / "snap").mkdir()
    (tmp_path / "snap" / "20260901.json").write_text("{}")
    (tmp_path / "snap" / "20260904.json").write_text("{}")
    chk = {"path": "snap", "kind": "json_dir", "budget": 6, "note": "x"}
    rows = audit(tmp_path, date(2026, 9, 5), [chk])
    assert rows[0][0] == "ok" and rows[0][2] == 1


# ── 占位行检测（2026-10-13 事故）───────────────────────────────────


def _asset_csv(root, rows):
    p = root / "data/yfinance/asset_prices.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(p, index=False)


# 2026-10-13 是周二；确认窗 UTC 20:30 在窗内
_NOW = datetime(2026, 10, 13, 20, 30, tzinfo=timezone.utc)


def test_placeholder_row_flagged(tmp_path, monkeypatch):
    _asset_csv(
        tmp_path,
        [
            {"date": "2026-10-09", "SPX": 7811.54, "DJI": 51654.95},
            {"date": "2026-10-12", "SPX": 7807.24, "DJI": 51850.55},
            {"date": "2026-10-13", "SPX": 7807.24, "DJI": 51850.55},  # 复制昨日
        ],
    )
    monkeypatch.setattr(fr, "_cross_etfs_moved", lambda: True)
    msg = fr.us_indices_placeholder_msg(tmp_path, _NOW)
    assert msg is not None and "SPX" in msg


def test_copied_from_day_before_yesterday_also_flagged(tmp_path, monkeypatch):
    """盘前占位 + 次日继续陈旧：与前行不同但与前两行相同也要拦。"""
    _asset_csv(
        tmp_path,
        [
            {"date": "2026-10-09", "SPX": 7811.54},
            {"date": "2026-10-12", "SPX": 7807.24},
            {"date": "2026-10-13", "SPX": 7807.24},
            {"date": "2026-10-14", "SPX": 7807.24},  # 陈旧两天
        ],
    )
    monkeypatch.setattr(fr, "_cross_etfs_moved", lambda: True)
    now = datetime(2026, 10, 14, 20, 30, tzinfo=timezone.utc)
    assert fr.us_indices_placeholder_msg(tmp_path, now) is not None


def test_updated_values_not_flagged(tmp_path, monkeypatch):
    _asset_csv(
        tmp_path,
        [
            {"date": "2026-10-12", "SPX": 7807.24, "DJI": 51850.55},
            {"date": "2026-10-13", "SPX": 7805.45, "DJI": 51670.10},
        ],
    )
    monkeypatch.setattr(fr, "_cross_etfs_moved", lambda: True)
    assert fr.us_indices_placeholder_msg(tmp_path, _NOW) is None


def test_holiday_cross_etf_flat_not_flagged(tmp_path, monkeypatch):
    """美股休市：指数与 ETF 同样不动 → 不误报。"""
    _asset_csv(
        tmp_path,
        [
            {"date": "2026-10-12", "SPX": 7807.24},
            {"date": "2026-10-13", "SPX": 7807.24},
        ],
    )
    monkeypatch.setattr(fr, "_cross_etfs_moved", lambda: False)
    assert fr.us_indices_placeholder_msg(tmp_path, _NOW) is None


def test_cross_source_down_fails_open(tmp_path, monkeypatch):
    """交叉源取不到（None）→ 宁漏报不误报。"""
    _asset_csv(
        tmp_path,
        [
            {"date": "2026-10-12", "SPX": 7807.24},
            {"date": "2026-10-13", "SPX": 7807.24},
        ],
    )
    monkeypatch.setattr(fr, "_cross_etfs_moved", lambda: None)
    assert fr.us_indices_placeholder_msg(tmp_path, _NOW) is None


def test_before_window_skips_without_network(tmp_path, monkeypatch):
    """盘前/盘中（窗口前）纵有占位行也不拦，且不发起交叉拉取。"""
    _asset_csv(
        tmp_path,
        [
            {"date": "2026-10-12", "SPX": 7807.24},
            {"date": "2026-10-13", "SPX": 7807.24},
        ],
    )

    def _boom():
        raise AssertionError("窗口前不应发起网络调用")

    monkeypatch.setattr(fr, "_cross_etfs_moved", _boom)
    early = datetime(2026, 10, 13, 13, 30, tzinfo=timezone.utc)
    assert fr.us_indices_placeholder_msg(tmp_path, early) is None


def test_weekend_skipped(tmp_path, monkeypatch):
    _asset_csv(
        tmp_path,
        [
            {"date": "2026-10-09", "SPX": 7811.54},
            {"date": "2026-10-10", "SPX": 7811.54},
        ],
    )
    monkeypatch.setattr(fr, "_cross_etfs_moved", lambda: True)
    sat = datetime(2026, 10, 10, 20, 30, tzinfo=timezone.utc)  # 周六
    assert fr.us_indices_placeholder_msg(tmp_path, sat) is None


def test_last_row_not_today_skipped(tmp_path, monkeypatch):
    """最新行不是今天（当日班次还没写入）→ 交给日期预算断言。"""
    _asset_csv(
        tmp_path,
        [
            {"date": "2026-10-10", "SPX": 7807.24},
            {"date": "2026-10-11", "SPX": 7807.24},
        ],
    )
    monkeypatch.setattr(fr, "_cross_etfs_moved", lambda: True)
    assert fr.us_indices_placeholder_msg(tmp_path, _NOW) is None
