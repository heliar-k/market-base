"""分析模块共享小工具：CSV 读取 + 时序取值/变化守卫（credit/rates/volatility 共用）。

口径注意：分位函数刻意不收敛——credit_analysis._pct 用严格 <（FRED OAS 整 bp
并列值多，<= 会虚高分位），volatility_analysis._percentile 用 <=（VIX 连续值），
语义不同，合并会改变行为。
"""

from pathlib import Path

import pandas as pd


def release_dates(fred_dir: Path) -> dict[str, str]:
    """FRED 系列 → last_updated（最近发布/修订日，data/fred/_release_dates.csv）。
    文件由 fetch_fred 每日拉数时写入（Actions 自动更新）。"""
    p = fred_dir / "_release_dates.csv"
    if not p.exists():
        return {}
    df = pd.read_csv(p, dtype=str)
    return dict(zip(df.series_id, df.last_updated))


def read_csv_or_empty(path: Path, index_col: str = "date") -> pd.DataFrame:
    """读时间序列 CSV；文件缺失返回空 DataFrame。"""
    if not path.exists():
        return pd.DataFrame()
    if index_col in ("date", "record_date"):
        # 全项目 CSV date 均为 ISO（AGENTS.md），显式格式避免逐元素 dateutil 回退警告
        return pd.read_csv(
            path, index_col=index_col, parse_dates=True, date_format="ISO8601"
        )
    # 非日期索引（如 refunding 的 id）不做日期解析
    return pd.read_csv(path, index_col=index_col)


# 7×24 标的：周末行是真实行情，不是快照占位
SEVEN_DAY = frozenset({"BTC", "ETH"})


def trading_only(s: pd.Series, seven_day: bool = False) -> pd.Series:
    """剥掉日频快照管线里的周末占位行（7×24 标的传 seven_day=True 原样返回）。

    bin/fetch_yfinance 走 save_daily_csv：按**拉取日**追加一行，周末也把最新
    已知价（与成交量）原样续写。这些行不是观测，却占了索引位：`tail(30)` 只
    有 20 个交易日、chg_pct(s, 14) 实际只回溯 9 天（credit CDS 页实测低估 60%）。

    序列以周末结尾时，尾部周末行携带着 Yahoo 对最后交易日的收盘修订（真数据）
    → 先回灌到那个交易日再删行；周末夹在中间时（后面还有真实交易日）直接删。

    ponytail: 只处理周末。周中节假日停市同样会产生占位行，但要精确剔除得挂
    交易日历（Fed H16 / pandas MarketCalendar），且快照次日续写会自愈；
    需要逐日精确窗口时再升级。
    """
    s = s.dropna()
    if seven_day or s.empty:
        return s
    wknd = s.index.weekday >= 5
    if not wknd.any():
        return s
    if wknd[-1] and (~wknd).any():
        last_wd = s.index[~wknd][-1]
        s.loc[last_wd] = s[s.index > last_wd].iloc[-1]
    return s[~wknd]


def clean_snapshot(df: pd.DataFrame, seven_day=SEVEN_DAY) -> pd.DataFrame:
    """日频快照宽表（列=标的）→ 逐列剥掉周末占位行。

    各列索引长度因此不再一致，但下游一律按列 dropna 后单独取值（credit /
    assets 的分析层都是这个形状），不影响使用。
    """
    return pd.DataFrame(
        {c: trading_only(df[c], seven_day=c in seven_day) for c in df.columns}
    )


def latest(s: pd.Series) -> float | None:
    """最后一个非 NaN 值；空序列返回 None。"""
    s = s.dropna()
    return float(s.iloc[-1]) if not s.empty else None


def chg_prev(s: pd.Series, n: int) -> tuple[float, float] | None:
    """最近值与 n 个交易日前值；样本不足或前值缺失/为零返回 None。

    变化类指标的公共守卫：统一判空条件，避免各副本各自实现导致行为漂移。
    """
    s = s.dropna()
    if len(s) < n + 1:
        return None
    cur, prev = s.iloc[-1], s.iloc[-1 - n]
    if pd.isna(prev) or prev == 0:
        return None  # 缺失或零基数（相对变化会除零）
    return float(cur), float(prev)


def chg_pct(s: pd.Series, n: int) -> float | None:
    """最近值相对 n 个交易日前的变化 %。"""
    pair = chg_prev(s, n)
    return None if pair is None else round((pair[0] / pair[1] - 1) * 100, 2)


def zone(v: float, zones: list[tuple[str, float, float, ...]]) -> str:
    """按 (label, lo, hi[, color]) 区间表查 v 所在区间；表外回落最后一档。

    只返回语义 label：颜色属于展示层（两档制下文字档 / 图形档得分开选），
    后端下发字面 hex 会被前端直接插进 `color:` 槽——亮色下不达 AA 且绕过主题。
    第四列保留给色表本身（如 volatility 的 `zones` 图例，前端只拿去画 background）。
    """
    for label, lo, hi, *_ in zones:
        if lo <= v < hi:
            return label
    return zones[-1][0]
