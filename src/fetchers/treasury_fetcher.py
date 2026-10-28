"""
Fetch US Treasury auction & debt data from Treasury Fiscal Data API.
写入 data/treasury/ 目录（auction_date / record_date 为 key）。

数据源（免费，无需认证）:
  - Fiscal Data auctions_query: 历史拍卖结果全量（中标利率、Bid-to-Cover、间接投标人%、Tail）
  - Fiscal Data upcoming_auctions: 未来拍卖日历（类型、期限、发行额、是否重开）
  - TreasuryDirect TA_WS: auctions_query / upcoming_auctions 的上游，两个数据集同时停更时
    由它补末段（auctioned = 成交结果，announced = 已宣布拍卖），见 TA_WS_DAYS

auction_results.csv: 每次全量覆盖（API 返回全量历史，非增量）。
upcoming_auctions.csv: 每次全量覆盖。
mspd.csv: 每次全量覆盖（月度未偿债务结构，派生 Bill 占比）。
"""

import logging
from urllib.parse import urlparse

import pandas as pd
import requests

API_BASE = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od"

API_BASE_DEBT = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/debt"

TA_WS_URL = "https://www.treasurydirect.gov/TA_WS/securities/auctioned"

TA_WS_ANNOUNCED_URL = "https://www.treasurydirect.gov/TA_WS/securities/announced"

# fiscaldata 的 auctions_query / upcoming_auctions 是 TA_WS 的下游再加工，两个会一起
# 停更（2026-10-15 起：成交结果 13 天没有新记录、日历只剩到 10-29 的两场）。TA_WS 当天
# 就出结果与公告，所以两个数据集都取 60 天窗口补末段；重叠场次逐字段相同
# （97 场比对 diff 全为 0），合并不改写已发布的历史值。
TA_WS_DAYS = 60

# TA_WS 字段 → 本地列名。语义已逐场核对（97 场 diff 全为 0）：
#   total_accepted    = grandTotalParAwarded（含非竞争性，等于 fiscal 的 Total Accepted）
#   avg_med_yield     = medianYield（等于 fiscal 的 Average/Median Yield，tail_bp 的减数）
#   high_discnt_rate  = highRate（Bill 的贴现率；票息券该字段为空，取 highYield）
#   security_type     = securityType（TIPS/FRN 与名义券共用此值，差异见 fetch_ta_ws_auctions）
TA_WS_FIELDS = {
    "security_type": "securityType",
    "security_term": "securityTerm",
    "offering_amt": "offeringAmount",
    "bid_to_cover_ratio": "bidToCoverRatio",
    "high_yield": "highYield",
    "high_discnt_rate": "highRate",
    "avg_med_yield": "medianYield",
    "indirect_bidder_accepted": "indirectBiddersCompetitiveParAwarded",
    "total_accepted": "grandTotalParAwarded",
    "reopening": "reopening",
    "cusip": "cusip",
    "issue_date": "issueDate",
    "maturity_date": "maturityDate",
}

TA_WS_UPCOMING_FIELDS = {
    "security_type": "securityType",
    "security_term": "securityTerm",
    "offering_amt": "offeringAmount",
    "reopening": "reopening",
    "cusip": "cusip",
    "issue_date": "issueDate",
}

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
    # TIPS 与名义券共用 security_term（TIPS 的 10Y 也叫 "10-Year"），只按期限归组
    # 会把 TIPS 混进名义券趋势。此字段（Yes/No）是唯一可靠的区分依据。
    "inflation_index_security",
    # FRN（2 年期浮动票息）无固定票息，源 API 的 high_yield 恒为 null；不标注的话
    # 页面「中标利率」整列「—」读起来像丢数据。
    "floating_rate",
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


def fetch_auction_results(previous: pd.DataFrame | None = None) -> pd.DataFrame:
    """全量历史拍卖结果，派生 indirect_pct / tail_bp。

    优先级：fiscaldata 已发布值 > TA_WS 补充 > previous（上一次落盘的历史）。
    最后一层是防历史被截断：两个源都停更时 TA 的 60 天窗口回不到停更起点
    （2026-10-16 ～ 今天 − 60 天那段），整份覆盖写会把那段已入库的场次删掉，
    而末场日期依然新鲜，时效断言看不出来。

    indirect_pct = indirect_bidder_accepted / total_accepted * 100（海外需求占比）
    tail_bp = (high_yield - avg_med_yield) * 100（代理口径，审计 P2-①：
      市场标准 tail = high yield − 发行前 when-issued 收益率；本数据源无 WI 值，
      以 中标收益率高位 − 中标收益率中位数 近似，方向性可用、数值偏大，
      前端拍卖页已标注代理口径）

    派生前先把 Bill 的口径分好：Bill 用 high_discnt_rate，Note/Bond 用 high_yield；
    TIPS 的 high_yield 是实际利率（比名义低约一个盈亏平衡通胀率），不能与名义券混算，
    故 inflation_index_security / floating_rate 原样保留给下游过滤与标注。
    """
    # reindex：旧文件可能少列（schema 演进过），缺的当空值，不因读旧文件失败；
    # auction_date 从 CSV 读回来是字符串，不转码会让索引变成 object（排不了序）
    prior = pd.DataFrame() if previous is None else previous.reindex(columns=RESULTS_COLUMNS)
    if not prior.empty:
        prior = prior.assign(auction_date=pd.to_datetime(prior["auction_date"]))
    tail_layer = _merge_rows(fetch_ta_ws_auctions(), prior, RESULTS_COLUMNS)
    return _derive_results(_merge_rows(_fiscaldata_auctions(), tail_layer, RESULTS_COLUMNS))


def _fiscaldata_auctions() -> pd.DataFrame:
    """fiscaldata auctions_query 全量历史（原始列，未派生）。"""
    rows = _fetch_all_pages(f"{API_BASE}/auctions_query", RESULTS_COLUMNS, "auction_date")
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["auction_date"] = pd.to_datetime(df["auction_date"])
    return df


def fetch_ta_ws_auctions(days: int = TA_WS_DAYS) -> pd.DataFrame:
    """TreasuryDirect 官方拍卖结果（最近 days 天，原始列，未派生）。

    成交结果当天发布，是 fiscaldata 停更期间唯一能推进末场日期的源。
    """
    resp = requests.get(
        TA_WS_URL,
        params={"format": "json", "days": days},
        timeout=60,
        # 官方 API 直连：proxies=None 覆盖 .env 的 SOCKS5 代理（同 fiscaldata 模式）
        proxies={"http": None, "https": None},
    )
    resp.raise_for_status()
    records = resp.json()
    print(f"  auctioned {len(records)} 条（近 {days} 天）")
    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(
        [
            {
                **{col: r.get(src) for col, src in TA_WS_FIELDS.items()},
                "auction_date": (r.get("competitiveAuctionCloseDate") or "")[:10],
                # TIPS/FRN 与名义券共用 security_type，差异只在 type 与 floatingRate：
                # type ∈ {Bill, Bond, Note, TIPS, FRN, CMB}，floatingRate 空值即非浮动
                "inflation_index_security": "Yes" if r.get("type") == "TIPS" else "No",
                "floating_rate": "Yes" if r.get("floatingRate") == "Yes" else "No",
            }
            for r in records
        ]
    )
    df["auction_date"] = pd.to_datetime(df["auction_date"])
    for col in ("issue_date", "maturity_date"):
        df[col] = df[col].str.slice(0, 10)
    return df[RESULTS_COLUMNS].replace("", pd.NA)


def _merge_rows(base: pd.DataFrame, extra: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """按 (拍卖日, CUSIP) 合并同一口径的两源：base 已发布的值保持原样，extra 补缺口。

    补的两种缺口：① base 的「已公告未拍卖」行（结果字段全空，与 extra 的
    成交记录同键）；② base 整段缺失的场次（整行由 extra 提供）。
    groupby.first() 取每列首个非空值，所以重叠场次不会被后到的源改写。
    """
    frames = [f for f in (base, extra) if not f.empty]
    if not frames:
        return pd.DataFrame()
    if len(frames) == 1:
        return frames[0][columns]
    merged = pd.concat(frames, ignore_index=True)
    merged = merged.groupby(
        ["auction_date", "cusip"], as_index=False, sort=False, dropna=False
    ).first()
    return merged[columns]  # groupby 把 cusip 提到第二列，恢复写盘列序


def _derive_results(df: pd.DataFrame) -> pd.DataFrame:
    """派生 indirect_pct / tail_bp / high_rate（合并后统一算一次）。"""
    if df.empty:
        return pd.DataFrame()
    # 按 (拍卖日, CUSIP) 规范排序：同日多场不能沿用源的返回顺序，否则每次拉取的
    # 当日行会互换位置（旧实现 sort_index 在重复日期上不稳定，实测单日改动上千行）。
    df = df.sort_values(["auction_date", "cusip"], kind="stable").set_index("auction_date")

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
    """拉取未来拍卖日历（全量，~100 条）；同日同 CUSIP 以 fiscaldata 为准。

    同一数据集同样会整段停更（2026-10 停更后日历只剩已宣布到 10-29 的 2 场，
    当周 Weekly Bill 都没进去），所以末段也由 TA_WS announced 补齐。
    """
    rows = _fetch_all_pages(f"{API_BASE}/upcoming_auctions", UPCOMING_COLUMNS, "auction_date")
    fiscal = pd.DataFrame()
    if rows:
        fiscal = pd.DataFrame(rows)
        fiscal["auction_date"] = pd.to_datetime(fiscal["auction_date"])

    df = _merge_rows(fiscal, fetch_ta_ws_announced(), UPCOMING_COLUMNS)
    if df.empty:
        return df
    return df.set_index("auction_date").sort_index(kind="stable")


def fetch_ta_ws_announced(days: int = TA_WS_DAYS) -> pd.DataFrame:
    """TreasuryDirect 已宣布拍卖（最近 days 天含未来场次，日历的补充源）。"""
    resp = requests.get(
        TA_WS_ANNOUNCED_URL,
        params={"format": "json", "days": days},
        timeout=60,
        proxies={"http": None, "https": None},
    )
    resp.raise_for_status()
    records = resp.json()
    print(f"  announced {len(records)} 条（近 {days} 天）")
    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(
        [
            {
                **{col: r.get(src) for col, src in TA_WS_UPCOMING_FIELDS.items()},
                "auction_date": (r.get("competitiveAuctionCloseDate") or "")[:10],
            }
            for r in records
        ]
    )
    df["auction_date"] = pd.to_datetime(df["auction_date"])
    df["issue_date"] = df["issue_date"].str.slice(0, 10)
    return df[UPCOMING_COLUMNS].replace("", pd.NA)


def fetch_mspd() -> pd.DataFrame:
    """拉取月度未偿债务结构（MSPD Table 1），派生 Bill 占比。

    返回 DataFrame: index=月末，列为各市场化品种债务（百万美元，BILLS/NOTES/
    BONDS/TIPS/FRN）+ MARKETABLE_TOTAL + BILL_SHARE（Bills/市场化总额，%）
    + TOTAL_DEBT（总未偿债务，含非市场化与政府间，D.3 官方占比分母）。
    金额口径=debt_held_public_mil_amt（公众持有，剔除政府间持有）；
    TOTAL_DEBT 取 total_mil_amt。源字段缺失时抛 ValueError。
    """
    rows = _fetch_all_pages(f"{API_BASE_DEBT}/mspd/mspd_table_1", MSPD_COLUMNS, "record_date")
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    if not {"record_date", "security_type_desc", "security_class_desc"}.issubset(df.columns):
        raise ValueError(f"MSPD 字段缺失: {list(df.columns)}")
    df["record_date"] = pd.to_datetime(df["record_date"])

    # 总未偿债务行（D.3 海外官方占比分母），先于 Marketable 过滤取出
    total_rows = df[df["security_type_desc"] == "Total Public Debt Outstanding"]
    total = pd.to_numeric(total_rows["total_mil_amt"], errors="coerce")
    total.index = total_rows["record_date"]

    df["_type"] = df["security_class_desc"].map(lambda d: _MSPD_TYPE_MAP.get(d or "", ""))
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
        resp = requests.get(url, params=params, timeout=60, proxies={"http": None, "https": None})
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
    failed: list[str] = []

    # ── 拍卖结果 ──
    # 三个数据集各自独立：任一源挂了不得连带停写其它两个（TA_WS 抖动不该拖住 mspd），
    # 失败一律在收尾 exit 1 标红，不静默放行。
    path = out_dir / "auction_results.csv"
    try:
        previous = pd.read_csv(path) if path.exists() else None
        results = fetch_auction_results(previous)
        if results.empty:
            raise ValueError("无数据")
        results.to_csv(path, index_label="auction_date")
        print(f"Treasury: → {path} ({len(results.columns)} 列 × {len(results)} 行)")
    except Exception as e:
        print(f"Treasury: auction_results 拉取失败: {e}")
        failed.append("auction_results")

    # ── 未来拍卖日历 ──
    try:
        upcoming = fetch_upcoming_auctions()
        if upcoming.empty:
            print("Treasury: upcoming_auctions 无数据（周末/假日正常）")
        else:
            path = out_dir / "upcoming_auctions.csv"
            upcoming.to_csv(path, index_label="auction_date")
            print(f"Treasury: → {path} ({len(upcoming.columns)} 列 × {len(upcoming)} 行)")
    except Exception as e:
        print(f"Treasury: upcoming_auctions 拉取失败: {e}")
        failed.append("upcoming_auctions")

    # ── 月度未偿债务结构（Bill 占比）──
    try:
        mspd = fetch_mspd()
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
    except Exception as e:
        print(f"Treasury: mspd 拉取失败: {e}")
        failed.append("mspd")

    if failed:
        print(f"Treasury: 失败的数据集: {', '.join(failed)}")
        raise SystemExit(1)
