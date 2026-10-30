"""
Fetch core asset prices via yfinance.
Needs SOCKS5 proxy: socks5://127.0.0.1:7890

其他模块用到 yfinance 时，先调 ensure_yf_proxy() 做代理设置 + 可达性检测：
    from src.fetchers.yfinance_fetcher import ensure_yf_proxy
    ensure_yf_proxy()
    import yfinance as yf
"""

import csv
import logging
import os
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pandas as pd

from ..clock import ET
from ..config import ROOT, config
from .quality import DataPoint, QAStatus

logger = logging.getLogger(__name__)

# ── 代理 URL（纯计算，无副作用）─────────────────────────────────────────
# YF_NO_PROXY=1（GitHub Actions 无代理环境）：跳过代理设置，直连 Yahoo。
# 兜底：.env 没配代理时用项目默认值。
# 注意用 socks5h://（代理端 DNS 解析）而非 socks5://（本地解析）：
# 本地 DNS 对 Yahoo 域名可能污染，socks5 本地解析会连接失败（curl 000/TLS 错）。
# env 写入与 TCP 探测收敛在 ensure_yf_proxy()；yfinance 在函数内惰性 import。
if os.environ.get("YF_NO_PROXY"):
    _PROXY_URL = ""
else:
    _PROXY_URL = config.https_proxy or "socks5h://127.0.0.1:7890"


def ensure_yf_proxy(timeout: float = 3.0) -> None:
    """设置 yfinance 代理环境变量 + TCP 可达性检测。

    在 import yfinance 之前调用。代理不可达时抛出 ConnectionError。
    import 模块本身零副作用（不写 env、不 import yfinance）；
    调用方约定：ensure_yf_proxy() → import yfinance（惰性）。
    """
    from urllib.parse import urlparse

    os.environ.setdefault("HTTPS_PROXY", _PROXY_URL)
    os.environ.setdefault("HTTP_PROXY", _PROXY_URL)

    u = urlparse(_PROXY_URL)
    host, port = u.hostname, u.port
    if not host or not port:
        return

    try:
        import socket

        s = socket.socket()
        s.settimeout(timeout)
        s.connect((host, port))
        s.close()
    except OSError:
        raise ConnectionError(
            f"代理 {host}:{port} 不可达。\n"
            f"  macOS: 检查 Clash/V2Ray/Surge 是否运行。\n"
            f"  或修改 .env 中的 HTTPS_PROXY 为正确地址。"
        ) from None


def fetch_ohlcv(ticker: str, period: str = "2y", auto_adjust: bool = True) -> pd.DataFrame:
    """拉取 OHLCV 日线，返回 DataFrame，列: open/high/low/close/volume。

    用于 IBKR 不可用时的回退。index 为 date (datetime)。
    默认 auto_adjust=True（拆股+分红复权）；与 IBKR 原始价合并写入同一文件时
    必须传 auto_adjust=False（yf_only_fetch 日线 / _symbol_fetch 回退）。
    """
    logger.info(f"[yfinance fallback] 拉取 {ticker} OHLCV (period={period})...")
    ensure_yf_proxy()
    import yfinance as yf

    t = yf.Ticker(ticker)
    df = t.history(period=period, auto_adjust=auto_adjust)
    if df.empty:
        return pd.DataFrame()
    # 统一列名与 ibkr_fetcher 一致；索引清洗（时区剥离 + 归一到零点）
    # 在此一处完成，各调用方不再各自处理（审计 E-9）
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df.index.name = "date"
    df = df.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    return df[keep].sort_index()


def yf_minute_bars(ticker: str, interval: str) -> pd.DataFrame:
    """yfinance 分钟线 OHLCV（韩股等 IBKR 无权限品种的回退源）。

    interval: "5m" | "15m" | "1h" | "4h"（深度：5m/15m 60 天，1h/4h 2 年）
    Returns 与 ibkr bars_to_dataframe 同构（open/high/low/close/volume，
    index 为带时区 datetime）。调用方需先 ensure_yf_proxy()。
    """
    period = {"5m": "60d", "15m": "60d", "1h": "730d", "4h": "730d"}[interval]
    ensure_yf_proxy()
    import yfinance as yf

    # auto_adjust=False：与 IBKR TRADES 原始价一致（默认 True 会做拆股复权，
    # 混用同一文件时拆股日前后会断层）
    h = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=False)
    if h.empty:
        return pd.DataFrame()
    df = h.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    df.index = pd.to_datetime(df.index)
    df.index.name = "date"
    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    return df[keep]


def fetch_hist(ticker: str) -> pd.DataFrame | None:
    """单标的 5d 日线原始 history（含 Close/Volume 原始列）；失败/空 → None。

    index 与 fetch_ohlcv 同样清洗：时区剥离 + 归一到零点（yfinance 对美股
    返回带 ET 时区的 index，不清洗则与 naive 日期比对永远 miss）。
    """
    try:
        ensure_yf_proxy()
        import yfinance as yf

        hist = yf.Ticker(ticker).history(period="5d")
        if hist.empty:
            return None
        hist.index = pd.to_datetime(hist.index).tz_localize(None).normalize()
        return hist
    except Exception as e:
        logger.info(f"  ✗ {ticker}: {e}")
        return None


def _snapshot_from_hist(name: str, hist: pd.DataFrame) -> DataPoint:
    """从 history 提取最近有效收盘 DataPoint。"""
    dp = DataPoint(metric=name)
    if "Close" not in hist:
        dp.mark_error()
        return dp
    # 最后一行可能为 NaN（亚洲指数数据延迟/时区错位）→ 取最近有效收盘
    close_series = hist["Close"].dropna()
    if close_series.empty:
        dp.mark_error()
        return dp
    dp.value = round(float(close_series.iloc[-1]), 4)
    # 同日成交量（部分品种/时段可能缺失 → None，容忍）
    vol_series = hist["Volume"].dropna() if "Volume" in hist else pd.Series(dtype=float)
    dp.volume = round(float(vol_series.iloc[-1])) if not vol_series.empty else None
    dp.mark_ok()
    return dp


def _fetch_ticker(ticker: str, name: str, hist: pd.DataFrame | None = None) -> DataPoint:
    """Fetch a single yfinance ticker and return a DataPoint.

    hist: 预取的 history（fetch_all_assets 一次拉取两用时传入，避免重复请求）。
    """
    if hist is None:
        hist = fetch_hist(ticker)
    if hist is None or hist.empty:
        dp = DataPoint(metric=name)
        dp.mark_error()
        return dp
    return _snapshot_from_hist(name, hist)


def fetch_all_assets() -> tuple[list[DataPoint], dict[str, pd.DataFrame]]:
    """Fetch all configured yfinance tickers.

    Returns (results, bars)：bars[name] = 5d 日线 history（含 Close/Volume），
    与 DataPoint 出自同一次拉取，供 repair_recent_rows 全列对账复用
    （零额外请求）；失败标的 bars[name] = 空 DataFrame。
    """
    results: list[DataPoint] = []
    bars: dict[str, pd.DataFrame] = {}
    empty = pd.DataFrame()
    for name, ticker in config.yf_tickers.items():
        logger.info(f"Fetching {name} ({ticker})...")
        hist = fetch_hist(ticker)
        bars[name] = hist if hist is not None else empty
        dp = _fetch_ticker(ticker, name, hist)
        results.append(dp)
        status = "✓" if dp.qa_status == QAStatus.OK else "✗"
        logger.info(f"  {status} {name}: {dp.value}")
    return results, bars


def wide_close(series: dict[str, str], period: str = "2y") -> pd.DataFrame:
    """批量 yfinance 日线收盘 → 宽表（index=date，列=key）。

    series: {key: yf_ticker}。失败逐键容忍（日志含 ticker 与异常详情）；
    同 index 重复去重。
    """
    frames: list[pd.DataFrame] = []
    for key, ticker in series.items():
        try:
            df = fetch_ohlcv(ticker, period=period)
        except Exception as e:
            logger.warning(f"  ✗ {key} ({ticker}): {e}")
            continue
        if df.empty:
            logger.warning(f"  ✗ {key} ({ticker}): 空数据")
            continue
        frames.append(pd.DataFrame({key: df["close"]}))
    if not frames:
        return pd.DataFrame()
    wide = pd.concat(frames, axis=1).sort_index()
    return wide[~wide.index.duplicated(keep="last")]


# 快照最少行数：credit_analysis 的 Market Liquidity 子分需要 22 日动量（≥23 条）
_SEED_MIN_ROWS = 23


def seed_history_if_short(filepath: Path) -> None:
    """快照行数不足 _SEED_MIN_ROWS 时，用 yfinance 日线历史回填（close + volume）。

    日频快照每天只追加 1 行，新建文件后要 ~1 个月才能支撑 HYG/LQD 22 日动量
    与 KBWB 偏离度（审计 P1-⑩）。回填只在新文件/长期未跑时触发一次；
    拉取复用 fetch_ohlcv（索引清洗/列名统一），写盘复用 _io.upsert_timeseries
    按观测日合并（审计 D-6），不覆盖既有行。失败逐品种容忍，不影响当日快照。
    """
    from ._io import load_timeseries, upsert_timeseries

    existing = load_timeseries(filepath)
    if len(existing) >= _SEED_MIN_ROWS:
        return
    ensure_yf_proxy()

    # 每个品种一段（date × [close, volume]），外连接拼宽表 → 全品种同日期同行
    frames: list[pd.DataFrame] = []
    for name, ticker in config.yf_tickers.items():
        try:
            df = fetch_ohlcv(ticker, period="2y")
        except Exception as e:
            logger.info(f"  ✗ 回填 {name}: {e}")
            continue
        if df.empty:
            continue
        vol = df["volume"] if "volume" in df else pd.Series(dtype=float)
        frames.append(pd.DataFrame({name: df["close"], f"{name}_volume": vol}))
    if not frames:
        return
    wide = pd.concat(frames, axis=1).sort_index()
    # 列序与 save_daily_csv 首次创建一致：价格列在前、volume 列在后
    wide = wide[
        [c for c in wide.columns if not c.endswith("_volume")]
        + [c for c in wide.columns if c.endswith("_volume")]
    ]
    upsert_timeseries(filepath, wide)
    logger.info(f"  yfinance: 历史回填 {len(wide)} 行 → {filepath.name}")


# 缺口回填回看的快照行数：覆盖周末（周一首班回看到上周五）+ EOD 迟发布（T+1 晚班）
_REPAIR_ROWS = 3

# 美股常规交易时段（ET）：对账窗内日线 bar 未发布时用 1m 会话末根当临时收盘
_SESSION_START = time(9, 30)
_SESSION_END = time(16, 0)


def _et_session_dates(now: datetime) -> list:
    """返回今天与上一工作日中属美股可能交易日的 date（now 须为 aware UTC），供临时收盘注入。"""
    out = []
    for offset in (0, 1):
        d = (now.astimezone(ET) - timedelta(days=offset)).date()
        if d.weekday() < 5:
            out.append(d)
    return out


def _in_reconcile_window(now: datetime) -> bool:
    """对账窗：ET 收盘（16:00）后。盘中快照本就是实时价，无需兑底；
    收盘后日线未发布的临时收盘对齐在此窗内进行。"""
    return now.astimezone(ET).time() >= _SESSION_END


def _session_close(ticker: str, day: date) -> tuple[float, float] | None:
    """1m 拉取该 ticker 在 day 的常规时段末根 → (close, volume)；无会话 → None。

    period=2d 覆盖今天与昨天（1d 只有最近一个会话，昨日缺口兜底会 miss）。
    """
    ensure_yf_proxy()
    import yfinance as yf

    m = yf.Ticker(ticker).history(period="2d", interval="1m")
    if m.empty:
        return None
    idx = m.index
    et = idx.tz_convert(ET) if idx.tz is not None else idx  # 部分版本返回 naive ET 墙钟
    mask = (et.date == day) & (et.time >= _SESSION_START) & (et.time <= _SESSION_END)
    sess = m[mask]
    if sess.empty:
        return None
    closes = sess["Close"].dropna()
    if closes.empty:
        return None
    vol = sess["Volume"].fillna(0).sum() if "Volume" in sess else 0.0
    return float(closes.iloc[-1]), float(vol)


def _late_close_fallback(bars: dict[str, pd.DataFrame], now: datetime | None = None) -> int:
    """EOD 迟发布兑底：日线 bar 缺今日/昨日收盘时，用 1m 会话末根注入 bars。

    Yahoo ETF 日线 EOD 常迟到 T+1 上午（实测 TLT 10-13 延迟 ~19h）；
    1m 的 ET 15:59 末根与正式收盘差分位级，先对齐时效，官方 EOD 发布后
    repair_recent_rows 对账自动升级。日线已有该日 bar 的标的跳过（日线优先）。
    返回注入标的数。

    shortcut: 每 (日×标的) 一次 1m 请求，晨间班次最坏 ~72 请求；
    限流症状出现时改为仅对缺口列发起。
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)  # naive 视作 UTC（调用方约定）
    if not _in_reconcile_window(now):
        return 0
    injected = 0
    for d in _et_session_dates(now):
        day = pd.Timestamp(d)
        for name, ticker in config.yf_tickers.items():
            hist = bars.get(name)
            if hist is None or hist.empty or day in hist.index:
                continue
            try:
                sc = _session_close(ticker, d)
            except Exception as e:
                logger.info(f"  ✗ 1m 兑底 {name}: {e}")
                continue
            if sc is None:
                continue
            bars[name] = pd.concat(
                [
                    hist,
                    pd.DataFrame(
                        {"Close": [sc[0]], "Volume": [sc[1]]},
                        index=pd.DatetimeIndex([day]),
                    ),
                ]
            ).sort_index()
            injected += 1
    return injected


def repair_recent_rows(filepath: Path, bars: dict[str, pd.DataFrame] | None = None) -> int:
    """最近几个快照行与日线 bar 收盘对账（真值源唯一：按 bar 日期）。

    快照以拉取日为 key，源延迟/盘前跑会留下两类伤（2026-10-13 事故）：
    - 缺失：当日拉取失败 / ETF EOD bar 收盘后数小时才发布
    - 复制：盘前班次把前日收盘挂进当日行（与前一交易日值完全相同）
    bars 缺省时只拉缺口列（兜底）；传入 fetch_all_assets 预取的全量 bars
    时做**全列对账**：最近 3 个工作日快照行的每个值必须等于该日 bar 收盘
    （对账基准是 bar 收盘而非快照值——EOD 迟发布时盘中价就是错的），
    不一致即改写。官方 EOD 发布后下一班对账自动升级。无差异零写盘。
    返回修复的列数。
    """
    from ._io import _load_columns, _load_rows

    rows = _load_rows(filepath)
    if len(rows) < 2:
        return 0
    target_dates = [r["date"][:10] for r in rows[-_REPAIR_ROWS:]]
    if bars is None:
        bars = _gap_bars(rows, target_dates)
    if not bars:
        return 0
    rows_by_date = {r["date"][:10]: r for r in rows[-_REPAIR_ROWS:]}
    first_row = rows[-_REPAIR_ROWS] if len(rows) >= _REPAIR_ROWS else rows[0]
    fixed = 0
    for name, ticker in config.yf_tickers.items():
        hist = bars.get(name)
        if hist is None or hist.empty:
            continue
        if "Close" not in hist.columns and "close" in hist.columns:
            # 兼容两种形状：原始 history（Close/Volume）与 fetch_ohlcv（close/volume）
            hist = hist.rename(columns={"close": "Close", "volume": "Volume"})
        cols = [c for c in (name, f"{name}_volume") if c in first_row]
        for d in target_dates:
            day = pd.Timestamp(d)
            if day not in hist.index:
                continue  # 该日无 bar（休市/未发布）→ 保留快照值
            bar_day = hist.loc[day]
            row = rows_by_date[d]
            for c in cols:
                is_vol = c.endswith("_volume")
                src = "Volume" if is_vol else "Close"
                if src not in bar_day.index or pd.isna(bar_day[src]):
                    continue
                new = (
                    str(round(float(bar_day[src])))
                    if is_vol
                    else str(round(float(bar_day[src]), 4))
                )
                if row[c] != new:
                    row[c] = new
                    fixed += 1
    if fixed:
        fieldnames = _load_columns(filepath)  # 先读表头再截断重写
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    return fixed


def _gap_bars(rows: list[dict], target_dates: list[str]) -> dict[str, pd.DataFrame]:
    """兜底模式：只拉缺口列（缺失/复制嫌疑）标的的 bars（旧行为）。

    复制嫌疑 = 值与前一快照行完全相同；周末行不拉（无交易，整行复制属正常）。
    """
    gaps: dict[str, set[str]] = {}
    for i, r in enumerate(rows):
        d = r["date"][:10]
        if d not in target_dates:
            continue
        try:
            if date.fromisoformat(d).weekday() >= 5:
                continue  # 周末行整行复制属正常（无交易），不回填
        except ValueError:
            continue
        prev = rows[i - 1]
        for c, v in r.items():
            if c != "date" and (v in (None, "") or v == prev.get(c)):
                gaps.setdefault(d, set()).add(c)
    if not gaps:
        return {}
    bars: dict[str, pd.DataFrame] = {}
    for name, ticker in config.yf_tickers.items():
        cols = {c for cs in gaps.values() for c in cs if c in (name, f"{name}_volume")}
        if not cols:
            continue
        try:
            bars[name] = fetch_ohlcv(ticker, period="5d")
        except Exception as e:
            logger.info(f"  ✗ 回填 {name}: {e}")
            continue
        if not bars[name].empty:
            continue
        del bars[name]  # 空表剔除，调用方视为无数据
    return bars


if __name__ == "__main__":
    from ._io import save_daily_csv

    results, bars = fetch_all_assets()
    ok = sum(1 for r in results if r.qa_status == QAStatus.OK)
    print(f"yfinance: {ok}/{len(results)} OK")

    path = ROOT / "data" / "yfinance" / "asset_prices.csv"
    save_daily_csv(path, results)
    print("  → data/yfinance/asset_prices.csv")
    _late_close_fallback(bars)
    fixed = repair_recent_rows(path, bars)
    if fixed:
        print(f"  ↻ 对账/回填近 {_REPAIR_ROWS} 行 {fixed} 列")
    seed_history_if_short(path)
