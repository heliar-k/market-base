"""Sell Put 选点位分析：期权墙 + 技术面交叉验证。

分析流程（固化自手工分析）：
1. 确保当日 GEX 明细存在（没有则调 compute_gex.py 拉取，IBKR 优先、yfinance 兜底）
2. 从 GEX 明细筛 spot 以下的 put：找最大 OI 行权价（put 墙 = dealer 对冲磁吸位）
3. IBKR bid/ask 缺失时自动用 yfinance 补全报价
4. 技术面交叉：MA20/60/120、RSI、ATR、近期低点（判断破位风险）
5. 按 DTE 分三段展示（近端/中端/远端），跨到期日对比权利金年化

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
import yfinance as yf

from src.fetchers.yfinance_fetcher import ensure_yf_proxy
from src.indicators import compute_all_indicators, load_data

ensure_yf_proxy()


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
        "-m",
        "src.compute_gex",
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


def _yf_option_chain(symbol: str) -> pd.DataFrame:
    """用 yfinance 获取全部到期日的 put 链（主数据源）。"""
    tk = yf.Ticker(symbol)
    rows: list[dict] = []
    for yf_date in tk.options:
        dte = (pd.to_datetime(yf_date) - pd.Timestamp.now()).days
        if dte < 0:
            continue
        try:
            for _, r in tk.option_chain(yf_date).puts.iterrows():
                rows.append(
                    {
                        "expiration": yf_date.replace("-", ""),
                        "dte": dte,
                        "strike": r["strike"],
                        "bid": r.get("bid", None),
                        "ask": r.get("ask", None),
                        "impliedVolatility": r.get("impliedVolatility", None),
                        "openInterest": r.get("openInterest", 0),
                        "delta": None,
                        "source": "yf",
                    }
                )
        except Exception:
            continue
    return pd.DataFrame(rows)


def _yf_spot(symbol: str) -> float | None:
    """用 yfinance 获取实时报价，失败返回 None。"""
    try:
        return float(yf.Ticker(symbol).fast_info.last_price)
    except Exception:
        return None


def analyze_puts(
    gex: pd.DataFrame, spot: float, atr: float, symbol: str
) -> pd.DataFrame:
    """spot 以下 put，保留所有 (strike, expiration)；

    IBKR 数据好则直接用，否则 yfinance 补全。
    """
    p = gex[(gex.right == "P") & (gex.strike < spot) & (gex.openInterest > 0)].copy()
    if p.empty:
        sys.exit("spot 以下无 OI>0 的 put")
    p["dte"] = (
        pd.to_datetime(p.expiration, format="%Y%m%d") - pd.Timestamp.now()
    ).dt.days
    p = p[p["dte"] > 0]

    has_ba = "bid" in p.columns and "ask" in p.columns
    if has_ba:
        p["mid"] = (p.bid + p.ask) / 2
        ibkr_ok = p["mid"].notna().mean() >= 0.8
    else:
        ibkr_ok = False

    if not ibkr_ok:
        # yfinance 做主数据源，IBKR greeks 为辅
        yfdf = _yf_option_chain(symbol)
        if not yfdf.empty:
            yfdf = yfdf[(yfdf.strike < spot) & (yfdf.openInterest > 0)].copy()
            # 补充 IBKR delta（如果有的话）
            if "delta" in gex.columns:
                ib = gex[(gex.right == "P") & gex["delta"].notna()]
                if not ib.empty:
                    ibd = ib[["expiration", "strike", "delta"]].rename(
                        columns={"delta": "delta_ib"}
                    )
                    yfdf = yfdf.merge(ibd, on=["expiration", "strike"], how="left")
                    yfdf["delta"] = yfdf["delta_ib"].where(
                        yfdf["delta_ib"].notna(), yfdf["delta"]
                    )
            yfdf["mid"] = (yfdf.bid + yfdf.ask) / 2
            p = yfdf[yfdf["mid"].notna() & (yfdf["mid"] > 0)].copy()
    else:
        p = p[p["mid"].notna() & (p["mid"] > 0)]

    if p.empty:
        sys.exit("无可用的期权报价")

    dist = (spot - p.strike) / spot
    yld = p.mid / p.strike
    return (
        pd.DataFrame(
            {
                "strike": p.strike,
                "exp": p.expiration,
                "dte": p.dte,
                "mid": p.mid,
                "delta": p.delta,
                "iv%": p.impliedVolatility * 100,
                "OI": p.openInterest.astype(int),
                "dist%": dist * 100,
                "ATR_x": (spot - p.strike) / atr if atr else 0,
                "yield%": yld * 100,
                "年化%": yld / p.dte * 365 * 100,
            }
        )
        .sort_values(["dte", "strike"], ascending=[True, False])
        .reset_index(drop=True)
    )


def _md_table(title: str, rows: pd.DataFrame) -> None:
    """打印期权候选 markdown 表格。"""
    if rows.empty:
        return
    print(f"\n#### {title}\n")
    print("| 行权价 | 到期日 | DTE | Mid | Delta | IV | OI | Dist% | ATR | 年化% |")
    print("|--------|--------|-----|-----|-------|-----|-----|-------|-----|-------|")
    for _, r in rows.iterrows():
        exp_s = str(r["exp"])
        exp_md = f"{exp_s[4:6]}/{exp_s[6:8]}"
        delta_s = f"{r['delta']:.2f}" if pd.notna(r.get("delta")) else "—"
        print(
            f"| ${r['strike']:.0f} | {exp_md} | {r['dte']:.0f}d | ${r['mid']:.2f} "
            f"| {delta_s} | {r['iv%']:.0f}% | {r['OI']:,} | {r['dist%']:.1f}% "
            f"| {r['ATR_x']:.1f}x | {r['年化%']:.0f}% |"
        )


def report(
    symbol: str, cands: pd.DataFrame, spot: float, tech: pd.DataFrame, gex: pd.DataFrame
) -> None:
    """技术面 + GEX 墙 + 分段候选点位 + 判断结论（markdown 报告）。"""
    latest = tech.iloc[-1]
    lo30, lo90 = tech.close.tail(30).min(), tech.close.tail(90).min()
    breaking = latest.close < latest.MA20 and latest.close < latest.MA60

    print(f"\n## {symbol} Sell Put 分析 — Spot: ${spot:.2f}\n")

    # ── 当前状态 ──
    print("### 当前状态\n")
    print("| 指标 | 数值 |")
    print("|------|------|")
    print(f"| **现价** | ${spot:.2f} |")
    print(f"| MA20 | ${latest.MA20:.1f} |")
    print(f"| MA60 | ${latest.MA60:.1f} |")
    print(f"| MA120 | ${latest.MA120:.1f} |")
    print(f"| RSI | {latest.RSI:.0f} |")
    print(f"| ATR(14) | ${latest.ATR:.1f} |")
    print(f"| 近30日最低 | ${lo30:.1f} |")
    print(f"| 近90日最低 | ${lo90:.1f} |")
    if breaking:
        rsi_desc = (
            "超卖" if latest.RSI < 30 else ("偏弱" if latest.RSI < 40 else "未超卖")
        )
        print(
            f"\n> ⚠️ **破位中**："
            f"价格跌破 MA20/MA60，处于弱势。"
            f"RSI {latest.RSI:.0f} {rsi_desc}。"
        )

    # ── GEX 期权墙（OTM put，按行权价聚合）──
    otm = gex[gex.strike < spot]
    if not otm.empty:
        call_gex = otm[otm.right == "C"].groupby("strike")["gex"].sum()
        put_gex = otm[otm.right == "P"].groupby("strike")["gex"].sum()
        oi = otm.groupby("strike")["openInterest"].sum()
        wall = pd.DataFrame(
            {"call_gex": call_gex, "put_gex": put_gex, "oi": oi}
        ).fillna(0)
        wall["total_gex"] = wall["call_gex"] + wall["put_gex"]
        wall = (
            wall.sort_values("oi", ascending=False).head(5).sort_index().reset_index()
        )
        net_gex = gex["gex"].sum()
        put_wall = oi.idxmax()

        print("\n### GEX 期权墙\n")
        print("| 行权价 | Call GEX | Put GEX | Total GEX | OI |")
        print("|--------|----------|---------|-----------|-----|")
        for _, r in wall.iterrows():
            print(
                f"| ${r['strike']:.0f} | ${r['call_gex']:,.0f} | ${r['put_gex']:,.0f} "
                f"| ${r['total_gex']:,.0f} | {r['oi']:,.0f} |"
            )
        direction = "做空" if net_gex < 0 else "做多"
        effect = "波动可能放大" if net_gex < 0 else "市场趋于稳定"
        print(
            f"\n净 GEX **${net_gex:,.0f}** → dealer {direction} gamma，{effect}。"
            f"Put 墙集中在 **${put_wall:.0f}**（OI 最大）。"
        )

    # ── Sell Put 候选（按 DTE 分三段）──
    near = cands[(cands.dte >= 7) & (cands.dte <= 50)].head(6)
    mid = cands[(cands.dte > 50) & (cands.dte <= 141)].head(6)
    far = cands[cands.dte > 141].head(6)

    print("\n### Sell Put 候选\n")
    _md_table("近端 (DTE ≤ 50) — 高收益但安全边际小", near)
    _md_table("中端 (DTE 51-141) — 甜点区", mid)
    _md_table("远端 (DTE > 141) — 稳健收租", far)

    # ── 判断 ──
    print("\n### 判断\n")
    best = None
    if not mid.empty:
        best = mid.loc[mid["OI"].idxmax()]
    elif not near.empty:
        best = near.loc[near["OI"].idxmax()]
    if best is not None:
        print(
            f"- **${best.strike:.0f}P（主选）**："
            f"距现价 {best['dist%']:.1f}%（{best.ATR_x:.1f}x ATR），"
            f"OI={best.OI:,}，年化 {best['年化%']:.0f}%。"
        )

    near_risky = near[near.ATR_x < 1.0]
    if not near_risky.empty:
        print(
            f"- **近端 "
            f"${near_risky.strike.min():.0f}-"
            f"${near_risky.strike.max():.0f}P**："
            f"距现价 <1x ATR，破位行情下被穿风险大，不推荐。"
        )

    if not far.empty:
        safe = far[far["dist%"] >= 8]
        if not safe.empty:
            bf = safe.loc[safe["年化%"].idxmax()]
            print(
                f"- **${bf.strike:.0f}P（保守）**："
                f"距现价 {bf['dist%']:.1f}%（{bf.ATR_x:.1f}x ATR），"
                f"OI={bf.OI:,}，年化 {bf['年化%']:.0f}%。"
            )

    if breaking:
        print("- **关键风险**：技术面破位中，MA20/60 都在上方压制。Put 墙不是铁底。")

    # ── 结论 ──
    print("\n**结论**：", end="")
    if breaking:
        wait = (
            f"等企稳信号（重新站上 MA20 或 RSI 拐头上 40）再考虑 ${best.strike:.0f}P。"
            if best is not None
            else "等企稳信号再入场。"
        )
        print(f"当前破位行情不适合马上 sell put，{wait}")
    elif best is not None:
        print(f"技术面健康，可考虑 ${best.strike:.0f}P 收租。")
    else:
        print("无合适候选。")


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

    # 加载技术面数据（stocks 或 indices）
    for kind in ("stocks", "indices"):
        data_path = Path(f"data/{kind}/{symbol}.csv")
        if data_path.exists():
            break
    else:
        sys.exit(f"无 {symbol} 日线数据，先 ./bin/fetch_ibkr --symbols {symbol}")
    tech = compute_all_indicators(load_data(str(data_path)))
    csv_close = float(tech["close"].iloc[-1])
    live_spot = _yf_spot(symbol)
    if live_spot and abs(live_spot - csv_close) / csv_close > 0.005:
        spot = live_spot
        print(f"⚠️ CSV 收盘 ${csv_close:.2f} → 实时 ${live_spot:.2f}，用实时价\n")
    else:
        spot = live_spot or csv_close
    gex_raw = pd.read_csv(ensure_gex_data(symbol, args), dtype={"expiration": str})
    cands = analyze_puts(gex_raw, spot, float(tech.iloc[-1].ATR), symbol)
    report(symbol, cands, spot, tech, gex_raw)


if __name__ == "__main__":
    main()
