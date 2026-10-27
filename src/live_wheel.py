"""盘前/盘中实时 Wheel 扫描：用实时现价 + 现场重算的期权结构跑同一套规则引擎。

与 `wheel_scanner` 的分工：
- **规则零改动** —— 阈值、四道选点闸门（点差 / OI / 月度 OpEx / 期权墙）、三态信号
  全部复用 `evaluate_symbol`，本模块只换两个入参：现价（实时）与期权快照（现场算）。
- **静态字段继承** —— 1Y IV Rank / IV Percentile / 财报日 / 除息日盘内不变，
  从当日 `data/options_structure/{date}.json` 继承，不重复请求 Barchart。

两个 Greeks 来源（`--greeks`）：
- `yfinance`（默认，~45s/16 标的）：实时 IV + BSM 反推 gamma。IV 与 delta 真实，
  gamma 为模型值。Actions / 无 TWS 环境可用。
- `ibkr`（~10min）：复用 `compute_gex.fetch_options_greeks` 拉交易所真实 gamma，
  需本地 TWS / IB Gateway。盘前 IBKR 常无期权行情（Error 10091），会退回 BSM。

报价新鲜度：盘前与盘后的 yfinance 期权链是**上一交易日收盘快照**（实测同到期日
bid 仅 13% 与盘中一致、IV 中位差 3.3pp），不是隔夜不变的实时价。所以每份推荐合约
都携带 `chain_date` / `quote_ts`，输出末尾汇总数据时点，不把昨收当实时挂单价。

用法:
    uv run python -m src.live_wheel                       # 全池实时扫描
    uv run python -m src.live_wheel --symbols NVDA,TSM    # 只扫指定标的
    uv run python -m src.live_wheel --greeks ibkr         # 交易所真实 Greeks
    uv run python -m src.live_wheel --json                # JSON 输出
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from src.config import ROOT
from src.options_structure import _extract_events, compute_structure
from src.pricing import fetch_yf_chain
from src.wheel_scanner import (
    THRESHOLDS,
    WHEEL_POOL,
    _iv_at_dte,
    print_report,
    scan_pool,
)

ET = ZoneInfo("America/New_York")
NOW = datetime.now(timezone.utc).astimezone(ET)


def _derived_snapshot() -> tuple[str | None, dict]:
    """当日（或最近）期权快照里的静态字段：IVR / 财报日 / 除息日 / 名称。

    这些字段盘内不变，实时模式只借用它们，不用其 spot / gex / iv_curve。
    """
    files = sorted((ROOT / "data" / "options_structure").glob("20*.json"))
    if not files:
        return None, {}
    data = json.loads(files[-1].read_text(encoding="utf-8"))
    return files[-1].stem, data.get("symbols", {})


def _live_spots(symbols: list[str]) -> tuple[dict[str, float], str]:
    """批量取实时/盘前价。返回 ({symbol: price}, 取数时刻)。"""
    from src.fetchers.yfinance_fetcher import ensure_yf_proxy

    ensure_yf_proxy()
    import yfinance as yf  # noqa: E402

    spots: dict[str, float] = {}
    # prepost=True 才能拿到盘前/盘后价；group_by="ticker" 逐标的取末根 non-NaN close
    raw = yf.download(
        tickers=" ".join(symbols),
        period="1d",
        interval="5m",
        prepost=True,
        progress=False,
        group_by="ticker",
        threads=True,
    )
    for s in symbols:
        try:
            col = raw[s]["Close"] if len(symbols) > 1 else raw["Close"]
            last = col.dropna()
            if not last.empty:
                spots[s] = float(last.iloc[-1])
        except Exception:
            continue
    ts = NOW.strftime("%Y-%m-%d %H:%M:%S ET")
    # 批量下载偶发丢个别标的（yfinance 报 possibly delisted）：逐个补
    for s in set(symbols) - set(spots):
        try:
            p = float(yf.Ticker(s).fast_info.last_price)
            if p > 0:
                spots[s] = p
        except Exception:
            continue
    return spots, ts


def _window_expiries(sym: str, options: tuple[str, ...]) -> list[str]:
    """取 DTE 窗口（含财报前窗口边界）内的到期日，格式 YYYYMMDD。

    窗口比 THRESHOLDS 的 dte_min..dte_max 略宽：选点器自己要按财报日收窄到
    7~e_dte-1 或挑 OpEx，这里给足候选再让它筛。
    """
    today = NOW.date()
    out = []
    for e in options:
        try:
            dte = (datetime.strptime(e, "%Y-%m-%d").date() - today).days
        except ValueError:
            continue
        if 5 <= dte <= THRESHOLDS["dte_max"] + 14:
            out.append(e.replace("-", ""))
    return out


def _chain_health(chain: pd.DataFrame) -> tuple[bool, str]:
    """期权链健康度闸门：盘前 yfinance 链会整片失效，不能拿去算结构。

    实测（盘前 05:57 ET）：bid>0 仅 3%、openInterest 全 0、impliedVolatility 字段
    报 0.00%~3%（ATM 真实值应用 lastPrice 反解为 44.8%）。用它算出的 iv_curve
    只剩 1.56%、net_gex 恒 0、gamma_flip=None——比没有数据更危险，因为读数看着像个数。

    健康阈值取收盘链的实测下限留余量：bid>0 占一半以上，OI>0 覆盖八成以上。
    """
    if chain.empty:
        return False, "链为空"
    bid_ok = float((pd.to_numeric(chain["bid"], errors="coerce").fillna(0) > 0).mean())
    oi_ok = float((pd.to_numeric(chain["openInterest"], errors="coerce").fillna(0) > 0).mean())
    if bid_ok < 0.5 or oi_ok < 0.5:
        return False, f"盘口未就绪(bid>0 {bid_ok:.0%}/OI>0 {oi_ok:.0%}，多为盘前未开市)"
    return True, ""


def _prev_trading_chain(sym: str, _static: dict) -> tuple[pd.DataFrame, str] | None:
    """读上一交易日落盘的 raw 链（盘前小时级刷新时，昨收链才是可用的期权面）。"""
    from src.config import ROOT as _ROOT

    files = sorted((_ROOT / "data" / "options_structure").glob(f"raw_{sym}_*.csv"))
    for f in reversed(files):
        try:
            d = pd.read_csv(f, dtype={"expiration": str})
        except Exception:
            continue
        if not d.empty:
            stamp = f.stem.rsplit("_", 1)[-1]
            return d, f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}"
    return None


def _build_opt_map(symbols: list[str], static: dict, greeks: str) -> tuple[dict, dict[str, str]]:
    """现场重算各标的期权结构，返回 (opt_map, {symbol: 状态说明})。"""
    from src.fetchers.yfinance_fetcher import ensure_yf_proxy

    ensure_yf_proxy()
    import yfinance as yf  # noqa: E402

    opt_map: dict = {}
    notes: dict[str, str] = {}
    for sym in symbols:
        inherited = static.get(sym) or {}
        try:
            tk = yf.Ticker(sym)
            exps = _window_expiries(sym, tk.options)
            if not exps:
                notes[sym] = "窗口内无到期日"
                continue
            spot = float(tk.fast_info.last_price)

            if greeks == "ibkr":
                chain = _chain_from_ibkr(sym, exps, spot)
                if chain is None or chain.empty:
                    notes[sym] = "IBKR 无行情 → BSM 估算"
                    chain = fetch_yf_chain(sym, exps)
            else:
                chain = fetch_yf_chain(sym, exps)
            chain = chain.dropna(subset=["impliedVolatility"])
            chain = chain[chain["impliedVolatility"] > 0.01]
            ok, why = _chain_health(chain)
            if not ok:
                # 盘前 yfinance 链整片失效（bid/OI 全 0）→ 回退到上一交易日收盘链。
                # 这样盘前仍能算 IV35/VRP/结构墙（结论基于昨收期权面 + 实时现价），
                # 并且陈旧程度会写进 notes 与 pick.stale_days，不当实时报价用。
                prev = _prev_trading_chain(sym, static.get(sym) or {})
                if prev is not None:
                    chain, cdate = prev
                    ok2, why2 = _chain_health(chain)
                    if ok2:
                        notes[sym] = f"回退昨收链 {cdate}（{why}）"
                    else:
                        notes[sym] = f"{why}；昨收链也不可用"
                        continue
                else:
                    notes[sym] = why
                    continue
            detail = compute_structure(chain, spot)
            if detail.get("spot") is None:
                notes[sym] = "结构计算为空"
                continue
            # 静态字段（IVR / 财报 / 除息）继承当日快照；实时字段（spot/gex/iv_curve）用现场值
            for k in ("iv_rank_1y", "iv_pct_1y", "wiv", "name"):
                if inherited.get(k) is not None:
                    detail[k] = inherited[k]
            for k, v in _extract_events(tk).items():
                if v:
                    detail[k] = v
            opt_map[sym] = detail
            # iv35 是常规结果标记（用作「本标的可用」的判别），但不能盖掉前面的告警：
            # IBKR 无行情 / 回退昨收链 这些就是靠 notes 往下传递的
            tag = f"iv35={_iv_at_dte(detail, 35)}"
            prev = notes.get(sym)
            notes[sym] = f"{prev}；{tag}" if prev else tag
        except Exception as e:
            notes[sym] = f"失败: {type(e).__name__}"
            continue
    return opt_map, notes


def _chain_from_ibkr(sym: str, exps: list[str], spot: float) -> pd.DataFrame | None:
    """IBKR 真实 Greeks + OI → 与 yfinance 链同构的 DataFrame。

    OI 来源：IBKR genericTick 101（compute_gex._oi_from_ticker）优先。盘前外部源
    （yfinance）的 bid/OI 整片失效，而 IBKR 的 OI 是交易所读数，不受盘前影响。
    仅当 IBKR 未返回 OI（全 None）时才回退 yfinance 左连接。
    """
    try:
        from src.compute_gex import connect_ib, fetch_options_greeks, filter_options
        from src.fetchers.ibkr_fetcher import IBKRConnectionError, get_option_chain_params
    except Exception:
        return None
    ib = None
    try:
        ib, _ = connect_ib(ports=(4001, 4002))
    except IBKRConnectionError:
        return None
    if not ib:
        return None
    try:
        chains = get_option_chain_params(ib, sym)
        if not chains:
            return None
        chain = max(chains, key=lambda c: len(c.expirations) + len(c.strikes))
        _exps, strikes = filter_options(chain, spot, max(len(exps), 2), 0.15)
        if not strikes:
            return None
        g = fetch_options_greeks(ib, sym, _exps or exps, strikes, batch_size=50)
        if g.empty or g["gamma"].isna().all():
            return None
        g["expiration"] = g["expiration"].astype(str).str.replace("-", "", regex=False)
        g = g.rename(columns={"iv": "impliedVolatility"})
        # 1) IBKR 自带 OI 可用就直接用；2) 全缺才回退 yfinance 左连接
        if "openInterest" not in g.columns or g["openInterest"].isna().all():
            oi = fetch_yf_chain(sym, sorted(g["expiration"].unique()))
            if not oi.empty:
                g = g.merge(
                    oi[["expiration", "strike", "right", "openInterest", "volume"]],
                    on=["expiration", "strike", "right"],
                    how="left",
                    suffixes=("", "_yf"),
                )
                if "openInterest_yf" in g.columns:
                    g["openInterest"] = g["openInterest"].fillna(g["openInterest_yf"])
                    g = g.drop(columns=["openInterest_yf"])
        if "openInterest" not in g.columns:
            return None
        g["openInterest"] = pd.to_numeric(g["openInterest"], errors="coerce").fillna(0).astype(int)
        return g
    except Exception:
        return None
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass


def run(
    symbols: list[str] | None,
    tiers: set[str] | None,
    holdings: set[str] | None,
    greeks: str,
) -> dict:
    pool = [p for p in WHEEL_POOL if not symbols or p.symbol in symbols]
    if tiers:
        pool = [p for p in pool if p.tier in tiers]
    opt_syms = [p.opt_symbol or p.symbol for p in pool]

    static_date, static = _derived_snapshot()
    spots, spot_ts = _live_spots([p.symbol for p in pool])
    opt_map, notes = _build_opt_map(opt_syms, static, greeks)

    snap_key = NOW.strftime("%Y%m%d")
    _, rows, summary = scan_pool(
        symbols=symbols,
        tiers=tiers,
        holdings=holdings,
        live_spot=spots,
        opt_map_override=opt_map,
        snap_date_override=snap_key,
    )
    return {
        "snapshot_date": snap_key,
        "live": True,
        "quoted_at": spot_ts,
        "greeks": greeks,
        "static_snapshot": static_date,
        "spot_count": len(spots),
        "pool_size": len(pool),
        "symbols_ok": len(opt_map),
        "notes": notes,
        "summary": summary,
        "rows": [asdict(r) for r in rows],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="盘前/盘中实时 Wheel 扫描")
    ap.add_argument("--symbols", default="", help="逗号分隔标的，如 NVDA,TSM")
    ap.add_argument("--tier", default="", help="筛选梯队，如 T0,T1")
    ap.add_argument("--holdings", default="", help="已持有正股底仓的标的（激活 SELL CALL）")
    ap.add_argument(
        "--greeks",
        choices=("yfinance", "ibkr"),
        default="yfinance",
        help="Greeks 来源：yfinance=实时 IV+BSM gamma（默认）；ibkr=交易所真实 gamma（需 TWS）",
    )
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--dump", default="", help="落盘到指定路径（如 /tmp/wheel_live.json）")
    args = ap.parse_args()

    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or None
    tiers = {t.strip().upper() for t in args.tier.split(",") if t.strip()} or None
    holdings = {h.strip().upper() for h in args.holdings.split(",") if h.strip()} or None

    result = run(syms, tiers, holdings, args.greeks)

    # TWS 未启动/未登录时，全池都会静默落 yfinance —— 显式提醒怎么修，不让人以为拿到了真 Greeks
    if args.greeks == "ibkr" and any("IBKR 无行情" in str(v) for v in result["notes"].values()):
        import sys

        print(
            "⚠ --greeks ibkr 未取到任何真实 Greeks（TWS/IB Gateway 未启动或未登录）。\n"
            "   已降级 yfinance BSM 估算。修：启动 TWS → 确认端口 4001(实盘只读)/4002(模拟)；\n"
            "   盘前 IBKR 常无期权行情（Error 10091），需等开盘后再试。",
            file=sys.stderr,
        )

    if args.dump:
        from pathlib import Path

        out = Path(args.dump)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已落盘: {out}")
        return
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print(
        f"\n== Wheel 实时扫描 {NOW:%Y-%m-%d %H:%M ET} ==  "
        f"greeks={result['greeks']} · 现价 {result['spot_count']}/{result['pool_size']} 只有效 · "
        f"链 {result['symbols_ok']}/{result['pool_size']} 只"
    )
    from dataclasses import fields

    from src.wheel_scanner import ScanRow

    rows = [
        ScanRow(**{k: v for k, v in r.items() if k in {f.name for f in fields(ScanRow)}})
        for r in result["rows"]
    ]
    print_report(result["snapshot_date"], rows, result["summary"])
    # 陈旧链与新缺数据必须显式暴露：盘前盘后拿到的是上一交易日收盘快照，不是实时挂单价
    stale = [
        f"{r['symbol']}:{p['chain_date']}"
        for r in result["rows"]
        for p in (r.get("put_pick"), r.get("call_pick"))
        if p and p.get("stale_days", 0) > 0
    ]
    if stale:
        print(f"\n⚠ 推荐合约来自旧链（非当日）：{', '.join(stale)}")
    missing = [f"{k}({v})" for k, v in result["notes"].items() if not str(v).startswith("iv35=")]
    if missing:
        print(f"⚠ 期权面附注：{', '.join(missing)}")
    print(
        f"\n现价时点 {result['quoted_at']} · 静态字段继承 {result['static_snapshot']} · "
        "盘前/盘后期权链为上一交易日收盘快照，年化收益勿直接当挂单价"
    )


if __name__ == "__main__":
    main()
