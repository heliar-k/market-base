import pandas as pd
import yfinance as yf

import src.compute_gex as cg


def test_empty_ibkr_chain_falls_back_to_yfinance(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cg.sys, "argv", ["compute_gex", "--symbol", "TEST"])

    class FakeIB:
        disconnected = False

        def disconnect(self):
            self.disconnected = True

    ib = FakeIB()
    monkeypatch.setattr(cg, "connect_ib", lambda ports: (ib, None))
    monkeypatch.setattr(cg, "get_spot", lambda ib, symbol: 100.0)
    monkeypatch.setattr(cg, "get_option_chain_params", lambda ib, symbol: [])

    class Ticker:
        options = ("20991218",)
        fast_info = type("FastInfo", (), {"last_price": 100.0})()

    monkeypatch.setattr(yf, "Ticker", lambda symbol: Ticker())
    monkeypatch.setattr(
        "src.fetchers.barchart_options_fetcher.fetch_barchart_chain",
        lambda symbol, exps: pd.DataFrame(),
    )
    chain = pd.DataFrame(
        [
            {
                "expiration": "20991218",
                "strike": 100.0,
                "right": "C",
                "openInterest": 100,
                "volume": 10,
                "impliedVolatility": 0.2,
            }
        ]
    )
    monkeypatch.setattr(cg, "fetch_yf_chain", lambda symbol, exps: chain)

    cg.main()

    assert ib.disconnected
    assert list((tmp_path / "data" / "gex").glob("TEST_gex_*.csv"))
