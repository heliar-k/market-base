"""Fetch options chain with greeks from Barchart（免费匿名，IBKR 降级源）。

compute_gex 降级链：IBKR → Barchart（真实市场 gamma + OI 同源）→ yfinance BS 反推。

数据源: core-api v1/options/chain?symbol={SYM}&expirationDate={DATE}&fields=...&raw=1
认证: 页面 <meta name="csrf-token"> → X-CSRF-TOKEN header（与外汇/期货的 X-XSRF 不同）。
⚠️ 延迟报价；4 个到期日约 3 次请求，勿高频调用。
"""

import logging

import pandas as pd

from .barchart_client import core_get, to_float

logger = logging.getLogger(__name__)

FIELDS = (
    "strikePrice,lastPrice,volume,openInterest,volatility,"
    "gamma,expirationDate,optionType"
)


def _exp_to_yyyymmdd(exp: str) -> str:
    """'08/21/26' 或 '2026-08-21' → '20260821'（compute_gex 的到期日格式）。"""
    if not exp or len(exp) < 8:
        return ""
    if "/" in exp:
        mm, dd, yy = exp.split("/")
        return f"20{yy}{mm}{dd}"
    return exp.replace("-", "")


def _chain_get(symbol: str, params: dict) -> dict:
    """调 options/chain：GET 页面取 meta csrf-token → X-CSRF-TOKEN header。

    options/chain 与外汇/期货的 quotes/get 认证不同（meta csrf-token 而非
    cookie XSRF-TOKEN），经 core_get(auth="csrf") 复用同一会话流程。
    """
    return core_get(
        params,
        referer=f"https://www.barchart.com/stocks/quotes/{symbol}/options",
        auth="csrf",
        endpoint="options/chain",
    )


def fetch_barchart_chain(symbol: str, expirations: list[str]) -> pd.DataFrame:
    """拉取指定到期日的 Barchart 期权链（全 strike，call + put）。

    Args:
        symbol: 标的代码（如 AAPL）
        expirations: 到期日列表，格式 YYYYMMDD（与 compute_gex 一致）
    Returns:
        DataFrame: expiration(YYYYMMDD), strike, right(C/P), gamma（put 翻转，与
        项目惯例 call 正/put 负一致）, iv（小数）, openInterest, volume。
        全部失败返回空 DataFrame。
    """
    rows: list[dict] = []
    for exp in expirations:
        exp_mmdd = f"{exp[0:4]}-{exp[4:6]}-{exp[6:8]}"  # YYYYMMDD → YYYY-MM-DD
        try:
            data = _chain_get(
                symbol,
                {
                    "symbol": symbol,
                    "expirationDate": exp_mmdd,
                    "fields": FIELDS,
                    "raw": "1",
                },
            )
        except Exception as e:
            logger.warning("Barchart %s %s 期权链拉取失败: %s", symbol, exp, e)
            continue
        n_before = len(rows)
        for rec in data.get("data", []):
            right = rec.get("optionType")
            gamma = to_float(rec.get("gamma"))
            iv = to_float(rec.get("volatility"))
            strike = to_float(rec.get("strikePrice"))
            oi = to_float(rec.get("openInterest"))
            if not right or gamma is None or iv is None or strike is None:
                continue
            rows.append(
                {
                    "expiration": _exp_to_yyyymmdd(rec.get("expirationDate", "")),
                    "strike": strike,
                    "right": "C" if right == "Call" else "P",
                    # Barchart put gamma 为正，翻转成项目惯例（call 正/put 负）
                    "gamma": gamma if right == "Call" else -gamma,
                    "iv": iv,
                    "openInterest": oi or 0,
                    "volume": to_float(rec.get("volume")) or 0,
                }
            )
        logger.info("Barchart %s %s: %d 条合约", symbol, exp_mmdd, len(rows) - n_before)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="Barchart 期权链拉取（验证用）")
    parser.add_argument("symbol")
    parser.add_argument("--expirations", default="", help="YYYYMMDD 逗号分隔")
    args = parser.parse_args()

    exps = [e.strip() for e in args.expirations.split(",") if e.strip()]
    if not exps:
        import yfinance as yf  # noqa: E402

        from .yfinance_fetcher import ensure_yf_proxy

        ensure_yf_proxy()
        ticker = yf.Ticker(args.symbol)
        exps = [e.replace("-", "") for e in ticker.options[:2]]

    df = fetch_barchart_chain(args.symbol, exps)
    if df.empty:
        print("无数据")
        sys.exit(1)
    print(f"{len(df)} 条: 到期 {sorted(df['expiration'].unique())}")
    print(df.head(3).to_string(index=False))
