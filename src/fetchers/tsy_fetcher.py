"""Fetch Treasury Securities Operations (POMO/RMP) details from NY Fed Markets API.

写入 data/fred/liquidity/tsy_operations.csv（操作明细，覆盖写）。

数据源: NY Fed Markets API /api/tsy/{purchases,sales}/results/details/search.json
（官方、免费、无 key；Treasury Securities Operations：Outrights 与价格）

操作级字段（每行一次操作）：
- operation_id / date(操作日) / settlement_date / operation_type
- is_rmp: 2025-12-12（RMP 启动）起的 Outright Bill Purchase = Reserve
  Management Purchases（技术性操作，补充银行准备金，非 QE）；此前为普通 POMO
- maturity_start / maturity_end: 到期范围
- submitted_b / accepted_b: 提交/接受额（十亿美元）
- accept_ratio: 接受比（accepted/submitted，%）
- note: 备注

每次运行拉全量历史覆盖写：源数据量小（每年 ~50 笔），零额外成本。
"""

import logging
import time

import pandas as pd
import requests

logger = logging.getLogger(__name__)

API_TEMPLATE = (
    "https://markets.newyorkfed.org/api/tsy/{op}/results/details/last/{n}.json"
)
HISTORY_N = 300  # 最近 N 笔操作（last 端点上限；300 覆盖 ~5 年）
RMP_START = "2025-12-12"  # Reserve Management Purchases 启动日（timsun 同口径）

_DIRECTION: dict[str, str] = {"P": "purchase", "S": "sale"}


def fetch_tsy_operations() -> pd.DataFrame:
    """拉取全部 Treasury 公开市场操作明细 → DataFrame（按操作日升序）。

    返回列：operation_id, date, settlement_date, operation_type, is_rmp,
    maturity_start, maturity_end, submitted_b, accepted_b, accept_ratio, note。
    """
    frames = []
    for op in ("purchases", "sales"):
        resp = requests.get(
            API_TEMPLATE.format(op=op, n=HISTORY_N),
            timeout=30,
        )
        resp.raise_for_status()
        auctions = resp.json().get("treasury", {}).get("auctions", [])
        frames.append(pd.DataFrame(auctions))
        time.sleep(1)  # 避免触发 NY Fed 限流（400）
    if not frames:
        raise ValueError("empty response")
    df = pd.concat(frames, ignore_index=True)
    if df.empty:
        raise ValueError("empty operations")

    df = df.rename(
        columns={
            "operationId": "operation_id",
            "operationDate": "date",
            "settlementDate": "settlement_date",
            "operationType": "operation_type",
            "maturityRangeStart": "maturity_start",
            "maturityRangeEnd": "maturity_end",
            "totalParAmtSubmitted": "submitted_b",
            "totalParAmtAccepted": "accepted_b",
        }
    )
    for col in ("submitted_b", "accepted_b"):
        df[col] = pd.to_numeric(df[col], errors="coerce") / 1e9
    df["accept_ratio"] = df["accepted_b"] / df["submitted_b"] * 100
    df["is_rmp"] = (df["date"] >= RMP_START) & (
        df["operation_type"] == "Outright Bill Purchase"
    )

    keep = [
        "operation_id",
        "date",
        "settlement_date",
        "operation_type",
        "is_rmp",
        "maturity_start",
        "maturity_end",
        "submitted_b",
        "accepted_b",
        "accept_ratio",
        "note",
    ]
    df = df[keep].sort_values("date").reset_index(drop=True)
    return df


if __name__ == "__main__":
    from ..config import ROOT

    path = ROOT / "data" / "fred" / "liquidity" / "tsy_operations.csv"

    df = fetch_tsy_operations()
    if df.empty:
        print("TSY: 拉取失败，无数据写入")
        raise SystemExit(1)

    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    rmp_n = int(df["is_rmp"].sum())
    print(
        f"TSY 操作明细 → {path} ({len(df)} 笔, "
        f"{df['date'].min()} ~ {df['date'].max()}, 其中 RMP {rmp_n} 笔)"
    )
