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
from datetime import date, datetime, time, timedelta, timezone
from math import sqrt
from pathlib import Path
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import numpy as np
import pandas as pd

from src.config import ROOT
from src.pricing import (
    R,
    _norm_pdf,
    bs_greeks,
    cumulative_gamma_flip,
    d1_from,
    fetch_yf_chain,
    years_to_expiration,
)

logger = logging.getLogger(__name__)

DATA = ROOT / "data" / "options_structure"
ET = ZoneInfo("America/New_York")
XNYS = xcals.get_calendar("XNYS")


def _now_et() -> datetime:
    return datetime.now(ET)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# raw 链保留的文件日期数。入库的只有这个窗口内的快照：
# `wheel_scanner` 选点要逐合约明细（JSON 快照里没有），而它跨日回退最多用
# 最近一份，留太久只会让库里多存已废弃的到期日。
RAW_KEEP_DAYS = 3


def chain_health(
    chain: pd.DataFrame,
    expected_expirations: list[str] | None = None,
    require_iv: bool = True,
) -> tuple[bool, str]:
    """结构计算健康检查：到期日完整，OI 与有效 IV 覆盖率均至少一半。"""
    if chain.empty:
        return False, "链为空"
    required = {"expiration", "openInterest"}
    if require_iv:
        required.add("impliedVolatility")
    missing_columns = sorted(required - set(chain.columns))
    if missing_columns:
        return False, f"缺列 {','.join(missing_columns)}"
    if expected_expirations is not None:
        actual = set(chain["expiration"].dropna().astype(str))
        missing = sorted(set(expected_expirations) - actual)
        if missing:
            return False, f"缺到期日 {','.join(missing)}"
    oi_ok = float((pd.to_numeric(chain["openInterest"], errors="coerce").fillna(0) > 0).mean())
    iv_ok = (
        float((pd.to_numeric(chain["impliedVolatility"], errors="coerce").fillna(0) > 0.01).mean())
        if require_iv
        else 1.0
    )
    if oi_ok < 0.5 or (require_iv and iv_ok < 0.5):
        return False, f"结构数据未就绪(OI>0 {oi_ok:.0%}/IV>1% {iv_ok:.0%})"
    for expiration, group in chain.groupby("expiration"):
        group_oi = pd.to_numeric(group["openInterest"], errors="coerce").fillna(0)
        if not bool((group_oi > 0).any()):
            return False, f"{expiration} 无正 OI"
        if require_iv:
            group_iv = pd.to_numeric(group["impliedVolatility"], errors="coerce").fillna(0)
            if not bool((group_iv > 0.01).any()):
                return False, f"{expiration} 无有效 IV"
    return True, ""


def drop_placeholder_expirations(
    chain: pd.DataFrame, expected: list[str]
) -> tuple[pd.DataFrame, list[str], list[str]]:
    """剔掉整条链没有任何正 OI 的到期日，返回（链, 预期到期日, 被剔列表）。

    Yahoo 会给标的列出刚上市的周一/周三周权，链里只有几张稀疏行权价、OI 与成交量全空。
    这类到期日对 OI 加权的统计贡献恒为 0，留在链里只污染非加权口径（ATM IV 等），
    并让 chain_health 把整个标的判成「无正 OI」。
    """
    if chain.empty:
        return chain, expected, []
    exp = chain["expiration"].astype(str)
    positive = (
        (pd.to_numeric(chain["openInterest"], errors="coerce").fillna(0) > 0).groupby(exp).any()
    )
    dropped = sorted(positive.index[~positive].tolist())
    if not dropped:
        return chain, expected, []
    kept = chain[~exp.isin(dropped)].reset_index(drop=True)
    return kept, [e for e in expected if e not in dropped], dropped


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


# 期权结构看板（指数/ETF + Mag7 + 重点个股）
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
    "TSM",
    "UNH",
    "BRK-B",
    "JPM",
    "V",
    "MA",
    "ISRG",
    "ABBV",
    "COST",
    "LLY",
    "AMD",
    "INTC",
    "MU",
    "MSTR",
    "SNDK",
    "CRCL",
    "BMNR",
    "AEHR",
    "SPCX",
]


def fetch_chain(
    symbol: str,
    expirations: list[str],
    use_cache: bool = True,
    oi_date: str | None = None,
    chain_date: date | None = None,
) -> pd.DataFrame:
    """拉取完整期权链；任何预期到期日缺失都拒绝写入 raw。"""
    day = chain_date or _now_et().date()
    raw = DATA / f"raw_{symbol}_{day:%Y%m%d}.csv"
    if use_cache and raw.exists():
        df = pd.read_csv(raw, dtype={"expiration": str})
        final_cache = (
            "oi_final" in df.columns
            and bool(df["oi_final"].astype(str).str.lower().eq("true").all())
            and "oi_date" in df.columns
            and bool(df["oi_date"].eq(day.isoformat()).all())
        )
        if final_cache:
            logger.info(f"  {symbol}: 复用已回补 raw 缓存 {len(df)} 行")
            return df
        if day < _now_et().date():
            final_mask = (
                df["oi_final"].astype(str).str.lower().eq("true")
                if "oi_final" in df.columns
                else pd.Series(False, index=df.index)
            )
            if final_mask.any() and (
                "oi_date" not in df.columns or df["oi_date"].ne(day.isoformat()).any()
            ):
                raise RuntimeError("raw 的 OI 日期与快照日期不一致")
            healthy, why = chain_health(df)
            if healthy:
                logger.info(f"  {symbol}: 复用历史收盘 raw 缓存 {len(df)} 行")
                return df
            raise RuntimeError(f"历史 raw 不健康（{why}），拒绝用当前链重建")
        healthy, why = chain_health(df, expected_expirations=expirations)
        if healthy:
            if oi_date is not None and (
                "oi_date" not in df.columns or df["oi_date"].dropna().empty
            ):
                df["oi_date"] = oi_date
                df["oi_final"] = False
                _write_raw(raw, df)
            logger.info(f"  {symbol}: 复用 raw 缓存 {len(df)} 行")
            return df
        logger.warning(f"  {symbol}: 删除异常 raw 缓存（{why}）")
        raw.unlink()

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
                        "bid": float(r["bid"]) if pd.notna(r.get("bid")) else 0.0,
                        "ask": float(r["ask"]) if pd.notna(r.get("ask")) else 0.0,
                        "openInterest": int(oi) if pd.notna(oi) else 0,
                        "volume": int(r.get("volume", 0)) if pd.notna(r.get("volume", 0)) else 0,
                        "impliedVolatility": float(r["impliedVolatility"])
                        if pd.notna(r.get("impliedVolatility"))
                        else np.nan,
                    }
                )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df, expirations, dropped = drop_placeholder_expirations(df, expirations)
    if dropped:
        logger.warning(f"  {symbol}: 剔除无 OI 的占位到期日 {','.join(dropped)}")
    healthy, why = chain_health(df, expected_expirations=expirations)
    if not healthy:
        logger.warning(f"  {symbol}: 期权链异常，拒绝写入缓存（{why}）")
        return pd.DataFrame()
    df["quote_ts"] = _utc_now_iso()
    df["oi_date"] = oi_date
    df["oi_final"] = False
    DATA.mkdir(parents=True, exist_ok=True)
    _write_raw(raw, df)
    logger.info(f"  {symbol}: {len(df)} 条 → raw 缓存")
    return df


# ── 聚合计算 ────────────────────────────────────────────────────────────────


def compute_structure(df: pd.DataFrame, spot: float, as_of: datetime | None = None) -> dict:
    """单标的聚合；as_of 用于收盘定稿，实时模式默认取当前美东时间。"""
    if df.empty or spot <= 0:
        return {"symbol": None}
    now = as_of or _now_et()
    today = now.date()
    d = df.dropna(subset=["impliedVolatility"])
    d = d[d["impliedVolatility"] > 0.01]
    if d.empty:
        return {"symbol": None}

    rows = []
    for _, r in d.iterrows():
        exp = datetime.strptime(r["expiration"], "%Y%m%d").date()
        days = (exp - today).days
        t = years_to_expiration(r["expiration"], now)
        if days < 0 or t <= 0:
            continue
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

    # 行权价带：剔除远端噪声。新高价股的链含 $5 级深 ITM 存量（yfinance 全链返回），
    # 其累计穿越/idxmin 会把 flip/wall 拉飞（MU 20260911：flip=105 @ spot=977）。
    # 与 compute_gex.filter_options 同思想，带取 ±100%（timsun 墙位可离现价 ±20%+，
    # 不能照搬 12%）。仅作用于 walls；flip 保持全链累计曲线口径（基线不能带内重置，
    # 否则带底第一个大 OI 深 ITM call 直接把累计抬正，穿越结构全部消失）。
    cb = c[c["strike"].between(spot * 0.5, spot * 2.0)]

    wall["cum"] = wall["gex"].cumsum()
    flip = cumulative_gamma_flip(wall.set_index("strike")["gex"], spot)

    # Walls：每点 $M（Σ γ·OI·100 / 1e6）按 call/put 两端（同带内取位）
    call_wall = cb[cb["right"] == "C"].groupby("strike")["gex"].sum()
    put_wall = cb[cb["right"] == "P"].groupby("strike")["gex"].sum()
    cw = call_wall.idxmax() if not call_wall.empty else None
    pw = put_wall.idxmin() if not put_wall.empty else None

    # IV 期限结构（各到期 ATM = 距 spot 最近行权价的 Call/Put 平均 IV）
    def _atm_iv(g: pd.DataFrame) -> float:
        by_strike = g.groupby("strike")["iv"].mean()
        nearest = int(np.argmin(np.abs(by_strike.index.to_numpy() - spot)))
        return float(by_strike.iloc[nearest])

    tm = c.groupby("expiration").apply(_atm_iv, include_groups=False).sort_index()
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
        call_strike = g[(g["right"] == "C") & (g["delta"].between(0.20, 0.30))]
        if not put_strike.empty and not call_strike.empty:
            put = put_strike.loc[(put_strike["delta"] - 0.25).abs().idxmin()]
            call = call_strike.loc[(call_strike["delta"] - 0.25).abs().idxmin()]
            skew_put_iv = float(put["iv"])
            skew_call_iv = float(call["iv"])
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
            float(c[c["right"] == "P"]["volume"].sum() / c[c["right"] == "C"]["volume"].sum()),
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
        "iv_front": {"dte": front_dte, "iv": round(front * 100, 2)} if front is not None else None,
        "iv_back": {"dte": back_dte, "iv": round(back * 100, 2)} if back is not None else None,
        "iv_slope": round((back - front) * 100, 2)
        if front is not None and back is not None
        else None,
        "skew": skew,
        "skew_dte": skew_dte,
        "skew_put_iv": round(skew_put_iv * 100, 2) if skew_put_iv is not None else None,
        "skew_call_iv": round(skew_call_iv * 100, 2) if skew_call_iv is not None else None,
        "net_vanna": round(float(c["vanna_tot"].sum()) / 1e9, 2),
        "net_charm": round(float(c["charm_tot"].sum()) / 1e9, 2),
        "charm_near7": round(
            float(c[c["days"] <= 7]["charm_tot"].sum()) / float(c["charm_tot"].sum()) * 100,
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
            "gex_pct": round(bucket_strength / abs_total * 100, 1) if abs_total != 0 else None,
            "gex_m": round(gex_sum / 1e6, 1),
            "contracts": int(g["oi"].sum()),
        }
    final["buckets"] = buckets
    # GEX 分布（wall 明细，供前端分布图）
    final["wall"] = [
        {
            "strike": float(w["strike"]),
            "gex_m": round(float(w["gex"]) / 1e6, 1),
            "cum_gex_m": round(float(w["cum"]) / 1e6, 1),
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
    # 近现价 OI Top 20（strike × 到期）— ATM ±10% 内按 Call + Put 总 OI 排序
    near = c[c["strike"].between(spot * 0.9, spot * 1.1)]
    top = (
        near.groupby(["strike", "expiration"])
        .agg(
            call_oi=("oi", lambda s: s[c.loc[s.index, "right"] == "C"].sum()),
            put_oi=("oi", lambda s: s[c.loc[s.index, "right"] == "P"].sum()),
            call_vol=("volume", lambda s: s[c.loc[s.index, "right"] == "C"].sum()),
            put_vol=("volume", lambda s: s[c.loc[s.index, "right"] == "P"].sum()),
        )
        .reset_index()
    )
    top["total"] = top["call_oi"] + top["put_oi"]
    top = top.nlargest(20, "total")
    final["oi_top"] = [
        {
            "strike": float(r["strike"]),
            "expiration": (f"{r['expiration'][:4]}-{r['expiration'][4:6]}-{r['expiration'][6:8]}"),
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


_QUOTE_SUMMARY_URL = "https://query2.finance.yahoo.com/v10/finance/quoteSummary"


def _epoch(v) -> int | None:
    """quoteSummary 的时间戳：`formatted=false` 时是裸数字，否则包成 {raw, fmt}。"""
    return (v.get("raw") if isinstance(v, dict) else v) or None


def _extract_events(ticker) -> dict:
    """从 Yahoo quoteSummary 取下次财报日（美东日期）、最近已发布日与除息日（失败静默返回空 dict）。

    不用 `ticker.calendar`：它拿时间戳走 `datetime.fromtimestamp()`，按本机时区折算。
    财报时间戳固定是美东 16:00（= 21:00 UTC），在 UTC+8 机器上整批后移一天，本地跑
    `live_wheel` 时 GOOG 这类刚发完财报的标的会停在「财报0d·禁卖」。
    除息日的时间戳是 UTC 零点（不是美东 16:00），所以按 UTC 取日期。
    """
    try:
        raw = ticker._data.get_raw_json(
            f"{_QUOTE_SUMMARY_URL}/{ticker.ticker}",
            params={"modules": "calendarEvents", "formatted": "false"},
        )
        ev = raw["quoteSummary"]["result"][0]["calendarEvents"]
        earn = ev.get("earnings") or {}

        def _dates(key: str) -> list[date]:
            # 未确认的财报日可能给一个区间（两枚时间戳），取最早的一天：宁可提前禁卖
            stamps = earn.get(key) or []
            return sorted(datetime.fromtimestamp(ts, ET).date() for ts in map(_epoch, stamps) if ts)

        days, calls = _dates("earningsDate"), _dates("earningsCallDate")
        next_ed = days[0] if days else None
        # Yahoo 把下一季日期确认后就丢掉最近一次发布日（MSFT 在发布当晚 earningsDate
        # 已是 2027-01-27）；earningsCallDate 早于它就说明那是刚开过的电话会 = 刚发的那份财报。
        last_ed = calls[0] if calls and (next_ed is None or calls[0] < next_ed) else None
        ex_div = _epoch(ev.get("exDividendDate"))
        return {
            "earnings_date": next_ed.strftime("%Y-%m-%d") if next_ed else None,
            "last_earnings_date": last_ed.strftime("%Y-%m-%d") if last_ed else None,
            "ex_dividend_date": (
                datetime.fromtimestamp(ex_div, timezone.utc).date().strftime("%Y-%m-%d")
                if ex_div
                else None
            ),
        }
    except Exception:
        return {}


def _fetch_barchart_iv_ranks(symbols: list[str]) -> dict[str, dict]:
    """一次请求 Barchart core-api 获取全标的官方 52 周 IV Rank / IV Percentile / WIV。

    新加标的无需等待本地积累 1 年期权快照即可直接获得 1Y IV Rank & IV Percentile。
    失败（如测试环境禁用浏览器）时静默返回空 dict，不阻塞主流程。
    """
    try:
        from src.fetchers.barchart_client import core_get

        bc_to_orig = {s.replace("-", "."): s for s in symbols}
        data = core_get(
            {
                "symbols": ",".join(bc_to_orig.keys()),
                "fields": (
                    "symbol,optionsImpliedVolatilityRank1y,"
                    "optionsImpliedVolatilityPercentile1y,"
                    "optionsWeightedImpliedVolatility"
                ),
                "raw": "1",
            },
            referer="https://www.barchart.com/stocks/quotes/NVDA/overview",
        )
        out: dict[str, dict] = {}
        for row in data.get("data", []):
            r = row.get("raw") or row
            bc_sym = str(r.get("symbol", ""))
            orig = bc_to_orig.get(bc_sym, bc_sym.replace(".", "-"))
            ivr = r.get("optionsImpliedVolatilityRank1y")
            ivp = r.get("optionsImpliedVolatilityPercentile1y")
            wiv = r.get("optionsWeightedImpliedVolatility")
            out[orig] = {
                "iv_rank_1y": round(float(ivr), 1) if ivr is not None else None,
                "iv_pct_1y": round(float(ivp) * 100, 1) if ivp is not None else None,
                "wiv": round(float(wiv) * 100, 1) if wiv is not None else None,
            }
        return out
    except Exception as e:
        logger.warning(f"  Barchart 1Y IV Rank 批量获取跳过: {e}")
        return {}


def _is_session(day: date) -> bool:
    return bool(XNYS.is_session(day.isoformat()))


def _next_session(day: date) -> date:
    return XNYS.next_session(day.isoformat()).date()


def _previous_session_date(day: date) -> date:
    return XNYS.previous_session(day.isoformat()).date()


def _snapshot_session(now: datetime) -> date:
    """取已经收盘的最近交易日，跨午夜延迟仍归属上一交易日。"""
    if now.time() < time(16, 0) or not _is_session(now.date()):
        return _previous_session_date(now.date())
    return now.date()


def _eod_anchor(day: date) -> datetime:
    return datetime.combine(day, time(16, 0), tzinfo=ET)


def _write_raw(path: Path, df: pd.DataFrame) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    df.to_csv(tmp, index=False)
    tmp.replace(path)


def _write_snapshot(path: Path, snapshot: dict) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(
        json.dumps(
            snapshot,
            ensure_ascii=False,
            allow_nan=False,
            default=str,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def build_snapshot(symbols: list[str] | None = None, use_cache: bool = True) -> dict:
    """生成收盘快照；OI 在次日回补前明确标为未定稿。"""
    from src.fetchers.yfinance_fetcher import ensure_yf_proxy

    ensure_yf_proxy()
    import yfinance as yf  # noqa: E402

    symbols = symbols or SYMBOLS
    now = _now_et()
    today = _snapshot_session(now)
    snapshot_date = today.isoformat()
    oi_date = _previous_session_date(today).isoformat()
    as_of = _eod_anchor(today)
    if today < now.date() and not use_cache:
        raise RuntimeError("历史收盘快照不能用 --no-cache 重建")
    existing_path = DATA / f"{today:%Y%m%d}.json"
    existing_symbols = (
        json.loads(existing_path.read_text(encoding="utf-8")).get("symbols", {})
        if existing_path.exists()
        else {}
    )
    bc_iv_map = _fetch_barchart_iv_ranks(symbols)
    out: dict = {
        "date": _utc_now_iso(),
        "snapshot_date": snapshot_date,
        "oi_date": oi_date,
        "oi_final": False,
        "run_symbols": symbols,
        "symbols": {},
        "errors": {},
        "failed_symbols": [],
    }
    for sym in symbols:
        try:
            ticker = yf.Ticker(sym)
            raw_path = DATA / f"raw_{sym}_{today:%Y%m%d}.csv"
            if today < now.date():
                if not raw_path.exists():
                    raise RuntimeError("历史收盘快照缺 raw，拒绝用盘中链重建")
                exps = sorted(
                    pd.read_csv(raw_path, usecols=["expiration"], dtype={"expiration": str})[
                        "expiration"
                    ]
                    .unique()
                    .tolist()
                )
            else:

                def _dte(e: str) -> int:
                    return (datetime.strptime(e, "%Y-%m-%d").date() - today).days

                # 近 35D 全收，后续每 45D 取一个代表，覆盖四个到期桶。
                exps = [e for e in ticker.options if _dte(e) <= 35]
                for dte_min in (36, 90, 180, 270):
                    pick = next((e for e in ticker.options if _dte(e) >= dte_min), None)
                    if pick and pick not in exps:
                        exps.append(pick)
                exps = [e.replace("-", "") for e in exps]
            existing_spot = existing_symbols.get(sym, {}).get("spot")
            if existing_spot is not None:
                spot = float(existing_spot)
            else:
                history = ticker.history(
                    start=today.isoformat(),
                    end=(today + timedelta(days=1)).isoformat(),
                    interval="1d",
                    auto_adjust=False,
                )
                closes = history["Close"].dropna()
                if closes.empty:
                    raise RuntimeError(f"缺少 {snapshot_date} 日线收盘价")
                spot = float(closes.iloc[-1])
            if not exps or spot <= 0:
                raise RuntimeError("无期权到期日或现货价")
            chain = fetch_chain(sym, exps, use_cache=use_cache, oi_date=oi_date, chain_date=today)
            detail = compute_structure(chain, spot, as_of=as_of)
            if detail.get("spot") is None:
                raise RuntimeError("结构计算为空")
            detail["symbol"] = sym
            detail["name"] = sym
            detail["snapshot_date"] = snapshot_date
            detail["quote_ts"] = (
                str(chain["quote_ts"].dropna().max())
                if "quote_ts" in chain.columns and not chain["quote_ts"].dropna().empty
                else None
            )
            chain_final = (
                "oi_final" in chain.columns
                and bool(chain["oi_final"].astype(str).str.lower().eq("true").all())
                and "oi_date" in chain.columns
                and bool(chain["oi_date"].eq(snapshot_date).all())
            )
            detail["oi_date"] = snapshot_date if chain_final else oi_date
            detail["oi_final"] = chain_final
            if chain_final and "oi_quote_ts" in chain.columns:
                detail["oi_quote_ts"] = str(chain["oi_quote_ts"].dropna().max())
            if chain_final and "oi_coverage" in chain.columns:
                detail["oi_coverage"] = float(chain["oi_coverage"].iloc[0])
                detail["oi_excluded_expired"] = int(chain["oi_excluded_expired"].iloc[0])
            detail.update(_extract_events(ticker))
            if sym in bc_iv_map:
                detail.update(bc_iv_map[sym])
            out["symbols"][sym] = detail
            logger.info(f"  {sym}: spot={detail['spot']} netGEX={detail['net_gex']}B")
        except Exception as e:
            out["errors"][sym] = f"{type(e).__name__}: {e}"
            logger.warning(f"  {sym}: {e}")
    out["failed_symbols"] = sorted(out["errors"])
    if out["symbols"] and all(d.get("oi_final") is True for d in out["symbols"].values()):
        out["oi_final"] = True
        out["oi_date"] = snapshot_date
        out["oi_quote_ts"] = max(
            (d["oi_quote_ts"] for d in out["symbols"].values() if d.get("oi_quote_ts")),
            default=None,
        )
    return out


def finalize_oi_snapshot(snapshot_key: str | None = None) -> dict | None:
    """次日只回补收盘快照的 OI；默认选择最早一份待回补快照。"""
    now = _now_et()
    if snapshot_key is not None:
        path = DATA / f"{snapshot_key}.json"
    else:
        path = None
        for candidate in sorted(DATA.glob("20*.json")):
            data = json.loads(candidate.read_text(encoding="utf-8"))
            candidate_date = (
                data.get("snapshot_date")
                or datetime.strptime(candidate.stem, "%Y%m%d").date().isoformat()
            )
            if (
                data.get("oi_final") is False
                and datetime.strptime(candidate_date, "%Y-%m-%d").date() < now.date()
            ):
                path = candidate
                break
        if path is None:
            return None
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    if snapshot.get("oi_final") is None:
        return None
    snapshot_date = (
        snapshot.get("snapshot_date") or datetime.strptime(path.stem, "%Y%m%d").date().isoformat()
    )
    day = datetime.strptime(snapshot_date, "%Y-%m-%d").date()
    if now.date() <= day:
        return None
    if not _is_session(now.date()):
        return None
    next_session = _next_session(day)
    if now.date() < next_session or now.astimezone(timezone.utc).time() < time(14, 52):
        return None
    if snapshot.get("oi_final") is True:
        raw_ready = True
        for sym in snapshot.get("symbols", {}):
            raw_path = DATA / f"raw_{sym}_{day:%Y%m%d}.csv"
            if not raw_path.exists():
                # final 历史快照的 raw 按保留窗口清理后无需回补。
                return None
            meta = pd.read_csv(raw_path, nrows=1)
            if (
                "oi_final" not in meta.columns
                or "oi_date" not in meta.columns
                or str(meta["oi_final"].iloc[0]).lower() != "true"
                or str(meta["oi_date"].iloc[0]) != snapshot_date
            ):
                raw_ready = False
                break
        if raw_ready:
            return None
        if now.date() > next_session:
            raise RuntimeError(f"{path.stem} 的 final raw 与快照不一致，且已错过回补窗口")
    elif now.date() > next_session:
        raise RuntimeError(
            f"{path.stem} 已错过 {next_session} 的 OI 回补窗口；当前源只提供后续交易日 OI"
        )

    finalized_at = _utc_now_iso()
    as_of = _eod_anchor(day)
    updated = json.loads(json.dumps(snapshot))
    raw_updates: dict[Path, pd.DataFrame] = {}
    for sym, old_detail in snapshot.get("symbols", {}).items():
        raw_path = DATA / f"raw_{sym}_{day:%Y%m%d}.csv"
        if not raw_path.exists():
            raise RuntimeError(f"OI 回补不完整：{sym} 缺 raw 链 {raw_path.name}")
        raw = pd.read_csv(raw_path, dtype={"expiration": str})
        if raw.empty:
            raise RuntimeError(f"OI 回补不完整：{sym} raw 链为空")
        already_final = (
            "oi_final" in raw.columns
            and bool(raw["oi_final"].astype(str).str.lower().eq("true").all())
            and "oi_date" in raw.columns
            and bool(raw["oi_date"].eq(snapshot_date).all())
        )
        previous_excluded = int(old_detail.get("oi_excluded_expired") or 0)
        if already_final:
            final_raw = raw.copy()
            excluded = previous_excluded or (
                int(final_raw["oi_excluded_expired"].iloc[0])
                if "oi_excluded_expired" in final_raw.columns
                else 0
            )
        else:
            # 只有快照锚点时已到期的合约可以排除；之后到期的合约仍需回补，
            # 源端已移除时任务失败并保留旧快照。
            eligible_mask = raw["expiration"] > f"{day:%Y%m%d}"
            excluded = int((~eligible_mask).sum()) + previous_excluded
            eligible = raw.loc[eligible_mask].copy()
            expirations = sorted(eligible["expiration"].unique().tolist())
            if not expirations:
                raise RuntimeError(f"OI 回补不完整：{sym} 没有可回补的未到期合约")
            current = fetch_yf_chain(sym, expirations)
            oi_coverage = (
                float(
                    (
                        pd.to_numeric(current.get("openInterest"), errors="coerce").fillna(0) > 0
                    ).mean()
                )
                if not current.empty
                else 0.0
            )
            if oi_coverage < 0.5:
                raise RuntimeError(f"OI 回补不完整：{sym} OI 正值覆盖率仅 {oi_coverage:.0%}")
            actual_exps = set(current.get("expiration", pd.Series(dtype=str)).astype(str))
            missing_exps = sorted(set(expirations) - actual_exps)
            if missing_exps:
                raise RuntimeError(f"OI 回补不完整：{sym} 缺到期日 {','.join(missing_exps)}")
            keys = ["expiration", "strike", "right"]
            current_oi = current[keys + ["openInterest"]]
            merged = eligible.merge(
                current_oi,
                on=keys,
                how="left",
                suffixes=("", "_new"),
                validate="one_to_one",
                indicator=True,
            )
            matched = int((merged["_merge"] == "both").sum())
            if matched != len(eligible):
                raise RuntimeError(f"OI 回补不完整：{sym} 合约匹配 {matched}/{len(eligible)}")
            merged["openInterest"] = merged["openInterest_new"]
            final_raw = merged.drop(columns=["openInterest_new", "_merge"])
        if final_raw.empty:
            raise RuntimeError(f"OI 回补不完整：{sym} 回补后为空")
        final_exps = sorted(final_raw["expiration"].unique().tolist())
        healthy, why = chain_health(final_raw, expected_expirations=final_exps)
        if not healthy:
            raise RuntimeError(f"OI 回补不完整：{sym} {why}")
        total = len(final_raw) + excluded
        coverage = round(len(final_raw) / total * 100, 2) if total else 0.0
        symbol_oi_quote_ts = (
            str(final_raw["oi_quote_ts"].dropna().max())
            if already_final and "oi_quote_ts" in final_raw.columns
            else finalized_at
        )
        final_raw["oi_date"] = snapshot_date
        final_raw["oi_final"] = True
        final_raw["oi_quote_ts"] = symbol_oi_quote_ts
        final_raw["oi_excluded_expired"] = excluded
        final_raw["oi_coverage"] = coverage
        detail = compute_structure(final_raw, float(old_detail["spot"]), as_of=as_of)
        if detail.get("spot") is None:
            raise RuntimeError(f"OI 回补不完整：{sym} 结构重算为空")
        updated["symbols"][sym] = {
            **old_detail,
            **detail,
            "symbol": sym,
            "snapshot_date": snapshot_date,
            "oi_date": snapshot_date,
            "oi_final": True,
            "oi_quote_ts": symbol_oi_quote_ts,
            "oi_coverage": coverage,
            "oi_excluded_expired": excluded,
        }
        raw_updates[raw_path] = final_raw

    if not updated.get("symbols"):
        raise RuntimeError("OI 回补不完整：快照没有标的")
    updated["oi_date"] = snapshot_date
    updated["oi_final"] = True
    updated["oi_quote_ts"] = max(detail["oi_quote_ts"] for detail in updated["symbols"].values())
    updated["finalized_at"] = finalized_at
    updated["errors"] = {}
    updated["failed_symbols"] = []
    for raw_path, final_raw in raw_updates.items():
        _write_raw(raw_path, final_raw)
    _write_snapshot(path, updated)
    logger.info(f"OI 回补完成 → {path}（{len(updated['symbols'])} 标的）")
    return updated


def finalize_pending_oi() -> tuple[int, dict[str, str]]:
    """处理全部历史待回补快照；单份失败不阻断其他日期。"""
    processed = 0
    errors: dict[str, str] = {}
    for path in sorted(DATA.glob("20*.json")):
        try:
            result = finalize_oi_snapshot(path.stem)
        except Exception as e:
            errors[path.stem] = f"{type(e).__name__}: {e}"
            logger.error(f"{path.stem} OI 回补失败：{e}")
            continue
        processed += int(result is not None)
    return processed, errors


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="期权结构快照（yfinance 降级源）")
    parser.add_argument(
        "--symbols",
        default="",
        help=f"逗号分隔标的列表（默认全部 {len(SYMBOLS)} 标的）",
    )
    parser.add_argument("--no-cache", action="store_true", help="不复用当日 raw 链缓存")
    parser.add_argument("--finalize-oi", action="store_true", help="次日回补上一收盘快照 OI")
    args = parser.parse_args()
    if args.finalize_oi:
        processed, errors = finalize_pending_oi()
        print(f"OI_FINALIZED={processed}")
        if errors:
            logger.error(f"OI 回补失败快照：{errors}")
            raise SystemExit(1)
        return
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] or None
    now = _now_et()
    if now.weekday() >= 5 or (now.time() >= time(16, 0) and not _is_session(now.date())):
        logger.info("非美股交易日，不生成收盘期权快照")
        return

    today = now.strftime("%Y%m%d")
    for f in DATA.glob("20*.json"):
        if len(f.stem) == 8 and f.stem.isdigit() and f.stem > today:
            f.unlink()

    snapshot = build_snapshot(symbols, use_cache=not args.no_cache)
    requested = set(symbols or SYMBOLS)
    missing = requested - set(snapshot["symbols"]) - set(snapshot.get("errors", {}))
    for sym in sorted(missing):
        snapshot.setdefault("errors", {})[sym] = "无计算结果"
    errors = snapshot.get("errors", {})
    snapshot["failed_symbols"] = sorted(errors)
    if not snapshot["symbols"] or (errors and symbols is None):
        logger.error(f"期权快照未写入：{errors or '全部标的无结果'}")
        raise SystemExit(1)

    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / f"{snapshot['snapshot_date'].replace('-', '')}.json"
    if path.exists() and symbols:
        existing = json.loads(path.read_text(encoding="utf-8"))
        existing["symbols"].update(snapshot["symbols"])
        for key, value in snapshot.items():
            if key != "symbols":
                existing[key] = value
        snapshot = existing
        snapshot["oi_final"] = bool(snapshot["symbols"]) and all(
            detail.get("oi_final") is True for detail in snapshot["symbols"].values()
        )
        if snapshot["oi_final"]:
            snapshot["oi_date"] = snapshot["snapshot_date"]
    _write_snapshot(path, snapshot)
    logger.info(f"快照 → {path}（{len(snapshot['symbols'])} 标的）")

    files = list(DATA.glob("raw_*.csv"))
    stamps = sorted(
        {
            stamp
            for f in files
            for stamp in [f.stem.rsplit("_", 1)[-1]]
            if len(stamp) == 8 and stamp.isdigit() and stamp <= today
        }
    )
    keep = set(stamps[-RAW_KEEP_DAYS:])
    for snapshot_path in DATA.glob("20*.json"):
        data = json.loads(snapshot_path.read_text(encoding="utf-8"))
        if data.get("oi_final") is False:
            keep.add(snapshot_path.stem)
    for f in files:
        stamp = f.stem.rsplit("_", 1)[-1]
        if len(stamp) == 8 and stamp.isdigit() and (stamp not in keep or stamp > today):
            f.unlink()
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    main()
