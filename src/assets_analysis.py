"""
大类资产专题分析引擎（对齐 timsun.net/assets：主页面 + 8 子页数据）。

规则引擎生成（透明可复盘、LLM 预留——_llm_generate() 接 LLM 后返回同构 dict，
未接入时回落规则引擎，参考 liquidity_analysis._llm_generate_dashboard 模式）：
  - 主页面：6 大类价格表 + 相关性热力数据 + 4 段交叉分析叙事
  - equities：广度判读（SPX/RUT 差距 + ABV 占比）+ 动能集中度
  - fx：美元广度评分（2/15 对）+ 各分组 20D USD 压力
  - commodities / bonds / crypto：20 日收盘表 + 走势归一化
  - positioning：CFTC 投机仓位拥挤度 + 分组合约（2 年百分位规则）
  - etfs：精选池温度筛选（1w/1m/3m 动量、20d 波动、3m 回撤、趋势判读）
  - crypto_derivatives：BTC 前瞻雷达（7 信号加权 + 触发/反证条件）

LLM 钩子只装在叙事段（cross_analysis / equity_analysis / crypto_radar）——
纯数据表页（bonds/commodities/etfs/fx/positioning）无叙事，不设 _llm_generate。

数据层只读现有 CSV/JSON：
  data/yfinance/asset_prices.csv       资产价格宽表（日频快照）
  data/cross_asset/correlation.csv     30 日相关性矩阵 + alerts.csv
  data/fx/fx_pairs.csv                 外汇对（独立管线）
  data/etf/…                           全量清单 + 精选池日线
  data/breadth/abv.csv                 标普成分股均线上方占比
  data/cot/cot.csv                     CFTC COT（周频）
  data/analyst/ndx_targets.csv         Nasdaq 100 分析师目标价
  data/options_structure/*.json        期权结构快照（src/options_structure.py）
  data/crypto_derivatives/*.json       加密衍生品快照
  data/fred/liquidity/liquidity.csv    净流动性（crypto 页溢出分析）
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from src.config import ETF_POOL, FX_PAIRS

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent


# LLM 预留：_llm_generate() 接好 LLM 后返回同构 dict，未接入时返回 None
# → 调用方回落规则引擎（_generate_* 统一入口，参考 liquidity_analysis 模式）
def _llm_generate(section: str) -> dict | None:
    """LLM 生成接人点。section ∈ {cross_analysis, equity_analysis, radar}。

    TODO: 实现后返回 {generator: 'llm', ...}；返回 None 则回落规则引擎。
    """
    return None


# ── 主页面资产分组（timsun 面板同款：代码 → 名称） ──────────────────────────
EQUITY_ROWS = [
    ("SPX", "标普500"),
    ("NDX", "纳斯达克100"),
    ("DJI", "道琼斯"),
    ("RUT", "罗素2000"),
]
BOND_ROWS = [
    ("TLT", "20年+国债ETF"),
    ("IEF", "7-10年国债ETF"),
    ("LQD", "投资级债券ETF"),
    ("HYG", "高收益债券ETF"),
]
COMMODITY_ROWS = [
    ("Gold", "黄金"),
    ("Silver", "白银"),
    ("WTI", "WTI原油"),
    ("NG", "天然气"),
    ("Copper", "铜"),
]
ETF_ROWS = [
    ("SPY", "SPY标普500 ETF"),
    ("QQQ", "QQQ纳斯达克100 ETF"),
    ("SMH", "SMH半导体ETF"),
    ("SOXX", "SOXX半导体ETF"),
]
FX_ROWS = [
    ("DXY", "美元指数"),
    ("EURUSD", "欧元/美元"),
    ("GBPUSD", "英镑/美元"),
    ("USDJPY", "美元/日元"),
    ("USDCNH", "美元/离岸人民币"),
    ("USDKRW", "美元/韩元"),
]
CRYPTO_ROWS = [("BTC", "比特币"), ("ETH", "以太坊")]


# ── 读数据 ───────────────────────────────────────────────────────────────────


def _read(path: str, **kw) -> pd.DataFrame:
    f = ROOT / "data" / path
    if not f.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(f, **kw)
    except Exception as e:
        logger.warning("读取 %s 失败: %s", path, e)
        return pd.DataFrame()


def _csv(path: str) -> pd.DataFrame:
    return _read(path, index_col="date", parse_dates=True)


def asset_prices() -> pd.DataFrame:
    df = _csv("yfinance/asset_prices.csv")
    if df.empty:
        return df
    return df[[c for c in df.columns if not c.endswith("_volume")]]


def _corr_matrix() -> pd.DataFrame | None:
    """相关性矩阵（首行 index='date' 跳过）。"""
    df = _read("cross_asset/correlation.csv")
    if df.empty:
        return None
    d = df[df["asset"] != "date"]
    return d.set_index("asset")


def _price_rows(df: pd.DataFrame, rows: list[tuple[str, str]]) -> list[dict]:
    """取一组标的的最新价 + 日涨跌（最新 vs 上一有效日）。"""
    out = []
    for key, name in rows:
        if key not in df.columns:
            continue
        s = df[key].dropna()
        if s.empty:
            continue
        chg = None
        if len(s) >= 2:
            chg = (s.iloc[-1] / s.iloc[-2] - 1) * 100
        out.append(
            {
                "symbol": key,
                "name": name,
                "last": float(s.iloc[-1]),
                "chg_pct": round(chg, 2) if chg is not None else None,
                "date": str(s.index[-1].date()),
            }
        )
    return out


def _recent_prices(df: pd.DataFrame, cols: list[str], n: int = 20) -> list[dict]:
    """近 n 日收盘表（行=日期，列=标的）。"""
    sub = df[[c for c in cols if c in df.columns]].dropna(how="all")
    sub = sub.tail(n)
    dates = [str(d.date()) for d in sub.index]
    series = {}
    for c in cols:
        series[c] = (
            [None if pd.isna(v) else float(v) for v in sub[c]]
            if c in sub.columns
            else []
        )
    return {"dates": dates, "series": series}


def _chg(s: pd.Series, days: int) -> float | None:
    """最新 vs 约 days 天前的百分比变化。"""
    v = s.dropna()
    if len(v) < 2:
        return None
    cutoff = v.index[-1] - pd.Timedelta(days=days)
    past = v.loc[:cutoff]
    if past.empty or not past.iloc[-1]:
        return None
    return (float(v.iloc[-1]) / float(past.iloc[-1]) - 1) * 100


# ── 主页面 ───────────────────────────────────────────────────────────────────


def overview() -> dict:
    p = asset_prices()
    out: dict = {
        "as_of": "",
        "tables": {},
        "corr": None,
        "alerts": None,
        "analysis": {},
    }
    if p.empty:
        return out
    groups = {
        "equities": EQUITY_ROWS,
        "bonds": BOND_ROWS,
        "commodities": COMMODITY_ROWS,
        "etfs": ETF_ROWS,
        "crypto": CRYPTO_ROWS,
    }
    # ETF 表（SPY/QQQ/SMH/SOXX）在精选池数据里更全（asset_prices 无 SPY 列）
    etf_pool = _csv("etf/pool_prices.csv")
    for g, rows in groups.items():
        tables = _price_rows(p, rows)
        if g == "etfs":
            pool_tables = _price_rows(etf_pool, [(k, n) for k, n in ETF_ROWS])
            tables = pool_tables or tables
        out["tables"][g] = tables
        if not out["as_of"] and tables:
            out["as_of"] = tables[0]["date"]
    # FX 表走独立管线（asset_prices 缺 CNH/CHF 等）
    fx = _csv("fx/fx_pairs.csv")
    out["tables"]["fx"] = _price_rows(fx, FX_ROWS)

    # 相关性热力
    out["corr"] = _corr_series()
    out["alerts"] = _corr_alerts()
    out["analysis"] = cross_analysis()
    return out


def _corr_series() -> dict | None:
    m = _corr_matrix()
    if m is None or m.empty:
        return None
    assets = [c for c in m.columns if c in m.index]
    return {
        "assets": assets,
        "matrix": [
            [
                round(float(m.loc[a][b]), 3) if pd.notna(m.loc[a][b]) else None
                for b in assets
            ]
            for a in assets
        ],
    }


def _corr_alerts() -> list[dict]:
    a = _read("cross_asset/alerts.csv")
    if a.empty:
        return []
    row = a.iloc[-1]
    out = []
    if pd.notna(row.get("SPX_TLT_30d")) and abs(float(row["SPX_TLT_30d"])) >= 0.3:
        out.append(
            {
                "title": "股债正相关 — 60/40 组合失效警告"
                if float(row["SPX_TLT_30d"]) > 0
                else "股债负相关 — 对冲属性回归",
                "text": f"SPX 与长债 30 日相关性 = {float(row['SPX_TLT_30d']):+.2f}。"
                + (
                    "对冲属性消失。"
                    if float(row["SPX_TLT_30d"]) > 0
                    else "组合分散化有效。"
                ),
            }
        )
    if pd.notna(row.get("WTI_SPX_30d")) and float(row["WTI_SPX_30d"]) <= -0.3:
        out.append(
            {
                "title": "原油-美股强负相关 — 市场交易滞胀",
                "text": (
                    f"原油与 SPX 30 日相关性 = {float(row['WTI_SPX_30d']):.2f}，"
                    "能源涨即股票跌。"
                ),
            }
        )
    return out


def cross_analysis() -> dict:
    """4 段交叉分析叙事（规则引擎；LLM 预留——_llm_generate 返回 dict 则直接使用）。"""
    llm = _llm_generate("cross_analysis")
    if llm:
        return llm
    p = asset_prices()
    rates = _csv("fred/rates/rates.csv")
    liq = _csv("fred/liquidity/liquidity.csv")
    corr = _corr_matrix()

    out = {}

    # 1. 跨资产相关性（用矩阵 + 10Y 盈亏平衡通胀 + 2s10s）
    def _corr(a: str, b: str) -> float | None:
        if corr is None or a not in corr.index or b not in corr.columns:
            return None
        v = pd.to_numeric(corr.loc[a][b], errors="coerce")
        return float(v) if pd.notna(v) else None

    spx_tlt = _corr("SPX", "TLT")
    wti_spx = _corr("WTI", "SPX")
    dei = None
    if "T10YIE" in rates.columns:
        dei = float(rates["T10YIE"].dropna().iloc[-1])
    curve = None
    if {"DGS10", "DGS2"}.issubset(rates.columns):
        r10, r2 = rates["DGS10"].dropna(), rates["DGS2"].dropna()
        if len(r10) and len(r2):
            curve = round(float(r10.iloc[-1] - r2.iloc[-1]), 2)
    parts = []
    if spx_tlt is not None:
        parts.append(
            f"SPX 与 TLT 30 日相关性为 {spx_tlt:+.2f}，"
            + (
                "股债同向波动，60/40 对冲属性弱化"
                if spx_tlt > 0.2
                else "股债呈传统对冲格局"
            )
        )
    if wti_spx is not None:
        parts.append(
            f"WTI 与 SPX 相关性 {wti_spx:+.2f}，"
            + (
                "能源上涨对应股票承压，滞胀交易特征"
                if wti_spx < -0.3
                else "能源与股票关联偏弱"
            )
        )
    if dei is not None:
        tag = (
            "通胀预期抬升"
            if dei > 2.2
            else ("通胀预期中性" if dei > 1.8 else "通胀预期偏弱")
        )
        parts.append(f"10Y 盈亏平衡通胀率 {dei:.2f}%，{tag}")
    if curve is not None:
        parts.append(
            f"10Y-2Y 利差 {curve:+.2f}（曲线{'走陡' if curve > 0.3 else '平坦'}）"
        )
    out["cross_asset"] = {
        "title": "跨资产相关性",
        "text": "；".join(parts) + "。" if parts else "数据不足。",
    }

    # 2. 美元与商品
    fx_df = fx_series()
    dxy_chg = _chg(fx_df["DXY"], 5) if "DXY" in fx_df.columns else None
    gold_chg = _chg(p["Gold"], 5) if "Gold" in p.columns else None
    wti_chg = _chg(p["WTI"], 5) if "WTI" in p.columns else None
    parts = []
    if dxy_chg is not None:
        parts.append(f"DXY 近 5 日 {dxy_chg:+.2f}%")
    if gold_chg is not None:
        parts.append(f"黄金 {gold_chg:+.2f}%")
    if wti_chg is not None:
        parts.append(f"WTI {wti_chg:+.2f}%")
    if dxy_chg is not None and dxy_chg < -0.5 and gold_chg is not None and gold_chg > 1:
        parts.append("美元走弱对商品构成计价支撑")
    out["dollar_commodities"] = {
        "title": "美元与商品",
        "text": "；".join(parts) + "。" if parts else "数据不足。",
    }

    # 3. 展望（流动性分层 → TGA/RRP 主收紧点）
    parts = []
    net = None
    if {"WALCL", "RRPONTSYD", "WTREGEN"}.issubset(liq.columns):
        last = liq[["WALCL", "RRPONTSYD", "WTREGEN"]].dropna().iloc[-1]
        net = (
            float(last["WALCL"])
            - float(last["RRPONTSYD"]) * 1000
            - float(last["WTREGEN"])
        ) / 1000
        tga = float(last["WTREGEN"]) / 1000
        rrp = float(last["RRPONTSYD"]) * 1000 / 1000
        parts.append(
            f"净流动性 {net:,.1f}T（WALCL {float(last['WALCL']) / 1000:,.1f}T − "
            f"TGA {tga:,.1f}T − RRP {rrp:,.1f}B）"
        )
        if tga > 700:
            parts.append(f"TGA 达 {tga:,.0f}B 是主要收紧点")
        if rrp < 100:
            parts.append(f"RRP 仅 {rrp:,.1f}B 缓冲耗尽")
    if spx_tlt is not None and spx_tlt > 0.3:
        parts.append("股债相关性为正，若 10Y 快速上行警惕股债双杀")
    out["outlook"] = {
        "title": "展望",
        "text": "；".join(parts) + "。" if parts else "数据不足。",
    }

    # 4. 风险偏好（SPX/黄金 + VIX + 信用利差 + BTC）
    spx_gold = None
    if {"SPX", "Gold"}.issubset(p.columns):
        s = p["SPX"].dropna()
        gold = p["Gold"].dropna()
        if len(s) and len(gold):
            spx_gold = float(s.iloc[-1]) / float(gold.iloc[-1])
    parts = []
    if spx_gold is not None:
        parts.append(f"SPX/黄金 {spx_gold:.2f}")
    vix = _csv("cboe/volatility.csv")
    vix_l = (
        float(vix["VIX"].dropna().iloc[-1])
        if "VIX" in vix.columns and not vix.empty
        else None
    )
    if vix_l is not None:
        parts.append(f"VIX {vix_l:.1f}" + ("（低位）" if vix_l < 17 else ""))
    hy = (
        _csv("fred/volatility/volatility.csv")
        if (ROOT / "data/fred/volatility/volatility.csv").exists()
        else pd.DataFrame()
    )
    if "HY_OAS" in hy.columns and not hy.empty:
        hy_oas = float(hy["HY_OAS"].dropna().iloc[-1])
        parts.append(f"HY OAS {hy_oas:.2f}")
    btc_chg = _chg(p["BTC"], 7) if "BTC" in p.columns else None
    if btc_chg is not None:
        parts.append(f"BTC 近 7 日 {btc_chg:+.2f}%")
    if spx_gold is not None and spx_gold < 2:
        parts.append("SPX/黄金低比值，资金偏向实物对冲")
    out["risk_appetite"] = {
        "title": "风险偏好",
        "text": "；".join(parts) + "。" if parts else "数据不足。",
    }
    return out


def fx_series() -> pd.DataFrame:
    return _csv("fx/fx_pairs.csv")


# ── equities 子页 ────────────────────────────────────────────────────────────


def equities() -> dict:
    p = asset_prices()
    out: dict = {"indices": {}, "breadth": {}, "analysis": {}}
    if p.empty:
        return out
    out["indices"] = _price_rows(p, EQUITY_ROWS)

    # 广度：SPX/RUT 20D + ABV 占比
    spx20 = _chg(p["SPX"], 20) if "SPX" in p.columns else None
    rut20 = _chg(p["RUT"], 20) if "RUT" in p.columns else None
    gap = None
    if spx20 is not None and rut20 is not None:
        gap = round(spx20 - rut20, 2)
        out["breadth"]["verdict"] = (
            f"SPX 与 RUT 同步（差距 {gap:+.2f} 个百分点），市场广度均衡"
            if abs(gap) < 1.0
            else (
                f"大盘领涨（差距 {gap:+.2f} pp），广度集中于权重股"
                if gap > 0
                else f"小盘领涨（差距 {gap:+.2f} pp），风格轮动"
            )
        )
    out["breadth"]["spx_20d"] = spx20
    out["breadth"]["rut_20d"] = rut20
    out["breadth"]["gap"] = gap

    abv = _csv("breadth/abv.csv")
    if not abv.empty:
        out["breadth"]["abv200"] = (
            float(abv["ABV200"].dropna().iloc[-1]) if "ABV200" in abv.columns else None
        )
        out["breadth"]["abv50"] = (
            float(abv["ABV50"].dropna().iloc[-1]) if "ABV50" in abv.columns else None
        )
        out["breadth"]["abv_dates"] = [str(d.date()) for d in abv.index]
        out["breadth"]["abv50_series"] = (
            [None if pd.isna(v) else round(float(v), 1) for v in abv["ABV50"]]
            if "ABV50" in abv.columns
            else []
        )
        out["breadth"]["abv200_series"] = (
            [None if pd.isna(v) else round(float(v), 1) for v in abv["ABV200"]]
            if "ABV200" in abv.columns
            else []
        )

    out["analysis"] = equity_analysis(out["breadth"])
    return out


def equity_analysis(b: dict) -> dict:
    """3 段叙事（规则引擎；LLM 预留——_llm_generate 返回 dict 则直接使用）。"""
    llm = _llm_generate("equity_analysis")
    if llm:
        return llm
    a200 = b.get("abv200")
    a50 = b.get("abv50")
    gap = b.get("gap")
    # 动能集中度
    parts = []
    if gap is not None:
        if abs(gap) < 1.0:
            parts.append(
                f"SPX/RUT 20 日差 {gap:+.2f} pp 处于健康区间，不构成权重股独秀"
            )
        else:
            parts.append(
                f"SPX/RUT 20 日差 {gap:+.2f} pp，存在"
                f"{'权重股主导' if gap > 0 else '小盘轮动'}特征"
            )
    if a200 is not None:
        zone = (
            "健康区间"
            if 60 <= a200 <= 80
            else ("强势区间" if a200 > 80 else "警戒区间")
        )
        parts.append(f"ABV200 {a200:.2f}%，位于 60-80% {zone}")
    out = {
        "momentum": {
            "title": "动能集中度",
            "text": "；".join(parts) + "。" if parts else "数据不足。",
        }
    }
    # 内部健康度
    parts = []
    if a50 is not None and a200 is not None:
        if a50 > a200:
            parts.append("ABV50 高于 ABV200，短周期修复强于长周期")
        else:
            parts.append("ABV50 低于 ABV200，短周期修复弱于长周期")
        if a200 is not None and a200 < 60:
            parts.append(f"ABV200 {a200:.1f}% 跌破 60% 阈值，内部走弱")
    out["health"] = {
        "title": "内部健康度",
        "text": "；".join(parts) + "。" if parts else "数据不足。",
    }
    # 展望
    parts = []
    if a200 is not None and gap is not None:
        if a200 > 60 and abs(gap) < 1:
            parts.append("广度健康、风格均衡，指数回调更接近健康轮动而非趋势破位")
        else:
            parts.append("广度或风格存在背离，需警惕回调扩大")
    parts.append("验证指标：ABV200 是否站稳 60%、VIX 是否升破 17")
    out["outlook"] = {"title": "展望", "text": "；".join(parts) + "。"}
    return out


# ── positioning 子页（CFTC COT） ─────────────────────────────────────────────

# 金融合约（对冲基金 L/S）与商品合约（管理资金 L/S）→ 测试可引用
FIN_SYMBOLS = ("NQ", "ES", "RTY", "VX", "ZF", "ZN", "ZB", "EUR", "JPY", "BTC")
COMMODITY_SYMBOLS = ("GC", "SI", "HG", "CL", "NG")


def _ls_cols(sym: str) -> tuple[str, str]:
    """(long 列, short 列)：金融 = HEDGE_L/HEDGE_S，商品 = MM_L/MM_S。"""
    if sym in FIN_SYMBOLS:
        return f"{sym}_HEDGE_L", f"{sym}_HEDGE_S"
    return f"{sym}_MM_L", f"{sym}_MM_S"


def positioning() -> dict:
    cot = _csv("cot/cot.csv")
    if cot.empty or len(cot) < 52:
        return {"crowding": None, "groups": [], "note": "COT 样本不足（<52 周）"}
    # 投机净仓 = L − S：金融=对冲基金(HEDGE_L−HEDGE_S)，商品=管理资金(MM_L−MM_S)
    longs: dict[str, pd.Series] = {}
    shorts: dict[str, pd.Series] = {}
    net: dict[str, pd.Series] = {}
    for sym in FIN_SYMBOLS + COMMODITY_SYMBOLS:
        lcol, scol = _ls_cols(sym)
        if lcol in cot.columns and scol in cot.columns:
            longs[sym] = cot[lcol]
            shorts[sym] = cot[scol]
            net[sym] = cot[lcol] - cot[scol]
    # 百分位（近 2 年滚动窗口取最新）
    groups_def = {
        "index_vol": ["NQ", "ES", "RTY", "VX"],
        "rates": ["ZF", "ZN", "ZB"],
        "fx": ["EUR", "JPY"],
        "commodities": ["GC", "SI", "HG", "CL", "NG"],
    }
    latest = cot.index[-1]
    out = {"latest_report": str(latest.date()), "groups": [], "crowding": None}
    all_pct = []
    for g, syms in groups_def.items():
        contracts = []
        for sym in syms:
            if sym not in net:
                continue
            s = net[sym].dropna()
            if len(s) < 52:
                continue
            net_val = float(s.iloc[-1])
            pct = float((s < net_val).mean() * 100)
            wk = None
            if len(s) >= 2:
                wk = float(s.iloc[-1] - s.iloc[-2])
            label = (
                "极度看多"
                if pct >= 90
                else (
                    "偏多"
                    if pct >= 75
                    else (
                        "极度看空" if pct <= 10 else ("偏空" if pct <= 25 else "中性")
                    )
                )
            )
            contracts.append(
                {
                    "symbol": sym,
                    "net": round(net_val, 0),
                    "pct_2y": round(pct, 1),
                    "label": label,
                    "week_chg": wk,
                    "week_dir": "增仓"
                    if wk is not None and wk > 0
                    else ("减仓" if wk is not None else None),
                    "long": round(float(longs[sym].iloc[-1]), 0),
                    "short": round(float(shorts[sym].iloc[-1]), 0),
                }
            )
            all_pct.append(abs(pct - 50) * 2)
        if contracts:
            worst = max(contracts, key=lambda c: abs(c["pct_2y"] - 50))
            out["groups"].append(
                {
                    "name": g,
                    "contracts": contracts,
                    "summary": (
                        f"最极端：{worst['symbol']}（2 年百分位 "
                        f"{worst['pct_2y']:.1f}，{worst['label']}）"
                    ),
                }
            )
    if all_pct:
        out["crowding"] = round(sum(all_pct) / len(all_pct), 1)
    return out


# ── fx 子页 ──────────────────────────────────────────────────────────────────


def fx() -> dict:
    df = fx_series()
    if df.empty:
        return {"breadth": None, "dashboard": []}
    # 20D USD 压力：直接报价（USDXXX）= 原始 20D 变化；间接（XXXUSD）= 取负
    INDIRECT = {"EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"}  # 美元在分母
    rows = []
    groups: dict[str, list[float]] = {}
    for key, (ticker, name, group) in FX_PAIRS.items():
        if key not in df.columns:
            continue
        s = df[key].dropna()
        if len(s) < 5:
            continue
        d1, d5, d20, d60 = _chg(s, 1), _chg(s, 5), _chg(s, 20), _chg(s, 60)
        sign = -1 if key in INDIRECT else 1
        pressure = round(sign * d20, 2) if d20 is not None else None
        if pressure is not None:
            groups.setdefault(group, []).append(pressure)
        rows.append(
            {
                "symbol": ticker,
                "key": key,
                "group": group,
                "name": name,
                "last": float(s.iloc[-1]),
                "d1": round(d1, 2) if d1 is not None else None,
                "d5": round(d5, 2) if d5 is not None else None,
                "d20": round(d20, 2) if d20 is not None else None,
                "d60": round(d60, 2) if d60 is not None else None,
                "pressure": pressure,
            }
        )
    # 美元广度：分组压力均值 → 强弱标签
    breadth = []
    for g, vals in groups.items():
        avg = sum(vals) / len(vals)
        label = "美元走弱" if avg < -1 else ("美元走强" if avg > 1 else "美元中性")
        breadth.append(
            {"group": g, "avg_pressure": round(avg, 2), "label": label, "n": len(vals)}
        )
    # 美元广度按对统计（每组均值供展示；weak/total 计数基于对级压力）
    weak_count = sum(
        1 for r in rows if r["pressure"] is not None and r["pressure"] < -1
    )
    total_pairs = sum(1 for r in rows if r["pressure"] is not None)
    overall = {
        "weak": weak_count,
        "total": total_pairs,
        "verdict": "美元走弱"
        if weak_count >= total_pairs / 2
        else ("美元走强" if weak_count == 0 and total_pairs else "分化"),
    }
    return {"breadth": {"groups": breadth, "overall": overall}, "dashboard": rows}


# ── bonds / commodities / crypto 子页 ────────────────────────────────────────


def bonds() -> dict:
    p = asset_prices()
    return {
        "cards": _price_rows(p, BOND_ROWS),
        "recent": _recent_prices(p, [k for k, _ in BOND_ROWS]),
    }


def commodities() -> dict:
    p = asset_prices()
    cols = [k for k, _ in COMMODITY_ROWS]
    # 归一化走势（1 年）
    norm = {}
    sub = p[[c for c in cols if c in p.columns]].dropna(how="all").tail(250)
    for c in cols:
        if c in sub.columns:
            s = sub[c].dropna()
            if len(s) > 1:
                norm[c] = [round(float(v) / float(s.iloc[0]) * 100 - 100, 2) for v in s]
    return {
        "cards": _price_rows(p, COMMODITY_ROWS),
        "recent": _recent_prices(p, cols),
        "normalized": {"dates": [str(d.date()) for d in sub.index], "series": norm},
    }


def crypto() -> dict:
    p = asset_prices()
    out = {
        "cards": _price_rows(p, CRYPTO_ROWS),
        "recent": _recent_prices(p, [k for k, _ in CRYPTO_ROWS]),
    }
    # 净流动性溢出
    liq = _csv("fred/liquidity/liquidity.csv")
    btc = p["BTC"].dropna() if "BTC" in p.columns else pd.Series(dtype=float)
    out["liquidity"] = {}
    if {"WALCL", "RRPONTSYD", "WTREGEN"}.issubset(liq.columns):
        wide = pd.DataFrame(index=liq.index)
        for c in ("WALCL", "WTREGEN"):
            wide[c] = liq[c].ffill()
        wide["RRP"] = liq["RRPONTSYD"]
        wide["NL"] = wide["WALCL"] - wide["RRP"] * 1000 - wide["WTREGEN"]
        nl = wide["NL"].dropna()
        btc30 = btc.tail(200)
        # 90 日收益率回归（NL 变化 vs BTC 日收益）
        r = pd.concat([nl, btc30], axis=1, keys=["NL", "BTC"]).dropna()
        rets = r.pct_change().dropna()
        beta = None
        r2 = None
        if len(rets) >= 60:
            y = rets["BTC"].tail(90).values
            x = rets["NL"].tail(90).values
            if np.std(x) > 0 and np.std(y) > 0:
                beta = float(np.cov(x, y)[0, 1] / np.var(x))
                r2 = float(np.corrcoef(x, y)[0, 1] ** 2)
        pulse = None
        if len(nl) >= 21:
            pulse = (float(nl.iloc[-1]) / float(nl.iloc[-21]) - 1) * 100
        div = None
        if len(nl) >= 31 and len(btc) >= 31:
            div = {
                "nl": round(
                    (float(nl.iloc[-1]) / float(nl.iloc[-31]) - 1) * 100 / 1000, 2
                ),
                "btc": round((float(btc.iloc[-1]) / float(btc.iloc[-31]) - 1) * 100, 1),
            }
        out["liquidity"] = {
            "net": round(float(nl.iloc[-1]) / 1000, 2),  # T
            "fed": round(float(wide["WALCL"].dropna().iloc[-1]) / 1000, 2),
            "tga": round(float(wide["WTREGEN"].dropna().iloc[-1]) / 1000, 1),
            "rrp": round(float(wide["RRP"].dropna().iloc[-1]) / 1000, 2),
            "pulse_20d": round(pulse, 0) if pulse is not None else None,
            "pulse_state": "收缩"
            if pulse is not None and pulse < 0
            else ("扩张" if pulse is not None else None),
            "beta": round(beta, 2) if beta is not None else None,
            "r2": round(r2, 2) if r2 is not None else None,
            "divergence": div,
            "nl_series": [round(float(v) / 1000, 2) for v in nl.tail(180)],
            "nl_dates": [str(d.date()) for d in nl.tail(180).index],
            "btc_series": [round(float(v), 0) for v in btc.tail(180)],
            "btc_dates": [str(d.date()) for d in btc.tail(180).index],
        }
    return out


# ── etfs 子页 ────────────────────────────────────────────────────────────────


def etfs() -> dict:
    pool = _csv("etf/pool_prices.csv")

    out: dict = {"pool": [], "universe": None, "note": ""}
    if not pool.empty:
        for ticker, (name, cat) in ETF_POOL.items():
            if ticker not in pool.columns:
                continue
            s = pool[ticker].dropna()
            if len(s) < 25:
                continue
            w1, m1, m3 = _chg(s, 7), _chg(s, 30), _chg(s, 90)
            vol = None
            rets = s.pct_change().dropna().tail(20)
            if len(rets) >= 15:
                vol = float(rets.std() * np.sqrt(252) * 100)
            dd = None
            if len(s) >= 63:
                hi3 = s.tail(63).max()
                dd = (float(s.iloc[-1]) / float(hi3) - 1) * 100
            trend = (
                "趋势向上"
                if m1 is not None and m3 is not None and m1 > 0 and m3 > 0
                else (
                    "趋势向下"
                    if m1 is not None and m3 is not None and m1 < 0 and m3 < 0
                    else "趋势分化"
                )
            )
            out["pool"].append(
                {
                    "ticker": ticker,
                    "name": name,
                    "category": cat,
                    "last": round(float(s.iloc[-1]), 2),
                    "w1": round(w1, 2) if w1 is not None else None,
                    "m1": round(m1, 2) if m1 is not None else None,
                    "m3": round(m3, 2) if m3 is not None else None,
                    "vol20d": round(vol, 1) if vol is not None else None,
                    "dd3m": round(dd, 2) if dd is not None else None,
                    "trend": trend,
                }
            )
    uni = _read("etf/universe.csv")
    if not uni.empty:
        cat_counts = uni["category"].value_counts().to_dict()
        out["universe"] = {
            "total": int(len(uni)),
            "categories": {k: int(v) for k, v in cat_counts.items()},
        }
    return out


# ── analytics（期权/加密衍生品页公共入口） ───────────────────────────────────


def options_board() -> dict | None:
    """读取最新期权结构快照（13 标的看板）。"""
    files = sorted((ROOT / "data" / "options_structure").glob("20*.json"))
    if not files:
        return None
    return json.loads(files[-1].read_text(encoding="utf-8"))


def crypto_derivatives() -> dict | None:
    files = sorted((ROOT / "data" / "crypto_derivatives").glob("20*.json"))
    if not files:
        return None
    snap = json.loads(files[-1].read_text(encoding="utf-8"))
    snap["radar"] = crypto_radar(snap)
    return snap


# ── BTC 前瞻雷达（衍生品页；信号来自快照 + CFTC COT） ────────────────────────


def crypto_radar(snap: dict) -> dict:
    """7 信号加权雷达（规则引擎；LLM 预留——_llm_generate 返回 dict 则直接使用）。

    信号权重（缺失信号不纳入评分）：CME 机构头寸 15 / 资金费率 15 / 基差 carry 20 /
    期权牵引 10 / 永续 OI 10 / 散户多空比 10 / ETF 资金流 15（数据源未接入时置 null）。
    总分 = Σsign(信号)·权重 / Σ权重，映射到 -4..+4。
    """
    llm = _llm_generate("radar")
    if llm:
        return llm

    signals: list[dict] = []
    score = 0.0
    weight_total = 0.0

    def add(name: str, weight: int, value: float | None, desc: str) -> None:
        nonlocal score, weight_total
        if value is None:
            signals.append(
                {
                    "name": name,
                    "weight": weight,
                    "dir": 0,
                    "desc": desc + "（数据不可用，不计入评分）",
                }
            )
            return
        d = 1 if value > 0 else (-1 if value < 0 else 0)
        signals.append(
            {
                "name": name,
                "weight": weight,
                "dir": d,
                "value": round(value, 3),
                "desc": desc,
            }
        )
        score += d * weight
        weight_total += weight

    perp = (snap.get("perp") or {}).get("BTC") or {}
    opt = snap.get("options_BTC") or {}
    taker = (snap.get("taker") or {}).get("BTC") or []
    cme = snap.get("cme") or {}

    # CME 机构头寸：CME BTC 期货 OI 周环比（CFTC COT，timsun 口径 CME OI 7d 变化）
    cot = _csv("cot/cot.csv")
    cme_oi_chg = None
    if "BTC_OI" in cot.columns:
        oi = cot["BTC_OI"].dropna()
        if len(oi) >= 2:
            cme_oi_chg = (float(oi.iloc[-1]) / float(oi.iloc[-2]) - 1) * 100
    add(
        "CME 机构头寸",
        15,
        cme_oi_chg,
        f"CME OI 周变化 {cme_oi_chg:+.1f}%（CFTC）"
        if cme_oi_chg is not None
        else "CFTC COT 更新前待积累",
    )

    # 资金费率（年化）
    funding = perp.get("funding_annual")
    add(
        "资金费率",
        15,
        funding,
        f"Funding {(perp.get('funding_rate') or 0) * 100:.4f}%/8h，"
        f"年化约 {(funding or 0) * 100:.1f}%",
    )

    # 基差 carry
    basis = cme.get("basis_pct")
    add(
        "基差 carry",
        20,
        basis,
        f"Spread {basis:.2f}%，CME ${cme.get('fut_price')} vs 现货 ${cme.get('spot')}"
        if basis is not None
        else "CME 基差不可用",
    )

    # 期权牵引（Call Wall 上方压制 / Put Wall 支撑）
    # 口径（与 timsun 显示一致，pcr=0.59 时给出正分）：低 PCR = call 拥挤 =
    # 看涨期权集中；分值 = (1 − PCR)×10，pcr<1 → 正分
    pcr = opt.get("pcr")
    call_wall = opt.get("call_wall")
    spot_a = opt.get("spot_anchor")
    dist = (call_wall / spot_a - 1) * 100 if call_wall and spot_a else None
    direction = None
    if pcr is not None:
        direction = (1.0 - pcr) * 10  # 见上：PCR 越低 → 正分（timsun 口径）
    add(
        "期权牵引",
        10,
        direction,
        f"PCR {pcr:.2f}，Call Wall ${call_wall}（距现价 {dist:+.1f}%），"
        f"Put Wall ${opt.get('put_wall')}"
        if pcr is not None
        else "期权数据不可用",
    )

    # 永续 OI（7d 变化需历史快照积累；未积累前不纳入评分）
    oi_usd = perp.get("oi_usd")
    add(
        "永续 OI",
        10,
        None,
        f"OI ${(oi_usd or 0) / 1e9:.1f}B（7d 变化待历史积累）"
        if oi_usd
        else "永续 OI 不可用",
    )

    # 散户多空比（OKX taker 3 日买/卖成交额比）
    if len(taker) >= 3:
        buy = sum(r["buy"] for r in taker[:3])
        sell = sum(r["sell"] for r in taker[:3])
        ls = buy / sell if sell else None
        add(
            "散户多空比",
            10,
            (-(ls - 1) * 5 if ls is not None else None),  # 买盘过热 → 反向信号
            f"L/S {ls:.2f}（3 日 taker 买/卖成交额）"
            if ls is not None
            else "taker 数据不可用",
        )
    else:
        add("散户多空比", 10, None, "taker 数据不可用")

    # ETF 资金流：公开免费源未接入 → null
    add("ETF 资金流", 15, None, "公开免费源未接入（Farside 预留）")

    total = round(score / weight_total * 100 / 25) if weight_total else None
    verdict = (
        None
        if total is None
        else ("偏多" if total >= 3 else ("偏空" if total <= -3 else "震荡等待确认"))
    )
    return {
        "total": total,
        "confidence": round(weight_total / 95 * 100) if weight_total else 0,
        "verdict": verdict,
        "structure": "偏多结构"
        if total is not None and total >= 2
        else ("偏空结构" if total is not None and total <= -2 else "现货通道待确认"),
        "signals": signals,
    }
