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
CRYPTO_ROWS = [("BTC", "BTC"), ("ETH", "ETH")]


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
    """近 n 日收盘表（行=日期，列=标的；timsun 同款：最新在前倒序）。"""
    sub = df[[c for c in cols if c in df.columns]].dropna(how="all")
    sub = sub.tail(n).iloc[::-1]
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
    out: dict = {
        "indices": {},
        "breadth": {},
        "analysis": {},
        "chart": {},
        "analyst": {},
        "radar": {},
    }
    if p.empty:
        return out
    out["indices"] = _price_rows(p, EQUITY_ROWS)

    # 指数归一化走势（近 1 年，窗口首日为 0%）——timsun 同款
    out["chart"] = _index_normalized(p, EQUITY_ROWS)

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

        def _col(name: str) -> pd.Series:
            if name in abv.columns:
                return abv[name].dropna()
            return pd.Series(dtype=float)

        a50 = _col("ABV50")
        a200 = _col("ABV200")
        out["breadth"]["abv200"] = (
            round(float(a200.iloc[-1]), 1) if not a200.empty else None
        )
        out["breadth"]["abv50"] = (
            round(float(a50.iloc[-1]), 1) if not a50.empty else None
        )
        out["breadth"]["abv_date"] = (
            str(a50.index[-1].date()) if not a50.empty else None
        )
        # 昨日变化（timsun 同款：卡片副文本 ▲+0.0 / ▼-0.0 日期）
        for key, s in (("abv50", a50), ("abv200", a200)):
            if len(s) >= 2:
                out["breadth"][f"{key}_chg"] = round(float(s.iloc[-1] - s.iloc[-2]), 1)
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
    out["analyst"] = analyst_board()
    out["radar"] = ndx_radar()
    return out


def _index_normalized(p: pd.DataFrame, rows: list[tuple[str, str]]) -> dict:
    """近 1 年归一化 %：窗口首日 = 0，输出 dates + 每标的 series。

    基准取每列自身首个有效值（列起点不一致时后启标的仍能正常归一化，
    如 ETH 历史短于 BTC 的情况）。
    """
    df = p[[k for k, _ in rows if k in p.columns]].dropna(how="all")
    if df.empty:
        return {"dates": [], "series": {}}
    df = df.dropna(how="all").tail(252)
    out = {"dates": [str(d.date()) for d in df.index]}
    out["series"] = {}
    for k, _ in rows:
        if k not in df.columns:
            continue
        base = df[k].dropna()
        base0 = float(base.iloc[0]) if not base.empty else None
        out["series"][k] = [
            round((float(v) / base0 - 1) * 100, 2) if pd.notna(v) and base0 else None
            for v in df[k]
        ]
    return out


def _latest_targets() -> pd.DataFrame:
    """最新交易日全部成分目标价（长表，一票一行）。"""
    df = _csv("analyst/ndx_targets.csv")
    if df.empty:
        return df
    latest = df.index.max()
    return df[df.index == latest]


def _industry_totals(comp: pd.DataFrame) -> dict[str, int]:
    """行业内成分总数（timsun 覆盖 X/Y 的分母）。"""
    c = comp.rename(columns={"category": "industry"})
    return c.groupby("industry")["ticker"].nunique().to_dict()


def analyst_board() -> dict:
    """Nasdaq 100 分析师目标价（timsun /assets/equities 面板）。

    指标：覆盖率 / 等权平均空间 / 去极值典型 / 高于现价占比 / 平均分析师数,
    榜单：上行 Top10 / 下行 Top10 / 分歧 Top10, 行业表 + 完整表。
    """
    df = _latest_targets()
    if df.empty:
        return {"rows": 0}
    comp = _read("analyst/ndx_components.csv")
    total = len(comp)
    up = df[df["upside"].notna()]
    n = len(up)
    if n < 10:
        return {"rows": n}

    # 去极值典型：掐头去尾各 10%（timsun 同款口径——样本越高噪音越低）
    cuts = max(1, int(n * 0.1))
    trimmed = up["upside"].sort_values().iloc[cuts:-cuts]

    def row(t: pd.Series) -> dict:
        return {
            "ticker": t["ticker"],
            "company": t["company"],
            "industry": t["industry"],
            "price": float(t["price"]) if pd.notna(t["price"]) else None,
            "mean": float(t["target_mean"]) if pd.notna(t["target_mean"]) else None,
            "high": float(t["target_high"]) if pd.notna(t["target_high"]) else None,
            "low": float(t["target_low"]) if pd.notna(t["target_low"]) else None,
            "upside": round(float(t["upside"]), 2),
            "analysts": int(t["analysts"]) if pd.notna(t["analysts"]) else None,
            "rating": t.get("rating"),
        }

    tops = up.nlargest(10, "upside")
    bottoms = up.nsmallest(10, "upside")
    spread = up.copy()
    spread["_sp"] = spread["target_high"] - spread["target_low"]
    divs = spread.dropna(subset=["_sp"]).nlargest(10, "_sp")

    industry = (
        up.groupby("industry")
        .agg(
            coverage=("ticker", "count"),
            avg_space=("upside", "mean"),
            med_space=("upside", "median"),
            avg_analysts=("analysts", "mean"),
        )
        .reset_index()
    )
    # 行业内成分总数（分母，timsun 同款 X/Y）
    ind_total = _industry_totals(comp)
    industry["total_ind"] = industry["industry"].map(ind_total).fillna(0).astype(int)
    industry = industry.sort_values("avg_space", ascending=False)
    industries = []
    for _, r in industry.iterrows():
        industries.append(
            {
                "industry": r["industry"],
                "coverage": int(r["coverage"]),
                "total_ind": int(r["total_ind"]),
                "avg": round(float(r["avg_space"]), 2),
                "med": round(float(r["med_space"]), 2),
                "analysts": round(float(r["avg_analysts"]), 1),
            }
        )

    rich = (
        up[
            [
                "ticker",
                "company",
                "industry",
                "price",
                "target_mean",
                "target_high",
                "target_low",
                "upside",
                "analysts",
                "rating",
            ]
        ]
        .rename(
            columns={"target_mean": "mean", "target_high": "high", "target_low": "low"}
        )
        .sort_values("upside", ascending=False)
        .to_dict("records")
    )
    for r in rich:
        for k in ("price", "mean", "high", "low"):
            r[k] = round(float(r[k]), 2) if pd.notna(r[k]) else None

    return {
        "date": str(df.index.max().date()),
        "rows": n,
        "total": total,
        "coverage_pct": round(n / total * 100, 1) if total else None,
        "avg_upside": round(float(up["upside"].mean()), 2),
        "med_upside": round(float(up["upside"].median()), 2),
        "trim_upside": round(float(trimmed.mean()), 2) if len(trimmed) else None,
        "above_share": round(float((up["upside"] > 0).mean() * 100), 1),
        "above_cnt": int((up["upside"] > 0).sum()),
        "avg_analysts": round(float(up["analysts"].mean()), 1),
        "tops": [row(t) for _, t in tops.iterrows()],
        "bottoms": [row(t) for _, t in bottoms.iterrows()],
        "divs": [row(t) for _, t in divs.iterrows()],
        "industries": industries,
        "table": rich,
    }


def ndx_radar() -> dict:
    """Nasdaq 100 成分股雷达：价格可得性 + 今日/短中期动能 + 行业强弱 + 成分行情。"""
    comp = _read("analyst/ndx_components.csv")
    # components 无 date 列（ticker/company/category），按 ticker 建立映射
    comp = comp.rename(columns={"category": "industry"})
    px = _csv("analyst/ndx_prices.csv")
    if comp.empty or px.empty:
        return {"rows": 0}
    price_cols = [c for c in px.columns if c != "date"]
    if not price_cols:
        return {"rows": 0}

    last = px.iloc[-1]
    has = [c for c in price_cols if pd.notna(last.get(c))]
    n = len(has)
    if n < 10:
        return {"rows": n}

    # 每票：1D / 5D / 20D / 60D 涨跌 + 50D/200D 均线上方 + 趋势
    stats = {}
    for t in has:
        s = px[t].dropna()
        if len(s) < 70:
            continue
        chg = {}
        for w in (1, 5, 20, 60):
            if len(s) > w and s.iloc[-w - 1]:
                chg[w] = (float(s.iloc[-1]) / float(s.iloc[-w - 1]) - 1) * 100
        ma50 = float(s.tail(50).mean())
        ma200 = float(s.tail(200).mean()) if len(s) >= 200 else None
        price = float(s.iloc[-1])
        stats[t] = {
            "price": price,
            "chg1": round(chg.get(1), 2) if 1 in chg else None,
            "chg5": round(chg.get(5), 2) if 5 in chg else None,
            "chg20": round(chg.get(20), 2) if 20 in chg else None,
            "chg60": round(chg.get(60), 2) if 60 in chg else None,
            "above50": price > ma50,
            "above200": price > ma200 if ma200 else None,
        }

    # 行业强弱（等权）：1D / 20D / 50DMA+ 占比
    comp_map = comp.set_index("ticker")
    by_industry: dict[str, list[str]] = {}
    for t in has:
        if t not in stats:
            continue
        industry = comp_map.loc[t, "industry"] if t in comp_map.index else "未知"
        by_industry.setdefault(industry, []).append(t)
    ind_total = _industry_totals(comp)
    industries = []
    for ind, tk in by_industry.items():
        if len(tk) < 1:
            continue
        chg1 = [stats[t]["chg1"] for t in tk if stats[t]["chg1"] is not None]
        chg20 = [stats[t]["chg20"] for t in tk if stats[t]["chg20"] is not None]
        industries.append(
            {
                "industry": ind,
                "coverage": len(tk),
                "total_ind": int(ind_total.get(ind, len(tk))),
                "chg1": round(sum(chg1) / len(chg1), 2) if chg1 else None,
                "chg20": round(sum(chg20) / len(chg20), 2) if chg20 else None,
                "above50": round(
                    sum(1 for t in tk if stats[t]["above50"]) / len(tk) * 100, 0
                ),
            }
        )
    industries.sort(key=lambda x: (x["chg20"] is None, -(x["chg20"] or 0)))

    today_up = sum(1 for t in stats if (stats[t]["chg1"] or 0) > 0)
    today_up_pct = round(today_up / len(stats) * 100, 1) if stats else None
    # 每票最后有效交易日（timsun 完整行情表日期列——停牌股最后日期更早）
    last_dates: dict[str, str] = {}
    for t in stats:
        s = px[t].dropna()
        last_dates[t] = (
            str(s.index[-1].date()) if not s.empty else str(px.index[-1].date())
        )

    rows = []
    for t, st in stats.items():
        meta = comp_map.loc[t] if t in comp_map.index else {}
        rows.append(
            {
                "ticker": t,
                "company": meta.get("company", ""),
                "industry": meta.get("industry", ""),
                "price": round(st["price"], 2),
                "chg1": st["chg1"],
                "chg5": st["chg5"],
                "chg20": st["chg20"],
                "chg60": st["chg60"],
                "above50": st["above50"],
                "above200": st["above200"],
                "date": last_dates.get(t),
            }
        )
    rows.sort(key=lambda r: (r["chg20"] is None, -(r["chg20"] or 0)))

    strong20 = [r for r in rows if r["chg20"] is not None][:8]
    weak20 = sorted(
        [r for r in rows if r["chg20"] is not None], key=lambda r: r["chg20"]
    )[:8]

    # 近 20 个交易日指数收盘
    p = asset_prices()
    recent = []
    if not p.empty:
        pp = p[[k for k, _ in EQUITY_ROWS if k in p.columns]].dropna(how="all").tail(20)
        recent = [
            {
                "date": str(d.date()),
                "SPX": round(float(pp.loc[d, "SPX"]), 2) if "SPX" in pp else None,
                "NDX": round(float(pp.loc[d, "NDX"]), 2) if "NDX" in pp else None,
                "DJI": round(float(pp.loc[d, "DJI"]), 2) if "DJI" in pp else None,
                "RUT": round(float(pp.loc[d, "RUT"]), 2) if "RUT" in pp else None,
            }
            for d in pp.index
        ]

    return {
        "date": str(px.index[-1].date()),
        "rows": len(rows),
        "total": n,
        "price_share": round(len(stats) / n * 100, 1) if n else None,
        "today_up": today_up,
        "today_up_pct": today_up_pct,
        "above50_pct": round(
            sum(1 for s2 in stats.values() if s2["above50"]) / len(stats) * 100, 1
        )
        if stats
        else None,
        "above200_pct": round(
            sum(1 for s2 in stats.values() if s2["above200"])
            / sum(1 for s2 in stats.values() if s2["above200"] is not None)
            * 100,
            1,
        )
        if any(s2["above200"] is not None for s2 in stats.values())
        else None,
        "avg20": round(sum(s2["chg20"] or 0 for s2 in stats.values()) / len(stats), 2)
        if stats
        else None,
        "med20": round(
            float(
                pd.Series(
                    [s2["chg20"] for s2 in stats.values() if s2["chg20"] is not None]
                ).median()
            ),
            2,
        )
        if any(s2["chg20"] is not None for s2 in stats.values())
        else None,
        "verdict": _radar_verdict(today_up_pct),
        "industries": industries,
        "strong20": strong20,
        "weak20": weak20,
        "table": rows,
        "recent": recent,
    }


def _radar_verdict(today_up_pct: float | None) -> str:
    """雷达标签（timsun 同款语义）：上涨家数 2/3 附近 = 结构分化。"""
    if today_up_pct is None:
        return "—"
    if today_up_pct >= 70:
        return "全面强势"
    if today_up_pct >= 50:
        return "分化修复"
    return "内部走弱"


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
FIN_SYMBOLS = ("NQ", "ES", "RTY", "VX", "ZF", "ZN", "ZB", "EUR", "JPY", "DXY", "BTC")
COMMODITY_SYMBOLS = ("GC", "SI", "HG", "CL", "NG")


def _ls_cols(sym: str) -> tuple[str, str]:
    """(long 列, short 列)：金融 = HEDGE_L/HEDGE_S，商品 = MM_L/MM_S。"""
    if sym in FIN_SYMBOLS:
        return f"{sym}_HEDGE_L", f"{sym}_HEDGE_S"
    return f"{sym}_MM_L", f"{sym}_MM_S"


# CFTC 合约元数据（timsun 同款展示：报告名 · 报告 code / 报告类型；
# code 在 cot.csv 未存，静态保留）
COT_META = {
    "NQ": ("纳斯达克100 E-mini", "NASDAQ MINI", "209742", "金融类 TFF"),
    "ES": ("标普500 E-mini", "E-MINI S&P 500", "13874A", "金融类 TFF"),
    "RTY": ("罗素2000 E-mini", "E-MINI RUSSELL 2000", "132741", "金融类 TFF"),
    "VX": ("VIX 期货", "VIX FUTURES", "1170E1", "金融类 TFF"),
    "ZF": ("5年美债", "UST 5Y NOTE", "044601", "金融类 TFF"),
    "ZN": ("10年美债", "UST 10Y NOTE", "043602", "金融类 TFF"),
    "ZB": ("30年美债", "UST BOND", "020601", "金融类 TFF"),
    "EUR": ("欧元", "EURO FX", "099741", "金融类 TFF"),
    "JPY": ("日元", "JAPANESE YEN", "097741", "金融类 TFF"),
    "DXY": ("美元指数", "USD INDEX", "098662", "金融类 TFF"),
    "GC": ("黄金", "GOLD", "088691", "商品类 DISAGG"),
    "SI": ("白银", "SILVER", "084691", "商品类 DISAGG"),
    "HG": ("铜", "COPPER", "089692", "商品类 DISAGG"),
    "CL": ("WTI 原油", "WTI CRUDE OIL", "067651", "商品类 DISAGG"),
    "NG": ("天然气", "NATURAL GAS", "023651", "商品类 DISAGG"),
    "BTC": ("比特币", "BITCOIN", "133741", "金融类 TFF"),
}
COT_TIPS = {
    "index_vol": "股指仓位主要看风险偏好是否拥挤。",
    "rates": "美债仓位主要看市场是在押注利率下行，还是继续押注收益率上行。",
    "fx": "外汇仓位看美元与非美货币的拥挤方向。",
    "commodities": "商品仓位看再通胀交易是否拥挤，以及趋势资金有没有追随。",
}


def _cot_reading(label: str, week_dir: str | None) -> str:
    """合约解读第一句：按判读桶给出方向含义（timsun 口径）。"""
    if label.startswith("极度偏多"):
        return (
            "仓位处在极度偏多区间，说明趋势资金已经明显站队；继续上涨需要新增买盘确认。"
        )
    if label.startswith("极度偏空"):
        return (
            "仓位处在极度偏空区间，说明市场共识很悲观；若价格抗跌，容易出现空头回补。"
        )
    if label == "偏多":
        return "仓位偏多，趋势资金倾向支持上行，但并未到最拥挤状态。"
    if label == "偏空":
        return "仓位偏空，说明资金仍偏防御；若价格转强，需要观察净空是否开始回补。"
    return "仓位处在中性区间，单独看 CFTC 还不能给出强方向结论。"


def _cot_week_text(week_dir: str | None) -> str:
    if week_dir == "增仓":
        return "本周净仓增加，说明资金边际上在加多或减空。"
    if week_dir == "减仓":
        return "本周净仓下降，说明资金边际上在减多或加空。"
    return ""


# 组判断模板（非极端时 timsun 同款；极端时换拥挤警示）
COT_VERDICT = {
    "index_vol": "股指投机仓位不极端，价格方向更多要看盈利、利率和期权结构确认。",
    "rates": "利率仓位不极端，长端利率的下一步更依赖通胀、财政供给和美联储定价。",
    "fx": "外汇仓位不极端，汇率方向更依赖利差和美元流动性。",
    "commodities": "商品仓位不极端，价格更需要库存、地缘和美元方向确认。",
}
COT_VERDICT_EXTREME = {
    "index_vol": "股指投机仓位较极端，拥挤状态下价格反向时波动会放大，关注 "
    + "{f}"
    + "。",
    "rates": "利率仓位较极端，拥挤状态下数据反向时的波动会放大，关注 " + "{f}" + "。",
    "fx": "外汇仓位较极端，拥挤状态下汇率反向波动会放大，关注 " + "{f}" + "。",
    "commodities": "商品仓位较极端，拥挤状态下价格反向时的波动会放大，关注 "
    + "{f}"
    + "。",
}


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
    # timsun 同款组合同：股指组无 RTY、外汇含 DXY、商品仅 GC/CL（其余合约仍拉取入库）
    groups_def = {
        "index_vol": ["NQ", "ES", "VX"],
        "rates": ["ZF", "ZN", "ZB"],
        "fx": ["EUR", "DXY", "JPY"],
        "commodities": ["GC", "CL"],
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
            # 2 年窗口（104 周）百分位——timsun 同款 rank 中点法 (count_less + 0.5)/n
            s2y = s.tail(104)
            pct = float(((s2y < net_val).sum() + 0.5) / len(s2y) * 100)
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
            week_dir = (
                "增仓"
                if wk is not None and wk > 0
                else ("减仓" if wk is not None else None)
            )
            name, cme, code, kind = COT_META.get(sym, (sym, sym, "", ""))
            # 2 年迷你图：投机方（金融=HEDGE，商品=MM）vs 资管（金融=ASSET，商品无）
            if sym in FIN_SYMBOLS:
                spec = cot[f"{sym}_HEDGE_L"] - cot[f"{sym}_HEDGE_S"]
                asset = cot[f"{sym}_ASSET_L"] - cot[f"{sym}_ASSET_S"]
            else:
                spec = cot[f"{sym}_MM_L"] - cot[f"{sym}_MM_S"]
                asset = pd.Series(index=cot.index, dtype=float)
            tail = cot.index[-53:]  # 迷你图窗口 1 年（52 周 + 当前），timsun 同款
            spark = {
                "dates": [str(d.date()) for d in tail],
                "spec": [
                    None if pd.isna(v) else round(float(v), 0)
                    for v in spec.reindex(tail)
                ],
                "asset": [
                    None if pd.isna(v) else round(float(v), 0)
                    for v in asset.reindex(tail)
                ],
            }
            contracts.append(
                {
                    "symbol": sym,
                    "name": name,
                    "cme": cme,
                    "code": code,
                    "kind": kind,
                    "net": round(net_val, 0),
                    "pct_2y": round(pct, 1),
                    "label": label,
                    "week_chg": wk,
                    "week_dir": week_dir,
                    "reading": _cot_reading(label, week_dir)
                    + " "
                    + _cot_week_text(week_dir),
                    "long": round(float(longs[sym].iloc[-1]), 0),
                    "short": round(float(shorts[sym].iloc[-1]), 0),
                    "oi": round(float(cot[f"{sym}_OI"].iloc[-1]), 0)
                    if f"{sym}_OI" in cot.columns
                    else None,
                    "spark": spark,
                }
            )
            all_pct.append(abs(pct - 50) * 2)
        if contracts:
            worst = max(contracts, key=lambda c: abs(c["pct_2y"] - 50))
            bulls = sum(
                1
                for c in contracts
                if c["label"].endswith("看多") or c["label"] == "偏多"
            )
            bears = sum(
                1
                for c in contracts
                if c["label"].endswith("看空") or c["label"] == "偏空"
            )
            # 组判断：组内极端合约 ≤ 半数 → 非极端（方向留给宏观确认）；
            # 多数极端 → 拥挤警示
            ext_n = sum(1 for c in contracts if c["label"].startswith("极度"))
            if ext_n and ext_n / len(contracts) > 0.5:
                verdict = COT_VERDICT_EXTREME[g].format(
                    f=f"{worst['name']}（{worst['pct_2y']:.1f}）"
                )
            else:
                verdict = COT_VERDICT[g]
            out["groups"].append(
                {
                    "name": g,
                    "contracts": contracts,
                    "summary": (
                        f"最极端：{worst['symbol']}（2 年百分位 "
                        f"{worst['pct_2y']:.1f}，{worst['label']}）"
                    ),
                    "verdict": verdict,
                    "focus": {
                        "symbol": worst["symbol"],
                        "name": worst.get("name", worst["symbol"]),
                        "pct_2y": worst["pct_2y"],
                    },
                    "bulls": bulls,
                    "bears": bears,
                }
            )
    if all_pct:
        out["crowding"] = round(sum(all_pct) / len(all_pct), 1)
        all = [c for g in out["groups"] for c in g["contracts"]]
        bulls = sum(
            1 for c in all if c["label"].endswith("看多") or c["label"] == "偏多"
        )
        bears = sum(
            1 for c in all if c["label"].endswith("看空") or c["label"] == "偏空"
        )
        ext = [c for c in all if c["label"].startswith("极度")]
        extremes = sorted(all, key=lambda c: abs(c["pct_2y"] - 50), reverse=True)[:3]
        out["overview"] = {
            "bias": "偏多" if bulls > bears else ("偏空" if bears > bulls else "中性"),
            "counts": {"bull": bulls, "bear": bears, "extreme": len(ext)},
            "extremes": [
                {"symbol": c["symbol"], "name": c["name"], "pct_2y": c["pct_2y"]}
                for c in extremes
            ],
        }
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
    # 美元广度按对统计（每组均值供展示；weak/strong/total 计数基于对级压力）
    weak_count = sum(
        1 for r in rows if r["pressure"] is not None and r["pressure"] < -1
    )
    strong_count = sum(
        1 for r in rows if r["pressure"] is not None and r["pressure"] > 1
    )
    total_pairs = sum(1 for r in rows if r["pressure"] is not None)
    overall = _fx_verdict(weak_count, strong_count, total_pairs)
    # 各组全量对数量（前端“有数据 N/M”分母；dashboard 只含有历史深度的对）
    group_pairs: dict[str, int] = {}
    for _t, _n, g in FX_PAIRS.values():
        group_pairs[g] = group_pairs.get(g, 0) + 1
    return {
        "breadth": {"groups": breadth, "overall": overall, "group_pairs": group_pairs},
        "dashboard": rows,
    }


def _fx_verdict(weak_count: int, strong_count: int, total_pairs: int) -> dict:
    """美元广度判定（对级）：0 对 → 数据不足；半数以上走弱 → 走弱；半数以上走强 → 走强；
    两者都不足半数且强弱均有 → 分化；全为中性（−1..+1）→ 中性。"""
    if total_pairs == 0:
        return {"weak": 0, "strong": 0, "total": 0, "verdict": "数据不足"}
    if weak_count >= total_pairs / 2:
        verdict = "美元走弱"
    elif strong_count >= total_pairs / 2:
        verdict = "美元走强"
    elif weak_count == 0 and strong_count == 0:
        verdict = "美元中性"
    else:
        verdict = "分化"
    return {
        "weak": weak_count,
        "strong": strong_count,
        "total": total_pairs,
        "verdict": verdict,
    }


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


def _reg_beta(
    nl: pd.Series, px: pd.Series, n: int = 90
) -> tuple[float | None, float | None]:
    """90 日回归：NL 与资产日收益率（beta, r2）；NL 为百万美元口径。"""
    r = pd.concat([nl, px], axis=1, keys=["NL", "PX"]).dropna()
    rets = r.pct_change().dropna().tail(n)
    if len(rets) < 60:
        return None, None
    x = rets["NL"].values
    y = rets["PX"].values
    if np.std(x) > 0 and np.std(y) > 0:
        beta = float(np.cov(x, y)[0, 1] / np.var(x))
        r2 = float(np.corrcoef(x, y)[0, 1] ** 2)
        return beta, r2
    return None, None


def crypto() -> dict:
    """加密货币页：BTC/ETH 卡片 + 净流动性溢出（timsun 对齐口径）+ 走势归一化。

    口径（与 timsun /assets/crypto 一致）：net/fed 以 T、tga/rrp 以 B、
    pulse_20d 为 20 日绝对变化（B）、divergence.nl 为 30 日绝对变化（T）。
    """
    p = asset_prices()
    out = {
        "cards": _price_rows(p, CRYPTO_ROWS),
        "recent": _recent_prices(p, [k for k, _ in CRYPTO_ROWS]),
        "trend": _index_normalized(p, CRYPTO_ROWS),
    }
    # BTC/ETH 价格比（走势图右轴；与归一化图同窗同网格）
    t = out.get("trend") or {}
    if t.get("dates"):
        grid = pd.to_datetime(t["dates"])
        bp = p["BTC"].reindex(grid)
        ep = p["ETH"].reindex(grid)
        t.setdefault("series", {})["RATIO"] = [
            round(float(b) / float(e), 2)
            if pd.notna(b) and pd.notna(e) and float(e)
            else None
            for b, e in zip(bp, ep)
        ]
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
        nl = wide["NL"].dropna()  # 百万美元
        # 90 日回归（Beta / R²；SPX 同样口径用于 β 对比）
        beta, r2 = _reg_beta(nl, btc)
        spx = p["SPX"].dropna() if "SPX" in p.columns else pd.Series(dtype=float)
        spx_beta, _ = _reg_beta(nl, spx)
        # 20 日脉冲：绝对变化（B）
        pulse = (
            (float(nl.iloc[-1]) - float(nl.iloc[-21])) / 1000 if len(nl) >= 21 else None
        )
        # 30 日背离：NL 绝对变化（T）+ BTC 涨跌幅（%）
        div = None
        if len(nl) >= 31 and len(btc) >= 31:
            div = {
                "nl": round((float(nl.iloc[-1]) - float(nl.iloc[-31])) / 1e6, 2),
                "btc": round((float(btc.iloc[-1]) / float(btc.iloc[-31]) - 1) * 100, 1),
            }
        # 180 日历日窗口（timsun 同款：日网格含周末，NL ffill 成连续线；
        # 而非 180 个交易日——否则窗口跨度变 260+ 天、形状错位）
        end = nl.index[-1]
        grid = pd.date_range(end - pd.Timedelta(days=179), end)
        nld = nl.reindex(grid).ffill().dropna()
        btc_al = btc.reindex(nld.index)
        # 交易含义（规则引擎；LLM 预留接 _llm_generate 后替换）
        parts: list[str] = []
        if beta is not None and spx_beta:
            parts.append(
                f"BTC 对美元流动性变化的敏感度约是标普的 {beta / spx_beta:.1f} 倍。"
            )
        if pulse is not None:
            if pulse < 0:
                parts.append(
                    f"当前脉冲为负（{round(pulse):.0f}B），历史经验下建议等待脉冲转正信号再建仓。"
                )
            else:
                parts.append(
                    f"当前脉冲为正（+{round(pulse):.0f}B），加密流动性顺风延续。"
                )
        if div and div["nl"] < 0 and div["btc"] > 0:
            parts.append("注意：净流动性收缩但 BTC 上涨，警惕回撤风险。")
        out["liquidity"] = {
            "net": round(float(nl.iloc[-1]) / 1e6, 2),  # T
            "fed": round(float(wide["WALCL"].dropna().iloc[-1]) / 1e6, 2),  # T
            "tga": round(float(wide["WTREGEN"].dropna().iloc[-1]) / 1000, 1),  # B
            "rrp": round(float(wide["RRP"].dropna().iloc[-1]), 2),  # B（FRED 已是 B）
            "pulse_20d": round(pulse) if pulse is not None else None,
            "pulse_state": "收缩"
            if pulse is not None and pulse < 0
            else ("扩张" if pulse is not None else None),
            "beta": round(beta, 2) if beta is not None else None,
            "r2": round(r2, 2) if r2 is not None else None,
            "spx_beta": round(spx_beta, 2) if spx_beta is not None else None,
            "divergence": div,
            "trading": " ".join(parts) or None,
            "nl_series": [round(float(v) / 1e6, 2) for v in nld],
            "nl_dates": [str(d.date()) for d in nld.index],
            "btc_series": [round(float(v), 0) if pd.notna(v) else None for v in btc_al],
            "btc_dates": [str(d.date()) for d in nld.index],
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
    """读取最新期权结构快照（13 标的看板），附加每标的规则引擎结构解读。"""
    files = sorted((ROOT / "data" / "options_structure").glob("20*.json"))
    if not files:
        return None
    board = json.loads(files[-1].read_text(encoding="utf-8"))
    for s in board.get("symbols", {}).values():
        s["narrative"] = _options_narrative(s)
    return board


def _options_narrative(s: dict) -> dict:
    """Timsun 风格结构解读（规则引擎；LLM 预留——接入后同构 dict 直接覆盖）。

    以 Gamma Flip、墙位、Regime 为骨架生成 6 块结论：gamma 定调 / 区间结构 /
    波动率结构 / 关键监控水平 / 当前环境下不宜 / 跨资产联动 + P/C 提示。
    """
    flip = s.get("gamma_flip")
    flip_pct = s.get("flip_dist")  # 正=现价在 Flip 上方（局部正 Gamma）
    cw = (s.get("call_wall") or {}).get("strike")
    pw = (s.get("put_wall") or {}).get("strike")
    net_gex = s.get("net_gex")
    pcr = s.get("pcr_oi_atm") or s.get("pcr_oi")
    iv_slope = s.get("iv_slope")
    near7 = s.get("charm_near7")
    name = s.get("symbol", "")

    local_pos = (
        flip_pct is not None and flip_pct >= 0
    )  # 现价在 Flip 上方 → 局部正 Gamma
    flip_txt = f"{flip:.0f}" if flip else "—"

    def fv(x: float | None, unit: str = "") -> str:
        return "—" if x is None else f"{x:,.0f}{unit}"

    # 1) Gamma 定调
    gamma_title = (
        "正 Gamma：波动更容易被压制" if local_pos else "负 Gamma：波动更容易放大"
    )
    range_a, range_b = (pw, cw) if pw is not None and cw is not None else (flip, cw)
    range_text = (
        f"{range_a:.0f}–{range_b:.0f}"
        if range_a is not None and range_b is not None
        else "墙位区间"
    )
    gex_text = "—" if net_gex is None else f"{net_gex:+.2f}B"
    if local_pos:
        gamma_text = (
            f"{name} 现价位于 Gamma Flip 上方，局部处于正 Gamma 环境。"
            f"全部期权合约合计的 Net GEX {gex_text} 仅作为强度参考，价格更容易"
            f"围绕关键墙位震荡，优先观察 {range_text}"
        )
    else:
        gamma_text = (
            f"{name} 现价位于 Gamma Flip 下方，局部处于负 Gamma 环境。"
            f"全部期权合约合计的 Net GEX {gex_text} 仅作为强度参考，价格更容易"
            f"放大波动，跌破关键支撑后波动扩散风险上升，优先观察 {range_text}"
        )

    # 2) 区间结构
    if local_pos:
        meaning = (
            f"Put Wall {fv(pw)} 与 Call Wall {fv(cw)} "
            "更适合被当作结构边界观察，突破前不宜把贴边波动直接外推成趋势。"
        )
        invalid = f"跌破 Gamma Flip {flip_txt} 后切换为负 Gamma 框架"
        risk = f"若跌破 Gamma Flip {flip_txt}，原有区间压制逻辑失效"
        direction = (
            f"接近 Call Wall {fv(cw)} 时上行动能容易放缓；"
            f"接近 Put Wall {fv(pw)} 时下方支撑需要重新验证"
        )
        avoid = [
            f"不宜把站上 {fv(cw)} 前的贴边波动直接解读为有效突破",
            "不宜把低波动环境误读成宏观风险消失；正 Gamma 只是短期结构压制",
            "不宜在区间中部给出强方向结论，关键是价格相对 Flip 和墙位的位置",
        ]
        vol_meaning = (
            "正 Gamma 且远离 Flip 时，隐含波动率通常更容易被压制；"
            "若价格重新靠近 Flip 或 VIX 抬升，低波动假设需要降权。"
        )
        vol_risk = (
            "VIX 突然抬升或跌破 Gamma Flip " + flip_txt + " 时，正 Gamma 解释力下降"
        )
    else:
        meaning = (
            f"现价位于 Gamma Flip {flip_txt} 下方，负 Gamma 环境下波动易被放大；"
            "突破前的贴边波动不宜直接外推成趋势。"
        )
        invalid = f"站上 Gamma Flip {flip_txt} 后切换为正 Gamma 框架"
        risk = f"若站上 Gamma Flip {flip_txt}，原有波动放大逻辑失效"
        direction = (
            f"跌破 Put Wall {fv(pw)} 后波动扩散风险上升；"
            f"接近 Call Wall {fv(cw)} 时关注负 Gamma 带来的快速反弹"
        )
        avoid = [
            "负 Gamma 环境下不宜在贴近墙位处追空，反弹容易快速",
            f"不宜把跌破 Gamma Flip {flip_txt} 后的加速下跌直接外推至趋势",
            "不宜忽视正 Delta 敞口的支撑；负 Gamma 放大的是双向波动",
        ]
        vol_meaning = (
            "负 Gamma 环境中隐含波动率通常被放大；"
            "若价格站上 Flip 或 IV 开始回落，高波动假设需要降权。"
        )
        vol_risk = (
            "价格站上 Gamma Flip " + flip_txt + " 或 IV 快速回落时，负 Gamma 解释力下降"
        )

    # 3) 波动率结构
    vol_direction = (
        "波动率回落更像结构结果，不等同于基本面风险消失"
        if (iv_slope or 0) >= 0
        else "IV 期限结构倒挂，短期事件定价高，注意到期后的回落"
    )

    # 4) 跨资产联动
    if pcr is not None and pcr >= 1.3:
        cross = (
            f"Put/Call OI 比 {pcr:.2f} 偏高 — 防御性仓位重，若这些 put 集中到期，"
            "对冲平仓可能释放正 Gamma，但需要结合到期日和价格位置验证"
        )
    elif pcr is not None:
        cross = f"Put/Call OI 比 {pcr:.2f}（ATM ±10% 口径）中性 — 市场多空仓位相对均衡"
    else:
        cross = "Put/Call OI 比暂无数据"

    return {
        "gamma": {"title": gamma_title, "text": gamma_text},
        "range": {
            "title": "区间结构（Range Regime）",
            "confidence": "高",
            "meaning": meaning,
            "watch": "观察：现价、Gamma Flip、Call Wall、Put Wall、VIX",
            "direction": direction,
            "invalid": invalid,
            "risk": risk,
        },
        "vol": {
            "title": "波动率结构",
            "confidence": "中",
            "meaning": vol_meaning,
            "watch": "观察：IV 期限结构、VIX、Gamma Flip 距离、到期日 Gamma 集中度",
            "direction": vol_direction,
            "risk": vol_risk,
        },
        "levels": [
            {
                "value": flip_txt,
                "label": "Gamma Flip",
                "desc": "正/负 Gamma 分界线。穿越此价位后结构解释需要切换",
            },
            {
                "value": fv(cw),
                "label": "Call Wall",
                "desc": "上方期权敞口集中位。接近时观察上涨动能是否放缓",
            },
            {
                "value": fv(pw),
                "label": "Put Wall",
                "desc": "下方期权敞口集中位。跌破后观察波动是否扩散",
            },
        ],
        "avoid": avoid,
        "cross": cross,
        "near7": near7,
    }


ETF_COLS = [
    "IBIT",
    "FBTC",
    "BITB",
    "ARKB",
    "BTCO",
    "EZBC",
    "BRRR",
    "HODL",
    "BTCW",
    "MSBT",
    "GBTC",
    "BTC",
    "Total",
]


def _etf_flows() -> dict:
    """Farside BTC 现货 ETF 资金流聚合（衍生日页 ETF FLOWS 模块）。

    数据源 data/etf_flows/etf_flows.csv（M USD 日频，观测日 key）。
    返回：最近有效值 / 5d·30d 累计 / 累计 AUM / Top 3 / 时效标记。
    stale 定义：最新数据日距今天交易日间隔 > 3（覆盖周末，含 Farside
    每日 21:00 UTC 更新的 1 天滞后）。
    """
    df = _csv("etf_flows/etf_flows.csv")
    if df.empty:
        return {"available": False}
    df = df[[c for c in ETF_COLS if c in df.columns]]
    last = df.dropna(subset=["Total"]).iloc[-1]
    latest = last.name
    # 交易日间隔（weekday 计数）
    trade_days = pd.bdate_range(latest, pd.Timestamp.today())
    stale = len(trade_days) > 3  # 周五数据周一跑 = 3 天（含更新滞后 1 天）
    recent = df.loc[:latest].tail(30)  # 30d 窗口（含 NaN 行保持日序）

    def _sum5() -> float | None:
        s = df.loc[:latest].tail(8).dropna(subset=["Total"]).tail(5)["Total"]
        return float(s.sum() / 1000) if len(s) else None  # M→B USD

    def _sum30() -> float | None:
        s = recent.dropna(subset=["Total"])["Total"]
        return float(s.sum() / 1000) if len(s) else None

    cum = df["Total"].cumsum()  # 自 2024-01-11 起累计净流入（M USD）
    top = []
    if len(cum):
        per = df.drop(columns=["Total"]).sum()
        top = [str(k) for k in per.sort_values(ascending=False).head(3).index]
    return {
        "available": True,
        "latest": str(latest.date()),
        "last_flow_musd": float(last["Total"]),
        "sum5d_busd": _sum5(),
        "sum30d_busd": _sum30(),
        "cum_aum_busd": round(float(cum.iloc[-1] / 1000), 1),
        "top": top,
        "stale": stale,
    }


def _crypto_basis() -> dict:
    """CME BTC 基差日序列聚合（衍生日页机构基差专题 + LAYER 1 KPI）。

    数据源 data/crypto_basis/basis.csv（Yahoo BTC=F proxy 日频，观测日 key）。
    返回：当前值 / 30d 均值 / 60d EMA / 距到期 / 百分位 + SOFR Spread。
    """
    df = _csv("crypto_basis/basis.csv")
    if df.empty or "basis_pct" not in df.columns:
        return {"available": False}
    basis = df["basis_pct"].dropna()
    if basis.empty:
        return {"available": False}
    cur = float(basis.iloc[-1])
    last_date = basis.index[-1]
    ma30 = float(basis.tail(30).mean())
    ema60 = float(basis.ewm(span=60, adjust=False).mean().iloc[-1])
    pct_rank = float((basis <= cur).mean() * 100)
    # SOFR（FRED），Spread = 基差 60d EMA − SOFR
    sofa = _csv("fred/rates/rates.csv")
    sofr = None
    if "SOFR" in sofa.columns:
        s = sofa["SOFR"].dropna()
        if len(s):
            sofr = float(s.iloc[-1])
    spread = ema60 - sofr if sofr is not None else None
    return {
        "available": True,
        "current": cur,
        "latest": str(last_date.date()),
        "ma30": round(ma30, 2),
        "ema60": round(ema60, 2),
        "pct_rank": round(pct_rank, 1),
        "spread": round(spread, 2) if spread is not None else None,
        "sofr": sofr,
    }


def _coinglass() -> dict:
    """Coinglass 全市场聚合（衍生日页 Coinglass 模块）；读取最新快照 json。"""
    files = sorted((ROOT / "data" / "coinglass").glob("20*.json"))
    if not files:
        return {"available": False}
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except Exception:
        return {"available": False}


# ── LAYER 1 · NOW（8 个核心 KPI：当前值 + 1 年百分位 + 1d 变化） ──────────────


def _latest_snapshot() -> dict | None:
    """加密衍生品最新快照（data/crypto_derivatives/ 文件名按日期排序取最新）。"""
    files = sorted((ROOT / "data" / "crypto_derivatives").glob("20*.json"))
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("读取快照失败: %s", e)
        return None


def _okx_funding_history() -> list[float]:
    """OKX 永续 BTC funding-rate-history（8h 一条，翻页拉近 3 个月上限，返回 %）。

    独立函数便于测试 monkeypatch；失败/无数据返回 []（不抛异常，KPI 降级"历史不足"）。
    """
    try:
        from src.fetchers.crypto_derivatives_fetcher import _okx

        # (fundingTime, rate%)，保留时间戳按时间排序
        rates: list[tuple[int, float]] = []
        seen: set[int] = set()
        after: str | None = None
        while len(rates) < 300:  # OKX 上限近 3 个月（实测 ~291 条）+ 余量
            params: dict = {"instId": "BTC-USDT-SWAP", "limit": "100"}
            if after:
                params["after"] = after
            rows = _okx("public/funding-rate-history", **params)
            if not rows:
                break
            before = len(rates)
            for r in rows:
                t = int(r["fundingTime"])
                if t not in seen:
                    seen.add(t)
                    rates.append((t, float(r["fundingRate"]) * 100))
            after = str(rows[-1]["fundingTime"])
            if len(rates) == before:  # 翻页无新数据，防死循环
                break
        # 按时间升序（旧→新），与 fetcher fetch_funding_history 口径一致
        rates.sort(key=lambda x: x[0])
        return [r for _, r in rates]
    except Exception as e:
        logger.warning("OKX funding history 拉取失败: %s", e)
        return []


def _okx_taker_history() -> list[float]:
    """OKX rubik taker-volume（BTC 合约日频，近 10 日，返回每日 buy/sell 比）。

    独立函数便于测试 monkeypatch；失败返回 []。
    """
    try:
        from src.fetchers.crypto_derivatives_fetcher import _okx

        rows = _okx(
            "rubik/stat/taker-volume", ccy="BTC", instType="CONTRACTS", period="1D"
        )
        out: list[float] = []
        for r in rows[:10]:
            sell = float(r[2])
            if sell:
                out.append(float(r[1]) / sell)
        return out
    except Exception as e:
        logger.warning("OKX taker history 拉取失败: %s", e)
        return []


def _pcr_history() -> list[float]:
    """历史快照 options_BTC.pcr 收集（现在只有 2 个文件，<30 点 → 百分位 None）。"""
    out: list[float] = []
    for f in sorted((ROOT / "data" / "crypto_derivatives").glob("20*.json")):
        try:
            j = json.loads(f.read_text(encoding="utf-8"))
            pcr = (j.get("options_BTC") or {}).get("pcr")
            if pcr is not None:
                out.append(float(pcr))
        except Exception:
            continue
    return out


def _window_1y(s: pd.Series) -> pd.Series:
    """近 1 个日历年窗口（按 index 日期切；稀疏序列不把观测数当“天数”）。"""
    if s.empty:
        return s
    return s[s.index >= s.index[-1] - pd.Timedelta(days=365)]


def _rank_pct(
    s: pd.Series, cur: float | None, min_n: int = 30
) -> tuple[float | None, str | None]:
    """百分位 rank（考虑小样本）：(series <= cur).mean()×100 保留 1 位小数。

    样本 < min_n → (None, 说明文字)。
    """
    if cur is None:
        return None, "当前值缺失"
    v = s.dropna()
    if len(v) < min_n:
        return None, f"样本 {len(v)} 个"
    return round(float((v <= cur).mean() * 100), 1), None


def _quartiles(s: pd.Series, nd: int = 2) -> list[float] | None:
    """P25/P50/P75（前端小柱刻度）；样本 <5 返回 None。"""
    v = s.dropna()
    if len(v) < 5:
        return None
    q = v.quantile([0.25, 0.5, 0.75])
    return [round(float(q[k]), nd) for k in (0.25, 0.5, 0.75)]


def _dir_chg(delta: float | None, unit: str = "", nd: int = 2) -> str | None:
    """1d 变化：↑/↓/→ + 幅度字符串（delta 为 None 返回 None）。"""
    if delta is None:
        return None
    arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "→")
    return f"{arrow} {delta:+.{nd}f}{unit}"


def _pct_label(n_pts: int, rank: float | None, unit: str = "天") -> str:
    if rank is None:
        return "历史不足"
    return f"过去 {n_pts} {unit} · 第 {rank:g} 百分位"


def _layer1_kpis() -> dict:
    """LAYER 1 · NOW：8 个核心 KPI（当前值 + 1 年百分位 + 1d 变化方向）。

    timsun /assets/crypto/derivatives 口径。每 KPI：
      label / value（字符串含单位）/ pct_rank（0-100 或 None）/ pct_label /
      chg（↑↓→ 方向 + 幅度）/ quartiles（P25/P50/P75 小柱刻度）/ note。
    网络拉取（OKX funding/taker 历史）失败 → 对应 KPI 降级 None，不阻断。
    """
    snap = _latest_snapshot() or {}
    perp = (snap.get("perp") or {}).get("BTC") or {}
    opt = snap.get("options_BTC") or {}
    taker_snap = (snap.get("taker") or {}).get("BTC") or []

    kpis: list[dict] = []
    N = 365  # 1 年窗口（日频/8h 序列）

    def add(**kw) -> None:
        kpis.append(
            {
                "label": kw["label"],
                "value": kw.get("value", "数据不足"),
                "pct_rank": kw.get("pct_rank"),
                "pct_label": kw.get("pct_label", "历史不足"),
                "chg": kw.get("chg"),
                "quartiles": kw.get("quartiles"),
                "note": kw.get("note"),
            }
        )

    # 1) BTC 现价（当前 = OKX perp last 优先，Deribit spot_anchor 兜底）
    px = asset_prices()
    btc = px["BTC"].dropna() if "BTC" in px.columns else pd.Series(dtype=float)
    cur_px = perp.get("last") or opt.get("spot_anchor")
    win = btc.tail(N)
    rank, note = _rank_pct(win, cur_px)
    chg = None
    if len(btc) >= 2:
        chg = _dir_chg((float(btc.iloc[-1]) / float(btc.iloc[-2]) - 1) * 100, "%")
    add(
        label="BTC 现价",
        value=f"${cur_px:,.0f}" if cur_px is not None else "数据不足",
        pct_rank=rank,
        pct_label=_pct_label(len(win), rank),
        chg=chg,
        quartiles=_quartiles(win),
        note=note,
    )

    # 2) BTC 30d 已实现波动率（日收益率 30d 滚动标准差，简单总体 std，年化 ×√365）
    vol = pd.Series(dtype=float)
    if len(btc) >= 31:
        rets = btc.pct_change().dropna()
        vol = (rets.rolling(30).std(ddof=0) * np.sqrt(365) * 100).dropna()
    cur_vol = float(vol.iloc[-1]) if len(vol) else None
    winv = vol.tail(N)
    rank, note = _rank_pct(winv, cur_vol)
    chg = None
    if cur_vol is not None and len(vol) >= 2:
        chg = _dir_chg(cur_vol - float(vol.iloc[-2]), "pp")
    add(
        label="BTC 30d 已实现波动率",
        value=f"{cur_vol:.1f}%" if cur_vol is not None else "数据不足",
        pct_rank=rank,
        pct_label=_pct_label(len(winv), rank),
        chg=chg,
        quartiles=_quartiles(winv),
        note=note,
    )

    # 3) 基差 60d EMA（crypto_basis/basis.csv，basis_pct 列；稀疏序列 ~35% 完整）：
    #    百分位窗口 = 近 1 个日历年（不以观测数充当“天数”）
    ema60 = pd.Series(dtype=float)
    bz = _csv("crypto_basis/basis.csv")
    if "basis_pct" in bz.columns:
        basis = bz["basis_pct"].dropna()
        if len(basis):
            ema60 = basis.ewm(span=60, adjust=False).mean().dropna()
    cur_ema = float(ema60.iloc[-1]) if len(ema60) else None
    win_ema = _window_1y(ema60)
    rank, note = _rank_pct(win_ema, cur_ema)
    chg = None
    if cur_ema is not None and len(ema60) >= 2:
        chg = _dir_chg(cur_ema - float(ema60.iloc[-2]), "pp")
    add(
        label="基差 60d EMA",
        value=f"{cur_ema:.2f}%" if cur_ema is not None else "数据不足",
        pct_rank=rank,
        pct_label=(f"近 1 年 · 第 {rank:g} 百分位" if rank is not None else "历史不足"),
        chg=chg,
        quartiles=_quartiles(win_ema),
        note=note,
    )

    # 4) Spread = 基差 60d EMA − SOFR（逐观测日配对当日 SOFR，ffill；缺失 → 数据不足）
    rates = _csv("fred/rates/rates.csv")
    sofr = None
    spread = pd.Series(dtype=float)
    if "SOFR" in rates.columns:
        sr = rates["SOFR"].dropna()
        if len(sr):
            sofr = float(sr.iloc[-1])
            if len(ema60):
                spread = (ema60 - sr.reindex(ema60.index, method="ffill")).dropna()
    cur_sp = float(spread.iloc[-1]) if len(spread) else None
    win_sp = _window_1y(spread)
    rank, note = _rank_pct(win_sp, cur_sp)
    chg = None
    if cur_sp is not None and len(spread) >= 2:
        chg = _dir_chg(cur_sp - float(spread.iloc[-2]), "pp")
    add(
        label="Spread（基差−SOFR）",
        value=f"{cur_sp:+.2f}%" if cur_sp is not None else "数据不足",
        pct_rank=rank,
        pct_label=(f"近 1 年 · 第 {rank:g} 百分位" if rank is not None else "历史不足"),
        chg=chg,
        quartiles=_quartiles(win_sp),
        note=note if sofr is not None else "SOFR 缺失",
    )

    # 5) 永续资金费率 8h（当前 = 快照 funding_rate；历史 = 快照 funding_hist 升序，
    #    无则现场拉 OKX（降级，不阻断））
    cur_fr = perp.get("funding_rate")
    cur_fr_pct = float(cur_fr) * 100 if cur_fr is not None else None
    snap_hist = snap.get("funding_hist")
    fund_hist = pd.Series(snap_hist or _okx_funding_history(), dtype=float)
    rank, note = _rank_pct(fund_hist, cur_fr_pct)
    chg = None
    if snap_hist and cur_fr_pct is not None and len(fund_hist) >= 4:
        # 升序序列：24h 参照 = hist[-3]。cur 来自 funding-rate（fundingTime=
        # 下一结算点，如 03:46 时取 08:00），hist[-1] 为上一结算点（如 00:00），
        # 两者结构化相差 1 格（8h）→ 当前费率 24h 前 = hist[-3]（不是 [-4]，
        # 那是相对 hist[-1] 的 24h）。仅快照自带 hist 时对齐有效：
        # 降级路径（现场拉）的 hist 是分析时刻结算点，与快照时刻 cur 跨 ≥1
        # 周期无法对齐 → chg 置 None（与 pct 降级口径一致）。
        chg = _dir_chg(cur_fr_pct - float(fund_hist.iloc[-3]), "pp", nd=4)
    add(
        label="永续资金费率 8h",
        value=f"{cur_fr_pct:.4f}%" if cur_fr_pct is not None else "数据不足",
        pct_rank=rank,
        pct_label=_pct_label(int(len(fund_hist) * 8 / 24), rank, unit="天"),
        chg=chg,
        quartiles=_quartiles(fund_hist, nd=4) if len(fund_hist) else None,
        # funding_rate 为下一结算周期预测值（OKX：实际结算以 settFundingRate 为准）
        note=(note + " · " if note else "") + "当前费率为预测值",
    )

    # 6) CME BTC OI（口径统一：当前与历史都用 CFTC COT 周频全合约 OI；
    #    快照 fut_oi 是近月单合约，与聚合口径不可直接比 → 不作为 KPI 当前值）
    cot = _csv("cot/cot.csv")
    cot_oi = (
        cot["BTC_OI"].dropna() if "BTC_OI" in cot.columns else pd.Series(dtype=float)
    )
    cur_oi = float(cot_oi.iloc[-1]) if len(cot_oi) else None
    win_oi = cot_oi.tail(52) if len(cot_oi) else cot_oi  # 近 1 年（周频 52 个观测）
    rank, note = _rank_pct(win_oi, cur_oi)
    chg = None
    if len(cot_oi) >= 2:
        chg = _dir_chg((float(cot_oi.iloc[-1]) / float(cot_oi.iloc[-2]) - 1) * 100, "%")
    add(
        label="CME BTC OI",
        value=f"{cur_oi:,.0f} 份" if cur_oi is not None else "数据不足",
        pct_rank=rank,
        pct_label=(f"近 1 年 · 第 {rank:g} 百分位" if rank is not None else "历史不足"),
        chg=chg,
        quartiles=_quartiles(win_oi, nd=0),
        note=(
            ((note + " · ") if note else "") + "周频 · COT 全合约口径"
            if len(cot_oi)
            else note
        ),
    )

    # 7) 永续多空比（当前 = 快照 taker 3 日 buy/sell 比；历史 = OKX 近 10 日）
    cur_ls = None
    if len(taker_snap) >= 3:
        buy = sum(r["buy"] for r in taker_snap[:3])
        sell = sum(r["sell"] for r in taker_snap[:3])
        cur_ls = buy / sell if sell else None
    taker_hist = _okx_taker_history()  # 降序（最新在前）
    if not taker_hist and len(taker_snap) >= 2:
        # 网络失败 → 快照自带日序列兜底（同降序）
        taker_hist = [r["buy"] / r["sell"] for r in taker_snap if r.get("sell")]
    th = pd.Series(taker_hist, dtype=float)
    rank, note = _rank_pct(th, cur_ls, min_n=7)  # OKX taker 仅近 10 日，>30 永不可达
    chg = None
    if len(th) >= 2:  # 降序：最新=iloc[0]，前一日=iloc[1]
        chg = _dir_chg((float(th.iloc[0]) / float(th.iloc[1]) - 1) * 100, "%")

    add(
        label="永续多空比",
        value=f"{cur_ls:.2f}" if cur_ls is not None else "数据不足",
        pct_rank=rank,
        pct_label=_pct_label(len(th), rank, unit="日"),
        chg=chg,
        quartiles=_quartiles(th),
        note=note,
    )

    # 8) 期权 Put/Call OI（当前 = 最新快照 pcr；历史 = 全部快照收集，<30 点注明）
    pcrs = _pcr_history()
    cur_pcr = opt.get("pcr")
    rank, note = _rank_pct(pd.Series(pcrs), cur_pcr)
    chg = None
    if len(pcrs) >= 2:
        chg = _dir_chg(float(pcrs[-1]) - float(pcrs[-2]), nd=2)
    add(
        label="期权 Put/Call OI",
        value=f"{cur_pcr:.2f}" if cur_pcr is not None else "数据不足",
        pct_rank=rank,
        pct_label=_pct_label(len(pcrs), rank, unit="个快照"),
        chg=chg,
        quartiles=_quartiles(pd.Series(pcrs)),
        note=(f"快照积累中 · {len(pcrs)} 个快照" if len(pcrs) and note else note),
    )

    return {"kpis": kpis}


def _cme_options() -> dict:
    """CME 期权墙（衍生日页 CME 机构期权模块）；读取最新快照 json。"""
    files = sorted((ROOT / "data" / "cme_options").glob("20*.json"))
    if not files:
        return {"available": False}
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except Exception:
        return {"available": False}


def crypto_derivatives() -> dict | None:
    files = sorted((ROOT / "data" / "crypto_derivatives").glob("20*.json"))
    if not files:
        return None
    snap = json.loads(files[-1].read_text(encoding="utf-8"))
    snap["etf"] = _etf_flows()
    snap["basis"] = _crypto_basis()
    snap["coinglass"] = _coinglass()
    snap["cme_options"] = _cme_options()
    snap["layer1"] = _layer1_kpis()
    snap["radar"] = crypto_radar(snap)
    snap["consensus"] = crypto_consensus(snap, snap["radar"])
    return snap


# ── 机构 vs 散户对照（衍生品页；规则引擎，LLM 预留） ──────────────────────────


def _stance(value: float | None, thr: float) -> str:
    """±thr 阈值 → 多/空/中性。"""
    if value is None:
        return "中性"
    return "偏多" if value > thr else ("偏空" if value < -thr else "中性")


def crypto_consensus(snap: dict, radar: dict) -> dict:
    """机构 vs 散户方向对照：机构=CME OI 周变化（+ETF 预留）；
    散户=资金费率 + 多空比 + PCR。返回双方立场与对照结论。"""
    cot = _csv("cot/cot.csv")
    chg = None
    if "BTC_OI" in cot.columns:
        oi = cot["BTC_OI"].dropna()
        if len(oi) >= 2:
            chg = (float(oi.iloc[-1]) / float(oi.iloc[-2]) - 1) * 100
    inst_stance = _stance(chg, 0.5)

    perp = (snap.get("perp") or {}).get("BTC") or {}
    taker = (snap.get("taker") or {}).get("BTC") or []
    opt = snap.get("options_BTC") or {}
    ann = perp.get("funding_annual")
    # 年化 >15% 视为多头拥挤（decimal → %）
    fr_stance = _stance((ann or 0) * 100 if ann is not None else None, 15)
    ls = None
    if len(taker) >= 3:
        buy = sum(r["buy"] for r in taker[:3])
        sell = sum(r["sell"] for r in taker[:3])
        ls = buy / sell if sell else None
    ls_stance = _stance((ls - 1) * 100 if ls is not None else None, 20)  # ±20%
    pcr = opt.get("pcr")
    pcr_stance = "中性"
    if pcr is not None:
        # 低 PCR = call 拥挤 = 偏多（与雷达口径一致）
        pcr_stance = "偏多" if pcr < 0.8 else ("偏空" if pcr > 1.2 else "中性")

    # ETF 通道（Farside；stale 不投票）
    etf = snap.get("etf") or {}
    etf_stance = "中性"
    if etf.get("available") and not etf.get("stale"):
        v5 = etf.get("sum5d_busd")
        etf_stance = (
            "偏多" if (v5 or 0) > 0.05 else ("偏空" if (v5 or 0) < -0.05 else "中性")
        )
    # 基差 carry 通道（Spread = 60d EMA − SOFR；>5% 吸引力足 / <0 负）
    bz = snap.get("basis") or {}
    spread = bz.get("spread")
    bz_stance = (
        "偏多" if (spread or 0) > 5 else ("偏空" if (spread or 0) < 0 else "中性")
    )

    def votes(stances: list[str]) -> str:
        def cnt(st: str) -> int:
            return sum(x == st for x in stances)

        return f"多 {cnt('偏多')} · 空 {cnt('偏空')} · 平 {cnt('中性')}"

    retail_stances = [fr_stance, ls_stance, pcr_stance]
    inst_stances = [inst_stance]
    # stale 的 ETF 不投票（与 radar dir=0 一致），可用才计入
    if etf.get("available") and not etf.get("stale"):
        inst_stances.append(etf_stance)
    if bz_stance != "中性" or spread is not None:
        inst_stances.append(bz_stance)
    names = []
    if chg is not None:
        names.append("CME")
    if etf.get("available") and not etf.get("stale"):
        names.append("ETF")
    if spread is not None:
        names.append("Spread")
    note_inst = f"({' / '.join(names)} 综合)" if names else "(CME 数据待积累)"
    short = {"偏多": "多", "偏空": "空", "中性": "中性"}
    # 机构侧以全体投票判向（CME/ETF/Spread 任一偏多即非全中性）
    inst_dirs = [s for s in inst_stances if s != "中性"]
    inst_lean = inst_dirs[0] if len(set(inst_dirs)) == 1 else "分化"
    short_inst = short.get(inst_lean, "分化")  # 分化时避免 KeyError
    both_neutral = not inst_dirs and all(s == "中性" for s in retail_stances)
    if both_neutral:
        verdict = "双方都按兵不动 — 等待新催化"
        detail = (
            "机构与散户立场均中性，无方向性持仓变化。等待宏观或链上新催化打破僵局。"
        )
    elif not inst_dirs:
        verdict = "机构按兵不动，散户有方向 — 看散户拥挤度"
        detail = (
            f"机构（{note_inst}）全体中性，散户（资金费率/多空比/PCR）"
            f"偏{short[retail_stances[0]]}——散户信号仅作反向拥挤度参考。"
        )
    elif inst_lean == "分化":
        verdict = "机构内部分歧 — 以 CME/ETF/Spread 通道对立为线索"
        detail = (
            f"机构通道分歧（{note_inst}），散户偏{short[retail_stances[0]]}。"
            "通道对立时以 ETF 现货通道为锚，Spread 作 carry 参考。"
        )
    elif all(s == inst_lean or s == "中性" for s in retail_stances) and inst_lean:
        verdict = f"机构与散户同向偏{short_inst[:1]} — 趋势延续概率上升"
        detail = f"机构（{note_inst}）偏{short_inst}且散户未反向，方向性信号同向。"
    else:
        verdict = f"机构偏{short_inst}，散户偏{short[retail_stances[0]]} — 分歧看定价权"
        detail = (
            "机构与散户立场不一致：以机构（CME/ETF/Spread）定价权为锚，"
            "散户信号仅作反向拥挤度参考。"
        )
    return {
        "inst": {
            "stance": inst_stance,
            "votes": votes(inst_stances),
            "note": note_inst,
            "text": f"CME OI 周变化 {chg:+.1f}%"
            if chg is not None
            else "CME COT 数据待积累",
        },
        "retail": {
            "stance": retail_stances[0] if len(set(retail_stances)) == 1 else "分化",
            "votes": votes(retail_stances),
            "note": "(资金费率 / 多空比 / PCR 综合)",
            "text": f"资金费率年化 {ann * 100:.1f}% · 多空比 {ls:.2f} · PCR {pcr}"
            if ann is not None and ls is not None and pcr is not None
            else "部分散户数据不可用",
        },
        "verdict": verdict,
        "detail": detail,
        "generator": "rules",
    }


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

    # 杠杆风险（资金费率年化；timsun 口径：>30% 高 / >15% 中 / 其余低）
    fr = perp.get("funding_annual") or 0
    lev_risk = "高" if abs(fr * 100) >= 30 else ("中" if abs(fr * 100) >= 15 else "低")
    # 主导力量（timsun 口径：机构增减仓 vs 现货驱动）
    driver = (
        f"CME 机构头寸: {'机构增仓' if (cme_oi_chg or 0) > 0 else '机构减仓'}"
        if cme_oi_chg is not None
        else "现货驱动 · CFTC 数据待积累"
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

    # 基差 carry（Spread = EMA60 − SOFR 判向：>0 机构 carry 有吸引力 → 正分；
    # SOFR 缺失则用 EMA60 本身）
    basis = cme.get("basis_pct")
    bz = snap.get("basis") or {}
    if bz.get("available"):
        bv = bz.get("spread") if bz.get("spread") is not None else bz.get("ema60")
        add(
            "基差 carry",
            20,
            bv,
            f"Spread {bz.get('spread')}% ({bz.get('ema60')}% EMA − SOFR "
            f"{bz.get('sofr')}%)"
            if bz.get("spread") is not None
            else f"基差 60d EMA {bz.get('ema60')}%（SOFR 不可用）",
        )
    else:
        add(
            "基差 carry",
            20,
            basis,
            f"Spread {basis:.2f}%，CME ${cme.get('fut_price')} vs "
            f"现货 ${cme.get('spot')}"
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
        if pcr is not None and call_wall and dist is not None
        else (
            f"PCR {pcr:.2f}，Call Wall/Put Wall 数据不足"
            if pcr is not None
            else "期权数据不可用"
        ),
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

    # ETF 资金流（Farside：5d 净流入，B USD；stale 时不计分）
    etf = snap.get("etf") or {}
    if etf.get("available") and not etf.get("stale"):
        v = etf.get("sum5d_busd")
        add(
            "ETF 资金流",
            15,
            v,
            f"近 5 日净流入 {v:+.2f} B USD（Farside，截至 {etf.get('latest')}）"
            if v is not None
            else "ETF 资金流数据不可用",
        )
    else:
        reason = (
            "超过时效阈值，不纳入方向评分"
            if etf.get("stale")
            else "公开免费源未接入（Farside）"
        )
        add("ETF 资金流", 15, None, reason)

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
        "lev_risk": lev_risk,
        "driver": driver,
        "signals": signals,
    }
