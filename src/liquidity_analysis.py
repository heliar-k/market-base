"""
流动性专题分析引擎（对齐 timsun.net/liquidity：主页面评估 + 7 子页数据）。

规则引擎生成：LPI 三分层评分（结构 45% / 融资确认 35% / 风险传导 20%）、
主页面流动性评估叙事（净流动性 / 展望 / 准备金）、各子页摘要数据。
LLM 预留：generate_dashboard() 统一入口，_llm_generate_dashboard() 接好 LLM
后返回同构 dict（参考 rates_analysis / volatility_dashboard 模式），未接入时
自动回落规则引擎。

数据层只读现有 CSV（FRED liquidity/rates/credit、Treasury DTS/拍卖、CFETS
掉期点、SRF、CBOE 波动率、yfinance 资产快照、OFR FSI），单位输出统一：
金额 = 十亿美元（B）／利率 = % ／利差 = bp ／掉期点 = pips。
LPI 评分规则为研究型规则（透明可复盘，未做完整历史统计校准）——
分值定义：0-3 宽松 / 3-5 中性 / 5-7 警戒观察 / 7+ 压力确认。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

# ─────────────────────────────────────────────────────────────────────────────
# 数据加载（只读 CSV，不写盘）
# ─────────────────────────────────────────────────────────────────────────────


def _read(path: str, **kw) -> pd.DataFrame:
    f = ROOT / "data" / path
    if not f.exists():
        return pd.DataFrame()
    return pd.read_csv(f, **kw)


def _csv(path: str, **kw) -> pd.DataFrame:
    """宽松读法：默认 date 索引；失败返回空帧。"""
    try:
        return _read(path, index_col="date", parse_dates=True, **kw)
    except Exception:
        return pd.DataFrame()


def _liq_daily() -> pd.DataFrame:
    """日度流动性帧：周度列（WALCL/TREAST/WSHOMCB/WRESBAL/TGA）ffill 到日度，
    日度列（RRP）保留原值；NET_LIQUIDITY = WALCL − RRP×1000 − TGA（百万美元）。
    RRP 列保持十亿美元原单位（与宏观模块口径一致）。
    """
    liq = _csv("fred/liquidity/liquidity.csv")
    if liq.empty:
        return pd.DataFrame()
    out = pd.DataFrame(index=liq.index)
    for col in ("WALCL", "WTREGEN", "WRESBAL", "TREAST", "WSHOMCB", "SWPT"):
        if col in liq.columns:
            out[col] = liq[col].ffill()
    if "RRPONTSYD" in liq.columns:
        out["RRPONTSYD"] = liq["RRPONTSYD"]
    if "NFCI" in liq.columns:
        out["NFCI"] = liq["NFCI"]
    if {"WALCL", "RRPONTSYD", "WTREGEN"}.issubset(out.columns):
        out["NET_LIQUIDITY"] = out["WALCL"] - out["RRPONTSYD"] * 1000 - out["WTREGEN"]
    return out


def _last(s: pd.Series, n: int | None = None) -> float | list[float] | None:
    v = s.dropna()
    if n is None:
        return float(v.iloc[-1]) if len(v) else None
    return v.iloc[-n:].tolist()


def _chg(s: pd.Series, days: int) -> float | None:
    """最新值 vs days 天前（日期回溯最近值）的差；无数据返回 None。"""
    v = s.dropna()
    if len(v) < 2:
        return None
    cutoff = v.index[-1] - pd.Timedelta(days=days)
    past = v.loc[:cutoff]
    if past.empty:
        return None
    return float(v.iloc[-1] - past.iloc[-1])


def _pct_chg(s: pd.Series, days: int) -> float | None:
    d = _chg(s, days)
    v = s.dropna()
    if d is None or len(v) < 2:
        return None
    cutoff = v.index[-1] - pd.Timedelta(days=days)
    past = v.loc[:cutoff]
    if past.empty or past.iloc[-1] == 0:
        return None
    return d / abs(float(past.iloc[-1])) * 100


def _bp(s: str, t: str, rates: pd.DataFrame) -> float | None:
    """利率列差×100（bp），取两列最新共同有效日。"""
    s_ = pd.to_numeric(rates.get(s), errors="coerce")
    t_ = pd.to_numeric(rates.get(t), errors="coerce")
    both = pd.concat([s_, t_], axis=1).dropna()
    if both.empty:
        return None
    return round(float(both.iloc[-1, 0] - both.iloc[-1, 1]) * 100, 1)


def _score(v: float | None, bands: list[tuple[float, int]]) -> float | None:
    """分段打分：从上到下找第一个 v >= 阈值 的档位（v 越大分越高）。"""
    if v is None:
        return None
    for th, sc in bands:
        if v >= th:
            return float(sc)
    return float(bands[-1][1])


def _weighted(parts: list[tuple[float | None, float]]) -> float | None:
    vals = [p for p in parts if p[0] is not None]
    if not vals:
        return None
    w = sum(p[1] for p in vals)
    return round(sum(p[0] * p[1] for p in vals) / w, 1)


def _fmt_b(v: float | None, digits: int = 1) -> str | None:
    return None if v is None else f"{v / 1000:,.{digits}f}"


# ─────────────────────────────────────────────────────────────────────────────
# 主页面快照
# ─────────────────────────────────────────────────────────────────────────────


def liquidity_snapshot() -> dict:
    """主页面卡片 + 评估叙事（net_liquidity / outlook / reserves 三段）。"""
    liq = _liq_daily()
    rates = _csv("fred/rates/rates.csv")
    if liq.empty:
        return {"cards": {}, "evaluation": {}, "data_date": None}

    cards = {}
    for key, label, fmt in [
        ("WALCL", "美联储总资产", "万亿"),
        ("RRPONTSYD", "RRP 余额", None),
        ("WTREGEN", "TGA 余额", None),
        ("NET_LIQUIDITY", "净流动性", None),
    ]:
        s = liq.get(key)
        if s is None:
            continue
        cards[key] = {
            "label": label,
            "value": None if s.dropna().empty else float(s.dropna().iloc[-1]),
            "change_1m": _pct_chg(s, 30),
            "change_1y": _pct_chg(s, 365),
        }

    nav = _narrative(liq, rates)
    return {
        "cards": cards,
        "evaluation": nav,
        "data_date": _data_date(liq),
    }


def _data_date(liq: pd.DataFrame) -> str | None:
    latest = max(
        (
            s.dropna().index[-1]
            for s in (liq[c] for c in liq.columns)
            if not s.dropna().empty
        ),
        default=None,
    )
    return latest.date().isoformat() if latest is not None else None


def _narrative(liq: pd.DataFrame, rates: pd.DataFrame) -> dict:
    """三段规则叙事：净流动性 / 展望 / 准备金，各带验证指标。"""
    nl = (
        liq["NET_LIQUIDITY"].dropna()
        if "NET_LIQUIDITY" in liq
        else pd.Series(dtype=float)
    )
    rrp = liq["RRPONTSYD"].dropna() if "RRPONTSYD" in liq else pd.Series(dtype=float)
    tga = liq["WTREGEN"].dropna() if "WTREGEN" in liq else pd.Series(dtype=float)
    walcl = liq["WALCL"].dropna() if "WALCL" in liq else pd.Series(dtype=float)
    res = liq["WRESBAL"].dropna() if "WRESBAL" in liq else pd.Series(dtype=float)
    nfci = liq["NFCI"].dropna() if "NFCI" in liq else pd.Series(dtype=float)
    srf = _csv("fred/liquidity/srf.csv")
    srf_usage = srf["SRF_USAGE"].dropna() if not srf.empty else pd.Series(dtype=float)
    asset = _csv("yfinance/asset_prices.csv")

    def _txt(v: float) -> str:
        return f"{v / 1e6:,.1f}"

    # 净流动性：方向判断 + 验证指标
    nl_latest = _last(nl)
    nl_1m = _chg(nl, 30) if not nl.empty else None
    tga_latest = _last(tga)
    rrp_latest = _last(rrp)
    tga_1m = _chg(tga, 30) if not tga.empty else None
    if nl_1m is None:
        nl_dir = "方向未知"
    elif nl_1m >= 0:
        nl_dir = "净流动性处于扩张方向"
    else:
        nl_dir = "净流动性处于收缩方向"
    nl_text = (
        f"截至{nl.index[-1].date()}，联储总资产{_txt(_last(walcl))}万亿，"
        f"TGA为{_txt(tga_latest)}万亿，RRP仅{rrp_latest:.1f}十亿，"
        f"净流动性为{_txt(nl_latest)}万亿。"
        f"TGA 1个月{'上升' if tga_1m is not None and tga_1m > 2e4 else '回落/平稳'}"
        f"{'（财政存款在抽水）' if tga_1m is not None and tga_1m > 2e4 else ''}，"
        f"RRP 缓冲{'接近耗尽' if (rrp_latest or 0) < 25 else '仍有余量'}，"
        f"净流动性1个月变化{nl_1m / 1000:+.0f}十亿，{nl_dir}而非扩张。"
        if nl_1m is not None
        else f"截至{nl.index[-1].date()}，净流动性数据不足。"
    )
    nl_verify = (
        "验证指标：TGA 若升至 1.0 万亿以上或净流动性跌破 5.3 万亿，"
        "则财政抽水加速；RRP 持续低于 250 十亿则市场冗余现金基本归零。"
    )

    # 展望：能源 + VIX + TGA 抽水节奏
    wti = (
        asset["WTI"].dropna()
        if not asset.empty and "WTI" in asset
        else pd.Series(dtype=float)
    )
    vix = _csv("cboe/volatility.csv")
    vix_s = (
        vix["VIX"].dropna()
        if not vix.empty and "VIX" in vix
        else pd.Series(dtype=float)
    )
    dgs1mo = rates.get("DGS1MO")
    dgs1mo_s = (
        pd.to_numeric(dgs1mo, errors="coerce").dropna()
        if dgs1mo is not None
        else pd.Series(dtype=float)
    )
    iorb = rates.get("IORB")
    iorb_s = (
        pd.to_numeric(iorb, errors="coerce").dropna()
        if iorb is not None
        else pd.Series(dtype=float)
    )
    wti_latest, wti_5d = _last(wti), _pct_chg(wti, 5)
    vix_latest = _last(vix_s)
    outlook = []
    if wti_latest is not None:
        geo = "油价已含地缘风险溢价" if wti_latest > 80 else "油价处于低位"
        wti_sign = "+" if (wti_5d or 0) >= 0 else "-"
        wti_txt = f"{wti_sign}{abs(wti_5d or 0):.1f}%"
        outlook.append(f"WTI 最新收于 {wti_latest:.2f}（5日 {wti_txt}），{geo}。")
    if vix_latest is not None:
        risk = (
            "风险偏好正常"
            if vix_latest < 18
            else "风险偏好开始逆转变弱"
            if vix_latest < 25
            else "风险偏好显著恶化"
        )
        outlook.append(f"VIX {vix_latest:.2f}，{risk}。")
    if tga_1m is not None and tga_1m > 0:
        outlook.append(f"TGA 高位（{_txt(tga_latest)} 万亿），国债发行持续抽水。")
    outlook_text = " ".join(outlook) if outlook else "数据不足，展望待更新。"
    vix_verify = (
        f"验证指标：WTI 站上 90 或 VIX 升破 20 则风险偏好正式逆转；"
        f"RRP 或 1M 国债收益率（当前 {_last(dgs1mo_s):.2f}%）"
        f"与 IORB（{_last(iorb_s):.2f}%）利差走扩则融资压力开始显现。"
    )

    # 准备金：水平 + 4周变化 + 触发条件
    res_latest, res_4w = _last(res), _pct_chg(res, 28)
    sofr_iorb = _bp("SOFR", "IORB", rates)
    nfci_latest = _last(nfci)
    srf_latest = _last(srf_usage)
    msg = []
    if res_latest is not None:
        msg.append(f"准备金 {res_latest / 1e6:.2f} 万亿，4周变化 {res_4w:+.1f}%。")
    if rrp_latest is not None:
        msg.append(
            f"RRP 仅 {rrp_latest:.1f} 十亿（市场冗余现金基本耗尽）"
            if rrp_latest < 25
            else f"RRP {rrp_latest:.0f} 十亿。"
        )
    if nfci_latest is not None:
        msg.append(
            f"NFCI {nfci_latest:.2f}"
            + ("（宽松）" if nfci_latest < 0 else "（收紧）")
            + "。"
        )
    if sofr_iorb is not None:
        msg.append(
            f"SOFR−IORB {sofr_iorb:+.1f}bp"
            + ("（充裕）" if sofr_iorb < 0 else "（偏紧）")
            + "。"
        )
    if srf_latest is not None:
        msg.append(
            f"SRF 使用 {srf_latest * 1000:.0f} 百万。"
            if srf_latest > 0
            else "SRF 未使用。"
        )
    res_text = " ".join(msg) if msg else "准备金数据不足。"
    res_verify = (
        f"触发条件：NFCI 若由 {nfci_latest:.3f} 升穿 0，或 RRP 连续三个交易日贴零"
        f"且 1M 国债收益率升破 IORB+20bp，则确认准备金进入稀缺状态。"
    )

    return {
        "net_liquidity": {"title": "净流动性", "text": nl_text + nl_verify},
        "outlook": {"title": "展望", "text": outlook_text + " " + vix_verify},
        "reserves": {"title": "准备金", "text": res_text + " " + res_verify},
        "generator": "rules",
    }


# ─────────────────────────────────────────────────────────────────────────────
# LPI 压力指数（transmission-chain）
# ─────────────────────────────────────────────────────────────────────────────


def lpi() -> dict:
    """规则型美元流动性压力指数：三层加权 + 离岸/外部输入 + 确认条件。"""
    liq = _liq_daily()
    rates = _csv("fred/rates/rates.csv")
    credit = _csv("fred/credit/credit.csv")
    vix = _csv("cboe/volatility.csv")
    srf = _csv("fred/liquidity/srf.csv")
    cfets = _csv("fred/liquidity/cfets_swap_points.csv")
    asset = _csv("yfinance/asset_prices.csv")
    if liq.empty:
        return {}

    res_s = liq["WRESBAL"].dropna() if "WRESBAL" in liq else pd.Series(dtype=float)
    nl_s = (
        liq["NET_LIQUIDITY"].dropna()
        if "NET_LIQUIDITY" in liq
        else pd.Series(dtype=float)
    )
    rrp_s = liq["RRPONTSYD"].dropna() if "RRPONTSYD" in liq else pd.Series(dtype=float)
    nfci_s = liq["NFCI"].dropna() if "NFCI" in liq else pd.Series(dtype=float)

    # ── 结构性缓冲（45%）：准备金4周变化 / 净流动性20日脉冲 / RRP 缓冲 ──
    res_4w = _pct_chg(res_s, 28)
    # 分档：≥0 正常 / -1~0 略降 / -6~-1 缓冲变薄 / ≤-6% 显著收缩
    # （-4.14% 落在“变薄”档 7，与参考页同口径；-6% 以下才计 9）
    s_res = _score(res_4w, [(0, 3), (-1, 5), (-6, 7)])
    nl_20d = _chg(nl_s, 20)  # 百万美元
    s_nl = _score(nl_20d, [(0, 4), (-150e6, 6), (-300e6, 8), (-500e6, 9)])
    rrp_latest = _last(rrp_s)
    s_rrp = _score(rrp_latest, [(500, 1), (200, 3), (100, 5), (20, 7), (0, 9)])

    # ── 融资市场确认（35%）：SOFR−IORB / SRF / SOFR99−IORB / 离岸 1M ──
    sofr_iorb = _bp("SOFR", "IORB", rates)
    s_sofr = _score(sofr_iorb, [(10, 10), (3, 8), (0, 6), (-2, 4), (-10, 2)])
    srf_usage = srf["SRF_USAGE"].dropna() if not srf.empty else pd.Series(dtype=float)
    srf_latest = _last(srf_usage)
    s_srf = _score(srf_latest, [(10, 8), (1, 5), (0.05, 2)])
    sofr99_iorb = _bp("SOFR99", "IORB", rates)
    s_tail = _score(sofr99_iorb, [(5, 8), (0, 5)])
    offshore = _offshore_score(cfets)
    s_off = _weighted([(offshore, 1.0)])
    s_fund = _weighted([(s_sofr, 0.4), (s_srf, 0.2), (s_tail, 0.25), (s_off, 0.15)])

    # ── 风险资产传导（20%）：VIX / MOVE / HY 利差 / NFCI ──
    vix_s = (
        vix["VIX"].dropna()
        if not vix.empty and "VIX" in vix
        else pd.Series(dtype=float)
    )
    vix_latest = _last(vix_s)
    s_vix = _score(vix_latest, [(35, 9), (25, 7), (18, 5), (14, 3)])
    move_s = (
        asset["MOVE"].dropna()
        if not asset.empty and "MOVE" in asset
        else pd.Series(dtype=float)
    )
    move_latest = _last(move_s)
    s_move = _score(move_latest, [(140, 8), (110, 6), (80, 4)])
    bb_oas = credit.get("BB_OAS") if not credit.empty else None
    bb_s = (
        pd.to_numeric(bb_oas, errors="coerce").dropna()
        if bb_oas is not None
        else pd.Series(dtype=float)
    )
    bb_latest = _last(bb_s)
    s_credit = _score(bb_latest, [(500, 8), (300, 6), (150, 4)])
    nfci_latest = _last(nfci_s)
    s_nfci = _score(nfci_latest, [(0.7, 9), (0.3, 7), (0, 5), (-0.3, 3)])
    s_risk = _weighted([(s_vix, 0.3), (s_move, 0.2), (s_credit, 0.3), (s_nfci, 0.2)])

    s_struc = (
        round(s_res * 0.4 + s_nl * 0.35 + s_rrp * 0.25, 1)
        if all(x is not None for x in (s_res, s_nl, s_rrp))
        else None
    )
    lpi_total = (
        round(0.45 * s_struc + 0.35 * s_fund + 0.20 * s_risk, 1)
        if all(x is not None for x in (s_struc, s_fund, s_risk))
        else None
    )

    conf = _confirmations(
        sofr_iorb, srf_latest, bb_s, nfci_latest, vix_latest, rrp_latest, rates
    )
    offshore_info = _offshore_detail(cfets)

    return {
        "score": lpi_total,
        "score_raw": lpi_total,
        "band": _band(lpi_total),
        "pctile_30d": lpi_percentile_30d(lpi_total),
        "history": _lpi_history(30),
        "layers": {
            "structure": {
                "score": s_struc,
                "weight": 0.45,
                "parts": [
                    {"name": "准备金4周变化", "value": res_4w, "score": s_res},
                    {"name": "净流动性20日脉冲", "value": nl_20d, "score": s_nl},
                    {"name": "RRP缓冲", "value": rrp_latest, "score": s_rrp},
                ],
            },
            "funding": {
                "score": s_fund,
                "weight": 0.35,
                "parts": [
                    {"name": "SOFR−IORB", "value": sofr_iorb, "score": s_sofr},
                    {"name": "SRF使用", "value": srf_latest, "score": s_srf},
                    {"name": "SOFR99−IORB", "value": sofr99_iorb, "score": s_tail},
                    {"name": "离岸1M basis", "value": offshore, "score": s_off},
                ],
            },
            "transmission": {
                "score": s_risk,
                "weight": 0.20,
                "parts": [
                    {"name": "VIX", "value": vix_latest, "score": s_vix},
                    {"name": "MOVE", "value": move_latest, "score": s_move},
                    {"name": "HY OAS(BB)", "value": bb_latest, "score": s_credit},
                    {"name": "NFCI", "value": nfci_latest, "score": s_nfci},
                ],
            },
        },
        "confirmations": conf,
        "offshore": offshore_info,
        "external": {
            "wti": _last(asset["WTI"]) if "WTI" in asset.columns else None,
            "adjust": 0.0,
            "note": "能源/地缘为外部冲击输入，不参与加权",
        },
        "evidence": _evidence(liq, rates, credit, vix, asset, srf),
        "data_date": _data_date(liq),
    }


LPI_HISTORY_PATH = ROOT / "data" / "liquidity" / "lpi_history.csv"


def save_lpi_snapshot() -> Path | None:
    """LPI 快照 → data/liquidity/lpi_history.csv（观测日 upsert，当日重跑覆盖）。

    每日运行积累 30 天+ 后即可做分位校准（参考页“过去 30 天第 87 分位”）。
    已由 daily-fetch workflow 自动触发；本地可手动运行 --snapshot。
    ponytail: 分析层写盘对齐 src/bill_share.py 先例（派生指标落盘非拉取），
    若未来派生落盘模块增多，再抽公共 tooling。
    """
    d = lpi()
    if not d or d.get("score") is None:
        print("lpi_snapshot: LPI 无数据，跳过")
        return None
    parts = {p["name"]: p for layer in d["layers"].values() for p in layer["parts"]}
    row = {
        "SCORE": d["score"],
        "STRUCTURE": d["layers"]["structure"]["score"],
        "FUNDING": d["layers"]["funding"]["score"],
        "TRANSMISSION": d["layers"]["transmission"]["score"],
        "RESERVES_4W_PCT": parts.get("准备金4周变化", {}).get("value"),
        "NL_20D_B": (parts.get("净流动性20日脉冲", {}).get("value") or 0) / 1000,
        "RRP_B": parts.get("RRP缓冲", {}).get("value"),
        "SOFR_IORB_BP": parts.get("SOFR−IORB", {}).get("value"),
        "SRF_B": parts.get("SRF使用", {}).get("value"),
        "SOFR99_IORB_BP": parts.get("SOFR99−IORB", {}).get("value"),
        "OFFSHORE_SCORE": parts.get("离岸1M basis", {}).get("score"),
        "VIX": parts.get("VIX", {}).get("value"),
        "MOVE": parts.get("MOVE", {}).get("value"),
        "BB_OAS_BP": parts.get("HY OAS(BB)", {}).get("value"),
        "NFCI": parts.get("NFCI", {}).get("value"),
    }
    df = pd.DataFrame([row], index=pd.DatetimeIndex([pd.Timestamp(date.today())]))
    from src.fetchers._io import upsert_timeseries

    upsert_timeseries(LPI_HISTORY_PATH, df, column_order=list(row))
    print(f"lpi_snapshot: LPI {d['score']}/10 → {LPI_HISTORY_PATH}")
    return LPI_HISTORY_PATH


def lpi_percentile_30d(score: float | None) -> float | None:
    """当前 LPI 在近 30 个快照中的分位（0-100）。历史不足 10 条返回 None。

    只读历史，不重算 lpi()（历史曾因内部重算造成互递归嵌套，审计后修复）。
    """
    if not LPI_HISTORY_PATH.exists():
        return None
    hist = pd.read_csv(LPI_HISTORY_PATH, index_col=0)
    s = pd.to_numeric(hist.get("SCORE"), errors="coerce").dropna().tail(30)
    if len(s) < 10 or score is None:
        return None
    return round((s <= score).mean() * 100)


def _lpi_history(n: int = 30) -> list[dict]:
    """近 n 条 LPI 快照（date/score），供 30 天历史走势图。"""
    if not LPI_HISTORY_PATH.exists():
        return []
    hist = pd.read_csv(LPI_HISTORY_PATH, index_col=0, parse_dates=True)
    s = pd.to_numeric(hist.get("SCORE"), errors="coerce").dropna().tail(n)
    return [{"date": d.date().isoformat(), "value": float(v)} for d, v in s.items()]


def _band(score: float | None) -> tuple[str, str]:
    if score is None:
        return "未知", "gray"
    if score < 3:
        return "宽松", "green"
    if score < 5:
        return "中性", "blue"
    if score < 7:
        return "警戒观察", "orange"
    return "压力确认", "red"


def _confirmations(
    sofr_iorb: float | None,
    srf_latest: float | None,
    bb_s: pd.Series,
    nfci_latest: float | None,
    vix_latest: float | None,
    rrp_latest: float | None,
    rates: pd.DataFrame,
) -> list[dict]:
    dgs1mo = (
        pd.to_numeric(rates.get("DGS1MO"), errors="coerce").dropna()
        if rates.get("DGS1MO") is not None
        else pd.Series(dtype=float)
    )
    iorb = (
        pd.to_numeric(rates.get("IORB"), errors="coerce").dropna()
        if rates.get("IORB") is not None
        else pd.Series(dtype=float)
    )
    bb_1m = _chg(bb_s, 30) if not bb_s.empty else None
    return [
        {
            "title": "SOFR−IORB 连续转正",
            "met": (sofr_iorb or 0) > 0,
            "detail": f"当前 {sofr_iorb:+.1f}bp"
            if sofr_iorb is not None
            else "数据缺失",
        },
        {
            "title": "SRF 出现数十亿美元级使用",
            "met": (srf_latest or 0) >= 1,
            "detail": f"当前 {srf_latest * 1000:.0f} 百万"
            if srf_latest is not None
            else "数据缺失",
        },
        {
            "title": "HY 利差明显走阔",
            "met": (bb_1m or 0) > 25,
            "detail": f"BB OAS 30日变化 {bb_1m:+.0f}bp"
            if bb_1m is not None
            else "数据缺失",
        },
        {
            "title": "NFCI 转正",
            "met": (nfci_latest or 0) > 0,
            "detail": f"当前 {nfci_latest:.2f}"
            if nfci_latest is not None
            else "数据缺失",
        },
        {
            "title": "VIX 升至 20 上方",
            "met": (vix_latest or 0) > 20,
            "detail": f"当前 {vix_latest:.2f}"
            if vix_latest is not None
            else "数据缺失",
        },
        {
            "title": "RRP 贴零且 1M 国债收益率升破 IORB+20bp",
            "met": (rrp_latest or 0) < 25
            and not dgs1mo.empty
            and not iorb.empty
            and float(dgs1mo.iloc[-1]) > float(iorb.iloc[-1]) + 0.20,
            "detail": f"RRP {rrp_latest:.2f}B, 1M T-Bill {_last(dgs1mo):.2f}% "
            f"/ IORB {_last(iorb):.2f}%",
        },
    ]


# 货币对→显示名唯一映射（offshore 评分/详情/页面共用，增删货币对只改这里）
_PAIR_DISPLAY = {
    "USDJPY": "USD/JPY",
    "USDCNH": "USD/CNH",
    "EURUSD": "EUR/USD",
    "GBPUSD": "GBP/USD",
    "USDCHF": "USD/CHF",
}


def _offshore_score(cfets: pd.DataFrame) -> float | None:
    """各货币对 1M 掉期点平均压力分：越负 → 美元利率溢价越高（偏紧）。"""
    scores: list[float] = []
    for pair in _PAIR_DISPLAY:
        col = f"{pair}_1M"
        if col not in cfets:
            continue
        v = float(cfets[col].dropna().iloc[-1])
        scores.append(_score(v, [(0, 3), (-30, 5), (-80, 7), (-150, 9)]))
    return round(sum(scores) / len(scores), 1) if scores else None


def _offshore_detail(cfets: pd.DataFrame) -> dict:
    out = {}
    for p in _PAIR_DISPLAY:
        row = {}
        for ten in ("1W", "1M", "3M", "6M", "1Y"):
            col = f"{p}_{ten}"
            if col in cfets and not cfets[col].dropna().empty:
                row[ten] = float(cfets[col].dropna().iloc[-1])
        out[p] = row
    return out


def _evidence(liq, rates, credit, vix, asset, srf) -> dict:
    return {
        "balancesheet": {
            "reserves_b": _fmt_b(_last(liq["WRESBAL"])),
            "net_liquidity_b": _fmt_b(_last(liq["NET_LIQUIDITY"])),
            "reserves_4w_pct": _pct_chg(liq["WRESBAL"], 28)
            if "WRESBAL" in liq
            else None,
            "dgs10": _last(pd.to_numeric(rates.get("DGS10"), errors="coerce"))
            if rates.get("DGS10") is not None
            else None,
        },
        "repo": {
            "sofr": _last(pd.to_numeric(rates.get("SOFR"), errors="coerce"))
            if rates.get("SOFR") is not None
            else None,
            "sofr_iorb_bp": _bp("SOFR", "IORB", rates),
            "rrp_b": _fmt_b(_last(liq["RRPONTSYD"])),
            "srf_m": _last(srf["SRF_USAGE"]) * 1000 if not srf.empty else None,
        },
        "energy": {
            "wti": _last(asset["WTI"])
            if not asset.empty and "WTI" in asset.columns
            else None,
            "wti_5d_pct": _pct_chg(asset["WTI"], 5)
            if not asset.empty and "WTI" in asset.columns
            else None,
            "ng": _last(asset["NG"])
            if not asset.empty and "NG" in asset.columns
            else None,
        },
        "intermediary": {
            "vix": _last(vix["VIX"]) if not vix.empty else None,
            "move": _last(asset["MOVE"]) if not asset.empty else None,
            "bb_oas": _last(pd.to_numeric(credit.get("BB_OAS"), errors="coerce"))
            if not credit.empty
            else None,
            "ig_oas": _last(pd.to_numeric(credit.get("BBB_OAS"), errors="coerce"))
            if not credit.empty
            else None,
            "nfci": _last(liq["NFCI"]),
        },
        "prices": {
            "spx": _last(asset["SPX"]) if not asset.empty else None,
            "dgs10": _last(pd.to_numeric(rates.get("DGS10"), errors="coerce"))
            if rates.get("DGS10") is not None
            else None,
            "dxy": _last(asset["DXY"]) if not asset.empty else None,
            "gold": _last(asset["Gold"]) if not asset.empty else None,
            "btc": _last(asset["BTC"]) if not asset.empty else None,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# 未来 14 天流动性热力（rrp-tga 子页）
# ─────────────────────────────────────────────────────────────────────────────


def _soma_week_net_b() -> float | None:
    """SOMA 周度净变化（十亿美元，负=抽水）：WALCL 周三观测序列最近 4 周周变化均值。

    覆盖 MBS 自然到期（paydown）与 RMP 购买抵冲后的净值——只看 TREAST 会把
    MBS 到期抽水漏掉（8-19 WALCL -14.3B，TREAST 却 +3.9B）。
    """
    liq = _liq_daily()
    tr = liq["WALCL"].dropna() if "WALCL" in liq else pd.Series(dtype=float)
    # ffill 日均为重复值 → 只取周三原始观测（H.4.1 发布日）做差，避免 0 污染
    wk = tr[tr.index.to_series().dt.weekday.eq(2)].diff().dropna().tail(4)
    if wk.empty:
        return None
    return round(float(wk.mean()) / 1000, 1)


def forward_calendar(days: int = 14) -> dict:
    """未来 days 天 TGA 净冲击估算：
    - 拍卖结算（确知）：upcoming_auctions 按 issue_date 归集 offering_amt（抽水 −）
    - SOMA 购买（估算）：TREAST 4 周周均变化，落在周四（结算日惯例，注入 +）
    返回 {days: [...], net_7d_b, net_14d_b, source_note}（net_7/14 = 未来窗口估算）。
    """
    up = _read("treasury/upcoming_auctions.csv", parse_dates=["issue_date"])
    auc = _read(
        "treasury/auction_results.csv", parse_dates=["maturity_date", "issue_date"]
    )
    today = pd.Timestamp(date.today())
    window = [today + pd.Timedelta(days=i) for i in range(1, days + 1)]

    # 净发行 = 当日新发结算（issue_date） − 当日到期（maturity_date，
    # 过去已发行证券的本金回笼）
    # 国库券每周四同日滚续，直接加总发行额会虚高（同窗口 08-27 新发 266B）；
    # 匹配当日到期后才是资金净流入/流出财政的真实幅度（参考口径）。
    settle: dict[pd.Timestamp, float] = {}
    if not up.empty and "issue_date" in up.columns:
        mask = (up["issue_date"] >= window[0]) & (up["issue_date"] <= window[-1])
        for _, row in up[mask].iterrows():
            d = pd.Timestamp(row["issue_date"])
            amt = float(row.get("offering_amt") or 0) / 1e9
            if amt > 0:
                settle[d] = settle.get(d, 0.0) - amt  # 十亿美元，抽水为负
    if not auc.empty and "maturity_date" in auc.columns:
        mat = (
            auc.dropna(subset=["maturity_date"])
            .groupby("maturity_date")["offering_amt"]
            .apply(lambda s: float(pd.to_numeric(s, errors="coerce").sum() or 0) / 1e9)
        )
        for d, amt in mat.items():
            d = pd.Timestamp(d)
            if window[0] <= d <= window[-1] and d in settle:
                settle[d] += amt  # 到期回笼（注入）抵消同日新发

    rmp_week = _soma_week_net_b()
    rows = []
    for d in window:
        flows: list[dict] = []
        net = 0.0
        if d in settle and settle[d] != 0:
            flows.append(
                {
                    "type": "auction_settlement",
                    "label": "拍卖结算",
                    "amount_b": settle[d],
                }
            )
            net += settle[d]
        if (
            d.weekday() == 3 and rmp_week is not None and rmp_week != 0
        ):  # 周四 = SOMA 周结算日
            flows.append(
                {"type": "soma_net", "label": "SOMA 净到期(估算)", "amount_b": rmp_week}
            )
            net += rmp_week
        bucket = (
            "injection"
            if net > 1
            else (
                "smooth"
                if net > -30
                else "mild"
                if net > -80
                else "strong"
                if net > -200
                else "extreme"
            )
        )
        rows.append(
            {
                "date": d.date().isoformat(),
                "weekday": "周" + "一二三四五六日"[d.weekday()],
                "flows": flows,
                "net_b": round(net, 1),
                "bucket": bucket,
            }
        )

    # 未来窗口估算（与逐日热力表同源）：7/14 日 = 前 7/14 行净冲击之和
    net_7 = round(sum(r["net_b"] for r in rows[:7]), 1) if rows else None
    net_14 = round(sum(r["net_b"] for r in rows[:14]), 1) if rows else None
    return {
        "days": rows,
        "net_7d_b": net_7,
        "net_14d_b": net_14,
        "source_note": "抽水 = 新债结算（财政部吸收现金）；SOMA 净到期 = WALCL"
        "最近 4 周周三观测周均（MBS 自然到期 − RMP 购买净值，周四结算惯例，估算）。",
    }


# ─────────────────────────────────────────────────────────────────────────────
# 各子页数据
# ─────────────────────────────────────────────────────────────────────────────


def _spread_series(
    rates: pd.DataFrame, a: str, b: str, scale: float = 100, n: int = 60
) -> list[dict]:
    """利率列差 × scale 的近 n 条序列 (date, value)；任一侧缺失返回空。"""
    if a not in rates.columns or b not in rates.columns:
        return []
    both = (
        pd.concat(
            [
                pd.to_numeric(rates[a], errors="coerce"),
                pd.to_numeric(rates[b], errors="coerce"),
            ],
            axis=1,
        )
        .dropna()
        .tail(n)
    )
    return [
        {
            "date": d.date().isoformat(),
            "value": round(float(r.iloc[0] - r.iloc[1]) * scale, 1),
        }
        for d, r in both.iterrows()
    ]


def page_fed_balance_sheet() -> dict:
    """子页：资产负债表 — 卡片 + 近 20 日对照表
    （Fed 总数/净流动性/SPX/NDX/国债/MBS）。"""
    # 金额为十亿美元（B）；SPX/NDX 取同日收盘
    liq = _liq_daily()
    asset = _csv("yfinance/asset_prices.csv")
    if liq.empty:
        return {}
    cards = {}
    for key, label in [
        ("WALCL", "美联储总资产"),
        ("TREAST", "国债持有"),
        ("WSHOMCB", "MBS持有"),
        ("NET_LIQUIDITY", "净流动性"),
    ]:
        s = liq.get(key)
        if s is not None and not s.dropna().empty:
            cards[key] = {
                "label": label,
                "value_b": round(float(s.dropna().iloc[-1]) / 1000, 3),
            }
    rows = []
    spx = (
        asset["SPX"].dropna()
        if not asset.empty and "SPX" in asset
        else pd.Series(dtype=float)
    )
    ndx = (
        asset["NDX"].dropna()
        if not asset.empty and "NDX" in asset
        else pd.Series(dtype=float)
    )
    for dt in spx.index[-20:]:
        r = {"date": dt.date().isoformat()}
        for col in ("NET_LIQUIDITY", "WALCL", "TREAST", "WSHOMCB"):
            if col in liq.columns:
                v = liq[col].get(dt)
                r[col] = round(float(v) / 1000, 3) if pd.notna(v) else None
        r["SPX"] = float(spx.get(dt)) if pd.notna(spx.get(dt)) else None
        r["NDX"] = float(ndx.get(dt)) if pd.notna(ndx.get(dt)) else None
        rows.append(r)
    return {
        "cards": cards,
        "rows": rows,
        "note": "Fed 账目为周度（H.4.1），非发布日为空；SPX/NDX 取该日收盘。",
    }


def page_operations() -> dict:
    """子页：公开市场操作 — RMP 统计 / 最近 40 条 / SOMA 摘要 / SRF 90 天。"""
    ops = _read("fred/liquidity/tsy_operations.csv", parse_dates=["settlement_date"])
    liq = _liq_daily()
    srf = _csv("fred/liquidity/srf.csv")
    out: dict = {"rmp": {}, "recent": [], "soma": {}, "srf": {}}
    if ops.empty:
        return out
    rmp = ops[ops["is_rmp"] == True].copy()  # noqa: E712
    if not rmp.empty:
        start = rmp["settlement_date"].min()
        months = max((pd.Timestamp(date.today()) - start).days / 30.4, 0.5)
        out["rmp"] = {
            "start": start.date().isoformat(),
            "cumulative_b": round(float(rmp["accepted_b"].sum()), 1),
            "monthly_b": round(float(rmp["accepted_b"].sum()) / months, 1),
            "count": int(len(rmp)),
            "target_b": 40.0,
        }
    recent = ops.sort_values("settlement_date", ascending=False).head(40)
    out["recent"] = [
        {
            "date": r["settlement_date"].date().isoformat()
            if pd.notna(r["settlement_date"])
            else None,
            "type": str(r.get("operation_type")),
            "maturity": f"{str(r.get('maturity_start'))[:10] or '—'} ~ "
            f"{str(r.get('maturity_end'))[:10] or '—'}",
            "submitted_b": round(float(r["submitted_b"]), 1),
            "accepted_b": round(float(r["accepted_b"]), 1),
            "accept_ratio": round(float(r["accept_ratio"]), 1)
            if pd.notna(r.get("accept_ratio"))
            else None,
        }
        for _, r in recent.iterrows()
    ]
    # RMP 日度序列（操作日 + 累计），图示用
    if not rmp.empty:
        daily = rmp.groupby("settlement_date")[["accepted_b"]].sum().sort_index()
        daily["cum_b"] = daily["accepted_b"].cumsum()
        out["rmp_series"] = [
            {
                "date": d.date().isoformat(),
                "accepted_b": round(float(r["accepted_b"]), 2),
                "cum_b": round(float(r["cum_b"]), 1),
            }
            for d, r in daily.iterrows()
        ]
    soma = {}
    for col, name in [("TREAST", "国债"), ("WSHOMCB", "MBS"), ("WALCL", "联储总资产")]:
        if col in liq.columns and not liq[col].dropna().empty:
            s = liq[col].dropna()
            w = _chg(s, 7) or 0
            m = _chg(s, 30) or 0
            soma[name] = {
                "holdings_b": round(float(s.iloc[-1]) / 1000, 1),
                "week_b": round(w / 1000, 2),
                "month_b": round(m / 1000, 2),
            }
    out["soma"] = soma
    if not srf.empty:
        s = srf["SRF_USAGE"].dropna().tail(90)
        out["srf"] = {
            "active_days_90": int(s.gt(0).sum()),
            "latest_m": round(float(s.iloc[-1]) * 1000, 0),
        }
    return out


def page_rrp_tga() -> dict:
    """子页：RRP & TGA — 压力预警 / 未来 14 天热力 / 当前余额 / 日度现金流。"""
    liq = _liq_daily()
    rates = _csv("fred/rates/rates.csv")
    dts = _csv("treasury/dts_cashflows.csv")
    dts_tga = _csv("treasury/dts_operating_cash.csv")
    out: dict = {"alert": {}, "calendar": {}, "current": {}, "cashflows": []}
    if liq.empty:
        return out
    sofr_iorb = _bp("SOFR", "IORB", rates)
    sofr = (
        _last(pd.to_numeric(rates.get("SOFR"), errors="coerce"))
        if rates.get("SOFR") is not None
        else None
    )
    iorb = (
        _last(pd.to_numeric(rates.get("IORB"), errors="coerce"))
        if rates.get("IORB") is not None
        else None
    )
    dff = (
        _last(pd.to_numeric(rates.get("DFEDTARU"), errors="coerce"))
        if rates.get("DFEDTARU") is not None
        else None
    )
    out["alert"] = {
        "sofr_iorb_bp": sofr_iorb,
        "state": "充裕"
        if (sofr_iorb or 0) < 0
        else "收紧启动"
        if (sofr_iorb or 0) < 3
        else "偏紧",
        "sofr": sofr,
        "iorb": iorb,
        "ffr_upper": dff,
    }
    out["calendar"] = forward_calendar(14)
    out["data_date"] = _data_date(dts_tga) if not dts_tga.empty else None
    srf = _csv("fred/liquidity/srf.csv")
    # TGA 优先 DTS 日度余额（更实时）；净流动性口径用 WTREGEN（与定义一致）
    tga_latest = None
    if not dts_tga.empty and not dts_tga["TGA_CLOSE"].dropna().empty:
        s = dts_tga["TGA_CLOSE"].dropna()
        tga_latest = round(float(s.iloc[-1]) / 1000, 1)
        tga_date = s.index[-1].date().isoformat()
    elif "WTREGEN" in liq.columns and not liq["WTREGEN"].dropna().empty:
        s = liq["WTREGEN"].dropna()
        tga_latest = round(float(s.iloc[-1]) / 1000, 1)
        tga_date = s.index[-1].date().isoformat()
    else:
        tga_date = None
    out["current"] = {
        "rrp_b": round(float(liq["RRPONTSYD"].dropna().iloc[-1]), 2)
        if "RRPONTSYD" in liq and not liq["RRPONTSYD"].dropna().empty
        else None,
        "tga_b": tga_latest,
        "tga_date": tga_date,
        "net_liq_b": round(float(liq["NET_LIQUIDITY"].dropna().iloc[-1]) / 1000, 1)
        if "NET_LIQUIDITY" in liq and not liq["NET_LIQUIDITY"].dropna().empty
        else None,
        "srf_m": round(float(srf["SRF_USAGE"].dropna().iloc[-1]) * 1000, 0)
        if not srf.empty and not srf["SRF_USAGE"].dropna().empty
        else None,
    }
    if not dts.empty:
        tail = dts.tail(60)
        out["cashflows"] = [
            {
                "date": d.date().isoformat(),
                "deposits_b": round(float(r["DEPOSITS"]) / 1000, 1),
                "withdrawals_b": round(float(r["WITHDRAWALS"]) / 1000, 1),
                "net_b": round(float(r["NET"]) / 1000, 1),
            }
            for d, r in tail.iterrows()
        ]
    # 60 日序列（SOFR−IORB 走势 + TGA 日度余额）
    series = {}
    s_i = _spread_series(rates, "SOFR", "IORB")
    if s_i:
        series["sofr_iorb_bp"] = s_i
    if "WTREGEN" in liq.columns and not liq["WTREGEN"].dropna().empty:
        t = liq["WTREGEN"].dropna().tail(60)
        series["tga_b"] = [
            {"date": d.date().isoformat(), "value": round(float(v) / 1000, 1)}
            for d, v in t.items()
        ]
    out["series"] = series
    return out


def page_reserves() -> dict:
    """子页：准备金 — 卡片 + 银行中介意愿（SOFR−3M T-Bill / SOFR−IORB 60 日）。"""
    liq = _liq_daily()
    rates = _csv("fred/rates/rates.csv")
    out: dict = {"cards": {}, "spreads": {}, "rows": []}
    if liq.empty:
        return out
    res = liq["WRESBAL"].dropna() if "WRESBAL" in liq else pd.Series(dtype=float)
    if not res.empty:
        out["cards"] = {
            "reserves": {
                "value_b": round(float(res.iloc[-1]) / 1000, 2),
                "date": res.index[-1].date().isoformat(),
                "chg_4w_pct": _pct_chg(res, 28),
            },
            "intermediation": {
                "state": "正常"
                if (sofr := _bp("SOFR", "DGS3MO", rates)) is not None and sofr < 15
                else "偏紧",
                "note": "SOFR−3M T-Bill 走扩表明担保融资相对无风险资产变贵",
            },
        }
    s1 = _bp("SOFR", "DGS3MO", rates)
    s2 = _bp("SOFR", "IORB", rates)
    out["spreads"] = {"sofr_tbill3m_bp": s1, "sofr_iorb_bp": s2}
    # 60 日序列（双确认走势图）
    series = {}
    for name, a, b in [
        ("sofr_tbill3m", "SOFR", "DGS3MO"),
        ("sofr_iorb", "SOFR", "IORB"),
    ]:
        series[name + "_bp"] = _spread_series(rates, a, b)
    out["series"] = series
    out["rows"] = (
        [
            {
                "date": d.date().isoformat(),
                "reserves_b": round(float(v) / 1000, 2) if pd.notna(v) else None,
            }
            for d, v in liq["WRESBAL"].tail(20).items()
        ]
        if "WRESBAL" in liq
        else []
    )
    return out


def page_global_dollar() -> dict:
    """子页：全球美元 — DXY / 央行互换 / 掉期点一览 + 压力读数。"""
    liq = _liq_daily()
    cfets = _csv("fred/liquidity/cfets_swap_points.csv")
    asset = _csv("yfinance/asset_prices.csv")
    out: dict = {"dxy": {}, "swap": {}, "pairs": {}}
    dxy = (
        asset["DXY"].dropna()
        if not asset.empty and "DXY" in asset
        else pd.Series(dtype=float)
    )
    if not dxy.empty:
        out["dxy"] = {"value": float(dxy.iloc[-1]), "chg_1d_pct": _pct_chg(dxy, 1)}
    swpt = liq["SWPT"].dropna() if "SWPT" in liq else pd.Series(dtype=float)
    if not swpt.empty:
        out["swap"] = {
            "balance_m": round(float(swpt.iloc[-1]), 0),
            "date": swpt.index[-1].date().isoformat(),
            "chg_30d_m": round(_chg(swpt, 30) or 0, 0),
        }
    out["pairs"] = {
        _PAIR_DISPLAY.get(p, p): v for p, v in _offshore_detail(cfets).items()
    }
    out["score"] = _offshore_score(cfets)
    return out


def page_subsurface() -> dict:
    """子页：次表层 — SOFR 分位 / 成交量 z / SRF 激活 / 央行互换变化。"""
    rates = _csv("fred/rates/rates.csv")
    srf = _csv("fred/liquidity/srf.csv")
    liq = _liq_daily()
    out: dict = {
        "percentiles": {},
        "volume": {},
        "srf": {},
        "swap": {},
        "composite": {},
    }
    if rates.empty:
        return out
    pct = {}
    for col, name in [
        ("SOFR1", "1分位"),
        ("SOFR25", "25分位"),
        ("SOFR", "中位数"),
        ("SOFR75", "75分位"),
        ("SOFR99", "99分位"),
    ]:
        if col in rates.columns and not rates[col].dropna().empty:
            pct[name] = float(rates[col].dropna().iloc[-1])
    iorb = (
        _last(pd.to_numeric(rates.get("IORB"), errors="coerce"))
        if rates.get("IORB") is not None
        else None
    )
    out["percentiles"] = {"values": pct, "iorb": iorb}
    vol = (
        pd.to_numeric(rates.get("SOFRVOL"), errors="coerce").dropna()
        if rates.get("SOFRVOL") is not None
        else pd.Series(dtype=float)
    )
    z = None
    if len(vol) >= 60:
        w = vol.tail(60)
        z = (
            round((float(w.iloc[-1]) - float(w.mean())) / float(w.std()), 2)
            if float(w.std()) > 0
            else 0.0
        )
    out["volume"] = {
        "latest_b": round(float(vol.iloc[-1]), 0) if not vol.empty else None,
        "mean_60d_b": round(float(vol.tail(60).mean()), 0) if len(vol) >= 60 else None,
        "z60": z,
    }
    if not vol.empty:
        out["volume_series"] = [
            {"date": d.date().isoformat(), "value": round(float(v), 0)}
            for d, v in vol.tail(60).items()
        ]
    if not srf.empty:
        s = srf["SRF_USAGE"].dropna()
        out["srf"] = {
            "active_30d": int(s.tail(30).gt(0).sum()),
            "latest_m": round(float(s.iloc[-1]) * 1000, 0),
        }
    swpt = liq["SWPT"].dropna() if "SWPT" in liq else pd.Series(dtype=float)
    if not swpt.empty:
        out["swap"] = {
            "balance_m": round(float(swpt.iloc[-1]), 0),
            "chg_30d_m": round(_chg(swpt, 30) or 0, 0),
        }
    # 综合次表层压力：分项 raw 值 + 各自 max（展示 x/max），状态按加权和
    tail_w = (
        _score((pct.get("99分位", 0) - (iorb or 0)) * 100, [(5, 3), (0, 1.5)])
        if iorb is not None and "99分位" in pct
        else 0
    )
    vol_z = _score(z, [(2, 2), (1, 1), (0, 0)]) if z is not None else 0
    srf_act = (
        _score((out["srf"].get("active_30d") or 0), [(15, 3), (5, 1.5), (0, 0)])
        if out.get("srf")
        else 0
    )
    sw_chg = (
        _score((out["swap"].get("chg_30d_m") or 0), [(3000, 2), (500, 1), (0, 0)])
        if out.get("swap")
        else 0
    )
    total = round(tail_w + vol_z + srf_act + sw_chg, 1)
    state = "承压" if total >= 4 else ("轻微" if total >= 2 else "正常")
    out["composite"] = {
        "tail": round(tail_w, 1),
        "tail_max": 3,
        "volume": round(vol_z, 1),
        "volume_max": 2,
        "srf": round(srf_act, 1),
        "srf_max": 3,
        "swap": round(sw_chg, 1),
        "swap_max": 2,
        "state": state,
    }
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 统一入口（LLM 替换点）
# ─────────────────────────────────────────────────────────────────────────────


def _llm_generate_dashboard() -> dict | None:
    """LLM 生成入口（预留）：返回同构 dict 或 None（回落规则引擎）。

    接入方式：调用 LLM，传入各 page_* / lpi() / liquidity_snapshot() 的结构化
    数据作为上下文，返回同构 dict（键与 generate_dashboard 相同），
    并置 _LLM_ENABLED = True。未接入时返回 None。
    """
    return None


_LLM_ENABLED = False


def generate_dashboard() -> dict:
    """流动性专题统一入口：LLM 优先（预留），规则引擎兜底。"""
    if _LLM_ENABLED:
        llm_out = _llm_generate_dashboard()
        if llm_out is not None:
            return {**llm_out, "generator": "llm"}
    return {
        "generator": "rules",
        "snapshot": liquidity_snapshot(),
        "lpi": lpi(),
        "fed_balance_sheet": page_fed_balance_sheet(),
        "operations": page_operations(),
        "rrp_tga": page_rrp_tga(),
        "reserves": page_reserves(),
        "global_dollar": page_global_dollar(),
        "subsurface": page_subsurface(),
    }


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="流动性分析：默认打印自检；--snapshot 存 LPI 快照"
    )
    parser.add_argument(
        "--snapshot",
        action="store_true",
        help="保存当日 LPI 快照到 data/liquidity/lpi_history.csv",
    )
    args = parser.parse_args()
    if args.snapshot:
        save_lpi_snapshot()
        raise SystemExit(0)

    # 自检：跑通 + 打印关键读数
    out = generate_dashboard()
    liq = out["lpi"]
    print(
        f"LPI {liq['score']}/10 分层: 结构{liq['layers']['structure']['score']} "
        f"融资{liq['layers']['funding']['score']} "
        f"传导{liq['layers']['transmission']['score']}"
    )
    print("确认条件:", json.dumps(liq["confirmations"], ensure_ascii=False)[:300])
    cal = out["rrp_tga"]["calendar"]
    print(f"热力: 7日 {cal['net_7d_b']}B / 14日 {cal['net_14d_b']}B")
    for d in cal["days"][:5]:
        print(" ", d["date"], d["weekday"], d["net_b"], d["bucket"])
    print(
        "评估叙事:",
        json.dumps(out["snapshot"]["evaluation"]["net_liquidity"], ensure_ascii=False)[
            :200
        ],
    )
