"""
宏观派生指标计算模块 — 从原始 FRED 系列派生时序指标。

本模块是纯计算层：接收含原始 FRED 列的 DataFrame，返回追加了派生列的 DataFrame。
不碰 IO，不读 CSV（由调用方负责）。

派生指标清单（公式 / 单位 / 数据源列名）：
  - SPREAD_2S10S        = DGS10 − DGS2                 （百分点，国债收益率曲线斜率）
  - NET_LIQUIDITY       = WALCL − RRPONTSYD − WTREGEN  （百万美元，联储净流动性）
  - BEI_5Y              = (DGS5 − DFII5) × 100         （bp，5 年期盈亏平衡通胀率）
  - BEI_10Y             = (DGS10 − DFII10) × 100       （bp，10 年期盈亏平衡通胀率）
  - SOFR_IORB_SPREAD_BP = (SOFR − IORB) × 100          （bp，融资-准备金利率利差）

设计原则：
  - 派生列只在所有输入列都存在时才计算，缺列则跳过（不报错）。
  - 派生列覆盖同名已存在列（本模块是权威定义）。
"""

import pandas as pd


def _spread_2s10s(df: pd.DataFrame) -> pd.Series | None:
    """2s10s 利差 = DGS10 − DGS2（百分点，国债收益率曲线斜率）。"""
    if not {"DGS10", "DGS2"}.issubset(df.columns):
        return None
    return df["DGS10"] - df["DGS2"]


def _net_liquidity(df: pd.DataFrame) -> pd.Series | None:
    """净流动性 = WALCL − RRPONTSYD − WTREGEN（百万美元，联储净流动性）。

    RRPONTSYD（隔夜逆回购）、WTREGEN（财政部一般账户 TGA）、WALCL（联储总资产）
    均为 FRED liquidity 分类的原始系列，单位百万美元。
    """
    cols = {"WALCL", "RRPONTSYD", "WTREGEN"}
    if not cols.issubset(df.columns):
        return None
    return df["WALCL"] - df["RRPONTSYD"] - df["WTREGEN"]


def _bei_5y(df: pd.DataFrame) -> pd.Series | None:
    """5 年期盈亏平衡通胀率 = (DGS5 − DFII5) × 100（bp）。"""
    if not {"DGS5", "DFII5"}.issubset(df.columns):
        return None
    return (df["DGS5"] - df["DFII5"]) * 100


def _bei_10y(df: pd.DataFrame) -> pd.Series | None:
    """10 年期盈亏平衡通胀率 = (DGS10 − DFII10) × 100（bp）。"""
    if not {"DGS10", "DFII10"}.issubset(df.columns):
        return None
    return (df["DGS10"] - df["DFII10"]) * 100


def _sofr_iorb_spread_bp(df: pd.DataFrame) -> pd.Series | None:
    """SOFR-IORB 利差 = (SOFR − IORB) × 100（bp，融资-准备金利率利差）。"""
    if not {"SOFR", "IORB"}.issubset(df.columns):
        return None
    return (df["SOFR"] - df["IORB"]) * 100


def derive_macro(df: pd.DataFrame) -> pd.DataFrame:
    """对含原始 FRED 列的 df 追加派生宏观指标列。

    输入列缺失时跳过对应派生，不报错。派生列覆盖同名已存在列（本模块是权威定义）。
    """
    out = df.copy()
    derivations = (
        ("SPREAD_2S10S", _spread_2s10s),
        ("NET_LIQUIDITY", _net_liquidity),
        ("BEI_5Y", _bei_5y),
        ("BEI_10Y", _bei_10y),
        ("SOFR_IORB_SPREAD_BP", _sofr_iorb_spread_bp),
    )
    for col, fn in derivations:
        series = fn(out)
        if series is not None:
            out[col] = series
    return out
