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
        verdict = "当前更像结构性波动——高波集中在商品/利率，全面系统性风险未扩散。"
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
                "text": "VIX 温和但 VVIX 偏高：市场没为日常波动付高价，"
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
                f"债券波动率月跌 {abs(m1m):.1f}%，当前不是利率端主导的系统性恐慌。"
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
    ("外汇 / 新兴市场", "VEEM", "VEWZ", "检查美元和海外风险是否确认"),
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
            "title": "升水套息（Contango Carry）",
            "strategy": "只考虑最大亏损可控的卖波动结构：Iron condor / covered call。",
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
        "title": "套息条件不足",
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
#
# 行契约（全站统一，AGENTS.md 第 8 节）：叙事字符串用 \n 分「结论 / 依据 / 触发」，最多
# 3 行；没有触发内容的段只给 2 行，不编造条件句。水平值归 hero 四卡 / .vol-stat /
# 期限结构段（其余段只引用变化量）；条件句不带「触发条件：」这类前缀——标签由前端
# R.sigBlocks 给，文案里再写一遍就是双份。

_NO_DATA = (
    "波动率数据缺失，暂不给出研判。运行 ./bin/fetch_cboe（CBOE 指数）、"
    "./bin/fetch_yfinance（MOVE）与 ./bin/fetch_barchart_vol（VXMO/VXEF）后重试。"
)
_CHG_LB = {"chg1d": "日", "chg5d": "周", "chg1m": "月", "chg1y": "年"}
_TERM_CN = ["1日", "9日", "30日", "3月", "6月"]


def _sig(*lines: str) -> str:
    """按行契约拼叙事：丢掉空行（缺触发内容时自然降为两行）。"""
    return "\n".join(ln for ln in lines if ln)


def _chg_txt(r: dict, key: str = "chg1m") -> str:
    """「MOVE 月涨 38.8%」——研判段引用变化量而非水平值（调用方保证该口径非 None）。"""
    v = r[key]
    return f"{r['symbol']} {_CHG_LB[key]}{'涨' if v >= 0 else '跌'} {abs(v):.1f}%"


def _narr_overview(rows: list[dict], st: dict) -> str:
    by = {r["symbol"]: r for r in rows}
    vix, vxn = by["VIX"], by["VXN"]
    move, ovx = by["MOVE"], by["OVX"]
    v = vix["value"]
    if v is None or st["avg1m"] is None:
        return _NO_DATA
    channel = "修复回落" if st["avg1m"] < 0 else "抬升"
    # 涨跌家数与三个平均变化已在 .vol-stat，这里只给 regime 判断（结论前置）
    if move["value"] and ovx["value"]:
        concl = f"整体波动率处于{channel}通道，" + (
            "但这是「权益低波、商品利率高波」的结构性环境，不是全面性的系统性恐慌。"
            if v < 15
            else "权益与商品利率波动同处高位，属于全面系统性风险阶段。"
        )
    elif v >= 25:
        concl = f"整体波动率处于{channel}通道，VIX 已上 25，权益市场现恐慌特征。"
    else:
        concl = (
            f"整体波动率处于{channel}通道，权益波动处于正常区间，"
            "风险集中在利率与商品端。"
        )
    basis = []
    if all(r["chg1m"] is not None for r in (vix, move, ovx)):
        # 「拉高绝对水平的是利率与商品端、并非股票市场恐慌」——原句判据，只换成变化量口径
        basis.append(
            f"综合 regime 偏「结构性分化」：拉高绝对水平的是利率与商品端"
            f"（{_chg_txt(move)}、{_chg_txt(ovx)}），"
            f"并非股票市场恐慌（{_chg_txt(vix)}）"
            if vix["chg1m"] < min(move["chg1m"], ovx["chg1m"])
            else f"权益端与跨资产端同向变化（{_chg_txt(vix)}、{_chg_txt(move)}、"
            f"{_chg_txt(ovx)}），分化并不明显"
        )
        if vxn["value"] and v and vxn["value"] > v:
            basis.append("科技 VXN 是主要股指中最高的，权益端风险集中在纳指")
    trig = (
        "VIX 升破 25 则权益端进入恐慌定价，regime 转为全面系统性风险。"
        if v < 25
        else "VIX 回落至 20 下方则恐慌定价解除。"
    )
    return _sig(concl, "；".join(basis) + "。" if basis else "", trig)


def _narr_source(rows: list[dict]) -> str:
    """风险来源定位：本段一律用变化量口径，水平值归 hero 卡与期限结构段。"""
    by = {r["symbol"]: r for r in rows}
    vxn, vix, vxd = by["VXN"], by["VIX"], by["VXD"]
    ovx, gvz = by["OVX"], by["GVZ"]
    vewz, veem = by["VEWZ"], by["VEEM"]
    vvix = by["VVIX"]
    # 结论：哪个板块在挑大梁（主要股指中最高者 + 商品端）
    lead = None
    hi = None
    if all(r["value"] for r in (vxn, vix, vxd)):
        hi = max(vxn, vix, vxd, key=lambda r: r["value"])
        lead = "科技（纳指）" if hi["symbol"] == "VXN" else hi["name"]
    if lead and ovx["value"] and vix["value"] and ovx["value"] > vix["value"]:
        lead += "与商品端"
    if not lead:
        return _NO_DATA
    # 「不在标普现货本身」只在风险源确实不是 VIX 时成立（VIX 最高时自相矛盾）
    away = "，不在标普现货本身" if hi["symbol"] != "VIX" else ""
    basis = [f"风险源在{lead}{away}。"]
    # 依据：逐板块变化量读数（不复述 hero 卡水平值）
    if all(r["value"] for r in (vxn, vix, vxd)):
        # 本段是 VXN 等个股/股指波动水平值的归属地
        seg = f"股指端 {hi['name']} {hi['value']:.2f} 最高"
        if hi["symbol"] == "VXN":
            seg += "，科技仍是估值敏感度最高的部分"
        basis.append(seg)
    stocks = [by[s] for s in ("VXIB", "VXAZ", "VXGO", "VXAP")]
    if all(r["value"] for r in stocks) and all(
        r["chg1m"] is not None and r["chg1m"] < 0 for r in stocks
    ):
        worst = min(stocks, key=lambda r: r["chg1m"])
        basis.append(
            f"个股波动（IBM/亚马逊/谷歌/苹果）月变化均在回落，"
            f"降幅最大 {worst['name']} {worst['chg1m']:.0f}%"
        )
    if ovx["value"] and gvz["value"]:
        seg = (
            f"商品端 {_chg_txt(ovx)}" if ovx["chg1m"] is not None else "商品端 OVX 仍高"
        )
        if gvz["chg1y"] and gvz["chg1y"] > 0:
            seg += f"、黄金 {_chg_txt(gvz, 'chg1y')}"
        basis.append(seg)
    if vewz["value"] and veem["chg1m"] is not None:
        basis.append(f"外汇/新兴市场 {_chg_txt(vewz)}、{_chg_txt(veem)}，存在局部分化")
    if vvix["value"] and vvix["chg1m"] is not None:
        basis.append(
            f"尾部端 {_chg_txt(vvix)}，日周月均"
            f"{'回落' if vvix['chg1m'] < 0 else '抬升'}，对波动率尾部风险的担忧"
            f"{'减弱' if vvix['chg1m'] < 0 else '加剧'}"
            + ("，但绝对水平仍不低" if vvix["value"] > 80 else "，对冲需求同步降温")
        )
    trig = (
        "个股波动月变化由降转升、或 VVIX 继续抬升，则事件扰动与尾部对冲需求重新抬头。"
        if vvix["value"] and vvix["chg1m"] is not None
        else ""
    )
    return _sig(basis[0], "；".join(basis[1:]) + "。", trig)


def _narr_term(df: pd.DataFrame, term: dict) -> str:
    """期限结构段：VIX 近远端水平值的唯一归属地（其余段只引用变化量）。"""
    s1y = df["VIX1Y"].dropna() if "VIX1Y" in df else pd.Series(dtype=float)
    v1y = round(float(s1y.iloc[-1]), 2) if not s1y.empty else None
    ts = term["values"]
    if ts[2] is None:
        return _NO_DATA
    levels = (
        "、".join(
            f"{lb} VIX {v}"
            for lb, v in zip(_TERM_CN, ts[:5], strict=False)
            if v is not None
        )
        + (f"、1年 VIX {v1y}" if v1y is not None else "")
        + "。"
    )
    if term["state"] == "contango":
        return _sig(
            "市场把近期风险定价很低，却为中期不确定性（政策路径、通胀粘性、"
            "盈利周期）支付高溢价。",
            f"期限结构呈标准 contango（近低远高）：{levels}",
            "若短端跌幅远大于长端使曲线变陡，则近端减压与远端防御形成背离，"
            "对应短期压力释放但中期风险并未同等下降。",
        )
    return _sig(
        "市场正为近期尾部风险支付溢价，期限结构倒挂通常伴随高波动阶段。",
        f"期限结构呈 backwardation（近高远低）：{levels}，"
        "常出现在事件冲击或流动性紧张时期。",
    )


def _narr_cross(rows: list[dict]) -> str:
    """交叉信号：只讲 VIX↔MOVE / VIX↔OVX 背离本身，水平值不重复报。"""
    by = {r["symbol"]: r for r in rows}
    vix, ovx, gvz = by["VIX"], by["OVX"], by["GVZ"]
    move, vtlx, vxhy = by["MOVE"], by["VTLT"], by["VXHY"]
    if not all(r["value"] for r in (vix, move)):
        return _NO_DATA
    concl = (
        "近期股市平静是局部现象：风险集中在能源、贵金属与利率链条，"
        "不是同涨的系统性信号。"
    )
    basis = []
    if ovx["value"]:
        basis.append(
            f"股票与商品明显分化：OVX 达 VIX 的 {ovx['value'] / vix['value']:.1f} 倍"
            + (f"、{_chg_txt(gvz)}" if gvz["chg1m"] is not None else "")
        )
    diverge = f"VIX 与 MOVE 形成典型股债背离：{_chg_txt(move)}"
    if vtlx["chg1m"]:
        diverge += f"、长端国债 {_chg_txt(vtlx)}"
    if vxhy["chg1m"]:
        diverge += f"、高收益债 {_chg_txt(vxhy)} 同步升温"
    diverge += "，利率市场不确定性显著高于股票市场"
    basis.append(diverge)
    trig = (
        "背离若持续，利率波动可能经折现率、信用利差与资产估值向权益传导："
        "2009 年以来 32 个 episode 中 97% 在 60 个交易日内出现 VIX 补涨 10%+"
        "（中位第 11 日），靠债端波动独自回落收敛的仅约 1%；但低 VIX 自身补涨"
        "基础率已达 96%，股波上行不宜直接当作债端风险传导的证据。"
    )
    return _sig(concl, "；".join(basis) + "。", trig)


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
    vix = by["VIX"]
    vxn, move, ovx = by["VXN"], by["MOVE"], by["OVX"]
    score = _risk_score(rows, term)
    if vix["value"] is None:
        return _NO_DATA, score
    concl = (
        f"综合评分 {score}/10：权益市场短期波动风险"
        f"{'偏低' if vix['value'] < 15 else '中性' if vix['value'] < 20 else '偏高'}，"
        "但跨资产结构性风险使整体环境仍属中性偏高。"
    )
    basis = []
    trig = []
    # 1 日 / 9 日 VIX 等水平值归期限结构段，
    # 本段只给「短期无恐慌 / 中期未出清」的分层判断（沿用原 <20 判据，不无条件断言）
    basis.append(
        "股票市场短期没有恐慌特征" if vix["value"] < 20 else "股票市场短期已现恐慌特征"
    )
    hot = "、".join(
        _chg_txt(r) for r in (move, ovx) if r["value"] and r["chg1m"] is not None
    )
    if hot:
        basis.append(f"但 {hot} 及长端 VIX 仍高，中期风险并未出清")
    if vix["value"] < 20:
        trig.append(
            "未来 1-2 周权益波动率继续下探空间有限（9 日 VIX 已接近低位），"
            "可能低位震荡甚至反弹；催化剂是 FOMC 政策信号、通胀与就业数据、"
            "国债拍卖和长端利率波动，以及原油地缘供给与新兴市场资金流。"
        )
    if vxn["value"] and vxn["value"] > 18:
        trig.append(
            "若利率波动由 MOVE 向权益传导，或科技股盈利预期生变，"
            "VXN 可能自当前水平重新抬升，带动整体 VIX 反弹。"
        )
    return _sig(concl, "；".join(basis) + "。", "".join(trig)), score


def _narr_trade(rows: list[dict], term: dict, skew: float | None) -> str:
    by = {r["symbol"]: r for r in rows}
    vix, vix9, vxn = by["VIX"], by["VXST"], by["VXN"]
    ovx, gvz, move = by["OVX"], by["GVZ"], by["MOVE"]
    concl, basis, trig = [], [], []
    if vix["value"] and vix9["value"] and vix["value"] < 20:
        concl.append(
            "权益保护正处低成本窗口，买入股票指数看跌保护相对便宜"
            + ("，但科技股需防 VXN 反弹" if vxn["value"] else "")
            + "。"
        )
    if term["state"] == "contango":
        concl.append(
            "做空近端波动率的展期收益为正但已有限，一旦事件冲击近端反弹最剧烈，"
            "不宜过度裸空 9D 或 VIX。"
        )
        basis.append(
            "更稳妥的是日历价差：做多 3 个月至 1 年远端波动并部分对冲近端空头，"
            "或用 VXTH 等尾部对冲工具应对极端行情"
        )
    if ovx["value"] and ovx["chg1m"] is not None:
        down = ovx["chg1m"] < 0
        basis.append(
            f"商品端 OVX 高企但周月{'快速回落' if down else '仍在抬升'}，"
            + ("追空原油需防地缘反复" if down else "做多商品波动可继续持有")
            + (
                "；黄金波动率同比仍高，可继续作为通胀与实际利率对冲线条"
                if gvz["chg1y"] and gvz["chg1y"] > 0
                else ""
            )
        )
    if move["value"] and move["value"] > 60:
        basis.append(
            "债券端 MOVE 处于高位，利率期权定价昂贵，长端债波动确认见顶前不宜单边做空"
        )
        trig.append(
            "股债波动背离收敛时，若长端利率波动继续上升，高估值科技股和信用债"
            "可能同时承受波动与估值压力。"
        )
    if skew is not None and skew >= 140:
        trig.append(
            f"SKEW {skew:.0f} 偏高、尾部对冲需求旺盛，保护仓位可在波动脉冲前提前布局。"
        )
    if not (concl or basis or trig):
        return _NO_DATA
    return _sig(
        "".join(concl),
        "；".join(basis) + "。" if basis else "",
        "".join(trig),
    )


def _narr_basic(rows: list[dict], term: dict, card: dict) -> list[dict]:
    """AI 基础分析三块：展望 / 期限结构 / VIX 水平。"""
    by = {r["symbol"]: r for r in rows}
    vix, vixd = by["VIX"], by["VIXD"]
    center = vix["value"]
    outlook = (
        (
            f"本周 VIX 大概率在 {max(0, center - 3):.0f}-{center + 3:.0f} 区间波动。"
            "拐点催化剂是通胀与就业数据：若核心通胀超预期，VIX 可能跳升并触发"
            "期限结构前端陡峭；若数据温和且无地缘升级，VIX 将回探下沿，"
            "但远端高溢价难以消退，持续压制风险偏好。"
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
        "市场定价的近期波动预期低迷，但下一份宏观数据发布将检验这一低迷定价。"
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
            "title": "波动率基础分析（规则引擎）",
            "text": "三段解读：本周展望 / 期限结构 / VIX 水平。",
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
        # Barchart 快照日频更新且不滞后（CBOE CDN 延迟一天），页头需区分两个时效
        "as_of_snapshot": (
            bc.index.max().strftime("%Y-%m-%d")
            if not bc.empty and hasattr(bc.index, "strftime")
            else None
        ),
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
