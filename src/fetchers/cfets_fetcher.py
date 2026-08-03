"""Fetch FX swap points from CFETS (chinamoney) + Yahoo CME futures.

写入 data/fred/liquidity/cfets_swap_points.csv（观测日为 key，upsert）。

数据源 1: 中国外汇交易中心 CFETS 外汇掉期曲线（官方、免费、无 key）
- 外汇掉期曲线（外币对）: /r/cms/www/chinamoney/data/fx/fx-sw-curv-{PAIR}.json
  覆盖 EUR.USD / USD.JPY / GBP.USD / AUD.USD / USD.HKD（每日 16:30 发布，17:00 可查）

数据源 2: Yahoo Finance chart API（CME 期货主连，真实市场报价）
- USD/CNH、USD/CHF 不在 CFETS 覆盖范围，用 CME 期货主连推近月掉期点：
  掉期点 = (F − S) × 10000，F = 主连期货价（约 1M 到期），S = 即期。
  仅近月单点（Yahoo 不提供具体月份合约）。

列名 {PAIR}_{TENOR}（如 USDJPY_3M），单位 = 掉期点（pips，1 pip = 0.0001）。
USDC NH_NEAR / USDCHF_NEAR 为 Yahoo CME 近月掉期点（来源标注 NEAR）。
"""

import json
import logging
import urllib.request
from datetime import datetime

import pandas as pd
import requests

logger = logging.getLogger(__name__)

BASE = "https://www.chinamoney.org.cn/r/cms/www/chinamoney/data/fx"
PAIRS = ["EUR.USD", "USD.JPY", "GBP.USD", "AUD.USD", "USD.HKD"]
TENORS = ["1W", "1M", "3M", "6M", "1Y"]

YAHOO_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=5d&interval=1d"
)
# (期货符号, 即期符号, 输出列名, 期货报价方向)
YAHOO_PAIRS = [
    ("CNH=F", "USDCNH=X", "USDCNH_NEAR", 1.0),  # 期货 USD/CNH，直接换算
    ("6S=F", "CHF=X", "USDCHF_NEAR", -1.0),  # 期货 CHF/USD，需取倒数
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


def _fetch_yahoo_near(pair_cfg: tuple) -> float | None:
    """从 Yahoo chart API 拉主连期货 + 即期，推近月掉期点（pips）。

    走 .env 的 HTTPS_PROXY（本地）；Actions 无代理时 urllib 自动直连。
    """
    fut_sym, spot_sym, _, direction = pair_cfg

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

    # Yahoo CME 近月（USD/CNH、USD/CHF，CFETS 不覆盖）；失败不影响 CFETS 数据
    for cfg in YAHOO_PAIRS:
        try:
            near = _fetch_yahoo_near(cfg)
            if near is not None:
                points[cfg[2]] = round(near, 1)
        except Exception as e:
            logger.warning("Yahoo %s 拉取失败: %s", cfg[2], e)

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
