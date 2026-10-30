"""确定性巨头池 Wheel 策略扫描器（Sell Put 抄底收租 + Covered Call 波段止盈）。

经对抗性量化风控审查（Adversarial Review）升级后的规则引擎：
1. 波动率真实定价：用快照 `iv_curve` 线性插值取 **35 DTE ATM IV**（拒绝 328d LEAPS IV 冒充），
   结合 Barchart 官方 **52 周 IV Rank / IV Percentile**（本地快照降级）与 **VRP = IV35 − HV20**。
2. 趋势与企稳过滤：用个股日波动率 σ 归一化均线回撤，识别确认空头趋势（SuperTrend 空 + ADX≥25），
   未出现右侧企稳信号（MACD 柱拐头 / 收复昨高 / 看涨吞没或锤子线）时降级为「左侧接刀·等企稳」。
3. 财报与除息排雷：财报落在选点窗口内（≤49d）时，优先搜寻财报前到期（7~e_dte 天）合约；
   若财报 ≤10d 或无财报前合约，拦截裸卖 Put；否决状态绝不输出可交易合约。
   财报日当天看参考时刻：美东收盘半小时后（AMC 已发布）归「已发」解除禁卖，盘中仍按待发保守处理。
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
from datetime import datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from src.clock import et_today, now_et
from src.config import ROOT
from src.indicators import compute_all_indicators, load_data
from src.options_structure import XNYS
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
    "put_bb_pos_max": 15.0,  # 布林带位置 <= 15%（贴下轨，与 RSI 满足其一即可）
    "put_ma50_sigma_max": -1.2,  # 距 MA50 偏离 <= -1.2 倍日标准差（波动率归一化）
    "put_vs_ma200_min": -3.0,  # 回踩 MA200 区间下限（低于 -3% 已有效跌破牛熊线）
    "put_vs_ma200_max": 3.0,  # 回踩 MA200 区间上限（高于 +3% 尚未贴近支撑）
    # Sell Call（Covered Call）技术击球区
    "call_rsi_trigger": 68.0,  # RSI(14) >= 68 动量过热
    "call_bb_pos_min": 85.0,  # 布林带位置 >= 85%（贴上轨，与 RSI 满足其一即可）
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
    "earnings_window_dte": 49,  # 与 dte_max 一致：财报落在整个选点窗口内就切财报前合约
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
    executable: bool = True  # True=来自真实期权链, False=BSM+Skew理论估算
    annual_roc_pct: float | None = None  # 若有 bid/ask 则算年化收益率
    chain_date: str | None = None  # 该合约实际来自哪天的 raw 链（YYYY-MM-DD）
    stale_days: int = 0  # 链比快照日旧几天：0=当日，>0=回溯到的旧链
    quote_ts: str | None = None  # 实际取数完成的 UTC 时刻（ISO）


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
    ivr_source: str | None  # barchart / local_iv35_1y；无可用历史时为 None
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
    event_tone: str = "flat"  # bear(财报<=10d) / warn(财报<=49d或除息<=14d) / flat
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
    opt_key: str, cur_iv: float | None, snap_date: str | None
) -> tuple[float | None, float | None]:
    """以快照日为截止，用本地近一年 IV35 计算 IV Rank 与 Percentile。

    需要有一年前的历史覆盖且窗口内至少 200 个有效观测；样本不足或 IV 无变化时
    返回未知，让 vol_rich 门槛保持关闭。
    """
    if cur_iv is None:
        return None, None
    ref_dt = _ref_date(snap_date)
    year_start = ref_dt - timedelta(days=365)
    dated_files: list[tuple[datetime.date, Path]] = []
    for f in sorted((ROOT / "data" / "options_structure").glob("20*.json")):
        try:
            file_date = datetime.strptime(f.stem, "%Y%m%d").date()
        except ValueError:
            continue
        if file_date <= ref_dt:
            dated_files.append((file_date, f))
    if not dated_files:
        return None, None

    observations: list[tuple[datetime.date, float]] = []
    for file_date, f in dated_files:
        v = (json.loads(f.read_text(encoding="utf-8")).get("symbols") or {}).get(opt_key) or {}
        iv = _iv_at_dte(v, 35)
        if iv:
            observations.append((file_date, float(iv)))
    if not observations or observations[0][0] > year_start:
        return None, None
    hist = [iv for file_date, iv in observations if file_date >= year_start]
    if len(hist) < 200:
        return None, None
    lo, hi = min(hist), max(hist)
    if hi <= lo:
        return None, None
    rank = round(float(np.clip((cur_iv - lo) / (hi - lo) * 100, 0.0, 100.0)), 1)
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
    return et_today()


def _is_monthly_opex(dt: datetime.date) -> bool:
    """判断是否为美股标准月度期权到期日（每月第 3 个周五，或遇节假日顺延至 15~21 日的周四）。"""
    return 15 <= dt.day <= 21 and dt.weekday() in (3, 4)


def _find_chain_file(opt_sym: str, ref_dt: datetime.date) -> tuple[Path | None, int]:
    """找最贴近 ref_dt 的 raw 期权链，返回 (路径, 陈旧天数)。

    为什么必须回溯：直接拼 raw_{sym}_{ref_str}.csv 找不到就落到 BSM 兜底分支，会产出
    oi=0 / expiration='~35d(BSM估算)' 的伪合约——而 opex / 点差 / OI 深度 / 期权墙四道
    闸门全部失效。数据班次里 options_structure 对单个标的失败（yfinance 抽风/限流）时
    当天就没有该标的的链，此时应当用前一个交易日的真实链并**标明陈旧天数**，
    而不是静默变成理论估算。

    文件日期已经统一为美东日期，未来日期文件不参与选点。
    """
    d = ROOT / "data" / "options_structure"
    exact = d / f"raw_{opt_sym}_{ref_dt:%Y%m%d}.csv"
    if exact.exists():
        return exact, 0
    best: Path | None = None
    best_dt: datetime.date | None = None
    for f in d.glob(f"raw_{opt_sym}_*.csv"):
        try:
            fdt = datetime.strptime(f.stem.rsplit("_", 1)[-1], "%Y%m%d").date()
        except ValueError:
            continue
        if fdt > ref_dt:
            continue
        if best_dt is None or fdt > best_dt:
            best, best_dt = f, fdt
    if best is None or best_dt is None:
        return None, 0
    return best, max((ref_dt - best_dt).days, 0)


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
    chain_info: tuple[pd.DataFrame, str] | None = None,
) -> OptionPick | None:
    """选点器：锚定快照日 DTE；优选 OpEx/高 OI 到期日；结合期权墙外侧与点差过滤。"""
    ref_dt = _ref_date(snap_date)
    raw_path = None
    if chain_info is not None:
        df, chain_date = chain_info
        df = df.copy()
        chain_stale = max((ref_dt - datetime.strptime(chain_date, "%Y-%m-%d").date()).days, 0)
    else:
        raw_path, chain_stale = _find_chain_file(opt_sym, ref_dt)
        chain_date = (
            datetime.strptime(raw_path.stem.rsplit("_", 1)[-1], "%Y%m%d").strftime("%Y-%m-%d")
            if raw_path
            else None
        )
        df = pd.DataFrame()
        if raw_path is not None:
            try:
                df = pd.read_csv(raw_path, dtype={"expiration": str})
            except Exception:
                pass

    dte_min, dte_max = THRESHOLDS["dte_min"], THRESHOLDS["dte_max"]
    pre_earnings = False
    # 若财报落在 11~49 天内，收敛到「财报前到期 (7 <= dte < earnings_dte)」避开财报跳空
    if earnings_dte is not None and 10 < earnings_dte <= THRESHOLDS["earnings_window_dte"]:
        dte_min, dte_max = 7, earnings_dte - 1
        pre_earnings = True

    if chain_info is not None or raw_path is not None:
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
                        chain_date=chain_date,
                        stale_days=chain_stale,
                        quote_ts=str(best["quote_ts"])
                        if "quote_ts" in best and pd.notna(best["quote_ts"])
                        else None,
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


# AMC 财报发布时刻（美东）：收盘后半小时，过此后当天的财报视为已经发布
AMC_RELEASE = time(16, 30)


def _after_close(snap_date: str | None = None) -> bool:
    """参考时刻是否已过当日 AMC 财报发布：今天的快照看当前时刻，更早的快照都是收盘后产物。"""
    now = now_et()
    return _ref_date(snap_date) < now.date() or now.time() >= AMC_RELEASE


def _event_fields(
    ov: dict, snap_date: str | None = None
) -> tuple[str | None, int | None, str | None, int | None, str, str, bool]:
    """解析财报日与除息日倒计时（锚定快照日），生成排雷标签、语义色调与财报日期可用性。"""
    ed = ov.get("earnings_date")
    e_dte = _days_until(ed, snap_date)
    # 财报日当天：Yahoo 在发布后还会把这一天的日期报一段时间，按日期算永远是「0d」→
    # 一直禁卖。参考时刻过了收盘半小时（AMC 已发布）就归「已发」；盘中仍按「今晚才发」保守处理。
    reported = e_dte is not None and (e_dte < 0 or (e_dte == 0 and _after_close(snap_date)))
    recent_ed = ed if (e_dte is not None and -14 <= e_dte <= 0 and reported) else None
    if reported:
        ed, e_dte = None, None
    # Yahoo 有时已经把日期推到下个季度，只报「财报90d」会看着像还没发；
    # 最近一次发布仍在 14 天内就一并标出来（不参与禁卖闸门，只作排除已发布用）。
    last_ed = ov.get("last_earnings_date")
    last_dte = _days_until(last_ed, snap_date)
    last_reported = last_dte is not None and (
        last_dte < 0 or (last_dte == 0 and _after_close(snap_date))
    )
    fresh_ed = last_ed if last_dte is not None and -14 <= last_dte <= 0 and last_reported else None
    # 下季日期已滚出、当晚财报尚未发布时，当天事件优先于下季日期参与禁卖
    if last_dte == 0 and not last_reported:
        ed, e_dte = last_ed, last_dte

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
    elif recent_ed or fresh_ed:
        tags.append(f"财报已发({(recent_ed or fresh_ed)[5:]})")
    else:
        tags.append("财报待定")
    if e_dte is not None and fresh_ed:
        tags.append(f"已发({fresh_ed[5:]})")
    if div_dte is not None and div_dte <= THRESHOLDS["ex_div_warn_dte"]:
        tags.append(f"除息{div_dte}d")
        if event_tone == "flat":
            event_tone = "warn"
    return (
        ed,
        e_dte,
        exd,
        div_dte,
        " · ".join(tags),
        event_tone,
        bool(e_dte is not None or recent_ed or fresh_ed),
    )


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
    live_spot: float | None = None,
    chain_info: tuple[pd.DataFrame, str] | None = None,
) -> ScanRow | None:
    csv_path = ROOT / "data" / "stocks" / f"{spec.symbol}.csv"
    if not csv_path.exists():
        return None
    df = load_data(str(csv_path))
    if df.empty or len(df) < 200:
        return None

    opt_key = spec.opt_symbol or spec.symbol
    ov = opt_map.get(opt_key) or {}
    snap_dt = pd.Timestamp(_ref_date(snap_date))
    # 静态扫描与实时模式都锚定期权快照日，丢弃快照之后的日线，避免新股价配旧期权面
    df = df[df.index <= snap_dt]
    if df.empty or len(df) < 200:
        return None

    last_daily_dt = df.index[-1]
    missing_sessions = 0
    if last_daily_dt < snap_dt:
        sessions = XNYS.sessions_in_range(
            last_daily_dt.strftime("%Y-%m-%d"), snap_dt.strftime("%Y-%m-%d")
        )
        missing_sessions = max(len(sessions) - 1, 0)

    snap_spot = float(ov["spot"]) if ov.get("spot") is not None else None
    eff_spot = live_spot if (live_spot is not None and live_spot > 0) else None
    if eff_spot is None and snap_spot is not None and snap_spot > 0 and last_daily_dt < snap_dt:
        eff_spot = snap_spot
    # 只有一个交易日缺口时才能用现价补当日 bar；非交易日、多日缺口或缺现价时禁止生成信号
    session_day = bool(XNYS.is_session(snap_dt.strftime("%Y-%m-%d")))
    daily_stale = not session_day or (
        last_daily_dt < snap_dt and (missing_sessions > 1 or eff_spot is None)
    )
    if daily_stale:
        eff_spot = None

    # 实时价或新于日线的快照价先拼成当日 bar，再统一计算技术指标。
    # 必须先拼后算：compute_all_indicators 不幂等（重复调用会出重名列，add_smc_mtf
    # 的 resample 会因此报 `'first' is not a valid function`）。因为实时行没有成交量，
    # volume 留 NaN 并在下面把 vol_ratio 回退中性值 1.0，不当作缩量。
    if eff_spot is not None:
        live_row = pd.DataFrame(
            {
                "open": [eff_spot],
                "high": [eff_spot],
                "low": [eff_spot],
                "close": [eff_spot],
                "volume": [np.nan],
            },
            index=pd.DatetimeIndex([snap_dt], name=df.index.name),
        )
        # 同一交易日重跑时不重复追加（日线已含当日收盘则整行替换）
        df = pd.concat([df[df.index < live_row.index[0]], live_row])
    df = compute_all_indicators(df)
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
    # 技术指标、期权选点与墙位基准统一使用同一现价 close
    opt_spot = close
    iv35 = _iv_at_dte(ov, 35)
    vrp = round(iv35 - hv20, 1) if iv35 is not None else None

    ivr = ov.get("iv_rank_1y")
    ivp = ov.get("iv_pct_1y")
    ivr_source = "barchart" if ivr is not None or ivp is not None else None
    if ivr is None and ivp is None:
        ivr, ivp = _local_iv_rank_fallback(opt_key, iv35, snap_date)
        if ivr is not None or ivp is not None:
            ivr_source = "local_iv35_1y"

    pw, cw = _directional_walls(ov, opt_spot)
    ed, e_dte, exd, div_dte, event_alert, event_tone, earnings_known = _event_fields(ov, snap_date)
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
    # 实时/快照补行没有成交量 → vol_ratio 无法定义，按中性 1.0 处理（不当作缩量）
    if eff_spot is not None:
        vol_ratio = 1.0
    raw_mh0 = last.get("MACD_hist", 0.0)
    macd_h0 = float(raw_mh0) if pd.notna(raw_mh0) else 0.0
    raw_mh1 = df["MACD_hist"].iloc[-2] if len(df) >= 2 else macd_h0
    macd_h1 = float(raw_mh1) if pd.notna(raw_mh1) else macd_h0
    prev_close = float(df["close"].iloc[-2]) if len(df) >= 2 else close

    # 强空头趋势：SuperTrend 空头 且 (ADX>=25 或 MA50<MA200 死叉 或 放量破布林下轨)
    severe_downtrend = supert_dir < 0 and (
        adx >= 25.0 or ma50 < ma200 or (bb_pos < 0 and vol_ratio >= 1.5)
    )
    # 右侧企稳信号：收盘站上前一日高点 / 锤子线或看涨吞没 / 止跌收红且 MACD 柱拐头向上
    price_action_ok = (
        close > float(df["high"].iloc[-2])
        or bool(last.get("CDL_HAMMER", 0))
        or bool(last.get("CDL_ENGULFING", 0) > 0)
    )
    stabilized = price_action_ok or (
        macd_h0 > macd_h1 and (not severe_downtrend or close >= prev_close)
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

    # 硬门槛：非交易日、日线缺口、T2 受限池、期权链或财报日期缺失时不发期权信号
    if not session_day:
        reasons.append("非交易日·不发出入场信号")
    elif daily_stale:
        reasons.append(
            f"股票日线落后{missing_sessions}个交易日·需更新"
            if missing_sessions > 1
            else "股票日线与快照日期不一致·需更新"
        )
    elif (
        spec.tier == "T2"
        or suitability in ("权利金过薄", "波动过大")
        or suitability.startswith("单手过重")
    ):
        # 不带金额：左栏「单手$k」已经写明具体数值，这里只给结论
        reasons.append(f"{suitability.split('(')[0]}·宜正股或作底仓")
    elif iv35 is None:
        reasons.append("缺期权数据·需运行 fetch_options_structure")
    elif not earnings_known:
        reasons.append("财报日缺失·暂不出手")
    else:
        is_oversold = rsi <= THRESHOLDS["put_rsi_trigger"] or bb_pos <= THRESHOLDS["put_bb_pos_max"]
        is_pullback = ma50_sigmas <= THRESHOLDS["put_ma50_sigma_max"] or (
            ma50 >= ma200
            and THRESHOLDS["put_vs_ma200_min"] <= vs_ma200 <= THRESHOLDS["put_vs_ma200_max"]
        )
        # IVR 两项可能都为 None（Barchart 拉取失败且本地近一年历史不足）。
        # 缺失时按「未达标」处理（保守不发信号是刻意的），但**理由文案必须说数据不足**，
        # 不能印成 "IVR 0%" —— 那是把「不知道」伪装成一个具体读数。
        ivr_known = ivr is not None or ivp is not None
        ivr_best = max(ivr or 0.0, ivp or 0.0)
        # 文案按**实际取到最大值那个字段**命名，不是按 ivr 是否有值判：
        # IVR (IV Rank) 与 IVP (IV Percentile) 口径不同但共用 25% 门槛，取 max 是
        # 「任一达标即可」。若一律标 IVR，NVDA 的 53.8/77.7 会被印成「IVR 78%」——
        # 把一个 IV Percentile 读数冒充成 IV Rank（此前修复犯过这个错）。
        ivr_nm = "IVR" if (ivr is not None and ivr >= (ivp or 0.0)) else "IVP"
        vol_rich = (
            iv35 >= THRESHOLDS["put_iv_min"]
            and (vrp is not None and vrp >= THRESHOLDS["vrp_min"])
            and ivr_known
            and ivr_best >= THRESHOLDS["ivr_min"]
        )

        is_overbought = (
            rsi >= THRESHOLDS["call_rsi_trigger"] or bb_pos >= THRESHOLDS["call_bb_pos_min"]
        ) and ma50_sigmas >= THRESHOLDS["call_ma50_sigma_min"]

        if is_oversold and is_pullback:
            # 只写「为什么」，不重复相邻列已有的读数：RSI 与 MA50(σ) 就在左边两列，
            # 事件排雷列也已写明「财报Nd」。重复会把行撑到 220 显示宽、在普通终端
            # 下折行。BB 位置没有独立列，保留。
            reasons.append(
                "超卖" if rsi <= THRESHOLDS["put_rsi_trigger"] else f"触布林下轨(BB {bb_pos:.0f}%)"
            )

            if e_dte is not None and e_dte <= THRESHOLDS["earnings_danger_dte"]:
                reasons.insert(0, "财报禁卖")
            elif not vol_rich:
                reasons.insert(0, "权利金偏薄")
                if iv35 < THRESHOLDS["put_iv_min"] or (
                    vrp is not None and vrp < THRESHOLDS["vrp_min"]
                ):
                    reasons.append("VRP/IV35 不足")
                elif not ivr_known:
                    reasons.append("IVR 数据不足")
                else:
                    reasons.append(f"{ivr_nm} 未达 {THRESHOLDS['ivr_min']:.0f}%")
            elif severe_downtrend and not stabilized:
                near_setup = True
                reasons.insert(0, "空头未企稳(等止跌)")
            elif e_dte is not None and e_dte <= THRESHOLDS["earnings_window_dte"]:
                signal, signal_tone = "SELL PUT", "bull"
                reasons.append("切财报前到期合约")
            else:
                signal, signal_tone = "SELL PUT", "bull"
                reasons.append("波动率够肥")

        elif is_pullback and rsi <= 45.0 and vol_rich:
            if e_dte is not None and e_dte <= THRESHOLDS["earnings_danger_dte"]:
                reasons.append("财报禁卖 · 近支撑")
            else:
                near_setup = True
                reasons.append("接近卖 Put 区 · 回踩支撑")

        elif is_overbought:
            ov_tag = (
                "RSI 过热"
                if rsi >= THRESHOLDS["call_rsi_trigger"]
                else f"触布林上轨(BB {bb_pos:.0f}%)"
            )
            reasons.append(ov_tag)
            has_stock = holdings is None or (spec.symbol in holdings)
            if not has_stock:
                reasons.insert(0, "超买但无底仓")
            elif e_dte is not None and e_dte <= THRESHOLDS["earnings_danger_dte"]:
                reasons.insert(0, "财报禁卖·防卖飞")
            elif iv35 < THRESHOLDS["call_iv_min"] or (
                vrp is not None and vrp < THRESHOLDS["call_vrp_min"]
            ):
                reasons.insert(0, "IV35/VRP 偏薄")
            else:
                signal, signal_tone = "SELL CALL", "bear"
                reasons.insert(0, "仅限备兑(禁裸卖)")
                if e_dte is not None and e_dte <= THRESHOLDS["earnings_window_dte"]:
                    reasons.append("切财报前到期合约")
                if div_dte is not None and div_dte <= THRESHOLDS["ex_div_warn_dte"]:
                    reasons.append(f"除息{div_dte}d防提前行权")
        else:
            reasons.append("通道中部")

    # 仅对 SELL PUT / SELL CALL 生成推荐合约（若盘口流动性枯竭或年化低于门槛，自动降级为暂不操作）
    put_pick = None
    call_pick = None
    if signal == "SELL PUT":
        put_pick = _pick_contract(
            opt_key,
            snap_date,
            ov,
            opt_spot,
            iv35,
            "P",
            THRESHOLDS["put_delta_target"],
            e_dte,
            pw,
            chain_info=chain_info,
        )
        if put_pick is None:
            signal, signal_tone = "暂不操作", "flat"
            msg = (
                f"财报前({e_dte}d)无合规短Put"
                if (e_dte is not None and 10 < e_dte <= THRESHOLDS["earnings_window_dte"])
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
            opt_key,
            snap_date,
            ov,
            opt_spot,
            iv35,
            "C",
            THRESHOLDS["call_delta_target"],
            e_dte,
            cw,
            chain_info=chain_info,
        )
        if call_pick is None:
            signal, signal_tone = "暂不操作", "flat"
            msg = (
                f"财报前({e_dte}d)无合规短Call"
                if (e_dte is not None and 10 < e_dte <= THRESHOLDS["earnings_window_dte"])
                else "期权盘口点差过宽(防滑点)"
            )
            reasons.insert(0, msg)
        elif (
            call_pick.annual_roc_pct is not None
            and call_pick.annual_roc_pct < THRESHOLDS["call_roc_min"]
        ):
            signal, signal_tone = "暂不操作", "flat"
            reasons.insert(0, f"候选年化仅{call_pick.annual_roc_pct:.0f}%偏薄")
            call_pick = None

    pick = put_pick or call_pick
    actionable = signal in ("SELL PUT", "SELL CALL") and bool(pick and pick.executable)
    # 推荐合约来自旧链时如实标注（不得让 T-1 报价静默充作当日报价）
    stale_pick = next(
        (p for p in (put_pick, call_pick) if p is not None and p.stale_days > 0), None
    )
    if stale_pick is not None and stale_pick.chain_date:
        reasons.append(f"链陈旧{stale_pick.stale_days}d({stale_pick.chain_date})")

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
        ivr_source=ivr_source,
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


def criteria_lines() -> list[str]:
    """Wheel 筛选标准的人话说明（卡底小字 / 页脚口径用）。

    阈值一律从 THRESHOLDS 取，不在前端或文档另抄一份 —— 改阈值时说明文字自动跟着变。
    返回按主题分组的句子，前端一段一行渲染。
    """
    t = THRESHOLDS
    k = lambda v: f"{v:.0f}k"  # noqa: E731
    return [
        f"池准入：单手占资 ≤ ${k(t['lot_ideal_max_k'])} 为理想"
        f"（T2 观察池或超 ${k(t['lot_hard_max_k'])} 判单手过重、直接不发信号）；"
        f"HV20 < {t['hv20_min']:.0f}% 且 IV35 < 22% 判权利金过薄，"
        f"HV20 > {t['hv20_max']:.0f}% 判波动过大 —— 三者都只发正股建议",
        f"卖 Put：RSI ≤ {t['put_rsi_trigger']:.0f} 或贴布林下轨"
        f"（≤ {t['put_bb_pos_max']:.0f}%），且距 MA50 ≤ {t['put_ma50_sigma_max']:+.1f}σ"
        f" 或牛市回踩 MA200（{t['put_vs_ma200_min']:+.0f}%~{t['put_vs_ma200_max']:+.0f}%）；"
        f"权利金须 IV35 ≥ {t['put_iv_min']:.0f}% 且 VRP ≥ {t['vrp_min']:+.0f}%"
        f" 且 1Y IVR/IVP ≥ {t['ivr_min']:.0f}%；年化 ≥ {t['put_roc_min']:.0f}%",
        f"备兑卖 Call：RSI ≥ {t['call_rsi_trigger']:.0f} 或贴布林上轨"
        f"（≥ {t['call_bb_pos_min']:.0f}%），且高于 MA50 ≥ {t['call_ma50_sigma_min']:+.1f}σ；"
        f"IV35 ≥ {t['call_iv_min']:.0f}% 且 VRP ≥ {t['call_vrp_min']:+.0f}%；"
        f"年化 ≥ {t['call_roc_min']:.0f}%，仅限持 100 股底仓（禁裸卖）",
        f"选点：DTE {t['dte_min']}~{t['dte_max']} 天，卖 Put 取 Δ {t['put_delta_target']:+.2f}"
        f" 附近、卖 Call 取 Δ {t['call_delta_target']:+.2f} 附近；"
        f"盘口点差 > {t['max_spread_pct']:.0f}% 直接放弃（防滑点）",
        f"事件排雷：财报 ≤ {t['earnings_danger_dte']} 天强制禁卖；"
        f"财报 ≤ {t['earnings_window_dte']} 天改选财报前到期合约；"
        f"除息 ≤ {t['ex_div_warn_dte']} 天卖 Call 警惕提前行权",
        f"组合限额：同板块最多 {t['max_green_per_sector']} 只同时出手，超限按 VRP 择优保留",
    ]


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

    put_candidates = [r for r in rows if r.signal == "SELL PUT" and r.actionable]
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
        "criteria": criteria_lines(),
    }


def scan_pool(
    tiers: set[str] | None = None,
    symbols: set[str] | None = None,
    holdings: set[str] | None = None,
    live_spot: dict[str, float] | None = None,
    opt_map_override: dict | None = None,
    snap_date_override: str | None = None,
    chain_map_override: dict[str, tuple[pd.DataFrame, str]] | None = None,
) -> tuple[str | None, list[ScanRow], dict]:
    """扫全池。

    live_spot / opt_map_override / snap_date_override / chain_map_override 仅供盘中实时模式注入：
    报表链与快照都走数据班次落盘的静态文件，实时模式由 src.live_wheel 传入
    实时价与现场重算的期权结构，其余规则（阈值/闸门）完全不变。
    """
    if opt_map_override is not None:
        snap_date, opt_map = snap_date_override, opt_map_override
    else:
        snap_date, opt_map = _latest_options_snapshot()
    rows: list[ScanRow] = []
    for spec in WHEEL_POOL:
        if tiers and spec.tier not in tiers:
            continue
        if symbols and spec.symbol not in symbols:
            continue
        if opt_map_override is not None and spec.symbol not in (live_spot or {}):
            continue
        r = evaluate_symbol(
            spec,
            snap_date,
            opt_map,
            holdings=holdings,
            live_spot=(live_spot or {}).get(spec.symbol),
            chain_info=(chain_map_override or {}).get(spec.opt_symbol or spec.symbol),
        )
        if r is not None:
            rows.append(r)

    # 同板块超限自动择优：按 VRP 降序只保留前 max_green_per_sector 个为 SELL PUT
    sec_counts: dict[str, int] = {}
    capped_by_sector: dict[str, list[str]] = {}
    for r in sorted(
        [x for x in rows if x.signal == "SELL PUT" and x.actionable],
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


def dump_board() -> Path | None:
    """把当日 Wheel 看板落盘到 data/wheel/{期权快照日}.json，供 Pages 导出只读。

    为什么必须落盘：真实期权链 raw_*.csv 不入库（~1MB/天，.gitignore），而 Pages 部署
    在 GitHub runner 上跑 export_pages —— 那里没有 raw 链，_pick_contract 会静默退化成
    BSM 兜底，点差 / OI 深度 / 月度 OpEx / 期权墙四道闸门全部失效（线上曾因此推荐过
    点差过宽的合约）。改在数据班次算（raw 链还在）、提交一份 ~15KB 的看板，导出端零计算。
    """
    snap_date, rows, summary = scan_pool()
    if not snap_date:
        return None
    out = ROOT / "data" / "wheel" / f"{snap_date}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "snapshot_date": snap_date,
                "summary": summary,
                "rows": [asdict(r) for r in rows],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return out


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
    from rich import box
    from rich.console import Console
    from rich.table import Table

    # 宽度取全表所需，不再手写列宽：rich 自己量 CJK 双宽字形。
    # 240 是上界，实际表宽由内容决定（~170）；给不够时 rich 会压缩最宽列，
    # 整表变成「标的 梯…」的截断。不依赖 console.width：非 TTY（管道/日志）
    # 下 rich 取不到真实宽度会退回 80 列。
    console = Console(width=240)
    vix_s = f" · {summary['vix_note']}" if summary.get("vix_note") else ""
    console.print(
        f"\n[bold]=== 确定性巨头池 · Wheel 策略雷达（期权快照: {snap_date or '无'}）===[/bold]"
    )
    console.print(f"大盘共识: {summary['market_regime']}{vix_s}")
    for w in summary.get("warnings", []):
        console.print(f"[yellow][告警] {w}[/yellow]")

    # 列宽不手写：rich 自己量 CJK 双宽字形，比手算 east_asian_width 可靠。
    # min_width 只给数字列（数字列没下限时表头与数字会贴在一起）；
    # 文本列不给，让内容自己定宽度——给了反而把整表白撑宽。
    table = Table(
        box=box.SIMPLE_HEAD,
        header_style="bold",
        padding=(0, 1),
        expand=False,
        show_edge=False,
    )
    for col, justify, min_w in (
        ("标的", "left", 4),
        ("梯队", "left", 2),
        ("现价", "right", 5),
        ("单手$k", "right", 5),
        ("RSI", "right", 3),
        ("MA50(σ)", "right", 10),
        ("HV/IV35/VRP", "right", 11),
        ("1Y_IVR/IVP", "right", 9),
        ("Put/Call墙", "right", 9),
        ("事件排雷", "left", 0),
        ("趋势状态", "left", 0),
        ("当日信号", "left", 0),
        ("触发与风控依据", "left", 0),
    ):
        extra = {"min_width": min_w} if min_w else {}
        table.add_column(col, justify=justify, no_wrap=True, **extra)

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
        table.add_row(
            r.symbol,
            r.tier,
            f"{r.close:.1f}",
            f"{r.lot_k:.1f}k",
            f"{r.rsi:.1f}",
            f"{r.vs_ma50_pct:+.1f}%({r.ma50_sigmas:+.1f})",
            vol_s,
            ivr_s,
            wall_s,
            r.event_alert,
            r.trend_state,
            r.signal,
            r.reason,
        )
    console.print(table)

    # 仅打印真正允许操作的候选合约（SELL PUT / SELL CALL）
    actionable = [r for r in rows if r.actionable and (r.put_pick or r.call_pick)]
    if actionable:
        console.print(
            "\n[bold]=== 候选期权合约（仅限非禁区标的；[理论估算] 需盘中核对券商盘口）===[/bold]"
        )
        picks = Table(box=box.SIMPLE_HEAD, header_style="bold", padding=(0, 1), expand=False)
        for col in ("标的", "操作", "合约", "期权墙", "事件排雷"):
            picks.add_column(col, no_wrap=True)
        for r in actionable:
            wall = r.put_wall if r.put_pick else r.call_wall
            wall_lbl = "下方 Put 墙" if r.put_pick else "上方 Call 墙"
            picks.add_row(
                r.symbol,
                f"卖 {'Put' if r.put_pick else 'Call'}",
                _fmt_pick(r.put_pick or r.call_pick),
                f"{wall_lbl} ${int(wall) if wall else '—'}",
                r.event_alert if r.event_alert != "—" else "",
            )
        console.print(picks)


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
    ap.add_argument(
        "--dump", action="store_true", help="落盘 data/wheel/{快照日}.json（供 Pages 导出只读）"
    )
    args = ap.parse_args()

    tiers = {t.strip().upper() for t in args.tier.split(",") if t.strip()} or None
    syms = {s.strip().upper() for s in args.symbols.split(",") if s.strip()} or None
    holdings = (
        {h.strip().upper() for h in args.holdings.split(",") if h.strip()}
        if args.holdings
        else None
    )
    if args.dump:
        path = dump_board()
        print(f"Wheel 看板已落盘: {path}" if path else "无期权快照，跳过落盘")
        return

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
