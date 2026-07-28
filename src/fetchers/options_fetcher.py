#!/usr/bin/env python3
"""
IBKR 期权链参数拉取脚本
========================
从 IB Gateway/TWS 拉取股票期权链的到期日 + 行权价，
保存到 data/options/ 目录。

输出文件:
    data/options/{SYMBOL}_chain.json   - 完整的期权链参数（到期日、行权价、交易所）
    data/options/{SYMBOL}_grid.csv     - 展开的到期日×行权价网格（便于后续分析）

用法:
    ./bin/fetch_options
    ./bin/fetch_options --symbols AAPL,TSLA
    ./bin/fetch_options --dry-run
"""

import argparse
import json
import logging
import random
import sys
from datetime import datetime
from pathlib import Path

from ib_insync import IB, Stock

from ..config import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("options_chain")


def connect_ib(host="127.0.0.1", port=4002, client_id=None):
    if client_id is None:
        client_id = random.randint(100, 9999)
    ib = IB()
    try:
        ib.connect(host, port, clientId=client_id, timeout=10)
        log.info(f"已连接 {host}:{port} (clientId={client_id})")
        return ib
    except Exception as e:
        log.error(f"连接失败: {e}")
        sys.exit(1)


def fetch_chain(ib: IB, sym_name: str) -> list[dict] | None:
    """拉取单只股票的期权链参数，返回各交易所的链数据"""
    contract = Stock(sym_name, "SMART", "USD")
    try:
        ib.qualifyContracts(contract)
        log.info(f"  合约: {contract}")
    except Exception as e:
        log.error(f"  ❌ 合约验证失败: {e}")
        return None

    try:
        chains = ib.reqSecDefOptParams(
            contract.symbol, "", contract.secType, contract.conId
        )
    except Exception as e:
        log.error(f"  ❌ 期权链查询失败: {e}")
        return None

    if not chains:
        log.warning("  ⚠️ 返回空（可能无期权或需数据订阅）")
        return None

    results = []
    for chain in chains:
        results.append(
            {
                "exchange": chain.exchange,
                "tradingClass": chain.tradingClass,
                "multiplier": int(chain.multiplier) if chain.multiplier else 100,
                "expirations": chain.expirations,
                "strikes": [float(s) for s in chain.strikes],
                "num_expirations": len(chain.expirations),
                "num_strikes": len(chain.strikes),
            }
        )

    return results


def save_chain_json(sym_name: str, chains: list[dict], output_dir: Path):
    """保存原始期权链参数为 JSON"""
    payload = {
        "symbol": sym_name,
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "exchanges": chains,
    }
    filepath = output_dir / f"{sym_name}_chain.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log.info(f"  📄 已保存 {filepath}")


def save_chain_csv(sym_name: str, chains: list[dict], output_dir: Path):
    """展开期权链为到期日×行权价网格，保存为 CSV"""
    import csv

    filepath = output_dir / f"{sym_name}_grid.csv"
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["exchange", "expiration", "strike", "multiplier", "trading_class"]
        )

        for chain in chains:
            exchange = chain["exchange"]
            trading_class = chain.get("tradingClass", "")
            multiplier = chain.get("multiplier", 100)
            for exp in chain["expirations"]:
                for strike in chain["strikes"]:
                    writer.writerow([exchange, exp, strike, multiplier, trading_class])

    with open(filepath, "r") as f:
        rows = sum(1 for _ in f) - 1
    log.info(f"  📊 已保存 {filepath} ({rows} 行)")


def main():
    parser = argparse.ArgumentParser(description="IBKR 期权链参数拉取")
    parser.add_argument("--symbols", help="逗号分隔，不指定则拉取全部股票")
    parser.add_argument(
        "--host", default=None, help=f"覆盖配置 (默认: {config.ibkr.host})"
    )
    parser.add_argument(
        "--port", type=int, default=None, help=f"覆盖配置 (默认: {config.ibkr.port})"
    )
    parser.add_argument("--dry-run", action="store_true", help="仅测试连接")
    args = parser.parse_args()

    host = args.host or config.ibkr.host
    port = args.port or config.ibkr.port

    # 只取股票品种
    all_stocks = [s for s in config.ibkr_symbols if s["type"] == "stock"]
    if args.symbols:
        requested = set(s.strip().upper() for s in args.symbols.split(","))
        stocks = [s for s in all_stocks if s["name"] in requested]
        if not stocks:
            log.error(f"未找到匹配股票: {args.symbols}")
            sys.exit(1)
    else:
        stocks = all_stocks

    log.info(f"待拉取股票期权链: {[s['name'] for s in stocks]}")

    ib = connect_ib(host, port)

    if args.dry_run:
        log.info("--dry-run 模式，退出")
        ib.disconnect()
        return

    script_dir = Path(__file__).resolve().parent  # src/fetchers/
    project_root = script_dir.parent.parent  # ticker-toolkit/
    output_dir = project_root / "data" / "options"
    output_dir.mkdir(parents=True, exist_ok=True)

    success = []
    failed = []
    for sym in stocks:
        name = sym["name"]
        log.info(f"--- {name} ---")
        chains = fetch_chain(ib, name)

        if not chains:
            failed.append(name)
            continue

        best = max(chains, key=lambda c: c["num_expirations"] + c["num_strikes"])

        save_chain_json(name, chains, output_dir)
        save_chain_csv(name, chains, output_dir)

        log.info(
            f"  ✅ {name}: {len(chains)} 个交易所, "
            f"最佳链: {best['exchange']} "
            f"({best['num_expirations']} 到期日 × {best['num_strikes']} 行权价)"
        )
        success.append(name)

    ib.disconnect()

    log.info("=" * 50)
    log.info(f"成功: {success}")
    if failed:
        log.warning(f"失败: {failed}")
    log.info(f"输出目录: {output_dir}")


if __name__ == "__main__":
    main()
