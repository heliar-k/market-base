#!/usr/bin/env python3
"""
IBKR 日线 K 线数据拉取脚本
===========================
通过 ib_insync 从 Interactive Brokers 拉取日线 OHLCV 数据，
增量更新到本地 CSV 文件。

依赖:
    ib_insync >= 0.9.86
    pandas

前置条件:
    1. TWS 或 IB Gateway 已在本地运行
    2. 已开启 API 端口（TWS → 配置 → API → 启用 ActiveX 和套接字客户端）
    3. 已订阅对应产品的市场数据

用法:
    ./bin/fetch_ibkr
    ./bin/fetch_ibkr --symbols SPX,AAPL
    ./bin/fetch_ibkr --days 365
    ./bin/fetch_ibkr --dry-run
"""

import argparse
import logging
import random
import sys
import time
from datetime import datetime, timezone

import pandas as pd
from ib_insync import IB, Index, Stock, util

from ..config import INDICES, ROOT, STOCKS, config
from ._io import load_timeseries, upsert_timeseries
from .yfinance_fetcher import fetch_ohlcv, yf_minute_bars

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
log = logging.getLogger("ibkr_fetch")


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def make_contract(sym: dict):
    """
    根据配置创建 IB 合约。
    若配置中有 symbol 字段，则用作合约代码（如 DJI -> INDU）。
    指数: Index(symbol, exchange, currency)
    股票: Stock(symbol, exchange, currency)
    """
    symbol = sym.get("symbol", sym["name"])
    if sym["type"] == "index":
        return Index(symbol, sym["exchange"], sym["currency"])
    elif sym["type"] == "stock":
        return Stock(symbol, sym["exchange"], sym["currency"])
    else:
        raise ValueError(f"不支持的品种类型: {sym['type']}")


def get_last_date(df: pd.DataFrame) -> str | None:
    """返回已有数据的最新日期（用于增量拉取）"""
    if df.empty:
        return None
    last = df.index.max()
    if isinstance(last, pd.Timestamp):
        return last.strftime("%Y%m%d-%H:%M:%S")
    return str(last)


def port_delay(connected_port: int | None) -> int:
    """请求间隔（秒）：实盘 4001 日线无硬性 pacing，1s 安全；模拟盘用配置值。"""
    if connected_port == 4001:
        return 1
    return config.ibkr.request_delay_seconds


class IBKRConnectionError(Exception):
    """无法连接 TWS/IB Gateway（所有候选端口均失败）。"""


def connect_ib(
    client_id: int | None = None,
    ports: tuple[int, ...] = (4002, 4001),
) -> tuple[IB, int]:
    """连接 IB Gateway/TWS，依次尝试 ports（默认 4002 paper → 4001 live/readonly）。

    返回 (ib, connected_port)；全部失败 raise IBKRConnectionError。
    readonly 约定：port == 4001 自动只读连接。
    """
    ibkr_cfg = config.ibkr
    if client_id is None:
        client_id = random.randint(100, 9999)
    ib = IB()
    for port in ports:
        readonly = port == 4001
        try:
            log.info("尝试 %s:%d (readonly=%s) ...", ibkr_cfg.host, port, readonly)
            ib.connect(
                ibkr_cfg.host, port, clientId=client_id, timeout=10, readonly=readonly
            )
            log.info("连接成功！端口 %d, readonly=%s", port, readonly)
            return ib, port
        except (ConnectionRefusedError, TimeoutError, OSError):
            log.warning("端口 %d 不可用", port)
            ib.disconnect()
        except Exception as e:
            log.warning("端口 %d 连接失败: %s", port, e)
            ib.disconnect()
    log.error("无法连接到 TWS/IB Gateway，请确认已启动并开启 API 端口")
    log.error("  TWS 纸交易端口: 7497，实盘端口: 7496")
    log.error("  IB Gateway 纸交易端口: 4002，实盘端口: 4001")
    raise IBKRConnectionError("无法连接到 TWS/IB Gateway（已尝试端口 %s）" % (ports,))


def get_option_chain_params(ib: IB, symbol: str) -> list:
    """获取股票期权链参数（全部交易所的链），options_fetcher / compute_gex 共用。"""
    stock = Stock(symbol, "SMART", "USD")
    ib.qualifyContracts(stock)
    return ib.reqSecDefOptParams(stock.symbol, "", stock.secType, stock.conId)


# ---------------------------------------------------------------------------
# 核心拉取逻辑
# ---------------------------------------------------------------------------
def fetch_single(
    ib: IB,
    contract,
    sym_name: str,
    ibkr_cfg,
    last_date: str | None,
    duration_override: str | None = None,
    connected_port: int | None = None,
    bar_size: str | None = None,
) -> list:
    """
    拉取单个品种的历史 K 线（日线/分钟线）。
    每次请求最多约 1000 条，超过则分多次请求回溯。
    """
    all_bars = []
    end_dt = ""  # 空字符串 = 当前时间
    max_iterations = 10  # 防止无限循环
    delay = port_delay(connected_port)
    bar_size = bar_size or ibkr_cfg.bar_size
    duration = duration_override or ibkr_cfg.duration
    # 分钟线必须带时间戳（formatDate=2），日线用 yyyyMMdd 即可
    format_date = 1 if bar_size == "1 day" else 2

    for i in range(max_iterations):
        try:
            bars = ib.reqHistoricalData(
                contract,
                endDateTime=end_dt,
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow=ibkr_cfg.what_to_show,
                useRTH=ibkr_cfg.use_rth,
                formatDate=format_date,  # 2 = yyyyMMdd HH:mm:ss（分钟线必需）
            )
        except Exception as e:
            log.error(f"[{sym_name}] 第 {i + 1} 次请求失败: {e}")
            break

        if not bars:
            log.info(f"[{sym_name}] 第 {i + 1} 次请求返回 0 条，停止")
            break

        log.info(
            f"[{sym_name}] 第 {i + 1} 次请求返回 {len(bars)} 条，"
            f"起始日期: {bars[0].date}"
        )

        # 过滤掉已有数据，只保留新数据（date > last_date）
        if last_date:
            # 完整时间戳比较（日线 bar 为当日零点，分钟线为精确时间）
            last_dt = datetime.strptime(last_date, "%Y%m%d-%H:%M:%S")
            # 统一类型再比较：日线 bar 是 date 对象，分钟线是 UTC tz-aware datetime
            if isinstance(bars[0].date, datetime):
                if bars[0].date.tzinfo is not None and last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
            else:
                last_dt = last_dt.date()
            new_bars = [b for b in bars if b.date > last_dt]
            if not new_bars:
                log.info(f"[{sym_name}] 均已是最新数据，无需增量")
                break
            all_bars.append(new_bars)
            # 如果原始批次最早数据已覆盖到已有数据，停止
            if bars[0].date <= last_dt:
                break
        else:
            all_bars.append(bars)

        # 用最早一条的日期作为下次请求的终点
        end_dt = bars[0].date.strftime("%Y%m%d-%H:%M:%S")
        time.sleep(delay)

    # 展开并去重
    return [b for batch in reversed(all_bars) for b in batch]


def bars_to_dataframe(bars: list) -> pd.DataFrame:
    """将 BarData 列表转为 DataFrame"""
    if not bars:
        return pd.DataFrame()
    df = util.df(bars)
    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)
    df.rename(
        columns={
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "volume": "volume",
            "average": "wap",
            "barCount": "count",
        },
        inplace=True,
    )
    # 保留需要的列
    cols = [
        c
        for c in ["open", "high", "low", "close", "volume", "wap", "count"]
        if c in df.columns
    ]
    return df[cols].sort_index()


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
# 分钟线历史深度受 IBKR 限制（超长需多次回溯，保守值即可）
BAR_MAX_DURATION = {
    "5m": "60 D",
    "15m": "90 D",
    "1h": "120 D",
    "4h": "180 D",
}


def yf_only_fetch(symbols: list, bar_size: str) -> None:
    """仅 yfinance 拉取（--yf-only，无需 TWS）。"""
    base_dir = ROOT / config.ibkr.output_dir
    total = 0
    for sym in symbols:
        # US 股票 Yahoo ticker 即 name；指数/韩股用显式 yf_ticker（^GSPC 等）
        yf_ticker = sym.get("yf_ticker") or sym["name"]
        subdir = "stocks" if sym["type"] == "stock" else "indices"
        if bar_size == "1d":
            # 日线写 {NAME}.csv；yf 为权威源：全量 upsert 覆盖同日，
            # 可自愈 IBKR 盘中写入的部分 bar（volume/count 明显偏小）
            filepath = base_dir / subdir / f"{sym['name']}.csv"
            # 10y 与 IBKR 现有深度相当；更深回溯由本地 IBKR 负责。
            # round(2)：对齐 IBKR 两位小数显示，避免全文件 float 精度噪声
            df = fetch_ohlcv(yf_ticker, period="10y", auto_adjust=False).round(2)
        else:
            # 分钟线只追加（bar 不可变，避免与 IBKR 浮点噪声互相覆盖）
            filepath = base_dir / subdir / f"{sym['name']}_{bar_size}.csv"
            df = yf_minute_bars(yf_ticker, bar_size)
            existing = load_timeseries(filepath)
            if not existing.empty and not df.empty:
                df = df[df.index > existing.index.max()]
        # yfinance 偶发缺 close 的坏行（韩股节前尾 bar）→ 拒写，防 NaN 进 CSV
        df = df.dropna()
        if df.empty:
            log.info(f"[{sym['name']}] 无新数据")
        else:
            upsert_timeseries(filepath, df)
            total += len(df)
            log.info(f"[{sym['name']}] 新增 {len(df)} 条")
        time.sleep(1)  # Yahoo 限流保护（Actions 一次性 ~130 个请求）
    log.info(f"yf-only 完成，共新增 {total} 条")


def fetch_minutes(
    ib: IB,
    connected_port: int,
    symbols: list[dict],
    bar_size: str,
    duration_override: str | None,
) -> int:
    """拉取一个周期的分钟线（复用已建立的 IB 连接），返回新增条数。"""
    ibkr_cfg = config.ibkr
    log.info(f"[{bar_size}] duration: {duration_override or ibkr_cfg.duration}")
    bar_size_ib = {
        "5m": "5 mins",
        "15m": "15 mins",
        "1h": "1 hour",
        "4h": "4 hours",
    }[bar_size]
    base_dir = ROOT / ibkr_cfg.output_dir
    base_dir.mkdir(parents=True, exist_ok=True)
    inter_symbol_delay = port_delay(connected_port)

    total_new = 0
    for sym in symbols:
        name = sym["name"]
        log.info(f"--- 开始拉取 {name} ({bar_size}) ---")

        try:
            contract = make_contract(sym)
            ib.qualifyContracts(contract)
            log.info(f"  合约: {contract}")
        except Exception as e:
            log.error(f"[{name}] 合约创建失败: {e}")
            continue

        subdir = "stocks" if sym["type"] == "stock" else "indices"
        output_dir = base_dir / subdir
        output_dir.mkdir(parents=True, exist_ok=True)
        filepath = (output_dir / f"{name}_{bar_size}.csv").resolve()
        existing = load_timeseries(filepath)
        last_date = get_last_date(existing)
        if last_date:
            log.info(f"  已有 {len(existing)} 条本地数据，最新: {last_date}")

        if inter_symbol_delay:
            log.info(f"  等待 {inter_symbol_delay}s ...")
            time.sleep(inter_symbol_delay)

        try:
            bars = fetch_single(
                ib,
                contract,
                name,
                ibkr_cfg,
                last_date,
                duration_override,
                connected_port=connected_port,
                bar_size=bar_size_ib,
            )
        except Exception as e:
            log.error(f"[{name}] 拉取失败: {e}")
            continue

        if bars:
            df = bars_to_dataframe(bars)
            if not existing.empty and not df.empty:
                df = df[df.index > existing.index.max()]
            upsert_timeseries(filepath, df)
            total_new += len(df)
            log.info(f"[{name}] 新增 {len(df)} 条")
        elif sym.get("yf_ticker") or sym["type"] == "stock":
            # IBKR 无权限 → yfinance 回退（US 股票 ticker 即 name）
            yf_ticker = sym.get("yf_ticker") or sym["name"]
            log.warning(f"[{name}] IBKR 无数据，回退 yfinance {yf_ticker}")
            df = yf_minute_bars(yf_ticker, bar_size)
            if not existing.empty and not df.empty:
                df = df[df.index > existing.index.max()]
            if df.empty:
                log.warning(f"[{name}] yfinance 回退也无新数据")
            else:
                upsert_timeseries(filepath, df)
                total_new += len(df)
                log.info(f"[{name}] yfinance 回退新增 {len(df)} 条")
        else:
            log.info(f"[{name}] 无新数据")
    return total_new


def resample_weekly(symbols: list[str] | None = None) -> None:
    """从已有日线 CSV 重采样周线（周五截止），无需连接 IBKR。"""
    base_dir = ROOT / config.ibkr.output_dir
    if symbols:
        wanted = set(s.upper() for s in symbols)
    else:
        wanted = None
    for sym in config.ibkr_symbols:
        name = sym["name"]
        if wanted and name not in wanted:
            continue
        subdir = "stocks" if sym["type"] == "stock" else "indices"
        src = base_dir / subdir / f"{name}.csv"
        if not src.exists():
            log.warning(f"[{name}] 无日线数据 {src}，跳过")
            continue
        df = load_timeseries(src)
        if df.empty:
            continue
        weekly = (
            df.resample("W-FRI")
            .agg(
                {
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                }
            )
            .dropna()
        )
        # 与分钟线一致：{NAME}_1w.csv 放在资产类型目录下
        out = base_dir / subdir / f"{name}_1w.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        weekly.to_csv(out, encoding=config.ibkr.output_encoding)
        log.info(f"[{name}] 已生成周线: {out} ({len(weekly)} 条)")


def main():
    parser = argparse.ArgumentParser(
        description="IBKR K 线数据拉取（日线/分钟线/周线）"
    )
    parser.add_argument(
        "--symbols", help="逗号分隔的品种名称，如 SPX,AAPL（不指定则拉全部）"
    )
    parser.add_argument(
        "--days", type=int, help="拉取最近 N 天数据（覆盖配置中的 duration）"
    )
    parser.add_argument(
        "--bar-size",
        choices=["1d", "all", "5m", "15m", "1h", "4h", "1w"],
        default="1d",
        help=(
            "K 线周期；all = 一次拉 5m/15m/1h/4h；"
            "1w 从本地日线重采样生成；配合 --yf-only 时 1d/5m/15m/1h/4h"
            "均走 yfinance（无需 TWS）"
        ),
    )
    parser.add_argument(
        "--yf-only",
        action="store_true",
        help="仅 yfinance 拉取（无需 TWS，韩股等 IBKR 无权限品种）",
    )
    parser.add_argument("--dry-run", action="store_true", help="仅检查连接，不拉取数据")
    parser.add_argument("--client-id", type=int, help="指定 clientId（默认随机生成）")
    args = parser.parse_args()

    requested = None
    if args.symbols:
        requested = set(s.strip().upper() for s in args.symbols.split(","))

    # 周线：纯本地重采样，不连 IBKR
    if args.bar_size == "1w":
        resample_weekly(list(requested) if requested else None)
        return

    # 日线：委托 _symbol_fetch 统一编排（单次连接，IBKR 优先 + yfinance 回退）
    if args.bar_size == "1d" and not args.yf_only:
        from ._symbol_fetch import run  # 函数级 import：_symbol_fetch 依赖本模块

        run([(s, "stock") for s in STOCKS] + [(s, "index") for s in INDICES], args)
        return

    # ── 以下为分钟线路径 ──
    symbols = [
        s for s in config.ibkr_symbols if requested is None or s["name"] in requested
    ]
    if not symbols:
        log.error(f"未找到匹配品种: {args.symbols}")
        sys.exit(1)

    # all = 一次拉全部周期
    bar_sizes = ["5m", "15m", "1h", "4h"] if args.bar_size == "all" else [args.bar_size]

    # yf-only：仅 yfinance，不连 IBKR（韩股等 IBKR 无权限品种）
    if args.yf_only:
        for bar in bar_sizes:
            yf_only_fetch(symbols, bar)
        return

    ibkr_cfg = config.ibkr
    log.info(f"待拉取品种: {[s['name'] for s in symbols]}")
    log.info(f"K 线周期: {bar_sizes}")
    log.info(f"TWS 地址: {ibkr_cfg.host}:{ibkr_cfg.port}")

    # 连接 IB — 依次尝试 4002 (paper) → 4001 (live/readonly)
    client_id = args.client_id or random.randint(100, 9999)
    log.info(f"使用 clientId: {client_id}")
    try:
        ib, connected_port = connect_ib(client_id)
    except IBKRConnectionError:
        sys.exit(1)

    if args.dry_run:
        log.info("--dry-run 模式，仅检查连接，退出")
        ib.disconnect()
        return

    # 逐周期拉取（复用同一连接）
    total_new = 0
    for bar in bar_sizes:
        duration_override = f"{args.days} D" if args.days else BAR_MAX_DURATION.get(bar)
        total_new += fetch_minutes(ib, connected_port, symbols, bar, duration_override)
    ib.disconnect()
    log.info(f"全部完成！本次共新增 {total_new} 条记录")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    main()
