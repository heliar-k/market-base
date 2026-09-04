"""期权结构快照聚合逻辑测试（Gamma Flip 最近穿越选择）。

回归背景：旧逻辑只找现价下方第一个累计 GEX 负转正穿越点，①翻转点在现价
上方时漏检（REGIME 卡回退 net_gex 符号后与分布图矛盾）②深 ITM 噪声穿越
被误当翻转点。新逻辑取离现价最近的穿越点（上下皆可），保证
sign(flip_dist) == sign(现价处累计 GEX)。
"""

from datetime import datetime, timedelta

import pandas as pd

from src.options_structure import compute_structure

EXP = (datetime.now() + timedelta(days=30)).strftime("%Y%m%d")


def _chain(legs: list[tuple[float, str, int]]) -> pd.DataFrame:
    """legs: (strike, right, oi) → 单到期日合成期权链（IV 0.2）。"""
    return pd.DataFrame(
        [
            {
                "strike": strike,
                "right": right,
                "expiration": EXP,
                "openInterest": oi,
                "volume": 0,
                "impliedVolatility": 0.2,
            }
            for strike, right, oi in legs
        ]
    )


def test_flip_nearest_crossing_above_spot():
    """现价处累计为负、穿越点在现价上方 → flip 取上方穿越（旧逻辑漏检返回 None）。"""
    out = compute_structure(
        _chain(
            [
                (90.0, "P", 50000),
                (95.0, "P", 50000),
                (100.0, "P", 20000),
                (105.0, "C", 90000),
                (110.0, "C", 10000),
            ]
        ),
        100.0,
    )
    assert out["gamma_flip"] == 105.0
    assert out["flip_dist"] < 0  # 现价在 Flip 下方 → 局部负 Gamma


def test_flip_nearest_of_multiple():
    """多个穿越点取离现价最近的（旧逻辑取第一个，被深 ITM 噪声带偏）。"""
    out = compute_structure(
        _chain(
            [
                (80.0, "P", 80000),  # 深负
                (84.0, "C", 60000),  # 第一次上穿（噪声位，旧逻辑取这里）
                (92.0, "P", 50000),  # 下穿
                (96.0, "C", 50000),  # 上穿 —— 离现价最近
            ]
        ),
        100.0,
    )
    assert out["gamma_flip"] == 96.0
    assert out["flip_dist"] > 0  # 现价在 Flip 上方 → 局部正 Gamma


def test_flip_none_when_no_crossing():
    """全链累计恒为正（无穿越）→ flip/flip_dist 均为 None。"""
    out = compute_structure(
        _chain(
            [
                (95.0, "C", 20000),
                (100.0, "C", 50000),
                (105.0, "C", 20000),
            ]
        ),
        100.0,
    )
    assert out["gamma_flip"] is None
    assert out["flip_dist"] is None
