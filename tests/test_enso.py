"""ENSO 专题测试：ONI/诊断研报解析（离线样例）+ enso_block/positioning 扩充结构。

不碰网络：解析函数用实测抓取的样例文本；分析层 monkeypatch _csv/asset_prices。
"""

import json

import pandas as pd
import pytest

from src import assets_analysis as A
from src.fetchers.cot_fetcher import COT_MARKETS
from src.fetchers.enso_fetcher import ONI_URL, parse_enso_discussion, parse_oni_ascii

# ── 离线样例（2026-10-17 实测 NOAA CPC 接口抓取的尾部片段与全文节选） ──────────

ONI_SAMPLE = """ SEAS  YR   TOTAL   ANOM
  DJF 1950  25.01  -1.32
  MAM 2026  28.09   0.46
  AMJ 2026  28.74   0.95
  MJJ 2026  29.02   1.39
  JJA 2026  29.09   1.80
  JAS 2026  29.12   2.16
"""

DISC_SAMPLE = """<html><body>
<strong>EL NI&Ntilde;O/SOUTHERN OSCILLATION (ENSO)</strong>
<strong>DIAGNOSTIC DISCUSSION</strong>
issued by<br>CLIMATE PREDICTION CENTER/NCEP/NWS<br>
8 October 2026
<p align="center">
<strong>ENSO Alert System Status: </font>
<a href="./enso-alert-readme.shtml"><span style="color:red">El Ni&ntilde;o Advisory</span></a></p>
<p align="justify"><u>Synopsis:</u>&nbsp;<strong>
El Ni&ntilde;o continues to strengthen, with a strong-to-very strong El Ni&ntilde;o likely through
January-March 2027 (remaining greater than an 83&#37; chance).
</strong></font></p>
<font>
<p>El Ni&ntilde;o strengthened further last month, with sea surface temperature anomalies
exceeding +4.0&deg;C in portions of the eastern equatorial Pacific. Most Ni&ntilde;o indices
increased in the past month, reaching +2.1&deg;C in Ni&ntilde;o-3.4.</p>
<p>This discussion is a consolidated effort of the National Oceanic and Atmospheric
Administration (NOAA). The next ENSO Diagnostics Discussion is scheduled for
12 November 2026.</p>
</font>
</body></html>"""


# ── parse_oni_ascii ──────────────────────────────────────────────────────────


class TestParseOni:
    def test_basic_parse(self):
        df = parse_oni_ascii(ONI_SAMPLE)
        assert list(df.columns) == ["season", "total", "oni"]
        assert len(df) == 6
        assert df.index[-1] == pd.Timestamp("2026-08-01")  # JAS 中心月 = 8
        assert df["season"].iloc[-1] == "JAS"
        assert df["oni"].iloc[-1] == pytest.approx(2.16)
        assert df["oni"].iloc[0] == pytest.approx(-1.32)

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            parse_oni_ascii("not oni data at all\nfoo bar baz qux\n")

    def test_url_constant(self):
        assert ONI_URL.startswith("https://www.cpc.ncep.noaa.gov/")


# ── parse_enso_discussion ────────────────────────────────────────────────────


class TestParseDiscussion:
    @pytest.fixture()
    def disc(self):
        return parse_enso_discussion(DISC_SAMPLE)

    def test_issued_date(self, disc):
        assert disc["as_of"] == "2026-10-08"

    def test_status(self, disc):
        assert disc["status"] == "El Niño Advisory"

    def test_headline(self, disc):
        assert "strong-to-very strong" in disc["headline"]
        assert "<" not in disc["headline"]

    def test_excerpt_and_next_release(self, disc):
        assert "Niño-3.4" in disc["excerpt"]
        assert disc["next_release"] == "12 November 2026"
        assert disc["url"].endswith("ensodisc.shtml")

    def test_garbage_page_is_incomplete(self):
        d = parse_enso_discussion("<html><body>nothing here</body></html>")
        assert d["as_of"] == "" and d["status"] == ""


# ── COT_MARKETS 农产品正则（离线：对实测市场名逐一匹配） ──────────────────────


class TestCotMarkets:
    @pytest.mark.parametrize(
        "sym,market",
        [
            ("SB", "SUGAR NO. 11 - ICE FUTURES U.S."),
            ("KC", "COFFEE C - ICE FUTURES U.S."),
            ("CC", "COCOA - ICE FUTURES U.S."),
            ("ZC", "CORN - CHICAGO BOARD OF TRADE"),
            ("ZS", "SOYBEANS - CHICAGO BOARD OF TRADE"),
            ("ZW", "WHEAT-SRW - CHICAGO BOARD OF TRADE"),
        ],
    )
    def test_match(self, sym, market):
        pat, rtype = COT_MARKETS[sym]
        assert rtype == "disagg"
        assert pd.Series([market]).str.match(pat, case=False).iloc[0]

    def test_soybeans_anchored(self):
        pat, _ = COT_MARKETS["ZS"]
        for wrong in (
            "SOYBEAN OIL - CHICAGO BOARD OF TRADE",
            "SOYBEAN MEAL - CHICAGO BOARD OF TRADE",
            "MINI SOYBEANS - CHICAGO BOARD OF TRADE",
        ):
            assert not pd.Series([wrong]).str.match(pat, case=False).iloc[0]


# ── _enso_phase 阈值 ─────────────────────────────────────────────────────────


class TestPhase:
    @pytest.mark.parametrize(
        "oni,expected",
        [
            (2.2, "超强厄尔尼诺"),
            (1.6, "强厄尔尼诺"),
            (0.7, "厄尔尼诺"),
            (0.2, "ENSO 中性"),
            (-0.7, "拉尼娜"),
            (-1.6, "强拉尼娜"),
            (-2.1, "超强拉尼娜"),
        ],
    )
    def test_thresholds(self, oni, expected):
        assert A._enso_phase(oni) == expected


# ── enso_block（monkeypatch 数据源，不碰磁盘） ────────────────────────────────


def _oni_df(values=None, start="2016-01-01"):
    if values is None:
        values = [0.5 + i * 0.35 for i in range(120)]  # 月频 10 年，覆盖 history 窗口
    idx = pd.date_range(start, periods=len(values), freq="MS")
    return pd.DataFrame(
        {"season": ["JAS"] * len(values), "total": [27.0] * len(values), "oni": values},
        index=idx,
    )


def _prices_df():
    idx = pd.date_range("2026-01-01", periods=120, freq="D")
    df = pd.DataFrame(
        {k: 100.0 for k in ["Sugar", "Coffee", "Cocoa", "Soybean", "Corn", "Wheat"]}, index=idx
    )
    for i, k in enumerate(df.columns):
        df[k] = 100 + i + df.index.dayofyear * (0.5 + i * 0.2)  # 各品种不同斜率，20D 必有值
    return df


class TestEnsoBlock:
    def _patch(self, monkeypatch, tmp_path, oni=None, cot=None, pool=None, infl=None):
        monkeypatch.setattr(A, "asset_prices", _prices_df)
        monkeypatch.setattr(
            A,
            "_csv",
            lambda p, **kw: {
                "macro/enso.csv": oni if oni is not None else _oni_df(),
                "cot/cot.csv": cot if cot is not None else pd.DataFrame(),
                "etf/pool_prices.csv": pool if pool is not None else pd.DataFrame(),
                "fred/inflation/inflation.csv": infl if infl is not None else pd.DataFrame(),
            }.get(p, pd.DataFrame()),
        )
        disc_file = tmp_path / "data" / "macro" / "enso_discussion.json"
        disc_file.parent.mkdir(parents=True, exist_ok=True)
        disc_file.write_text(
            json.dumps(
                {
                    "status": "El Niño Advisory",
                    "as_of": "2026-10-08",
                    "headline": "h",
                    "excerpt": "e",
                    "next_release": "n",
                    "url": "u",
                }
            )
        )
        monkeypatch.setattr(A, "ROOT", tmp_path)

    def test_missing_oni_returns_none(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, tmp_path, oni=pd.DataFrame())
        assert A.enso_block(_prices_df()) is None

    def test_full_structure(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, tmp_path)
        block = A.enso_block(_prices_df())
        assert block is not None
        assert block["generator"] == "rules"
        assert block["as_of"] == "2025-12-01"
        # KPI 卡 4 项
        k = block["kpis"]
        assert k["oni"]["phase"] == "超强厄尔尼诺"
        assert k["oni"]["chg_3m"] == pytest.approx(
            round((0.5 + 119 * 0.35) - (0.5 + 116 * 0.35), 2)
        )
        assert k["momentum"]["n"] == 6
        assert len(k["momentum"]["leaders"]) == 2
        assert k["cpi_food"] == {}
        # 三段研判（结论/依据/触发）
        assert len(block["signals"]) == 3
        for s in block["signals"]:
            assert set(s) == {"title", "text"}
            assert len(s["text"].split("\n")) == 3
        assert {s["title"] for s in block["signals"]} == {
            "气候相位与产区传导",
            "盘面定价与持仓拥挤度",
            "宏观通胀外溢",
        }
        # 速查表 + 讨论透传
        assert len(block["playbook"]) == 6
        assert block["discussion"]["status"] == "El Niño Advisory"

    def test_cot_worst_picked(self, monkeypatch, tmp_path):
        idx = pd.date_range("2025-01-01", periods=80, freq="W")
        cot = pd.DataFrame(
            {
                f"{s}_{m}": float(i * 1000)
                for s in ("SB", "KC")
                for i, m in enumerate(("MM_L", "MM_S"), 1)
            },
            index=idx,
        )
        self._patch(monkeypatch, tmp_path, cot=cot)
        block = A.enso_block(_prices_df())
        assert block["kpis"]["cot"]["worst"]["symbol"] in ("SB", "KC")
        assert len(block["cot_rows"]) == 2

    def test_history_from_pool(self, monkeypatch, tmp_path):
        m_idx = pd.date_range("2016-01-01", periods=130, freq="ME")
        pool = pd.DataFrame({"DBA": range(100, 230)}, index=m_idx)
        self._patch(monkeypatch, tmp_path, pool=pool)
        block = A.enso_block(_prices_df())
        assert block["history"] is not None
        assert len(block["history"]["dates"]) >= 24


# ── commodities() / positioning() 扩充结构 ───────────────────────────────────


class TestCommoditiesAndPositioning:
    def test_commodities_has_agri_and_enso(self, monkeypatch, tmp_path):
        p = _prices_df()
        monkeypatch.setattr(A, "asset_prices", lambda: p)
        monkeypatch.setattr(
            A,
            "_csv",
            lambda _p, **kw: _oni_df(),
        )
        monkeypatch.setattr(A, "ROOT", tmp_path)
        (tmp_path / "enso_discussion.json").write_text("{}")
        c = A.commodities()
        assert len(c["agri_cards"]) == 6
        assert set(c["agri_normalized"]["series"]) == {
            "Sugar",
            "Coffee",
            "Cocoa",
            "Soybean",
            "Corn",
            "Wheat",
        }
        assert set(c["recent"]["series"]) >= {"Sugar", "Wheat", "Gold"}
        assert c["enso"] is not None

    def test_positioning_ag_softs_group(self, monkeypatch):
        idx = pd.date_range("2024-01-01", periods=110, freq="W")
        cols = {}
        for sym in ("SB", "KC", "CC", "ZC", "ZS", "ZW"):
            cols[f"{sym}_MM_L"] = [1000 + i for i in range(110)]
            cols[f"{sym}_MM_S"] = [500] * 110
            cols[f"{sym}_OI"] = [5000] * 110
        monkeypatch.setattr(A, "_csv", lambda _p, **kw: pd.DataFrame(cols, index=idx))
        pos = A.positioning()
        names = [g["name"] for g in pos["groups"]]
        assert "ag_softs" in names
        ag = next(g for g in pos["groups"] if g["name"] == "ag_softs")
        assert [c["symbol"] for c in ag["contracts"]] == ["SB", "KC", "CC", "ZC", "ZS", "ZW"]
        assert all("pct_2y" in c and "spark" in c for c in ag["contracts"])

    def test_ag_softs_meta_complete(self):
        from src.assets_analysis import COT_META, COT_TIPS, COT_VERDICT, COT_VERDICT_EXTREME

        for sym in ("SB", "KC", "CC", "ZC", "ZS", "ZW"):
            assert sym in COT_META
            assert COT_META[sym][2] in ("080732", "083731", "073732", "002602", "005602", "001602")
        assert (
            "ag_softs" in COT_TIPS
            and "ag_softs" in COT_VERDICT
            and "ag_softs" in COT_VERDICT_EXTREME
        )


# ── inflation_analysis 轻联动 ────────────────────────────────────────────────


class TestInflationFood:
    def test_signal_drivers_includes_food(self):
        from src.inflation_analysis import signal_drivers

        comp = {
            k: {"value": 3.0}
            for k in (
                "CPI_SHELTER",
                "CORE_SERVICES",
                "SUPERCORE_PCE",
                "CORE_GOODS",
                "CPI_ENERGY",
                "CPI_FOOD",
            )
        }
        out = signal_drivers(comp, {})
        assert "食品 3.0%" in out
