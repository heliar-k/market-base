"""确定性巨头池 Wheel 策略扫描器（Sell Put 抄底收租 + Covered Call 波段止盈）。

经对抗性量化风控审查（Adversarial Review）升级后的规则引擎：
1. 波动率真实定价：用快照 `iv_curve` 线性插值取 **35 DTE ATM IV**（拒绝 328d LEAPS IV 冒充），
   结合 Barchart 官方 **52 周 IV Rank / IV Percentile**（本地快照降级）与 **VRP = IV35 − HV20**。
2. 趋势与企稳过滤：用个股日波动率 σ 归一化均线回撤，识别确认空头趋势（SuperTrend 空 + ADX≥25），
   未出现右侧企稳信号（MACD 柱拐头 / 收复昨高 / 看涨吞没或锤子线）时降级为「左侧接刀·等企稳」。
3. 财报与除息排雷：财报落在目标期权周期内（≤35d）时，优先搜寻财报前到期（7~e_dte 天）合约；
   若财报 ≤10d 或无财报前合约，拦截裸卖 Put；否决状态绝不输出可交易合约。
4. 适合性硬门槛与组合风控：T2 观察池（单手过重/低波薄利）与缺期权链标的不发入场信号；
   报告头部输出同板块信号集中度与站内共识 Regime（`src/consensus.py` + VIX 期限结构）。

用法:
    uv run python -m src.wheel_scanner                  # 扫描全池（默认 T0+T1+T2）
    uv run python -m src.wheel_scanner --tier T0,T1     # 仅扫描 T0+T1 主力池
    uv run python -m src.wheel_scanner --symbols V,MA,GOOG,NVDA
    uv run python -m src.wheel_scanner --holdings UNH,V # 声明持有底仓（仅对持仓股激活 SELL CALL）
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
    tier: str  # T0=核心主力, T1=行业寡头对冲, T2=观察/受限(单手过重或低波)
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
    PoolSpec("AVGO", "T0", "AI算力", "定制ASIC+交换芯片+VMware现金牛，高弹性高权利金"),
    PoolSpec("TSM", "T0", "AI算力", "全球先进制程唯一底座，地缘噪音常给高IV买点"),
    PoolSpec("UNH", "T0", "医疗保险", "全美最大商业医保+Optum闭环，与科技股低相关"),
    PoolSpec("MSFT", "T0", "企业软件/云", "企业OS+Azure双垄断，确定性天花板"),
    # ── T1：跨行业寡头与轮动对冲池（防科技板块单边杀估值）──
    PoolSpec("META", "T1", "社交广告", "30亿月活广告印钞机，IV肥，单手~$65k偏大"),
    PoolSpec("V", "T1", "支付清算", "全球法币消费收税官，净利率50%+，单手~$41k优于MA"),
    PoolSpec("JPM", "T1", "金融底座", "美国大而不倒第一行，单手~$34k，金融周期压舱石"),
    PoolSpec("AAPL", "T1", "硬件生态", "美股第一回购机器，平时HV偏低，等果链利空IV脉冲出手"),
    PoolSpec("ISRG", "T1", "医疗器械", "达芬奇手术机器人垄断，装机+耗材剃须刀模式"),
    PoolSpec("ABBV", "T1", "创新药", "高股息医药现金牛，单手~$28k，接货自带3%+分红垫"),
    # ── T2：观察/受限池（好公司但做标准 Wheel 有硬伤：单手过重或权利金过薄）──
    PoolSpec("MA", "T2", "支付清算", "与V同级垄断，但单手~$63k占资比V高50%，优选V"),
    PoolSpec("COST", "T2", "会员零售", "护城河极深，但单手~$102k过重+HV仅17%，宜正股定投"),
    PoolSpec("LLY", "T2", "代谢/创新药", "GLP-1龙头，但单手~$131k锁资过重，宜正股分批"),
    PoolSpec(
        "BRK.B", "T2", "保险/现金池", "HV仅10%权利金太薄，适合充当保证金底仓而非卖期权", "BRK-B"
    ),
]

# ═══════════════════════════════════════════════════════════════════════════════
# 2. 筛选与信号阈值（经量化风控校准）
# ═══════════════════════════════════════════════════════════════════════════════

THRESHOLDS = {
    # 四维适合性门槛
    "lot_ideal_max_k": 55.0,  # 单手理想占资上限（$55k，即股价 $550）
    "lot_hard_max_k": 70.0,  # 单手硬上限（超此值标记「单手过重」，不发期权信号）
    "hv20_min": 14.0,  # 20日历史波动率下限（低于此值且IV<22%判为低波薄利）
    "hv20_max": 55.0,  # 20日历史波动率上限（高于此值属高Beta剧烈波动）
    # 波动率定价门槛
    "put_iv_min": 24.0,  # Sell Put 35d ATM IV 绝对下限（%）
    "call_iv_min": 20.0,  # Covered Call 35d ATM IV 绝对下限（%）
    "vrp_min": 2.0,  # Sell Put 波动率风险溢价下限：VRP = IV35 − HV20 >= 2.0%（拒绝负期望贱卖）
    "call_vrp_min": 0.0,  # Covered Call VRP 下限：IV35 >= HV20（持仓增厚不贱卖负期望）
    "ivr_min": 25.0,  # Sell Put 52周 IV Rank 或 IV Percentile 至少一项 >= 25%
    # Sell Put 技术击球区
    "put_rsi_trigger": 40.0,  # RSI(14) <= 40 超卖/偏弱
    "put_ma50_sigma_max": -1.2,  # 距 MA50 偏离 <= -1.2 倍日标准差（波动率归一化）
    # Sell Call（Covered Call）技术击球区
    "call_rsi_trigger": 68.0,  # RSI(14) >= 68 动量过热
    "call_ma50_sigma_min": 2.0,  # 高于 MA50 >= +2.0 倍日标准差
    # 期权选点参数（DTE 与 |Delta|）
    "dte_min": 21,
    "dte_max": 49,
    "put_delta_target": -0.22,  # Sell Put 目标 Delta（-0.18 ~ -0.28）
    "call_delta_target": 0.18,  # Sell Call 目标 Delta（0.14 ~ 0.25）
    "max_spread_pct": 35.0,  # 盘口最大允许买卖点差 (ask-bid)/mid <= 35%，拦截僵尸滑点盘口
    "call_roc_min": 6.0,  # Covered Call 最低年化收益率门槛（%）
    "put_roc_min": 10.0,  # Sell Put 最低年化收益率门槛（%）
    # 事件排雷阈值
    "earnings_danger_dte": 10,  # 财报 <= 10 天：禁止裸卖近月期权
    "earnings_window_dte": 35,  # 财报 <= 35 天：落入月度期权窗口，优先切财报前到期合约
    "ex_div_warn_dte": 14,  # 除息 <= 14 天：Sell Call 警惕提前被行权
    # 组合风控阈值
    "max_green_per_sector": 2,  # 同板块最多同时激活 N 个绿灯
}


@dataclass
class OptionPick:
    """推荐的单个期权合约（区分真实期权链合约 vs BSM 理论估算）。"""

    right: str  # 'P' or 'C'
    expiration: str  # YYYY-MM-DD 或 '~35d(BSM理论)'
    dte: int
    strike: float
    delta: float
    iv_pct: float
    oi: int
    dist_pct: float  # (strike - spot) / spot * 100
    executable: bool = True  # True=来自当日真实期权链, False=BSM+Skew理论估算
    annual_roc_pct: float | None = None  # 若有 bid/ask 则算年化收益率


@dataclass
class ScanRow:
    symbol: str
    opt_symbol: str  # options_structure 快照中的 ticker（GOOG->GOOGL, BRK.B->BRK-B）
    tier: str
    sector: str
    close: float
    lot_k: float  # 100 股占压资金 ($k)
    rsi: float
    vs_ma50_pct: float
    vs_ma200_pct: float
    ma50_sigmas: float  # 距 MA50 的日标准差倍数
    bb_pos_pct: float  # 布林带位置 (0%=下轨, 100%=上轨)
    hv20_pct: float
    iv35_pct: float | None  # 经 iv_curve 插值后的真实 35d ATM IV
    vrp_pct: float | None  # 真实 VRP = IV35 - HV20
    iv_rank_1y: float | None  # 52周 IV Rank (Barchart 官方或本地快照降级)
    iv_pct_1y: float | None  # 52周 IV Percentile
    put_wall: float | None  # 仅保留 < close 的有效下方支撑墙
    call_wall: float | None  # 仅保留 > close 的有效上方阻力墙
    earnings_date: str | None
    earnings_dte: int | None
    ex_div_date: str | None
    ex_div_dte: int | None
    event_alert: str
    trend_state: str  # 多头趋势 / 震荡修复 / 回调企稳 / 弱势回调 / 空头破位
    suitability: str  # 适合 / 单手偏大 / 单手过重 / 权利金过薄 / 波动过大
    signal: str  # 仅三态：SELL PUT / SELL CALL / 暂不操作
    signal_tone: str  # bull(SELL PUT) / bear(SELL CALL) / flat(暂不操作)，与信号一一对应
    reason: str
    actionable: bool = False  # True=允许直接出手（SELL PUT / SELL CALL）
    near_setup: bool = False  # True=已进入/接近卖Put击球区但暂缓出手（空头待企稳 / 接近支撑区）
    event_tone: str = "flat"  # bear(财报<=10d) / warn(财报<=35d或除息<=14d) / flat
    rsi_tone: str = "flat"  # bull(<=40超卖) / bear(>=68超买) / flat
    vrp_tone: str = "flat"  # bull(>=2%正期望) / bear(<0负期望) / flat
    put_pick: OptionPick | None = None
    call_pick: OptionPick | None = None


def _latest_options_snapshot() -> tuple[str | None, dict]:
    opt_dir = ROOT / "data" / "options_structure"
    files = sorted(opt_dir.glob("20*.json"))
    if not files:
        return None, {}
    data = json.loads(files[-1].read_text(encoding="utf-8"))
    return files[-1].stem, data.get("symbols", {})


def _iv_at_dte(ov: dict, target_dte: int = 35) -> float | None:
    """从快照 iv_curve 线性插值到目标 DTE（避免拿 328d LEAPS IV 或 1DTE IV 冒充 35d 月选 IV）。"""
    pts = sorted(
        (int(c["dte"]), float(c["iv"]))
        for c in (ov.get("iv_curve") or [])
        if c.get("dte") is not None and c.get("iv")
    )
    if len(pts) >= 2:
        xs, ys = zip(*pts)
        return round(float(np.interp(target_dte, xs, ys)), 1)
    if len(pts) == 1 and abs(pts[0][0] - target_dte) <= 20:
        return round(pts[0][1], 1)
    if ov.get("wiv"):
        return round(float(ov["wiv"]), 1)
    front = ov.get("iv_front") or {}
    if (
        front.get("iv")
        and front.get("dte") is not None
        and abs(int(front["dte"]) - target_dte) <= 20
    ):
        return round(float(front["iv"]), 1)
    return None


def _local_iv_rank_fallback(
    opt_key: str, cur_iv: float | None
) -> tuple[float | None, float | None]:
    """当快照无 Barchart 1Y IVR 时，从本地历史快照计算滚动 IV Rank 与 Percentile。"""
    if cur_iv is None:
        return None, None
    files = sorted((ROOT / "data" / "options_structure").glob("20*.json"))[-60:]
    hist: list[float] = []
    for f in files:
        try:
            v = (json.loads(f.read_text(encoding="utf-8")).get("symbols") or {}).get(opt_key) or {}
            iv = _iv_at_dte(v, 35)
            if iv:
                hist.append(float(iv))
        except Exception:
            continue
    if len(hist) < 15:
        return None, None
    lo, hi = min(hist), max(hist)
    rank = round((cur_iv - lo) / (hi - lo) * 100, 1) if hi > lo else 50.0
    pct = round(sum(h <= cur_iv for h in hist) / len(hist) * 100, 1)
    return rank, pct


def _ref_date(snap_date: str | None) -> datetime.date:
    """统一以期权快照日为 DTE 与事件倒计时锚点，避免周末/盘前重跑时 DTE 漂移。"""
    if snap_date:
        try:
            s = str(snap_date).replace("-", "")[:8]
            return datetime.strptime(s, "%Y%m%d").date()
        except Exception:
            pass
    return datetime.now().date()


def _is_monthly_opex(dt: datetime.date) -> bool:
    """判断是否为美股标准月度期权到期日（每月第 3 个周五，或遇节假日顺延至 15~21 日的周四）。"""
    return 15 <= dt.day <= 21 and dt.weekday() in (3, 4)


def _pick_contract(
    opt_sym: str,
    snap_date: str | None,
    ov: dict,
    spot: float,
    iv35: float | None,
    right: str,
    target_delta: float,
    earnings_dte: int | None = None,
    wall_strike: float | None = None,
) -> OptionPick | None:
    """选点器：锚定快照日 DTE；优选 OpEx/高 OI 到期日；结合期权墙外侧与点差过滤。"""
    ref_dt = _ref_date(snap_date)
    ref_str = ref_dt.strftime("%Y%m%d")
    raw_path = ROOT / "data" / "options_structure" / f"raw_{opt_sym}_{ref_str}.csv"
    if not raw_path.exists():
        raw_path = None

    dte_min, dte_max = THRESHOLDS["dte_min"], THRESHOLDS["dte_max"]
    pre_earnings = False
    # 若财报落在 11~35 天内，卖 Put 收敛到「财报前到期 (7 <= dte < earnings_dte)」避开跳空
    if right == "P" and earnings_dte is not None and 10 < earnings_dte <= dte_max:
        dte_min, dte_max = 7, earnings_dte - 1
        pre_earnings = True

    if raw_path is not None:
        try:
            df = pd.read_csv(raw_path, dtype={"expiration": str})
        except Exception:
            df = pd.DataFrame()
        if not df.empty:
            df = df[
                (df["right"] == right)
                & (df["openInterest"] >= 100)
                & (df["impliedVolatility"] > 0.01)
            ]
            df = df[df["strike"] < spot * 0.99] if right == "P" else df[df["strike"] > spot * 1.01]
        if not df.empty:
            df["exp_dt"] = pd.to_datetime(df["expiration"], format="%Y%m%d").dt.date
            df["dte"] = df["exp_dt"].apply(lambda d: (d - ref_dt).days)
            win = df[(df["dte"] >= dte_min) & (df["dte"] <= dte_max)].copy()
            if not win.empty:
                # 1) 到期日排序：财报前短 Put 选最远到期日；常规选标准月度 OpEx 与高总 OI 到期日
                exp_groups = []
                for exp_s, grp in win.groupby("expiration"):
                    exp_dt = grp["exp_dt"].iloc[0]
                    dte_v = int(grp["dte"].iloc[0])
                    tot_oi = int(grp["openInterest"].sum())
                    opex = _is_monthly_opex(exp_dt)
                    if pre_earnings:
                        sort_key = (dte_v, tot_oi)
                    else:
                        sort_key = (1 if opex else 0, tot_oi, -abs(dte_v - 32))
                    exp_groups.append((sort_key, exp_s, grp))
                exp_groups.sort(key=lambda x: x[0], reverse=True)

                # 逐个候选到期日寻找合规行权价（若首选到期日无合规合约，自动回退次优到期日）
                for _, exp_s, sub in exp_groups:
                    sub = sub.copy()
                    deltas = []
                    for _, r in sub.iterrows():
                        t = max(int(r["dte"]), 1) / 365.0
                        _, dc = bs_greeks(
                            spot, float(r["strike"]), t, float(r["impliedVolatility"])
                        )
                        deltas.append(dc if right == "C" else dc - 1.0)
                    sub["delta"] = deltas
                    sub["delta_diff"] = (sub["delta"] - target_delta).abs()
                    sub = sub[sub["delta_diff"] <= 0.08].copy()
                    if sub.empty:
                        continue

                    # 盘口质量过滤：剔除 bid<=0.05 或点差 > max_spread_pct 的滑点僵尸盘口
                    if "bid" in sub.columns and "ask" in sub.columns:
                        bid_s = sub["bid"].fillna(0.0).astype(float)
                        ask_s = sub["ask"].fillna(0.0).astype(float)
                        mid_s = (bid_s + ask_s) / 2.0
                        spr_pct = (ask_s - bid_s) / mid_s.replace(0, np.nan) * 100.0
                        liquid = sub[
                            (bid_s > 0.05) & (spr_pct <= THRESHOLDS["max_spread_pct"])
                        ].copy()
                        if liquid.empty:
                            continue
                        sub = liquid

                    # 2) 行权价综合打分：Delta 贴合度 + 期权墙外侧保护 + 单档 OI 深度
                    def _strike_score(row: pd.Series) -> float:
                        k = float(row["strike"])
                        d_err = float(row["delta_diff"])
                        oi_v = max(int(row["openInterest"]), 1)
                        # 期权墙协同：Put <= Put Wall（支撑墙外），Call >= Call Wall（阻力墙外）
                        wall_bonus = 0.0
                        if wall_strike is not None:
                            if right == "P":
                                wall_bonus = 0.025 if k <= wall_strike else -0.025
                            else:
                                wall_bonus = 0.025 if k >= wall_strike else -0.025
                        # log10(OI) 每差 10 倍约抵消 0.012 Delta 偏差，避免选出冷门非整数档
                        oi_bonus = min(np.log10(oi_v), 5.0) * 0.012
                        return -d_err + wall_bonus + oi_bonus

                    sub["score"] = sub.apply(_strike_score, axis=1)
                    best = sub.sort_values("score", ascending=False).iloc[0]
                    dte_best = max(int(best["dte"]), 1)
                    roc = None
                    if "bid" in best and "ask" in best and float(best.get("bid") or 0) > 0:
                        bid_v = float(best["bid"])
                        ask_v = float(best["ask"])
                        # 保守计入半点差滑点（向 bid 倾斜 70%），防止宽点差盘口虚增 mid 年化收益
                        eff_prem = 0.7 * bid_v + 0.3 * ask_v if ask_v >= bid_v else bid_v
                        # Sell Put 以行权价（现金担保额）为分母；Covered Call 以正股现价为分母
                        denom = float(best["strike"]) if right == "P" else spot
                        roc = round(eff_prem / denom * (365.0 / dte_best) * 100.0, 1)
                    return OptionPick(
                        right=right,
                        expiration=f"{exp_s[:4]}-{exp_s[4:6]}-{exp_s[6:8]}",
                        dte=dte_best,
                        strike=float(best["strike"]),
                        delta=round(float(best["delta"]), 2),
                        iv_pct=round(float(best["impliedVolatility"]) * 100, 1),
                        oi=int(best["openInterest"]),
                        dist_pct=round((float(best["strike"]) - spot) / spot * 100, 1),
                        executable=True,
                        annual_roc_pct=roc,
                    )
        # 若存在当日真实期权链但窗口内全部因点差过大/流动性枯竭被拒，绝不回退 BSM 伪造合约
        return None

    # 2) Fallback：仅当本地无 raw 链文件时，优先用快照同期限 ATM IV 与 25Δ Skew 比例估算理论行权价
    if not iv35:
        return None
    real_skew_iv = ov.get("skew_put_iv") if right == "P" else ov.get("skew_call_iv")
    skew_dte = int(ov.get("skew_dte") or 30)
    if real_skew_iv and float(real_skew_iv) > 1.0:
        # 分母必须取与 skew_dte 同期限的 ATM IV，避免拿 1DTE iv_front 作分母导致比值失真
        atm_at_skew = _iv_at_dte(ov, max(skew_dte, 5)) or float(iv35)
        ratio = np.clip(float(real_skew_iv) / max(float(atm_at_skew), 5.0), 0.85, 1.25)
        sigma = (float(iv35) * float(ratio)) / 100.0
    else:
        skew_mult = 1.08 if right == "P" else 0.98
        sigma = (float(iv35) * skew_mult) / 100.0
    dte = min(35, dte_max) if dte_max >= 7 else 35
    t = dte / 365.0
    step = 5.0 if spot >= 150 else 2.5
    strikes = (
        np.arange(round(spot * 0.72 / step) * step, spot * 0.99, step)
        if right == "P"
        else np.arange(round(spot * 1.01 / step) * step, spot * 1.28, step)
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
    if best_k is None or min_err > 0.10:
        return None
    return OptionPick(
        right=right,
        expiration=f"~{dte}d(BSM估算)",
        dte=dte,
        strike=best_k,
        delta=round(best_d, 2),
        iv_pct=round(sigma * 100, 1),
        oi=0,
        dist_pct=round((best_k - spot) / spot * 100, 1),
        executable=False,
    )


def _days_until(date_str: str | None, snap_date: str | None = None) -> int | None:
    if not date_str:
        return None
    try:
        dt = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
        return (dt - _ref_date(snap_date)).days
    except Exception:
        return None


def _event_fields(
    ov: dict, snap_date: str | None = None
) -> tuple[str | None, int | None, str | None, int | None, str, str]:
    """解析财报日与除息日倒计时（锚定快照日），生成紧凑的排雷标签与语义色调。"""
    ed = ov.get("earnings_date")
    e_dte = _days_until(ed, snap_date)
    recent_ed = ed if (e_dte is not None and -14 <= e_dte < 0) else None
    if e_dte is not None and e_dte < 0:
        ed, e_dte = None, None

    exd = ov.get("ex_dividend_date")
    div_dte = _days_until(exd, snap_date)
    if div_dte is not None and div_dte < 0:
        exd, div_dte = None, None

    tags: list[str] = []
    event_tone = "flat"
    if e_dte is not None:
        if e_dte <= THRESHOLDS["earnings_danger_dte"]:
            tags.append(f"财报{e_dte}d({ed[5:]})")
            event_tone = "bear"
        elif e_dte <= THRESHOLDS["earnings_window_dte"]:
            tags.append(f"财报{e_dte}d({ed[5:]})")
            event_tone = "warn"
        else:
            tags.append(f"财报{e_dte}d")
    elif recent_ed:
        tags.append(f"财报已发({recent_ed[5:]})")
    else:
        tags.append("财报待定")
    if div_dte is not None and div_dte <= THRESHOLDS["ex_div_warn_dte"]:
        tags.append(f"除息{div_dte}d")
        if event_tone == "flat":
            event_tone = "warn"
    return ed, e_dte, exd, div_dte, " · ".join(tags), event_tone


def normalize_directional_walls(s: dict) -> None:
    """原地校准期权结构字典中的 put_wall (< spot) 与 call_wall (> spot)（全站唯一实现）。

    若全链最大墙已被现价涨穿/跌穿，从 s["wall"] 分布中回退寻找现价对应侧最强 GEX 墙；
    若该侧无有效墙位则置为 None，保证期权结构页与 Wheel 雷达 100% 同源。
    """
    spot = s.get("spot")
    if not spot:
        return
    spot_f = float(spot)
    wall_dist = s.get("wall") or []

    pw_strike = (s.get("put_wall") or {}).get("strike")
    if pw_strike is None or float(pw_strike) >= spot_f:
        puts = [
            x
            for x in wall_dist
            if (x.get("strike") or 0) > 0
            and float(x["strike"]) < spot_f
            and (x.get("put_gex_m") or 0) < 0
        ]
        if puts:
            best_p = min(puts, key=lambda x: float(x.get("put_gex_m") or 0))
            s["put_wall"] = {
                "strike": float(best_p["strike"]),
                "per_point_m": round(float(best_p["put_gex_m"]), 1),
            }
        else:
            s["put_wall"] = None

    cw_strike = (s.get("call_wall") or {}).get("strike")
    if cw_strike is None or float(cw_strike) <= spot_f:
        calls = [
            x
            for x in wall_dist
            if (x.get("strike") or 0) > spot_f and (x.get("call_gex_m") or 0) > 0
        ]
        if calls:
            best_c = max(calls, key=lambda x: float(x.get("call_gex_m") or 0))
            s["call_wall"] = {
                "strike": float(best_c["strike"]),
                "per_point_m": round(float(best_c["call_gex_m"]), 1),
            }
        else:
            s["call_wall"] = None


def _directional_walls(ov: dict, close: float) -> tuple[float | None, float | None]:
    """返回现价下方最强 Put 墙与上方最强 Call 墙（委托 normalize_directional_walls 单源）。"""
    tmp = {
        "spot": close,
        "put_wall": ov.get("put_wall"),
        "call_wall": ov.get("call_wall"),
        "wall": ov.get("wall"),
    }
    normalize_directional_walls(tmp)
    pw = (tmp.get("put_wall") or {}).get("strike")
    cw = (tmp.get("call_wall") or {}).get("strike")
    return (float(pw) if pw is not None else None, float(cw) if cw is not None else None)


def evaluate_symbol(
    spec: PoolSpec,
    snap_date: str | None,
    opt_map: dict,
    holdings: set[str] | None = None,
) -> ScanRow | None:
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

    bb_lo, bb_up = float(last["BB_lower"]), float(last["BB_upper"])
    bb_pos = round((close - bb_lo) / (bb_up - bb_lo) * 100, 0) if bb_up > bb_lo else 50.0

    hv20 = round(float(df["close"].pct_change().rolling(20).std().iloc[-1] * np.sqrt(252) * 100), 1)
    day_sigma = max(hv20 / np.sqrt(252), 0.5)
    ma50_sigmas = round(vs_ma50 / day_sigma, 2)

    # ── 期权面数据（35d 插值 IV + 1Y IV Rank + 有效墙位校验）──
    opt_key = spec.opt_symbol or spec.symbol
    ov = opt_map.get(opt_key) or {}
    # 期权选点与墙位基准优先对齐期权链快照自身的 spot，避免股票日线与期权快照跨日错位
    opt_spot = float(ov.get("spot") or close)
    iv35 = _iv_at_dte(ov, 35)
    vrp = round(iv35 - hv20, 1) if iv35 is not None else None

    ivr = ov.get("iv_rank_1y")
    ivp = ov.get("iv_pct_1y")
    if ivr is None and ivp is None:
        ivr, ivp = _local_iv_rank_fallback(opt_key, iv35)

    pw, cw = _directional_walls(ov, opt_spot)
    ed, e_dte, exd, div_dte, event_alert, event_tone = _event_fields(ov, snap_date)
    rsi_tone = (
        "bull"
        if rsi <= THRESHOLDS["put_rsi_trigger"]
        else ("bear" if rsi >= THRESHOLDS["call_rsi_trigger"] else "flat")
    )
    vrp_tone = (
        "flat"
        if vrp is None
        else ("bull" if vrp >= THRESHOLDS["vrp_min"] else ("bear" if vrp < 0 else "flat"))
    )

    # ── 趋势与右侧企稳判定（防半山腰接飞刀，NaN 安全转型）──
    raw_st = last.get("SUPERT_dir", 1)
    supert_dir = int(raw_st) if pd.notna(raw_st) else 1
    raw_adx = last.get("ADX", 20.0)
    adx = float(raw_adx) if pd.notna(raw_adx) else 20.0
    raw_vr = last.get("vol_ratio", 1.0)
    vol_ratio = float(raw_vr) if pd.notna(raw_vr) else 1.0
    raw_mh0 = last.get("MACD_hist", 0.0)
    macd_h0 = float(raw_mh0) if pd.notna(raw_mh0) else 0.0
    raw_mh1 = df["MACD_hist"].iloc[-2] if len(df) >= 2 else macd_h0
    macd_h1 = float(raw_mh1) if pd.notna(raw_mh1) else macd_h0

    # 强空头趋势：SuperTrend 空头 且 (ADX>=25 或 MA50<MA200 死叉 或 放量破布林下轨)
    severe_downtrend = supert_dir < 0 and (
        adx >= 25.0 or ma50 < ma200 or (bb_pos < 0 and vol_ratio >= 1.5)
    )
    # 右侧企稳信号：MACD 柱拐头向上 / 收盘站上前一日高点 / 锤子线或看涨吞没
    stabilized = (
        macd_h0 > macd_h1
        or close > float(df["high"].iloc[-2])
        or bool(last.get("CDL_HAMMER", 0))
        or bool(last.get("CDL_ENGULFING", 0) > 0)
    )
    if severe_downtrend and not stabilized:
        trend_state = "空头破位"
    elif supert_dir < 0:
        trend_state = "回调企稳" if stabilized else "弱势回调"
    elif ma50 >= ma200:
        trend_state = "多头趋势"
    else:
        trend_state = "震荡修复"

    # ── 1) 四维适合性硬门槛 ──
    if lot_k > THRESHOLDS["lot_hard_max_k"]:
        suitability = f"单手过重(${lot_k:.0f}k)"
    elif hv20 < THRESHOLDS["hv20_min"] and (iv35 or 0) < 22.0:
        suitability = "权利金过薄"
    elif hv20 > THRESHOLDS["hv20_max"]:
        suitability = "波动过大"
    elif lot_k > THRESHOLDS["lot_ideal_max_k"]:
        suitability = f"单手偏大(${lot_k:.0f}k)"
    else:
        suitability = "适合"

    # ── 2) 信号生成（Wheel 严格三态：SELL PUT=bull / SELL CALL=bear / 暂不操作=flat）──
    reasons: list[str] = []
    signal = "暂不操作"
    signal_tone = "flat"
    near_setup = False

    # 硬门槛 1：T2 受限池（单手过重/权利金过薄/波动过大）或无期权链数据，直接不发期权信号
    if suitability in ("权利金过薄", "波动过大") or suitability.startswith("单手过重"):
        reasons.append(f"{suitability}·宜正股或作底仓")
    elif iv35 is None:
        reasons.append("缺期权数据·需运行 fetch_options_structure")
    else:
        is_oversold = rsi <= THRESHOLDS["put_rsi_trigger"] or bb_pos <= 15
        is_pullback = ma50_sigmas <= THRESHOLDS["put_ma50_sigma_max"] or vs_ma200 <= 3.0
        vol_rich = (
            iv35 >= THRESHOLDS["put_iv_min"]
            and (vrp is not None and vrp >= THRESHOLDS["vrp_min"])
            and (max(ivr or 0.0, ivp or 0.0) >= THRESHOLDS["ivr_min"])
        )

        is_overbought = (
            rsi >= THRESHOLDS["call_rsi_trigger"] or bb_pos >= 85
        ) and ma50_sigmas >= THRESHOLDS["call_ma50_sigma_min"]

        if is_oversold and is_pullback:
            # 触发原因如实记录（区分 RSI 超卖 vs 布林下轨）
            reasons.append(
                f"RSI {rsi}"
                if rsi <= THRESHOLDS["put_rsi_trigger"]
                else f"触布林下轨(BB {bb_pos:.0f}%)"
            )
            reasons.append(f"MA50 {vs_ma50:+.1f}%({ma50_sigmas:+.1f}σ)")

            if e_dte is not None and e_dte <= THRESHOLDS["earnings_danger_dte"]:
                reasons.insert(0, f"财报禁卖({e_dte}d后财报)")
            elif not vol_rich:
                reasons.insert(0, "权利金偏薄")
                reasons.append(f"VRP {vrp:+.1f}% / IVR {ivr or 0:.0f}%不足")
            elif severe_downtrend and not stabilized:
                near_setup = True
                reasons.insert(0, "空头未企稳(等止跌)")
            elif e_dte is not None and e_dte <= THRESHOLDS["earnings_window_dte"]:
                signal, signal_tone = "SELL PUT", "bull"
                reasons.append(f"财报{e_dte}d·切财报前到期合约")
            else:
                signal, signal_tone = "SELL PUT", "bull"
                reasons.append(f"VRP {vrp:+.1f}% · IVR {ivr or 0:.0f}%")

        elif is_pullback and rsi <= 45.0 and vol_rich:
            if e_dte is not None and e_dte <= THRESHOLDS["earnings_danger_dte"]:
                reasons.append(f"财报禁卖({e_dte}d后财报) · 近支撑(RSI {rsi})")
            else:
                near_setup = True
                reasons.append(f"接近卖Put区 · 回踩支撑({ma50_sigmas:+.1f}σ / RSI {rsi})")

        elif is_overbought:
            ov_tag = (
                f"RSI {rsi}过热"
                if rsi >= THRESHOLDS["call_rsi_trigger"]
                else f"触布林上轨(BB {bb_pos:.0f}%)"
            )
            reasons.append(f"{ov_tag} · 高于MA50 +{vs_ma50:.1f}%({ma50_sigmas:+.1f}σ)")
            has_stock = holdings is None or (spec.symbol in holdings)
            if not has_stock:
                reasons.insert(0, "超买但无底仓")
            elif e_dte is not None and e_dte <= THRESHOLDS["earnings_danger_dte"]:
                reasons.insert(0, f"财报禁卖({e_dte}d后财报·防卖飞)")
            elif iv35 < THRESHOLDS["call_iv_min"] or (
                vrp is not None and vrp < THRESHOLDS["call_vrp_min"]
            ):
                reasons.insert(0, f"IV35 {iv35:.0f}%/VRP {vrp:+.1f}%偏薄")
            else:
                signal, signal_tone = "SELL CALL", "bear"
                reasons.insert(0, "仅限备兑(禁裸卖)")
                if div_dte is not None and div_dte <= THRESHOLDS["ex_div_warn_dte"]:
                    reasons.append(f"除息{div_dte}d防提前行权")
        else:
            reasons.append(f"通道中部(RSI {rsi} / {ma50_sigmas:+.1f}σ)")

    # 仅对 SELL PUT / SELL CALL 生成推荐合约（若盘口流动性枯竭或年化低于门槛，自动降级为暂不操作）
    put_pick = None
    call_pick = None
    if signal == "SELL PUT":
        put_pick = _pick_contract(
            opt_key, snap_date, ov, opt_spot, iv35, "P", THRESHOLDS["put_delta_target"], e_dte, pw
        )
        if put_pick is None:
            signal, signal_tone = "暂不操作", "flat"
            msg = (
                f"财报前({e_dte}d)无合规短Put"
                if (e_dte is not None and 10 < e_dte <= THRESHOLDS["dte_max"])
                else "期权盘口点差过宽或无合适档位"
            )
            reasons.insert(0, msg)
        elif (
            put_pick.annual_roc_pct is not None
            and put_pick.annual_roc_pct < THRESHOLDS["put_roc_min"]
        ):
            signal, signal_tone = "暂不操作", "flat"
            reasons.insert(0, f"候选年化仅{put_pick.annual_roc_pct:.0f}%偏薄")
            put_pick = None
    elif signal == "SELL CALL":
        call_pick = _pick_contract(
            opt_key, snap_date, ov, opt_spot, iv35, "C", THRESHOLDS["call_delta_target"], e_dte, cw
        )
        if call_pick is None:
            signal, signal_tone = "暂不操作", "flat"
            reasons.insert(0, "期权盘口点差过宽(防滑点)")
        elif (
            call_pick.annual_roc_pct is not None
            and call_pick.annual_roc_pct < THRESHOLDS["call_roc_min"]
        ):
            signal, signal_tone = "暂不操作", "flat"
            reasons.insert(0, f"候选年化仅{call_pick.annual_roc_pct:.0f}%偏薄")
            call_pick = None

    actionable = signal in ("SELL PUT", "SELL CALL")

    return ScanRow(
        symbol=spec.symbol,
        opt_symbol=opt_key,
        tier=spec.tier,
        sector=spec.sector,
        close=close,
        lot_k=lot_k,
        rsi=rsi,
        vs_ma50_pct=vs_ma50,
        vs_ma200_pct=vs_ma200,
        ma50_sigmas=ma50_sigmas,
        bb_pos_pct=bb_pos,
        hv20_pct=hv20,
        iv35_pct=iv35,
        vrp_pct=vrp,
        iv_rank_1y=ivr,
        iv_pct_1y=ivp,
        put_wall=pw,
        call_wall=cw,
        earnings_date=ed,
        earnings_dte=e_dte,
        ex_div_date=exd,
        ex_div_dte=div_dte,
        event_alert=event_alert,
        trend_state=trend_state,
        suitability=suitability,
        signal=signal,
        signal_tone=signal_tone,
        reason=" · ".join(reasons),
        actionable=actionable,
        near_setup=near_setup,
        event_tone=event_tone,
        rsi_tone=rsi_tone,
        vrp_tone=vrp_tone,
        put_pick=put_pick,
        call_pick=call_pick,
    )


def portfolio_summary(
    rows: list[ScanRow], capped_by_sector: dict[str, list[str]] | None = None
) -> dict:
    """组合层风控汇总：站内市场共识 Regime + 同板块信号集中度检查。"""
    mkt_state = "未知"
    vix_note = ""
    try:
        from src.consensus import compute as compute_consensus

        c = compute_consensus()
        mkt_state = (c.get("market") or {}).get("state", "未知")
        for m in (c.get("market") or {}).get("members", []):
            if m.get("name") == "波动率":
                vix_note = m.get("detail", "")
                break
    except Exception:
        pass

    put_candidates = [r for r in rows if r.signal == "SELL PUT"]
    by_sector: dict[str, list[str]] = {}
    for r in put_candidates:
        by_sector.setdefault(r.sector, []).append(r.symbol)

    warnings: list[str] = []
    for sec, dropped in (capped_by_sector or {}).items():
        kept = by_sector.get(sec, [])
        all_syms = kept + dropped
        warnings.append(
            f"板块集中度限额：{sec} 共 {len(all_syms)} 只触发({','.join(all_syms)})，"
            f"按 VRP 择优保留 {','.join(kept)}，其余({','.join(dropped)})已降为暂不操作"
        )
    for sec, syms in by_sector.items():
        if sec not in (capped_by_sector or {}) and len(syms) > THRESHOLDS["max_green_per_sector"]:
            warnings.append(
                f"板块集中度告警：{sec} 同时触发 {len(syms)} 只({','.join(syms)})，"
                f"同因子上限 {THRESHOLDS['max_green_per_sector']} 只"
            )
    if mkt_state == "确认 risk-off":
        warnings.append("大盘共识处于「确认 risk-off」，全池 Sell Put 仓位建议减半或下移行权价")

    return {
        "market_regime": mkt_state,
        "vix_note": vix_note,
        "active_put_count": len(put_candidates),
        "sector_groups": by_sector,
        "warnings": warnings,
    }


def scan_pool(
    tiers: set[str] | None = None,
    symbols: set[str] | None = None,
    holdings: set[str] | None = None,
) -> tuple[str | None, list[ScanRow], dict]:
    snap_date, opt_map = _latest_options_snapshot()
    rows: list[ScanRow] = []
    for spec in WHEEL_POOL:
        if tiers and spec.tier not in tiers:
            continue
        if symbols and spec.symbol not in symbols:
            continue
        r = evaluate_symbol(spec, snap_date, opt_map, holdings=holdings)
        if r is not None:
            rows.append(r)

    # 同板块超限自动择优：按 VRP 降序只保留前 max_green_per_sector 个为 SELL PUT
    sec_counts: dict[str, int] = {}
    capped_by_sector: dict[str, list[str]] = {}
    for r in sorted(
        [x for x in rows if x.signal == "SELL PUT"],
        key=lambda x: (x.vrp_pct or 0.0, x.iv_rank_1y or 0.0),
        reverse=True,
    ):
        cnt = sec_counts.get(r.sector, 0) + 1
        sec_counts[r.sector] = cnt
        if cnt > THRESHOLDS["max_green_per_sector"]:
            r.signal = "暂不操作"
            r.signal_tone = "flat"
            r.actionable = False
            r.put_pick = None
            r.reason = f"同属{r.sector}已满{THRESHOLDS['max_green_per_sector']}只限额 · " + r.reason
            capped_by_sector.setdefault(r.sector, []).append(r.symbol)

    summary = portfolio_summary(rows, capped_by_sector=capped_by_sector)
    return snap_date, rows, summary


def _fmt_pick(p: OptionPick | None) -> str:
    if not p:
        return "—"
    exp_lbl = p.expiration if p.expiration.startswith("~") else f"{p.expiration[5:]}({p.dte}d)"
    roc_s = f" 年化:{p.annual_roc_pct:.0f}%" if p.annual_roc_pct else ""
    src_s = "" if p.executable else " [理论估算]"
    return (
        f"{exp_lbl} ${p.strike:g}{p.right} Δ{p.delta:+.2f} "
        f"IV:{p.iv_pct:.0f}% ({p.dist_pct:+.1f}%){roc_s}{src_s}"
    )


def print_report(snap_date: str | None, rows: list[ScanRow], summary: dict) -> None:
    print(f"\n=== 确定性巨头池 · Wheel 策略雷达（期权快照: {snap_date or '无'}）===")
    vix_s = f" · {summary['vix_note']}" if summary.get("vix_note") else ""
    print(f"大盘共识: {summary['market_regime']}{vix_s}")
    for w in summary.get("warnings", []):
        print(f"[告警] {w}")
    print()

    hdr = (
        f"{'标的':<6}{'梯队':<4}{'现价':>7}{'单手$k':>7}{'RSI':>5}"
        f"{'MA50(σ)':>10}{'HV/IV35/VRP':>14}{'1Y_IVR/IVP':>11}{'Put/Call墙':>11}"
        f"  {'事件排雷':<17}{'趋势状态':<8}{'当日信号':<15}{'触发与风控依据'}"
    )
    print(hdr)
    print("-" * 148)
    for r in rows:
        vol_s = (
            f"{r.hv20_pct:.0f}/{r.iv35_pct:.0f}/{r.vrp_pct:+.0f}%"
            if r.iv35_pct is not None and r.vrp_pct is not None
            else f"{r.hv20_pct:.0f}/—/—"
        )
        ivr_s = (
            f"{r.iv_rank_1y:.0f}%/{r.iv_pct_1y:.0f}%"
            if r.iv_rank_1y is not None and r.iv_pct_1y is not None
            else "—/—"
        )
        wall_s = (
            f"{int(r.put_wall) if r.put_wall else '—'}/{int(r.call_wall) if r.call_wall else '—'}"
        )
        ma_s = f"{r.vs_ma50_pct:+.1f}%({r.ma50_sigmas:+.1f})"
        print(
            f"{r.symbol:<6}{r.tier:<4}{r.close:>7.1f}{r.lot_k:>6.1f}k{r.rsi:>5.1f}"
            f"{ma_s:>10}{vol_s:>14}{ivr_s:>11}{wall_s:>11}"
            f"  {r.event_alert:<17}{r.trend_state:<8}{r.signal:<15}{r.reason}"
        )

    # 仅打印真正允许操作的候选合约（SELL PUT / SELL CALL）
    actionable = [r for r in rows if r.actionable and (r.put_pick or r.call_pick)]
    if actionable:
        print("\n=== 候选期权合约（仅限非禁区标的；[理论估算] 需盘中核对券商盘口）===")
        for r in actionable:
            ev_tag = f" | {r.event_alert}" if r.event_alert != "—" else ""
            if r.put_pick:
                pick_s = _fmt_pick(r.put_pick)
                print(
                    f"  {r.signal:<15} {r.symbol:<5} 卖 Put : {pick_s}"
                    f" | 下方有效 Put 墙: ${r.put_wall or '—'}{ev_tag}"
                )
            elif r.call_pick:
                pick_s = _fmt_pick(r.call_pick)
                print(
                    f"  {r.signal:<15} {r.symbol:<5} 卖 Call: {pick_s}"
                    f" | 上方有效 Call 墙: ${r.call_wall or '—'}{ev_tag}"
                )


def main() -> None:
    ap = argparse.ArgumentParser(description="确定性巨头池 Wheel 策略扫描器")
    ap.add_argument("--tier", default="", help="筛选梯队，如 T0,T1")
    ap.add_argument("--symbols", default="", help="指定逗号分隔标的，如 V,MA,GOOG")
    ap.add_argument(
        "--holdings",
        default="",
        help="声明已持有正股底仓的标的（逗号分隔）；未指定时默认展示全部潜在持仓 Call 机会",
    )
    ap.add_argument("--json", action="store_true", help="输出 JSON 格式")
    args = ap.parse_args()

    tiers = {t.strip().upper() for t in args.tier.split(",") if t.strip()} or None
    syms = {s.strip().upper() for s in args.symbols.split(",") if s.strip()} or None
    holdings = (
        {h.strip().upper() for h in args.holdings.split(",") if h.strip()}
        if args.holdings
        else None
    )
    snap_date, rows, summary = scan_pool(tiers=tiers, symbols=syms, holdings=holdings)
    if args.json:
        print(
            json.dumps(
                {
                    "snapshot_date": snap_date,
                    "summary": summary,
                    "rows": [asdict(r) for r in rows],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print_report(snap_date, rows, summary)


if __name__ == "__main__":
    main()
