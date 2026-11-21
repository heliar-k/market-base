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
    VXMO（标准月度）、VXEF（EFA）——免费源均无历史，由 barchart_vol_fetcher
    逐日快照积累；VXHY 原指数 2019 停发，用 VXHYG（HYG 版）替代。
  别名（2026-08-06 调研确认，详见 DATA_CATALOG）：VXMT≡VIX6M、VXV≡VIX3M、
    VXNG≡VXUNG、VEEM≡VXEEM（timsun 显示名 vs 官方代码，数值已验证一致）。

每次运行都拉源全量历史并 upsert：忘记运行几天，下次跑自动补齐缺失日期。
"""

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from io import StringIO
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter

from ..clock import et_today

logger = logging.getLogger(__name__)

_CSV = "https://cdn.cboe.com/api/global/us_indices/daily_prices/"
_PC_JSON = "https://cdn.cboe.com/data/us/options/market_statistics/daily/{date}_daily_options"
_PC_START_DATE = date(2019, 10, 7)

PUT_CALL_MAP = {
    "TOTAL PUT/CALL RATIO": "TOTAL_PC",
    "INDEX PUT/CALL RATIO": "INDEX_PC",
    "EXCHANGE TRADED PRODUCTS PUT/CALL RATIO": "ETP_PC",
    "EQUITY PUT/CALL RATIO": "EQUITY_PC",
    "CBOE VOLATILITY INDEX (VIX) PUT/CALL RATIO": "VIX_PC",
    "SPX + SPXW + SPXO PUT/CALL RATIO": "SPX_PC",
    "SPX + SPXW PUT/CALL RATIO": "SPX_PC",
}
PUT_CALL_COLS = list(dict.fromkeys(PUT_CALL_MAP.values()))

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
    # VXV 不拉：CDN 上 VXV_History.csv 已被删除（S3 AccessDenied），残档只到 2017-10 且
    # 与 VIX3M 重叠 23 行 diff=0（2018 更名）。dashboard 的 VXV 行直接读 VIX3M 列。
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
    "VXNG": _CSV + "VXUNG_History.csv",  # 天然气（timsun 名 VXNG，官方 VXUNG，2020-11 起）
    "VXEEM": _CSV + "VXEEM_History.csv",  # 新兴市场 VIX（EEM 期权版，2011-03 起；
    #   timsun 面板的 VEEM（EWZ 版）无免费源，VXEEM 为近似替代）
    # ── 补充（2026-08-15，dashboard 30 指数对齐）──
    "VXHY": _CSV + "VXHYG_History.csv",  # 高收益债 VIX（官方 VXHYG=HYG 版，2015-04 起；
    #   timsun 名 VXHY；原 VXHY 指数 2019 年停发）
    "VEWZ": _CSV + "VXEWZ_History.csv",  # 巴西 ETF VIX（官方 VXEWZ，2011-03 起）
    # ── 期权微观结构扩展（离散度与隐含相关性，官方全量历史 CSV）──
    "DSPX": _CSV + "DSPX_History.csv",  # 标普 500 30日隐含离散度
    "VIXEQ": _CSV + "VIXEQ_History.csv",  # 标普 500 个股平均隐含波动率
    "COR1M": _CSV + "COR1M_History.csv",  # 标普 500 1月隐含相关性
    "COR3M": _CSV + "COR3M_History.csv",  # 标普 500 3月隐含相关性
}


def fetch_cboe_volatility() -> pd.DataFrame:
    """下载全部 VIX 家族指数全量历史，按观测日对齐。

    单序列拉取失败时跳过并告警；VIX_TERM_SLOPE 需 VIX+VIX9D 同时在场。
    """
    session = requests.Session()
    session.trust_env = False  # 直连 CDN，绕过 .env socks5 代理（代理反而 SSL 断）
    series: dict[str, pd.Series] = {}
    try:
        for name, url in CBOE_URLS.items():
            try:
                series[name] = _fetch_cboe_series(name, url, session)
                s = series[name]
                logger.info(f"  CBOE {name}: {len(s)} 条 ({s.index[0]} → {s.index[-1]})")
            except Exception as e:
                logger.warning(f"CBOE {name} 拉取失败: {e}")
    finally:
        session.close()
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


def _parse_put_call_payload(payload: dict) -> dict[str, float]:
    """解析 CBOE daily_options JSON 的 ratios 数组为标准列字典。"""
    row: dict[str, float] = {}
    for item in payload.get("ratios") or []:
        name = str(item.get("name", "")).strip().upper()
        col = PUT_CALL_MAP.get(name)
        if not col:
            continue
        raw_val = item.get("value")
        if raw_val in (None, "", "N/A"):
            continue
        try:
            row[col] = float(raw_val)
        except (TypeError, ValueError):
            continue
    return row


def _fetch_put_call_one(
    day_iso: str, session: requests.Session
) -> tuple[str, dict[str, float] | None, bool]:
    """拉取单个交易日的 CBOE Put/Call JSON。

    返回 (日期, 数据字典或 None, 是否发生真实网络/解析异常)；
    HTTP 403/404 视为休市或当日尚未发布，不计入异常。
    """
    url = _PC_JSON.format(date=day_iso)
    try:
        resp = session.get(url, timeout=15)
        if resp.status_code in (403, 404):
            return day_iso, None, False
        resp.raise_for_status()
        parsed = _parse_put_call_payload(resp.json())
        return day_iso, parsed if parsed else None, False
    except Exception as e:
        logger.warning(f"CBOE Put/Call {day_iso} 拉取失败: {e}")
        return day_iso, None, True


def fetch_cboe_put_call(
    existing_path: Path | None = None,
    lookback_days: int | None = None,
    end_date: date | None = None,
    force: bool = False,
) -> pd.DataFrame:
    """增量拉取 CBOE 每日分类 Put/Call 成交量比值。

    首次文件不存在默认回溯 400 个自然日（约 1 年多交易日）；
    增量更新默认回溯 14 个自然日并跳过已有且完整的旧日期。
    全量目标出现真实网络/解析异常且无数据成功写入时抛 RuntimeError，避免静默绿灯。
    """
    end = end_date or et_today()
    existing_complete: set[str] = set()
    if existing_path and existing_path.exists() and not force:
        old = pd.read_csv(existing_path, index_col="date")
        if not old.empty and set(PUT_CALL_COLS).issubset(old.columns):
            complete_mask = old[PUT_CALL_COLS].notna().all(axis=1)
            existing_complete = set(old.index[complete_mask].astype(str))

    days = lookback_days if lookback_days is not None else (400 if not existing_complete else 14)
    start = max(_PC_START_DATE, end - timedelta(days=max(1, days)))
    # 最近 2 个工作日允许刷新，兼容盘后修订
    refresh_cutoff = (end - timedelta(days=3)).isoformat()

    targets: list[str] = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            iso = cur.isoformat()
            if force or iso >= refresh_cutoff or iso not in existing_complete:
                targets.append(iso)
        cur += timedelta(days=1)

    if not targets:
        return pd.DataFrame(columns=PUT_CALL_COLS)

    rows: dict[str, dict[str, float]] = {}
    errors = 0
    workers = min(8, len(targets))
    with requests.Session() as session:
        session.trust_env = False
        adapter = HTTPAdapter(pool_connections=workers, pool_maxsize=workers)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for iso, parsed, failed in pool.map(lambda d: _fetch_put_call_one(d, session), targets):
                if failed:
                    errors += 1
                elif parsed:
                    rows[iso] = parsed

    if not rows:
        if errors > 0:
            raise RuntimeError(f"CBOE Put/Call 拉取异常（{errors}/{len(targets)} 日失败）")
        return pd.DataFrame(columns=PUT_CALL_COLS)

    df = pd.DataFrame.from_dict(rows, orient="index")
    for col in PUT_CALL_COLS:
        if col not in df.columns:
            df[col] = float("nan")
    return df[PUT_CALL_COLS].sort_index()


def check_latest_complete(path: Path) -> list[str]:
    """最新观测日缺值的指标名（派生列 VIX_TERM_SLOPE 跟随 VIX / VIX9D 一起缺）。"""
    if not path.exists():
        return ["文件不存在"]
    df = pd.read_csv(path, index_col="date")
    if df.empty:
        return ["空文件"]
    return list(df.columns[df.iloc[-1].isna()])


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
    parser.add_argument(
        "--check-latest",
        action="store_true",
        help="只查本地 CSV 最新观测日的值是否齐全（不联网），缺列则非零退出",
    )
    parser.add_argument(
        "--pc-days",
        type=int,
        default=None,
        help="Put/Call 增量回溯自然日数（首次自动 400 天，日常默认 14 天）",
    )
    parser.add_argument(
        "--vol-only",
        action="store_true",
        help="仅更新 volatility.csv，跳过 Put/Call（供 cboe-retry 慢文件重试使用）",
    )
    args = parser.parse_args()

    path = ROOT / "data" / "cboe" / "volatility.csv"
    pc_path = ROOT / "data" / "cboe" / "put_call.csv"

    if args.check_latest:
        missing = check_latest_complete(path)
        if missing:
            print("CBOE 最新观测日缺列:" + ",".join(missing))
            raise SystemExit(1)
        print("CBOE 最新观测日齐全")
        raise SystemExit(0)

    df = fetch_cboe_volatility()
    if df.empty:
        print("CBOE: 全部源拉取失败，无数据写入")
        raise SystemExit(1)

    upsert_timeseries(path, df, backfill=args.backfill)
    mode = "backfill 覆盖" if args.backfill else "upsert"
    print(f"CBOE {mode}: → {path} ({len(df.columns)} 指标 × {len(df)} 行)")

    if args.vol_only:
        raise SystemExit(0)

    pc_df = fetch_cboe_put_call(
        existing_path=pc_path,
        lookback_days=args.pc_days,
        force=args.backfill,
    )
    if not pc_df.empty:
        # Put/Call 按日增量抓窗口，始终 upsert 合并，避免 --backfill 截断更早历史
        upsert_timeseries(pc_path, pc_df, backfill=False)
        print(f"CBOE Put/Call upsert: → {pc_path} ({len(pc_df.columns)} 指标 × {len(pc_df)} 新行)")
    elif not pc_path.exists():
        print("CBOE Put/Call: 首次拉取无数据写入")
        raise SystemExit(1)
