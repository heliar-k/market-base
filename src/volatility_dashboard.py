"""波动率全景仪表盘 — timsun.net/volatility/dashboard 复刻（规则引擎）。

与 volatility_analysis.py（VIX 详情页）分工：本模块输出跨资产 30 指数全景
（Hero 卡 / 风险矩阵 / Vol Trade Map / 统计 / 7 段叙事），VIX 深挖留在详情页。

数据源（三合一）：
  - data/cboe/volatility.csv     28 个指数全量历史（CBOE CDN）
  - data/yfinance/asset_prices.csv  MOVE / TDEX / VOLI（yfinance 快照+种子）
  - data/barchart/volatility_snapshot.csv  30 指数快照（VXMO/VXEF 唯一源，
    另存 1D/5D/1M/1Y 官方变化字段，作本地历史不足时的兜底）

变化口径：Barchart 官方字段（1D/5D/1M/1Y）优先，与 timsun 同源同口径；
本地历史自算作兜底（快照缺失时）。最新值：CBOE/yfinance 本地序列优先，
VXMO/VXEF 取快照序列。

LLM 预留：generate_dashboard() 唯一入口，_llm_generate_dashboard() 接好 LLM
返回同构 dict 即可替换规则引擎输出，调用方（server.py）零改动。
"""

from __future__ import annotations

import pandas as pd

from src.analysis_utils import chg_pct, latest, read_csv_or_empty
from src.config import ROOT
from src.volatility_analysis import ZONES, term_structure

# ── 30 指数定义：timsun 符号 → (中文名, 分类, 本地列, 本地源) ──
# 本地列与源：cboe = data/cboe/volatility.csv；yf = data/yfinance/asset_prices.csv；
# barchart = data/barchart/volatility_snapshot.csv（列名同 timsun 符号）
_CAT = {
    "equity": "权益",
    "rates": "利率",
    "commodity": "商品",
    "credit": "信用",
    "fxem": "FX/EM",
    "tail": "尾部保护",
}
INDICES: list[tuple[str, str, str, str, str]] = [
    ("VIX", "标普500波动率", "equity", "VIX", "cboe"),
    ("VIXD", "VIX 1日", "equity", "VIX1D", "cboe"),
    ("VXST", "VIX 短期(9日)", "equity", "VIX9D", "cboe"),
    (
        "VXV",
        "VIX 3个月",
        "equity",
        "VIX3M",
        "cboe",
    ),  # VXV≡VIX3M（CBOE VXV 列 2017 年停更）
    ("VXMT", "VIX 6个月", "equity", "VIX6M", "cboe"),
    ("VIXY", "VIX 1年", "equity", "VIX1Y", "cboe"),
    ("VXMO", "标准月度VIX", "equity", "VXMO", "barchart"),
    ("VIN", "Near Term VIX", "equity", "VIN", "cboe"),
    ("VIF", "Far Term VIX", "equity", "VIF", "cboe"),
    ("VVIX", "波动率的波动率", "equity", "VVIX", "cboe"),
    ("VXTH", "尾部对冲指数", "tail", "VXTH", "cboe"),
    ("MOVE", "美债波动率", "rates", "MOVE", "yf"),
    ("TDEX", "TailDex 尾部指数", "tail", "TDEX", "yf"),
    ("VOLI", "VolDex 波动率指数", "tail", "VOLI", "yf"),
    ("VTLT", "20年国债VIX", "rates", "VTLT", "cboe"),
    ("VXHY", "高收益债VIX", "credit", "VXHY", "cboe"),
    ("VXN", "纳指100波动率", "equity", "VXN", "cboe"),
    ("VXD", "道指波动率", "equity", "VXD", "cboe"),
    ("GVZ", "黄金波动率", "commodity", "GVZ", "cboe"),
    ("VXSL", "白银波动率", "commodity", "VXSL", "cboe"),
    ("OVX", "原油波动率", "commodity", "OVX", "cboe"),
    ("VXNG", "天然气波动率", "commodity", "VXNG", "cboe"),
    ("VEWZ", "巴西ETF波动率", "fxem", "VEWZ", "cboe"),
    ("VEEM", "新兴市场波动率", "fxem", "VXEEM", "cboe"),
    ("VXEF", "EFA波动率", "fxem", "VXEF", "barchart"),
    ("VXGO", "Google VIX", "equity", "VXGO", "cboe"),
    ("VXGS", "高盛VIX", "equity", "VXGS", "cboe"),
    ("VXAP", "Apple VIX", "equity", "VXAP", "cboe"),
    ("VXAZ", "Amazon VIX", "equity", "VXAZ", "cboe"),
    ("VXIB", "IBM VIX", "equity", "VXIB", "cboe"),
]
_HERO = ["VIX", "VVIX", "MOVE", "OVX"]
_CHG_N = {"chg1d": 1, "chg5d": 5, "chg1m": 21, "chg1y": 252}


def _load() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    cboe = read_csv_or_empty(ROOT / "data" / "cboe" / "volatility.csv")
    yf = read_csv_or_empty(ROOT / "data" / "yfinance" / "asset_prices.csv")
    bc = read_csv_or_empty(ROOT / "data" / "barchart" / "volatility_snapshot.csv")
    return cboe, yf, bc


def _series(col: str, src: str, dfs: dict) -> pd.Series:
    return dfs[src].get(col, pd.Series(dtype=float)).dropna()


def _chg(s: pd.Series, n: int, fallback: float | None) -> float | None:
    """本地历史算 n 日变化 %；历史不足回落 Barchart 官方字段。"""
    v = chg_pct(s, n)
    return v if v is not None else fallback


def _round(v: float | None, nd: int = 2) -> float | None:
    return None if v is None or pd.isna(v) else round(float(v), nd)


def _indices_table(
    cboe: pd.DataFrame, yf: pd.DataFrame, bc: pd.DataFrame
) -> list[dict]:
    """30 指数统一表：最新值 + 1D/5D/1M/1Y 变化（本地优先，Barchart 兜底）。"""
    dfs = {"cboe": cboe, "yf": yf, "barchart": bc}
    rows = []
    for symbol, name, cat, col, src in INDICES:
        s = _series(col, src, dfs)
        row = {
            "symbol": symbol,
            "name": name,
            "category": cat,
            "category_name": _CAT[cat],
            "value": _round(latest(s)) if not s.empty else None,
        }
        for key, n in _CHG_N.items():
            fb = (
                _round(latest(bc[f"{symbol}_{key}"]))
                if f"{symbol}_{key}" in bc
                else None
            )
            # 变化口径：Barchart 官方字段优先（与 timsun 同源同口径），
            # 本地历史自算作兜底（快照缺失/过期时）
            row[key] = fb if fb is not None else _chg(s, n, None)
        rows.append(row)
    return rows


# ── 区块一：Hero 四卡 + 一句话定调 ──


def _hero(rows: list[dict]) -> dict:
    by = {r["symbol"]: r for r in rows}
    cards = [
        {
            "symbol": s,
            "name": by[s]["name"],
            "value": by[s]["value"],
            "chg1d": by[s]["chg1d"],
        }
        for s in _HERO
    ]
    vix, vvix, move, ovx = (by[s]["value"] for s in _HERO)
    if (
        vix is not None
        and vix < 15
        and (move is None or ovx is None or move > 60 or ovx > 40)
    ):
        verdict = "当前更像结构性波动（商品/利率高波），而非全面系统性风险。"
    elif vix is not None and vix >= 25:
        verdict = "权益波动进入警戒区，跨资产波动同步抬升，系统性风险升温。"
    elif vix is not None and vix < 15:
        verdict = "权益波动处于平静区间，市场风险偏好稳定。"
    else:
        verdict = "权益波动处于正常区间，跨资产分化中需关注利率与商品端的溢出。"
    return {"cards": cards, "verdict": verdict}


# ── 区块二：三张信号卡 ──


def _signals(rows: list[dict]) -> list[dict]:
    by = {r["symbol"]: r for r in rows}
    out = []
    vix, vvix = by["VIX"], by["VVIX"]
    chg = lambda r: f"{r['chg1d']:+.2f}%" if r["chg1d"] is not None else "—"  # noqa: E731
    if vvix["value"] and vix["value"] and vvix["value"] > 80 and vix["value"] < 20:
        out.append(
            {
                "title": "尾部保护与现货 VIX 背离",
                "metric": f"VIX {vix['value']:.2f}, VVIX {vvix['value']:.2f}"
                f" ({chg(vvix)} 1D)",
                "text": "VIX 温和但 VVIX 偏高，说明市场并未为日常波动付高价，"
                "却仍在买波动率跳升风险。",
                "advice": "不适合裸卖波动；若做多保护，优先用价差控制 carry。",
            }
        )
    move = by["MOVE"]
    if move["value"]:
        m1m = move["chg1m"]
        if m1m is not None and m1m < -5:
            title, text = (
                "利率波动降温",
                f"债券波动率月跌 {abs(m1m):.1f}%，说明当前不是利率端主导的系统性恐慌。"
                "股债波动联动减弱，组合对冲要更多看商品与尾部风险。",
            )
        elif m1m is not None and m1m > 5:
            title, text = (
                "利率波动升温",
                f"债券波动率月涨 {m1m:.1f}%，久期/政策路径冲击正在成为主要风险源。"
                "股债波动联动增强，警惕利率波动向权益传导。",
            )
        else:
            title, text = (
                "利率波动中性",
                f"MOVE {move['value']:.1f}（{chg(move)} 1D），债券波动无明显方向。"
                "利率端暂非主要矛盾，观察长端国债拍卖与政策信号。",
            )
        out.append(
            {
                "title": title,
                "metric": f"MOVE {move['value']:.2f} ({chg(move)} 1D)",
                "text": text,
                "advice": "",
            }
        )
    ovx, vix = by["OVX"], by["VIX"]
    if ovx["value"] and vix["value"] and ovx["value"] > vix["value"] * 2:
        out.append(
            {
                "title": "商品波动仍是主线",
                "metric": f"OVX {ovx['value']:.2f} ({chg(ovx)} 1D)",
                "text": f"OVX 达 VIX 的 {ovx['value'] / vix['value']:.1f} 倍，"
                "能源、贵金属或白银波动率显著高于权益核心 VIX，"
                "风险更像供应/地缘冲击。",
                "advice": "观察原油、白银与黄金期权，而不是只看 SPX 保护。",
            }
        )
    return out


# ── 区块三：风险来源矩阵（6 格，对齐 timsun 配对标）──

_RISK_PAIRS = [
    ("权益", "VXD", "VXN", "判断 SPX/NDX 保护是否开始重新定价"),
    ("利率", "VTLT", "MOVE", "确认是否由久期/政策路径冲击驱动"),
    ("商品", "VXNG", "OVX", "观察能源与贵金属是否成为风险源"),
    ("信用", "VXHY", "VXHY", "检验风险是否向融资与信用扩散"),
    ("FX/EM", "VEEM", "VEWZ", "检查美元和海外风险是否确认"),
    ("尾部保护", "VOLI", "VXTH", "评估保护需求与保险成本"),
]


def _risk_matrix(rows: list[dict]) -> list[dict]:
    by = {r["symbol"]: r for r in rows}
    out = []
    for name, chg_sym, lvl_sym, check in _RISK_PAIRS:
        a, b = by.get(chg_sym), by.get(lvl_sym)
        chg_v = a["chg1d"] if a else None
        lvl_v = b["value"] if b else None
        out.append(
            {
                "name": name,
                "chg_symbol": chg_sym,
                "chg_value": chg_v,
                "chg_label": f"1D {chg_v:+.2f}%" if chg_v is not None else "1D —",
                "level_symbol": lvl_sym,
                "level_value": lvl_v,
                "level_label": f"{lvl_sym} {lvl_v:.2f}"
                if lvl_v is not None
                else f"{lvl_sym} —",
                "check": check,
            }
        )
    return out


# ── 区块四：Vol Trade Map ──


def _trade_map(rows: list[dict]) -> dict:
    by = {r["symbol"]: r for r in rows}
    vix, vxv = by["VIX"]["value"], by["VXV"]["value"]
    vix9d_chg = by["VXST"]["chg1d"]
    spread = (vxv - vix) if vix is not None and vxv is not None else None
    if vix is not None and vix < 20 and spread is not None and spread > 2:
        conf = "中"
        if vix9d_chg is not None and vix9d_chg > 3:
            conf = "低"
        return {
            "title": "VIX contango carry",
            "strategy": "只考虑定义风险的卖波动结构：Iron condor / covered call "
            "overwrite / defined-risk short vol",
            "trigger": f"VXV−VIX 维持 +2pt 以上（当前 {spread:+.1f}pt），VIX 不上破 20",
            "invalidate": "VIX9D 或 VIXD 跳升并压平期限结构",
            "confidence": conf,
        }
    if vix is not None and vix >= 20:
        return {
            "title": "波动偏高：防守优先",
            "strategy": "买入保护性价差 / 日历价差做多远端波动，避免裸卖波动",
            "trigger": f"VIX {vix:.1f} 已上 20，等待回落至 15 以下再重启 carry",
            "invalidate": "VIX9D 连续回落并 VIX 跌破 15",
            "confidence": "中",
        }
    return {
        "title": "carry 条件不足",
        "strategy": "观望：期限结构斜率不足或数据缺失，不做方向性波动交易",
        "trigger": spread is not None
        and f"VXV−VIX 仅 {spread:+.1f}pt，需回到 +2pt 以上"
        or "VXV−VIX 数据缺失",
        "invalidate": "VXV−VIX 升破 +2pt 且 VIX < 20",
        "confidence": "低",
    }


# ── 区块五：统计条 ──


def _stats(rows: list[dict]) -> dict:
    n = len(rows)

    def avg(key):  # noqa: E306
        vals = [r[key] for r in rows if r[key] is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    return {
        "n": n,
        "up": sum(1 for r in rows if r["chg1d"] is not None and r["chg1d"] > 0),
        "down": sum(1 for r in rows if r["chg1d"] is not None and r["chg1d"] < 0),
        "avg1d": avg("chg1d"),
        "avg5d": avg("chg5d"),
        "avg1m": avg("chg1m"),
    }


# ── 区块六：7 段叙事（规则引擎）──


def _narr_overview(rows: list[dict], st: dict) -> str:
    vix = next(r for r in rows if r["symbol"] == "VIX")
    vxn = next(r for r in rows if r["symbol"] == "VXN")
    move = next(r for r in rows if r["symbol"] == "MOVE")
    ovx = next(r for r in rows if r["symbol"] == "OVX")
    parts = [
        f"样本内 {st['n']} 个波动率指数中 {st['down']} 个下跌、{st['up']} 个上涨，"
        f"日平均 {st['avg1d']:+.2f}%、周平均 {st['avg5d']:+.2f}%、"
        f"月平均 {st['avg1m']:+.2f}%，整体波动率处于"
        f"{'修复回落' if st['avg1m'] and st['avg1m'] < 0 else '抬升'}通道。",
    ]
    if vix["value"] is not None and vix["value"] < 15:
        parts.append(
            f"核心权益波动指标已明显降温：VIX 仅 {vix['value']:.2f}，处于偏低波动区域；"
            f"科技股波动相对偏高，VXN {vxn['value']:.2f} 是主要股指中最高的。"
        )
    elif vix["value"] is not None and vix["value"] >= 25:
        parts.append(f"VIX 高达 {vix['value']:.2f}，权益市场已现恐慌特征。")
    if move["value"] and ovx["value"] and vix["value"]:
        if move["value"] > 60 or ovx["value"] > 40:
            parts.append(
                f"综合 regime 偏「结构性分化」：MOVE {move['value']:.1f}、"
                f"OVX {ovx['value']:.1f} 拉高绝对水平，但并非股票市场恐慌。"
            )
        parts.append(
            "总体看，这是「权益低波、商品利率高波」的结构性环境，而非全面性的系统性恐慌。"
            if vix["value"] < 15
            else "权益与商品利率波动同处高位，属于全面系统性风险阶段。"
        )
    return "".join(parts)


def _narr_source(rows: list[dict]) -> str:
    by = {r["symbol"]: r for r in rows}
    parts = []
    # 权益：指数 + 个股波动
    vxn, vix, vxd = by["VXN"], by["VIX"], by["VXD"]
    if all(r["value"] for r in (vxn, vix, vxd)):
        hi = max(vxn, vix, vxd, key=lambda r: r["value"])
        parts.append(
            f"股票波动中 {hi['name']} 最大（{hi['value']:.2f}），高于标普 VIX "
            f"{vix['value']:.2f} 和道指 VIX {vxd['value']:.2f}，说明科技板块仍是"
            "风险偏好和估值敏感度最高的部分。"
        )
    stocks = [by[s] for s in ("VXIB", "VXAZ", "VXGO", "VXAP")]
    if all(r["value"] for r in stocks) and all(r["chg1m"] is not None for r in stocks):
        worst = min(stocks, key=lambda r: r["chg1m"])
        parts.append(
            f"个股波动中 IBM({by['VXIB']['value']:.2f})、"
            f"亚马逊({by['VXAZ']['value']:.2f})、"
            f"谷歌({by['VXGO']['value']:.2f})和苹果({by['VXAP']['value']:.2f})"
            f"绝对水平偏高，但月变化普遍达 {worst['chg1m']:+.0f}% 左右，"
            "显示财报或事件扰动后正在去波动。"
        )
    # 商品
    ovx, gvz = by["OVX"], by["GVZ"]
    if ovx["value"] and gvz["value"]:
        parts.append(
            f"商品端 OVX 仍高达 {ovx['value']:.2f}"
            + (
                f"，周跌 {abs(ovx['chg5d']):.1f}%、月跌 {abs(ovx['chg1m']):.1f}%，"
                "反映原油市场对地缘或供给冲击的担忧正在快速消退；"
                if ovx["chg1m"] and ovx["chg1m"] < 0
                else "，"
            )
            + f"黄金 GVZ {gvz['value']:.2f}"
            + (
                f" 同比 +{gvz['chg1y']:.0f}%，通胀、实际利率和央行购金等"
                "中期不确定性仍支撑黄金波动。"
                if gvz["chg1y"] and gvz["chg1y"] > 0
                else "。"
            )
        )
    # FX/EM
    vewz, veem = by["VEWZ"], by["VEEM"]
    if vewz["value"] and veem["chg1m"] is not None:
        parts.append(
            f"外汇与新兴市场端，巴西 ETF VIX {vewz['value']:.2f}，新兴市场 VEEM 月变化 "
            f"{veem['chg1m']:+.1f}%，显示汇率与跨境资产波动存在局部分化。"
        )
    # 尾部
    vvix = by["VVIX"]
    if vvix["value"] and vvix["chg1m"] is not None:
        parts.append(
            f"VVIX 为 {vvix['value']:.2f}，日周月均"
            f"{'回落' if vvix['chg1m'] < 0 else '抬升'}，表明市场对波动率尾部风险的"
            f"恐惧{'减弱' if vvix['chg1m'] < 0 else '加剧'}，"
            + (
                "但绝对水平仍不低，投资者尚未完全放松对冲需求。"
                if vvix["value"] > 80
                else "对冲需求同步降温。"
            )
        )
    return "".join(parts)


def _narr_term(df: pd.DataFrame, term: dict) -> str:
    s1y = df["VIX1Y"].dropna() if "VIX1Y" in df else pd.Series(dtype=float)
    v1y = round(float(s1y.iloc[-1]), 2) if not s1y.empty else None
    ts = term["values"]
    head = (
        f"当前期限结构呈{'标准 ' if term['state'] == 'contango' else ''}"
        f"{term['state']}："
        f"1日 VIX {ts[0]}、9日 VIX {ts[1]}、30日 VIX {ts[2]}、3月 VIX {ts[3]}、"
        f"6月 VIX {ts[4]}" + (f"、1年 VIX {v1y}" if v1y is not None else "") + "。"
    )
    if term["state"] == "contango":
        body = (
            "近低远高，远端对近端的风险溢价显著，市场把近期风险定价很低，"
            "却为中期不确定性（政策路径、通胀粘性、盈利周期）支付高溢价。"
            "若短端跌幅远大于长端，曲线变陡，通常意味着短期压力释放、"
            "但中期风险并未同等下降，近端减压与远端防御之间形成背离。"
        )
    else:
        body = (
            "近高远低，市场正为近期尾部风险支付溢价，期限结构倒挂通常伴随"
            "高波动阶段，且常出现在事件冲击或流动性紧张时期。"
        )
    return head + body


def _narr_cross(rows: list[dict]) -> str:
    by = {r["symbol"]: r for r in rows}
    vix, ovx, gvz = by["VIX"], by["OVX"], by["GVZ"]
    move, vtlx = by["MOVE"], by["VTLT"]
    parts = []
    if all(r["value"] for r in (vix, ovx)):
        parts.append(
            f"股票与商品波动明显分化：VIX 已降至 {vix['value']:.2f}，而 OVX 仍达 "
            f"{ovx['value']:.2f}、GVZ {gvz['value']:.2f}，这不是同涨的系统性风险信号，"
            "而是集中在能源、贵金属和通胀链条上的结构性风险。"
        )
    if all(r["value"] for r in (vix, move)):
        parts.append(
            f"VIX 与 MOVE 形成典型股债波动背离："
            f"MOVE {move['value']:.1f} 处历史偏高水平，"
            + (
                f"长端国债 VTLT 日涨 {vtlx['chg1d']:+.1f}%、"
                f"月涨 {vtlx['chg1m']:+.1f}%，"
                "高收益债 VXHY 同步升温，说明利率市场不确定性"
                "显著高于股票市场。"
                if vtlx["chg1m"]
                else ""
            )
            + "这种背离若持续，利率波动可能通过折现率、信用利差和资产估值"
            "向权益波动率传导，历史上最终常以股票波动率补涨收敛。"
        )
    vals = [r["value"] for r in rows if r["value"] is not None]
    if len(vals) > 5:
        parts.append(
            f"样本内最高与最低波动离散度很大（{max(vals):.1f} vs {min(vals):.1f}），"
            "说明当前波动并非全面扩散，而是局部和结构性扰动。总体交叉信号指向："
            "近期股市平静可能是局部现象，跨资产中利率和商品压力尚未完全消除。"
        )
    return "".join(parts)


def _risk_score(rows: list[dict], term: dict) -> int:
    """综合评分 1-10：权益波动越低扣分越多，利率/商品/尾部/倒挂各加分。"""
    by = {r["symbol"]: r for r in rows}
    vix = by["VIX"]["value"]
    score = 4
    if vix is not None:
        if vix < 15:
            score -= 2
        elif vix < 20:
            score -= 1
        elif vix >= 30:
            score += 3
        elif vix >= 25:
            score += 2
    if by["MOVE"]["value"] and by["MOVE"]["value"] > 60:
        score += 1
    if by["OVX"]["value"] and by["OVX"]["value"] > 40:
        score += 1
    if by["VVIX"]["value"] and by["VVIX"]["value"] > 80:
        score += 1
    if term["state"] == "backwardation":
        score += 1
    return max(1, min(10, score))


def _narr_risk(rows: list[dict], term: dict) -> tuple[str, int]:
    by = {r["symbol"]: r for r in rows}
    vix, vixd, vix9 = by["VIX"], by["VIXD"], by["VXST"]
    vxn, move, ovx = by["VXN"], by["MOVE"], by["OVX"]
    score = _risk_score(rows, term)
    parts = [
        f"综合评分 {score}/10：权益市场短期波动风险"
        f"{'偏低' if vix['value'] < 15 else '中性' if vix['value'] < 20 else '偏高'}，"
        "但跨资产结构性风险使整体环境仍属中性偏高。"
    ]
    if vix["value"] < 20:
        parts.append(
            f"核心理由：VIX {vix['value']:.2f}、1日 VIX {vixd['value']:.2f}、"
            f"9日 VIX {vix9['value']:.2f}，股票市场短期没有恐慌特征；"
            f"但 MOVE {move['value']:.1f}、OVX {ovx['value']:.1f} 及长端 VIX 仍高，"
            "中期风险并未出清。"
        )
        parts.append(
            "未来 1-2 周权益波动率继续下探空间有限，9日 VIX 已接近低位，"
            "可能出现低位震荡甚至反弹。需关注的风险事件包括 FOMC 政策信号、"
            "通胀与就业数据、国债拍卖和长端利率波动，以及原油地缘供给和新兴市场资金流。"
        )
    if vxn["value"] and vxn["value"] > 18:
        parts.append(
            f"若利率波动由 MOVE 向权益传导，或科技股盈利预期生变，"
            f"VXN 可能自 {vxn['value']:.1f} 附近重新抬升，并带动整体 VIX 反弹。"
        )
    return "".join(parts), score


def _narr_trade(rows: list[dict], term: dict, skew: float | None) -> str:
    by = {r["symbol"]: r for r in rows}
    vix, vix9, vxn = by["VIX"], by["VXST"], by["VXN"]
    ovx, gvz, move = by["OVX"], by["GVZ"], by["MOVE"]
    parts = []
    if vix["value"] and vix9["value"] and vix["value"] < 20:
        parts.append(
            f"当前最值得关注的是权益波动率保护：VIX {vix['value']:.2f} 和 9 日 VIX "
            f"{vix9['value']:.2f} 已较低，买入股票指数看跌保护成本相对便宜"
            + (
                f"，科技股尤需防 VXN 从 {vxn['value']:.1f} 反弹。"
                if vxn["value"]
                else "。"
            )
        )
    if term["state"] == "contango":
        parts.append(
            "期限结构 contango 下，做空近端波动率的展期收益为正但已有限，"
            "且一旦事件冲击近端反弹最剧烈，不宜过度裸空 9D 或 VIX。更稳妥的策略"
            "可构建日历价差，做多 3 个月至 1 年远端波动并部分对冲近端空头，"
            "或用 VXTH 等尾部对冲工具管理黑天鹅风险。"
        )
    if ovx["value"] and ovx["chg1m"] is not None:
        ovx_dir = "快速回落" if ovx["chg1m"] < 0 else "仍在抬升"
        ovx_action = (
            "追空原油波动需防地缘反复" if ovx["chg1m"] < 0 else "做多商品波动可继续持有"
        )
        parts.append(
            f"商品端 OVX 高企但周月{ovx_dir}，{ovx_action}；"
            f"黄金波动率同比仍{'高' if gvz['chg1y'] and gvz['chg1y'] > 0 else '低'}，"
            "可继续作为通胀与实际利率对冲线条。"
        )
    if move["value"] and move["value"] > 60:
        parts.append(
            "债券端 MOVE 高企说明利率期权定价昂贵，但在长端债波动确认见顶前"
            "不宜单边做空债券波动。跨资产配置需警惕股债波动背离收敛：若长端利率"
            "波动继续上升，高估值科技股和信用债可能同时承受波动与估值压力。"
        )
    if skew is not None and skew >= 140:
        parts.append(
            f"SKEW {skew:.0f} 偏高，尾部对冲需求旺盛，保护仓位应适度提前布局。"
        )
    return "".join(parts)


def _narr_basic(rows: list[dict], term: dict, card: dict) -> list[dict]:
    """AI 基础分析三块：展望 / 期限结构 / VIX 水平。"""
    by = {r["symbol"]: r for r in rows}
    vix, vixd = by["VIX"], by["VIXD"]
    center = vix["value"]
    outlook = (
        (
            f"本周 VIX 大概率在 {max(0, center - 3):.0f}-{center + 3:.0f} 区间波动。"
            "拐点催化剂是通胀与就业数据：若核心通胀超预期，VIX 将瞬间跳升并触发"
            "期限结构前端陡峭；若数据温和且无地缘升级，VIX 将回探下沿，"
            "但远端高溢价不会消退，持续压制风险偏好。"
            f"若原油供给端出现实质性断供威胁，VIX 将突破 {center + 7:.0f}。"
        )
        if center is not None
        else "数据缺失，暂不给出展望。"
    )
    term_txt = (
        (
            f"期限结构呈{'陡峭 ' if term['state'] == 'contango' else ''}"
            f"{term['state']}，"
            f"1日 VIX 仅 {vixd['value']:.2f}，30日 VIX {vix['value']:.2f}，"
            f"1年期 VIX {by['VIXY']['value']:.2f}，"
            "前端近乎完全平坦，市场对极短期风险几乎无对冲；远端溢价定价的是"
            "地缘升级、融资压力等中期尾部事件，而非即期冲击。"
        )
        if vixd["value"] and by["VIXY"]["value"]
        else "期限结构数据缺失。"
    )
    pct = card.get("percentile_1y")
    vix_txt = (
        f"截至最新收盘，VIX 为 {vix['value']:.2f}，"
        f"日内{'上涨' if vix['chg1d'] and vix['chg1d'] > 0 else '下跌'}"
        f"{abs(vix['chg1d']):.2f}%"
        if vix["chg1d"] is not None
        else ""
    )
    vix_txt += (
        f"，近一年 {pct}% 分位，绝对水平处于"
        f"{'平静区间下沿' if vix['value'] < 15 else '正常区间'}，"
        "市场定价的近期波动预期低迷，但下一份宏观数据将直接考验这一安逸定价。"
        if pct is not None and vix["value"] is not None
        else "。"
    )
    return [
        {"title": "展望", "text": outlook},
        {"title": "期限结构", "text": term_txt},
        {"title": "VIX 水平", "text": vix_txt},
    ]


def _narrative(
    rows: list[dict], st: dict, df: pd.DataFrame, term: dict, card: dict
) -> list[dict]:
    skew = latest(df["SKEW"]) if "SKEW" in df else None
    risk_txt, score = _narr_risk(rows, term)
    return [
        {"title": "波动率全景概述", "text": _narr_overview(rows, st)},
        {"title": "波动来源定位", "text": _narr_source(rows)},
        {"title": "期限结构分析", "text": _narr_term(df, term)},
        {"title": "交叉信号分析", "text": _narr_cross(rows)},
        {"title": "风险评估与前瞻", "text": risk_txt, "score": score},
        {"title": "交易含义", "text": _narr_trade(rows, term, skew)},
        {
            "title": "波动率 AI 基础分析",
            "text": "规则引擎生成的三段式基础分析。",
            "parts": _narr_basic(rows, term, card),
        },
    ]


def _llm_generate_dashboard() -> dict | None:
    """LLM 生成入口（预留）：返回同构 dict 或 None（回落规则引擎）。"""
    return None


_LLM_ENABLED = False


def generate_dashboard() -> dict:
    """波动率全景仪表盘统一入口：LLM 优先（预留），规则引擎兜底。"""
    if _LLM_ENABLED:
        llm_out = _llm_generate_dashboard()
        if llm_out is not None:
            return {**llm_out, "generator": "llm"}
    cboe, yf, bc = _load()
    if cboe.empty or "VIX" not in cboe:
        return {"error": "data/cboe/volatility.csv 为空，先运行 ./bin/fetch_cboe"}
    rows = _indices_table(cboe, yf, bc)
    st = _stats(rows)
    term = term_structure(cboe)
    # 期限结构补 1Y 端点（timsun 六点结构）
    s1y = cboe["VIX1Y"].dropna() if "VIX1Y" in cboe else pd.Series(dtype=float)
    term["labels"].append("1Y")
    term["values"].append(round(float(s1y.iloc[-1]), 2) if not s1y.empty else None)
    card = {}
    if "VIX" in cboe and not cboe["VIX"].dropna().empty:
        from src.volatility_analysis import vix_card

        card = vix_card(cboe)
    return {
        "generator": "rules",
        "as_of": cboe.index.max().strftime("%Y-%m-%d"),
        "hero": _hero(rows),
        "signals": _signals(rows),
        "indices": rows,
        "stats": st,
        "risk_matrix": _risk_matrix(rows),
        "trade_map": _trade_map(rows),
        "narrative": _narrative(rows, st, cboe, term, card),
        "term_structure": term,
        # 区间色表下发（前端色条直接消费）
        "zones": [{"label": lb, "color": c} for lb, _, _, c in ZONES],
    }


if __name__ == "__main__":
    # 自检：跑通 + 打印渲染结果
    import json

    out = generate_dashboard()
    print(json.dumps(out, ensure_ascii=False, indent=1)[:6000])
