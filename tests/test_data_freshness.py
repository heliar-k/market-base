"""data_freshness 时效断言回归测试。

覆盖三类判定（ok / STALE / MISSING）与两个真实解析坑（fed 的 `%Y%m%d` 被当 epoch、
_release_dates 的混时区串），都用注入的 checks 跑，不碰真实 data/。
"""

from datetime import date

import pandas as pd

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
