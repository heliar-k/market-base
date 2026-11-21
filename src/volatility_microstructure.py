from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import pandas as pd

from .analysis_utils import read_csv_or_empty
from .config import ROOT

_SNAPSHOT_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})\.json$")
WIDE_INDICES = ("SPY", "QQQ", "IWM")
CORE_TERM_COLS = ("VIX9D", "VIX", "VIX3M")
CORE_ANCHOR_COLS = ("VIX9D", "VIX", "VIX3M", "VVIX", "VIXEQ", "COR1M")

# 规则阈值表（经 1 年历史分布校准，单源供评分/清单/测试共用）
THRESHOLDS: dict[str, float] = {
    "vix_risk": 25.0,
    "vix_relief": 22.0,
    "vvix_warn": 110.0,
    "vvix_stress": 120.0,
    "vvix_relief": 105.0,
    "skew_warn": 150.0,
    "term_invert": 1.00,
    "disp_risk": 2.00,
    "disp_relief": 2.20,
    "cor1m_risk": 25.0,
    "cor1m_warn": 20.0,
    "cor1m_relief": 20.0,
    "cor3m_warn": 22.0,
    "dspx_warn": 42.0,
    "pc_equity_risk": 0.72,
    "pc_index_risk": 1.15,
    "pc_total_risk": 1.00,
    "pc_etp_risk": 1.25,
    "pc_vix_risk": 0.80,
    "pc_spx_risk": 1.28,
}

# 五维基础权重（某维度缺数据时对其余可用维度按权重比例归一化）
DIM_WEIGHTS: dict[str, float] = {
    "vol": 0.25,
    "term": 0.15,
    "dealer": 0.30,
    "disp": 0.15,
    "flow": 0.15,
}

STAGES: list[dict[str, Any]] = [
    {
        "id": 1,
        "code": "STAGE 01",
        "name": "缩圈与离散稳态",
        "desc": (
            "宽基处于正 Gamma 缓冲区间，做市商高抛低吸压制日内波动；"
            "个股与指数波动比维持高位，相关性温和。"
        ),
        "rule": "VIX < 20 · 期限升水 · 宽基 GEX ≥ 0",
        "tone": "up",
    },
    {
        "id": 2,
        "code": "STAGE 02",
        "name": "拥挤见顶与尾部预警",
        "desc": (
            "左尾保护溢价、VVIX 或短端贴水抬头，离散交易趋于拥挤或宽基转入负 Gamma，"
            "市场对冲击敏感度上升。"
        ),
        "rule": "期限倒挂 / 宽基 GEX < 0 / SKEW ≥ 150 / VVIX ≥ 110",
        "tone": "warn",
    },
    {
        "id": 3,
        "code": "STAGE 03",
        "name": "负反馈翻转与 Vanna 踩踏",
        "desc": (
            "相关性急升导致离散头寸承压，宽基翻入负 Gamma 且期限结构倒挂，"
            "做市商被迫顺势对冲放大单边波动。"
        ),
        "rule": "期限倒挂 > 1.00 · 宽基 GEX < 0 · VIX ≥ 22",
        "tone": "down",
    },
    {
        "id": 4,
        "code": "STAGE 04",
        "name": "OPEX 重置与反向 Vanna",
        "desc": (
            "短期看跌期权随到期自然清仓或止盈，VVIX 先行回落且期限结构全面恢复升水，"
            "做市商回补空头形成机械买盘。"
        ),
        "rule": "全期限升水 · VVIX < 105 · 宽基 GEX ≥ 0",
        "tone": "brand",
    },
]


def _clean_num(val: Any) -> float | None:
    """只接受有限实数，剔除 bool / None / NaN / inf / 非数值。"""
    if val is None or isinstance(val, bool):
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _round_half_up(x: float) -> int:
    """与前端 JS Math.round() 一致的向正无穷半入舍入，消除 .5 边界漂移。"""
    return int(math.floor(x + 0.5))


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _tri_or(*conds: bool | None) -> bool | None:
    """Kleene 三值逻辑 OR：任一真即真；否则任一未知即未知；全假才假。"""
    if any(c is True for c in conds):
        return True
    if any(c is None for c in conds):
        return None
    return False


def _tri_and(*conds: bool | None) -> bool | None:
    """Kleene 三值逻辑 AND：任一假即假；否则任一未知即未知；全真才真。"""
    if any(c is False for c in conds):
        return False
    if any(c is None for c in conds):
        return None
    return True


def _pctile(series: pd.Series, value: float | None, window: int = 252) -> float | None:
    """计算 value 在最近 window 个有效观测中的历史分位（0–100）；样本不足 20 返回 None。"""
    v = _clean_num(value)
    if v is None:
        return None
    s = pd.to_numeric(series, errors="coerce").dropna().tail(window)
    if len(s) < 20:
        return None
    return round(float((s <= v).mean() * 100), 1)


def _metric_summary(
    series: pd.Series, window: int = 252, digits: int = 2
) -> dict[str, float | None]:
    """抽取单序列最新值、前值、1日变化与 1 年分位。"""
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return {"value": None, "prev": None, "chg1d": None, "pctile_1y": None}
    cur = _clean_num(s.iloc[-1])
    if cur is None:
        return {"value": None, "prev": None, "chg1d": None, "pctile_1y": None}
    prev = _clean_num(s.iloc[-2]) if len(s) >= 2 else None
    chg = round(cur - prev, digits) if prev is not None else None
    return {
        "value": round(cur, digits),
        "prev": round(prev, digits) if prev is not None else None,
        "chg1d": chg,
        "pctile_1y": _pctile(s, cur, window=window),
    }


def _weighted_avg(parts: list[tuple[float | None, float]]) -> float | None:
    """按可用子项权重归一化求加权均值；全部缺失返回 None。"""
    valid = [(v, w) for v, w in parts if v is not None]
    if not valid:
        return None
    w_sum = sum(w for _, w in valid)
    if w_sum <= 0:
        return None
    return sum(v * w for v, w in valid) / w_sum


def _snapshot_date(path: Path, payload: dict[str, Any]) -> str:
    raw = payload.get("snapshot_date")
    if isinstance(raw, str) and len(raw) == 10:
        return raw
    m = _SNAPSHOT_RE.match(path.name)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return str(payload.get("date", ""))[:10]


def _load_dealer_snapshots(options_dir: Path, max_files: int = 252) -> dict[str, Any]:
    """读取最近 max_files 个宽基（SPY/QQQ/IWM）期权敞口快照及历史宽基汇总 GEX 分布。"""
    empty: dict[str, Any] = {
        "date": None,
        "oi_final": None,
        "agg_gex": None,
        "agg_dex": None,
        "hist_min": None,
        "hist_max": None,
        "hist_count": 0,
        "pctile": None,
        "max_abs": 7.0,
        "symbols": [],
    }
    if not options_dir.exists():
        return empty

    files = sorted(p for p in options_dir.iterdir() if _SNAPSHOT_RE.match(p.name))[-max_files:]
    if not files:
        return empty

    hist_agg: list[float] = []
    latest_payload: dict[str, Any] | None = None
    latest_date: str | None = None

    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        syms = data.get("symbols")
        if not isinstance(syms, dict) or not all(
            isinstance(syms.get(k), dict) for k in WIDE_INDICES
        ):
            continue
        gex_vals = [_clean_num(syms[k].get("net_gex")) for k in WIDE_INDICES]
        if any(v is None for v in gex_vals):
            continue
        agg = round(sum(v for v in gex_vals if v is not None), 2)
        hist_agg.append(agg)
        latest_payload = data
        latest_date = _snapshot_date(path, data)

    if latest_payload is None:
        return empty

    syms = latest_payload["symbols"]
    items: list[dict[str, Any]] = []
    for sym in WIDE_INDICES:
        d = syms[sym]
        pw = d.get("put_wall") if isinstance(d.get("put_wall"), dict) else {}
        cw = d.get("call_wall") if isinstance(d.get("call_wall"), dict) else {}
        items.append(
            {
                "symbol": sym,
                "spot": _clean_num(d.get("spot")),
                "net_gex": _clean_num(d.get("net_gex")),
                "net_dex": _clean_num(d.get("net_dex")),
                "gamma_flip": _clean_num(d.get("gamma_flip")),
                "flip_dist": _clean_num(d.get("flip_dist")),
                "put_wall": _clean_num(pw.get("strike")),
                "call_wall": _clean_num(cw.get("strike")),
                "net_vanna": _clean_num(d.get("net_vanna")),
                "net_charm": _clean_num(d.get("net_charm")),
            }
        )

    agg_gex = hist_agg[-1]
    dex_vals = [_clean_num(syms[k].get("net_dex")) for k in WIDE_INDICES]
    agg_dex = (
        round(sum(v for v in dex_vals if v is not None), 2)
        if all(v is not None for v in dex_vals)
        else None
    )
    hist_min = min(hist_agg)
    hist_max = max(hist_agg)
    peak = max(abs(hist_min), abs(hist_max), abs(agg_gex))
    max_abs = float(max(6, math.ceil(peak * 1.15)))
    pctile = (
        round(sum(1 for x in hist_agg if x <= agg_gex) / len(hist_agg) * 100, 1)
        if len(hist_agg) >= 20
        else None
    )

    return {
        "date": latest_date,
        "oi_final": latest_payload.get("oi_final"),
        "agg_gex": agg_gex,
        "agg_dex": agg_dex,
        "hist_min": hist_min,
        "hist_max": hist_max,
        "hist_count": len(hist_agg),
        "pctile": pctile,
        "max_abs": max_abs,
        "symbols": items,
    }


def _select_cboe_anchor_df(vol_df: pd.DataFrame) -> pd.DataFrame:
    """把 CBOE 宽表截断到最新共同有效交易日，避免慢文件窗口（VIX 滞后）或节假日行跨日混除。"""
    if vol_df.empty:
        return vol_df
    # 优先要求核心 6 列齐备（防止 VIXEQ/COR1M 与 VIX 跨日相除）；次选核心期限 3 列齐备
    for req in (CORE_ANCHOR_COLS, CORE_TERM_COLS):
        present = [c for c in req if c in vol_df.columns]
        if len(present) == len(req):
            valid = vol_df.dropna(subset=present)
            if not valid.empty:
                return vol_df.loc[: valid.index[-1]]
    return vol_df


def compute_microstructure_state(inputs: dict[str, Any]) -> dict[str, Any]:
    """纯函数规则引擎：根据指标快照计算五维评分、阶段判定、传导链与双向确认清单。

    缺失或非有限输入统一按 None 处理，不对缺失项伪造中性读数；可用维度按原权重归一化。
    """
    vix = _clean_num(inputs.get("vix"))
    vvix = _clean_num(inputs.get("vvix"))
    vix9d = _clean_num(inputs.get("vix9d"))
    vix3m = _clean_num(inputs.get("vix3m"))
    skew = _clean_num(inputs.get("skew"))
    vixeq = _clean_num(inputs.get("vixeq"))
    cor1m = _clean_num(inputs.get("cor1m"))
    pc_equity = _clean_num(inputs.get("pc_equity"))
    pc_index = _clean_num(inputs.get("pc_index"))
    agg_gex = _clean_num(inputs.get("agg_gex"))

    vix_vix3m = (vix / vix3m) if (vix is not None and vix3m is not None and vix3m > 0) else None
    vix9d_vix = (vix9d / vix) if (vix9d is not None and vix is not None and vix > 0) else None
    vixeq_vix = (vixeq / vix) if (vixeq is not None and vix is not None and vix > 0) else None

    # 1. 波动率挤兑（25%）
    vol_raw = _weighted_avg(
        [
            (_clamp01((vix - 14.0) / 22.0) * 100 if vix is not None else None, 0.65),
            (_clamp01((vvix - 85.0) / 50.0) * 100 if vvix is not None else None, 0.35),
        ]
    )
    # 2. 期限结构与偏度（15%）
    term_raw = _weighted_avg(
        [
            (_clamp01((vix_vix3m - 0.85) / 0.25) * 100 if vix_vix3m is not None else None, 0.45),
            (_clamp01((vix9d_vix - 0.88) / 0.24) * 100 if vix9d_vix is not None else None, 0.35),
            (_clamp01((skew - 135.0) / 25.0) * 100 if skew is not None else None, 0.20),
        ]
    )
    # 3. 做市商敞口（30%）：量程对齐宽基历史分布（+3.5B → 0 分，-6.5B → 100 分）
    dealer_raw = _clamp01((3.5 - agg_gex) / 10.0) * 100 if agg_gex is not None else None
    # 4. 离散与相关性（15%）
    disp_raw = _weighted_avg(
        [
            (_clamp01((2.50 - vixeq_vix) / 0.95) * 100 if vixeq_vix is not None else None, 0.50),
            (_clamp01((cor1m - 10.0) / 26.0) * 100 if cor1m is not None else None, 0.50),
        ]
    )
    # 5. 期权流量（15%）
    flow_raw = _weighted_avg(
        [
            (_clamp01((pc_equity - 0.50) / 0.32) * 100 if pc_equity is not None else None, 0.50),
            (_clamp01((pc_index - 0.95) / 0.35) * 100 if pc_index is not None else None, 0.50),
        ]
    )

    dim_scores: dict[str, int | None] = {
        "vol": _round_half_up(vol_raw) if vol_raw is not None else None,
        "term": _round_half_up(term_raw) if term_raw is not None else None,
        "dealer": _round_half_up(dealer_raw) if dealer_raw is not None else None,
        "disp": _round_half_up(disp_raw) if disp_raw is not None else None,
        "flow": _round_half_up(flow_raw) if flow_raw is not None else None,
    }
    total_raw = _weighted_avg([(dim_scores[k], DIM_WEIGHTS[k]) for k in DIM_WEIGHTS])
    total = _round_half_up(total_raw) if total_raw is not None else None

    inv_main = vix_vix3m > THRESHOLDS["term_invert"] if vix_vix3m is not None else None
    inv_short = vix9d_vix > THRESHOLDS["term_invert"] if vix9d_vix is not None else None
    inverted_tri = _tri_or(inv_main, inv_short)
    inverted = inverted_tri is True

    # 阶段判定：先判阶段 3，再把所有异常/倒挂/负 Gamma 归入阶段 2，阶段 4 严要求全期限升水
    stage_id: int | None
    if total is None:
        stage_id = None
    elif (
        inverted and agg_gex is not None and agg_gex < 0 and vix is not None and vix >= 22.0
    ) or total >= 65:
        stage_id = 3
    elif (
        inverted
        or (skew is not None and skew >= THRESHOLDS["skew_warn"])
        or (vvix is not None and vvix >= THRESHOLDS["vvix_warn"])
        or (vixeq_vix is not None and vixeq_vix < 2.05)
        or (agg_gex is not None and agg_gex < 0)
        or (vix is not None and vix >= 20.0)
        or total >= 35
    ):
        stage_id = 2
    elif (
        inverted_tri is False
        and vix_vix3m is not None
        and vix_vix3m < 0.96
        and vix is not None
        and vix >= 17.0
        and vvix is not None
        and vvix < THRESHOLDS["vvix_relief"]
        and (agg_gex is None or agg_gex >= 0)
    ):
        stage_id = 4
    else:
        stage_id = 1

    dims = [
        {"key": "vol", "name": "波动率挤兑", "weight": 0.25, "score": dim_scores["vol"]},
        {"key": "term", "name": "期限与偏度", "weight": 0.15, "score": dim_scores["term"]},
        {"key": "dealer", "name": "做市商敞口", "weight": 0.30, "score": dim_scores["dealer"]},
        {"key": "disp", "name": "离散相关性", "weight": 0.15, "score": dim_scores["disp"]},
        {"key": "flow", "name": "期权流量", "weight": 0.15, "score": dim_scores["flow"]},
    ]

    def _chain_state(cond_lit: bool | None, cond_warn: bool | None) -> str:
        if cond_lit is None and cond_warn is None:
            return "missing"
        if cond_lit:
            return "lit"
        if cond_warn:
            return "warn"
        return "normal"

    chain = [
        {
            "step": "1. 相关性抬升",
            "val": f"COR1M {cor1m:.1f}" if cor1m is not None else "COR1M 缺失",
            "state": _chain_state(
                cor1m >= THRESHOLDS["cor1m_risk"] if cor1m is not None else None,
                cor1m >= THRESHOLDS["cor1m_warn"] if cor1m is not None else None,
            ),
        },
        {
            "step": "2. 离散度压缩",
            "val": f"VIXEQ/VIX {vixeq_vix:.2f}" if vixeq_vix is not None else "VIXEQ/VIX 缺失",
            "state": _chain_state(
                vixeq_vix < THRESHOLDS["disp_risk"] if vixeq_vix is not None else None,
                vixeq_vix < THRESHOLDS["disp_relief"] if vixeq_vix is not None else None,
            ),
        },
        {
            "step": "3. 负 Gamma",
            "val": (
                f"宽基 {'+' if agg_gex >= 0 else ''}${agg_gex:.2f}B"
                if agg_gex is not None
                else "宽基 GEX 缺失"
            ),
            "state": _chain_state(
                agg_gex < -2.0 if agg_gex is not None else None,
                agg_gex < 0.0 if agg_gex is not None else None,
            ),
        },
        {
            "step": "4. 期限倒挂",
            "val": f"VIX/3M {vix_vix3m:.2f}" if vix_vix3m is not None else "VIX/3M 缺失",
            "state": _chain_state(
                vix_vix3m > THRESHOLDS["term_invert"] if vix_vix3m is not None else None,
                vix_vix3m > 0.96 if vix_vix3m is not None else None,
            ),
        },
        {
            "step": "5. Vanna 加速",
            "val": f"VVIX {vvix:.1f}" if vvix is not None else "VVIX 缺失",
            "state": _chain_state(
                (vvix >= THRESHOLDS["vvix_stress"] and (agg_gex is None or agg_gex < 0))
                if vvix is not None
                else None,
                vvix >= THRESHOLDS["vvix_warn"] if vvix is not None else None,
            ),
        },
    ]

    def _fmt_part(label: str, val: float | None, digits: int = 2) -> str:
        return f"{label} = {val:.{digits}f}" if val is not None else f"{label} 缺失"

    risk_checks = [
        {
            "id": "r1",
            "title": "相关性突破与离散度压缩",
            "cond": (
                f"VIXEQ/VIX < {THRESHOLDS['disp_risk']:.2f} 或 "
                f"COR1M ≥ {THRESHOLDS['cor1m_risk']:.0f}，离散头寸面临平仓压力"
            ),
            "read": f"{_fmt_part('VIXEQ/VIX', vixeq_vix, 2)} · {_fmt_part('COR1M', cor1m, 2)}",
            "met": _tri_or(
                vixeq_vix < THRESHOLDS["disp_risk"] if vixeq_vix is not None else None,
                cor1m >= THRESHOLDS["cor1m_risk"] if cor1m is not None else None,
            ),
        },
        {
            "id": "r2",
            "title": "波动率期限结构倒挂",
            "cond": (
                f"VIX ≥ {THRESHOLDS['vix_risk']:.0f} 且 "
                "VIX/VIX3M > 1.00 或 VIX9D/VIX > 1.00（贴水确认）"
            ),
            "read": (
                f"{_fmt_part('VIX', vix, 2)} · "
                f"{_fmt_part('VIX/VIX3M', vix_vix3m, 3)} · "
                f"{_fmt_part('VIX9D/VIX', vix9d_vix, 3)}"
            ),
            "met": _tri_and(
                vix >= THRESHOLDS["vix_risk"] if vix is not None else None,
                inverted_tri,
            ),
        },
        {
            "id": "r3",
            "title": "宽基做市商翻入净负 Gamma",
            "cond": "SPY + QQQ + IWM 汇总 Net GEX < 0，对冲流由缓冲转为顺势放大",
            "read": (
                f"当前汇总 = {'+' if agg_gex >= 0 else ''}${agg_gex:.2f}B"
                if agg_gex is not None
                else "宽基期权快照缺失"
            ),
            "met": None if agg_gex is None else agg_gex < 0,
        },
        {
            "id": "r4",
            "title": "期权流量防卫溢价扩散",
            "cond": (
                f"CBOE 指数 P/C ≥ {THRESHOLDS['pc_index_risk']:.2f} 或 "
                f"个股 P/C ≥ {THRESHOLDS['pc_equity_risk']:.2f}，保护买盘放量"
            ),
            "read": (
                f"{_fmt_part('Index P/C', pc_index, 2)} · {_fmt_part('Equity P/C', pc_equity, 2)}"
            ),
            "met": _tri_or(
                pc_index >= THRESHOLDS["pc_index_risk"] if pc_index is not None else None,
                pc_equity >= THRESHOLDS["pc_equity_risk"] if pc_equity is not None else None,
            ),
        },
    ]

    relief_checks = [
        {
            "id": "f1",
            "title": "期限结构恢复健康升水",
            "cond": "VIX9D/VIX < 1.00 且 VIX/VIX3M < 1.00，短端恐慌溢价消退",
            "read": (
                f"{_fmt_part('VIX9D/VIX', vix9d_vix, 3)} · {_fmt_part('VIX/VIX3M', vix_vix3m, 3)}"
            ),
            "met": _tri_and(
                vix9d_vix < THRESHOLDS["term_invert"] if vix9d_vix is not None else None,
                vix_vix3m < THRESHOLDS["term_invert"] if vix_vix3m is not None else None,
            ),
        },
        {
            "id": "f2",
            "title": "VVIX 先行见顶回落",
            "cond": (
                f"VVIX < {THRESHOLDS['vvix_relief']:.0f} 且 "
                f"VIX < {THRESHOLDS['vix_relief']:.0f}，波动率期权买盘降温"
            ),
            "read": f"{_fmt_part('VVIX', vvix, 1)} · {_fmt_part('VIX', vix, 2)}",
            "met": _tri_and(
                vvix < THRESHOLDS["vvix_relief"] if vvix is not None else None,
                vix < THRESHOLDS["vix_relief"] if vix is not None else None,
            ),
        },
        {
            "id": "f3",
            "title": "个股与指数波动比回升",
            "cond": (
                f"VIXEQ/VIX ≥ {THRESHOLDS['disp_relief']:.2f} 且 "
                f"COR1M < {THRESHOLDS['cor1m_relief']:.0f}，相关性回落至常态区间"
            ),
            "read": f"{_fmt_part('VIXEQ/VIX', vixeq_vix, 2)} · {_fmt_part('COR1M', cor1m, 2)}",
            "met": _tri_and(
                vixeq_vix >= THRESHOLDS["disp_relief"] if vixeq_vix is not None else None,
                cor1m < THRESHOLDS["cor1m_relief"] if cor1m is not None else None,
            ),
        },
        {
            "id": "f4",
            "title": "宽基净 GEX 企稳翻正",
            "cond": "SPY + QQQ + IWM 汇总 Net GEX ≥ 0，做市商恢复低买高卖缓冲",
            "read": (
                f"当前汇总 = {'+' if agg_gex >= 0 else ''}${agg_gex:.2f}B"
                if agg_gex is not None
                else "宽基期权快照缺失"
            ),
            "met": None if agg_gex is None else agg_gex >= 0,
        },
    ]

    stage_obj = next((s for s in STAGES if s["id"] == stage_id), None)
    return {
        "score": total,
        "stage_id": stage_id,
        "stage_name": stage_obj["name"] if stage_obj else None,
        "stage_desc": stage_obj["desc"] if stage_obj else "核心数据源缺失，无法完成阶段判定。",
        "dims": dims,
        "chain": chain,
        "inverted": inverted,
        "derived": {
            "vix_vix3m": round(vix_vix3m, 3) if vix_vix3m is not None else None,
            "vix9d_vix": round(vix9d_vix, 3) if vix9d_vix is not None else None,
            "vixeq_vix": round(vixeq_vix, 2) if vixeq_vix is not None else None,
        },
        "checklists": {"risk": risk_checks, "relief": relief_checks},
    }


def generate_volatility_microstructure(data_dir: Path | None = None) -> dict[str, Any]:
    """生成期权微观结构与负反馈监控完整负载（只读 CSV/JSON，不写盘）。"""
    base = data_dir or (ROOT / "data")
    vol_raw_df = read_csv_or_empty(base / "cboe" / "volatility.csv")
    pc_df = read_csv_or_empty(base / "cboe" / "put_call.csv")
    dealer = _load_dealer_snapshots(base / "options_structure")

    if vol_raw_df.empty and pc_df.empty and dealer["date"] is None:
        return {"error": "未找到 CBOE 波动率、Put/Call 与期权结构数据，请先运行 ./bin/fetch_cboe"}

    # 统一锚定到最新核心列齐全交易日，避免 VIX 滞后或节假日单点行引发跨日混算
    vol_df = _select_cboe_anchor_df(vol_raw_df)
    cboe_date = vol_df.index[-1].strftime("%Y-%m-%d") if not vol_df.empty else None
    pc_date = pc_df.index[-1].strftime("%Y-%m-%d") if not pc_df.empty else None

    def _col(df: pd.DataFrame, name: str) -> pd.Series:
        if name not in df.columns:
            return pd.Series(dtype=float)
        return pd.to_numeric(df[name], errors="coerce")

    vix_s = _col(vol_df, "VIX")
    vvix_s = _col(vol_df, "VVIX")
    vix9d_s = _col(vol_df, "VIX9D")
    vix3m_s = _col(vol_df, "VIX3M")
    skew_s = _col(vol_df, "SKEW")
    dspx_s = _col(vol_df, "DSPX")
    vixeq_s = _col(vol_df, "VIXEQ")
    cor1m_s = _col(vol_df, "COR1M")
    cor3m_s = _col(vol_df, "COR3M")

    # 派生比值序列（保持 float64 且严格过滤非正分母）
    vix_vix3m_s = (
        (vix_s / vix3m_s.where(vix3m_s > 0)).dropna()
        if not vol_df.empty
        else pd.Series(dtype=float)
    )
    vix9d_vix_s = (
        (vix9d_s / vix_s.where(vix_s > 0)).dropna() if not vol_df.empty else pd.Series(dtype=float)
    )
    vixeq_vix_s = (
        (vixeq_s / vix_s.where(vix_s > 0)).dropna() if not vol_df.empty else pd.Series(dtype=float)
    )

    metrics = {
        "vix": _metric_summary(vix_s, digits=2),
        "vvix": _metric_summary(vvix_s, digits=1),
        "vix9d": _metric_summary(vix9d_s, digits=2),
        "vix3m": _metric_summary(vix3m_s, digits=2),
        "skew": _metric_summary(skew_s, digits=2),
        "dspx": _metric_summary(dspx_s, digits=2),
        "vixeq": _metric_summary(vixeq_s, digits=2),
        "cor1m": _metric_summary(cor1m_s, digits=2),
        "cor3m": _metric_summary(cor3m_s, digits=2),
        "vix_vix3m": _metric_summary(vix_vix3m_s, digits=3),
        "vix9d_vix": _metric_summary(vix9d_vix_s, digits=3),
        "vixeq_vix": _metric_summary(vixeq_vix_s, digits=2),
    }

    # Put/Call 分类结构（含 1 年分位）
    pc_specs = [
        ("SPX_PC", "SPX 期权", THRESHOLDS["pc_spx_risk"]),
        ("INDEX_PC", "Index 指数", THRESHOLDS["pc_index_risk"]),
        ("VIX_PC", "VIX 期权", THRESHOLDS["pc_vix_risk"]),
        ("ETP_PC", "ETP / ETF", THRESHOLDS["pc_etp_risk"]),
        ("TOTAL_PC", "Total 全市场", THRESHOLDS["pc_total_risk"]),
        ("EQUITY_PC", "Equity 个股", THRESHOLDS["pc_equity_risk"]),
    ]
    pc_items: list[dict[str, Any]] = []
    pc_latest_map: dict[str, float | None] = {}
    for col, label, warn in pc_specs:
        info = _metric_summary(_col(pc_df, col), digits=2)
        pc_latest_map[col] = info["value"]
        pc_items.append(
            {
                "key": col,
                "label": label,
                "value": info["value"],
                "prev": info["prev"],
                "chg1d": info["chg1d"],
                "pctile_1y": info["pctile_1y"],
                "warn": warn,
            }
        )

    # 规则引擎输入快照（取锚定行同日读数，确保与 metrics 同源同日）
    anchor_row = vol_df.iloc[-1] if not vol_df.empty else None

    def _row_val(col_name: str, digits: int = 2) -> float | None:
        if anchor_row is None or col_name not in anchor_row:
            return None
        v = _clean_num(anchor_row[col_name])
        return round(v, digits) if v is not None else None

    live_inputs: dict[str, float | None] = {
        "vix9d": _row_val("VIX9D", 2),
        "vix": _row_val("VIX", 2),
        "vix3m": _row_val("VIX3M", 2),
        "vvix": _row_val("VVIX", 1),
        "skew": _row_val("SKEW", 2),
        "dspx": _row_val("DSPX", 2),
        "vixeq": _row_val("VIXEQ", 2),
        "cor1m": _row_val("COR1M", 2),
        "cor3m": _row_val("COR3M", 2),
        "pc_equity": pc_latest_map.get("EQUITY_PC"),
        "pc_total": pc_latest_map.get("TOTAL_PC"),
        "pc_etp": pc_latest_map.get("ETP_PC"),
        "pc_vix": pc_latest_map.get("VIX_PC"),
        "pc_index": pc_latest_map.get("INDEX_PC"),
        "pc_spx": pc_latest_map.get("SPX_PC"),
        "agg_gex": dealer["agg_gex"],
    }

    state = compute_microstructure_state(live_inputs)

    # 近 1 年离散度与隐含相关性走势（前端 type='time' 点对数组）
    history_disp: list[list[Any]] = []
    history_cor1m: list[list[Any]] = []
    if not vol_df.empty:
        tail_df = vol_df.tail(252)
        for dt, row in tail_df.iterrows():
            iso = dt.strftime("%Y-%m-%d")
            v_val = _clean_num(row.get("VIX"))
            veq_val = _clean_num(row.get("VIXEQ"))
            c1_val = _clean_num(row.get("COR1M"))
            if v_val is not None and veq_val is not None and v_val > 0:
                history_disp.append([iso, round(veq_val / v_val, 2)])
            if c1_val is not None:
                history_cor1m.append([iso, round(c1_val, 2)])

    return {
        "as_of": {
            "cboe": cboe_date,
            "put_call": pc_date,
            "options": dealer["date"],
        },
        "score": state["score"],
        "stage_id": state["stage_id"],
        "stage_name": state["stage_name"],
        "stage_desc": state["stage_desc"],
        "stages": STAGES,
        "dims": state["dims"],
        "chain": state["chain"],
        "thresholds": THRESHOLDS,
        "metrics": metrics,
        "live_inputs": live_inputs,
        "inverted": state["inverted"],
        "put_call": {
            "date": pc_date,
            "items": pc_items,
        },
        "dealer": dealer,
        "history": {
            "vixeq_vix": history_disp,
            "cor1m": history_cor1m,
        },
        "checklists": state["checklists"],
    }
