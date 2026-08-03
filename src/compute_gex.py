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
import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.fetchers.ibkr_fetcher import (
    IBKRConnectionError,
    connect_ib,
    get_option_chain_params,
)
from src.pricing import aggregate_wall, fetch_yf_chain

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("gex")


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

    today = _dt.datetime.now()

    # 只保留周五（跳过本周一到期的）
    fri_exps = []
    for exp in chain.expirations:
        d = _dt.datetime.strptime(exp, "%Y%m%d")
        if d.weekday() == 4:  # Friday
            days_to_exp = (d - today).days
            if days_to_exp >= 3:  # 至少 3 天后到期
                fri_exps.append((exp, days_to_exp))

    fri_exps.sort(key=lambda x: x[1])
    selected_exps = [e[0] for e in fri_exps[:max_expirations]]

    # 行权价过滤
    lo = spot * (1 - strike_pct)
    hi = spot * (1 + strike_pct)
    selected_strikes = [s for s in chain.strikes if lo <= s <= hi]

    log.info(f"过滤后: {len(selected_exps)} 到期日 × {len(selected_strikes)} 行权价")
    return selected_exps, selected_strikes


def fetch_options_greeks(ib, symbol, expirations, strikes, batch_size=3):
    """
    从 IBKR 小批量获取期权 Greeks（串行流式订阅，跳过 qualify）。
    纸交易账户同时订阅数有限（~3-5），必须小批量串行。

    返回 DataFrame: [expiration, strike, right, bid, ask, delta, gamma, theta, vega, iv]
    """
    from ib_insync import Option

    # 构建合约列表（不 qualify，直接试）
    contracts = []
    for exp in expirations:
        for strike in strikes:
            for right in ["C", "P"]:
                opt = Option(
                    symbol=symbol,
                    lastTradeDateOrContractMonth=exp,
                    strike=strike,
                    right=right,
                    exchange="SMART",
                    currency="USD",
                )
                contracts.append((opt, exp, strike, right))

    total = len(contracts)
    log.info(f"共 {total} 个候选合约，批量={batch_size}/次（跳过 qualify 直发行情）")

    results = []
    empty_streak = 0
    for i in range(0, total, batch_size):
        batch = contracts[i : i + batch_size]
        tickers = []

        # 批量订阅（无合约的不报错，只是返回 nan）
        for opt, exp, strike, right in batch:
            t = ib.reqMktData(opt, "", False, False)
            tickers.append((t, exp, strike, right))

        ib.sleep(3)

        for t, exp, strike, right in tickers:
            g = t.modelGreeks
            row = {
                "expiration": exp,
                "strike": strike,
                "right": right,
                "bid": t.bid if not (t.bid != t.bid) else None,
                "ask": t.ask if not (t.ask != t.ask) else None,
                "last": t.last if not (t.last != t.last) else None,
                "volume": t.volume if not (t.volume != t.volume) else None,
                "delta": g.delta if g else None,
                # TWS gamma 恒非负；统一为项目惯例 call + / put -（同 greeks_from_yf）
                "gamma": (-g.gamma if right == "P" else g.gamma) if g else None,
                "theta": g.theta if g else None,
                "vega": g.vega if g else None,
                "iv": g.impliedVol if g else None,
            }
            results.append(row)
            ib.cancelMktData(opt)

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

        # 连续 3 批无任何 gamma → IBKR 行情不可用，不必等完全部合约
        if any(r["gamma"] is not None for r in results[-batch_size:]):
            empty_streak = 0
        else:
            empty_streak += 1
            if empty_streak >= 3:
                log.warning("连续 3 批无 Greeks 返回，IBKR 行情不可用，提前中止")
                break

    df = pd.DataFrame(results)
    # 过滤无效行（bid=nan 且 no gamma = 合约不存在）
    df = df.dropna(subset=["gamma", "bid"], how="all")
    if not df.empty:
        valid = df["gamma"].notna().sum()
        log.info(
            f"有效行: {len(df)}，其中 {valid} 条有 gamma ({valid / len(df) * 100:.0f}%)"
        )
    return df


# ============================================================================
# 2. yfinance 降级（BS gamma 反推）
# ============================================================================
def greeks_from_yf(oi_df, spot, r=0.04):
    """IBKR Greeks 不可用时的降级路径：用 yfinance IV 反推 BS gamma。

    gamma = φ(d1) / (S·σ·√T)；惯例 call > 0、put < 0（与 compute_gex 一致）。
    """
    from math import exp, pi, sqrt
    from math import log as ln

    today = datetime.now().date()
    rows = []
    for _, row in oi_df.iterrows():
        iv = row["impliedVolatility"]
        if not iv or iv <= 0:
            continue
        t = (datetime.strptime(row["expiration"], "%Y%m%d").date() - today).days / 365
        if t <= 0:
            continue
        sig_t = iv * sqrt(t)
        d1 = (ln(spot / row["strike"]) + (r + iv * iv / 2) * t) / sig_t
        gamma = exp(-d1 * d1 / 2) / sqrt(2 * pi) / (spot * sig_t)
        rows.append(
            {
                "expiration": row["expiration"],
                "strike": row["strike"],
                "right": row["right"],
                "gamma": gamma if row["right"] == "C" else -gamma,
                "iv": iv,
            }
        )
    df = pd.DataFrame(rows)
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

    # 合并
    if not oi_df.empty:
        merged = greeks_df.merge(
            oi_df, on=["expiration", "strike", "right"], how="left"
        )
        merged["openInterest"] = merged["openInterest"].fillna(0).astype(int)
    else:
        merged = greeks_df.copy()
        merged["openInterest"] = 0
        log.warning("无 OI 数据，用 0 代替")

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
            " ← 阻力"
            if row["total_gex"] < -1e6
            else (" ← 支撑" if row["total_gex"] > 1e6 else "")
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
    print(
        f"{'合计':>8}  ${total_call:>13,.0f}  ${total_put:>13,.0f}  ${net_gex:>13,.0f}"
    )

    # 解读
    print("\n📊 GEX 解读:")
    if net_gex > 0:
        print(
            f"  净 GEX > 0 (${net_gex:,.0f}): dealer 做多 gamma → 市场趋于稳定，"
            f"波动被抑制"
        )
    else:
        print(
            f"  净 GEX < 0 (${net_gex:,.0f}): dealer 做空 gamma → 市场波动可能放大，"
            f"注意风险"
        )

    # Gamma Flip（GEX 由正转负的行权价）
    wall_sorted = wall.sort_values("strike")
    flip_points = []
    for i in range(1, len(wall_sorted)):
        prev = wall_sorted.iloc[i - 1]["total_gex"]
        curr = wall_sorted.iloc[i]["total_gex"]
        if (prev > 0 and curr < 0) or (prev < 0 and curr > 0):
            flip_points.append(
                (
                    wall_sorted.iloc[i - 1]["strike"],
                    wall_sorted.iloc[i]["strike"],
                )
            )

    if flip_points:
        print("\n  Gamma Flip 区域:")
        for lo, hi in flip_points:
            print(f"    ${lo:.0f} → ${hi:.0f}")


# ============================================================================
# 入口
# ============================================================================
def main():
    parser = argparse.ArgumentParser(description="GEX 与期权墙计算")
    parser.add_argument("--symbol", default="AAPL", help="标的股票")
    parser.add_argument(
        "--expirations", type=int, default=4, help="到期日数量（默认 4）"
    )
    parser.add_argument(
        "--strike-pct", type=float, default=0.12, help="行权价范围（±%，默认 12%）"
    )
    parser.add_argument(
        "--no-yfinance", action="store_true", help="不拉取 yfinance OI（仅 IBKR 数据）"
    )
    parser.add_argument("--output", help="输出 CSV 文件路径")
    parser.add_argument(
        "--port", type=int, default=4002, help="IBKR 端口（4001 实盘 / 4002 模拟）"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=3,
        help="行情批量订阅数（模拟账户 3-5，实盘可 50）",
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
        cache = Path(f"data/gex/{symbol}_greeks_{datetime.now():%Y%m%d}.csv")
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
            chain = max(chains, key=lambda c: len(c.expirations) + len(c.strikes))
            expirations, strikes = filter_options(
                chain, spot, args.expirations, args.strike_pct
            )
            if expirations:
                log.info(
                    f"行权价范围: ${strikes[0]:.0f} ~ ${strikes[-1]:.0f}"
                    f" ({len(strikes)} 个)"
                )
                greeks_df = fetch_options_greeks(
                    ib, symbol, expirations, strikes, batch_size=args.batch_size
                )
                if not greeks_df.empty:
                    snap = Path(f"data/gex/{symbol}_greeks_{datetime.now():%Y%m%d}.csv")
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
            expirations = [
                e.replace("-", "") for e in ticker.options[: args.expirations]
            ]
        if spot is None:
            spot = float(ticker.fast_info.last_price)

    if spot is None or not expirations:
        log.error("无可用标的价格或期权到期日")
        sys.exit(1)
    log.info(f"{symbol} spot: ${spot:.2f}")
    log.info(f"到期日: {expirations}")

    # === Step 2: OI（Barchart 优先——gamma + OI 同源；yfinance 兜底） ===
    oi_df = pd.DataFrame()
    if not args.no_yfinance:
        if greeks_df.empty or greeks_df["gamma"].isna().all():
            from src.fetchers.barchart_options_fetcher import fetch_barchart_chain

            try:
                bc = fetch_barchart_chain(symbol, expirations)
            except Exception as e:
                log.warning("Barchart 期权链拉取失败: %s", e)
                bc = pd.DataFrame()
            if not bc.empty:
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

        if oi_df.empty:
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
        greeks_df = greeks_from_yf(oi_df, spot)

    # === Step 3: 计算 GEX ===
    contract_df, wall_df = compute_gex(greeks_df, oi_df, spot)

    if wall_df is not None and not wall_df.empty:
        print_wall(wall_df, spot)

    # === 输出 ===
    if args.output and wall_df is not None:
        wall_df.to_csv(args.output, index=False)
        log.info(f"已保存期权墙: {args.output}")

    if contract_df is not None:
        out = Path(
            f"data/gex/{symbol}_gex_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        contract_df.to_csv(out, index=False)
        log.info(f"已保存 GEX 明细: {out}")


if __name__ == "__main__":
    main()
