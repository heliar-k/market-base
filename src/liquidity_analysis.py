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

from src.analysis_utils import read_csv_or_empty as _csv

ROOT = Path(__file__).resolve().parent.parent

# ─────────────────────────────────────────────────────────────────────────────
# 数据加载（只读 CSV，不写盘）
# ─────────────────────────────────────────────────────────────────────────────


def _liq_daily() -> pd.DataFrame:
    """日度流动性帧：周度列（WALCL/TREAST/WSHOMCB/WRESBAL/TGA）ffill 到日度，
    日度列（RRP）保留原值；NET_LIQUIDITY = WALCL − RRP×1000 − TGA（百万美元）。
    RRP 列保持十亿美元原单位（与宏观模块口径一致）。
    """
    liq = _csv(ROOT / "data" / "fred/liquidity/liquidity.csv")
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
    if past.empty:
        return None
    base = float(past.iloc[-1])
    # 近零基数守卫：基数小于序列自身量级的 0.1% 时，百分比是噪声不是信号。
    # RRP 从 2553B 抽干到个位数 B，基数 0.5B 能算出 +2000%；用绝对零判定拦不住。
    # 阈值用 max(abs) 做量级代理 → 不依赖单位（万亿级 WALCL 与两位数 DXY 同一规则）。
    if abs(base) <= 1e-3 * float(v.abs().max()):
        return None
    return d / abs(base) * 100


def _bp(s: str, t: str, rates: pd.DataFrame) -> float | None:
    """利率列差×100（bp），取两列最新共同有效日。"""
    s_ = pd.to_numeric(rates.get(s), errors="coerce")
    t_ = pd.to_numeric(rates.get(t), errors="coerce")
    both = pd.concat([s_, t_], axis=1).dropna()
    if both.empty:
        return None
    return round(float(both.iloc[-1, 0] - both.iloc[-1, 1]) * 100, 1)


def _score(v: float | None, bands: list[tuple[float, int]]) -> float | None:
    """分段打分：
    - 阈值降序（VIX 等「越大越紧」）：找第一个 v >= th 的档位；
    - 阈值升序（准备金/掉期点等「越负越紧」）：找第一个 v <= th 的档位；
    未命中时回退末档分数。
    """
    if v is None:
        return None
    if len(bands) >= 2 and bands[0][0] < bands[-1][0]:
        for th, sc in bands:
            if v <= th:
                return float(sc)
        return float(bands[-1][1])
    for th, sc in bands:
        if v >= th:
            return float(sc)
    return float(bands[-1][1])


# ── 单源阈值与口径常量（消除跨页矛盾与微额测试误报）──
SRF_SUBSTANTIAL_B = 0.05  # SRF 实质动用门槛：$50M（0.05B），剔除日常 $1M-$3M 连通性测试


def _srf_window_counts(srf: pd.DataFrame, days: int) -> tuple[int, int]:
    """按日历天窗口 [最新日 − days, 最新日] 统计 SRF (实质动用天数, 含微额总天数)。"""
    if srf.empty or "SRF_USAGE" not in srf:
        return 0, 0
    s = srf["SRF_USAGE"].dropna()
    if s.empty:
        return 0, 0
    w = s.loc[s.index >= s.index[-1] - pd.Timedelta(days=days)]
    return int(w.ge(SRF_SUBSTANTIAL_B).sum()), int(w.gt(0).sum())


def _sofr_iorb_state(bp: float | None) -> str:
    """全站唯一 SOFR−IORB 状态词（<0 充裕 / 0~3 中性偏紧 / >3 偏紧）。"""
    if bp is None:
        return "未知"
    if bp < 0:
        return "充裕"
    if bp <= 3:
        return "中性偏紧"
    return "偏紧"


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
    rates = _csv(ROOT / "data" / "fred/rates/rates.csv")
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
    lpi_d = lpi()
    # 近 52 个 H.4.1 周三观测点（~1 年），供入口页净流动性 + 三要素走势图（无需额外拉取 85KB JSON）
    raw_liq = _csv(ROOT / "data" / "fred/liquidity/liquidity.csv")
    h41_idx = (
        raw_liq["WALCL"].dropna().index[-52:]
        if not raw_liq.empty and "WALCL" in raw_liq
        else liq.index[-52:]
    )
    series_1y = [
        {
            "date": dt.date().isoformat(),
            "net_liq_t": round(float(liq["NET_LIQUIDITY"].get(dt)) / 1e6, 3)
            if "NET_LIQUIDITY" in liq and pd.notna(liq["NET_LIQUIDITY"].get(dt))
            else None,
            "reserves_t": round(float(liq["WRESBAL"].get(dt)) / 1e6, 3)
            if "WRESBAL" in liq and pd.notna(liq["WRESBAL"].get(dt))
            else None,
            "tga_t": round(float(liq["WTREGEN"].get(dt)) / 1e6, 3)
            if "WTREGEN" in liq and pd.notna(liq["WTREGEN"].get(dt))
            else None,
            "rrp_t": round(float(liq["RRPONTSYD"].get(dt)) / 1e3, 3)
            if "RRPONTSYD" in liq and pd.notna(liq["RRPONTSYD"].get(dt))
            else None,
        }
        for dt in h41_idx
    ]
    return {
        "cards": cards,
        "evaluation": nav,
        "lpi_summary": {
            "score": lpi_d.get("score"),
            "band": lpi_d.get("band"),
            "pctile_30d": lpi_d.get("pctile_30d"),
            "layers": lpi_d.get("layers"),
            "confirmations": lpi_d.get("confirmations"),
        }
        if lpi_d
        else {},
        "series_1y": series_1y,
        "data_date": _data_date(liq),
    }


def _data_date(liq: pd.DataFrame) -> str | None:
    latest = max(
        (s.dropna().index[-1] for s in (liq[c] for c in liq.columns) if not s.dropna().empty),
        default=None,
    )
    return latest.date().isoformat() if latest is not None else None


def _narrative(liq: pd.DataFrame, rates: pd.DataFrame) -> dict:
    """三段规则叙事：净流动性 / 展望 / 准备金，各带验证指标。"""
    nl = liq["NET_LIQUIDITY"].dropna() if "NET_LIQUIDITY" in liq else pd.Series(dtype=float)
    rrp = liq["RRPONTSYD"].dropna() if "RRPONTSYD" in liq else pd.Series(dtype=float)
    tga = liq["WTREGEN"].dropna() if "WTREGEN" in liq else pd.Series(dtype=float)
    res = liq["WRESBAL"].dropna() if "WRESBAL" in liq else pd.Series(dtype=float)
    nfci = liq["NFCI"].dropna() if "NFCI" in liq else pd.Series(dtype=float)
    srf = _csv(ROOT / "data" / "fred/liquidity/srf.csv")
    srf_usage = srf["SRF_USAGE"].dropna() if not srf.empty else pd.Series(dtype=float)
    asset = _csv(ROOT / "data" / "yfinance/asset_prices.csv")

    def _txt(v: float) -> str:
        return f"{v / 1e6:,.1f}"

    # 净流动性：结论（方向）/ 依据（TGA·RRP 节奏）/ 触发（阈值由现值推导）
    nl_latest = _last(nl)
    nl_1m = _chg(nl, 30) if not nl.empty else None
    tga_latest = _last(tga)
    rrp_latest = _last(rrp)
    tga_1m = _chg(tga, 30) if not tga.empty else None
    nl_dir = "方向未知" if nl_1m is None else ("在扩张" if nl_1m >= 0 else "在收缩")
    # 不复述上方 WALCL / RRP / TGA / 净流动性四张卡的水平值（研判只给卡上没有的
    # 1 月变化与判断）；旧文案把四张卡逐字重念一遍，且阈值写死 1.0 / 5.3 万亿
    # （现值已 0.95 / 5.78，等于「明天就触发」），改为相对现值推。
    nl_date = f"{nl.index[-1].date()}" if not nl.empty else None
    drain = tga_1m is not None and tga_1m > 2e4  # TGA 1 月升 > 200 十亿 = 财政在抽水
    if nl_1m is None:
        nl_text = f"截至 {nl_date}，净流动性数据不足。" if nl_date else "净流动性数据不足。"
    else:
        nl_basis = [f"截至 {nl_date}"]
        if tga_1m is not None:
            nl_basis.append(
                f"TGA 1 个月{'上升' if drain else '回落/平稳'}"
                + ("（财政存款在抽水）" if drain else "")
            )
        if rrp_latest is not None:
            nl_basis.append(f"RRP 缓冲{'接近耗尽' if rrp_latest < 25 else '仍有余量'}")
        trig = []
        if tga_latest is not None:
            trig.append(f"TGA 较现值再升 0.1 万亿（→ {_txt(tga_latest + 1e5)} 万亿）")
        if nl_latest is not None:
            trig.append(f"净流动性回吐 0.5 万亿（→ {_txt(nl_latest - 5e5)} 万亿）")
        nl_verify = ("、".join(trig) + "，则财政抽水加速。") if trig else ""
        nl_text = (
            f"净流动性{nl_dir}（1 个月 {nl_1m / 1000:+.0f} 十亿），"
            + ("财政端正在抽水。" if drain else "财政端未重新抽水。")
            + "\n"
            + " · ".join(nl_basis)
            + (f"\n{nl_verify}" if nl_verify else "")
        )

    # 展望：能源 + VIX + TGA 抽水节奏
    wti = asset["WTI"].dropna() if not asset.empty and "WTI" in asset else pd.Series(dtype=float)
    vix = _csv(ROOT / "data" / "cboe/volatility.csv")
    vix_s = vix["VIX"].dropna() if not vix.empty and "VIX" in vix else pd.Series(dtype=float)
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
    risk = "风险偏好数据不足"
    if vix_latest is not None:
        risk = (
            "风险偏好正常"
            if vix_latest < 18
            else "风险偏好开始逆转变弱"
            if vix_latest < 25
            else "风险偏好显著恶化"
        )
    outlook = []
    if wti_latest is not None:
        geo = "油价已含地缘风险溢价" if wti_latest > 80 else "油价处于低位"
        wti_sign = "+" if (wti_5d or 0) >= 0 else "-"
        wti_txt = f"{wti_sign}{abs(wti_5d or 0):.1f}%"
        outlook.append(f"WTI 收于 {wti_latest:.2f}（5 日 {wti_txt}），{geo}")
    if vix_latest is not None:
        outlook.append(f"VIX {vix_latest:.2f}")
    if tga_1m is not None and tga_1m > 0:
        outlook.append(f"TGA 高位（{_txt(tga_latest)} 万亿），国债发行持续抽水")
    # 1M 国债 vs IORB 利差（旧文案拿 RRP 与 IORB 比利率，RRP 是量不是价）
    d1mo, iorb_latest = _last(dgs1mo_s), _last(iorb_s)
    funding_gap = None if d1mo is None or iorb_latest is None else d1mo - iorb_latest
    wti_trig = (
        f"WTI 较现值再涨 10 美元（站上 {wti_latest + 10:.0f}）"
        if wti_latest is not None and wti_latest >= 88
        else "WTI 站上 90"
    )
    vix_trig = (
        "VIX 升破 25（进入高波动区）"
        if vix_latest is not None and vix_latest >= 20
        else "VIX 升破 20"
    )
    vix_verify = f"风险事件看 {wti_trig} 或 {vix_trig}，则风险偏好进一步恶化。"
    if funding_gap is not None:
        vix_verify += (
            f"1M 国债与 IORB 利差（现 {funding_gap * 100:+.0f}bp）"
            "继续走扩则短端融资溢价向风险资产传导。"
        )
    outlook_text = (
        f"{risk}。\n" + " · ".join(outlook) + f"。\n{vix_verify}"
        if outlook
        else "数据不足，展望待更新。"
    )

    # 准备金：结论（充裕/中性偏紧/偏紧）/ 依据（水平 + 4 周变化 + 各子项）/ 触发
    res_latest, res_4w = _last(res), _pct_chg(res, 28)
    sofr_iorb = _bp("SOFR", "IORB", rates)
    sofr_tgcr = _bp("SOFR", "TGCR", rates)
    effr_iorb = _bp("DFF", "IORB", rates)
    nfci_latest = _last(nfci)
    srf_latest = _last(srf_usage)
    srf_30d, _ = _srf_window_counts(srf, 30)
    if sofr_iorb is not None:
        si_state = _sofr_iorb_state(sofr_iorb)
        res_head = f"融资面{si_state}"
    elif nfci_latest is not None:
        res_head = "金融条件宽松" if nfci_latest < 0 else "金融条件收紧"
    else:
        res_head = "准备金数据不足"
    msg = []
    if res_latest is not None:
        res_4w_txt = f"（4 周 {res_4w:+.1f}%）" if res_4w is not None else ""
        msg.append(f"准备金 {res_latest / 1e6:.2f} 万亿{res_4w_txt}")
    if rrp_latest is not None:
        # 水平值不重复（RRP 卡片就在上方），只留判断
        msg.append("RRP 缓冲耗尽" if rrp_latest < 25 else "RRP 仍有余量")
    if nfci_latest is not None:
        msg.append(f"NFCI {nfci_latest:.2f}" + ("（宽松）" if nfci_latest < 0 else "（收紧）"))
    if sofr_iorb is not None:
        msg.append(f"SOFR−IORB {sofr_iorb:+.1f}bp（{_sofr_iorb_state(sofr_iorb)}）")
    if sofr_tgcr is not None:
        msg.append(f"SOFR−TGCR {sofr_tgcr:+.1f}bp")
    if srf_latest is not None:
        msg.append(
            f"SRF 当日 {srf_latest * 1000:.0f} 百万（30日实质动用 {srf_30d} 天）"
            if srf_latest >= SRF_SUBSTANTIAL_B or srf_30d > 0
            else "SRF 无实质动用"
        )
    # 触发条件：1M T-Bill 已高于 IORB+20bp 时改看 SOFR−IORB > +3bp 与 EFFR−IORB 转正
    res_trigs = []
    if sofr_iorb is not None and sofr_iorb <= 3:
        res_trigs.append(f"SOFR−IORB 突破 +3bp（现 {sofr_iorb:+.1f}bp）")
    if effr_iorb is not None and effr_iorb < 0:
        res_trigs.append(f"EFFR−IORB 由负转正（现 {effr_iorb:+.1f}bp）")
    if nfci_latest is not None and nfci_latest < 0:
        res_trigs.append(f"NFCI 升穿 0（现 {nfci_latest:.2f}）")
    res_verify = (
        ("、".join(res_trigs) + "，则确认准备金进入稀缺状态。")
        if res_trigs
        else "多项准备金稀缺指标已触发，密切跟踪 SRF 单日规模与 RMP 购买节奏。"
    )
    res_text = (
        f"{res_head}。\n" + " · ".join(msg) + f"。\n{res_verify}" if msg else "准备金数据不足。"
    )

    return {
        "net_liquidity": {"title": "净流动性", "text": nl_text},
        "outlook": {"title": "展望", "text": outlook_text},
        "reserves": {"title": "准备金", "text": res_text},
        "generator": "rules",
    }


# ─────────────────────────────────────────────────────────────────────────────
# LPI 压力指数（transmission-chain）
# ─────────────────────────────────────────────────────────────────────────────


def lpi() -> dict:
    """规则型美元流动性压力指数：三层加权 + 离岸/外部输入 + 确认条件。"""
    liq = _liq_daily()
    rates = _csv(ROOT / "data" / "fred/rates/rates.csv")
    credit = _csv(ROOT / "data" / "fred/credit/credit.csv")
    vix = _csv(ROOT / "data" / "cboe/volatility.csv")
    srf = _csv(ROOT / "data" / "fred/liquidity/srf.csv")
    cfets = _csv(ROOT / "data" / "fred/liquidity/cfets_swap_points.csv")
    asset = _csv(ROOT / "data" / "yfinance/asset_prices.csv")
    if liq.empty:
        return {}

    res_s = liq["WRESBAL"].dropna() if "WRESBAL" in liq else pd.Series(dtype=float)
    nl_s = liq["NET_LIQUIDITY"].dropna() if "NET_LIQUIDITY" in liq else pd.Series(dtype=float)
    rrp_s = liq["RRPONTSYD"].dropna() if "RRPONTSYD" in liq else pd.Series(dtype=float)
    nfci_s = liq["NFCI"].dropna() if "NFCI" in liq else pd.Series(dtype=float)

    # ── 结构性缓冲（45%）：准备金4周变化 / 净流动性20日脉冲 / RRP 缓冲 ──
    res_4w = _pct_chg(res_s, 28)
    # 分档（升序阈值）：≤-6% 显著收缩(9) / ≤-3%(7) / ≤-1%(5) / ≤0%(3) / >0% 扩张(2)
    s_res = _score(res_4w, [(-6, 9), (-3, 7), (-1, 5), (0, 3), (999, 2)])
    nl_20d_m = _chg(nl_s, 20)  # 原始序列为百万美元
    nl_20d_b = (
        round(nl_20d_m / 1000, 1) if nl_20d_m is not None else None
    )  # 统一折算为十亿美元（B）
    # 分档（十亿美元）：≤-500B(9) / ≤-300B(8) / ≤-150B(6) / <0(5) / ≤+50B(4) / >+50B(2)
    s_nl = _score(nl_20d_b, [(-500, 9), (-300, 8), (-150, 6), (-0.001, 5), (50, 4), (99999, 2)])
    rrp_latest = _last(rrp_s)
    # RRP 缓冲（十亿美元，越低缓冲越薄）：≤20B 耗尽(9) / ≤100B(7) / ≤200B(5) / ≤500B(3) / >500B(1)
    s_rrp = _score(rrp_latest, [(20, 9), (100, 7), (200, 5), (500, 3), (99999, 1)])

    # ── 融资市场确认（35%）：SOFR−IORB / SRF / SOFR99−IORB / 离岸 1M ──
    sofr_iorb = _bp("SOFR", "IORB", rates)
    s_sofr = _score(sofr_iorb, [(10, 10), (3, 8), (0, 6), (-2, 4), (-10, 2), (-99, 1)])
    srf_usage = srf["SRF_USAGE"].dropna() if not srf.empty else pd.Series(dtype=float)
    srf_latest = _last(srf_usage)
    s_srf = _score(srf_latest, [(10, 8), (1, 5), (SRF_SUBSTANTIAL_B, 2), (0, 1)])
    sofr99_iorb = _bp("SOFR99", "IORB", rates)
    s_tail = _score(sofr99_iorb, [(15, 10), (5, 8), (0, 5), (-10, 2), (-99, 1)])
    offshore = _offshore_score(cfets)
    s_off = _weighted([(offshore, 1.0)])
    s_fund = _weighted([(s_sofr, 0.4), (s_srf, 0.2), (s_tail, 0.25), (s_off, 0.15)])

    # ── 风险资产传导（20%）：VIX / MOVE / HY 利差 / NFCI ──
    vix_s = vix["VIX"].dropna() if not vix.empty and "VIX" in vix else pd.Series(dtype=float)
    vix_latest = _last(vix_s)
    s_vix = _score(vix_latest, [(35, 9), (25, 7), (18, 5), (14, 3), (0, 1)])
    move_s = (
        asset["MOVE"].dropna() if not asset.empty and "MOVE" in asset else pd.Series(dtype=float)
    )
    move_latest = _last(move_s)
    s_move = _score(move_latest, [(140, 8), (110, 6), (80, 4), (0, 2)])
    bb_oas = credit.get("BB_OAS") if not credit.empty else None
    bb_s_pct = (
        pd.to_numeric(bb_oas, errors="coerce").dropna()
        if bb_oas is not None
        else pd.Series(dtype=float)
    )
    # FRED BAMLC0A4CBBB / BAMLH0A1HYBB 原始单位为百分点（2.10 = 2.10% = 210bp），统一乘 100 转为 bp
    bb_s_bp = bb_s_pct * 100
    bb_latest_bp = round(float(bb_s_bp.iloc[-1]), 0) if not bb_s_bp.empty else None
    s_credit = _score(bb_latest_bp, [(500, 8), (300, 6), (200, 4), (0, 2)])
    nfci_latest = _last(nfci_s)
    s_nfci = _score(nfci_latest, [(0.7, 9), (0.3, 7), (0, 5), (-0.3, 3), (-99, 1)])
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
        sofr_iorb, srf_latest, bb_s_bp, nfci_latest, vix_latest, rrp_latest, rates
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
                    {"name": "准备金4周变化", "value": res_4w, "unit": "%", "score": s_res},
                    {"name": "净流动性20日脉冲", "value": nl_20d_b, "unit": "B", "score": s_nl},
                    {"name": "RRP缓冲", "value": rrp_latest, "unit": "B", "score": s_rrp},
                ],
            },
            "funding": {
                "score": s_fund,
                "weight": 0.35,
                "parts": [
                    {"name": "SOFR−IORB", "value": sofr_iorb, "unit": "bp", "score": s_sofr},
                    {"name": "SRF使用", "value": srf_latest, "unit": "B", "score": s_srf},
                    {"name": "SOFR99−IORB", "value": sofr99_iorb, "unit": "bp", "score": s_tail},
                    {"name": "离岸美元压力", "value": offshore, "unit": "分", "score": s_off},
                ],
            },
            "transmission": {
                "score": s_risk,
                "weight": 0.20,
                "parts": [
                    {"name": "VIX", "value": vix_latest, "unit": "", "score": s_vix},
                    {"name": "MOVE", "value": move_latest, "unit": "", "score": s_move},
                    {"name": "HY OAS(BB)", "value": bb_latest_bp, "unit": "bp", "score": s_credit},
                    {"name": "NFCI", "value": nfci_latest, "unit": "", "score": s_nfci},
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
    已由 daily-full workflow 自动触发；本地可手动运行 --snapshot。
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
        "NL_20D_B": parts.get("净流动性20日脉冲", {}).get("value"),
        "RRP_B": parts.get("RRP缓冲", {}).get("value"),
        "SOFR_IORB_BP": parts.get("SOFR−IORB", {}).get("value"),
        "SRF_B": parts.get("SRF使用", {}).get("value"),
        "SOFR99_IORB_BP": parts.get("SOFR99−IORB", {}).get("value"),
        "OFFSHORE_SCORE": parts.get("离岸美元压力", {}).get("score"),
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
    """当前 LPI 在近 30 个快照中的分位（0-100，中位秩口径）。历史不足 10 条返回 None。

    只读历史，不重算 lpi()（历史曾因内部重算造成互递归嵌套，审计后修复）。
    """
    if not LPI_HISTORY_PATH.exists():
        return None
    hist = pd.read_csv(LPI_HISTORY_PATH, index_col=0)
    s = pd.to_numeric(hist.get("SCORE"), errors="coerce").dropna().tail(30)
    if len(s) < 10 or score is None:
        return None
    less = (s < score).sum()
    eq = (s == score).sum()
    return round((less + 0.5 * eq) / len(s) * 100)


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
    bb_s_bp: pd.Series,
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
    bb_1m_bp = _chg(bb_s_bp, 30) if not bb_s_bp.empty else None
    return [
        {
            "title": "SOFR−IORB 突破 +3bp 偏紧线",
            "met": (sofr_iorb or 0) > 3,
            "detail": f"当前 {sofr_iorb:+.1f}bp" if sofr_iorb is not None else "数据缺失",
        },
        {
            "title": "SRF 当日出现十亿美元级使用（≥$1B）",
            "met": (srf_latest or 0) >= 1,
            "detail": f"当日 {srf_latest * 1000:.0f} 百万"
            if srf_latest is not None
            else "数据缺失",
        },
        {
            "title": "HY 利差明显走阔（30日 > +25bp）",
            "met": (bb_1m_bp or 0) > 25,
            "detail": f"BB OAS 30日变化 {bb_1m_bp:+.0f}bp" if bb_1m_bp is not None else "数据缺失",
        },
        {
            "title": "NFCI 转正",
            "met": (nfci_latest or 0) > 0,
            "detail": f"当前 {nfci_latest:.2f}" if nfci_latest is not None else "数据缺失",
        },
        {
            "title": "VIX 升至 20 上方",
            "met": (vix_latest or 0) > 20,
            "detail": f"当前 {vix_latest:.2f}" if vix_latest is not None else "数据缺失",
        },
        {
            "title": "RRP 贴零且 1M 国债收益率升破 IORB+20bp",
            "met": (rrp_latest or 0) < 25
            and not dgs1mo.empty
            and not iorb.empty
            and float(dgs1mo.iloc[-1]) > float(iorb.iloc[-1]) + 0.20,
            "detail": (
                f"RRP {rrp_latest:.2f}B, 1M T-Bill {_last(dgs1mo):.2f}% / IORB {_last(iorb):.2f}%"
                if rrp_latest is not None and not dgs1mo.empty and not iorb.empty
                else "数据缺失"
            ),
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
# 掉期点方向归一化系数：
# - USD/JPY、USD/CNH、USD/CHF 以 USD 为基准货币，F−S < 0 表示美元利率高于外币；
# - EUR/USD、GBP/USD 以 USD 为计价货币，F−S > 0 表示美元利率高于欧元/英镑。
# 统一乘以符号后，各货币对按各自 pip 量纲定制阈值（CNH 常态 -150，JPY -38，EUR +14）。
_PAIR_STRESS_BANDS: dict[str, tuple[float, list[tuple[float, int]]]] = {
    "USDJPY": (1.0, [(-60, 9), (-45, 7), (-30, 5), (0, 3)]),
    "USDCNH": (1.0, [(-220, 9), (-160, 7), (-100, 5), (0, 3)]),
    "USDCHF": (1.0, [(-45, 9), (-32, 7), (-20, 5), (0, 3)]),
    "EURUSD": (-1.0, [(-25, 9), (-16, 7), (-8, 5), (0, 3)]),
    "GBPUSD": (-1.0, [(-15, 9), (-8, 7), (-2, 5), (0, 3)]),
}


def _offshore_score(cfets: pd.DataFrame) -> float | None:
    """各货币对 1M 掉期点平均压力分（按报价方向与各自 pip 量纲分档）。"""
    scores: list[float] = []
    for pair, (sign, bands) in _PAIR_STRESS_BANDS.items():
        col = f"{pair}_1M"
        if col not in cfets or cfets[col].dropna().empty:
            continue
        v = float(cfets[col].dropna().iloc[-1]) * sign
        s = _score(v, bands)
        if s is not None:
            scores.append(s)
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
            "reserves_4w_pct": _pct_chg(liq["WRESBAL"], 28) if "WRESBAL" in liq else None,
            "dgs10": _last(pd.to_numeric(rates.get("DGS10"), errors="coerce"))
            if rates.get("DGS10") is not None
            else None,
        },
        "repo": {
            "sofr": _last(pd.to_numeric(rates.get("SOFR"), errors="coerce"))
            if rates.get("SOFR") is not None
            else None,
            "sofr_iorb_bp": _bp("SOFR", "IORB", rates),
            "sofr_iorb_state": _sofr_iorb_state(_bp("SOFR", "IORB", rates)),
            "sofr_tgcr_bp": _bp("SOFR", "TGCR", rates),
            "effr_iorb_bp": _bp("DFF", "IORB", rates),
            # RRPONTSYD 原始单位已是十亿美元（B），不可调 _fmt_b（会再除 1000 变万亿）
            "rrp_b": f"{_last(liq['RRPONTSYD']):,.2f}"
            if "RRPONTSYD" in liq and _last(liq["RRPONTSYD"]) is not None
            else None,
            "srf_m": _last(srf["SRF_USAGE"]) * 1000 if not srf.empty else None,
            "srf_active_30d": _srf_window_counts(srf, 30)[0],
        },
        "energy": {
            "wti": _last(asset["WTI"]) if not asset.empty and "WTI" in asset.columns else None,
            "wti_5d_pct": _pct_chg(asset["WTI"], 5)
            if not asset.empty and "WTI" in asset.columns
            else None,
            "ng": _last(asset["NG"]) if not asset.empty and "NG" in asset.columns else None,
        },
        "intermediary": {
            "vix": _last(vix["VIX"]) if not vix.empty else None,
            "move": _last(asset["MOVE"]) if not asset.empty else None,
            # BB_OAS / BBB_OAS 原始单位为 %（2.10 = 2.10% = 210bp），统一乘 100 输出 bp
            "bb_oas": round(
                float(pd.to_numeric(credit.get("BB_OAS"), errors="coerce").dropna().iloc[-1]) * 100,
                0,
            )
            if not credit.empty and credit.get("BB_OAS") is not None
            else None,
            "ig_oas": round(
                float(pd.to_numeric(credit.get("BBB_OAS"), errors="coerce").dropna().iloc[-1])
                * 100,
                0,
            )
            if not credit.empty and credit.get("BBB_OAS") is not None
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


def _settle_bus_day(d: pd.Timestamp) -> pd.Timestamp:
    """国债到期/结算日遇周末顺延至下一交易日（周六+2d，周日+1d）。"""
    if d.weekday() == 5:
        return d + pd.Timedelta(days=2)
    if d.weekday() == 6:
        return d + pd.Timedelta(days=1)
    return d


def forward_calendar(days: int = 14) -> dict:
    """未来 days 天 TGA 净冲击估算：
    - 拍卖结算：upcoming_auctions 按 issue_date 归集 offering_amt（已排期但尚未公布金额的
      场次按同品种/同期限最近一期拍卖规模补齐估算，抽水 −），抵消同结算日到期本金回笼（+）
    - SOMA 净变动（估算）：WALCL 最近 4 周周三观测周均，落在周四（结算日惯例）
    返回 {days: [...], net_7d_b, net_14d_b, source_note}（net_7/14 = 未来窗口估算）。
    """
    up = pd.read_csv(ROOT / "data" / "treasury/upcoming_auctions.csv", parse_dates=["issue_date"])
    auc = pd.read_csv(
        ROOT / "data" / "treasury/auction_results.csv",
        parse_dates=["maturity_date", "issue_date"],
    )
    today = pd.Timestamp(date.today())
    window = [today + pd.Timedelta(days=i) for i in range(1, days + 1)]

    # 最近一期同品种/同期限拍卖规模（供已列入 upcoming_auctions 但 offering_amt=NaN 的场次估算）
    latest_amt: dict[tuple[str, str], float] = {}
    if not auc.empty and {"security_type", "security_term", "offering_amt"}.issubset(auc.columns):
        valid_auc = auc.dropna(subset=["offering_amt"]).copy()
        for _, r in valid_auc.iterrows():
            st = str(r.get("security_type", ""))
            if r.get("inflation_index_security") == "Yes":
                st = f"TIPS {st}"
            elif r.get("floating_rate") == "Yes":
                st = f"FRN {st}"
            term = str(r.get("security_term", ""))
            amt_v = pd.to_numeric(r.get("offering_amt"), errors="coerce")
            if pd.notna(amt_v) and amt_v > 0:
                latest_amt[(st, term)] = float(amt_v)

    # 净发行 = 当日新发结算（issue_date） − 当日到期（maturity_date，周末到期顺延周一）
    # 国库券每周二/四同日滚续，匹配当日到期后才是资金净流入/流出财政的真实幅度。
    settle: dict[pd.Timestamp, float] = {}
    settle_has_est: set[pd.Timestamp] = set()
    seen_2y_fixed: set[pd.Timestamp] = set()
    if not up.empty and "issue_date" in up.columns:
        mask = (up["issue_date"] >= window[0]) & (up["issue_date"] <= window[-1])
        for _, row in up[mask].iterrows():
            d = _settle_bus_day(pd.Timestamp(row["issue_date"]))
            st, term = str(row.get("security_type", "")), str(row.get("security_term", ""))
            raw_amt = pd.to_numeric(row.get("offering_amt"), errors="coerce")
            is_est = False
            if pd.isna(raw_amt) or raw_amt <= 0:
                # 同一结算日出现两只 2-Year Note 时，第二只为 FRN 2-Year Note
                if st == "Note" and term == "2-Year" and d in seen_2y_fixed:
                    raw_amt = latest_amt.get(("FRN Note", "2-Year"), 0.0)
                else:
                    raw_amt = latest_amt.get((st, term), 0.0)
                    if st == "Note" and term == "2-Year":
                        seen_2y_fixed.add(d)
                is_est = True
            else:
                if st == "Note" and term == "2-Year":
                    seen_2y_fixed.add(d)
            amt = float(raw_amt or 0.0) / 1e9
            if amt > 0:
                settle[d] = settle.get(d, 0.0) - amt  # 十亿美元，抽水为负
                if is_est:
                    settle_has_est.add(d)
    if not auc.empty and "maturity_date" in auc.columns:
        mat_by_day: dict[pd.Timestamp, float] = {}
        for _, r in auc.dropna(subset=["maturity_date"]).iterrows():
            md = _settle_bus_day(pd.Timestamp(r["maturity_date"]))
            if window[0] <= md <= window[-1]:
                amt_v = pd.to_numeric(r.get("offering_amt"), errors="coerce")
                if pd.notna(amt_v) and amt_v > 0:
                    mat_by_day[md] = mat_by_day.get(md, 0.0) + float(amt_v) / 1e9
        for d, amt in mat_by_day.items():
            if d in settle:
                settle[d] += amt  # 到期回笼（注入）抵消同日新发

    rmp_week = _soma_week_net_b()
    rows = []
    for d in window:
        flows: list[dict] = []
        net = 0.0
        if d in settle and round(settle[d], 1) != 0:
            lbl = "拍卖净结算(含未公布期估算)" if d in settle_has_est else "拍卖结算"
            flows.append(
                {
                    "type": "auction_settlement",
                    "label": lbl,
                    "amount_b": round(settle[d], 1),
                }
            )
            net += settle[d]
        if d.weekday() == 3 and rmp_week is not None and rmp_week != 0:  # 周四 = SOMA 周结算日
            soma_lbl = "SOMA 净变动(估算)" if rmp_week > 0 else "SOMA 净到期(估算)"
            flows.append({"type": "soma_net", "label": soma_lbl, "amount_b": rmp_week})
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
        "source_note": "净冲击 = 当日到期回笼 − 新发抽水（已入日历但未公布金额的场次"
        "按同期限近一期规模估算，周末到期顺延至周一）；"
        "SOMA 净变动 = WALCL 最近 4 周周三观测周均（周四结算惯例，估算）。"
        "尚未列入 Treasury 未来拍卖日历的远期周度账单按净滚续为 0 处理。",
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
    """子页：资产负债表 — 卡片 + 近 20 期 H.4.1 周三对照表 + 近 2 年周三序列
    （Fed 总数/净流动性/SPX/NDX/国债/MBS）。"""
    # 金额为十亿美元（B）；SPX/NDX 取同日（或最近交易日）收盘
    liq = _liq_daily()
    raw_liq = _csv(ROOT / "data" / "fred/liquidity/liquidity.csv")
    asset = _csv(ROOT / "data" / "yfinance/asset_prices.csv")
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
                "chg_4w_pct": _pct_chg(s, 28),
            }
    spx = (
        asset["SPX"].dropna().ffill()
        if not asset.empty and "SPX" in asset
        else pd.Series(dtype=float)
    )
    ndx = (
        asset["NDX"].dropna().ffill()
        if not asset.empty and "NDX" in asset
        else pd.Series(dtype=float)
    )
    # 只取真实 H.4.1 周三发布日（消除旧表 20 行里 16 行是 null 空行的缺陷）
    h41_dates = (
        raw_liq["WALCL"].dropna().index if not raw_liq.empty and "WALCL" in raw_liq else liq.index
    )

    def _row_at(dt: pd.Timestamp) -> dict:
        r: dict = {"date": dt.date().isoformat()}
        for col in ("NET_LIQUIDITY", "WALCL", "TREAST", "WSHOMCB"):
            if col in liq.columns:
                v = liq[col].get(dt)
                r[col] = round(float(v) / 1000, 3) if pd.notna(v) else None
        spx_v = spx.asof(dt) if not spx.empty else None
        ndx_v = ndx.asof(dt) if not ndx.empty else None
        r["SPX"] = float(spx_v) if pd.notna(spx_v) else None
        r["NDX"] = float(ndx_v) if pd.notna(ndx_v) else None
        return r

    rows = [_row_at(dt) for dt in h41_dates[-20:]]
    series = [_row_at(dt) for dt in h41_dates[-104:]]
    # 研判：QT 缩表节奏与净流动性去向（hub 三段讲净流动性方向，这里讲资产端怎么缩）
    walcl = liq["WALCL"].dropna() if "WALCL" in liq else pd.Series(dtype=float)
    treast = liq["TREAST"].dropna() if "TREAST" in liq else pd.Series(dtype=float)
    mbs = liq["WSHOMCB"].dropna() if "WSHOMCB" in liq else pd.Series(dtype=float)
    walcl_4w, treast_4w, mbs_4w = (_pct_chg(s, 28) for s in (walcl, treast, mbs))
    if walcl_4w is not None:
        parts_4w = []
        if treast_4w is not None:
            parts_4w.append(f"国债 {treast_4w:+.2f}%")
        if mbs_4w is not None:
            parts_4w.append(f"MBS {mbs_4w:+.2f}%")
        shrink = "缩表进行中" if walcl_4w < 0 else "总资产走平或回升（QT 停止或 RMP 抵消）"
        walcl_t = walcl.iloc[-1] / 1e6
        judgement = {
            "title": "缩表研判",
            "text": f"{shrink}。\n总资产 {walcl_t:.2f} 万亿（4 周 {walcl_4w:+.2f}%）"
            + ("，其中 " + " · ".join(parts_4w) if parts_4w else "")
            + "。\n"
            + (
                "缩表在抽水，但只要 RRP 同步下降即是被吸收，真正伤及准备金的是 RRP 贴零之后。"
                if walcl_4w < 0
                else "关注回升是 RMP 投放还是 QT 实质转向，两者对流动性的含义不同。"
            ),
        }
    else:
        judgement = None
    return {
        "cards": cards,
        "rows": rows,
        "series": series,
        "judgement": judgement,
        "note": "仅列 H.4.1 周三发布日（消除非发布日空行）；SPX/NDX 取同日收盘。",
    }


def page_operations() -> dict:
    """子页：公开市场操作 — RMP 统计 / 最近 40 条 / SOMA 摘要 / SRF 90 天。"""
    ops = pd.read_csv(
        ROOT / "data" / "fred/liquidity/tsy_operations.csv",
        parse_dates=["settlement_date"],
    )
    liq = _liq_daily()
    srf = _csv(ROOT / "data" / "fred/liquidity/srf.csv")
    out: dict = {"rmp": {}, "recent": [], "soma": {}, "srf": {}, "as_of": ""}
    if ops.empty:
        return out
    out["as_of"] = str(ops["settlement_date"].max().date())
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
        act90, raw90 = _srf_window_counts(srf, 90)
        s = srf["SRF_USAGE"].dropna()
        out["srf"] = {
            "active_days_90": act90,
            "raw_days_90": raw90,
            "threshold_m": int(SRF_SUBSTANTIAL_B * 1000),
            "latest_m": round(float(s.iloc[-1]) * 1000, 0),
        }
    # 研判：RMP 执行评估（页面只展示进度数字，不判断「是否达标 / 市场紧不紧」）
    if out["rmp"]:
        r = out["rmp"]
        pace_ratio = r["monthly_b"] / r["target_b"] if r["target_b"] else None
        if pace_ratio is None:
            pace_txt = "节奏无法评估"
        elif pace_ratio >= 0.85:
            pace_txt = f"月均 {r['monthly_b']:.1f}B，接近或达到 {r['target_b']:.0f}B 目标"
        elif pace_ratio >= 0.5:
            pace_txt = f"月均 {r['monthly_b']:.1f}B，低于 {r['target_b']:.0f}B 目标但持续投放"
        else:
            pace_txt = f"月均 {r['monthly_b']:.1f}B，明显落后于 {r['target_b']:.0f}B 目标"
        srf_d = out["srf"].get("active_days_90", 0)
        srf_txt = (
            "SRF 无实质动用（≥$50M），准备金管理操作属于预防性投放而非救急"
            if srf_d == 0
            else f"SRF 90 天实质动用（≥$50M）{srf_d} 天，回购市场在结算/季末日存在阶段性摩擦"
        )
        out["judgement"] = {
            "title": "操作研判",
            "text": (
                f"{pace_txt}。\n累计 {r['cumulative_b']:.0f}B / {r['count']} 次操作；{srf_txt}。\n"
            )
            + (
                "SRF 持续非零则 RMP 需加码或提前结束 QT，跟踪下次 FOMC 的资产负债表指引。"
                if srf_d > 0
                else "RMP 属技术性补充（非 QE），若 SOFR−IORB 走扩或 SRF 激活，即为加码信号。"
            ),
        }
    return out


def page_rrp_tga() -> dict:
    """子页：RRP & TGA — 压力预警 / 未来 14 天热力 / 当前余额 / 日度现金流。"""
    liq = _liq_daily()
    raw_liq = _csv(ROOT / "data" / "fred/liquidity/liquidity.csv")
    rates = _csv(ROOT / "data" / "fred/rates/rates.csv")
    dts = _csv(ROOT / "data" / "treasury/dts_cashflows.csv")
    dts_tga = _csv(ROOT / "data" / "treasury/dts_operating_cash.csv")
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
        "state": _sofr_iorb_state(sofr_iorb),
        "sofr": sofr,
        "iorb": iorb,
        "ffr_upper": dff,
    }
    # 给 21 天（展示口径 14 天）：静态站构建期固化，前端按打开时间重切 14 天
    out["calendar"] = forward_calendar(21)
    out["data_date"] = _data_date(dts_tga) if not dts_tga.empty else _data_date(liq)
    srf = _csv(ROOT / "data" / "fred/liquidity/srf.csv")
    # TGA 优先 DTS 日度余额（更实时），同时附带 H.4.1 周度值供与首页净流动性口径对照
    tga_latest = None
    tga_wtregen_b = (
        round(float(raw_liq["WTREGEN"].dropna().iloc[-1]) / 1000, 1)
        if not raw_liq.empty and "WTREGEN" in raw_liq and not raw_liq["WTREGEN"].dropna().empty
        else None
    )
    if not dts_tga.empty and not dts_tga["TGA_CLOSE"].dropna().empty:
        s = dts_tga["TGA_CLOSE"].dropna()
        tga_latest = round(float(s.iloc[-1]) / 1000, 1)
        tga_date = s.index[-1].date().isoformat()
    elif tga_wtregen_b is not None:
        tga_latest = tga_wtregen_b
        tga_date = raw_liq["WTREGEN"].dropna().index[-1].date().isoformat()
    else:
        tga_date = None
    out["current"] = {
        "rrp_b": round(float(liq["RRPONTSYD"].dropna().iloc[-1]), 2)
        if "RRPONTSYD" in liq and not liq["RRPONTSYD"].dropna().empty
        else None,
        "tga_b": tga_latest,
        "tga_wtregen_b": tga_wtregen_b,
        "tga_date": tga_date,
        "net_liq_b": round(float(liq["NET_LIQUIDITY"].dropna().iloc[-1]) / 1000, 1)
        if "NET_LIQUIDITY" in liq and not liq["NET_LIQUIDITY"].dropna().empty
        else None,
        "srf_m": round(float(srf["SRF_USAGE"].dropna().iloc[-1]) * 1000, 0)
        if not srf.empty and not srf["SRF_USAGE"].dropna().empty
        else None,
        "srf_active_30d": _srf_window_counts(srf, 30)[0],
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
    # 60 日序列（SOFR−IORB 走势 + TGA 日度余额：优先 DTS 日度 TGA_CLOSE，消除周频 ffill 阶梯）
    series = {}
    s_i = _spread_series(rates, "SOFR", "IORB")
    if s_i:
        series["sofr_iorb_bp"] = s_i
    if not dts_tga.empty and "TGA_CLOSE" in dts_tga and not dts_tga["TGA_CLOSE"].dropna().empty:
        t = dts_tga["TGA_CLOSE"].dropna().tail(60)
        series["tga_b"] = [
            {"date": d.date().isoformat(), "value": round(float(v) / 1000, 1)} for d, v in t.items()
        ]
    elif "WTREGEN" in liq.columns and not liq["WTREGEN"].dropna().empty:
        t = liq["WTREGEN"].dropna().tail(60)
        series["tga_b"] = [
            {"date": d.date().isoformat(), "value": round(float(v) / 1000, 1)} for d, v in t.items()
        ]
    out["series"] = series
    # 研判：RRP 缓冲对未来净冲击的覆盖倍数（页面只展示金额，不下结论）
    rrp_b = out["current"]["rrp_b"]
    net14 = out["calendar"].get("net_14d_b")  # 复用 forward_calendar(21) 的 14 天窗口和
    if rrp_b is not None and net14 is not None:
        if net14 >= 0:
            out["judgement"] = {
                "title": "前瞻研判",
                "text": f"未来 14 天预计净注入 {net14:+,.0f}B。\n"
                f"财政净投放叠加 RRP 缓冲 {rrp_b:,.0f}B，窗口内不存在抽水压力。\n"
                "日历为拍卖结算 + SOMA 估算，实际以 DTS 日度现金流为准。",
            }
        else:
            cover = rrp_b / -net14 if net14 < 0 else None
            if rrp_b <= 25:
                state_txt = "RRP 缓冲已耗尽"
                verdict = "缓冲垫趋零后净冲击直接消耗准备金，压力将先出现在 SOFR−IORB 与 SRF"
            elif cover is not None and cover < 1:
                state_txt = "净冲击超出 RRP 缓冲"
                verdict = "缺口部分直接由银行准备金承担，重点关注结算日的货币市场利率反应"
            elif cover is not None and cover < 2:
                state_txt = "RRP 缓冲仅够单轮净冲击"
                verdict = "缓冲较薄，若叠加发债上量则准备金将加速消耗，留意 RRP 下降斜率"
            else:
                state_txt = "RRP 缓冲可覆盖本轮抽水"
                verdict = "缓冲层足够厚，除非发债节奏显著上量，窗口内压力可控"
            out["judgement"] = {
                "title": "前瞻研判",
                "text": f"{state_txt}。\n未来 14 天净抽水 {-net14:,.0f}B vs RRP 缓冲 {rrp_b:,.1f}B"
                + (f"（覆盖 {cover:.1f} 倍）" if cover is not None else "")
                + f"。\n{verdict}。",
            }
    return out


def page_reserves() -> dict:
    """子页：准备金 — 卡片 + 银行中介意愿（SOFR−3M T-Bill / SOFR−IORB / SOFR−TGCR / EFFR−IORB）。"""
    liq = _liq_daily()
    rates = _csv(ROOT / "data" / "fred/rates/rates.csv")
    out: dict = {"cards": {}, "spreads": {}, "rows": []}
    if liq.empty:
        return out
    res = liq["WRESBAL"].dropna() if "WRESBAL" in liq else pd.Series(dtype=float)
    s1 = _bp("SOFR", "DGS3MO", rates)
    s2 = _bp("SOFR", "IORB", rates)
    s_tgcr = _bp("SOFR", "TGCR", rates)
    s_effr = _bp("DFF", "IORB", rates)
    raw_liq = _csv(ROOT / "data" / "fred/liquidity/liquidity.csv")
    raw_res = raw_liq["WRESBAL"].dropna() if not raw_liq.empty and "WRESBAL" in raw_liq else res
    if not res.empty:
        # 区分「交易商资产负债表摩擦（SOFR−TGCR ≥ 3bp）」与「银行准备金稀缺（EFFR−IORB ≥ 0bp）」
        dealer_friction = s_tgcr is not None and s_tgcr >= 3
        bank_scarce = s_effr is not None and s_effr >= 0
        if bank_scarce and dealer_friction:
            friction_state = "双重收紧（准备金+交易商）"
        elif bank_scarce:
            friction_state = "银行准备金偏紧"
        elif dealer_friction:
            friction_state = "交易商资产负债表摩擦"
        else:
            friction_state = "双层畅通"
        out["cards"] = {
            "reserves": {
                "value_b": round(float(raw_res.iloc[-1]) / 1000, 2),
                "date": raw_res.index[-1].date().isoformat(),
                "chg_4w_pct": _pct_chg(raw_res, 28),
            },
            "intermediation": {
                "state": "正常" if s1 is not None and s1 < 15 else "偏紧",
                "note": "SOFR−3M T-Bill 走扩表明担保融资相对无风险资产变贵",
            },
            "dealer_friction": {
                "sofr_tgcr_bp": s_tgcr,
                "effr_iorb_bp": s_effr,
                "state": friction_state,
                "note": "SOFR−TGCR 看美债供给挤占交易商，EFFR−IORB 看银行准备金真实水位",
            },
        }
    out["spreads"] = {
        "sofr_tbill3m_bp": s1,
        "sofr_iorb_bp": s2,
        "sofr_tgcr_bp": s_tgcr,
        "effr_iorb_bp": s_effr,
    }
    # 60 日序列（四条核心利差走势图）
    series = {}
    for name, a, b in [
        ("sofr_tbill3m", "SOFR", "DGS3MO"),
        ("sofr_iorb", "SOFR", "IORB"),
        ("sofr_tgcr", "SOFR", "TGCR"),
        ("effr_iorb", "DFF", "IORB"),
    ]:
        series[name + "_bp"] = _spread_series(rates, a, b)
    out["series"] = series
    # 仅取 H.4.1 周三原始观测点（消除 ffill 重复值）
    out["rows"] = [
        {
            "date": d.date().isoformat(),
            "reserves_b": round(float(v) / 1000, 2) if pd.notna(v) else None,
        }
        for d, v in raw_res.tail(20).items()
    ]
    # 研判：四维交叉确认（SOFR−IORB + SOFR−3M T-Bill + SOFR−TGCR + EFFR−IORB）
    if s2 is not None and s1 is not None:
        tight = s2 > 3
        watch = 0 <= s2 <= 3
        mid_tight = s1 >= 15
        if tight and mid_tight:
            head, verdict = "双确认：准备金进入稀缺区间", "两个口径同时走扩，稀缺是确认状态而非噪声"
        elif tight or mid_tight:
            head = "单口径走扩，观察窗口"
            which = "SOFR−IORB" if tight else "SOFR−3M T-Bill"
            verdict = f"仅 {which} 越过偏紧线，等另一口径确认再升级判断"
        elif watch:
            head = f"融资面{_sofr_iorb_state(s2)}"
            if s_tgcr is not None and s_effr is not None and s_tgcr > 0 and s_effr < 0:
                verdict = (
                    f"SOFR 贴平 IORB 主因双边回购摩擦（SOFR−TGCR {s_tgcr:+.1f}bp），"
                    f"无担保 EFFR−IORB（{s_effr:+.1f}bp）仍在走廊下方，"
                    "属交易商资产负债表约束而非银行缺准备金"
                )
            else:
                verdict = "SOFR 贴近 IORB 但尚未突破 +3bp 偏紧线，处于观察窗口"
        else:
            head, verdict = "准备金充裕", "两个利差口径均未走扩"
        res_4w_n = _pct_chg(res, 28)
        basis = [
            f"SOFR−IORB {s2:+.1f}bp（{_sofr_iorb_state(s2)}）",
            f"SOFR−3M T-Bill {s1:+.1f}bp",
        ]
        if s_tgcr is not None:
            basis.append(f"SOFR−TGCR {s_tgcr:+.1f}bp")
        if s_effr is not None:
            basis.append(f"EFFR−IORB {s_effr:+.1f}bp")
        if res_4w_n is not None:
            basis.append(f"准备金 4 周 {res_4w_n:+.1f}%")
        out["judgement"] = {
            "title": "准备金研判",
            "text": f"{head}。\n" + " · ".join(basis) + f"。\n{verdict}。"
            "若 EFFR−IORB 同步转正且 SRF 出现实质动用（≥$50M），则确认准备金稀缺。",
        }
    return out


def page_global_dollar() -> dict:
    """子页：全球美元 — DXY / 央行互换 / 掉期点一览 + 压力读数。"""
    raw_liq = _csv(ROOT / "data" / "fred/liquidity/liquidity.csv")
    cfets = _csv(ROOT / "data" / "fred/liquidity/cfets_swap_points.csv")
    asset = _csv(ROOT / "data" / "yfinance/asset_prices.csv")
    out: dict = {"dxy": {}, "swap": {}, "pairs": {}}
    dxy = asset["DXY"].dropna() if not asset.empty and "DXY" in asset else pd.Series(dtype=float)
    if not dxy.empty:
        # 剔除周末/盘前相邻重复占位行，取最近两个真实交易日计算 1 日涨跌
        dxy_dedup = dxy.loc[dxy.diff().fillna(1.0) != 0]
        chg_1d = (
            float((dxy_dedup.iloc[-1] - dxy_dedup.iloc[-2]) / abs(dxy_dedup.iloc[-2]) * 100)
            if len(dxy_dedup) >= 2
            else _pct_chg(dxy, 1)
        )
        out["dxy"] = {"value": float(dxy.iloc[-1]), "chg_1d_pct": chg_1d}
    swpt = (
        raw_liq["SWPT"].dropna()
        if not raw_liq.empty and "SWPT" in raw_liq
        else pd.Series(dtype=float)
    )
    if not swpt.empty:
        out["swap"] = {
            "balance_m": round(float(swpt.iloc[-1]), 0),
            "date": swpt.index[-1].date().isoformat(),
            "chg_30d_m": round(_chg(swpt, 28) or 0, 0),
        }
    out["pairs"] = {_PAIR_DISPLAY.get(p, p): v for p, v in _offshore_detail(cfets).items()}
    out["score"] = _offshore_score(cfets)
    return out


def page_subsurface() -> dict:
    """子页：次表层 — SOFR 分位 / 成交量 z / SRF 激活 / 央行互换变化。"""
    rates = _csv(ROOT / "data" / "fred/rates/rates.csv")
    srf = _csv(ROOT / "data" / "fred/liquidity/srf.csv")
    raw_liq = _csv(ROOT / "data" / "fred/liquidity/liquidity.csv")
    out: dict = {
        "percentiles": {},
        "volume": {},
        "srf": {},
        "swap": {},
        "composite": {},
        "as_of": "",
    }
    if rates.empty:
        return out
    sofr_s = (
        pd.to_numeric(rates.get("SOFR"), errors="coerce").dropna()
        if rates.get("SOFR") is not None
        else pd.Series(dtype=float)
    )
    out["as_of"] = (
        sofr_s.index[-1].date().isoformat() if not sofr_s.empty else str(rates.index[-1])[:10]
    )
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
    tgcr = (
        _last(pd.to_numeric(rates.get("TGCR"), errors="coerce"))
        if rates.get("TGCR") is not None
        else None
    )
    effr = (
        _last(pd.to_numeric(rates.get("DFF"), errors="coerce"))
        if rates.get("DFF") is not None
        else None
    )
    out["percentiles"] = {
        "values": pct,
        "iorb": iorb,
        "tgcr": tgcr,
        "effr": effr,
        "sofr_tgcr_bp": _bp("SOFR", "TGCR", rates),
        "tgcr_iorb_bp": _bp("TGCR", "IORB", rates),
        "effr_iorb_bp": _bp("DFF", "IORB", rates),
    }
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
        act30, raw30 = _srf_window_counts(srf, 30)
        s = srf["SRF_USAGE"].dropna()
        out["srf"] = {
            "active_30d": act30,
            "raw_30d": raw30,
            "threshold_m": int(SRF_SUBSTANTIAL_B * 1000),
            "latest_m": round(float(s.iloc[-1]) * 1000, 0),
        }
    swpt = (
        raw_liq["SWPT"].dropna()
        if not raw_liq.empty and "SWPT" in raw_liq
        else pd.Series(dtype=float)
    )
    if not swpt.empty:
        out["swap"] = {
            "balance_m": round(float(swpt.iloc[-1]), 0),
            "chg_30d_m": round(_chg(swpt, 28) or 0, 0),
        }
    # 综合次表层压力：分项 raw 值 + 各自 max（展示 x/max），状态按加权和
    # SOFR 99 分位常态高出 IORB 5~8bp（季末/结算日可达 15~30bp），≥15bp 计满分 3，≥8bp 计 1.5
    tail_w = (
        _score((pct.get("99分位", 0) - (iorb or 0)) * 100, [(15, 3), (8, 1.5), (0, 0)])
        if iorb is not None and "99分位" in pct
        else 0
    )
    vol_z = _score(z, [(2, 2), (1, 1), (0, 0)]) if z is not None else 0
    # SRF 实质动用（≥$50M）：30 天内 ≥5 天计满分 3，≥2 天计 1.5
    srf_act = (
        _score((out["srf"].get("active_30d") or 0), [(5, 3), (2, 1.5), (0, 0)])
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
    # 研判：归因主导分项（页面只展示分数，不判断「谁在驱动」）
    drivers = {
        "尾部利率（SOFR 99 分位）": tail_w,
        "成交量异动": vol_z,
        "SRF 激活": srf_act,
        "央行互换变化": sw_chg,
    }
    top_name, top_v = max(drivers.items(), key=lambda kv: kv[1])
    if total == 0:
        out["judgement"] = {
            "title": "次表层研判",
            "text": "次表层正常。\n四个分项（尾部利率 / 成交量 / SRF / 央行互换）均未计分，"
            "未被均衡吸收的融资摩擦尚无积累迹象。\n任一分项开始计分即进入观察窗口，"
            "是主流指标确认之前的领先信号。",
        }
    else:
        tail_bp = None if iorb is None or "99分位" not in pct else (pct["99分位"] - iorb) * 100
        sub_msg = {
            "尾部利率（SOFR 99 分位）": (
                f"99 分位高出 IORB {tail_bp:+.1f}bp" if tail_bp is not None else "99 分位抬升"
            ),
            "成交量异动": f"60 日 z-score {z:+.2f}" if z is not None else "成交量偏离均值",
            "SRF 激活": (
                f"30 天激活 {out['srf'].get('active_30d', 0)} 天"
                if out.get("srf")
                else "SRF 已动用"
            ),
            "央行互换变化": (
                f"30 日变化 {out['swap'].get('chg_30d_m', 0):+,.0f}M"
                if out.get("swap")
                else "央行互换回升"
            ),
        }[top_name]
        mid = (
            "但幅度未达主流指标确认线，属于观察窗口"
            if total < 4
            else "已达承压档，需对照 LPI 融资确认层是否同步抬升"
        )
        tail = (
            "若主导分项继续抬升且 LPI 同步上行，则压力在从摩擦向系统性传导。"
            if state != "正常"
            else "分项回落则视为噪声，不升级判断。"
        )
        out["judgement"] = {
            "title": "次表层研判",
            "text": (
                f"次表层压力{state}（{total}/10）。\n主导分项是{top_name}（{sub_msg}），{mid}。\n{tail}"
            ),
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

    parser = argparse.ArgumentParser(description="流动性分析：默认打印自检；--snapshot 存 LPI 快照")
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
        json.dumps(out["snapshot"]["evaluation"]["net_liquidity"], ensure_ascii=False)[:200],
    )
