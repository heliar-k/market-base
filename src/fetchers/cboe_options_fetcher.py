# CBOE delayed quotes 期权链：yfinance 现场链失效时的上一收盘快照保底源。
#
# 数据源 https://cdn.cboe.com/api/global/delayed_quotes/options/{SYMBOL}.json
# 一次请求返回全部到期日的全链（SPY ~1.2 万条 / 5.4MB），每条含
# open_interest / iv / bid / ask / volume，另有 last_trade_time 标注整份快照的时点。
#
# 定位：延迟 15 分钟的报价，OI 仍是 OCC 日终结算值。盘前与凌晨时段 yfinance 的
# bid/OI 整片失效（实测凌晨 SPY 近月 858 个合约 OI 全为 0），CBOE 同一时刻池内
# 17 个标的正 OI 57%~90%、bid>0 75%~95%，且不依赖本地 raw 文件是否存在。
#
# 路径符号用点号：BRK-B 要请求 BRK.B（带横线返回 403），其余与 yahoo 代码一致。

import logging
import time
from datetime import datetime, timezone

import pandas as pd
import requests

from src.clock import ET
from src.pricing import CHAIN_COLUMNS

logger = logging.getLogger(__name__)

_BASE = "https://cdn.cboe.com/api/global/delayed_quotes/options/"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}

# 同一进程内两次请求的最小间隔。全池 17 个标的串行拉取，单份 0.6~5.4MB；
# 大标的自身耗时已过 1s，这里是给小标的与瞬时连续调用留的间隔。
_MIN_INTERVAL_SECONDS = 1.0
# 重试退避（秒），总尝试次数 = len(_RETRY_BACKOFF) + 1。
# 4xx 不重试：403 是路径符号写错或源上已下架，重试只会拖慢。
_RETRY_BACKOFF = (2.0, 5.0)
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

_last_request_at = 0.0


def _throttle() -> None:
    """请求前的节流：距上一次请求不足 _MIN_INTERVAL_SECONDS 就等到满足。"""
    global _last_request_at
    wait = _MIN_INTERVAL_SECONDS - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def _get_chain_json(code: str) -> dict | None:
    """请求单标的全链 JSON。网络错误与 5xx/429 按 _RETRY_BACKOFF 重试，其余直接放弃。"""
    url = f"{_BASE}{code}.json"
    attempts = len(_RETRY_BACKOFF) + 1
    for attempt in range(attempts):
        _throttle()
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=40)
        except requests.RequestException as e:
            reason = f"{type(e).__name__}: {e}"
            if attempt + 1 >= attempts:
                logger.warning(f"CBOE {code} 请求失败（已尝试 {attempts} 次）：{reason}")
                return None
            wait = _RETRY_BACKOFF[attempt]
            logger.warning(f"CBOE {code} 请求失败（第 {attempt + 1} 次），{wait}s 后重试：{reason}")
            time.sleep(wait)
            continue
        if resp.status_code in _RETRY_STATUS and attempt + 1 < attempts:
            wait = _RETRY_BACKOFF[attempt]
            logger.warning(
                f"CBOE {code} HTTP {resp.status_code}（第 {attempt + 1} 次），{wait}s 后重试"
            )
            time.sleep(wait)
            continue
        if resp.status_code != 200:
            logger.warning(f"CBOE {code} HTTP {resp.status_code}，不重试")
            return None
        try:
            return resp.json()
        except ValueError as e:
            logger.warning(f"CBOE {code} 响应非 JSON：{e}")
            return None
    return None


def _osi_to_parts(osi: str) -> tuple[str, float, str] | None:
    """OSI 合约符号 → (expiration %Y%m%d, strike, right)，如 NVDA261030C00210000。"""
    body = osi[-15:]
    if len(body) != 15 or body[6] not in ("C", "P"):
        return None
    ymd, strike = body[:6], body[7:]
    if not ymd.isdigit() or not strike.isdigit():
        return None
    return f"20{ymd}", int(strike) / 1000.0, body[6]


def _quote_stamp(raw: str | None) -> tuple[str | None, str | None]:
    """last_trade_time（美东本地时间，无时区后缀）→ (美东日期, UTC ISO 时点)。"""
    if not raw:
        return None, None
    try:
        dt = datetime.fromisoformat(raw).replace(tzinfo=ET)
    except ValueError:
        return None, None
    return dt.date().isoformat(), dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_cboe_chain(
    symbol: str, expirations: list[str] | None = None
) -> tuple[pd.DataFrame, str] | None:
    """拉 CBOE 全链，返回 (链, 报价交易日)。源不可用时返回 None。

    `symbol` 用池里的 opt_symbol（GOOGL / BRK.B）。`expirations` 给出时只保留这些
    到期日：实时模式按 DTE 窗口过滤，避免上万条合约全进 compute_structure；
    传空列表视为不做窗口过滤。
    """
    code = symbol.replace("-", ".")
    payload = _get_chain_json(code)
    if payload is None:
        return None
    data = payload.get("data") or {}

    opts = data.get("options") or []
    if not opts:
        logger.warning(f"CBOE {code} 期权链为空")
        return None
    # 缺报价时点就无法标注这份快照属于哪个交易日，按不可用处理
    chain_date, quote_ts = _quote_stamp(data.get("last_trade_time"))
    if chain_date is None:
        logger.warning(f"CBOE {code} 缺 last_trade_time，无法标注报价时点")
        return None

    want = set(expirations) if expirations else None
    rows = []
    for o in opts:
        parts = _osi_to_parts(o.get("option") or "")
        if parts is None:
            continue
        exp, strike, right = parts
        if want is not None and exp not in want:
            continue
        # OI 缺失归一为 0（与 fetch_yf_chain 一致）：下游 compute_structure 用 int(oi)
        # 取值，NaN 会直接抛 ValueError
        oi = o.get("open_interest")
        rows.append(
            {
                "expiration": exp,
                "strike": strike,
                "right": right,
                "bid": o.get("bid"),
                "ask": o.get("ask"),
                "last": o.get("last_trade_price"),
                "openInterest": int(oi) if oi is not None else 0,
                "impliedVolatility": o.get("iv"),
                "volume": o.get("volume"),
                "quote_ts": quote_ts,
            }
        )
    if not rows:
        logger.warning(f"CBOE {code} 窗口内无合约")
        return None
    df = pd.DataFrame(rows, columns=CHAIN_COLUMNS + ["quote_ts"])
    logger.info(f"CBOE {code}: {len(df)} 条合约（快照 {chain_date}）")
    return df, chain_date
