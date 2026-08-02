"""Fetch Standing Repo Facility (SRF) daily usage from NY Fed Markets API.

写入 data/fred/liquidity/srf.csv（观测日为 key，全量 upsert）。

数据源: NY Fed Markets API /api/rp/results/search.json（官方、免费、无 key）

识别规则（2026-08 验证）：
- 2021-07-29（SRF 上线）~ 2025-12-10：SRF 以 Multiple Price 拍卖运行，每日
  13:30 一场 —— operationType=Repo 且 operationMethod=Multiple Price 即可识别
  （2025-10-31 两次 SRF 操作 20.35+30.0B = Reuters 报道的 50.35B）。排除
  10:30 场（编号 1，SVE 测试：note 含 "small value exercise" 或 releaseTime
  =10:30；2023-03-08 / 2024-09-24 的 SVE note 为空，仅靠 note 会漏）。
- 2025-12-11 起：SRF 改为 Full Allotment，每日固定两场 —— 25 号操作
  （08:15 早场）+ 26/27 号操作（13:30 下午场），operationType=Repo 且
  operationMethod=Full Allotment 即 SRF。SVE 测试操作（operationId 编号 99，
  note 含 "Small Value Exercise"）不计入；POMO/RMP 国债购买是 Outright
  Purchase，在另一套 API，不在此出现。
- 交叉验证：FRED RPONTTLD（全部 repo 总和）与当日所有 SRF 场次接受额
  之和逐日吻合（2025-12-15: 16.801B、2025-12-31: 74.6B、2026-07-30:
  0.008B 等；切换前 3 个 SVE 日子 2022-04-11 / 2023-03-08 / 2024-09-24 的
  10:30 场也不在 RPONTTLD 内）。

SRF_USAGE = 当日所有 SRF 操作 totalAmtAccepted 之和（十亿美元）。

每次运行拉最近 5 天增量 upsert；CSV 不存在（首次）时自动拉全历史。
"""

import logging
from datetime import date, timedelta

import pandas as pd
import requests

logger = logging.getLogger(__name__)

API_URL = "https://markets.newyorkfed.org/api/rp/results/search.json"
SRF_START = "2021-07-01"  # SRF 2021-07-29 上线，往前多拉几天做缓冲
INCREMENTAL_DAYS = 5  # 覆盖一整周的工作日


SRF_METHOD_SWITCH = (
    "2025-12-10"  # 此日（含）前 SRF 为 Multiple Price 拍卖；之后为 Full Allotment
)


def fetch_srf_usage(start: str, end: str) -> pd.DataFrame:
    """拉取 [start, end]（含）区间内 SRF 日度使用量（十亿美元）。

    返回 DataFrame: index=操作日期(ISO str)，列=[SRF_USAGE]；无 SRF 操作时为空。
    """
    resp = requests.get(
        API_URL, params={"startDate": start, "endDate": end}, timeout=30
    )
    resp.raise_for_status()
    ops = resp.json().get("repo", {}).get("operations", [])
    by_day: dict[str, float] = {}
    for op in ops:
        if op.get("operationType") != "Repo":
            continue
        day = op.get("operationDate")
        amt = op.get("totalAmtAccepted") or 0
        method = op.get("operationMethod")
        release = op.get("releaseTime", "")
        note = op.get("note", "") or ""
        if "small value exercise" in note.lower() or release == "10:30":
            # SVE 测试场（切换前 10:30 场 / 切换后编号 99），不计入
            continue
        if method == "Multiple Price" and day <= SRF_METHOD_SWITCH:
            by_day[day] = by_day.get(day, 0) + amt / 1e9
        elif method == "Full Allotment" and day > SRF_METHOD_SWITCH:
            # 2025-12-11 起每日两场 SRF 均为 Full Allotment，全部计入
            by_day[day] = by_day.get(day, 0) + amt / 1e9
    if not by_day:
        return pd.DataFrame()
    return pd.DataFrame({"SRF_USAGE": by_day}).sort_index()


if __name__ == "__main__":
    import argparse

    from ..config import ROOT
    from ._io import load_timeseries, upsert_timeseries

    parser = argparse.ArgumentParser(description="SRF 使用量拉取（NY Fed Markets API）")
    parser.add_argument("--backfill", action="store_true", help="全量覆盖重拉历史")
    args = parser.parse_args()

    path = ROOT / "data" / "fred" / "liquidity" / "srf.csv"

    need_history = args.backfill or load_timeseries(path).empty
    if need_history:
        start, end = SRF_START, date.today().isoformat()
        label = "全历史"
    else:
        start = (date.today() - timedelta(days=INCREMENTAL_DAYS)).isoformat()
        end = date.today().isoformat()
        label = "增量 5 天"

    df = fetch_srf_usage(start, end)
    if df.empty:
        print(f"SRF: {label} 区间内无 SRF 操作（{start} ~ {end}）")
        raise SystemExit(1)

    upsert_timeseries(path, df, backfill=args.backfill)
    print(f"SRF {label} upsert: → {path} ({len(df)} 个操作日, 最新 {df.index[-1]})")
