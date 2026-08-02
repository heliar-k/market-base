"""_io.py 持久化接缝的 TDD 测试（C2 加深）。

钉住三种写入语义 + 统一读法：
  1. upsert: 同日新值覆盖 / 新日追加 / 缺列保留旧值;
  2. backfill: 全量覆盖（清旧格式 junk）;
  3. OHLCV 行覆盖: 完整新行覆盖旧行（ibkr save_data 旧语义的等价性）;
  4. load_timeseries: date 解析为索引; 文件不存在/空文件返回空 DataFrame。
"""

import pandas as pd

from src.fetchers._io import load_timeseries, upsert_timeseries


def _write(tmp_path, name: str, df: pd.DataFrame) -> None:
    path = tmp_path / name
    df.to_csv(path, index_label="date")
    return path


# ── upsert: 合并语义 ────────────────────────────────────────────────────────


def test_upsert_new_file_writes_date_as_first_col(tmp_path):
    path = tmp_path / "a.csv"
    df = pd.DataFrame({"v": [1.0]}, index=pd.Index(["2024-01-01"]))

    upsert_timeseries(path, df)

    raw = pd.read_csv(path)
    assert list(raw.columns)[0] == "date"
    assert raw["date"].iloc[0] == "2024-01-01"


def test_upsert_same_date_overwrites_value(tmp_path):
    path = _write(tmp_path, "a.csv", pd.DataFrame({"v": [1.0]}, index=["2024-01-01"]))
    new = pd.DataFrame({"v": [2.0]}, index=["2024-01-01"])

    upsert_timeseries(path, new)

    df = load_timeseries(path)
    assert df.loc["2024-01-01", "v"] == 2.0
    assert len(df) == 1


def test_upsert_new_date_appends(tmp_path):
    path = _write(tmp_path, "a.csv", pd.DataFrame({"v": [1.0]}, index=["2024-01-01"]))
    new = pd.DataFrame({"v": [2.0]}, index=["2024-01-02"])

    upsert_timeseries(path, new)

    df = load_timeseries(path)
    assert df.index.astype(str).tolist() == ["2024-01-01", "2024-01-02"]


def test_upsert_missing_column_keeps_old(tmp_path):
    path = _write(
        tmp_path,
        "a.csv",
        pd.DataFrame({"a": [1.0], "b": [10.0]}, index=["2024-01-01"]),
    )
    new = pd.DataFrame({"a": [2.0]}, index=["2024-01-01"])

    upsert_timeseries(path, new)

    df = load_timeseries(path)
    assert df.loc["2024-01-01", "a"] == 2.0
    assert df.loc["2024-01-01", "b"] == 10.0


def test_upsert_new_column_is_added(tmp_path):
    path = _write(tmp_path, "a.csv", pd.DataFrame({"a": [1.0]}, index=["2024-01-01"]))
    new = pd.DataFrame({"a": [2.0], "c": [3.0]}, index=["2024-01-02"])

    upsert_timeseries(path, new)

    df = load_timeseries(path)
    assert set(df.columns) == {"a", "c"}
    assert pd.isna(df.loc["2024-01-01", "c"])  # 旧行缺新列 → NaN 保留
    assert df.loc["2024-01-02", "c"] == 3.0


# ── backfill: 全量覆盖 ──────────────────────────────────────────────────────


def test_backfill_overwrites_clearing_old_junk(tmp_path):
    # 旧格式文件：有 junk 列 + 多余日期
    path = tmp_path / "a.csv"
    old = pd.DataFrame(
        {"v": [1.0, 2.0], "junk": [9.0, 9.0]}, index=["2024-01-01", "2024-01-02"]
    )
    old.to_csv(path, index_label="date")
    new = pd.DataFrame({"v": [5.0]}, index=["2024-01-03"])

    upsert_timeseries(path, new, backfill=True)

    df = load_timeseries(path)
    assert list(df.columns) == ["v"]  # junk 被清掉
    assert df.index.astype(str).tolist() == ["2024-01-03"]
    assert df.loc["2024-01-03", "v"] == 5.0


# ── OHLCV 行覆盖（旧 save_data 语义）────────────────────────────────────────


def test_ohlcv_full_row_replaces_old(tmp_path):
    """完整 OHLCV 行（无 NaN）覆盖旧行，等价于 concat + duplicated(keep=last)。"""
    path = _write(
        tmp_path,
        "a.csv",
        pd.DataFrame(
            {"open": [10.0], "close": [11.0], "volume": [100]},
            index=["2024-01-01"],
        ),
    )
    new = pd.DataFrame(
        {"open": [12.0], "close": [13.0], "volume": [200]},
        index=["2024-01-01"],
    )

    upsert_timeseries(path, new)

    df = load_timeseries(path)
    assert df.loc["2024-01-01", "close"] == 13.0
    assert df.loc["2024-01-01", "volume"] == 200
    assert len(df) == 1


def test_upsert_empty_df_keeps_existing(tmp_path):
    path = _write(tmp_path, "a.csv", pd.DataFrame({"v": [1.0]}, index=["2024-01-01"]))

    upsert_timeseries(path, pd.DataFrame())

    df = load_timeseries(path)
    assert len(df) == 1
    assert df.loc["2024-01-01", "v"] == 1.0


# ── load_timeseries: 统一读法 ───────────────────────────────────────────────


def test_load_timeseries_parses_date_index(tmp_path):
    path = tmp_path / "a.csv"
    pd.DataFrame({"v": [1.0]}, index=pd.to_datetime(["2024-01-01"])).to_csv(
        path, index_label="date"
    )

    df = load_timeseries(path)

    assert df.index.name == "date"
    assert isinstance(df.index, pd.DatetimeIndex)


def test_load_timeseries_string_date_col(tmp_path):
    """兼容字符串 date 列（commodities 旧格式风格）。"""
    path = tmp_path / "a.csv"
    pd.DataFrame({"date": ["2024-01-01 00:00:00"], "close": [1.0]}).to_csv(
        path, index=False
    )

    df = load_timeseries(path)

    assert df.index.name == "date"
    assert df.loc["2024-01-01", "close"] == 1.0


def test_load_timeseries_missing_file_returns_empty(tmp_path):
    df = load_timeseries(tmp_path / "nope.csv")
    assert df.empty


def test_load_timeseries_corrupt_file_returns_empty(tmp_path):
    """损坏 CSV（写盘中断）视为无数据，不抛异常。"""
    path = tmp_path / "a.csv"
    path.write_text('date,open,close\n"2024-01-01,10.0,\n')

    df = load_timeseries(path)

    assert df.empty


def test_load_timeseries_header_only_returns_empty(tmp_path):
    path = tmp_path / "a.csv"
    path.write_text("date,open,close\n")
    assert load_timeseries(path).empty
