"""
Fetch Fed Funds futures-implied FOMC rate expectations.
数据源: ZQ 30-Day Fed Funds Futures（IBKR 本地 CSV，
经 ./bin/fetch_commodities --symbols ZQ 拉取）→ CME FedWatch 方法论近似。

输出:
  data/rate_expectations/fomc_probabilities.csv  — 每日快照，一行一 FOMC 会议
  data/rate_expectations/zq_futures.csv           — 每日快照，一行一 ZQ 合约

用法:
  uv run python -m src.fetchers.rate_expectations_fetcher           # 拉取+计算
  uv run python -m src.fetchers.rate_expectations_fetcher --backfill # 全量覆盖
"""

import calendar
import logging
from datetime import datetime

import pandas as pd

from ..config import FOMC_MEETINGS, ROOT

logger = logging.getLogger(__name__)

_STEP = 0.25  # Fed 利率步长


def _zq_label(meeting_year: int, meeting_month: int) -> str:
    """ZQ 合约标签，与 commodities 文件名一致：ZQ_{YYYYMM}。"""
    return f"ZQ_{meeting_year}{meeting_month:02d}"


def _read_zq_close(meeting_year: int, meeting_month: int) -> tuple[float, str] | None:
    """从 commodities 本地 CSV 读取 ZQ 合约最新收盘价（= 日结算价）。

    Returns (settlement, as_of_date) 或 None（文件不存在/无数据）。
    """
    path = (
        ROOT
        / "data"
        / "commodities"
        / "ZQ"
        / f"ZQ_{meeting_year}{meeting_month:02d}.csv"
    )
    if not path.exists():
        logger.warning(f"ZQ {meeting_year}-{meeting_month:02d}: 无本地数据 {path}")
        logger.warning("  请先运行: ./bin/fetch_commodities --symbols ZQ")
        return None
    df = pd.read_csv(path, dtype={"date": str})
    if df.empty:
        return None
    last = df.iloc[-1]
    return float(last["close"]), str(last["date"])


def _days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _current_target_range() -> tuple[float, float]:
    """从 FRED 本地数据读取最新 DFEDTARL / DFEDTARU。"""
    p = ROOT / "data" / "fred" / "rates" / "rates.csv"
    if p.exists():
        df = pd.read_csv(p, index_col=0, parse_dates=True)
        cols = ["DFEDTARL", "DFEDTARU"]
        if all(c in df.columns for c in cols):
            latest = df[cols].dropna()
            if not latest.empty:
                r = latest.iloc[-1]
                return float(r["DFEDTARL"]), float(r["DFEDTARU"])
    # ponytail: 硬编码兜底，FRED 数据就绪后自动覆盖
    return 3.50, 3.75


def _range_midpoint(lo: float, hi: float) -> float:
    return (lo + hi) / 2


def _closest_range(r: float) -> tuple[float, float]:
    """给定隐含利率 r，找到最近的 25bp 目标区间。"""
    lo = round(r / _STEP) * _STEP
    if lo > r:
        lo -= _STEP
    return lo, lo + _STEP


def _calc_probabilities(
    post_rate: float, ranges: list[tuple[float, float]]
) -> dict[str, float]:
    """将 post-meeting 隐含利率分配到多个 25bp 目标区间的概率。

    相邻两个区间 [L₀,U₀] [L₁,U₁] 的中点为 m₀ m₁。
    post_rate 落在 m₀ 则概率 100% [L₀,U₀]，线性插值到两区间之间。
    """
    probs: dict[str, float] = {}
    # 只为相邻区间计算概率
    for i, (lo, hi) in enumerate(ranges):
        mid = _range_midpoint(lo, hi)
        if post_rate == mid:
            probs[f"{lo:.2f}-{hi:.2f}"] = 1.0
            for j, (l2, h2) in enumerate(ranges):
                if j != i:
                    probs[f"{l2:.2f}-{h2:.2f}"] = 0.0
            return probs

    # post_rate 落在两个区间 mid 之间
    mids = [(_range_midpoint(lo, hi), lo, hi) for lo, hi in ranges]
    for i in range(len(mids) - 1):
        m0, l0, h0 = mids[i]
        m1, l1, h1 = mids[i + 1]
        if m0 <= post_rate <= m1:
            w1 = (post_rate - m0) / (m1 - m0)
            w0 = 1 - w1
            for _, lo, hi in mids:
                probs[f"{lo:.2f}-{hi:.2f}"] = 0.0
            probs[f"{l0:.2f}-{h0:.2f}"] = round(w0, 4)
            probs[f"{l1:.2f}-{h1:.2f}"] = round(w1, 4)
            return probs

    # 极端情况：选最近的区间
    closest = min(mids, key=lambda x: abs(x[0] - post_rate))
    for _, lo, hi in mids:
        probs[f"{lo:.2f}-{hi:.2f}"] = 0.0
    probs[f"{closest[1]:.2f}-{closest[2]:.2f}"] = 1.0
    return probs


def _expectation_label(current_lo: float, current_hi: float, probs: dict) -> str:
    """根据当前目标区间和概率分布判断市场预期：降息/维持/加息。"""
    p_cut = sum(v for k, v in probs.items() if float(k.split("-")[0]) < current_lo)
    p_hike = sum(v for k, v in probs.items() if float(k.split("-")[1]) > current_hi)
    if p_cut > 0.5:
        return "降息"
    if p_hike > 0.5:
        return "加息"
    return "维持"


def fetch_rate_expectations() -> tuple[pd.DataFrame, pd.DataFrame]:
    """拉取 ZQ 期货并计算 FOMC 概率。

    Returns:
      (fomc_df, zq_df) — FOMC 概率表 + ZQ 合约快照表
    """
    today = datetime.now()
    current_lo, current_hi = _current_target_range()
    logger.info(f"当前目标区间: {current_lo:.2f}%-{current_hi:.2f}%")

    # ── 筛选未来 FOMC 会议 ──
    future = [
        m for m in FOMC_MEETINGS if (m.year, m.month) >= (today.year, today.month)
    ]
    if not future:
        logger.warning("无未来 FOMC 会议")
        return pd.DataFrame(), pd.DataFrame()

    # ── 逐会议读取 ZQ 本地收盘 + 计算 ──
    rows: list[dict] = []
    zq_rows: list[dict] = []

    prev_post_rate = _range_midpoint(current_lo, current_hi)

    for meeting in future:
        contract = _zq_label(meeting.year, meeting.month)
        result = _read_zq_close(meeting.year, meeting.month)
        if result is None:
            continue
        settle, as_of = result
        logger.info(
            f"  {contract}: settle={settle:.4f} (as of {as_of})"
            f" → implied={100 - settle:.4f}%"
        )

        implied = round(100.0 - settle, 4)
        total_days = _days_in_month(meeting.year, meeting.month)
        days_before = meeting.end_day
        days_after = total_days - days_before

        # 分解出 post-meeting 隐含利率
        if days_after > 0:
            post_rate = round(
                (implied * total_days - days_before * prev_post_rate) / days_after, 4
            )
        else:
            post_rate = implied  # 会议在月末最后一天

        # 构建可能的利率范围（当前区间 ± 2 步）
        ranges = []
        for step in range(-3, 4):
            lo = current_lo + step * _STEP
            ranges.append((lo, lo + _STEP))
        probs = _calc_probabilities(post_rate, ranges)
        label = _expectation_label(current_lo, current_hi, probs)

        rows.append(
            {
                "meeting_date": (
                    f"{meeting.year}-{meeting.month:02d}-{meeting.end_day:02d}"
                ),
                "contract": contract,
                "settlement": settle,
                "implied_rate": implied,
                "post_meeting_rate": post_rate,
                "prob_cut": sum(
                    v for k, v in probs.items() if float(k.split("-")[0]) < current_lo
                ),
                "prob_hold": probs.get(f"{current_lo:.2f}-{current_hi:.2f}", 0.0),
                "prob_hike": sum(
                    v for k, v in probs.items() if float(k.split("-")[1]) > current_hi
                ),
                **{f"range_{k}": v for k, v in probs.items()},
                "expectation": label,
            }
        )

        zq_rows.append(
            {
                "contract": contract,
                "month": f"{meeting.year}-{meeting.month:02d}",
                "settlement": settle,
                "implied_rate": implied,
            }
        )

        # 更新"前次会议后利率"用于下次计算
        prev_post_rate = post_rate

    fomc_df = pd.DataFrame(rows)
    zq_df = pd.DataFrame(zq_rows)
    return fomc_df, zq_df


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description="FOMC 利率预期（ZQ 期货 → FedWatch）")
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="全量覆盖已有数据；默认追加",
    )
    args = parser.parse_args()

    fomc_df, zq_df = fetch_rate_expectations()

    if fomc_df.empty:
        print(
            "无数据：未读到 ZQ 本地数据，请先运行 ./bin/fetch_commodities --symbols ZQ"
        )
        raise SystemExit(1)

    out_dir = ROOT / "data" / "rate_expectations"
    out_dir.mkdir(parents=True, exist_ok=True)

    # FOMC 概率表 — 每日快照
    fomc_path = out_dir / "fomc_probabilities.csv"
    today_str = datetime.now().strftime("%Y-%m-%d")
    fomc_df.insert(0, "date", today_str)

    if args.backfill or not fomc_path.exists():
        fomc_df.to_csv(fomc_path, index=False)
        print(f"FOMC 概率 --backfill: → {fomc_path}")
    else:
        existing = pd.read_csv(fomc_path)
        existing = existing[existing["date"] != today_str]
        combined = pd.concat([existing, fomc_df], ignore_index=True)
        combined.to_csv(fomc_path, index=False)
        print(f"FOMC 概率 upsert: → {fomc_path}")

    # ZQ 合约快照
    zq_path = out_dir / "zq_futures.csv"
    zq_df.insert(0, "date", today_str)
    if args.backfill or not zq_path.exists():
        zq_df.to_csv(zq_path, index=False)
        print(f"ZQ 合约 --backfill: → {zq_path}")
    else:
        existing = pd.read_csv(zq_path)
        existing = existing[existing["date"] != today_str]
        combined = pd.concat([existing, zq_df], ignore_index=True)
        combined.to_csv(zq_path, index=False)
        print(f"ZQ 合约 upsert: → {zq_path}")

    # ── 打印摘要 ──
    print()
    for _, r in fomc_df.iterrows():
        print(
            f"  {r['meeting_date']}  {r['contract']:10s}  "
            f"implied={r['implied_rate']:.4f}%  "
            f"cut={r['prob_cut']:.1%}  hold={r['prob_hold']:.1%}  "
            f"hike={r['prob_hike']:.1%}  → {r['expectation']}"
        )
