"""共享编排逻辑：IBKR 优先，yfinance 回退。

stock_fetcher / index_fetcher 的公共实现，不直接作为入口。
"""

import logging
import random
import sys
import time
from pathlib import Path

from ib_insync import IB

from ..config import _to_legacy_dict as _to_ibkr_dict
from ..config import config
from .ibkr_fetcher import (
    bars_to_dataframe,
    fetch_single,
    get_last_date,
    load_existing_data,
    make_contract,
    save_data,
)
from .yfinance_fetcher import fetch_ohlcv

log = logging.getLogger("symbol_fetch")


def _connect_ib(client_id: int) -> tuple[IB, int]:
    """连接 IBKR，依次尝试 4002 → 4001。返回 (ib, port)。"""
    ibkr_cfg = config.ibkr
    ib = IB()
    for port in [4002, 4001]:
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
    sys.exit(1)


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


def _try_yfinance(sym, filepath: Path, existing, encoding: str) -> int:
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

        save_data(df, filepath, encoding)
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

    ib, connected_port = _connect_ib(client_id)
    inter_symbol_delay = 3 if connected_port == 4001 else ibkr_cfg.request_delay_seconds

    if getattr(args, "dry_run", False):
        log.info("--dry-run 模式，仅检查连接，退出")
        ib.disconnect()
        return

    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent.parent
    base_dir = project_root / ibkr_cfg.output_dir
    subdir = "stocks" if kind == "stock" else "indices"
    output_dir = base_dir / subdir
    output_dir.mkdir(parents=True, exist_ok=True)

    total_new = 0
    for sym in symbols:
        name = sym.name
        log.info(f"--- 开始拉取 {name} ---")

        filepath = (output_dir / f"{name}.csv").resolve()
        existing = load_existing_data(filepath)
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
                save_data(df, filepath, ibkr_cfg.output_encoding)
                n = len(df)
                total_new += n
                log.info(f"[{name}] 新增 {n} 条")
            else:
                log.info(f"[{name}] 无新数据")
        else:
            n = _try_yfinance(sym, filepath, existing, ibkr_cfg.output_encoding)
            total_new += n

    ib.disconnect()
    log.info(f"全部完成！本次共新增 {total_new} 条记录")
