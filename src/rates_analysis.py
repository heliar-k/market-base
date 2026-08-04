"""利率研判规则引擎 — 复刻 timsun.net/rates 风格的确定性研判生成。

规则引擎先行：全部结论由本地 CSV（FRED rates/tips/inflation + rate_expectations
+ treasury auctions）确定性推导，无 LLM 依赖、可测试。

LLM 预留：`generate_analysis()` 是唯一入口，内部先尝试 `_llm_generate()`（当前
返回 None = 未启用），None 则回落规则引擎。接好 LLM 后只需实现 `_llm_generate()`
返回同构 dict，调用方（server.py /api/rates/analysis）零改动。

输出结构（两段）：
  yield_curve: 曲线形态/变动驱动/关键利差/验证指标/交易含义/失效条件/时间框架
  overview:    利率研判四段文（曲线形态/实际利率/联储预期/展望）
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd

from src.analysis_utils import read_csv_or_empty as _read
from src.config import ROOT, TERM_SERIES

TENORS = ["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"]

# 时间窗（天）：当前 / 1周前 / 1月前 / 3月前
_WINDOWS = {"current": 0, "1w": 7, "1m": 30, "3m": 90}

_BP = 100  # 百分数 → bp


# ═══════════════════════════════════════════════════════════════════════════════
# 数据装载
# ═══════════════════════════════════════════════════════════════════════════════


def _load() -> tuple[
    pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame
]:
    """rates / tips / inflation / rate_expectations / auction_results / cgb
    六张本地表。"""
    rates = _read(ROOT / "data" / "fred" / "rates" / "rates.csv")
    tips = _read(ROOT / "data" / "fred" / "tips" / "tips.csv")
    infl = _read(ROOT / "data" / "fred" / "inflation" / "inflation.csv")
    rex = _read(ROOT / "data" / "rate_expectations" / "fomc_probabilities.csv")
    auc = _read(ROOT / "data" / "treasury" / "auction_results.csv", "auction_date")
    cgb = _read(ROOT / "data" / "fred" / "rates" / "cgb.csv")  # chinamoney，FRED 无
    return rates, tips, infl, rex, auc, cgb


def _snapshot(rates: pd.DataFrame, col: str, window: int) -> float | None:
    """col 序列在 last_date − window 天前最近一个非空值。"""
    s = rates[col].dropna()
    if s.empty:
        return None
    last = s.index[-1]
    cutoff = last - timedelta(days=window)
    if window == 0:
        return float(s.iloc[-1])
    prev = s[s.index <= cutoff]
    return float(prev.iloc[-1]) if not prev.empty else None


def _bp_change(rates: pd.DataFrame, col: str, window: int) -> float | None:
    cur, prev = _snapshot(rates, col, 0), _snapshot(rates, col, window)
    if cur is None or prev is None:
        return None
    return round((cur - prev) * _BP, 1)


def _spread_vals(va: float | None, vb: float | None) -> float | None:
    """利差核心：va − vb（bp）；任一侧缺失返回 None，不伪造 0。"""
    if va is None or vb is None:
        return None
    return round((va - vb) * _BP, 1)


def _spread(rates: pd.DataFrame, a: str, b: str, window: int = 0) -> float | None:
    va, vb = _snapshot(rates, a, window), _snapshot(rates, b, window)
    return _spread_vals(va, vb)


def _spread_vs_us(local: float | None, us: float | None) -> float | None:
    """该市场相对美国的利差（bp）。约定 美国 − 该市场（与 timsun 符号一致，
    审计 P1-④）；复用 _spread_vals 核心公式（审计 D-5），缺失不伪造 0。"""
    return _spread_vals(us, local)


# ═══════════════════════════════════════════════════════════════════════════════
# 规则：收益率曲线解读
# ═══════════════════════════════════════════════════════════════════════════════


def _shape_label(s2s10_1m: float | None, y10_1m: float | None) -> str:
    """按 1 月变化判定曲线形态：熊陡/牛陡/熊平/牛平/走平。"""
    if s2s10_1m is None or y10_1m is None:
        return "数据不足"
    steep = s2s10_1m > 8  # 2s10s 走扩 > 8bp
    flat = s2s10_1m < -8
    if steep:
        return "熊陡" if y10_1m > 0 else "牛陡"
    if flat:
        return "熊平" if y10_1m > 0 else "牛平"
    return "走平"


def _spread_note(spread: float | None, name: str) -> str:
    if spread is None:
        return f"{name} 数据缺失"
    sign = "正" if spread > 0 else "负"
    if name == "2s10s":
        if spread > 0:
            return f"{name} {spread:+.0f}bp → {sign}利差，衰退概率低、软着陆预期"
        return f"{name} {spread:+.0f}bp → 倒挂，衰退预警信号"
    if name == "3m10s":
        if spread > 0:
            return f"{name} {spread:+.0f}bp → 政策利率或已偏紧，市场计入降息预期"
        return f"{name} {spread:+.0f}bp → 政策利率偏低，加息预期"
    # 5s30s
    if spread > 30:
        return f"{name} {spread:+.0f}bp → 超长端期限溢价高企，供给担忧明显"
    return f"{name} {spread:+.0f}bp → 超长端补偿正常"


def _coupon_cover(auc: pd.DataFrame) -> float | None:
    """近 10 场付息券平均 Bid-to-Cover；无数据返回 None。"""
    if auc.empty:
        return None
    coupon = auc[auc["security_type"] != "Bill"]
    cover = (
        pd.to_numeric(coupon["bid_to_cover_ratio"], errors="coerce").dropna().tail(10)
    )
    return float(cover.mean()) if not cover.empty else None


def _breakeven(nominal: float | None, real: float | None) -> float | None:
    """盈亏平衡通胀 = 名义 − 实际；任一缺失返回 None（不伪造 0）。"""
    if nominal is None or real is None:
        return None
    return round(nominal - real, 2)


def _time_frame(shape: str, s2s10_1m: float | None) -> str:
    """时间框架：利差快速变动时窗口缩短，平稳时放宽。"""
    if shape == "走平":
        return "未来 15-20 个交易日（等待方向确认）"
    if s2s10_1m is not None and abs(s2s10_1m) > 15:
        return "未来 15-20 个交易日"
    return "未来 30 个交易日"


def _trade_implications(shape: str, s2s10: float | None) -> list[str]:
    table = {
        "熊陡": [
            "做陡 2s10s：买入 2Y 国债/期货（ZT/ZF），卖出 10Y（ZN），博利差继续走扩",
            "做空超长端：卖出 TLT 或 30Y 期货（ZB），捕捉期限溢价上行",
            "短端受益于降息预期：持有/买入 SHY 或 2Y 现券",
        ],
        "牛陡": [
            "做陡 2s10s：利差走扩中长端弹性更大，卖出 2Y 买入 10Y",
            "做多长端：TLT/EDV 直接受益于长端收益率下行",
        ],
        "熊平": [
            "做平 2s10s：卖出 2Y 买入 10Y，利差收窄交易",
            "长端承压：回避或做空 TLT",
        ],
        "牛平": [
            "做平 2s10s：买入 2Y 卖出 10Y，利差收窄交易",
            "短端受益最大：做多 2Y/SHY",
        ],
        "走平": ["形态信号中性，等待 2s10s 突破 ±8bp 再建仓"],
    }
    trades = table.get(shape, ["形态信号中性，等待方向明确"])
    if s2s10 is not None:
        if shape in ("熊陡", "牛陡"):
            trades.append(
                f"目标/止损：2s10s 走扩至 {s2s10 + 15:.0f}bp 为目标，"
                f"收窄至 {s2s10 - 15:.0f}bp 以下止损"
            )
        elif shape in ("熊平", "牛平"):
            trades.append(
                f"目标/止损：2s10s 收窄至 {s2s10 - 15:.0f}bp 为目标，"
                f"走扩至 {s2s10 + 15:.0f}bp 以上止损"
            )
    return trades


def _invalidation(shape: str, s2s10: float | None, y10: float | None) -> list[str]:
    conds = []
    if s2s10 is not None:
        if shape in ("熊陡", "牛陡"):
            conds.append(f"2s10s 利差收窄至 {s2s10 - 20:.0f}bp 以下")
        elif shape in ("熊平", "牛平"):
            conds.append(f"2s10s 利差走扩至 {s2s10 + 20:.0f}bp 以上")
        else:
            conds.append(
                f"2s10s 单方向移动超 20bp"
                f"（{s2s10 - 20:.0f}bp 以下或 {s2s10 + 20:.0f}bp 以上）"
            )
    if y10 is not None:
        conds.append(
            f"10Y 名义收益率反向突破 {y10 + 0.30:.2f}% 或跌破 {y10 - 0.40:.2f}%"
        )
    conds.append("FOMC 意外转向（加息/降息信号与当前定价相反）")
    return conds


def yield_curve_analysis() -> dict:
    """收益率曲线解读（规则版，对应 timsun 的『曲线变动解读』）。"""
    rates, tips, infl, _, auc, cgb = _load()
    if rates.empty:
        return {"error": "data/fred/rates/rates.csv 缺失，先运行 ./bin/fetch_fred"}

    as_of = rates.index.max().strftime("%Y-%m-%d")
    s2s10 = _spread(rates, "DGS10", "DGS2")
    s3m10 = _spread(rates, "DGS10", "DGS3MO")
    s5s30 = _spread(rates, "DGS30", "DGS5")
    y10_1m = _bp_change(rates, "DGS10", 30)
    y2_1m = _bp_change(rates, "DGS2", 30)

    # 实际 2s10s 1m 变化
    s2s10_1m = None
    if s2s10 is not None:
        prev_s2s10 = _spread(rates, "DGS10", "DGS2", 30)
        s2s10_1m = round(s2s10 - prev_s2s10, 1) if prev_s2s10 is not None else None
    shape = _shape_label(s2s10_1m, y10_1m)

    # 驱动归因：1 月内实际利率 vs 盈亏平衡变动
    real_1m = (
        _bp_change(tips, "DFII10", 30) if not tips.empty and "DFII10" in tips else None
    )
    be_1m = (
        _bp_change(infl, "T10YIE", 30) if not infl.empty and "T10YIE" in infl else None
    )
    if real_1m is not None and be_1m is not None and abs(real_1m) >= abs(be_1m):
        driver = "实际利率/期限溢价主导"
    elif real_1m is not None and be_1m is not None:
        driver = "通胀预期主导"
    else:
        driver = "数据不足"

    # 验证指标
    checks = []
    if be_1m is not None:
        ok = be_1m < 5
        checks.append(
            {
                "name": "通胀预期（T10YIE）",
                "value": f"{be_1m:+.1f}bp",
                "ok": ok,
                "note": "未明显上行，不支持通胀驱动"
                if ok
                else "明显上行，通胀驱动占优",
            }
        )
    d10 = _snapshot(tips, "DFII10", 0) if not tips.empty and "DFII10" in tips else None
    if d10 is not None:
        checks.append(
            {
                "name": "实际利率（DFII10）",
                "value": f"{d10:.2f}%",
                "ok": d10 > 2.0,
                "note": "高位主导长端，与熊陡逻辑一致"
                if d10 > 2.0
                else "低于 2%，压制长端",
            }
        )
    effr = _snapshot(rates, "FEDFUNDS", 0)
    y2 = _snapshot(rates, "DGS2", 0)
    if effr is not None and y2 is not None:
        # 短端定价与曲线形态的一致性（审计 P1-⑥）：
        # 熊陡/熊平 = 长端驱动 → 短端应未定价降息（higher-for-longer，2Y ≥ EFFR）；
        # 牛陡/牛平 = 短端驱动 → 短端应定价降息（2Y < EFFR）。
        # 原判据恒取 y2 < effr，与同页熊陡归因自相矛盾（timsun 同场景标 ✓）。
        if shape in ("熊陡", "熊平"):
            ok = y2 >= effr
            note = (
                f"降息预期回落，2Y 在 {y2:.2f}% 处获得支撑，与长端驱动一致"
                if ok
                else "短端定价降息，与长端驱动矛盾"
            )
        elif shape in ("牛陡", "牛平"):
            ok = y2 < effr
            note = (
                "短端定价降息，与短端驱动一致"
                if ok
                else "短端未定价降息，与牛向形态矛盾"
            )
        else:
            ok = True
            note = "形态中性，短端定价不构成矛盾"
        checks.append(
            {
                "name": "联储利率预期",
                "value": f"EFFR {effr:.2f}% vs 2Y {y2:.2f}%",
                "ok": ok,
                "note": note,
            }
        )
    if not auc.empty:
        avg = _coupon_cover(auc)
        if avg is not None:
            ok = avg >= 2.5
            checks.append(
                {
                    "name": "国债拍卖需求",
                    "value": f"近 10 场付息券 {avg:.2f}x",
                    "ok": ok,
                    "note": "需求良好，长端压力有限"
                    if ok
                    else "投标倍数偏低，长端供给压力",
                }
            )
    confidence = sum(1 for c in checks if c["ok"])

    # 全球长端对照（美/日/中 10Y + 30Y）：
    # spread = 美国 − 该市场（bp，与 timsun 符号一致）；缺数据返回 None 不伪造
    us10, us30 = _snapshot(rates, "DGS10", 0), _snapshot(rates, "DGS30", 0)
    jp10 = _snapshot(rates, "JP10Y", 0) if "JP10Y" in rates.columns else None
    cn10 = (
        _snapshot(cgb, "cgb_10y", 0)
        if not cgb.empty and "cgb_10y" in cgb.columns
        else None
    )
    cn30 = (
        _snapshot(cgb, "cgb_30y", 0)
        if not cgb.empty and "cgb_30y" in cgb.columns
        else None
    )

    return {
        "as_of": as_of,
        "shape": shape,
        "driver": driver,
        "spreads": {
            "2s10s": s2s10,
            "3m10s": s3m10,
            "5s30s": s5s30,
            "2s10s_1m_chg": s2s10_1m,
            "10y_1m_chg": y10_1m,
            "2y_1m_chg": y2_1m,
        },
        "spread_notes": [
            _spread_note(s2s10, "2s10s"),
            _spread_note(s3m10, "3m10s"),
            _spread_note(s5s30, "5s30s"),
        ],
        "validation": checks,
        "confidence": f"{confidence}/{len(checks)}",
        "trades": _trade_implications(shape, s2s10),
        "invalidation": _invalidation(shape, s2s10, _snapshot(rates, "DGS10", 0)),
        "time_frame": _time_frame(shape, s2s10_1m),
        "tenors": [
            {
                "tenor": t,
                "current": _snapshot(rates, c, 0),
                "prev_1w": _snapshot(rates, c, 7),
                "prev_1m": _snapshot(rates, c, 30),
                "prev_3m": _snapshot(rates, c, 90),
                "chg_1w": _bp_change(rates, c, 7),
                "chg_1m": _bp_change(rates, c, 30),
                "chg_3m": _bp_change(rates, c, 90),
            }
            for t, c in zip(TENORS, TERM_SERIES["rates"], strict=True)
        ],
        "tips": [
            {
                "tenor": t,
                "nominal": _snapshot(rates, dgs, 0),
                "real": _snapshot(tips, tip, 0),
                "breakeven": _breakeven(
                    _snapshot(rates, dgs, 0), _snapshot(tips, tip, 0)
                ),
            }
            for t, dgs, tip in zip(
                ["5Y", "7Y", "10Y", "20Y", "30Y"],
                ["DGS5", "DGS7", "DGS10", "DGS20", "DGS30"],
                TERM_SERIES["tips"],
                strict=True,
            )
        ],
        # 全球长端对照（美/日/中 10Y + 30Y）
        "global_long_end": [
            {
                "market": "美国",
                "note": "10Y Treasury",
                "rate": us10,
                "rate30": us30,
                "spread_vs_us": 0.0,
                "spread30_vs_us": 0.0,
                "source": "FRED DGS10 · daily",
            },
            {
                "market": "日本",
                "note": "10Y JGB",
                "rate": jp10,
                "rate30": None,
                "spread_vs_us": _spread_vs_us(jp10, us10),
                "spread30_vs_us": None,
                "source": "FRED IRLTLT01JPM156N · monthly",
            },
            {
                "market": "中国",
                "note": "10Y CGB",
                "rate": cn10,
                "rate30": cn30,
                "spread_vs_us": _spread_vs_us(cn10, us10),
                "spread30_vs_us": _spread_vs_us(cn30, us30),
                "source": "chinamoney RtimeYldCurv · daily",
            },
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 规则：利率研判四段文（入口页）
# ═══════════════════════════════════════════════════════════════════════════════


def overview_analysis() -> dict:
    """四段研判：曲线形态 / 实际利率 / 联储预期 / 展望。"""
    rates, tips, infl, rex, auc, cgb = _load()
    if rates.empty:
        return {"sections": []}
    as_of = rates.index.max().strftime("%Y-%m-%d")

    # ── 1. 曲线形态 ──
    s2s10 = _spread(rates, "DGS10", "DGS2")
    s2s10_1w = None
    if s2s10 is not None:
        prev = _spread(rates, "DGS10", "DGS2", 7)
        s2s10_1w = round(s2s10 - prev, 1) if prev is not None else None
    y10 = _snapshot(rates, "DGS10", 0)
    y2 = _snapshot(rates, "DGS2", 0)
    y10_1m = _bp_change(rates, "DGS10", 30)
    y2_1m = _bp_change(rates, "DGS2", 30)
    s2s10_1m = None
    if s2s10 is not None:
        prev_s2s10 = _spread(rates, "DGS10", "DGS2", 30)
        s2s10_1m = round(s2s10 - prev_s2s10, 1) if prev_s2s10 is not None else None
    shape = _shape_label(s2s10_1m, y10_1m)
    # 驱动侧：1 月内 10Y 涨幅 ≥ 2Y → 长端驱动；否则短端驱动（避免水平比较恒真）
    if y10_1m is not None and y2_1m is not None:
        driver_side = "长端风险溢价重定价" if y10_1m >= y2_1m else "短端政策预期"
    else:
        driver_side = "驱动方向待确认"
    curve_text = (
        f"曲线呈{shape}形态：2s10s 利差现报 {s2s10 or 0:.0f}bp"
        f"（1 周 {'走扩' if (s2s10_1w or 0) > 0 else '收窄'} "
        f"{abs(s2s10_1w or 0):.0f}bp），10Y 报 {y10 or 0:.2f}%。"
        f"驱动来自{driver_side}："
        f"验证指标——若 2s10s 突破 {s2s10 + 15:.0f}bp"
        f"且拍卖需求未见恶化，陡峭化将延续。"
    )

    # ── 2. 实际利率 ──
    d10 = _snapshot(tips, "DFII10", 0) if not tips.empty and "DFII10" in tips else None
    d10_1w = (
        _bp_change(tips, "DFII10", 7) if not tips.empty and "DFII10" in tips else None
    )
    be = _snapshot(infl, "T10YIE", 0) if not infl.empty and "T10YIE" in infl else None
    real_text = (
        f"10Y TIPS 实际利率报 {d10:.2f}%（1 周 {d10_1w:+.0f}bp），"
        f"盈亏平衡通胀 {be:.2f}%——长端上行的"
        f"{'实际利率贡献更大' if (d10_1w or 0) > 0 else '通胀预期贡献更大'}。"
        f"触发条件：若实际利率跌破 {d10 - 0.15:.2f}%，"
        f"将直接推升黄金与长端债券。"
    )

    # ── 3. 联储预期 ──
    effr = _snapshot(rates, "FEDFUNDS", 0)
    gap = (y2 or 0) - (effr or 0)
    fed_text = (
        f"联邦基金有效利率 {effr:.2f}%，2Y 收益率 {y2:.2f}%"
        f"（利差 {gap * _BP:+.0f}bp），"
        f"市场隐含{'加息' if gap > 0 else '降息'}定价"
        f"{'（与点阵图一致）' if abs(gap) < 0.25 else '（与点阵图背离）'}。"
        f"验证指标：若 2Y-EFFR 利差维持 {abs(gap) * _BP:.0f}bp 以上"
        f"且 FOMC 未释放信号，前端波动将加剧。"
    )

    # ── 4. 展望 ──
    m1 = _snapshot(rates, "DGS1MO", 0)
    short_txt = (
        f"1M 国库券 {m1:.2f}%"
        f"{'高于' if (m1 or 0) > (effr or 0) else '低于'}联邦基金利率"
        f"{'，短端流动性分层' if (m1 or 0) > (effr or 0) else '，短端平稳'}"
    )
    if not auc.empty:
        avg = _coupon_cover(auc)
        auc_txt = (
            f"近 10 场付息券投标倍数 {avg:.2f}x，"
            f"{'需求良好' if (avg or 0) >= 2.5 else '需求偏弱'}"
            if avg is not None
            else "拍卖数据待接入"
        )
    else:
        auc_txt = "拍卖数据待接入"
    outlook_text = (
        f"短端：{short_txt}。长端：10Y 在 {y10:.2f}%，供给与期限溢价主导，"
        f"关注季度再融资与 TGA 余额变化。{auc_txt}。关键触发点："
        f"2s10s 利差单方向移动超 15bp 或 FOMC 措辞变化"
        f"将决定下一阶段方向。"
    )

    return {
        "as_of": as_of,
        "sections": [
            {"title": "曲线形态", "body": curve_text},
            {"title": "实际利率", "body": real_text},
            {"title": "联储预期", "body": fed_text},
            {"title": "展望", "body": outlook_text},
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 统一入口（LLM 替换点）
# ═══════════════════════════════════════════════════════════════════════════════


def _llm_generate() -> dict | None:
    """LLM 生成入口（预留）。

    接入方式：在此调用 LLM（如
    `src.fetchers.rate_expectations_fetcher` 同款 requests 直连），传入
    `yield_curve_analysis()` / `overview_analysis()` 的结构化数据作为上下文，
    返回同构 dict（yield_curve / overview 两个键），并置 `_LLM_ENABLED = True`。
    未接入时返回 None，调用方自动回落规则引擎。
    """
    return None


_LLM_ENABLED = False


def generate_analysis() -> dict:
    """利率研判统一入口：LLM 优先（预留），规则引擎兜底。

    返回 {generator, overview: {...}, yield_curve: {...}}。
    """
    if _LLM_ENABLED:
        llm_out = _llm_generate()
        if llm_out is not None:
            return {**llm_out, "generator": "llm"}
    return {
        "generator": "rules",
        "overview": overview_analysis(),
        "yield_curve": yield_curve_analysis(),
    }


if __name__ == "__main__":
    # 自检：跑通 + 打印渲染结果
    import json

    out = generate_analysis()
    print(json.dumps(out, ensure_ascii=False, indent=1)[:4000])
