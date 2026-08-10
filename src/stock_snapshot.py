"""交易计划快照：多标的盘前/实时价 + 期权 OI 墙 + 大盘方向。

planning-trades skill 阶段 2 用：拉盘前实时价、期权墙验证、大盘方向。
GEX 墙用 compute_gex.py（重），本工具只出轻量快照。

用法:
    uv run python -m src.snapshot SNDK MU            # 价格快照 + QQQ/SPY
    uv run python -m src.snapshot SNDK MU --oi       # 追加期权 OI 墙
    uv run python -m src.snapshot SNDK --exps 6      # 看 6 个到期日

数据来源：yfinance（需要 SOCKS5 代理，.env 配置；代理断则报错）。
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from src.fetchers.yfinance_fetcher import ensure_yf_proxy

ensure_yf_proxy()
import yfinance as yf  # noqa: E402

BENCHMARKS = ["QQQ", "SPY"]


def price_snapshot(symbols: list[str]) -> None:
    """昨收 / 盘后·盘前现价 / 当日区间。

    昨收与区间取日线最后一根（避免 5m prepost 混入跨日 bar）；
    最新价取 5m prepost 最后一根（盘前时段=盘前价，盘后=盘后价）。
    """
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    print(f"\n=== 价格快照（{now}）===")
    print(f"{'标的':<6}{'昨收':>10}{'最新':>12}{'距昨收':>9}{'昨交易日低→高':>22}")
    for tk in symbols:
        try:
            t = yf.Ticker(tk)
            d = t.history(period="1mo", interval="1d")
            if d.empty:
                print(f"{tk:<6}  无数据")
                continue
            # 盘中日线最后一根 bar 是当日 partial bar → 昨收/昨日区间取前一根
            prev_idx = -1
            if d.index[-1].date() == datetime.now(ZoneInfo("America/New_York")).date():
                prev_idx = -2
            r = d.iloc[prev_idx]
            prev = float(r["Close"])
            lo, hi = float(r["Low"]), float(r["High"])
            h = t.history(period="1d", interval="5m", prepost=True)
            last = float(h["Close"].iloc[-1]) if not h.empty else prev
            chg = (last / prev - 1) * 100
            print(
                f"{tk:<6}{prev:>10.2f}{last:>12.2f}{chg:>+8.1f}%{lo:>10.2f}–{hi:<10.2f}"
            )
        except Exception as e:
            print(f"{tk:<6}  错误: {e}")


def oi_walls(symbols: list[str], exps: int) -> None:
    """每标的按到期日前 exps 档，输出最大 call/put OI 墙（真实 OI，无 IV）。"""
    for tk in symbols:
        t = yf.Ticker(tk)
        try:
            opts = t.options
        except Exception as e:
            print(f"\n{tk}: 期权链错误 {e}")
            continue
        n = min(exps, len(opts))
        print(f"\n=== {tk} 期权 OI 墙（到期日前 {n} 档，隔夜 OI）===")
        print(f"{'到期日':<12}{'call 墙(行权价:OI)':<34}{'put 墙(行权价:OI)'}")
        for exp in opts[:exps]:
            ch = t.option_chain(exp)
            cw = (
                ch.calls.groupby("strike")["openInterest"]
                .sum()
                .sort_values(ascending=False)
            )
            pw = (
                ch.puts.groupby("strike")["openInterest"]
                .sum()
                .sort_values(ascending=False)
            )
            c = " ".join(f"{int(s)}:{int(v):,}" for s, v in cw.head(3).items())
            p = " ".join(f"{int(s)}:{int(v):,}" for s, v in pw.head(3).items())
            print(f"{exp:<12}{c:<34}{p}")


def main() -> None:
    ap = argparse.ArgumentParser(description="交易计划快照")
    ap.add_argument("symbols", nargs="+", help="标的，如 SNDK MU")
    ap.add_argument("--oi", action="store_true", help="追加期权 OI 墙")
    ap.add_argument("--exps", type=int, default=4, help="OI 墙到期日档数（默认 4）")
    args = ap.parse_args()

    price_snapshot(args.symbols + BENCHMARKS)
    if args.oi:
        oi_walls(args.symbols, args.exps)


if __name__ == "__main__":
    main()
