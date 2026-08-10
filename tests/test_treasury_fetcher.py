"""Treasury fetcher 单元测试。"""

import pandas as pd
import pytest

from src.fetchers.treasury_fetcher import _endpoint_name, fetch_mspd


def mock_mspd_response():
    """构造最小 mspd_table_1 API 响应（月末 × 品种）。"""
    return {
        "data": [
            {
                "record_date": "2026-06-30",
                "security_type_desc": "Marketable",
                "security_class_desc": "Bills",
                "debt_held_public_mil_amt": "3000000",
            },
            {
                "record_date": "2026-06-30",
                "security_type_desc": "Marketable",
                "security_class_desc": "Notes",
                "debt_held_public_mil_amt": "6000000",
            },
            {
                "record_date": "2026-06-30",
                "security_type_desc": "Marketable",
                "security_class_desc": "Bonds",
                "debt_held_public_mil_amt": "1000000",
            },
            {
                "record_date": "2026-06-30",
                "security_type_desc": "Non-marketable",
                "security_class_desc": "Savings Bonds",
                "debt_held_public_mil_amt": "1000000",
            },
            {
                "record_date": "2026-05-31",
                "security_type_desc": "Marketable",
                "security_class_desc": "Bills",
                "debt_held_public_mil_amt": "2800000",
            },
            {
                "record_date": "2026-05-31",
                "security_type_desc": "Marketable",
                "security_class_desc": "Notes",
                "debt_held_public_mil_amt": "5900000",
            },
            {
                "record_date": "2026-06-30",
                "security_type_desc": "Total Public Debt Outstanding",
                "security_class_desc": "_",
                "debt_held_public_mil_amt": "30000000",
                "total_mil_amt": "38000000",
            },
            {
                "record_date": "2026-05-31",
                "security_type_desc": "Total Public Debt Outstanding",
                "security_class_desc": "_",
                "debt_held_public_mil_amt": "29000000",
                "total_mil_amt": "37000000",
            },
        ],
        "links": {"self": "...", "next": None},
    }


def _fake_get(body: dict):
    class FakeResp:
        @staticmethod
        def json():
            return body

        @staticmethod
        def raise_for_status():
            pass

    return lambda *a, **k: FakeResp()


def mock_auctions_response():
    """构造一个最小但完整的 auctions_query API 响应。"""
    return {
        "data": [
            {
                "security_type": "Note",
                "security_term": "10-Year",
                "offering_amt": "42000000000",
                "bid_to_cover_ratio": "2.50",
                "high_yield": "4.200",
                "avg_med_yield": "4.150",
                "indirect_bidder_accepted": "25000000000",
                "total_accepted": "42000000000",
                "reopening": "No",
                "cusip": "91282CXX0",
                "auction_date": "2025-01-08",
                "issue_date": "2025-01-15",
                "maturity_date": "2034-11-15",
            },
            {
                "security_type": "Bill",
                "security_term": "4-Week",
                "offering_amt": "100000000000",
                "bid_to_cover_ratio": "2.80",
                "high_yield": "3.750",
                "avg_med_yield": "3.720",
                "indirect_bidder_accepted": "55000000000",
                "total_accepted": "100000000000",
                "reopening": "Yes",
                "cusip": "912797XX0",
                "auction_date": "2025-01-06",
                "issue_date": "2025-01-09",
                "maturity_date": "2025-02-06",
            },
        ],
        "links": {"self": "...", "next": None},
    }


def mock_upcoming_response():
    """构造一个最小 upcoming_auctions API 响应。"""
    return {
        "data": [
            {
                "security_type": "Note",
                "security_term": "2-Year",
                "offering_amt": "69000000000",
                "reopening": "No",
                "cusip": "91282CYY0",
                "auction_date": "2025-02-25",
                "issue_date": "2025-02-28",
            },
        ],
        "links": {"self": "...", "next": None},
    }


class TestTreasuryFetcher:
    def test_endpoint_name(self):
        assert (
            _endpoint_name(
                "https://api.fiscaldata.treasury.gov/.../auctions_query?format=json"
            )
            == "auctions_query"
        )

    def test_fetch_auction_results_structure(self, monkeypatch):
        """验证返回 DataFrame 含预期列和派生指标。"""
        import requests

        def mock_get(url, params=None, timeout=None, **kwargs):
            resp = requests.Response()
            resp.status_code = 200

            class FakeResp:
                @staticmethod
                def json():
                    return mock_auctions_response()

                @staticmethod
                def raise_for_status():
                    pass

            return FakeResp()

        monkeypatch.setattr("requests.get", mock_get)

        from src.fetchers.treasury_fetcher import fetch_auction_results

        df = fetch_auction_results()
        assert not df.empty
        assert len(df) == 2
        assert "indirect_pct" in df.columns
        assert "tail_bp" in df.columns
        assert "bid_to_cover_ratio" in df.columns

        # 验证派生指标计算
        row = df.loc["2025-01-08"]
        assert row["indirect_pct"] == pytest.approx(59.5, abs=0.1)  # 25B/42B
        assert row["tail_bp"] == pytest.approx(5.0, abs=0.1)  # (4.200-4.150)*100

    def test_fetch_auction_results_index_is_datetime(self, monkeypatch):
        import requests

        def mock_get(url, params=None, timeout=None, **kwargs):
            resp = requests.Response()
            resp.status_code = 200

            class FakeResp:
                @staticmethod
                def json():
                    return mock_auctions_response()

                @staticmethod
                def raise_for_status():
                    pass

            return FakeResp()

        monkeypatch.setattr("requests.get", mock_get)

        from src.fetchers.treasury_fetcher import fetch_auction_results

        df = fetch_auction_results()
        assert isinstance(df.index, pd.DatetimeIndex)
        assert df.index.name == "auction_date"

    def test_fetch_upcoming_auctions(self, monkeypatch):
        import requests

        def mock_get(url, params=None, timeout=None, **kwargs):
            resp = requests.Response()
            resp.status_code = 200

            class FakeResp:
                @staticmethod
                def json():
                    return mock_upcoming_response()

                @staticmethod
                def raise_for_status():
                    pass

            return FakeResp()

        monkeypatch.setattr("requests.get", mock_get)

        from src.fetchers.treasury_fetcher import fetch_upcoming_auctions

        df = fetch_upcoming_auctions()
        assert not df.empty
        assert len(df) == 1
        assert "security_type" in df.columns
        assert "offering_amt" in df.columns
        assert isinstance(df.index, pd.DatetimeIndex)

    def test_fetch_results_raises_on_connection_error(self, monkeypatch):
        import requests

        def mock_get(url, params=None, timeout=None, **kwargs):
            raise requests.ConnectionError("no network")

        monkeypatch.setattr("requests.get", mock_get)

        from src.fetchers.treasury_fetcher import fetch_auction_results

        with pytest.raises(requests.ConnectionError):
            fetch_auction_results()

    def test_fetch_results_empty_data(self, monkeypatch):
        import requests

        def mock_get(url, params=None, timeout=None, **kwargs):
            resp = requests.Response()
            resp.status_code = 200

            class FakeResp:
                @staticmethod
                def json():
                    return {"data": [], "links": {"next": None}}

                @staticmethod
                def raise_for_status():
                    pass

            return FakeResp()

        monkeypatch.setattr("requests.get", mock_get)

        from src.fetchers.treasury_fetcher import fetch_auction_results

        df = fetch_auction_results()
        assert df.empty


class TestMspd:
    def test_fetch_mspd_pivots_and_derives_share(self, monkeypatch):
        monkeypatch.setattr(
            "src.fetchers.treasury_fetcher.requests.get",
            _fake_get(mock_mspd_response()),
        )

        df = fetch_mspd()

        # 只有市场化品种列 + 派生列；Savings Bonds 行被过滤
        assert set(df.columns) == {
            "BILLS",
            "NOTES",
            "BONDS",
            "MARKETABLE_TOTAL",
            "BILL_SHARE",
            "TOTAL_DEBT",
        }
        assert df.index.tolist() == [
            pd.Timestamp("2026-05-31"),
            pd.Timestamp("2026-06-30"),
        ]
        # 2026-06: 市场化 = 3B+6B+1B = 10B，Bill 占比 = 30%
        row = df.loc[pd.Timestamp("2026-06-30")]
        assert row["MARKETABLE_TOTAL"] == pytest.approx(10_000_000)
        assert row["BILL_SHARE"] == pytest.approx(30.0)
        assert row["TOTAL_DEBT"] == pytest.approx(38_000_000)
        assert df.loc[pd.Timestamp("2026-05-31"), "TOTAL_DEBT"] == pytest.approx(
            37_000_000
        )

    def test_fetch_mspd_missing_fields_raises(self, monkeypatch):
        monkeypatch.setattr(
            "src.fetchers.treasury_fetcher.requests.get",
            _fake_get({"data": [{"record_date": "2026-06-30", "amount": "1"}]}),
        )
        with pytest.raises(ValueError):
            fetch_mspd()

    def test_fetch_mspd_no_marketable_rows_raises(self, monkeypatch):
        monkeypatch.setattr(
            "src.fetchers.treasury_fetcher.requests.get",
            _fake_get(
                {
                    "data": [
                        {
                            "record_date": "2026-06-30",
                            "security_type_desc": "Non-marketable",
                            "security_class_desc": "Savings Bonds",
                            "debt_held_public_mil_amt": "1000000",
                        }
                    ]
                }
            ),
        )
        with pytest.raises(ValueError):
            fetch_mspd()
