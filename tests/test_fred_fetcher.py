"""fetch_fred 元数据回归测试。

fredapi 0.5.x 的 get_series_info 返回 pandas Series（旧版是 dict），
旧代码 `if info and info.get(...)` 对 Series 求布尔 → ValueError →
全部系列的 last_updated 落不到 data/fred/_release_dates.csv，
该文件冻结在坏掉那天，通胀/信用/拍卖页的「发布时间」从此不再更新。
"""

import pandas as pd

import src.config as cfg
import src.fetchers.fred_fetcher as mod
from src.fetchers.fred_fetcher import fetch_all_fred


class _FakeFred:
    """最小 Fred 替身：序列返回两期观测，元数据返回 Series（0.5.x 行为）。"""

    def get_series(self, series_id):
        return pd.Series([1.0, 2.0], index=pd.to_datetime(["2026-08-01", "2026-09-01"]))

    def get_series_info(self, series_id):
        return pd.Series(
            {"id": series_id, "last_updated": "2026-09-10 16:03:31-05"}, dtype=object
        )


def test_release_dates_written_when_info_is_series(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_get_fred", lambda: _FakeFred())
    monkeypatch.setattr(cfg, "ROOT", tmp_path)
    monkeypatch.setattr(cfg.config, "fred_series", {"inflation": {"T10YIE": "T10YIE"}})
    (tmp_path / "data" / "fred").mkdir(parents=True)

    out = fetch_all_fred()

    rel = pd.read_csv(tmp_path / "data" / "fred" / "_release_dates.csv", dtype=str)
    assert (
        rel.loc[rel["series_id"] == "T10YIE", "last_updated"]
        .iloc[0]
        .startswith("2026-09-10")
    )
    assert out["inflation"].index.tolist() == ["2026-08-01", "2026-09-01"]
