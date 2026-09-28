"""Farside ETH ETF 资金流 fetcher 单元测试（解析复用 BTC 版，样本内嵌不联网）。"""

import pandas as pd

from src.fetchers.etf_flows_eth_fetcher import COLUMNS
from src.fetchers.etf_flows_fetcher import parse_farside

# 与真实页面一致的管道表（12 值列：11 ETF + Total）；列表拼行防 E501


def _row(*cells: str) -> str:
    return "| " + " | ".join(cells) + " |"


_HEAD = _row("", *COLUMNS)
_SEP = "|" + " --- |" * (len(COLUMNS) + 1)
_FEE = _row(
    "Fee",
    "0.25%",
    "0.25%",
    "0.25%",
    "0.20%",
    "0.21%",
    "0.20%",
    "0.25%",
    "0.19%",
    "0.14%",
    "2.50%",
    "0.15%",
    "",
)
_D1 = _row(
    "23 Jul 2024",
    "266.5",
    "-",
    "71.3",
    "204.0",
    "7.5",
    "7.6",
    "5.5",
    "13.2",
    "-",
    "(484.1)",
    "15.1",
    "106.6",
)
_D2 = _row(
    "24 Jul 2024",
    "17.4",
    "-",
    "74.5",
    "29.6",
    "0.0",
    "19.8",
    "2.5",
    "3.9",
    "-",
    "(326.9)",
    "45.9",
    "(133.3)",
)
_SAMPLE = "\n".join([_HEAD, _SEP, _FEE, _D1, _D2]) + "\n"


def test_parse_eth_columns():
    assert COLUMNS[0] == "ETHA" and COLUMNS[-1] == "Total"
    assert len(COLUMNS) == 12


def test_parse_eth_table():
    df = parse_farside(_SAMPLE, COLUMNS)
    assert list(df.index) == ["2024-07-23", "2024-07-24"]
    assert list(df.columns) == COLUMNS
    assert df.loc["2024-07-23", "ETHA"] == 266.5
    assert df.loc["2024-07-23", "ETHE"] == -484.1  # 括号负值
    assert df.loc["2024-07-23", "Total"] == 106.6
    assert df.loc["2024-07-24", "Total"] == -133.3
    assert pd.isna(df.loc["2024-07-23", "ETHB"])  # '-' → NaN
    assert pd.isna(df.loc["2024-07-23", "MSSE"])


def test_parse_skips_fee_seed_rows():
    """Fee/Seed 等非日期管道行不进入结果。"""
    df = parse_farside(_SAMPLE, COLUMNS)
    assert len(df) == 2
