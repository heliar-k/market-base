"""分时价位分析：单日分钟线的触及次数 + 成交量价位分布。

回答三个问题：
1. 某价位是"平台"还是"插针"（触及次数 / 极值附近 bar 数）
2. 筹码密集区在哪（成交量按价位分布，量大的价位 = 供应/支撑区）
3. 指定价位上方/下方的价格行为明细（--above / --below 逐 bar 打印）

用法：
    uv run python -m src.intraday_levels MU                      # 最近一个交易日 5m
    uv run python -m src.intraday_levels MU --date 2026-08-06
    uv run python -m src.intraday_levels MU --levels 895,900,913.33 --above 893
    uv run python -m src.intraday_levels MU --bar 15m            # 其他周期
"""

import argparse
from pathlib import Path

import pandas as pd


def load_day(symbol: str, bar: str, date: str | None) -> pd.DataFrame:
    path = Path(f"data/stocks/{symbol}_{bar}.csv")
    if not path.exists():
        raise SystemExit(
            f"数据不存在: {path}（Actions minute_bars 每日更新，先 git pull）"
        )
    df = pd.read_csv(path, parse_dates=["date"], date_format="mixed")
    df["date"] = pd.to_datetime(df["date"], utc=True, format="mixed").dt.tz_convert(
        "US/Eastern"
    )
    if date:
        df = df[df["date"].dt.strftime("%Y-%m-%d") == date]
    else:
        df = df[df["date"].dt.date == df["date"].dt.date.max()]
    if df.empty:
        raise SystemExit(f"{date} 无数据（非交易日或未更新）")
    return df.set_index("date")


def analyze(
    symbol: str,
    bar: str,
    date: str | None,
    levels: list[float] | None,
    above: float | None,
    below: float | None,
) -> None:
    d = load_day(symbol, bar, date)
    day = d.index[0].strftime("%Y-%m-%d")
    hi, lo = d["high"].max(), d["low"].min()
    hi_t, lo_t = (
        d["high"].idxmax().strftime("%H:%M"),
        d["low"].idxmin().strftime("%H:%M"),
    )
    vol_m = d["volume"].sum() / 1e6
    print(
        f"== {symbol} {day} {bar} ==  O{d['open'].iloc[0]:.2f} H{hi:.2f}@{hi_t} "
        f"L{lo:.2f}@{lo_t} C{d['close'].iloc[-1]:.2f}  V{vol_m:.1f}M  {len(d)} bars"
    )

    # 极值是插针还是平台：极值 0.3% 以内的 bar 数
    spike = len(d[d["high"] >= hi * 0.997])
    verdict = "插针（扫流动性，易被再扫）" if spike <= 6 else "平台（真实供应）"
    print(f"高点性质: {spike} 根 bar 在高点 0.3% 以内 → {verdict}")

    # 触及次数：level 落在 bar [low, high] 内记一次
    if levels is None:
        step = max(1, round((hi - lo) / 15))  # ponytail: 自适应 ~15 档
        levels = [x for x in range(int(lo) + 1, int(hi)) if x % step == 0]
    print("\n-- 触及次数（platform vs 线）--")
    for lv in levels:
        t = d[(d["low"] <= lv) & (d["high"] >= lv)]
        if len(t):
            first, last = t.index[0].strftime("%H:%M"), t.index[-1].strftime("%H:%M")
            print(f"  {lv:>8}: {len(t):>3} 次  首 {first} 尾 {last}")

    # 成交量价位分布（typical price 加权）
    step = max(1, round((hi - lo) / 12))
    d = d.assign(bucket=((d["high"] + d["low"] + d["close"]) / 3 / step).round() * step)
    vp = d.groupby("bucket")["volume"].sum().sort_values(ascending=False)
    total = vp.sum()
    print(f"\n-- 成交量价位分布（bucket={step}，前 8）--")
    for px, v in vp.head(8).items():
        blocks = "█" * int(v / vp.iloc[0] * 30)
        print(f"  {px:>8.0f}  {v / 1e6:5.1f}M  {v / total:4.0%}  {blocks}")

    # 阈值明细
    for label, mask in (
        ("above", above and d["high"] >= above),
        ("below", below and d["low"] <= below),
    ):
        th = above if label == "above" else below
        if th is None:
            continue
        print(f"\n-- {label} {th} 的 bar --")
        for ts, r in d[mask].iterrows():
            hm = ts.strftime("%H:%M")
            print(
                f"  {hm} O{r['open']:7.1f} H{r['high']:7.1f} "
                f"L{r['low']:7.1f} C{r['close']:7.1f} V{r['volume'] / 1e3:5.0f}k"
            )


def main() -> None:
    p = argparse.ArgumentParser(description="分时价位分析：触及次数 + 量能分布")
    p.add_argument("symbol")
    p.add_argument("--date", help="YYYY-MM-DD，默认最近一个有数据的交易日")
    p.add_argument("--bar", default="5m", choices=["5m", "15m", "1h", "4h"])
    p.add_argument("--levels", help="逗号分隔价位，默认自适应生成")
    p.add_argument("--above", type=float, help="打印 high ≥ 此价位的 bar")
    p.add_argument("--below", type=float, help="打印 low ≤ 此价位的 bar")
    a = p.parse_args()
    levels = [float(x) for x in a.levels.split(",")] if a.levels else None
    analyze(a.symbol.upper(), a.bar, a.date, levels, a.above, a.below)


if __name__ == "__main__":
    main()
