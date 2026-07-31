"""
Fetch commodity futures OHLCV data from IBKR.
默认拉取所有未过期合约（整条期货曲线），
--front-month 仅拉取最近月。

用法:
    ./bin/fetch_commodities                    # 全部未过期合约
    ./bin/fetch_commodities --front-month      # 仅主力合约
    ./bin/fetch_commodities --dry-run
    ./bin/fetch_commodities --symbols GC,SI
"""

import argparse
import logging
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
from ib_insync import IB, Future, util

from ..config import config
from .ibkr_fetcher import connect_ib, port_delay

log = logging.getLogger(__name__)

OUTPUT_DIR = Path("data/commodities")
BAR_SIZE = config.ibkr.bar_size
DURATION = config.ibkr.duration
WHAT_TO_SHOW = config.ibkr.what_to_show
USE_RTH = config.ibkr.use_rth


def _resolve_all_contracts(
    ib: IB, symbol: str, exchange: str, max_contracts: int = 24
) -> list[Future]:
    """
    获取指定品种所有未过期合约，按到期日升序排列。
    同月多个合约（如标准+迷你）仅保留第一个。
    max_contracts 限制最大数量（WTI 月月有合约，避免太多）。
    """
    today = datetime.now().strftime("%Y%m%d")
    c = Future(symbol, exchange=exchange, currency="USD")
    details = ib.reqContractDetails(c)

    if not details:
        raise ValueError(f"{symbol}: 未找到任何合约 @ {exchange}")

    active = [d for d in details if d.contract.lastTradeDateOrContractMonth >= today]

    if not active:
        log.warning("%s: 所有合约已过期", symbol)
        return []

    active.sort(key=lambda d: d.contract.lastTradeDateOrContractMonth)

    # 去重：同月只保留第一个
    seen_months: set[str] = set()
    deduped: list[Future] = []
    for d in active:
        month = _expiry_to_month(d.contract.lastTradeDateOrContractMonth)
        if month not in seen_months:
            seen_months.add(month)
            deduped.append(d.contract)

    return deduped[:max_contracts]


def _expiry_to_month(expiry: str) -> str:
    """将 YYYYMMDD 到期日转为 YYYYMM 合约月份。"""
    return expiry[:6]


def _fetch_bars(ib: IB, contract: Future) -> pd.DataFrame | None:
    """拉取单个合约的日线 OHLCV。"""
    try:
        bars = ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=DURATION,
            barSizeSetting=BAR_SIZE,
            whatToShow=WHAT_TO_SHOW,
            useRTH=USE_RTH,
            formatDate=1,
        )
        if not bars:
            return None

        df = util.df(bars)
        df = df.rename(
            columns={
                "date": "date",
                "open": "open",
                "high": "high",
                "low": "low",
                "close": "close",
                "volume": "volume",
                "average": "average",
                "barCount": "barCount",
            }
        )
        df["date"] = df["date"].astype(str)
        return df

    except Exception as e:
        log.warning("    拉取失败: %s", e)
        return None


def _save_csv(df: pd.DataFrame, symbol: str, month: str) -> None:
    """保存增量 CSV，按 date 去重。"""
    subdir = OUTPUT_DIR / symbol
    subdir.mkdir(parents=True, exist_ok=True)
    path = subdir / f"{symbol}_{month}.csv"

    if path.exists():
        existing = pd.read_csv(path, dtype={"date": str})
        combined = pd.concat([existing, df], ignore_index=True).drop_duplicates(
            subset=["date"], keep="last"
        )
        combined = combined.sort_values("date")
    else:
        combined = df

    combined.to_csv(path, index=False, encoding=config.ibkr.output_encoding)
    log.info("    → %s", path)


def fetch_commodity_contracts(
    ib: IB,
    symbol: str,
    name: str,
    exchange: str,
    front_month_only: bool = False,
    connected_port: int | None = None,
) -> None:
    """
    拉取单只品种的全部（或仅主力）合约数据并保存。

    Args:
        front_month_only: True 仅拉最近月合约
        connected_port: 已连接的 IBKR 端口（决定请求间隔）
    """
    try:
        contracts = _resolve_all_contracts(ib, symbol, exchange)
    except Exception as e:
        log.error("  %s (%s): 合约搜索失败 — %s", name, symbol, e)
        return

    if not contracts:
        log.warning("  %s (%s): 无活跃合约", name, symbol)
        return

    target = contracts[:1] if front_month_only else contracts
    mode = "主力" if front_month_only else f"全部 {len(target)} 个"
    log.info(
        "拉取 %s (%s @ %s) — %s合约",
        name,
        symbol,
        exchange,
        mode,
    )

    for i, contract in enumerate(target):
        month = _expiry_to_month(contract.lastTradeDateOrContractMonth)
        local = contract.localSymbol
        log.info(
            "  [%d/%d] %s expiry=%s conId=%s",
            i + 1,
            len(target),
            local,
            contract.lastTradeDateOrContractMonth,
            contract.conId,
        )

        df = _fetch_bars(ib, contract)
        if df is not None:
            _save_csv(df, symbol, month)
            log.info(
                "      %s bars, %s → %s",
                len(df),
                df["date"].iloc[0],
                df["date"].iloc[-1],
            )
        else:
            log.warning("      无数据")

        if i < len(target) - 1:
            time.sleep(port_delay(connected_port))


def fetch_all_commodities(
    symbols: list[str] | None = None,
    dry_run: bool = False,
    front_month_only: bool = False,
    client_id: int | None = None,
) -> None:
    """拉取所有配置的商品期货数据。"""
    ib, connected_port = connect_ib(client_id)

    if dry_run:
        log.info("--dry-run 模式，退出")
        ib.disconnect()
        return

    targets = {
        sym: (name, ex)
        for sym, (name, ex) in config.commodity_futures.items()
        if symbols is None or sym in symbols
    }

    inter_commodity_delay = port_delay(connected_port)

    for symbol, (name, exchange) in targets.items():
        fetch_commodity_contracts(
            ib, symbol, name, exchange, front_month_only, connected_port
        )
        time.sleep(inter_commodity_delay)

    ib.disconnect()
    log.info("完成")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="IBKR 商品期货 OHLCV 数据拉取")
    parser.add_argument(
        "--symbols",
        type=str,
        default=None,
        help="逗号分隔的品种代码，如 GC,CL,SI,HG",
    )
    parser.add_argument(
        "--front-month",
        action="store_true",
        help="仅拉取主力合约（最近月），默认拉全部未过期合约",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅测试连接，不拉取数据",
    )
    parser.add_argument(
        "--client-id",
        type=int,
        default=None,
        help="IB Gateway client ID",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    symbol_list = [s.strip() for s in args.symbols.split(",")] if args.symbols else None

    fetch_all_commodities(
        symbols=symbol_list,
        dry_run=args.dry_run,
        front_month_only=args.front_month,
        client_id=args.client_id,
    )
