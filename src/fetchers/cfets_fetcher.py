"""Fetch CFETS FX swap curve (swap points) from chinamoney.org.cn.

写入 data/fred/liquidity/cfets_swap_points.csv（观测日为 key，upsert）。

数据源: 中国外汇交易中心 CFETS 外汇掉期曲线（官方、免费、无 key）
- C-Swap 定盘曲线（USD.CNY）: /r/cms/www/chinamoney/data/fx/fx-c-sw-curv-USD.CNY.json
- 外汇掉期曲线（外币对）:   /r/cms/www/chinamoney/data/fx/fx-sw-curv-{PAIR}.json
  覆盖 EUR.USD / USD.JPY / GBP.USD / AUD.USD / USD.HKD（每日 16:30 发布，17:00 可查）

本 fetcher 取 timsun global-dollar 页面同款期限 1W/1M/3M/6M/1Y，
列名 {PAIR}_{TENOR}（如 USDJPY_3M），单位 = 掉期点（pips，1 pip = 0.0001）。
USD/CNH（离岸）与 USD/CHF 不在 CFETS 覆盖范围（页面亦标注"暂未覆盖"）。
"""

import logging
from datetime import datetime

import pandas as pd
import requests

logger = logging.getLogger(__name__)

BASE = "https://www.chinamoney.org.cn/r/cms/www/chinamoney/data/fx"
PAIRS = ["EUR.USD", "USD.JPY", "GBP.USD", "AUD.USD", "USD.HKD"]
TENORS = ["1W", "1M", "3M", "6M", "1Y"]


class _LegacyTLSAdapter(requests.adapters.HTTPAdapter):
    """chinamoney 服务器要求 legacy renegotiation，Python 3.13 默认禁用；
    用 urllib3 的 old ssl renegotiation 配置直连。"""

    def init_poolmanager(self, *args, **kwargs):
        import ssl

        ctx = ssl.create_default_context()
        ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0)
        kwargs["ssl_context"] = ctx
        super().init_poolmanager(*args, **kwargs)


def fetch_swap_points() -> pd.DataFrame:
    """拉取当日 CFETS 外汇掉期点，返回 index=[观测日] 的单行宽表。"""
    session = requests.Session()
    session.headers.update(
        {"User-Agent": "Mozilla/5.0", "Referer": "https://www.chinamoney.org.cn/"}
    )
    # chinamoney TLS 为老式 renegotiation，走 socks5 代理会握手失败；直连即可
    session.trust_env = False
    session.mount("https://", _LegacyTLSAdapter())

    points: dict[str, float] = {}
    for pair in PAIRS:
        resp = session.post(
            f"{BASE}/fx-sw-curv-{pair}.json", data={"t": "1"}, timeout=30
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        for rec in data.get("voArray", []):
            tenor = rec.get("tenor")
            if tenor in TENORS:
                points[f"{pair.replace('.', '')}_{tenor}"] = float(rec["points"])

    if not points:
        return pd.DataFrame()

    obs_date = datetime.now().strftime("%Y-%m-%d")
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
