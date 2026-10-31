from __future__ import annotations

import pandas as pd
import pytest
from fastapi import HTTPException

from src import server

_COLS = [
    "date",
    "meeting_date",
    "contract",
    "settlement",
    "implied_rate",
    "post_meeting_rate",
    "prob_cut",
    "prob_hold",
    "prob_hike",
    "expectation",
    "contract_used",
    "zq_as_of",
]


def _row(
    snap_date: str,
    meeting_date: str,
    post: float,
    ranges: dict[str, float],
    **over: object,
) -> dict:
    """一行快照：ranges 的键是绝对区间（如 "3.75-4.00"），值是该区间概率。"""
    row: dict = {
        "date": snap_date,
        "meeting_date": meeting_date,
        "contract": f"ZQ_{meeting_date[:4]}{meeting_date[5:7]}",
        "settlement": 96.0,
        "implied_rate": 4.0,
        "post_meeting_rate": post,
        # 三档相对各快照当时的目标区间，与历史图无关；故意给成与 range_* 不一致的值
        "prob_cut": 0.5,
        "prob_hold": 0.5,
        "prob_hike": 0.0,
        "expectation": "维持",
        "contract_used": f"ZQ_{meeting_date[:4]}{meeting_date[5:7]}",
        "zq_as_of": snap_date,
    }
    row.update({f"range_{k}": v for k, v in ranges.items()})
    row.update(over)
    return row


def _write(rows: list[dict], root) -> None:
    d = root / "data" / "rate_expectations"
    d.mkdir(parents=True, exist_ok=True)
    # 区间列取全部行的并集（旧快照没有后加的区间列 → 读到 NaN）
    range_cols: list[str] = []
    for r in rows:
        range_cols += [k for k in r if k.startswith("range_") and k not in range_cols]
    pd.DataFrame(rows)[_COLS + range_cols].to_csv(d / "fomc_probabilities.csv", index=False)


@pytest.fixture
def hist(tmp_path, monkeypatch):
    """两场会议：一场已结束（2020，前端口径的历史回看）+ 一场未来；快照乱序写入。"""
    rows = [
        _row("2026-10-31", "2026-12-09", 4.0582, {"3.75-4.00": 0.2672, "4.00-4.25": 0.7328}),
        _row("2026-10-29", "2026-12-09", 4.105, {"3.75-4.00": 0.1, "4.00-4.25": 0.9}),
        _row("2026-10-30", "2026-12-09", 4.09, {"3.75-4.00": 0.2, "4.00-4.25": 0.8}),
        _row("2026-10-30", "2020-01-29", 2.5, {"2.25-2.50": 1.0}),
    ]
    _write(rows, tmp_path)
    monkeypatch.setattr(server, "ROOT", tmp_path)
    return server.get_rate_expectations_history()


def test_meetings_keyed_by_full_meeting_date(hist) -> None:
    assert set(hist["meetings"]) == {"2026-12-09", "2020-01-29"}


def test_snapshot_days_sorted_ascending(hist) -> None:
    """文件里的顺序是 10-31 / 10-29 / 10-30，返回必须按快照日升序。"""
    assert [r["date"] for r in hist["meetings"]["2026-12-09"]] == [
        "2026-10-29",
        "2026-10-30",
        "2026-10-31",
    ]


def test_as_of_is_latest_snapshot(hist) -> None:
    assert hist["as_of"] == "2026-10-31"


def test_closed_meeting_kept(hist) -> None:
    """已结束会议照旧返回（兄弟端点会按今天过滤掉它，历史图要能回看）。"""
    assert hist["meetings"]["2020-01-29"] == [
        {
            "date": "2026-10-30",
            "post_meeting_rate": 2.5,
            "probs": [{"lo": 2.25, "hi": 2.5, "prob": 1.0}],
        }
    ]


def test_probs_are_absolute_ranges_not_cut_hold_hike(hist) -> None:
    """三档在同一行是 0.5/0.5/0，图里必须是 range_* 的绝对值。"""
    day = hist["meetings"]["2026-12-09"][0]
    assert day["probs"] == [
        {"lo": 3.75, "hi": 4.0, "prob": 0.1},
        {"lo": 4.0, "hi": 4.25, "prob": 0.9},
    ]
    assert day["post_meeting_rate"] == 4.105


def test_zero_probability_ranges_dropped(hist) -> None:
    """快照里出现 0.0 的区间不进 payload（前端堆叠带只画有概率的）。"""
    assert all(p["prob"] > 0 for r in hist["meetings"]["2026-12-09"] for p in r["probs"])
    assert sum(p["prob"] for p in hist["meetings"]["2026-12-09"][2]["probs"]) == pytest.approx(1.0)


def test_duplicate_day_deduped_keeping_last_row(tmp_path, monkeypatch) -> None:
    """同日同会议重跑会叠出两行（快照写入对「一日多行」不幂等），只留文件里最后一行。"""
    rows = [
        _row("2026-10-31", "2026-12-09", 4.0, {"3.75-4.00": 0.4, "4.00-4.25": 0.6}),
        _row("2026-10-31", "2026-12-09", 4.2, {"3.75-4.00": 0.05, "4.00-4.25": 0.95}),
    ]
    _write(rows, tmp_path)
    monkeypatch.setattr(server, "ROOT", tmp_path)
    out = server.get_rate_expectations_history()
    series = out["meetings"]["2026-12-09"]
    assert len(series) == 1
    assert series[0]["post_meeting_rate"] == 4.2
    assert series[0]["probs"] == [
        {"lo": 3.75, "hi": 4.0, "prob": 0.05},
        {"lo": 4.0, "hi": 4.25, "prob": 0.95},
    ]


def test_range_column_missing_in_old_snapshot(tmp_path, monkeypatch) -> None:
    """区间列是后来才出现的（目标区间上移），旧快照该列是 NaN → 不产出该区间。"""
    rows = [
        _row("2026-07-29", "2026-12-09", 3.8, {"3.50-3.75": 0.3, "3.75-4.00": 0.7}),
        _row(
            "2026-10-31",
            "2026-12-09",
            4.05,
            {"3.50-3.75": 0.0, "3.75-4.00": 0.3, "4.00-4.25": 0.7},
        ),
    ]
    _write(rows, tmp_path)
    monkeypatch.setattr(server, "ROOT", tmp_path)
    series = server.get_rate_expectations_history()["meetings"]["2026-12-09"]
    assert series[0]["probs"] == [
        {"lo": 3.5, "hi": 3.75, "prob": 0.3},
        {"lo": 3.75, "hi": 4.0, "prob": 0.7},
    ]
    assert series[1]["probs"] == [
        {"lo": 3.75, "hi": 4.0, "prob": 0.3},
        {"lo": 4.0, "hi": 4.25, "prob": 0.7},
    ]


def test_header_only_csv_returns_empty(tmp_path, monkeypatch) -> None:
    """只有表头（当天首次写入前）：给空 meetings，不去 strftime 一个 NaT。"""
    d = tmp_path / "data" / "rate_expectations"
    d.mkdir(parents=True)
    (d / "fomc_probabilities.csv").write_text(
        "date,meeting_date,post_meeting_rate,range_3.75-4.00\n", encoding="utf-8"
    )
    monkeypatch.setattr(server, "ROOT", tmp_path)
    assert server.get_rate_expectations_history() == {"as_of": None, "meetings": {}}


def test_missing_csv_404_with_run_command(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(server, "ROOT", tmp_path)
    with pytest.raises(HTTPException) as e:
        server.get_rate_expectations_history()
    assert e.value.status_code == 404
    assert "fetch_rate_expectations" in e.value.detail
