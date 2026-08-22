"""
Fetch US Treasury Daily Treasury Statement (DTS) from Fiscal Data API.
写入 data/treasury/（观测日 upsert，同 FRED 宏观模式）：
  dts_operating_cash.csv: TGA 日度收盘余额（表 I，2022-04 起，百万美元）
  dts_cashflows.csv: TGA 日度现金流（表 II Deposits/Withdrawals 当日合计，百万美元）

数据源（免费无认证）:
  https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/dts
    - operating_cash_balance: 表 I 各类账户开/收盘余额（约 50 行/工作日）
    - deposits_withdrawals_operating_cash: 表 II 逐类存款/支出（约 40 类/工作日）

默认增量拉最近 35 天（窗口覆盖周末/假日与短漏跑，自动补漏）；
--backfill 全量重拉（表 II 全量约 20 万行，20 页，仅首次/修复用）。
"""

import logging
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import requests

from ._io import upsert_timeseries

API_BASE = (
    "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/dts"
)

logger = logging.getLogger(__name__)

# DTS 发布延迟：当日数据于东部次日 10:00 前后发布（UTC 次日），
# 增量窗口取 35 天覆盖周末/假日 + 漏跑
DEFAULT_WINDOW_DAYS = 35


def fetch_operating_cash_balance() -> pd.DataFrame:
    """TGA 日度收盘余额。index=record_date，列 TGA_CLOSE（百万美元，2022-04 起）。

    注意 API 字段命名反直觉："Closing Balance" 行的实际余额放在 open_today_bal
    （close_today_bal 恒为 null；验证：开盘 936406 + 当日存款 295501
    − 当日支出 296830 = 935077，等于该行值 ✓）。
    """
    rows = _fetch_pages(
        f"{API_BASE}/operating_cash_balance",
        filter_expr="account_type:eq:Treasury General Account (TGA) Closing Balance",
    )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["record_date"] = pd.to_datetime(df["record_date"])
    df = df.set_index("record_date").sort_index()
    # close_today_bal 恒 null；真实值在 open_today_bal（该行当日收盘余额）
    val = df["open_today_bal"].where(
        df["close_today_bal"].isna(), df["close_today_bal"]
    )
    df = pd.to_numeric(val, errors="coerce").to_frame("TGA_CLOSE")
    return df.dropna()


def fetch_cashflows(start: str | None = None) -> pd.DataFrame:
    """TGA 日度现金流。index=record_date，列 DEPOSITS / WITHDRAWALS / NET（百万美元）。

    start: 增量起点 YYYY-MM-DD；None 拉取全量历史（2006-01 起）。
    """
    flt = "account_type:eq:Treasury General Account (TGA)"
    if start:
        flt = f"record_date:gte:{start},{flt}"
    rows = _fetch_pages(
        f"{API_BASE}/deposits_withdrawals_operating_cash",
        filter_expr=flt,
    )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["record_date"] = pd.to_datetime(df["record_date"])
    df["amt"] = pd.to_numeric(df["transaction_today_amt"], errors="coerce")
    pivot = df.pivot_table(
        index="record_date", columns="transaction_type", values="amt", aggfunc="sum"
    ).sort_index()
    pivot = pivot.rename(columns={"Deposits": "DEPOSITS", "Withdrawals": "WITHDRAWALS"})
    pivot["NET"] = pivot["DEPOSITS"].fillna(0) - pivot["WITHDRAWALS"].fillna(0)
    return pivot.dropna(subset=["DEPOSITS", "WITHDRAWALS"])


def _fetch_pages(url: str, filter_expr: str | None) -> list[dict]:
    """分页拉取（直连，proxies=None 覆盖 .env SOCKS5，同 treasury_fetcher 模式）。
    ponytail: 与 treasury_fetcher._fetch_all_pages 分页骨架重复，跨 fetcher 抽共享
    helper 需动既有模块，暂留；若再出现第三个同构分页再抽。
    """
    all_rows: list[dict] = []
    page = 1
    name = urlparse(url).path.rstrip("/").split("/")[-1]
    while True:
        params = {
            "format": "json",
            "page[size]": 10000,
            "page[number]": page,
            "sort": "record_date",
        }
        if filter_expr:
            params["filter"] = filter_expr
        resp = requests.get(
            url, params=params, timeout=60, proxies={"http": None, "https": None}
        )
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data", [])
        total_pages = int(body.get("meta", {}).get("total-pages", 1))
        for item in data:
            # 归一化空值字符串（API 用 "null" 字符串表示缺失）
            all_rows.append(
                {k: (None if v in (None, "null", "") else v) for k, v in item.items()}
            )
        if page >= total_pages or not data:
            break
        page += 1
    logger.info(f"  {name}: {len(all_rows)} 条")
    return all_rows


def _out_paths(ROOT: Path) -> tuple[Path, Path]:
    out_dir = ROOT / "data" / "treasury"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / "dts_operating_cash.csv", out_dir / "dts_cashflows.csv"


if __name__ == "__main__":
    import argparse
    import sys
    from datetime import date, timedelta

    from ..config import ROOT

    parser = argparse.ArgumentParser(description="DTS 拉取（表 I 余额 + 表 II 现金流）")
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="全量重拉 dts_cashflows（默认增量最近 35 天）",
    )
    args = parser.parse_args()

    op_path, cf_path = _out_paths(ROOT)

    # ── 表 I：TGA 日度余额（全量，1 页）──
    try:
        ocb = fetch_operating_cash_balance()
    except Exception as e:
        print(f"DTS: operating_cash_balance 拉取失败: {e}")
        sys.exit(1)
    if ocb.empty:
        print("DTS: operating_cash_balance 无数据")
    else:
        upsert_timeseries(op_path, ocb, column_order=["TGA_CLOSE"])
        print(
            f"DTS: → {op_path} ({len(ocb.columns)} 列 × {len(ocb)} 行; "
            f"最新 {ocb.index[-1].date()} TGA {ocb.iloc[-1, 0]:,.0f}M)"
        )

    # ── 表 II：日度现金流（增量 35 天 / --backfill 全量）──
    start = (
        None
        if args.backfill
        else (date.today() - timedelta(days=DEFAULT_WINDOW_DAYS)).isoformat()
    )
    try:
        cf = fetch_cashflows(start)
    except Exception as e:
        print(f"DTS: deposits_withdrawals 拉取失败: {e}")
        sys.exit(1)
    if cf.empty:
        print("DTS: dts_cashflows 无数据")
    else:
        upsert_timeseries(
            cf_path,
            cf,
            backfill=args.backfill,
            column_order=["DEPOSITS", "WITHDRAWALS", "NET"],
        )
        print(
            f"DTS: → {cf_path} ({len(cf.columns)} 列 × {len(cf)} 行; "
            f"最新 {cf.index[-1].date()} 净 {cf.iloc[-1]['NET']:,.0f}M)"
        )
