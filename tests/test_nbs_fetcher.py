import pytest

from src.fetchers.nbs_fetcher import (
    INCOME_LEVEL,
    INCOME_REAL_GROWTH,
    INCOME_SERIES,
    es_data_params,
    parse_es_data,
    period_end,
)

# 摘自 2026-11-03 官方 esData 实际响应，保留解析所需字段。
OFFICIAL_INCOME_RESPONSE = {
    "data": [
        {
            "code": "202603SS",
            "name": "2026年第三季度",
            "values": [
                {
                    "i_showname": "居民人均可支配收入累计增长 (%) ",
                    "_id": "7abcbb6f7c844b669d43da985d3f6ff2",
                    "du_name": "%",
                    "value": "4.4",
                    "da": "000000000000",
                    "da_name": "全国",
                }
            ],
        },
        {
            "code": "202602SS",
            "name": "2026年第二季度",
            "values": [
                {
                    "i_showname": "居民人均可支配收入累计增长 (%) ",
                    "_id": "7abcbb6f7c844b669d43da985d3f6ff2",
                    "du_name": "%",
                    "value": "4.2",
                    "da": "000000000000",
                    "da_name": "全国",
                }
            ],
        },
    ],
    "success": True,
    "state": 20000,
    "message": "成功",
}


def test_parse_official_income_response() -> None:
    df = parse_es_data(OFFICIAL_INCOME_RESPONSE, (INCOME_REAL_GROWTH,))

    assert df.index.name == "date"
    assert df.index.tolist() == ["2026-06-30", "2026-09-30"]
    assert df.columns.tolist() == ["nbs_income_real_yoy_ytd_pct"]
    assert df.iloc[:, 0].tolist() == [4.2, 4.4]


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("202603SS", "2026-09-30"),
        ("202604SS", "2026-12-31"),
        ("202609MM", "2026-09-30"),
        ("202602MM", "2026-02-28"),
        ("2025YY", "2025-12-31"),
    ],
)
def test_period_end(code, expected) -> None:
    assert period_end(code) == expected


def test_es_data_params_for_full_national_history() -> None:
    params = es_data_params(INCOME_SERIES, "201301SS", "202603SS")

    assert params == {
        "cid": "ec2d57ed282f456e8d025aff035b4fad",
        "indicatorIds": [INCOME_LEVEL.indicator_id, INCOME_REAL_GROWTH.indicator_id],
        "daCatalogId": "",
        "das": [{"text": "全国", "value": "000000000000"}],
        "showType": 1,
        "dts": ["201301SS-202603SS"],
    }


def test_parse_rejects_missing_indicator() -> None:
    with pytest.raises(ValueError, match="缺少有效指标数据"):
        parse_es_data(OFFICIAL_INCOME_RESPONSE, (INCOME_LEVEL,))


def test_parse_rejects_unsuccessful_response() -> None:
    payload = {"success": False, "state": 40000, "message": "查询失败", "data": None}

    with pytest.raises(ValueError, match="NBS 查询失败"):
        parse_es_data(payload, (INCOME_REAL_GROWTH,))
