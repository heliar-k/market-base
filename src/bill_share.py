"""派生日频 Bill 占比 — MSPD 月末锚定 + 拍卖净发行。

输入（本地 CSV，无需新数据源）:
  - data/treasury/mspd.csv           锚：最新月末各券种未偿（百万美元）
  - data/treasury/auction_results.csv 锚后每日发行/到期（offering_amt，美元）

输出: data/treasury/bill_share_daily.csv
  date, BILLS, MARKETABLE, BILL_SHARE — 锚日至今逐日，单位百万美元

口径说明:
  - 每日未偿 = 最新 MSPD 月末值 + Σ(锚后发行) − Σ(锚后到期)
  - 定期拍卖（auctions_query 不含 CMB）：CMB 存量波动不捕捉，误差约 ±1pp
    占比（2026-06 实测 +4.5% 存量差），阈值告警时注意
  - 分母 MARKETABLE 同理；Treasury 买回（buybacks）不反映，量级 ~0.1% 忽略

用法:
  uv run python -m src.bill_share
"""

import pandas as pd

from src.config import ROOT

MSPD_CSV = ROOT / "data" / "treasury" / "mspd.csv"
AUCTIONS_CSV = ROOT / "data" / "treasury" / "auction_results.csv"
OUT_CSV = ROOT / "data" / "treasury" / "bill_share_daily.csv"


def _net_flows(
    auctions: pd.DataFrame, anchor_date: pd.Timestamp, bills_only: bool = False
) -> pd.Series:
    """锚后逐日净发行（百万美元）。

    issued 端只算锚后新发行；matured 端算所有锚后到期（含锚前发行、锚后
    到期——其发行已计入锚余额，只应扣到期）。两端过滤独立，不能先按
    issue_date 过滤整帧，否则锚前发行的到期会被整批丢掉。
    两个 groupby 索引不同（发行日 vs 到期日），sub(fill_value=0) 在相减前
    补零——先减后 fillna 会把"该日无到期"的对齐 NaN 也抹成 0。
    """
    if bills_only:
        auctions = auctions[auctions["security_type"] == "Bill"]
    issued = (
        auctions[auctions["issue_date"] > anchor_date]
        .groupby("issue_date")["offering_amt"]
        .sum()
        / 1e6
    )
    matured = (
        auctions[auctions["maturity_date"] > anchor_date]
        .groupby("maturity_date")["offering_amt"]
        .sum()
        / 1e6
    )
    return issued.sub(matured, fill_value=0).sort_index()


def compute_daily_bill_share(
    mspd: pd.DataFrame, auctions: pd.DataFrame, end: pd.Timestamp | None = None
) -> pd.DataFrame:
    """锚定法派生日频未偿。返回 index=日期，列 BILLS/MARKETABLE/BILL_SHARE。

    mspd: index=月末(parse_dates)，列含 BILLS/MARKETABLE_TOTAL（百万美元）。
    auctions: 需 issue_date/maturity_date/security_type/offering_amt（美元）。
    锚 = mspd 最新月末；只输出锚日之后（含锚日）的逐日序列。
    end: 输出截止日（默认今天；传固定日期可保证测试确定性）。
    """
    anchor_date = mspd.index.max()
    anchor = mspd.loc[anchor_date]
    auc = auctions.dropna(subset=["issue_date", "maturity_date"]).copy()
    auc["issue_date"] = pd.to_datetime(auc["issue_date"])
    auc["maturity_date"] = pd.to_datetime(auc["maturity_date"])
    auc["offering_amt"] = pd.to_numeric(auc["offering_amt"], errors="coerce")

    net = _net_flows(auc, anchor_date)
    net_bill = _net_flows(auc, anchor_date, bills_only=True)

    days = pd.date_range(anchor_date, end or pd.Timestamp.today().normalize())
    out = pd.DataFrame(index=days)
    out["BILLS"] = anchor["BILLS"] + net_bill.reindex(days).fillna(0).cumsum()
    out["MARKETABLE"] = (
        anchor["MARKETABLE_TOTAL"] + net.reindex(days).fillna(0).cumsum()
    )
    out["BILL_SHARE"] = (out["BILLS"] / out["MARKETABLE"] * 100).round(2)
    out.index.name = "date"
    return out


def main() -> None:
    mspd = pd.read_csv(MSPD_CSV, index_col="record_date", parse_dates=True)
    auctions = pd.read_csv(AUCTIONS_CSV, index_col="auction_date", parse_dates=True)
    df = compute_daily_bill_share(mspd, auctions)
    df.to_csv(OUT_CSV)
    latest = df.iloc[-1]
    print(
        f"bill_share_daily: → {OUT_CSV}（{len(df)} 天, "
        f"{df.index[0].date()} → {df.index[-1].date()}）"
    )
    print(
        f"  最新 {latest.name.date()}: BILLS ${latest['BILLS'] / 1e6:.2f}T / "
        f"MARKETABLE ${latest['MARKETABLE'] / 1e6:.2f}T = {latest['BILL_SHARE']:.2f}%"
        f"（锚 {mspd.index.max().date()}，CMB 未计）"
    )


if __name__ == "__main__":
    main()
