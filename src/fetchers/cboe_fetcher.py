"""
Fetch CBOE volatility data: OVX, VIX1D/9D, VIX, VIX3M/6M/1Y, SKEW, term structure.
写入 data/cboe/volatility.csv（观测日为 key，全量 upsert）。

数据源: CBOE CDN CSV（每日更新，含全量历史）
  - OVX (Crude Oil Volatility Index)
  - VIX1D / VIX9D (1-day / 9-day VIX)
  - VIX (30-day VIX；FRED VIXCLS 也有，此处用 CBOE 原始序列算期限结构)
  - VIX3M / VIX6M / VIX1Y（波动率期限结构长端）
  - SKEW（看跌偏斜指数，尾盘对冲成本）
  - VIX_TERM_SLOPE = VIX - VIX9D（正=contango，负=backwardation）

扩展（timsun 波动率面板 30 指数对齐，2026-08）：
  列名用 timsun 面板显示名，URL 用 CBOE 官方代码：
    VXSL→VXSLV（白银）、VTLT→VXTLT（20Y 国债）、VXGO→VXGOG（Google）、
    VXAP→VXAPL（Apple）、VXAZ→VXAZN（Amazon）、VXIB→VXIBM（IBM）
  CBOE CDN 无数据的（S3 403，不含在 URL 表，拉取自动跳过）：
    VXHY（高收益债）、VEWZ（巴西）、VXMO（标准月度）——免费源均验证无；
    VXEF（MSCI 新兴市场）CBOE 2026 公告停止 MSCI 衍生品系列。
  别名（2026-08-06 调研确认，详见 DATA_CATALOG）：VXMT≡VIX6M、VXV≡VIX3M、
    VXNG≡VXUNG、VEEM≡VXEEM（timsun 显示名 vs 官方代码，数值已验证一致）。

每次运行都拉源全量历史并 upsert：忘记运行几天，下次跑自动补齐缺失日期。
"""

import logging
import re
from datetime import datetime
from io import StringIO

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_CSV = "https://cdn.cboe.com/api/global/us_indices/daily_prices/"

CBOE_URLS = {
    "VIX1D": _CSV + "VIX1D_History.csv",
    "OVX": _CSV + "OVX_History.csv",
    "VIX9D": _CSV + "VIX9D_History.csv",
    "VIX": _CSV + "VIX_History.csv",
    "VIX3M": _CSV + "VIX3M_History.csv",
    "VIX6M": _CSV + "VIX6M_History.csv",
    "VIX1Y": _CSV + "VIX1Y_History.csv",
    "SKEW": _CSV + "SKEW_History.csv",
    # ── timsun 面板扩展（2026-08）──
    # 列名 = timsun 显示名；URL 用 CBOE 官方代码（命名不同的已在注释标注）
    "GVZ": _CSV + "GVZ_History.csv",  # 黄金
    "VXSL": _CSV + "VXSLV_History.csv",  # 白银（官方 VXSLV）
    "VXN": _CSV + "VXN_History.csv",  # 纳指 100
    "VXD": _CSV + "VXD_History.csv",  # 道琼斯
    "VVIX": _CSV + "VVIX_History.csv",  # 波动率的波动率
    "VXV": _CSV + "VXV_History.csv",  # 3 个月 VIX
    "VIN": _CSV + "VIN_History.csv",  # Near Term
    "VIF": _CSV + "VIF_History.csv",  # Far Term
    "VXTH": _CSV + "VXTH_History.csv",  # Tail Hedge
    "VTLT": _CSV + "VXTLT_History.csv",  # 20Y 国债（官方 VXTLT）
    "VXGO": _CSV + "VXGOG_History.csv",  # Google（官方 VXGOG）
    "VXGS": _CSV + "VXGS_History.csv",  # 高盛
    "VXAP": _CSV + "VXAPL_History.csv",  # Apple（官方 VXAPL）
    "VXAZ": _CSV + "VXAZN_History.csv",  # Amazon（官方 VXAZN）
    "VXIB": _CSV + "VXIBM_History.csv",  # IBM（官方 VXIBM）
    # ── 调研补充（2026-08-06，见 DATA_CATALOG 别名表）──
    "VXNG": _CSV
    + "VXUNG_History.csv",  # 天然气（timsun 名 VXNG，官方 VXUNG，2020-11 起）
    "VXEEM": _CSV + "VXEEM_History.csv",  # 新兴市场 VIX（EEM 期权版，2011-03 起；
    #   timsun 面板的 VEEM（EWZ 版）无免费源，VXEEM 为近似替代）
}


def fetch_cboe_volatility() -> pd.DataFrame:
    """下载全部 VIX 家族指数全量历史，按观测日对齐。

    单序列拉取失败时跳过并告警；VIX_TERM_SLOPE 需 VIX+VIX9D 同时在场。
    """
    session = requests.Session()
    session.trust_env = False  # 直连 CDN，绕过 .env socks5 代理（代理反而 SSL 断）
    series: dict[str, pd.Series] = {}
    for name, url in CBOE_URLS.items():
        try:
            series[name] = _fetch_cboe_series(name, url, session)
            s = series[name]
            logger.info(f"  CBOE {name}: {len(s)} 条 ({s.index[0]} → {s.index[-1]})")
        except Exception as e:
            logger.warning(f"CBOE {name} 拉取失败: {e}")
    if not series:
        return pd.DataFrame()

    df = pd.DataFrame(series)
    if "VIX" in df.columns and "VIX9D" in df.columns:
        df["VIX_TERM_SLOPE"] = df["VIX"] - df["VIX9D"]
    return df.sort_index()


def _fetch_cboe_series(name: str, url: str, session: requests.Session) -> pd.Series:
    """单个 CBOE CSV → 全量 Series（index=ISO 日期字符串，name=指标名）。"""
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    df = pd.read_csv(StringIO(resp.text))
    if df.empty:
        raise ValueError("empty CSV")

    date_col = _find_column(df.columns, ["DATE", "Date", "date"])
    if not date_col:
        raise ValueError(f"找不到日期列: {list(df.columns)}")
    val_col = _find_column(df.columns, ["OVX", "CLOSE", "Close", "close", name])
    if not val_col:
        # 值列名与指标名不一致（如 VXTLT 的列名是 VXTLT 而非 VTLT）
        # → 取第一个非日期数值列
        for col in df.columns:
            if col != date_col and pd.api.types.is_numeric_dtype(df[col]):
                val_col = col
                break
    if not val_col:
        raise ValueError(f"找不到值列: {list(df.columns)}")

    s = pd.Series(
        df[val_col].astype(float).values,
        index=df[date_col].astype(str).apply(_normalize_date),
        name=name,
    )
    # 同日多条（极少见）取最后一条
    return s[~s.index.duplicated(keep="last")]


def _find_column(columns: list, candidates: list) -> str:
    """Find the first matching column name from candidates (case-insensitive)."""
    for c in candidates:
        for col in columns:
            if col.strip().upper() == c.upper():
                return col
    return ""


def _normalize_date(raw: str) -> str:
    """Convert various date formats to ISO YYYY-MM-DD."""
    raw = str(raw).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return raw
    if re.match(r"^\d{2}/\d{2}/\d{4}$", raw):
        return datetime.strptime(raw, "%m/%d/%Y").strftime("%Y-%m-%d")
    if re.match(r"^\d{2}-\d{2}-\d{4}$", raw):
        return datetime.strptime(raw, "%m-%d-%Y").strftime("%Y-%m-%d")
    for fmt in ["%Y/%m/%d", "%Y-%m-%d", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y"]:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    logger.warning(f"Could not normalize date: {raw}")
    return raw


if __name__ == "__main__":
    import argparse

    from ..config import ROOT
    from ._io import upsert_timeseries

    parser = argparse.ArgumentParser(description="CBOE 波动率数据拉取")
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="全量覆盖（清旧格式 junk，干净重来）；默认为 upsert",
    )
    args = parser.parse_args()

    path = ROOT / "data" / "cboe" / "volatility.csv"

    df = fetch_cboe_volatility()
    if df.empty:
        print("CBOE: 全部源拉取失败，无数据写入")
        raise SystemExit(1)

    upsert_timeseries(path, df, backfill=args.backfill)
    mode = "backfill 覆盖" if args.backfill else "upsert"
    print(f"CBOE {mode}: → {path} ({len(df.columns)} 指标 × {len(df)} 行)")
