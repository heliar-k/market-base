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
from pathlib import Path

import pandas as pd

from ..config import FED_TARGET_RANGE_FALLBACK, FOMC_MEETINGS, ROOT

logger = logging.getLogger(__name__)

_STEP = 0.25  # Fed 利率步长


def _zq_label(meeting_year: int, meeting_month: int) -> str:
    """ZQ 合约标签，与 commodities 文件名一致：ZQ_{YYYYMM}。"""
    return f"ZQ_{meeting_year}{meeting_month:02d}"


def _read_zq_last(path: Path) -> tuple[float, str] | None:
    """读单个 ZQ 合约 CSV 的最新收盘价，返回 (settlement, date) 或 None。"""
    if not path.exists():
        return None
    df = pd.read_csv(path, dtype={"date": str})
    if df.empty:
        return None
    last = df.iloc[-1]
    return float(last["close"]), str(last["date"])


def _pick_fresher(
    ibkr: tuple[float, str] | None, barchart: tuple[float, str] | None
) -> tuple[float, str] | None:
    """两源取数据日期更新者；同日优先 IBKR（质量更高）。"""
    if ibkr is None:
        return barchart
    if barchart is None:
        return ibkr
    # date 为 ISO 字符串，字典序即时间序
    return barchart if barchart[1] > ibkr[1] else ibkr


def _read_zq_close(
    meeting_year: int, meeting_month: int, root: Path | None = None
) -> tuple[float, str] | None:
    """读取 ZQ 合约最新结算价，两源（IBKR / Barchart）取日期更新者。

    IBKR 本地文件可能停更（TWS 未启动），日期落后于 Barchart 降级源时
    必须用新数据，否则概率基于过期期货价。

    Returns (settlement, as_of_date) 或 None（两源均无数据）。
    """
    root = root or ROOT
    label = _zq_label(meeting_year, meeting_month)
    result = _pick_fresher(
        _read_zq_last(root / "data" / "commodities" / "ZQ" / f"{label}.csv"),
        _read_zq_last(
            root / "data" / "barchart" / "commodities" / "ZQ" / f"{label}.csv"
        ),
    )
    if result is None:
        logger.warning(f"ZQ {meeting_year}-{meeting_month:02d}: 两源均无本地数据")
        logger.warning(
            "  请先运行: ./bin/fetch_commodities --symbols ZQ 或 "
            "./bin/fetch_barchart_futures --symbols ZQ"
        )
    return result


def _days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _has_fomc_meeting(year: int, month: int) -> bool:
    return any(m.year == year and m.month == month for m in FOMC_MEETINGS)


def _use_next_month_contract(
    days_after: int, meeting_year: int, meeting_month: int, next_readable: bool
) -> bool:
    """月末会议定约判定（CME FedWatch 官方方法）。

    会议后本月采样天数 <10 时，本月合约结算价 1bp 噪声被放大 ~10 倍；
    改用下月合约（整月在会后，implied 直接就是 post-meeting rate）。
    下月有 FOMC 会议（合约被污染）或下月合约不可读时回退本月方法。
    days_after == 0 不走此路（现状已直接用 implied）。
    """
    if not (0 < days_after < 10) or not next_readable:
        return False
    ny, nm = _next_month(meeting_year, meeting_month)
    return not _has_fomc_meeting(ny, nm)


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
    # ponytail: 硬编码兜底，FRED 数据就绪后自动覆盖；
    # 常量位置: config.FED_TARGET_RANGE_FALLBACK
    lo, hi = FED_TARGET_RANGE_FALLBACK
    logger.warning(
        f"FRED 目标区间缺失（data/fred/rates/rates.csv），"
        f"使用硬编码兜底 {lo:.2f}-{hi:.2f}；"
        "调息后需更新 config.FED_TARGET_RANGE_FALLBACK，否则概率将错位"
    )
    return FED_TARGET_RANGE_FALLBACK


def _range_midpoint(lo: float, hi: float) -> float:
    return (lo + hi) / 2


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

    使用注意：post-meeting 隐含利率取自 ZQ 结算价，`days_after=3` 采样对
    结算噪声敏感；月末会议（会后采样 <10 天）自动改用下月合约定价
    （FedWatch 官方方法，消除噪声放大）。

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

        # ── 定价输入：默认本月合约；月末会议换下月合约（免噪声放大）──
        price_settle, price_as_of, price_implied = settle, as_of, implied
        contract_used = contract
        post_rate: float | None = None
        if 0 < days_after < 10:
            ny, nm = _next_month(meeting.year, meeting.month)
            nxt = _read_zq_close(ny, nm)
            if _use_next_month_contract(
                days_after, meeting.year, meeting.month, nxt is not None
            ):
                price_settle, price_as_of = nxt
                price_implied = round(100.0 - nxt[0], 4)
                contract_used = _zq_label(ny, nm)
                # 下月整月在会后，implied 直接就是 post-meeting rate
                post_rate = price_implied
                logger.info(
                    f"  月末会议（会后仅 {days_after} 天采样）→ 改用下月合约 "
                    f"{contract_used}: settle={price_settle:.4f} "
                    f"(as of {price_as_of}) → post={post_rate:.4f}%"
                )
        if contract_used == contract:
            # 本月合约方法：从月均 implied 中剥离会前部分
            if days_after > 0:
                post_rate = round(
                    (implied * total_days - days_before * prev_post_rate) / days_after,
                    4,
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
                "contract_used": contract_used,
                "settlement": price_settle,
                "implied_rate": price_implied,
                "post_meeting_rate": post_rate,
                "zq_as_of": price_as_of,
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

    def _write_snapshot(path: Path, df: pd.DataFrame) -> None:
        """快照写入：移除旧文件中同 date 的全部行再合并。

        upsert_timeseries 按唯一观测日设计，对「一日多行」的快照不幂等
        （combine_first 对重复索引按位置对齐 → 产生重复行），此处专用。
        """
        if args.backfill or not path.exists():
            combined = df
        else:
            old = pd.read_csv(path, index_col=0)
            old.index = old.index.astype(str)
            combined = pd.concat([old[~old.index.isin(df.index)], df])
        combined.sort_index().to_csv(path, index_label="date")

    # FOMC 概率表 — 每日快照（date=today 索引，同日覆盖）
    fomc_path = out_dir / "fomc_probabilities.csv"
    today_str = datetime.now().strftime("%Y-%m-%d")
    fomc_df.insert(0, "date", today_str)
    _write_snapshot(fomc_path, fomc_df.set_index("date"))
    print(f"FOMC 概率 {'backfill' if args.backfill else '快照覆盖'}: → {fomc_path}")

    # ZQ 合约快照 — 每日快照（date=today 索引，同日覆盖）
    zq_path = out_dir / "zq_futures.csv"
    zq_df.insert(0, "date", today_str)
    _write_snapshot(zq_path, zq_df.set_index("date"))
    print(f"ZQ 合约 {'backfill' if args.backfill else '快照覆盖'}: → {zq_path}")

    # ── 打印摘要 ──
    print()
    for _, r in fomc_df.iterrows():
        used = f"→ {r['contract_used']} " if r["contract_used"] != r["contract"] else ""
        print(
            f"  {r['meeting_date']}  {r['contract']:10s} {used} "
            f"implied={r['implied_rate']:.4f}%  "
            f"cut={r['prob_cut']:.1%}  hold={r['prob_hold']:.1%}  "
            f"hike={r['prob_hike']:.1%}  → {r['expectation']}"
        )
