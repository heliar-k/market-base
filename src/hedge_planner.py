#!/usr/bin/env python3
"""
下跌保护结构报价器
==================
从 yfinance 拉取期权报价，对三类下跌保护结构算成本与盈亏：
  1. 裸买 put            — 保护不封顶，最贵
  2. put 价差（买高卖低） — 保护区间封顶，便宜 ~40%
  3. 领口（买 put + 卖 call）— 最便宜，代价是上行被封顶

用法:
    uv run python src/hedge_planner.py --symbol TSM
    uv run python src/hedge_planner.py --symbol MSFT \
        --exps 2026-09-18 --puts 360,380 --calls 430
"""

import argparse
import logging
from dataclasses import dataclass
from datetime import date

from src.fetchers.yfinance_fetcher import ensure_yf_proxy
from src.pricing import fetch_yf_chain

ensure_yf_proxy()
import yfinance as yf  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("hedge")


# ============================================================================
# 结构盈亏计算（纯函数，每股计）
# ============================================================================
def naked_put(k: float, cost: float) -> dict:
    """裸买 put：亏损上限 = 成本，盈亏平衡 = K - 成本，下方保护不封顶"""
    return {"cost": cost, "breakeven": k - cost, "max_loss": cost, "max_profit": None}


def put_spread(k_long: float, k_short: float, cost: float) -> dict:
    """买 K_long put + 卖 K_short put：赔付封顶 = 行权价间距 - 成本"""
    width = k_long - k_short
    return {
        "cost": cost,
        "breakeven": k_long - cost,
        "max_loss": cost,
        "max_profit": width - cost,
    }


def collar(k_put: float, k_call: float, cost: float) -> dict:
    """买 K_put put + 卖 K_call call：下限 K_put，上行封顶 K_call，成本为负 = 净收入"""
    return {"cost": cost, "floor": k_put, "cap": k_call, "max_loss": cost}


# ============================================================================
# 报价拉取
# ============================================================================
@dataclass
class Quote:
    strike: float
    right: str
    bid: float
    ask: float
    oi: int

    @property
    def mid(self) -> float:
        # ponytail: 用中间价近似成本，实际成交看挂单深度
        return (self.bid + self.ask) / 2


def fetch_quotes(symbol: str, exp: str) -> dict[tuple[float, str], Quote]:
    """拉一个到期日的全部报价，返回 {(strike, right): Quote}。

    bid/ask 无效时用 lastPrice（昨收）近似成本（Yahoo 多数合约无盘口）。
    """
    quotes = {}
    for _, row in fetch_yf_chain(symbol, [exp]).iterrows():
        bid, ask = float(row["bid"]), float(row["ask"])
        if bid <= 0:
            # ponytail: Yahoo 多数合约无盘口，用 lastPrice（昨收）近似成本
            last = float(row["last"] or 0)
            if last <= 0:
                continue
            bid = ask = last
        q = Quote(
            strike=float(row["strike"]),
            right=row["right"],
            bid=bid,
            ask=ask,
            oi=int(row["openInterest"]),  # 共享层已把 NaN 归 0
        )
        quotes[(q.strike, q.right)] = q
    return quotes


def pick_expirations(options: list[str], targets=(30, 60, 90)) -> list[str]:
    """从可用到期日中挑出最接近 30/60/90 天的（自动跳过周内短期合约）"""
    today = date.today()
    options = [e for e in options if date.fromisoformat(e) >= today]
    if not options:
        return []  # 全链已过期 → 无合约可选（调用方自行降级）
    picked = []
    for t in targets:
        best = min(options, key=lambda e: abs((date.fromisoformat(e) - today).days - t))
        if best not in picked:
            picked.append(best)
    return picked


def snap(strikes: list[float], target: float) -> float:
    """把目标价位吸附到链上最近的真实行权价"""
    return min(strikes, key=lambda s: abs(s - target))


# ============================================================================
# 输出
# ============================================================================
def fmt_money(per_share: float) -> str:
    return f"${per_share:.2f} (${per_share * 100:,.0f}/手)"


def print_structures(
    exp: str, quotes: dict, puts: list[float], calls: list[float], spot: float
):
    days = (date.fromisoformat(exp) - date.today()).days
    print(f"\n{'=' * 66}\n=== {exp}（{days} 天后到期）===\n{'=' * 66}")

    # 报价明细
    for s in puts:
        q = quotes.get((s, "P"))
        if q:
            print(
                f"  P ${s:.0f}: bid={q.bid:.2f} ask={q.ask:.2f}"
                f" mid={q.mid:.2f} OI={q.oi:,}"
            )
    for s in calls:
        q = quotes.get((s, "C"))
        if q:
            print(
                f"  C ${s:.0f}: bid={q.bid:.2f} ask={q.ask:.2f}"
                f" mid={q.mid:.2f} OI={q.oi:,}"
            )

    # 裸买 put
    print("\n  [裸买 put]")
    for s in puts:
        q = quotes.get((s, "P"))
        if not q:
            continue
        r = naked_put(s, q.mid)
        print(
            f"    P ${s:.0f}: 成本 {fmt_money(r['cost'])}  "
            f"BE ${r['breakeven']:.2f}（{r['breakeven'] / spot - 1:+.1%}）  "
            f"最大亏损 {fmt_money(r['max_loss'])}  保护不封顶"
        )

    # put 价差（相邻两档）
    print("\n  [put 价差（买高卖低）]")
    for hi, lo in zip(puts, puts[1:], strict=False):
        q_hi, q_lo = quotes.get((hi, "P")), quotes.get((lo, "P"))
        if not q_hi or not q_lo:
            continue
        cost = q_hi.mid - q_lo.mid
        r = put_spread(hi, lo, cost)
        ratio = r["max_profit"] / cost if cost > 0 else float("inf")
        print(
            f"    {hi:.0f}/{lo:.0f}: 成本 {fmt_money(cost)}  "
            f"BE ${r['breakeven']:.2f}（{r['breakeven'] / spot - 1:+.1%}）  "
            f"最大盈利 {fmt_money(r['max_profit'])}（+{ratio:.0%}）  "
            f"最大亏损 {fmt_money(r['max_loss'])}"
        )

    # 领口（每个 put × 每个 call）
    print("\n  [领口（买 put + 卖 call，上行封顶）]")
    for kp in puts:
        for kc in calls:
            q_p, q_c = quotes.get((kp, "P")), quotes.get((kc, "C"))
            if not q_p or not q_c:
                continue
            cost = q_p.mid - q_c.mid
            r = collar(kp, kc, cost)
            tag = "净收入" if cost < 0 else "成本"
            print(
                f"    {kp:.0f}P+{kc:.0f}C: {tag} {fmt_money(abs(cost))}  "
                f"下限 ${r['floor']:.0f}（{r['floor'] / spot - 1:+.1%}）  "
                f"上限 ${r['cap']:.0f}（{r['cap'] / spot - 1:+.1%}）"
            )


def main():
    parser = argparse.ArgumentParser(description="下跌保护结构报价器")
    parser.add_argument("--symbol", required=True, help="标的，如 TSM / MSFT")
    parser.add_argument(
        "--exps", help="逗号分隔到期日 YYYY-MM-DD，默认自动挑 30/60/90 天"
    )
    parser.add_argument(
        "--puts", help="逗号分隔 put 行权价，默认现价下方 2.5%%~10%% 吸附"
    )
    parser.add_argument(
        "--calls", help="逗号分隔 call 行权价，默认现价上方 10%%~12.5%% 吸附"
    )
    args = parser.parse_args()

    symbol = args.symbol.upper()
    ticker = yf.Ticker(symbol)
    spot = float(ticker.fast_info.last_price)
    print(f"{symbol} 现价: ${spot:.2f}")

    # 财报日历（拿不到不阻塞）
    try:
        cal = ticker.calendar
        if isinstance(cal, dict) and cal.get("Earnings Date"):
            print(f"财报日期: {cal['Earnings Date']}")
    except Exception:
        pass

    exps = args.exps.split(",") if args.exps else pick_expirations(list(ticker.options))
    log.info(f"到期日: {exps}")

    for exp in exps:
        quotes = fetch_quotes(symbol, exp)
        if not quotes:
            log.warning(f"{exp} 无报价，跳过")
            continue
        strikes = sorted({s for s, _ in quotes})
        if args.puts:
            puts = sorted((float(x) for x in args.puts.split(",")), reverse=True)
        else:
            puts = sorted(
                {snap(strikes, spot * p) for p in (0.975, 0.95, 0.925, 0.90)},
                reverse=True,
            )
        if args.calls:
            calls = sorted(float(x) for x in args.calls.split(","))
        else:
            calls = sorted({snap(strikes, spot * p) for p in (1.10, 1.125)})
        print_structures(exp, quotes, puts, calls, spot)


if __name__ == "__main__":
    main()
