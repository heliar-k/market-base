"""股票/指数日线拉取统一编排：IBKR 优先，yfinance 回退。

bin/fetch_stock、bin/fetch_index、bin/fetch_ibkr（默认 1d）共用此入口；
ibkr_fetcher 只保留分钟线 / 1w 重采样 / --yf-only 分支。
"""

import argparse
import logging
import random
import sys
import time
from pathlib import Path

from ib_insync import IB

from ..config import INDICES, ROOT, STOCKS, SymbolConfig, config
from ..config import _to_legacy_dict as _to_ibkr_dict
from ._io import load_timeseries, upsert_timeseries
from .ibkr_fetcher import (
    IBKRConnectionError,
    bars_to_dataframe,
    connect_ib,
    fetch_single,
    get_last_date,
    make_contract,
    port_delay,
)
from .yfinance_fetcher import fetch_ohlcv

log = logging.getLogger("symbol_fetch")


def _try_ibkr(
    ib: IB,
    sym: SymbolConfig,
    sym_dict: dict,
    ibkr_cfg,
    last_date: str | None,
    duration_override: str | None,
    connected_port: int | None,
) -> list | None:
    """尝试 IBKR 拉取，返回 bars 列表。失败返回 None。"""
    name = sym.name
    try:
        contract = make_contract(sym_dict)
        ib.qualifyContracts(contract)
        log.info(f"  合约: {contract}")
    except Exception as e:
        log.error(f"[{name}] 合约创建失败: {e}")
        return None

    try:
        return fetch_single(
            ib,
            contract,
            name,
            ibkr_cfg,
            last_date,
            duration_override,
            connected_port=connected_port,
        )
    except Exception as e:
        log.error(f"[{name}] IBKR 拉取失败: {e}")
        return None


def _try_yfinance(sym: SymbolConfig, filepath: Path, existing) -> int:
    """yfinance 回退。返回新增条数。"""
    # US 股票 Yahoo ticker 即 name；指数用显式 yf_ticker（^GSPC 等）
    yf_ticker = sym.yf_ticker or sym.name
    if not yf_ticker:
        return 0

    name = sym.name
    log.info(f"[{name}] IBKR 无数据，回退到 yfinance ({yf_ticker})...")
    try:
        # auto_adjust=False：与 IBKR TRADES 原始价一致
        df = fetch_ohlcv(yf_ticker, auto_adjust=False)
        if df.empty:
            log.warning(f"[{name}] yfinance 也无数据")
            return 0

        if not existing.empty:
            df = df[df.index > existing.index.max()]
        if df.empty:
            log.info(f"[{name}] yfinance 数据均已是最新")
            return 0

        upsert_timeseries(filepath, df)
        log.info(f"[{name}] yfinance 回退成功，新增 {len(df)} 条")
        return len(df)
    except Exception as e:
        log.error(f"[{name}] yfinance 回退失败: {e}")
        return 0


def run(symbols: list[tuple[SymbolConfig, str]], args) -> None:
    """执行拉取编排。symbols 为 (品种配置, kind) 对，kind 决定输出子目录。"""
    ibkr_cfg = config.ibkr
    duration_override = f"{args.days} D" if getattr(args, "days", None) else None

    if getattr(args, "symbols", None):
        requested = set(s.strip().upper() for s in args.symbols.split(","))
        symbols = [(s, k) for s, k in symbols if s.name in requested]
        if not symbols:
            log.error(f"未找到匹配品种: {args.symbols}")
            sys.exit(1)

    log.info(f"待拉取品种: {[s.name for s, _ in symbols]}")

    client_id = getattr(args, "client_id", None) or random.randint(100, 9999)
    log.info(f"使用 clientId: {client_id}")

    try:
        ib, connected_port = connect_ib(client_id)
    except IBKRConnectionError:
        sys.exit(1)
    inter_symbol_delay = port_delay(connected_port)

    if getattr(args, "dry_run", False):
        log.info("--dry-run 模式，仅检查连接，退出")
        ib.disconnect()
        return

    base_dir = ROOT / ibkr_cfg.output_dir

    total_new = 0
    for sym, kind in symbols:
        name = sym.name
        log.info(f"--- 开始拉取 {name} ---")

        subdir = "stocks" if kind == "stock" else "indices"
        output_dir = base_dir / subdir
        output_dir.mkdir(parents=True, exist_ok=True)

        filepath = (output_dir / f"{name}.csv").resolve()
        existing = load_timeseries(filepath)
        last_date = get_last_date(existing)
        if last_date:
            log.info(f"  已有 {len(existing)} 条本地数据，最新: {last_date}")

        if inter_symbol_delay:
            log.info(f"  等待 {inter_symbol_delay}s ...")
            time.sleep(inter_symbol_delay)

        bars = _try_ibkr(
            ib,
            sym,
            _to_ibkr_dict(sym, kind),
            ibkr_cfg,
            last_date,
            duration_override,
            connected_port,
        )

        if bars:
            df = bars_to_dataframe(bars)
            if not existing.empty and not df.empty:
                df = df[df.index > existing.index.max()]
            if not df.empty:
                upsert_timeseries(filepath, df)
                n = len(df)
                total_new += n
                log.info(f"[{name}] 新增 {n} 条")
            else:
                log.info(f"[{name}] 无新数据")
        else:
            total_new += _try_yfinance(sym, filepath, existing)

    ib.disconnect()
    log.info(f"全部完成！本次共新增 {total_new} 条记录")


def main() -> None:
    """CLI 入口：python -m src.fetchers._symbol_fetch stock|index [选项]。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="日线拉取（IBKR 优先，yfinance 回退）")
    parser.add_argument("kind", choices=["stock", "index"], help="拉股票还是指数")
    parser.add_argument("--symbols", help="逗号分隔的品种名称（不指定则拉全部）")
    parser.add_argument("--days", type=int, help="拉取最近 N 天")
    parser.add_argument("--dry-run", action="store_true", help="仅检查连接")
    parser.add_argument("--client-id", type=int, help="指定 clientId（默认随机）")
    args = parser.parse_args()
    pool = STOCKS if args.kind == "stock" else INDICES
    run([(s, args.kind) for s in pool], args)


if __name__ == "__main__":
    main()
