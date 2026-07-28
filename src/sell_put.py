"""Sell Put 选点位分析：期权墙 + 技术面交叉验证。

分析流程（固化自手工分析）：
1. 确保当日 GEX 明细存在（没有则调 compute_gex.py 拉取，IBKR 优先、yfinance 兜底）
2. 从 GEX 明细筛 spot 以下的 put：找最大 OI 行权价（put 墙 = dealer 对冲磁吸位）
3. 候选点位 = put 墙（主选）+ 墙下一档（保守），各取 OI 最大的到期日
4. 技术面交叉：MA20/60/120、RSI、ATR、近期低点（判断破位风险）
5. 输出收益/风险表：距离、ATR 倍数、delta、权利金收益率（年化）

用法:
    uv run python src/sell_put.py --symbol TSM                # 有当日数据就直接用
    uv run python src/sell_put.py --symbol TSM --fetch        # 强制重新拉取
    uv run python src/sell_put.py --symbol TSM --port 4001 --batch-size 50
"""

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent)
)  # 允许直接 python src/sell_put.py
from src.indicators import compute_all_indicators, load_data


def latest_gex_csv(symbol: str) -> Path | None:
    """找最新的 GEX 明细 CSV（优先当天）。"""
    files = sorted(Path("data/gex").glob(f"{symbol}_gex_*.csv"))
    if not files:
        return None
    today = datetime.now().strftime("%Y%m%d")
    todays = [f for f in files if f.name.startswith(f"{symbol}_gex_{today}")]
    return todays[-1] if todays else files[-1]


def ensure_gex_data(symbol: str, args: argparse.Namespace) -> Path:
    """有当日 CSV 直接复用；否则子进程调 compute_gex.py 拉取（复用其全部降级逻辑）。"""
    csv = latest_gex_csv(symbol)
    today = datetime.now().strftime("%Y%m%d")
    if csv and not args.fetch and today in csv.name:
        print(f"复用当日数据: {csv}")
        return csv
    cmd = [
        "uv",
        "run",
        "python",
        "src/compute_gex.py",
        "--symbol",
        symbol,
        "--port",
        str(args.port),
        "--batch-size",
        str(args.batch_size),
    ]
    print(f"拉取期权数据: {' '.join(cmd)}")
    if subprocess.run(cmd).returncode != 0:
        sys.exit("compute_gex 失败")
    csv = latest_gex_csv(symbol)
    if not csv:
        sys.exit("未生成 GEX CSV")
    return csv


def spot_of(symbol: str) -> float:
    """从股票日线取最新收盘价作为 spot 参考。"""
    for kind in ("stocks", "indices"):
        p = Path(f"data/{kind}/{symbol}.csv")
        if p.exists():
            return float(pd.read_csv(p)["close"].iloc[-1])
    sys.exit(f"无 {symbol} 日线数据，先 ./bin/fetch_ibkr --symbols {symbol}")


def analyze_puts(df: pd.DataFrame, spot: float, atr: float) -> pd.DataFrame:
    """spot 以下 put 按行权价聚合，附最优到期日的报价/收益指标。"""
    p = df[(df.right == "P") & (df.strike < spot) & (df.openInterest > 0)].copy()
    if p.empty:
        sys.exit("spot 以下无 OI>0 的 put")
    p["mid"] = (p.bid + p.ask) / 2
    p["dte"] = (
        pd.to_datetime(p.expiration, format="%Y%m%d") - pd.Timestamp.now()
    ).dt.days

    rows = []
    for strike, g in p.groupby("strike"):
        best = g.loc[g.openInterest.idxmax()]  # OI 最大的到期日为代表
        dist = (spot - strike) / spot
        y = best.mid / strike
        rows.append(
            {
                "strike": strike,
                "exp": best.expiration,
                "dte": best.dte,
                "mid": best.mid,
                "delta": best.delta,
                "iv%": best.impliedVolatility * 100,
                "OI_total": int(g.openInterest.sum()),
                "dist%": dist * 100,
                "ATR_x": (spot - strike) / atr if atr else 0,
                "yield%": y * 100,
                "年化%": y / best.dte * 365 * 100 if best.dte > 0 else 0,
            }
        )
    return (
        pd.DataFrame(rows).sort_values("strike", ascending=False).reset_index(drop=True)
    )


def report(symbol: str, cands: pd.DataFrame, spot: float) -> None:
    """技术面 + 候选点位报告。"""
    df = compute_all_indicators(load_data(f"data/stocks/{symbol}.csv"))
    r = df.iloc[-1]
    print(f"\n{'=' * 66}")
    print(f"  {symbol} Sell Put 分析  —  Spot: ${spot:.2f}")
    print(f"{'=' * 66}")
    print(
        f"技术面: close={r.close:.2f} MA20={r.MA20:.1f} MA60={r.MA60:.1f} "
        f"MA120={r.MA120:.1f} RSI={r.RSI:.0f} ATR={r.ATR:.1f}"
    )
    lo30, lo90 = df.close.tail(30).min(), df.close.tail(90).min()
    print(f"近30日最低: {lo30:.1f}  近90日最低: {lo90:.1f}")
    if r.close < r.MA20 and r.close < r.MA60:
        print("⚠️  破位中（跌破 MA20/60）：减仓或等企稳，put 墙不是底")

    wi = cands.OI_total.idxmax()  # put 墙位置（最大 OI）
    cols = [
        "strike",
        "exp",
        "dte",
        "mid",
        "delta",
        "iv%",
        "OI_total",
        "dist%",
        "ATR_x",
        "yield%",
        "年化%",
    ]
    show = cands.iloc[max(0, wi - 5) : wi + 1]  # 墙 + 墙以上 5 档（更激进的候选）
    ws, wo = cands.loc[wi, "strike"], cands.loc[wi, "OI_total"]
    print(f"\nPut 墙（最大 OI）: ${ws:.0f}  (OI {wo:,})")
    print(show[cols].round(2).to_string(index=False))
    w = cands.loc[wi]
    print(
        f"\n主选: ${w.strike:.0f}P {w.exp}  mid ${w.mid:.2f}  delta {w.delta:.2f}  "
        f"低于现价 {w['dist%']:.1f}% ({w.ATR_x:.1f}x ATR)  "
        f"权利金 {w['yield%']:.1f}% (年化 {w['年化%']:.0f}%)"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Sell Put 选点位分析")
    ap.add_argument("--symbol", default="AAPL")
    ap.add_argument(
        "--fetch", action="store_true", help="强制重新拉取（默认复用当日 CSV）"
    )
    ap.add_argument("--port", type=int, default=4001)
    ap.add_argument("--batch-size", type=int, default=50)
    args = ap.parse_args()
    symbol = args.symbol.upper()

    df = pd.read_csv(ensure_gex_data(symbol, args), dtype={"expiration": str})
    spot = spot_of(symbol)
    tech = compute_all_indicators(load_data(f"data/stocks/{symbol}.csv"))
    report(symbol, analyze_puts(df, spot, float(tech.iloc[-1].ATR)), spot)


if __name__ == "__main__":
    main()
