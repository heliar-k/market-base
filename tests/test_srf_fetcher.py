"""SRF fetcher 单元测试：过滤规则 + 按日求和。"""

from src.fetchers.srf_fetcher import fetch_srf_usage


def _op(op_id, method, date, accepted, op_type="Repo", note=""):
    return {
        "operationId": op_id,
        "operationType": op_type,
        "operationMethod": method,
        "operationDate": date,
        "totalAmtAccepted": accepted,
        "note": note,
    }


def _fake_get(payload):
    def _get(*a, **k):
        return type(
            "R",
            (),
            {"json": lambda self: payload, "raise_for_status": lambda self: None},
        )()

    return _get


def test_filters_srf_and_sums_by_day(monkeypatch):
    """Multiple Price 的 Repo 操作 = SRF；同日多笔求和，其他操作忽略。"""
    payload = {
        "repo": {
            "operations": [
                _op("RP 103125 1", "Multiple Price", "2025-10-31", 20_350_000_000),
                _op("RP 103125 2", "Fixed Rate", "2025-10-31", 51_802_000_000),
                _op("RP 103125 3", "Multiple Price", "2025-10-31", 30_000_000_000),
                _op("RP 103025 1", "Multiple Price", "2025-10-30", 0),
                _op("RP 103025 2", "Full Allotment", "2025-10-30", 2_000_000_000),
            ]
        }
    }
    monkeypatch.setattr("src.fetchers.srf_fetcher.requests.get", _fake_get(payload))

    df = fetch_srf_usage("2025-10-30", "2025-10-31")

    assert list(df.columns) == ["SRF_USAGE"]
    assert df.loc["2025-10-31", "SRF_USAGE"] == 50.35  # 10/31 两笔 SRF 相加（十亿美元）
    assert df.loc["2025-10-30", "SRF_USAGE"] == 0.0  # 只有 Full Allotment 的日子记 0
    assert df.index.tolist() == ["2025-10-30", "2025-10-31"]


def test_reverse_repo_ignored(monkeypatch):
    """Reverse Repo 的 Multiple Price 操作不算 SRF。"""
    payload = {
        "repo": {
            "operations": [
                _op(
                    "RRP 103125 1",
                    "Multiple Price",
                    "2025-10-31",
                    9_000_000_000,
                    op_type="Reverse Repo",
                ),
            ]
        }
    }
    monkeypatch.setattr("src.fetchers.srf_fetcher.requests.get", _fake_get(payload))

    assert fetch_srf_usage("2025-10-31", "2025-10-31").empty


def test_no_operations_returns_empty(monkeypatch):
    monkeypatch.setattr(
        "src.fetchers.srf_fetcher.requests.get",
        _fake_get({"repo": {"operations": []}}),
    )
    assert fetch_srf_usage("2026-07-30", "2026-07-31").empty


def test_sve_excluded(monkeypatch):
    """Small Value Exercise（测试操作）不计入使用量。"""
    payload = {
        "repo": {
            "operations": [
                _op(
                    "RP 021826 28",
                    "Multiple Price",
                    "2026-02-18",
                    56_000_000,
                    note="This operation is a Small Value Exercise (SVE)...",
                ),
            ]
        }
    }
    monkeypatch.setattr("src.fetchers.srf_fetcher.requests.get", _fake_get(payload))

    assert fetch_srf_usage("2026-02-18", "2026-02-18").empty


def test_post_switch_full_allotment_counted(monkeypatch):
    """2025-12-11 起 SRF 改 Full Allotment，每日两场（25 早场 + 26/27）均计入。"""
    payload = {
        "repo": {
            "operations": [
                _op("RP 073126 25", "Full Allotment", "2026-07-31", 0),
                _op("RP 073126 26", "Full Allotment", "2026-07-31", 2_500_000_000),
            ]
        }
    }
    monkeypatch.setattr("src.fetchers.srf_fetcher.requests.get", _fake_get(payload))

    df = fetch_srf_usage("2026-07-31", "2026-07-31")
    assert df.loc["2026-07-31", "SRF_USAGE"] == 2.5


def test_sve_10h30_switch_style_excluded(monkeypatch):
    """切换前的 SVE 测试场（releaseTime=10:30，note 可能为空）不计入。"""
    payload = {
        "repo": {
            "operations": [
                {
                    "operationId": "RP 030823 1",
                    "operationType": "Repo",
                    "operationMethod": "Multiple Price",
                    "operationDate": "2023-03-08",
                    "releaseTime": "10:30",
                    "totalAmtAccepted": 56_000_000,
                    "note": "",
                },
                {
                    "operationId": "RP 030823 2",
                    "operationType": "Repo",
                    "operationMethod": "Multiple Price",
                    "operationDate": "2023-03-08",
                    "releaseTime": "13:30",
                    "totalAmtAccepted": 0,
                    "note": "",
                },
            ]
        }
    }
    monkeypatch.setattr("src.fetchers.srf_fetcher.requests.get", _fake_get(payload))

    df = fetch_srf_usage("2023-03-08", "2023-03-08")
    assert df.loc["2023-03-08", "SRF_USAGE"] == 0.0  # 10:30 场被排除
    assert df.index.tolist() == ["2023-03-08"]
