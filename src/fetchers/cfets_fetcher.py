"""Fetch FX swap points from CFETS (chinamoney) + Barchart + Yahoo CME futures.

写入 data/fred/liquidity/cfets_swap_points.csv（观测日为 key，upsert）。

数据源 1: 中国外汇交易中心 CFETS 外汇掉期曲线（官方、免费、无 key）
- 外汇掉期曲线（外币对）: /r/cms/www/chinamoney/data/fx/fx-sw-curv-{PAIR}.json
  覆盖 EUR.USD / USD.JPY / GBP.USD / AUD.USD / USD.HKD（每日 16:30 发布，17:00 可查）

数据源 2: Barchart 远期点曲线（USDCNH / USDCHF 全期限，免费匿名，无 key）
- CFETS 不覆盖这两个货币对，但 Barchart 的 forward-rates 页面有完整曲线：
  ON/TN/SN/1W/2W/3W/1M~11M/1Y/2Y/3Y（4Y+ 常为 N/A）。
- 两步匿名请求：GET 页面种 cookie（laravel_session/XSRF-TOKEN）→ 带 X-XSRF-TOKEN
  调 core-api `quotes/get?lists=forex.forwardCurves(^PAIR)`，取 bid/ask 中值。
  ⚠️ 数据质量标注：① 延迟报价（非实时）；② 非官方清算曲线，仅市场报价；
  ③ 4Y+ 期限可能 N/A（自动跳过）；④ 页面依赖的 core-api 若改版需同步更新。

数据源 3: Yahoo Finance chart API（CME 期货主连）——仅作 Barchart 失败时的降级
- 掉期点 = (F − S) × 10000，F = 主连期货价（约 1M 到期），S = 即期。仅近月单点。

列名 {PAIR}_{TENOR}（如 USDJPY_3M、USDCNH_1M），单位 = 掉期点（pips，1 pip = 0.0001）。
USDCNH_NEAR / USDCHF_NEAR 为近月掉期点（优先 Barchart 1M，降级 Yahoo CME）。
"""

import json
import logging
import re
import urllib.request
from dataclasses import dataclass
from datetime import datetime

import pandas as pd
import requests

from .barchart_client import core_get

logger = logging.getLogger(__name__)

BASE = "https://www.chinamoney.org.cn/r/cms/www/chinamoney/data/fx"
PAIRS = ["EUR.USD", "USD.JPY", "GBP.USD", "AUD.USD", "USD.HKD"]
TENORS = ["1W", "1M", "3M", "6M", "1Y"]

# Barchart：CFETS 不覆盖的货币对 → 全期限远期点（免费匿名）
BARCHART_PAIRS = ["USDCNH", "USDCHF"]
BARCHART_PAGE = "https://www.barchart.com/forex/quotes/%5E{PAIR}/forward-rates"
_TENOR_RE = re.compile(r"(Overnight|Tomorrow|Spot|(\d+)-(Week|Month|Year)) Forward$")

YAHOO_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=5d&interval=1d"
)


@dataclass(frozen=True)
class YahooPair:
    future: str  # CME 期货主连符号
    spot: str  # 即期符号
    column: str  # 输出列名
    direction: float  # 期货报价方向：1.0=USD/外币，-1.0=外币/USD（取倒数）


YAHOO_PAIRS = [
    YahooPair("CNH=F", "USDCNH=X", "USDCNH_NEAR", 1.0),  # 期货 USD/CNH，直接换算
    YahooPair("6S=F", "CHF=X", "USDCHF_NEAR", -1.0),  # 期货 CHF/USD，需取倒数
]


class _LegacyTLSAdapter(requests.adapters.HTTPAdapter):
    """chinamoney 服务器要求 legacy renegotiation，Python 3.13 默认禁用；
    用 urllib3 的 old ssl renegotiation 配置直连。"""

    def init_poolmanager(self, *args, **kwargs):
        import ssl

        ctx = ssl.create_default_context()
        ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0)
        kwargs["ssl_context"] = ctx
        super().init_poolmanager(*args, **kwargs)


def _tenor_from_name(symbol_name: str) -> str | None:
    """ "USD/CNH 1-Month Forward" → "1M"; 解析失败返回 None。"""
    m = _TENOR_RE.search(symbol_name)
    if not m:
        return None
    if m.group(1) == "Overnight":
        return "ON"
    if m.group(1) == "Tomorrow":
        return "TN"
    if m.group(1) == "Spot":
        return "SN"
    return f"{m.group(2)}{m.group(3)[0]}"  # 1-Week→1W, 3-Month→3M, 2-Year→2Y


def _fetch_barchart_curves(pair: str) -> dict[str, float] | None:
    """Barchart 远期点曲线 → {TENOR: mid_pips}（bid/ask 中值，N/A 跳过）。

    经 barchart_client 匿名两步请求（页面种 cookie → core-api 带 XSRF）。
    """
    page_url = BARCHART_PAGE.format(PAIR=pair)
    resp = core_get(
        {
            "lists": f"forex.forwardCurves(^{pair})",
            "fields": "symbolName,bidPrice,askPrice,tradeTime",
        },
        referer=page_url,
    )
    curves: dict[str, float] = {}
    for rec in resp.get("data", []):
        tenor = _tenor_from_name(rec.get("symbolName", ""))
        bid, ask = rec.get("bidPrice"), rec.get("askPrice")
        if not tenor or bid in (None, "N/A") or ask in (None, "N/A"):
            continue
        mid = (float(str(bid).replace(",", "")) + float(str(ask).replace(",", ""))) / 2
        curves[tenor] = round(mid, 1)
    return curves or None


def _fetch_yahoo_near(pair_cfg: YahooPair) -> float | None:
    """从 Yahoo chart API 拉主连期货 + 即期，推近月掉期点（pips）。

    走 .env 的 HTTPS_PROXY（本地）；Actions 无代理时 urllib 自动直连。
    """
    fut_sym, spot_sym, _, direction = (
        pair_cfg.future,
        pair_cfg.spot,
        pair_cfg.column,
        pair_cfg.direction,
    )

    def _get(sym: str) -> float | None:
        url = YAHOO_URL.format(sym=sym)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
        closes = [
            c
            for c in data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
            if c
        ]
        return closes[-1] if closes else None

    fut, spot = _get(fut_sym), _get(spot_sym)
    if fut is None or spot is None:
        return None
    fut_price = fut if direction > 0 else 1 / fut  # 期货统一成 USD/外币 方向
    return (fut_price - spot) * 10000


def fetch_swap_points() -> pd.DataFrame:
    """拉取当日 CFETS 外币对掉期点 + Yahoo CME 近月掉期点，返回单行宽表。"""
    session = requests.Session()
    session.headers.update(
        {"User-Agent": "Mozilla/5.0", "Referer": "https://www.chinamoney.org.cn/"}
    )
    # chinamoney TLS 为老式 renegotiation，走 socks5 代理会握手失败；直连即可
    session.trust_env = False
    session.mount("https://", _LegacyTLSAdapter())

    points: dict[str, float] = {}
    obs_dates: set[str] = set()
    for pair in PAIRS:
        resp = session.post(
            f"{BASE}/fx-sw-curv-{pair}.json", data={"t": "1"}, timeout=30
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        show_date = data.get("showDateCN") or data.get("nowDate")
        if show_date:
            obs_dates.add(str(show_date)[:10])
        for rec in data.get("voArray", []):
            tenor = rec.get("tenor")
            if tenor in TENORS:
                points[f"{pair.replace('.', '')}_{tenor}"] = float(rec["points"])

    if not points:
        return pd.DataFrame()

    # Barchart 远期点曲线（USDCNH / USDCHF 全期限，CFETS 不覆盖）；失败不影响 CFETS
    for pair in BARCHART_PAIRS:
        try:
            curves = _fetch_barchart_curves(pair)
            if curves:
                points.update({f"{pair}_{t}": v for t, v in curves.items()})
                if f"{pair}_NEAR" not in points and curves.get("1M") is not None:
                    points[f"{pair}_NEAR"] = curves["1M"]
        except Exception as e:
            logger.warning("Barchart %s 拉取失败: %s", pair, e)

    # Yahoo CME 近月（仅 Barchart 失败时兜底，避免覆盖全期限 1M）
    for cfg in YAHOO_PAIRS:
        if cfg.column in points:
            continue  # Barchart 已有近月点
        try:
            near = _fetch_yahoo_near(cfg)
            if near is not None:
                points[cfg.column] = round(near, 1)
        except Exception as e:
            logger.warning("Yahoo %s 拉取失败: %s", cfg.column, e)

    # 用 API 自带的数据日期（周末/节假日时与运行日不同），避免错位
    obs_date = (
        sorted(obs_dates)[-1] if obs_dates else datetime.now().strftime("%Y-%m-%d")
    )
    return pd.DataFrame([points], index=[obs_date])


if __name__ == "__main__":
    import argparse

    from ..config import ROOT
    from ._io import upsert_timeseries

    parser = argparse.ArgumentParser(description="CFETS 外汇掉期点拉取（chinamoney）")
    parser.add_argument("--backfill", action="store_true", help="全量覆盖（清旧格式）")
    args = parser.parse_args()

    path = ROOT / "data" / "fred" / "liquidity" / "cfets_swap_points.csv"
    df = fetch_swap_points()
    upsert_timeseries(path, df, backfill=args.backfill)
    if not df.empty:
        n_cols = len(df.columns)
        latest = df.iloc[0].to_dict()
        sample = {k: v for k, v in list(latest.items())[:3]}
        print(
            f"CFETS 掉期点 → {path} ({n_cols} 列, 观测日 {df.index[0]}, 样例 {sample})"
        )
