#!/usr/bin/env python3
"""
GEX (Gamma Exposure) 与期权墙计算脚本
=====================================
从 IBKR 获取实时 gamma + 标的价格，
从 yfinance 获取 Open Interest，
计算并可视化 GEX 分布。

GEX = Σ gamma_i × OI_i × spot × 100   (所有期权合约)

数据获取:
  - Gamma, IV, Delta → IBKR reqMktData（流式）
  - Open Interest    → yfinance option_chain()
  - Spot             → IBKR 历史收盘价

用法:
    uv run python code/compute_gex.py                    # AAPL（默认）
    uv run python code/compute_gex.py --symbol TSLA      # 指定品种
    uv run python code/compute_gex.py --symbol SPY --expirations 6  # 6个到期月
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from src.config import ROOT
from src.fetchers.ibkr_fetcher import (
    IBKRConnectionError,
    connect_ib,
    get_option_chain_params,
)
from src.options_structure import chain_health
from src.pricing import (
    aggregate_wall,
    cumulative_gamma_flip,
    fetch_yf_chain,
    years_to_expiration,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

log = logging.getLogger("gex")
ET = ZoneInfo("America/New_York")


def _now_et() -> datetime:
    return datetime.now(timezone.utc).astimezone(ET)


# 合约 conId 缓存：conId 对同一合约永远不变（实测同一批 310 个合约隔一次连接重查
# 完全一致），而 ib.qualifyContracts() 验 600+ 个候选要 15~25 秒。缓存命中后
# 直接拿 conId 构造合约，跳过 qualify 那一段（订阅时不需要预先 qualify）。
CONID_CACHE_PATH = ROOT / "data" / "cache" / "option_conid.json"

# 每批期权订阅的最大等待秒数（按秒轮询，bid + gamma 到齐即提前结束）。
# IBKR 的盘口与 generic tick 101（OI）不是同一时刻到达：同一批 50 个合约，
# 3 秒时只有 11 个有 bid，10 秒才全部到齐。
GREEKS_WAIT_SECONDS = 15
# 至少等这么久才允许提前结束（低于这个秒数时还没到齐的只是还没推过来）
GREEKS_MIN_WAIT = 8
# 默认批大小：每个 reqMktData 流式订阅同时占一条 market data line，
# 默认额度 100 条（含 TWS 窗口里的订阅），留 10 条余量。
DEFAULT_BATCH_SIZE = 90


# ============================================================================
# 1. IBKR 数据获取
# ============================================================================
def get_spot(ib, symbol):
    """获取标的最近收盘价"""
    from ib_insync import Stock

    stock = Stock(symbol, "SMART", "USD")
    ib.qualifyContracts(stock)
    bars = ib.reqHistoricalData(stock, "", "3 D", "1 day", "TRADES", True, 1)
    return bars[-1].close if bars else None


def filter_options(chain, spot, max_expirations=4, strike_pct=0.12):
    """
    过滤期权链：近月 + 现货附近行权价
    返回 [(expiration, strike), ...]
    """
    import datetime as _dt

    now = _now_et()
    today = now.date()
    dated = []
    for exp in chain.expirations:
        day = _dt.datetime.strptime(exp, "%Y%m%d").date()
        days = (day - today).days
        if years_to_expiration(exp, now) > 0:
            dated.append((exp, days, day.weekday()))
    dated.sort(key=lambda item: item[1])
    selected_exps = []
    if dated and max_expirations > 0:
        # 最近到期单独保留，0DTE 与周度合约不再被周五筛选排除。
        selected_exps.append(dated[0][0])
        fridays = [item for item in dated if item[2] == 4 and item[1] >= 3]
        for exp, _days, _weekday in fridays:
            if exp not in selected_exps:
                selected_exps.append(exp)
            if len(selected_exps) >= max_expirations:
                break
        selected_exps = selected_exps[:max_expirations]

    # 行权价过滤
    lo = spot * (1 - strike_pct)
    hi = spot * (1 + strike_pct)
    selected_strikes = [s for s in chain.strikes if lo <= s <= hi]

    log.info(f"过滤后: {len(selected_exps)} 到期日 × {len(selected_strikes)} 行权价")
    return selected_exps, selected_strikes


def _oi_from_ticker(t, right: str) -> int | None:
    """取单合约的 openInterest（genericTick 101）。

    单合约订阅时 IBKR 只回该合约方向的 OI：call 合约落在 callOpenInterest，
    put 合约落在 putOpenInterest。两者都为 nan 时返回 None（不是 0）——
    「未请求到」与「OI 真的为 0」必须能区分，否则会生成虚假的零权重墙位。
    """
    raw = t.callOpenInterest if right == "C" else t.putOpenInterest
    if raw is None or raw != raw or raw < 0:
        return None
    return int(raw)


def _ticker_ready(t) -> bool:
    """该合约的 bid 与数值 Gamma 已到；OI 另算，真实零值也有效。"""
    g = t.modelGreeks
    return (
        t.bid is not None
        and t.bid == t.bid
        and g is not None
        and g.gamma is not None
        and g.gamma == g.gamma
    )


def _conid_key(symbol: str, exp: str, strike: float, right: str) -> str:
    # 必须带 symbol：conId 只在自己的标的内部唯一，不同标的同一个行权价/到期日
    # 会落到同一个 key 上互相覆盖（缓存串标的）。
    return f"{symbol}|{exp}|{strike:g}|{right}"


def _load_conid_cache() -> dict[str, int]:
    if not CONID_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CONID_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # 缓存损坏就当空，但打一行日志：静默吞掉会让每次都退回去 qualify 而查不出原因
        log.warning(f"conId 缓存无法读取，本次按空处理: {CONID_CACHE_PATH}")
        return {}


def _save_conid_cache(cache: dict[str, int]) -> None:
    CONID_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONID_CACHE_PATH.write_text(json.dumps(cache), encoding="utf-8")


def fetch_options_greeks(ib, symbol, expirations, strikes, batch_size=3):
    """从 IBKR 小批量获取期权 Greeks（串行流式订阅，跳过 qualify）。

    并发上限由行情订阅配额决定，而非账户类型：默认 100 market data lines/用户，
    模拟账号镜像实盘权限（IBKR 官方文档）。没订阅期权行情时拿到的是延误数据或 nan，
    与用实盘还是模拟无关。batch_size 在 4001/4002 上可相同，但实际可并发数取决于
    当前已占用的 line 数（含 TWS 窗口里的订阅）。

    每批订阅后必须逐条取消：IBKR 只给 100 条 market data lines，残留下一条就少一条，
    累计满了之后所有新订阅静默无数据（不报错，只回 nan）。

    返回 DataFrame: [expiration, strike, right, bid, ask, delta, gamma, theta, vega,
    iv, openInterest]（openInterest 为 None 表示未取到，不是 0）
    """
    from ib_insync import Option

    # 强制实时行情（会话级设置，整连接有效）。不设时 IBKR 可给 Frozen/Delayed 数据，
    # 返回值看着有 gamma 却可能陈旧 15 分钟 —— 比拿不到更危险。
    # 注：readonly 只关下单通道，不改变行情订阅权限与配额。
    ib.reqMarketDataType(1)

    # 构建合约列表。conId 缓存命中的直接用 conId 构造，免掉 qualify；
    # 未命中的才走 qualify（不存在的合约在这里被剔除，也一并记入缓存的不存在集合）。
    cache = _load_conid_cache()
    contracts = []
    need_qualify = []
    for exp in expirations:
        for strike in strikes:
            for right in ["C", "P"]:
                key = _conid_key(symbol, exp, strike, right)
                if key in cache:
                    cid = cache[key]
                    if not cid:
                        continue  # 缓存里明确记着「该合约不存在」，直接跳过
                    opt = Option(
                        symbol=symbol,
                        lastTradeDateOrContractMonth=exp,
                        strike=strike,
                        right=right,
                        exchange="SMART",
                        currency="USD",
                        conId=cid,
                    )
                    contracts.append((opt, exp, strike, right))
                else:
                    opt = Option(
                        symbol=symbol,
                        lastTradeDateOrContractMonth=exp,
                        strike=strike,
                        right=right,
                        exchange="SMART",
                        currency="USD",
                    )
                    need_qualify.append((opt, exp, strike, right))

    if need_qualify:
        qualified_n = 0
        for i in range(0, len(need_qualify), 200):
            chunk = need_qualify[i : i + 200]
            ib.qualifyContracts(*(c[0] for c in chunk))
            for c in chunk:
                key = _conid_key(symbol, c[1], c[2], c[3])
                if c[0].conId:
                    cache[key] = c[0].conId
                    contracts.append(c)
                    qualified_n += 1
                else:
                    cache[key] = 0  # 标记不存在，下次直接跳过
        _save_conid_cache(cache)
    else:
        qualified_n = 0

    total = len(contracts)
    log.info(
        f"共 {total} 个有效合约"
        f"（缓存直取 {total - qualified_n} 个，本次 qualify 候选 {len(need_qualify)} 个）"
    )

    results = []
    empty_streak = 0
    replied = 0  # 有响应（哪怕只回 bid）的合约数，判空批次用
    for i in range(0, total, batch_size):
        batch = contracts[i : i + batch_size]
        tickers = []

        # 批量订阅（无合约的不报错，只是返回 nan）
        # genericTickList="101" → putOpenInterest / callOpenInterest（见 ib.reqMktData 文档）。
        # 不传这个 tick 时 Ticker 的 OI 字段恒为 nan，get_option_chain_params 也不带 OI，
        # 于是下游只能用外部源（yfinance）的 OI 拟合 —— 盘前正是外部源失效的时段。
        for opt, exp, strike, right in batch:
            t = ib.reqMktData(opt, "101", False, False)
            tickers.append((t, exp, strike, right))

        # 等到位为止，不用固定 sleep：三个字段不是同时到达的（实测同一批 50 个合约，
        # 第 4 秒 gamma 全到、第 6 秒 bid 全到、第 7 秒 OI 才全到）。
        # 提前结束要求 bid、gamma、OI 全部到齐且已过最短等待；OI 未齐时
        # 允许等满上限，只有 bid/gamma 自身停滞才提前结束。
        progress_prev, stall = None, 0
        for second in range(1, GREEKS_WAIT_SECONDS + 1):
            ib.sleep(1)
            ready = sum(1 for t, _e, _k, _r in tickers if _ticker_ready(t))
            oi_ready = sum(
                1
                for t, _e, _k, right in tickers
                if _ticker_ready(t) and _oi_from_ticker(t, right) is not None
            )
            if ready == len(tickers) and oi_ready == len(tickers) and second >= GREEKS_MIN_WAIT:
                break
            progress = (ready, oi_ready)
            stall = stall + 1 if progress == progress_prev else 0
            if ready < len(tickers) and stall >= 2 and second > GREEKS_MIN_WAIT:
                break
            progress_prev = progress

        batch_replied = 0
        for t, exp, strike, right in tickers:
            g = t.modelGreeks
            has_bid = t.bid is not None and t.bid == t.bid
            gamma = g.gamma if g and g.gamma is not None and g.gamma == g.gamma else None
            row = {
                "expiration": exp,
                "strike": strike,
                "right": right,
                "bid": t.bid if has_bid else None,
                "ask": t.ask if not (t.ask != t.ask) else None,
                "last": t.last if not (t.last != t.last) else None,
                "volume": t.volume if not (t.volume != t.volume) else None,
                "delta": g.delta if g else None,
                # TWS gamma 恒非负；统一为项目惯例 call + / put -（同 greeks_from_yf）
                "gamma": (-gamma if right == "P" and gamma is not None else gamma),
                "theta": g.theta if g else None,
                "vega": g.vega if g else None,
                "iv": g.impliedVol if g else None,
                "openInterest": _oi_from_ticker(t, right),
            }
            if has_bid or g:
                batch_replied += 1
            results.append(row)
            # 必须用该 ticker 自己的合约取消。传入别的合约（或循环里残留的变量）会打
            # 「No reqId found」并让这条订阅线一直占着，累计到 100 条后新订阅全部无数据。
            ib.cancelMktData(t.contract)
        replied += batch_replied

        if results:
            last = results[-1]
            has_g = last["gamma"] is not None
            done = min(i + len(batch), total)
            pct = done / total * 100
            log.info(
                f"  [{done}/{total}] "
                f"{last['right']} ${last['strike']:.0f} {last['expiration']} "
                f"gamma={'✓' if has_g else '✗'} ({pct:.0f}%)"
            )

        # 连续 3 批一个合约都没响应 → IBKR 行情不可用，不必等完全部合约。
        # 用「有任何 tick 回来」而不是「有 gamma」判空：候选里混着不存在的合约（Error 200），
        # 它们永远没有 gamma，按 gamma 判空会把正常批次误判成行情不可用。
        if batch_replied:
            empty_streak = 0
        else:
            empty_streak += 1
            if empty_streak >= 3:
                log.warning("连续 3 批无任何行情返回，IBKR 行情不可用，提前中止")
                break

    df = pd.DataFrame(results)
    # 过滤无效行（bid=nan 且 no gamma = 合约不存在）
    df = df.dropna(subset=["gamma", "bid"], how="all")
    if not df.empty:
        valid = df["gamma"].notna().sum()
        log.info(f"有效行: {len(df)}，其中 {valid} 条有 gamma ({valid / len(df) * 100:.0f}%)")
    return df


# ============================================================================
# 2. yfinance 降级（BS gamma 反推）
# ============================================================================
def greeks_from_yf(oi_df, spot, r=0.04, as_of=None):
    """IBKR Greeks 不可用时的降级路径：用 yfinance IV 反推 BS gamma。

    gamma = φ(d1) / (S·σ·√T)；惯例 call > 0、put < 0（与 compute_gex 一致）。
    复用 src.pricing.bs_greeks 同一实现（T≤0 与此处 t≤0 continue 语义一致）。
    """
    from src.pricing import bs_greeks

    now = as_of or _now_et()
    rows = []
    for _, row in oi_df.iterrows():
        iv = row["impliedVolatility"]
        if not iv or iv <= 0.01:
            continue
        t = years_to_expiration(row["expiration"], now)
        if t <= 0:
            continue
        gamma_p, _delta = bs_greeks(spot, row["strike"], t, iv, r=r)
        rows.append(
            {
                "expiration": row["expiration"],
                "strike": row["strike"],
                "right": row["right"],
                "gamma": gamma_p if row["right"] == "C" else -gamma_p,
                "iv": iv,
            }
        )
    df = pd.DataFrame(rows, columns=["expiration", "strike", "right", "gamma", "iv"])
    log.info(f"BS gamma 计算完成: {len(df)} 条")
    return df


# ============================================================================
# 3. GEX 计算
# ============================================================================
def compute_gex(greeks_df, oi_df, spot):
    """
    合并 Greeks 和 OI，计算 GEX。

    GEX = Σ gamma_i × OI_i × spot × 100

    返回:
      - gex_df: 每个合约的 GEX 贡献
      - wall_df: 按行权价聚合的 GEX（期权墙）
    """
    if greeks_df.empty:
        log.error("Greeks 数据为空，无法计算 GEX")
        return None, None
    now = _now_et()
    greeks_df = greeks_df[
        greeks_df["expiration"].apply(lambda exp: years_to_expiration(str(exp), now) > 0)
    ].copy()
    if greeks_df.empty:
        log.error("全部 Greeks 合约均已到期，无法计算 GEX")
        return None, None

    # 合并
    if not oi_df.empty:
        merged = greeks_df.merge(oi_df, on=["expiration", "strike", "right"], how="left")
        # IBKR 的 OI 包含真实零值；只在 IBKR 未返回时才使用外部源。
        if "openInterest_x" in merged.columns:
            merged["openInterest"] = merged["openInterest_x"].fillna(merged["openInterest_y"])
            merged = merged.drop(columns=["openInterest_x", "openInterest_y"])
        missing_oi = int(merged["openInterest"].isna().sum())
        if missing_oi:
            log.warning(f"{missing_oi} 个合约缺少两源 OI，按零权重处理")
        merged["openInterest"] = merged["openInterest"].fillna(0).astype(int)
    else:
        merged = greeks_df.copy()
        if "openInterest" not in merged.columns:
            merged["openInterest"] = 0
            log.warning("无 OI 数据，用 0 代替")
        else:
            # IBKR 自带 OI；未取到的合约为 None → 0（保留「没取到」与「真为0」的差异已在源头，
            # 这里加权计算必须拿一个数，但会打一行可观测的日志）
            nan_n = int(pd.to_numeric(merged["openInterest"], errors="coerce").isna().sum())
            if nan_n:
                log.warning(f"{nan_n} 个合约 IBKR OI 未返回，按 0 计（墙位可能偏低）")
            merged["openInterest"] = (
                pd.to_numeric(merged["openInterest"], errors="coerce").fillna(0).astype(int)
            )

    # 过滤无效 gamma
    merged = merged.dropna(subset=["gamma"])

    # GEX per contract (单位为美元)
    # 惯例: 负号表示 dealer 做空 gamma（客户做多）
    # 我们保持 gamma 的原始符号（call gamma > 0, put gamma < 0）
    merged["gex"] = merged["gamma"] * merged["openInterest"] * spot * 100

    # 按行权价聚合（期权墙）
    wall = aggregate_wall(merged)

    # 找关键水平
    if not wall.empty:
        max_gex_idx = wall["total_gex"].idxmax()
        min_gex_idx = wall["total_gex"].idxmin()
        log.info(
            f"最大正 GEX: ${wall.loc[max_gex_idx, 'strike']:.0f} "
            f"(${wall.loc[max_gex_idx, 'total_gex']:,.0f})"
        )
        log.info(
            f"最大负 GEX: ${wall.loc[min_gex_idx, 'strike']:.0f} "
            f"(${wall.loc[min_gex_idx, 'total_gex']:,.0f})"
        )

    return merged, wall


def print_wall(wall, spot, top_n=10):
    """打印期权墙关键水平"""
    print(f"\n{'=' * 70}")
    print(f"  期权墙 (Options Wall)  —  Spot: ${spot:.2f}")
    print(f"{'=' * 70}")
    print(
        f"{'行权价':>8}  {'Call GEX':>14}  {'Put GEX':>14}"
        f"  {'Total GEX':>14}  {'OI':>10}  {'IV':>7}"
    )
    print(f"{'-' * 70}")

    # 显示 total_gex 最大的几个行权价（正负分别）
    top_positive = wall.nlargest(top_n, "total_gex")
    top_negative = wall.nsmallest(top_n, "total_gex")
    shown = pd.concat([top_positive, top_negative]).drop_duplicates()
    shown = shown.sort_values("strike")

    for _, row in shown.iterrows():
        marker = (
            " ← 阻力" if row["total_gex"] < -1e6 else (" ← 支撑" if row["total_gex"] > 1e6 else "")
        )
        print(
            f"${row['strike']:>7.0f}  "
            f"${row['call_gex']:>13,.0f}  "
            f"${row['put_gex']:>13,.0f}  "
            f"${row['total_gex']:>13,.0f}  "
            f"{row['total_oi']:>10,}  "
            f"{row['avg_iv']:>6.1%}"
            f"{marker}"
        )

    # 汇总
    total_call = wall["call_gex"].sum()
    total_put = wall["put_gex"].sum()
    net_gex = wall["total_gex"].sum()
    print(f"{'-' * 70}")
    print(f"{'合计':>8}  ${total_call:>13,.0f}  ${total_put:>13,.0f}  ${net_gex:>13,.0f}")

    # 解读
    print("\n📊 GEX 解读:")
    if net_gex > 0:
        print(f"  净 GEX > 0 (${net_gex:,.0f}): dealer 做多 gamma → 市场趋于稳定，波动被抑制")
    else:
        print(f"  净 GEX < 0 (${net_gex:,.0f}): dealer 做空 gamma → 市场波动可能放大，注意风险")

    flip = cumulative_gamma_flip(wall.set_index("strike")["total_gex"], spot)
    if flip is not None:
        print(f"\n  Gamma Flip: ${flip:.0f}")
    else:
        print("\n  Gamma Flip: 无累计 GEX 负到正穿越点")


# ============================================================================
# 入口
# ============================================================================
def main():
    parser = argparse.ArgumentParser(description="GEX 与期权墙计算")
    parser.add_argument("--symbol", default="AAPL", help="标的股票")
    parser.add_argument("--expirations", type=int, default=4, help="到期日数量（默认 4）")
    parser.add_argument("--strike-pct", type=float, default=0.12, help="行权价范围（±%，默认 12%）")
    parser.add_argument(
        "--no-yfinance", action="store_true", help="不拉取 yfinance OI（仅 IBKR 数据）"
    )
    parser.add_argument("--output", help="输出 CSV 文件路径")
    parser.add_argument("--port", type=int, default=4002, help="IBKR 端口（4001 实盘 / 4002 模拟）")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=(
            f"行情批量订阅数（每个订阅占一条 market data line，默认 {DEFAULT_BATCH_SIZE}；"
            "额度 100 条含 TWS 窗口占用，撞限流时调小）"
        ),
    )
    parser.add_argument(
        "--reuse-greeks",
        action="store_true",
        help="复用当日 Greeks 快照（data/gex/），跳过 IBKR，只拉新鲜 OI",
    )
    parser.add_argument(
        "--spot", type=float, default=None, help="手动指定标的价格（跳过历史数据查询）"
    )
    args = parser.parse_args()

    symbol = args.symbol.upper()

    # === Step 1: 期权链与现价（IBKR 优先，yfinance 兜底） ===
    log.info(f"{'=' * 50}")
    log.info(f"GEX 计算: {symbol}")
    log.info(f"{'=' * 50}")

    spot = args.spot
    expirations = []
    greeks_df = pd.DataFrame()

    if args.reuse_greeks:
        cache = Path(f"data/gex/{symbol}_greeks_{_now_et():%Y%m%d}.csv")
        if cache.exists():
            greeks_df = pd.read_csv(cache, dtype={"expiration": str})
            expirations = sorted(greeks_df["expiration"].unique())
            log.info(f"复用 Greeks 快照: {cache}（{len(greeks_df)} 行）")
        else:
            log.warning(f"快照不存在: {cache}，回退实时拉取")

    ib = None
    if greeks_df.empty:
        try:
            ib, _ = connect_ib(ports=(args.port,))
        except IBKRConnectionError:
            log.warning("IBKR 连接失败，降级 yfinance")
    if ib:
        spot = spot or get_spot(ib, symbol)
        if spot:
            chains = get_option_chain_params(ib, symbol)
            if not chains:
                log.warning("IBKR 未返回期权链参数，降级 yfinance")
            else:
                chain = max(chains, key=lambda c: len(c.expirations) + len(c.strikes))
                expirations, strikes = filter_options(
                    chain, spot, args.expirations, args.strike_pct
                )
                if expirations:
                    log.info(
                        f"行权价范围: ${strikes[0]:.0f} ~ ${strikes[-1]:.0f} ({len(strikes)} 个)"
                    )
                    greeks_df = fetch_options_greeks(
                        ib, symbol, expirations, strikes, batch_size=args.batch_size
                    )
                    if not greeks_df.empty:
                        snap = Path(f"data/gex/{symbol}_greeks_{_now_et():%Y%m%d}.csv")
                        snap.parent.mkdir(parents=True, exist_ok=True)
                        greeks_df.to_csv(snap, index=False)
                        log.info(f"已保存 Greeks 快照: {snap}")
        else:
            log.warning("IBKR 无法获取标的价格")
        ib.disconnect()

    # IBKR 连不上 / 数据拿不到 → 缺的部分用 yfinance 补齐
    if (spot is None or not expirations or greeks_df.empty) and not args.no_yfinance:
        from src.fetchers.yfinance_fetcher import ensure_yf_proxy

        ensure_yf_proxy()
        import yfinance as yf  # noqa: E402

        ticker = yf.Ticker(symbol)
        if not expirations:
            now = _now_et()
            valid_exps = [
                e.replace("-", "")
                for e in ticker.options
                if years_to_expiration(e.replace("-", ""), now) > 0
            ]
            expirations = valid_exps[: args.expirations]
        if spot is None:
            spot = float(ticker.fast_info.last_price)

    if spot is None or not expirations:
        log.error("无可用标的价格或期权到期日")
        sys.exit(1)
    log.info(f"{symbol} spot: ${spot:.2f}")
    log.info(f"到期日: {expirations}")

    # === Step 2: OI（Barchart 优先——真实 gamma + OI 同源；yfinance 兜底） ===
    oi_df = pd.DataFrame()
    bs_from_yf = False
    if greeks_df.empty or greeks_df["gamma"].isna().all():
        # Barchart 降级源（非 yfinance，不受 --no-yfinance 限制）
        from src.fetchers.barchart_options_fetcher import fetch_barchart_chain

        try:
            bc = fetch_barchart_chain(symbol, expirations)
        except Exception as e:
            log.warning("Barchart 期权链拉取失败: %s", e)
            bc = pd.DataFrame()
        if not bc.empty:
            # 与 IBKR 路径的 filter_options(strike_pct) 对齐：只保留现货附近行权价
            if args.strike_pct:
                lo = spot * (1 - args.strike_pct)
                hi = spot * (1 + args.strike_pct)
                n_before = len(bc)
                bc = bc[(bc["strike"] >= lo) & (bc["strike"] <= hi)]
                log.info(f"Barchart 行权价过滤 ±{args.strike_pct:.0%}: {n_before} → {len(bc)} 条")
            healthy, why = chain_health(bc, expected_expirations=expirations, require_iv=False)
            if healthy:
                log.info(f"Barchart 期权链: {len(bc)} 条（真实市场 gamma + OI）")
                greeks_df = bc[["expiration", "strike", "right", "gamma", "iv"]]
                oi_df = bc.rename(columns={"iv": "impliedVolatility"})[
                    [
                        "expiration",
                        "strike",
                        "right",
                        "openInterest",
                        "volume",
                        "impliedVolatility",
                    ]
                ]
            else:
                log.warning(f"Barchart 期权链不完整（{why}），回退 yfinance")

    if not args.no_yfinance and oi_df.empty:
        oi_df = fetch_yf_chain(symbol, expirations)[
            [
                "expiration",
                "strike",
                "right",
                "openInterest",
                "volume",
                "impliedVolatility",
            ]
        ]
        # 消费侧规整：volume/IV NaN → 0（greeks_from_yf 跳过 iv<=0 的行）
        oi_df["volume"] = oi_df["volume"].fillna(0).astype(int)
        oi_df["impliedVolatility"] = oi_df["impliedVolatility"].fillna(0)

    # IBKR Greeks 不可用 → 降级：Barchart 真实 gamma（已在上方填 greeks_df）
    # → yfinance IV 反推 BS gamma（Barchart 也失败时）
    # 注意：IBKR 可能返回全 NaN gamma 的非空表（如盘前无行情），按有效 gamma 判断
    if greeks_df.empty or greeks_df["gamma"].isna().all():
        if oi_df.empty:
            log.error("未获取到任何 Greeks 数据")
            sys.exit(1)
        log.warning("IBKR Greeks 为空，用 yfinance IV 计算 BS gamma")
        bs_from_yf = True
        greeks_df = greeks_from_yf(oi_df, spot)

    if greeks_df.empty or greeks_df["gamma"].notna().sum() == 0:
        log.error("没有有效 Gamma：请检查 IV 是否全部低于 1% 或期权行情是否不可用")
        sys.exit(1)

    # === Step 3: 计算 GEX ===
    valid_greeks = greeks_df[greeks_df["gamma"].notna()]
    missing_greek_exps = sorted(
        set(expirations) - set(valid_greeks.get("expiration", pd.Series(dtype=str)).astype(str))
    )
    if missing_greek_exps:
        log.error(f"Greeks 缺到期日：{','.join(missing_greek_exps)}")
        sys.exit(1)
    if not oi_df.empty:
        healthy, why = chain_health(oi_df, expected_expirations=expirations, require_iv=bs_from_yf)
        if not healthy:
            has_ibkr_oi = (
                "openInterest" in greeks_df.columns and greeks_df["openInterest"].notna().any()
            )
            if not has_ibkr_oi:
                log.error(f"期权链不完整：{why}")
                sys.exit(1)
            log.warning(f"外部期权链整体不健康（{why}），仍用于补齐 IBKR 缺失 OI")
    contract_df, wall_df = compute_gex(greeks_df, oi_df, spot)

    if wall_df is not None and not wall_df.empty:
        print_wall(wall_df, spot)

    # === 输出 ===
    if args.output and wall_df is not None:
        wall_df.to_csv(args.output, index=False)
        log.info(f"已保存期权墙: {args.output}")

    if contract_df is not None:
        out = Path(f"data/gex/{symbol}_gex_{_now_et().strftime('%Y%m%d_%H%M')}.csv")
        out.parent.mkdir(parents=True, exist_ok=True)
        contract_df.to_csv(out, index=False)
        log.info(f"已保存 GEX 明细: {out}")


if __name__ == "__main__":
    main()
