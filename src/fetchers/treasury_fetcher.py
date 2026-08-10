"""
Fetch US Treasury auction & debt data from Treasury Fiscal Data API.
写入 data/treasury/ 目录（auction_date / record_date 为 key）。

数据源: US Treasury Fiscal Data API（免费，无需认证）
  - auctions_query: 历史拍卖结果（中标利率、Bid-to-Cover、间接投标人%、Tail）
  - upcoming_auctions: 未来拍卖日历（类型、期限、发行额、是否重开）

auction_results.csv: 每次全量覆盖（API 返回全量历史，非增量）。
upcoming_auctions.csv: 每次全量覆盖。
mspd.csv: 每次全量覆盖（月度未偿债务结构，派生 Bill 占比）。
"""

import logging
from urllib.parse import urlparse

import pandas as pd
import requests

API_BASE = (
    "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od"
)

API_BASE_DEBT = (
    "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/debt"
)

RESULTS_COLUMNS = [
    "security_type",
    "security_term",
    "offering_amt",
    "bid_to_cover_ratio",
    "high_yield",
    "high_discnt_rate",
    "avg_med_yield",
    "indirect_bidder_accepted",
    "total_accepted",
    "reopening",
    "cusip",
    "auction_date",
    "issue_date",
    "maturity_date",
]

UPCOMING_COLUMNS = [
    "security_type",
    "security_term",
    "offering_amt",
    "reopening",
    "cusip",
    "auction_date",
    "issue_date",
]

MSPD_COLUMNS = [
    "record_date",
    "security_type_desc",
    "security_class_desc",
    "debt_held_public_mil_amt",
    "total_mil_amt",
]

# security_class_desc → 输出列名（市场化品种，bill 占比监控用）
_MSPD_TYPE_MAP = {
    "Bills": "BILLS",
    "Notes": "NOTES",
    "Bonds": "BONDS",
    "Treasury Inflation-Protected Securities": "TIPS",
    "Floating Rate Notes": "FRN",
}

logger = logging.getLogger(__name__)


def fetch_auction_results() -> pd.DataFrame:
    """拉取全量历史拍卖结果，派生 indirect_pct / tail_bp。

    indirect_pct = indirect_bidder_accepted / total_accepted * 100（海外需求占比）
    tail_bp = (high_yield - avg_med_yield) * 100（代理口径，审计 P2-①：
      市场标准 tail = high yield − 发行前 when-issued 收益率；本数据源无 WI 值，
      以 中标收益率高位 − 中标收益率中位数 近似，方向性可用、数值偏大，
      前端拍卖页已标注代理口径）
    """
    rows = _fetch_all_pages(
        f"{API_BASE}/auctions_query", RESULTS_COLUMNS, "auction_date"
    )
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["auction_date"] = pd.to_datetime(df["auction_date"])
    df = df.set_index("auction_date").sort_index()

    # 派生指标
    total = pd.to_numeric(df["total_accepted"], errors="coerce")
    indirect = pd.to_numeric(df["indirect_bidder_accepted"], errors="coerce")
    df["indirect_pct"] = (indirect / total.replace(0, pd.NA) * 100).round(1)

    high_yield = pd.to_numeric(df["high_yield"], errors="coerce")
    avg_yield = pd.to_numeric(df["avg_med_yield"], errors="coerce")
    df["tail_bp"] = ((high_yield - avg_yield) * 100).round(1)

    # Bill 用贴现率（high_discnt_rate），Note/Bond 用收益率（high_yield）
    discnt = pd.to_numeric(df["high_discnt_rate"], errors="coerce")
    df["high_rate"] = high_yield.where(df["security_type"] != "Bill", discnt)

    return df


def fetch_upcoming_auctions() -> pd.DataFrame:
    """拉取未来拍卖日历（全量，~93 条）。"""
    rows = _fetch_all_pages(
        f"{API_BASE}/upcoming_auctions", UPCOMING_COLUMNS, "auction_date"
    )
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["auction_date"] = pd.to_datetime(df["auction_date"])
    return df.set_index("auction_date").sort_index()


def fetch_mspd() -> pd.DataFrame:
    """拉取月度未偿债务结构（MSPD Table 1），派生 Bill 占比。

    返回 DataFrame: index=月末，列为各市场化品种债务（百万美元，BILLS/NOTES/
    BONDS/TIPS/FRN）+ MARKETABLE_TOTAL + BILL_SHARE（Bills/市场化总额，%）
    + TOTAL_DEBT（总未偿债务，含非市场化与政府间，D.3 官方占比分母）。
    金额口径=debt_held_public_mil_amt（公众持有，剔除政府间持有）；
    TOTAL_DEBT 取 total_mil_amt。源字段缺失时抛 ValueError。
    """
    rows = _fetch_all_pages(
        f"{API_BASE_DEBT}/mspd/mspd_table_1", MSPD_COLUMNS, "record_date"
    )
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    if not {"record_date", "security_type_desc", "security_class_desc"}.issubset(
        df.columns
    ):
        raise ValueError(f"MSPD 字段缺失: {list(df.columns)}")
    df["record_date"] = pd.to_datetime(df["record_date"])

    # 总未偿债务行（D.3 海外官方占比分母），先于 Marketable 过滤取出
    total_rows = df[df["security_type_desc"] == "Total Public Debt Outstanding"]
    total = pd.to_numeric(total_rows["total_mil_amt"], errors="coerce")
    total.index = total_rows["record_date"]

    df["_type"] = df["security_class_desc"].map(
        lambda d: _MSPD_TYPE_MAP.get(d or "", "")
    )
    df = df[(df["security_type_desc"] == "Marketable") & (df["_type"] != "")].copy()
    if df.empty:
        raise ValueError("MSPD 无市场化品种行（security_type_desc/class 值异常）")
    df["_amt"] = pd.to_numeric(df["debt_held_public_mil_amt"], errors="coerce")

    pivot = df.pivot_table(
        index="record_date", columns="_type", values="_amt", aggfunc="first"
    ).sort_index()
    pivot["MARKETABLE_TOTAL"] = pivot.sum(axis=1)
    pivot["BILL_SHARE"] = (pivot["BILLS"] / pivot["MARKETABLE_TOTAL"] * 100).round(2)
    pivot["TOTAL_DEBT"] = total.reindex(pivot.index)
    return pivot


def _fetch_all_pages(url: str, columns: list[str], sort_field: str) -> list[dict]:
    """分页拉取 Treasury API，返回扁平化记录列表。"""
    all_rows: list[dict] = []
    page_size = 10000
    page = 1
    name = _endpoint_name(url)

    while True:
        print(f"  {name} page {page} ...", end=" ", flush=True)
        params = {
            "format": "json",
            "page[size]": page_size,
            "page[number]": page,
            "sort": f"-{sort_field}",
        }
        # 官方 API 直连：proxies=None 覆盖 .env 的 SOCKS5 代理（同 cfets 模式）
        resp = requests.get(
            url, params=params, timeout=60, proxies={"http": None, "https": None}
        )
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data", [])
        total_pages = int(body.get("meta", {}).get("total-pages", 1))
        print(f"{len(data)} 条")

        for item in data:
            row = {col: item.get(col) for col in columns}
            row = {k: (None if v in (None, "null", "") else v) for k, v in row.items()}
            all_rows.append(row)

        if page >= total_pages:
            break
        page += 1

    logger.info(f"  {name}: {len(all_rows)} 条 total")
    return all_rows


def _endpoint_name(url: str) -> str:
    return urlparse(url).path.rstrip("/").split("/")[-1]


if __name__ == "__main__":
    from ..config import ROOT

    out_dir = ROOT / "data" / "treasury"
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── 拍卖结果 ──
    try:
        results = fetch_auction_results()
    except Exception as e:
        print(f"Treasury: auction_results 拉取失败: {e}")
        raise SystemExit(1)
    if results.empty:
        print("Treasury: auction_results 无数据")
        raise SystemExit(1)
    path = out_dir / "auction_results.csv"
    results.to_csv(path, index_label="auction_date")
    print(f"Treasury: → {path} ({len(results.columns)} 列 × {len(results)} 行)")

    # ── 未来拍卖日历 ──
    try:
        upcoming = fetch_upcoming_auctions()
    except Exception as e:
        print(f"Treasury: upcoming_auctions 拉取失败: {e}")
        raise SystemExit(1)
    if upcoming.empty:
        print("Treasury: upcoming_auctions 无数据（周末/假日正常）")
    else:
        path = out_dir / "upcoming_auctions.csv"
        upcoming.to_csv(path, index_label="auction_date")
        print(f"Treasury: → {path} ({len(upcoming.columns)} 列 × {len(upcoming)} 行)")

    # ── 月度未偿债务结构（Bill 占比）──
    try:
        mspd = fetch_mspd()
    except Exception as e:
        print(f"Treasury: mspd 拉取失败: {e}")
        raise SystemExit(1)
    if mspd.empty:
        print("Treasury: mspd 无数据")
    else:
        path = out_dir / "mspd.csv"
        mspd.to_csv(path, index_label="record_date")
        latest = mspd.iloc[-1]
        print(
            f"Treasury: → {path} ({len(mspd.columns)} 列 × {len(mspd)} 行; "
            f"最新 {mspd.index[-1].date()} Bill 占比 {latest['BILL_SHARE']:.1f}%)"
        )
