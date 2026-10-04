"""CME BTC 期货仓位快照（官方 Settlements API，衍生日页 CME 机构卡数据源）。

端点（CmeWS JSON，2026-09 实测可用）：
  /CmeWS/mvc/Settlements/Futures/Settlements/8478/FUT?tradeDate=MM/DD/YYYY
  8478 = Bitcoin Futures（经 ProductSlate API 核实）。

背景：原 cme_options_fetcher（volume/options 页面经 Jina）因页面产品选择器需
JS 交互、Jina 无点击能力而长期回落到默认产品表（小麦/ES 量级），已于 2026-09
废弃。本 fetcher 改走 Settlements JSON——过 TLS 指纹检测（curl_cffi）即可直连，
Jina Reader 作降级通道，两头都不依赖页面交互。

内容：全部挂牌月份的 settle/volume/openInterest → 总 OI、主力/次主力、
期限结构（contango/backwardation 判定 + 主力→次主力年化基差）。

写入 data/cme_futures/{YYYYMMDD}.json（抓取日命名，覆盖写；as_of = tradeDate）。

用法:
    uv run python -m src.fetchers.cme_futures_fetcher
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import pandas as pd

from src.config import ROOT

logger = logging.getLogger(__name__)

PRODUCT_ID = 8478  # Bitcoin Futures（ProductSlate 核实；9024 = Micro 不取）
API = (
    "https://www.cmegroup.com/CmeWS/mvc/Settlements/Futures/Settlements/"
    f"{PRODUCT_ID}/FUT?tradeDate={{}}&pageSize=500&isProtected&_t={{}}"
)
DATA = ROOT / "data" / "cme_futures"

_MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "JAN",
            "FEB",
            "MAR",
            "APR",
            "MAY",
            "JUN",
            "JUL",
            "AUG",
            "SEP",
            "OCT",
            "NOV",
            "DEC",
        ],
        start=1,
    )
}


def _get(url: str) -> dict:
    """curl_cffi（TLS 指纹）直连，失败降级 Jina Reader。"""
    proxies = (
        {"https": os.environ["HTTPS_PROXY"]} if os.environ.get("HTTPS_PROXY") else None
    )
    try:
        from curl_cffi import requests as cr

        r = cr.Session(impersonate="chrome").get(url, timeout=30, proxies=proxies)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning("curl_cffi 直连失败（%s），降级 Jina", str(e)[:80])
        from src.fetchers.jina_reader import jina_fetch

        return json.loads(jina_fetch(url, timeout=60))


def _to_float(v: str | None) -> float | None:
    if not v:
        return None
    v = v.replace(",", "").rstrip("AB")  # CME 价格带 A/B（ask/bid）后缀
    try:
        return float(v)
    except ValueError:
        return None


def _expiry(month_label: str) -> str | None:
    """'SEP 26' → 合约到期日（BTC 期货最后交易日 = 合约月最后一个周五）。"""
    m = re.match(r"^([A-Z]{3})\s+(\d{2})$", month_label or "")
    if not m or m.group(1) not in _MONTHS:
        return None
    year, mon = 2000 + int(m.group(2)), _MONTHS[m.group(1)]
    # 月末最后一天
    d = datetime(year + (mon == 12), mon % 12 + 1, 1) - timedelta(days=1)
    while d.weekday() != 4:  # 回退到周五
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def _fetch_trade_date(trade_date: datetime) -> list[dict]:
    """拉某交易日的结算表 → [{month, expiry, settle, volume, oi}]，空返回 []。"""
    url = API.format(trade_date.strftime("%m/%d/%Y"), int(trade_date.timestamp()))
    rows = []
    for s in _get(url).get("settlements") or []:
        settle = _to_float(s.get("settle"))
        oi = _to_float(s.get("openInterest"))
        exp = _expiry(s.get("month"))
        if settle is None or oi is None or exp is None:
            continue
        rows.append(
            {
                "month": s.get("month"),
                "expiry": exp,
                "settle": settle,
                "volume": int(_to_float(s.get("volume")) or 0),
                "oi": int(oi),
            }
        )
    rows.sort(key=lambda r: r["expiry"])
    return rows


def _btc_spot() -> float | None:
    """BTC 现价锚（yfinance 资产日线，与全站价格面板同源）；取不到返回 None。"""
    path = ROOT / "data" / "yfinance" / "asset_prices.csv"
    if not path.exists():
        return None
    try:
        s = pd.read_csv(path)["BTC"].dropna()
        return float(s.iloc[-1]) if len(s) else None
    except Exception:
        return None


def snapshot_plausible(snap: dict, spot: float | None) -> bool:
    """快照合理性校验：total_oi > 0 且主力结算价在 [0.3x, 3x] BTC 现价区间。"""
    if not snap.get("total_oi") or not spot:
        return False
    front = (snap.get("front") or {}).get("settle")
    return front is not None and 0.3 * spot <= front <= 3 * spot


def build_snapshot(rows: list[dict], trade_date: datetime) -> dict:
    """结算行 → 快照 dict（总 OI、主力/次主力、期限结构、年化基差）。"""
    total_oi = sum(r["oi"] for r in rows)
    front, nxt = rows[0], rows[1] if len(rows) > 1 else None
    basis_ann = None
    term = None
    if front and nxt and front["settle"] > 0:
        d0 = datetime.fromisoformat(front["expiry"])
        d1 = datetime.fromisoformat(nxt["expiry"])
        days = (d1 - d0).days
        if days > 0:
            basis_ann = round(
                (nxt["settle"] / front["settle"] - 1) * 365 / days * 100, 2
            )
            if basis_ann > 0.5:
                term = "contango"
            elif basis_ann < -0.5:
                term = "backwardation"
            else:
                term = "flat"
    return {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "title": "CME BTC 期货仓位",
        "as_of": trade_date.strftime("%Y-%m-%d"),
        "months": rows,
        "total_oi": total_oi,
        "total_oi_btc": total_oi * 5,  # 标准合约 5 BTC/张
        "front": front,
        "next": nxt,
        "basis_ann_pct": basis_ann,
        "term": term,
        "source": "CME Settlements API（官方每日结算）",
    }


def fetch_snapshot() -> dict:
    """从最近有结算数据的工作日拉取快照（周末/假日向前回退，最多 7 天）。"""
    today = datetime.now(timezone.utc)
    for back in range(7):
        td = today - timedelta(days=back)
        try:
            rows = _fetch_trade_date(td)
        except Exception as e:
            logger.warning("CME settlements 拉取失败（%s）：%s", td.date(), str(e)[:80])
            continue
        if rows:
            snap = build_snapshot(rows, td)
            if not snapshot_plausible(snap, _btc_spot()):
                logger.error(
                    "CME 期货快照未通过合理性校验（total_oi=%s front=%s），拒绝落盘",
                    snap.get("total_oi"),
                    (snap.get("front") or {}).get("settle"),
                )
                return {}
            return snap
    logger.error("近 7 天无 CME 结算数据")
    return {}


def main() -> None:
    snap = fetch_snapshot()
    if not snap:
        return
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / f"{datetime.now():%Y%m%d}.json"
    path.write_text(
        json.dumps(snap, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    logger.info(
        "快照 → %s（as_of %s，%d 个月份，总 OI %d 张 ≈ %d BTC，%s）",
        path,
        snap["as_of"],
        len(snap["months"]),
        snap["total_oi"],
        snap["total_oi_btc"],
        snap["term"],
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    main()
