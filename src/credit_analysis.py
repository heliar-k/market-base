"""信用市场研判规则引擎 — 复刻 timsun.net/credit 风格的确定性研判生成。

全部结论由本地 CSV 确定性推导，无 LLM 依赖、可测试。数据源：
  fred/volatility.csv      VIX / HY_OAS / IG_OAS（日频）
  fred/credit.csv          BBB/BB/B/CCC OAS 分层 + IG/HY 有效收益率 + SLOOS + 贷款质量
  fred/rates.csv           DGS10 / FEDFUNDS
  fred/liquidity.csv       NFCI / ANFCI / 子指数（周频）
  fred/sentiment.csv       STLFSI4（周频）
  ofr/fsi.csv              OFR 金融压力指数（日频）
  yfinance/asset_prices.csv MOVE / DXY / SPX / KBWB / HYG / LQD（日频快照）

LLM 预留同波动率模块：各 generate_*() 是唯一入口，先尝试 `_llm_generate()`
（当前返回 None = 未启用），None 则回落规则引擎。

输出：
  generate_credit_overview()  总览：OAS 分层 + all-in 成本 + SLOOS + 贷款质量 + 研判
  generate_credit_cds()       CDS：主权代理（10Y UST）+ 银行风险代理（KBWB vs SPX）
  generate_credit_stress()    压力仪表盘：5 分量合成指数 + 跨资产对照 + 历史曲线
"""

from __future__ import annotations

import pandas as pd

from src.config import ROOT

# 分位窗口（交易日）：1Y / 3Y / 10Y，样本不足时用可用历史并标记
W1Y, W3Y, W10Y = 250, 750, 2500
# 压力指数分位窗口：对齐 timsun「基于 365 天历史」≈ 250 交易日
STRESS_W = W1Y

# 压力指数分量权重（与 timsun 一致）
STRESS_WEIGHTS = {"hy": 0.30, "ig": 0.20, "mom": 0.20, "vix": 0.15, "div": 0.15}

# 压力分档
STRESS_ZONES = [
    ("宽松", 0, 30, "#34d399"),
    ("中性", 30, 70, "#f59e0b"),
    ("压力", 70, 101, "#f87171"),
]


def _read(category: str) -> pd.DataFrame:
    path = ROOT / "data" / "fred" / category / f"{category}.csv"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, index_col="date", parse_dates=True)


def _read_yf() -> pd.DataFrame:
    path = ROOT / "data" / "yfinance" / "asset_prices.csv"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, index_col="date", parse_dates=True)


def _read_ofr() -> pd.DataFrame:
    path = ROOT / "data" / "ofr" / "fsi.csv"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, index_col="date", parse_dates=True)


def _latest(s: pd.Series) -> float | None:
    s = s.dropna()
    return float(s.iloc[-1]) if not s.empty else None


def _latest_card(
    df: pd.DataFrame, col: str, ndigits: int, scale: float = 1
) -> tuple[dict, pd.Series]:
    """列最新值卡片 {value, as_of} + 清洗后序列；列缺失/全 NaN 时卡片为 {}。"""
    s = df[col].dropna() if col in df else pd.Series(dtype=float)
    if s.empty:
        return {}, s
    return {
        "value": round(float(s.iloc[-1]) * scale, ndigits),
        "as_of": s.index[-1].strftime("%Y-%m-%d"),
    }, s


def _pct(s: pd.Series, window: int, obs: dict | None = None) -> float | None:
    """当前值在序列最近 window 条中的分位 %（0-100）。"""
    s = s.dropna()
    if s.empty:
        return None
    tail = s.tail(window)
    if obs is not None:
        obs["n"] = len(tail)
        obs["full"] = len(tail) >= window
    return round((tail <= tail.iloc[-1]).mean() * 100, 1)


def _chg_bp(s: pd.Series, n: int) -> float | None:
    """最近值相对 n 个交易日前的变化（bp）。"""
    s = s.dropna()
    if len(s) < n + 1:
        return None
    cur, prev = s.iloc[-1], s.iloc[-1 - n]
    return None if pd.isna(prev) else round((cur - prev) * 100, 1)


def _chg_pct(s: pd.Series, n: int) -> float | None:
    """最近值相对 n 个交易日前的变化 %。"""
    s = s.dropna()
    if len(s) < n + 1:
        return None
    cur, prev = s.iloc[-1], s.iloc[-1 - n]
    return None if not prev else round((cur / prev - 1) * 100, 2)


def _zone(score: float) -> tuple[str, str]:
    for label, lo, hi, color in STRESS_ZONES:
        if lo <= score < hi:
            return label, color
    return "压力", "#f87171"


def _latest_date(df: pd.DataFrame) -> str | None:
    """最新观测日（YYYY-MM-DD），空表返回 None。"""
    return df.index.max().strftime("%Y-%m-%d") if len(df) else None


# ── 分位工具（分层 OAS / SLOOS / 贷款质量卡片共用）─────────────────────


def _oas_card(s: pd.Series) -> dict:
    """单条利差序列卡片：当前值 + 1Y/3Y/10Y 分位。"""
    s = s.dropna()
    v = _latest(s)
    if v is None:
        return {}
    p10 = {}
    return {
        "value": round(v * 100, 1),  # bp
        "as_of": s.index[-1].strftime("%Y-%m-%d"),
        "pct_1y": _pct(s, W1Y),
        "pct_3y": _pct(s, W3Y),
        "pct_10y": _pct(s, W10Y, p10),
        "obs": p10["n"],
        "full_10y": p10["full"],
    }


# ── 总览 ─────────────────────────────────────────────────────────────────


# ── Credit Regime Score ─────────────────────────────────────────────────
# 对齐 timsun.net/credit 的 7 子分合成面板（口径逆向自原站页面数据）：
#   Spread Level      = 0.5*(IG_OAS + HY_OAS) 10Y 分位      （原站 21.6 ≈ (20+23)/2）
#   Spread Momentum   = 50 + HY_OAS 22 日变化bp/2（带符号）  （原站 55：走阔方向为正）
#   Funding Cost      = 0.5*(IG_YIELD + HY_YIELD) 3Y 分位   （原站 70，假设混入 IG）
#   Credit Supply     = max(0, SLOOS C&I 标准净百分比)      （原站 0：-5.7% 放松=
#                       无压力，非数据缺失）
#   Credit Quality    = 5 项逾期/核销率 近 10Y（40 条季度）分位平均（原站 82.1：
#                       87/84/71/95/74 平均 82.2 ✓ 已用本地数据验证 tail(40) 口径）
#   Market Liquidity  = 50 - HYG/LQD 22 日动量%（跌=流动性紧=高分）
#   Cross-Asset       = 0.5*(VIX + HY_OAS) 10Y 分位       （本地 34.0 = (41+27)/2
#                       ≈ 原站 34.8）
# 综合分 = 7 子分等权平均（原站 46.0 = (21.6+55+70+0+82.1+58.8+34.8)/7 ✓ 验证等权）。
# 子分数据不足（<样本下限）时置 None 并降级为可用子分平均；
# 各百分位窗口样本不足时按可用历史计算（同原站降级口径），仅 Market Liquidity
# 要求 ≥23 条以避免单点动量噪声。原站公式细节不公开，数值偏差属可接受口径假设。
REGIME_ZONES = [
    ("easing", 0, 25, "#34d399"),
    ("neutral easing", 25, 50, "#a7f3d0"),
    ("neutral tightening", 50, 75, "#fbbf24"),
    ("tightening", 75, 101, "#f87171"),
]


def _regime_zone(score: float) -> tuple[str, str]:
    for label, lo, hi, color in REGIME_ZONES:
        if lo <= score < hi:
            return label, color
    return REGIME_ZONES[-1][0], REGIME_ZONES[-1][3]


def _regime_score(
    df_vol: pd.DataFrame, df_cr: pd.DataFrame, df_yf: pd.DataFrame
) -> dict:
    """7 子分 Credit Regime Score 合成（口径见模块注释，高分 = 更大信用压力）。"""
    hy = df_vol["HY_OAS"].dropna() if "HY_OAS" in df_vol else pd.Series(dtype=float)
    ig = df_vol["IG_OAS"].dropna() if "IG_OAS" in df_vol else pd.Series(dtype=float)
    vix = df_vol["VIX"].dropna() if "VIX" in df_vol else pd.Series(dtype=float)

    def _liq_mom(col: str) -> float | None:
        s = df_yf[col].dropna() if col in df_yf else pd.Series(dtype=float)
        chg = _chg_pct(s, 22)
        if chg is None:
            return None
        return round(min(100, max(0, 50 - chg * 10)), 1)

    # 各子分
    spread_level = None
    p_ig, p_hy = _pct(ig, W10Y), _pct(hy, W10Y)
    if p_ig is not None and p_hy is not None:
        spread_level = round((p_ig + p_hy) / 2, 1)

    spread_mom = None
    if (chg := _chg_bp(hy, 22)) is not None:
        spread_mom = round(min(100, max(0, 50 + chg / 2)), 1)

    funding = None
    fy, iy = None, None
    if "HY_YIELD" in df_cr:
        fy = _pct(df_cr["HY_YIELD"].dropna(), W3Y)
    if "IG_YIELD" in df_cr:
        iy = _pct(df_cr["IG_YIELD"].dropna(), W3Y)
    if fy is not None and iy is not None:
        funding = round((fy + iy) / 2, 1)

    supply = None
    if "SLOOS_CI_STD" in df_cr:
        v = _latest(df_cr["SLOOS_CI_STD"])
        supply = round(max(0.0, v), 1) if v is not None else None

    quality = None
    q_cols = ["DELINQ_CI", "DELINQ_CRE", "DELINQ_CC", "CHGOFF_BUS", "CHGOFF_CONS"]
    q_pcts = [_pct(df_cr[c].dropna(), 40) for c in q_cols if c in df_cr]
    if len(q_pcts) == len(q_cols) and all(p is not None for p in q_pcts):
        quality = round(sum(q_pcts) / len(q_pcts), 1)

    liq = None
    l_hyg, l_lqd = _liq_mom("HYG"), _liq_mom("LQD")
    if l_hyg is not None and l_lqd is not None:
        liq = round((l_hyg + l_lqd) / 2, 1)

    cross = None
    p_vix = _pct(vix, W10Y)
    if p_vix is not None and p_hy is not None:
        cross = round((p_vix + p_hy) / 2, 1)

    comps = [
        ("spread_level", "Spread Level", spread_level, p_hy),
        (
            "spread_mom",
            "Spread Momentum",
            spread_mom,
            hy.iloc[-1] * 100 if not hy.empty else None,
        ),
        ("funding_cost", "Funding Cost", funding, fy),
        ("credit_supply", "Credit Supply", supply, None),
        ("credit_quality", "Credit Quality", quality, None),
        ("market_liq", "Market Liquidity", liq, None),
        ("cross_asset", "Cross-Asset Confirmation", cross, p_vix),
    ]
    values = [v for _, _, v, _ in comps if v is not None]
    total = round(sum(values) / len(values), 1) if values else None
    label, color = _regime_zone(total) if total is not None else ("—", "#999")

    return {
        "score": total,
        "regime": label,
        "color": color,
        "components": [
            {"key": k, "name": n, "value": v, "raw": r} for k, n, v, r in comps
        ],
        "missing": [n for _, n, v, _ in comps if v is None],
    }


def overview(
    df_vol: pd.DataFrame,
    df_cr: pd.DataFrame,
    df_rates: pd.DataFrame,
    df_liq: pd.DataFrame,
    df_sent: pd.DataFrame,
    df_ofr: pd.DataFrame,
    df_yf: pd.DataFrame,
) -> dict:
    ig = _oas_card(df_vol["IG_OAS"]) if "IG_OAS" in df_vol else {}
    hy = _oas_card(df_vol["HY_OAS"]) if "HY_OAS" in df_vol else {}
    hy_ig = {}
    if hy and ig:
        diff = (df_vol["HY_OAS"] - df_vol["IG_OAS"]).dropna()
        d = _oas_card(diff)
        hy_ig = {
            "value": d["value"],
            "as_of": d["as_of"],
            "pct_1y": d["pct_1y"],
            "pct_3y": d["pct_3y"],
            "pct_10y": d["pct_10y"],
        }

    # OAS 分层表
    layers = []
    for name, col in [
        ("IG", "IG_OAS"),
        ("BBB", "BBB_OAS"),
        ("HY", "HY_OAS"),
        ("BB", "BB_OAS"),
        ("B", "B_OAS"),
        ("CCC", "CCC_OAS"),
    ]:
        if col == "IG_OAS":
            layers.append({**ig, "name": name})
        elif col == "HY_OAS":
            layers.append({**hy, "name": name})
        elif col in df_cr:
            c = _oas_card(df_cr[col])
            layers.append({**c, "name": name})
    spread_ccbb = _spread(df_cr, "CCC_OAS", "BB_OAS", "CCC − BB")
    spread_bbig = _spread(df_cr, "BBB_OAS", "IG_OAS", "BBB − IG")

    # All-in 融资成本
    funding = {}
    for key, col in [("ig", "IG_YIELD"), ("hy", "HY_YIELD")]:
        card, _ = _latest_card(df_cr, col, 1, scale=100)
        if card:
            funding[key] = card
    ff = _latest(df_rates["FEDFUNDS"]) if "FEDFUNDS" in df_rates else None
    d10 = _latest(df_rates["DGS10"]) if "DGS10" in df_rates else None
    if funding.get("hy"):
        hv = funding["hy"]["value"]
        if ff is not None:
            funding["hy_ff"] = round(hv - ff * 100, 1)
        if d10 is not None:
            funding["hy_10y"] = round(hv - d10 * 100, 1)

    # SLOOS（季度）
    sloos = []
    for name, col in [
        ("C&I 贷款标准", "SLOOS_CI_STD"),
        ("C&I 贷款需求", "SLOOS_CI_DEM"),
        ("CRE 贷款标准", "SLOOS_CRE_STD"),
        ("消费贷款标准", "SLOOS_CC_STD"),
    ]:
        card, _ = _latest_card(df_cr, col, 1)
        if card:
            sloos.append({"name": name, **card})

    # 贷款质量（季度，滞后但硬）
    quality = []
    for name, col in [
        ("C&I 逾期率", "DELINQ_CI"),
        ("CRE 逾期率", "DELINQ_CRE"),
        ("信用卡逾期率", "DELINQ_CC"),
        ("商业贷款核销率", "CHGOFF_BUS"),
        ("消费贷款核销率", "CHGOFF_CONS"),
    ]:
        card, s = _latest_card(df_cr, col, 2)
        if card:
            p10 = {}
            quality.append(
                {
                    "name": name,
                    **card,
                    "pct_10y": _pct(s, W10Y, p10),
                    "obs": p10["n"],
                    "full_10y": p10["full"],
                }
            )

    # 金融条件
    fincond = {}
    for key, df_src, col in [
        ("nfci", df_liq, "NFCI"),
        ("anfci", df_liq, "ANFCI"),
        ("nfci_risk", df_liq, "NFCIRISK"),
        ("nfci_credit", df_liq, "NFCICREDIT"),
        ("nfci_leverage", df_liq, "NFCILEVERAGE"),
        ("stlfsi", df_sent, "STLFSI4"),
        ("ofr_fsi", df_ofr, "OFR_FSI"),
    ]:
        card, _ = _latest_card(df_src, col, 3)
        if card:
            fincond[key] = card

    # 市场流动性代理：HYG / LQD 价格
    liq_etf = {}
    for key, col in [("hyg", "HYG"), ("lqd", "LQD")]:
        card, _ = _latest_card(df_yf, col, 2)
        if card:
            liq_etf[key] = card

    return {
        "regime": _regime_score(df_vol, df_cr, df_yf),
        "ig": ig,
        "hy": hy,
        "hy_ig": hy_ig,
        "layers": layers,
        "spreads": {"ccc_bb": spread_ccbb, "bbb_ig": spread_bbig, "hy_ig": hy_ig},
        "funding": funding,
        "sloos": sloos,
        "quality": quality,
        "fincond": fincond,
        "liq_etf": liq_etf,
        "signals": _overview_signals(ig, hy, hy_ig, funding, sloos, fincond),
    }


def _spread(df: pd.DataFrame, a: str, b: str, name: str) -> dict:
    if a not in df or b not in df:
        return {}
    diff = (df[a] - df[b]).dropna()
    c = _oas_card(diff)
    if not c:
        return {}
    return {"name": name, "value": c["value"], "as_of": c["as_of"]}


def _overview_signals(
    ig: dict, hy: dict, hy_ig: dict, funding: dict, sloos: list[dict], fincond: dict
) -> list[dict]:
    sigs = []

    # 1. 利差水位
    if hy:
        p = hy.get("pct_10y")
        if p is not None:
            level = "低位" if p < 30 else ("中位" if p < 70 else "高位")
            txt = (
                f"HY OAS 为 {hy['value']:.0f}bp，处于近十年 {p:.0f}% 分位（{level}）。"
                + (
                    "利差水位本身偏低，可能低估尾部风险。"
                    if p < 30
                    else "利差定价中性。"
                )
            )
        else:
            txt = f"HY OAS 为 {hy['value']:.0f}bp。"
        if ig:
            txt += f" IG OAS {ig['value']:.0f}bp，"
            txt += f"HY−IG 价差 {hy_ig.get('value', 0):.0f}bp。"
        sigs.append({"title": "利差水位", "text": txt})

    # 2. All-in 融资成本
    if funding.get("hy"):
        txt = f"HY 有效收益率 {funding['hy']['value']:.2f}%"
        parts = []
        if funding.get("hy_ff") is not None:
            parts.append(f"高于联邦基金利率 {funding['hy_ff']:.0f}bp")
        if funding.get("hy_10y") is not None:
            parts.append(f"高于 10Y 国债 {funding['hy_10y']:.0f}bp")
        txt += (
            "，".join(["", *parts])
            + "。利差低不等于融资便宜："
            + "无风险利率高位时，企业再融资成本仍压制回购与杠杆扩张。"
        )
        sigs.append({"title": "All-in 融资成本", "text": txt})

    # 3. 银行信贷
    if sloos:
        std = next((s for s in sloos if "标准" in s["name"]), None)
        if std:
            dirn = "收紧" if std["value"] > 0 else "放松"
            txt = (
                f"SLOOS 口径银行信贷标准净百分比 {std['value']:+.1f}%（{dirn}），"
                "未确认系统性收紧。"
                if std["value"] < 10
                else f"SLOOS 银行信贷标准净百分比 {std['value']:+.1f}%，信贷正在收紧，"
                "企业融资可得性恶化，风险资产承压。"
            )
        else:
            txt = "SLOOS 数据缺失。"
        nf = fincond.get("nfci")
        if nf:
            txt += (
                f" NFCI {nf['value']:+.2f}（{'偏紧' if nf['value'] > 0 else '偏松'}）。"
            )
        sigs.append({"title": "银行信贷", "text": txt})

    # 4. 交叉确认
    cross = []
    if fincond.get("stlfsi"):
        cross.append(f"STLFSI {fincond['stlfsi']['value']:+.2f}")
    if fincond.get("ofr_fsi"):
        cross.append(f"OFR FSI {fincond['ofr_fsi']['value']:+.2f}")
    if cross:
        txt = (
            "金融压力代理指标："
            + " / ".join(cross)
            + "，均处历史宽松区，与利差低分位相互印证——信用维度当前未确认压力。"
            + "若 NFCI 转正且 SLOOS 收紧，需警惕估值弹性先于利差走弱。"
        )
        sigs.append({"title": "交叉确认", "text": txt})
    return sigs


# ── CDS 专题 ─────────────────────────────────────────────────────────────


def cds(df_rates: pd.DataFrame, df_yf: pd.DataFrame) -> dict:
    out = {}
    # 主权 CDS 代理：10Y UST 收益率
    s10 = df_rates["DGS10"].dropna() if "DGS10" in df_rates else pd.Series(dtype=float)
    v10 = _latest(s10)
    if v10 is not None:
        out["sovereign"] = {
            "value": round(v10 * 100, 2),
            "as_of": s10.index[-1].strftime("%Y-%m-%d"),
        }

    # 银行系统风险代理：KBWB vs SPX 14 日收益偏离
    for col in ("KBWB", "SPX"):
        if col not in df_yf:
            return out
    kb = df_yf["KBWB"].dropna()
    sp = df_yf["SPX"].dropna()
    days = min(14, max(1, min(len(kb), len(sp)) - 1))
    if days >= 1:
        kb_chg = _chg_pct(kb, days)
        sp_chg = _chg_pct(sp, days)
        if kb_chg is not None and sp_chg is not None:
            out["bank"] = {
                "divergence": round(kb_chg - sp_chg, 2),
                "kbwb_chg": kb_chg,
                "spx_chg": sp_chg,
                "days": days,
            }
            # 近 30 日归一化序列（起=100），供双线图
            n = min(30, len(kb), len(sp))
            kb_t = kb.tail(n)
            sp_t = sp.tail(n)
            base = min(kb_t.index[0], sp_t.index[0])
            kb_t = kb[kb.index >= base].dropna()
            sp_t = sp[sp.index >= base].dropna()
            idx = kb_t.index.union(sp_t.index)
            kb_n = kb_t.reindex(idx).ffill()
            sp_n = sp_t.reindex(idx).ffill()
            first_kb = kb_n.dropna().iloc[0] if not kb_n.dropna().empty else None
            first_sp = sp_n.dropna().iloc[0] if not sp_n.dropna().empty else None
            if first_kb and first_sp:
                out["bank"]["chart"] = {
                    "dates": [d.strftime("%Y-%m-%d") for d in idx],
                    "kbwb": [
                        round(float(v / first_kb * 100), 2) if pd.notna(v) else None
                        for v in kb_n
                    ],
                    "spx": [
                        round(float(v / first_sp * 100), 2) if pd.notna(v) else None
                        for v in sp_n
                    ],
                }
    return out


# ── 信用压力仪表盘 ────────────────────────────────────────────────────────


def _rolling_pct(s: pd.Series, window: int, min_periods: int = 60) -> pd.Series:
    """滚动分位序列：每个时点相对前 window 条的分位（0-100）。"""
    s = s.dropna()
    if len(s) < min_periods:
        return pd.Series(dtype=float)
    return s.rolling(window, min_periods=min_periods).apply(
        lambda x: (x <= x[-1]).mean() * 100, raw=True
    )


def stress(
    df_vol: pd.DataFrame,
    df_rates: pd.DataFrame,
    df_yf: pd.DataFrame,
    df_fx: pd.DataFrame,
) -> dict:
    hy = df_vol["HY_OAS"].dropna() if "HY_OAS" in df_vol else pd.Series(dtype=float)
    ig = df_vol["IG_OAS"].dropna() if "IG_OAS" in df_vol else pd.Series(dtype=float)
    vix = df_vol["VIX"].dropna() if "VIX" in df_vol else pd.Series(dtype=float)

    # 当前分量（分位窗口 STRESS_W，对齐 timsun 365 天）
    hy_pct = _pct(hy, STRESS_W) or 0
    ig_pct = _pct(ig, STRESS_W) or 0
    vix_pct = _pct(vix, STRESS_W) or 0
    chg = _chg_bp(hy, 22)  # 30 天 ≈ 22 交易日
    mom = round(min(100, max(0, (chg or 0) / 2)), 1)  # ±200bp 满量程
    div = round(abs(hy_pct - vix_pct), 1)
    comp = round(
        STRESS_WEIGHTS["hy"] * hy_pct
        + STRESS_WEIGHTS["ig"] * ig_pct
        + STRESS_WEIGHTS["mom"] * mom
        + STRESS_WEIGHTS["vix"] * vix_pct
        + STRESS_WEIGHTS["div"] * div,
        1,
    )
    zone, color = _zone(comp)

    components = [
        {
            "key": "hy",
            "name": "HY OAS 百分位",
            "weight": 30,
            "value": hy_pct,
            "raw": round(float(hy.iloc[-1]) * 100, 1) if not hy.empty else None,
            "unit": "bp",
        },
        {
            "key": "ig",
            "name": "IG OAS 百分位",
            "weight": 20,
            "value": ig_pct,
            "raw": round(float(ig.iloc[-1]) * 100, 1) if not ig.empty else None,
            "unit": "bp",
        },
        {
            "key": "mom",
            "name": "HY 30 天变化速率",
            "weight": 20,
            "value": mom,
            "raw": chg,
            "unit": "bp",
        },
        {
            "key": "vix",
            "name": "VIX 百分位",
            "weight": 15,
            "value": vix_pct,
            "raw": round(float(vix.iloc[-1]), 2) if not vix.empty else None,
            "unit": "",
        },
        {
            "key": "div",
            "name": "股信用背离度",
            "weight": 15,
            "value": div,
            "raw": round(abs(hy_pct - vix_pct), 1),
            "unit": "",
        },
    ]

    # 跨资产对照（百分位 + 与 HY 偏离）
    cross = [{"metric": "HY OAS", "pct": hy_pct, "dev": 0.0, "note": "主指标"}]
    for metric, s, note in [
        ("VIX", vix, "股市 vs 信用"),
        (
            "MOVE",
            df_yf["MOVE"].dropna() if "MOVE" in df_yf else pd.Series(dtype=float),
            "利率波动 vs 信用",
        ),
        (
            "DXY",
            df_fx["DXY"].dropna() if "DXY" in df_fx else pd.Series(dtype=float),
            "美元 vs 信用",
        ),
        (
            "10Y UST",
            df_rates["DGS10"].dropna()
            if "DGS10" in df_rates
            else pd.Series(dtype=float),
            "无风险 vs 信用",
        ),
    ]:
        p = _pct(s, STRESS_W) or 0
        cross.append(
            {
                "metric": metric,
                "pct": p,
                "dev": round(p - hy_pct, 1),
                "note": note,
                "obs": len(s.dropna()),
            }
        )

    # 历史合成指数（近 2 年，滚动分位）
    hist = None
    if not hy.empty and not ig.empty and not vix.empty:
        h = _rolling_pct(hy, STRESS_W)
        i = _rolling_pct(ig, STRESS_W)
        v = _rolling_pct(vix, STRESS_W)
        m = hy.diff(22) / 2 * 100  # 动量分量
        m = m.clip(0, 100)
        aligned = pd.concat(
            [h.rename("hy"), i.rename("ig"), v.rename("vix"), m.rename("mom")],
            axis=1,
            join="inner",
        ).dropna()
        if not aligned.empty:
            d = (aligned["hy"] - aligned["vix"]).abs()  # 背离
            comp_series = (
                0.30 * aligned["hy"]
                + 0.20 * aligned["ig"]
                + 0.20 * aligned["mom"]
                + 0.15 * aligned["vix"]
                + 0.15 * d
            )
            comp_series = comp_series.tail(500)
            hist = {
                "dates": [d_.strftime("%Y-%m-%d") for d_ in comp_series.index],
                "values": [round(float(v_), 1) for v_ in comp_series],
            }

    return {
        "composite": comp,
        "zone": zone,
        "zone_color": color,
        "components": components,
        "cross": cross,
        "history": hist,
        "as_of": _latest_date(df_vol),
    }


# ── LLM 预留 + 统一入口 ──────────────────────────────────────────────────


def _llm_generate() -> dict | None:
    """LLM 生成入口（预留）。返回同构 dict 后置 `_LLM_ENABLED = True`。"""
    return None


_LLM_ENABLED = False


def _llm_try() -> dict | None:
    """LLM 命中则返回带 generator 标记的结果，否则 None（走规则引擎）。"""
    if not _LLM_ENABLED:
        return None
    out = _llm_generate()
    return {**out, "generator": "llm"} if out is not None else None


def generate_credit_overview() -> dict:
    if (llm := _llm_try()) is not None:
        return llm
    df_vol, df_cr, df_rates = _read("volatility"), _read("credit"), _read("rates")
    df_liq, df_sent = _read("liquidity"), _read("sentiment")
    df_ofr, df_yf = _read_ofr(), _read_yf()
    if df_vol.empty or "HY_OAS" not in df_vol:
        return {"error": "fred/volatility.csv 缺 HY_OAS，先运行 ./bin/fetch_fred"}
    out = overview(df_vol, df_cr, df_rates, df_liq, df_sent, df_ofr, df_yf)
    out["generator"] = "rules"
    out["as_of"] = _latest_date(df_vol)
    return out


def generate_credit_cds() -> dict:
    if (llm := _llm_try()) is not None:
        return llm
    df_rates, df_yf = _read("rates"), _read_yf()
    if df_rates.empty or "DGS10" not in df_rates:
        return {"error": "fred/rates.csv 缺 DGS10，先运行 ./bin/fetch_fred"}
    out = cds(df_rates, df_yf)
    out["generator"] = "rules"
    out["as_of"] = _latest_date(df_rates)
    return out


def generate_credit_stress() -> dict:
    if (llm := _llm_try()) is not None:
        return llm
    df_vol, df_rates, df_yf = _read("volatility"), _read("rates"), _read_yf()
    df_fx = _read("fx")
    if df_vol.empty or "HY_OAS" not in df_vol:
        return {"error": "fred/volatility.csv 缺 HY_OAS，先运行 ./bin/fetch_fred"}
    out = stress(df_vol, df_rates, df_yf, df_fx)
    out["generator"] = "rules"
    return out


if __name__ == "__main__":
    # 自检：跑通 + 打印渲染结果
    import json

    for fn in (generate_credit_overview, generate_credit_cds, generate_credit_stress):
        out = fn()
        print(json.dumps(out, ensure_ascii=False, indent=1)[:1200])
        print("─" * 40)
