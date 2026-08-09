"""insider_fetcher 解析/去重逻辑测试（fixture XML，无网络）。"""

import pandas as pd

from src.fetchers import insider_fetcher

# 带 xmlns 的经典结构（覆盖命名空间剥离路径）
_XML_NS = """<?xml version="1.0"?>
<ownershipDocument xmlns="http://www.sec.gov/edgar/ownership" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <periodOfReport>2026-01-05</periodOfReport>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>Test Person</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>0</isDirector>
      <isOfficer>1</isOfficer>
      <officerTitle>CEO</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-01-05</value></transactionDate>
      <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>1000</value></transactionShares>
        <transactionPricePerShare><value>50.5</value></transactionPricePerShare>
      </transactionAmounts>
      <postTransactionAmounts><sharesOwnedFollowingTransaction><value>5000</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-01-06</value></transactionDate>
      <transactionCoding><transactionCode>A</transactionCode></transactionCoding>
      <transactionAmounts><transactionShares><value>200</value></transactionShares></transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
"""

# 无 xmlns 的 X0508 新结构 + 董事（无 officerTitle）
_XML_NO_NS = """<?xml version="1.0"?>
<ownershipDocument>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>Director One</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>1</isDirector>
      <isOfficer>0</isOfficer>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-02-01</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>10</value></transactionShares>
        <transactionPricePerShare><value>200</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
"""


def test_parse_xml_with_namespace():
    """剥命名空间后解析：S 带金额，A 无价格（value=None）。"""
    rows = insider_fetcher.parse_ownership_xml(_XML_NS.encode())
    assert len(rows) == 2
    s, a = rows
    assert (s["code"], s["insider_name"], s["title"]) == ("S", "Test Person", "CEO")
    assert s["shares"] == 1000.0 and s["price"] == 50.5 and s["value"] == 50500.0
    assert s["shares_after"] == 5000.0
    assert (a["code"], a["value"]) == ("A", None)


def test_parse_xml_director_fallback():
    """无 officerTitle 时用 isDirector=1 → '董事'。"""
    rows = insider_fetcher.parse_ownership_xml(_XML_NO_NS.encode())
    assert rows[0]["title"] == "董事"
    assert rows[0]["code"] == "P" and rows[0]["value"] == 2000.0


def test_fetch_insider_dedupe_by_accession(tmp_path, monkeypatch):
    """accession 去重：已有文件不重复拉取；新 accession 追加。"""
    monkeypatch.setattr(insider_fetcher, "OUT_DIR", tmp_path)
    monkeypatch.setattr(insider_fetcher, "fetch_cik_map", lambda: {"AAPL": "320193"})

    def fake_recent(cik, cutoff):
        return [("acc-1", "2026-01-10"), ("acc-2", "2026-02-10")]

    monkeypatch.setattr(insider_fetcher, "_recent_form4", fake_recent)
    monkeypatch.setattr(
        insider_fetcher, "_ownership_xml_url", lambda cik, acc: f"https://x/{acc}.xml"
    )
    monkeypatch.setattr(insider_fetcher, "_get", lambda url: _XML_NS.encode())

    result = insider_fetcher.fetch_insider(["AAPL"])
    assert result["AAPL"] == (4, 0)  # 首次：2 个 accession × 各 2 笔交易
    df = pd.read_csv(tmp_path / "AAPL.csv")
    assert set(df["accession"]) == {"acc-1", "acc-2"}
    assert len(df) == 4

    result2 = insider_fetcher.fetch_insider(["AAPL"])
    assert result2["AAPL"] == (0, 2)  # 二次：accession 全跳过
    assert len(pd.read_csv(tmp_path / "AAPL.csv")) == 4  # 无重复行


def test_summary_counts_open_market_only(tmp_path):
    """汇总只数 P/S；近 90 天窗口过滤。"""
    path = tmp_path / "AAPL.csv"
    pd.DataFrame(
        [
            ["2026-08-01", "2026-07-30", "A", "P", 100.0, 10.0, 1000.0],
            ["2026-08-01", "2026-07-30", "A", "S", 50.0, 10.0, 500.0],
            ["2026-08-01", "2026-07-30", "A", "M", 1000.0, None, None],  # 不算
        ],
        columns=[
            "filing_date",
            "transaction_date",
            "insider_name",
            "code",
            "shares",
            "price",
            "value",
        ],
    ).to_csv(path, index=False)
    out = insider_fetcher._summary(path)
    assert "净买入" in out and "$500" in out
    assert "买入 1 笔" in out and "卖出 1 笔" in out  # M 行不计入买卖
