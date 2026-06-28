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
    uv run python code/fetchers/ibkr_fetcher.py
    uv run python code/fetchers/ibkr_fetcher.py --symbols SPX,AAPL
    uv run python code/fetchers/ibkr_fetcher.py --days 365
    uv run python code/fetchers/ibkr_fetcher.py --dry-run
    uv run python code/fetchers/ibkr_fetcher.py --client-id 12345
"""

import argparse
import logging
import random
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
from ib_insync import IB, Index, Stock, util

# 支持直接执行时找到 code/ 下的兄弟模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import config

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
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


def load_existing_data(filepath: Path) -> pd.DataFrame:
    """加载已有的本地数据文件"""
    if not filepath.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(filepath, parse_dates=["date"])
        df.set_index("date", inplace=True)
        return df
    except Exception as e:
        log.warning(f"读取已有数据失败 {filepath}: {e}，将重新拉取")
        return pd.DataFrame()


def get_last_date(df: pd.DataFrame) -> str | None:
    """返回已有数据的最新日期（用于增量拉取）"""
    if df.empty:
        return None
    last = df.index.max()
    if isinstance(last, pd.Timestamp):
        return last.strftime("%Y%m%d-%H:%M:%S")
    return str(last)


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
) -> list:
    """
    拉取单个品种的历史日线。
    每次请求最多约 1 年日线，超过则分多次请求。
    """
    all_bars = []
    end_dt = ""  # 空字符串 = 当前时间
    max_iterations = 5  # 防止无限循环
    delay = ibkr_cfg.request_delay_seconds
    duration = duration_override or ibkr_cfg.duration

    for i in range(max_iterations):
        try:
            bars = ib.reqHistoricalData(
                contract,
                endDateTime=end_dt,
                durationStr=duration,
                barSizeSetting=ibkr_cfg.bar_size,
                whatToShow=ibkr_cfg.what_to_show,
                useRTH=ibkr_cfg.use_rth,
                formatDate=1,  # yyyyMMdd
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
            last_dt = datetime.strptime(last_date, "%Y%m%d-%H:%M:%S").date()
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


def save_data(df: pd.DataFrame, filepath: Path, encoding: str = "utf-8"):
    """保存到 CSV，自动合并已有数据"""
    existing = load_existing_data(filepath)

    if not df.empty:
        combined = pd.concat([existing, df])
        combined = combined[~combined.index.duplicated(keep="last")]
        combined.sort_index(inplace=True)
    else:
        combined = existing

    if combined.empty:
        log.warning(f"无数据可保存: {filepath}")
        return

    filepath.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(filepath, encoding=encoding)
    log.info(f"已保存: {filepath} ({len(combined)} 条)")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="IBKR 日线 K 线数据拉取")
    parser.add_argument(
        "--symbols", help="逗号分隔的品种名称，如 SPX,AAPL（不指定则拉全部）"
    )
    parser.add_argument(
        "--days", type=int, help="拉取最近 N 天数据（覆盖配置中的 duration）"
    )
    parser.add_argument("--dry-run", action="store_true", help="仅检查连接，不拉取数据")
    parser.add_argument("--client-id", type=int, help="指定 clientId（默认随机生成）")
    args = parser.parse_args()

    ibkr_cfg = config.ibkr
    duration_override = f"{args.days} D" if args.days else None

    # 筛选品种
    all_symbols = config.ibkr_symbols
    if args.symbols:
        requested = set(s.strip().upper() for s in args.symbols.split(","))
        symbols = [s for s in all_symbols if s["name"] in requested]
        if not symbols:
            log.error(f"未找到匹配品种: {args.symbols}")
            sys.exit(1)
    else:
        symbols = all_symbols

    log.info(f"待拉取品种: {[s['name'] for s in symbols]}")
    log.info(f"TWS 地址: {ibkr_cfg.host}:{ibkr_cfg.port}")

    # 连接 IB
    client_id = args.client_id or random.randint(100, 9999)
    log.info(f"使用 clientId: {client_id}")
    ib = IB()
    try:
        ib.connect(ibkr_cfg.host, ibkr_cfg.port, clientId=client_id, timeout=10)
    except (ConnectionRefusedError, TimeoutError):
        log.error("无法连接到 TWS/IB Gateway，请确认已启动并开启 API 端口")
        log.error("  TWS 纸交易端口: 7497，实盘端口: 7496")
        log.error("  IB Gateway 纸交易端口: 4002，实盘端口: 4001")
        sys.exit(1)
    except OSError as e:
        log.error(f"连接超时: {e}")
        sys.exit(1)
    except Exception as e:
        log.error(f"连接失败: {e}")
        sys.exit(1)

    log.info("连接成功！")

    if args.dry_run:
        log.info("--dry-run 模式，仅检查连接，退出")
        ib.disconnect()
        return

    # 输出目录
    script_dir = Path(__file__).resolve().parent  # code/fetchers/
    project_root = script_dir.parent.parent  # K线分析/
    base_dir = project_root / ibkr_cfg.output_dir
    base_dir.mkdir(parents=True, exist_ok=True)

    # 逐品种拉取
    total_new = 0
    for sym in symbols:
        name = sym["name"]
        log.info(f"--- 开始拉取 {name} ---")

        try:
            contract = make_contract(sym)
            ib.qualifyContracts(contract)
            log.info(f"  合约: {contract}")
        except Exception as e:
            log.error(f"[{name}] 合约创建失败: {e}")
            continue

        # 按品种类型分目录: stock → data/stocks, index → data/indices
        subdir = "stocks" if sym["type"] == "stock" else "indices"
        output_dir = base_dir / subdir
        output_dir.mkdir(parents=True, exist_ok=True)

        # 检查本地已有数据
        filepath = output_dir / f"{name}.csv"
        filepath = filepath.resolve()
        existing = load_existing_data(filepath)
        last_date = get_last_date(existing)
        if last_date:
            log.info(f"  已有 {len(existing)} 条本地数据，最新: {last_date}")

        # 拉取
        try:
            bars = fetch_single(
                ib,
                contract,
                name,
                ibkr_cfg,
                last_date,
                duration_override,
            )
        except Exception as e:
            log.error(f"[{name}] 拉取失败: {e}")
            continue

        if bars:
            df = bars_to_dataframe(bars)
            if not existing.empty and not df.empty:
                df = df[df.index > existing.index.max()]
            save_data(df, filepath, ibkr_cfg.output_encoding)
            new_count = len(df)
            total_new += new_count
            log.info(f"[{name}] 新增 {new_count} 条")
        else:
            log.info(f"[{name}] 无新数据")

        log.info(f"  等待 {ibkr_cfg.request_delay_seconds}s 以避免请求限流...")
        time.sleep(ibkr_cfg.request_delay_seconds)

    ib.disconnect()
    log.info(f"全部完成！本次共新增 {total_new} 条记录")


if __name__ == "__main__":
    main()
