"""Fetch China 10Y/30Y government bond yields from chinamoney real-time curve.

写入 data/fred/rates/cgb.csv（观测日为 key，upsert）。

数据源：中国货币网（chinamoney.com.cn）债券实时收益率曲线（官方、免费、无 key）
- POST /ags/ms/cm-u-bk-currency/RtimeYldCurv?lang=CN（UA + Referer 即可）
- 返回国债到期收益率全期限（0.1~50Y），每行三档：报买 / 均值 / 报卖
- 每交易日 9:30 首条、每分钟更新至 17:00；周末/节假日无新点（upsert 保留旧值）
- ⚠️ 该站 TLS 为老式 renegotiation（Python 3.13 默认拒绝）→ 复用 cfets 的
  _LegacyTLSAdapter 直连，不走 .env 的 socks5 代理（trust_env=False）

FRED 无中国国债收益率系列（10Y/30Y 均无），yield-curve 页 global_long_end
对照（美/日/中）依赖本 fetcher 产出的 cgb_10y / cgb_30y 两列（单位 %）。
"""

import argparse
import logging

import pandas as pd
import requests

from .cfets_fetcher import _LegacyTLSAdapter  # 同源站点复用 TLS 适配器

logger = logging.getLogger(__name__)

URL = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-currency/RtimeYldCurv?lang=CN"
TENORS = {10.0: "cgb_10y", 30.0: "cgb_30y"}  # 标准期限 → 输出列名


def fetch_cgb_curves() -> pd.DataFrame:
    """拉取当日国债实时曲线，返回单行宽表 {cgb_10y, cgb_30y}，index=曲线日期。"""
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.chinamoney.com.cn/chinese/bkcurvrty/",
        }
    )
    session.trust_env = False  # 直连，绕过 .env socks5 代理
    session.mount("https://", _LegacyTLSAdapter())

    resp = session.post(URL, timeout=30)
    resp.raise_for_status()
    payload = resp.json()

    # data.data: 每行 8 列 [买, 卖, 均?, 待偿期, 标准期限标记, 买, 均值, 卖]
    # 实测 10Y 行 = ['', '', '', '10.0', '', 1.7194, 1.7145, 1.7096] → idx6 为均值档
    rows = payload.get("data", {}).get("data", [])
    out: dict[str, float] = {}
    for r in rows:
        if len(r) > 6 and r[6]:
            col = TENORS.get(float(r[3])) if r[3] else None
            if col:
                out[col] = round(float(r[6]), 4)
    if not out:
        return pd.DataFrame()

    date = str(payload.get("data", {}).get("date", ""))[:10]
    if not date:
        date = pd.Timestamp.now().strftime("%Y-%m-%d")
    return pd.DataFrame([out], index=[date])


if __name__ == "__main__":
    from ..config import ROOT
    from ._io import upsert_timeseries

    parser = argparse.ArgumentParser(
        description="中国国债收益率（chinamoney 实时曲线）"
    )
    parser.add_argument("--backfill", action="store_true", help="全量覆盖（清旧格式）")
    args = parser.parse_args()

    path = ROOT / "data" / "fred" / "rates" / "cgb.csv"
    df = fetch_cgb_curves()
    upsert_timeseries(path, df, backfill=args.backfill)
    if not df.empty:
        latest = df.iloc[0].to_dict()
        print(f"CGB → {path} (观测日 {df.index[0]}, {latest})")
