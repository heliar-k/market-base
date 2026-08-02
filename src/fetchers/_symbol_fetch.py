"""共享编排逻辑：IBKR 优先，yfinance 回退。

stock_fetcher / index_fetcher 的公共实现，不直接作为入口。
"""

import logging
import random
import sys
import time
from pathlib import Path

from ib_insync import IB

from ..config import ROOT, config
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
    sym,
    sym_dict: dict,
    ibkr_cfg,
    last_date: str | None,
    duration_override: str | None,
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
        return fetch_single(ib, contract, name, ibkr_cfg, last_date, duration_override)
    except Exception as e:
        log.error(f"[{name}] IBKR 拉取失败: {e}")
        return None


def _try_yfinance(sym, filepath: Path, existing) -> int:
    """yfinance 回退。返回新增条数。"""
    if not sym.yf_ticker:
        return 0

    name = sym.name
    log.info(f"[{name}] IBKR 无数据，回退到 yfinance ({sym.yf_ticker})...")
    try:
        df = fetch_ohlcv(sym.yf_ticker)
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


def run(symbol_configs, kind: str, args) -> None:
    """执行拉取编排。kind ∈ {"stock", "index"}，决定输出子目录。"""
    ibkr_cfg = config.ibkr
    duration_override = f"{args.days} D" if getattr(args, "days", None) else None

    if getattr(args, "symbols", None):
        requested = set(s.strip().upper() for s in args.symbols.split(","))
        symbols = [s for s in symbol_configs if s.name in requested]
        if not symbols:
            log.error(f"未找到匹配品种: {args.symbols}")
            sys.exit(1)
    else:
        symbols = list(symbol_configs)

    log.info(f"待拉取{kind}品种: {[s.name for s in symbols]}")

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
    subdir = "stocks" if kind == "stock" else "indices"
    output_dir = base_dir / subdir
    output_dir.mkdir(parents=True, exist_ok=True)

    total_new = 0
    for sym in symbols:
        name = sym.name
        log.info(f"--- 开始拉取 {name} ---")

        filepath = (output_dir / f"{name}.csv").resolve()
        existing = load_timeseries(filepath)
        last_date = get_last_date(existing)
        if last_date:
            log.info(f"  已有 {len(existing)} 条本地数据，最新: {last_date}")

        if inter_symbol_delay:
            log.info(f"  等待 {inter_symbol_delay}s ...")
            time.sleep(inter_symbol_delay)

        sym_dict = _to_ibkr_dict(sym, kind)
        bars = _try_ibkr(ib, sym, sym_dict, ibkr_cfg, last_date, duration_override)

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
            n = _try_yfinance(sym, filepath, existing)
            total_new += n

    ib.disconnect()
    log.info(f"全部完成！本次共新增 {total_new} 条记录")
