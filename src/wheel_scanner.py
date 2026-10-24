"""确定性巨头池 Wheel 策略扫描器（Sell Put 抄底收租 + Covered Call 波段止盈）。

把「四维入池标准 + 入场信号 + 30~45 DTE 期权选点」固化为可复用的评估引擎。
仅依赖本地已拉取的日线（data/stocks/）与期权结构快照/缓存（data/options_structure/）。

用法:
    uv run python -m src.wheel_scanner                  # 扫描全池（默认 T0+T1+T2）
    uv run python -m src.wheel_scanner --tier T0,T1     # 仅扫描 T0+T1 主力池
    uv run python -m src.wheel_scanner --symbols V,MA,GOOG,NVDA
    uv run python -m src.wheel_scanner --json           # 输出 JSON
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from src.config import ROOT
from src.indicators import compute_all_indicators, load_data
from src.pricing import bs_greeks


@dataclass(frozen=True)
class PoolSpec:
    """确定性巨头池单标的定义与入池理由。"""

    symbol: str
    tier: str  # T0=核心主力, T1=行业寡头对冲, T2=观察/受限(单手过贵或低波)
    sector: str
    moat: str  # 护城河与现金流简述
    opt_symbol: str | None = None  # options_structure 中的 ticker（如 GOOG->GOOGL）


# ═══════════════════════════════════════════════════════════════════════════════
# 1. 确定性巨头池定义（四维筛选：生态/制度垄断 + 回购现金牛 + 单手占资 + 波动率甜点）
# ═══════════════════════════════════════════════════════════════════════════════

WHEEL_POOL: list[PoolSpec] = [
    # ── T0：核心主力池（单手 $20k~$55k，IV 25%~50%，期权极深，常驻监控）──
    PoolSpec("NVDA", "T0", "AI算力", "AI卖铲人垄断，拆股后单手~$21k，IV常年35%+"),
    PoolSpec("AMZN", "T0", "云/电商", "AWS+北美电商基建，单手~$25k，FCF爆发"),
    PoolSpec("GOOG", "T0", "搜索/云", "搜索+YouTube+TPU，常因反垄断/AI噪音出黄金坑", "GOOGL"),
    PoolSpec("AVGO", "T0", "ASIC/网络", "定制ASIC+交换芯片+VMware现金牛，高弹性高权利金"),
    PoolSpec("TSM", "T0", "晶圆代工", "全球先进制程唯一底座，地缘噪音常给高IV买点"),
    PoolSpec("UNH", "T0", "医疗保险", "全美最大商业医保+Optum闭环，与科技股低相关"),
    PoolSpec("MSFT", "T0", "企业软件/云", "企業OS+Azure双垄断，确定性天花板"),
    # ── T1：跨行业寡头与轮动对冲池（防科技板块单边杀估值）──
    PoolSpec("META", "T1", "社交广告", "30亿月活广告印钞机，IV肥，单手~$65k偏大"),
    PoolSpec("V", "T1", "支付清算", "全球法币消费收税官，净利率50%+，单手~$41k优于MA"),
    PoolSpec("JPM", "T1", "金融底座", "美国大而不倒第一行，单手~$34k，金融周期压舱石"),
    PoolSpec("AAPL", "T1", "硬件生态", "美股第一回购机器，平时HV偏低，等果链利空IV脉冲出手"),
    PoolSpec("ISRG", "T1", "医疗器械", "达芬奇手术机器人垄断，装机+耗材剃须刀模式"),
    PoolSpec("ABBV", "T1", "创新药", "高股息医药现金牛，单手~$28k，接货自带3%+分红垫"),
    # ── T2：观察/受限池（好公司但做标准 Wheel 有硬伤：单手过贵或权利金过薄）──
    PoolSpec("MA", "T2", "支付清算", "与V同级垄断，但单手~$63k占资比V高50%，优选V"),
    PoolSpec("COST", "T2", "会员零售", "护城河极深，但单手~$102k过重+HV仅17%，宜正股定投"),
    PoolSpec("LLY", "T2", "代谢/创新药", "GLP-1龙头，但单手~$131k锁资过重，宜正股分批"),
    PoolSpec(
        "BRK.B", "T2", "保险/现金池", "HV仅10%权利金太薄，适合充当保证金底仓而非卖期权", "BRK-B"
    ),
]

# ═══════════════════════════════════════════════════════════════════════════════
# 2. 筛选与信号阈值（固化规则）
# ═══════════════════════════════════════════════════════════════════════════════

THRESHOLDS = {
    # 四维适格门槛
    "lot_ideal_max_k": 55.0,  # 单手理想占资上限（$55k，即股价 $550）
    "lot_hard_max_k": 70.0,  # 单手硬上限（超此值标记「单手偏重」）
    "hv20_min": 18.0,  # 20日历史波动率下限（低于此值权利金太薄）
    "hv20_max": 50.0,  # 20日历史波动率上限（高于此值属高Beta妖股）
    # Sell Put 击球区（满足其一即进入观察/出手）
    "put_rsi_trigger": 40.0,  # RSI(14) <= 40 超卖/偏弱
    "put_ma50_dist_max": -2.0,  # 跌破 MA50 至少 2% 或回踩 MA200 附近
    "put_iv_min": 25.0,  # 隐含波动率（或 HV20）>= 25% 保证权利金厚度
    # Sell Call（Covered Call）击球区
    "call_rsi_trigger": 68.0,  # RSI(14) >= 68 动量过热
    "call_ma50_dist_min": 6.0,  # 高于 MA50 >= 6%（短期拉升过急）
    # 期权选点参数（DTE 与 |Delta|）
    "dte_min": 21,
    "dte_max": 49,
    "put_delta_target": -0.22,  # Sell Put 目标 Delta（-0.18 ~ -0.28）
    "call_delta_target": 0.18,  # Sell Call 目标 Delta（0.14 ~ 0.25）
    # 事件排雷阈值
    "earnings_danger_dte": 7,  # 财报 <= 7 天：禁止裸卖近月，信号强制转为排雷警告
    "earnings_warn_dte": 35,  # 财报 <= 35 天：落入 30~45 DTE 期权周期内，标注跨财报警告
    "ex_div_warn_dte": 14,  # 除息 <= 14 天：Sell Call 警惕提前被行权（Early Assignment）
}


@dataclass
class OptionPick:
    """推荐的单个期权合约（从当日 raw 期权链按 BS Delta 筛出）。"""

    right: str  # 'P' or 'C'
    expiration: str  # YYYY-MM-DD
    dte: int
    strike: float
    delta: float
    iv_pct: float
    oi: int
    dist_pct: float  # 距现价百分比（Put 为负或正距离，统一用 |strike-spot|/spot）


@dataclass
class ScanRow:
    symbol: str
    tier: str
    sector: str
    close: float
    lot_k: float  # 100 股占压资金 ($k)
    rsi: float
    vs_ma50_pct: float
    vs_ma200_pct: float
    dd_52w_pct: float
    bb_pos_pct: float  # 布林带位置 (0%=下轨, 100%=上轨)
    hv20_pct: float
    iv_front_pct: float | None
    iv_back_pct: float | None
    iv_hv_spread: float | None
    put_wall: float | None
    call_wall: float | None
    earnings_date: str | None
    earnings_dte: int | None
    ex_div_date: str | None
    ex_div_dte: int | None
    event_alert: str  # 事件排雷提示（如 "⚠️财报5d" / "📅财报25d" / "💰除息4d"）
    suitability: str  # 适格评估：黄金 / 适格 / 单手偏重 / 低波薄利
    signal: str  # 🟢 SELL PUT / 🔴 SELL CALL / 🟡 观察 / ⛔ 财报避险 / ⚪ 等待
    reason: str
    put_pick: OptionPick | None = None
    call_pick: OptionPick | None = None


def _latest_options_snapshot() -> tuple[str | None, dict]:
    opt_dir = ROOT / "data" / "options_structure"
    files = sorted(opt_dir.glob("20*.json"))
    if not files:
        return None, {}
    data = json.loads(files[-1].read_text(encoding="utf-8"))
    return files[-1].stem, data.get("symbols", {})


def _pick_contract(
    opt_sym: str, snap_date: str | None, ov: dict, spot: float, right: str, target_delta: float
) -> OptionPick | None:
    """优先从 raw 期权链选 21~49 DTE 合约；若 raw 已清理则按快照 Back IV + BSM 估算目标行权价。"""
    raw_path = (
        ROOT / "data" / "options_structure" / f"raw_{opt_sym}_{snap_date}.csv"
        if snap_date
        else None
    )
    if not raw_path or not raw_path.exists():
        cands = sorted((ROOT / "data" / "options_structure").glob(f"raw_{opt_sym}_*.csv"))
        raw_path = cands[-1] if cands else None

    if raw_path and raw_path.exists():
        df = pd.read_csv(raw_path, dtype={"expiration": str})
        df = df[
            (df["right"] == right) & (df["openInterest"] >= 50) & (df["impliedVolatility"] > 0.01)
        ]
        df = df[df["strike"] < spot * 0.99] if right == "P" else df[df["strike"] > spot * 1.01]
        if not df.empty:
            today = datetime.now().date()
            df["exp_dt"] = pd.to_datetime(df["expiration"], format="%Y%m%d").dt.date
            df["dte"] = df["exp_dt"].apply(lambda d: (d - today).days)
            win = df[
                (df["dte"] >= THRESHOLDS["dte_min"]) & (df["dte"] <= THRESHOLDS["dte_max"])
            ].copy()
            if win.empty:
                win = df[(df["dte"] >= 14) & (df["dte"] <= 65)].copy()
            if not win.empty:
                best_exp = win.iloc[(win["dte"] - 35).abs().argsort()[:1]]["expiration"].iloc[0]
                sub = win[win["expiration"] == best_exp].copy()
                deltas = []
                for _, r in sub.iterrows():
                    t = max(int(r["dte"]), 1) / 365.0
                    _, dc = bs_greeks(spot, float(r["strike"]), t, float(r["impliedVolatility"]))
                    deltas.append(dc if right == "C" else dc - 1.0)
                sub["delta"] = deltas
                sub["delta_diff"] = (sub["delta"] - target_delta).abs()
                sub = sub[sub["delta_diff"] <= 0.12]
                if not sub.empty:
                    best = sub.sort_values("delta_diff").iloc[0]
                    exp_s = str(best["expiration"])
                    return OptionPick(
                        right=right,
                        expiration=f"{exp_s[:4]}-{exp_s[4:6]}-{exp_s[6:8]}",
                        dte=int(best["dte"]),
                        strike=float(best["strike"]),
                        delta=round(float(best["delta"]), 2),
                        iv_pct=round(float(best["impliedVolatility"]) * 100, 1),
                        oi=int(best["openInterest"]),
                        dist_pct=round((float(best["strike"]) - spot) / spot * 100, 1),
                    )

    # Fallback：raw 链不存在时，用快照 35d IV 反推理论行权价（供盘后快速参考）
    iv_ref = (ov.get("iv_back") or {}).get("iv") or (ov.get("iv_front") or {}).get("iv")
    if not iv_ref:
        return None
    sigma = float(iv_ref) / 100.0
    dte = 35
    t = dte / 365.0
    step = 5.0 if spot >= 150 else 2.5
    strikes = (
        np.arange(round(spot * 0.75 / step) * step, spot * 0.99, step)
        if right == "P"
        else np.arange(round(spot * 1.01 / step) * step, spot * 1.25, step)
    )
    if len(strikes) == 0:
        return None
    best_k, best_d, min_err = None, 0.0, 999.0
    for k in strikes:
        _, dc = bs_greeks(spot, float(k), t, sigma)
        d_val = dc if right == "C" else dc - 1.0
        err = abs(d_val - target_delta)
        if err < min_err:
            best_k, best_d, min_err = float(k), d_val, err
    if best_k is None or min_err > 0.12:
        return None
    return OptionPick(
        right=right,
        expiration="~35d(BSM估算)",
        dte=dte,
        strike=best_k,
        delta=round(best_d, 2),
        iv_pct=round(float(iv_ref), 1),
        oi=0,
        dist_pct=round((best_k - spot) / spot * 100, 1),
    )


def _days_until(date_str: str | None) -> int | None:
    if not date_str:
        return None
    try:
        dt = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
        return (dt - datetime.now().date()).days
    except Exception:
        return None


def _event_fields(ov: dict) -> tuple[str | None, int | None, str | None, int | None, str]:
    """解析财报日与除息日倒计时，生成紧凑的排雷标签。"""
    ed = ov.get("earnings_date")
    e_dte = _days_until(ed)
    if e_dte is not None and e_dte < 0:
        ed, e_dte = None, None  # 已过去的旧财报日不展示

    exd = ov.get("ex_dividend_date")
    div_dte = _days_until(exd)
    if div_dte is not None and div_dte < 0:
        exd, div_dte = None, None

    tags: list[str] = []
    if e_dte is not None:
        if e_dte <= THRESHOLDS["earnings_danger_dte"]:
            tags.append(f"⚠️财报{e_dte}d({ed[5:]})")
        elif e_dte <= THRESHOLDS["earnings_warn_dte"]:
            tags.append(f"📅财报{e_dte}d({ed[5:]})")
        else:
            tags.append(f"财报{e_dte}d")
    if div_dte is not None and div_dte <= THRESHOLDS["ex_div_warn_dte"]:
        tags.append(f"💰除息{div_dte}d")
    return ed, e_dte, exd, div_dte, " ".join(tags) or "—"


def evaluate_symbol(spec: PoolSpec, snap_date: str | None, opt_map: dict) -> ScanRow | None:
    csv_path = ROOT / "data" / "stocks" / f"{spec.symbol}.csv"
    if not csv_path.exists():
        return None
    df = compute_all_indicators(load_data(str(csv_path)))
    if df.empty or len(df) < 200:
        return None

    last = df.iloc[-1]
    close = float(last["close"])
    lot_k = round(close * 0.1, 1)
    rsi = round(float(last["RSI"]), 1)
    ma50 = float(last["MA50"])
    ma200 = float(last["MA200"])
    vs_ma50 = round((close / ma50 - 1) * 100, 1)
    vs_ma200 = round((close / ma200 - 1) * 100, 1)
    hi52 = float(df["high"].tail(252).max())
    dd52 = round((close / hi52 - 1) * 100, 1)

    bb_lo, bb_up = float(last["BB_lower"]), float(last["BB_upper"])
    bb_pos = round((close - bb_lo) / (bb_up - bb_lo) * 100, 0) if bb_up > bb_lo else 50.0

    hv20 = round(float(df["close"].pct_change().rolling(20).std().iloc[-1] * np.sqrt(252) * 100), 1)

    opt_key = spec.opt_symbol or spec.symbol
    ov = opt_map.get(opt_key, {})
    iv_f = (ov.get("iv_front") or {}).get("iv")
    iv_b = (ov.get("iv_back") or {}).get("iv")
    ref_iv = iv_b or iv_f
    iv_hv = round(ref_iv - hv20, 1) if ref_iv is not None else None
    pw = (ov.get("put_wall") or {}).get("strike")
    cw = (ov.get("call_wall") or {}).get("strike")
    ed, e_dte, exd, div_dte, event_alert = _event_fields(ov)

    # ── 1) 四维适格判定 ──
    # 单手占资 >$70k 为过重，>$55k 为偏大；HV20<=12% 且 IV<25% 为低波薄利（如 BRK.B）
    eff_vol = ref_iv if ref_iv is not None else hv20
    if lot_k > THRESHOLDS["lot_hard_max_k"]:
        suitability = f"单手过重(${lot_k:.0f}k)"
    elif lot_k > THRESHOLDS["lot_ideal_max_k"]:
        suitability = f"单手偏大(${lot_k:.0f}k)"
    elif hv20 <= 12.0 and (ref_iv or 0) < 25.0:
        suitability = "低波薄利"
    else:
        suitability = "黄金适格"

    # 2) 入场信号判定（红日卖 Put / 绿日卖 Call）
    reasons: list[str] = []
    signal = "⚪ 等待"

    is_oversold = rsi <= THRESHOLDS["put_rsi_trigger"] or bb_pos <= 15
    is_pullback = vs_ma50 <= THRESHOLDS["put_ma50_dist_max"] or vs_ma200 <= 3.0
    vol_ok = eff_vol >= THRESHOLDS["put_iv_min"]

    is_overbought = rsi >= THRESHOLDS["call_rsi_trigger"] or bb_pos >= 85
    is_extended = vs_ma50 >= THRESHOLDS["call_ma50_dist_min"]

    if is_oversold and is_pullback and vol_ok:
        if e_dte is not None and e_dte <= THRESHOLDS["earnings_danger_dte"]:
            signal = "⛔ 财报等落地"
            reasons.append(f"{e_dte}d后财报·防跳空")
        else:
            signal = "🟢 SELL PUT"
        reasons.append(f"RSI {rsi}")
        if vs_ma200 < 0:
            reasons.append(f"破MA200({vs_ma200:+.1f}%)")
        else:
            reasons.append(f"回踩MA50({vs_ma50:+.1f}%)")
        if ref_iv:
            reasons.append(f"IV {ref_iv:.0f}%")
        if (
            e_dte is not None
            and THRESHOLDS["earnings_danger_dte"] < e_dte <= THRESHOLDS["earnings_warn_dte"]
        ):
            reasons.append(f"跨财报({ed[5:]})")
    elif is_pullback and rsi <= 46.0 and vol_ok:
        if e_dte is not None and e_dte <= THRESHOLDS["earnings_danger_dte"]:
            signal = "⛔ 财报等落地"
            reasons.append(f"{e_dte}d后财报·防跳空")
        else:
            signal = "🟡 观察PUT"
        reasons.append(f"近支撑(MA50 {vs_ma50:+.1f}% / RSI {rsi})")
    elif is_overbought and is_extended:
        if e_dte is not None and e_dte <= THRESHOLDS["earnings_danger_dte"]:
            signal = "⛔ 财报勿卖Call"
            reasons.append(f"{e_dte}d后财报·防跳空卖飞")
        else:
            signal = "🔴 SELL CALL"
        reasons.append(f"RSI {rsi}过热")
        reasons.append(f"高于MA50 +{vs_ma50:.1f}%")
        if div_dte is not None and div_dte <= THRESHOLDS["ex_div_warn_dte"]:
            reasons.append(f"除息{div_dte}d防提前行权")
    else:
        reasons.append(f"通道中部(RSI {rsi} / MA50 {vs_ma50:+.1f}%)")

    put_pick = _pick_contract(opt_key, snap_date, ov, close, "P", THRESHOLDS["put_delta_target"])
    call_pick = _pick_contract(opt_key, snap_date, ov, close, "C", THRESHOLDS["call_delta_target"])

    return ScanRow(
        symbol=spec.symbol,
        tier=spec.tier,
        sector=spec.sector,
        close=close,
        lot_k=lot_k,
        rsi=rsi,
        vs_ma50_pct=vs_ma50,
        vs_ma200_pct=vs_ma200,
        dd_52w_pct=dd52,
        bb_pos_pct=bb_pos,
        hv20_pct=hv20,
        iv_front_pct=iv_f,
        iv_back_pct=iv_b,
        iv_hv_spread=iv_hv,
        put_wall=pw,
        call_wall=cw,
        earnings_date=ed,
        earnings_dte=e_dte,
        ex_div_date=exd,
        ex_div_dte=div_dte,
        event_alert=event_alert,
        suitability=suitability,
        signal=signal,
        reason=" · ".join(reasons),
        put_pick=put_pick,
        call_pick=call_pick,
    )


def scan_pool(
    tiers: set[str] | None = None, symbols: set[str] | None = None
) -> tuple[str | None, list[ScanRow]]:
    snap_date, opt_map = _latest_options_snapshot()
    rows: list[ScanRow] = []
    for spec in WHEEL_POOL:
        if tiers and spec.tier not in tiers:
            continue
        if symbols and spec.symbol not in symbols:
            continue
        r = evaluate_symbol(spec, snap_date, opt_map)
        if r is not None:
            rows.append(r)
    return snap_date, rows


def _fmt_pick(p: OptionPick | None) -> str:
    if not p:
        return "—"
    exp_lbl = p.expiration if p.expiration.startswith("~") else f"{p.expiration[5:]}({p.dte}d)"
    return (
        f"{exp_lbl} ${p.strike:g}{p.right} Δ{p.delta:+.2f} IV:{p.iv_pct:.0f}% ({p.dist_pct:+.1f}%)"
    )


def print_report(snap_date: str | None, rows: list[ScanRow]) -> None:
    print(f"\n=== 确定性巨头池 · Wheel 策略雷达（期权快照: {snap_date or '无'}）===\n")
    hdr = (
        f"{'标的':<6}{'梯队':<4}{'现价':>7}{'单手$k':>7}{'RSI':>6}"
        f"{'距MA50':>8}{'距MA200':>9}{'HV20/IV':>10}{'Put/Call墙':>11}"
        f"  {'事件排雷':<17}{'适格度':<12}{'当日信号':<13}{'触发依据'}"
    )
    print(hdr)
    print("-" * 138)
    for r in rows:
        iv_ref = r.iv_back_pct or r.iv_front_pct
        vol_s = f"{r.hv20_pct:.0f}%/{iv_ref:.0f}%" if iv_ref else f"{r.hv20_pct:.0f}%/—"
        wall_s = (
            f"{int(r.put_wall) if r.put_wall else '—'}/{int(r.call_wall) if r.call_wall else '—'}"
        )
        print(
            f"{r.symbol:<6}{r.tier:<4}{r.close:>7.1f}{r.lot_k:>6.1f}k{r.rsi:>6.1f}"
            f"{r.vs_ma50_pct:>+7.1f}%{r.vs_ma200_pct:>+8.1f}%{vol_s:>10}{wall_s:>11}"
            f"  {r.event_alert:<17}{r.suitability:<12}{r.signal:<13}{r.reason}"
        )

    # 打印已触发信号的期权合约建议
    active = [r for r in rows if r.signal.startswith(("🟢", "🟡", "🔴", "⛔"))]
    if active:
        print("\n=== 🎯 30~45 DTE 候选合约（基于 BS 模型 Delta 与当日 OI 链）===")
        for r in active:
            ev_tag = f" | {r.event_alert}" if r.event_alert != "—" else ""
            if ("PUT" in r.signal or "财报等落地" in r.signal) and r.put_pick:
                pick_s = _fmt_pick(r.put_pick)
                print(
                    f"  {r.signal:<13} {r.symbol:<5} 推荐卖 Put : {pick_s}"
                    f" | 下方 Put 墙: ${r.put_wall or '—'}{ev_tag}"
                )
            elif ("CALL" in r.signal or "勿卖Call" in r.signal) and r.call_pick:
                pick_s = _fmt_pick(r.call_pick)
                print(
                    f"  {r.signal:<13} {r.symbol:<5} 推荐卖 Call: {pick_s}"
                    f" | 上方 Call 墙: ${r.call_wall or '—'}{ev_tag}"
                )


def main() -> None:
    ap = argparse.ArgumentParser(description="确定性巨头池 Wheel 策略扫描器")
    ap.add_argument("--tier", default="", help="筛选梯队，如 T0,T1")
    ap.add_argument("--symbols", default="", help="指定逗号分隔标的，如 V,MA,GOOG")
    ap.add_argument("--json", action="store_true", help="输出 JSON 格式")
    args = ap.parse_args()

    tiers = {t.strip().upper() for t in args.tier.split(",") if t.strip()} or None
    syms = {s.strip().upper() for s in args.symbols.split(",") if s.strip()} or None
    snap_date, rows = scan_pool(tiers=tiers, symbols=syms)
    if args.json:
        print(
            json.dumps(
                {"snapshot_date": snap_date, "rows": [asdict(r) for r in rows]},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print_report(snap_date, rows)


if __name__ == "__main__":
    main()
