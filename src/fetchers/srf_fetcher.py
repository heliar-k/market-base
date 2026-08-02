"""Fetch Standing Repo Facility (SRF) daily usage from NY Fed Markets API.

写入 data/fred/liquidity/srf.csv（观测日为 key，全量 upsert）。

数据源: NY Fed Markets API /api/rp/results/search.json（官方、免费、无 key）

识别规则与已知限制（2026-08 验证）：
- 2021-07-29（SRF 上线）~ 2025-12-10：SRF 以 Multiple Price 拍卖运行 ——
  operationType=Repo 且 operationMethod=Multiple Price 即可精确识别
  （2025-10-31 两次 SRF 操作 20.35+30.0B = Reuters 报道的 50.35B）。
- 2025-12-11 起：SRF 与 POMO 同为 Full Allotment 格式，API 不再区分
  （FRED RPONTTLD 同日起等于 Full Allotment 总和、且 2025-10-31 的
  RPONTTLD=50.35B 已含 SRF）。SRF 单独使用量自此无官方来源，timsun 页面
  同样显示 0。→ 本 fetcher 2025-12-11 后 SRF_USAGE 记 0；若单日 Full
  Allotment 使用 >1B（可能含 SRF），告警提示人工核对。SVE（Small Value
  Exercise，测试操作）不计入使用量。

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


SRF_METHOD_SWITCH = "2025-12-10"  # 此日（含）前 SRF 为 Multiple Price；之后不再单独披露
# 切换后 Full Allotment 单日使用超过该值（十亿美元）时告警（可能含 SRF）
WARN_THRESHOLD_B = 1.0


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
        if method == "Multiple Price" and day <= SRF_METHOD_SWITCH:
            if "Small Value Exercise" not in op.get("note", ""):  # SVE 为测试，不计
                by_day[day] = by_day.get(day, 0) + amt / 1e9
        elif (
            method == "Full Allotment"
            and day > SRF_METHOD_SWITCH
            and amt / 1e9 > WARN_THRESHOLD_B
        ):
            # 2025-12-11 后 SRF 与 POMO 同为 Full Allotment，无法拆分；大额使用时告警
            logger.warning(
                f"{day} {op.get('operationId')}: Full Allotment 使用 {amt / 1e9:.1f}B"
                f"（{SRF_METHOD_SWITCH} 后 SRF 不再单独披露，可能含 SRF，需人工核对）"
            )
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
