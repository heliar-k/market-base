"""CFETS 掉期点 fetcher 单元测试：voArray 解析 + 期限过滤。"""

from src.fetchers.cfets_fetcher import PAIRS, TENORS, fetch_swap_points


def _rec(tenor, points):
    return {"tenor": tenor, "points": points, "swapAllPrc": "1.15"}


class _FakeSession:
    """模拟 requests.Session：post 按 URL 中的货币对返回对应 payload。"""

    headers: dict = {}

    def __init__(self, payloads):
        self._payloads = payloads

    def mount(self, *args):
        pass

    def post(self, url, *a, **k):
        pair = next(p for p in PAIRS if p in url)
        payload = self._payloads[pair]
        return type(
            "R",
            (),
            {
                "json": lambda _self, p=payload: p,
                "raise_for_status": lambda _self: None,
            },
        )()


def test_parses_swap_points_by_tenor(monkeypatch):
    """只有 1W/1M/3M/6M/1Y 五档被保留，列名 {PAIR}_{TENOR}。"""
    payloads = {
        "EUR.USD": {
            "data": {
                "voArray": [_rec("ON", 1.84), _rec("1M", 14.13), _rec("1Y", 168.64)]
            }
        },
        "USD.JPY": {"data": {"voArray": [_rec("3M", -116.96)]}},
        "GBP.USD": {"data": {"voArray": []}},
        "AUD.USD": {"data": {"voArray": []}},
        "USD.HKD": {"data": {"voArray": []}},
    }
    monkeypatch.setattr(
        "src.fetchers.cfets_fetcher.requests.Session", lambda: _FakeSession(payloads)
    )

    df = fetch_swap_points()

    assert df.shape == (1, 3)
    assert set(df.columns) == {"EURUSD_1M", "EURUSD_1Y", "USDJPY_3M"}
    assert df.iloc[0]["EURUSD_1M"] == 14.13
    assert df.iloc[0]["USDJPY_3M"] == -116.96
    assert TENORS == ["1W", "1M", "3M", "6M", "1Y"]


def test_empty_when_no_pairs(monkeypatch):
    """全部货币对无数据时返回空 DataFrame，不写文件。"""
    payloads = {p: {"data": {"voArray": []}} for p in PAIRS}
    monkeypatch.setattr(
        "src.fetchers.cfets_fetcher.requests.Session", lambda: _FakeSession(payloads)
    )

    assert fetch_swap_points().empty
