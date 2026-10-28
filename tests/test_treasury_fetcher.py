"""Treasury fetcher 单元测试。"""

import pandas as pd
import pytest

from src.fetchers.treasury_fetcher import (
    RESULTS_COLUMNS,
    UPCOMING_COLUMNS,
    _endpoint_name,
    fetch_mspd,
)


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


def _fake_get_by_url(routes: dict[str, object]):
    """按 URL 片段分派响应：auctions_query 与 TA_WS 两条通道各自有固定载荷。

    未知 URL 直接抛错——两个源混用同一份载荷会把字段映射测成巧合。
    """

    class FakeResp:
        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

        def raise_for_status(self):
            pass

    def get(url, params=None, timeout=None, **kwargs):
        for frag, body in routes.items():
            if frag in url:
                return FakeResp(body)
        raise AssertionError(f"未预期的请求: {url}")

    return get


def mock_ta_ws_response():
    """TA_WS auctioned 响应（数组本体，与 fiscaldata 的 {data: []} 不同）。

    两条：一条与 mock_auctions_response 的 10-Year 同键（重叠场次），
    一条 fiscaldata 完全没有（2-Year，验证追加）。
    """
    base = {
        "securityType": "Note",
        "offeringAmount": "42000000000",
        "bidToCoverRatio": "2.50",
        "highYield": "4.2000",
        "highRate": "",
        "medianYield": "4.150",
        "indirectBiddersCompetitiveParAwarded": "25000000000",
        "grandTotalParAwarded": "42000000000",
        "reopening": "No",
        "type": "Note",
        "floatingRate": "No",
    }
    return [
        {
            **base,
            "securityTerm": "10-Year",
            "cusip": "91282CXX0",
            "competitiveAuctionCloseDate": "2025-01-08T00:00:00",
            "issueDate": "2025-01-15T00:00:00",
            "maturityDate": "2034-11-15T00:00:00",
        },
        {
            **base,
            "securityTerm": "2-Year",
            "cusip": "91282CZZ1",
            "bidToCoverRatio": "2.10",
            "highYield": "4.0500",
            "medianYield": "4.010",
            "grandTotalParAwarded": "40000000000",
            "indirectBiddersCompetitiveParAwarded": "20400000000",
            "competitiveAuctionCloseDate": "2025-01-09T00:00:00",
            "issueDate": "2025-01-16T00:00:00",
            "maturityDate": "2027-01-15T00:00:00",
        },
    ]


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
            _endpoint_name("https://api.fiscaldata.treasury.gov/.../auctions_query?format=json")
            == "auctions_query"
        )

    def test_fetch_auction_results_structure(self, monkeypatch):
        """验证返回 DataFrame 含预期列和派生指标。"""
        monkeypatch.setattr(
            "requests.get",
            _fake_get_by_url(
                {
                    "/auctions_query": mock_auctions_response(),
                    "/TA_WS/securities/auctioned": mock_ta_ws_response(),
                }
            ),
        )

        from src.fetchers.treasury_fetcher import fetch_auction_results

        df = fetch_auction_results()
        assert not df.empty
        assert len(df) == 3  # 两条 fiscal + 一条 TA_WS 独有场次（整行追加）
        assert "indirect_pct" in df.columns
        assert "tail_bp" in df.columns
        assert "bid_to_cover_ratio" in df.columns

        # 验证派生指标计算
        row = df.loc["2025-01-08"]
        assert row["indirect_pct"] == pytest.approx(59.5, abs=0.1)  # 25B/42B
        assert row["tail_bp"] == pytest.approx(5.0, abs=0.1)  # (4.200-4.150)*100

    def test_fetch_auction_results_index_is_datetime(self, monkeypatch):
        monkeypatch.setattr(
            "requests.get",
            _fake_get_by_url(
                {
                    "/auctions_query": mock_auctions_response(),
                    "/TA_WS/securities/auctioned": [],
                }
            ),
        )

        from src.fetchers.treasury_fetcher import fetch_auction_results

        df = fetch_auction_results()
        assert isinstance(df.index, pd.DatetimeIndex)
        assert df.index.name == "auction_date"

    def test_fetch_upcoming_auctions(self, monkeypatch):
        """fiscaldata 日历 + TA_WS announced 合并：本周 Weekly Bill 不能漏。"""
        monkeypatch.setattr(
            "requests.get",
            _fake_get_by_url(
                {
                    "/upcoming_auctions": mock_upcoming_response(),
                    "/TA_WS/securities/announced": [
                        {
                            "securityType": "Bill",
                            "securityTerm": "4-Week",
                            "offeringAmount": "115000000000",
                            "reopening": "Yes",
                            "cusip": "912797VZ7",
                            "competitiveAuctionCloseDate": "2026-10-29T00:00:00",
                            "issueDate": "2026-11-03T00:00:00",
                        },
                        # 与 fiscaldata 同键：以 fiscaldata 为准，不重复
                        {
                            "securityType": "Note",
                            "securityTerm": "2-Year",
                            "offeringAmount": "69000000000",
                            "reopening": "No",
                            "cusip": "91282CYY0",
                            "competitiveAuctionCloseDate": "2025-02-25T00:00:00",
                            "issueDate": "2025-02-28T00:00:00",
                        },
                    ],
                }
            ),
        )

        from src.fetchers.treasury_fetcher import fetch_upcoming_auctions

        df = fetch_upcoming_auctions()
        assert list(df.columns) == [c for c in UPCOMING_COLUMNS if c != "auction_date"]
        assert df.index.tolist() == [pd.Timestamp("2025-02-25"), pd.Timestamp("2026-10-29")]
        assert df.loc[pd.Timestamp("2026-10-29"), "security_term"] == "4-Week"
        assert df.loc[pd.Timestamp("2026-10-29"), "issue_date"] == "2026-11-03"

    def test_fetch_results_raises_on_connection_error(self, monkeypatch):
        import requests

        def mock_get(url, params=None, timeout=None, **kwargs):
            raise requests.ConnectionError("no network")

        monkeypatch.setattr("requests.get", mock_get)

        from src.fetchers.treasury_fetcher import fetch_auction_results

        with pytest.raises(requests.ConnectionError):
            fetch_auction_results()

    def test_fetch_results_empty_data(self, monkeypatch):
        monkeypatch.setattr(
            "requests.get",
            _fake_get_by_url(
                {
                    "/auctions_query": {"data": [], "links": {"next": None}},
                    "/TA_WS/securities/auctioned": [],
                }
            ),
        )

        from src.fetchers.treasury_fetcher import fetch_auction_results

        df = fetch_auction_results()
        assert df.empty


class TestTaWsSource:
    """TA_WS 通道：字段映射与两源合并。

    fiscaldata 的 auctions_query 会整段停更（2026-10-15 起 13 天没有新记录），
    停更期间页面「最近拍卖结果」整段停在旧日期且无告警。TA_WS 是它的上游、
    当天就出结果，两源重叠场次逐字段相同，所以合并只补缺口、不改历史。
    """

    @staticmethod
    def _ta_record(**over) -> dict:
        rec = {
            "securityType": "Bill",
            "securityTerm": "13-Week",
            "offeringAmount": "95000000000",
            "bidToCoverRatio": "2.77",
            "highYield": "",
            "highRate": "4.095",
            "medianYield": "",
            "indirectBiddersCompetitiveParAwarded": "55195179600",
            "grandTotalParAwarded": "101701941200",
            "reopening": "Yes",
            "cusip": "912797TM9",
            "competitiveAuctionCloseDate": "2026-10-19T00:00:00",
            "issueDate": "2026-10-22T00:00:00",
            "maturityDate": "2027-01-21T00:00:00",
            "type": "Bill",
            "floatingRate": "",
        }
        rec.update(over)
        return rec

    def test_field_mapping(self, monkeypatch):
        """Bill 贴现率走 highRate、TIPS 靠 type 标、日期切掉时分秒。"""
        monkeypatch.setattr(
            "requests.get",
            _fake_get_by_url(
                {
                    "/TA_WS/securities/auctioned": [
                        self._ta_record(),
                        self._ta_record(
                            securityType="Note",
                            securityTerm="5-Year",
                            type="TIPS",
                            cusip="91282CRR4",
                            highYield="2.367",
                            highRate="",
                            medianYield="2.300",
                            floatingRate="No",
                            reopening="No",
                            competitiveAuctionCloseDate="2026-10-22T00:00:00",
                        ),
                    ]
                }
            ),
        )

        from src.fetchers.treasury_fetcher import fetch_ta_ws_auctions

        df = fetch_ta_ws_auctions(days=60).set_index("auction_date")
        assert isinstance(df.index, pd.DatetimeIndex)

        bill = df.loc[pd.Timestamp("2026-10-19")]
        assert bill["security_type"] == "Bill"
        assert bill["high_discnt_rate"] == "4.095"  # Bill 用贴现率
        assert pd.isna(bill["high_yield"])
        assert bill["floating_rate"] == "No"  # 空字符串→No
        assert bill["inflation_index_security"] == "No"
        assert bill["issue_date"] == "2026-10-22"  # T00:00:00 被切掉
        assert bill["total_accepted"] == "101701941200"

        tips = df.loc[pd.Timestamp("2026-10-22")]
        assert tips["inflation_index_security"] == "Yes"  # type=TIPS
        assert tips["avg_med_yield"] == "2.300"  # medianYield

    def test_merge_fills_announcement_row(self, monkeypatch):
        """fiscaldata 的「已公告未拍卖」行被 TA_WS 的成交记录就地补齐。"""
        monkeypatch.setattr(
            "requests.get",
            _fake_get_by_url(
                {
                    "/auctions_query": {
                        "data": [
                            {
                                "security_type": "Bill",
                                "security_term": "13-Week",
                                "offering_amt": "95000000000",
                                "reopening": "Yes",
                                "cusip": "912797TM9",
                                "auction_date": "2026-10-19",
                                "issue_date": "2026-10-22",
                                "maturity_date": "2027-01-21",
                                "inflation_index_security": "No",
                                "floating_rate": "No",
                            }
                        ],
                        "links": {"next": None},
                    },
                    "/TA_WS/securities/auctioned": [self._ta_record()],
                }
            ),
        )

        from src.fetchers.treasury_fetcher import fetch_auction_results

        df = fetch_auction_results()
        assert len(df) == 1  # 同键合并，不重复
        row = df.loc[pd.Timestamp("2026-10-19")]
        assert row["bid_to_cover_ratio"] == "2.77"
        assert row["offering_amt"] == "95000000000"  # fiscal 字段保留
        assert row["high_rate"] == pytest.approx(4.095)  # Bill 取贴现率
        assert row["indirect_pct"] == pytest.approx(54.3, abs=0.1)

    def test_merge_appends_missing_row_and_keeps_fiscal_values(self):
        """fiscaldata 整段缺失的场次追加；重叠场次保留 fiscal 原值（不产生改写）。"""
        from src.fetchers.treasury_fetcher import _derive_results, _merge_rows

        fiscal = pd.DataFrame(
            {
                "auction_date": pd.to_datetime(["2026-10-07"]),
                "security_type": ["Note"],
                "security_term": ["9-Year 10-Month"],
                "offering_amt": ["39000000000"],
                "bid_to_cover_ratio": ["2.770000"],
                "high_yield": ["5.3000"],
                "high_discnt_rate": [None],
                "avg_med_yield": ["5.255000"],
                "indirect_bidder_accepted": ["31063238000"],
                "total_accepted": ["39926321900"],
                "reopening": ["Yes"],
                "cusip": ["91282CRF0"],
                "issue_date": ["2026-10-15"],
                "maturity_date": ["2036-08-15"],
                "inflation_index_security": ["No"],
                "floating_rate": ["No"],
            }
        )
        ta = pd.DataFrame(
            {
                "auction_date": pd.to_datetime(["2026-10-07", "2026-10-27"]),
                "security_type": ["Note", "Note"],
                "security_term": ["9-Year 10-Month", "5-Year"],
                "offering_amt": ["39000000000", "70000000000"],
                "bid_to_cover_ratio": ["2.77", "2.33"],
                "high_yield": ["5.30", "4.845"],
                "high_discnt_rate": [None, None],
                "avg_med_yield": ["5.255", "4.790"],
                "indirect_bidder_accepted": ["31063238000", "41588400000"],
                "total_accepted": ["39926321900", "77778350600"],
                "reopening": ["Yes", "No"],
                "cusip": ["91282CRF0", "91282CRT0"],
                "issue_date": ["2026-10-15", "2026-11-02"],
                "maturity_date": ["2036-08-15", "2031-10-31"],
                "inflation_index_security": ["No", "No"],
                "floating_rate": ["No", "No"],
            }
        )

        out = _derive_results(_merge_rows(fiscal, ta, RESULTS_COLUMNS))
        assert len(out) == 2
        # 列序必须原样回到 RESULTS_COLUMNS：groupby 会把合并键 cusip 提到第二列，
        # 不恢复就整份文件换列序（实测改写 1.1 万行）
        assert list(_merge_rows(fiscal, ta, RESULTS_COLUMNS).columns) == RESULTS_COLUMNS
        # 重叠场次：fiscal 的字符串原样保留（否则每天都会重写 60 天历史）
        assert out.loc[pd.Timestamp("2026-10-07"), "bid_to_cover_ratio"] == "2.770000"
        # TA_WS 独有场次：整行追加，tail 由 medianYield 派生
        new = out.loc[pd.Timestamp("2026-10-27")]
        assert new["cusip"] == "91282CRT0"
        assert new["tail_bp"] == pytest.approx(5.5, abs=0.1)  # (4.845-4.790)*100
        assert new["high_rate"] == pytest.approx(4.845)

    def test_previous_layer_keeps_rows_outside_both_windows(self, monkeypatch):
        """上次落盘的行不能被覆盖写删掉（两源都停更时 TA 的 60 天窗口回不到停更起点）。"""
        monkeypatch.setattr(
            "requests.get",
            _fake_get_by_url(
                {
                    "/auctions_query": {
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
                            }
                        ],
                        "links": {"next": None},
                    },
                    "/TA_WS/securities/auctioned": [],
                }
            ),
        )
        cols = {
            "security_type": "Bill",
            "security_term": "13-Week",
            "offering_amt": "95000000000",
            "bid_to_cover_ratio": "2.77",
            "reopening": "Yes",
            "issue_date": "2026-01-21",
            "maturity_date": "2027-04-22",
            "inflation_index_security": "No",
            "floating_rate": "No",
        }
        previous = pd.DataFrame(
            [
                {**cols, "auction_date": "2026-01-05", "cusip": "912797OLD"},
                # 同键但结果已被新数据代替：fresh 赢，previous 不改写
                {
                    **cols,
                    "auction_date": "2025-01-08",
                    "cusip": "91282CXX0",
                    "bid_to_cover_ratio": "9.99",
                },
            ]
        )

        from src.fetchers.treasury_fetcher import fetch_auction_results

        df = fetch_auction_results(previous)
        assert df.loc[pd.Timestamp("2026-01-05"), "cusip"] == "912797OLD"  # 两源都没有，保留
        assert df.loc[pd.Timestamp("2025-01-08"), "bid_to_cover_ratio"] == "2.50"  # fresh 优先


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
        assert df.loc[pd.Timestamp("2026-05-31"), "TOTAL_DEBT"] == pytest.approx(37_000_000)

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
