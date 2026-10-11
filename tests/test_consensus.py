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
    """票龄 >2 个工作日 → 缺席 + stale 注记；2 个工作日内在场。

    票龄不含 as_of 当天（review #35 修正）：周一观测周六算 = 4（Tue–Fri），
    周四观测周六算 = 1（Fri）。
    """
    members = [
        _m("old", "on", as_of="2026-10-05"),  # 周一 → 周六 = 4 个工作日
        _m("fresh", "on", as_of="2026-10-08"),  # 周四 → 周六 = 1 个工作日
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


# ── review #35 修复回归：_band / _chg 日历窗口 / _m_gex 防崩 / cls 下发 ──


def test_band_directions() -> None:
    """_band 双向边界：high_is_on 两种方向 + 中间弃权。"""
    assert consensus._band(65, 60, 40, high_is_on=True) is True
    assert consensus._band(50, 60, 40, high_is_on=True) is None
    assert consensus._band(35, 60, 40, high_is_on=True) is False
    assert consensus._band(15, 17, 22, high_is_on=False) is True
    assert consensus._band(20, 17, 22, high_is_on=False) is None
    assert consensus._band(25, 17, 22, high_is_on=False) is False
    assert consensus._band(None, 60, 40, high_is_on=True) is None


def test_chg_days_weekly_and_daily() -> None:
    """_chg 按日历日取差：周频序列 ≈ 4-5 观测、日频 ≈ 21-22 观测，同为真 1M。"""
    # 周频（liquidity.csv 形态：仅周三有值）；target=10-07−30d=09-07，
    # base 取 09-02（值 101，gap 35≤37 天）
    weeks = pd.date_range("2026-08-26", "2026-10-07", freq="7D")
    s = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 110.0], index=weeks)
    assert consensus._chg(s) == 9.0
    # 日频：target=10-09−30d=09-09 恰有观测 → 差 = 30
    days = pd.date_range("2026-08-31", "2026-10-09", freq="D")
    s2 = pd.Series(range(len(days)), index=days, dtype=float)
    assert consensus._chg(s2) == 30.0


def test_chg_days_gapped_series_returns_none() -> None:
    """基准观测距末值超 days+7（断更）→ None。"""
    idx = pd.to_datetime(["2026-08-01", "2026-10-09"])
    assert consensus._chg(pd.Series([1.0, 5.0], index=idx)) is None


def test_gex_corrupt_or_missing_absent(tmp_path, monkeypatch) -> None:
    """损坏 JSON / 空目录 → 期权结构成员缺席而非崩溃（验收条款）。"""
    monkeypatch.setattr(consensus, "ROOT", tmp_path)
    d = tmp_path / "data/options_structure"
    d.mkdir(parents=True)
    (d / "2026-10-10.json").write_text("{corrupt!!")
    m = consensus._m_gex()
    assert not m["present"] and m["vote"] is None
    (d / "2026-10-10.json").unlink()
    assert not consensus._m_gex()["present"]


def test_aggregate_emits_cls() -> None:
    """状态→色档由 API 单源下发，前端不维护映射。"""
    assert _agg(["on", "on", "on", "on", "off", "off"])["cls"] == "on"
    assert _agg(["off", "off", "off", "off", "on", "on"])["cls"] == "off"
    assert _agg(["on", "on", "on", "off", "off", "off"])["cls"] == "div"
    assert _agg([None] * 6)["cls"] == "na"
