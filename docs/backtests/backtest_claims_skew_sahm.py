# ruff: noqa: E501  # 一次性回测脚本，输出宽表可读性优先
"""页面叙事断言回测（Sahm / SKEW 领先 / 股债背离收敛）。只读数据。

来源：src/labor_analysis.py、src/volatility_analysis.py、src/volatility_dashboard.py
引用数字的文案改动需同步此脚本结论。uv run python docs/backtests/xxx.py 复现。
"""

import pathlib as _pl

import numpy as np
import pandas as pd

BASE = str(_pl.Path(__file__).resolve().parents[2])
LAB = pd.read_csv(
    f"{BASE}/data/fred/labor/labor.csv", index_col="date", parse_dates=True
)["UNRATE"].dropna()
VOL = pd.read_csv(
    f"{BASE}/data/cboe/volatility.csv", index_col="date", parse_dates=True
)
ASSET = pd.read_csv(
    f"{BASE}/data/yfinance/asset_prices.csv", index_col="date", parse_dates=True
)


def merge_runs(pos, gap):
    """把相邻（索引间隔 <= gap）的起点合并为一个 episode 锚点。"""
    anchors = []
    for p in pos:
        if anchors and p - anchors[-1][-1] <= gap:
            anchors[-1].append(p)
        else:
            anchors.append([p])
    return anchors


# ---------------- 断言 1 ----------------
print("=" * 70)
print("ASSERTION 1  Sahm rule")
u = LAB
avg3 = u.rolling(3).mean()


def sahms(win_includes_current=True):
    if win_includes_current:
        return avg3 - avg3.rolling(12).min()
    return avg3 - avg3.shift(1).rolling(12).min()


for label, sh in [("min窗口含当月(FRED口径)", True), ("min窗口不含当月", False)]:
    s = sahms(sh)
    trig = s[s >= 0.5]
    eps = merge_runs(list(trig.index), gap=pd.Timedelta(days=62))
    rows = []
    for ep in eps:
        p = u.index.get_loc(ep[0])
        rec = {
            "start": ep[0].date(),
            "m": len(ep),
            "peak12": round(u.iloc[p : p + 13].max() - u.iloc[p], 2),
        }
        for h in (3, 6, 12):
            rec[f"d{h}"] = (
                round(u.iloc[p + h] - u.iloc[p], 2) if p + h < len(u) else np.nan
            )
        rows.append(rec)
    d = pd.DataFrame(rows)
    print(
        f"\n-- {label}: 触发月 {len(trig)}  episodes {len(eps)}  最后触发 {trig.index[-1].date()}  当前 sahm {s.iloc[-1]:.2f}"
    )
    print(d.to_string(index=False))
    for h in (3, 6, 12):
        v = d[f"d{h}"].dropna()
        print(
            f"   +{h}m 上行 {(v > 0).sum()}/{len(v)} ({100 * (v > 0).mean():.0f}%)  中位 {v.median():+.2f}pp  区间[{v.min():+.2f},{v.max():+.2f}]"
        )
    print(
        f"   触发后 12 月内失业率峰值中位 +{d.peak12.median():.2f}pp，峰值最低 {d.peak12.min():+.2f}pp"
    )

# 剔除 2020（V 型）后的稳健性
s = sahms(True)
trig = s[s >= 0.5]
eps = merge_runs(list(trig.index), gap=pd.Timedelta(days=62))
ex = [e[0] for e in eps if e[0].year != 2020]
ups = {
    h: sum(
        1
        for m in ex
        if (u.index.get_loc(m) + h < len(u))
        and u.iloc[u.index.get_loc(m) + h] > u.iloc[u.index.get_loc(m)]
    )
    for h in (3, 6, 12)
}
print(f"剔除 2020-04 后 episodes={len(ex)}  上行计数 {ups}")
print(
    f"UNRATE 样本 {u.index[0].date()} – {u.index[-1].date()}（{len(u)} 月）  最新 {u.iloc[-1]}"
)

# ---------------- 断言 2 ----------------
print("=" * 70)
print("ASSERTION 2  SKEW -> VIX spike")
v = VOL[["VIX", "SKEW"]].dropna()
vix = v.VIX
print(
    f"样本 {v.index[0].date()} – {v.index[-1].date()}  交易日 {len(v)}  SKEW max {v.SKEW.max():.1f}  分位90 中位 {v.SKEW.rolling(250).quantile(0.9).median():.1f}"
)
p90 = v.SKEW.rolling(250).quantile(0.90)
sigs = {"SKEW>=150": v.SKEW >= 150, "SKEW>=近250日90分位": v.SKEW >= p90}
rows = []
rev = vix.iloc[::-1]
for k in (5, 10, 15, 20):
    fwd_max = (
        rev.rolling(k, min_periods=1).max().iloc[::-1].shift(-1)
    )  # 未来 k 日最大值（不含当日）
    gain = fwd_max / vix - 1
    for name, mask in sigs.items():
        mm = (mask & gain.notna()).astype(bool)
        base = gain.notna() & ~mask
        for th in (0.20, 0.30):
            hr = 100 * (gain[mm] >= th).mean()
            br = 100 * (gain[base] >= th).mean()
            rows.append(
                {
                    "口径": name,
                    "窗口": k,
                    "脉冲": f">{int(th * 100)}%",
                    "信号日": int(mm.sum()),
                    "命中率%": round(hr, 1),
                    "基础率%": round(br, 1),
                    "差值pp": round(hr - br, 1),
                    "提升x": round(hr / max(br, 0.05), 2),
                }
            )
print(pd.DataFrame(rows).to_string(index=False))
# 事件级（episode 去重，间隔 20 日）+ 领先天数
for name, mask in sigs.items():
    pos = list(np.where(mask.values)[0])
    anchors = [g[0] for g in merge_runs(pos, gap=20)]
    n = len(vix)
    lead_all, hit20, hit30 = [], 0, 0
    for i in anchors:
        tgt20, tgt30 = vix.iloc[i] * 1.2, vix.iloc[i] * 1.3
        for j in range(i + 1, min(i + 21, n)):
            if vix.iloc[j] >= tgt20:
                lead_all.append(j - i)
                hit20 += 1
                break
        for j in range(i + 1, min(i + 21, n)):
            if vix.iloc[j] >= tgt30:
                hit30 += 1
                break
    print(
        f"事件级[{name}] 独立 episode {len(anchors)}  20日内≥20%脉冲 {hit20}({100 * hit20 / len(anchors):.0f}%)  ≥30% {hit30}({100 * hit30 / len(anchors):.0f}%)  中位领先 {np.median(lead_all):.0f}d"
        if lead_all
        else name
    )
# 基础率（事件级近似：全体交易日随机抽样日 20 日窗口）
fwd20 = rev.rolling(20, min_periods=1).max().iloc[::-1].shift(-1)
g20 = fwd20 / vix - 1
for th in (0.2, 0.3):
    print(
        f"全样本基础率 20日 ≥{int(th * 100)}%: {100 * (g20.dropna() >= th).mean():.1f}%"
    )

# ---------------- 断言 3 ----------------
print("=" * 70)
print("ASSERTION 3  MOVE high / VIX low")
j = pd.concat([VOL["VIX"], ASSET["MOVE"].rename("MOVE")], axis=1).dropna()
print(
    f"MOVE 样本 {j.index[0].date()} – {j.index[-1].date()}  交易日 {len(j)}  MOVE {j.MOVE.min():.0f}–{j.MOVE.max():.0f}  VIX {j.VIX.min():.1f}–{j.VIX.max():.1f}"
)


def run(mv, vx, tag, horizon=60, dedup=20, verbose=True):
    q = ((mv.values > float(tag[0])) & (vx.values < tag[1])).astype(int)
    pos = [i for i in range(len(q)) if q[i] and (i == 0 or not q[i - 1])]  # 连续段起点
    pos = [i for i in pos if len(mv) - i > 10]
    anchors = [g[0] for g in merge_runs(pos, gap=dedup)]
    out = []
    for st in anchors:
        e = min(st + horizon, len(mv) - 1)
        w_mv, w_vx = mv.values[st + 1 : e + 1], vx.values[st + 1 : e + 1]
        vg, md = w_vx.max() / vx.values[st] - 1, 1 - w_mv.min() / mv.values[st]
        kind, days = "未收敛", None
        if vg >= 0.10 and vg > md:
            kind, days = "VIX补涨", int(np.argmax(w_vx >= vx.values[st] * 1.10)) + 1
        elif md >= 0.10 and vg < 0.02:
            kind, days = "MOVE回落", int(np.argmax(w_mv <= mv.values[st] * 0.90)) + 1
        out.append(
            {
                "start": mv.index[st].date(),
                "MOVE": round(mv.values[st], 1),
                "VIX": round(vx.values[st], 1),
                "VIX涨": round(100 * vg, 1),
                "MOVE跌": round(100 * md, 1),
                "方式": kind,
                "天数": days,
                "期末VIX": round(100 * (w_vx[-1] / vx.values[st] - 1), 1),
                "期末MOVE": round(100 * (w_mv[-1] / mv.values[st] - 1), 1),
            }
        )
    d = pd.DataFrame(out)
    c = d.方式.value_counts()
    print(
        f"\n[{tag[0]}/{tag[1]}] 连续段 {len(pos)} → episode {len(d)}  "
        + "  ".join(f"{k}={v}({100 * v / len(d):.0f}%)" for k, v in c.items())
        + f"  平均收敛天数={d.天数.dropna().mean():.0f}  中位={d.天数.dropna().median():.0f}"
    )
    print(
        f"   60日后 VIX 中位变化 {d['期末VIX'].median():+.1f}%  MOVE 中位变化 {d['期末MOVE'].median():+.1f}%"
    )
    if verbose:
        print(d.to_string(index=False))
    return d


run(j.MOVE, j.VIX, (60, 15))
run(j.MOVE, j.VIX, (60, 20))
run(j.MOVE, j.VIX, (100, 20))

# GVZ 长样本代理
scale = (ASSET["MOVE"] / VOL["GVZ"]).dropna().median()
g = pd.concat([VOL["VIX"], (VOL["GVZ"] * scale).rename("MOVE")], axis=1).dropna()
print(
    f"\nGVZ→MOVE 换算系数 {scale:.2f}  长样本 {g.index[0].date()} – {g.index[-1].date()} ({len(g)} 日)  GVZ {VOL.GVZ.dropna().min():.1f}-{VOL.GVZ.dropna().max():.1f}"
)
thr = 60 / scale
print(f"等效 GVZ 阈值 {thr:.1f}")
run(g.MOVE, g.VIX, (60, 15), verbose=False)
run(g.MOVE, g.VIX, (60, 20), verbose=False)
