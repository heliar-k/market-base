"""数据时效断言 —— Actions 收尾用：job 绿不代表数据新鲜。

用法：
    uv run python -m src.data_freshness      # 打印全表，有落后项则 exit 1

动机（两次真实事故都是「异常被吞、job 全绿」）：
- 2026-09：fredapi 0.5.x 元数据抛 ValueError 被 per-series except 吞掉，
  data/fred/_release_dates.csv 静默冻结近一个月。
- AGENTS.md 记录在案：Actions 漏配 YF_NO_PROXY，options_structure 静默停更 12 天。

本模块不看退出码，只看**产物的末观测日期**：每个数据集给一个「允许落后天数」
（= 源固有滞后 + 周末/发布抖动余量），超线即失败。阈值宁可漏报不误报——
目标是抓住「静默冻结一周以上」，不是抓当天晚几小时。

不覆盖：逐标的价格文件（单只失败已由步骤级 FAILED_LIST 标红）、财报/SEC 原文
（季度节奏）、内部人 Form 4（逐标的 irregular）、GEX/options 网格（本地 IBKR）。
新增 fetcher 时在 CHECKS 补一行。
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import TypedDict

import pandas as pd

from .config import ROOT


class Check(TypedDict):
    """一条时效检查。

    kind="csv"      取单个 CSV 的末观测日（column 缺省用首列）
    kind="json_dir" 取目录下文件名里的最新日期（{YYYYMMDD}.json 快照）
    kind="ahead"    日历型：末行日期必须领先今天至少 budget 天
    """

    path: str
    kind: str
    budget: int
    note: str
    column: str | None


def _c(
    path: str,
    budget: int,
    note: str,
    kind: str = "csv",
    column: str | None = None,
) -> Check:
    return {
        "path": path,
        "kind": kind,
        "budget": budget,
        "note": note,
        "column": column,
    }


# budget 单位=天：日频统一 6（周末 2 天 + 发布抖动），周频/月频按源节奏放宽。
D = 6
CHECKS: list[Check] = [
    # 事故先例：FRED 元数据（列是 last_updated，不是 date）
    _c("data/fred/_release_dates.csv", D, "FRED 元数据", column="last_updated"),
    _c("data/fred/rates/rates.csv", D, "FRED 日频利率"),
    _c("data/fred/volatility/volatility.csv", D, "信用页主序列 VIX/HY_OAS/IG_OAS"),
    _c("data/fred/inflation/inflation.csv", D, "盈亏平衡日频"),
    _c("data/fred/credit/credit.csv", D, "FRED 信用利差"),
    _c("data/fred/tips/tips.csv", D, "TIPS 实际利率"),
    _c("data/fred/liquidity/liquidity.csv", D, "准备金/回购等流动性"),
    _c("data/fred/liquidity/srf.csv", D, "SRF 使用量"),
    _c("data/fred/liquidity/cfets_swap_points.csv", 8, "CFETS 掉期点"),
    _c("data/fred/liquidity/tsy_operations.csv", 10, "RMP/POMO 明细", column="date"),
    _c("data/fred/rates/cgb.csv", D, "中国国债收益率"),
    _c("data/fred/fx/fx.csv", 12, "FRED 汇率（周频抖动大）"),
    _c("data/fred/sentiment/sentiment.csv", 14, "信心指数"),
    _c("data/fred/labor/labor.csv", 14, "劳动市场（含周频初请）"),
    _c("data/fred/labor_market/labor_market.csv", 60, "月频失业率等"),
    _c("data/fred/producer_prices/producer_prices.csv", 45, "月频 PPI（早 CPI 一期）"),
    _c("data/fred/growth/growth.csv", 90, "月频增长，两个发布周期"),
    _c("data/fred/consumption/consumption.csv", 90, "月频消费"),
    _c("data/fred/tic/tic.csv", 150, "TIC 海外持仓，源滞约 3 个月"),
    _c("data/cboe/volatility.csv", D, "CBOE 28 波动率指数"),
    _c("data/ofr/fsi.csv", 10, "OFR 金融压力指数（源约 T+2）"),
    _c("data/shapiro/shapiro.csv", 90, "FRBSF 供需分解（源约 T+2 月）"),
    _c("data/sce/sce.csv", 60, "NY Fed SCE 月频"),
    _c("data/barchart/volatility_snapshot.csv", 8, "Barchart 波动率 30 指数"),
    _c("data/cot/cot.csv", 16, "CFTC COT 周频"),
    _c("data/fx/fx_pairs.csv", D, "外汇 16 对日线"),
    _c("data/etf/pool_prices.csv", D, "精选池 ETF 日线"),
    _c("data/etf_flows/etf_flows.csv", 8, "BTC 现货 ETF 资金流"),
    _c("data/crypto_basis/basis.csv", 10, "CME BTC 基差（丢末根未完成 bar）"),
    _c("data/short_selling/finra_daily.csv", 8, "FINRA 卖空量（源约 T+1）"),
    _c("data/rate_expectations/fomc_probabilities.csv", D, "FOMC 概率"),
    _c("data/rate_expectations/zq_futures.csv", D, "ZQ 期货快照"),
    _c("data/breadth/abv.csv", D, "市场广度 ABV"),
    _c("data/analyst/ndx_targets.csv", 8, "NDX 分析师目标价"),
    _c("data/treasury/bill_share_daily.csv", 8, "日频 Bill 占比（派生）"),
    _c("data/treasury/dts_cashflows.csv", 10, "DTS 现金流"),
    # 日历型：末行必须领先今天 ≥1 天，日历过期即说明源再没更新
    _c(
        "data/treasury/upcoming_auctions.csv",
        1,
        "拍卖日历需领先今天",
        kind="ahead",
        column="auction_date",
    ),
    _c("data/yfinance/asset_prices.csv", D, "yfinance 资产快照"),
    # fed 两件：发布节奏 irregular，blackout 期可连空三周
    _c("data/fed/speeches.csv", 30, "官员演讲", column="date"),
    _c("data/fed/statements.csv", 90, "声明/纪要", column="date"),
    _c("data/options_structure", D, "期权结构快照", kind="json_dir"),
    _c("data/crypto_derivatives", D, "加密衍生品快照（7×24）", kind="json_dir"),
    _c("data/coinglass", D, "Coinglass 聚合快照", kind="json_dir"),
    _c("data/cme_options", 8, "CME 期权墙快照", kind="json_dir"),
]


def _from_filename(names: list[str]) -> date | None:
    """从 {YYYYMMDD}.json 文件名里取最新日期。"""
    found = [
        datetime.strptime(m, "%Y%m%d").date()
        for n in names
        for m in [Path(n).stem[-8:]]
        if len(m) == 8 and m.isdigit()
    ]
    return max(found) if found else None


def _last_csv_date(path: Path, column: str | None) -> date | None:
    """CSV 末观测日（首列或指定列；非日期列返回 None）。

    先转字符串再取前 10 字符，避开两个具体坑：
    - `20260903` 是 int，pandas 当 epoch 纳秒 → 解成 1970（fed 文件）
    - `2026-08-17 15:15:50-05` 混时区 → to_datetime 直接抛错（_release_dates）
    取 ISO 前缀（天）后两者统一能解。
    """
    df = pd.read_csv(path, low_memory=False)
    if df.empty:
        return None
    col = column if column and column in df.columns else df.columns[0]
    s = df[col].dropna().astype(str).str.slice(0, 10)
    try:
        dts = pd.to_datetime(s, errors="coerce", format="mixed").dropna()
    except Exception:
        return None
    return None if dts.empty else dts.max().date()


def latest(path: Path, chk: Check) -> date | None:
    """该数据集当前可用的最新观测日；文件缺失/为空/解析失败 → None。"""
    if chk["kind"] == "json_dir":
        return _from_filename([p.name for p in path.glob("*.json")])
    if not path.exists():
        return None
    return _last_csv_date(path, chk.get("column"))


def audit(
    root: Path,
    today: date,
    checks: list[Check] | None = None,
) -> list[tuple[str, str, int | None, str, int]]:
    """返回 (状态, 数据集, 落后天数, 说明, 预算)；状态 ok / STALE / MISSING。

    落后为负 = 末观测在未来（已排期的拍卖），非 ahead 类型永远算 ok。
    """
    rows = []
    for chk in CHECKS if checks is None else checks:
        last = latest(root / chk["path"], chk)
        if last is None:
            rows.append(("MISSING", chk["path"], None, chk["note"], chk["budget"]))
            continue
        age = (today - last).days
        budget = chk["budget"]
        ok = age <= -budget if chk["kind"] == "ahead" else age <= budget
        status = "ok" if ok else "STALE"
        rows.append((status, chk["path"], age, chk["note"], budget))
    return rows


def main() -> int:
    today = datetime.now(timezone.utc).date()
    rows = audit(Path(ROOT), today)
    bad = [r for r in rows if r[0] != "ok"]
    print(f"数据时效（UTC {today}，{len(rows)} 项）")
    for status, path, age, note, budget in rows:
        a = "  n/a" if age is None else f"{age:>5}"
        print(f"  {status:8} {path:48} 落后{a} 天 / 预算{budget:>4} 天  {note}")
    if not bad:
        print("\n全部在预算内。")
        return 0
    print(f"\n{len(bad)} 项超出预算：")
    for status, path, age, note, budget in bad:
        print(f"  {status} {path}（落后 {age} 天 > {budget}）：{note}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:  # Actions：失败清单写进 job summary，不用翻日志
        lines = [
            "## 数据时效断言失败",
            "",
            "| 状态 | 数据集 | 落后(天) | 预算 | 说明 |",
        ]
        lines += ["|---|---|---|---|---|"]
        lines += [f"| {s} | `{p}` | {a} | {b} | {n} |" for s, p, a, n, b in bad]
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
