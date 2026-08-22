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

    # Max Pain（到期时卖方损失最小价）：
    # min_K Σ [call OI×max(0,K−K_i) + put OI×max(0,K_i−K)]
    pain = None
    if not df.empty:
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
        pain = float(best_k) if best_k is not None else None

    return {
        "spot_anchor": round(spot, 1),
        "call_wall": call_wall,
        "put_wall": put_wall,
        "pcr": round(pcr, 2) if pcr else None,
        "max_pain": pain,
        "total_oi": round(call_oi + put_oi, 0),
        "d_exp": int(df["expiration"].nunique()),
    }


def fetch_cme_basis() -> dict | None:
    """CME 比特币期货（yfinance BTC=F）vs Deribit 现货指数 → 基差 %。"""
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
        return {
            "fut_price": round(last, 1),
            "spot": round(spot, 1),
            "basis_pct": round((last / spot - 1) * 100, 2),
            "as_of": str(fut.index[-1].date()),
        }
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
