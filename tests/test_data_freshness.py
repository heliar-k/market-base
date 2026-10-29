"""data_freshness 时效断言回归测试。

覆盖三类判定（ok / STALE / MISSING）与两个真实解析坑（fed 的 `%Y%m%d` 被当 epoch、
_release_dates 的混时区串），都用注入的 checks 跑，不碰真实 data/。
另盖占位行检测（us_indices_placeholder_msg）：交叉 ETF 打桩，不碰网络。
"""

from datetime import date, datetime, time, timezone
from pathlib import Path

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


def test_require_column_ignores_unfinished_rows(tmp_path):
    """国债拍卖文件混着「已公告未拍卖」的空结果行：按日期最大值判定会被公告行
    顶到未来场次（2026-10 实测：成交结果停在 10-15，审计报「落后 5 天 ok」）。
    require 把判定基准改回「末场有结果的拍卖日」。"""
    p = tmp_path / "a.csv"
    pd.DataFrame(
        {
            "auction_date": ["2026-10-15", "2026-10-22", "2026-10-28"],
            "bid_to_cover_ratio": [2.56, None, None],
        }
    ).to_csv(p, index=False)
    loose = {
        "path": "a.csv",
        "kind": "csv",
        "budget": 8,
        "note": "x",
        "column": "auction_date",
    }
    strict = {**loose, "require": "bid_to_cover_ratio"}
    rows = audit(tmp_path, date(2026, 10, 28), [loose, strict])
    assert rows[0][0] == "ok" and rows[0][2] == 0  # 公告行把日期顶到当天
    assert rows[1][0] == "STALE" and rows[1][2] == 13  # 真实停更

    # 列不存在不静默放行：报 MISSING 而不是退回「首列最大值」
    bad = {**strict, "require": "no_such_column"}
    assert audit(tmp_path, date(2026, 10, 28), [bad])[0][0] == "MISSING"


def test_ahead_calendar_needs_lead(tmp_path):
    """拍卖日历末行必须领先今天 ≥ budget；过期的日历=源停更，判 STALE。"""
    chk = {"path": "u.csv", "kind": "ahead", "budget": 1, "note": "x"}
    _csv(tmp_path, "u.csv", ["2026-09-14", "2026-09-17"])
    assert audit(tmp_path, date(2026, 9, 12), [chk])[0][0] == "ok"
    _csv(tmp_path, "u.csv", ["2026-09-10", "2026-09-12"])
    assert audit(tmp_path, date(2026, 9, 12), [chk])[0][0] == "STALE"


def test_ahead_calendar_uses_et_baseline(tmp_path):
    """日历场次日是美东日期：run 延迟跨过 UTC 零点时不能少算一天。

    回归：2026-10-29 daily-full 延迟到 UTC 00:37（美东仍是 10-28），日历末行 10-29，
    拿 UTC 日期比得 0 天判 STALE，实际领先美东今天 1 天。
    """
    chk = {"path": "u.csv", "kind": "ahead", "budget": 1, "note": "x"}
    _csv(tmp_path, "u.csv", ["2026-10-29"])
    assert audit(tmp_path, date(2026, 10, 29), [chk], et_today=date(2026, 10, 28))[0][0] == "ok"
    assert audit(tmp_path, date(2026, 10, 29), [chk], et_today=date(2026, 10, 29))[0][0] == "STALE"
    # 其他 kind 不受 et_today 影响：仍按 UTC 日期算
    daily = {"path": "u.csv", "kind": "csv", "budget": 1, "note": "x"}
    assert audit(tmp_path, date(2026, 10, 30), [daily], et_today=date(2026, 10, 28))[0][2] == 1


def test_json_dir_uses_filename_date(tmp_path):
    (tmp_path / "snap").mkdir()
    (tmp_path / "snap" / "20260901.json").write_text("{}")
    (tmp_path / "snap" / "20260904.json").write_text("{}")
    (tmp_path / "snap" / "20260906.json").write_text("{}")
    chk = {"path": "snap", "kind": "json_dir", "budget": 6, "note": "x"}
    rows = audit(tmp_path, date(2026, 9, 5), [chk])
    assert rows[0][0] == "ok" and rows[0][2] == 1


def test_csv_dir_globs_reads_raw_chain_filename_date(tmp_path, monkeypatch):
    """raw_{sym}_{YYYYMMDD}.csv 的日期在文件名末尾，不能按 CSV 首列解。

    回归：JSON 快照每天更新、raw 链却停在 3 天前（raw 被 .gitignore 排除，
    Actions 产出没入库）。只看 data/options_structure 的 json_dir 永远是绿的。
    """
    monkeypatch.setattr("src.options_structure.SYMBOLS", ["NVDA", "BRK-B"])
    d = tmp_path / "data/options_structure"
    d.mkdir(parents=True)
    (d / "20261027.json").write_text("{}")
    (d / "raw_NVDA_20261024.csv").write_text("strike,right\n200,P\n")
    (d / "raw_BRK-B_20261023.csv").write_text("strike,right\n540,P\n")
    (d / "raw_NVDA_20261027.csv").write_text("strike,right\n200,P\n")
    (d / "raw_NVDA_20261028.csv").write_text("strike,right\n200,P\n")

    json_chk = {"path": "data/options_structure", "kind": "json_dir", "budget": 2, "note": "x"}
    raw_chk = {
        "path": "data/options_structure",
        "kind": "csv_dir_globs",
        "budget": 2,
        "note": "x",
    }
    rows = audit(tmp_path, date(2026, 10, 27), [json_chk, raw_chk])
    assert rows[0][0] == "ok"  # JSON 快照是当日的
    # NVDA 当日新鲜不能掩盖 BRK-B 停在前 4 天；未来文件不参与判定
    assert rows[1][0] == "STALE" and rows[1][2] == 4

    # 旧文件被清理后，标的缺失也必须报警，不能从检查中消失
    (d / "raw_BRK-B_20261023.csv").unlink()
    rows = audit(tmp_path, date(2026, 10, 27), [raw_chk])
    assert rows[0][0] == "MISSING"


def test_options_oi_pending_flagged_only_after_finalization_window(tmp_path):
    d = tmp_path / "data/options_structure"
    d.mkdir(parents=True)
    path = d / "20261027.json"
    path.write_text('{"snapshot_date":"2026-10-27","oi_final":false}')
    before = datetime(2026, 10, 28, 14, 0, tzinfo=timezone.utc)
    after = datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc)
    assert fr.options_oi_pending_msg(tmp_path, before) is None
    assert fr.options_oi_pending_msg(tmp_path, after) is not None
    path.write_text('{"snapshot_date":"2026-10-27","oi_final":true}')
    assert fr.options_oi_pending_msg(tmp_path, after) is None
    (d / "20261026.json").write_text('{"snapshot_date":"2026-10-26","oi_final":false}')
    assert "2026-10-26" in fr.options_oi_pending_msg(tmp_path, after)


def test_options_oi_pending_skips_weekday_holiday(tmp_path, monkeypatch):
    monkeypatch.setattr("src.options_structure._is_session", lambda day: False)
    d = tmp_path / "data/options_structure"
    d.mkdir(parents=True)
    (d / "20261027.json").write_text('{"snapshot_date":"2026-10-27","oi_final":false}')
    after = datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc)
    assert fr.options_oi_pending_msg(tmp_path, after) is None


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
    monkeypatch.setattr(fr, "_cross_etfs_moved", lambda now: True)
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
    monkeypatch.setattr(fr, "_cross_etfs_moved", lambda now: True)
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
    monkeypatch.setattr(fr, "_cross_etfs_moved", lambda now: True)
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
    monkeypatch.setattr(fr, "_cross_etfs_moved", lambda now: False)
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
    monkeypatch.setattr(fr, "_cross_etfs_moved", lambda now: None)
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

    def _boom(now):
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
    monkeypatch.setattr(fr, "_cross_etfs_moved", lambda now: True)
    sat = datetime(2026, 10, 10, 20, 30, tzinfo=timezone.utc)  # 周六
    assert fr.us_indices_placeholder_msg(tmp_path, sat) is None


def test_cross_witness_requires_todays_bar(monkeypatch):
    """证人语义：假日无当日 bar（末根是昨日）→ 不作证（False），不误报。"""
    from src.fetchers import yfinance_fetcher as yf_mod

    # 末根 10-12，而 now 是 10-13（假日）：无当日 bar
    hist = pd.DataFrame(
        {"Close": [100.0, 101.0]},
        index=pd.DatetimeIndex(["2026-10-09", "2026-10-12"]),
    )
    monkeypatch.setattr(yf_mod, "fetch_hist", lambda tkr: hist)
    now = datetime(2026, 10, 13, 21, 0, tzinfo=timezone.utc)
    assert fr._cross_etfs_moved(now) is False


def test_cross_witness_true_on_todays_move(monkeypatch):
    from src.fetchers import yfinance_fetcher as yf_mod

    hist = pd.DataFrame(
        {"Close": [100.0, 101.0]},
        index=pd.DatetimeIndex(["2026-10-12", "2026-10-13"]),
    )
    monkeypatch.setattr(yf_mod, "fetch_hist", lambda tkr: hist)
    now = datetime(2026, 10, 13, 21, 0, tzinfo=timezone.utc)
    assert fr._cross_etfs_moved(now) is True


def test_cross_witness_flat_today(monkeypatch):
    """当日 bar 存在但与前一 bar 相同（真平盘）→ 不作证。"""
    from src.fetchers import yfinance_fetcher as yf_mod

    hist = pd.DataFrame(
        {"Close": [100.0, 100.0]},
        index=pd.DatetimeIndex(["2026-10-12", "2026-10-13"]),
    )
    monkeypatch.setattr(yf_mod, "fetch_hist", lambda tkr: hist)
    now = datetime(2026, 10, 13, 21, 0, tzinfo=timezone.utc)
    assert fr._cross_etfs_moved(now) is False


def test_confirm_window_tracks_dst():
    """确认窗 = ET 16:15，夏令时 EDT → 20:15 UTC，冬季 EST → 21:15 UTC。"""
    edt = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)  # 10 月仍 EDT
    est = datetime(2026, 12, 10, 12, 0, tzinfo=timezone.utc)  # 12 月已 EST
    assert fr._confirm_from_utc(edt).replace(tzinfo=None) == time(20, 15)
    assert fr._confirm_from_utc(est).replace(tzinfo=None) == time(21, 15)


def test_last_row_not_today_skipped(tmp_path, monkeypatch):
    """最新行不是今天（当日班次还没写入）→ 交给日期预算断言。"""
    _asset_csv(
        tmp_path,
        [
            {"date": "2026-10-10", "SPX": 7807.24},
            {"date": "2026-10-11", "SPX": 7807.24},
        ],
    )
    monkeypatch.setattr(fr, "_cross_etfs_moved", lambda now: True)
    assert fr.us_indices_placeholder_msg(tmp_path, _NOW) is None


# ── 票委名册跨年断言（roster.csv 只有 year 列，日期预算套不上）──────────────


def _roster_csv(root: Path, year: int) -> None:
    (root / "data" / "fed").mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [{"year": year, "speaker": "Warsh", "name_zh": "沃什", "role": "美联储主席", "votes": True}]
    ).to_csv(root / "data" / "fed" / "roster.csv", index=False)


def test_roster_year_ok_when_current(tmp_path):
    _roster_csv(tmp_path, 2026)
    assert fr.roster_year_msg(tmp_path, date(2026, 10, 17)) is None


def test_roster_year_stale_across_year(tmp_path):
    """跨年没换人 → 报红：页面会把去年的人当现任票委展示。"""
    _roster_csv(tmp_path, 2026)
    msg = fr.roster_year_msg(tmp_path, date(2027, 2, 15))
    assert msg and "2026" in msg and "2027" in msg


def test_roster_year_january_grace(tmp_path):
    """1 月整月豁免：换人发生在 1 月首次会议（通常下旬），月初报红是误报。"""
    _roster_csv(tmp_path, 2026)
    assert fr.roster_year_msg(tmp_path, date(2027, 1, 10)) is None
    # 2 月起不再豁免
    assert fr.roster_year_msg(tmp_path, date(2027, 2, 1)) is not None


def test_roster_missing_file_reports(tmp_path):
    """文件缺失 → 报错。roster.csv 不进 CHECKS（year 会被当日期解），
    这里必须自己承担 MISSING 判定，否则名册静默消失无人知。"""
    msg = fr.roster_year_msg(tmp_path, date(2026, 6, 1))
    assert msg and "缺失" in msg


def test_roster_malformed_csv_reports(tmp_path):
    """CSV 坏掉 → 报错（不抛异常，收尾断言本身不该炸）。"""
    (tmp_path / "data" / "fed").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "fed" / "roster.csv").write_text("not,a,valid\ncsv")
    msg = fr.roster_year_msg(tmp_path, date(2026, 6, 1))
    assert msg and "解析" in msg or "异常" in (msg or "")
