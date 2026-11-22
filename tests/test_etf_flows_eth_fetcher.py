"""Farside ETH ETF 资金流 fetcher 单元测试（解析复用 BTC 版，样本内嵌不联网）。"""

import pandas as pd

from src.fetchers.etf_flows_eth_fetcher import COLUMNS
from src.fetchers.etf_flows_fetcher import parse_farside

# 与真实页面一致：表头（首格空）+ Fee / Seed 行 + 每日一行；12 值列 = 11 ETF + Total
_HEAD = "<tr><td></td>" + "".join(f"<th>{c}</th>" for c in COLUMNS) + "</tr>"
_FEE = "<tr><td>Fee</td>" + "".join(f"<td>0.{i}5%</td>" for i in range(len(COLUMNS))) + "</tr>"
_SEED = "<tr><td>Seed</td><td>9,199.3*</td>" + "<td></td>" * (len(COLUMNS) - 1) + "</tr>"


def _row(date: str, **vals: str) -> str:
    """一行；未给的列填 '-'（真实页面里缺失值就是 '-'）。"""
    cells = ["<td>-</td>" if c not in vals else f"<td>{vals[c]}</td>" for c in COLUMNS]
    return f"<tr><td>{date}</td>{''.join(cells)}</tr>"


_SAMPLE = (
    "<html><body><table>\n"
    + _HEAD
    + "\n"
    + _FEE
    + "\n"
    + _SEED
    + "\n"
    + _row(
        "23 Jul 2024",
        ETHA="266.5",
        FETH="71.3",
        ETHW="204.0",
        TETH="7.5",
        ETHV="7.6",
        QETH="5.5",
        EZET="13.2",
        ETHE="(484.1)",
        ETH="15.1",
        Total="106.6",
    )
    + "\n"
    + _row(
        "24 Jul 2024",
        ETHA="17.4",
        FETH="74.5",
        ETHW="29.6",
        TETH="0.0",
        ETHV="19.8",
        QETH="2.5",
        EZET="3.9",
        ETHE="(326.9)",
        ETH="45.9",
        Total="(133.3)",
    )
    + "\n</table></body></html>"
)


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


def test_parse_skips_fee_and_seed_rows():
    """Fee / Seed 等非日期行不进入结果（Seed 的 '9,199.3*' 带星号）。"""
    df = parse_farside(_SAMPLE, COLUMNS)
    assert len(df) == 2
