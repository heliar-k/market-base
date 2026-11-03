# 纽约联储 Household Debt and Credit 季度报告：信用卡逾期率。

import io
import logging
import re
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests

from ._io import upsert_timeseries

logger = logging.getLogger(__name__)

HOUSEHOLD_PAGE_URL = "https://www.newyorkfed.org/microeconomics/hhdc/background.html"
CREDIT_CARD_90PLUS_DELINQ_PCT = "CREDIT_CARD_90PLUS_DELINQ_PCT"
CREDIT_CARD_30PLUS_FLOW_4Q_SUM_PCT = "CREDIT_CARD_30PLUS_FLOW_4Q_SUM_PCT"
OUTPUT_COLUMNS = [CREDIT_CARD_90PLUS_DELINQ_PCT, CREDIT_CARD_30PLUS_FLOW_4Q_SUM_PCT]

_WORKBOOK_LINK_RE = re.compile(
    r"href=[\"'](?P<href>[^\"']*hhd_c_report_(?P<year>\d{4})q(?P<quarter>[1-4])"
    r"\.xlsx(?:\?[^\"']*)?)[\"']",
    re.IGNORECASE,
)
_QUARTER_RE = re.compile(r"(?P<year>\d{2}):Q(?P<quarter>[1-4])", re.IGNORECASE)
_QUARTER_ENDS = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}
_DIRECT_PROXIES = {"http": None, "https": None}


def _find_latest_workbook_url(page_html: str) -> str:
    """从官网报告页选择季度最新的 XLSX 链接。"""
    matches = list(_WORKBOOK_LINK_RE.finditer(page_html))
    if not matches:
        raise ValueError("纽约联储 Household Debt 页面未列出季度 XLSX")
    latest = max(matches, key=lambda m: (int(m.group("year")), int(m.group("quarter"))))
    return urljoin(HOUSEHOLD_PAGE_URL, latest.group("href"))


def _quarter_end(value: object) -> str | None:
    """将源文件的 03:Q1 形式转换为季度末 ISO 日期。"""
    match = _QUARTER_RE.fullmatch(str(value).strip())
    if not match:
        return None
    year = 2000 + int(match.group("year"))
    quarter = int(match.group("quarter"))
    return f"{year}-{_QUARTER_ENDS[quarter]}"


def _read_credit_card_series(
    workbook: pd.ExcelFile,
    sheet_name: str,
    required_labels: tuple[str, ...],
    output_name: str,
) -> pd.Series:
    """按表名、标题与 CC 表头提取一列，避免依赖表头所在行号。"""
    raw = pd.read_excel(workbook, sheet_name=sheet_name, header=None)
    heading = " ".join(str(value) for value in raw.iloc[:, 0].dropna().head(6))
    missing_labels = [label for label in required_labels if label not in heading]
    if missing_labels:
        raise ValueError(f"{sheet_name} 标题口径不符，缺少: {missing_labels}")

    cc_locations = [
        (row, col)
        for row in range(len(raw))
        for col in range(raw.shape[1])
        if str(raw.iat[row, col]).strip().upper() == "CC"
    ]
    if len(cc_locations) != 1:
        raise ValueError(f"{sheet_name} 的 CC 表头数量异常: {cc_locations}")
    header_row, cc_col = cc_locations[0]

    records: dict[str, float] = {}
    for row in range(header_row + 1, len(raw)):
        date = _quarter_end(raw.iat[row, 0])
        if date is None:
            continue
        value = pd.to_numeric(raw.iat[row, cc_col], errors="coerce")
        if pd.isna(value):
            raise ValueError(f"{sheet_name} {date} 的 CC 数值缺失")
        if date in records:
            raise ValueError(f"{sheet_name} 出现重复季度: {date}")
        records[date] = float(value)

    series = pd.Series(records, name=output_name, dtype="float64").sort_index()
    series.index.name = "date"
    if series.empty:
        raise ValueError(f"{sheet_name} 没有季度观测")
    if not series.between(0, 100).all():
        raise ValueError(f"{sheet_name} 的 CC 百分比超出 0–100")
    return series


def parse_nyfed_household_xlsx(source: str | Path | bytes) -> pd.DataFrame:
    """解析官方 XLSX，返回季度末索引及信用卡 90+、30+ 两个百分比口径。"""
    if isinstance(source, bytes):
        source = io.BytesIO(source)
    with pd.ExcelFile(source) as workbook:
        serious = _read_credit_card_series(
            workbook,
            "Page 12 Data",
            ("Percent of Balance 90+ Days Delinquent by Loan Type",),
            CREDIT_CARD_90PLUS_DELINQ_PCT,
        )
        early_flow = _read_credit_card_series(
            workbook,
            "Page 13 Data",
            ("New Delinquent", "30 or more days delinquent"),
            CREDIT_CARD_30PLUS_FLOW_4Q_SUM_PCT,
        )

    if not serious.index.equals(early_flow.index):
        raise ValueError("90+ 与 30+ 系列的季度范围不一致")
    return pd.concat([serious, early_flow], axis=1)[OUTPUT_COLUMNS]


def fetch_nyfed_household() -> pd.DataFrame:
    """从官网发现并下载最新季度 XLSX，返回全量季度历史。"""
    page_response = requests.get(HOUSEHOLD_PAGE_URL, timeout=60, proxies=_DIRECT_PROXIES)
    page_response.raise_for_status()
    workbook_url = _find_latest_workbook_url(page_response.text)
    logger.info("NY Fed Household Debt XLSX: %s", workbook_url)

    response = requests.get(workbook_url, timeout=120, proxies=_DIRECT_PROXIES)
    response.raise_for_status()
    result = parse_nyfed_household_xlsx(response.content)
    logger.info("NY Fed Household Debt: %d 个季度，截至 %s", len(result), result.index[-1])
    return result


def upsert_nyfed_household(
    filepath: Path,
    df: pd.DataFrame,
    backfill: bool = False,
) -> None:
    """以季度末观测日为键 upsert 完整历史。"""
    upsert_timeseries(filepath, df, backfill=backfill, column_order=OUTPUT_COLUMNS)


if __name__ == "__main__":
    import argparse

    from ..config import ROOT

    parser = argparse.ArgumentParser(description="NY Fed Household Debt 信用卡季度逾期率")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data" / "nyfed_household" / "household_credit.csv",
        help="输出 CSV；默认可传入 .scratch 路径进行真实抓取验证",
    )
    parser.add_argument("--backfill", action="store_true", help="全量覆盖；默认为 upsert")
    args = parser.parse_args()

    df = fetch_nyfed_household()
    upsert_nyfed_household(args.output, df, backfill=args.backfill)
    print(
        f"NY Fed Household Debt upsert: → {args.output} "
        f"({len(df.columns)} 指标 × {len(df)} 行, {df.index[0]} → {df.index[-1]}, "
        f"最新信用卡 90+ = {df[CREDIT_CARD_90PLUS_DELINQ_PCT].iloc[-1]:.2f}%, "
        f"30+ 四季流量 = {df[CREDIT_CARD_30PLUS_FLOW_4Q_SUM_PCT].iloc[-1]:.2f}%)"
    )
