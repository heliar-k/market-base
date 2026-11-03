import argparse
from dataclasses import dataclass
from http.cookiejar import MozillaCookieJar
from pathlib import Path

import pandas as pd
import requests

from ..config import ROOT

API_ROOT = "https://data.stats.gov.cn/dg/website/publicrelease/web/external"
ES_DATA_URL = f"{API_ROOT}/stream/esData"
NATIONAL_AREA = {"text": "全国", "value": "000000000000"}
DEFAULT_START = "201301SS"


@dataclass(frozen=True)
class IndicatorSpec:
    indicator_id: str
    column: str
    show_name: str
    unit: str


@dataclass(frozen=True)
class SeriesSpec:
    cid: str
    indicators: tuple[IndicatorSpec, ...]


INCOME_LEVEL = IndicatorSpec(
    indicator_id="bb5699c5ad534b568cca7c946227225a",
    column="nbs_income_per_capita_ytd_cny",
    show_name="居民人均可支配收入累计值 (元)",
    unit="元",
)
INCOME_REAL_GROWTH = IndicatorSpec(
    indicator_id="7abcbb6f7c844b669d43da985d3f6ff2",
    column="nbs_income_real_yoy_ytd_pct",
    show_name="居民人均可支配收入累计增长 (%)",
    unit="%",
)
CONSUMPTION_LEVEL = IndicatorSpec(
    indicator_id="8e8fb36d72e544658caebd7419900d2a",
    column="nbs_consumption_per_capita_ytd_cny",
    show_name="居民人均消费支出累计值 (元)",
    unit="元/人",
)
CONSUMPTION_REAL_GROWTH = IndicatorSpec(
    indicator_id="1158b14b49ed4337adbe255c1e12341e",
    column="nbs_consumption_real_yoy_ytd_pct",
    show_name="居民人均消费支出累计增长 (%)",
    unit="%",
)

INCOME_SERIES = SeriesSpec(
    cid="ec2d57ed282f456e8d025aff035b4fad",
    indicators=(INCOME_LEVEL, INCOME_REAL_GROWTH),
)
CONSUMPTION_SERIES = SeriesSpec(
    cid="71e8c15775d242b8b212edb4cab6e808",
    indicators=(CONSUMPTION_LEVEL, CONSUMPTION_REAL_GROWTH),
)
HOUSEHOLD_SERIES = (INCOME_SERIES, CONSUMPTION_SERIES)
HOUSEHOLD_COLUMNS = [
    indicator.column for series in HOUSEHOLD_SERIES for indicator in series.indicators
]


def period_end(code: str) -> str:
    """把 NBS 的 YYYYMMSS / YYYYMMMM / YYYYYY 编码转换为期末日期。"""
    if code.endswith("SS"):
        year, quarter = int(code[:4]), int(code[4:6])
        if len(code) != 8 or quarter not in range(1, 5):
            raise ValueError(f"无效季度编码: {code}")
        return pd.Period(f"{year}Q{quarter}", freq="Q").end_time.strftime("%Y-%m-%d")
    if code.endswith("MM"):
        year, month = int(code[:4]), int(code[4:6])
        if len(code) != 8 or month not in range(1, 13):
            raise ValueError(f"无效月度编码: {code}")
        return pd.Period(f"{year}-{month:02d}", freq="M").end_time.strftime("%Y-%m-%d")
    if code.endswith("YY") and len(code) == 6:
        return pd.Period(code[:4], freq="Y").end_time.strftime("%Y-%m-%d")
    raise ValueError(f"无效 NBS 报告期编码: {code}")


def _number(value: object) -> float | None:
    if value is None or value in {"", "...", "…", "--"}:
        return None
    number = float(str(value).replace(",", ""))
    if not pd.notna(number) or number in {float("inf"), float("-inf")}:
        raise ValueError(f"无效 NBS 数值: {value!r}")
    return number


def parse_es_data(payload: dict, indicators: tuple[IndicatorSpec, ...]) -> pd.DataFrame:
    """解析 esData 全国时间序列，指标缺失或元数据变化时抛出异常。"""
    if payload.get("success") is not True or payload.get("state") != 20000:
        raise ValueError(
            f"NBS 查询失败: {payload.get('message')!r}, state={payload.get('state')!r}"
        )
    data = payload.get("data")
    if not isinstance(data, list):
        raise ValueError("NBS esData 响应缺少 data 数组")

    by_id = {indicator.indicator_id: indicator for indicator in indicators}
    if len(by_id) != len(indicators):
        raise ValueError("指标 UUID 重复")
    rows: dict[str, dict[str, float | None]] = {}
    found: set[str] = set()
    for period in data:
        date = period_end(period["code"])
        row = rows.setdefault(date, {})
        for value in period.get("values", []):
            indicator = by_id.get(value.get("_id"))
            if indicator is None:
                continue
            if value.get("da") != NATIONAL_AREA["value"]:
                raise ValueError(f"指标 {indicator.column} 返回了非全国地区: {value.get('da')!r}")
            if value.get("i_showname", "").strip() != indicator.show_name:
                raise ValueError(
                    f"指标 {indicator.indicator_id} 名称变化: {value.get('i_showname')!r}"
                )
            if value.get("du_name") != indicator.unit:
                raise ValueError(f"指标 {indicator.column} 单位变化: {value.get('du_name')!r}")
            if indicator.column in row:
                raise ValueError(f"指标 {indicator.column} 在 {date} 重复")
            number = _number(value.get("value"))
            row[indicator.column] = number
            if number is not None:
                found.add(indicator.indicator_id)

    missing = [indicator.column for indicator in indicators if indicator.indicator_id not in found]
    if missing:
        raise ValueError(f"NBS 响应缺少有效指标数据: {missing}")
    if not rows:
        raise ValueError("NBS esData 响应没有时间序列")

    columns = [indicator.column for indicator in indicators]
    df = pd.DataFrame.from_dict(rows, orient="index").reindex(columns=columns).sort_index()
    df.index.name = "date"
    return df.dropna(how="all")


def es_data_params(spec: SeriesSpec, start: str, end: str) -> dict:
    """构造新版发布库全国历史查询参数。"""
    if period_end(start) > period_end(end):
        raise ValueError(f"起始报告期晚于结束报告期: {start} > {end}")
    return {
        "cid": spec.cid,
        "indicatorIds": [indicator.indicator_id for indicator in spec.indicators],
        "daCatalogId": "",
        "das": [NATIONAL_AREA],
        "showType": 1,
        "dts": [f"{start}-{end}"],
    }


def _current_quarter() -> str:
    now = pd.Timestamp.now()
    return f"{now.year}{(now.month - 1) // 3 + 1:02d}SS"


def _new_session(cookie_file: Path | None = None) -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    if cookie_file is not None:
        cookies = MozillaCookieJar(cookie_file)
        cookies.load(ignore_discard=True)
        session.cookies.update(cookies)
    session.headers.update(
        {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Referer": "https://data.stats.gov.cn/dg/website/page.html#/pc/national/quarterData",
        }
    )
    return session


def fetch_nbs_household(
    start: str = DEFAULT_START,
    end: str | None = None,
    cookie_file: Path | None = None,
) -> pd.DataFrame:
    """抓取全国居民人均可支配收入、消费支出的累计值与实际累计同比增速。"""
    end = end or _current_quarter()
    session = _new_session(cookie_file)
    frames = []
    for spec in HOUSEHOLD_SERIES:
        response = session.post(ES_DATA_URL, json=es_data_params(spec, start, end), timeout=60)
        response.raise_for_status()
        frames.append(parse_es_data(response.json(), spec.indicators))
    return frames[0].join(frames[1], how="outer").reindex(columns=HOUSEHOLD_COLUMNS)


def main() -> None:
    from ._io import upsert_timeseries

    parser = argparse.ArgumentParser(description="国家统计局全国居民收支历史（新版数据发布库）")
    parser.add_argument("--start", default=DEFAULT_START, help="起始季度，格式 YYYY0NSS")
    parser.add_argument("--end", default=None, help="结束季度，格式 YYYY0NSS，默认当前季度")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "data" / "china" / "nbs_household.csv"
    )
    parser.add_argument("--cookie-file", type=Path, default=None, help="访问验证后的 cookie 文件")
    parser.add_argument("--backfill", action="store_true", help="全量覆盖输出文件")
    args = parser.parse_args()

    df = fetch_nbs_household(start=args.start, end=args.end, cookie_file=args.cookie_file)
    upsert_timeseries(args.output, df, backfill=args.backfill, column_order=HOUSEHOLD_COLUMNS)
    print(
        f"NBS 居民收支 → {args.output}（{len(df)} 个季度，"
        f"{df.index[0]} → {df.index[-1]}）\n{df.iloc[-1].to_dict()}"
    )


if __name__ == "__main__":
    main()
