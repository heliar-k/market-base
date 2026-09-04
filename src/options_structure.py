"""
期权市场结构快照（timsun /assets/equities/options 面板数据源）。

对 13 个指数/ETF/龙头股拉取 yfinance 期权链（真实 OI + IV），用 BS 模型反推
Greeks（gamma/delta/vanna/charm），输出每组聚合指标：
  - Net GEX / Net DEX（按 timsun 口径：call 端正向、put 端卖保护翻号）
  - Gamma Flip + 现价距离、Call/Put Wall（每点 $M）、25Δ Skew
  - IV 期限结构（Front/Back IV + 斜率）、到期 Gamma 集中度
  - OI Top 20（strike × 到期）
快照存 data/options_structure/{date}.json（全部标的汇总）+ {symbol}_{date}.json
（单标的明细；前端详图用），raw 链缓存 raw_{symbol}_{date}.csv 支持当日复用。

模型口径说明（与 timsun 页脚一致）：GEX/DEX/Vanna/Charm 为模型估算，
非交易所官方利用率或持仓方向；gamma 符号 call 正 / put 负。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from math import sqrt

import numpy as np
import pandas as pd

from src.config import ROOT
from src.pricing import R, _norm_pdf, bs_greeks, d1_from

logger = logging.getLogger(__name__)

DATA = ROOT / "data" / "options_structure"


def bucket_dte(days: int) -> str:
    """DTE → 到期 Gamma 集中度桶（timsun 口径：0DTE/本周/月度/季度+）。"""
    if days == 0:
        return "0DTE"
    if days <= 7:
        return "本周"
    if days <= 35:
        return "月度"
    return "季度+"


def _pcr_oi_atm(c: pd.DataFrame, spot: float) -> float | None:
    """ATM ±10% 行权价带的 P/C OI 比（剔深 OTM 存量污染）。"""
    a = c[c["strike"].between(spot * 0.9, spot * 1.1)]
    call_oi = float(a[a["right"] == "C"]["oi"].sum())
    if call_oi == 0:
        return None
    return round(float(a[a["right"] == "P"]["oi"].sum() / call_oi), 2)


# 13 标的看板（指数/ETF + Mag7 + 重点个股）
SYMBOLS: list[str] = [
    "SPY",
    "QQQ",
    "IWM",
    "NVDA",
    "AAPL",
    "MSFT",
    "AMZN",
    "META",
    "TSLA",
    "GOOGL",
    "AVGO",
    "BRK-B",
    "JPM",
]


def fetch_chain(
    symbol: str, expirations: list[str], use_cache: bool = True
) -> pd.DataFrame:
    """拉取期权链（strike/right/expiration/OI/volume/IV），当日 raw 缓存复用。

    raw 列：strike, right, expiration(YYYYMMDD str), openInterest, volume,
    impliedVolatility。timsun 口径按"最近可得 OI"——raw 当日复用即可。
    """
    raw = DATA / f"raw_{symbol}_{datetime.now():%Y%m%d}.csv"
    if use_cache and raw.exists():
        df = pd.read_csv(raw, dtype={"expiration": str})
        logger.info(f"  {symbol}: 复用 raw 缓存 {len(df)} 行")
        return df

    from src.fetchers.yfinance_fetcher import ensure_yf_proxy

    ensure_yf_proxy()
    import yfinance as yf  # noqa: E402

    ticker = yf.Ticker(symbol)
    rows = []
    for exp in expirations:
        yf_date = exp.replace("-", "")
        fmt = f"{yf_date[:4]}-{yf_date[4:6]}-{yf_date[6:8]}"
        try:
            chain = ticker.option_chain(fmt)
        except Exception as e:
            logger.warning(f"  {symbol} {fmt}: {e}")
            continue
        for right, df_c in [("C", chain.calls), ("P", chain.puts)]:
            for _, r in df_c.iterrows():
                oi = r.get("openInterest", 0)
                rows.append(
                    {
                        "strike": float(r["strike"]),
                        "right": right,
                        "expiration": yf_date,
                        "openInterest": int(oi) if pd.notna(oi) else 0,
                        "volume": int(r.get("volume", 0))
                        if pd.notna(r.get("volume", 0))
                        else 0,
                        "impliedVolatility": float(r["impliedVolatility"])
                        if pd.notna(r.get("impliedVolatility"))
                        else np.nan,
                    }
                )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    DATA.mkdir(parents=True, exist_ok=True)
    df.to_csv(raw, index=False)
    logger.info(f"  {symbol}: {len(df)} 条 → raw 缓存")
    return df


# ── 聚合计算 ────────────────────────────────────────────────────────────────


def compute_structure(df: pd.DataFrame, spot: float) -> dict:
    """单标的聚合（timsun /assets/equities/options 全套指标）。"""
    if df.empty or spot <= 0:
        return {"symbol": None}
    now = datetime.now()
    d = df.dropna(subset=["impliedVolatility"])
    d = d[d["impliedVolatility"] > 0]
    if d.empty:
        return {"symbol": None}

    rows = []
    for _, r in d.iterrows():
        exp = datetime.strptime(r["expiration"], "%Y%m%d")
        days = (exp - now).days
        if days < 0:
            continue  # 已过期的合约剔除；当日到期（0DTE）保留
        # 0DTE 期权的 T→0 会炸 gamma/char，用 1 天作最小 T（timsun 同口径保留当日桶）
        t = max(days, 1) / 365.0
        iv = float(r["impliedVolatility"])
        sig = r["right"] == "C"
        gamma_p, delta = bs_greeks(spot, r["strike"], t, iv)
        # gamma 符号：call 正 / put 负（timsun 与 compute_gex 统一口径）
        gamma = gamma_p if sig else -gamma_p
        # delta 卖保护口径：call 正向；put 取短保护（dealer 短 put = 正 delta）
        delta_d = delta if sig else 1.0 - delta
        # vanna/charm（标准 BSM）：call 正向、put 翻号
        d1 = d1_from(spot, r["strike"], t, iv)
        # vanna = ∂Vega/∂S = -γ·S·√T·d2（用 d2，d1 会在 ATM 附近引入 ~7% 偏差）
        vanna = -gamma * spot * sqrt(t) * (d1 - iv * sqrt(t))  # per share
        # charm = ∂Δ_call/∂T = φ(d1)·[(r+σ²/2)/(σ√T) − d1/(2T)]；put 翻号
        r_term = (R + iv * iv / 2) / (iv * sqrt(t)) - d1 / (2 * t)
        phi = _norm_pdf(d1)
        charm = phi * r_term if sig else -phi * r_term
        oi = int(r["openInterest"])
        rows.append(
            {
                "strike": r["strike"],
                "right": r["right"],
                "expiration": r["expiration"],
                "days": days,
                "oi": oi,
                "volume": int(r["volume"]) if pd.notna(r["volume"]) else 0,
                "iv": iv,
                "gamma": gamma,
                "delta": delta_d,
                "vanna": vanna,
                "charm": charm,
                "gex": gamma * oi * 100 * spot,
                "dex": delta_d * oi * 100 * spot,
                "vanna_tot": vanna * oi * 100,
                "charm_tot": charm * oi * 100,
            }
        )
    c = pd.DataFrame(rows)
    if c.empty:
        return {"symbol": None}

    # 行权价聚合（期权墙）
    wall = (
        c.groupby("strike")
        .agg(
            gex=("gex", "sum"),
            dex=("dex", "sum"),
            call_gex=("gex", lambda s: s[c.loc[s.index, "right"] == "C"].sum()),
            put_gex=("gex", lambda s: s[c.loc[s.index, "right"] == "P"].sum()),
            oi=("oi", "sum"),
        )
        .reset_index()
    )
    wall = wall.sort_values("strike").reset_index(drop=True)

    # Gamma Flip：累计 GEX 由负转正的穿越点中，按现价处累计符号取位——
    # 现价处累计 ≥0 → 取现价下方最后一个翻转点；<0 → 取现价上方第一个。
    # 只找现价下方第一个穿越有两个坑：①现价处累计已转负、翻转点在现价上方时
    # 漏检（SPY 20260904：现价处累计 -694M、穿越点在上方 800 → 返回 None，
    # REGIME 卡回退 net_gex 符号后与分布图直接矛盾）；②深 ITM 噪声穿越被误当
    # 翻转点（NVDA 取 103，真实翻转在 225）。取位规则保证
    # sign(flip_dist) == sign(现价处累计 GEX)，与前端分布图黑线一致；
    # 全链无负→正穿越时返回 None，此时 sign(net_gex) 即现价处符号，
    # 前端与 narrative 引擎同款回退（勿兜底最低行权价，0DTE 链远端价权会失真）。
    wall["cum"] = wall["gex"].cumsum()
    spot_rows = wall.loc[wall["strike"] <= spot, "cum"]
    spot_cum = float(spot_rows.iloc[-1]) if not spot_rows.empty else 0.0
    flips: list[float] = []
    prev_cum = 0.0
    for _, w in wall.iterrows():
        cum = float(w["cum"])
        if prev_cum < 0 <= cum:  # 仅负→正为翻转；正→负是反转向，不构成 Flip
            flips.append(float(w["strike"]))
        prev_cum = cum
    if spot_cum >= 0:
        below = [k for k in flips if k <= spot]
        flip = below[-1] if below else None
    else:
        above = [k for k in flips if k > spot]
        flip = above[0] if above else None

    # Walls：每点 $M（Σ γ·OI·100 / 1e6）按 call/put 两端
    call_wall = c[c["right"] == "C"].groupby("strike")["gex"].sum()
    put_wall = c[c["right"] == "P"].groupby("strike")["gex"].sum()
    cw = call_wall.idxmax() if not call_wall.empty else None
    pw = put_wall.idxmin() if not put_wall.empty else None

    # IV 期限结构（各到期 ATM = 距 spot 最近行权价的平均 IV）
    tm = (
        c.groupby("expiration")
        .apply(
            lambda g: float(g.loc[(g["strike"] - spot).abs().idxmin(), "iv"]),
            include_groups=False,
        )
        .sort_index()
    )
    # IV 需按到期日 DTE 排序（月末/季月长到期日期可能早于短期周选）
    dte_map = c.groupby("expiration")["days"].first()
    order = dte_map.sort_values()
    front = float(tm[order.index[0]]) if len(order) else None
    back = float(tm[order.index[-1]]) if len(order) else None
    front_dte = int(order.iloc[0]) if len(order) else None
    back_dte = int(order.iloc[-1]) if len(order) else None

    # 25Δ Skew：最近 ≥5d 到期，IV_put(Δ=-0.25) − IV_call(Δ=+0.25)
    skew = None
    skew_dte = None
    skew_put_iv = None
    skew_call_iv = None
    for exp in order.index:
        dt = int(dte_map[exp])
        if dt < 5:
            continue
        g = c[c["expiration"] == exp]
        put_strike = g[(g["right"] == "P") & (g["delta"].between(0.20, 0.30))]
        call_strike = g[(g["right"] == "C") & (g["delta"].between(0.70, 0.80))]
        if not put_strike.empty and not call_strike.empty:
            skew_put_iv = float(
                put_strike.sort_values("iv", ascending=False).iloc[0]["iv"]
            )
            skew_call_iv = float(
                call_strike.sort_values("iv", ascending=False).iloc[0]["iv"]
            )
            skew = round((skew_put_iv - skew_call_iv) * 100, 2)
            skew_dte = dt
            break

    # 到期 Gamma 集中度（按 DTE 桶占净 GEX 比例）
    c["bucket"] = c["days"].map(bucket_dte)
    final: dict = {
        "spot": round(spot, 2),
        "net_gex": round(float(c["gex"].sum()) / 1e9, 2),
        "net_dex": round(float(c["dex"].sum()) / 1e9, 2),
        "call_dex": round(float(c[c["right"] == "C"]["dex"].sum()) / 1e9, 2),
        "put_dex": round(float(c[c["right"] == "P"]["dex"].sum()) / 1e9, 2),
        "pcr_oi": round(
            float(c[c["right"] == "P"]["oi"].sum() / c[c["right"] == "C"]["oi"].sum()),
            2,
        )
        if c[c["right"] == "C"]["oi"].sum()
        else None,
        # 近端 P/C（ATM ±10% 行权价带）：全样本被深 OTM 存量大单污染（如 SPY
        # 11/20 500P OI 30 万），narrative 与页面展示用此口径更贴近市场结构
        # 近端 P/C（ATM ±10% 行权价带）：全样本被深 OTM 存量大单污染（如 SPY
        # 11/20 500P OI 30 万），narrative 与页面展示用此口径更贴近市场结构
        "pcr_oi_atm": _pcr_oi_atm(c, spot),
        "pcr_vol": round(
            float(
                c[c["right"] == "P"]["volume"].sum()
                / c[c["right"] == "C"]["volume"].sum()
            ),
            2,
        )
        if c[c["right"] == "C"]["volume"].sum()
        else None,
        "gamma_flip": flip,
        "flip_dist": round((spot - flip) / spot * 100, 2) if flip is not None else None,
        "call_wall": {
            "strike": float(cw),
            "per_point_m": round(float(call_wall[cw]) / 1e6, 1),
        }
        if cw is not None
        else None,
        "put_wall": {
            "strike": float(pw),
            "per_point_m": round(float(put_wall[pw]) / 1e6, 1),
        }
        if pw is not None
        else None,
        "iv_front": {"dte": front_dte, "iv": round(front * 100, 2)}
        if front is not None
        else None,
        "iv_back": {"dte": back_dte, "iv": round(back * 100, 2)}
        if back is not None
        else None,
        "iv_slope": round((back - front) * 100, 2)
        if front is not None and back is not None
        else None,
        "skew": skew,
        "skew_dte": skew_dte,
        "skew_put_iv": round(skew_put_iv * 100, 2) if skew_put_iv is not None else None,
        "skew_call_iv": round(skew_call_iv * 100, 2)
        if skew_call_iv is not None
        else None,
        "net_vanna": round(float(c["vanna_tot"].sum()) / 1e9, 2),
        "net_charm": round(float(c["charm_tot"].sum()) / 1e9, 2),
        "charm_near7": round(
            float(c[c["days"] <= 7]["charm_tot"].sum())
            / float(c["charm_tot"].sum())
            * 100,
            1,
        )
        if float(c["charm_tot"].sum()) != 0
        else None,
    }
    # 到期集中度（4 桶恒存在，0 值也显示——timsun 页面同款）
    # gex_pct = 桶内 Σ|gex| / 全链 Σ|gex|（绝对强度占比，四桶加总 = 100%）。
    # 不能分子用“桶净值”、分母用“全链绝对强度”——两者量纲不一致，加总只有
    # ~23%（SPY 实测），不可加和、不代表集中度。gex_m 带符号表达桶方向。
    abs_total = float(c["gex"].abs().sum())
    buckets: dict[str, dict] = {
        "0DTE": {"gex_pct": 0.0, "gex_m": 0.0, "contracts": 0},
        "本周": {"gex_pct": 0.0, "gex_m": 0.0, "contracts": 0},
        "月度": {"gex_pct": 0.0, "gex_m": 0.0, "contracts": 0},
        "季度+": {"gex_pct": 0.0, "gex_m": 0.0, "contracts": 0},
    }
    for b, g in c.groupby("bucket"):
        gex_sum = float(g["gex"].sum())
        bucket_strength = float(g["gex"].abs().sum())
        buckets[b] = {
            "gex_pct": round(bucket_strength / abs_total * 100, 1)
            if abs_total != 0
            else None,
            "gex_m": round(gex_sum / 1e6, 1),
            "contracts": int(g["oi"].sum()),
        }
    final["buckets"] = buckets
    # GEX 分布（wall 明细，供前端分布图）
    final["wall"] = [
        {
            "strike": float(w["strike"]),
            "gex_m": round(float(w["gex"]) / 1e6, 1),
            "call_gex_m": round(float(w["call_gex"]) / 1e6, 1),
            "put_gex_m": round(float(w["put_gex"]) / 1e6, 1),
            "oi": int(w["oi"]),
        }
        for _, w in wall.iterrows()
    ]
    # IV 期限结构序列
    final["iv_curve"] = [
        {"expiration": e, "dte": int(dte_map[e]), "iv": round(float(tm[e]) * 100, 2)}
        for e in order.index
    ]
    # OI Top 20（strike × 到期）— 按 |gamma 加权 GEX| 排序（pin 位参考，
    # 深 OTM 存量大单按总 OI 会占满榜，对 dealer 关键位无参考价值）
    top = (
        c.groupby(["strike", "expiration"])
        .agg(
            call_oi=("oi", lambda s: s[c.loc[s.index, "right"] == "C"].sum()),
            put_oi=("oi", lambda s: s[c.loc[s.index, "right"] == "P"].sum()),
            call_vol=("volume", lambda s: s[c.loc[s.index, "right"] == "C"].sum()),
            put_vol=("volume", lambda s: s[c.loc[s.index, "right"] == "P"].sum()),
            abs_gex=("gex", lambda s: abs(float(s.sum()))),
        )
        .reset_index()
    )
    top["total"] = top["call_oi"] + top["put_oi"]
    top = top.nlargest(20, "abs_gex")
    final["oi_top"] = [
        {
            "strike": float(r["strike"]),
            "expiration": (
                f"{r['expiration'][:4]}-{r['expiration'][4:6]}-{r['expiration'][6:8]}"
            ),
            "call_oi": int(r["call_oi"]),
            "put_oi": int(r["put_oi"]),
            "total": int(r["total"]),
            "call_vol": int(r["call_vol"]),
            "put_vol": int(r["put_vol"]),
        }
        for _, r in top.iterrows()
    ]
    # GEX 分布图（行权价 × bucketed gex）
    final["gex_dist"] = [
        {"strike": float(r["strike"]), "gex_m": round(float(r["gex"]) / 1e6, 1)}
        for _, r in c.groupby("strike")["gex"].sum().reset_index().iterrows()
    ]
    return final


def build_snapshot(symbols: list[str] | None = None, use_cache: bool = True) -> dict:
    """全标的快照 dict（写入 data/options_structure/{date}.json）。"""
    from src.fetchers.yfinance_fetcher import ensure_yf_proxy

    ensure_yf_proxy()
    import yfinance as yf  # noqa: E402

    symbols = symbols or SYMBOLS
    out: dict = {"date": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"), "symbols": {}}
    for sym in symbols:
        try:
            ticker = yf.Ticker(sym)
            # 覆盖四个到期桶（timsun 口径 0DTE/本周/月度/季度+）：
            # 近 35D 全收（月度桶含月选，SPY 9/18 月选 27D 曾被 ≥30D 采样跳过，
            # 月度桶 GEX 占比 20%→47.9%）+ 后续每 45D 一个代表（月度/季月尾部）
            today = datetime.now().date()

            def _dte(e: str) -> int:
                return (datetime.strptime(e, "%Y-%m-%d").date() - today).days

            exps = [e for e in ticker.options if _dte(e) <= 35]
            for dte_min in (36, 90, 180, 270):
                pick = next((e for e in ticker.options if _dte(e) >= dte_min), None)
                if pick and pick not in exps:
                    exps.append(pick)
            exps = [e.replace("-", "") for e in exps]
            spot = float(ticker.fast_info.last_price)
            if not exps or not spot:
                logger.warning(f"  {sym}: 无期权/无现价，跳过")
                continue
            chain = fetch_chain(sym, exps, use_cache=use_cache)
            detail = compute_structure(chain, spot)
            if detail.get("spot") is None:
                logger.warning(f"  {sym}: 计算为空，跳过")
                continue
            detail["symbol"] = sym
            detail["name"] = sym
            out["symbols"][sym] = detail
            logger.info(f"  {sym}: spot={detail['spot']} netGEX={detail['net_gex']}B")
        except Exception as e:
            logger.warning(f"  {sym}: {e}")
    return out


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="期权结构快照（yfinance 降级源）")
    parser.add_argument(
        "--symbols", default="", help="逗号分隔标的列表（默认 13 标的）"
    )
    parser.add_argument("--no-cache", action="store_true", help="不复用当日 raw 链缓存")
    args = parser.parse_args()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] or None

    snapshot = build_snapshot(symbols, use_cache=not args.no_cache)
    if snapshot["symbols"]:
        DATA.mkdir(parents=True, exist_ok=True)
        path = DATA / f"{datetime.now():%Y%m%d}.json"
        # 子集运行（--symbols）合并当天既有快照，避免丢失其余标的（全量运行则覆盖）
        if symbols and path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            existing["symbols"].update(snapshot["symbols"])
            snapshot = existing
        # compact 单行（程序消费的快照，不经 pretty-format-json——多行展开会让
        # git 差异不可读且体积翻倍）
        path.write_text(
            json.dumps(
                snapshot,
                ensure_ascii=False,
                allow_nan=False,
                default=str,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        logger.info(f"快照 → {path}（{len(snapshot['symbols'])} 标的）")
        # 清理过期 raw 链缓存（仅当日复用，隔天即废；避免 git 体积膨胀）
        cutoff = datetime.now().strftime("%Y%m%d")
        for f in DATA.glob("raw_*.csv"):
            if cutoff not in f.stem:
                f.unlink()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    main()
