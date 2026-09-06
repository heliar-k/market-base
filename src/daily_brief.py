"""今日研判数据引擎 — 复刻 timsun.net 首页「今日宏观决策台」结构。

只读本地 CSV（与 *_analysis 同构），产出单一 JSON：
  indicators: 跨资产变化表 10 行（最新值 + 5/20 观测变化 + 迷你走势 + 截至 + 时效）
  scenarios:  同一组事实的三种可能解释（规则匹配，支持=规则命中数，不是概率）
  alerts:     跨资产结构报警（复用 assets_analysis.overview 的 alerts）

LLM 预留：_llm_generate() 接好后返回同构 dict，未接入回落规则引擎。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.analysis_utils import read_csv_or_empty

ROOT = Path(__file__).resolve().parent.parent

# 指标窗口（观测间隔数，timsun 同款：情景 5 个 / 表格 5+20 个）
SCEN_WINDOW = 5
TABLE_WINDOWS = (5, 20)
SPARK_N = 20

# 跨资产观察表行（key → 名称/一句话说明/单位）——timsun 首页 10 行同款
INDICATORS: list[tuple[str, str, str, str]] = [
    ("SPX", "标普500", "股票风险偏好的价格表现；需与信用和波动率交叉验证。", "px"),
    ("DXY", "美元指数", "美元相对一篮子货币的强弱；不能单独证明全球美元短缺。", "px"),
    ("BTC", "比特币", "全天候风险资产；同时受杠杆、ETF 与加密原生事件影响。", "px"),
    (
        "WTI",
        "WTI 原油",
        "近月原油期货；供给与需求都能推动价格，换月也会影响序列。",
        "px",
    ),
    ("Gold", "黄金", "避险与实际利率的镜像；需结合美元方向解读。", "px"),
    ("Y10", "10Y 国债收益率", "名义利率；需拆分实际利率、通胀补偿与期限溢价。", "bp"),
    ("VIX", "VIX", "标普期权隐含的未来约 30 天波动预期，不是涨跌方向预测。", "pt"),
    (
        "HY_OAS",
        "高收益债 OAS",
        "高收益债相对国债的利差；扩大通常意味着信用风险补偿上升。",
        "bp",
    ),
    ("RRP", "隔夜逆回购余额", "隔夜逆回购余额；下降不等于同额资金直接流入股票。", "bn"),
    ("TGA", "TGA 余额", "财政部现金余额；其影响需结合发债、支出及资金来源。", "bn"),
    (
        "NET_LIQ",
        "净流动性",
        "Fed 总资产 − RRP − TGA 的资产负债表代理量，不等于可投资现金。",
        "bn",
    ),
]


def _llm_generate() -> dict | None:
    """LLM 生成接入点（各专题分析同款）。未接入返回 None → 回落规则引擎。"""
    return None


# ── 数据层（只读）───────────────────────────────────────────────────────────


def _series() -> dict[str, pd.Series]:
    """11 个指标的日度序列（各自 dropna）。"""
    rates = read_csv_or_empty(ROOT / "data/fred/rates/rates.csv")
    vol = read_csv_or_empty(ROOT / "data/fred/volatility/volatility.csv")
    liq = read_csv_or_empty(ROOT / "data/fred/liquidity/liquidity.csv")
    prices = read_csv_or_empty(ROOT / "data/yfinance/asset_prices.csv")

    out: dict[str, pd.Series] = {}
    for k in ("SPX", "DXY", "BTC", "WTI", "Gold"):
        if k in prices.columns:
            out[k] = prices[k].dropna()
    if "DGS10" in rates.columns:
        out["Y10"] = rates["DGS10"].dropna()
    if "VIX" in vol.columns:
        out["VIX"] = vol["VIX"].dropna()
    if "HY_OAS" in vol.columns:
        out["HY_OAS"] = (vol["HY_OAS"] * 100).dropna()  # % → bp
    if "RRPONTSYD" in liq.columns:
        out["RRP"] = liq["RRPONTSYD"].dropna()
    if "WTREGEN" in liq.columns:
        out["TGA"] = liq["WTREGEN"].dropna()
    if {"WALCL", "RRPONTSYD", "WTREGEN"} <= set(liq.columns):
        out["NET_LIQ"] = (liq["WALCL"] - liq["RRPONTSYD"] - liq["WTREGEN"]).dropna()
    return out


# ── 跨资产变化表 ────────────────────────────────────────────────────────────


def _chg(s: pd.Series, n: int, mode: str) -> float | None:
    """最新 vs n 个观测前的变化：px → %，bp/pt/bn → 差值。"""
    if len(s) < n + 1:
        return None
    cur, past = float(s.iloc[-1]), float(s.iloc[-1 - n])
    if past == 0:
        return None
    return round((cur / past - 1) * 100, 2) if mode == "px" else round(cur - past, 2)


def _spark(s: pd.Series, n: int = SPARK_N) -> list[list]:
    """近 n 个观测间隔的 [date, value]（n+1 个点，首点即 Δn 基准，
    颜色与 Δn 列语义一致，不会出现曲线方向与数字颜色矛盾）。"""
    s = s.tail(n + 1)
    return [[d.strftime("%Y-%m-%d"), round(float(v), 2)] for d, v in s.items()]


# 数据截至分组（页头多源分段用）
AS_OF_GROUPS = {
    "资产": ("SPX", "DXY", "BTC", "WTI", "Gold"),
    "利率/利差": ("Y10", "VIX", "HY_OAS"),
    "流动性": ("RRP", "TGA", "NET_LIQ"),
}


def _indicators(series: dict[str, pd.Series]) -> dict:
    rows = []
    for key, name, note, unit in INDICATORS:
        s = series.get(key)
        if s is None or s.empty:
            rows.append({"key": key, "name": name, "note": note, "unit": unit})
            continue
        row = {
            "key": key,
            "name": name,
            "note": note,
            "unit": unit,
            "last": round(float(s.iloc[-1]), 2),
            "as_of": s.index[-1].strftime("%Y-%m-%d"),
            "chg": {f"d{n}": _chg(s, n, unit) for n in TABLE_WINDOWS},
            "spark": _spark(s),
        }
        rows.append(row)
    dates = [r["as_of"] for r in rows if r.get("as_of")]
    by_key = {r["key"]: r.get("as_of") for r in rows}
    # 多源「数据截至」分段（页头 re-as-of 统一格式：源 · 源）
    groups = {}
    for g, keys in AS_OF_GROUPS.items():
        ds = [by_key[k] for k in keys if by_key.get(k)]
        if ds:
            groups[g] = max(ds)
    return {"as_of": max(dates) if dates else None, "groups": groups, "rows": rows}


# ── 各指标的信号与证伪条件（timsun 方法论：单指标相容 ≠ 机制成立）────────


def _pct1y(s: pd.Series) -> float | None:
    """最新值在近 1 年窗口内的分位（宏观锚点）。"""
    s = s.tail(252)
    if len(s) < 60:
        return None
    return round(float((s < s.iloc[-1]).mean() * 100))


# 每个指标给两条：
#   signal —— 当前读数的机制解读，按跨资产条件表述（需其他指标方向确认，而非单指标翻转）
#   refute —— 证伪条件：什么情况下这个读法失效/需要重新核验
# d = 近 5 观测方向（+1/-1/0），p = 1Y 分位，ctx = 全部指标的近 5 观测方向表
READING_RULES: dict[str, object] = {
    "SPX": lambda d, p, ctx: (
        "近 5 观测上行；信用利差未走扩、VIX 未抬升，改善有广度"
        if d == 1 and ctx.get("HY_OAS") in (-1, 0) and ctx.get("VIX") in (-1, 0)
        else "近 5 观测上行，但信用/波动率未同步放松，反弹质量待核验"
        if d == 1
        else "下跌伴随利差走扩或 VIX 抬升，压力在扩散"
        if d == -1 and (ctx.get("HY_OAS") == 1 or ctx.get("VIX") == 1)
        else "回调未传导至信用与波动率，更像局部仓位调整"
        if d == -1
        else "窄幅震荡，方向待选择",
        "上行若伴随利差与 VIX 同步走扩，非健康 risk-on；信用波动双稳的下跌多为噪音",
    ),
    "DXY": lambda d, p, ctx: (
        "美元走弱减轻全球融资压力，利好新兴与大宗"
        if d == -1
        else "强美元抽紧全球美元流动性，压制大宗与新兴市场"
        if d == 1
        else "美元横盘，方向待选择",
        "弱美元若源于避险或通胀担忧（金油同步急涨）则非利好；强美元若由利差走阔驱动（美国相对增长占优），未必压制风险资产",
    ),
    "BTC": lambda d, p, ctx: (
        "与其他风险资产同步上行，反映风险偏好回升的杠杆端放大"
        if d == 1 and ctx.get("SPX") == 1
        else "独立于美股走强，驱动多来自加密内部（ETF 流/杠杆）"
        if d == 1
        else "回调若与美股同步，是全市场去杠杆的一部分"
        if d == -1 and ctx.get("SPX") == -1
        else "独立下跌，更多是加密原生事件而非宏观信号"
        if d == -1
        else "横盘蓄势",
        "清算连锁与资金费率放大短期方向；ETF 大额单日流出入常先于价格转向，先查资金面",
    ),
    "WTI": lambda d, p, ctx: (
        "油价上行伴随股市走弱、长端利率上行，更像成本冲击而非需求改善"
        if d == 1 and ctx.get("SPX") == -1
        else "需求定价占优，能源股与再通胀交易受益"
        if d == 1
        else "回落若是需求走弱信号，周期性风险上升"
        if d == -1 and ctx.get("SPX") == -1
        else "回落缓解通胀与企业成本压力"
        if d == -1
        else "供需再平衡中",
        "近月换月与供给事件可扭曲 5 观测方向，核对近月/次月价差与库存再定方向",
    ),
    "Gold": lambda d, p, ctx: (
        "与长端利率同涨，定价的是通胀尾部或去美元化，而非单纯避险"
        if d == 1 and ctx.get("Y10") == 1
        else "避险与央行购金需求提供支撑"
        if d == 1
        else "实际利率回落降低持有成本，或避险退潮资金回流风险资产"
        if d == -1
        else "区间盘整",
        "若美元走强而金价仍涨，头寸可能拥挤；避险溢价随事件降温回吐，跟踪 ETF 持仓",
    ),
    "Y10": lambda d, p, ctx: (
        "长端上行需拆分：增长/再通胀定价还是期限溢价，曲线熊陡指向后者"
        if d == 1
        else "下行多为宽松预期或增长走弱定价，看曲线形态分辨"
        if d == -1
        else "收益率企稳",
        "若仅名义利率动而盈亏平衡持平，是实际利率驱动，对成长股与久期资产压制更强，也非景气改善",
    ),
    "VIX": lambda d, p, ctx: (
        "波动率自低位抬升，可能是事件冲击的起点，确认需前端期限（VIX1D/9D）继续上行"
        if d == 1 and (p is None or p < 50)
        else "波动率处于 1Y 高位（{}%）且仍在抬升，去杠杆压力未除".format(p)
        if d == 1
        else "波动率回落；配合信用利差收窄时，风险偏好修复可信"
        if d == -1 and ctx.get("HY_OAS") in (-1, 0)
        else "波动率回落，但信用利差未同步收窄，修复待确认"
        if d == -1
        else "波动率平静（1Y 分位 {}%）".format(p)
        if (p is None or p < 50)
        else "波动率高位盘整（1Y 分位 {}%）".format(p),
        "VIX 是波动预期不是方向；现货信用平稳而 VIX 单独抬升，先查 ETP 仓位",
    ),
    "HY_OAS": lambda d, p, ctx: (
        "利差连续走扩，信用条件收紧，领先于盈利与违约周期"
        if d == 1
        else "利差收窄且处 1Y 低位（{}%），信用宽松但风险补偿偏薄".format(p)
        if d == -1 and (p is None or p < 50)
        else "利差收窄，融资成本下降"
        if d == -1
        else "利差平稳",
        "单日 1-2bp 多为噪声，连续走扩或 IG 同步走扩才算确认；过窄本身即补偿不足",
    ),
    "RRP": lambda d, p, ctx: (
        "余额下降，资金从工具释放回市场；余额近零后此缓冲消失"
        if d == -1
        else "资金回淤工具，市场流动性边际收紧"
        if d == 1
        else "余额平稳",
        "RRP 下降不等于同额资金流入风险资产，准备金总量与银行体系分布才是约束",
    ),
    "TGA": lambda d, p, ctx: (
        "余额下降=财政净支出向市场注资"
        if d == -1
        else "发债重建现金，从市场抽水"
        if d == 1
        else "余额平稳",
        "TGA 影响取决于发债结构与支出节奏，税收/结算日常见单日跳变，勿据单日定方向",
    ),
    "NET_LIQ": lambda d, p, ctx: (
        "净流动性扩张，风险资产顺风；若靠被动扩表则质量待验证"
        if d == 1
        else "净流动性回落，若为缩表自然节奏未必利空"
        if d == -1
        else "流动性平台期",
        "净流动性是资产负债表代理量，与股价无稳定倍数关系；单周急降比缓降信号强",
    ),
}


def _readings(series: dict[str, pd.Series]) -> list[dict]:
    """每个指标：信号（机制解读，跨资产条件化）+ 证伪条件。"""
    d_all = {k: _dir(s) for k, s in series.items()}
    out = []
    for key, name, _, _ in INDICATORS:
        rule = READING_RULES.get(key)
        s = series.get(key)
        if rule is None or s is None or s.empty:
            continue
        p = _pct1y(s)
        signal, refute = rule(d_all.get(key), p, d_all)  # type: ignore[operator]
        out.append(
            {
                "key": key,
                "name": name,
                "dir": d_all.get(key),
                "pct_1y": p,
                "signal": signal,
                "refute": refute,
            }
        )
    return out


# ── 三种可能解释（规则匹配）─────────────────────────────────────────────────


def _dir(s: pd.Series | None, n: int = SCEN_WINDOW) -> int | None:
    """近 n 个观测的方向：+1 上行 / -1 下行 / 0 持平 / None 数据不足。"""
    if s is None or len(s) < n + 1:
        return None
    cur, past = float(s.iloc[-1]), float(s.iloc[-1 - n])
    if abs(cur - past) < 1e-9:
        return 0
    return 1 if cur > past else -1


def _window(series: dict[str, pd.Series], keys: list[str]) -> tuple[str, str] | None:
    """情景窗口 = 所需序列共同覆盖的 [起点, 终点]（起点取最晚，终点取最早）。"""
    ok = [k for k in keys if series.get(k) is not None and len(series[k]) > SCEN_WINDOW]
    if not ok:
        return None
    start = max(series[k].index[-1 - SCEN_WINDOW] for k in ok)
    end = min(series[k].index[-1] for k in ok)
    if start >= end:
        return None
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def _scenarios(series: dict[str, pd.Series]) -> list[dict]:
    spx, hy, vix, wti, y10 = (
        series.get(k) for k in ("SPX", "HY_OAS", "VIX", "WTI", "Y10")
    )
    d_spx, d_hy, d_vix, d_wti, d_y10 = (_dir(x) for x in (spx, hy, vix, wti, y10))
    win = lambda keys: _window(series, keys)  # noqa: E731

    def ev(label: str, d: int | None) -> str:
        arrow = {1: "上行", -1: "下行", 0: "持平", None: "数据不足"}[d]
        return f"{label}近 5 个观测{arrow}"

    return [
        {
            "key": "risk-on",
            "title": "风险偏好修复",
            "desc": "股市上涨若伴随信用利差收窄、隐含波动下降，风险承担改善更有广度。",
            "matched": d_spx == 1 and d_hy in (-1, 0) and d_vix in (-1, 0),
            "evidence": [
                ev("标普500", d_spx),
                ev("高收益债 OAS", d_hy),
                ev("VIX", d_vix),
            ],
            "window": win(["SPX", "HY_OAS", "VIX"]),
            "refute": "若股市上涨而信用利差扩大或 VIX 上升，应下调这一解释的权重。",
        },
        {
            "key": "risk-off",
            "title": "风险压力扩散",
            "desc": "股市下跌若同时传导到信用与期权市场，应检查融资及流动性条件。",
            "matched": d_spx == -1 and (d_hy == 1 or d_vix == 1),
            "evidence": [
                ev("标普500", d_spx),
                ev("高收益债 OAS", d_hy),
                ev("VIX", d_vix),
            ],
            "window": win(["SPX", "HY_OAS", "VIX"]),
            "refute": "若信用保持稳定、波动率回落，调整可能仍是局部估值或仓位变化。",
        },
        {
            "key": "energy-cost",
            "title": "能源成本压力",
            "desc": "油价涨、股市跌、长端利率升并存，与成本压力相容；仍需事件验证。",
            "matched": d_wti == 1 and d_spx == -1 and d_y10 == 1,
            "evidence": [
                ev("WTI 原油", d_wti),
                ev("标普500", d_spx),
                ev("10Y 收益率", d_y10),
            ],
            "window": win(["WTI", "SPX", "Y10"]),
            "refute": "油价回落、股票企稳或实际需求改善，都可能改变成本冲击的解释。",
        },
    ]


# ── 报警（复用 assets_analysis 的结构报警对）────────────────────────────────


def _alerts() -> list[dict]:
    try:
        from src.assets_analysis import overview as assets_overview

        return assets_overview().get("alerts") or []
    except Exception:  # 数据缺失不阻断今日研判
        return []


# ── 统一入口 ────────────────────────────────────────────────────────────────


def generate_daily_brief() -> dict:
    out = _llm_generate()
    if out is not None:
        return out
    series = _series()
    return {
        "generator": "rules",
        "indicators": _indicators(series),
        "readings": _readings(series),
        "scenarios": _scenarios(series),
        "alerts": _alerts(),
    }
