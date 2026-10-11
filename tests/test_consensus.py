"""共识层单元测试 —— 投票/聚合/降级/票龄/留痕（ADR-0004，issue #33）。

只用合成的成员/子信号字典，不依赖仓内真实数据文件；留痕测试 monkeypatch
ROOT 到 tmp_path，不污染 data/consensus/。
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from src import consensus


def _m(name: str, vote: str | None, present: bool = True, as_of: str = "2026-10-09") -> dict:
    return {"name": name, "vote": vote, "present": present, "subs": [], "as_of": as_of}


def _agg(votes: list[str | None], presents: list[bool] | None = None) -> dict:
    presents = presents if presents is not None else [True] * len(votes)
    members = [_m(f"m{i}", v, p) for i, (v, p) in enumerate(zip(votes, presents))]
    return consensus._aggregate(members, "ON", "OFF")


# ── 聚合：确认阈值 / 分歧 / 降级 ──


def test_confirm_two_thirds() -> None:
    """4 on / 1 off / 1 弃权 → 确认 on（4/5 ≥ 2/3）。"""
    assert _agg(["on", "on", "on", "on", "off", None])["state"] == "ON"


def test_divergent_when_below_bar() -> None:
    """3 on / 3 off → 分歧；3 on / 1 off / 2 弃权 → 3/4 = 75% 确认 on。"""
    assert _agg(["on", "on", "on", "off", "off", "off"])["state"] == "分歧"
    assert _agg(["on", "on", "on", "off", None, None])["state"] == "ON"


def test_exactly_two_thirds_confirms() -> None:
    """4 on / 2 off → 4/6 = 2/3，边界含入确认。"""
    assert _agg(["on", "on", "on", "on", "off", "off"])["state"] == "ON"


def test_absent_majority_insufficient() -> None:
    """4/6 缺席 → 数据不足（哪怕在场票全同向）。"""
    assert (
        _agg(["on", "on", None, None, None, None], [True, True, False, False, False, False])[
            "state"
        ]
        == "数据不足"
    )


def test_all_abstain_insufficient() -> None:
    """全员有数据但全弃权（平票）→ 数据不足。"""
    assert _agg([None] * 6)["state"] == "数据不足"


def test_counts_reported() -> None:
    card = _agg(["on", "off", None, None])
    assert (card["n_on"], card["n_off"], card["n_abstain"], card["n_present"]) == (1, 1, 2, 4)


# ── 成员：子信号多数 / 平票弃权 / 缺席 ──


def test_member_majority_and_tie() -> None:
    """子信号 2on/1off → on；1on/1off 平票 → 弃权但在场。"""
    m = consensus._member(
        "x",
        [
            consensus._sub("a", True, 1.0, "2026-10-09"),
            consensus._sub("b", True, 1.0, "2026-10-09"),
            consensus._sub("c", False, 1.0, "2026-10-09"),
        ],
    )
    assert m["vote"] == "on" and m["present"]
    tie = consensus._member(
        "y",
        [
            consensus._sub("a", True, 1.0, "2026-10-09"),
            consensus._sub("b", False, 1.0, "2026-10-09"),
        ],
    )
    assert tie["vote"] is None and tie["present"]


def test_member_absent_without_data() -> None:
    """所有子信号无数据 → 缺席。"""
    m = consensus._member("x", [consensus._sub("a", None, None, None)])
    assert not m["present"] and m["vote"] is None


# ── 票龄 ──


def test_staleness_marks_absent() -> None:
    """票龄 >2 个工作日 → 缺席 + stale 注记；2 个工作日内在场。"""
    members = [
        _m("old", "on", as_of="2026-10-05"),  # 周一 → 周六 = 5 个工作日
        _m("fresh", "on", as_of="2026-10-08"),  # 周四 → 周六 = 2 个工作日
    ]
    consensus._apply_staleness(members, date(2026, 10, 10))
    assert members[0]["present"] is False and members[0]["vote"] is None and members[0]["stale"]
    assert members[1]["present"] is True and members[1]["vote"] == "on"


def test_stale_vote_excluded_from_denominator() -> None:
    """陈年票剔除后，剩余 2 on / 1 off → 2/3 确认 on（陈年 off 不参与）。"""
    members = [
        _m("a", "on"),
        _m("b", "on"),
        _m("c", "off"),
        _m("stale_off", "off", as_of="2026-10-01"),
    ]
    consensus._apply_staleness(members, date(2026, 10, 10))
    assert consensus._aggregate(members, "ON", "OFF")["state"] == "ON"


# ── 留痕：盘中临时值不覆盖定稿行 ──


def _result(state: str = "ON") -> dict:
    card = {
        "state": state,
        "members": [_m("趋势", "on")],
        "n_present": 1,
        "n_members": 1,
        "n_on": 1,
        "n_off": 0,
        "n_abstain": 0,
    }
    return {"ref_date": "2026-10-10", "market": card, "macro": card}


def test_history_upsert_idempotent_and_final_protected(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(consensus, "ROOT", tmp_path)
    consensus.append_history(_result(), intraday=False)
    consensus.append_history(_result(), intraday=True)  # 同日盘中：不覆盖定稿
    consensus.append_history(_result(), intraday=True)
    df = pd.read_csv(tmp_path / "data/consensus/history.csv")
    assert len(df) == 1 and df.iloc[0]["source"] == "final"
    consensus.append_history(_result("OFF"), intraday=False)  # 定稿覆盖
    df = pd.read_csv(tmp_path / "data/consensus/history.csv")
    assert len(df) == 1 and df.iloc[0]["market_state"] == "OFF"


def test_history_multiday_rows(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(consensus, "ROOT", tmp_path)
    consensus.append_history(_result(), intraday=False)
    r2 = _result()
    r2["ref_date"] = "2026-10-13"
    consensus.append_history(r2, intraday=False)
    df = pd.read_csv(tmp_path / "data/consensus/history.csv")
    assert list(df["date"]) == ["2026-10-10", "2026-10-13"]


def test_votes_tag_marks_absent() -> None:
    card = {"members": [_m("趋势", "on"), _m("广度", None, present=False), _m("信用", None)]}
    tag = consensus._votes_tag(card)
    assert "趋势:on" in tag and "广度:absent" in tag and "信用:abst" in tag
