"""C7: fetcher 模块 import 零副作用测试。

logging.basicConfig 幂等（root 已有 handler 时 no-op）导致 reload 方案
测不出模块级 basicConfig —— 必须 subprocess 隔离真·首次 import：
- logging.root.handlers 数量不变（无模块级 basicConfig）
- HTTPS_PROXY / HTTP_PROXY 不被写入（无模块级 env setdefault）

编排：先 import src.config（正常链路，可能经 .env 写入代理 env），
再清掉代理键，随后 import fetcher —— 保证写入者只能是 fetcher 本身。
"""

import importlib
import subprocess
import sys

import pandas as pd

_IMPORT_ALL = "\n".join(
    f"import {m}"
    for m in [
        "src.fetchers.commodities_fetcher",
        "src.fetchers.index_fetcher",
        "src.fetchers.options_fetcher",
        "src.fetchers.stock_fetcher",
        "src.fetchers.yfinance_fetcher",
    ]
)


def test_fetcher_import_no_logging_or_env_side_effect():
    code = f"""
import logging, os
import src.config
os.environ.pop("HTTPS_PROXY", None); os.environ.pop("HTTP_PROXY", None)
before = len(logging.root.handlers)
{_IMPORT_ALL}
after = len(logging.root.handlers)
assert after == before, f"logging handlers {{before}} -> {{after}}"
assert "HTTPS_PROXY" not in os.environ and "HTTP_PROXY" not in os.environ, \
    "fetcher import 写入了代理 env"
print("CLEAN")
"""
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, f"exit={r.returncode}\n{r.stderr}"
    assert "CLEAN" in r.stdout


def test_commodities_no_frozen_config_constants():
    """import 时冻结 config.ibkr.* 的 4 个模块级常量已删除。"""
    mod = importlib.import_module("src.fetchers.commodities_fetcher")
    importlib.reload(mod)  # 确保模块级状态重跑
    for attr in ("BAR_SIZE", "DURATION", "WHAT_TO_SHOW", "USE_RTH"):
        assert not hasattr(mod, attr), f"{attr} 仍被模块级冻结"


def test_yf_functions_call_ensure_proxy(monkeypatch):
    """C7 回归：删模块级 setdefault 后，yfinance 三个 fetch 函数自调
    ensure_yf_proxy()（幂等），覆盖 ibkr_fetcher 等不显式调用的调用方。
    """
    import yfinance

    calls = []
    monkeypatch.setattr(
        "src.fetchers.yfinance_fetcher.ensure_yf_proxy", lambda: calls.append(1)
    )

    class _Fake:
        def history(self, **kw):
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

    monkeypatch.setattr(yfinance, "Ticker", lambda ticker: _Fake())

    from src.fetchers.yfinance_fetcher import _fetch_ticker, fetch_ohlcv, yf_minute_bars

    fetch_ohlcv("X", "5d")
    yf_minute_bars("X", "5m")
    _fetch_ticker("X", "x")
    assert len(calls) == 3, f"ensure_yf_proxy 应被 3 个函数各调一次，实际 {calls}"
