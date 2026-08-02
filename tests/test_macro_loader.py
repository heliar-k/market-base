"""src/macro.py 分类加载管线的 TDD 测试（C1 加深）。

加载管线（读 CSV → 伙伴 join → derive）收进 macro.py 后，测试钉住：
  1. RRP 单位归一化只做一次（旧 server 预乘 ×1000 → 净流动性错 1000 倍的回归）；
  2. 单分类伙伴 left join / 多分类并集 outer join 语义；
  3. 端点直调（monkeypatch config.ROOT），不引 httpx。
"""

import pandas as pd
import pytest

from src.macro import (
    categories_for,
    load_macro_categories,
    load_macro_category,
    read_macro_category,
    rrp_in_millions,
)


def _write_fred_csv(tmp_path, category: str, df: pd.DataFrame) -> None:
    """写 data/fred/{category}/{category}.csv（date 索引）。"""
    path = tmp_path / "data" / "fred" / category
    path.mkdir(parents=True, exist_ok=True)
    df.to_csv(path / f"{category}.csv", index_label="date")


def _liquidity_fixture() -> pd.DataFrame:
    """流动性分类：WALCL/WTREGEN 百万美元，RRPONTSYD 十亿美元（FRED 原始单位）。"""
    return pd.DataFrame(
        {
            "WALCL": [6_700_000, 6_800_000],
            "RRPONTSYD": [1.4, 1.2],
            "WTREGEN": [800_000, 750_000],
            "WRESBAL": [3_200_000, 3_300_000],
        },
        index=pd.to_datetime(["2024-01-01", "2024-01-02"]),
    )


# ── read_macro_category ──────────────────────────────────────────────────────


def test_read_macro_category_returns_raw_df(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.ROOT", tmp_path)
    _write_fred_csv(tmp_path, "liquidity", _liquidity_fixture())

    df = read_macro_category("liquidity")

    assert df.index.name == "date"
    assert set(df.columns) == {"WALCL", "RRPONTSYD", "WTREGEN", "WRESBAL"}
    assert "NET_LIQUIDITY" not in df.columns  # 原始读取不派生


def test_read_macro_category_missing_csv_raises(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.ROOT", tmp_path)

    with pytest.raises(FileNotFoundError):
        read_macro_category("nope")


# ── load_macro_category ──────────────────────────────────────────────────────


def test_load_macro_category_net_liquidity_normalized_once(tmp_path, monkeypatch):
    """回归（C1 核心 bug）：RRP 单位归一化只在 derive_macro 内做一次。

    server 旧代码预乘 RRPONTSYD×1000 再调 derive_macro → 净流动性错 1000 倍。
    loader 不做任何预乘，NET_LIQUIDITY 必须等于 WALCL − RRP×1000 − WTREGEN。
    """
    monkeypatch.setattr("src.config.ROOT", tmp_path)
    _write_fred_csv(tmp_path, "liquidity", _liquidity_fixture())

    df = load_macro_category("liquidity")

    expected = df["WALCL"] - df["RRPONTSYD"] * 1000 - df["WTREGEN"]
    pd.testing.assert_series_equal(df["NET_LIQUIDITY"], expected, check_names=False)
    # 量级钉死：若 RRP 被预乘（旧 bug），这里会是 ~1.4e9 级别而非 5.9e6
    assert df["NET_LIQUIDITY"].iloc[0] == pytest.approx(
        6_700_000 - 1.4 * 1000 - 800_000
    )


def test_load_macro_category_joins_partners_for_cross_category(tmp_path, monkeypatch):
    """rates + tips 伙伴 left join → BEI_10Y 可算。"""
    monkeypatch.setattr("src.config.ROOT", tmp_path)
    _write_fred_csv(
        tmp_path,
        "rates",
        pd.DataFrame(
            {"DGS10": [4.0, 4.2]},
            index=pd.to_datetime(["2024-01-01", "2024-01-02"]),
        ),
    )
    _write_fred_csv(
        tmp_path,
        "tips",
        pd.DataFrame(
            {"DFII10": [2.0, 2.1]},
            index=pd.to_datetime(["2024-01-01", "2024-01-02"]),
        ),
    )

    df = load_macro_category("rates")

    assert "BEI_10Y" in df.columns
    assert df["BEI_10Y"].iloc[0] == pytest.approx((4.0 - 2.0) * 100)


# ── load_macro_categories ────────────────────────────────────────────────────


def test_load_macro_categories_union_no_duplicate_suffixes(tmp_path, monkeypatch):
    """回归：correlate 旧实现逐分类 join 伙伴再 outer 合并 → 列重复产生 _x/_y，
    BEI 请求直接 404。loader 先并集再合并原始帧 → 无后缀列，BEI_5Y 可算。
    """
    monkeypatch.setattr("src.config.ROOT", tmp_path)
    _write_fred_csv(
        tmp_path,
        "rates",
        pd.DataFrame({"DGS5": [4.0]}, index=pd.to_datetime(["2024-01-01"])),
    )
    _write_fred_csv(
        tmp_path,
        "tips",
        pd.DataFrame({"DFII5": [2.0]}, index=pd.to_datetime(["2024-01-01"])),
    )

    df = load_macro_categories({"rates", "tips"})

    assert not any(col.endswith("_x") or col.endswith("_y") for col in df.columns)
    assert "BEI_5Y" in df.columns
    assert df["BEI_5Y"].iloc[0] == pytest.approx(200)


def test_load_macro_categories_missing_raises(tmp_path, monkeypatch):
    """请求的分类缺失必须报错。"""
    monkeypatch.setattr("src.config.ROOT", tmp_path)
    _write_fred_csv(
        tmp_path,
        "rates",
        pd.DataFrame({"DGS5": [4.0]}, index=pd.to_datetime(["2024-01-01"])),
    )

    with pytest.raises(FileNotFoundError):
        load_macro_categories({"rates", "tips"})


def test_load_macro_categories_missing_partner_is_lenient(tmp_path, monkeypatch):
    """伙伴分类缺失则跳过（与单分类语义一致），不 404。"""
    monkeypatch.setattr("src.config.ROOT", tmp_path)
    _write_fred_csv(
        tmp_path,
        "rates",
        pd.DataFrame({"DGS5": [4.0]}, index=pd.to_datetime(["2024-01-01"])),
    )
    # tips 是 rates 的伙伴（BEI），但 CSV 不存在 → 跳过，只返回 rates
    df = load_macro_categories({"rates"})

    assert list(df.columns) == ["DGS5"]


def test_load_macro_categories_empty_set_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.ROOT", tmp_path)
    df = load_macro_categories(set())
    assert df.empty


def test_rrp_in_millions_only_touches_rrp(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.ROOT", tmp_path)
    _write_fred_csv(tmp_path, "liquidity", _liquidity_fixture())
    df = load_macro_category("liquidity")

    out = rrp_in_millions(df)

    # RRP 十亿→百万，其他原始列与派生列原样
    assert out["RRPONTSYD"].iloc[0] == 1400
    assert out["WALCL"].iloc[0] == 6_700_000
    pd.testing.assert_series_equal(
        out["NET_LIQUIDITY"], df["NET_LIQUIDITY"], check_names=False
    )


# ── categories_for ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name,expected",
    [
        ("NET_LIQUIDITY", ["liquidity"]),  # 派生：输入列都在 liquidity
        ("BEI_10Y", ["rates", "tips"]),  # 派生：跨 rates+tips
        ("SOFR_IORB_SPREAD_BP", ["rates"]),
        ("DGS10", ["rates"]),  # 原始：所在分类
        ("DFII5", ["tips"]),
        ("NOPE", None),  # 未知
    ],
)
def test_categories_for(name, expected):
    assert categories_for(name) == expected


# ── server 端点直调（接缝测试，不引 httpx）──────────────────────────────────


def test_server_get_macro_net_liquidity_correct(tmp_path, monkeypatch):
    """端点到接缝：get_macro 输出 NET_LIQUIDITY 量级正确，RRP 显示单位百万。"""
    monkeypatch.setattr("src.config.ROOT", tmp_path)
    _write_fred_csv(tmp_path, "liquidity", _liquidity_fixture())

    from src.server import get_macro

    rows = get_macro("liquidity")

    nl = [r["NET_LIQUIDITY"] for r in rows if r["NET_LIQUIDITY"] is not None]
    assert nl == pytest.approx(
        [6_700_000 - 1.4 * 1000 - 800_000, 6_800_000 - 1.2 * 1000 - 750_000]
    )
    # 显示层：RRP 以百万返回（与旧响应契约一致，供前端同图）
    rrp = [r["RRPONTSYD"] for r in rows]
    assert rrp == pytest.approx([1400.0, 1200.0])


def test_server_get_macro_correlate_bei_works(tmp_path, monkeypatch):
    """回归：旧实现 correlate 请求 BEI_5Y 因 _x/_y 后缀列 404；新 loader 可返回。"""
    monkeypatch.setattr("src.config.ROOT", tmp_path)
    _write_fred_csv(
        tmp_path,
        "rates",
        pd.DataFrame({"DGS5": [4.0]}, index=pd.to_datetime(["2024-01-01"])),
    )
    _write_fred_csv(
        tmp_path,
        "tips",
        pd.DataFrame({"DFII5": [2.0]}, index=pd.to_datetime(["2024-01-01"])),
    )

    from src.server import get_macro_correlate

    out = get_macro_correlate("BEI_5Y")

    assert out["indicators"]["BEI_5Y"]["category"] == "derived"
    assert out["indicators"]["BEI_5Y"]["data"][0]["value"] == pytest.approx(200)


def test_server_get_liquidity_overview_correct(tmp_path, monkeypatch):
    """liquidity overview 端点走 loader；派生列正确 + RRP 显示百万
    （stacked 同单位）。"""
    monkeypatch.setattr("src.config.ROOT", tmp_path)
    _write_fred_csv(tmp_path, "liquidity", _liquidity_fixture())

    from src.server import get_liquidity_overview

    out = get_liquidity_overview()

    nl = out["summary"]["NET_LIQUIDITY"]["latest_value"]
    assert nl == pytest.approx(6_800_000 - 1.2 * 1000 - 750_000)
    # 卡片/stacked 用百万：RRP 1.2 十亿 → 1200 百万
    assert out["summary"]["RRPONTSYD"]["latest_value"] == pytest.approx(1200)
    stacked_rrp = out["stacked"]["RRPONTSYD"][-1]["value"]
    assert stacked_rrp == pytest.approx(3_300_000 + 750_000 + 1200)
