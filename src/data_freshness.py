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

日期预算之外另有两条**值维断言**：us_indices_placeholder_msg（asset_prices 这类
「拉取日为 key」的快照可能日期新鲜、值是复制（2026-10-13 占位行事故），
在收盘确认窗用交叉 ETF 作证单独拦）与 roster_year_msg（票委名册只有 year 列，
跨年不换 = fetch_roster 停了，页面会把去年的人当现任票委展示）。

不覆盖：逐标的价格文件（单只失败已由步骤级 FAILED_LIST 标红）、财报/SEC 原文
（季度节奏）、内部人 Form 4（逐标的 irregular）、GEX/options 网格（本地 IBKR）。
新增 fetcher 时在 CHECKS 补一行。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import TypedDict
from zoneinfo import ZoneInfo

import pandas as pd

from .config import ROOT


class Check(TypedDict):
    """一条时效检查。

    kind="csv"      取单个 CSV 的末观测日（column 缺省用首列）
    kind="json_dir" 取目录下文件名里的最新日期（{YYYYMMDD}.json 快照）
    kind="csv_dir_globs" 同 json_dir，但扫目录下 *.csv（raw_{sym}_{YYYYMMDD}.csv）
    kind="ahead"    日历型：末行日期必须领先今天至少 budget 天
    """

    path: str
    kind: str
    budget: int
    note: str
    column: str | None
    require: str | None


def _c(
    path: str,
    budget: int,
    note: str,
    kind: str = "csv",
    column: str | None = None,
    require: str | None = None,
) -> Check:
    """require=只统计该列非空的行（文件里混着未完成行时用）。"""
    return {
        "path": path,
        "kind": kind,
        "budget": budget,
        "note": note,
        "column": column,
        "require": require,
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
    _c("data/euro_sovereign/yields.csv", D, "欧元区 10Y 主权利率（CNBC）"),
    _c("data/fred/fx/fx.csv", 12, "FRED 汇率（周频抖动大）"),
    _c("data/fred/sentiment/sentiment.csv", 14, "信心指数"),
    _c("data/fred/labor/labor.csv", 14, "劳动市场（含周频初请）"),
    _c("data/fred/labor_market/labor_market.csv", 68, "月频失业率等（波峰 61+余量）"),
    _c(
        "data/fred/producer_prices/producer_prices.csv",
        75,
        "月频 PPI（obs 月初，发布 T+40，波峰 68+余量）",
    ),
    _c("data/fred/growth/growth.csv", 90, "月频增长，两个发布周期"),
    _c("data/fred/consumption/consumption.csv", 90, "月频消费"),
    _c("data/fred/tic/tic.csv", 150, "TIC 海外持仓，源滞约 3 个月"),
    _c("data/cboe/volatility.csv", D, "CBOE 28 波动率指数"),
    _c("data/ofr/fsi.csv", 10, "OFR 金融压力指数（源约 T+2）"),
    _c("data/shapiro/shapiro.csv", 90, "FRBSF 供需分解（源约 T+2 月）"),
    _c("data/sce/sce.csv", 72, "NY Fed SCE 月频（发布 T+38，波峰 67+余量）"),
    _c("data/barchart/volatility_snapshot.csv", 8, "Barchart 波动率 30 指数"),
    _c("data/cot/cot.csv", 16, "CFTC COT 周频"),
    # ONI 是三月滑窗，末行标记是窗口中心月：JAS（08-01）10 月初才发、ASO（09-01）要等
    # 10 月过完 11 月初才发 → 跨月波峰 ≈96 天。按「当下年龄 + 小余量」标会每逢月末误报。
    _c("data/macro/enso.csv", 105, "NOAA ONI 指数（三月滑窗，末行是窗口中心月，跨月波峰 96 天）"),
    _c("data/commodities/etf_holdings.csv", D, "贵金属 ETF 持仓（GLD/SLV 官方日度）"),
    # 末行是「月初标记」（2026-09 = 9 月），要等次月月初才发布 → 跨月波峰 ≈63 天
    _c("data/wgc/etf_flows.csv", 70, "WGC 区域 ETF 流入（月频，月初标记，次月月初发布）"),
    _c("data/wgc/wgc_holdings.csv", 50, "WGC 全球黄金 ETF 总持仓（月频，同上）"),
    _c("data/fx/fx_pairs.csv", D, "外汇 16 对日线"),
    _c("data/etf/pool_prices.csv", D, "精选池 ETF 日线"),
    _c("data/etf_flows/etf_flows.csv", 8, "BTC 现货 ETF 资金流"),
    _c("data/etf_flows_eth/etf_flows_eth.csv", 8, "ETH 现货 ETF 资金流"),
    _c(
        "data/btc_dominance/btc_dominance.csv",
        D,
        "BTC/ETH Dominance（CoinGecko 日快照）",
    ),
    _c("data/crypto_basis/basis.csv", 10, "CME BTC 基差（丢末根未完成 bar）"),
    _c("data/short_selling/finra_daily.csv", 8, "FINRA 卖空量（源约 T+1）"),
    # mspd 是 bill_share_daily 的口径锚（月末未偿）：它停更时日频派生序列仍推进到当天，
    # 只查派生文件的末行日期会永远绿 → 分母冻结、占比静默漂移而无人发现。
    _c("data/treasury/mspd.csv", 45, "MSPD 月末未偿（Bill 占比锚）", column="record_date"),
    # 查 auction_date 而不是 issue_date：后者领先前者最多 8 天（52-Week Bill 4 周、
    # Note 8 天），用它当末观测日会让「8 天预算」实际变成 16 天，且靠未来日期撑绿。
    # require 同理：文件里混着「已公告未拍卖」的结果空行，按日期最大值判定会被
    # 公告行顶到未来场次（2026-10 就这样把结果停更 13 天报成「落后 5 天 ok」）。
    _c(
        "data/treasury/auction_results.csv",
        8,
        "国债拍卖结果",
        column="auction_date",
        require="bid_to_cover_ratio",
    ),
    _c("data/rate_expectations/fomc_probabilities.csv", D, "FOMC 概率"),
    _c("data/rate_expectations/zq_futures.csv", D, "ZQ 期货快照"),
    _c("data/breadth/abv.csv", D, "市场广度 ABV"),
    _c("data/analyst/ndx_targets.csv", 8, "NDX 分析师目标价"),
    _c("data/treasury/bill_share_daily.csv", 8, "日频 Bill 占比（派生）"),
    _c("data/consensus/history.csv", 6, "共识状态留痕（派生；工作日票龄 2 + 周末余量）"),
    _c("data/treasury/dts_cashflows.csv", 10, "DTS 现金流"),
    # 日历型：末行必须领先**美东**今天 ≥1 天，日历过期即说明源再没更新
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
    # raw 链是选点的逐合约明细（JSON 快照里没有），单独查：
    # 两者节奏可以不一致——2026-10 就出现过「JSON 每天更新、raw 却停在 3 天前」，
    # 因为 raw 当时被 .gitignore 排除，Actions 产出的 raw 从没入库。
    # 预算只给 2 天（不是日频那 6）：stale_days 一旦超 2，选出的到期日/行权价
    # 已不是市场当前的盘口，拿它算年化收益会偏。
    _c("data/options_structure", 2, "期权链 raw（选点明细）", kind="csv_dir_globs"),
    # Wheel 看板由 daily-full 在 options_structure + 日线之后落盘（导出端只读它，
    # 因为真实期权链 raw_*.csv 不入库）；缺它 = 线上选点会退化成 BSM 估算
    _c("data/wheel", D, "Wheel 看板快照", kind="json_dir"),
    _c("data/crypto_derivatives", D, "加密衍生品快照（7×24）", kind="json_dir"),
    _c("data/coinglass", D, "Coinglass 聚合快照", kind="json_dir"),
    _c("data/polymarket", D, "Polymarket 预测市场快照", kind="json_dir"),
    _c("data/cme_futures", 8, "CME BTC 期货仓位快照", kind="json_dir"),
]


def _from_filename(names: list[str], not_after: date | None = None) -> date | None:
    """从文件名取最新有效日期；快照日期不能超过本次审计日。"""
    found = []
    for n in names:
        m = Path(n).stem[-8:]
        if len(m) != 8 or not m.isdigit():
            continue
        d = datetime.strptime(m, "%Y%m%d").date()
        if not_after is None or d <= not_after:
            found.append(d)
    return max(found) if found else None


def _oldest_symbol_date(
    names: list[str], not_after: date | None = None, expected: list[str] | None = None
) -> date | None:
    """取每个标的最新有效日期中的最早值；任一预期标的缺失即返回 None。"""
    by_symbol: dict[str, date | None] = {}
    for n in names:
        stem = Path(n).stem
        stamp = stem[-8:]
        if len(stamp) != 8 or not stamp.isdigit():
            continue
        symbol = stem[:-9]
        by_symbol.setdefault(symbol, None)
        d = datetime.strptime(stamp, "%Y%m%d").date()
        if not_after is not None and d > not_after:
            continue
        if by_symbol[symbol] is None or d > by_symbol[symbol]:
            by_symbol[symbol] = d
    if expected is not None:
        dates = [by_symbol.get(f"raw_{s}") for s in expected]
        if any(d is None for d in dates):
            return None
        return min(d for d in dates if d is not None)
    if not by_symbol or any(d is None for d in by_symbol.values()):
        return None
    return min(d for d in by_symbol.values() if d is not None)


def _last_csv_date(path: Path, column: str | None, require: str | None = None) -> date | None:
    """CSV 末观测日（首列或指定列；非日期列返回 None）。

    先转字符串再取前 10 字符，避开两个具体坑：
    - `20260903` 是 int，pandas 当 epoch 纳秒 → 解成 1970（fed 文件）
    - `2026-08-17 15:15:50-05` 混时区 → to_datetime 直接抛错（_release_dates）
    取 ISO 前缀（天）后两者统一能解。

    require=只统计该列非空的行；该列不存在→None（宁可报 MISSING，不静默放行）。
    """
    df = pd.read_csv(path, low_memory=False)
    if df.empty:
        return None
    if require:
        if require not in df.columns:
            return None
        df = df[df[require].notna()]
        if df.empty:
            return None
    col = column if column and column in df.columns else df.columns[0]
    s = df[col].dropna().astype(str).str.slice(0, 10)
    try:
        dts = pd.to_datetime(s, errors="coerce", format="mixed").dropna()
    except Exception:
        return None
    return None if dts.empty else dts.max().date()


def latest(path: Path, chk: Check, today: date | None = None) -> date | None:
    """该数据集当前可用的最新观测日；文件缺失/为空/解析失败 → None。"""
    if chk["kind"] == "json_dir":
        return _from_filename([p.name for p in path.glob("*.json")], today)
    if chk["kind"] == "csv_dir_globs":
        # 目录下多个文件，日期在文件名末尾（raw_NVDA_20261027.csv）
        from .options_structure import SYMBOLS

        return _oldest_symbol_date([p.name for p in path.glob("*.csv")], today, SYMBOLS)
    if not path.exists():
        return None
    return _last_csv_date(path, chk.get("column"), chk.get("require"))


def audit(
    root: Path,
    today: date,
    checks: list[Check] | None = None,
    et_today: date | None = None,
) -> list[tuple[str, str, int | None, str, int]]:
    """返回 (状态, 数据集, 落后天数, 说明, 预算)；状态 ok / STALE / MISSING。

    文件名快照只采用审计日当日或更早的文件，避免未来文件掩盖停更。
    et_today=日历类（ahead）的基准日：CSV 里的日期是美东场次日，拿 UTC 日期比会在
    daily-full 被延迟跨过 UTC 零点时整差一天（2026-10-29 就这样误报过）。
    """
    rows = []
    for chk in CHECKS if checks is None else checks:
        last = latest(root / chk["path"], chk, today)
        if last is None:
            rows.append(("MISSING", chk["path"], None, chk["note"], chk["budget"]))
            continue
        base = (et_today or today) if chk["kind"] == "ahead" else today
        age = (base - last).days
        budget = chk["budget"]
        ok = age <= -budget if chk["kind"] == "ahead" else age <= budget
        status = "ok" if ok else "STALE"
        rows.append((status, chk["path"], age, chk["note"], budget))
    return rows


# ── 占位行检测（值维断言）──────────────────────────────────────────────
# 2026-10-13 事故：asset_prices 是全站唯一「拉取日为 key」的快照，盘前班次把
# 前日收盘写进当日行；收盘后 Yahoo 指数日线迟迟不更新，「同值 → 无 diff → 不
# commit」让占位值固化，日期新鲜骗过了上方的日期预算断言。其余数据集走
# upsert（观测日 key，同日覆盖可自愈）或 {date}.json 快照，无此固化路径。
US_SNAPSHOT_COLS = ("SPX", "NDX", "RUT", "DJI")
CROSS_ETFS = ("SPY", "QQQ", "IWM", "DIA")  # 同源 ETF 与指数同动，互为证人
_ET_ZONE = ZoneInfo("America/New_York")


def _confirm_from_utc(now: datetime) -> time:
    """收盘确认窗起点 = ET 16:15（收盘 16:00 后留 15 分钟结算），按日期算 DST。"""
    et = now.astimezone(_ET_ZONE)
    start = et.replace(hour=16, minute=15, second=0, microsecond=0)
    return start.astimezone(timezone.utc).timetz()


def _cross_etfs_moved(now: datetime) -> bool | None:
    """交叉 ETF 当日是否有交易且变动；取不到（网络/限流）→ None，宁漏报不误报。

    证人语义 = **当日** bar 存在且收盘 != 前 bar：假日（无当日 bar）或全平
    （相等）都不作证。只比「末两根不等」会在假日把周四周五当「今日变动」
    误作证，故显式过滤当日。
    """
    try:
        from .fetchers.yfinance_fetcher import fetch_hist

        today = now.date()
        for tkr in CROSS_ETFS:
            hist = fetch_hist(tkr)
            if hist is None or "Close" not in hist or hist.empty:
                continue
            closes = hist["Close"].dropna()
            if len(closes) < 2 or closes.index[-1].date() != today:
                continue  # 无当日 bar（假日/未发布）→ 此标的不作证
            if closes.iloc[-1] != closes.iloc[-2]:
                return True
        return False
    except Exception:
        return None


def roster_year_msg(root: Path, today: date) -> str | None:
    """FOMC 票委名册缺失/跨年未更新检测。

    roster.csv 只有 year 列没有观测日，日期预算套不上，所以它**不进 CHECKS**
    （进了会让 _last_csv_date 把 year 当日期解成 2026-01-01，永远落后 0 天、
    反而把问题掩盖掉）。这里一并承担 MISSING 的判定：文件缺失/空/缺 year 列
    都报错 —— 名册是页面核心内容，静默消失比跨年不换更难发现。
    票委名册每年 1 月第一个例行会议换人（官网 fomc.htm 同步改），跨年不换意味着
    fetch_roster 停了或解析退化 —— 名册会静默沿用去年的人，页面把已离任的人
    当现任票委展示。容忍到 1 月底：换人发生在 1 月首次会议（通常下旬），月初报红是误报。
    """
    path = root / "data/fed/roster.csv"
    if not path.exists():
        return "票委名册文件缺失 —— fetch_roster 未运行或写盘失败，页面无名册表"
    try:
        df = pd.read_csv(path)
    except Exception:
        return "票委名册 CSV 无法解析 —— 文件损坏，需重跑 fetch_roster"
    if df.empty or "year" not in df.columns:
        return "票委名册为空或缺 year 列 —— fetch_roster 写入异常"
    year = int(df["year"].iloc[0])
    # 1 月整月豁免：换人后 fetch 还没跑 / 官网尚未改版都属正常
    if year >= today.year or (today.month == 1 and year == today.year - 1):
        return None
    return (
        f"票委名册停在 {year} 年（今年 {today.year}）——"
        "fetch_roster 可能失败或 fomc.htm 结构变了，页面仍在展示去年的人"
    )


def us_indices_placeholder_msg(root: Path, now_utc: datetime) -> str | None:
    """美股指数占位行检测；返回失败文案，窗口外/数据不可判 → None 放行。

    判定（工作日收盘确认窗，ET 16:15 起）：今日快照行的美股指数列与前两行
    之一完全相同（复制自昨日或更早），而交叉 ETF 当日有变动 → 市场明明动了、
    指数值是旧的。标红后由 intraday 15 分钟一班自然重试；假日交叉 ETF 无当日
    bar 不作证，不误报。
    """
    if now_utc.weekday() >= 5 or now_utc.timetz() < _confirm_from_utc(now_utc):
        return None
    path = root / "data/yfinance/asset_prices.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path, low_memory=False)
    if len(df) < 3 or str(df["date"].iloc[-1])[:10] != now_utc.date().isoformat():
        return None
    last = df.iloc[-1]
    prior = df.iloc[-3:-1]  # 前两行（复制自昨日或更早都算占位）
    unchanged = [
        c
        for c in US_SNAPSHOT_COLS
        if c in df.columns and (last[c] == prior.iloc[0][c] or last[c] == prior.iloc[1][c])
    ]
    if not unchanged:
        return None
    moved = _cross_etfs_moved(now_utc)
    if not moved:  # False=休市/全平；None=交叉源取不到 → 均不标红
        return None
    return (
        f"占位行固化：{','.join(unchanged)} 今日快照与近两行之一完全相同，"
        "而交叉 ETF 当日有变动（收盘确认窗内指数值未更新）"
    )


def options_oi_pending_msg(root: Path, now_utc: datetime) -> str | None:
    """工作日 OI 发布窗后，上一收盘快照仍未回补则报警。"""
    if now_utc.weekday() >= 5 or now_utc.time() < time(14, 52):
        return None
    from .options_structure import _is_session

    if not _is_session(now_utc.astimezone(_ET_ZONE).date()):
        return None
    pending = []
    for path in sorted((root / "data/options_structure").glob("20*.json")):
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        snapshot_date = snapshot.get("snapshot_date")
        if not snapshot_date:
            continue
        day = datetime.strptime(snapshot_date, "%Y-%m-%d").date()
        if day < now_utc.astimezone(_ET_ZONE).date() and snapshot.get("oi_final") is False:
            pending.append(snapshot_date)
    if pending:
        return f"{', '.join(pending)} 收盘快照尚未完成次日 OI 回补"
    return None


def main() -> int:
    now_utc = datetime.now(timezone.utc)
    et_today = now_utc.astimezone(_ET_ZONE).date()
    rows = audit(Path(ROOT), now_utc.date(), et_today=et_today)
    msg = us_indices_placeholder_msg(Path(ROOT), now_utc)
    if msg:  # 值维断言：日期新鲜但值是复制（占位行固化）
        rows.append(("STALE", "data/yfinance/asset_prices.csv", None, msg, 0))
    rmsg = roster_year_msg(Path(ROOT), now_utc.date())
    if rmsg:  # 值维断言：roster 只有 year 列，日期预算套不上
        rows.append(("STALE", "data/fed/roster.csv", None, rmsg, 0))
    oi_msg = options_oi_pending_msg(Path(ROOT), now_utc)
    if oi_msg:
        rows.append(("STALE", "data/options_structure", None, oi_msg, 0))
    bad = [r for r in rows if r[0] != "ok"]
    print(f"数据时效（UTC {now_utc.date()} · 美东 {et_today}，{len(rows)} 项）")
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
