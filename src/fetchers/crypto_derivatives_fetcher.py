"""
加密衍生品快照（timsun /assets/crypto/derivatives 面板数据源）。

公开 API 直连（无密钥）：
  - OKX v5 public：funding-rate（永续资金费率）、open-interest（OI USD）、
    market/ticker（永续现价）、rubik taker-volume（买卖成交比 → 散户多空比代理）
  - Deribit public：get_book_summary_by_currency（BTC/ETH options 全线 OI+IV，
    → 期权墙 / PCR / Max Pain）、get_index_price（现货锚）
  - yfinance BTC=F：CME 比特币期货（基差 = CME − 现货）
  - CFTC COT（data/cot/cot.csv）：CME 机构头寸信号（fetcher 不依赖，分析层读取）

快照 data/crypto_derivatives/{date}.json（覆盖写，每日 Actions 跑）。
本地需代理（OKX 域名本地 DNS 劫持）；Actions 直连 OKX/Deribit（无代理）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import pandas as pd

from src.config import ROOT

logger = logging.getLogger(__name__)

DATA = ROOT / "data" / "crypto_derivatives"

_OKX = "https://www.okx.com/api/v5"
_DERIBIT = "https://www.deribit.com/api/v2"


def _req(url: str, params: dict | None = None, timeout: int = 20) -> dict:
    import os

    import requests

    proxies = None
    if "HTTPS_PROXY" in os.environ:
        proxies = {"https": os.environ["HTTPS_PROXY"]}
    resp = requests.get(
        url,
        params=params,
        timeout=timeout,
        headers={"User-Agent": "Mozilla/5.0"},
        proxies=proxies,
    )
    resp.raise_for_status()
    return resp.json()


def _okx(path: str, **params) -> list:
    """OKX v5 GET：code=0 时返回 data 列表。"""
    d = _req(f"{_OKX}/{path}", params or None)
    if d.get("code") != "0":
        raise RuntimeError(f"OKX {path}: {d.get('msg')}")
    return d.get("data", [])


def _deribit(method: str, **params) -> dict:
    d = _req(f"{_DERIBIT}/public/{method}", params)
    if "error" in d and d["error"]:
        raise RuntimeError(f"Deribit {method}: {d['error']}")
    return d.get("result", {})


def fetch_btc_perp() -> dict:
    """OKX 永续：资金费率 + OI + 现价（BTC/ETH）。"""
    out = {}
    for sym, usdt in [("BTC", "BTC-USDT-SWAP"), ("ETH", "ETH-USDT-SWAP")]:
        fr = _okx("public/funding-rate", instId=usdt)[0]
        oi = _okx("public/open-interest", instId=usdt)[0]
        tk = _okx("market/ticker", instId=usdt)[0]
        out[sym] = {
            "funding_rate": float(fr["fundingRate"]),
            "funding_annual": float(fr["fundingRate"]) * 3 * 365,  # 8h 计息
            "oi_usd": float(oi["oiUsd"]),
            "oi_ccy": float(oi["oiCcy"]),
            "last": float(tk["last"]),
            "ts": int(tk["ts"]),
        }
    return out


def fetch_taker_ratio() -> dict:
    """OKX taker 买卖成交额（7 日明细，→ 多空成交比代理；T+1 日更新）。"""
    out = {}
    for ccy in ("BTC", "ETH"):
        rows = _okx(
            "rubik/stat/taker-volume", ccy=ccy, instType="CONTRACTS", period="1D"
        )
        out[ccy] = [
            {
                "date": datetime.fromtimestamp(
                    int(r[0]) / 1000, tz=timezone.utc
                ).strftime("%Y-%m-%d"),
                "buy": float(r[1]),
                "sell": float(r[2]),
            }
            for r in rows[:10]
        ]
    return out


def _parse_exp(exp: str) -> datetime | None:
    """Deribit 到期段 DDMYY → 日期；解析失败返回 None（如未知格式）。"""
    try:
        return datetime.strptime(exp, "%d%b%y")
    except ValueError:
        return None


def _wall(
    df: pd.DataFrame, right: str
) -> tuple[float | None, float | None, list[dict]]:
    """期权墙：df 内 right 侧按行权价聚 OI，返回 (墙 strike, 墙 OI, OI 前 5)。"""
    sub = df[df["right"] == right]
    if sub.empty:
        return None, None, []
    by_k = sub.groupby("strike")["oi"].sum().sort_values(ascending=False)
    return (
        float(by_k.index[0]),
        float(by_k.iloc[0]),
        [{"strike": float(s), "oi": float(o)} for s, o in by_k.head(5).items()],
    )


def _max_pain(df: pd.DataFrame) -> float | None:
    """Max Pain（到期时卖方损失最小价）：df 需含 strike/right/oi 列。
    min_K Σ [call OI×max(0,K−K_i) + put OI×max(0,K_i−K)]
    """
    if df.empty:
        return None
    ks = sorted(df["strike"].unique())
    call_by_k = df[df["right"] == "C"].groupby("strike")["oi"].sum()
    put_by_k = df[df["right"] == "P"].groupby("strike")["oi"].sum()
    best_k, best_v = None, float("inf")
    for k in ks:
        v = sum(oi * max(0.0, k - ki) for ki, oi in call_by_k.items()) + sum(
            oi * max(0.0, ki - k) for ki, oi in put_by_k.items()
        )
        if v < best_v:
            best_v, best_k = v, k
    return float(best_k) if best_k is not None else None


def fetch_options(currency: str = "BTC") -> dict:
    """Deribit options book summary（全到期）；返回按行权价/到期聚合。"""
    rows = _deribit("get_book_summary_by_currency", currency=currency, kind="option")
    if not isinstance(rows, list):
        return {}
    parsed = []
    for r in rows:
        name = r.get("instrument_name", "")  # BTC-25DEC26-104000-C
        parts = name.split("-")
        if len(parts) != 4:
            continue
        _sym, exp, strike, right = parts
        parsed.append(
            {
                "expiration": exp,
                "strike": float(strike),
                "right": right,
                "oi": float(r.get("open_interest", 0) or 0),
                "volume": float(r.get("volume", 0) or 0),
                "underlying": float(r.get("underlying_price", 0) or 0),
            }
        )
    if not parsed:
        return {}
    df = pd.DataFrame(parsed)
    spot = float(df["underlying"].iloc[-1]) or 0

    # 期权墙：call/put 分别按行权价聚 OI
    by_strike = df.groupby(["strike", "right"]).agg(oi=("oi", "sum"))
    call = (
        by_strike.xs("C", level="right")
        if "C" in by_strike.index.get_level_values(1)
        else pd.DataFrame()
    )
    put = (
        by_strike.xs("P", level="right")
        if "P" in by_strike.index.get_level_values(1)
        else pd.DataFrame()
    )
    call_wall = float(call["oi"].idxmax()) if not call.empty else None
    put_wall = float(put["oi"].idxmax()) if not put.empty else None

    # PCR（OI）
    call_oi = float(df[df["right"] == "C"]["oi"].sum())
    put_oi = float(df[df["right"] == "P"]["oi"].sum())
    pcr = put_oi / call_oi if call_oi else None

    pain = _max_pain(df)

    # 最近到期月度口径（timsun Strike Wall 模块）：按 DDMYY 到期段解析取最早
    df["_exp_date"] = df["expiration"].map(_parse_exp)
    near = df.dropna(subset=["_exp_date"])
    nearest_exp, near_call_wall, near_put_wall = None, None, None
    near_call_wall_oi = near_put_wall_oi = None
    top_calls: list[dict] = []
    top_puts: list[dict] = []
    near_max_pain = None
    if not near.empty:
        min_d = near["_exp_date"].min()
        nearest_exp = min_d.strftime("%Y-%m-%d")
        near = near[near["_exp_date"] == min_d]
        near_call_wall, near_call_wall_oi, top_calls = _wall(near, "C")
        near_put_wall, near_put_wall_oi, top_puts = _wall(near, "P")
        near_max_pain = _max_pain(near)

    return {
        "spot_anchor": round(spot, 1),
        "call_wall": call_wall,
        "put_wall": put_wall,
        "pcr": round(pcr, 2) if pcr else None,
        "max_pain": pain,
        "total_oi": round(call_oi + put_oi, 0),
        "d_exp": int(df["expiration"].nunique()),
        "nearest_exp": nearest_exp,
        "near_call_wall": near_call_wall,
        "near_call_wall_oi": near_call_wall_oi,
        "near_put_wall": near_put_wall,
        "near_put_wall_oi": near_put_wall_oi,
        "near_max_pain": near_max_pain,
        "top_calls": top_calls,
        "top_puts": top_puts,
    }


def _yahoo_quote_oi() -> dict:
    """Yahoo v7/finance/quote 免费 OI（crumb 两步匿名流程；无 scope）。

    返回 {fut_oi: BTC=F 近月 OI, micro_oi: MBT=F OI, fut_contract, micro_contract}。
    失败返回空 dict（不影响主快照）。
    """
    try:
        import requests as _rq

        s = _rq.Session()
        s.headers["User-Agent"] = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
        s.get("https://fc.yahoo.com", timeout=10)
        crumb = s.get(
            "https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=10
        ).text
        r = s.get(
            "https://query1.finance.yahoo.com/v7/finance/quote",
            params={
                "symbols": "BTC=F,MBT=F",
                "fields": "shortName,openInterest,volume",
                "crumb": crumb,
            },
            timeout=10,
        )
        r.raise_for_status()
        out: dict = {}
        for q in r.json().get("quoteResponse", {}).get("result", []):
            sym = q.get("symbol")
            oi = q.get("openInterest")
            if oi is None:
                continue
            if sym == "BTC=F":
                out["fut_oi"] = int(oi)
                out["fut_contract"] = q.get("shortName", "")
            elif sym == "MBT=F":
                out["micro_oi"] = int(oi)
                out["micro_contract"] = q.get("shortName", "")
        return out
    except Exception as e:
        logger.warning("Yahoo quote OI: %s", e)
        return {}


def fetch_funding_history(hours: int = 24 * 365) -> list[float]:
    """OKX BTC 永续资金费率历史（8h 一条，降序→已翻转为升序返回）。

    供 LAYER 1 KPI 百分位/1d 变化使用；失败返回 []（KPI 降级"历史不足"）。
    注意：OKX 接口上限近 3 个月（实测 ~291 条），"1 年"口径实际为 3 个月，
    百分位标签按实际条数显示；返回按 fundingTime 降序（最新在前），此处翻转为升序。
    """
    try:
        n = hours // 8 + 1
        rows: list[tuple[int, float]] = []
        seen: set[int] = set()
        after: str | None = None
        prev_after: str | None = None
        while len(rows) < n:
            params: dict = {"instId": "BTC-USDT-SWAP", "limit": "100"}
            if after:
                params["after"] = after
            page = _okx("public/funding-rate-history", **params)
            if not page:
                break
            new = 0
            for r in page:
                t = int(r["fundingTime"])
                if t not in seen:
                    seen.add(t)
                    rows.append((t, float(r["fundingRate"])))
                    new += 1
            after = str(page[-1]["fundingTime"])
            if new == 0 or after == prev_after:
                break  # 翻页无新数据（防死循环）
            prev_after = after
        # 按时间升序（旧→新），去重
        rows.sort(key=lambda x: x[0])
        return [r * 100 for _, r in rows]
    except Exception as e:
        logger.warning("OKX funding history: %s", e)
        return []


def fetch_cme_basis() -> dict | None:
    """CME 比特币期货（yfinance BTC=F）vs Deribit 现货指数 → 基差 %；
    附带 Yahoo quote 免费 OI（BTC=F 近月 + MBT=F Micro）。"""
    try:
        from src.fetchers.yfinance_fetcher import ensure_yf_proxy, fetch_ohlcv

        ensure_yf_proxy()
        fut = fetch_ohlcv("BTC=F", period="3mo")
        if fut.empty:
            return None
        idx = _deribit("get_index_price", index_name="btc_usd")
        spot = float(idx.get("index_price", 0))
        last = float(fut["close"].iloc[-1])
        if not spot:
            return None
        out = {
            "fut_price": round(last, 1),
            "spot": round(spot, 1),
            "basis_pct": round((last / spot - 1) * 100, 2),
            "as_of": str(fut.index[-1].date()),
        }
        out.update(_yahoo_quote_oi())
        return out
    except Exception as e:
        logger.warning("CME basis: %s", e)
        return None


def main() -> None:
    snap: dict = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "title": "加密衍生品快照",
    }
    try:
        snap["perp"] = fetch_btc_perp()
    except Exception as e:
        logger.warning("perp: %s", e)
    try:
        snap["taker"] = fetch_taker_ratio()
    except Exception as e:
        logger.warning("taker: %s", e)
    for ccy in ("BTC", "ETH"):
        try:
            snap[f"options_{ccy}"] = fetch_options(ccy)
        except Exception as e:
            logger.warning(f"options {ccy}: %s", e)
    try:
        snap["cme"] = fetch_cme_basis()
    except Exception as e:
        logger.warning("cme: %s", e)
    try:
        hist = fetch_funding_history()
        if hist:
            snap["funding_hist"] = hist  # 升序（旧→新），% 单位
    except Exception as e:
        logger.warning("funding_hist: %s", e)

    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / f"{datetime.now():%Y%m%d}.json"
    path.write_text(
        json.dumps(snap, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    logger.info(f"快照 → {path}: {list(snap.keys())}")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    main()
