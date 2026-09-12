# ruff: noqa: E501  # 一次性回测脚本，输出宽表可读性优先
"""页面叙事断言回测 v3：股债背离（MOVE高/VIX低）收敛方式 + 事件级基础率。

来源：src/volatility_dashboard.py 交叉信号段。只读数据，uv run python 本文件复现。
"""

import pathlib as _pl

import numpy as np
import pandas as pd

BASE = str(_pl.Path(__file__).resolve().parents[2])
VOL = pd.read_csv(
    f"{BASE}/data/cboe/volatility.csv", index_col="date", parse_dates=True
)
ASSET = pd.read_csv(
    f"{BASE}/data/yfinance/asset_prices.csv", index_col="date", parse_dates=True
)

v = VOL[["VIX", "SKEW"]].dropna()
vix = v.VIX
rev = vix.iloc[::-1]
fwd20 = rev.rolling(20, min_periods=1).max().iloc[::-1].shift(-1)
gain20 = fwd20 / vix - 1
p90 = v.SKEW.rolling(250).quantile(0.90)
print("== 事件级基础率（非重叠网格：每 20 交易日取一日）==")
grid = np.arange(0, len(vix) - 21, 20)
for name, mask in {"SKEW>=150": v.SKEW >= 150, "SKEW>=90分位": v.SKEW >= p90}.items():
    mv = mask.values
    sig_g = [i for i in grid if mv[i]]
    oth_g = [
        i for i in grid if not mv[i] and not np.any(mv[max(0, i - 20) : i])
    ]  # 前 20 日无信号
    for th in (0.2, 0.3):
        a = np.mean([gain20.iloc[i] >= th for i in sig_g]) * 100
        b = np.mean([gain20.iloc[i] >= th for i in oth_g]) * 100
        print(
            f"{name} ≥{int(th * 100)}%: 信号窗 {a:.0f}% (n={len(sig_g)})  干净对照窗 {b:.0f}% (n={len(oth_g)})  差 {a - b:+.0f}pp"
        )

print("\n== 断言 3：原始连续段（不按 20 日去重） ==")
j = pd.concat([VOL["VIX"], ASSET["MOVE"].rename("MOVE")], axis=1, sort=False).dropna()


def detail(mv, vx, thr_mv, thr_vix, horizon=60, dedup=None):
    q = (mv.values > thr_mv) & (vx.values < thr_vix)
    pos = [i for i in range(len(q)) if q[i] and (i == 0 or not q[i - 1])]
    pos = [i for i in pos if len(mv) - i > 10]
    anchors = pos
    if dedup is not None:
        merged = []
        for p in pos:
            if merged and p - merged[-1][-1] <= dedup:
                merged[-1].append(p)
            else:
                merged.append([p])
        anchors = [m[0] for m in merged]
    strict, loose_vix, loose_mv, both, days_late = 0, 0, 0, 0, []
    for st in anchors:
        e = min(st + horizon, len(mv) - 1)
        w_mv, w_vx = mv.values[st + 1 : e + 1], vx.values[st + 1 : e + 1]
        vg, md = w_vx.max() / vx.values[st] - 1, 1 - w_mv.min() / mv.values[st]
        hit_v, hit_m = vg >= 0.10, md >= 0.10
        loose_vix += hit_v
        loose_mv += hit_m
        both += hit_v and hit_m
        s = (vg >= 0.10 and vg > md) or (md >= 0.10 and vg < 0.02)
        strict += s
        if vg >= 0.10:
            days_late.append(int(np.argmax(w_vx >= vx.values[st] * 1.10)) + 1)
    n = len(anchors)
    print(
        f"MOVE>{thr_mv} & VIX<{thr_vix}  n={n}  严格判定(补涨|回落)={strict}({100 * strict / n:.0f}%)"
        f"  窗口内VIX曾升≥10%={loose_vix}({100 * loose_vix / n:.0f}%)"
        f"  窗口内MOVE曾跌≥10%={loose_mv}({100 * loose_mv / n:.0f}%)"
        f"  两者皆有={both}({100 * both / n:.0f}%)  VIX首破+10%中位天数={np.median(days_late):.0f}"
    )


print("原始连续段：")
for a, b in [(60, 15), (60, 20), (100, 20)]:
    detail(j.MOVE, j.VIX, a, b, dedup=None)
print("20 交易日去重 episode：")
for a, b in [(60, 15), (60, 20), (100, 20)]:
    detail(j.MOVE, j.VIX, a, b, dedup=20)

scale = (ASSET["MOVE"] / VOL["GVZ"]).dropna().median()
g = pd.concat(
    [VOL["VIX"], (VOL["GVZ"] * scale).rename("MOVE")], axis=1, sort=False
).dropna()
print("\nGVZ 代理（长样本 2009–2026）原始连续段：")
for a, b in [(60, 15), (60, 20)]:
    detail(g.MOVE, g.VIX, a, b, dedup=None)
print("GVZ 代理 20 日去重 episode：")
for a, b in [(60, 15), (60, 20)]:
    detail(g.MOVE, g.VIX, a, b, dedup=20)

# VIX 在背离期后的方向分布（长样本 GVZ 口径，宽松）
print("\nGVZ 口径 episode（MOVE>60等值 & VIX<15）VIX 60日最大涨幅分布：")
q = (g.MOVE > 60) & (g.VIX < 15)
qnv = q.to_numpy()
pos = [i for i in range(len(qnv)) if qnv[i] and (i == 0 or not qnv[i - 1])]
merged = []
for p in pos:
    if merged and p - merged[-1][-1] <= 20:
        merged[-1].append(p)
    else:
        merged.append([p])
vals = []
for st in [m[0] for m in merged]:
    e = min(st + 60, len(g) - 1)
    if e - st < 10:
        continue
    vals.append(100 * (g.VIX.values[st + 1 : e + 1].max() / g.VIX.values[st] - 1))
vals = np.array(vals)
print(
    f"n={len(vals)}  中位 +{np.median(vals):.1f}%  P25 +{np.percentile(vals, 25):.1f}%  P75 +{np.percentile(vals, 75):.1f}%  ≥10%占比 {100 * (vals >= 10).mean():.0f}%  ≥20% {100 * (vals >= 20).mean():.0f}%"
)
