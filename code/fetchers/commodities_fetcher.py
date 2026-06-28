"""
Fetch commodity futures OHLCV data from IBKR.
Auto-detects front-month contracts via reqContractDetails.

用法:
    ./bin/fetch_commodities
    ./bin/fetch_commodities --dry-run
    ./bin/fetch_commodities --symbols GC,SI
"""

import argparse
import logging
import random
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
from ib_insync import IB, Future, util

from ..config import config

log = logging.getLogger(__name__)

# ── 商品期货配置 ──
COMMODITY_FUTURES = {
    "GC": ("Gold", "COMEX"),
    "CL": ("WTI", "NYMEX"),
    "SI": ("Silver", "COMEX"),
    "HG": ("Copper", "COMEX"),
}

OUTPUT_DIR = Path("data/commodities")
BAR_SIZE = config.ibkr.bar_size
DURATION = config.ibkr.duration
WHAT_TO_SHOW = config.ibkr.what_to_show
USE_RTH = config.ibkr.use_rth
REQUEST_DELAY = config.ibkr.request_delay_seconds


def _connect(client_id: int | None = None) -> IB:
    """连接 IB Gateway/TWS，返回 IB 实例。"""
    host = config.ibkr.host
    port = config.ibkr.port
    if client_id is None:
        client_id = random.randint(100, 9999)

    ib = IB()
    ib.connect(host, port, clientId=client_id)
    log.info("已连接 %s:%s (clientId=%s)", host, port, client_id)
    return ib


def _resolve_front_month(ib: IB, symbol: str, exchange: str) -> Future:
    """
    自动找到指定商品期货的主力合约（最近月）。
    通过 reqContractDetails 获取所有可用合约，
    过滤出未过期合约，返回到期日最近的一个。
    """
    today = datetime.now().strftime("%Y%m%d")
    c = Future(symbol, exchange=exchange, currency="USD")
    details = ib.reqContractDetails(c)

    if not details:
        raise ValueError(f"{symbol}: 未找到任何合约 @ {exchange}")

    active = [d for d in details if d.contract.lastTradeDateOrContractMonth >= today]
    if not active:
        # 全部过期，取最新过期的一个（可能刚到期还有最后数据）
        active = details
        log.warning("%s: 所有合约已过期，使用最近到期的一个", symbol)

    active.sort(key=lambda d: d.contract.lastTradeDateOrContractMonth)
    contract = active[0].contract
    log.info(
        "  %s 主力: %s conId=%s expiry=%s",
        symbol,
        contract.localSymbol,
        contract.conId,
        contract.lastTradeDateOrContractMonth,
    )
    return contract


def fetch_one_commodity(
    ib: IB, symbol: str, name: str, exchange: str
) -> pd.DataFrame | None:
    """
    拉取单只商品期货的日线 OHLCV 数据。
    返回 DataFrame（columns: date/open/high/low/close/volume/
    average/barCount），失败返回 None。
    """
    try:
        contract = _resolve_front_month(ib, symbol, exchange)
    except Exception as e:
        log.error("  %s (%s): 合约解析失败 — %s", name, symbol, e)
        return None

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
            log.warning("  %s (%s): 无数据返回", name, symbol)
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
        log.info(
            "  %s: %s bars, %s → %s",
            name,
            len(df),
            df["date"].iloc[0],
            df["date"].iloc[-1],
        )
        return df

    except Exception as e:
        log.error("  %s (%s): 数据拉取失败 — %s", name, symbol, e)
        return None


def _save_csv(df: pd.DataFrame, symbol: str) -> None:
    """保存/更新 CSV 文件（增量合并，按 date 去重）。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{symbol}.csv"

    if path.exists():
        existing = pd.read_csv(path, dtype={"date": str})
        combined = pd.concat([existing, df], ignore_index=True).drop_duplicates(
            subset=["date"], keep="last"
        )
        combined = combined.sort_values("date")
    else:
        combined = df

    combined.to_csv(path, index=False, encoding=config.ibkr.output_encoding)
    log.info("  → %s", path)


def fetch_all_commodities(
    symbols: list[str] | None = None,
    dry_run: bool = False,
    client_id: int | None = None,
) -> None:
    """
    拉取指定（或全部）商品期货数据并保存。

    Args:
        symbols: 要拉取的品种列表，None 表示全部
        dry_run: True 则仅连接检查，不拉取数据
        client_id: TWS/IBGW clientId
    """
    ib = _connect(client_id)

    if dry_run:
        log.info("--dry-run 模式，退出")
        ib.disconnect()
        return

    targets = {
        sym: (name, ex)
        for sym, (name, ex) in COMMODITY_FUTURES.items()
        if symbols is None or sym in symbols
    }

    for symbol, (name, exchange) in targets.items():
        log.info("拉取 %s (%s @ %s)...", name, symbol, exchange)
        df = fetch_one_commodity(ib, symbol, name, exchange)
        if df is not None:
            _save_csv(df, symbol)
        time.sleep(REQUEST_DELAY)

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
        client_id=args.client_id,
    )
